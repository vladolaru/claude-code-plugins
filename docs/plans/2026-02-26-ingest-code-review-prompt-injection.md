# ingest-code-review Prompt Injection Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Replace the single-pass `ingest-code-review.md` workflow with a 6-step script-driven prompt injection that forces factored verification — Claude reads and answers questions about each finding *before* judging it.

**Architecture:** A new `scripts/ingest-code-review.py` CLI injects step-specific instructions per invocation (mirroring `decision-critic.py`). The command file `commands/ingest-code-review.md` is rewritten to describe the loop pattern. State accumulates in `--thoughts` across 6 steps: SETUP (1-2) → SCOPE (3) → VERIFICATION (4-5) → SYNTHESIS (6).

**Tech Stack:** Python 3 stdlib only (argparse, sys). Pytest for tests. No new dependencies.

---

## Background: How decision-critic.py works (read this first)

The `scripts/decision-critic.py` pattern to mirror:
1. Script is a CLI that takes `--step-number N --total-steps 7 --thoughts "<accumulated state>"`
2. It prints instructions for step N and nothing else
3. Claude executes those instructions, then calls the script again with `--step-number N+1`
4. `--thoughts` carries ALL state (finding IDs, statuses, answers) from all prior steps
5. A `state_requirement` string is injected in steps 2+ reminding Claude to preserve all prior IDs

Key enforcement mechanisms:
- Mandatory `--thoughts` arg on every call (Claude must write state explicitly)
- `state_requirement` text in every step 2-6 instruction set
- `"next"` field at end of each step tells Claude exactly what to do next
- Step 1 requires `--output-dir` (the "decision" analog)

Read `/Users/vladolaru/Work/a8c/claude-code-plugins/plugins/pirategoat-tools/skills/decision-critic/scripts/decision-critic.py` before implementing.

---

## Task 1: Create `scripts/ingest-code-review.py`

**Files:**
- Create: `plugins/pirategoat-tools/scripts/ingest-code-review.py`

### The 6-step workflow design

```
SETUP (1-2)         Locate output, parse findings → assign F1, F2, ... IDs
      |
      v
SCOPE (3)           Compare each finding against CHANGED_FILES and diff hunks
      |              Mark: IN_SCOPE | OUT_OF_SCOPE
      v
VERIFICATION (4-5)  Step 4: Generate falsification questions per IN_SCOPE finding
      |              Step 5: Answer independently (factored verification)
      v              Mark: VERIFIED | FAILED | UNCERTAIN
SYNTHESIS (6)       Categorize all findings → action plan
```

### Step-by-step instruction content

**Step 1: SETUP — Locate & Initialize**

Actions to inject:
- Parse `--output-dir` arg or auto-detect from `git branch --show-current`
- Run: `ls "${OUTPUT_DIR}"/*.json 2>/dev/null` — if nothing, STOP and tell user to run `/code-review` first
- Run: `cat "${OUTPUT_DIR}/.review-state.json" 2>/dev/null` — read `git_range_used` if present, else compute from `git symbolic-ref`
- Run: `git diff --name-only <GIT_RANGE>` — store result as CHANGED_FILES
- OUTPUT_FORMAT: Record `OUTPUT_DIR=<path>`, `GIT_RANGE=<range>`, `CHANGED_FILES=[file1, file2, ...]` in `--thoughts`
- Next: Step 2

**Step 2: SETUP — Parse Findings & Assign IDs**

Actions to inject:
- Read `${OUTPUT_DIR}/reconciled.json` (fallback: `reconciled.md`, then `*-review.json`)
- Parse all findings; assign stable IDs: F1, F2, F3...
- Extract per-finding: `file`, `line`, `severity`, `title`, `source_agents` (list), `confidence`
- OUTPUT_FORMAT (one line per finding):
  ```
  F1: <title> | <file>:<line> | severity=<s> | agents=[<a1>,<a2>] | confidence=<c>
  ```
