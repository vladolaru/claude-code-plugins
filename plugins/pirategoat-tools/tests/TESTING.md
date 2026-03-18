# Testing Framework

Testing for pirategoat-tools follows a two-level eval architecture: fast deterministic script evals and slower agent compliance evals. Both levels use code-based graders — no model calls in the core test suite.

## Architecture Overview

```
tests/
├── TESTING.md                        # This file
├── __init__.py                       # Package marker
├── conftest.py                       # Shared fixtures (setup_temp_git_repo, bootstrap_repo, pipeline_mod)
├── test_bootstrap_reviewer.py        # Level 1: Bootstrap unit tests (direct imports)
├── test_bootstrap_integration.py     # Level 1: Bootstrap integration tests (subprocess)
├── test_review_pipeline.py           # Level 1: Pipeline briefing tests (get_step_guidance)
├── test_pipeline_infrastructure.py   # Level 1: Pipeline infrastructure (step sequence, routing, state, CLI)
├── test_pipeline_orchestration.py    # Level 1: Pipeline orchestration (subprocess, telemetry, integration)
├── test_domain_routing.py            # Level 1: Domain routing evals (pytest)
├── test_commands.py                  # Level 1: Shared structural + review command tests
├── test_commands_helpers.py          # Shared helpers for command tests
├── test_commands_pr_update.py        # Level 1: pr-update.md content tests
├── test_commands_switch_to.py        # Level 1: switch-to.md content tests
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

Deterministic pytest suite. Tests `bootstrap-reviewer.py` by importing its functions directly. No network or model calls.

| Class | Functions under test | What it verifies |
|---|---|---|
| `TestDeriveReviewerName` | `derive_reviewer_name()` | All agents produce correct output names, edge cases (no suffix, empty string) |
| `TestExtractProtocolSections` | `extract_protocol_sections()` | Skip-list works, new sections auto-included, code fences not misread, L1 title stripped |
| `TestExtractFields` | `extract_pr_number()`, `extract_output_dir()`, `extract_status()` | Parses structured scope output, handles missing fields |
| `TestBuildOutput` | `build_output()` | Section markers, conditional sections (domain rules, exploration scope), output paths, builder snippet |
| `TestBuildErrorOutput` | `build_error_output()` | Error structure, plugin root, action directive |

### Level 1: Bootstrap Integration Tests (`test_bootstrap_integration.py`)

Integration tests that run the full `bootstrap-reviewer.py` script via subprocess for all agents against a temp git repo (created from `multi-file-realistic.diff`, isolated from real repo state):

| Class | What it verifies |
|---|---|
| `TestOutputStructure` | Every agent produces all required section markers |
| `TestContentIdentity` | REVIEW RULES content is identical across all agents; DOMAIN RULES identical across test agents |
| `TestConditionalSections` | DOMAIN RULES only for test agents; EXPLORATION SCOPE only for patterns-reviewer; tests-mutation-reviewer has no scope |
| `TestPersonalization` | REVIEWER_NAME, output file paths, builder snippet are correct per agent |
| `TestErrorHandling` | Unknown agent exits 1 with structured error; all valid agents exit 0 |

### Level 1: Domain Routing Evals (`test_domain_routing.py`)

Deterministic pytest suite that verifies `review-scope.py` routes each diff fixture to the correct set of domains. For every fixture × domain combination, creates a temp git repo, applies the diff, runs `review-scope.py --domain <X>`, and asserts STATUS is `OK` or `NO_DOMAIN_FILES`.

Uses a `ROUTING_MATRIX` dict mapping fixture → expected domain results. Parameterized across all 9 domains and all 9 fixtures (81 test cases). Repos are cached per fixture to avoid redundant git operations.

**Fixture domain coverage:**

| Fixture | code | security | perf | arch | wp-arch | php-tests | js-tests | e2e-tests | patterns |
|---|---|---|---|---|---|---|---|---|---|
| `php-source.diff` | OK | OK | OK | OK | OK | - | - | - | OK |
| `js-ts-source.diff` | OK | OK | OK | OK | OK | - | - | - | OK |
| `php-test-only.diff` | OK | OK | OK | - | OK | OK | - | - | OK |
| `js-test-only.diff` | OK | OK | OK | - | OK | - | OK | - | OK |
| `e2e-test-only.diff` | OK | OK | OK | OK | OK | - | - | OK | OK |
| `mixed-code-and-tests.diff` | OK | OK | OK | OK | OK | OK | OK | - | OK |
| `wp-hooks-and-i18n.diff` | OK | OK | OK | OK | OK | - | - | - | OK |
| `multi-file-realistic.diff` | OK | OK | OK | OK | OK | OK | OK | OK | OK |
| `no-code-changes.diff` | - | - | - | - | - | - | - | - | - |

`OK` = STATUS: OK (domain matches files), `-` = STATUS: NO_DOMAIN_FILES (domain excludes all files)

### Level 1: Command Structure Evals (`test_commands.py` + per-command files)

Deterministic pytest suite that validates structural properties of command files. Shared helpers live in `test_commands_helpers.py`. No network or model calls.

**Shared structural tests** (`test_commands.py`):

| Class | What it verifies |
|---|---|
| `TestFrontmatter` | All commands exist, have valid YAML frontmatter with `description` field |
| `TestScriptReferences` | Scripts referenced in commands (`review-pipeline.py`) exist on disk |
| `TestReviewCommandsReferenceUnifiedScript` | All review commands reference `review-pipeline.py` with correct mode |
| `TestMarketplaceRegistration` | All review commands are registered in `marketplace.json` |
| `TestCodeReviewIterative` | `code-review.md` has incremental mode, full/reset option, baseline reference |
| `TestFullCodeReview` | `full-code-review.md` has full mode |
| `TestBaselineFileGrading` | `.branch-review-baseline.json` round-trip: valid baseline files pass, incremented counts pass, explicit ranges pass |
| `TestPrReview` | `pr-review.md` is a thin wrapper delegating to `review-pipeline.py` |
| `TestUnifiedMission` | All review commands reference the unified pipeline mission |

**Per-command tests:**

| File | Class | What it verifies |
|---|---|---|
| `test_commands_pr_update.py` | `TestPrUpdate` | `pr-update.md` has PR detection, template detection, validation, approval gate, GHE fallback, size-based brevity |
| `test_commands_switch_to.py` | `TestSwitchTo` | `switch-to.md` has argument parsing, dirty state handling, branch switching, remote sync, PR flow, post-switch context |

### Level 1: ReviewOutputBuilder Unit Tests (`test_review_output.py`)

Direct unit tests on the `ReviewOutputBuilder` class from `scripts/review_output_simple.py`. Tests cover initialization, issue addition with validation, recommendations, verdict calculation, serialization (dict, JSON, markdown), and file output.

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

### 6. All agents, always

Integration tests are parameterized across all reviewer agents. Adding a new agent to `AGENT_CONFIG` in `bootstrap-reviewer.py` automatically includes it in all parameterized tests. No test file changes needed.

### 7. Tests read real protocol files

Integration tests run the actual bootstrap script against real `reviewer-protocol.md` and `tests-reviewer-protocol.md` files. This means tests catch heading drift (e.g., someone renames a section that the skip-list references).

### 8. Mock git repos, not the real repo

Integration tests that shell out to scripts (which run git commands) use temporary git repos created from `.diff` fixtures via `setup_temp_git_repo()` in `conftest.py`. This isolates tests from the real repository state — dirty working trees, recent commits, and branch structure don't affect results. The scripts resolve their plugin files via their own script path (`os.path.abspath(__file__)`), so changing `cwd` to a temp repo only affects git operations.

## How To

### Add a new reviewer agent

1. Add the agent to `AGENT_CONFIG` in `scripts/bootstrap-reviewer.py`
2. Create the agent `.md` file in `agents/`
3. Run `pytest plugins/pirategoat-tools/tests/test_bootstrap_reviewer.py -v`
4. All parameterized tests automatically pick up the new agent — no test changes needed

### Add a new grader

1. Write the grading function in `graders.py` following the pattern:
   - Accept a path or text string
   - Build a list of `(condition, failure_message)` tuples
   - Return `_grade(checks)`
2. Add tests in `test_graders.py` with at least one positive and one negative case
3. If the grader validates output files, use `ReviewOutputBuilder` from `scripts/review_output_simple.py` to create valid test fixtures

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
2. Write unit tests for pure functions with synthetic inputs
3. Write integration tests that run the script via subprocess
4. Use `@pytest.mark.parametrize` for agent-level coverage
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

Scripts have hyphenated names (`bootstrap-reviewer.py`), so use `importlib`:

```python
import importlib
_spec = importlib.util.spec_from_file_location("module_name", str(SCRIPT_PATH))
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

These are the canonical valid values used by graders. If the review output schema changes, update both the source (`review_output_simple.py`) and the grader constants (`graders.py`).

| Constant | Values | Source |
|---|---|---|
| `VALID_SEVERITIES` | `critical`, `high`, `medium`, `low`, `info` | `ReviewOutputBuilder.add_issue()` |
| `VALID_VERDICTS` | `approve`, `block`, `request_changes`, `comment` | `ReviewOutputBuilder._calculate_verdict()` |
| `REQUIRED_JSON_TOP_FIELDS` | `pr_id`, `reviewer`, `verdict`, `summary`, `issues`, `meta` | `ReviewOutputBuilder.to_dict()` |
| `REQUIRED_ISSUE_FIELDS` | `id`, `severity`, `title`, `file`, `description`, `recommendation` | `ReviewOutputBuilder.add_issue()` |
| `REQUIRED_STATE_FIELDS` | `last_reviewed_sha`, `last_reviewed_at`, `review_count`, `base_ref`, `git_range_used` | `code-review.md` Step 5 |
