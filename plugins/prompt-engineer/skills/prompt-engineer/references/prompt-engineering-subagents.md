# Prompt Engineering: Research-Backed Techniques for Subagent Orchestration

This document synthesizes practical prompt engineering patterns for **orchestrating multiple LLM calls** in parallel or hierarchical structures. These techniques target scenarios where an orchestrator decomposes work across specialized subagents, executes tasks in parallel, or coordinates multiple LLM instances toward a shared goal.

**Meta-principle**: The value of subagent orchestration comes from decomposition and parallelization--breaking complex tasks into independent units that can be processed concurrently, then synthesizing results. This trades sequential depth for parallel breadth.

**Prerequisite**: Assumes familiarity with single-turn techniques (CoT, Plan-and-Solve) and multi-turn refinement patterns (Self-Refine, CoVe).

**Notation**: `S1`, `S2` denote Subagent 1, Subagent 2--concurrent or specialized LLM calls. `O` denotes the Orchestrator.

---

## Technique Selection Guide

| Domain              | Technique                  | Trigger Condition                             | Stacks With                    | Conflicts With           | Cost/Tradeoff                     | Effect                                    |
| ------------------- | -------------------------- | --------------------------------------------- | ------------------------------ | ------------------------ | --------------------------------- | ----------------------------------------- |
| **Parallelization** | Skeleton-of-Thought        | Long-form answers with plannable structure    | Any single-turn technique      | Step-by-step reasoning   | N parallel API calls + synthesis  | 1.89x-2.39x latency reduction             |
| **Parallelization** | SoT with Router            | Mixed query types requiring adaptive dispatch | Skeleton-of-Thought            | --                       | Router call overhead              | Enables SoT for suitable queries only     |
| **Search**          | Tree of Thoughts (BFS)     | Problems requiring exploration with pruning   | State evaluation, backtracking | Sequential CoT           | b x T LLM calls (beam x steps)    | Game of 24: 4%->74% vs CoT                |
| **Search**          | Tree of Thoughts (DFS)     | Deep exploration with early termination       | Value-based pruning            | Parallel expansion       | Variable; supports backtracking   | Crosswords: 15.6%->60% word accuracy      |
| **Decomposition**   | Least-to-Most              | Complex problems harder than examples         | Any verification technique     | Single-turn CoT          | 2+ sequential LLM calls           | SCAN: 99.7% vs 16% standard               |
| **Decomposition**   | Task Orchestration         | Complex task requiring multiple model types   | Any technique                  | Monolithic single-model  | Planning + dispatch overhead      | Enables specialized models per subtask    |
| **Reflection**      | Explicit Reflection        | Tool returns error; retry needed              | Tool-augmented workflows       | Immediate retry          | One reflection step per retry     | Concrete diagnosis improves next attempt  |
| **Reflection**      | Self-Contrast              | Self-evaluation unreliable                    | Multi-perspective generation   | Single-path reflection   | 3-stage process                   | GSM8K +7.8%; invalid reflections -30.8%   |
| **Reflection**      | Anticipatory Reflection    | Agent tasks with potential early failures     | Tree search, plan execution    | Post-hoc only reflection | R backup actions per step         | WebArena: 23.5%; 45% fewer plan revisions |
| **Verification**    | Multi-Perspective SC       | Code generation requiring implicit verify     | Solution + spec + test gen     | Single-perspective vote  | 3-partite graph construction      | HumanEval +15.91%; CodeContests +9.37%    |
| **Verification**    | LM^2 (Decomposer-Solver)   | Complex reasoning requiring step verification | Concept generation             | Monolithic prompting     | 3 models + policy coordination    | MATH: +8.1%; MedQA: +9.7%                 |
| **Coordination**    | Multi-Expert Prompting     | Open-ended tasks; diverse expertise needed    | NGT aggregation                | Single-expert prompting  | n experts + 7-subtask aggregation | TruthfulQA: 89.35% SOTA                   |
| **Coordination**    | Role-Specialized Subagents | Task requires distinct expertise areas        | Any verification technique     | Monolithic prompting     | Role setup overhead               | Specialized responses per domain          |

---

