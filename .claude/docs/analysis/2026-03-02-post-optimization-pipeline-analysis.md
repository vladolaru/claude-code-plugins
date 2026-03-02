# Post-Optimization Review Pipeline Analysis

**Last updated:** 2026-03-02 18:45
**Data sources:** 10 post-change sessions across 3 projects (ciab-admin, ciab-admin-dup, woo-develop)
**Baseline reference:** [2026-02-28-reviewer-agent-value-ranking.md](./2026-02-28-reviewer-agent-value-ranking.md) (47 sessions, 305 dispatches, 290 findings)
**Change period:** 2026-02-28 → 2026-03-02 (commits `f2f0bf52` → `b664fbb`)

---

## Executive Summary

The optimization campaign produced **mixed results**. Cost-normalized spending per session increased 29% (not decreased), but findings output tripled (+231%). The Opus→Sonnet demotion succeeded in reducing per-finding cost by 50-75% for demoted agents, but total session cost rose because the pipeline now dispatches more agents per session (11.1 vs 6.5 baseline) and finds more issues.

### Key Wins
1. **Cost per finding dropped 37%** — 4.3 Hq/finding vs 6.8 baseline
2. **Model demotion worked** — 70% Sonnet / 30% Opus (was 45% Opus / 30% Sonnet)
3. **patterns-reviewer cost/finding dropped 73%** — 6.2 vs 23.4 Hq/finding
4. **security-reviewer transformed** — Hit rate 11% → 56%, cost/finding 36.5 → 3.0 Hq
5. **codex and gemini removed** — Zero dispatches, eliminating 1% budget waste
6. **reliability-reviewer added** — 100% hit rate, 2.0 Hq/finding, 29 findings in 8 dispatches

### Key Regressions
1. **history-insights-reviewer got SLOWER** — 6m44s avg vs 5m35s baseline (+20%)
2. **Total session cost increased 29%** — 88.1M vs 68.2M Hq-eq per session
3. **pr-reviewer hit rate dropped** — 50% vs 59% baseline

### Unexpected Side Effects
1. **Hit rates spiked across the board** — Most agents went from 30-70% to 100%. Possible explanations: agents producing more noise, PRs having more issues, or improved agent focus.
2. **reliability-reviewer appeared** — New agent not in baseline, dispatched in 80% of sessions. Not explicitly part of the optimization campaign.
3. **Severity data now captured** — 204 findings with severity: 12% high+critical, 65% medium, 24% low+info. Baseline didn't capture severity consistently.

---

## Changes Applied (commit log)

| Version | Date | Key Changes |
|---|---|---|
| v1.38.0 | Feb 28 | Adaptive agent dispatch (Step 3.6 triage) — LLM-assisted conditional dispatch/skip |
| v1.38.1 | Feb 28 | Pinned 4 agents from Opus to Sonnet (architecture, wp-architecture, patterns, history-insights) |
| v1.39.0 | Feb 28 | History-insights efficiency overhaul — pre-computed diffs, file history, command budget, patterns dedup |
| v1.39.1 | Feb 28 | Pipeline efficiency fixes — ~6 wasted calls per dispatch eliminated |
| v1.41.3 | Mar 1 | Reconciliator model change, analyzing-cc-sessions optimizations |
| — | Mar 1 | Review pipeline simplification with deterministic orchestration |
| — | Feb 28 | Architecture-reviewer scope narrowed (eliminate patterns overlap) |
| — | Mar 1 | Patterns-reviewer tool discipline and search scoping |
| — | Feb 28 | Review API contract tests + reconcile→ingest data loss fix |

---

## Per-Agent Comparison: Post-Change vs Baseline

### Cost-Normalized Metrics (10 post-change sessions vs 47 baseline sessions)

