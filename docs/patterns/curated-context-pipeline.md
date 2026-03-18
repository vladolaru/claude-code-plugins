# Curated Context Pipeline

A pattern for multi-step LLM workflows where a Python script acts as **context curator** — reading all state, pre-digesting information, and presenting exactly what the LLM needs at each step. The script controls flow, owns structured state, and speaks conversationally. The LLM acts on briefings, exercises judgment, and writes small handoff artifacts when synthesis is needed downstream.

Evolves the [step-by-step prompt injection](step-by-step-prompt-injection.md) pattern, which established the core mechanism (script injects one step at a time). This pattern adds context curation, file-based state, mode-driven step routing, and conversational output.

## The Core Idea

**The problem:** When an LLM workflow has multiple modes that share 80% of their logic (e.g., PR review, full branch review, incremental review), maintaining separate command files leads to drift. Improvements to one flow don't reach the others. Inline markdown instructions duplicate logic that should be centralized.

**The mechanism:** A single Python script owns the universal step sequence. Each step's guidance adapts to the current mode. The script reads all state files, extracts what's relevant, and presents it as a briefing. The LLM never reads structured files to extract values — the script does that and presents the values inline.

```
LLM calls: script --step 5 --mode pr --output-dir /tmp/review-42
  ← script reads pipeline-state.json, review-context.json, dispatch-plan.json
  ← script formats a briefing with exactly what step 5 needs
  ← script computes next step (may skip steps irrelevant to this mode)
  → LLM receives: situation + action + handoff instructions
  → LLM executes, writes any requested handoff artifacts
LLM calls: script --step 8 --mode pr --output-dir /tmp/review-42
  (jumped from 5 to 8 because steps 6-7 don't apply in PR mode)
```

## Design Principles

### 1. Goal-Oriented Pipeline

Every pipeline starts with a clear, stated goal. This goal is captured as an inline comment at the top of the script — the first thing any future editor reads. It anchors all step design, triage decisions, and tradeoff resolutions.

```python
#!/usr/bin/env python3
# PIPELINE GOAL: Deliver a complete review of code changes that is comprehensive
# in its analysis, contextual in its focus, accurate in its findings, and actionable
# in its recommendations — maintaining a high quality bar for codebases so they can
# deliver great business results and awesome user experiences.
```

The goal isn't decorative. It's the tiebreaker:
- When deciding whether to add a step: does it serve the goal?
- When deciding whether to skip a step for efficiency: does skipping hurt the goal?
- When designing a step's briefing: what does the LLM need to know to serve this goal at this moment?

Each step's output can reference the goal implicitly through its framing. Early steps build understanding ("Here's what these changes are trying to accomplish..."), execution steps deploy resources toward the goal ("These 7 agents will examine the changes from different quality angles..."), synthesis steps connect findings back to it ("Two findings directly threaten the reliability of this payment flow...").

### 1a. Pipeline Identity Anchoring

The goal comment (Principle 1) is for human editors reading the source. The LLM never sees it — it sees briefings. The pipeline's identity must live **in the conversation**, not just in the code.

**Mission at step 1.** The first briefing states who the LLM is, what it's doing, and what quality means. This is the only place with the full mission. It sets the tone for every subsequent step.

```python
_PIPELINE_MISSION = (
    "You are a code review orchestrator. Your mission: ensure the review "
    "pipeline runs to completion with dedication, precision, and care — "
    "producing a comprehensive, accurate, and actionable review..."
)
```

Step 1's situation block prepends this constant. The LLM reads it before anything mode-specific.

**Phase-transition reminders.** By step 8, the mission from step 1 has drifted deep in the context window. Rather than repeating it verbatim (banner blindness), inject **contextual variations** at phase boundaries — moments where the work shifts character:

| Transition | Step | Anchoring |
|------------|------|-----------|
| SETUP → EXECUTION | First execution step | "You understand the changes. Execute precisely." |
| EXECUTION → SYNTHESIS | First synthesis step | "Specialists are done. Bring it together faithfully." |
| SYNTHESIS → VALIDATION | First validation step | "Before this reaches a human, stress-test it." |
| VALIDATION → OUTPUT | First output step | "Deliver completely. Nothing missing." |

Each variation connects the mission to what's about to happen. They feel like natural thoughts at that moment, not bolted-on reminders.

