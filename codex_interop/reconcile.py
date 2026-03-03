"""Desired-state reconcile engine for Codex interop artifacts."""

from __future__ import annotations

from dataclasses import dataclass
import difflib
from pathlib import Path
import shutil
import tempfile
from typing import Iterable
from uuid import uuid4

from codex_interop.model import (
    ClaudeShimDecision,
    DiscoveryResult,
    GeneratedAgentRole,
    GeneratedSkill,
    ReconcileError,
)
from codex_interop.state import InteropState


DEFAULT_CODEX_HOME = Path.home() / ".codex"
STATE_RELATIVE_PATH = Path(".codex") / "interop-state.json"
CONFIG_RELATIVE_PATH = Path(".codex") / "config.toml"


@dataclass(frozen=True)
class DesiredState:
    """Full desired-state model for one project reconcile."""

    project_root: Path
    codex_home: Path
    project_files: tuple[tuple[Path, bytes], ...]
    skills: tuple[GeneratedSkill, ...]
    selected_plugins: tuple[str, ...]
    state_path: Path


@dataclass(frozen=True)
class Change:
    """One file or directory level change."""

    kind: str
    path: Path
    detail: str = ""


@dataclass(frozen=True)
class ReconcileReport:
    """Reconcile or dry-run result."""

    changes: tuple[Change, ...]
    applied: bool


@dataclass(frozen=True)
class _PendingSwap:
    """One staged replacement waiting to be committed."""

    destination: Path
    staged_path: Path
    backup_root: Path
    is_dir: bool


@dataclass(frozen=True)
class _PendingRemoval:
    """One path that should be removed transactionally."""

    destination: Path
    backup_root: Path
    is_dir: bool


@dataclass
class _AppliedChange:
    """One committed path mutation that can be rolled back."""

    destination: Path
    backup_path: Path | None
    is_dir: bool


def build_desired_state(
    discovery: DiscoveryResult,
    shim_decision: ClaudeShimDecision,
    prompt_files: dict[Path, str],
    rendered_config: str,
    skills: Iterable[GeneratedSkill],
    *,
    codex_home: str | Path | None = None,
) -> DesiredState:
    """Build the desired generated outputs for a project."""
    if shim_decision.action == "fail":
        raise ReconcileError(shim_decision.reason)

    project_root = discovery.project.root
    codex_home_path = Path(codex_home or DEFAULT_CODEX_HOME).expanduser().resolve()
    project_files: list[tuple[Path, bytes]] = []

    if shim_decision.content is not None:
        project_files.append((project_root / "CLAUDE.md", shim_decision.content.encode()))

    project_files.append((project_root / CONFIG_RELATIVE_PATH, rendered_config.encode()))
    for relpath, content in sorted(prompt_files.items(), key=lambda item: item[0].as_posix()):
        project_files.append((project_root / relpath, content.encode()))

    selected_plugins = tuple(
        f"{plugin.marketplace}/{plugin.plugin_name}@{plugin.version_text}"
        for plugin in discovery.plugins
    )

    return DesiredState(
        project_root=project_root,
        codex_home=codex_home_path,
        project_files=tuple(project_files),
        skills=tuple(skills),
        selected_plugins=selected_plugins,
        state_path=project_root / STATE_RELATIVE_PATH,
    )


def diff_desired_state(desired: DesiredState) -> ReconcileReport:
    """Compare current outputs to desired state without writing."""
    previous_state = InteropState.from_path(desired.state_path)
    changes = _compute_changes(desired, previous_state)
    return ReconcileReport(changes=changes, applied=False)


def reconcile_desired_state(desired: DesiredState) -> ReconcileReport:
    """Apply the desired state to disk."""
    previous_state = InteropState.from_path(desired.state_path)
    changes = _compute_changes(desired, previous_state)
    if not changes:
        _write_state_if_needed(desired, previous_state)
        return ReconcileReport(changes=(), applied=True)

    pending_swaps, pending_removals, stage_roots = _stage_transaction(desired, changes)
    applied_changes: list[_AppliedChange] = []
    try:
        _apply_transaction(pending_swaps, pending_removals, applied_changes)
    except Exception:
        _rollback_transaction(applied_changes, desired)
        raise
    else:
        _finalize_transaction(applied_changes, desired)
    finally:
        for stage_root in stage_roots:
            if stage_root.exists():
                shutil.rmtree(stage_root)

    return ReconcileReport(changes=changes, applied=True)


