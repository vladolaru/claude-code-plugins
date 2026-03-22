# Testing Framework

Testing for pirategoat-tools follows a two-level eval architecture: fast deterministic script evals and slower agent compliance evals. Both levels use code-based graders — no model calls in the core test suite.

## Architecture Overview

```
tests/
├── TESTING.md                        # This file
├── __init__.py                       # Package marker
├── conftest.py                       # Shared fixtures (setup_temp_git_repo, bootstrap_repo, pipeline_mod)
├── test_bootstrap_reviewer.py        # Level 1: Bootstrap unit tests (direct imports)
├── test_bootstrap_integration.py     # Level 1: Bootstrap integration (smoke + category reps + build_output)
├── test_review_pipeline.py           # Level 1: Pipeline briefing tests (get_step_guidance)
├── test_pipeline_infrastructure.py   # Level 1: Pipeline infrastructure (step sequence, routing, state, CLI)
├── test_pipeline_orchestration.py    # Level 1: Pipeline orchestration (subprocess, telemetry, integration)
├── test_domain_routing.py            # Level 1: Domain routing (direct function calls + branch freshness)
├── test_commands.py                  # Level 1: Structural + review command tests (incl. pr-update, switch-to)
├── test_commands_helpers.py          # Shared helpers for command tests
├── test_review_output.py             # Level 1: ReviewOutputBuilder unit tests (pytest)
├── test_review_api_contract.py       # Level 1: Cross-component contract tests (pytest)
├── graders.py                        # Shared grading functions
├── test_graders.py                   # Tests for the graders themselves
├── eval_agent_compliance.py          # Level 2: Agent compliance evals
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

### Level 1: Bootstrap Unit Tests (`test_bootstrap_reviewer.py`)

Deterministic pytest suite. Tests `review/agent/bootstrap.py` pure functions by importing them directly — `extract_protocol_sections`, `build_output`, `compute_review_budget`, `load_pr_intent`, and others. No network or model calls.

### Level 1: Bootstrap Integration Tests (`test_bootstrap_integration.py`)

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

### Level 1: Domain Routing Evals (`test_domain_routing.py`)

Deterministic pytest suite that verifies `review/agent/scope.py` domain routing logic by calling `filter_noise()` + `filter_domain()` directly (pure functions, no subprocess). For each fixture, creates a temp git repo, gets the changed file list via `git diff --name-only`, and runs the filter functions for each domain.

Uses a `ROUTING_MATRIX` dict mapping fixture → expected domain results. Parameterized across all 14 domains and all 12 fixtures (168 test cases). Repos and file lists are cached per fixture.

Also includes `TestBranchFreshness` — 6 integration tests that run `review/agent/scope.py` via subprocess to verify merge-base detection, stale branch warnings, and range rebasing (these need the full pipeline).

**Fixture domain coverage:** See `ROUTING_MATRIX` dict in `test_domain_routing.py` for the complete 12×14 matrix. Each entry maps `(fixture, domain) → "OK" | "NO_DOMAIN_FILES"`.

### Level 1: Command Structure Evals (`test_commands.py`)

Deterministic pytest suite that validates structural properties of command files. Shared helpers live in `test_commands_helpers.py`. No network or model calls.

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

### Level 1: ReviewOutputBuilder Unit Tests (`test_review_output.py`)

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

### Level 1: Cross-Component Contract Tests (`test_review_api_contract.py`)

Tests the contracts between the three review pipeline layers: ReviewOutputBuilder (producer), reconcile() (consumer 1), and preprocess_findings() (consumer 2). Uses real output from one layer as input to the next.

| Class | What it verifies |
|---|---|
| `TestProducerToReconcileContract` | Builder output consumed by reconcile; multi-agent dedup; all fields survive; non-builder JSON skipped; extra fields don't break reconcile |
| `TestReconcileToIngestContract` | Reconcile output consumed by ingest (catches clusters/issues mismatch); issues key present; correct count; empty→empty |
| `TestFullRoundTrip` | 3-agent pipeline end-to-end (no findings dropped); severity preserved; all fields present in ingest output |

### Shared Graders (`graders.py`)

Reusable grading functions for review output files. Used by both `test_graders.py` (validates the graders themselves) and `eval_agent_compliance.py` (grades actual agent output).

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
| `grade_review_markdown(path)` | Path to `{reviewer}-review.md` | File exists, `# ... Review` header, `## Executive Summary`, `**Verdict:**` |
| `grade_signal_format(text)` | Return signal text | `STATUS: FINISHED`, `OUTPUT_FILES:`, `COUNTS:`, `VERDICT:`, `SUMMARY:` |
| `grade_no_domain_files(text)` | Agent output for no-code scenario | APPROVE verdict, zero findings |
| `grade_error_exit(text)` | Agent output for error scenario | Error indication, no STATUS: FINISHED |
| `grade_output_pair(output_dir, reviewer_name)` | Output directory + reviewer name | Both `.json` and `.md` exist, delegates to json + markdown graders, reviewer name matches |
| `grade_review_baseline(path)` | Path to `.branch-review-baseline.json` | File exists, valid JSON, required fields (`last_reviewed_sha`, `last_reviewed_at`, `review_type`, `review_count`, `base_ref`, `git_range_used`), SHA format (7-40 hex), positive review_count, range contains `..` |

