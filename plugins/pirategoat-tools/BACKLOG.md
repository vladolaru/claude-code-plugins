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
