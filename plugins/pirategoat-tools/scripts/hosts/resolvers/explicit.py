"""Explicit host configuration from .pirategoat/config.json."""

import json
import os
from typing import Any, Dict, List

from hosts.resolvers.base import HostResolver, ResolverResult
from hosts.types import HostEntry


class ExplicitResolver(HostResolver):
    source = "explicit"

    def resolve(self, repo_path: str) -> ResolverResult:
        config_path = os.path.join(repo_path, ".pirategoat", "config.json")
        if not os.path.isfile(config_path):
            return ResolverResult(entries=[], unresolved=[], notes={})

        try:
            with open(config_path) as f:
                data = json.load(f)
        except (json.JSONDecodeError, OSError) as err:
            return ResolverResult(
                entries=[], unresolved=[],
                notes={"parse_error": f"{config_path}: {err}"},
            )

        if not isinstance(data, dict):
            return ResolverResult(
                entries=[], unresolved=[],
                notes={"parse_error": f"{config_path}: expected object at root, got {type(data).__name__}"},
            )

        hosts_section = data.get("hosts")
        if not isinstance(hosts_section, dict):
            hosts_section = {}
        runtime_hosts = hosts_section.get("runtime") or []

        entries: List[HostEntry] = []
        for h in runtime_hosts:
            if not isinstance(h, dict):
                return ResolverResult(
                    entries=[], unresolved=[],
                    notes={"parse_error": f"{config_path}: expected object in runtime list, got {type(h).__name__}: {h!r}"},
                )
            name = h.get("name")
            if not name or not isinstance(name, str):
                return ResolverResult(
                    entries=[], unresolved=[],
                    notes={"parse_error": f"{config_path}: missing required 'name' in host entry: {h!r}"},
                )
            raw_path = h.get("path")
            if not raw_path or not isinstance(raw_path, str):
                return ResolverResult(
                    entries=[], unresolved=[],
                    notes={"parse_error": f"{config_path}: host '{name}' missing required 'path'"},
                )
            resolved_path = os.path.abspath(os.path.join(repo_path, raw_path))
            if not os.path.exists(resolved_path):
                return ResolverResult(
                    entries=[], unresolved=[],
                    notes={"parse_error": f"{config_path}: host '{name}' path does not exist: {resolved_path}"},
                )
            if not os.path.isdir(resolved_path):
                return ResolverResult(
                    entries=[], unresolved=[],
                    notes={"parse_error": f"{config_path}: host '{name}' path is not a directory: {resolved_path}"},
                )
            if self._is_inside_repo(resolved_path, repo_path):
                return ResolverResult(
                    entries=[], unresolved=[],
                    notes={"skipped": f"{config_path}: host '{name}' path is inside reviewed repo: {resolved_path}"},
                )
            entries.append(HostEntry(
                name=name,
                kind="runtime-host",
                path=resolved_path,
                source=self.source,
                version=h.get("version"),
                confidence="high",
            ))
        return ResolverResult(entries=entries, unresolved=[], notes={})

    @staticmethod
    def _is_inside_repo(path: str, repo_path: str) -> bool:
        resolved_path = os.path.realpath(path)
        resolved_repo = os.path.realpath(repo_path)
        try:
            return os.path.commonpath([resolved_path, resolved_repo]) == resolved_repo
        except ValueError:
            return False