### Level 3: E2E Pipeline Tests (`tests/e2e/`)

End-to-end tests for the `/pr-review` pipeline using a permanent test repo on GitHub (`vladolaru/pirategoat-pr-review-pipeline-test-repo`). Two layers:

**Layer 1 — Script-level** (`test_scripts.py`): Calls pipeline scripts directly via subprocess against a clone of the test repo. No Claude CLI, no API cost. Validates context schema, merge-base correctness, and dispatch decisions.

**Layer 2 — Full pipeline** (`test_pipeline.py`): Spawns the Claude CLI with `--output-format stream-json`, parses the JSONL stream in real-time, and fires step-level checkpoints. Each test takes 5-15 minutes and costs $2-5. Run manually.

| Component | Purpose |
|---|---|
| `conftest.py` | Session-scoped repo clone, per-test output dirs |
| `expectations.py` | `PRExpectations` dataclass with PR1-4 constants |
| `assertions.py` | File existence, schema, context field, severity helpers |
| `stream_monitor.py` | JSONL parser, checkpoint engine, `StreamMonitor` class |
| `checkpoints.py` | Builds step-level checkpoints from expectations |
| `test_scripts.py` | Layer 1: script-level tests |
| `test_pipeline.py` | Layer 2: full pipeline tests |

**Running e2e tests:**

```bash
# Layer 1 (fast, free):
pytest plugins/pirategoat-tools/tests/e2e/test_scripts.py -v

# Layer 2 — single PR:
pytest plugins/pirategoat-tools/tests/e2e/test_pipeline.py -v -k "test_pr1" --timeout=900

# Layer 2 — all PRs (slow, expensive):
pytest plugins/pirategoat-tools/tests/e2e/test_pipeline.py -v --timeout=900
```

**Test repo:** 4 permanent PRs exercise clean/buggy/large/non-default-branch scenarios. Reviews on PRs exercise the "analyze PR review state" pipeline step.

### Level 2: Agent Compliance Evals (`eval_agent_compliance.py`)

Tests that agents actually produce correct output when dispatched. Two modes:

- **`--grade-only /path/to/output`** — Scans an existing output directory for `*-review.json` and `*-review.md` files, grades each pair. Fast, no model calls. Use after a real review run.
- **`--dispatch --agent <name>`** — Full pipeline: creates temp git repo, applies fixture diff, runs bootstrap, dispatches agent via `claude -p`, grades output files. Slow, requires `claude` CLI, makes model calls.

## Design Principles

These principles guide all testing decisions. Follow them when adding or modifying tests.

### 1. Code-based graders, not model-based

All graders are deterministic Python functions. No LLM calls in the grading path. This keeps tests fast (~15s for the full suite), reproducible (same input = same result), and cheap (no API costs).

Model-based evaluation is reserved for `--dispatch` mode which intentionally makes model calls to test end-to-end agent behavior.

### 2. Grade outcomes, not paths

Tests verify what the output contains, not how it was produced. A test checks "the output has a `=== REVIEW RULES ===` section" not "the script called `extract_protocol_sections` with the right arguments." This makes tests resilient to refactoring.

### 3. Positive and negative cases

Every grader has tests for both:
- **Positive**: valid ReviewOutputBuilder output passes all checks
- **Negative**: missing fields fail, invalid values fail, empty files fail

### 4. Test the graders too

`test_graders.py` validates that grading functions work correctly on synthetic inputs. This prevents false passes (grader too lenient) and false failures (grader too strict). A grader bug could silently undermine the entire eval system.

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

### 8. Tests read real protocol files

Integration tests run the actual bootstrap script against real `reviewer-protocol.md` and `tests-reviewer-protocol.md` files. This means tests catch heading drift (e.g., someone renames a section that the skip-list references).