## Quick Reference: Key Principles

1. **Skeleton-First for Structured Answers** -- 1.89x-2.39x latency reduction; quality improved on knowledge/generic/writing, degraded on math/coding
2. **Route Before Dispatching** -- SoT degrades math (1.34x, quality loss) and coding (2.06x, quality loss); router overhead minimal vs wrong-dispatch cost
3. **Independence Enables Parallelism** -- Point N depending on point N-1 causes parallel expansion failure; test: can points be answered in any order?
4. **Synthesis Requires Full Context** -- Final aggregation needs all parallel outputs; discarding intermediate reasoning loses evidence for meta-reasoning
5. **Role Prompts Beat Generic Prompts** -- Explicit constraints ("ONLY point 3", "1-2 sentences", "Do NOT continue other points") critical for focused behavior
6. **8 Samples Optimal for Voting** -- Beyond 8 samples, gains diminish; mostly paying for redundant correct answers
7. **Tree Search for Exploration** -- ToT: Game of 24 4%->74%; Crosswords 15.6%->60%; cost ~5.5k tokens vs ~67 for CoT
8. **BFS for Pruning, DFS for Backtrack** -- BFS: breadth-first with beam pruning when evaluation reliable; DFS: depth-first with backtrack when early termination valuable
9. **Context Accumulation for Generalization** -- Least-to-Most: solved subproblems feed into harder ones; SCAN 16%->99.7%
10. **Dependency DAG for Task Routing** -- Structured `{task, id, dep}` enables parallel execution of independent subtasks
11. **Explicit Reflection Before Retry** -- Concrete diagnosis ("0-indexed but input 1-indexed") beats vague ("fix the bug"); diminishing returns after 2-3 retries
12. **Contrast Dissimilar Solutions** -- Two incorrect solutions with different errors (75.5%) beat single evaluation (70.1%); errors "cancel out"
13. **Anticipate Failures Before Acting** -- Backup actions before execution: 23.5% success + 0.64 revisions vs 20.0% + 1.89 (reflection alone)
14. **Multi-Perspective Verification** -- MPSC: HumanEval +15.91pp, CodeContests +9.37pp; specs (45.93%) and tests (63.82%) individually worse but consistency helps
15. **Concepts Before Decomposition** -- LM^2: removing concepts drops out-of-domain 17.5% vs in-domain 6%; concepts drive generalization
16. **Three Experts Optimal** -- Multi-expert aggregation: 3 experts + NGT = 89.35% TruthfulQA; more experts can reduce truthfulness
17. **NGT Aggregation Required** -- Simply picking best expert underperforms; need agreement + conflict resolution + unique perspectives
18. **Nuanced Verification Beats Binary** -- 9-category error feedback (conceptual, computational, procedural, position) enables targeted response
19. **Complexity-Weighted Voting** -- Top-K complex chains: 80.5% vs 78.0% standard SC on GSM8K; more steps -> more likely correct
20. **Self Red-Team Against Overconfidence** -- "Why opponent could win" reduces confidence escalation 10.34%->3.05%; debates 61.7% end with both claiming >=75%

---

## 1. Parallelization

Techniques that reduce latency by executing independent subtasks concurrently.

### Skeleton-of-Thought (SoT)

Generates answer skeleton first, then expands each point in parallel. Per Ning et al. (2024): "For many question types, we first derive the skeleton according to some protocols and strategies, and then add evidence and details." See also `prompt-engineering-multi-turn.md` Section 7 (sequential refinement focus).

**Process:**

```
O (Skeleton): Question -> [point_1, point_2, ..., point_n] (3-5 words each)
S1..Sn (Expand): Question + skeleton + point_i -> 1-2 sentence expansion
                 [Execute ALL points IN PARALLEL]
O (Concatenate): Join all expansions
```

**Skeleton prompt:**

```
Give skeleton only (not full content). Provide numbered list of points,
each 3-5 words. Generally 3-10 points.

{question}

Skeleton:
1.
```

**Point-expanding prompt:**

```
Continue ONLY point {index}. Write 1-2 sentences. Do NOT continue other points.

{index}. {point_skeleton}
```

**Performance:**

