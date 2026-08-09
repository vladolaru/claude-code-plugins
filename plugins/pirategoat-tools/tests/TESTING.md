# Testing Framework

Testing for pirategoat-tools uses fast, deterministic code-based graders — no model calls. All tests are pytest-based and run in under 25 seconds.

## Architecture Overview

```
tests/
├── TESTING.md                        # This file
├── __init__.py                       # Package marker
├── conftest.py                       # Shared fixtures + sys.path setup (SCRIPTS_DIR on path)
├── review/                           # Tests for scripts/review/
│   ├── test_pipeline.py              # Pipeline briefing tests (get_step_guidance)
│   ├── test_pipeline_infra.py        # Pipeline infrastructure (step sequence, routing, state, CLI)
│   ├── test_pipeline_integration.py  # Pipeline orchestration (subprocess, telemetry, integration)
│   ├── test_plan_dispatch.py         # Dispatch planning tests
│   ├── test_context.py               # Review context collection tests
│   ├── test_agents_status.py         # Agent readiness gate tests
│   ├── test_workspace_setup.py       # Workspace setup tests
│   ├── test_critic.py                # Decision critic tests
│   ├── test_telemetry.py             # Telemetry logging tests
│   ├── test_agent_registry.py        # Agent registry validation tests
│   └── agent/                        # Tests for scripts/review/agent/
│       ├── test_bootstrap.py         # Bootstrap unit tests (direct imports)
│       ├── test_bootstrap_integration.py  # Bootstrap integration (smoke + category reps + build_output)
│       ├── test_scope.py             # Scope filtering unit tests
│       ├── test_scope_routing.py     # Domain routing (direct function calls + branch freshness)
│       ├── test_output.py            # ReviewOutputBuilder unit tests
│       └── test_diff_noise_filter.py # Semantic diff noise filter tests
├── linear/                           # Tests for scripts/linear/
│   ├── test_pipeline.py              # Linear issue pipeline tests
│   ├── test_pipeline_guidance.py     # Linear pipeline briefing tests
│   └── test_events.py               # Pipeline events tests
├── iterative_review/                 # Tests for scripts/iterative_review/
│   ├── test_briefing.py              # Iterative review briefing tests
│   ├── test_cli.py                   # CLI argument tests
│   ├── test_codex.py                 # Codex integration tests
│   ├── test_loop.py                  # Review loop tests
│   └── test_telemetry.py            # Iterative review telemetry tests
├── analysis/                         # Tests for scripts/analysis/
│   ├── test_session_metrics.py       # Session metrics extraction tests
│   └── test_session_analyzer.py      # Session analyzer tests
├── commands/                         # Tests for commands/
│   └── test_commands.py              # Structural + review command tests (incl. pr-update, switch-to)
├── grading/                          # Test graders and offline compliance grading
│   ├── test_graders.py               # Tests for the graders themselves
│   └── eval_agent_compliance.py      # Offline grading tool for review output files
├── helpers/                          # Shared test utilities
│   ├── graders.py                    # Shared grading functions
│   ├── command_helpers.py            # Shared helpers for command tests
│   └── context_fixtures.py          # Review context fixture generators
└── fixtures/
    ├── no-code-changes.diff          # Docs-only diff for NO_DOMAIN_FILES tests
    ├── php-source.diff               # PHP source: SQL injection, tight coupling
    ├── js-ts-source.diff             # JS/TS source: XSS, hardcoded API key
    ├── php-test-only.diff            # PHP tests: missing assertions, over-mocking
    ├── js-test-only.diff             # JS tests: snapshot overuse, weak assertions
    ├── e2e-test-only.diff            # E2E tests: hard-coded waits
    ├── mixed-code-and-tests.diff     # Cart logic + PHP/JS tests
    ├── wp-hooks-and-i18n.diff        # WP plugin: hooks, i18n, escaping, $wpdb
    └── multi-file-realistic.diff     # 7 files across all 9 domains
```

###Bootstrap Unit Tests (`review/agent/test_bootstrap.py`)

Deterministic pytest suite. Tests `review/agent/bootstrap.py` pure functions by importing them directly — `extract_protocol_sections`, `build_output`, `compute_review_budget`, `load_pr_intent`, and others. No network or model calls.

