# Reviewer Agent Value Ranking

**Date:** 2026-02-28
**Data sources:**
- Operational metrics: 47 ciab-admin sessions with subagent data (305 agent invocations, 290 findings)
- Precision metrics: 29 sessions across 2 projects (313 validated findings via ingest-code-review)
- Overlap analysis: 38 multi-source findings from 20 ciab-admin sessions

---

## Composite Value Ranking

Combining precision (from ingest validation), operational metrics (47 sessions), and overlap analysis.

| Rank | Agent | Value | Precision | FP Rate | Hit Rate | Dispatches | Unique% | Avg Runtime | Avg Cache | Model |
|---|---|---|---|---|---|---|---|---|---|---|
| 1 | **js-tests-reviewer** | **Top tier** | 83.3% | 0% | 71% | 14/47 (30%) | 87.5% | 2m 24s | 1.1M | Sonnet |
| 2 | **performance-reviewer** | **Top tier** | 66.7% | 0% | 56% | 25/47 (53%) | 76.9% | 2m 45s | 1.5M | Sonnet |
| 3 | **pr-reviewer** | **High** | 78.9% | 2.6% | 59% | 27/47 (57%) | ~45% | 3m 16s | 2.9M | Opus |
| 4 | **dead-code-reviewer** | **High** (when hits) | 92.9% | 0% | 32% | 19/47 (40%) | 100% | 2m 48s | 2.0M | Sonnet |
| 5 | **history-insights-reviewer** | **Moderate** | 67.3% | 2.0% | 87% | 23/47 (49%) | ~55% | 5m 35s | 5.3M | Sonnet³ |
| 6 | **patterns-reviewer** | **Moderate** | 66.2% | 0% | 41% | 41/47 (87%) | 61.9% | 4m 38s | 4.8M | Sonnet³ |
| 7 | **wp-architecture-reviewer** | **Moderate** | 60.9% | **13.0%** | 33% | 55/47¹ (117%) | 80% | 2m 54s | 1.4M | Sonnet³ |
| 8 | **architecture-reviewer** | **Questionable** | 62.8% | 7.0% | 67% | 24/47 (51%) | **50%** | 2m 59s | 1.8M | Sonnet³ |
| 9 | **php-tests-reviewer** | **Promising** | 100% | 0% | 31% | 13/47 (28%) | 66.7% | 2m 30s | 1.9M | Sonnet |
| 10 | **e2e-tests-reviewer** | **Promising** | 80% | 0% | 46% | 13/47 (28%) | 100% | 1m 54s | 1.1M | Sonnet |
| 11 | **security-reviewer** | **Low** | 66.7% | 11.1% | **11%** | 38/47 (81%) | 60% | 3m 14s | 1.6M | Sonnet |
| 12 | **a11y-reviewer** | **Low** | 0%² | 0% | 40% | 5/47 (11%) | 100% | 1m 46s | 1.6M | Opus |
| 13 | **gemini-reviewer** | **Low** | 100% | 0% | **0%** | 5/47 (11%) | 66.7% | 3m 43s | 829K | Haiku |
| 14 | **codex-reviewer** | **Zero** | — | — | **0%** | 3/47 (6%) | — | 6m 4s | 671K | Haiku |

¹ wp-architecture-reviewer is dispatched multiple times in some sessions (55 dispatches across 47 sessions)
² Both validated a11y findings were STYLE/PREFERENCE — precision sample is only 2
³ Demoted from Opus (inherit) to Sonnet in v1.38.1 — metrics above reflect pre-demotion Opus runs

---

## Token Budget Breakdown

Total cache read tokens across 47 sessions: **728.4M** (93% cache hit rate, 305 agent executions)

| Agent | Dispatches | Total Cache Read | % Budget | Findings | Cache per Finding |
|---|---|---|---|---|---|
| patterns-reviewer | 41 | 196.8M | **27.0%** | 42 | 4.7M/finding |
| history-insights-reviewer | 23 | 121.9M | **16.7%** | 44 | 2.8M/finding |
| wp-architecture-reviewer | 55 | 77.0M | 10.6% | 44 | 1.8M/finding |
| pr-reviewer | 27 | 78.3M | 10.7% | 29 | 2.7M/finding |
| security-reviewer | 38 | 60.8M | **8.3%** | 5 | 12.2M/finding |
| architecture-reviewer | 24 | 43.2M | 5.9% | 33 | 1.3M/finding |
| dead-code-reviewer | 19 | 38.0M | 5.2% | 7 | 5.4M/finding |
| performance-reviewer | 25 | 37.5M | 5.1% | 24 | 1.6M/finding |
| php-tests-reviewer | 13 | 24.7M | 3.4% | 17 | 1.5M/finding |
| js-tests-reviewer | 14 | 15.4M | 2.1% | 27 | 571K/finding |
| e2e-tests-reviewer | 13 | 14.3M | 2.0% | 16 | 894K/finding |
| a11y-reviewer | 5 | 8.0M | 1.1% | 2 | 4.0M/finding |
| gemini-reviewer | 5 | 4.1M | 0.6% | 0 | ∞ |
| codex-reviewer | 3 | 2.0M | 0.3% | 0 | ∞ |