- Count: "N findings total. Proceeding to scope check."
- State requirement reminder
- Next: Step 3

**Step 3: SCOPE — Classify Scope**

Actions to inject (analogous to decision-critic Step 2 classification):
- For each finding Fi:
  1. Is `file` in CHANGED_FILES? If no → OUT_OF_SCOPE (file not in diff)
  2. If yes: run `git diff <GIT_RANGE> -- <file>` → check if `line` falls in a diff hunk
  3. If line not in diff → OUT_OF_SCOPE (pre-existing code in changed file) — **unless** the change directly interacts with the flagged line (use judgment; if yes, mark IN_SCOPE with note)
- OUTPUT_FORMAT (one line per finding):
  ```
  F1 [IN_SCOPE]: <title>
  F2 [OUT_OF_SCOPE: file not in diff]: <title>
  F3 [IN_SCOPE*: interacts with change]: <title>
  ```
- COUNT: "X of N findings are IN_SCOPE. Proceeding to generate verification questions."
- SKIP_NOTE: OUT_OF_SCOPE findings will not be verified in steps 4-5; they go straight to SYNTHESIS as OUT_OF_SCOPE category
- State requirement reminder
- Next: Step 4

**Step 4: VERIFICATION — Generate Verification Questions**

Actions to inject (mirrors decision-critic Step 3):
- For each IN_SCOPE finding, generate 1-2 verification questions
- CRITERIA FOR GOOD QUESTIONS:
  - Specific and independently answerable using only the actual code
  - Designed to reveal if the finding could be WRONG (falsification focus)
  - Do not assume the finding is correct when asking the question
- QUESTION BOUNDS: Simple finding → 1 question. Multi-part/complex → 2 questions max
- OUTPUT_FORMAT:
  ```
  F1 [IN_SCOPE]: <title>
    Q1: <can you find the actual code doing X at file:line?>
    Q2: <does the codebase have protection Y already?>
  ```
- Academic note: Chain-of-Verification (Dhuliawala et al., 2023)
- State requirement reminder
- Next: Step 5

**Step 5: VERIFICATION — Factored Verification**

Actions to inject (mirrors decision-critic Step 4 — the critical epistemic boundary step):
- Answer each question INDEPENDENTLY
- EPISTEMIC BOUNDARY (critical):
  - Answer using ONLY:
    - (a) The actual code at the referenced location — use the Read tool to examine the file
    - (b) Stated context from `--thoughts` (git range, constraints)
    - (c) Established domain knowledge (security patterns, WP conventions, etc.)
  - Do NOT assume the finding is correct and work backward
  - Do NOT assume the finding is wrong and seek to disprove it
- SEPARATE answer from implication (same as decision-critic):
  - ANSWER: What the code actually does at that location (evidence-based)
  - IMPLICATION: What this means for the finding's accuracy
- Mark each IN_SCOPE finding:
  - VERIFIED — answers are consistent with the finding; issue exists as described
  - FAILED — answers reveal the finding is inaccurate, doesn't apply, or misunderstands the code
  - UNCERTAIN — insufficient evidence; state what would resolve it
- OUTPUT_FORMAT:
  ```
  F1 [IN_SCOPE] VERIFIED:
    Q1: <question>
      Answer: <what the code actually does, based on Read tool>
      Implication: <what this means for the finding>
    Status: VERIFIED
    Rationale: Direct $_GET usage confirmed at line 42, no sanitization present
  ```
- State requirement reminder
- Next: Step 6

**Step 6: SYNTHESIS — Categorize & Plan**

Actions to inject:
- Combine scope status and verification status into final categories:
  ```
  CONFIRMED     = IN_SCOPE + VERIFIED
  LIKELY VALID  = IN_SCOPE + UNCERTAIN (plausible but unverified)
  FALSE POSITIVE = IN_SCOPE + FAILED (finding is inaccurate)
  OUT OF SCOPE  = OUT_OF_SCOPE (from step 3)
  STYLE/PREF    = IN_SCOPE + VERIFIED but subjective/non-defect
  ```
