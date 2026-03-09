"""Rule registry assembly and detector wrapping."""

from __future__ import annotations

import re


def _compile_patterns(pattern_list):
    """Compile a list of regex strings into `re.Pattern` objects."""
    return [re.compile(pattern) for pattern in pattern_list]


def _wrap_custom_detector(detect_fn, message):
    """Normalize custom detectors to the legacy `(bool, message)` interface."""
    def detect(command, tool_name, tool_input, config):
        result = detect_fn(command, tool_name, tool_input, config)
        if isinstance(result, tuple):
            detected, custom_message = result
            if detected:
                return True, custom_message or message
            return False, None
        if result:
            return True, message
        return False, None
    return detect


def _make_detector(compiled):
    """Generate a detector from compiled declarative rule data."""
    patterns = compiled.get("patterns", [])
    pattern_groups = compiled.get("pattern_groups", [])
    require = compiled.get("require", [])
    exclude = compiled.get("exclude", [])
    message = compiled["message"]

    def detect(command, tool_name, tool_input, config):
        for pattern in exclude:
            if pattern.search(command):
                return False, None

        matched = False
        for pattern in patterns:
            if pattern.search(command):
                matched = True
                break
        if not matched:
            for group in pattern_groups:
                if all(pattern.search(command) for pattern in group):
                    matched = True
                    break
        if not matched:
            return False, None
        for pattern in require:
            if not pattern.search(command):
                return False, None
        return True, message

    return detect


def build_registry(rules):
    """Compile rules into the per-tool evaluation registry."""
    rules_by_tool = {}
    for rule_id, rule in rules.items():
        if "detect" in rule:
            detect_fn = _wrap_custom_detector(rule["detect"], rule["message"])
        else:
            compiled = {"message": rule["message"]}
            if "patterns" in rule:
                compiled["patterns"] = _compile_patterns(rule["patterns"])
            if "pattern_groups" in rule:
                compiled["pattern_groups"] = [
                    _compile_patterns(group) for group in rule["pattern_groups"]
                ]
            if "require" in rule:
                compiled["require"] = _compile_patterns(rule["require"])
            if "exclude" in rule:
                compiled["exclude"] = _compile_patterns(rule["exclude"])
            detect_fn = _make_detector(compiled)

        rule["_detect"] = detect_fn
        tier = rule["tier"]
        for tool in rule["tools"]:
            rules_by_tool.setdefault(tool, []).append((rule_id, tier, detect_fn))
    return rules_by_tool


def is_allowlisted(command, allowlist_patterns, disabled=None):
    """Check if command matches any allowlist pattern."""
    if disabled is None:
        disabled = set()
    for rule_id, pattern in allowlist_patterns:
        if rule_id not in disabled and pattern.search(command):
            return True
    return False
