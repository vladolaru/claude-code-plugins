# Curated Context Pipeline

A pattern for multi-step LLM workflows where a Python script acts as **context curator** — reading all state, pre-digesting information, and presenting exactly what the LLM needs at each step. The script controls flow, owns structured state, and speaks conversationally. The LLM acts on briefings, exercises judgment, and writes small handoff artifacts when synthesis is needed downstream.

Evolves the [step-by-step prompt injection](step-by-step-prompt-injection.md) pattern, which established the core mechanism (script injects one step at a time). This pattern adds context curation, file-based state, condition-driven step routing, and conversational output.

**Running example.** Throughout this document, a multi-agent code review pipeline illustrates the principles. Examples from this reference implementation are marked with "> **Example:**" blocks. The principles themselves are general — they apply to any multi-step LLM workflow with shared modes and rich context.

## The Core Idea

**The problem:** When an LLM workflow has multiple modes that share 80% of their logic, maintaining separate command files leads to drift. Improvements to one flow don't reach the others. Inline markdown instructions duplicate logic that should be centralized.

**The mechanism:** A single Python script owns the universal step sequence. Each step's guidance adapts to the current mode. The script reads all state files, extracts what's relevant, and presents it as a briefing. The LLM never reads structured files to extract values — the script does that and presents the values inline.

```
LLM calls: script --step N --mode <mode> --output-dir /tmp/run-42
  ← script reads pipeline-state.json, context files
  ← script formats a briefing with exactly what step N needs
  ← script computes next step (may skip steps irrelevant to this mode/state)
  → LLM receives: situation + actions + handoff instructions
  → LLM executes, writes any requested handoff artifacts
LLM calls: script --step M --mode <mode> --output-dir /tmp/run-42
  (jumped from N to M because intermediate steps' conditions weren't met)
```

## Design Principles

### 1. Goal-Oriented Pipeline (Source Code)

Every pipeline starts with a clear, stated goal. This goal is captured as a docstring or inline comment at the top of the script — the first thing any future editor reads. It anchors all step design, triage decisions, and tradeoff resolutions.

The goal isn't decorative. It's the tiebreaker:
- When deciding whether to add a step: does it serve the goal?
- When deciding whether to skip a step for efficiency: does skipping hurt the goal?
- When designing a step's briefing: what does the LLM need to know to serve this goal at this moment?

Each step's output can reference the goal implicitly through its framing. Early steps build understanding, execution steps deploy resources toward the goal, synthesis steps connect findings back to it.

> **Example:** A code review pipeline's goal: "Deliver a complete review of code changes that is comprehensive in its analysis, contextual in its focus, accurate in its findings, and actionable in its recommendations." This goal resolves questions like: should we add a linting step? (Only if linting serves accuracy. If CI already catches lint errors, no — it duplicates without serving the goal.)

### 2. Pipeline Identity Anchoring (Conversation)

The goal comment (Principle 1) anchors editors reading the source — both human and LLM agents modifying the pipeline. But the LLM *executing* the pipeline never reads the source; it calls the script via subprocess and only sees its stdout. The pipeline's identity must live **in the conversation** (the briefing output), not just in the code.

**Mission at step 1.** The first briefing states who the LLM is, what it's doing, and what quality means. This is the only place with the full mission. It sets the tone for every subsequent step. Store the mission as a constant in the script and prepend it to step 1's situation block — the LLM reads it before anything mode-specific.

**Phase-transition reminders.** In long pipelines, the mission from step 1 drifts deep in the context window. Rather than repeating it verbatim (which causes banner blindness), inject **contextual variations** at phase boundaries — moments where the work shifts character. Each variation connects the mission to what's about to happen, feeling like a natural thought at that moment rather than a bolted-on reminder.

The general pattern: pipelines have phases (setup, execution, synthesis, validation, output — or whatever phases your domain requires). At the first step of each new phase, prepend a one-sentence anchor that connects the mission to the upcoming work.

**Mode-agnostic identity in commands.** When multiple modes share a pipeline, the command files share the same mission. Mode-specific context comes after. The identity is stable; the context varies.

> **Example:** A code review pipeline defines its mission: "You are a code review orchestrator. Your mission: ensure the review pipeline runs to completion with dedication, precision, and care — producing a comprehensive, accurate, and actionable review..." The four phase transitions anchor on: precision in dispatch (EXECUTION), faithful synthesis (SYNTHESIS), stress-testing before it reaches a human (VALIDATION), and complete delivery (OUTPUT). Command files share the mission; PR mode adds "This run reviews a **pull request**...", branch mode adds "This run reviews **all changes on the current branch**..."