### 9. Mock git repos, not the real repo

Integration tests that shell out to scripts (which run git commands) use temporary git repos created from `.diff` fixtures via `setup_temp_git_repo()` in `conftest.py`. This isolates tests from the real repository state — dirty working trees, recent commits, and branch structure don't affect results. The scripts resolve their plugin files via their own script path (`os.path.abspath(__file__)`), so changing `cwd` to a temp repo only affects git operations.

## How To

### Add a new reviewer agent

1. Add the agent to `scripts/review/agent_registry.json`
2. Create the agent `.md` file in `agents/`
3. Run `pytest plugins/pirategoat-tools/tests/test_bootstrap_integration.py -v` — the `TestSmokeAllAgents` smoke test automatically picks up the new agent and validates it exits 0
4. If the agent introduces a **new conditional path** through `main()` (new protocol type, new flag like `file_history` or `extra_scope`), add a category representative test in `TestCategoryRepresentatives`
5. If the agent introduces a new domain, add it to `ALL_DOMAINS` in `test_domain_routing.py` and update `ROUTING_MATRIX` for each fixture
6. **Do NOT** add `@pytest.mark.parametrize("agent_name", ALL_AGENTS)` tests for template assertions — the smoke test handles registry validation; category representatives handle conditional paths

### Add a new grader

1. Write the grading function in `graders.py` following the pattern:
   - Accept a path or text string
   - Build a list of `(condition, failure_message)` tuples
   - Return `_grade(checks)`
2. Add tests in `test_graders.py` with at least one positive and one negative case
3. If the grader validates output files, use `ReviewOutputBuilder` from `scripts/review/agent/output.py` to create valid test fixtures

### Add a new compliance scenario

1. Add a scenario dict to `SCENARIOS` in `eval_agent_compliance.py`:
   ```python
   "scenario_name": {
       "description": "What this scenario tests",
       "agents": ["security-reviewer"],  # or ALL_AGENTS
       "diff": str(FIXTURES_DIR / "my-fixture.diff"),
       "grader": "output_pair",  # or "no_domain_files", "error_exit", "signal_format"
   }
   ```
2. Create any needed diff fixtures in `tests/fixtures/`
3. If a new grader type is needed, add it to `graders.py` first

### Add a test for a new script

Follow the pattern in `test_bootstrap_reviewer.py`:

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
4. Add the fixture to `ROUTING_MATRIX` in `test_domain_routing.py` with expected STATUS per domain
5. Run `pytest plugins/pirategoat-tools/tests/test_domain_routing.py -v` to verify routing

## Conventions

### Importing from scripts/

Scripts are organized in domain packages (`review/`, `linear/`, `figma/`, `analysis/`). Use `importlib` with the full path:

```python
import importlib
_spec = importlib.util.spec_from_file_location("module_name", str(SCRIPTS_DIR / "review" / "agent" / "bootstrap.py"))
_mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_mod)
function_under_test = _mod.function_name
```

### Path resolution

All paths are derived from `Path(__file__).resolve().parent`:

```python
TESTS_DIR = Path(__file__).resolve().parent
PLUGIN_ROOT = TESTS_DIR.parent
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
- **Zero model calls** in `test_bootstrap_reviewer.py`, `test_commands.py`, and `test_graders.py`
- `eval_agent_compliance.py --dispatch` requires the `claude` CLI

## Valid Values Reference

These are the canonical valid values used by graders. If the review output schema changes, update both the source (`review/agent/output.py`) and the grader constants (`graders.py`).

| Constant | Values | Source |
|---|---|---|
| `VALID_SEVERITIES` | `critical`, `high`, `medium`, `low`, `info` | `ReviewOutputBuilder.add_issue()` |
| `VALID_VERDICTS` | `approve`, `block`, `request_changes`, `comment`, `not_applicable` | `ReviewOutputBuilder._calculate_verdict()` |
| `REQUIRED_JSON_TOP_FIELDS` | `pr_id`, `reviewer`, `verdict`, `summary`, `issues`, `meta` | `ReviewOutputBuilder.to_dict()` |
| `REQUIRED_ISSUE_FIELDS` | `id`, `severity`, `title`, `file`, `description`, `recommendation` | `ReviewOutputBuilder.add_issue()` |
| `REQUIRED_STATE_FIELDS` | `last_reviewed_sha`, `last_reviewed_at`, `review_count`, `base_ref`, `git_range_used` | `code-review.md` Step 5 |
