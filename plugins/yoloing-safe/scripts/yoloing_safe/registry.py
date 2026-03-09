"""Rule registry assembly and detector wrapping."""

from __future__ import annotations

import re

from .context import EvalContext


def _compile_patterns(pattern_list):
    """Compile a list of regex strings into `re.Pattern` objects."""
    return [re.compile(pattern) for pattern in pattern_list]


def _wrap_custom_detector(detect_fn, message):
    """Wrap a ctx-aware custom detector to the legacy test interface.

    Custom detectors have signature ``detect(ctx) -> bool | (bool, msg)``.
    The wrapper returns ``(bool, message)`` and creates a fresh EvalContext
    per call so tests can call ``_detect(command, tool_name, tool_input, config)``.
    """
    def _detect(command, tool_name, tool_input, config):
        ctx = EvalContext(tool_name, tool_input, config, command)
        result = detect_fn(ctx)
        if isinstance(result, tuple):
            detected, custom_message = result
            if detected:
                return True, custom_message or message
            return False, None
        if result:
            return True, message
        return False, None
    return _detect


def _wrap_custom_detector_ctx(detect_fn, message):
    """Wrap a ctx-aware custom detector for runtime use.

    Returns ``(bool, message)`` from a shared EvalContext.
    """
    def detect(ctx):
        result = detect_fn(ctx)
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
    """Generate a legacy detector from compiled declarative rule data."""
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


def _adapt_for_ctx(legacy_detect_fn):
    """Adapt a legacy declarative detector to accept EvalContext."""
    def detect(ctx):
        return legacy_detect_fn(ctx.command, ctx.tool_name, ctx.tool_input, ctx.config)
    return detect


def build_registry(rules):
    """Compile rules into the per-tool evaluation registry.

    Each rule gets two detector forms:
    - ``_detect(command, tool_name, tool_input, config)`` — legacy test interface
    - runtime detector in ``RULES_BY_TOOL`` — takes ``EvalContext``
    """
    rules_by_tool = {}
    for rule_id, rule in rules.items():
        if "detect" in rule:
            legacy_fn = _wrap_custom_detector(rule["detect"], rule["message"])
            runtime_fn = _wrap_custom_detector_ctx(rule["detect"], rule["message"])
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
            legacy_fn = _make_detector(compiled)
            runtime_fn = _adapt_for_ctx(legacy_fn)

        rule["_detect"] = legacy_fn
        tier = rule["tier"]
        for tool in rule["tools"]:
            rules_by_tool.setdefault(tool, []).append((rule_id, tier, runtime_fn))
    return rules_by_tool


def is_allowlisted(command, allowlist_patterns, disabled=None):
    """Check if command matches any allowlist pattern."""
    if disabled is None:
        disabled = set()
    for rule_id, pattern in allowlist_patterns:
        if rule_id not in disabled and pattern.search(command):
            return True
    return False


# ---------------------------------------------------------------------------
# Rule builders — reduce boilerplate and enforce required fields
# ---------------------------------------------------------------------------

def block_rule(*, tools, message, examples, detect=None, patterns=None,
               pattern_groups=None, require=None, exclude=None):
    """Build a block-tier rule spec dict."""
    return _build_rule("block", tools=tools, message=message, examples=examples,
                       detect=detect, patterns=patterns, pattern_groups=pattern_groups,
                       require=require, exclude=exclude)


def ask_rule(*, tools, message, examples, detect=None, patterns=None,
             pattern_groups=None, require=None, exclude=None):
    """Build an ask-tier rule spec dict."""
    return _build_rule("ask", tools=tools, message=message, examples=examples,
                       detect=detect, patterns=patterns, pattern_groups=pattern_groups,
                       require=require, exclude=exclude)


def _build_rule(tier, *, tools, message, examples, detect=None, patterns=None,
                pattern_groups=None, require=None, exclude=None):
    """Assemble a rule spec dict with validation."""
    spec = {
        "tier": tier,
        "tools": tools if isinstance(tools, set) else set(tools),
        "message": message,
        "examples": examples,
    }
    if detect is not None:
        spec["detect"] = detect
    if patterns is not None:
        spec["patterns"] = patterns
    if pattern_groups is not None:
        spec["pattern_groups"] = pattern_groups
    if require is not None:
        spec["require"] = require
    if exclude is not None:
        spec["exclude"] = exclude
    return spec