### 3. Script as Context Curator

The script's primary job is **reading files and presenting their contents** — not listing files for the LLM to read. At every step, the script loads all relevant state and presents exactly what the LLM needs to act.

**Wrong — scavenger hunt:**
```
Read context.json for the input parameters and changed files.
Then read plan.json to determine which workers completed.
```

**Right — curated briefing:**
```
Input range: abc123..def456 (14 items, 23 files).
7 of 9 dispatched workers completed. Missing: worker-A, worker-B.
Completed output files:
  - /tmp/run-42/worker-C-output.json
  - /tmp/run-42/worker-D-output.json
```

The LLM should only read files when it needs to deeply understand content the script can't summarize (e.g., reading actual code, using MCP tools). If the script can read it and present it, it should.

### 4. File-Based State, Not LLM Memory

Structured data lives in files. The LLM's conversation context handles qualitative reasoning naturally — it doesn't need a `--thoughts` mechanism to remember what happened three steps ago.

| State type | Owned by | Mechanism |
|------------|----------|-----------|
| Caller config (mode, identifiers, feature flags) | Caller → script | `run-config.json` — written once, read-only during run |
| Execution state (step history, resolved params, worker status) | Script | `pipeline-state.json` — updated at each step |
| Gathered context (domain-specific input data) | Script | Context file (e.g., `context.json`) |
| Qualitative reasoning ("this change fixes a payment flow bug") | LLM | Conversation context (free — already there) |
| LLM synthesis needed downstream ("purpose summary") | LLM → file | Explicit handoff artifact (e.g., `change-purpose.md`) |

**Why not `--thoughts`:**
- LLMs are good at reasoning, bad at bookkeeping. Asking them to faithfully maintain `STASH_REF=abc123` across 12 steps is asking them to do what scripts do better.
- Each `--thoughts` string appears in conversation context. Over N steps, that's N copies of growing state blobs — token waste.
- The LLM's conversation history already contains everything it learned. Serializing it to a string and back is redundant.
- Shell escaping of prose in CLI arguments is fragile.

### 5. One Universal Step Sequence, Condition-Driven Routing

Multiple modes of the same workflow share a single step sequence. Each step declares its phase and a condition for when it runs. The script evaluates conditions against the current mode, state, and context. Steps whose conditions aren't met are skipped — the LLM never calls them.

```python
STEP_SEQUENCE = [
    {"step": 1,  "title": "Parse Input",           "phase": "SETUP",     "condition": "always"},
    {"step": 2,  "title": "Workspace Setup",        "phase": "SETUP",     "condition": "needs_workspace_setup"},
    {"step": 3,  "title": "Gather Context",          "phase": "SETUP",     "condition": "always"},
    {"step": 4,  "title": "Fetch Extra Context",     "phase": "SETUP",     "condition": "has_pending_items"},
    {"step": 5,  "title": "Plan + Triage",           "phase": "EXECUTION", "condition": "always"},
    ...
]
```

Conditions can be static (`"always"`), mode-dependent (`"needs_workspace_setup"` — true only for certain modes), or data-driven (`"has_unfetched_issues"` — true when the script detects unresolved items in context). A condition evaluator function maps condition strings to boolean checks against the current state. Data-driven conditions let any mode activate a step when the data warrants it.

**Step skipping is transparent.** When a step is skipped, the previous step's output explains why:

```
NEXT: Step 5 — Plan + Triage.
(Skipping steps 3-4: Workspace Setup is mode-A only; no pending items detected.)
Run: <pipeline>.py --step 5
```

This keeps the LLM oriented when step numbers aren't consecutive.

### 6. Conversational Output

The script's output is language, not machine code. LLMs are language-native — they reason better when guided by natural, contextually adapted prose than by repetitive instruction templates.

**Wrong — robotic repetition:**
```
You are a thorough analyst. Running the full pipeline.
You are a thorough analyst. Dispatch all workers in parallel.
You are a thorough analyst. Write the final report.
```

**Right — adapted to the moment:**
```
Step 3: "23 files across 3 domains. The payment gateway changes are the
most sensitive — that's where focus should land."

Step 6: "7 workers ready to dispatch. Security and reliability are
especially relevant given the new API endpoint."

Step 9: "4 findings from reconciliation. Connect them to what this change
is trying to accomplish and frame recommendations the author can act on."
```