**Mode-agnostic identity in commands.** When multiple modes share a pipeline, the command files share the same mission. Mode-specific context comes after: "This run reviews a **pull request**..." or "This run reviews **all changes on the current branch**..." The identity is stable; the context varies.

### 2. Script as Context Curator

The script's primary job is **reading files and presenting their contents** — not listing files for the LLM to read. At every step, the script loads all relevant state and presents exactly what the LLM needs to act.

**Wrong — scavenger hunt:**
```
Read review-context.json for GIT_RANGE, MERGE_BASE, and changed files.
Then read dispatch-plan.json to determine which agents completed.
```

**Right — curated briefing:**
```
Git range: abc123..def456 (14 commits, 23 files, medium PR).
Changed domains: code (18), security (3), php-tests (5).
7 of 9 dispatched agents completed. Missing: dead-code-reviewer, go-tests-reviewer.
Build the reconciliator prompt with these review files:
  - /tmp/review-42/pr-review.json
  - /tmp/review-42/security-review.json
  ...
```

The LLM should only read files when it needs to deeply understand content the script can't summarize (e.g., reading actual code, using MCP tools). If the script can read it and present it, it should.

### 2. File-Based State, Not LLM Memory

Structured data lives in files. The LLM's conversation context handles qualitative reasoning naturally — it doesn't need a `--thoughts` mechanism to remember what happened three steps ago.

| State type | Owned by | Mechanism |
|------------|----------|-----------|
| Structured data (PR number, git range, mode, step history, flags) | Script | `pipeline-state.json` |
| Context data (PR metadata, reviews, changed files, size) | Script | `review-context.json` (or domain equivalent) |
| Qualitative reasoning ("this PR fixes a payment flow bug") | LLM | Conversation context (free — already there) |
| LLM synthesis needed downstream ("change purpose summary") | LLM → file | Explicit handoff artifact (e.g., `change-purpose.txt`) |

**Why not `--thoughts`:**
- LLMs are good at reasoning, bad at bookkeeping. Asking them to faithfully maintain `STASH_REF=abc123` across 12 steps is asking them to do what scripts do better.
- Each `--thoughts` string appears in conversation context. Over N steps, that's N copies of growing state blobs — token waste.
- The LLM's conversation history already contains everything it learned. Serializing it to a string and back is redundant.
- Shell escaping of prose in CLI arguments is fragile.

### 3. One Universal Step Sequence, Mode-Driven Routing

Multiple modes of the same workflow share a single step sequence. The script decides which steps apply to each mode. Steps that don't apply are skipped via the `next` field — the LLM never calls them.

```python
STEP_SEQUENCE = [
    {"step": 1,  "title": "Parse Input",            "modes": ["all"]},
    {"step": 2,  "title": "Repo Setup",             "modes": ["pr"]},
    {"step": 3,  "title": "Gather Context",          "modes": ["all"]},
    {"step": 4,  "title": "Fetch Linear Issues",     "modes": ["data:has_linear_issues"]},
    {"step": 5,  "title": "Dispatch Plan + Triage",  "modes": ["all"]},
    ...
]
```

Conditions can be mode-based (`"pr"`, `"full"`, `"incremental"`) or data-driven (`"data:has_linear_issues"` — true when the script detects Linear issue IDs in context). Data-driven conditions let any mode activate a step when the data warrants it.

**Step skipping is transparent.** When a step is skipped, the previous step's output explains why:

```
NEXT: Step 5 — Dispatch Plan + Triage.
(Skipping steps 3-4: Repo Setup is PR-only; no Linear issues detected for issue fetching.)
Call review-pipeline.py with --step 5.
```

This keeps the LLM oriented when step numbers aren't consecutive.

### 5. Conversational Output

The script's output is language, not machine code. LLMs are language-native — they reason better when guided by natural, contextually adapted prose than by repetitive instruction templates.

**Wrong — robotic repetition:**
```
You are a thorough PR reviewer. Running the full review pipeline.
You are a thorough PR reviewer. Dispatch all agents in parallel.
You are a thorough PR reviewer. Write the review report.
```

