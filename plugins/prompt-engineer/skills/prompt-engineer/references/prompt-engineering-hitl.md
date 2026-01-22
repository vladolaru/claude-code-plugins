# Prompt Engineering: Research-Backed Patterns for Human-in-the-Loop LLM Workflows

This document synthesizes practical patterns for integrating **human feedback
and oversight** into multi-turn LLM workflows. These patterns target scenarios
where human judgment is required at critical decision points, where iterative
refinement benefits from human guidance, or where autonomous agent outputs
require validation before execution.

**Prerequisite**: This guide assumes familiarity with multi-turn techniques
(Self-Refine, CoVe) and basic agentic patterns. Human-in-the-loop (HITL)
patterns extend these by introducing structured intervention points where human
intelligence augments or validates LLM outputs.

**Meta-principle**: The value of HITL comes from combining LLM capabilities
(speed, breadth, consistency) with human capabilities (judgment, domain
expertise, accountability). The goal is not to slow down automation but to
insert human oversight where it most improves outcomes.

---

## Technique Selection Guide

| Domain         | Pattern                            | Trigger Condition                                  | Human Role                                        | LLM Role                                              | Cost/Tradeoff              | Effect                                     |
| -------------- | ---------------------------------- | -------------------------------------------------- | ------------------------------------------------- | ----------------------------------------------------- | -------------------------- | ------------------------------------------ |
| **Planning**   | Plan Review Gate                   | Complex task requiring multi-step execution        | Approve/edit plan before execution                | Generate initial plan; regenerate on feedback         | Human review latency       | 82% plan approval rate in production       |
| **Execution**  | Iterative Refinement with Feedback | Output quality improvable through domain expertise | Provide specific feedback on deficiencies         | Refine output incorporating feedback                  | Multiple review cycles     | Higher alignment with human intent         |
| **Validation** | Pre-Execution Checkpoint           | Irreversible or high-stakes actions                | Approve/reject before execution                   | Generate candidate action; await approval             | Blocking on human response | Prevents costly errors                     |
| **Quality**    | Selective Escalation               | Confidence-based routing                           | Review low-confidence outputs only                | Flag uncertainty; handle high-confidence autonomously | Reduced human load         | Focuses human attention on difficult cases |
| **Context**    | Human Context Augmentation         | LLM lacking task-specific information              | Provide additional context, constraints, examples | Incorporate human input into generation               | Upfront human effort       | Better grounding in requirements           |

---

## Quick Reference: Key Principles

1. **Gate Before Irreversible Actions** — Per HULA: 82% plan approval rate when
   humans review before execution; prevents costly errors
2. **Input Quality Determines Output Quality** — Per HULA: "The detail of input
   can highly affect the performance"; 79% plan generation success with
   well-structured inputs
3. **Feedback Must Be Actionable** — Per Self-Refine research: feedback must be
   "actionable and specific"; vague feedback fails to improve outputs
4. **Preserve Human Authority** — Humans can override, edit, or reject at any
   stage; 59% of approved PRs merged in production
5. **Minimize Context Switching** — Batch human review (e.g., Selective
   Escalation) rather than requiring constant attention
6. **Make Uncertainty Visible** — Confidence signaling enables selective
   routing; focus human attention on difficult cases
7. **Document the Handoff** — Per HULA's DPDE paradigm: clear interfaces between
   human and LLM responsibilities reduce confusion

---

## 1. The HULA Framework

Human-in-the-Loop LLM-based Agents (HULA) provides a production-validated
framework for software development tasks. Per Takerngsaksiri et al. (2025):
"Rather than aiming to fully automate software development tasks, we designed an
LLM-based software development agent to collaborate with practitioners,
functioning as an assistant to help resolve software development tasks,
promoting the Human-AI synergy."

**Framework architecture:**