| Category     | Speed-up | Quality Impact |
| ------------ | -------- | -------------- |
| Knowledge    | 2.33x    | Improved       |
| Generic      | 2.31x    | Improved       |
| Common-sense | 2.24x    | Improved       |
| Writing      | 2.26x    | Maintained     |
| Coding       | 2.06x    | Degraded       |
| Math         | 1.34x    | Degraded       |

**Critical limitation:** SoT assumes point independence. When point N depends on result of point N-1, parallel expansion fails.

WRONG: `Skeleton for "Calculate 15*7+23": 1. Multiply 2. Add result` -- Step 2 needs Step 1's output
RIGHT: `Skeleton for "Features of Python": 1. Dynamic typing 2. Interpreted 3. Rich stdlib` -- Independent points

**Stacking:** Combine with post-hoc verification (CoVe) on expanded points.

### SoT with Router

Uses routing to determine SoT appropriateness before dispatching. Per Ning et
al.: "We ask the LLM if the desired answer is in a list of independent points."

**Router prompt:**

```
Does this question require a response organized as independent points?
Answer only "yes" or "no".

Question: {question}
```

**Routing logic:**

```
If "yes": Execute SoT (skeleton + parallel expansion)
If "no":  Execute normal sequential generation
```

Router overhead is minimal (single call); cost of applying SoT to unsuitable
queries is high (degraded quality + wasted parallel calls).

**Implementation:** Use GPT-4 for prompting router (no training) or fine-tune
small classifier (RoBERTa, 120M params) on labeled examples.

### Parallel Sampling

Issues N concurrent LLM calls with temperature > 0 for downstream aggregation.

**Process:**

```
S1..Sn (Sample): Issue N concurrent calls with temp > 0
O (Aggregate):   Method A: Majority voting (extractable answers)
                 Method B: USC selection (free-form)
                 Method C: Meta-reasoning synthesis (evidence combination)
```

**Optimal sample count:** 8 samples balances accuracy and cost. Beyond this, gains diminish--mostly paying for redundant correct answers.

WRONG: `Generate 20 samples for simple factual question` -- Wasteful
RIGHT: `Generate 8 samples for complex reasoning with multiple valid paths`

**Stacking:** Combine with complexity-weighted voting (select top-K complex chains before majority vote: 80.5% vs 78.0% standard SC on GSM8K).

### Parallelization Techniques Compared

| Technique         | Independence Requirement      | Output Type          | Best For                    |
| ----------------- | ----------------------------- | -------------------- | --------------------------- |
| SoT               | Points must be independent    | Structured long-form | Knowledge, generic, writing |
| Parallel Sampling | N/A (samples are independent) | Multiple candidates  | Reasoning with valid paths  |

**Why SoT differs from Parallel Sampling:** SoT parallelizes within a single answer (expanding independent points concurrently). Parallel Sampling parallelizes across multiple complete answers (N independent attempts). SoT reduces latency for structured responses; Parallel Sampling increases quality through selection/voting.

---

## 2. Search and Exploration

Techniques that explore multiple reasoning paths through tree search.

### Tree of Thoughts (ToT)

Explores multiple reasoning paths with self-evaluation and backtracking. Per Yao et al. (2023): "ToT allows LMs to perform deliberate decision making by considering multiple different reasoning paths and self-evaluating choices."

**Four design dimensions:**

| Dimension             | Options                                           |
| --------------------- | ------------------------------------------------- |
| Thought decomposition | Task-specific granularity (equation, paragraph)   |
| Thought generation    | Sample i.i.d. (rich space) or propose (constrain) |
| State evaluation      | Value each state or vote across states            |
| Search algorithm      | BFS (breadth, pruning) or DFS (depth, backtrack)  |

**BFS process (Game of 24 example):**

```
O: Initialize S = {input_numbers}
For each step t:
  S1..Sk: Generate candidate operations from each state in S
  O: Evaluate all candidates ("sure/likely/impossible")
  O: Keep top b candidates -> new S
Return: Solution from best final state
```

**Value prompt:**