| Agent | Disp | Find | Hit% | Hq/find | AvgDur | Model | BL Hit% | BL Hq/f | BL Dur | BL Model | Delta |
|---|---|---|---|---|---|---|---|---|---|---|---|
| **js-tests-reviewer** | 8 | 18 | 100% | **0.9** | 2m33s | Sonnet | 71% | 1.7 | 2m24s | Sonnet | ↑ cost-eff, ↑ hit |
| **performance-reviewer** | 10 | 25 | 100% | **2.6** | 3m27s | Sonnet | 56% | 4.7 | 2m45s | Sonnet | ↑ cost-eff, ↑ hit, ↓ speed |
| **reliability-reviewer** | 8 | 29 | 100% | **2.0** | 3m52s | Sonnet | — | — | — | — | NEW |
| **security-reviewer** | 9 | 13 | **56%** | **3.0** | 2m56s | Sonnet | 11% | 36.5 | 3m14s | Sonnet | ↑↑ cost-eff, ↑↑ hit |
| **architecture-reviewer** | 10 | 18 | **100%** | **3.2** | 2m44s | Sonnet | 67% | 6.5 | 2m59s | Opus→Sonnet | ↑ cost-eff, ↑ hit |
| **wp-architecture-reviewer** | 10 | 21 | 100% | 2.8 | 3m28s | Sonnet | 33% | 8.8 | 2m54s | Opus | ↑↑ cost-eff, ↑↑ hit |
| **reconciliator**³ | 9 | — | — | — | 2m48s | Opus | — | — | — | — | Misidentified |
| **patterns-reviewer** | 10 | 24 | **100%** | **6.2** | 4m21s | Sonnet | 41% | 23.4 | 4m38s | Opus→Sonnet | ↑↑↑ cost-eff, ↑↑↑ hit |
| **history-insights-reviewer** | 10 | 28 | 100% | 7.5 | **6m44s** | Sonnet | 87% | 13.9 | 5m35s | Opus→Sonnet | ↑ cost-eff, ↓ speed |
| **a11y-reviewer** | 7 | 4 | 43% | 10.8 | 2m1s | Opus | 40% | 20.0 | 1m46s | Opus | ↑ cost-eff, ≈ hit |
| **dead-code-reviewer** | 4 | 1 | 25% | 10.3 | 1m49s | Sonnet | 32% | 16.3 | 2m48s | Sonnet | ↑ cost-eff, ↓ dispatches |
| **php-tests-reviewer** | 6 | 14 | **83%** | **1.1** | 2m12s | Sonnet | 31% | 4.4 | 2m30s | Sonnet | ↑↑ cost-eff, ↑↑ hit |
| **pr-reviewer** | 10 | 9 | **50%** | 12.1 | 2m34s | Opus | 59% | 13.5 | 3m16s | Opus | ≈ cost-eff, ↓ hit |
| **codex-reviewer** | 0 | 0 | — | — | — | — | 0% | ∞ | 6m4s | Haiku | REMOVED |
| **gemini-reviewer** | 0 | 0 | — | — | — | — | 0% | ∞ | 3m43s | Haiku | REMOVED |

³ 9 reconciliator sessions were misidentified as wp-architecture-reviewer by the metrics extraction script's keyword inference, which matched "wp-architecture" in agent signal text like "wp-architecture-reviewer: STATUS=COMPLETED". Fixed in extract-session-metrics.py (commit f0bd776).

---

## Token Budget Comparison

### Per-Session Averages

| Metric | Post-Change (n=10) | Baseline (n=47) | Change |
|---|---|---|---|
| Raw cache read | 25.0M | 15.5M | +61% |
| Cost-normalized (Hq-eq) | 88.1M | 68.2M | **+29%** |
| Total findings | 20.4 | 6.2 | **+231%** |
| Cost per finding (Hq-eq) | **4.3** | 6.8 | **-37%** |
| Agents dispatched | 11.1 | 6.5 | +71% |
| Pipeline wall clock (max agent) | 6m41s | ~5m35s | +19% |

### Cost-Normalized Budget Distribution (10 sessions)

