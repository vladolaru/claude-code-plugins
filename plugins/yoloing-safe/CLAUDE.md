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
| `tests/scenarios/*.json` | `pytest plugins/yoloing-safe/tests/ -v` |
| `tests/e2e/test-fixtures.json` (overrides) | `cd tests/e2e && make generate` |
