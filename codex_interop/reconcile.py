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

    _apply_project_file_changes(desired, previous_state, changes)
    _apply_skill_changes(desired, previous_state, changes)
    _remove_stale_outputs(desired, previous_state, changes)
    _write_state(desired)
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


def _apply_project_file_changes(
    desired: DesiredState,
    previous_state: InteropState | None,
    changes: tuple[Change, ...],
) -> None:
    """Apply create/update operations for project files."""
    project_updates = {
        change.path
        for change in changes
        if change.kind in {"create", "update"} and _is_project_path(desired, change.path)
    }
    if not project_updates:
        return

    desired_map = dict(desired.project_files)
    for path in sorted(project_updates):
        _atomic_write_file(path, desired_map[path])


def _apply_skill_changes(
    desired: DesiredState,
    previous_state: InteropState | None,
    changes: tuple[Change, ...],
) -> None:
    """Apply create/update operations for skill directories."""
    changed_skill_paths = {
        change.path
        for change in changes
        if change.kind in {"create", "update"} and change.detail == "skill"
    }
    if not changed_skill_paths:
        return

    skills_by_name = {skill.install_dir_name: skill for skill in desired.skills}
    skills_root = desired.codex_home / "skills"
    skills_root.mkdir(parents=True, exist_ok=True)

    for destination in sorted(changed_skill_paths):
        skill = skills_by_name[destination.name]
        stage_dir = Path(tempfile.mkdtemp(prefix=f".interop-skill-{destination.name}-", dir=skills_root))
        try:
            _write_skill_tree(stage_dir, skill)
            if destination.exists():
                backup_dir = skills_root / f".interop-backup-{destination.name}-{uuid4().hex}"
                destination.rename(backup_dir)
                try:
                    stage_dir.rename(destination)
                except Exception:
                    backup_dir.rename(destination)
                    raise
                finally:
                    if backup_dir.exists():
                        shutil.rmtree(backup_dir)
            else:
                stage_dir.rename(destination)
        finally:
            if stage_dir.exists():
                shutil.rmtree(stage_dir)


def _remove_stale_outputs(
    desired: DesiredState,
    previous_state: InteropState | None,
    changes: tuple[Change, ...],
) -> None:
    """Remove previously managed outputs that are no longer desired."""
    for change in changes:
        if change.kind != "remove":
            continue
        if change.path == desired.state_path:
            continue
        if change.detail == "skill":
            shutil.rmtree(change.path)
        else:
            change.path.unlink()
            _cleanup_empty_project_dirs(change.path.parent, desired.project_root / ".codex")


def _write_state(desired: DesiredState) -> None:
    """Write the project-local interop state file deterministically."""
    state = _build_state_record(desired)
    _atomic_write_file(desired.state_path, state.to_json().encode())


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
