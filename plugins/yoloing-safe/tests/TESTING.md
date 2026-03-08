# Testing — yoloing-safe

## Architecture

The test suite has six layers:

1. **Unit tests** — Parameterized tests for each detection function. Each function gets both positive (should detect) and negative (should not detect) cases. Tests call the function directly via module import.

2. **Integration tests** — Run the actual script via `subprocess.run` with JSON on stdin. Verify exit codes (0 = allow, 2 = block), stderr messages (block tier), and JSON output with `permissionDecision: "ask"` (ask tier).

3. **Adversarial evasion suite** — Bypass techniques (path prefix, alias bypass, whitespace, command chaining, xargs, find -delete, etc.) loaded from `scenarios/evasion.json`. Each entry has a `rule_id` field for per-rule coverage validation. All must produce exit code 2 or an ask response.

4. **Scenario regression suite** — `scenarios/blocked.json` (block tier), `scenarios/asked.json` (ask tier), and `scenarios/allowed.json` (safe commands) loaded and run via subprocess. Comprehensive regression coverage across all categories.

5. **Meta-tests** — Structural invariant checks (`test_meta.py`) that prevent drift between `RULES`, message catalogs, allowlist patterns, and scenario files. Fast unit tests — no subprocess, no I/O beyond reading JSON scenario files. These are the primary defense against rule-test desync.

6. **Performance benchmark** — Runs the hook via subprocess across a weighted mix of tool calls (from `scenarios/benchmark.json`) that approximates a real session (~55% Read, ~30% Bash, ~10% Write/Edit). Measures wall-clock time, in-process time, and rule evaluation time. Includes a pytest regression test with thresholds.

## Safety Rule

Tests never execute dangerous commands. The hook script receives command strings as JSON data for pattern matching — the commands are never passed to a shell. No test may import `os.system` or call `subprocess.run` with a command from scenario files.

## Running Tests

```bash
# Full suite (excludes benchmark — it's slower)
pytest plugins/yoloing-safe/tests/test_safety_hook.py plugins/yoloing-safe/tests/test_meta.py -v

# Unit tests only (fast, no subprocess)
pytest plugins/yoloing-safe/tests/test_safety_hook.py -k "not TestIntegration and not TestEvasion and not TestBlocked and not TestAllowed and not TestAsked and not TestDisable" -v

# Integration tests
pytest plugins/yoloing-safe/tests/test_safety_hook.py -k "TestIntegration" -v

# Evasion suite
pytest plugins/yoloing-safe/tests/test_safety_hook.py::TestEvasionSuite -v

# Scenario regression
pytest plugins/yoloing-safe/tests/test_safety_hook.py -k "TestBlockedScenarios or TestAllowedScenarios or TestAskedScenarios" -v

# Meta-tests (structural invariants)
pytest plugins/yoloing-safe/tests/test_meta.py -v

# Performance benchmark (regression test)
pytest plugins/yoloing-safe/tests/benchmark_hook.py -v

# Performance benchmark (full report, CLI)
python3 plugins/yoloing-safe/tests/benchmark_hook.py
python3 plugins/yoloing-safe/tests/benchmark_hook.py --iterations 200
```

## Adding Tests

### New detection function
Add a `TestXxx` class (naming: `Test{PascalCaseRuleId}`) with `test_detected` and `test_not_detected` parametrized methods.

### New evasion technique
Add an entry to `scenarios/evasion.json` with `command`, `should` (`"block"` or `"ask_or_block"`), `technique`, and `rule_id`.

### New block scenario
Add an entry to `scenarios/blocked.json` with `tool_name`, `tool_input`, and `category` (must be a valid block-tier rule_id).

### New ask scenario
Add an entry to `scenarios/asked.json` with `tool_name`, `tool_input`, and `category` (must be a valid ask-tier rule_id).

### New allowed scenario
Add an entry to `scenarios/allowed.json` with `tool_name`, `tool_input`, and `category` (a rule_id or safe alias listed in `test_meta.py:SAFE_ALIASES`).

### Updating the benchmark workload
Edit `scenarios/benchmark.json`. Each scenario has a `weight` (relative frequency), `tool_name`, `tool_input`, and `label`. Weights approximate a real session distribution — adjust if usage patterns shift significantly.

### Validating structural sync
Run `pytest plugins/yoloing-safe/tests/test_meta.py -v` after any change to `RULES`, message catalogs, allowlist patterns, or scenario files. Meta-tests verify:
- Every rule_id has a corresponding message
- Every allowlist rule_id maps to a real rule
- Every scenario category is a valid rule_id (or safe alias)
- Every rule has scenario coverage (in blocked, asked, or allowed)
- Every block rule has evasion coverage (via `rule_id` field)
- No duplicate rule_ids
- No orphaned messages

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
| `RULES` (add/remove/rename) | `pytest plugins/yoloing-safe/tests/test_meta.py -v` (catches desync immediately) |
| `BLOCK_MESSAGES` / `ASK_MESSAGES` | `pytest plugins/yoloing-safe/tests/test_meta.py -v` |
| `ALLOWLIST_PATTERNS` | `pytest plugins/yoloing-safe/tests/test_meta.py -v` |
| `tests/scenarios/*.json` | `pytest plugins/yoloing-safe/tests/ -k "TestBlockedScenarios or TestAllowedScenarios or TestAskedScenarios or TestEvasionSuite" -v` |
| `tests/scenarios/benchmark.json` | `pytest plugins/yoloing-safe/tests/benchmark_hook.py -v` |
