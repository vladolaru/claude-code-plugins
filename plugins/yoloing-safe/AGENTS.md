# yoloing-safe — Agent Instructions

You maintain a PreToolUse safety hook for Claude Code's YOLO mode (`--dangerously-skip-permissions`). The hook pattern-matches tool calls against a `RULES` dict and either blocks (exit 2) or asks for confirmation (JSON `permissionDecision: "ask"`).

The hook script (`scripts/pre-tool-use-safety.py`) is the single source of truth. Every change to rules, detection, or allowlists happens there. This document tells you how.

## Key Files

| File | Role |
|------|------|
| `scripts/pre-tool-use-safety.py` | The hook script. Single source of truth — contains `RULES` dict, detection functions, allowlist, and all runtime logic. |
| `hooks/hooks.json` | Claude Code hook registration (PreToolUse event wiring). |
| `tests/test_safety_hook.py` | Unit + integration tests. Uses `get_detect(hook, rule_id)` helper to call detection functions. |
| `tests/test_meta.py` | Structural invariant checks — catches drift between RULES, allowlist, and scenario files. |
| `tests/scenarios/blocked.json` | Block-tier regression scenarios (must exit 2). |
| `tests/scenarios/asked.json` | Ask-tier regression scenarios (must return ask JSON). |
| `tests/scenarios/allowed.json` | Safe-command scenarios (must exit 0 silently). |
| `tests/scenarios/evasion.json` | Adversarial bypass attempts (must be caught). Each entry has a `rule_id` field. |
| `tests/e2e/test-fixtures.json` | Optional e2e test overrides (tool, branch, subagent, pattern, prompt). Most rules need no entry. |
| `CHANGELOG.md` | Version history — every behavior change needs an entry. |

## Critical Constraints

- `test-cases.json` is generated — edit `test-fixtures.json` or `RULES` examples instead, then run `make generate`.
- Block-tier rules come before ask-tier rules in the `RULES` dict.

## RULES Dict Structure

Each rule in `RULES` is a dict with these keys:

| Key | Required | Type | Description |
|-----|----------|------|-------------|
| `tier` | yes | `"block"` or `"ask"` | Block exits 2; ask returns JSON for user confirmation. |
| `tools` | yes | `set` | Tool names this rule applies to: `{"Bash"}`, `{"Read", "Write", "Edit"}`, etc. |
| `message` | yes | `str` | Guidance for Claude. Block: guide toward safer alternatives. Ask: explain the risk and request confirmation. |
| `examples` | yes | `list[str]` | Example commands (Bash) or file paths (Read/Write/Edit) for e2e test generation. |
| `detect` | custom rules | `callable` | `(command, tool_name, tool_input, config) -> (bool, str\|None)`. Omit for declarative rules. |
| `patterns` | declarative | `list[str]` | Regex strings — any match triggers (OR). |
| `pattern_groups` | declarative | `list[list[str]]` | AND-groups — all patterns in a group must match; groups are OR'd. |
| `require` | declarative | `list[str]` | Additional patterns that must ALL match (AND with patterns/pattern_groups). |
| `exclude` | declarative | `list[str]` | If any match, rule does NOT trigger — checked first. |

**Either `detect` or at least one of `patterns`/`pattern_groups` must be present.** For declarative rules (no `detect`), `build_registry()` auto-generates a detection function from the pattern keys.

## Rule Types: Declarative vs Custom

**Decision test:** Can the rule be expressed entirely as "match X, require Y, exclude Z" against the command string? Use declarative. If you need conditionals, config lookups, `tool_input` fields, or per-segment logic, use custom.

**Declarative** — the rule is regex matching against the command:

```python
"git_force_push": {
    "tier": "ask",
    "tools": {"Bash"},
    "patterns": [r"^git push\b"],
    "require": [r"(--force\b|-f\b)"],
    "exclude": [r"--force-with-lease", r"--force-if-includes"],
    "message": "Force push rewrites remote history. Use --force-with-lease instead.",
    "examples": ["git push --force origin hotfix/fix-arena"],
},
```