###Bootstrap Integration Tests (`review/agent/test_bootstrap_integration.py`)

Integration tests that run `review/agent/bootstrap.py` via subprocess against a temp git repo (created from `multi-file-realistic.diff`, isolated from real repo state). Uses category representatives (principle §6) and right-layer testing (principle §7):

| Class | Tests | What it verifies |
|---|---|---|
| `TestCategoryRepresentatives` | 5 | One comprehensive test per agent category: standard, test-agent, exploration, null-domain, history+override. Each verifies section structure, conditional sections, personalization, and budget in one shot. |
| `TestArchitecturalInvariants` | 2 | REVIEW RULES identical across 3 representative agents; DOMAIN RULES identical across 2 test agents |
| `TestSmokeAllAgents` | 21 | Every registered agent exits 0 — the ONE legitimate ALL_AGENTS parameterization (validates registry correctness) |
| `TestErrorCases` | 2 | Unknown agent exits 1 with structured error output |
| `TestReviewOutputBuilderAPIExample` | 6 | Section 3 includes complete builder API usage example (direct `build_output()` call) |
| `TestBootstrapOutputSizeCap` | 4 | Large scope truncated with file reference; small scope inline (direct `build_output()` call) |
| `TestDynamicDispatchRisk` | 4 | dead-code-reviewer gets DYNAMIC_DISPATCH_RISK; PHP → high, no PHP → low (direct `build_output()` call) |
| `TestOutputFilenameConsistency` | 2 | Output filenames from `save()` match bootstrap expectations (direct `build_output()` call) |

###Domain Routing Evals (`review/agent/test_scope_routing.py`)

Deterministic pytest suite that verifies `review/agent/scope.py` domain routing logic by calling `filter_noise()` + `filter_domain()` directly (pure functions, no subprocess). For each fixture, creates a temp git repo, gets the changed file list via `git diff --name-only`, and runs the filter functions for each domain.

Uses a `ROUTING_MATRIX` dict mapping fixture → expected domain results. Parameterized across all 14 domains and all 12 fixtures (168 test cases). Repos and file lists are cached per fixture.

Also includes `TestBranchFreshness` — 6 integration tests that run `review/agent/scope.py` via subprocess to verify merge-base detection, stale branch warnings, and range rebasing (these need the full pipeline).

**Fixture domain coverage:** See `ROUTING_MATRIX` dict in `review/agent/test_scope_routing.py` for the complete 12×14 matrix. Each entry maps `(fixture, domain) → "OK" | "NO_DOMAIN_FILES"`.

###Command Structure Evals (`commands/test_commands.py`)

Deterministic pytest suite that validates structural properties of command files. Shared helpers live in `helpers/command_helpers.py`. No network or model calls.

| Class | What it verifies |
|---|---|
| `TestFrontmatter` | All commands exist, have valid YAML frontmatter with `description` field |
| `TestScriptReferences` | Scripts referenced in commands (`review/pipeline.py`) exist on disk |
| `TestReviewCommandsReferenceUnifiedScript` | All review commands reference `review/pipeline.py` with correct mode |
| `TestMarketplaceRegistration` | All review commands are registered in `marketplace.json` |
| `TestCodeReviewIterative` | `code-review.md` has incremental mode, full/reset option, baseline reference |
| `TestFullCodeReview` | `full-code-review.md` has full mode |
| `TestBaselineFileGrading` | `.branch-review-baseline.json` round-trip: valid baseline files pass, incremented counts pass, explicit ranges pass |
| `TestPrReview` | `pr-review.md` is a thin wrapper delegating to `review/pipeline.py` |
| `TestUnifiedMission` | All review commands reference the unified pipeline mission |
| `TestPrUpdate` | `pr-update.md` structural validation: file exists, frontmatter, marketplace registration, not in review commands |
| `TestSwitchTo` | `switch-to.md` structural validation: file exists, frontmatter, marketplace registration |

###ReviewOutputBuilder Unit Tests (`review/agent/test_output.py`)

Direct unit tests on the `ReviewOutputBuilder` class from `scripts/review/agent/output.py`. Tests cover initialization, issue addition with validation, recommendations, verdict calculation, serialization (dict, JSON, markdown), and file output.