| Agent | Raw Cache (M) | Hq-eq (M) | % Budget | BL % Budget | Change |
|---|---|---|---|---|---|
| history-insights-reviewer | 62.8 | 210.4 | **23.9%** | 19.0% | ↑ 4.9pp |
| patterns-reviewer | 44.1 | 149.9 | **17.0%** | 30.7% | ↓ 13.7pp |
| pr-reviewer | 21.9 | 109.3 | **12.4%** | 12.2% | ≈ |
| wp-architecture-reviewer | 9.3 | 27.8 | **3.2%** | 12.0% | ↓ 8.8pp |
| reconciliator | 18.6 | 81.1 | **9.2%** | — | Misidentified |
| performance-reviewer | 21.5 | 64.4 | 7.3% | 3.5% | ↑ 3.8pp |
| reliability-reviewer | 19.2 | 57.7 | 6.5% | — | NEW |
| architecture-reviewer | 17.0 | 56.8 | 6.4% | 6.7% | ≈ |
| a11y-reviewer | 8.6 | 43.2 | 4.9% | 1.2% | ↑ 3.7pp |
| security-reviewer | 13.1 | 39.4 | 4.5% | 5.7% | ↓ 1.2pp |
| js-tests-reviewer | 5.2 | 15.7 | 1.8% | 1.4% | ≈ |
| php-tests-reviewer | 5.0 | 15.1 | 1.7% | 2.3% | ≈ |
| dead-code-reviewer | 3.4 | 10.3 | 1.2% | 3.6% | ↓ 2.4pp |
| **TOTAL** | **249.8** | **881.2** | **100%** | | |

**Key shift:** patterns-reviewer dropped from 30.7% to 17.0% of cost budget (Opus→Sonnet demotion). history-insights now the largest cost center at 23.9%. reliability-reviewer is new at 6.5%.

### Model Distribution

| Model | Post-Change | Baseline |
|---|---|---|
| Opus | 33 (30%) | 137+9 (48%) |
| Sonnet | 78 (70%) | 92+34 (41%) |
| Haiku | 0 (0%) | 23 (8%) |

The Opus→Sonnet shift succeeded. The 30% Opus remaining is:
- pr-reviewer: 10 dispatches (by design — anchor role)
- a11y-reviewer: 7 dispatches (still on Opus/inherit)
- reconciliator: ~9 dispatches (correctly on Opus — was misidentified as wp-architecture in original analysis)
- architecture/patterns/history-insights on Feb 28: ~7 dispatches (before demotion took effect)

---

## Severity Distribution

| Severity | Count | % |
|---|---|---|
| Critical | 3 | 1.5% |
| High | 21 | 10.3% |
| Medium | 132 | 64.7% |
| Low | 44 | 21.6% |
| Info | 4 | 2.0% |
| **Total** | **204** | |

The baseline did not capture severity consistently, so direct comparison is not possible. The distribution looks healthy — 76% medium or higher.

**Top severity producers:**
- reliability-reviewer: 2 critical, 4 high (highest severity per finding)
- history-insights-reviewer: 6 high
- wp-architecture-reviewer: 1 critical, 5 high

---

## Retracted: ~~wp-architecture-reviewer Double-Dispatch~~ → Reconciliator Misidentification

**Original claim:** wp-architecture-reviewer was dispatched twice per session — once as a Sonnet review (useful) and once as an Opus "ghost" dispatch (waste), totalling 19 dispatches across 10 sessions.

**Actual cause:** The metrics extraction script (`extract-session-metrics.py`) misidentified ~9 reconciliator agent sessions as wp-architecture-reviewer. The reconciliator's prompt contains agent signal text like `wp-architecture-reviewer: STATUS=COMPLETED`, which matched the keyword inference pattern for `wp-architecture`. The "secondary Opus dispatch" was actually the reconciliator running on Opus (its intended model).

**Evidence:**
- The "secondary dispatch" always had 0 findings and "none" verdict — consistent with reconciliator behavior (it synthesizes, not reviews)
- It always ran on Opus — the reconciliator's configured model, not wp-architecture's pinned Sonnet
- The 5-8 minute gap between "primary" and "secondary" is the expected gap between review completion and reconciliation start