```
Evaluate if given numbers can reach 24 (sure/likely/impossible).
10 14: 10 + 14 = 24. sure
3 3 8: 3 * 8 = 24, 24 - 3 = 21. impossible
{current_numbers}:
```

**DFS process (Crosswords example):**

```
O: Start from initial state
  S: Generate candidates, sort by value
  For each candidate with value > threshold:
    Recurse with candidate as new state
    If solution found: return
  Backtrack if no valid candidates
```

**Performance:**

| Task             | CoT   | ToT (b=5) | Improvement |
| ---------------- | ----- | --------- | ----------- |
| Game of 24       | 4%    | 74%       | 18.5x       |
| Creative Writing | 6.93  | 7.56      | +9%         |
| Crosswords       | 15.6% | 60%       | 3.8x        |

**Cost:** ~5.5k tokens per problem vs ~67 for CoT. Justified when base accuracy is low and exploration valuable.

WRONG: `ToT for "What is the capital of France?"` -- No exploration benefit
RIGHT: `ToT for Game of 24 with [4, 9, 10, 13]` -- Multiple valid paths exist

**Stacking:** Combine with Anticipatory Reflection--generate backup actions at each ToT node to reduce plan revisions.

### BFS vs DFS Selection

| Factor            | Use BFS                      | Use DFS                |
| ----------------- | ---------------------------- | ---------------------- |
| Solution depth    | Shallow (few steps)          | Deep (many steps)      |
| Branching factor  | High (many options per step) | Low (few options)      |
| Pruning           | Strong evaluation signal     | Weak evaluation signal |
| Memory            | Available                    | Constrained            |
| Early termination | Not needed                   | Beneficial             |

**Why BFS differs from DFS:** BFS explores breadth-first with beam pruning--maintains top-b candidates at each level, good when evaluation reliably distinguishes promising states. DFS explores depth-first with backtracking--goes deep, backs up on failure, good when early termination on success is valuable and evaluation is less reliable.

---

## 3. Decomposition

Techniques that break complex tasks into simpler subtasks solved sequentially.

### Least-to-Most Prompting

Two-stage decomposition enabling problems harder than examples. Per Zhou et al. (2023): "Break down a complex problem into a series of simpler subproblems and then solve them in sequence."

**Process:**

```
O (Decompose): Problem + few-shot examples -> ordered subproblem list
For each subproblem (easiest to hardest):
  S (Solve): Few-shot + previous solutions + current subproblem -> solution
  O: Append solution to context for next iteration
```

**Decomposition prompt:**

```
Q: "think, machine, learning"
A: "think", "think, machine", "think, machine, learning"

Q: {problem}
A:
```

**Solving prompt (with accumulated context):**

```
Q: "think, machine"
A: Last letter of "think" is "k". Last letter of "machine" is "e".
   Concatenating: "ke".

[Previous subproblem solutions appended here]

Q: {current_subproblem}
A:
```

**Performance:**

| Task                   | Chain-of-Thought | Least-to-Most | Improvement |
| ---------------------- | ---------------- | ------------- | ----------- |
| Last-letter (12 words) | 7.7%             | 94.0%         | 12.2x       |
| SCAN (length split)    | 16.0%            | 99.7%         | 6.2x        |

**Critical insight:** Context accumulation enables length generalization--each solved subproblem feeds into the next, allowing inductive extension beyond example complexity.

**Limitation:** Domain-specific decomposition prompts don't generalize across domains. Math decomposition won't teach commonsense decomposition.

WRONG: `Least-to-Most for "What is 2+3?"` -- Overhead unjustified
RIGHT: `Least-to-Most for compositional task harder than examples`

### Task Decomposition Orchestration

Controller LLM decomposes complex tasks and routes to specialized models. Per Shen et al. (2023) in HuggingGPT: "Leverages LLMs to connect various AI models to solve complicated AI tasks."

**Four-stage pipeline:**

```
O (Plan):    User request -> [{task, id, dep, args}, ...]
O (Select):  Each subtask -> best-fit model from pool
S1..Sn:      Execute subtasks respecting dependency order
O (Respond): All results -> integrated final response
```

**Planning prompt:**