**Right — adapted to the moment:**
```
Step 3: "This PR touches 23 files across 3 domains. The payment gateway
changes are the most sensitive — that's where review focus should land."

Step 6: "7 agents are ready to dispatch. Security and reliability reviewers
are especially relevant given the new API endpoint in src/api/payments.ts."

Step 9: "The reconciliator found 4 issues. Connect them to what this PR
is trying to accomplish — a migration to the v3 payment API — and frame
recommendations the developer can act on."
```

Each step's output adapts its framing to the phase:
- **Setup steps** build understanding: "Here's what we know..."
- **Execution steps** are directive: "Dispatch these agents..."
- **Synthesis steps** frame the task: "Connect these findings to..."
- **Validation steps** invite scrutiny: "The critic will stress-test whether..."

**Structure of each step's output:**

1. **Situation** — what you need to know right now (pre-read, pre-formatted, relevant to this step only)
2. **Action** — what to do with it (specific commands, tool calls, or judgments to make)
3. **Handoff** — what to produce for the next step, if anything (explicit file to write, or nothing)
4. **Next** — which step comes next and why (with skip explanations if non-consecutive)

**Tone:** direct, information-dense, no filler. Present facts the LLM needs to act on, not instructions to go find facts. But write as a colleague briefing a colleague, not a machine printing instructions. The script has access to all the context — use it to write output that's specific to this particular review, not generic boilerplate.

**Voice design.** Choose a specific voice for the script's personality and maintain it across all steps. The voice lives *within* the structural headers (Situation, Actions, Handoff) — don't soften the headers themselves, as they're machine-readable landmarks that help the LLM parse the briefing.

Good voice choices create a natural dynamic between the script and the LLM. For example: "senior reviewer briefing the orchestrator" gives the script authority on process while trusting the LLM on execution. The voice should feel like one side of a dialogue, not a specification document or a numbered checklist.

### 6. Explicit Handoff Points

At specific steps where the LLM produces synthesis needed by later steps, the script names the exact artifact to write:

```
HANDOFF: Write a 1-3 sentence change purpose summary to:
  /tmp/review-42/change-purpose.txt

This will be used by the reconciliator at Step 8 to anchor findings
against the developer's intent. Derive it from the commit messages
and context presented above.
```

The consuming step's script reads the handoff file and presents its contents inline. If the file is missing, the script provides a fallback:

```
Change purpose (from change-purpose.txt): "Refactors the payment gateway
to support multi-currency checkout."

-- or, if the file is missing --

Change purpose: Not available (change-purpose.txt not found).
Derive from commit messages: <script presents commit message summary here>.
```

This is resilient — the pipeline doesn't break if a handoff is missed, it degrades gracefully.

### 7. Artifact Discipline

Handoff points (Principle 6) define *what* to write. Artifact discipline defines *the contract around writing it*. The biggest failure mode in multi-step LLM pipelines isn't bad judgment — it's sloppy execution: files half-written, verification skipped, the LLM moving on without confirming its own work.

**Write → Verify → Proceed.** Every step that asks the LLM to produce a file follows this rhythm. The briefing says what to write, then says to verify the file exists, then the `handoff` section gates the next step. The LLM cannot proceed until the artifact is confirmed.

**`handoff` is the sole gate.** Requirements for "must exist before proceeding" belong in the `handoff` section, not buried in `actions`. Actions are what to do; handoff is what must be true. This structural separation makes gates scannable — the LLM always knows where to look for blocking requirements.

**Schema-not-placeholders for structured data.** When a step shows a JSON example the LLM should write, use schema format with explicit options instead of a copyable default value:

```
Wrong — the LLM copies "REQUEST_CHANGES" as the literal value:
{"verdict": "REQUEST_CHANGES"}
Valid values: APPROVE, REQUEST_CHANGES, COMMENT

Right — the LLM must choose:
{"verdict": "<APPROVE | REQUEST_CHANGES | COMMENT>"}
```

This eliminates a class of errors where the LLM fills in the template instead of making a decision.

## Script Architecture

### CLI Interface

```bash
python3 review-pipeline.py \
  --step <N> \
  --mode <pr|full|incremental> \
  --output-dir /tmp/review-42 \
  [--pr-number 123]              # PR mode only, step 1 only
  [--git-range "main..HEAD"]     # explicit range override
```

Minimal arguments. The script reads everything else from files in `--output-dir`.

No `--total-steps` (the script knows its own sequence). No `--thoughts` (state lives in files and conversation context).

