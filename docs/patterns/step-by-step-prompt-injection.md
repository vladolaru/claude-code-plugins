# Step-by-Step Prompt Injection

A pattern for enforcing multi-step analytical discipline in Claude Code skills and commands. Instead of relying on Claude to follow all instructions in one pass, a Python CLI script injects step-specific instructions one at a time — preventing premature synthesis, enforcing epistemic boundaries, and accumulating state explicitly between phases.

## The Core Idea

**The problem:** When a skill has multiple analytical phases (e.g., gather evidence → verify → judge), Claude will collapse them into a single pass. It evaluates while gathering, decides while verifying. This is fast but produces lower-quality reasoning — especially when later steps require conclusions that are independent of each other.

**The mechanism:** A Python script acts as a step injector. Claude calls it once per step. The script prints the instructions for that step and nothing else. Claude executes those instructions, captures all results and intermediate state in a `--thoughts` argument, then calls the script again for the next step. The instructions for step N don't appear until step N-1 is complete.

```
Claude calls: script --step-number 1 --thoughts ""
  → script prints: instructions for step 1 only
  → Claude executes step 1, records state in --thoughts
Claude calls: script --step-number 2 --thoughts "<step 1 state>"
  → script prints: instructions for step 2 only
  → Claude executes step 2, adds to --thoughts
... and so on
```

## When to Apply It

The pattern pays off when **all three** of these hold:

1. **Analytically distinct phases** — the steps require genuinely different cognitive modes, not just sequential operations. "Gather context → run a command → show output" is sequential. "Gather evidence → independently verify each claim → synthesize verdict" is analytically distinct.

2. **Epistemic boundary needed** — at least one step requires Claude to form an answer *before* knowing how it will affect the final conclusion. The classic case: "read the code at this location and describe what it does" must happen before "decide if this finding is accurate." If both happen in one pass, Claude reads the code *looking for* the issue and finds it even when it's not there.

3. **State accumulation with stable IDs** — intermediate results have identifiers (F1/F2, C1/A1, finding IDs) that later steps reference. Without IDs, the state can't be reliably threaded forward.

### Fit signals

- The skill currently says things like "for each X, do A, then B, then C" — and A's result should influence B independently
- There's a verification or validation step where confirmation bias is a real risk
- Multiple agents produce inputs that need independent cross-checking before a verdict
- The skill has a natural "stop and think before you judge" moment

### Poor fit signals

- Steps are sequential operations: fetch → transform → output (no analytical independence)
- No state to accumulate across steps (each step is self-contained)
- The skill is a one-shot reference lookup
- Already has external scripts doing the heavy lifting; adding another layer is redundant

## Script Structure

The script is a pure Python CLI — no external dependencies, no model calls. It takes a step number, returns instructions for that step, and exits.