```
Parse user input to tasks:
[{"task": task, "id": task_id, "dep": dependency_ids, "args": arguments}]

The "dep" field denotes ids of previous tasks generating required resources.
```

**Dependency handling:**

```
Given: Task 1 (no deps), Task 2 (dep: 1), Task 3 (no deps), Task 4 (dep: 2,3)

Execution:
  Parallel: Task 1, Task 3
  Sequential: Task 2 (after 1)
  Sequential: Task 4 (after 2 and 3)
```

WRONG: `"First extract text, then translate, then make audio"` -- Unstructured
RIGHT: `[{task: "OCR", id: 1}, {task: "translate", id: 2, dep: [1]}, ...]` -- Structured with dependencies

**Stacking:** Add verification (CRITIC) after each subtask execution.

### Decomposition Techniques Compared

| Technique          | Decomposition Strategy | Context Handling               | Generalization             |
| ------------------ | ---------------------- | ------------------------------ | -------------------------- |
| Least-to-Most      | Easy-to-hard ordering  | Accumulates solved subproblems | Beyond example complexity  |
| Task Orchestration | Dependency-based DAG   | Passes outputs between tasks   | Multi-model specialization |

**Why Least-to-Most differs from Task Orchestration:** Least-to-Most solves subproblems in complexity order, accumulating context--each solution feeds into harder problems. Task Orchestration routes independent subtasks to specialized models based on dependencies. Least-to-Most excels at length/complexity generalization; Task Orchestration excels at leveraging diverse model capabilities.

---

## 4. Reflection and Introspection

Techniques for improving retry quality and learning from failures.

### Explicit Reflection Prompting

Forces concrete failure diagnosis before retry. Derived from Reflexion (Shinn et al. 2023), scoped to within-session patterns.

**Process:**

```
S (Attempt): Generate solution -> Execute -> Error returned
S (Reflect): Explicit analysis of failure cause
S (Retry):   Generate solution conditioned on reflection
```

**Reflection prompt:**

```
Previous attempt failed with: {error_output}

Before generating new solution:
1. Identify specific cause of failure
2. Explain what assumption was incorrect
3. Describe concretely how next attempt will differ

Revised solution:
```

**Concrete vs vague reflection:**

WRONG: `"The code failed. I should fix the bug."` -- Non-actionable
RIGHT: `"Failed because 0-indexed but input 1-indexed. Next: start from 1, end at n+1."` -- Specific, actionable

**Scope limitation:** Diminishing returns after 2-3 reflection-informed retries. Beyond that, problem is likely missing information or fundamental approach mismatch.

### Self-Contrast

Improves reflection by contrasting multiple solution perspectives. Per Zhang et al. (2024): "Adaptively explores diverse solving perspectives, contrasts differences, and summarizes discrepancies into a checklist."

**Problem with direct self-evaluation:**

| Feedback Type           | Frequency |
| ----------------------- | --------- |
| Overconfident           | 46.7%     |
| Inconsistent            | 45.7%     |
| Accurate identification | 6.9%      |

**Three-stage process:**

```
S1..Sn (Perspectives): Generate N solutions from N different approaches
O (Contrast):          Identify differences between solution pairs
O (Checklist):         Generate re-examination checklist from discrepancies
S (Reflect):           Revise using checklist, eliminate discrepancies
```

**Contrastive analysis prompt:**

```
Compare solutions and identify differences:

Solution 1: {solution_1}
Solution 2: {solution_2}

1. Different solving objectives?
2. Different solution steps?
3. Why are answers different?

Checklist for re-examining:
[] {directive_1}
[] {directive_2}
```

**Key finding:** Contrasting two incorrect solutions (with different errors)
improves reflection even when both are wrong--errors "cancel out" through
comparison.

| Strategy                             | Accuracy |
| ------------------------------------ | -------- |
| Self-evaluate one incorrect          | 70.1%    |
| Contrast correct + incorrect         | 83.6%    |
| Contrast two incorrect (same errors) | 70.9%    |
| Contrast two incorrect (diff errors) | 75.5%    |

**Performance:** GSM8K +7.8% over self-reflection; invalid reflections reduced 30.8%; toxic reflections (correct->incorrect) reduced 78.9%.

