# Ingest Validation Analysis: Review Agent Precision

**Date:** 2026-02-28
**Sample:** 313 validated findings across 29 sessions (2 projects)
**Method:** Extracted ingest-code-review step 6 validation tables from Claude Code session transcripts

| Project | Tech Stack | Sessions | Findings |
|---|---|---|---|
| cabrero | Go CLI/TUI | 9 | 109 |
| ciab-admin | WordPress/React/PHP | 20 | 204 |

---

## Overall Accuracy

| Metric | Count | Rate |
|---|---|---|
| **Actionable** (CONFIRMED + LIKELY VALID) | 217 | **69.3%** |
| STYLE/PREFERENCE | 49 | 15.7% |
| OUT OF SCOPE | 20 | 6.4% |
| FALSE POSITIVE | 15 | 4.8% |
| DISMISSED | 9 | 2.9% |
| INFORMATIONAL | 3 | 1.0% |

## Cross-Project Comparison

| Metric | cabrero (Go) | ciab-admin (WP/JS) | Delta |
|---|---|---|---|
| Actionable rate | **83.5%** | **61.8%** | -21.7pp |
| FALSE POSITIVE | 4.6% | 4.9% | +0.3pp |
| OUT OF SCOPE | 1.8% | 8.8% | **+7.0pp** |
| STYLE/PREFERENCE | 10.1% | 18.6% | **+8.5pp** |

The FP rate is nearly identical across projects (~5%). The precision gap is driven by two categories:

- **OUT OF SCOPE (+7pp):** Reviewers flag pre-existing WordPress code not in the PR diff more often — likely because WordPress plugins share more interconnected state than a standalone Go CLI.
- **STYLE/PREFERENCE (+8.5pp):** More subjective findings in the JS/React codebase — formatting opinions, test style preferences, documentation conventions.

## Per-Agent Precision (combined, ranked by sample size)

| Agent | Findings | Actionable | Precision | FP | FP Rate | Primary Noise |
|---|---|---|---|---|---|---|
| **UNKNOWN** (no source) | 80 | 64 | 80.0% | 7 | 8.8% | FP, STYLE |
| **patterns-reviewer** | 71 | 47 | 66.2% | 0 | **0.0%** | STYLE (14), OOS (9) |
| **history-insights** | 49 | 33 | 67.3% | 1 | 2.0% | STYLE (7), OOS (4) |
| **architecture-reviewer** | 43 | 27 | 62.8% | 3 | 7.0% | STYLE (12), OOS (1) |
| **pr-reviewer** | 38 | 30 | 78.9% | 1 | 2.6% | OOS (4), STYLE (2) |
| **performance-reviewer** | 24 | 16 | 66.7% | 0 | 0.0% | STYLE (6), OOS (1) |
| **js-tests-reviewer** | 24 | 20 | 83.3% | 0 | 0.0% | STYLE (3), DISMISSED (1) |
| **wp-architecture-reviewer** | 23 | 14 | 60.9% | 3 | **13.0%** | OOS (3), STYLE (3) |
| **dead-code-reviewer** | 14 | 13 | **92.9%** | 0 | 0.0% | STYLE (1) |
| **security-reviewer** | 9 | 6 | 66.7% | 1 | 11.1% | OOS (1), DISMISSED (1) |
| **e2e-tests-reviewer** | 5 | 4 | 80.0% | 0 | 0.0% | STYLE (1) |
| **gemini-reviewer** | 4 | 4 | **100%** | 0 | 0.0% | — |
| **codex-reviewer** | 1 | 1 | **100%** | 0 | 0.0% | — |
| **php-tests-reviewer** | 3 | 3 | **100%** | 0 | 0.0% | — |
| **go-tests-reviewer** | 2 | 1 | 50.0% | 0 | 0.0% | STYLE (1) |
| **a11y-reviewer** | 2 | 0 | 0.0% | 0 | 0.0% | STYLE (2) |

### Per-Agent Precision by Project

