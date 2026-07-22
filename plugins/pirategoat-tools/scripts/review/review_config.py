#!/usr/bin/env python3
"""Repo-contributed review configuration from .pirategoat/config.json.

Reads the OPTIONAL ``review`` section of the reviewed repository's
``.pirategoat/config.json`` and normalizes it into a structure the pipeline
carries in ``review-context.json`` under the ``review_config`` key. Two
capabilities are declared here by the repo under review:

- ``rules[]``  - regression-seeded review checklists that pirategoat's existing
  reviewer agents read and apply (scoped by applicability). See bootstrap's
  REPO REVIEW RULES section.
- ``reviewers[]`` - self-contained, pirategoat-agnostic reviewer prompts that a
  generic adapter agent runs and normalizes into the standard findings format.

This is a SIBLING reader to hosts/resolvers/explicit.py, not an extension of it:
the host-context resolver chain must keep returning HostEntry records, so review
configuration is parsed separately and kept on its own context key.

Fault tolerance mirrors ExplicitResolver: a malformed file or a bad entry never
raises to the caller. Invalid entries are dropped and recorded in
``diagnostics`` so a review always proceeds.
"""

import json
import os
import re
from typing import Any, Dict, List

CONFIG_RELPATH = os.path.join(".pirategoat", "config.json")

DEFAULT_EXECUTION = "inline"
DEFAULT_CHANNEL = "blocking"
_VALID_EXECUTIONS = {"inline", "isolated"}
_VALID_CHANNELS = {"blocking", "advisory"}

# Complexity caps for repo-supplied path globs. Patterns come from the reviewed
# repo's config (semi-trusted), and glob_match runs a translated regex against
# every changed file. Chained ``**`` can drive catastrophic backtracking, so an
# over-complex pattern is rejected (fails closed — the rule/reviewer simply does
# not match). Real globs (``includes/**/*.php``) are far below these limits.
_MAX_GLOB_LEN = 256
_MAX_GLOB_STARS = 20


def empty_config() -> Dict[str, Any]:
    """The neutral result: no repo review config present."""
    return {
        "defaults": {"execution": DEFAULT_EXECUTION, "channel": DEFAULT_CHANNEL},
        "rules": [],
        "reviewers": [],
        "diagnostics": [],
    }


def load_review_config(repo_path: str) -> Dict[str, Any]:
    """Read + validate the ``review`` section of ``.pirategoat/config.json``.

    Returns a normalized dict (always the :func:`empty_config` shape) so callers
    never branch on absence. Never raises for repo-provided input.
    """
    result = empty_config()
    config_path = os.path.join(repo_path, CONFIG_RELPATH)
    # Guard the config path itself against a committed symlink that escapes the
    # repo (nested rule/reviewer paths get the same _path_inside_repo check).
    if not os.path.isfile(config_path) or not _path_inside_repo(config_path, repo_path):
        return result

    try:
        with open(config_path) as f:
            data = json.load(f)
    except (json.JSONDecodeError, OSError) as err:
        result["diagnostics"].append(f"{CONFIG_RELPATH}: parse error: {err}")
        return result

    if not isinstance(data, dict):
        result["diagnostics"].append(
            f"{CONFIG_RELPATH}: expected object at root, got {type(data).__name__}"
        )
        return result

    review = data.get("review")
    if review is None:
        return result
    if not isinstance(review, dict):
        result["diagnostics"].append(
            f"{CONFIG_RELPATH}: 'review' must be an object, got {type(review).__name__}"
        )
        return result

    defaults = review.get("defaults")
    if isinstance(defaults, dict):
        execution = defaults.get("execution")
        if execution in _VALID_EXECUTIONS:
            result["defaults"]["execution"] = execution
        channel = defaults.get("channel")
        if channel in _VALID_CHANNELS:
            result["defaults"]["channel"] = channel

    diagnostics = result["diagnostics"]
    seen_rule_ids: set = set()
    seen_reviewer_ids: set = set()

    for raw in _as_list(review.get("rules")):
        entry = _normalize_rule(raw, repo_path, result["defaults"], seen_rule_ids, diagnostics)
        if entry is not None:
            result["rules"].append(entry)

    for raw in _as_list(review.get("reviewers")):
        entry = _normalize_reviewer(
            raw, repo_path, result["defaults"], seen_reviewer_ids, diagnostics
        )
        if entry is not None:
            result["reviewers"].append(entry)

    return result