def format_change_report(report: ReconcileReport) -> str:
    """Format a file-level summary of changes."""
    if not report.changes:
        return "No changes."
    return "\n".join(
        f"{change.kind.upper()}: {change.path}{f' ({change.detail})' if change.detail else ''}"
        for change in report.changes
    )


def format_diff_report(desired: DesiredState, report: ReconcileReport) -> str:
    """Format diff output, including unified diffs for text files where useful."""
    if not report.changes:
        return "No changes."

    lines = [format_change_report(report)]
    for change in report.changes:
        if change.kind not in {"create", "update"}:
            continue
        if change.path.suffix not in {".md", ".toml", ".json"}:
            continue
        desired_bytes = _lookup_desired_file_bytes(desired, change.path)
        if desired_bytes is None:
            continue
        existing_text = change.path.read_text() if change.path.exists() else ""
        desired_text = desired_bytes.decode()
        if existing_text == desired_text:
            continue
        diff = difflib.unified_diff(
            existing_text.splitlines(),
            desired_text.splitlines(),
            fromfile=str(change.path),
            tofile=str(change.path),
            lineterm="",
        )
        diff_lines = list(diff)
        if diff_lines:
            lines.append("")
            lines.extend(diff_lines)
    return "\n".join(lines)


def _compute_changes(
    desired: DesiredState,
    previous_state: InteropState | None,
) -> tuple[Change, ...]:
    """Compute file and directory changes, enforcing ownership safety."""
    project_file_map = dict(desired.project_files)
    managed_project_files = set(previous_state.managed_project_files) if previous_state else set()
    changes: list[Change] = []

    for path, content in sorted(project_file_map.items(), key=lambda item: str(item[0])):
        relative = _project_relative(desired, path)
        owned = relative in managed_project_files
        if not path.exists():
            changes.append(Change("create", path))
            continue
        if path.is_symlink():
            raise ReconcileError(f"Refusing to overwrite symlinked project file: {path}")
        existing = path.read_bytes()
        if existing == content:
            continue
        if not owned:
            raise ReconcileError(f"Refusing to overwrite non-generated project file: {path}")
        changes.append(Change("update", path))

    desired_project_paths = {_project_relative(desired, path) for path, _ in desired.project_files}
    for relative in sorted(managed_project_files - desired_project_paths):
        if relative == STATE_RELATIVE_PATH.as_posix():
            continue
        path = desired.project_root / relative
        if path.exists():
            changes.append(Change("remove", path))

    managed_skill_dirs = set(previous_state.managed_codex_skill_dirs) if previous_state else set()
    desired_skill_names = {skill.install_dir_name for skill in desired.skills}
    skills_root = desired.codex_home / "skills"
    for skill in desired.skills:
        path = skills_root / skill.install_dir_name
        owned = skill.install_dir_name in managed_skill_dirs
        if not path.exists():
            changes.append(Change("create", path, detail="skill"))
            continue
        if not path.is_dir():
            raise ReconcileError(f"Expected a skill directory but found a file: {path}")
        if _directory_matches_skill(path, skill):
            continue
        if not owned:
            raise ReconcileError(f"Refusing to overwrite non-generated skill directory: {path}")
        changes.append(Change("update", path, detail="skill"))

    for stale_skill_name in sorted(managed_skill_dirs - desired_skill_names):
        stale_path = skills_root / stale_skill_name
        if stale_path.exists():
            changes.append(Change("remove", stale_path, detail="skill"))

    return tuple(changes)


def _write_state_if_needed(desired: DesiredState, previous_state: InteropState | None) -> None:
    """Ensure state exists and stays in sync even when content outputs are unchanged."""
    desired_state = _build_state_record(desired)
    if previous_state is not None and previous_state == desired_state:
        return
    _atomic_write_file(desired.state_path, desired_state.to_json().encode())


def _write_skill_tree(destination: Path, skill: GeneratedSkill) -> None:
    """Write one staged skill directory tree."""
    for generated_file in skill.files:
        file_path = destination / generated_file.relative_path
        file_path.parent.mkdir(parents=True, exist_ok=True)
        file_path.write_bytes(generated_file.content)
        file_path.chmod(generated_file.mode)


def _directory_matches_skill(path: Path, skill: GeneratedSkill) -> bool:
    """Check whether an installed skill directory matches the desired tree exactly."""
    expected_files = {generated_file.relative_path: generated_file for generated_file in skill.files}
    actual_files = {
        item.relative_to(path): item
        for item in path.rglob("*")
        if item.is_file()
    }
    if set(actual_files) != set(expected_files):
        return False

    for relative_path, actual_path in actual_files.items():
        expected = expected_files[relative_path]
        if actual_path.read_bytes() != expected.content:
            return False
        if (actual_path.stat().st_mode & 0o777) != expected.mode:
            return False

    return True


