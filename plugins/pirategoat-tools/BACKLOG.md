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

### 5. `critic_verdict == "unavailable"` is a pirategoat-bot contract, not a spelling choice

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

### 11. Weak triage keywords dispatch reviewers that find nothing

On run 4 (PR #12095, 9 JS/TS files) four `keyword` dispatches rested on words that were in the author's text but carried no signal: concurrency (`await` from `awaiting_response`, `transaction*` from a route path, `cach*` from a type name), data-flow-privacy (`merchant*` in prose, `charge*` from `charge_id`, `address` from a commit subject), docs-drift (`hook`/`hooks` from `hooks.ts`) and devils-advocate (`cach*`, `fallback*`, `table*`). The orchestrator removed the first two with correct reasons; the other two ran and declared themselves not applicable at 3.3 % of subagent tokens. Plan E removed template and trailer matches; these are real words that are weak in JavaScript codebases.

**Evidence:** `.claude/docs/analysis/2026-09-06-claude-pr-review-run-8fffee52-e2e-audit.md` §3; `dispatch-plan.initial.json` of run `20260906T192325-pr-12095-8fffee52`.
**Deferred because:** four keywords from one run are not a rule; the triage regression fixtures replay three PRs and a keyword change must hold on all of them.
**Do when:** a second run shows the same words dispatching a reviewer that finds nothing, or when `tests/fixtures/triage-runs/` gains a JS/TS-only fixture to replay against.

### 12. history-insights is the costliest reviewer that never produces a finding

Across the four audited runs history-insights took 15.6 / 4.5 / 12.9 / 11.7 % of subagent tokens and produced no finding on any of them (three checks on run 4, a nine-file PR). It is the evidence for improvement-list item 13 (briefing size / scenario budget), which stays deferred.

**Evidence:** audit §5 cohort table; `usage-snapshot.json` of the four runs.
**Deferred because:** its checks are consumed by the reconciliator even when it raises nothing, so cost without findings is not proof of no value; the plan F change (scenario count from the budget, one `--all` walk) has had one run to show its effect.
**Do when:** the next two runs keep it above 10 % with no finding, or a reconciliator ledger shows none of its checks cited.

### 13. Upstream reads outside a resolved host have no citation form

Run 4's reconciliator and decision critic both verified findings against `wpcom`'s `class-dispute-repository.php`, a repository the maintainer's project map names but no resolver of the reviewed clone mounts, so it is not a resolved host. Their `source_cited` values carried no `host@` prefix and `evidence.host_citations` read `{}` although two upstream verifications settled a medium finding.

**Evidence:** audit §4 and §6; the two synthesis transcripts of session `2b765382`.
**Deferred because:** the citation grammar is `<host>@<version|commit|unknown>:<path>:<line>` for resolved hosts only; a form for an unresolved local checkout needs a host source (a "local map" resolver or an explicit `hosts.runtime` entry in the clone's `.pirategoat/config.json`), which is a design decision, not a fix.
**Do when:** a second run cites an unresolved upstream path, or when WooPayments' `.pirategoat/config.json` gains a `hosts.runtime` entry for `wpcom`.

### 14. The reconciliator reads source through Bash although its definition says the Read tool

`agents/review-reconciliator.md` says "use the Read tool to read the source file directly"; under the harness's bypass-permissions guidance ("read files with cat, head, or sed -n") run 4's reconciliator read every source through `sed -n`/`grep -n`, and so did 14 of 16 subagents. The read detector now recognises those idioms, so the measurement is right; the definition and the harness guidance still disagree.

**Evidence:** audit §5 and §7; the subagent transcripts of session `2b765382`.
**Deferred because:** the harness guidance is session-level and outside the plugin; the definition's wording only matters if the Read tool's structured result (`toolUseResult.filePath`) is needed for a measurement Bash cannot give.
**Do when:** a measurement needs the Read tool's structured result, or the harness guidance changes.

### 15. The save gate's verbatim-method rule is satisfied by the builder it gates

The accounting gate requires a merged check to contain each source check's method text by substring, and since the run-4 fixes the ledger builder reads `reconciliation-context.json` and pastes that text in (`record_check` appends `[<stem>:<id>] <method>` lines and unions `verifies`). The gate now verifies output the builder wrote, the check's `method` field is a container for N blobs the renderer prints verbatim, and the builder — the one deliberate `ReviewOutputBuilder` subclass — has become a reader of pipeline artifacts.

**Evidence:** the A-branch simplify pass (`.claude/docs/analysis/2026-09-07-claude-a-branch-simplify-pass.md`, D1); `findings_save._accounting_problems`, `findings_ledger._source_check`, `agents/review-reconciliator.md` rule 5.
**Deferred because:** it changes the ledger contract, the gate, the renderer and the reconciliator prose together, right before a release.
**Do when:** the next reconciliator contract change is planned. Recommendation: `sources` already identifies each merged check by `(stem, id)`; stamp `sources[].method` and `sources[].verifies` from the context in `stamp_pipeline_facts` the way `sources[].severity` is stamped today, render them from there, and drop the substring rule, the union rule, `_source_check` and the `[<stem>:<id>]` convention. Derive `source_reviewers` from `sources` in the same change.

### 16. Step 9 runs the whole usage capture to read one integer

`_reconciliation_verification` shells out to `usage_snapshot.py --stdout`, which correlates every subagent transcript, to read the reconciliator row's `repository_reads`; step 11 runs the same capture again for the durable snapshot. Measured at 0.15–0.3 s per run.

**Evidence:** simplify pass D2; efficiency review measurement.
**Deferred because:** the per-agent body has to be carved out of `enrich_run_transcript` (`measure_agent(run_dir, agent_name)`), which is its own change to the transcript module.
**Do when:** a second step-9-time question about one agent's transcript appears, or the capture grows past a second.

### 17. Range-truth outcomes are classified in the briefing renderer

`context.py` records `base_fetch`/`scope_check` atoms with bare status literals; the outcome the orchestrator acts on (a five-way base-fetch reading, inflated/short scope) is derived in `briefings.py`, mirrored by `telemetry.py`'s private projections and `sanitize.py`'s field-for-field copy, and pinned as ~40 sentences in `test_pipeline.py`.

**Evidence:** simplify pass D3.
**Deferred because:** the outcome enum, public `BASE_FETCH_*`/`SCOPE_CHECK_*` constants and one shared projection for the sanitizer are a plan across four modules; `_resolve_range` and the shared SHA grammar landed in the pass.
**Do when:** the next range fact is added (it would be the fifth spelling), or when the step-3 pins next break on a wording change.

### 18. Three captured review-run fixtures each need a translator on every schema change

`review_run_fixture._normalize_legacy_proposal` already rewrites the pre-1.119.0 critic vocabulary for all three captured runs, and the reviewer documents under `tests/fixtures/review-runs/*/reviewers/**` (31 files, ~3,000 lines) are asserted on by nothing.

**Evidence:** simplify pass D4; altitude and simplification reviews.
**Deferred because:** keeping one historical run plus a synthetic current-schema fixture built from the pipeline's own producers is a re-capture and a helper redesign.
**Do when:** the next manifest or ledger schema bump would need a second translation clause.

### 19. Planner reasons are parsed as prose by the regression suite

`plan_dispatch` carries the decision three ways (`reason`, `signal`, `agent_signals` strings), compares `reason` against a literal beside the `SIGNAL_ALWAYS` enum, and `test_triage_run_regressions.py` re-parses the reason prose (`_KEYWORDS_RE`, `_match_pairs`) to check matched keywords.

**Evidence:** simplify pass D5. (`planner_signals` and both reasons are stripped at the share boundary, so the disclosure holds.)
**Deferred because:** structured `matches: {source: [keyword…]}` on the plan is a planner output change plus a triage-fixture re-capture.
**Do when:** a reason is next reworded and the regression suite breaks on it.

### 20. `HostEntry` keeps commit and date in free-form notes

`review_document.HOST_CITATION_RE` and `cited_hosts` are now the one parser of `<host>@<version|commit|unknown>:<path>:<line>` (simplify pass 2), used by the evidence manifest for findings and checks alike; the reviewer protocol still states the grammar in prose, and nothing validates a citation at reviewer write time. `HostEntry` types `version` but carries `commit`/`commit_date`/`identity_scope` in `notes`, with two names for the refresh time.

**Evidence:** simplify pass D6.
**Deferred because:** a `parse_source_citation` in `review_document.py` adds validation at reviewer write time; typed entry fields need the three captured `review-context.json` fixtures re-captured.
**Do when:** a citation is next mis-parsed, or the fixtures are re-captured for item 18.

### 21. The review-record renderer lives in `orchestration.py`; the read detector has two operand parsers

`assemble_review_record`, `_render_run_notes`, `_render_record_verdict_line`, `_render_verify_items` and their helpers (~220 lines) sit beside the step seams; `review_markdown.py` is the renderer the record's body already shares. `review_transcript.py`'s read detector keeps `_file_operands` and `_pattern_then_files` as two operand walks with per-tool option tables.

**Evidence:** simplify pass D7.
**Deferred because:** each is a module move or a table-driven rewrite with its own test-map and import-graph consequences.
**Do when:** either module next needs a non-trivial change.

### 22. The consent disclosure is a hand-maintained prose inventory of the manifest

`telemetry_share.CONSENT_DISCLOSURE` names every shared section in one sentence, is edited whenever a section is added, and no test checks it against the manifest.

**Evidence:** simplify pass D8.
**Deferred because:** a per-section description table rendered into the disclosure changes the step-12 prompt the user reads; it wants the maintainer's eye.
**Do when:** the next manifest section is added.

### 23. The reconciliator assembles the ledger through a Python API it has to import

`agents/review-reconciliator.md` has the agent write a script that imports `FindingsLedgerBuilder`, calls it, serializes `to_dict()` and hands the file to `findings_save.py`. The builder's value — id allocation, verbatim source-method autofill, the closed vocabularies — is real, but the contract is a class the model must call correctly from memory of a Markdown template, and every field run so far spent between 10 s and 4 min reading `agent/output.py`, `findings_ledger.py` and `verdict_rules.py` to check what the class accepts. The 1.119.0 template now states those facts, which removes the reads without removing the reason for them.

**Evidence:** the 2026-09-07 two-run audit (`.claude/docs/analysis/2026-09-07-claude-two-field-runs-audit.md`, F4) and the six-run reconciliator source-read census in that session.
**Deferred because:** the alternative is a document contract — the reconciliator writes a JSON ledger without ids or source methods and `findings_save.py` allocates ids, autofills methods and validates — which moves builder logic into the save script and re-pins every snippet test; it is a design change, not a release fix.
**Do when:** the reconciliator next needs a new builder call, or a field run shows it reading plugin source again.

### 24. The read detector's certification is interleaved with its walk

`_bash_read_paths` decides in one loop, through five flags, which simple commands a successful call certifies as run and as succeeded, and reads paths and moves the working directory in the same pass. The model is coherent (last foreground list, no `||`, not a non-final pipeline stage, nothing after a possible exit; a backgrounded list is opaque), but the next certification rule — `set -o pipefail`, `set -e`, `!` — lands inside that loop.

**Evidence:** simplify pass 2, altitude 5.
**Deferred because:** a `_certified_commands(lists) -> [(simple, ran, succeeded)]` extraction moves about forty lines with no behaviour change; the eighty-row edge-case table already pins the outcomes.
**Do when:** the next certification rule is added.

### 25. An empty approve is warned about, not refused

`ReviewOutputBuilder` knows at finalize that a verdict is approve with no finding, check or observation, and prints a NOTE; protocol rule 8 tells the reviewer to record a check instead. Refusing at finalize would make the reviewer a missing agent the reconciliator measures, rather than a clean approve the record cannot tell from a real one.

**Evidence:** simplify pass 2, altitude 6.
**Deferred because:** the trade — an unfinished reviewer over an empty approve — is the maintainer's to make.
**Do when:** a field run shows an empty approve counted as coverage.

### 26. The version-shaped gate sits in the one producer that records refs

`wp_env._parse_remote_ref` keeps a `#ref` as a version only when it is version-shaped; `explicit`, `plugin_headers` and the cache fulfilment path copy `version` and `declared_minimum` into the projection unchecked. Today those sources carry declared versions, not branch names.

**Evidence:** simplify pass 2, altitude 3.
**Deferred because:** a predicate applied in `manifest_sections.project_host_entry` would add a static `hosts` import to the review package for a producer that does not exist.
**Do when:** a second resolver records a ref, or `.pirategoat/config.json` hosts gain a free-form version field.

### 27. A repo reviewer's path globs have two readers of one declaration

`plan_dispatch` stamps `include_paths` on the adapter row from `applies_to.paths`; bootstrap ref-mode reads the same globs from the repo config through `find_repo_reviewer_declaration` and passes them as `--include-path`. Both derive from one declaration at different times.

**Evidence:** simplify pass 2, reuse 6.
**Deferred because:** bootstrap ref-mode reads the whole declaration from the config; reading one field from the plan row instead splits the declaration's readers rather than joining them.
**Do when:** bootstrap ref-mode next changes.

### 28. A mounted sibling and an upstream host carry the same kind

The wp-env resolver knows whether an entry came from `core` or from a plugin mapping, but both are `runtime-host`; the reviewer protocol now says a mount proves co-installation, not direction, and leaves the direction to the reviewer with the diff.

**Evidence:** the PR #68212 field-run audit (`.claude/docs/analysis/2026-09-08-claude-pr-68212-field-run-audit.md`, F1); simplify pass 2, altitude 7.
**Deferred because:** a `relation` note rendered only for mounted entries is forty lines across two resolvers, bootstrap and tests, for a line no reviewer has yet acted on.
**Do when:** a run shows a reviewer reading a mounted sibling as upstream.