### State File: `pipeline-state.json`

Written by the script at each step. Read by the script at the next step.

```json
{
  "mode": "pr",
  "current_step": 5,
  "completed_steps": [1, 2, 3],
  "skipped_steps": [4],
  "skip_reasons": {"4": "no Linear issues detected"},
  "pr_number": "123",
  "git_range": "abc123..def456",
  "has_linear_issues": false,
  "dispatched_agents": ["pr-reviewer", "security-reviewer", "..."],
  "completed_agents": [],
  "verdict": null
}
```

The script updates this file after computing each step's guidance. The LLM never reads or writes this file directly — the script handles it.

### Step Guidance Function

```python
def get_step_guidance(step: int, mode: str, state: dict, context: dict) -> dict:
    """Return guidance for a step, or None if the step is skipped in this mode.

    Args:
        step: Step number.
        mode: Pipeline mode (pr, full, incremental).
        state: Current pipeline-state.json contents.
        context: Current review-context.json contents.

    Returns:
        Dict with keys: phase, title, situation, actions, handoff, next_step, skip_reason.
        next_step is computed by scanning forward for the next applicable step.
    """
```

The function computes `next_step` by scanning forward through the step sequence, skipping steps that don't apply to the current mode/state. This is where the routing logic lives.

### Output Formatting

```python
def format_output(step: int, guidance: dict) -> str:
    lines = []
    lines.append(f"═══ REVIEW PIPELINE Step {step}: {guidance['title']} ({guidance['phase']}) ═══")
    lines.append("")

    # Situation: pre-digested context
    if guidance.get("situation"):
        for line in guidance["situation"]:
            lines.append(line)
        lines.append("")

    # Actions: what to do
    for action in guidance["actions"]:
        lines.append(action)
    lines.append("")

    # Handoff: what to produce
    if guidance.get("handoff"):
        lines.append("HANDOFF:")
        for line in guidance["handoff"]:
            lines.append(f"  {line}")
        lines.append("")

    # Next step with skip explanation
    if guidance["next_step"]:
        next_s = guidance["next_step"]
        skip_msg = ""
        if guidance.get("skip_reason"):
            skip_msg = f"\n(Skipping: {guidance['skip_reason']})"
        lines.append(
            f"NEXT: Step {next_s['step']} — {next_s['title']}.{skip_msg}\n"
            f"Call review-pipeline.py with --step {next_s['step']}."
        )
    else:
        lines.append("PIPELINE COMPLETE — Present results to user.")

    return "\n".join(lines)
```

## Command File Structure

Each command file becomes a thin wrapper — parse arguments, construct the output directory, call step 1. The command states the pipeline's mission (mode-agnostic identity), then adds mode-specific context. All modes share the same mission; the specialization comes from the `--mode` flag, not from the identity.

```markdown
---
description: <what this mode does>
---

You are a <domain> orchestrator. Your mission: ensure the pipeline runs to
completion with dedication, precision, and care — producing <quality goal>.
Every step has required artifacts; treat each as a contract. Do not
approximate, skip, or move on until the step's outputs are verified.

This run <mode-specific context>.

## Workflow

A Python script provides step-specific briefings. Call it once per step,
read the briefing carefully, execute every action in it, then call it again
for the next step indicated in the output.

Each briefing specifies required artifacts. Treat each as a contract — write
the file, verify it exists, then move on. Do not skip verification.

## Starting the Workflow

**Parse arguments:** `$ARGUMENTS`
- Extract PR number, branch name, or git range as appropriate

**Construct output directory:**
\```bash
REPO_ROOT=$(git rev-parse --show-toplevel)
SAFE_REPO_PATH=$(echo "$REPO_ROOT" | tr '/' '-' | tr -c 'a-zA-Z0-9._-' '-' | sed 's/^-//')
OUTPUT_DIR="/tmp/<pipeline>-${SAFE_REPO_PATH}-<identifier>"
mkdir -p "$OUTPUT_DIR"
\```

**Run Step 1:**
\```bash
python3 ${CLAUDE_PLUGIN_ROOT}/scripts/<pipeline>.py \
  --step 1 \
  --mode <mode> \
  --output-dir "$OUTPUT_DIR" \
  [--mode-specific-args]
\```

Execute the briefing. Then call with `--step N` where N is the next step
indicated in the output. Continue until the script signals PIPELINE COMPLETE.
```

