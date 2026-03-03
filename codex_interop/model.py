"""Data models for Codex interop discovery."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import re


SEMVER_RE = re.compile(
    r"^(?P<major>0|[1-9]\d*)\.(?P<minor>0|[1-9]\d*)\.(?P<patch>0|[1-9]\d*)"
    r"(?:-(?P<prerelease>[0-9A-Za-z.-]+))?"
    r"(?:\+(?P<build>[0-9A-Za-z.-]+))?$"
)


class DiscoveryError(RuntimeError):
    """Raised when project or installed-plugin discovery fails."""


class TranslationError(RuntimeError):
    """Raised when translation from Claude artifacts to Codex artifacts fails."""


class ReconcileError(RuntimeError):
    """Raised when reconcile planning or writes cannot proceed safely."""


@dataclass(frozen=True)
class SemVer:
    """Semantic version with precedence comparison."""

    major: int
    minor: int
    patch: int
    prerelease: tuple[str, ...] = ()

    @classmethod
    def parse(cls, value: str) -> "SemVer":
        """Parse a semantic version string or raise ValueError."""
        match = SEMVER_RE.match(value)
        if not match:
            raise ValueError(f"Invalid semantic version: {value}")

        prerelease = tuple(
            part for part in (match.group("prerelease") or "").split(".") if part
        )

        return cls(
            major=int(match.group("major")),
            minor=int(match.group("minor")),
            patch=int(match.group("patch")),
            prerelease=prerelease,
        )

    def __lt__(self, other: object) -> bool:
        """Compare semantic versions by precedence."""
        if not isinstance(other, SemVer):
            return NotImplemented

        core_self = (self.major, self.minor, self.patch)
        core_other = (other.major, other.minor, other.patch)
        if core_self != core_other:
            return core_self < core_other

        if not self.prerelease and other.prerelease:
            return False
        if self.prerelease and not other.prerelease:
            return True
        if not self.prerelease and not other.prerelease:
            return False

        return _compare_prerelease(self.prerelease, other.prerelease) < 0


def _compare_prerelease(left: tuple[str, ...], right: tuple[str, ...]) -> int:
    """Return -1, 0, or 1 for prerelease precedence comparison."""
    for left_part, right_part in zip(left, right):
        if left_part == right_part:
            continue

        left_is_num = left_part.isdigit()
        right_is_num = right_part.isdigit()

        if left_is_num and right_is_num:
            return -1 if int(left_part) < int(right_part) else 1
        if left_is_num and not right_is_num:
            return -1
        if not left_is_num and right_is_num:
            return 1
        return -1 if left_part < right_part else 1

    if len(left) == len(right):
        return 0
    return -1 if len(left) < len(right) else 1


@dataclass(frozen=True)
class ProjectContext:
    """Resolved project root and instruction file."""

    root: Path
    agents_md_path: Path


@dataclass(frozen=True)
class InstalledPluginVersion:
    """One installed Claude plugin version."""

    marketplace: str
    plugin_name: str
    version_text: str
    version: SemVer
    installed_path: Path
    resolved_path: Path


@dataclass(frozen=True)
class InstalledPlugin:
    """Latest installed plugin source selected for generation."""

    marketplace: str
    plugin_name: str
    version_text: str
    version: SemVer
    installed_path: Path
    source_path: Path
    skills: tuple[Path, ...]
    agents: tuple[Path, ...]


@dataclass(frozen=True)
class DiscoveryResult:
    """Combined project and installed-plugin discovery result."""

    project: ProjectContext
    plugins: tuple[InstalledPlugin, ...]


@dataclass(frozen=True)
class ClaudeShimDecision:
    """Decision for handling the project root CLAUDE.md shim."""

    action: str
    path: Path
    content: str | None = None
    reason: str = ""


@dataclass(frozen=True)
class GeneratedAgentRole:
    """Generated Codex role metadata derived from a Claude agent."""

    plugin_name: str
    source_path: Path
    role_name: str
    description: str
    original_model_hint: str | None
    model: str
    tools: tuple[str, ...]
    prompt_relpath: Path
    prompt_body: str


@dataclass(frozen=True)
class GeneratedSkillFile:
    """One file that will be materialized inside a generated Codex skill."""

    relative_path: Path
    content: bytes
    mode: int


@dataclass(frozen=True)
class GeneratedSkill:
    """Generated Codex skill tree derived from a Claude skill."""

    marketplace: str
    plugin_name: str
    source_path: Path
    install_dir_name: str
    original_skill_name: str
    codex_skill_name: str
    files: tuple[GeneratedSkillFile, ...]
