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
| `scripts/yoloing_safe/context.py` | `EvalContext` — cached evaluation context shared across detectors per invocation. |
| `scripts/yoloing_safe/shell.py` | Command normalization, shell tokenization, heredoc stripping, segment splitting. |
| `scripts/yoloing_safe/paths.py` | Path extraction, sensitive target detection, Bash mutation target collection. |
| `scripts/yoloing_safe/registry.py` | Declarative detector compilation, custom detector wrapping, and rule builders (`block_rule`, `ask_rule`). |
| `scripts/yoloing_safe/runtime.py` | Main evaluation loop and block/ask/allow output helpers. |
| `scripts/yoloing_safe/rules/__init__.py` | Canonical ordered rule assembly plus allowlist aggregation. |
| `scripts/yoloing_safe/rules/*.py` | Domain rule implementations. Add new rules here. |
| `hooks/hooks.json` | Claude Code hook registration. Do not change the entrypoint path casually. |
| `tests/test_core.py` | Core compatibility, config, allowlist, and utility tests. |
| `tests/test_rules_*.py` | Domain rule tests — each file owns its test classes directly. |
| `tests/test_integration.py` | End-to-end subprocess regression tests. |
| `tests/test_scenarios.py` | Scenario and evasion regression suites. |
| `tests/test_meta.py` | Structural invariant checks for rules, allowlists, and scenarios. |
| `tests/e2e/test-fixtures.json` | Optional e2e overrides. Most rules need no entry. |
| `CHANGELOG.md` | Version history. Every behavior or shipped architecture change needs an entry. |

## Critical Constraints

- `test-cases.json` is generated. Edit `test-fixtures.json` or rule `examples`, then run `make generate`.
- Block-tier rules must stay before ask-tier rules in `scripts/yoloing_safe/rules/__init__.py`.
- Preserve the public compatibility surface exposed by `scripts/pre-tool-use-safety.py` unless you intentionally update tests, docs, and e2e tooling together.

## Public Testing Contract

The shim (`scripts/pre-tool-use-safety.py`) must export these names for tests, e2e, and benchmark to work. `TestShimCompatContract` in `test_meta.py` enforces this — if a refactor removes any of them, the test fails before downstream tooling breaks silently.

| Name | Type | Consumers |
|------|------|-----------|
| `RULES` | `dict` | All test suites, e2e generator, benchmark |
| `RULES_BY_TOOL` | `dict` | Runtime, benchmark |
| `ALLOWLIST_PATTERNS` | `list` | Test suites, runtime |
| `DEFAULTS` | `dict` | Config tests |
| `NON_DISABLEABLE_RULES` | `set`/`frozenset` | Config tests |
| `normalize_command` | callable | Unit tests |
| `load_config` | callable | Config tests, runtime |
| `is_allowlisted` | callable | Allowlist tests, runtime |
| `strip_writer_heredocs` | callable | Unit tests |

The e2e generator (`tests/e2e/generate-test-cases.py`) imports `RULES` directly from the package (`from yoloing_safe.rules import RULES`) rather than going through the shim, so it depends only on the canonical registry.

## Rule Structure

Each assembled `RULES` entry has:

| Key | Required | Description |
|-----|----------|-------------|
| `tier` | yes | `"block"` or `"ask"` |
| `tools` | yes | Tool names the rule applies to |
| `message` | yes | Guidance for Claude |
| `examples` | yes | Example commands or file paths for e2e generation |
| `detect` | custom only | Detector function taking `ctx` (EvalContext), returning `bool` or `(bool, custom_message)` |
| `patterns` | declarative only | Regex strings, OR semantics |
| `pattern_groups` | optional | List of AND groups, OR semantics across groups |
| `require` | optional | Additional regexes that must all match |
| `exclude` | optional | Regexes that short-circuit the rule |

For declarative rules, `registry.py` compiles patterns and generates the legacy `_detect` callable. For custom rules, `registry.py` wraps the internal detector so tests still see the legacy `(detected, message)` interface via `hook.RULES[rule_id]["_detect"]`.

## Rule Types