### Anticipatory Reflection

Generates backup actions before execution. Per Wang et al. (2024): "Equips LLM agents with introspection, enhancing consistency and adaptability."

**Three-layer introspection:**

```
Layer 1 (Pre-Action):  Before executing action a_t
  -> Generate R alternative "remedy" actions
  -> Push all to stack with primary on top

Layer 2 (Post-Action): After executing action a_t
  -> Evaluate: Does result align with subtask objective?
  -> If misaligned: Pop next remedy, backtrack

Layer 3 (Plan Revision): Upon plan failure (stack empty)
  -> Review history, generate refined plan
```

**Anticipatory prompt:**

```
You are about to execute: {action}

If your action above is not correct, the next action should be:
[Generate R alternatives]
```

**Performance (WebArena):**

| Method                  | Success | Plan Revisions |
| ----------------------- | ------- | -------------- |
| Plan + Act              | 19.8%   | 2.03           |
| Plan + Act + Reflection | 20.0%   | 1.89           |
| LATS (tree search)      | 22.7%   | 1.16           |
| Anticipatory Reflection | 23.5%   | 0.64           |

**Stacking:** Combine with ToT--at each node, generate primary + backup actions; pop backup without regenerating plan on failure.

### Reflection Techniques Compared

| Technique           | Trigger      | Mechanism                                   | Key Insight                           |
| ------------------- | ------------ | ------------------------------------------- | ------------------------------------- |
| Explicit Reflection | Post-failure | Diagnose -> analyze -> plan fix             | Concrete diagnosis > vague retry      |
| Self-Contrast       | Pre-revision | Multiple solutions -> contrast -> checklist | Different errors cancel out           |
| Anticipatory        | Pre-action   | Generate backups before executing           | Prepare for failure reduces revisions |

**Why Self-Contrast differs from Explicit Reflection:** Explicit Reflection diagnoses a single failure path. Self-Contrast generates multiple solution paths first, then contrasts their differences to identify errors--even two incorrect solutions with different errors yield better reflection than analyzing one. Self-Contrast is proactive (before knowing which is wrong); Explicit Reflection is reactive (after failure).

**Why Anticipatory differs from both:** Anticipatory Reflection generates backup actions before execution, not after failure. It's predictive rather than diagnostic--reduces the need for reflection by having alternatives ready.

---

## 5. Verification

Techniques for validating outputs through multiple perspectives or external signals.

### Multi-Perspective Self-Consistency (MPSC)

Evaluates code through three complementary perspectives forming a consistency graph. Per Huang et al. (2024): "Incorporates both inter- and intra-consistency across outputs from multiple perspectives."

**Three perspectives:**

```
Solutions:      Code implementing functionality
Specifications: Pre/post-conditions describing valid behavior
Test cases:     Input-output pairs demonstrating expected behavior
```

**Process:**

```
S1..Si: Generate I solutions
S1..Sj: Generate J specifications
S1..Sk: Generate K test cases
O: Construct 3-partite graph with inter-consistency edges
   (solution passes test? solution satisfies spec? test satisfies spec?)
O: Compute intra-consistency within each perspective
O: Select solution with highest combined score
```

**Performance:**

| Benchmark    | ChatGPT | +MPSC  | Improvement |
| ------------ | ------- | ------ | ----------- |
| HumanEval    | 68.38%  | 84.29% | +15.91pp    |
| CodeContests | 2.57%   | 11.94% | +9.37pp     |

**Key finding:** Specifications (45.93% accurate) and test cases (63.82%) are individually worse than solutions (68.38%), yet MPSC improves results. Gains come from consistency relationships, not better verification properties.

### LM^2: Coordinated Multi-Model Reasoning

Modularizes decomposition, solution, and verification into three coordinated models. Per Juneja et al. (2024): "These models are trained to coordinate using policy learning."

**Architecture:**

```
Decomposer (finetuned): Generate concepts -> Generate subquestions step-by-step
Solver (frozen API):    Answer each subquestion given concepts + prior context
Verifier (finetuned):   Classify error type (9 categories)
```

**Concept generation prompt:**