def _atomic_write_file(path: Path, content: bytes) -> None:
    """Atomically write one file on the target filesystem."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as handle:
        handle.write(content)
        temp_path = Path(handle.name)
    temp_path.replace(path)


def _project_relative(desired: DesiredState, path: Path) -> str:
    """Return a project-relative path string."""
    return path.relative_to(desired.project_root).as_posix()


def _lookup_desired_file_bytes(desired: DesiredState, path: Path) -> bytes | None:
    """Look up desired bytes for a project file path."""
    for candidate_path, content in desired.project_files:
        if candidate_path == path:
            return content
    return None


def _is_project_path(desired: DesiredState, path: Path) -> bool:
    """Return True when a path is inside the target project."""
    try:
        path.relative_to(desired.project_root)
        return True
    except ValueError:
        return False


def _cleanup_empty_project_dirs(path: Path, stop_at: Path) -> None:
    """Remove empty generated directories up to `.codex`."""
    current = path
    while current != stop_at and current.exists():
        try:
            current.rmdir()
        except OSError:
            break
        current = current.parent


def _build_state_record(desired: DesiredState) -> InteropState:
    """Build the desired stable state payload."""
    managed_project_files = tuple(
        sorted(
            (*(_project_relative(desired, path) for path, _ in desired.project_files), STATE_RELATIVE_PATH.as_posix())
        )
    )
    return InteropState(
        project_root=desired.project_root,
        codex_home=desired.codex_home,
        selected_plugins=desired.selected_plugins,
        managed_project_files=managed_project_files,
        managed_codex_skill_dirs=tuple(sorted(skill.install_dir_name for skill in desired.skills)),
    )


def _stage_transaction(
    desired: DesiredState,
    changes: tuple[Change, ...],
) -> tuple[tuple[_PendingSwap, ...], tuple[_PendingRemoval, ...], tuple[Path, ...]]:
    """Stage all create/update artifacts before mutating any live outputs."""
    stage_roots: list[Path] = []
    pending_swaps: list[_PendingSwap] = []
    pending_removals: list[_PendingRemoval] = []

    desired_map = dict(desired.project_files)
    project_stage_root = Path(tempfile.mkdtemp(prefix=".interop-project-stage-", dir=desired.project_root))
    stage_roots.append(project_stage_root)
    project_backup_root = Path(tempfile.mkdtemp(prefix=".interop-project-backup-", dir=desired.project_root))
    stage_roots.append(project_backup_root)

    for change in sorted(changes, key=lambda item: str(item.path)):
        if change.detail == "skill":
            continue
        if not _is_project_path(desired, change.path):
            continue
        if change.kind == "remove":
            pending_removals.append(
                _PendingRemoval(
                    destination=change.path,
                    backup_root=project_backup_root,
                    is_dir=False,
                )
            )
            continue
        relative = _project_relative(desired, change.path)
        staged_path = project_stage_root / relative
        _write_staged_file(staged_path, desired_map[change.path])
        pending_swaps.append(
            _PendingSwap(
                destination=change.path,
                staged_path=staged_path,
                backup_root=project_backup_root,
                is_dir=False,
            )
        )

    state_bytes = _build_state_record(desired).to_json().encode()
    if not desired.state_path.exists() or desired.state_path.read_bytes() != state_bytes:
        staged_state_path = project_stage_root / STATE_RELATIVE_PATH
        _write_staged_file(staged_state_path, state_bytes)
        pending_swaps.append(
            _PendingSwap(
                destination=desired.state_path,
                staged_path=staged_state_path,
                backup_root=project_backup_root,
                is_dir=False,
            )
        )

    skills_root = desired.codex_home / "skills"
    desired.codex_home.mkdir(parents=True, exist_ok=True)
    skill_stage_root = Path(tempfile.mkdtemp(prefix=".interop-skill-stage-", dir=skills_root.parent))
    stage_roots.append(skill_stage_root)
    skill_backup_root = Path(tempfile.mkdtemp(prefix=".interop-skill-backup-", dir=skills_root.parent))
    stage_roots.append(skill_backup_root)
    skills_by_name = {skill.install_dir_name: skill for skill in desired.skills}

    for change in sorted(changes, key=lambda item: str(item.path)):
        if change.detail != "skill":
            continue
        if change.kind == "remove":
            pending_removals.append(
                _PendingRemoval(
                    destination=change.path,
                    backup_root=skill_backup_root,
                    is_dir=True,
                )
            )
            continue

        skill = skills_by_name[change.path.name]
        staged_dir = skill_stage_root / change.path.name
        staged_dir.mkdir(parents=True, exist_ok=True)
        _write_skill_tree(staged_dir, skill)
        pending_swaps.append(
            _PendingSwap(
                destination=change.path,
                staged_path=staged_dir,
                backup_root=skill_backup_root,
                is_dir=True,
            )
        )

    return tuple(pending_swaps), tuple(pending_removals), tuple(stage_roots)


def _apply_transaction(
    pending_swaps: tuple[_PendingSwap, ...],
    pending_removals: tuple[_PendingRemoval, ...],
    applied_changes: list[_AppliedChange],
) -> None:
    """Apply all staged replacements and removals with rollback metadata."""
    for swap in pending_swaps:
        backup_path = _swap_path(swap.destination, swap.staged_path, swap.backup_root, is_dir=swap.is_dir)
        applied_changes.append(
            _AppliedChange(
                destination=swap.destination,
                backup_path=backup_path,
                is_dir=swap.is_dir,
            )
        )

    for removal in pending_removals:
        backup_path = _remove_path(removal.destination, removal.backup_root, is_dir=removal.is_dir)
        applied_changes.append(
            _AppliedChange(
                destination=removal.destination,
                backup_path=backup_path,
                is_dir=removal.is_dir,
            )
        )


def _finalize_transaction(applied_changes: list[_AppliedChange], desired: DesiredState) -> None:
    """Discard backups after the transaction has completed successfully."""
    for applied_change in reversed(applied_changes):
        if applied_change.backup_path is None:
            continue
        _remove_existing_path(applied_change.backup_path, is_dir=applied_change.is_dir)
        if not applied_change.is_dir:
            _cleanup_empty_project_dirs(
                applied_change.backup_path.parent,
                desired.project_root,
            )

    for applied_change in applied_changes:
        if applied_change.backup_path is not None:
            continue
        if not applied_change.destination.exists():
            continue
        if applied_change.is_dir:
            continue
        _cleanup_empty_project_dirs(
            applied_change.destination.parent,
            desired.project_root / ".codex",
        )


def _rollback_transaction(applied_changes: list[_AppliedChange], desired: DesiredState) -> None:
    """Restore the last known good outputs after a failed apply."""
    for applied_change in reversed(applied_changes):
        if applied_change.destination.exists():
            _remove_existing_path(applied_change.destination, is_dir=applied_change.is_dir)
        if applied_change.backup_path is not None and applied_change.backup_path.exists():
            applied_change.destination.parent.mkdir(parents=True, exist_ok=True)
            applied_change.backup_path.rename(applied_change.destination)

    for applied_change in applied_changes:
        if applied_change.is_dir:
            continue
        if applied_change.backup_path is None:
            _cleanup_empty_project_dirs(
                applied_change.destination.parent,
                desired.project_root / ".codex",
            )
        else:
            _cleanup_empty_project_dirs(
                applied_change.backup_path.parent,
                desired.project_root,
            )


def _swap_path(destination: Path, staged_path: Path, backup_root: Path, *, is_dir: bool) -> Path | None:
    """Replace a live path with a staged one, returning the backup path when replaced."""
    destination.parent.mkdir(parents=True, exist_ok=True)
    backup_path = None
    if destination.exists():
        backup_path = backup_root / uuid4().hex / destination.name
        backup_path.parent.mkdir(parents=True, exist_ok=True)
        destination.rename(backup_path)
    staged_path.rename(destination)
    return backup_path


def _remove_path(destination: Path, backup_root: Path, *, is_dir: bool) -> Path | None:
    """Move a live path aside so it can be restored if the transaction fails."""
    if not destination.exists():
        return None
    backup_path = backup_root / uuid4().hex / destination.name
    backup_path.parent.mkdir(parents=True, exist_ok=True)
    destination.rename(backup_path)
    return backup_path


def _write_staged_file(path: Path, content: bytes) -> None:
    """Write one staged file inside a transaction staging directory."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)


def _remove_existing_path(path: Path, *, is_dir: bool) -> None:
    """Remove an existing file or directory."""
    if is_dir:
        shutil.rmtree(path)
    else:
        path.unlink()
