# yoloing-safe — Agent Instructions

## What This Plugin Does

PreToolUse safety hook for Claude Code's YOLO mode (`--dangerously-skip-permissions`). It pattern-matches tool calls against `RULE_REGISTRY` and either blocks (exit 2) or asks for confirmation (JSON `permissionDecision: "ask"`).

## Key Files

| File | Role |
|------|------|
| `scripts/pre-tool-use-safety.py` | The hook script. Contains `RULE_REGISTRY` — the canonical list of all safety rules, including example commands for e2e test generation. |
| `hooks.json` | Claude Code hook registration (PreToolUse event wiring). |
| `tests/e2e/test-fixtures.json` | Optional e2e test overrides (tool, branch, subagent, pattern, prompt). Most rules need no entry. |

## Testing

### Unit + Integration Tests

Fast, deterministic, no API calls. Run after any change to the hook script or test scenarios.

```bash
# Full suite (unit + integration + evasion + scenario regression + benchmark)
pytest plugins/yoloing-safe/tests/ -v

# Quick smoke test (unit tests only, no subprocess)
pytest plugins/yoloing-safe/tests/test_safety_hook.py -k "not TestIntegration and not TestEvasion and not TestBlocked and not TestAllowed and not TestDisable" -v
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

## After Modifying Rules

When you add, remove, rename, or change the tier of a rule in `RULE_REGISTRY`:

### 1. Include example commands in the rule tuple

Every rule must have a 5th element: a list of example commands that trigger the rule. The e2e generator uses these to create test cases automatically.

```python
("my_new_rule", "block", detect_my_new_rule, {"Bash"},
    ["dangerous-command --flag"]),
```

For non-Bash rules, the example is a file path (e.g., `"./.env"` for Read, `"~/.bashrc"` for Write).

### 2. Add overrides if needed (optional)

If the rule needs non-default test config (tool override, branch, subagent, custom pattern or prompt), add an entry in `tests/e2e/test-fixtures.json` under `"overrides"`. Most rules need no entry.

### 3. Update unit/integration tests

Add or update test cases in `tests/test_safety_hook.py` and the scenario files (`tests/scenarios/blocked.json`, `tests/scenarios/allowed.json`, `tests/scenarios/evasion.json`).

### 4. Run unit tests

```bash
pytest plugins/yoloing-safe/tests/ -v
```

### 5. Regenerate e2e test cases

```bash
cd plugins/yoloing-safe/tests/e2e
make generate
```

The generator reads examples directly from `RULE_REGISTRY` and merges with optional overrides from `test-fixtures.json`. A rule with no examples fails the generator.

**Never edit `test-cases.json` directly.** It is generated and will be overwritten.

### 6. Rebuild and run e2e

```bash
make build
make run
```

### Quick staleness check

```bash
make check
```

## Which Tests to Run

| What changed | Run |
|---|---|
| `scripts/pre-tool-use-safety.py` (detection logic) | `pytest plugins/yoloing-safe/tests/ -v` |
| `scripts/pre-tool-use-safety.py` (RULE_REGISTRY) | `pytest plugins/yoloing-safe/tests/ -v` then `cd tests/e2e && make generate` |
| `RULE_REGISTRY` (add/remove/rename rule) | `pytest plugins/yoloing-safe/tests/test_meta.py -v` (catches desync immediately) |
| `BLOCK_MESSAGES` / `ASK_MESSAGES` | `pytest plugins/yoloing-safe/tests/test_meta.py -v` |
| `ALLOWLIST_PATTERNS` | `pytest plugins/yoloing-safe/tests/test_meta.py -v` |
| `tests/scenarios/*.json` | `pytest plugins/yoloing-safe/tests/ -v` |
| `tests/e2e/test-fixtures.json` (overrides) | `cd tests/e2e && make generate` |

## Rule Templates

### Adding a New Rule

Copy-paste template. Replace `my_rule` with your rule_id (snake_case).

**1. Add regex** (in "Pre-compiled Regex Patterns" section of `pre-tool-use-safety.py`):

```python
_RE_MY_RULE = re.compile(r"my_pattern")
```

**2. Add message** (in `BLOCK_MESSAGES` or `ASK_MESSAGES`):

```python
# Block tier:
"my_rule": "Guidance message telling Claude what to do instead."
# Ask tier:
"my_rule": "Explanation of the risk. Confirm this is intentional."
```

**3. Add detection function** (in "Detection Functions" section):

```python
def detect_my_rule(command, tool_name, tool_input, config):
    """One-line description of what this detects."""
    if _RE_MY_RULE.search(command):
        return True, BLOCK_MESSAGES["my_rule"]  # or ASK_MESSAGES
    return False, None
```

**4. Add to RULE_REGISTRY** (maintain block-before-ask ordering):

```python
("my_rule", "block", detect_my_rule, {"Bash"},
    ["example-dangerous-command"]),
```

**5. Add unit test class** (in `test_safety_hook.py`):

Test class naming convention: `Test{PascalCaseRuleId}` (e.g., `TestMyRule`).

```python
class TestMyRule:
    @pytest.mark.parametrize("command", [
        "example that should trigger",
    ])
    def test_detected(self, hook, command):
        cmd = hook.normalize_command(command)
        detected, msg = hook.detect_my_rule(cmd, "Bash", {}, hook.DEFAULTS)
        assert detected is True
        assert msg is not None

    @pytest.mark.parametrize("command", [
        "similar but safe command",
    ])
    def test_not_detected(self, hook, command):
        cmd = hook.normalize_command(command)
        detected, _ = hook.detect_my_rule(cmd, "Bash", {}, hook.DEFAULTS)
        assert detected is False
```

**6. Add scenario entries:**

- Block-tier: add to `scenarios/blocked.json`
- Ask-tier: add to `scenarios/asked.json`
- Add safe variant to `scenarios/allowed.json`
- Block-tier rules: add at least one evasion to `scenarios/evasion.json` (with `rule_id` field)

**7. Run meta-tests to verify sync:**

```bash
pytest plugins/yoloing-safe/tests/test_meta.py -v
```

If meta-tests pass, all structural invariants are satisfied.

### Removing a Rule

1. Remove the tuple from `RULE_REGISTRY`
2. Remove the detection function (`detect_xxx`)
3. Remove associated `_RE_` regex patterns (verify not shared with other rules)
4. Remove the message from `BLOCK_MESSAGES` or `ASK_MESSAGES`
5. Remove any `ALLOWLIST_PATTERNS` entries with this rule_id
6. Remove scenarios from `blocked.json`, `asked.json`, `allowed.json`, `evasion.json`
7. Remove the unit test class from `test_safety_hook.py`
8. Remove any override from `tests/e2e/test-fixtures.json`
9. Run meta-tests: `pytest plugins/yoloing-safe/tests/test_meta.py -v`
10. Regenerate e2e: `cd tests/e2e && make generate`

### Renaming a Rule

Rename in ALL of these locations (meta-tests will catch any you miss):

1. `RULE_REGISTRY` tuple — first element
2. `BLOCK_MESSAGES` or `ASK_MESSAGES` — dict key
3. `ALLOWLIST_PATTERNS` — rule_id field (if applicable)
4. Detection function return — message dict key reference
5. `blocked.json` / `asked.json` / `allowed.json` — `category` field
6. `evasion.json` — `rule_id` field
7. `test-fixtures.json` — override key (if applicable)
8. Unit test class docstring (if it references the rule_id)
