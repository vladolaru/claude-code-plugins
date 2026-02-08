# Testing Framework

Testing for pirategoat-tools follows a two-level eval architecture: fast deterministic script evals and slower agent compliance evals. Both levels use code-based graders — no model calls in the core test suite.

## Architecture Overview

```
tests/
├── TESTING.md                      # This file
├── __init__.py                     # Package marker
├── test_bootstrap_reviewer.py      # Level 1: Script evals (pytest)
├── graders.py                      # Shared grading functions
├── test_graders.py                 # Tests for the graders themselves
├── eval_agent_compliance.py        # Level 2: Agent compliance evals
└── fixtures/
    └── no-code-changes.diff        # Docs-only diff for NO_DOMAIN_FILES tests
```

### Level 1: Script Evals (`test_bootstrap_reviewer.py`)

Deterministic pytest suite. Tests `bootstrap-reviewer.py` by importing its functions directly (unit tests) and by running it as a subprocess (integration tests). Runs in ~15 seconds, no network or model calls.

**Unit test classes** test individual functions with synthetic inputs:

| Class | Functions under test | What it verifies |
|---|---|---|
| `TestDeriveReviewerName` | `derive_reviewer_name()` | All 11 agents produce correct output names, edge cases (no suffix, empty string) |
| `TestExtractProtocolSections` | `extract_protocol_sections()` | Skip-list works, new sections auto-included, code fences not misread, L1 title stripped |
| `TestExtractFields` | `extract_pr_number()`, `extract_output_dir()`, `extract_status()` | Parses structured scope output, handles missing fields |
| `TestBuildOutput` | `build_output()` | Section markers, conditional sections (domain rules, exploration scope), output paths, builder snippet |
| `TestBuildErrorOutput` | `build_error_output()` | Error structure, plugin root, action directive |

**Integration test classes** run the full script via subprocess for all 11 agents:

| Class | What it verifies |
|---|---|
| `TestOutputStructure` | Every agent produces all required section markers |
| `TestContentIdentity` | REVIEW RULES content is identical across all agents; DOMAIN RULES identical across test agents |
| `TestConditionalSections` | DOMAIN RULES only for test agents; EXPLORATION SCOPE only for patterns-reviewer; tests-mutation-reviewer has no scope |
| `TestPersonalization` | REVIEWER_NAME, output file paths, builder snippet are correct per agent |
| `TestErrorHandling` | Unknown agent exits 1 with structured error; all valid agents exit 0 |

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

### 6. All 11 agents, always

Integration tests are parameterized across all 11 reviewer agents. Adding a new agent to `AGENT_CONFIG` in `bootstrap-reviewer.py` automatically includes it in all parameterized tests. No test file changes needed.

### 7. Tests read real protocol files

Integration tests run the actual bootstrap script against real `reviewer-protocol.md` and `tests-reviewer-protocol.md` files. This means tests catch heading drift (e.g., someone renames a section that the skip-list references).

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
2. Use standard unified diff format (`diff --git a/... b/...`)
3. Keep fixtures minimal — just enough to trigger the scenario
4. Reuse existing fixtures from `test-samples/json-output-test/` via path reference when possible (don't duplicate)

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
- **Zero model calls** in `test_bootstrap_reviewer.py` and `test_graders.py`
- `eval_agent_compliance.py --dispatch` requires the `claude` CLI

## Valid Values Reference

These are the canonical valid values used by graders. If the review output schema changes, update both the source (`review_output_simple.py`) and the grader constants (`graders.py`).

| Constant | Values | Source |
|---|---|---|
| `VALID_SEVERITIES` | `critical`, `high`, `medium`, `low`, `info` | `ReviewOutputBuilder.add_issue()` |
| `VALID_VERDICTS` | `approve`, `block`, `request_changes`, `comment` | `ReviewOutputBuilder._calculate_verdict()` |
| `REQUIRED_JSON_TOP_FIELDS` | `pr_id`, `reviewer`, `verdict`, `summary`, `issues`, `meta` | `ReviewOutputBuilder.to_dict()` |
| `REQUIRED_ISSUE_FIELDS` | `id`, `severity`, `title`, `file`, `description`, `recommendation` | `ReviewOutputBuilder.add_issue()` |