```python
#!/usr/bin/env python3
"""
<Skill Name> - Step-by-step prompt injection for <purpose>.

Grounded in:
- Chain-of-Verification (Dhuliawala et al., 2023)   # if factored verification is used
- Multi-Expert Prompting (Wang et al., 2024)         # if adversarial/multi-perspective used
"""

import argparse
import sys
from typing import Optional


def get_phase_name(step: int) -> str:
    """Return the phase name for a given step number."""
    if step <= 2:
        return "PHASE_A"
    elif step <= 4:
        return "PHASE_B"
    else:
        return "PHASE_C"


# Inject into steps 2-N. Be specific about what state must be carried forward.
state_requirement = (
    "CONTEXT REQUIREMENT: Your --thoughts from this step must include ALL <item> IDs, "
    "their <classification> status, and <any other state later steps need>. "
    "This accumulated state is essential for workflow continuity."
)


def get_step_guidance(step: int, total_steps: int, primary_input: Optional[str], thoughts: Optional[str]) -> dict:
    next_step = step + 1 if step < total_steps else None

    if step == 1:
        return {
            "phase": get_phase_name(step),
            "step_title": "Step One Title",
            "actions": [
                # Concrete instructions. Shell commands Claude should run. Output format.
                # No state_requirement here — nothing to carry forward yet.
            ],
            "next": f"Step {next_step}: <what step 2 does>.",
            "academic_note": None,
        }

    if step == 2:
        return {
            "phase": get_phase_name(step),
            "step_title": "Step Two Title",
            "actions": [
                # Instructions...
                "",
                state_requirement,
            ],
            "next": f"Step {next_step}: <what step 3 does>.",
            "academic_note": None,
        }

    # ... remaining steps

    if step == total_steps:
        return {
            "phase": get_phase_name(step),
            "step_title": "Final Step Title",
            "actions": [
                # Synthesis instructions...
                "",
                state_requirement,
            ],
            "next": None,           # Signals end of workflow
            "academic_note": "Optional citation for the technique used in this step.",
        }

    return {
        "phase": "UNKNOWN",
        "step_title": "Unknown Step",
        "actions": ["Invalid step number."],
        "next": None,
        "academic_note": None,
    }


def format_output(step: int, total_steps: int, guidance: dict) -> str:
    lines = []
    lines.append(f"═══ <SKILL NAME> Step {step}/{total_steps}: {guidance['step_title']} ({guidance['phase']}) ═══")
    lines.append("")
    for action in guidance["actions"]:
        lines.append(action)
    lines.append("")
    if guidance.get("academic_note"):
        lines.append(f"[{guidance['academic_note']}]")
        lines.append("")
    if guidance["next"]:
        lines.append(f"NEXT (MANDATORY): {guidance['next']} Do NOT stop — call <script>.py with --step-number {step + 1} immediately.")
    else:
        lines.append("PIPELINE COMPLETE — Present <verdict/plan/output> to user.")
    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(description="<Skill Name> - Step-by-step prompt injection")
    parser.add_argument("--step-number", type=int, required=True)
    parser.add_argument("--total-steps", type=int, required=True)
    parser.add_argument("--primary-input", type=str)   # Required for step 1; name it for your domain
    parser.add_argument("--thoughts", type=str, required=True)
    args = parser.parse_args()

    if args.step_number < 1 or args.step_number > args.total_steps:
        print(f"ERROR: step-number must be between 1 and {args.total_steps}", file=sys.stderr)
        sys.exit(1)

    if args.step_number == 1 and not args.primary_input:
        print("ERROR: --primary-input is required for step 1", file=sys.stderr)
        sys.exit(1)

    guidance = get_step_guidance(args.step_number, args.total_steps, args.primary_input, args.thoughts)

    if args.step_number == 1 and args.primary_input:
        print(f"INPUT: {args.primary_input}")
        print()

    print(format_output(args.step_number, args.total_steps, guidance))


if __name__ == "__main__":
    main()
```

### Naming conventions

| Element | Convention | Example |
|---|---|---|
| Script filename | `scripts/<skill-name>.py` | `scripts/ingest-code-review.py` |
| Header prefix | `<SKILL NAME> - Step N/M:` | `INGEST CODE REVIEW - Step 3/6:` |
| Primary input arg | Named for the domain | `--decision`, `--output-dir`, `--issue-id` |
| Step count | Always `--total-steps` | `--total-steps 6` |
| State arg | Always `--thoughts` | `--thoughts "<accumulated state>"` |

## Skill/Command File Structure

The skill or command file becomes a thin orchestration wrapper. It does not contain the step instructions (those live in the script). It contains:

1. **Frontmatter** — unchanged (description, name)
2. **Role and mindset** — one paragraph, preserved from any prior version
3. **Workflow summary** — one paragraph naming the phases
4. **Phase overview table** — maps steps to phases, describes each phase in one line
5. **Invocation table** — documents all CLI args with Required column
6. **Starting instructions** — how to parse `$ARGUMENTS` and call step 1
7. **Loop instruction** — explicit: "call --step-number N+1 and pass ALL accumulated state in --thoughts"

