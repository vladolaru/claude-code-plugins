# Architecture-Reviewer ↔ Patterns-Reviewer Coupling Analysis

**Date:** 2026-02-28
**Goal:** Resolve the highest-overlap pair in the review pipeline to reduce redundancy, token spend, and noise.
**Status:** Analysis complete — ready for implementation in a separate session.

---

## The Problem

architecture-reviewer and patterns-reviewer are the **most redundant pair** in the 14-agent review pipeline. They co-report the same finding more than any other agent pair.

| Metric | architecture-reviewer | patterns-reviewer |
|---|---|---|
| **Precision** | 62.8% (43 validated) | 66.2% (71 validated) |
| **FP rate** | 7.0% (3 FPs) | **0.0%** (0 FPs) |
| **Hit rate** | 67% (16/24) | 41% (17/41) |
| **Unique contribution** | **50%** (worst in pipeline) | 61.9% |
| **Dispatches** | 24/47 (51%) | 41/47 (87%) |
| **Avg runtime** | 2m 59s | 4m 38s (pipeline bottleneck) |
| **Avg cache** | 1.8M tokens | 4.8M tokens |
| **Total budget share** | 5.9% (43.2M) | **27.0%** (196.8M) |
| **Cache per finding** | 1.3M | 4.7M |
| **Model** | Opus | Opus |
| **Cross-project precision** | Go: 80% → WP: 53.6% | Go: 84.2% → WP: 59.6% |

**Bottom line:** architecture-reviewer has the worst unique contribution (50%) in the pipeline. Half its findings are also reported by patterns-reviewer. patterns-reviewer finds more, has zero FPs, but costs 4.5x more per run and is the second pipeline wall-clock bottleneck.

---

## Overlap Evidence

### Co-reported findings: 8 (highest of any agent pair)

From the overlap analysis of 204 ciab-admin findings (38 multi-source findings total):

| # | Finding | All Agents | Verdict | Severity |
|---|---|---|---|---|
| 15 | `getDaysRemaining` duplicated across modules | architecture + history + patterns | CONFIRMED | medium |
| 43 | `getDaysRemaining` duplicated (second instance) | architecture + history + patterns | CONFIRMED | medium |
| 56 | Document custom allowlist divergence from defaults | architecture + patterns | LIKELY VALID | low |
| 75 | Filter mapping duplicated + PHP divergence risk | architecture + history + patterns | LIKELY VALID | medium |
| 83 | Filter mapping duplicated + PHP divergence (second) | architecture + history + patterns | LIKELY VALID | medium |
| 93 | Duplicated `useWindowFocus` | architecture + patterns | STYLE/PREFERENCE | medium |
| 97 | Inconsistent error forwarding | architecture + patterns | STYLE/PREFERENCE | low |
| 149 | Duplicated empty state CTA across 3 pages | architecture + patterns | CONFIRMED | medium |

### What the overlap looks like

Both agents find the same issue but frame it differently:

| Finding type | architecture says | patterns says |
|---|---|---|
| Code duplication | "DRY violation / SRP issue" | "Existing utility exists — REUSE" |
| Inconsistent approach | "Cohesion problem across modules" | "Codebase convention not followed — ALIGN" |
| Structural redundancy | "Feature Envy / Shotgun Surgery" | "Consolidation opportunity — CONSOLIDATE" |

The observations are the same. The vocabulary is different. The user sees both and filters one.

### Overlap pattern: all 8 findings are about duplication or inconsistency