| Class | What it verifies |
|---|---|
| `TestBuilderInit` | pr_id/reviewer stored, defaults (empty lists, confidence 0.95), timestamp is ISO |
| `TestAddIssue` | Returns 8-char ID, stores all fields, severity case-insensitive, invalid severity raises, confidence boundaries, extra kwargs, defaults |
| `TestAddRecommendation` | Valid priorities store, invalid silently ignored, multiple per bucket |
| `TestAddPositive` | Stores observations in insertion order |
| `TestSetFilesReviewed` | Stores count |
| `TestSetConfidence` | Valid range works, invalid raises ValueError |
| `TestAddToolResult` | Stores tool names, deduplicates |
| `TestCalculateVerdict` | All 9 verdict boundaries (approve/comment/request_changes/block) |
| `TestToDict` | All top-level keys, severity counts, meta structure, None for empty fields |
| `TestToJson` | json.loads(to_json()) roundtrips to match to_dict() |
| `TestToMarkdown` | Header format, issues grouped by severity, positive observations |
| `TestSave` | Creates both files, JSON matches to_dict(), return paths correct |

### Shared Graders (`helpers/graders.py`)

Reusable grading functions for review output files. Used by both `grading/test_graders.py` (validates the graders themselves) and `grading/eval_agent_compliance.py` (grades actual agent output).

Every grader returns a `GradeResult`:

```python
@dataclass
class GradeResult:
    passed: bool        # All checks passed
    score: float        # 0.0-1.0 (checks_passed / checks_run)
    failures: list      # Description of each failure
    checks_run: int
    checks_passed: int
```

**Available graders:**

| Function | Input | Checks |
|---|---|---|
| `grade_review_json(path)` | Path to `{reviewer}-review.json` | File exists, valid JSON, required fields (`pr_id`, `reviewer`, `verdict`, `summary`, `issues`, `meta`), valid severities, valid verdict, issue schema, summary structure |
| `grade_review_markdown(path)` | Path to `{reviewer}-review.md` | File exists, `# ... Review` header, `## Executive Summary`, `**Verdict:**` — rendered from the JSON when absent |
| `grade_signal_format(text)` | Return signal text | `STATUS: FINISHED`, `OUTPUT_FILES:`, `COUNTS:`, `VERDICT:`, `SUMMARY:` |
| `grade_no_domain_files(text)` | Agent output for no-code scenario | APPROVE verdict, zero findings |
| `grade_error_exit(text)` | Agent output for error scenario | Error indication, no STATUS: FINISHED |
| `grade_output_pair(output_dir, reviewer_name)` | Output directory + reviewer name | Both `.json` and `.md` exist, delegates to json + markdown graders, reviewer name matches |
| `grade_review_baseline(path)` | Path to `.branch-review-baseline.json` | File exists, valid JSON, required fields (`last_reviewed_sha`, `last_reviewed_at`, `review_type`, `review_count`, `base_ref`, `git_range_used`), SHA format (7-40 hex), positive review_count, range contains `..` |

### Agent Compliance Grading (`grading/eval_agent_compliance.py`)

Offline grading tool for review output files — not part of the pytest suite.

- **`--grade-only /path/to/output`** — Scans an existing output directory for `*-review.json` files and grades each json/md pair, materializing the Markdown from each JSON first (`save()` publishes the JSON only; Markdown is a derived artifact). Fast, no model calls. Use after a real review run to validate agent output format.

### Detection Benchmark (live model calls)

Beyond protocol compliance, dispatch mode scores **detection quality** against
per-scenario answer keys (`SCENARIOS[...]["expected"]` in
`grading/eval_agent_compliance.py`). Keys assert required findings (recall),
acceptable secondary findings (never punished), severity ceilings and verdict
sets (precision), and `expect_not_applicable` (correct abstention). Clean-code
scenarios (`php_clean_review`, `js_clean_review`) are pure false-positive
probes.

```bash
# Single benchmark run for one scenario
python3 tests/grading/eval_agent_compliance.py --dispatch --scenario standard_review

# Restrict to one agent of a scenario
python3 tests/grading/eval_agent_compliance.py --dispatch --scenario realistic_multi_file --agent php-tests-reviewer

# Nondeterminism-controlled benchmark: majority vote across 3 dispatches
python3 tests/grading/eval_agent_compliance.py --dispatch --scenario standard_review --trials 3

# Structured report for cross-version comparison
python3 tests/grading/eval_agent_compliance.py --dispatch --report-out "$TMPDIR/detection-report.json"
```