```markdown
---
description: <unchanged description>
---

<Role statement. Mindset paragraph.>

## Workflow

<1-2 sentences explaining the step-injection approach and the N phases.>

## Phase Overview

| Phase | Steps | What happens |
|---|---|---|
| PHASE_A | 1-2 | <description including key outputs and IDs assigned> |
| PHASE_B | 3-4 | <description including what state is consumed and produced> |
| PHASE_C | 5   | <description of synthesis and output format> |

## Invocation

\```bash
python3 scripts/<skill-name>.py \
  --step-number <1-N> \
  --total-steps N \
  --primary-input "<domain-specific input>" \
  --thoughts "<accumulated state from all previous steps>"
\```

| Argument | Required | Description |
|---|---|---|
| `--step-number` | Yes | Current step (1-N) |
| `--total-steps` | Yes | Always N |
| `--primary-input` | Step 1 only | <what it represents>. Steps 2-N read it from `--thoughts` instead. |
| `--thoughts` | Yes | All accumulated state. Pass `""` on step 1. |

## Starting the Workflow

**Parse arguments:** `$ARGUMENTS`
- If <condition>: use as `--primary-input`
- If empty: <default>

**Run Step 1:**

\```bash
python3 scripts/<skill-name>.py \
  --step-number 1 \
  --total-steps N \
  --primary-input "<parsed from $ARGUMENTS>" \
  --thoughts ""
\```

Execute the instructions printed by the script. After completing each step's work, call the script with `--step-number N+1` and pass ALL accumulated state in `--thoughts`. Continue until Step N completes.
```

## The Key Mechanisms

### 1. `--thoughts` as explicit state bus

Claude must write down everything subsequent steps need. If step 3 assigns IDs `F1, F2, F3` with scope status, those IDs must appear verbatim in `--thoughts` when calling step 4. The script does not store state — `--thoughts` is the only continuity mechanism.

**What to include in `state_requirement`:**
- The names of all ID types assigned in step 1-2 (e.g., "F1, F2...", "C1, A1...")
- Every classification/status marker subsequent steps depend on (e.g., IN_SCOPE/OUT_OF_SCOPE, VERIFIED/FAILED/UNCERTAIN)
- Any computed values later steps reference (e.g., OUTPUT_DIR, GIT_RANGE, CHANGED_FILES)
- Question-answer pairs if step N uses them to synthesize step N+2

### 2. `state_requirement` in steps 2-N

Every step except step 1 appends the `state_requirement` string to its `actions` list. This is injected into the step instructions Claude sees, explicitly reminding it to carry all prior state forward. Without this, Claude summarizes state and loses the IDs and status markers.

Step 1 omits it — there's no prior state to preserve yet.

### 3. Epistemic boundaries

The highest-value use of this pattern is enforcing a boundary where Claude must form an independent answer *before* knowing how it affects the conclusion. The pattern is:

- Step N: Generate questions (what would we need to check to know if X is true?)
- Step N+1: Answer those questions independently, using only specified evidence sources
- Step N+2: Apply the answers to form a verdict

**Specifying the epistemic boundary in step N+1:**

```python
"EPISTEMIC BOUNDARY (critical for avoiding confirmation bias):",
"",
"  Answer using ONLY:",
"    (a) The actual <artifact> at the referenced location",
"    (b) Stated constraints from --thoughts",
"    (c) Established domain knowledge — only when (a) and (b) are insufficient",
"",
"  Do NOT:",
"    - Assume the claim is correct and work backward",
"    - Assume the claim is incorrect and seek to disprove",
```

Key: specify *what* Claude is allowed to use as evidence, not just what it should avoid.

### 4. The `next` pointer

Each step ends with `NEXT (MANDATORY): Step N+1: <brief description>. Do NOT stop — call <script>.py with --step-number N+1 immediately.` This nudges Claude to continue the loop rather than treating step N as the end of the workflow. Step `total_steps` has `next=None`, which prints `PIPELINE COMPLETE` and signals termination.

### 5. Academic notes

Steps that implement a research-backed technique include a one-line citation. This serves two purposes: it reminds Claude *why* the step's discipline matters (not just what to do), and it signals that the step structure is intentional, not arbitrary.

Used for:
- Factored verification: Chain-of-Verification (Dhuliawala et al., 2023)
- Adversarial/contrarian steps: Multi-Expert Prompting (Wang et al., 2024)
- Final synthesis consistency: Self-Consistency (Wang et al., 2023)

Not every step needs a citation. Use only where the technique is directly applied.

