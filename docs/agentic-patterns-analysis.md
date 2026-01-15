# Agentic Patterns Analysis for PR Review & Code Quality Agents

**Source:** [awesome-agentic-patterns](https://github.com/nibzard/awesome-agentic-patterns)
**Date:** 2026-01-15
**Purpose:** Identify patterns to improve our PR reviewing and code quality agent ecosystem

## Executive Summary

Analyzed 116+ agentic AI patterns to identify improvements for our code review agents (pr-reviewer, architecture-reviewer, tests-reviewer, security-reviewer, performance-reviewer).

**Most Critical Patterns Identified:**
1. **Structured Output Specification** - Reliable, parseable review outputs
2. **CriticGPT-Style Evaluation** - Multi-criteria quality assessment
3. **Sub-Agent Spawning** - Parallel specialized reviews
4. **Curated Code Context Window** - Token-efficient context management
5. **Deterministic Security Scanning** - Integrate battle-tested security tools
6. **Human-in-the-Loop Approval** - Critical findings oversight
7. **Rich Feedback Loops** - Ground truth for iterative improvement

---

## Pattern Categories Overview

The repository organizes patterns into 7 categories:

1. **Context & Memory** - Context management, episodic memory
2. **Feedback Loops** - Code review, CI integration, self-critique
3. **Learning & Adaptation** - Skill evolution, reinforcement learning
4. **Orchestration & Control** - Task decomposition, sub-agents, phases
5. **Reliability & Eval** - Guardrails, eval harnesses, logging
6. **Security & Safety** - Isolated execution, PII handling
7. **Tool Use & Environment** - Shell, browser, database integration

---

## Tier 1: Implement Immediately (High Impact, Low Complexity)

### 1. Structured Output Specification

**Pattern:** Ensure all review outputs conform to defined schemas (JSON Schema, TypeScript interfaces)

**Problem:** Current markdown outputs are human-readable but not machine-parseable.

**Solution:**
```typescript
interface ReviewOutput {
  pr_id: string;
  reviewer: string;
  verdict: 'approve' | 'block' | 'request_changes';
  summary: { total_issues: number; critical: number; high: number; };
  issues: Issue[];
  recommendations: Recommendation[];
  meta: { confidence_score: number; };
}
```

**Apply to:** All reviewers
**Effort:** Medium (4-6 hours per agent)
**Impact:** Enables automation, metrics, downstream workflows

---

### 2. Curated Code Context Window

**Pattern:** Load only top-K relevant files using search sub-agent. Filter semantic changes only.

**Problem:** Loading entire diffs wastes tokens on noise (whitespace, formatting).

**Solution:**
- Search sub-agent finds top 3-5 relevant files
- Semantic filter extracts only: function changes, logic mods, dependency updates
- Skip: whitespace, comments, import reordering

**Apply to:** All reviewers
**Effort:** Medium (6-8 hours)
**Impact:** 10-100x token reduction, clearer reasoning

---

### 3. Rich Feedback Loops

**Pattern:** Provide test results, linter output, benchmark data as ground truth.

**Problem:** Agents guess about correctness without concrete feedback.

**Solution:**
```yaml
# Provide to reviewers:
- test_results.json (pass/fail per test)
- lint_output.json (specific violations)
- benchmark_comparison.json (before/after metrics)
- coverage_delta.json (coverage change)
```

**Apply to:** All reviewers
**Effort:** Low (2-3 hours)
**Impact:** Agents reason from facts, not speculation

---

### 4. Semantic Context Filtering

**Pattern:** Filter diffs to semantic changes before sending to agent.

**Problem:** Raw diffs include formatting, whitespace, trivial changes.

**Solution:**
```python
def semantic_diff(base, head):
    # Parse AST for both versions
    base_ast = parse(base)
    head_ast = parse(head)

    # Compare semantic structures
    return extract_changes(base_ast, head_ast)
    # Returns only:
    # - Function signature changes
    # - Logic modifications
    # - Dependency additions/removals
```

**Apply to:** All reviewers
**Effort:** Low (3-4 hours)
**Impact:** 10-100x token reduction, better signal-to-noise

---

### 5. Verbose Reasoning Transparency

**Pattern:** Add `--verbose` mode showing agent's reasoning process.

**Problem:** Agent decisions are black-box, hard to debug/trust.

**Solution:**
```markdown
# Normal output:
Issue: SQL injection on line 42

# Verbose output:
Issue: SQL injection on line 42

<reasoning>
1. Detected direct string interpolation in SQL query
2. Checked for prepared statements: NOT FOUND
3. Verified user input sanitization: MISSING
4. Cross-referenced security-patterns skill: VIOLATION
5. Confidence: 95% (pattern match + missing mitigations)
</reasoning>
```

**Apply to:** All reviewers
**Effort:** Low (1-2 hours per agent)
**Impact:** Trust, debugging, transparency

---

## Tier 2: High Value, Moderate Complexity

### 6. CriticGPT-Style Multi-Criteria Evaluation

**Pattern:** Decompose code quality into subscores with weighted aggregation.

**Problem:** Binary approve/block doesn't quantify quality dimensions.

**Solution:**
```typescript
interface ReviewScore {
  overall: number; // 0-1
  subscores: {
    correctness: { score: number; weight: 0.4; reasoning: string; };
    security: { score: number; weight: 0.2; reasoning: string; };
    performance: { score: number; weight: 0.15; reasoning: string; };
    maintainability: { score: number; weight: 0.15; reasoning: string; };
    style: { score: number; weight: 0.1; reasoning: string; };
  };
}
```

**Apply to:** PR reviewer (main orchestrator)
**Effort:** High (10-12 hours)
**Impact:** Quantifiable quality, explainable decisions, trend tracking

---

### 7. Sub-Agent Spawning (Parallel)

**Pattern:** Spawn all specialized reviewers in parallel, aggregate findings.

**Problem:** Sequential invocation is slower than necessary.

**Solution:**
```python
# Spawn all reviewers in parallel
reviews = await spawn_parallel([
    ('security-reviewer', context),
    ('performance-reviewer', context),
    ('architecture-reviewer', context),
    ('tests-reviewer', context)
])

# Aggregate findings
consolidated = aggregate_reviews(reviews)
```

**Apply to:** PR reviewer orchestration
**Effort:** Low (2-3 hours)
**Impact:** 3-4x faster reviews

---

### 8. Deterministic Security Scanning Integration

**Pattern:** Run SAST tools (Semgrep, Bandit) before LLM analysis.

**Problem:** LLM may miss known security patterns.

**Solution:**
```bash
# Security reviewer workflow:
1. Run semgrep --config=auto
2. Run bandit -r src/
3. Parse deterministic findings
4. Agent MUST address all tool findings
5. Agent adds contextual LLM findings
6. Combine into final report
```

**Apply to:** Security reviewer
**Effort:** Medium (6-8 hours)
**Impact:** Zero false negatives for known patterns

---

### 9. Human-in-the-Loop Approval Framework

**Pattern:** Insert approval gates for critical findings.

**Problem:** No human oversight for severe vulnerabilities or architectural changes.

**Solution:**
```python
if issue.severity == 'critical':
    # Block merge
    pr.add_label('needs:security-approval')

    # Notify team
    notify_slack(channel='#security',
                 message=f"🚨 Critical issue in PR #{pr.number}")

    # Wait for approval
```

**Apply to:** Security and architecture reviewers
**Effort:** Medium (8-10 hours)
**Impact:** Prevents catastrophic merges

---

### 10. Discrete Phase Separation

**Pattern:** Research → Analysis → Recommendation in separate contexts.

**Problem:** Single-pass reviews may contaminate reasoning.

**Solution:**
```mermaid
graph LR
    A[Research Phase] --> B[Analysis Phase]
    B --> C[Recommendation Phase]
```

- **Phase 1:** Understand PR intent (distilled findings < 5KB)
- **Phase 2:** Load findings, identify issues (issue catalog)
- **Phase 3:** Load catalog, generate recommendations (final review)

**Apply to:** Architecture reviewer (complex decisions)
**Effort:** High (12-15 hours)
**Impact:** Cleaner reasoning, prevents contamination

---

## Tier 3: Advanced Patterns

### 11. Plan-Then-Execute for Large PRs

**Pattern:** Create review plan, get approval, then execute.

**Trigger:** PRs > 50 files or > 1000 lines
**Effort:** Medium (8-10 hours)
**Impact:** Better handling of large PRs

---

### 12. Opponent Processor / Multi-Agent Debate

**Pattern:** Spawn pro/con agents for architectural decisions.

**Apply to:** Architecture reviewer (when multiple valid approaches exist)
**Effort:** High (12-15 hours)
**Impact:** Better exploration of trade-offs

---

### 13. LLM Observability

**Pattern:** Integrate Datadog/LangSmith for span-level tracing.

**Apply to:** All reviewers (infrastructure)
**Effort:** High (infrastructure setup)
**Impact:** Visual debugging, performance monitoring

---

### 14. Workflow Evals with Mocked Tools

**Pattern:** Test complete review workflows with mocked tools in CI.

**Apply to:** All reviewers (quality assurance)
**Effort:** High (15-20 hours)
**Impact:** Regression prevention, quality assurance

---

## Implementation Roadmap

### Phase 1: Foundation (Week 1-2)
- ✅ Semantic Context Filtering
- ✅ Verbose Reasoning Mode
- ✅ Structured Output Specification
- ✅ Parallel Sub-Agent Spawning (if supported)

### Phase 2: Multi-Criteria Review (Week 3-4)
- ✅ CriticGPT-Style Scoring Framework
- ✅ Rich Feedback Loops (test results, linter output)
- ✅ Deterministic Security Scanning

### Phase 3: Oversight (Week 5-6)
- ✅ Human-in-the-Loop Approval
- ✅ Curated Context Window
- ✅ Critical Finding Notifications

### Phase 4: Advanced (Week 7-8)
- ✅ Discrete Phase Separation (architecture reviewer)
- ✅ Plan-Then-Execute (large PRs)
- ✅ LLM Observability

### Phase 5: Quality Assurance (Ongoing)
- ✅ Workflow Evals
- ✅ Skill Library Evolution
- ✅ Progressive Autonomy

---

## Pattern Synergies

**Core Review Stack:**
- Structured Output + CriticGPT + Rich Feedback Loops
- **Result:** Reliable, multi-criteria reviews with iterative improvement

**Context Management Stack:**
- Curated Context + Semantic Filtering + Context Minimization
- **Result:** 10-100x token reduction while improving signal

**Orchestration Stack:**
- Sub-Agent Spawning + Discrete Phase Separation + Structured Output
- **Result:** Parallel specialized reviews with clean handoffs

**Quality Assurance Stack:**
- Workflow Evals + Deterministic Security + Human-in-the-Loop
- **Result:** Testing, enforcement, and oversight

**Transparency Stack:**
- Verbose Reasoning + LLM Observability + Chain-of-Thought Monitoring
- **Result:** Full visibility and debugging capability

---

## Anti-Patterns to Avoid

1. ❌ Load entire codebase → ✅ Use curated context window
2. ❌ Rely solely on LLM for security → ✅ Combine with deterministic tools
3. ❌ Single monolithic reviewer → ✅ Spawn specialized sub-agents
4. ❌ Vague, unstructured feedback → ✅ Use structured output schemas
5. ❌ Single-phase reviews → ✅ Separate research, analysis, recommendation
6. ❌ Send raw diffs with noise → ✅ Filter to semantic changes
7. ❌ Auto-approve critical issues → ✅ Require human approval
8. ❌ Black-box reviews → ✅ Provide verbose reasoning
9. ❌ Keep scaffolding forever → ✅ Progressively remove as models improve
10. ❌ Ignore test/build feedback → ✅ Create rich feedback loops

---

## Key Metrics to Track

**Review Quality:**
- False positive rate
- False negative rate (missed issues)
- Human override rate

**Efficiency:**
- Token usage per review
- Review latency
- Parallel sub-agent speedup

**Coverage:**
- Issues detected per category
- Severity distribution
- Pattern library growth

**Trust:**
- Human approval rate
- Interruption frequency
- Feedback incorporation rate

**Learning:**
- Skill library size
- Skill reuse rate
- Scaffolding reduction over time

---

## Questions for Decision Making

1. **Structured Output:** JSON in addition to markdown, or replace markdown entirely?
2. **Parallel Spawning:** Should pr-reviewer spawn all specialists in parallel?
3. **Scoring Framework:** Numerical quality scores (0-1) or qualitative assessments?
4. **Security Tools:** Which scanners to integrate? (Semgrep, Bandit, ESLint, PHP Security Checker, npm audit)
5. **Human Approval:** Slack/email integration or just GitHub labels?
6. **Implementation Priority:** Agree with Tier 1 priorities or different focus?

---

## References

**Primary Source:** [nibzard/awesome-agentic-patterns](https://github.com/nibzard/awesome-agentic-patterns)

**Key Patterns Referenced:**
- AI-Assisted Code Review / Verification
- CriticGPT-Style Evaluation
- Inference-Healed Code Review Reward
- Sub-Agent Spawning
- Structured Output Specification
- Discrete Phase Separation
- Human-in-the-Loop Approval Framework
- Curated Code Context Window
- Semantic Context Filtering
- Rich Feedback Loops > Perfect Prompts
- Deterministic Security Scanning Build Loop
- Plan-Then-Execute Pattern
- Opponent Processor / Multi-Agent Debate
- Skill Library Evolution
- Workflow Evals with Mocked Tools
- LLM Observability
- Verbose Reasoning Transparency

---

## Next Steps

1. Review and approve/modify proposals
2. Select priority patterns for implementation
3. Follow writing-skills discipline for changes:
   - Test current agent behavior (baseline)
   - Implement improvement
   - Test improved behavior
   - Refactor and close loopholes
4. Update agent prompts and skills
5. Add to CHANGELOG
6. Deploy and monitor metrics
