# Testing — yoloing-safe

## Architecture

The test suite has four layers:

1. **Unit tests** — Parameterized tests for each detection function. Each function gets both positive (should detect) and negative (should not detect) cases. Tests call the function directly via module import.

2. **Integration tests** — Run the actual script via `subprocess.run` with JSON on stdin. Verify exit codes (0 = allow, 2 = block), stderr messages (block tier), and JSON output with `permissionDecision: "ask"` (ask tier).

3. **Adversarial evasion suite** — 15 bypass techniques (path prefix, alias bypass, whitespace, command chaining, xargs, find -delete, etc.) loaded from `scenarios/evasion.json`. All must produce exit code 2.

4. **Scenario regression suite** — `scenarios/blocked.json` (26 scenarios) and `scenarios/allowed.json` (26 scenarios) loaded and run via subprocess. Comprehensive regression coverage across all categories.

## Safety Rule

Tests never execute dangerous commands. The hook script receives command strings as JSON data for pattern matching — the commands are never passed to a shell. No test may import `os.system` or call `subprocess.run` with a command from scenario files.

## Running Tests

```bash
# Full suite
pytest plugins/yoloing-safe/tests/ -v

# Unit tests only (fast, no subprocess)
pytest plugins/yoloing-safe/tests/test_safety_hook.py -k "not TestIntegration and not TestEvasion and not TestBlocked and not TestAllowed and not TestDisable" -v

# Integration tests
pytest plugins/yoloing-safe/tests/test_safety_hook.py -k "TestIntegration" -v

# Evasion suite
pytest plugins/yoloing-safe/tests/test_safety_hook.py::TestEvasionSuite -v

# Scenario regression
pytest plugins/yoloing-safe/tests/test_safety_hook.py -k "TestBlockedScenarios or TestAllowedScenarios" -v
```

## Adding Tests

### New detection function
Add a `TestXxx` class with `test_detected` and `test_not_detected` parametrized methods.

### New evasion technique
Add an entry to `scenarios/evasion.json` with `command`, `should: "block"`, and `technique`.

### New block/allow scenario
Add an entry to `scenarios/blocked.json` or `scenarios/allowed.json` with `tool_name`, `tool_input`, and `category`.

## Which tests to run after changes

| Changed file | Run |
|---|---|
| `scripts/pre-tool-use-safety.py` | `pytest plugins/yoloing-safe/tests/ -v` (full suite) |
| `tests/scenarios/*.json` | `pytest plugins/yoloing-safe/tests/ -k "TestBlockedScenarios or TestAllowedScenarios or TestEvasionSuite" -v` |
| `config/defaults.json` | No tests reference this file directly (defaults are hardcoded in script) |