Use a declarative rule when the behavior is fully expressible as regex match plus require/exclude conditions. Use the `ask_rule()` or `block_rule()` builders from `registry.py`:

```python
("git_force_push", ask_rule(
    tools={"Bash"},
    patterns=[r"^git push\b"],
    require=[r"(--force\b|-f\b)"],
    exclude=[r"--force-with-lease", r"--force-if-includes"],
    message="Force push rewrites remote history. Use --force-with-lease instead.",
    examples=["git push --force origin hotfix/fix-arena"],
)),
```

Use a custom rule when you need config lookups, tool-specific path handling, chain-aware logic, or anything procedural. Custom detectors take an `EvalContext` object:

```python
def detect_my_rule(ctx):
    # ctx.command, ctx.tool_name, ctx.tool_input, ctx.config
    # ctx.whole_command (cached), ctx.segments (cached)
    return bool(_RE_MY_PATTERN.search(ctx.command))

("my_rule", block_rule(
    tools={"Bash"},
    detect=detect_my_rule,
    message="Guidance message.",
    examples=["dangerous-command --flag"],
)),
```

## Domain Modules

Each domain module exports an ordered `RULE_SPECS` list of `(rule_id, spec)` tuples and an `ALLOWLIST_PATTERNS` list. The aggregator in `rules/__init__.py` concatenates them with block-tier first, ask-tier second.

| Module | Domain |
|--------|--------|
| `rules/filesystem.py` | Destructive deletion, credential access, zero-access paths, sensitive writes |
| `rules/git.py` | Push safety, force push, hard reset, discard changes, stash, history rewrite |
| `rules/network.py` | Data exfiltration, package publishing, SSH destruction, GitHub operations |
| `rules/system.py` | Permissions, brew, Docker, database, Terraform, inline interpreters |

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
3. For custom rules: write a `detect_my_rule(ctx)` function.
4. Add the rule to the module's `RULE_SPECS` using `block_rule()` or `ask_rule()`.
5. If the rule needs a safe variant, add it to that module's `ALLOWLIST_PATTERNS`.
6. Add a `Test{PascalCaseRuleId}` class to the matching split suite (`test_rules_*.py`).
7. Add scenarios:
   - block rule: `scenarios/blocked.json` and at least one `scenarios/evasion.json` entry
   - ask rule: `scenarios/asked.json`
   - both: safe variant in `scenarios/allowed.json`
8. Run the [After Any Rule Change](#after-any-rule-change) steps.

### Removing a Rule

1. Remove it from the domain module's `RULE_SPECS`.
2. Remove any allowlist entries for that rule.
3. Remove scenarios from `blocked.json`, `asked.json`, `allowed.json`, and `evasion.json`.
4. Remove the unit test class from the matching split suite.
5. Remove any `tests/e2e/test-fixtures.json` override.
6. Run the [After Any Rule Change](#after-any-rule-change) steps.

### Renaming a Rule

Rename it in all of these places:

1. Domain module `RULE_SPECS` key and detector function name if applicable
2. Module `ALLOWLIST_PATTERNS`
3. `blocked.json`, `asked.json`, `allowed.json`, `evasion.json`
4. `tests/e2e/test-fixtures.json`
5. Test class and any explicit references in tests
6. Run the [After Any Rule Change](#after-any-rule-change) steps

### Changing a Rule's Tier

1. Change the builder from `block_rule()` to `ask_rule()` or vice versa.
2. Move scenario entries between `blocked.json` and `asked.json`.
3. If promoting to block, add evasion scenarios.
4. Update the message tone to match the new tier.
5. Run the [After Any Rule Change](#after-any-rule-change) steps.

### After Any Rule Change

1. `pytest plugins/yoloing-safe/tests/test_meta.py -v`
2. `pytest plugins/yoloing-safe/tests/ -v`
3. `pytest plugins/yoloing-safe/tests/benchmark_hook.py -v`
4. `cd plugins/yoloing-safe/tests/e2e && make generate`
5. Update `CHANGELOG.md` and bump `.claude-plugin/marketplace.json` per the repo versioning rules