- Present validation summary table (see format in current ingest-code-review.md Step 5)
- Build action plan for CONFIRMED + LIKELY VALID only:
  - Critical / Must Fix (security, data loss, crashes)
  - Important / Should Fix (bugs, perf, significant quality)
  - Consider (LIKELY VALID — uncertain findings)
  - Dismissed (OUT_OF_SCOPE and FALSE POSITIVE — explain each)
- Present plan and ask: "How would you like to proceed — fix everything, fix critical only, or discuss specific items?"
- No "NEXT" — workflow is complete

### Script structure (mirror decision-critic.py exactly)

```python
#!/usr/bin/env python3
"""
Ingest Code Review - Step-by-step prompt injection for structured finding validation.

Grounded in:
- Chain-of-Verification (Dhuliawala et al., 2023)
- Multi-Expert Prompting (Wang et al., 2024)
"""

import argparse
import sys
from typing import Optional


def get_phase_name(step: int) -> str:
    if step <= 2:
        return "SETUP"
    elif step == 3:
        return "SCOPE"
    elif step <= 5:
        return "VERIFICATION"
    else:
        return "SYNTHESIS"


def get_step_guidance(step, total_steps, output_dir, thoughts) -> dict:
    # ... per-step dicts with: phase, step_title, actions, next, academic_note
    pass


def format_output(step, total_steps, guidance) -> str:
    # ... same as decision-critic: header, actions, academic_note, NEXT
    pass


def main():
    parser = argparse.ArgumentParser(...)
    parser.add_argument("--step-number", type=int, required=True)
    parser.add_argument("--total-steps", type=int, required=True)
    parser.add_argument("--output-dir", type=str)      # Required for step 1
    parser.add_argument("--thoughts", type=str, required=True)
    args = parser.parse_args()

    if args.step_number < 1 or args.step_number > 6:
        print("ERROR: step-number must be between 1 and 6", file=sys.stderr)
        sys.exit(1)

    if args.step_number == 1 and not args.output_dir:
        print("ERROR: --output-dir is required for step 1 (or set to 'auto' to detect from branch)",
              file=sys.stderr)
        sys.exit(1)

    guidance = get_step_guidance(args.step_number, args.total_steps, args.output_dir, args.thoughts)

    if args.step_number == 1 and args.output_dir:
        print(f"REVIEW OUTPUT: {args.output_dir}")
        print()

    print(format_output(args.step_number, args.total_steps, guidance))


if __name__ == "__main__":
    main()
```

### Step 1: Write tests first (TDD)

Tests live in `plugins/pirategoat-tools/tests/test_ingest_reviewer.py`.

