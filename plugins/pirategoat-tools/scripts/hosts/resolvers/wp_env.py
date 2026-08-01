"""wp-env configuration resolver (.wp-env.json + .wp-env.override.json)."""

import json
import os
import re
from typing import Any, Dict, List, Optional, Tuple

from hosts.containment import contains
from hosts.resolvers.base import HostResolver, ResolverResult
from hosts.types import HostEntry


_REMOTE_REF_PATTERN = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+(#[^/]+)?$")
_MAPPING_CODE_TARGET_PREFIXES = (
    "wp-content/plugins/",
    "wp-content/themes/",
)


class WpEnvResolver(HostResolver):
    source = "wp-env"

    def resolve(self, repo_path: str) -> ResolverResult:
        base = self._read_json(os.path.join(repo_path, ".wp-env.json"))
        override = self._read_json(os.path.join(repo_path, ".wp-env.override.json"))
        if not base and not override:
            return ResolverResult(entries=[], unresolved=[], notes={})

        merged = {**(base or {}), **(override or {})}
        if (
            isinstance((base or {}).get("mappings"), dict)
            and isinstance((override or {}).get("mappings"), dict)
        ):
            merged["mappings"] = {
                **base["mappings"],
                **override["mappings"],
            }

        entries: List[HostEntry] = []
        unresolved: List[Dict[str, Any]] = []

        # mappings: {target: source}  — source is usually local
        mappings = merged.get("mappings") or {}
        if isinstance(mappings, dict):
            for target, source in mappings.items():
                if not isinstance(source, str):
                    # Object-form source (e.g. {"ref": ..., "localPath": ...})
                    # — not a local path, skip. Callers relying on these refs
                    # should use the remote-ref branch.
                    continue
                self._handle_mapping_source(repo_path, source, target, entries, unresolved)

        # plugins / themes arrays
        for field in ("plugins", "themes"):
            for item in merged.get(field, []) or []:
                if not isinstance(item, str):
                    continue
                self._handle_array_item(repo_path, item, entries, unresolved)

        # core (may be string)
        core = merged.get("core")
        if isinstance(core, str):
            self._handle_core(repo_path, core, entries, unresolved)

        return ResolverResult(entries=entries, unresolved=unresolved, notes={})

    @staticmethod
    def _read_json(path: str) -> Optional[Dict[str, Any]]:
        if not os.path.isfile(path):
            return None
        try:
            with open(path) as f:
                return json.load(f)
        except (json.JSONDecodeError, OSError):
            return None

    @staticmethod
    def _classify(value: str) -> str:
        """Return 'local', 'remote', 'url', or 'other' for a wp-env source value."""
        if value == "." or value.startswith("./") or value.startswith("../") or value.startswith("/"):
            return "local"
        if _REMOTE_REF_PATTERN.match(value):
            return "remote"
        if "://" in value or value.startswith("http"):
            return "url"
        return "other"

    @staticmethod
    def _parse_remote_ref(value: str) -> Tuple[Optional[str], Optional[str]]:
        """Parse 'owner/repo#ref' → (repo_name, ref). Returns (None, None) if unmatched."""
        m = re.match(r"^[A-Za-z0-9_.-]+/([A-Za-z0-9_.-]+)(?:#(.+))?$", value)
        if not m:
            return (None, None)
        return (m.group(1), m.group(2))

    def _handle_mapping_source(self, repo_path, source, target, entries, unresolved):
        name = self._name_from_code_mapping_target(target)
        if name is None:
            return
        kind = self._classify(source)
        if kind != "local":
            unresolved.append({
                "name": name,
                "reason": "remote_ref_not_local",
                "source": "wp-env",
                "raw": source,
            })
            return
        resolved = os.path.abspath(os.path.join(repo_path, source))
        if not os.path.isdir(resolved):
            unresolved.append({
                "name": name,
                "reason": "path_missing",
                "source": "wp-env",
                "raw": source,
            })
            return
        if self._is_inside_repo(repo_path, resolved):
            return
        entries.append(HostEntry(
            name=name,
            kind="runtime-host",
            path=resolved,
            source=self.source,
            confidence="high",
        ))

    def _handle_array_item(self, repo_path, item, entries, unresolved):
        if item == ".":
            return  # self, not upstream
        kind = self._classify(item)
        if kind == "remote":
            name, version = self._parse_remote_ref(item)
            if name is not None:
                unresolved.append({
                    "name": name,
                    "version": version,
                    "reason": "remote_ref_not_local",
                    "source": "wp-env",
                    "raw": item,
                })
            return
        if kind != "local":
            return
        resolved = os.path.abspath(os.path.join(repo_path, item))
        if not os.path.isdir(resolved):
            return  # silent skip for missing local path in arrays; explicit mapping gets the unresolved
        if self._is_inside_repo(repo_path, resolved):
            return
        entries.append(HostEntry(
            name=os.path.basename(resolved.rstrip("/")),
            kind="runtime-host",
            path=resolved,
            source=self.source,
            confidence="high",
        ))

    def _handle_core(self, repo_path, core, entries, unresolved):
        kind = self._classify(core)
        if kind == "local":
            resolved = os.path.abspath(os.path.join(repo_path, core))
            if os.path.isdir(resolved):
                if self._is_inside_repo(repo_path, resolved):
                    return
                entries.append(HostEntry(
                    name="wordpress",
                    kind="runtime-host",
                    path=resolved,
                    source=self.source,
                    confidence="high",
                ))
            return
        if kind == "remote":
            _, version = self._parse_remote_ref(core)
            unresolved.append({
                "name": "wordpress",
                "version": version,  # None is fine if parse failed
                "reason": "remote_ref_not_local",
                "source": "wp-env",
                "raw": core,
            })

    @staticmethod
    def _is_inside_repo(repo_path: str, resolved_path: str) -> bool:
        return contains(repo_path, resolved_path)

    @staticmethod
    def _name_from_code_mapping_target(target: str) -> Optional[str]:
        normalized = target.strip().strip("/")
        for prefix in _MAPPING_CODE_TARGET_PREFIXES:
            if normalized.startswith(prefix):
                remainder = normalized[len(prefix):].strip("/")
                if remainder:
                    return remainder.split("/")[0]
        return None