**Custom** — the rule needs procedural logic (conditional checks, config-dependent patterns, per-segment chain analysis, tool_input inspection):

```python
def detect_my_rule(command, tool_name, tool_input, config):
    """One-line description."""
    if _RE_MY_PATTERN.search(command):
        return True, RULES["my_rule"]["message"]
    return False, None

# Then in RULES:
"my_rule": {
    "tier": "block",
    "tools": {"Bash"},
    "detect": detect_my_rule,
    "message": "Guidance message.",
    "examples": ["dangerous-command --flag"],
},
```

Detection function signature: `(command: str, tool_name: str, tool_input: dict, config: dict) -> (bool, str | None)`.
- `command` is the normalized Bash command (empty string for Read/Write/Edit).
- `tool_input` has `file_path` (Read/Write/Edit) or `command` (Bash).
- `config` is the merged user config (credential patterns, zero-access paths, disabled rules).
- Return `(True, message)` to trigger, `(False, None)` to pass.

## Testing

### Unit + Integration Tests

Fast, deterministic, no API calls. Run after any change to the hook script or test scenarios.

```bash
pytest plugins/yoloing-safe/tests/ -v
```

See `tests/TESTING.md` for the full breakdown of test layers, adding tests, and profiling.

### E2E Tests (Docker)

Runs Claude Code in YOLO mode inside a Docker container against crafted prompts. Makes real API calls — costs money and takes 5-10 minutes. Don't run casually.

```bash
cd plugins/yoloing-safe/tests/e2e
make build        # Build Docker image
make auth         # One-time: run 'claude auth login' inside container
make run          # Run full suite (33 tests, 8 sessions)
make run-save     # Same, but save results to ./results/
```

See `tests/e2e/README.md` for full setup, output format, and debugging.

## Which Tests to Run

| What changed | Run |
|---|---|
| Detection logic in `pre-tool-use-safety.py` | `pytest plugins/yoloing-safe/tests/ -v` |
| `RULES` dict (add/remove/rename/retier) | `pytest plugins/yoloing-safe/tests/ -v` then `cd tests/e2e && make generate` |
| `ALLOWLIST_PATTERNS` | `pytest plugins/yoloing-safe/tests/ -v` |
| `tests/scenarios/*.json` | `pytest plugins/yoloing-safe/tests/ -v` |
| `tests/e2e/test-fixtures.json` | `cd tests/e2e && make generate` |

**When tests fail:** Meta-test failures usually mean a scenario file is missing an entry or a rule lacks required fields — read the assertion message, fix the gap, and rerun. Unit test failures mean detection logic doesn't match expectations — adjust the regex or detection function, not the test.

## Rule Workflows

### Adding a New Rule

Before starting, read `scripts/pre-tool-use-safety.py` to understand the current rule layout, naming conventions, and where new rules should be inserted. Check that no existing rule already covers the behavior you want to detect.

**Step 1** — Add regex constants in the "Pre-compiled Regex Patterns" section (if needed).

**Step 2** — For custom rules: add a detection function in the "Detection Functions" section. See "Rule Types" above for the template and signature.

**Step 3** — Add entry to `RULES` dict. Maintain block-before-ask ordering. See "Rule Types" above for declarative and custom formats.

**Step 4 — Consider allowlist safe variants** (if the rule could false-positive on safe usage):

```python
# In ALLOWLIST_PATTERNS list:
("my_rule", re.compile(r"^my_command\b.*--dry-run")),
```

Each allowlist entry is `(rule_id, compiled_regex)`. The `rule_id` ties it to the rule — disabling a rule also disables its allowlist entries.

**Step 5 — Add unit test class** (in `test_safety_hook.py`):