## Two Reference Implementations

### decision-critic (7 steps)

**Domain:** Decision validation
**Primary input:** `--decision "<decision statement>"` + `--context "<constraints>"`
**Phases:** DECOMPOSITION (1-2) → VERIFICATION (3-4) → CHALLENGE (5-6) → SYNTHESIS (7)

| Step | Title | Key mechanism |
|---|---|---|
| 1 | Extract Structure | Assigns C/A/K/J IDs to claims, assumptions, constraints, judgments |
| 2 | Classify Verifiability | Tags each ID as [V]/[J]/[C]; edge case rule: prefer [V] |
| 3 | Generate Verification Questions | Falsification-focused questions per [V] item; 1-3 per item |
| 4 | Factored Verification | **Epistemic boundary step** — answers Q independently from the decision's correctness |
| 5 | Contrarian Perspective | Steel-man the opposition using FAILED/UNCERTAIN items as ammunition |
| 6 | Alternative Framing | Challenges the problem statement, not just the solution |
| 7 | Synthesis and Verdict | STAND / REVISE / ESCALATE with rubric tied to step 4 statuses |

**What makes it work:** The separation of step 3 (generate questions) and step 4 (answer them) prevents Claude from generating easy questions for findings it has already decided to confirm.

### ingest-code-review (6 steps)

**Domain:** Code review finding validation
**Primary input:** `--output-dir "<path to review output directory>"`
**Phases:** SETUP (1-2) → SCOPE (3) → VERIFICATION (4-5) → SYNTHESIS (6)

| Step | Title | Key mechanism |
|---|---|---|
| 1 | Locate & Initialize | Runs bash commands, establishes OUTPUT_DIR/GIT_RANGE/CHANGED_FILES in --thoughts |
| 2 | Parse Findings & Assign IDs | Reads reconciled review, assigns F1/F2/... IDs with file/line/severity/agents |
| 3 | Classify Scope | Compares each finding against CHANGED_FILES + diff hunks; marks IN_SCOPE/OUT_OF_SCOPE |
| 4 | Generate Verification Questions | 1-2 falsification questions per IN_SCOPE finding |
| 5 | Factored Verification | **Epistemic boundary step** — reads actual code via Read tool before judging finding accuracy |
| 6 | Categorize & Plan | Maps scope+verification to CONFIRMED/LIKELY VALID/FALSE POSITIVE/OUT OF SCOPE/STYLE; produces Action Plan |

**What makes it work:** Step 5's epistemic boundary requires Claude to read the code *before* knowing whether the finding is confirmed. Without the boundary, Claude reads the code *looking for* the issue and finds it even when it's not there.

### Side-by-side comparison

| Aspect | decision-critic | ingest-code-review |
|---|---|---|
| ID scheme | C1/A1/K1/J1 (typed) | F1/F2/F3 (flat) |
| Primary input arg | `--decision` | `--output-dir` |
| Step 1 purpose | Parse decision text | Run bash commands |
| Epistemic boundary | Step 4 | Step 5 |
| Evidence source | Established domain knowledge | Actual code via Read tool |
| Final output | STAND/REVISE/ESCALATE | Action plan with priority tiers |
| Step count | 7 | 6 |

## Testing Pattern

Follow TDD. Write tests before implementing the script. The test file goes in `tests/test_<skill-name>.py`.

### Required test classes

```python
class TestGetPhaseName:
    """One test per phase boundary."""
    def test_setup_steps(self): ...
    def test_scope_step(self): ...

class TestGetStepGuidance:
    """One test per step title (exact string match).
    Tests for required content in key steps."""
    def test_step_N_title(self): ...
    def test_steps_2_to_N_have_state_requirement(self):
        for step in range(2, total_steps + 1):
            g = _mod.get_step_guidance(step, total_steps, None, "state")
            assert "CONTEXT REQUIREMENT" in "\n".join(g["actions"])
    def test_step_1_no_state_requirement(self): ...
    def test_epistemic_boundary_step_mentions_boundary(self): ...
    def test_epistemic_boundary_step_mentions_evidence_source(self): ...
    def test_final_step_mentions_all_output_categories(self): ...

class TestFormatOutput:
    """Header format (═══ separators), academic note, MANDATORY next pointer, PIPELINE COMPLETE."""

class TestCLIIntegration:
    """Subprocess tests — exit codes, required args, all steps produce ═══ header."""
```