| Agent | cabrero Precision | ciab-admin Precision | Notes |
|---|---|---|---|
| **pr-reviewer** | 100% (9) | 72.4% (29) | Drops in WP — more OOS findings |
| **history-insights** | 100% (9) | 60.0% (40) | Significant drop — more STYLE + OOS in WP |
| **patterns-reviewer** | 84.2% (19) | 59.6% (52) | Higher STYLE + OOS in WP; 0% FP in both |
| **architecture-reviewer** | 80.0% (15) | 53.6% (28) | 0% FP in Go, 10.7% FP in WP |
| **dead-code-reviewer** | 88.9% (9) | 100% (5) | Consistently precise |
| **performance-reviewer** | 66.7% (6) | 66.7% (18) | Identical precision across projects |
| **security-reviewer** | 75.0% (4) | 60.0% (5) | Small samples; both have OOS/FP issues |

## Severity vs Actionability

| Severity | Total | Actionable | Rate |
|---|---|---|---|
| CRITICAL | 3 | 2 | 67% |
| HIGH | 42 | 39 | **92.9%** |
| MEDIUM | 158 | 114 | 72.2% |
| LOW | 88 | 50 | 56.8% |
| INFO/UNKNOWN | 22 | 12 | 54.5% |

HIGH findings are 93% actionable — severity calibration is strong at the top. LOW findings drop to 57% — most STYLE/PREFERENCE noise concentrates here.

## Key Patterns

### 1. FP rate is stable at ~5% regardless of project

The false positive rate barely changes between Go (4.6%) and WordPress (4.9%). The FP failure mode is consistent: **the reviewer misread the actual code**. This is an inherent LLM limitation, not an agent design problem.

### 2. The precision gap is STYLE/PREFERENCE and OUT OF SCOPE, not FP

ciab-admin's lower actionable rate (61.8% vs 83.5%) comes from more subjective findings and more scope drift — not from more wrong findings. This suggests the WordPress/JS ecosystem generates more surface area for opinionated feedback.

### 3. patterns-reviewer: highest volume, zero FPs, but most noise

71 findings (largest contributor) with 0 false positives but 66.2% precision. Its noise is 14 STYLE and 9 OUT OF SCOPE findings — never factually wrong, but often raising patterns from code outside the PR's diff or flagging stylistic preferences. The Tier A changes from v1.36.0 (3+ usage gate, staleness check, proximity modifiers) should reduce this.

### 4. wp-architecture-reviewer has the highest FP rate (13%)

3 FPs from 23 findings. The FP pattern is specific: misunderstanding framework conventions — flagging documented API patterns as problems, flagging developer-only errors as needing i18n, claiming a function was "dead" when it was cleanly removed. This agent needs better framework-awareness.

### 5. architecture-reviewer degrades in WordPress codebases