None of the co-reported findings are about:
- SOLID violations (architecture's exclusive domain)
- Design pattern recommendations (architecture's exclusive domain)
- Hexagonal architecture checks (architecture's exclusive domain)

All 8 are about code duplication or structural inconsistency — which is exactly where the two agents' mandates overlap.

---

## Scope and Domain Analysis

### Domain filters in `review-scope.py`

```python
# architecture domain — implementation files, excluding tests
"architecture": {
    "include": r"\.(php|js|ts|jsx|tsx|py|java|cs|go|rb)$",
    "exclude": r"(test|spec|\.test\.|\.spec\.|__tests__)",
}

# patterns domain — ALL code files, no exclusions
"patterns": {
    "include": r"\.(php|js|ts|jsx|tsx|css|scss|py|java|rb|go)$",
    "exclude": None,
}
```

patterns-reviewer sees everything architecture-reviewer sees, **plus** CSS/SCSS and test files. The file coverage is a superset.

### Agent mandates

**architecture-reviewer** (`agents/architecture-reviewer.md`):
- SOLID principles
- Design patterns (GoF)
- Coupling/cohesion analysis
- Architectural code smells (God Object, Feature Envy, Shotgun Surgery, etc.)
- Hexagonal architecture
- Confidence scoring with 60+ threshold

**patterns-reviewer** (`agents/patterns-reviewer.md`):
- Existing pattern discovery (base ref search)
- Git history archaeology
- Naming convention enforcement
- Consolidation opportunities
- Pattern evolution tracking
- 3+ usage gate for "established" patterns
- Proximity modifiers for confidence
- Staleness checks

### Where the mandates overlap

| Concern | architecture | patterns | Overlap? |
|---|---|---|---|
| Code duplication detection | Yes (DRY/SRP) | Yes (REUSE/CONSOLIDATE) | **YES** |
| Structural inconsistency | Yes (cohesion) | Yes (ALIGN) | **YES** |
| SOLID violations | Yes | No | architecture-exclusive |
| Design pattern recommendations | Yes | No | architecture-exclusive |
| Hexagonal architecture | Yes | No | architecture-exclusive |
| Git history search | No | Yes | patterns-exclusive |
| Naming conventions | No | Yes | patterns-exclusive |
| Pattern evolution tracking | No | Yes | patterns-exclusive |
| Base-ref comparison | Minimal | Core activity | patterns-exclusive |

The overlap zone is precisely **duplication detection and structural consistency** — which accounts for all 8 co-reported findings.

---

## Options

### Option A: Merge architecture into patterns (recommended)

**What:** Fold architecture-reviewer's SOLID/coupling/cohesion checks into patterns-reviewer. Remove architecture-reviewer as a separate agent.

**Rationale:**
- patterns-reviewer already catches 62% of architecture's findings (with 0% FP vs 7%)
- architecture's unique contribution is 50% — but its truly exclusive findings (SOLID violations, design pattern recommendations) could be added to patterns-reviewer's mandate
- Saves ~6% token budget (43.2M) and one Opus agent dispatch
- Reduces wall-clock time (one fewer agent competing for resources)

**Risk:**
- patterns-reviewer is already the most expensive agent (27% budget, 4m 38s). Adding architecture's mandate could make it slower
- patterns-reviewer's precision might drop if it tries to cover SOLID analysis too
- Loss of specialization — patterns-reviewer is good at what it does; broadening scope could dilute quality

**Implementation:**
1. Add SOLID/coupling analysis section to `agents/patterns-reviewer.md`
2. Add architecture code smell detection to patterns-reviewer's checklist
3. Move the `software-architecture` skill routing table from architecture-reviewer to patterns-reviewer
4. Update `AGENT_CONFIG` in `bootstrap-reviewer.py` to remove `architecture-reviewer`
5. Update dispatch tables in `commands/code-review.md` and `commands/full-code-review.md`
6. Update reconciliation agent if it has agent-specific logic
7. Remove `agents/architecture-reviewer.md`

### Option B: Narrow architecture's scope (exclude duplication/consistency)

**What:** Keep both agents but explicitly exclude "duplication detection" and "structural consistency" from architecture-reviewer's mandate. Architecture focuses exclusively on SOLID, design patterns, and hexagonal architecture.

**Rationale:**
- Eliminates the overlap zone without removing an agent
- architecture-reviewer's exclusive findings (SOLID, design patterns) are valuable
- Simpler change — just update the agent instructions

**Risk:**
- architecture-reviewer's hit rate will drop significantly (many of its 16/24 hits were duplication findings)
- May not be worth dispatching if it only fires on SOLID/design pattern issues (low frequency)
- Still costs an Opus dispatch for potentially fewer findings

**Implementation:**
1. Update `agents/architecture-reviewer.md`:
   - Add explicit exclusion: "Do NOT report code duplication, structural inconsistency, or consolidation opportunities — these are handled by patterns-reviewer"
   - Narrow scope to: SOLID violations, design pattern misuse, hexagonal architecture, God Object, Feature Envy only when they indicate a maintainability hazard (not just duplication)
2. Add to confidence scoring: "-20 for findings that are primarily about 'code should be shared' or 'these two things are inconsistent'"
3. No changes needed to patterns-reviewer

### Option C: Keep both, dedup in reconciliation

**What:** Add overlap detection to the reconciliation step. When architecture and patterns report the same finding, keep only the one with higher confidence.

**Rationale:**
- No agent changes needed
- The reconciliation agent already exists (`agents/review-reconciliator.md`)
- Zero risk of losing coverage

**Risk:**
- Doesn't save any token budget (both agents still run)
- Doesn't reduce pipeline time (both still dispatched)
- Only reduces noise in the final output — the cost is already spent
- Reconciliation would need to be smart enough to detect semantic equivalence

**Implementation:**
1. Update `agents/review-reconciliator.md` with explicit dedup rules for architecture + patterns overlap
2. Add a "merge similar findings" step before the final output

---

## Recommendation

**Option B first, Option A later.**

Option B is the safer first step: narrow architecture-reviewer's scope to exclude duplication/consistency, and measure the impact over 10-20 sessions. If architecture-reviewer's hit rate drops below 20% (meaning SOLID/design pattern-only findings are rare), proceed to Option A and merge it into patterns-reviewer entirely.

This avoids the risk of making patterns-reviewer (already the most expensive agent) even more bloated, while immediately eliminating the overlap.

### Suggested implementation order

1. **Update `agents/architecture-reviewer.md`** — add explicit exclusions for duplication/consistency
2. **Run 10-20 review sessions** — measure architecture-reviewer's hit rate with narrowed scope
3. **If hit rate < 20%:** Proceed to Option A (merge into patterns-reviewer)
4. **If hit rate >= 20%:** Keep Option B as the permanent solution

---

## Data Source References

All supporting data was generated in the 2026-02-28 analysis session. The following files contain the raw evidence:

### Permanent files (committed to repo)

| File | Contents |
|---|---|
| `.claude/docs/analysis/2026-02-28-reviewer-agent-value-ranking.md` | Composite value ranking — 47 sessions, 305 executions, all operational metrics, overlap analysis, tier assessments |
| `.claude/docs/analysis/2026-02-28-ingest-validation-analysis.md` | Precision analysis — 313 findings across 29 sessions (9 cabrero + 20 ciab-admin), per-agent precision, FP details |
| `plugins/pirategoat-tools/scripts/extract-session-metrics.py` | General-purpose CLI for extracting session metrics from Claude Code transcripts |

### Temporary files (may not persist across sessions — regenerate from scripts if needed)

| File | Contents | How to regenerate |
|---|---|---|
| `$TMPDIR/reviewer_metrics_40sessions.md` | Raw 47-session operational metrics (305 reviewer agent executions) with runtime, tokens, model, verdict, findings per agent | `python3 plugins/pirategoat-tools/scripts/extract-session-metrics.py --sessions-dir ~/.claude/projects/-Users-vladolaru-Work-a8c-ciab-admin/ --limit 50 --format markdown` |
| `$TMPDIR/agent_overlap_analysis.md` | Finding co-occurrence analysis — 38 multi-source findings from 204 ciab-admin findings, co-occurrence matrix, unique contribution rates | Extracted from ingest-code-review step 6 tables in ciab-admin session transcripts |
| `$TMPDIR/ciab_ingest_results.md` | Raw ingest extraction — 204 findings from 20 ciab-admin sessions with verdict, severity, source agent | Extracted from session transcripts using `ingest-code-review` step 6 validation tables |
| `$TMPDIR/ingest_analysis_results.md` | Raw ingest extraction — 109 findings from 9 cabrero sessions | Same method as above, from cabrero session transcripts |

### Session transcripts (source of truth)

- **ciab-admin sessions:** `~/.claude/projects/-Users-vladolaru-Work-a8c-ciab-admin/*.jsonl` (47 sessions with subagent data)
- **cabrero sessions:** `~/.claude/projects/-Users-vladolaru-Work-a8c-cabrero/*.jsonl` (9 sessions with ingest data)
- **This analysis session:** `~/.claude/projects/-Users-vladolaru-Work-a8c-claude-code-plugins/e5aa8371-bfa7-4b87-8dbe-31803c8d4205.jsonl`

### Agent definition files

| File | Role |
|---|---|
| `plugins/pirategoat-tools/agents/architecture-reviewer.md` | Full agent prompt — SOLID, design patterns, coupling, hexagonal architecture, confidence scoring |
| `plugins/pirategoat-tools/agents/patterns-reviewer.md` | Full agent prompt — codebase archaeology, git history, 3+ usage gate, staleness check, proximity modifiers |
| `plugins/pirategoat-tools/agents/shared/reviewer-protocol.md` | Shared protocol — bootstrap, scope discovery, output format |
| `plugins/pirategoat-tools/scripts/bootstrap-reviewer.py` | Agent dispatch config — `AGENT_CONFIG` dict with domain filters, protocols, scope flags |
| `plugins/pirategoat-tools/scripts/review-scope.py` | Domain filter definitions — `DOMAIN_FILTERS` dict with include/exclude regexes per domain |
| `plugins/pirategoat-tools/commands/code-review.md` | Dispatch table — which agents run in `/full-code-review` |
| `plugins/pirategoat-tools/commands/full-code-review.md` | Same dispatch table (full-code-review variant) |

---

## Appendix: All architecture-reviewer findings by uniqueness

From 47 operational sessions (24 dispatches, 33 findings):

**Unique findings (50% — 11 findings):** SOLID violations, design pattern recommendations, architectural code smells that no other agent reported.

**Co-reported with patterns (8 findings):** All about code duplication or structural inconsistency (see table above).

**Co-reported with other agents (3 findings):**
- With wp-architecture: JSDoc accuracy (#28), DATE-FORMAT-BAN (#39), dead function (#129 — FP)
- With pr-reviewer: 1 finding

**Key insight:** architecture-reviewer's unique 50% is valuable but narrow — it's exclusively SOLID/design-pattern analysis. The redundant 50% is entirely duplication/consistency work that patterns-reviewer already covers (with better precision and zero FPs).