# ---------------------------------------------------------------------------
# Normalization helpers
# ---------------------------------------------------------------------------

def _as_list(value: Any) -> List[Any]:
    return value if isinstance(value, list) else []


def _normalize_rule(raw, repo_path, defaults, seen_ids, diagnostics):
    kind = "rule"
    if not isinstance(raw, dict):
        diagnostics.append(f"{kind}: expected object, got {type(raw).__name__}")
        return None

    rid = _valid_id(raw.get("id"), kind, seen_ids, diagnostics)
    if rid is None:
        return None

    resolved = _resolve_repo_file(raw.get("path"), repo_path, kind, rid, "path", diagnostics)
    if resolved is None:
        return None
    rel_path, abs_path = resolved

    channel = _valid_channel(raw.get("channel"), defaults["channel"], kind, rid, diagnostics)

    return {
        "id": rid,
        "path": rel_path,
        "resolved_path": abs_path,
        "applies_to": _normalize_applies_to(raw.get("applies_to")),
        "channel": channel,
    }


def _normalize_reviewer(raw, repo_path, defaults, seen_ids, diagnostics):
    kind = "reviewer"
    if not isinstance(raw, dict):
        diagnostics.append(f"{kind}: expected object, got {type(raw).__name__}")
        return None

    rid = _valid_id(raw.get("id"), kind, seen_ids, diagnostics)
    if rid is None:
        return None

    resolved = _resolve_repo_file(raw.get("ref"), repo_path, kind, rid, "ref", diagnostics)
    if resolved is None:
        return None
    rel_ref, abs_ref = resolved

    label = raw.get("label")
    if not isinstance(label, str) or not label.strip():
        label = rid
    label = label.strip()

    channel = _valid_channel(raw.get("channel"), defaults["channel"], kind, rid, diagnostics)

    execution = raw.get("execution")
    if execution not in _VALID_EXECUTIONS:
        if execution is not None:
            diagnostics.append(
                f"{kind} '{rid}': invalid execution {execution!r}; using {defaults['execution']}"
            )
        execution = defaults["execution"]

    model = raw.get("model")
    if model is not None and not isinstance(model, str):
        diagnostics.append(f"{kind} '{rid}': model must be a string; ignoring {model!r}")
        model = None

    return {
        "id": rid,
        "label": label,
        "ref": rel_ref,
        "resolved_ref": abs_ref,
        "applies_to": _normalize_applies_to(raw.get("applies_to")),
        "channel": channel,
        "execution": execution,
        "model": model,
    }


def _valid_id(value, kind, seen_ids, diagnostics):
    if not isinstance(value, str) or not value:
        diagnostics.append(f"{kind}: missing or non-string 'id'")
        return None
    if not all(c.isalnum() or c == "-" for c in value):
        diagnostics.append(f"{kind} '{value}': id must be kebab-case alphanumeric")
        return None
    if value in seen_ids:
        diagnostics.append(f"{kind} '{value}': duplicate id, skipping")
        return None
    seen_ids.add(value)
    return value


def _valid_channel(value, default, kind, rid, diagnostics):
    if value in _VALID_CHANNELS:
        return value
    if value is not None:
        diagnostics.append(f"{kind} '{rid}': invalid channel {value!r}; using {default}")
    return default


def _resolve_repo_file(raw_path, repo_path, kind, rid, field, diagnostics):
    """Resolve a repo-relative file path; must exist and be INSIDE the repo."""
    if not isinstance(raw_path, str) or not raw_path:
        diagnostics.append(f"{kind} '{rid}': missing or non-string '{field}'")
        return None
    abs_path = os.path.abspath(os.path.join(repo_path, raw_path))
    if not _path_inside_repo(abs_path, repo_path):
        diagnostics.append(f"{kind} '{rid}': {field} escapes the repo: {raw_path}")
        return None
    if not os.path.isfile(abs_path):
        diagnostics.append(f"{kind} '{rid}': {field} not found or not a file: {raw_path}")
        return None
    rel_path = os.path.relpath(abs_path, os.path.realpath(repo_path))
    return rel_path, abs_path


