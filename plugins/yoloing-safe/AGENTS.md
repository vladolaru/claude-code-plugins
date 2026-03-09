# yoloing-safe — Agent Instructions

You maintain a PreToolUse safety hook for Claude Code's YOLO mode (`--dangerously-skip-permissions`). The hook evaluates tool calls in this order:

1. allowlist
2. block-tier rules
3. ask-tier rules
4. allow

The runtime entrypoint stays at `scripts/pre-tool-use-safety.py`, but that file is now a compatibility shim. The assembled rule registry in `scripts/yoloing_safe/rules/__init__.py` is the canonical rule order and metadata source.

## Key Files

| File | Role |
|------|------|
| `scripts/pre-tool-use-safety.py` | Runtime entrypoint and legacy compatibility surface. Re-exports `RULES`, `RULES_BY_TOOL`, `ALLOWLIST_PATTERNS`, `DEFAULTS`, and helpers for tests and e2e tooling. |
| `scripts/yoloing_safe/config.py` | Defaults, user config loading, self-protection constants, non-disableable rules. |
| `scripts/yoloing_safe/shell.py` | Command normalization, shell tokenization, heredoc stripping, segment splitting. |
| `scripts/yoloing_safe/paths.py` | Path extraction, sensitive target detection, Bash mutation target collection. |
| `scripts/yoloing_safe/registry.py` | Declarative detector compilation and custom detector wrapping. |
| `scripts/yoloing_safe/runtime.py` | Main evaluation loop and block/ask/allow output helpers. |
| `scripts/yoloing_safe/rules/__init__.py` | Canonical ordered rule assembly plus allowlist aggregation. |
| `scripts/yoloing_safe/rules/*.py` | Domain rule implementations. Add new rules here. |
| `hooks/hooks.json` | Claude Code hook registration. Do not change the entrypoint path casually. |
| `tests/test_core.py` | Core compatibility, config, allowlist, and utility tests. |
| `tests/test_rules_*.py` | Domain rule test entrypoints. |
| `tests/test_integration.py` | End-to-end subprocess regression tests. |
| `tests/test_scenarios.py` | Scenario and evasion regression suites. |
| `tests/test_meta.py` | Structural invariant checks for rules, allowlists, and scenarios. |
| `tests/_legacy_safety_hook_tests.py` | Backing module for the current split test entrypoints. Add or edit test classes here, then re-export them from the matching split suite. |
| `tests/e2e/test-fixtures.json` | Optional e2e overrides. Most rules need no entry. |
| `CHANGELOG.md` | Version history. Every behavior or shipped architecture change needs an entry. |

## Critical Constraints

- `test-cases.json` is generated. Edit `test-fixtures.json` or rule `examples`, then run `make generate`.
- Block-tier rules must stay before ask-tier rules in `scripts/yoloing_safe/rules/__init__.py`.
- Preserve the public compatibility surface exposed by `scripts/pre-tool-use-safety.py` unless you intentionally update tests, docs, and e2e tooling together.

## Rule Structure

Each assembled `RULES` entry has:

| Key | Required | Description |
|-----|----------|-------------|
| `tier` | yes | `"block"` or `"ask"` |
| `tools` | yes | Tool names the rule applies to |
| `message` | yes | Guidance for Claude |
| `examples` | yes | Example commands or file paths for e2e generation |
| `detect` | custom only | Internal detector function returning `bool` or `(bool, custom_message)` |
| `patterns` | declarative only | Regex strings, OR semantics |
| `pattern_groups` | optional | List of AND groups, OR semantics across groups |
| `require` | optional | Additional regexes that must all match |
| `exclude` | optional | Regexes that short-circuit the rule |

For declarative rules, `registry.py` compiles patterns and generates the legacy `_detect` callable. For custom rules, `registry.py` wraps the internal detector so tests still see the legacy `(detected, message)` interface via `hook.RULES[rule_id]["_detect"]`.

## Rule Types

Use a declarative rule when the behavior is fully expressible as regex match plus require/exclude conditions.

```python
"git_force_push": {
    "tier": "ask",
    "tools": {"Bash"},
    "patterns": [r"^git push\b"],
    "require": [r"(--force\b|-f\b)"],
    "exclude": [r"--force-with-lease", r"--force-if-includes"],
    "message": "Force push rewrites remote history. Use --force-with-lease instead.",
    "examples": ["git push --force origin hotfix/fix-arena"],
}
```