```
┌─────────────────────────────────────────────────────────────────┐
│                        HULA WORKFLOW                            │
├─────────────────────────────────────────────────────────────────┤
│                                                                 │
│  Stage 1: Setting Up Task                                       │
│  ┌─────────────┐                                                │
│  │   Human     │ → Prepare input (issue description, context)   │
│  └─────────────┘                                                │
│        ↓                                                        │
│  Stage 2: Planning                                              │
│  ┌─────────────┐    ┌─────────────┐    ┌─────────────┐         │
│  │ AI Planner  │ → │   Human     │ → │ AI Planner  │          │
│  │  Agent      │    │   Review    │    │ (regenerate)│          │
│  └─────────────┘    └─────────────┘    └─────────────┘         │
│  Generate plan      Approve/Edit       If feedback given        │
│        ↓                                                        │
│  Stage 3: Coding                                                │
│  ┌─────────────┐    ┌─────────────┐    ┌─────────────┐         │
│  │ AI Coding   │ → │   Tools     │ → │   Human     │          │
│  │  Agent      │    │  Feedback   │    │   Review    │          │
│  └─────────────┘    └─────────────┘    └─────────────┘         │
│  Generate code      Linter/compiler    Approve/Edit             │
│        ↓                                                        │
│  Stage 4: Raising PR                                            │
│  ┌─────────────┐                                                │
│  │   Human     │ → Create PR or checkout branch for editing     │
│  └─────────────┘                                                │
│                                                                 │
└─────────────────────────────────────────────────────────────────┘
```

**Three agent types:**

Per the paper:

> "• AI Planner Agent (P) is an LLM model receiving inputs (i.e., a JIRA issue
> and the source code repository) to identify the more relevant files that are
> associated with the given JIRA issue... Then, the LLM model uses the list of
> relevant files and the JIRA issue to generate a coding plan. • AI Coding Agent
> (C) is an LLM model receiving inputs (i.e., the coding plan) to generate a set
> of code changes. • Human Agent (H) is a software engineer assigned to the
> given task. The human agent will provide feedback and work cooperatively with
> AI agents."

**Production results:**

| Metric                  | Result               |
| ----------------------- | -------------------- |
| Plan generation success | 527/663 issues (79%) |
| Plan approval rate      | 433/527 (82%)        |
| PR creation rate        | 95/376 (25%)         |
| PR merge rate           | 56/95 (59%)          |

**Key finding on input quality:**

Per the paper: "The detail of input can highly affect the performance of HULA...
participants acknowledged that HULA promotes good documentation practice, though
this requires more effort to provide detailed issue descriptions."

**Why this works:**

HULA succeeds because it combines LLM speed with human judgment at critical
checkpoints. The LLM does the heavy lifting (file identification, plan
generation, code writing), while humans validate decisions where expertise
matters most. This division of labor matches capabilities to tasks.

**Non-obvious insight:** The 25% PR creation rate and 59% merge rate are not
failures—they represent appropriate filtering. Not all generated plans should
become PRs; not all PRs should merge. The human gates prevent bad outputs from
propagating, which is the system working correctly.

---

## 2. Plan Review Gates

A checkpoint pattern where the LLM generates a plan that must be approved or
modified by a human before execution proceeds.

**The pattern:**

```
Turn 1 (Plan Generation):
  Input: Task description + context
  Output: Structured plan with steps, affected files/components, approach

Turn 2 (Human Review):
  Human examines plan
  Options:
    A) Approve → Proceed to execution
    B) Edit → Modify plan directly, then proceed
    C) Regenerate → Provide feedback, request new plan
    D) Reject → Abort workflow

Turn 3 (If regenerate):
  Input: Original task + previous plan + human feedback
  Output: Revised plan incorporating feedback
  → Return to Turn 2
```

**Plan presentation format:**

Structure plans for easy human review:

```
## Plan for: {task_title}

### Files to Modify:
1. {filepath_1} - {brief rationale}
2. {filepath_2} - {brief rationale}

### Approach:
1. {step_1_description}
2. {step_2_description}
3. {step_3_description}

### Considerations:
- {potential_risk_or_tradeoff}
- {assumption_made}

### Questions for Reviewer:
- {clarification_needed}
```