All commands for the same pipeline use the same script. They differ only in `--mode` and which CLI arguments they pass.

## Comparison with Step-by-Step Prompt Injection

| Aspect | Step-by-Step Prompt Injection | Curated Context Pipeline |
|--------|-------------------------------|--------------------------|
| State mechanism | `--thoughts` (LLM carries all state) | File-based (`pipeline-state.json`) |
| Context delivery | Instructions to read files | Pre-digested briefings |
| Step routing | Always sequential (step N → N+1) | Mode-driven skipping via `next` |
| Multi-mode support | Not addressed | Core feature — one sequence, many modes |
| Script role | Instruction injector | Context curator + state manager + flow controller |
| LLM file reads | Frequent (extract values from JSON) | Rare (only for deep content understanding) |
| Output tone | Task list | Conversational briefing |

The step-by-step prompt injection pattern remains valid for simpler cases — single-mode workflows where epistemic boundaries are the primary concern (e.g., the decision critic). The curated context pipeline is for multi-mode operational workflows where context management and flow control matter as much as reasoning discipline.

## When to Apply

**Good fit:**
- Multiple modes sharing the same core workflow (the modes differ in scope or input, not in logic)
- Steps that need rich context from previous steps' outputs (files, API responses, computed values)
- Workflows where the LLM dispatches subagents or external tools and needs to track their results
- Long pipelines (8+ steps) where token efficiency matters

**Poor fit:**
- Single-mode analytical workflows where epistemic boundaries are the main concern (use step-by-step prompt injection instead)
- Short workflows (3-4 steps) where inline command instructions are simpler
- Workflows with no shared state between steps

## Implementation Checklist

**Design**
- [ ] Map the universal step sequence — every step that any mode needs
- [ ] For each step, define: which modes it applies to, what data-driven conditions activate it
- [ ] Identify handoff points — which steps produce LLM synthesis needed by later steps
- [ ] Identify what state the script needs to track in `pipeline-state.json`
- [ ] Define pipeline mission statement and phase-transition texts
- [ ] Choose a voice for the script's briefings

**Script**
- [ ] `get_step_guidance()` returns guidance or `None` (skipped) for each step/mode combination
- [ ] `next_step` is computed by scanning forward, not hardcoded
- [ ] Skip reasons are human-readable and included in output
- [ ] Every step's situation section pre-digests all relevant state from files
- [ ] Handoff instructions name exact file paths and describe what to write
- [ ] Fallback behavior when handoff files are missing
- [ ] `pipeline-state.json` updated at each step
- [ ] Stale state detection (e.g., output dir from a previous run)
- [ ] Mission injected at step 1, phase transitions at phase-entry steps
- [ ] File-producing steps: verification checkpoint + `handoff` gate
- [ ] JSON examples use schema format, not copyable placeholder values

**Command files**
- [ ] Thin wrappers — parse arguments, construct output dir, call step 1
- [ ] All modes reference the same script
- [ ] No duplicated logic between command files
- [ ] Unified mission language across all mode commands

**Testing**
- [ ] Each step's guidance tested for each mode (including skip behavior)
- [ ] `next_step` routing tested for each mode (verify correct skips and jump explanations)
- [ ] State file read/write round-trip tested
- [ ] Handoff file missing → fallback behavior tested
- [ ] CLI integration: each mode runs step 1 successfully
- [ ] Mission present in step 1 output for all modes
- [ ] Phase-transition text present at each phase-entry step
- [ ] File-producing steps have `handoff` (not None)
- [ ] No copyable placeholder values in JSON examples

## Risks

| Risk | Mitigation |
|------|------------|
| Script output too long — bloats LLM context | Budget situation sections. Summarize, don't dump. Set max lines per section. |
| Handoff file not written by LLM | Script provides fallback at consuming step. Pipeline degrades, doesn't break. |
| Stale `pipeline-state.json` from crashed run | Script checks timestamps or clears state at step 1 for fresh runs. |
| Mode proliferation — too many conditions | Keep modes to 3-5. Prefer data-driven conditions over new mode flags. |
| Step sequence changes require updating tests | Parameterize tests over the step sequence, don't hardcode step numbers. |