def _normalize_applies_to(raw) -> Dict[str, List[str]]:
    out = {"domains": [], "agents": [], "paths": []}
    if not isinstance(raw, dict):
        return out
    for key in out:
        value = raw.get(key)
        if isinstance(value, list):
            out[key] = [v for v in value if isinstance(v, str) and v]
    return out


def _path_inside_repo(path: str, repo_path: str) -> bool:
    resolved_path = os.path.realpath(path)
    resolved_repo = os.path.realpath(repo_path)
    try:
        return os.path.commonpath([resolved_path, resolved_repo]) == resolved_repo
    except ValueError:
        return False


# ---------------------------------------------------------------------------
# Applicability primitives (shared by bootstrap rule-injection and plan_dispatch
# reviewer expansion — single source of truth so the two consumers never drift)
# ---------------------------------------------------------------------------

def _glob_to_regex(pattern: str) -> str:
    """Translate a repo-relative glob to a regex.

    ``**`` matches any number of path segments (including none), ``*`` matches
    within a segment (does not cross ``/``), ``?`` one non-slash char. Kept
    deliberately conventional so ``includes/**`` and ``**/*.php`` behave the way
    the rule authors expect.
    """
    i, out = 0, ["^"]
    n = len(pattern)
    while i < n:
        if pattern[i:i + 3] == "**/":
            out.append("(?:.*/)?")
            i += 3
        elif pattern[i:i + 2] == "**":
            out.append(".*")
            i += 2
        elif pattern[i] == "*":
            out.append("[^/]*")
            i += 1
        elif pattern[i] == "?":
            out.append("[^/]")
            i += 1
        else:
            out.append(re.escape(pattern[i]))
            i += 1
    out.append("$")
    return "".join(out)


def glob_match(pattern: str, path: str) -> bool:
    """True if ``path`` (repo-relative, forward slashes) matches ``pattern``.

    Over-complex patterns (length or wildcard count beyond the caps) are treated
    as non-matching to bound regex backtracking against semi-trusted input.
    """
    if not pattern or not isinstance(path, str):
        return False
    if len(pattern) > _MAX_GLOB_LEN or pattern.count("*") > _MAX_GLOB_STARS:
        return False
    try:
        return bool(re.match(_glob_to_regex(pattern), path))
    except re.error:
        return False


def any_glob_match(patterns, paths) -> bool:
    """True if any pattern matches any path."""
    return any(glob_match(p, f) for p in (patterns or []) for f in (paths or []))


def rule_applies_to_agent(applies_to, agent_name, agent_domains, scope_files) -> bool:
    """Does a repo RULE apply to the reviewer agent currently being bootstrapped?

    A rule with no ``applies_to`` constraints applies to every agent (an
    intentionally broad, discouraged default). Otherwise it applies when ANY of
    its declared axes match: the agent name, one of the agent's domains, or a
    changed file in the agent's scope.
    """
    if not isinstance(applies_to, dict):
        return True
    agents = applies_to.get("agents") or []
    domains = applies_to.get("domains") or []
    paths = applies_to.get("paths") or []
    if not agents and not domains and not paths:
        return True
    if agent_name and agent_name in agents:
        return True
    if domains and set(domains) & set(agent_domains or []):
        return True
    if paths and any_glob_match(paths, scope_files):
        return True
    return False


def reviewer_applies_to_diff(applies_to, domains_with_files, changed_files) -> bool:
    """Should a repo REVIEWER dispatch for this diff?

    A reviewer with no ``applies_to`` constraints always dispatches. Otherwise it
    dispatches when one of its declared domains has changed files, or one of its
    path globs matches a changed file.
    """
    if not isinstance(applies_to, dict):
        return True
    domains = applies_to.get("domains") or []
    paths = applies_to.get("paths") or []
    if not domains and not paths:
        return True
    if domains and set(domains) & set(domains_with_files or []):
        return True
    if paths and any_glob_match(paths, changed_files):
        return True
    return False
