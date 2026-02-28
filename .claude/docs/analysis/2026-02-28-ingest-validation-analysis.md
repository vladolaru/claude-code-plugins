# Ingest Validation Analysis: Review Agent Precision

**Date:** 2026-02-28
**Sample:** 109 validated findings across 9 sessions (cabrero project, Go codebase)
**Method:** Extracted ingest-code-review step 6 validation tables from Claude Code session transcripts

---

## Overall Accuracy

| Metric | Count | Rate |
|---|---|---|
| **Actionable** (CONFIRMED + LIKELY VALID) | 91 | **83.5%** |
| STYLE/PREFERENCE | 11 | 10.1% |
| FALSE POSITIVE | 5 | 4.6% |
| OUT OF SCOPE | 2 | 1.8% |

## Per-Agent Precision (ranked)

| Agent | Findings | Actionable | Precision | Noise Type |
|---|---|---|---|---|
| **pr-reviewer** | 9 | 9 | **100%** | — |
| **history-reviewer** | 9 | 9 | **100%** | — |
| **codex-reviewer** | 1 | 1 | **100%** | — |
| **dead-code-reviewer** | 9 | 8 | **88.9%** | 1 STYLE |
| **patterns-reviewer** | 19 | 16 | **84.2%** | 3 STYLE |
| **architecture-reviewer** | 15 | 12 | **80.0%** | 3 STYLE |
| **security-reviewer** | 4 | 3 | **75.0%** | 1 OUT OF SCOPE |
| **performance-reviewer** | 6 | 4 | **66.7%** | 1 OUT OF SCOPE, 1 STYLE |
| **testing-reviewer** | 2 | 1 | **50.0%** | 1 STYLE |

Note: 53 findings (48.6%) came from sessions where the ingest table lacked a Source column — all 5 false positives are in this group.

## Severity vs Actionability

| Severity | Total | Actionable | Rate |
|---|---|---|---|
| CRITICAL | 3 | 2 | 67% (1 FP: misread code) |
| HIGH | 17 | 17 | **100%** |
| MEDIUM | 55 | 46 | 83.6% |
| LOW | 34 | 26 | 76.5% |

## Key Patterns

### 1. No agent consistently produces false positives

All 5 FPs came from sessions without source attribution, so they can't be pinned to specific agents. The FP descriptions reveal a common thread: **the reviewer misread the actual code** — claiming an FD leak when `f.Close()` was unconditional, claiming O(N²) when code already had a linear optimization, claiming byte-level unsafety when code used rune iteration.

### 2. Nearly all noise is STYLE/PREFERENCE, not wrong

11 of 18 non-actionable findings (61%) are technically correct observations about stylistic or subjective issues ("redundant import alias", "pipeline stdout coupling", "CLI inline truncation"). The reviewers aren't wrong — they're raising items the developer considers low-value.

### 3. Severity strongly predicts actionability

HIGH findings are 100% actionable. MEDIUM is 83.6%. LOW drops to 76.5%. All STYLE/PREFERENCE noise concentrates at MEDIUM and LOW severity — the system's severity calibration is working well.

### 4. patterns-reviewer: high volume, solid precision

Produces the most findings (19) but maintains 84.2% precision. Its noise is exclusively style/preference issues, not false positives. The Tier A changes from v1.36.0 (3+ usage gate, staleness check) should further reduce its style-level noise.

### 5. performance-reviewer and security-reviewer flag pre-existing code

Their OUT OF SCOPE findings (`store.ListSessions() overhead`, `Non-atomic write to settings.json`) exist in the codebase but aren't part of the PR's changes. This suggests the exploration scope boundary could be tighter.

## Actionable Insights for Agent Improvement

| Priority | Agent | Issue | Potential Fix |
|---|---|---|---|
| 1 | **performance-reviewer** | 66.7% precision, flags pre-existing code | Tighter scope enforcement — only flag performance issues in changed code or code directly called by changes |
| 2 | **testing-reviewer** | 50% precision (small sample) | Need more data, but its STYLE finding ("exact string match in test") suggests it may be too opinionated about test style |
| 3 | **architecture-reviewer** | 3 STYLE findings at 80% precision | Its style findings ("Claude CLI invocation without abstraction", "Pipeline stdout coupling") are architecture opinions — consider raising the confidence threshold for non-bug findings |
| 4 | **All agents** | FP pattern: misreading code | The 5 FPs all involved the reviewer not reading the actual implementation carefully enough before reporting — this is an inherent LLM limitation but could be mitigated with a "verify by reading the actual code" step |

## False Positive Details

| # | Finding | Severity | Reason for FP | Session |
|---|---|---|---|---|
| 1 | FD leak in `logview/follow.go` | CRITICAL | `f.Close()` is on line 46, *before* the `if n > 0` — unconditional close in all paths | 2d9ae8ab |
| 2 | `RenderStatusBar` O(N²) | MEDIUM | Code already has "Pre-compute each part's rendered width once" comment + linear pass | 2d9ae8ab |
| 3 | `escapeAppleScript` not UTF-8 safe | MEDIUM | Already uses `for _, r := range s` (rune iteration) + `b.WriteRune(r)` | 2d9ae8ab |
| 4 | `TestRelativeTime` flaky | MEDIUM | Threshold is 60s; test uses `-10s` input — 50s buffer; not realistically flaky | 2d9ae8ab |
| 5 | `escapeAppleScript` UTF-8 unsafe (byte-index loop) | MEDIUM | `"` (0x22) and `\` (0x5C) are ASCII-range — cannot appear as UTF-8 continuation bytes. Byte-loop is safe for these two cases | 3aee0c2e |

## Sample Limitations

- All sessions are from a single project (cabrero, Go codebase) — precision rates may differ for WordPress/PHP codebases
- 53/109 findings lack source attribution, limiting per-agent analysis
- testing-reviewer and codex-reviewer have very small samples (2 and 1 findings respectively)
- No a11y-reviewer, js-tests-reviewer, e2e-tests-reviewer, wp-architecture-reviewer, or gemini-reviewer findings in this sample (those agents weren't dispatched or produced no findings in these sessions)

## Per-Session Breakdown

| Session | Findings | CONFIRMED | LIKELY VALID | FALSE POSITIVE | OUT OF SCOPE | STYLE/PREF |
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