Each step's output adapts its framing to the phase:
- **Setup steps** build understanding: "Here's what we know..."
- **Execution steps** are directive: "Dispatch these workers..."
- **Synthesis steps** frame the task: "Connect these findings to..."
- **Validation steps** invite scrutiny: "Stress-test whether..."

**Structure of each step's output:**

1. **Situation** — what you need to know right now (pre-read, pre-formatted, relevant to this step only)
2. **Actions** — what to do with it (specific commands, tool calls, or judgments to make)
3. **Handoff** — what must be true before proceeding (explicit file to write, or nothing)
4. **Next** — which step comes next and why (with skip explanations if non-consecutive)

**Tone:** direct, information-dense, no filler. Present facts the LLM needs to act on, not instructions to go find facts. But write as a colleague briefing a colleague, not a machine printing instructions. The script has access to all the context — use it to write output that's specific to this particular run, not generic boilerplate.

**Voice design.** Choose a specific voice for the script's personality and maintain it across all steps. The voice lives *within* the structural headers (Situation, Actions, Handoff) — don't soften the headers themselves, as they're machine-readable landmarks that help the LLM parse the briefing.

Good voice choices create a natural dynamic between the script and the LLM. For example: "senior reviewer briefing the orchestrator" gives the script authority on process while trusting the LLM on execution. The voice should feel like one side of a dialogue, not a specification document or a numbered checklist.

### 7. Explicit Handoff Points

At specific steps where the LLM produces synthesis needed by later steps, the script names the exact artifact to write — the file path, the expected content, and why it matters downstream.

The consuming step's script reads the handoff file and presents its contents inline. If the file is missing, the script provides a fallback. The pipeline degrades gracefully rather than breaking.

> **Example:** A code review pipeline asks the LLM to write a change-purpose summary at step 3. At step 8, the reconciliation step reads `change-purpose.md` and presents it inline: "Change purpose: Refactors the payment gateway to support multi-currency checkout." If the file is missing, the script falls back to commit messages: "Change purpose (from commits): feat: add multi-currency support; test: add currency conversion tests."

### 8. Artifact Discipline

Handoff points (Principle 7) define *what* to write. Artifact discipline defines *the contract around writing it*. The biggest failure mode in multi-step LLM pipelines isn't bad judgment — it's sloppy execution: files half-written, verification skipped, the LLM moving on without confirming its own work.

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
python3 <pipeline>.py \
  --step <N> \
  --mode <mode> \
  --output-dir /tmp/<pipeline>-<id> \
  [--mode-specific-args]           # e.g., --pr-number 123
```

Minimal arguments. The script reads everything else from files in `--output-dir`.

No `--total-steps` (the script knows its own sequence). No `--thoughts` (state lives in files and conversation context). The `--mode` flag is required at step 1; for subsequent steps, the script reads it from `run-config.json`.

### Split State Model

Two files with distinct ownership:

**`run-config.json`** — Caller-provided configuration. Written once before or at step 1 (from CLI args or a pre-existing config). Read-only during the run. Contains mode, identifiers, feature flags, and explicit overrides.

**`pipeline-state.json`** — Execution state. Owned exclusively by the script. Updated after each step. The LLM never reads or writes this file directly.

```json
{
  "run_id": "20260318T150000-modeA-42",
  "completed_steps": [1, 2, 3],
  "skipped_steps": [4],
  "resolved_params": {
    "input_range": "abc123..def456",
    "has_pending_items": false
  },
  "workers": {
    "dispatched": ["worker-A", "worker-B"],
    "completed": ["worker-A"],
    "failed": [],
    "output_files": ["/tmp/run-42/worker-A-output.json"]
  },
  "verdict": null
}
```

**Why split:** Mode and caller config don't change during a run — they're input. Execution state (which steps completed, which workers finished) evolves at every step. Separating them prevents accidental mutation of config and makes it clear what the script owns vs. what the caller provides.

### Module Boundaries

Keep the executable pipeline as a facade over three concern-specific modules:

```text
pipeline.py  ← conditions, routing, state I/O, output, telemetry, CLI
├── imports pipeline_contract.py  ← shared vocabulary
├── imports briefings.py          ← pure guidance and formatting
│           └── imports pipeline_contract.py
└── imports orchestration.py      ← side-effecting per-step work
            └── imports pipeline_contract.py