```python
# test_ingest_reviewer.py
import importlib.util
import subprocess
import sys
from pathlib import Path

TESTS_DIR = Path(__file__).resolve().parent
PLUGIN_ROOT = TESTS_DIR.parent
SCRIPT_PATH = PLUGIN_ROOT / "scripts" / "ingest-code-review.py"

# Load module for unit tests
_spec = importlib.util.spec_from_file_location("ingest_code_review", str(SCRIPT_PATH))
_mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_mod)


class TestGetPhaseName:
    def test_steps_1_2_are_setup(self):
        assert _mod.get_phase_name(1) == "SETUP"
        assert _mod.get_phase_name(2) == "SETUP"

    def test_step_3_is_scope(self):
        assert _mod.get_phase_name(3) == "SCOPE"

    def test_steps_4_5_are_verification(self):
        assert _mod.get_phase_name(4) == "VERIFICATION"
        assert _mod.get_phase_name(5) == "VERIFICATION"

    def test_step_6_is_synthesis(self):
        assert _mod.get_phase_name(6) == "SYNTHESIS"


class TestGetStepGuidance:
    def test_step_1_title(self):
        g = _mod.get_step_guidance(1, 6, "/tmp/review", "")
        assert g["step_title"] == "Locate & Initialize"

    def test_step_2_title(self):
        g = _mod.get_step_guidance(2, 6, None, "prior state")
        assert g["step_title"] == "Parse Findings & Assign IDs"

    def test_step_3_title(self):
        g = _mod.get_step_guidance(3, 6, None, "prior state")
        assert g["step_title"] == "Classify Scope"

    def test_step_4_title(self):
        g = _mod.get_step_guidance(4, 6, None, "prior state")
        assert g["step_title"] == "Generate Verification Questions"

    def test_step_5_title(self):
        g = _mod.get_step_guidance(5, 6, None, "prior state")
        assert g["step_title"] == "Factored Verification"

    def test_step_6_title(self):
        g = _mod.get_step_guidance(6, 6, None, "prior state")
        assert g["step_title"] == "Categorize & Plan"

    def test_step_1_has_no_next_for_step_6(self):
        g = _mod.get_step_guidance(6, 6, None, "")
        assert g["next"] is None

    def test_steps_1_to_5_have_next(self):
        for step in range(1, 6):
            g = _mod.get_step_guidance(step, 6, None, "")
            assert g["next"] is not None, f"Step {step} missing 'next'"

    def test_step_5_has_academic_note(self):
        """Factored verification step cites Chain-of-Verification."""
        g = _mod.get_step_guidance(5, 6, None, "")
        assert g.get("academic_note") is not None
        assert "Chain-of-Verification" in g["academic_note"]

    def test_steps_2_to_6_have_state_requirement(self):
        """Steps 2-6 must include state_requirement text."""
        for step in range(2, 7):
            g = _mod.get_step_guidance(step, 6, None, "prior state")
            actions_text = "\n".join(g["actions"])
            assert "CONTEXT REQUIREMENT" in actions_text, (
                f"Step {step} missing CONTEXT REQUIREMENT (state_requirement)"
            )

    def test_step_1_no_state_requirement(self):
        """Step 1 has no prior state to preserve."""
        g = _mod.get_step_guidance(1, 6, "/tmp/review", "")
        actions_text = "\n".join(g["actions"])
        assert "CONTEXT REQUIREMENT" not in actions_text

    def test_step_3_mentions_changed_files(self):
        """Scope step must reference CHANGED_FILES."""
        g = _mod.get_step_guidance(3, 6, None, "CHANGED_FILES=[src/foo.php]")
        actions_text = "\n".join(g["actions"])
        assert "CHANGED_FILES" in actions_text

    def test_step_5_mentions_epistemic_boundary(self):
        """Factored verification must include the epistemic boundary rule."""
        g = _mod.get_step_guidance(5, 6, None, "F1 verified questions")
        actions_text = "\n".join(g["actions"])
        assert "EPISTEMIC BOUNDARY" in actions_text

    def test_step_5_mentions_read_tool(self):
        """Factored verification must tell Claude to use the Read tool."""
        g = _mod.get_step_guidance(5, 6, None, "F1 verified questions")
        actions_text = "\n".join(g["actions"])
        assert "Read tool" in actions_text

    def test_step_6_mentions_all_categories(self):
        """Synthesis step must mention all 5 finding categories."""
        g = _mod.get_step_guidance(6, 6, None, "findings")
        actions_text = "\n".join(g["actions"])
        for cat in ["CONFIRMED", "LIKELY VALID", "FALSE POSITIVE", "OUT OF SCOPE", "STYLE"]:
            assert cat in actions_text, f"Step 6 missing category: {cat}"


class TestFormatOutput:
    def test_header_format(self):
        g = _mod.get_step_guidance(1, 6, "/tmp/r", "")
        output = _mod.format_output(1, 6, g)
        assert "INGEST CODE REVIEW - Step 1/6:" in output
        assert "Phase: SETUP" in output

    def test_academic_note_included_when_present(self):
        g = _mod.get_step_guidance(5, 6, None, "")
        output = _mod.format_output(5, 6, g)
        assert "Chain-of-Verification" in output

    def test_next_step_shown(self):
        g = _mod.get_step_guidance(1, 6, "/tmp/r", "")
        output = _mod.format_output(1, 6, g)
        assert "NEXT:" in output

    def test_workflow_complete_on_step_6(self):
        g = _mod.get_step_guidance(6, 6, None, "")
        output = _mod.format_output(6, 6, g)
        assert "WORKFLOW COMPLETE" in output


class TestCLIIntegration:
    def _run(self, *args):
        return subprocess.run(
            [sys.executable, str(SCRIPT_PATH), *args],
            capture_output=True, text=True
        )

    def test_step_1_exits_0(self):
        result = self._run(
            "--step-number", "1",
            "--total-steps", "6",
            "--output-dir", "/tmp/test-review",
            "--thoughts", "",
        )
        assert result.returncode == 0

    def test_step_1_requires_output_dir(self):
        result = self._run(
            "--step-number", "1",
            "--total-steps", "6",
            "--thoughts", "",
        )
        assert result.returncode == 1
        assert "output-dir" in result.stderr.lower()

    def test_invalid_step_exits_1(self):
        result = self._run(
            "--step-number", "8",
            "--total-steps", "6",
            "--thoughts", "some state",
        )
        assert result.returncode == 1

    def test_step_2_no_output_dir_needed(self):
        result = self._run(
            "--step-number", "2",
            "--total-steps", "6",
            "--thoughts", "OUTPUT_DIR=/tmp/r GIT_RANGE=main..HEAD CHANGED_FILES=[foo.php]",
        )
        assert result.returncode == 0

    def test_thoughts_required(self):
        result = self._run(
            "--step-number", "1",
            "--total-steps", "6",
            "--output-dir", "/tmp/r",
        )
        assert result.returncode != 0

    def test_all_steps_produce_phase_header(self):
        for step in range(1, 7):
            args = ["--step-number", str(step), "--total-steps", "6", "--thoughts", "state"]
            if step == 1:
                args += ["--output-dir", "/tmp/r"]
            result = self._run(*args)
            assert result.returncode == 0, f"Step {step} failed: {result.stderr}"
            assert "Phase:" in result.stdout, f"Step {step} missing Phase header"
```

