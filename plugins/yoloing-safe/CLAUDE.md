# yoloing-safe — Agent Instructions

## What This Plugin Does

PreToolUse safety hook for Claude Code's YOLO mode (`--dangerously-skip-permissions`). It pattern-matches tool calls against `RULE_REGISTRY` and either blocks (exit 2) or asks for confirmation (JSON `permissionDecision: "ask"`).

## Key Files

| File | Role |
|------|------|
| `scripts/pre-tool-use-safety.py` | The hook script. Contains `RULE_REGISTRY` — the canonical list of all safety rules. |
| `config/defaults.json` | Default configuration (credential patterns, zero-access paths, disabled rules). |
| `hooks.json` | Claude Code hook registration (PreToolUse event wiring). |

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
make run          # Run full suite (31 tests, 8 sessions)
make run-save     # Same, but save results to ./results/
```

See `tests/e2e/README.md` for full setup, output format, and debugging.

## After Modifying Rules

When you add, remove, rename, or change the tier of a rule in `RULE_REGISTRY`:

### 1. Update unit/integration tests

Add or update test cases in `tests/test_safety_hook.py` and the scenario files (`tests/scenarios/blocked.json`, `tests/scenarios/allowed.json`, `tests/scenarios/evasion.json`).

### 2. Run unit tests

```bash
pytest plugins/yoloing-safe/tests/ -v
```

### 3. Update e2e test fixtures

Add or update the rule's entry in `tests/e2e/test-fixtures.json`. This is the only file you edit by hand for e2e — it holds the test command, pattern, and any overrides per rule.

### 4. Regenerate e2e test cases

```bash
cd plugins/yoloing-safe/tests/e2e
make generate
```

The generator imports `RULE_REGISTRY` from the hook script and merges it with `test-fixtures.json`. It validates bidirectionally — every registry rule must have a fixture and vice versa. If a rule is missing a fixture, it fails with an error.

**Never edit `test-cases.json` directly.** It is generated and will be overwritten.

### 5. Rebuild and run e2e

```bash
make build
make run
```

### Quick staleness check

To verify `test-cases.json` matches the current `RULE_REGISTRY` without regenerating:

```bash
make check
```

## Which Tests to Run

| What changed | Run |
|---|---|
| `scripts/pre-tool-use-safety.py` (detection logic) | `pytest plugins/yoloing-safe/tests/ -v` |
| `scripts/pre-tool-use-safety.py` (RULE_REGISTRY) | `pytest plugins/yoloing-safe/tests/ -v` then `cd tests/e2e && make generate` |
| `tests/scenarios/*.json` | `pytest plugins/yoloing-safe/tests/ -v` |
| `tests/e2e/test-fixtures.json` | `cd tests/e2e && make generate` |
