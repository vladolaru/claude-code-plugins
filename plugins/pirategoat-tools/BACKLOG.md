# Backlog

The canonical home for deferred work: follow-ups from audits and reviews that
were judged real but deliberately not fixed yet. Session analysis docs under
`.claude/docs/` are gitignored and session-bound — an item recorded only there
is an item lost. If a review or audit defers something, it lands here or it
does not exist.

**Entry contract:** each item states the problem, the evidence (where it was
established), why it was deferred, and the condition under which it becomes
worth doing. Remove items when done (the changelog records the fix) or when
their condition is judged dead — this file lists only open, still-valid work.

---

## Open items

### 1. Unmeasured coverage is indistinguishable from measured-clean in the report

When the unscoped-files population cannot be measured (`files_unscoped: null` —
no changed-file list, or a changed path that defeats normalization), the human
report renders exactly what a clean run renders: no `## Review coverage`
section. The unmeasured/measured distinction is preserved faithfully in state
and JSON, but nothing consumes it, so the zero≠unknown doctrine stops one
surface short of the reader. Two coupled facets:

- Surface an explicit "coverage population not measured" line in the report
  when the state is unmeasured.
- Strict normalization currently voids the WHOLE population to unmeasured on
  one unnormalizable changed path (filenames containing newline/tab are legal
  on Unix). Fail-loud direction is right; the blast radius is one-file-voids-all
  and becomes visible only once the first facet lands.

**Evidence:** 2026-08-21 pokedex field-audit fix batch, WP3 re-review
(commits `21fd3187`/`1f0619d1` made the state honest; rendering deferred).
**Deferred because:** rendering the state touches legacy/edge report shapes;
the batch scoped to measurement honesty.
**Do when:** next time the step-9 report template is edited, or the first time
an unmeasured run is observed in the field.

### 2. Run metrics cannot group a cohort by build

`plugin_commit` (short HEAD of a dev-mount build) lives in `run-config.json`
only — deliberately not threaded into telemetry events, the manifest, or
`review_run_metrics` (`f06a1bbe`). Consequence: cohort views can group by
model or agent but not by producing build, which is the exact query a
regression hunt across dev builds would want.

**Evidence:** WP4 review of the 2026-08-21 fix batch.
**Deferred because:** threading it means a schema-bearing event field plus two
strict consumer allowlists, for a query nobody has yet run.
**Do when:** the first real cohort-by-build question is asked. Until then,
joining run metrics against each run's `run-config.json` by `session_id` is
the sanctioned workaround.

### 3. Metrics-layer representative test coverage (assessment option F/J)

The analysis/metrics layer carries ~571 degraded-path-named test nodes. The
whole-branch test assessment (2026-08-21, §"option J") classified thinning
this to per-family representative coverage (happy path + one degraded case +
the availability-doctrine pin) as **a scope decision for the human, not a
redundancy finding** — the cut count is unverified and the layer keeps its
thinner net only if it is genuinely feature-frozen.

