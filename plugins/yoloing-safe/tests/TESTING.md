# Testing — yoloing-safe

## Architecture

The test suite has five layers:

1. **Unit tests** — Parameterized tests for each detection function. Each function gets both positive (should detect) and negative (should not detect) cases. Tests call the function directly via module import.

2. **Integration tests** — Run the actual script via `subprocess.run` with JSON on stdin. Verify exit codes (0 = allow, 2 = block), stderr messages (block tier), and JSON output with `permissionDecision: "ask"` (ask tier).

3. **Adversarial evasion suite** — 15 bypass techniques (path prefix, alias bypass, whitespace, command chaining, xargs, find -delete, etc.) loaded from `scenarios/evasion.json`. All must produce exit code 2.

4. **Scenario regression suite** — `scenarios/blocked.json` (26 scenarios) and `scenarios/allowed.json` (26 scenarios) loaded and run via subprocess. Comprehensive regression coverage across all categories.

5. **Performance benchmark** — Runs the hook via subprocess across a weighted mix of tool calls (from `scenarios/benchmark.json`) that approximates a real session (~55% Read, ~30% Bash, ~10% Write/Edit). Measures wall-clock time, in-process time, and rule evaluation time. Includes a pytest regression test with thresholds.

## Safety Rule

Tests never execute dangerous commands. The hook script receives command strings as JSON data for pattern matching — the commands are never passed to a shell. No test may import `os.system` or call `subprocess.run` with a command from scenario files.

## Running Tests

```bash
# Full suite (excludes benchmark — it's slower)
pytest plugins/yoloing-safe/tests/test_safety_hook.py -v

# Unit tests only (fast, no subprocess)
pytest plugins/yoloing-safe/tests/test_safety_hook.py -k "not TestIntegration and not TestEvasion and not TestBlocked and not TestAllowed and not TestDisable" -v

# Integration tests
pytest plugins/yoloing-safe/tests/test_safety_hook.py -k "TestIntegration" -v

# Evasion suite
pytest plugins/yoloing-safe/tests/test_safety_hook.py::TestEvasionSuite -v

# Scenario regression
pytest plugins/yoloing-safe/tests/test_safety_hook.py -k "TestBlockedScenarios or TestAllowedScenarios" -v

# Performance benchmark (regression test)
pytest plugins/yoloing-safe/tests/benchmark_hook.py -v

# Performance benchmark (full report, CLI)
python3 plugins/yoloing-safe/tests/benchmark_hook.py
python3 plugins/yoloing-safe/tests/benchmark_hook.py --iterations 200
```

## Adding Tests

### New detection function
Add a `TestXxx` class with `test_detected` and `test_not_detected` parametrized methods.

### New evasion technique
Add an entry to `scenarios/evasion.json` with `command`, `should: "block"`, and `technique`.

### New block/allow scenario
Add an entry to `scenarios/blocked.json` or `scenarios/allowed.json` with `tool_name`, `tool_input`, and `category`.

### Updating the benchmark workload
Edit `scenarios/benchmark.json`. Each scenario has a `weight` (relative frequency), `tool_name`, `tool_input`, and `label`. Weights approximate a real session distribution — adjust if usage patterns shift significantly.

## Profiling

The hook script supports `YOLOING_SAFE_PROFILE=1` to emit timing breakpoints to stderr. This is zero-cost when disabled.

```bash
# Single-call profiling
echo '{"tool_name":"Bash","tool_input":{"command":"git status"}}' | \
  YOLOING_SAFE_PROFILE=1 python3 plugins/yoloing-safe/scripts/pre-tool-use-safety.py 2>&1 >/dev/null
```

Output:
```
[yoloing-safe:profile] module_loaded 0.005ms
[yoloing-safe:profile] registry_built 0.307ms
[yoloing-safe:profile] stdin_start 0.321ms
[yoloing-safe:profile] stdin_parsed 0.336ms
[yoloing-safe:profile] config_loaded 0.361ms
[yoloing-safe:profile] rules_start 0.437ms
[yoloing-safe:profile] rules_done 1.327ms
[yoloing-safe:profile] exit 1.335ms
```

All timestamps are milliseconds since process start (`time.monotonic()`). The benchmark script (`benchmark_hook.py`) parses these automatically.

## Performance Thresholds

The benchmark regression test enforces two thresholds:

| Metric | Threshold | What it catches |
|---|---|---|
| Wall-clock median | 80ms | Expensive imports, blocking I/O, subprocess regressions |
| In-process median | 10ms | O(n^2) loops, expensive regexes, config loading regressions |

These are deliberately generous — the goal is catching regressions, not micro-benchmarking. Baseline (2026-03-06, Apple Silicon M4 Max): wall-clock ~23ms, in-process ~1.2ms.

## Which tests to run after changes

| Changed file | Run |
|---|---|
| `scripts/pre-tool-use-safety.py` | `pytest plugins/yoloing-safe/tests/ -v` (full suite including benchmark) |
| `tests/scenarios/*.json` | `pytest plugins/yoloing-safe/tests/ -k "TestBlockedScenarios or TestAllowedScenarios or TestEvasionSuite" -v` |
| `tests/scenarios/benchmark.json` | `pytest plugins/yoloing-safe/tests/benchmark_hook.py -v` |
| `config/defaults.json` | No tests reference this file directly (defaults are hardcoded in script) |