**Dispatch identity.** Each dispatch runs `claude -p
--dangerously-skip-permissions --setting-sources project --plugin-dir <shim>
--agent pirategoat-tools:<name> --output-format json` — the session IS the
reviewer, so the canonical `agents/<name>.md` is its system prompt and its
full frontmatter contract (model, effort, tools) is applied natively by the
host with no re-encoding, and there is no orchestrating parent whose
artifacts could be misattributed to the agent. The reviewer runs bootstrap
itself, mirroring the production step 6 subagent prompt. The `<shim>` is a
per-process tempdir with a minimal plugin manifest and a symlink to the
WORKTREE `agents/` (`ensure_plugin_shim`) — the plugin directory itself
carries no manifest, so pointing `--plugin-dir` at it resolves nothing and
the user-scope INSTALLED plugin would silently answer instead
(sentinel-verified). `--setting-sources project` excludes that installed
copy plus user hooks and memory, making runs machine-independent. Model
routing is pinned to `agent_registry.json` (the single source of truth) at
three layers: `check_model_routing` refuses to dispatch when frontmatter
drifts from the registry tier, a `TestDispatchIdentity` guard requires the
two to stay equal for every agent, and each run's JSON `modelUsage` is
verified post-hoc — the PRIMARY model sums only `inputTokens`,
`outputTokens`, `cacheReadInputTokens`, and `cacheCreationInputTokens`, then
resolves `canonicalModel` before registry-tier validation (`modelUsage` is a
session accumulator that includes auxiliary calls). Any nonzero dispatch exit —
model mismatch, session error, timeout, non-JSON output — fails the entry
before grading (`dispatch_rejected` in its detail), because the reviewer
may have written a plausible artifact before the rejection surfaced. A run
that graded a bare-bootstrap generic session, or an unrepresentative model,
would measure the wrong instrument.

**Authoring answer keys: derive from the agent's doctrine, not intuition.**
The dispatched agent's `.md` states explicit severity doctrines (e.g.
performance-reviewer: missing `LIMIT` in raw queries is CRITICAL;
wp-architecture-reviewer: unprefixed global classes are CRITICAL). A key's
`verdict_in` and finding specs must be derived from what the *configured
reviewer* mandates for the fixture content — read the definition before
keying, and cite the doctrine in a key comment. A key written from generic
reviewer intuition can reject the agent's correct behavior (mandated `block`
not in `verdict_in`) or reward a miss (a required spec whose `match_any`
accepts a mere source token, e.g. `\$_GET`, lets an access-control finding
satisfy an injection spec — require technique/sink evidence). When the
doctrine mandates a severity class (SQL injection/XSS are CRITICAL for
security-reviewer), set `min_severity` on the required spec and derive
`verdict_in` from the builder's auto-verdict (any critical → `block`) — an
under-classified finding is a calibration miss the benchmark must measure,
not a match. Whenever a fixture, key, or agent definition changes, re-walk
this derivation.

Grading is deterministic (file + line-window + keyword regexes over
title/description/category — no model judge). A correct finding the patterns
miss shows up under `match.unexpected` in the report with its location and
the matcher-visible fields (title, category, truncated description); widen
that spec's `match_any` from what the reviewer actually wrote. Each detection
detail also records `output_dir` — the per-dispatch artifact directory
(review JSON, dispatch transcript) — so multi-trial misses are traceable to
their trial. Keys are
validated against their fixtures by `tests/grading/test_answer_keys.py` (pure
pytest, no model calls): files must exist in the diff, lines must be in range,
regexes must compile, fixtures must apply, and every key needs at least one
gate. When editing a fixture or key, run that guard first.