**Regeneration with feedback:**

When humans request changes, include their feedback explicitly:

```
You previously generated this plan:
{previous_plan}

The reviewer provided this feedback:
{human_feedback}

Generate a revised plan that addresses the feedback while maintaining
the original task objective.
```

**Why this works:**

Plans make decisions visible before execution. Without plan review, the LLM
makes implicit decisions (which files to modify, what approach to take) that
humans only discover after code is written. Review gates surface these decisions
early, when changes are cheap.

**Non-obvious insight:** Plan review is faster than code review. Reviewing "I
will modify auth.py to add session validation" takes seconds; reviewing the
actual diff takes minutes. The plan gate catches direction errors before effort
is invested.

**CORRECT (structured plan for easy review):**

```
## Plan for: Add session timeout

### Files to Modify:
1. auth.py - Add timeout check to session validation
2. config.py - Add SESSION_TIMEOUT setting

### Approach:
1. Add SESSION_TIMEOUT = 3600 to config
2. In validate_session(), check if session.created_at + timeout < now
3. Return 401 if expired

### Questions:
- Should expired sessions be deleted or just rejected?
```

**INCORRECT (vague plan that can't be reviewed):**

```
I'll update the authentication to handle timeouts.
```

---

## 3. Iterative Refinement with Human Feedback

Extends Self-Refine by incorporating human judgment instead of (or in addition
to) LLM self-critique.

**The pattern:**

```
Turn 1 (Initial Generation):
  Input: Task description
  Output: Initial output y₀

Turn 2 (Human Feedback):
  Human reviews y₀
  Provides specific, actionable feedback

Turn 3 (Refinement):
  Input: Task + y₀ + human feedback
  Output: Refined output y₁

[Repeat until human approves or iteration limit reached]
```

**Feedback quality guidelines:**

Per Self-Refine research, feedback must be actionable and specific. Human
feedback should follow the same principles:

**EFFECTIVE human feedback:**

```
"The function handles the happy path but doesn't account for null input.
Add a null check at line 15 that returns an empty array."
```

**INEFFECTIVE human feedback:**

```
"The code could be better. Please improve it."
```

**Feedback prompt template:**

```
You generated the following output:
{previous_output}

The human reviewer provided this feedback:
{human_feedback}

Revise the output to address each point in the feedback. Explain what
changes you made and why.

Revised output:
```

**Convergence tracking:**

Track iterations to prevent infinite loops:

```
Iteration 1: Generated initial output
Iteration 2: Human feedback on X, Y -> Revised
Iteration 3: Human feedback on Z -> Revised
Iteration 4: Human approved

Total iterations: 4
Issues addressed: X, Y, Z
```

**Why this works:**

Human feedback provides the external signal that pure self-refinement lacks. Per
Huang et al. (2024), LLMs cannot reliably self-correct without external
feedback—they either keep the original answer or change correct answers to
incorrect ones. Human expertise breaks this failure mode.

**Non-obvious insight:** The value of human feedback is not just correction but
_direction_. The LLM may produce multiple valid outputs; human feedback selects
among them based on preferences, context, or constraints the LLM doesn't know.
This is guidance, not just error correction.

---

## 4. Pre-Execution Checkpoints

A blocking pattern for high-stakes actions where the LLM proposes an action and
waits for explicit human approval before execution.

**Use cases:**

- Executing code that modifies production systems
- Sending communications on behalf of the user
- Making purchases or financial transactions
- Modifying or deleting files
- API calls with side effects

**The pattern:**

```
Turn 1 (Action Proposal):
  Input: Task requiring action
  Output: Proposed action + rationale + impact assessment

Turn 2 (Human Decision):
  Human reviews proposal
  Options:
    A) Approve → Execute action
    B) Modify → Adjust action parameters, then approve
    C) Reject → Do not execute; optionally provide alternative

Turn 3 (Execution or Abort):
  If approved: Execute action, report result
  If rejected: Acknowledge, await further instructions
```

**Action proposal format:**

```
## Proposed Action

**Action**: {action_type}
**Target**: {what will be affected}
**Parameters**: {specific details}

**Rationale**: {why this action addresses the task}

**Impact Assessment**:
- Reversibility: {reversible/partially reversible/irreversible}
- Scope: {what systems/data will be affected}
- Risk: {potential negative outcomes}

**Awaiting your approval to proceed.**
```

**Approval interface:**

Design clear approval mechanisms:

```
[APPROVE] - Execute the proposed action
[MODIFY]  - I want to change: _______________
[REJECT]  - Do not execute because: _______________
```

**Why this works:**

Pre-execution checkpoints convert autonomous decisions into human-approved
decisions. The LLM proposes; the human disposes. This preserves human authority
over consequential actions while still leveraging LLM capabilities for proposal
generation.

**Non-obvious insight:** The checkpoint format matters as much as the checkpoint
existence. "Awaiting approval to proceed" is weaker than showing exactly what
will happen with explicit impact assessment. Humans approve more thoughtfully
when they understand consequences.

**CORRECT (explicit impact assessment):**

```
## Proposed Action

**Action**: DELETE
**Target**: user_sessions table (PostgreSQL)
**Records affected**: 12,847 rows older than 30 days

**Reversibility**: IRREVERSIBLE (no backup configured)
**Risk**: Loss of session history for analytics

Awaiting your approval.
```

**INCORRECT (vague action description):**

```
I'll clean up old sessions from the database.
Proceed? [Y/N]
```

---

## 5. Selective Escalation

A routing pattern where the LLM handles high-confidence cases autonomously and
escalates low-confidence cases to human review.

**The pattern:**

```
For each task:
  1. LLM generates output with confidence assessment
  2. If confidence >= threshold:
       → Return output directly
     Else:
       → Flag for human review
       → Human provides guidance or correction
       → LLM incorporates feedback
```

**Confidence signaling prompt:**

```
Answer the following question. After your answer, rate your confidence
on a scale of 1-5:
  1 = Very uncertain, likely need human verification
  2 = Somewhat uncertain, may contain errors
  3 = Moderately confident, standard accuracy expected
  4 = Confident, high likelihood of correctness
  5 = Very confident, well within my capabilities

Question: {question}

Answer:
Confidence: [1-5]
Reasoning for confidence level:
```

**Escalation thresholds:**

| Confidence | Action                         |
| ---------- | ------------------------------ |
| 4-5        | Autonomous execution           |
| 3          | Execute with logging for audit |
| 1-2        | Escalate to human review       |

**Human review queue:**

Batch low-confidence items for efficient human processing:

```
## Items Requiring Review

### Item 1 (Confidence: 2)
Task: {task_description}
LLM Output: {output}
Uncertainty reason: {why_uncertain}
[Approve] [Edit] [Regenerate]

### Item 2 (Confidence: 1)
Task: {task_description}
LLM Output: {output}
Uncertainty reason: {why_uncertain}
[Approve] [Edit] [Regenerate]
```

**Why this works:**

Selective escalation optimizes human attention. Humans are the bottleneck in
HITL systems—their time is scarce and expensive. Routing only low-confidence
items to humans focuses their attention on cases where it matters most, while
high-confidence items proceed autonomously.

**Non-obvious insight:** The confidence threshold should be calibrated
empirically, not set arbitrarily. Analyze historical accuracy at each confidence
level, then set the threshold where autonomous accuracy meets your requirements.
Too high a threshold wastes human time on easy cases; too low misses errors.

**CORRECT (calibrated thresholds):**

```
# Based on historical accuracy analysis:
# Confidence 5: 99.2% accuracy -> autonomous
# Confidence 4: 97.1% accuracy -> autonomous
# Confidence 3: 89.4% accuracy -> autonomous with logging
# Confidence 2: 71.3% accuracy -> escalate
# Confidence 1: 52.8% accuracy -> escalate
AUTONOMOUS_THRESHOLD = 3
```

---

## 6. Human Context Augmentation

A pattern where humans provide additional context, constraints, or examples that
improve LLM output quality.

**Context types:**

1. **Domain knowledge**: Specialized information not in LLM training
2. **Constraints**: Rules or requirements the output must satisfy
3. **Examples**: Preferred style, format, or approach demonstrated
4. **Corrections**: Fixing misconceptions from previous interactions

**The pattern:**

```
Stage 1 (Context Collection):
  System prompts human for relevant context
  Human provides: background, constraints, examples, preferences

Stage 2 (Augmented Generation):
  Input: Task + human-provided context
  Output: Generation informed by context

Stage 3 (Optional Iteration):
  Human reviews output
  If gaps identified: provide additional context
  Regenerate with augmented context
```

**Context collection prompt:**

```
Before I begin this task, please provide any relevant context:

1. **Background**: What should I know about this project/domain?
2. **Constraints**: What rules or requirements must the output satisfy?
3. **Examples**: Can you show me an example of what good output looks like?
4. **Preferences**: Any stylistic preferences or conventions to follow?
5. **Avoid**: Anything I should specifically NOT do?

You can skip any categories that aren't relevant.
```

**Applying context in generation:**

```
Task: {task_description}

Human-provided context:
- Background: {background}
- Constraints: {constraints}
- Example of preferred style: {example}
- Must avoid: {avoid_list}

Generate output that addresses the task while respecting all provided
context and constraints.
```

**Why this works:**

LLMs lack task-specific knowledge that humans possess: organizational
conventions, implicit requirements, historical context, stakeholder preferences.
Context augmentation bridges this knowledge gap, grounding LLM generation in
information it couldn't otherwise access.

**Non-obvious insight:** The upfront cost of providing context pays off across
multiple interactions. Once the LLM understands your coding style, naming
conventions, or domain terminology, it produces outputs that require less
correction. Invest heavily in the first interaction; subsequent interactions
benefit.

**CORRECT (comprehensive context):**

```
Background: This is a financial services app under SOC 2 compliance.
All database queries must be parameterized (no string concatenation).

Constraints:
- Use SQLAlchemy ORM, not raw SQL
- All user-facing errors must be logged with correlation IDs
- No PII in log messages

Example: See auth_service.py for our standard error handling pattern.

Avoid: print() statements, bare except clauses, hardcoded credentials.
```

**INCORRECT (minimal context):**

```
Write a database query function.
```

---

## 7. Implementation Patterns

### Coordination Paradigm

Per HULA: "We exploit the cooperative communication style with the Decentralized
Planning Decentralized Execution (DPDE) paradigm to ensure that each AI agent is
independently responsible for its own objective and capabilities while being
able to work cooperatively with human agents."

**Key design principles:**

1. **Minimal communication overhead**: Agents don't need constant coordination
2. **Shared memory**: All agents access the same information (task, context,
   history)
3. **Local adaptation**: Each agent adjusts based on local information and human
   feedback
4. **Clear handoffs**: Explicit transitions between agent and human
   responsibility

### User Interface Considerations

Per HULA's deployment: The framework "seamlessly integrated into Atlassian JIRA"
with clear stages visible to users.

**UI requirements:**

1. **Stage visibility**: Users can see current workflow stage
2. **Output display**: LLM outputs presented clearly for review
3. **Feedback input**: Easy mechanisms for approval, editing, feedback
4. **History tracking**: Record of human decisions and feedback
5. **Regeneration controls**: Ability to request new outputs with feedback

### Feedback Loop Logging

Track human-AI interactions for system improvement:

```
{
  "task_id": "ABC-123",
  "stage": "planning",
  "llm_output": "{generated_plan}",
  "human_action": "edit",
  "human_feedback": "{specific_edits_made}",
  "iterations": 2,
  "final_outcome": "approved",
  "timestamp": "2025-01-04T10:30:00Z"
}
```

---

## 8. Anti-Patterns

### The Rubber Stamp

**Anti-pattern:** Humans approve LLM outputs without meaningful review.

```
# PROBLEMATIC
LLM generates plan → Human clicks "Approve" without reading → Execute
Human oversight provides no actual value
```

**Mitigation:** Require specific acknowledgments; highlight key decisions
requiring attention.

```
# BETTER
"This plan will modify 3 production files. Please confirm you have
reviewed the following changes:
□ Modification to auth.py (security-sensitive)
□ Modification to database.py (data-sensitive)
□ Modification to config.py (configuration change)
[I have reviewed these changes and approve]"
```

### Feedback Without Context

**Anti-pattern:** Human provides feedback referencing information the LLM
doesn't have.

```
# PROBLEMATIC
Human: "Use the approach we discussed in the last meeting"
LLM has no access to meeting content; cannot comply effectively
```

**Mitigation:** Require self-contained feedback that includes necessary context.

```
# BETTER
Human: "Use the singleton pattern for the database connection, as this
prevents multiple connections being opened. Here's an example of our
preferred implementation: {code_example}"
```

### Escalation Fatigue

**Anti-pattern:** Too many items escalated, overwhelming human reviewers.

```
# PROBLEMATIC
Confidence threshold set too high → 80% of items escalated
Humans can't keep up → Start rubber-stamping → Defeats purpose
```

**Mitigation:** Tune thresholds based on actual error rates; prioritize
escalations.

```
# BETTER
Analyze historical accuracy at each confidence level
Set threshold where autonomous accuracy is acceptable (e.g., 95%)
Prioritize escalated items by potential impact
```

### Blocking Without Timeout

**Anti-pattern:** Workflow blocks indefinitely waiting for human response.

```
# PROBLEMATIC
LLM generates action proposal → Awaits human approval
Human is unavailable → Workflow stuck indefinitely
```

**Mitigation:** Implement timeouts with appropriate fallback behavior.

```
# BETTER
"Awaiting approval for proposed action.
If no response within 24 hours, this request will:
[  ] Be automatically rejected (safe default)
[  ] Be escalated to {backup_approver}
[  ] Expire and require re-initiation"
```

---

## 9. Lessons from Production Deployment

Per HULA's findings from Atlassian deployment:

**Lesson 1: Input quality is critical**

> "The detail of input can highly affect the performance of HULA."

Implication: Invest in structured input templates and user guidance.
Automatically augment inputs with available context (documentation, type
definitions, historical data).

**Lesson 2: Human feedback enhances context**

> "Incorporating human feedback into HULA can enhance the input context and be
> beneficial in practice."

Implication: Design feedback mechanisms that capture and preserve human
knowledge for future iterations.

**Lesson 3: Code quality remains challenging**

> "HULA still requires human involvement to ensure the generated code completely
> addresses the task... challenges around code quality remain a concern in some
> cases."

Implication: Don't assume LLM outputs are production-ready. Build in
verification steps and human review for quality-sensitive outputs.

**Lesson 4: Readability aids adoption**

> "61% of the participants agreed that the generated code was easy to read and
> modify, which helped reduce their initial development time and effort."

Implication: LLM outputs that serve as starting points for human modification
provide value even when not perfect. Optimize for modifiability, not just
correctness.

---

## Research Citations

- Huang, J., Chen, X., Mishra, S., et al. (2024). "Large Language Models Cannot
  Self-Correct Reasoning Yet." arXiv.
- Madaan, A., et al. (2023). "Self-Refine: Iterative Refinement with
  Self-Feedback." arXiv.
- Takerngsaksiri, W., Pasuksmit, J., Thongtanunam, P., et al. (2025).
  "Human-In-the-Loop Software Development Agents." arXiv.
- Wang, L., et al. (2023). "A Survey on Large Language Model based Autonomous
  Agents." arXiv.
- Wu, Q., et al. (2023). "AutoGen: Enabling Next-Gen LLM Applications via
  Multi-Agent Conversation." arXiv.