Use a custom rule when you need config lookups, tool-specific path handling, chain-aware logic, or anything procedural.

```python
def detect_my_rule(command, tool_name, tool_input, config):
    return bool(_RE_MY_PATTERN.search(command))

"my_rule": {
    "tier": "block",
    "tools": {"Bash"},
    "detect": detect_my_rule,
    "message": "Guidance message.",
    "examples": ["dangerous-command --flag"],
}
```

## Testing

Fast suites:

```bash
pytest plugins/yoloing-safe/tests/ -v
pytest plugins/yoloing-safe/tests/test_meta.py -v
pytest plugins/yoloing-safe/tests/benchmark_hook.py -v
```

E2E suite:

```bash
cd plugins/yoloing-safe/tests/e2e
make build
make auth
make run
```

## Which Tests to Run

| What changed | Run |
|---|---|
| Any runtime code under `scripts/` | `pytest plugins/yoloing-safe/tests/ -v` and `pytest plugins/yoloing-safe/tests/benchmark_hook.py -v` |
| `RULES` assembly or rule metadata | `pytest plugins/yoloing-safe/tests/test_meta.py -v` then `cd plugins/yoloing-safe/tests/e2e && make generate` |
| `ALLOWLIST_PATTERNS` | `pytest plugins/yoloing-safe/tests/test_meta.py -v` and the affected rule suites |
| `tests/scenarios/*.json` | `pytest plugins/yoloing-safe/tests/test_scenarios.py -v` |
| `tests/e2e/test-fixtures.json` | `cd plugins/yoloing-safe/tests/e2e && make generate` |

## Rule Workflows

### Adding a New Rule

1. Choose the domain module in `scripts/yoloing_safe/rules/`.
2. Add regex constants and helpers in that module if needed.
3. Add the rule to the module's `BLOCK_RULES`, `ASK_RULES`, or both as appropriate.
4. If the rule needs a safe variant, add it to that module's `ALLOWLIST_PATTERNS`.
5. Assemble the rule in `scripts/yoloing_safe/rules/__init__.py` in the correct global order.
6. Add a `Test{PascalCaseRuleId}` class to `tests/_legacy_safety_hook_tests.py`.
7. Re-export that class from the matching split suite (`test_rules_*.py`, `test_integration.py`, or `test_core.py`).
8. Add scenarios:
   - block rule: `scenarios/blocked.json` and at least one `scenarios/evasion.json` entry
   - ask rule: `scenarios/asked.json`
   - both: safe variant in `scenarios/allowed.json`
9. Run the [After Any Rule Change](#after-any-rule-change) steps.

### Removing a Rule

1. Remove it from the domain module.
2. Remove it from `scripts/yoloing_safe/rules/__init__.py`.
3. Remove any allowlist entries for that rule.
4. Remove scenarios from `blocked.json`, `asked.json`, `allowed.json`, and `evasion.json`.
5. Remove the unit test class from `tests/_legacy_safety_hook_tests.py`.
6. Remove its re-export from the matching split suite.
7. Remove any `tests/e2e/test-fixtures.json` override.
8. Run the [After Any Rule Change](#after-any-rule-change) steps.

### Renaming a Rule

Rename it in all of these places:

1. Domain module rule key
2. Domain module detector function name if applicable
3. `scripts/yoloing_safe/rules/__init__.py`
4. Module `ALLOWLIST_PATTERNS`
5. `blocked.json`, `asked.json`, `allowed.json`, `evasion.json`
6. `tests/e2e/test-fixtures.json`
7. Test class and any explicit references in tests
8. Run the [After Any Rule Change](#after-any-rule-change) steps

### Changing a Rule's Tier

1. Change `"tier"` in the rule definition.
2. If needed, move the rule between `BLOCK_RULES` and `ASK_RULES`.
3. Keep the global order correct in `rules/__init__.py`.
4. Move scenario entries between `blocked.json` and `asked.json`.
5. If promoting to block, add evasion scenarios.
6. Update the message tone to match the new tier.
7. Run the [After Any Rule Change](#after-any-rule-change) steps.

### After Any Rule Change

1. `pytest plugins/yoloing-safe/tests/test_meta.py -v`
2. `pytest plugins/yoloing-safe/tests/ -v`
3. `pytest plugins/yoloing-safe/tests/benchmark_hook.py -v`
4. `cd plugins/yoloing-safe/tests/e2e && make generate`
5. Update `CHANGELOG.md` and bump `.claude-plugin/marketplace.json` per the repo versioning rules