Test class naming convention: `Test{PascalCaseRuleId}` (e.g., `TestMyRule`).

```python
class TestMyRule:
    @pytest.mark.parametrize("command", [
        "example that should trigger",
    ])
    def test_detected(self, hook, command):
        cmd = hook.normalize_command(command)
        detected, msg = get_detect(hook, "my_rule")(cmd, "Bash", {}, hook.DEFAULTS)
        assert detected is True
        assert msg is not None

    @pytest.mark.parametrize("command", [
        "similar but safe command",
    ])
    def test_not_detected(self, hook, command):
        cmd = hook.normalize_command(command)
        detected, _ = get_detect(hook, "my_rule")(cmd, "Bash", {}, hook.DEFAULTS)
        assert detected is False
```

For non-Bash rules (Read/Write/Edit), pass the file path via `tool_input`:
```python
detected, msg = get_detect(hook, "my_rule")("", "Read", {"file_path": "/path/to/file"}, hook.DEFAULTS)
```

**Step 6 — Add scenario entries:**

- Block-tier rule: add to `scenarios/blocked.json` AND at least one entry to `scenarios/evasion.json` (with `rule_id` field)
- Ask-tier rule: add to `scenarios/asked.json`
- Both tiers: add a safe variant to `scenarios/allowed.json`

Scenario entry format:
```json
{"tool_name": "Bash", "tool_input": {"command": "example-command"}, "category": "my_rule"}
```

Evasion entry format:
```json
{"command": "evasion attempt", "should": "block", "technique": "description", "rule_id": "my_rule"}
```

**Step 7** — Run the [After Any Rule Change](#after-any-rule-change) steps.

### Removing a Rule

1. Remove the entry from `RULES` dict
2. Remove the detection function (`detect_xxx`) — custom rules only
3. Remove associated `_RE_` regex constants (verify not shared with other rules)
4. Remove any `ALLOWLIST_PATTERNS` entries with this rule_id
5. Remove scenarios from `blocked.json`, `asked.json`, `allowed.json`, `evasion.json`
6. Remove the unit test class from `test_safety_hook.py`
7. Remove any override from `tests/e2e/test-fixtures.json`
8. Run the [After Any Rule Change](#after-any-rule-change) steps

### Renaming a Rule

Rename in ALL of these locations (meta-tests catch most misses):

1. `RULES` dict — key
2. Detection function name and its `RULES["old_id"]` reference (custom rules)
3. `ALLOWLIST_PATTERNS` — rule_id field (if applicable)
4. `blocked.json` / `asked.json` / `allowed.json` — `category` field
5. `evasion.json` — `rule_id` field
6. `test-fixtures.json` — override key (if applicable)
7. Unit test class name and docstring
8. Run the [After Any Rule Change](#after-any-rule-change) steps

### Changing a Rule's Tier

1. Change `"tier"` value in `RULES` dict (`"block"` ↔ `"ask"`)
2. Move scenario entries between `blocked.json` and `asked.json`
3. If promoting to block: add evasion entries to `evasion.json` (required for block-tier rules)
4. If demoting to ask: evasion entries can stay (they test `"ask_or_block"`) or be removed
5. Update the `message` to match the new tier's tone (see `message` description in RULES Dict Structure)
6. Run the [After Any Rule Change](#after-any-rule-change) steps

### After Any Rule Change

Run these steps after completing any rule workflow (add, remove, rename, retier):

1. `pytest plugins/yoloing-safe/tests/test_meta.py -v` — verify structural invariants (rule has message, examples, scenario coverage)
2. `pytest plugins/yoloing-safe/tests/ -v` — full test suite
3. `cd plugins/yoloing-safe/tests/e2e && make generate` — regenerate e2e test cases from `RULES` examples + `test-fixtures.json` overrides
4. Update `CHANGELOG.md` and bump version in `../../.claude-plugin/marketplace.json` (per root AGENTS.md versioning rules)