```
Tell me all specific concepts, theorems and formulas used in this solution.

Question: {question}
Answer: {solution}

Concepts:
```

**Verifier categories:**

1. Conceptual mistakes (wrong concept)
2. Computational mistakes (calculation error)
3. Procedural mistakes (wrong steps)
4. Misunderstood question
5. Mistake in first step
6. Mistake in first half
7. Mistake in second half
8. Mistake in last step
9. No mistake

**Error-driven response:**

| Error Type            | Response                                |
| --------------------- | --------------------------------------- |
| Conceptual/Procedural | Regenerate subquestion (wrong approach) |
| Computational         | Proceed (can fix; tool-assisted)        |
| First-step mistake    | High penalty; regenerate immediately    |
| Later-step mistake    | Lower penalty; may self-correct         |

**Performance:** MATH +8.1%; JEEBench +7.71% (out-of-domain); MedQA +9.7%.

**Key finding:** Removing concept generation drops accuracy by 17.5% on out-of-domain (Chemistry) vs 6% on in-domain (Math). Concepts drive generalization.

### Verification Techniques Compared

| Technique | Signal Source           | Domain    | Key Mechanism                   |
| --------- | ----------------------- | --------- | ------------------------------- |
| MPSC      | Inter-consistency graph | Code      | Solution/spec/test agreement    |
| LM^2      | Specialized verifier    | Reasoning | 9-category error classification |

**Why MPSC differs from LM^2:** MPSC uses implicit verification through consistency relationships--solutions, specifications, and test cases must agree. LM^2 uses explicit verification through a trained classifier that identifies specific error types. MPSC works without training; LM^2 requires finetuning but provides actionable error categories. For single-turn verification, see CoVe and CRITIC in `prompt-engineering-multi-turn.md`.

---

## 6. Coordination

Techniques for combining multiple expert perspectives or specialized roles.

### Multi-Expert Prompting

Simulates multiple domain experts with structured aggregation. Per Long et al. (2024): "Aggregates expert responses in single turn without iterative refinement." See also `prompt-engineering-multi-turn.md` (iterative aggregation focus).

**Process:**

```
O: Generate n expert identities (one-sentence descriptions)
S1..Sn: Each expert responds independently
O (NGT Aggregation):
  S1. Generate consensus viewpoints (>50% agreement)
  S2. Identify conflicting viewpoints
  S3. Resolve conflicts using knowledge
  S4. Collect unique perspectives
  S5. Gather all viewpoints
  S6. Generate aggregated response
  S7. Select best (individual or aggregated)
```

**Expert generation:**

```
Generate {n} diverse expert identities best suited to answer:
{instruction}

For each: Expert role + one-sentence description
```

**Aggregation prompt:**

```
Given {n} expert responses to: {instruction}

Expert 1 ({id_1}): {response_1}
...

S1. Consensus viewpoints:
S2. Conflicting viewpoints:
S3. Resolved conflicts:
S4. Unique perspectives:
S5. All viewpoints gathered:
S6. Aggregated response:
S7. Best response selection:
```

**Performance:**

| Model   | Baseline | Multi-Expert | Improvement |
| ------- | -------- | ------------ | ----------- |
| ChatGPT | 80.66%   | 89.35%       | +8.69pp     |

**Key finding:** Three experts is optimal. More than 3 can reduce truthfulness. Aggregated response selected >90% of cases over individual experts.

WRONG: `Generate 3 experts, pick best` -- Missing NGT aggregation
RIGHT: `NGT: agreement -> conflict resolution -> unique perspectives -> aggregate -> select`

### Role-Specialized Subagents

Assigns distinct roles to different LLM calls for specialized handling. Per Kong et al. (2024): role-play prompting provides +10pp accuracy on math benchmarks through implicit role-based reasoning.

**Role assignment pattern:**

```
[System:] You are an expert {role_name} with responsibilities:
{role_description}

Your task is to {specific_subtask}. Do not address aspects outside
your specialization.

[User:] {task_input}
```

**Critical:** Role description must include explicit constraints, not just identity.

WRONG: `"You are a legal expert. Help answer this."` -- No constraint
RIGHT: `"You are responsible for ONLY legal implications. Do not address technical or financial aspects."` -- Explicit scope