**The top 3 token consumers (patterns, history-insights, pr-reviewer) use 54.4% of the budget.** All three run on Opus. history-insights (5m 35s) and patterns (4m 38s) are the pipeline's wall-clock bottleneck — all other agents finish in 1m 46s – 3m 16s.

**Worst efficiency:** security-reviewer uses 12.2M cache tokens per finding — 21x worse than js-tests-reviewer (571K/finding). It's dispatched 38 times but only finds issues in 4 of those runs (11% hit rate).

---

## Cost-Normalized Token Budget (Haiku-Equivalent)

Raw token counts are misleading — a token on Opus costs 5x a token on Haiku. The pricing ratio is uniform across all token types (input, output, cache read, cache write):

| Model | Factor | Price Rationale |
|---|---|---|
| Opus | ×5 | $0.50/MTok cache read vs Haiku $0.10/MTok |
| Sonnet | ×3 | $0.30/MTok cache read vs Haiku $0.10/MTok |
| Haiku | ×1 | Baseline |

Source: [Claude API Pricing](https://platform.claude.com/docs/en/about-claude/pricing) (February 2026)

### Cost-normalized budget (47 sessions, cache reads)

| Agent | Model | Raw Cache MTok | Factor | Haiku-eq MTok | % Budget |
|---|---|---|---|---|---|
| patterns-reviewer | Opus | 196.8 | ×5 | **984.0** | 30.7% |
| history-insights-reviewer | Opus | 121.9 | ×5 | **609.5** | 19.0% |
| pr-reviewer | Opus | 78.3 | ×5 | **391.5** | 12.2% |
| wp-architecture-reviewer | Opus | 77.0 | ×5 | **385.0** | 12.0% |
| architecture-reviewer | Opus | 43.2 | ×5 | **216.0** | 6.7% |
| security-reviewer | Sonnet | 60.8 | ×3 | **182.4** | 5.7% |
| dead-code-reviewer | Sonnet | 38.0 | ×3 | **114.0** | 3.6% |
| performance-reviewer | Sonnet | 37.5 | ×3 | **112.5** | 3.5% |
| php-tests-reviewer | Sonnet | 24.7 | ×3 | **74.1** | 2.3% |
| js-tests-reviewer | Sonnet | 15.4 | ×3 | **46.2** | 1.4% |
| e2e-tests-reviewer | Sonnet | 14.3 | ×3 | **42.9** | 1.3% |
| a11y-reviewer | Opus | 8.0 | ×5 | **40.0** | 1.2% |
| gemini-reviewer | Haiku | 4.1 | ×1 | **4.1** | 0.1% |
| codex-reviewer | Haiku | 2.0 | ×1 | **2.0** | 0.1% |
| **Total** | | **722.0** | | **3,204.2** | |

**Key insight:** Opus agents consume **82% of the cost-normalized budget** despite only 72% of raw tokens. The 5x cost multiplier makes them disproportionately expensive. Patterns-reviewer alone (984 Haiku-eq MTok) costs more than all 8 Sonnet+Haiku agents combined (576 Haiku-eq MTok).

### Cost-per-finding (Haiku-normalized, sorted by efficiency)

| Agent | Haiku-eq MTok | Findings | Haiku-eq MTok/Finding | Precision |
|---|---|---|---|---|
| js-tests-reviewer | 46.2 | 27 | **1.7** | 83.3% |
| e2e-tests-reviewer | 42.9 | 16 | **2.7** | 80.0% |
| php-tests-reviewer | 74.1 | 17 | **4.4** | 100% |
| performance-reviewer | 112.5 | 24 | **4.7** | 66.7% |
| architecture-reviewer | 216.0 | 33 | **6.5** | 62.8% |
| wp-architecture-reviewer | 385.0 | 44 | **8.8** | 60.9% |
| pr-reviewer | 391.5 | 29 | **13.5** | 78.9% |
| history-insights-reviewer | 609.5 | 44 | **13.9** | 67.3% |
| dead-code-reviewer | 114.0 | 7 | **16.3** | 92.9% |
| a11y-reviewer | 40.0 | 2 | **20.0** | 0%² |
| patterns-reviewer | 984.0 | 42 | **23.4** | 66.2% |
| security-reviewer | 182.4 | 5 | **36.5** | 66.7% |
| gemini-reviewer | 4.1 | 0 | ∞ | — |
| codex-reviewer | 2.0 | 0 | ∞ | — |

**Security-reviewer costs 36.5 Haiku-eq MTok per finding** — 21x more than js-tests-reviewer (1.7). Patterns-reviewer at 23.4 and a11y at 20.0 are the next worst. The most efficient agents (js-tests, e2e-tests, php-tests, performance) are all on Sonnet — the Opus agents occupy the bottom half of the efficiency ranking.

### Model demotion savings scenarios

**Scenario A — Inherit → Sonnet (conservative, keep pr-reviewer on Opus):**

| Agent | Before (Haiku-eq) | After (Haiku-eq) | Saved |
|---|---|---|---|
| patterns | 984.0 | 590.4 | 393.6 |
| history-insights | 609.5 | 365.7 | 243.8 |
| wp-architecture | 385.0 | 231.0 | 154.0 |
| architecture | 216.0 | 129.6 | 86.4 |
| a11y | 40.0 | 24.0 | 16.0 |
| **Total** | **2,234.5** | **1,340.7** | **893.8 (28% overall)** |

**Scenario B — Aggressive: mechanical agents → Haiku, reasoning agents → Sonnet:**

| Agent | Current | Proposed | Saved (Haiku-eq) |
|---|---|---|---|
| patterns | Opus | Haiku | 787.2 |
| history-insights | Opus | Haiku | 487.6 |
| wp-architecture | Opus | Sonnet | 154.0 |
| architecture | Opus | Sonnet | 86.4 |
| a11y | Opus | Sonnet | 16.0 |
| dead-code | Sonnet | Haiku | 76.0 |
| reconciliator | Sonnet | Haiku | ~84* |
| **Total saved** | | | **~1,691 (53% overall)** |

*Reconciliator cost estimated (not in per-agent data)

---

## Agent Overlap Analysis

38 of 204 ciab-admin findings (18.6%) were reported by 2+ agents. Key overlap pairs:

| Agent Pair | Co-reported Findings | Overlap Type |
|---|---|---|
| **architecture + patterns** | 8 | Structural duplication, code reuse issues |
| **history-insights + patterns** | ~6 | Both find recurring patterns in git history |
| **architecture + history-insights** | ~5 | Architectural issues with historical context |
| **pr-reviewer + wp-architecture** | ~5 | PR-level concerns that are also WP-specific |
| **js-tests + pr-reviewer** | 3 | Test gaps noticed by both generalist and specialist |
| **performance + pr-reviewer** | 3 | Performance issues visible at PR level |

### The architecture–patterns–history triad

These three agents form a **high-overlap cluster**. They frequently co-report the same finding:
- architecture-reviewer has only **50% unique contribution** — half its findings are also reported by another agent (usually patterns)
- patterns-reviewer has **62% unique contribution** — better, but 38% overlap mostly with architecture
- history-insights has **~55% unique contribution** — overlaps with both patterns and architecture

When all three report the same finding, it's almost always CONFIRMED — **but reporting the same finding 3 times doesn't add 3x value**.

### Agents with highest unique contribution (least redundant)

| Agent | Unique % | Notes |
|---|---|---|
| dead-code-reviewer | 100% | Completely unique findings (when it finds anything) |
| e2e-tests-reviewer | 100% | Specialized domain, no overlap |
| a11y-reviewer | 100% | Specialized domain, no overlap |
| js-tests-reviewer | 87.5% | Highly specialized, minimal overlap |
| wp-architecture-reviewer | 80% | WP-specific concerns others miss |
| performance-reviewer | 76.9% | Perf-specific concerns others miss |

---

## Detailed Agent Assessments

### Tier 1: High Value — Keep as-is

**js-tests-reviewer** — The standout agent
- 71% hit rate (10/14), 83.3% precision, 87.5% unique contributions
- Fastest (2m 24s) and cheapest (1.1M cache) agent in the pipeline
- 571K cache tokens per finding — **21x more efficient** than security-reviewer
- Runs on Sonnet, finds HIGH severity issues (test gaps, mock misuse)
- Only dispatched when JS test files exist (30% of sessions) — correctly conditional

**performance-reviewer** — Best value among high-frequency agents
- 56% hit rate (14/25), 66.7% precision, 76.9% unique contributions
- Cheap (1.5M cache on Sonnet), fast (2m 45s), focused findings
- All 24 findings are MEDIUM severity — practical, actionable performance issues
- 0% false positive rate across both projects

### Tier 2: Valuable but expensive

**pr-reviewer** — The generalist backbone
- 78.9% precision, 59% hit rate (16/27), ~45% unique contribution
- Overlaps with wp-architecture (5 shared findings) and js-tests (3 shared)
- 2.9M cache avg on Opus for 1.8 findings/run
- VALUE: catches cross-cutting issues other specialists miss; produces the PR-level synthesis

**history-insights-reviewer** — Highest hit rate, highest cost
- **87% hit rate** (20/23) — most consistent finding producer
- 5.3M cache on Opus, 5m 35s runtime — most expensive and **slowest agent** (pipeline bottleneck)
- 44 total findings, 67.3% precision, ~55% unique contribution
- 16.7% of total token budget
- VALUE: only agent that surfaces lessons from git history; unique perspective worth the cost

**patterns-reviewer** — Highest volume, most dispatched, zero FPs
- 42 total findings (tied for most), 0% FP rate, 66.2% precision
- **Most dispatched agent: 41/47 sessions (87%)**
- 4.8M cache on Opus, 4m 38s runtime — second pipeline bottleneck
- 27.0% of total token budget — largest single consumer
- 38% of its findings overlap with architecture-reviewer
- VALUE: unique pattern-detection, but noise ratio is high. v1.36.0 changes should help.

### Tier 3: Situational value

**wp-architecture-reviewer** — WP-specific, high volume, but highest FP rate
- **Most invoked: 55 times** (dispatched multiple times per session)
- 80% unique contribution (valuable WP specialization)
- 13% FP rate — highest of any agent, from misunderstanding framework conventions
- 33% hit rate (18/55) — many invocations find nothing
- CONCERN: 3 FPs in 23 validated findings; pattern is always "misread the API/type definitions"

**dead-code-reviewer** — Precise but rarely fires
- 92.9% precision, 100% unique contribution — when it finds something, it's real and unique
- **68% zero-finding rate** (13/19 runs produce nothing)
- 2.0M cache per run on Sonnet
- RECOMMENDATION: Consider conditional dispatch (skip for small PRs or PRs that don't add/remove files)

**architecture-reviewer** — Most redundant agent
- 62.8% precision (lowest among high-frequency agents)
- **50% overlap** — half its findings are also reported by patterns-reviewer
- Degrades from 80% precision (Go) to 54% (WordPress)
- 67% hit rate (16/24) but many findings are duplicated by patterns
- CONCERN: highest redundancy with patterns-reviewer (8 co-reported findings)

**php-tests-reviewer** — Promising but conditional
- 100% precision in validation (n=3), 31% hit rate (4/13)
- 4.2 avg findings when it fires — highest density of any agent
- Cheap on Sonnet, fast (2m 30s)
- Only dispatched when PHP test files exist (28% of sessions) — correctly conditional

**e2e-tests-reviewer** — Promising, fast, cheap
- 80% precision in validation (n=5), 46% hit rate (6/13), 100% unique
- Fastest agent at 1m 54s, cheap on Sonnet (1.1M cache)
- 2.7 avg findings when it fires
- Only dispatched when E2E test files exist (28% of sessions) — correctly conditional

### Tier 4: Low value — Candidates for removal or restructuring

**security-reviewer** — Most dispatched, least productive
- **89% zero-finding rate** (34/38 runs produce nothing)
- Dispatched in 81% of sessions (38/47) — nearly always included
- 60.8M total cache tokens for only 5 findings = **12.2M cache per finding**
- 11.1% FP rate (1 FP: misread `rel` attribute in HTML)
- RECOMMENDATION: Conditional dispatch — only for PRs touching auth, input handling, or data flow

**a11y-reviewer** — 0% precision in ingest validation
- Both ingest-validated findings were STYLE/PREFERENCE
- 40% hit rate (2/5), but findings aren't actionable
- 1.6M cache on Opus — expensive for its yield
- RECOMMENDATION: Needs more data; consider switching to Sonnet and restricting to UI PRs

**gemini-reviewer** — Zero findings in 47-session sample
- 0/5 runs produced findings in the operational data
- The 1 CRITICAL finding (Math.ceil bug) was from the ingest validation sample
- 829K cache on Haiku — cheap but unproductive
- VALUE: too inconsistent to justify default dispatch

**codex-reviewer** — Zero value observed
- 0 findings in 3 runs, 671K cache, **6m 4s runtime** (slowest agent by far)
- RECOMMENDATION: Remove from default dispatch

---

## Recommendations

### 1. Remove or make opt-in (save ~1% token budget, reduce noise)
- **codex-reviewer**: Zero findings across 3 runs, slowest agent (6m). Remove from default dispatch.
- **gemini-reviewer**: Zero findings in 47-session operational data. Make opt-in.

### 2. Conditional dispatch (save ~14% token budget)
- **security-reviewer**: 89% zero-finding rate, dispatched in 81% of sessions. Only dispatch for PRs touching auth, REST endpoints, input handling, or data sanitization. Saves ~8.3% of total budget.
- **dead-code-reviewer**: 68% zero-finding rate. Only dispatch for PRs that add/remove files or refactor imports. Saves ~5.2% of total budget.

### 3. Address the architecture–patterns overlap (save ~6% token budget)
The architecture-reviewer and patterns-reviewer share 8 findings — the most of any pair. Options:
- **Option A**: Merge architecture-reviewer into patterns-reviewer (patterns already detects 62% of architecture's findings)
- **Option B**: Narrow architecture-reviewer scope to SOLID/coupling/cohesion only, explicitly excluding "pattern consistency" from its mandate (which patterns-reviewer already covers)
- **Option C**: Keep both but add dedup in reconciliation step

### 4. Model demotions (save 28-53% of cost-normalized budget)

The Opus→Haiku cost ratio is 5:1 (not close to 1:1). Six agents run on Opus and consume 82% of the cost-normalized budget. See the "Model demotion savings scenarios" section above for detailed numbers.

**Phase 1 — Pin inherit agents to Sonnet (save 28%):**
- **architecture-reviewer**: 62.8% precision, 50% unique — doesn't justify Opus.
- **wp-architecture-reviewer**: 60.9% precision, 13% FP — Opus isn't preventing FPs.
- **patterns-reviewer**: Largest single cost (984 Haiku-eq MTok). Pattern matching is structured work.
- **history-insights-reviewer**: Second largest cost (609.5 Haiku-eq MTok). Git log mining is structured.
- **a11y-reviewer**: WCAG checklist evaluation. Sonnet is sufficient.
- Keep **pr-reviewer** on Opus — anchor role, 78.9% precision, worth the premium.

**Phase 2 — Experiment with Haiku for mechanical agents:**
- **patterns-reviewer** → Haiku: Search + compare is the most mechanical work. Saves 787 Haiku-eq MTok.
- **history-insights** → Haiku: Git log correlation. Saves 488 Haiku-eq MTok.
- **dead-code-reviewer** → Haiku: Reference checking. Saves 76 Haiku-eq MTok.
- **reconciliator** → Haiku: JSON aggregation, not analysis. Saves ~84 Haiku-eq MTok.
- Validate quality over 10 sessions before committing.

### 5. Pipeline bottleneck
**history-insights-reviewer** (5.6m) and **patterns-reviewer** (5.4m) set the wall-clock time for the entire parallel dispatch. All other agents finish in 2-3m. Demoting these two to Sonnet or Haiku would likely also reduce wall-clock time (faster models, less exploration), cutting total review time by ~40%.

---

## Appendix: Operational Summary (47 sessions)

### Duration distribution (305 invocations)

- **Average:** 3m 20s
- **Median:** 2m 51s
- **Min:** 10s, Max: 26m 14s
- 83% of agents complete within 5 minutes

### Model distribution (pre-v1.38.1 demotion)

| Model | Executions | % |
|---|---|---|
| claude-opus-4-6 | 137 | 45% |
| claude-sonnet-4-6 | 92 | 30% |
| claude-sonnet-4-5 | 34 | 11% |
| claude-haiku-4-5 | 23 | 8% |
| claude-opus-4-5 | 9 | 3% |

Post-v1.38.1: 4 agents moved from Opus (inherit) to Sonnet (architecture, wp-architecture, patterns, history-insights). pr-reviewer and a11y-reviewer remain on Opus (inherit). Expected new distribution: ~25% Opus, ~60% Sonnet, ~15% Haiku.

### Verdict distribution

| Verdict | Count | % |
|---|---|---|
| COMMENT | 100 | 33% |
| APPROVE | 74 | 24% |
| REQUEST_CHANGES | 23 | 8% |
| No verdict captured | 107 | 35% |

### Extraction script

The session metrics were extracted using `plugins/pirategoat-tools/scripts/extract-session-metrics.py` — a general-purpose tool for extracting operational metrics from Claude Code session transcripts and their subagent files.
