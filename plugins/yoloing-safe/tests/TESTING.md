# Testing — yoloing-safe

## Architecture

The test suite has six layers:

1. **Unit tests** — direct detector assertions through the imported hook module.
2. **Integration tests** — subprocess execution of the real hook script.
3. **Adversarial evasion suite** — bypass attempts loaded from `scenarios/evasion.json`.
4. **Scenario regression suite** — blocked, asked, and allowed scenario files.
5. **Meta-tests** — structural invariants between `RULES`, allowlists, and scenarios.
6. **Performance benchmark** — weighted subprocess benchmark using hook profiling marks.

The public test entrypoints are:

- `test_core.py`
- `test_rules_filesystem.py`
- `test_rules_git.py`
- `test_rules_network.py`
- `test_rules_system.py`
- `test_integration.py`
- `test_scenarios.py`
- `test_meta.py`
- `benchmark_hook.py`

Test class implementations currently live in `_legacy_safety_hook_tests.py` and are re-exported by those entrypoints. Add new classes there, then re-export them from the matching split suite.

## Safety Rule

Tests never execute dangerous commands. The hook script receives command strings as JSON data for pattern matching; the commands are never passed to a shell.

## Running Tests

```bash
# Full suite
pytest plugins/yoloing-safe/tests/ -v

# Core + unit-style rule suites
pytest \
  plugins/yoloing-safe/tests/test_core.py \
  plugins/yoloing-safe/tests/test_rules_filesystem.py \
  plugins/yoloing-safe/tests/test_rules_git.py \
  plugins/yoloing-safe/tests/test_rules_network.py \
  plugins/yoloing-safe/tests/test_rules_system.py -v

# Integration tests
pytest plugins/yoloing-safe/tests/test_integration.py -v

# Evasion + scenario regression
pytest plugins/yoloing-safe/tests/test_scenarios.py -v

# Meta-tests
pytest plugins/yoloing-safe/tests/test_meta.py -v

# Performance benchmark
pytest plugins/yoloing-safe/tests/benchmark_hook.py -v

# Performance benchmark with report
python3 plugins/yoloing-safe/tests/benchmark_hook.py
python3 plugins/yoloing-safe/tests/benchmark_hook.py --iterations 200
```

## Adding Tests

### New detection function

1. Add a `Test{PascalCaseRuleId}` class to `_legacy_safety_hook_tests.py`.
2. Re-export it from the matching split suite.
3. Include `test_detected` and `test_not_detected` coverage.

### New evasion technique

Add an entry to `scenarios/evasion.json` with:

- `command`
- `should` (`"block"` or `"ask_or_block"`)
- `technique`
- `rule_id`

### New scenario coverage

- block rule: add to `scenarios/blocked.json`
- ask rule: add to `scenarios/asked.json`
- safe variant: add to `scenarios/allowed.json`

### Updating the benchmark workload

Edit `scenarios/benchmark.json`. Each scenario has `weight`, `tool_name`, `tool_input`, and `label`.

### Validating structural sync

Run `pytest plugins/yoloing-safe/tests/test_meta.py -v` after any change to assembled `RULES`, allowlists, or scenario files. Meta-tests verify:

- every rule has a message
- every allowlist rule_id maps to a real rule
- every scenario category is valid
- every rule has scenario coverage
- every block rule has evasion coverage
- every rule has a corresponding test class

## Profiling

The hook supports `YOLOING_SAFE_PROFILE=1` to emit timing marks to stderr.

```bash
echo '{"tool_name":"Bash","tool_input":{"command":"git status"}}' | \
  YOLOING_SAFE_PROFILE=1 python3 plugins/yoloing-safe/scripts/pre-tool-use-safety.py 2>&1 >/dev/null
```

Example output:

```text
[yoloing-safe:profile] module_loaded 0.005ms
[yoloing-safe:profile] registry_built 0.307ms
[yoloing-safe:profile] stdin_start 0.321ms
[yoloing-safe:profile] stdin_parsed 0.336ms
[yoloing-safe:profile] config_loaded 0.361ms
[yoloing-safe:profile] rules_start 0.437ms
[yoloing-safe:profile] rules_done 1.327ms
[yoloing-safe:profile] exit 1.335ms
```

## Performance Thresholds

| Metric | Threshold | Purpose |
|---|---|---|
| Wall-clock median | 80ms | Catch expensive imports or subprocess regressions |
| In-process median | 10ms | Catch algorithmic or regex regressions |

Baseline (2026-03-06, Apple Silicon M4 Max): wall-clock about 23ms, in-process about 1.2ms.

## Which Tests to Run After Changes

| Changed file | Run |
|---|---|
| Any file under `scripts/` | `pytest plugins/yoloing-safe/tests/ -v` and `pytest plugins/yoloing-safe/tests/benchmark_hook.py -v` |
| `scripts/yoloing_safe/rules/__init__.py` or a domain rule module | `pytest plugins/yoloing-safe/tests/test_meta.py -v` and `pytest plugins/yoloing-safe/tests/ -v` |
| `ALLOWLIST_PATTERNS` | `pytest plugins/yoloing-safe/tests/test_meta.py -v` and the affected rule suites |
| `tests/scenarios/*.json` | `pytest plugins/yoloing-safe/tests/test_scenarios.py -v` |
| `tests/scenarios/benchmark.json` | `pytest plugins/yoloing-safe/tests/benchmark_hook.py -v` |
