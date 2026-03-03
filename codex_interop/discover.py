"""Phase 1 discovery for Codex interop generation."""

from __future__ import annotations

from collections import defaultdict
from pathlib import Path

from codex_interop.model import (
    DiscoveryError,
    DiscoveryResult,
    InstalledPlugin,
    InstalledPluginVersion,
    ProjectContext,
    SemVer,
)


AGENTS_MD = "AGENTS.md"
CLAUDE_CACHE_DIR = Path.home() / ".claude" / "plugins" / "cache"


def resolve_project_root(start_path: str | Path | None = None) -> ProjectContext:
    """Resolve a project root from cwd or an explicit path.

    Searches upward from the selected path until it finds `AGENTS.md`.
    """
    candidate = Path(start_path or Path.cwd()).resolve()
    if candidate.is_file():
        candidate = candidate.parent

    for directory in (candidate, *candidate.parents):
        agents_md_path = directory / AGENTS_MD
        if agents_md_path.is_file():
            return ProjectContext(root=directory, agents_md_path=agents_md_path)

    raise DiscoveryError(
        f"Could not resolve a project root with {AGENTS_MD} from: {candidate}"
    )


def discover(
    project_path: str | Path | None = None,
    cache_dir: str | Path | None = None,
) -> DiscoveryResult:
    """Resolve the target project and latest installed Claude plugins."""
    project = resolve_project_root(project_path)
    plugins = discover_latest_plugins(cache_dir)
    return DiscoveryResult(project=project, plugins=plugins)


def discover_latest_plugins(
    cache_dir: str | Path | None = None,
) -> tuple[InstalledPlugin, ...]:
    """Discover the latest installed version of each Claude plugin."""
    root = Path(cache_dir or CLAUDE_CACHE_DIR).expanduser().resolve()
    if not root.is_dir():
        raise DiscoveryError(f"Claude plugin cache not found: {root}")

    grouped_versions: dict[tuple[str, str], list[InstalledPluginVersion]] = defaultdict(list)

    for marketplace_dir in sorted(_iter_dirs(root)):
        for plugin_dir in sorted(_iter_dirs(marketplace_dir)):
            versions = list(_collect_plugin_versions(marketplace_dir.name, plugin_dir))
            if not versions:
                raise DiscoveryError(
                    "No valid semantic versions found for installed Claude plugin "
                    f"{marketplace_dir.name}/{plugin_dir.name}"
                )
            grouped_versions[(marketplace_dir.name, plugin_dir.name)].extend(versions)

    if not grouped_versions:
        raise DiscoveryError(f"No installed Claude plugins found in cache: {root}")

    latest_plugins = []
    for key in sorted(grouped_versions):
        latest = sorted(grouped_versions[key], key=lambda item: item.version)[-1]
        latest_plugins.append(
            InstalledPlugin(
                marketplace=latest.marketplace,
                plugin_name=latest.plugin_name,
                version_text=latest.version_text,
                version=latest.version,
                installed_path=latest.installed_path,
                source_path=latest.resolved_path,
                skills=tuple(_discover_skills(latest.resolved_path)),
                agents=tuple(_discover_agents(latest.resolved_path)),
            )
        )

    return tuple(latest_plugins)


def _collect_plugin_versions(
    marketplace: str,
    plugin_dir: Path,
) -> list[InstalledPluginVersion]:
    """Collect valid semantic-version installs for one plugin directory."""
    versions = []
    for version_dir in sorted(_iter_dirs(plugin_dir)):
        try:
            version = SemVer.parse(version_dir.name)
        except ValueError:
            continue

        versions.append(
            InstalledPluginVersion(
                marketplace=marketplace,
                plugin_name=plugin_dir.name,
                version_text=version_dir.name,
                version=version,
                installed_path=version_dir,
                resolved_path=version_dir.resolve(),
            )
        )
    return versions


def _discover_skills(plugin_path: Path) -> list[Path]:
    """Return skill directories that contain SKILL.md."""
    skills_dir = plugin_path / "skills"
    if not skills_dir.is_dir():
        return []

    return sorted(
        skill_dir for skill_dir in _iter_dirs(skills_dir) if (skill_dir / "SKILL.md").is_file()
    )


def _discover_agents(plugin_path: Path) -> list[Path]:
    """Return top-level agent markdown files for a plugin."""
    agents_dir = plugin_path / "agents"
    if not agents_dir.is_dir():
        return []

    return sorted(
        path for path in agents_dir.iterdir() if path.is_file() and path.suffix == ".md"
    )


def _iter_dirs(path: Path):
    """Yield direct child directories in deterministic order."""
    return (entry for entry in path.iterdir() if entry.is_dir())