**Multi-trial semantics.** `--trials N` re-dispatches each *keyed* agent N
times (unkeyed agents always run once; the `Running:` line prints once, so
re-dispatches are silent) and majority-votes every check: compliance, verdict,
each required finding, each severity/unexpected gate, and both abstention
conditions. The threshold is strictly more than half (`N // 2 + 1`), so
`--trials 2` demands both trials pass. An unreadable trial counts as a miss on
every check. Because per-check majorities can be assembled from DIFFERENT
trials, the aggregate additionally requires that a majority of trials passed
outright — per-check votes are diagnostics, not the pass condition on their
own. Note that aggregate check counts (~3-6 votes) are not comparable
with single-trial check counts (compliance + detection checks), so don't mix
`--trials` settings when comparing reports.

**Abstention keys.** `expect_not_applicable` accepts BOTH `not_applicable`
and `approve` verdicts (each with zero findings): the shared reviewer
protocol mandates `mark_not_applicable` on `NO_DOMAIN_FILES` while the
tests-reviewer agent definitions instruct APPROVE on the same status — a
live doctrine conflict in the plugin's own definitions. Until that is
reconciled, punishing either compliant reading would grade a documentation
inconsistency, not reviewer quality.

**Report schema** (`--report-out`, dispatch mode only): top-level `mode`,
`trials` (the requested count), and `results[]` with `scenario`, `agent`,
`trials` (trial attempts run for this entry), `keyed` (whether an answer
key exists), `dispatch_count` (attempts with evidence of a live model call),
`dispatched` (whether `dispatch_count == trials`; deterministic bootstrap-only
entries, pre-model failures, and partially dispatched multi-trial entries
carry `false`; timeouts and unparseable dispatch output conservatively lack
dispatch evidence; pass rates over reviewer behavior must filter on this
complete-dispatch flag), `passed`, `checks_run`,
`checks_passed`, `failures`, `detail`. `detail` is polymorphic — discriminate per result, never on the
top-level `trials` (unkeyed agents run once even under `--trials N`):
`detail: null` with `keyed: false` is a compliance-only entry (dispatched
unkeyed entries carry `{output_dir}` instead of null); `keyed: true` with
`dispatch_rejected` or null detail is a keyed run that failed before
detection grading (see `failures`); result `trials` of 1 otherwise means
single-trial detail `{verdict, match, gates, compliance_passed, output_dir,
models}` — abstention keys carry `issue_count` and no `gates`/`match` keys;
result `trials` above 1 means aggregate detail `{trials, per_trial,
per_trial_failures, per_trial_passed, models}`; each `per_trial` entry retains
its `model_dispatched` evidence when available. Exit codes: 2 for any
configuration error (unknown scenario, empty selection, invalid flags,
unwritable report path — always before artifacts exist), 1 when the eval
ran and at least one entry failed, 0 on full pass. The comparative metric
is per-entry `passed` (and detection detail) — check counts and ratios are
per-entry diagnostics only, because compliance adds checks per schema-valid
issue and would score a more verbose reviewer higher for identical
detection performance.

## Design Principles

These principles guide all testing decisions. Follow them when adding or modifying tests.

### 1. Code-based graders, not model-based

All graders are deterministic Python functions. No LLM calls in the grading path. This keeps tests fast (~20s for the full suite), reproducible (same input = same result), and cheap (no API costs).

### 2. Grade outcomes, not paths

Tests verify what the output contains, not how it was produced. A test checks "the output has a `=== REVIEW RULES ===` section" not "the script called `extract_protocol_sections` with the right arguments." This makes tests resilient to refactoring.

### 3. Positive and negative cases

Every grader has tests for both:
- **Positive**: valid ReviewOutputBuilder output passes all checks
- **Negative**: missing fields fail, invalid values fail, empty files fail

### 4. Test the graders too

`grading/test_graders.py` validates that grading functions work correctly on synthetic inputs. This prevents false passes (grader too lenient) and false failures (grader too strict). A grader bug could silently undermine the entire eval system.

### 5. Skip-list resilience

Protocol extraction uses a skip-list (sections to exclude) rather than an include-list. New sections added to `reviewer-protocol.md` are automatically included in bootstrap output — and the test `test_new_section_auto_included` verifies this. If someone adds a section to the protocol, they don't need to update the bootstrap script or tests.

### 6. Parameterize on the axis of variation

Parameterize tests over the dimension that creates different code paths, not over the entire agent population.

**ALL_AGENTS parameterization is ONLY for smoke tests** — where each agent can independently fail due to config (bad domain, missing protocol file). The smoke test `TestSmokeAllAgents.test_exits_0` validates registry correctness.