**Coordination patterns:**

| Pattern            | Description                               |
| ------------------ | ----------------------------------------- |
| Parallel-Aggregate | All roles respond; aggregator synthesizes |
| Sequential Handoff | Each role builds on previous output       |
| Debate/Critique    | Roles challenge each other iteratively    |

---

## 7. Anti-Patterns

### Forcing Parallelism on Sequential Tasks

WRONG: `SoT skeleton for multi-step math: 1. First step 2. Second step` -- Dependencies exist
RIGHT: `Route math/coding to sequential; SoT for knowledge/generic/writing`

### Over-Decomposition

WRONG: `Simple factual question -> 5 expert roles -> aggregation` -- Overhead exceeds gain
RIGHT: `Route by complexity; simple -> direct; complex -> appropriate decomposition`

### Insufficient Role Specificity

WRONG: `"You are an expert. Answer this."` -- No constraint
RIGHT: `"Continue ONLY point 3. Write 1-2 sentences. Do NOT continue other points."` -- Explicit constraints

### Binary Verification When Nuance Available

WRONG: `Verifier: "Incorrect"` -- No guidance for decomposer
RIGHT: `Verifier: "Conceptual mistake in first step: applied wrong formula"` -- Actionable feedback

### Immediate Retry Without Reflection

WRONG: `Error -> immediately regenerate` -- May repeat mistake
RIGHT: `Error -> explicit failure analysis -> regenerate conditioned on reflection`

### Overconfidence in Multi-Agent Debate

Per Prasad & Nguyen (2025): LLMs exhibit confidence escalation in debate (72.9% -> 83.3% by final round). 61.7% of debates end with both sides claiming >=75%.

WRONG: `Multi-round debate without calibration`
RIGHT: `Add: "Think why you will win, but also why opponent could win"` -- Reduces escalation 10.34%->3.05%

### Direct Self-Evaluation

Per Zhang et al. (2024): Direct self-evaluation produces overconfident (46.7%), inconsistent (45.7%), or accurate (6.9%) feedback.

WRONG: `"Review your solution and identify errors."`
RIGHT: `Self-Contrast: multiple perspectives -> contrast differences -> checklist -> reflect`

### Skipping Result Validation

WRONG: `Subtask completes -> add to context -> continue` -- Errors propagate
RIGHT: `Subtask completes -> validate format/sanity -> if valid continue; else retry`

---

## Research Citations

- Huang, B., et al. (2024). "Enhancing Large Language Models in Coding Through Multi-Perspective Self-Consistency." ACL.
- Juneja, G., Dutta, S., & Chakraborty, T. (2024). "LM^2: A Simple Society of Language Models Solves Complex Reasoning." arXiv.
- Kong, A., et al. (2024). "Better Zero-Shot Reasoning with Role-Play Prompting." arXiv.
- Long, D.X., et al. (2024). "Multi-expert Prompting Improves Reliability, Safety and Usefulness of Large Language Models." EMNLP.
- Ning, X., Lin, Z., Zhou, Z., et al. (2024). "Skeleton-of-Thought: Prompting LLMs for Efficient Parallel Generation." ICLR.
- Prasad, P.S. & Nguyen, M.N. (2025). "When Two LLMs Debate, Both Think They'll Win." arXiv.
- Shen, Y., et al. (2023). "HuggingGPT: Solving AI Tasks with ChatGPT and its Friends in Hugging Face." arXiv.
- Shinn, N., et al. (2023). "Reflexion: Language Agents with Verbal Reinforcement Learning." NeurIPS.
- Wang, H., et al. (2024). "Devil's Advocate: Anticipatory Reflection for LLM Agents." arXiv.
- Yao, S., et al. (2023). "Tree of Thoughts: Deliberate Problem Solving with Large Language Models." NeurIPS.
- Zhang, W., et al. (2024). "Self-Contrast: Better Reflection Through Inconsistent Solving Perspectives." arXiv.
- Zhou, D., et al. (2023). "Least-to-Most Prompting Enables Complex Reasoning in Large Language Models." ICLR.