80% precision in Go drops to 53.6% in WordPress. The additional noise is mostly STYLE (abstract architecture opinions that don't apply to WordPress plugin conventions) and FPs (3 in ciab vs 0 in cabrero).

### 6. OUT OF SCOPE is a systemic issue in WordPress reviews

20 OOS findings (6.4% overall, 8.8% in ciab). Reviewers flag pre-existing code outside the PR diff — especially patterns-reviewer (9 OOS) and pr-reviewer (4 OOS). WordPress plugins' interconnected nature makes scope boundaries harder to enforce.

### 7. HIGH severity findings are highly reliable

92.9% actionable at HIGH severity. The review pipeline is well-calibrated — when agents flag something as HIGH, it's almost always a real issue. The noise concentrates at LOW severity (57% actionable).

## False Positive Details (all 15)

### cabrero (Go) — 5 FPs

| # | Finding | Severity | Reason | Session |
|---|---|---|---|---|
| 1 | FD leak in `logview/follow.go` | CRITICAL | `f.Close()` is on line 46, unconditional close in all paths | 2d9ae8ab |
| 2 | `RenderStatusBar` O(N²) | MEDIUM | Code already has linear pass with pre-computed widths | 2d9ae8ab |
| 3 | `escapeAppleScript` not UTF-8 safe | MEDIUM | Already uses `for _, r := range s` (rune iteration) | 2d9ae8ab |
| 4 | `TestRelativeTime` flaky | MEDIUM | 50s buffer makes it not realistically flaky | 2d9ae8ab |
| 5 | `escapeAppleScript` UTF-8 unsafe (byte-index) | MEDIUM | Target chars are ASCII-range, cannot be UTF-8 continuation bytes | 3aee0c2e |

### ciab-admin (WordPress) — 10 FPs

| # | Finding | Source | Severity | Reason | Session |
|---|---|---|---|---|---|
| 1 | Query machinery complexity | — | — | No reason recorded | 163b376e |
| 2 | `buildPayoutTimelineItems` untestable | architecture | LOW | All 5 branches tested indirectly via component tests | 35482f6f |
| 3 | `target="_blank"` without `rel=noopener` | security | MEDIUM | `rel` IS in `NOTE_ALLOWED_ATTR`. Agent misread the code | 48140706 |
| 4 | Success notice fires before polling confirms | pr-reviewer | LOW | Notice fires after POST returns valid `export_id`. Text says "being prepared" — accurate | 7d63c591 |
| 5 | Hook reference as data property | architecture | LOW | Documented framework API (`useIsComplete` in types.ts) | a5f2d6df |
| 6 | Dead `formatDateShort` in format-date.ts | wp-arch + arch | MEDIUM | Function was cleanly removed; only pre-existing JSDoc issue | bd0ca19c |
| 7 | Transaction PHP hook activates for first time | history-insights | HIGH | JS side never requests the data — hook is unreachable | bf998bad |
| 8 | `useIsWooPaymentsActivated` returns `undefined` | wp-architecture | MEDIUM | `CompletionState` type is explicitly `boolean \| undefined` — correct | d7a5a401 |
| 9 | Hardcoded error messages in `createFetchClientSecret` | wp-architecture | MEDIUM | Stripe SDK integration errors, not user-facing strings | d7a5a401 |
| 10 | Hardcoded English error messages | — | MEDIUM | Developer-only errors, never surface to users | d7a5a401 |

**FP root causes:**
- **Misreading code** (7/15): Agent didn't read the actual implementation carefully enough
- **Misunderstanding framework conventions** (5/15): Agent flagged documented/intentional API patterns
- **Overclaiming** (3/15): Agent made a testability/complexity claim that doesn't hold under scrutiny

## Actionable Insights for Agent Improvement

| Priority | Agent | Issue | Potential Fix |
|---|---|---|---|
| 1 | **wp-architecture-reviewer** | 13% FP rate, misunderstands framework APIs | Add instruction to verify against type definitions before flagging; reduce confidence for convention claims without code evidence |
| 2 | **architecture-reviewer** | 7% FP rate in WP, 53.6% precision | Raise confidence threshold for non-bug findings; add WordPress-specific context about when "architecture opinions" are STYLE vs real issues |
| 3 | **patterns-reviewer** | 0% FP but 66.2% precision, 9 OOS findings | Tier A changes (v1.36.0) should help; additionally enforce stricter diff-scope checking for pattern searches |
| 4 | **history-insights** | 67.3% precision, drops to 60% in WP | Tighten scope enforcement — historical context from unrelated code areas shouldn't become findings |
| 5 | **All agents** | 6.4% OUT OF SCOPE rate | Strengthen the bootstrap-reviewer scope enforcement — the `REVIEW SCOPE` vs `EXPLORATION SCOPE` distinction may need clearer guardrails |
| 6 | **All agents** | 15.7% STYLE/PREFERENCE rate | Consider adding a "is this a bug or a preference?" self-check before reporting LOW/MEDIUM findings |
| 7 | **All agents** | FP root cause: misreading code | Add a "verify by reading the actual implementation line" step for factual claims about what code does |

## Appendix A: Per-Session Breakdown — cabrero (Go)

| Session | Findings | CONFIRMED | LIKELY VALID | FP | OOS | STYLE |
|---|---|---|---|---|---|---|
| 00e49455 | 6 | 5 | 1 | 0 | 0 | 0 |
| 28252e0f | 5 | 4 | 0 | 0 | 0 | 1 |
| 2d9ae8ab | 20 | 12 | 2 | 4 | 0 | 2 |
| 3aee0c2e | 20 | 19 | 0 | 1 | 0 | 0 |
| 4167547c | 4 | 4 | 0 | 0 | 0 | 0 |
| 5566d86d | 7 | 4 | 2 | 0 | 0 | 1 |
| c4fc2328 | 9 | 9 | 0 | 0 | 0 | 0 |
| c7ac13a6 | 20 | 14 | 1 | 0 | 0 | 5 |
| e1a9ef12 | 18 | 12 | 2 | 0 | 2 | 2 |

## Appendix B: Per-Session Breakdown — ciab-admin (WordPress)

| Session | Branch | Findings | CONF | LV | FP | OOS | STYLE | DIS | INFO |
|---|---|---|---|---|---|---|---|---|---|
| 163b376e | add/WOOPRD-2691-list-auto-refresh | 4 | 2 | 0 | 1 | 0 | 1 | 0 | 0 |
| 180abd06 | trunk | 9 | 6 | 1 | 0 | 2 | 0 | 0 | 0 |
| 1b1a4b90 | add/dispute-details | 2 | 2 | 0 | 0 | 0 | 0 | 0 | 0 |
| 1ea2d5af | trunk | 7 | 3 | 2 | 0 | 1 | 1 | 0 | 0 |
| 35482f6f | add/woopayments-payout-detail-page | 8 | 3 | 0 | 1 | 0 | 4 | 0 | 0 |
| 41a3d5f0 | add/disputes-badges | 18 | 14 | 0 | 0 | 0 | 1 | 3 | 0 |
| 48140706 | add/woopayments-payout-detail-page | 8 | 5 | 2 | 1 | 0 | 0 | 0 | 0 |
| 73874246 | add/store-and-checkout | 4 | 2 | 1 | 0 | 1 | 0 | 0 | 0 |
| 7899bf0d | fix/woopayments-account-cache-worka | 12 | 8 | 0 | 0 | 0 | 4 | 0 | 0 |
| 7d63c591 | trunk | 15 | 9 | 3 | 1 | 0 | 1 | 1 | 0 |
| 980ab377 | add/WOOPRD-2691-list-auto-refresh | 11 | 5 | 0 | 0 | 0 | 6 | 0 | 0 |
| a5f2d6df | update/WOOPRD-2264-woopayments-onbo | 26 | 10 | 2 | 1 | 3 | 8 | 0 | 2 |
| bd0ca19c | add/woopayments-payout-detail-page | 9 | 5 | 1 | 1 | 0 | 2 | 0 | 0 |
| bf998bad | trunk | 7 | 1 | 1 | 1 | 4 | 0 | 0 | 0 |
| c7da9d8e | fix/woopayments-settings-design-gap | 5 | 2 | 2 | 0 | 1 | 0 | 0 | 0 |
| d7a5a401 | update/WOOPRD-2264-woopayments-onbo | 13 | 6 | 1 | 3 | 0 | 3 | 0 | 0 |
| e236125c | add/WOOPRD-2691-list-auto-refresh | 12 | 6 | 1 | 0 | 0 | 5 | 0 | 0 |
| e42af381 | fix/woopayments-settings-design-gap | 8 | 5 | 0 | 0 | 0 | 1 | 1 | 1 |
| fc83e60f | chore/update-woopayments-10.5.1 | 17 | 9 | 4 | 0 | 0 | 0 | 4 | 0 |
| fddb5115 | add/user-data-store | 9 | 2 | 0 | 0 | 6 | 1 | 0 | 0 |