**For everything else, use category representatives** — one agent per conditional path through the code. The five categories that cover all branches in `main()`:

| Category | Representative | Path covered |
|----------|---------------|-------------|
| Standard | `performance-reviewer` | Default path, no special flags |
| Test agent | `php-tests-reviewer` | `"tests-reviewer" in protocols` → DOMAIN RULES |
| Exploration | `patterns-reviewer` | `extra_scope` → EXPLORATION SCOPE |
| Null domain | `tests-mutation-reviewer` | `domain is None` → no scope discovery |
| History + override | `history-insights-reviewer` | `file_history` + `budget_override` |

**Adding a new agent?** It automatically appears in the smoke test. If it introduces a new conditional path (new protocol type, new flag), add it as a new category representative.

**Anti-patterns to avoid:**
- `@pytest.mark.parametrize("agent_name", ALL_AGENTS)` for template string assertions — the template doesn't vary by agent
- Parameterizing the "absent" case over all non-matching agents — 1-2 representatives suffice for an else-branch
- Testing `derive_reviewer_name()` output via subprocess for 21 agents — it's a pure function; if it works for 1, it works for all

### 7. Test at the right layer

If a function is importable and deterministic, test it as a unit test — don't invoke it via subprocess. Subprocess tests validate **orchestration**: "does `main()` correctly wire together plugin root discovery → protocol extraction → scope discovery → `build_output()`?" Unit tests validate **logic**: "does `build_output()` include DOMAIN RULES when `domain_rules` is not None?"

**Wrong layer:** Running a subprocess to verify that `"=== REVIEW RULES ===" in stdout` — this is a hardcoded string in `build_output()`, already covered by unit tests.

**Right layer:** Running a subprocess to verify that `returncode == 0` for every registered agent — this exercises the full `main()` orchestration path, which unit tests can't cover.

The `build_output()`-based test classes (`TestReviewOutputBuilderAPIExample`, `TestBootstrapOutputSizeCap`, `TestDynamicDispatchRisk`, `TestOutputFilenameConsistency`) demonstrate the right pattern: import the function, call it directly, assert on the result. Fast and focused.

**When to keep subprocess tests:**
- Testing `sys.exit()` paths (CLI argument validation)
- Testing cross-process state persistence (e.g., marker files read by a separate process)
- Testing the full `main()` orchestration that can't be called directly (calls `sys.exit()`)
**When to replace subprocess with direct calls:**
- Config round-trip tests — call `write_config()` + `read_config()` directly instead of spawning two `pipeline.py` processes
- Context loading — call `load_and_fill()` directly
- Functions that need a git repo CWD — call `build_scope()` directly with `os.chdir()` (saves ~0.15s interpreter spawn per test while keeping real git behavior)
- Any test already covered by a unit test on the same function — delete the subprocess duplicate

**Example — config round-trip (from `TestQuickModeConfig`):**
```python
# Wrong: two subprocess spawns to verify config persistence (~1.5s)
self._run("--step", "1", "--mode", "pr", "--output-dir", str(tmp_path), "--pr-number", "42", "--quick")
r = self._run("--step", "3", "--output-dir", str(tmp_path))
config = json.loads((tmp_path / "run-config.json").read_text())
assert config["quick"] is True

# Right: direct function calls (~0.001s)
mod.write_config(str(tmp_path), {"mode": "pr", "pr_number": "42", "interactive": True, "quick": True})
config = mod.read_config(str(tmp_path))
assert config["quick"] is True
```

**Example — git-dependent functions (from `TestMergeBaseGatingIntegration`):**
```python
# Wrong: subprocess spawn per test (~0.3s overhead on top of git ops)
result = subprocess.run([sys.executable, str(SCOPE_SCRIPT), "--domain", "code",
                         "--range", "main..HEAD", "--format", "json"], cwd=repo, ...)
data = json.loads(result.stdout)

# Right: direct call with os.chdir (~0.15s saved per test, real git behavior preserved)
saved_cwd = os.getcwd()
try:
    os.chdir(repo)
    scope = review_scope.build_scope(args)
finally:
    os.chdir(saved_cwd)
data = json.loads(review_scope.format_json_output(scope))
```

