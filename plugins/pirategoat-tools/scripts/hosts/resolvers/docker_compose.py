"""docker-compose volume resolver."""

import glob
import os
import re
from typing import Any, Dict, List, Optional, Tuple

try:
    import yaml  # PyYAML
except ImportError:
    yaml = None

from containment import contains
from hosts.resolvers.base import HostResolver, ResolverResult
from hosts.types import HostEntry


_WP_PLUGIN_PREFIX = "/var/www/html/wp-content/plugins/"
_WP_THEME_PREFIX = "/var/www/html/wp-content/themes/"
_WP_CORE_ROOT = "/var/www/html"

_COMPOSE_FILE_PATTERNS = [
    "docker-compose*.yml",
    "docker-compose*.yaml",
    "compose.yml",
    "compose.yaml",
]

_COMPOSE_VAR_RE = re.compile(
    r"\$(?:"
    r"(?P<bare>[A-Za-z_][A-Za-z0-9_]*)"
    r"|\{(?P<braced>[A-Za-z_][A-Za-z0-9_]*)(?:(?P<op>:?[-+?])(?P<arg>[^}]*))?\}"
    r")"
)

_LONG_FORM_VOLUME_KEYS = frozenset({"type", "source", "src", "target", "dst", "destination"})


class DockerComposeResolver(HostResolver):
    source = "docker-compose"

    def resolve(self, repo_path: str) -> ResolverResult:
        compose_files = sorted({
            path
            for pattern in _COMPOSE_FILE_PATTERNS
            for path in glob.glob(os.path.join(repo_path, pattern))
        })
        if not compose_files:
            return ResolverResult(entries=[], unresolved=[], notes={})

        entries: List[HostEntry] = []
        unresolved: List[Dict[str, Any]] = []
        parse_errors: List[str] = []

        for cf in compose_files:
            compose_env = self._load_env_file(os.path.dirname(cf))
            compose_env.update(os.environ)

            if yaml is None:
                self._handle_compose_file_without_pyyaml(
                    repo_path, cf, compose_env, entries, unresolved, parse_errors
                )
                continue

            try:
                with open(cf) as f:
                    data = yaml.safe_load(f) or {}
            except (yaml.YAMLError, OSError) as err:
                parse_errors.append(f"{cf}: {err}")
                continue

            if not isinstance(data, dict):
                parse_errors.append(f"{cf}: expected object at root, got {type(data).__name__}")
                continue

            services = (data.get("services") or {})
            for svc_name, svc in services.items():
                for vol in (svc.get("volumes") or []):
                    if isinstance(vol, str):
                        self._handle_volume(repo_path, cf, vol, compose_env, entries, unresolved)
                    elif isinstance(vol, dict):
                        self._handle_long_form_volume(repo_path, cf, vol, compose_env, entries, unresolved)

        notes: Dict[str, Any] = {}
        if parse_errors:
            notes["parse_error"] = "; ".join(parse_errors)
        return ResolverResult(entries=entries, unresolved=unresolved, notes=notes)

    def _handle_compose_file_without_pyyaml(
        self,
        repo_path: str,
        compose_file: str,
        env: Dict[str, str],
        entries: List[HostEntry],
        unresolved: List[Dict[str, Any]],
        parse_errors: List[str],
    ) -> None:
        try:
            with open(compose_file) as f:
                lines = f.readlines()
        except OSError as err:
            parse_errors.append(f"{compose_file}: {err}")
            return

        for vol in self._fallback_volume_entries(lines):
            if isinstance(vol, str):
                self._handle_volume(repo_path, compose_file, vol, env, entries, unresolved)
            else:
                self._handle_long_form_volume(repo_path, compose_file, vol, env, entries, unresolved)

    def _handle_volume(
        self,
        repo_path: str,
        compose_file: str,
        vol: str,
        env: Dict[str, str],
        entries,
        unresolved,
    ):
        parts = self._split_short_volume(vol)
        if parts is None:
            return
        source, target = parts
        self._handle_bind_mount(repo_path, compose_file, source, target, vol, env, entries, unresolved)

    def _handle_long_form_volume(
        self,
        repo_path: str,
        compose_file: str,
        vol: Dict[str, Any],
        env: Dict[str, str],
        entries,
        unresolved,
    ):
        if vol.get("type") != "bind":
            return
        source = vol.get("source") or vol.get("src")
        target = vol.get("target") or vol.get("dst") or vol.get("destination")
        if not isinstance(source, str) or not isinstance(target, str):
            return
        self._handle_bind_mount(repo_path, compose_file, source, target, vol, env, entries, unresolved)

    @classmethod
    def _fallback_volume_entries(cls, lines: List[str]) -> List[Any]:
        """Extract obvious Compose volume entries without a YAML dependency."""
        volumes_indent: Optional[int] = None
        current_map: Optional[Dict[str, str]] = None
        current_item_indent: Optional[int] = None
        entries: List[Any] = []

        def flush_current_map() -> None:
            nonlocal current_map, current_item_indent
            if current_map is not None:
                entries.append(current_map)
            current_map = None
            current_item_indent = None

        for raw_line in lines:
            if not raw_line.strip() or raw_line.lstrip().startswith("#"):
                continue

            indent = len(raw_line) - len(raw_line.lstrip())
            stripped = raw_line.strip()

            if volumes_indent is not None and indent <= volumes_indent and stripped != "volumes:":
                flush_current_map()
                volumes_indent = None

            if stripped == "volumes:":
                flush_current_map()
                volumes_indent = indent
                continue

            if volumes_indent is None:
                continue

            if stripped.startswith("- "):
                flush_current_map()
                item = stripped[2:].strip()
                if not item:
                    current_map = {}
                    current_item_indent = indent
                    continue

                key, sep, value = item.partition(":")
                if sep and key.strip() in _LONG_FORM_VOLUME_KEYS:
                    current_map = {key.strip(): cls._unquote_scalar(value.strip())}
                    current_item_indent = indent
                else:
                    entries.append(cls._unquote_scalar(item))
                continue

            if (
                current_map is not None
                and current_item_indent is not None
                and indent > current_item_indent
            ):
                key, sep, value = stripped.partition(":")
                if sep and key.strip() in _LONG_FORM_VOLUME_KEYS:
                    current_map[key.strip()] = cls._unquote_scalar(value.strip())

        flush_current_map()
        return entries

    @staticmethod
    def _unquote_scalar(value: str) -> str:
        if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
            return value[1:-1]
        return value

    def _handle_bind_mount(
        self,
        repo_path: str,
        compose_file: str,
        source: str,
        target: str,
        raw: Any,
        env: Dict[str, str],
        entries,
        unresolved,
    ):
        kind = self._classify_target(target)
        if kind is None:
            return  # unrelated volume

        expanded_source, unresolved_vars = self._expand_source(source, env)
        if unresolved_vars:
            unresolved.append({
                "name": self._name_from_target(target, expanded_source),
                "reason": "variable_unresolved",
                "source": "docker-compose",
                "raw": raw,
                "variables": unresolved_vars,
            })
            return
        if not self._looks_like_bind_source(source, expanded_source):
            return  # named Docker volume, not host source code

        is_absolute = os.path.isabs(expanded_source)
        resolved = expanded_source if is_absolute else os.path.abspath(
            os.path.join(os.path.dirname(compose_file), expanded_source)
        )
        name = self._name_from_target(target, resolved)
        if self._is_inside_repo(resolved, repo_path):
            # Two cases produce an in-repo source:
            # 1. The repo IS the host — `.` mounted at its own slot, or a
            #    monorepo subdirectory like `./plugins/<name>` providing
            #    that plugin. Silent skip — the repo can't be its own
            #    upstream.
            # 2. The repo VENDORS a copy of WordPress core for its dev
            #    stack (e.g. `./docker/wordpress:/var/www/html/`). The
            #    bundled copy isn't useful as upstream source, but the
            #    repo IS signaling it needs WP. Surface as unresolved so
            #    the chain's cache-fulfillment pass can satisfy it.
            #
            # Only flag (2) for `core` targets where the source isn't the
            # repo root itself. Plugin/theme self-mounts are almost
            # always "repo is the plugin/theme" and shouldn't trigger
            # banners or fulfillment.
            if (
                kind == "core"
                and os.path.realpath(resolved) != os.path.realpath(repo_path)
            ):
                unresolved.append({
                    "name": name,
                    "reason": "vendored_self_mount",
                    "source": "docker-compose",
                    "raw": raw,
                    "target": target,
                })
            return

        if not os.path.isdir(resolved):
            unresolved.append({
                "name": name,
                "reason": "path_missing",
                "source": "docker-compose",
                "raw": raw,
            })
            return

        entry = HostEntry(
            name=name,
            kind="runtime-host",
            path=resolved,
            source=self.source,
            confidence="high",
            notes={"wp_kind": kind, "personal": is_absolute},
        )
        entries.append(entry)

    @staticmethod
    def _split_short_volume(vol: str) -> Optional[Tuple[str, str]]:
        separators: List[int] = []
        brace_depth = 0
        for index, char in enumerate(vol):
            if char == "{":
                brace_depth += 1
            elif char == "}" and brace_depth:
                brace_depth -= 1
            elif char == ":" and brace_depth == 0:
                separators.append(index)
                if len(separators) == 2:
                    break

        if not separators:
            return None

        first = separators[0]
        second = separators[1] if len(separators) > 1 else len(vol)
        return vol[:first], vol[first + 1:second]

    @staticmethod
    def _load_env_file(compose_dir: str) -> Dict[str, str]:
        env_path = os.path.join(compose_dir, ".env")
        values: Dict[str, str] = {}
        try:
            with open(env_path) as f:
                lines = f.readlines()
        except OSError:
            return values

        for raw_line in lines:
            line = raw_line.strip()
            if not line or line.startswith("#"):
                continue
            if line.startswith("export "):
                line = line[len("export "):].lstrip()
            if "=" not in line:
                continue
            name, value = line.split("=", 1)
            name = name.strip()
            if not re.match(r"^[A-Za-z_][A-Za-z0-9_]*$", name):
                continue
            value = value.strip()
            if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
                value = value[1:-1]
            values[name] = value
        return values

    @staticmethod
    def _expand_source(source: str, env: Dict[str, str]) -> Tuple[str, List[str]]:
        unresolved_vars: List[str] = []

        def replace(match: re.Match) -> str:
            name = match.group("bare") or match.group("braced")
            op = match.group("op")
            arg = match.group("arg") or ""
            value = env.get(name)
            is_set = value is not None
            is_nonempty = bool(value)

            if op in (None, ""):
                if not is_set:
                    unresolved_vars.append(name)
                return value or ""
            if op == ":-":
                return value if is_nonempty else arg
            if op == "-":
                return value if is_set else arg
            if op == ":+":
                return arg if is_nonempty else ""
            if op == "+":
                return arg if is_set else ""
            if op in (":?", "?"):
                if not (is_nonempty if op == ":?" else is_set):
                    unresolved_vars.append(name)
                return value if (is_nonempty if op == ":?" else is_set) else ""
            return match.group(0)

        expanded = os.path.expanduser(_COMPOSE_VAR_RE.sub(replace, source))
        return expanded, sorted(set(unresolved_vars))

    @staticmethod
    def _looks_like_bind_source(source: str, expanded_source: str) -> bool:
        if os.path.isabs(expanded_source):
            return True
        if expanded_source.startswith(("./", "../")):
            return True
        if source.startswith(("~", "$")):
            return True
        return False

    @staticmethod
    def _is_inside_repo(path: str, repo_path: str) -> bool:
        return contains(repo_path, path)

    @staticmethod
    def _classify_target(target: str) -> Optional[str]:
        if target.startswith(_WP_PLUGIN_PREFIX):
            return "plugin"
        if target.startswith(_WP_THEME_PREFIX):
            return "theme"
        if target in (_WP_CORE_ROOT, _WP_CORE_ROOT + "/"):
            return "core"
        return None

    @staticmethod
    def _name_from_target(target: str, resolved: str) -> str:
        if target.startswith(_WP_PLUGIN_PREFIX):
            remainder = target[len(_WP_PLUGIN_PREFIX):].strip("/")
            # Path can have double slashes in real configs; keep last segment
            return remainder.split("/")[-1] or os.path.basename(resolved.rstrip("/"))
        if target.startswith(_WP_THEME_PREFIX):
            remainder = target[len(_WP_THEME_PREFIX):].strip("/")
            return remainder.split("/")[-1] or os.path.basename(resolved.rstrip("/"))
        if target in (_WP_CORE_ROOT, _WP_CORE_ROOT + "/"):
            return "wordpress"
        return os.path.basename(resolved.rstrip("/"))
