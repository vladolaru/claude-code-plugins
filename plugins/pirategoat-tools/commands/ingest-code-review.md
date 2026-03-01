---
description: Analyze code review findings critically, validate against actual changes, filter false positives, and propose an action plan
---

You are a senior engineer ingesting review findings from a prior `/code-review` or `/full-code-review` run. Your job is NOT to blindly accept every finding — it is to **think critically**, validate each finding against the actual code, filter out false positives and out-of-scope noise, and propose a focused plan for what genuinely needs fixing.

**Mindset:** Review agents are thorough but imperfect. They sometimes flag pre-existing code, misunderstand intent, or report stylistic preferences as issues. Your value is in separating signal from noise.

## Workflow

This command uses a two-phase approach:

1. **Deterministic preprocessing** (`ingest-preprocess.py`): Scope checking, ID assignment, and pre-classification — no LLM needed.
2. **LLM verification** (`ingest-code-review.py`): 3 steps of verification and synthesis — only for findings that need human-level judgment.

The preprocessor reduces the pipeline from 6 LLM steps to 3 by handling the mechanical work (scope checking against CHANGED_FILES and diff hunks, stable ID assignment, pre-classification) deterministically.

## Phase Overview

| Phase | Steps | What happens |
|---|---|---|
| PREPROCESS | (script) | `ingest-preprocess.py` assigns IDs, checks scope against diff hunks, pre-classifies findings |
| VERIFICATION | 1–2 | Generate falsification questions for IN_SCOPE findings; answer with factored verification (VERIFIED / FAILED / UNCERTAIN) |
| SYNTHESIS | 3 | Categorize: CONFIRMED, LIKELY VALID, FALSE POSITIVE, OUT OF SCOPE, STYLE/PREFERENCE; produce Action Plan |

## Starting the Workflow

**Parse arguments:** `$ARGUMENTS`
- If a path is provided: use it as OUTPUT_DIR
- If empty: detect from current branch:
  ```bash
  BRANCH=$(git branch --show-current)
  BRANCH_SAFE=$(echo "$BRANCH" | tr '/' '-' | sed 's/^-//')
  OUTPUT_DIR="/tmp/branch-review-${BRANCH_SAFE}"
  ```

### Step 0: Run the Preprocessor

Determine the git range:
```bash
# Try reading from review state file
GIT_RANGE=$(cat "${OUTPUT_DIR}/.review-state.json" 2>/dev/null | python3 -c "import sys,json; print(json.load(sys.stdin).get('git_range_used',''))" 2>/dev/null)

# Fallback: compute from branch
if [ -z "$GIT_RANGE" ]; then
  DEFAULT_BRANCH=$(git symbolic-ref refs/remotes/origin/HEAD 2>/dev/null | sed 's@^refs/remotes/origin/@@')
  GIT_RANGE="${DEFAULT_BRANCH:-main}..HEAD"
fi
```

Run the preprocessor:
```bash
python3 scripts/ingest-preprocess.py \
  --output-dir "${OUTPUT_DIR}" \
  --git-range "${GIT_RANGE}"
```

**If the preprocessor exits with an error** (e.g., "No reconciled findings found"), STOP. Report the error to the user. They need to run `/code-review` or `/full-code-review` first.

**If successful**, read the preprocessor summary output. It reports how many findings are in scope vs. out of scope. If `ingest-preprocessed.json` was created, proceed to Step 1.

### Step 1: Generate Verification Questions

```bash
python3 scripts/ingest-code-review.py \
  --step-number 1 \
  --total-steps 3 \
  --output-dir "${OUTPUT_DIR}" \
  --thoughts ""
```

Read the instructions printed by the script and execute them. The script tells you to read `ingest-preprocessed.json` and generate falsification questions for each IN_SCOPE finding.

### Steps 2–3: Continue the Workflow

After each step:
1. Read the instructions printed by the script
2. Execute those instructions completely
3. Call the script again with `--step-number N+1` and ALL accumulated state in `--thoughts`

```bash
python3 scripts/ingest-code-review.py \
  --step-number <2-3> \
  --total-steps 3 \
  --output-dir "${OUTPUT_DIR}" \
  --thoughts "<your accumulated findings, IDs, and statuses from all previous steps>"
```

Three calls total (steps 1–3). The workflow completes when step 3 produces the categorized Action Plan.

## Invocation Reference

| Argument | Required | Description |
| --- | --- | --- |
| `--step-number` | Yes | Current step (1–3) |
| `--total-steps` | Yes | Always 3 (preprocessed mode) |
| `--output-dir` | Yes | Path to review output directory. Used to locate `ingest-preprocessed.json`. |
| `--thoughts` | Yes | All accumulated state (finding IDs, scope status, verification results). Pass `""` on step 1. |

## Legacy Mode (Fallback)

If `ingest-preprocessed.json` does not exist in the output directory (e.g., preprocessing was skipped), fall back to the original 6-step workflow:

```bash
python3 scripts/ingest-code-review.py \
  --step-number 1 \
  --total-steps 6 \
  --output-dir "<path or 'auto'>" \
  --thoughts ""
```

In legacy mode, run steps 1–6. The script handles setup, scope checking, verification, and synthesis all via LLM steps. Six calls total.