### 7a. Avoid `time.sleep()` — mock timestamps instead

`time.sleep()` adds real wall-clock delay to every test run. When tests need different timestamps (e.g., for filename uniqueness with 1-second resolution), mock `datetime.now()` instead.

**Pattern:** Subclass `datetime` (the C type can't be patched directly) and patch it on the module under test:

```python
from unittest.mock import patch

class FakeDatetime(datetime):
    _times = iter([...])
    @classmethod
    def now(cls, tz=None):
        return next(cls._times)

with patch.object(mod, "datetime", FakeDatetime):
    # Code that calls datetime.now() gets controlled timestamps
```

**Acceptable uses of `time.sleep()`:** Small sleeps (0.05s) to ensure measurable duration in tests that verify elapsed-time calculations (e.g., `TestLogStep.test_calculates_duration_since_prev`). These are testing that the code measures real time correctly, not just generating unique identifiers.

**Wrong:** `time.sleep(1.1)` to ensure 1-second-resolution timestamps differ (2.2s wasted per test pair).
**Right:** Mock `datetime.now()` to return timestamps 2 seconds apart (0.001s).

### 8. Tests read real protocol files

Integration tests run the actual bootstrap script against real `reviewer-protocol.md` and `tests-reviewer-protocol.md` files. This means tests catch heading drift (e.g., someone renames a section that the skip-list references).

### 9. Mock git repos, not the real repo

Integration tests that shell out to scripts (which run git commands) use temporary git repos created from `.diff` fixtures via `setup_temp_git_repo()` in `conftest.py`. This isolates tests from the real repository state — dirty working trees, recent commits, and branch structure don't affect results. The scripts resolve their plugin files via their own script path (`os.path.abspath(__file__)`), so changing `cwd` to a temp repo only affects git operations.

## How To

### Add a new reviewer agent

1. Add the agent to `scripts/review/agent_registry.json`
2. Create the agent `.md` file in `agents/`
3. Run `pytest plugins/pirategoat-tools/tests/review/agent/test_bootstrap_integration.py -v` — the `TestSmokeAllAgents` smoke test automatically picks up the new agent and validates it exits 0
4. If the agent introduces a **new conditional path** through `main()` (new protocol type, new flag like `file_history` or `extra_scope`), add a category representative test in `TestCategoryRepresentatives`
5. If the agent introduces a new domain, add it to `ALL_DOMAINS` in `review/agent/test_scope_routing.py` and update `ROUTING_MATRIX` for each fixture
6. **Do NOT** add `@pytest.mark.parametrize("agent_name", ALL_AGENTS)` tests for template assertions — the smoke test handles registry validation; category representatives handle conditional paths

### Add a new grader

1. Write the grading function in `helpers/graders.py` following the pattern:
   - Accept a path or text string
   - Build a list of `(condition, failure_message)` tuples
   - Return `_grade(checks)`
2. Add tests in `grading/test_graders.py` with at least one positive and one negative case
3. If the grader validates output files, use `ReviewOutputBuilder` from `scripts/review/agent/output.py` to create valid test fixtures

### Add a test for a new script

Follow the pattern in `review/agent/test_bootstrap.py`:

1. Use `importlib` to import from the hyphenated script filename
2. Write unit tests for pure functions with synthetic inputs (right layer — principle §7)
3. Write integration tests via subprocess only for orchestration paths that unit tests can't cover
4. Use `@pytest.mark.parametrize` only on the axis that creates variation (principle §6) — never ALL_AGENTS for invariant assertions
5. Derive `PLUGIN_ROOT` from the test file's location (no hardcoded paths)

### Add a new diff fixture

1. Create the `.diff` file in `tests/fixtures/`
2. **Use `new file mode 100644` with `--- /dev/null`** for all files in the diff. This ensures `git apply` works in a fresh temp repo (no context lines to match). Example:
   ```diff
   diff --git a/src/Example.php b/src/Example.php
   new file mode 100644
   --- /dev/null
   +++ b/src/Example.php
   @@ -0,0 +1,5 @@
   +<?php
   +class Example {
   +    // ...
   +}
   ```
3. Keep fixtures minimal — just enough to trigger the scenario
4. Add the fixture to `ROUTING_MATRIX` in `review/agent/test_scope_routing.py` with expected STATUS per domain
5. Run `pytest plugins/pirategoat-tools/tests/review/agent/test_scope_routing.py -v` to verify routing

## Conventions

### Importing from scripts/

Scripts are organized in domain packages (`review/`, `linear/`, `figma/`, `analysis/`). `conftest.py` adds `scripts/` to `sys.path`, so standard `from` imports work:

```python
from review.agent.output import ReviewOutputBuilder
from review.agent.scope import build_scope, filter_domain
```

For scripts with hyphenated filenames (not valid Python identifiers), use `importlib`:

```python
import importlib.util
_spec = importlib.util.spec_from_file_location("module_name", str(SCRIPTS_DIR / "review" / "agent" / "bootstrap.py"))
_mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_mod)
function_under_test = _mod.function_name
```

### Package namespace rule

**Test directories that mirror `scripts/` package names MUST NOT have `__init__.py` files.**

`scripts/review/` is a Python package (has `__init__.py`). If `tests/review/` also has `__init__.py`, Python caches whichever `review` package it discovers first — making `from review.agent.output import ...` resolve to the wrong package depending on test execution order.

- `tests/review/` — NO `__init__.py` (would shadow `scripts/review/`)
- `tests/review/agent/` — NO `__init__.py` (same reason)
- `tests/helpers/` — HAS `__init__.py` (no collision, unique name)
- `tests/commands/` — HAS `__init__.py` (no collision)

### Importing from helpers/

Shared test utilities live in `tests/helpers/`. The `helpers/` package is importable from any test subdirectory via the `conftest.py` sys.path setup:

```python
from helpers.graders import grade_review_json, grade_output_pair
from helpers.command_helpers import load_command, get_frontmatter
from helpers.context_fixtures import make_review_context
```

### Path resolution

Tests are organized in subdirectories mirroring `scripts/`. Path constants adapt based on subdirectory depth:

```python
# In tests/review/test_pipeline.py (one level deep)
TESTS_DIR = Path(__file__).resolve().parent.parent   # tests/
PLUGIN_ROOT = TESTS_DIR.parent                        # pirategoat-tools/
SCRIPTS_DIR = PLUGIN_ROOT / "scripts"

# In tests/review/agent/test_bootstrap.py (two levels deep)
TESTS_DIR = Path(__file__).resolve().parent.parent.parent  # tests/
PLUGIN_ROOT = TESTS_DIR.parent                              # pirategoat-tools/
SCRIPTS_DIR = PLUGIN_ROOT / "scripts"
```

Never hardcode absolute paths. Tests run from any working directory.

### Test data

- **Unit tests**: synthetic strings and dicts defined inline in the test class
- **Integration tests**: run the real script against real protocol files
- **Grader tests**: use `ReviewOutputBuilder` to create valid fixtures, hand-craft invalid ones
- **Compliance eval**: diff fixtures in `tests/fixtures/`, can reference `test-samples/` for existing diffs

### Dependencies

- **pytest** — the only external dependency (stdlib otherwise)
- **Zero model calls** in the entire test suite

## Valid Values Reference

These are the canonical valid values used by graders. If the review output schema changes, update both the source (`review/agent/output.py`) and the grader constants (`helpers/graders.py`).

| Constant | Values | Source |
|---|---|---|
| `VALID_SEVERITIES` | `critical`, `high`, `medium`, `low`, `info` | `ReviewOutputBuilder.add_issue()` |
| `VALID_VERDICTS` | `approve`, `block`, `request_changes`, `comment`, `not_applicable` | `ReviewOutputBuilder._calculate_verdict()` |
| `REQUIRED_JSON_TOP_FIELDS` | `pr_id`, `reviewer`, `verdict`, `summary`, `issues`, `meta` | `ReviewOutputBuilder.to_dict()` |
| `REQUIRED_ISSUE_FIELDS` | `id`, `severity`, `title`, `file`, `description`, `recommendation` | `ReviewOutputBuilder.add_issue()` |
| `REQUIRED_STATE_FIELDS` | `last_reviewed_sha`, `last_reviewed_at`, `review_count`, `base_ref`, `git_range_used` | `code-review.md` Step 5 |
