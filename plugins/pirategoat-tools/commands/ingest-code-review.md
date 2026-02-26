---
description: Analyze code review findings critically, validate against actual changes, filter false positives, and propose an action plan
---

You are a senior engineer ingesting review findings from a prior `/code-review` or `/full-code-review` run. Your job is NOT to blindly accept every finding — it is to **think critically**, validate each finding against the actual code, filter out false positives and out-of-scope noise, and propose a focused plan for what genuinely needs fixing.

**Mindset:** Review agents are thorough but imperfect. They sometimes flag pre-existing code, misunderstand intent, or report stylistic preferences as issues. Your value is in separating signal from noise.

## Workflow

This command uses step-by-step prompt injection to enforce analytical discipline — especially **factored verification** (reading the actual code before judging a finding). The workflow runs 6 steps across 4 phases: SETUP → SCOPE → VERIFICATION → SYNTHESIS.

## Phase Overview

| Phase | Steps | What happens |
|---|---|---|
| SETUP | 1–2 | Locate review output, parse findings, assign stable IDs (F1, F2…) |
| SCOPE | 3 | Compare each finding against CHANGED_FILES and diff hunks; mark IN_SCOPE or OUT_OF_SCOPE |
| VERIFICATION | 4–5 | Generate falsification questions; answer with factored verification (VERIFIED / FAILED / UNCERTAIN) |
| SYNTHESIS | 6 | Categorize: CONFIRMED, LIKELY VALID, FALSE POSITIVE, OUT OF SCOPE, STYLE/PREFERENCE; produce Action Plan |

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
| `--step-number` | Yes | Current step (1–6) |
| `--total-steps` | Yes | Always 6 |
| `--output-dir` | Step 1 only | Path to review output directory, or `auto` to detect from current branch. Steps 2-6 read it from `--thoughts` instead. |
| `--thoughts` | Yes | All accumulated state (finding IDs, scope status, verification results). Pass `""` on step 1. |

## Starting the Workflow

You will run `scripts/ingest-code-review.py` once per step. The script prints the instructions for that step; you execute them, then call the script again for the next step. Six calls total.

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

**If the script exits with an error** (e.g., "No review output found at …"), STOP. Report the error to the user and do not proceed to Step 2.

**After each step — repeat until Step 6 completes:**
1. Read the instructions printed by the script
2. Execute those instructions completely
3. Call the script again with `--step-number N+1` and ALL accumulated state in `--thoughts`