**Impact of correction:**
- wp-architecture dispatches: 19 → ~10 (normal, one per session)
- No double-dispatch bug exists — the pipeline is working correctly
- The ~48M Hq-eq "waste" was actually reconciliator cost (legitimate, expected)

**Fix:** `extract-session-metrics.py` now detects reconciliator sessions by prompt fingerprint (Strategy 1.5) and strips agent signal lines before keyword inference (hardened Strategy 2). See commit f0bd776.

---

## Adaptive Triage (Step 3.6) Effectiveness

### Observed Decisions (60 decisions found in 1 orchestrator session)

| Agent | Dispatched | Skipped | Skip Rate | Baseline Hit Rate |
|---|---|---|---|---|
| security-reviewer | 10 | 0 | 0% | 11% |
| dead-code-reviewer | 0 | **10** | **100%** | 32% |
| architecture-reviewer | 5 | 5 | 50% | 67% |
| wp-architecture-reviewer | 10 | 0 | 0% | 33% |
| performance-reviewer | 10 | 0 | 0% | 56% |
| a11y-reviewer | 9 | 1 | 10% | 40% |

### Assessment

**dead-code-reviewer skip is well-calibrated.** Triage correctly identifies that additive PRs (net positive lines, no deletions) don't need dead-code analysis. This reduced dead-code dispatches from 19 (baseline) to 4 (post-change). The skip reasons are specific and accurate:
- "no files deleted, no refactoring commits, net +120 lines"
- "primarily new code (+2147/-94), no significant refactoring"

**architecture-reviewer skip is aggressive but reasonable.** 50% skip rate for PRs with "single component file changed, no structural reorganization." However, architecture-reviewer now shows 100% hit rate in post-change data (when dispatched), suggesting triage may be over-skipping.