### Key assertions to make (not just structure)

- `"CONTEXT REQUIREMENT" in actions_text` for each step 2-N
- `"EPISTEMIC BOUNDARY" in actions_text` for the verification step
- `"<evidence source tool/method>" in actions_text` for the verification step
- `"Do NOT assume" in actions_text` for the verification step
- Every output category name present in the synthesis step
- `g["next"] is None` for the final step
- `g["next"] is not None` for all other steps

### What NOT to test

- The exact wording of instructions (brittle; breaks on any refinement)
- That `--thoughts` content is parsed (the script doesn't parse it; Claude does)
- The academic note text verbatim (citation formatting may evolve)

## Implementation Checklist

When applying this pattern to a new skill:

**Analysis**
- [ ] Identify the analytically distinct phases (3-7 phases is the sweet spot)
- [ ] Identify the epistemic boundary step (where Claude must form an answer before knowing the conclusion)
- [ ] Identify the state to accumulate: what IDs get assigned in step 1-2, what statuses get assigned in the verification phase, what the synthesis step needs from all prior steps

**Script**
- [ ] Name it `scripts/<skill-name>.py`
- [ ] Implement `get_phase_name()`, `get_step_guidance()`, `format_output()`, `main()`
- [ ] Header prefix matches skill name
- [ ] `state_requirement` constant names every ID type and status marker exactly
- [ ] Epistemic boundary step specifies allowed evidence sources, not just prohibitions
- [ ] Verification step includes a concrete example (e.g., a real finding + two example questions)
- [ ] Synthesis step names every output category and includes a tie-breaker rule for ambiguous cases
- [ ] Step N's `next` uses `f"Step {next_step}: ..."` (not hardcoded)
- [ ] `next=None` on final step
- [ ] `--thoughts` is required by argparse
- [ ] Primary input arg is required only at step 1 (validated in `main()`, not in argparse)

**Skill/command file**
- [ ] Phase overview table present (steps → phases → descriptions)
- [ ] Invocation table documents all args with Required column
- [ ] `--primary-input`: clarify "Step 1 only; steps 2-N read from --thoughts"
- [ ] `--thoughts`: clarify "pass `""` on step 1"
- [ ] Loop instruction present: "call --step-number N+1 with ALL accumulated state in --thoughts"
- [ ] Preserves any strings that existing tests check for

**Tests**
- [ ] Test file at `tests/test_<skill-name>.py`
- [ ] Write tests first, verify they fail, then implement
- [ ] All four test classes present
- [ ] State requirement tested for all steps 2-N
- [ ] Epistemic boundary content tested (not just presence)
- [ ] Output categories tested on synthesis step
- [ ] CLI integration tests cover: step 1 exits 0, primary input required at step 1, invalid step exits 1, all steps produce Phase header

## Risks

| Risk | Mitigation |
|---|---|
| State fidelity degradation — Claude summarizes --thoughts and loses IDs | Make `state_requirement` list every ID type and status explicitly by name |
| Epistemic boundary too loose — item (c) permits domain knowledge to substitute for reading actual artifacts | Qualify (c) as "only when (a) and (b) are insufficient"; give an example of the distinction |
| Synthesis category ambiguity — STYLE/PREF overlaps CONFIRMED | Add explicit tie-breaker rule: "when uncertain, use the more severe category" |
| Branching workflows don't fit a linear step sequence | If a skill has conditional paths (e.g., bug vs. feature), model as a single branching step (step 2 detects type, sets a TYPE marker in --thoughts, and following steps check it) — do not try to create separate step sequences per path |
| Latency multiplication | Each step is a separate invocation. Acceptable for quality-critical workflows; don't apply to fast reference skills |
| Script/file sync drift | Any workflow change requires updating both the script and the skill file. The `TestScriptReferences` test class catches the file existence issue; add workflow description tests to `test_commands.py` to catch content drift |