```

`briefings.py` and `orchestration.py` are siblings: neither imports the other. Both import shared vocabulary directly from `pipeline_contract.py`; `pipeline.py` imports all three and remains the directly executable compatibility surface. This one-directional dependency graph keeps pure briefing changes separate from subprocess and state-management work while preserving one stable entry point for callers.

### Step Guidance Function

```python
def get_step_guidance(step, mode, state, context, config=None, output_dir=None):
    """Return guidance for a step. Pure formatting — no I/O.

    Args:
        step: Step number.
        mode: Pipeline mode.
        state: Current pipeline-state.json contents.
        context: Current domain context (gathered data, metadata, etc.).
        config: Run config (caller-provided, read-only).
        output_dir: Output directory path (for constructing file paths in briefings).

    Returns:
        Dict with keys: phase, title, situation, actions, handoff.
    """
```

**Critical constraint: `get_step_guidance()` is a pure formatting function.** It reads its arguments and returns a dict. No file I/O, no subprocess calls, no side effects. All orchestration — running scripts, reading files into state, writing state — happens in a separate `_orchestrate_step()` function that runs *before* `get_step_guidance()` is called. This makes every step's briefing testable with plain dicts, no filesystem needed.

```
main() for each step:
  1. _orchestrate_step() — reads files, runs subprocesses, updates state (I/O here)
  2. get_step_guidance() — formats state into a briefing (pure function, no I/O)
  3. format_output() — renders the briefing as text for stdout
```

Routing logic (computing the next step) lives in `main()` using `compute_next_step()`, which scans forward through the step sequence, skipping steps whose conditions aren't met.

### Output Formatting

```python
def format_output(step, guidance):
    lines = []

    # Header — rigid structure, machine-readable
    lines.append(f"{'═' * 60}")
    lines.append(f"PIPELINE Step {step} — {guidance['phase']}: {guidance['title']}")
    lines.append(f"{'═' * 60}")
    lines.append("")

    # Situation: pre-digested context
    if guidance.get("situation"):
        lines.append("## SITUATION")
        lines.append("")
        for line in guidance["situation"]:
            lines.append(line)
        lines.append("")

    # Actions: what to do
    if guidance.get("actions"):
        lines.append("## ACTIONS")
        lines.append("")
        for action in guidance["actions"]:
            lines.append(action)
        lines.append("")

    # Handoff: required before proceeding
    if guidance.get("handoff"):
        lines.append("## HANDOFF — Required before proceeding")
        lines.append("")
        for line in guidance["handoff"]:
            lines.append(f"- {line}")
        lines.append("")

    # Next step pointer
    next_step = guidance.get("next_step")
    if next_step:
        lines.append(f"{'─' * 60}")
        lines.append(f"Next: Step {next_step['step']} — {next_step['title']}")
    else:
        lines.append(f"{'─' * 60}")
        lines.append("PIPELINE COMPLETE")

    return "\n".join(lines)
```

Section headers are structural landmarks — the LLM uses them to parse the briefing. The conversational voice (Principle 6) lives *within* these sections, not in the headers themselves.

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
- Extract mode-specific identifiers as appropriate

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
| State mechanism | `--thoughts` (LLM carries all state) | Split files (`run-config.json` + `pipeline-state.json`) |
| Context delivery | Instructions to read files | Pre-digested briefings |
| Step routing | Always sequential (step N → N+1) | Condition-driven skipping (mode + data-driven) |
| Multi-mode support | Not addressed | Core feature — one sequence, many modes |
| Script role | Instruction injector | Context curator + state manager + flow controller |
| LLM file reads | Frequent (extract values from JSON) | Rare (only for deep content understanding) |
| Output tone | Task list | Conversational briefing |
| Guidance functions | May have side effects | Pure formatting — no I/O (testable with plain dicts) |

The step-by-step prompt injection pattern remains valid for simpler cases — single-mode workflows where epistemic boundaries are the primary concern (e.g., a validation step that must reason independently). The curated context pipeline is for multi-mode operational workflows where context management and flow control matter as much as reasoning discipline.

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
- [ ] Shared vocabulary, pure briefings, side-effecting orchestration, and routing/CLI have one-directional module boundaries
- [ ] `get_step_guidance()` is a pure formatting function — no I/O, no subprocess calls
- [ ] Orchestration (file reads, subprocess calls) in a separate function, called before guidance
- [ ] `get_step_guidance()` returns guidance for each step/mode combination
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
| I/O leaking into guidance functions | Enforce the pure formatting constraint. If a guidance function needs data, the orchestration function must read it into state first. |
| Mission and phase transitions cause banner blindness | Use contextual variations, not verbatim repetition. Phase transitions connect the mission to the upcoming work. |