**security-reviewer is NOT being skipped.** Despite the baseline 89% zero-finding rate, triage dispatches security-reviewer 100% of the time. Yet the post-change hit rate is 56% — much better than baseline 11%. This suggests either: (a) the PRs being reviewed are more security-relevant, or (b) the security-reviewer itself improved (unlikely since the agent wasn't changed).

**Triage coverage is limited.** Only 6 agents are triaged. The remaining agents (patterns, history-insights, pr-reviewer, js/php/e2e-tests, reliability, a11y) are always dispatched. This is by design (Step 3.6 only gates conditional agents), but means 50%+ of agents are never skipped.

---

## Detailed Agent Assessment Changes

### Improved Agents

**patterns-reviewer** — The biggest winner
- Cost/finding: 23.4 → **6.2 Hq** (73% reduction)
- Hit rate: 41% → **100%** (unprecedented)
- Budget share: 30.7% → **17.0%** (largest single improvement)
- Cause: Opus→Sonnet demotion + tool discipline/search scoping
- Risk: 100% hit rate may indicate lower quality threshold. Need precision validation.

**security-reviewer** — Transformed from liability to contributor
- Hit rate: 11% → **56%** (5x improvement)
- Cost/finding: 36.5 → **3.0 Hq** (12x improvement)
- Dispatches: 38 → 9 (fewer, more targeted)
- Now produces 13 findings with 2 HIGH severity across 9 dispatches

**php-tests-reviewer** — Quiet breakout
- Hit rate: 31% → **83%** (2.7x)
- Cost/finding: 4.4 → **1.1 Hq** (4x improvement)
- Only 6 dispatches but very efficient when it fires

### Regressed Agents

**history-insights-reviewer** — Slower despite efficiency overhaul
- Duration: 5m35s → **6m44s** (+20%)
- Still the pipeline wall-clock bottleneck
- Cost/finding improved (13.9 → 7.5 Hq) due to Opus→Sonnet demotion
- The pre-computed diffs and file history may have increased context size, making Sonnet take longer
- One outlier session: 10m29s (ciab-admin-dup)

**pr-reviewer** — Less productive
- Hit rate: 59% → **50%**
- Findings/dispatch: 1.1 → 0.9
- Still on Opus (by design), costing 12.1 Hq/finding
- May be finding fewer things because other agents now cover more ground (reliability-reviewer overlap?)

### Unchanged Agents

**a11y-reviewer** — Same profile
- Hit rate: 40% → 43% (within noise)
- Still on Opus — candidate for Sonnet demotion
- Cost/finding improved slightly (20.0 → 10.8 Hq) but still expensive for its yield

**dead-code-reviewer** — Correctly throttled
- Dispatches: 19 → 4 (triage skipping additive PRs)
- Hit rate: 32% → 25% (too few samples)
- When dispatched, found 1 issue — still precise

### New Agent

**reliability-reviewer** — Strong debut
- 8 dispatches, 29 findings, **100% hit rate**, **2.0 Hq/finding**
- 2 CRITICAL, 4 HIGH severity findings — highest severity density
- 3.6 findings/dispatch — highest volume per dispatch
- Cost: 57.7M Hq-eq total (6.5% of budget) — reasonable for its output
- Not in the baseline — appears to have been added during the optimization period

---

## Pipeline Wall-Clock Analysis

| Session | Date | Project | Agents | Wall Clock | Bottleneck |
|---|---|---|---|---|---|
| e236125c | Feb 28 10:53 | ciab-admin | 11 | 6m55s | history-insights (Opus) |
| 980ab377 | Feb 28 11:22 | ciab-admin | 11 | 5m20s | history-insights (Opus) |
| cfd9ead0 | Mar 1 19:29 | ciab-admin | 11 | 7m32s | security (Sonnet) |
| f6c03837 | Mar 1 20:04 | ciab-admin | 12 | 8m25s | history-insights (Sonnet) |
| 6f6098d2 | Mar 2 04:13 | ciab-admin-dup | 8 | **10m29s** | history-insights (Sonnet) |
| b9e2bfd6 | Mar 2 04:37 | ciab-admin | 12 | 7m16s | patterns (Sonnet) |
| 9259749b | Mar 2 06:51 | ciab-admin-dup | 11 | 5m2s | history-insights (Sonnet) |
| 0146c3bf | Mar 2 07:43 | ciab-admin | 12 | 6m40s | history-insights (Sonnet) |
| 9cbf39d2 | Mar 2 10:17 | woo-develop | 11 | 7m52s | wp-arch secondary (Opus) |
| f3e6f60f | Mar 2 10:21 | ciab-admin | 12 | 6m15s | history-insights (Sonnet) |

**Average wall clock: 7m11s** (baseline: ~5m35s) — **+29% slower**

**history-insights remains the dominant bottleneck** — it's the wall-clock bottleneck in 7 of 10 sessions. Moving it to Sonnet didn't help with speed; it may have hurt by adding more context (pre-computed diffs/file history) while using a model that's slower at processing large context.

---

## Verdict Distribution

| Verdict | Post-Change | % | Baseline | % |
|---|---|---|---|---|
| APPROVE | 34 | 31% | 74 | 24% |
| COMMENT | 60 | 54% | 100 | 33% |
| REQUEST_CHANGES | 7 | 6% | 23 | 8% |
| none | 10 | 9% | 107 | 35% |

The "none" verdict dropped from 35% to 9%. The remaining 10 "none" verdicts are mostly from reconciliator sessions that were misidentified as wp-architecture-reviewer (reconciliators don't produce review verdicts). This suggests the deterministic orchestration changes improved verdict capture significantly.

---

## Recommendations

### 1. Address history-insights speed regression
Despite the efficiency overhaul, history-insights is 20% slower (6m44s vs 5m35s). Options:
- **Reduce pre-computed context size** — the file history and diffs may be too large for Sonnet to process efficiently
- **Add a token budget cap** — limit the total input context to prevent runaway exploration
- **Experiment with Haiku** — for the mechanical git-log-mining phase

### 2. Validate the 100% hit rate agents
patterns-reviewer (41% → 100%), architecture-reviewer (67% → 100%), performance-reviewer (56% → 100%), and others jumped to 100% hit rate. This needs validation:
- **Run ingest-code-review on 5 recent sessions** to get precision metrics
- If precision dropped significantly, the agents may be generating more noise post-Sonnet
- If precision held, the improvements are genuine

### 3. Consider demoting a11y-reviewer to Sonnet
Still on Opus, 43% hit rate, 10.8 Hq/finding. The Opus premium isn't justified for WCAG checklist evaluation. Other agents that moved to Sonnet maintained or improved their performance.

### 4. Investigate pr-reviewer hit rate drop
Hit rate dropped from 59% to 50%. The pr-reviewer is the most expensive per-finding agent that remains on Opus (12.1 Hq/finding). If the reliability-reviewer is covering some of its findings, consider:
- Narrowing pr-reviewer scope to pure cross-cutting synthesis
- Or reducing its dispatch frequency

### 5. Audit reliability-reviewer for overlap
New agent, 100% hit rate, 29 findings — but no overlap analysis exists yet. If it overlaps significantly with existing agents (especially architecture or performance), its 6.5% budget share may not be fully justified.

---

## Appendix: Per-Session Breakdown

### Session Metrics Summary

| Session | Date | Project | Agents | Cache (M) | Findings | Wall | Cost/Find (Hq) |
|---|---|---|---|---|---|---|---|
| e236125c | Feb 28 | ciab | 11 | 21.9 | 12 | 6m55s | 7.3 |
| 980ab377 | Feb 28 | ciab | 11 | 19.4 | 12 | 5m20s | 6.5 |
| cfd9ead0 | Mar 1 | ciab | 11 | 30.8 | 24 | 7m32s | 5.2 |
| f6c03837 | Mar 1 | ciab | 12 | 35.4 | 22 | 8m25s | 6.4 |
| 6f6098d2 | Mar 2 | ciab-dup | 8 | 17.0 | 17 | 10m29s | 3.4 |
| b9e2bfd6 | Mar 2 | ciab | 12 | 34.0 | 25 | 7m16s | 5.6 |
| 9259749b | Mar 2 | ciab-dup | 11 | 17.1 | 8 | 5m2s | 8.8 |
| 0146c3bf | Mar 2 | ciab | 12 | 28.8 | 28 | 6m40s | 4.0 |
| 9cbf39d2 | Mar 2 | woo-dev | 11 | 16.8 | 35 | 7m52s | 2.0 |
| f3e6f60f | Mar 2 | ciab | 12 | 28.8 | 21 | 6m15s | 5.4 |

### Model Transition Timeline

| Date | Opus Agents | Sonnet Agents | Notes |
|---|---|---|---|
| Feb 28 | arch, wp-arch, patterns, hist-ins, pr, a11y | sec, perf, js-tests, dead-code | Pre-demotion |
| Mar 1+ | pr, a11y, wp-arch(bug) | arch, patterns, hist-ins, sec, perf, js/php/e2e-tests, dead-code, reliability, wp-arch(primary) | Post-demotion |

---

## Methodology Notes

1. **Session selection:** All sessions with ≥5 reviewer agents from Feb 28 onward across ciab-admin (7), ciab-admin-dup (2), and woo-develop (1). Single-agent sessions excluded.
2. **Metrics extraction:** Using `extract-session-metrics.py` for per-agent operational metrics (tokens, duration, model, findings, severity, verdict).
3. **Cost normalization:** Opus ×5, Sonnet ×3, Haiku ×1 (based on Claude API cache read pricing, Feb 2026).
4. **Baseline:** 47-session dataset from the 2026-02-28 value ranking analysis (pre-optimization).
5. **Limitations:**
   - No precision/quality validation on post-change findings (ingest-code-review not run on these sessions)
   - Small sample (10 sessions) vs baseline (47 sessions) — some metrics may shift with more data
   - Feb 28 sessions (2 of 10) ran during the transition — they have pre-demotion Opus models for some agents
   - Different PR sizes/complexity across sessions affects per-session averages