### Step 2: Run tests to verify they fail (before implementation)

```bash
cd /Users/vladolaru/Work/a8c/claude-code-plugins
pytest plugins/pirategoat-tools/tests/test_ingest_reviewer.py -v
```
Expected: `ModuleNotFoundError` or import failure (script doesn't exist yet).

### Step 3: Implement `scripts/ingest-code-review.py`

Write the full implementation. Follow `decision-critic.py` structure exactly:
- `get_phase_name(step)` → returns phase string
- `get_step_guidance(step, total_steps, output_dir, thoughts)` → returns dict with `phase`, `step_title`, `actions` (list of strings), `next`, `academic_note`
- `format_output(step, total_steps, guidance)` → returns formatted string
- `main()` → argparse + validation + print

The `state_requirement` string (inject into steps 2-6 actions):
```python
state_requirement = (
    "CONTEXT REQUIREMENT: Your --thoughts from this step must include ALL finding IDs (F1, F2...), "
    "their scope status (IN_SCOPE/OUT_OF_SCOPE), and any verification questions and statuses "
    "from previous steps. This accumulated state is essential for workflow continuity."
)
```

### Step 4: Run tests to verify they pass

```bash
pytest plugins/pirategoat-tools/tests/test_ingest_reviewer.py -v
```
Expected: All tests pass.

### Step 5: Commit

```bash
cd /Users/vladolaru/Work/a8c/claude-code-plugins
git add plugins/pirategoat-tools/scripts/ingest-code-review.py
git add plugins/pirategoat-tools/tests/test_ingest_reviewer.py
git commit -m "feat(pirategoat-tools): Add ingest-code-review step injector script"
```

---

## Task 2: Update `commands/ingest-code-review.md`

**Files:**
- Modify: `plugins/pirategoat-tools/commands/ingest-code-review.md`

### What changes

The command file currently contains the full 6-step instructions inline. After this change, it becomes a thin orchestration wrapper that tells Claude to:
1. Parse `$ARGUMENTS` for an optional output directory
2. Call the script at step 1, then loop through steps 2-6

### New content structure

```markdown
---
description: Analyze code review findings critically, validate against actual changes, filter false positives, and propose an action plan
---

You are a senior engineer ingesting review findings from a prior `/code-review` or `/full-code-review` run. Your job is NOT to blindly accept every finding — it is to **think critically**, validate each finding against the actual code, filter out false positives and out-of-scope noise, and propose a focused plan for what genuinely needs fixing.

**Mindset:** Review agents are thorough but imperfect. They sometimes flag pre-existing code, misunderstand intent, or report stylistic preferences as issues. Your value is in separating signal from noise.

## Workflow

This command uses step-by-step prompt injection to enforce analytical discipline — especially **factored verification** (reading the actual code before judging a finding). The workflow runs 6 steps across 4 phases: SETUP → SCOPE → VERIFICATION → SYNTHESIS.

## Invocation

```bash
python3 scripts/ingest-code-review.py \
  --step-number <1-6> \
  --total-steps 6 \
  --output-dir "<review output directory or 'auto'>" \
  --thoughts "<your accumulated findings, IDs, and statuses from all previous steps>"
```

| Argument | Required | Description |
| --- | --- | --- |
| `--step-number` | Yes | Current step (1-6) |
| `--total-steps` | Yes | Always 6 |
| `--output-dir` | Step 1 | Path to review output directory, or `auto` to detect from current branch |
| `--thoughts` | Yes | All accumulated state (finding IDs, scope status, verification results) |

## Starting the Workflow

**Parse arguments:** `$ARGUMENTS`
- If a path is provided: use it as `--output-dir`
- If empty: use `--output-dir auto`

**Run Step 1:**

```bash
python3 scripts/ingest-code-review.py \
  --step-number 1 \
  --total-steps 6 \
  --output-dir "<path from $ARGUMENTS, or 'auto'>" \
  --thoughts ""
```

Execute the instructions printed by the script. After completing each step's work, call the script with `--step-number N+1` and pass ALL accumulated state in `--thoughts`. Continue until Step 6 completes.
```

**Preservation requirement:** The updated file MUST still contain these strings (existing tests check for them):
- `OUT_OF_SCOPE` — appears in step 3 and step 6 instructions (injected by script, referenced here)
- `FALSE POSITIVE` — appears in step 6 category description
- `CHANGED_FILES` — appears in step 3 description
- `Action Plan` — appears in step 6 output format
- `CONFIRMED`, `LIKELY VALID`, `FALSE POSITIVE`, `OUT OF SCOPE`, `STYLE` — at least 4 of 5 categories

These can be in a "Phase Overview" table or summary section that describes what the script does.

### Step 1: Check which tests currently pass

```bash
cd /Users/vladolaru/Work/a8c/claude-code-plugins
pytest plugins/pirategoat-tools/tests/test_commands.py::TestIngestCodeReview -v
```
Note exactly which tests pass. All should pass before and after the rewrite.

### Step 2: Update `commands/ingest-code-review.md`

Write the new content. Include a "Phase Overview" section that names the categories so existing tests pass:

```markdown
## Phase Overview

| Phase | Steps | What happens |
|---|---|---|
| SETUP | 1-2 | Locate review output, parse findings, assign F1/F2/... IDs |
| SCOPE | 3 | Compare each finding against CHANGED_FILES and diff hunks; mark IN_SCOPE or OUT_OF_SCOPE |
| VERIFICATION | 4-5 | Generate falsification questions; answer with factored verification (VERIFIED/FAILED/UNCERTAIN) |
| SYNTHESIS | 6 | Categorize: CONFIRMED, LIKELY VALID, FALSE POSITIVE, OUT OF SCOPE, STYLE/PREFERENCE; propose Action Plan |
```

### Step 3: Run existing tests to verify they still pass

```bash
pytest plugins/pirategoat-tools/tests/test_commands.py::TestIngestCodeReview -v
```
Expected: All 5 existing tests pass.

Also run the script reference test (verifies new script is found on disk):
```bash
pytest plugins/pirategoat-tools/tests/test_commands.py::TestScriptReferences -v
```
Expected: Pass (ingest-code-review.py now exists and is referenced in command).

### Step 4: Commit

```bash
git add plugins/pirategoat-tools/commands/ingest-code-review.md
git commit -m "feat(pirategoat-tools): Rewrite ingest-code-review to use step injector"
```

---

## Task 3: Add script reference test to `test_commands.py`

**Files:**
- Modify: `plugins/pirategoat-tools/tests/test_commands.py`

### What to add

Add one test to `TestIngestCodeReview` that verifies:
1. `ingest-code-review.md` references `ingest-code-review.py`
2. The script actually exists on disk

```python
def test_references_step_injector_script(self):
    """ingest-code-review.md should reference the step injector script."""
    content = _read_command("ingest-code-review.md")
    assert "ingest-code-review.py" in content, (
        "ingest-code-review.md: missing reference to ingest-code-review.py script"
    )
    script_path = SCRIPTS_DIR / "ingest-code-review.py"
    assert script_path.is_file(), (
        f"ingest-code-review.py not found at {script_path}"
    )
```

### Step 1: Add the test

Edit `plugins/pirategoat-tools/tests/test_commands.py`, add the method to `TestIngestCodeReview`.

### Step 2: Run to verify it passes

```bash
pytest plugins/pirategoat-tools/tests/test_commands.py::TestIngestCodeReview -v
```
Expected: 6 tests pass (5 existing + 1 new).

### Step 3: Run full test suite

```bash
pytest plugins/pirategoat-tools/tests/ -v
```
Expected: All tests pass.

### Step 4: Commit

```bash
git add plugins/pirategoat-tools/tests/test_commands.py
git commit -m "test(pirategoat-tools): Verify ingest-code-review references step injector script"
```

---

## Task 4: CHANGELOG + version bump

**Files:**
- Modify: `plugins/pirategoat-tools/CHANGELOG.md`
- Modify: `.claude-plugin/marketplace.json` (version: `1.32.4` → `1.33.0`)

### Step 1: Add CHANGELOG entry

Add at the top of the existing entries (after the `# Changelog` header and intro):

```markdown
## [1.33.0] - 2026-02-26

### Changed

- **`/ingest-code-review` uses step-by-step prompt injection** — Replaced single-pass instructions with a 6-step script-driven workflow (`scripts/ingest-code-review.py`). Claude now enforces factored verification in steps 4-5: it generates falsification questions per finding, reads the actual code with the Read tool, then answers questions independently before judging a finding. Grounded in Chain-of-Verification (Dhuliawala et al., 2023).
```

### Step 2: Bump version in `marketplace.json`

Change `"version": "1.32.4"` to `"version": "1.33.0"` in the pirategoat-tools entry.

### Step 3: Commit

```bash
git add plugins/pirategoat-tools/CHANGELOG.md .claude-plugin/marketplace.json
git commit -m "feat(pirategoat-tools): Step-by-step prompt injection for ingest-code-review"
```

---

## Verification

Run the full test suite to confirm everything works end-to-end:

```bash
pytest plugins/pirategoat-tools/tests/ -v
```

Expected output: all existing tests pass + new `test_ingest_reviewer.py` tests pass.

Spot-check the script manually:
```bash
python3 plugins/pirategoat-tools/scripts/ingest-code-review.py \
  --step-number 1 --total-steps 6 --output-dir auto --thoughts ""
```
Expected: Prints "INGEST CODE REVIEW - Step 1/6: Locate & Initialize" with setup instructions.