**Evidence:** `.claude/docs/analysis/2026-08-21-claude-whole-branch-test-assessment.md`
§options table; overengineering-retrospective standing policy 6 ("treat the
metrics layer as feature-complete until a second consumer appears").
**Deferred because:** medium risk, contingent on the freeze holding, and the
verified-redundancy trims (−273 tests) already took the safe cut.
**Do when:** the feature-freeze has held through a release or two AND suite
runtime or maintenance friction in the metrics layer actually bites. Enumerate
and instrument-verify before cutting — estimates overshoot ~2× (twice
confirmed).

### 4. `review-findings.md` still has no named human reader

Every Markdown projection in a run directory should name a human reader or die — that is the principle `review-record.md` was built on, and it retired both agent-facing projections (`reconciliation-context.md`, `critic-context.md`) in the same batch. `review-findings.md` did not go with them: the record now covers the reading it was doing (it renders the same body, plus coverage and run notes), so its remaining role is a cheap mechanical render nobody has been shown to open.

It stays because retiring it is not a rename. The decision critic's probe found six unlisted consumers — pipeline fallbacks, briefing text, and test fixtures that key on the file — and each has to be audited and migrated before the file can go. That is a batch of its own, not a rider on this one.

**Evidence:** run12 audit, Task 12 (decision record B2, Option A) — projection inventory, `M-six-consumers` amendment.
**Deferred because:** the migration is wider than the record work it would ride on, and the record artifact has not yet been observed in the field.
**Do when:** the record has bedded in over a release, AND someone audits the six consumers. If any of them turns out to be a human reading the file, this item is dead — the projection has its reader.

### 5. Declared-vs-autofilled unreviewed attribution reaches no rendered surface

`aggregate_inline_coverage()` splits unreviewed files into `files_declared_unreviewed` (the reviewer's own budget judgment) and `files_autofilled_unreviewed` (the system's save-time backfill), so the system's honesty is never credited to the reviewer. The retired `reconciliation-context.md` rendered the distinction in its gaps section; `review-record.md`'s coverage section does not — it renders gaps, deferred-review claims, and unscoped files, none of which split on attribution. The measurement survives intact in `reconciliation-context.json`; only its rendering is gone.

**Evidence:** run12 audit, Task 12 — `to_markdown()` deletion; the accounting is still pinned by `TestAutofilledUnreviewedAttribution` in `tests/review/test_reconciliation_context.py`.
**Deferred because:** the split was rendered into a document only one agent ever read, so nothing observably consumed it; adding it to the record's coverage section is a rendering decision, not a measurement fix.
**Do when:** a run's coverage section is disputed on the grounds of who declared what, or the next time `_render_review_coverage_section` is edited.

### 6. `critic_verdict == "unavailable"` is a pirategoat-bot contract, not a spelling choice

`pirategoat-bot/src/orchestrator-review.js:399` and `src/resume/orchestrator-review.js:532` both branch on the literal string `"unavailable"` to render the bot's "not cross-validated" message. Task 11 deliberately kept that exact value instead of the brief's proposed `"absent"`, carrying the missing/absent distinction through `degradation_notes` instead of the vocabulary. Nothing enforces this from this repo's side — a future rename of the value (not just the field) would silently break the bot's message with no local test to catch it.

**Evidence:** run12 audit, Task 11 Step 3 deviation ("`critic_verdict` vocabulary preserved on purpose").
**Deferred because:** the value is correct today; this is a coordination trap for later, not a bug now.
**Do when:** the next pirategoat-bot sync, or before any change to `critic_verdict`'s vocabulary in `pipeline-result.json` — grep the bot first.

### 7. F10 — watch the decision critic's rejection bar across runs

Run11's critic removed a finding via an adjustment on a rationale run12's critic judged wrong, and the finding returned in run12. The field-audit report honestly flagged "0 false positives dropped" as weak evidence of a working false-positive gate. No code change is indicated by a single data point; this is a signal to keep watching, not a fix to make.

**Evidence:** `.claude/docs/analysis/2026-08-22-claude-pokedex-field-audit-run12.md` F10 (INFO).
**Deferred because:** one cross-run comparison is not a trend; the critic's adjustment rationale is otherwise sound.
**Do when:** a second run shows the same pattern (a critic reversing a prior run's adjustment on grounds the prior critic would have rejected), or cohort metrics gain a way to compare adjustment rationale across runs.

### 8. Re-entered step 11 cannot tell "report never authored" from "this is the pre-authoring snapshot"

`report_path` in `pipeline-result.json` resolves report → record → findings Markdown, so a normal finalize names the authored report while a crash or interruption between record re-assembly and report authoring leaves a re-entered step 11 naming the record instead. Nothing on the artifact records *which* of those happened — the step-11 handoff gate and pirategoat-bot's own missing-file failure both catch the outcome, but the self-description stays ambiguous to any other reader of `pipeline-result.json`.

**Evidence:** run12 audit, Task 12 report, "Deferred, per the coordinator" section.
**Deferred because:** the ambiguity is cosmetic today — every path that matters already fails loudly on a missing report; nothing yet reads the field for this distinction.
**Do when:** a field run shows a report that was never authored while the run still reads as clean, or the next time `pipeline-result.json` gains a field (a natural place to add `report_authored: bool`).

### 9. `scripts/linear/pipeline.py`'s step-handoff footer has no no-truncation note

Task 1 pinned "run the printed command unfiltered" to the review pipeline's step-handoff footer, because piping it through `head`/`tail`/`grep` was eating load-bearing briefing lines. `scripts/linear/pipeline.py:442`'s own `Run: python3 ... pipeline.py --step ...` footer is the same shape and carries no equivalent note — the same failure class, in the sibling pipeline Task 1 never touched.

**Evidence:** run12 audit, Task 14 review of `scripts/linear/pipeline.py` (`_render_guidance`, the `next_step` branch around line 442), compared against Task 1's fix to `scripts/review/briefings.py`.
**Deferred because:** out of Task 1's scope (review pipeline only); no field evidence yet that the Linear pipeline's briefings get truncated the same way.
**Do when:** a Linear-issue run shows truncated next-step guidance, or the next time `scripts/linear/pipeline.py`'s footer rendering is edited.

### 10. `require_php_source_file` is an undocumented registry gate

Three registry entries (`wp-architecture-reviewer`, `ecosystem-integration-reviewer`, `woo-regression-reviewer`) carry `require_php_source_file: true`, but the field has no row in AGENTS.md's Agent Registry field table (only `require_triage_keyword_match` does), and only `woo-regression-reviewer.md`'s own prompt explains the gate to the agent it applies to — `wp-architecture-reviewer.md` and `ecosystem-integration-reviewer.md` say nothing about it, so an agent reading either prompt cold has no way to learn why it was skipped on a PHP-less diff.

**Evidence:** run12 audit, Task 14 review of `scripts/review/agent_registry.json` (three carriers) against `AGENTS.md`'s registry field table and the three agents' `.md` prompts.
**Deferred because:** the gate behaves correctly; this is a discovery gap for future agents extending the registry, not a dispatch bug.
**Do when:** the next time `agent_registry.json`'s field table in AGENTS.md is edited, or a fourth agent adopts `require_php_source_file` and needs the same explanation copied a third time.
