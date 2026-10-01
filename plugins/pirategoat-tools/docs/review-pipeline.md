# The review pipeline

Read this before changing anything under `scripts/review/` that touches a step's briefing, handoff, readiness gate, or output artifact, and before adding a reviewer-facing rule to the shared protocols. `AGENTS.md` carries the rules; this document carries how the pipeline is built so you can see which rule a change is about to break.

## Shape

```
Command (thin wrapper: pr-review.md, full-code-review.md, code-review.md)
  │
  └─ review/pipeline.py --step N --mode pr|full|incremental
      │
      ├─ pipeline_contract.py ← shared host, step, timeout, path, Git vocabulary
      ├─ briefings.py         ← pure get_step_guidance() + step text
      ├─ orchestration.py     ← side-effecting _orchestrate_step() dispatch
      │
      ├─ Step 3: review/context.py → review-context.json
      ├─ Step 5: review/plan_dispatch.py → dispatch-plan.json (+ dispatch-plan.initial.json);
      │          the orchestrator adjusts it only through review/dispatch_adjust.py
      │
      ├─ Step 6: orchestration stamps dispatch_waves into dispatch-plan.json
      │   (cap, wave 1, queued); then for each wave-1 agent (parallel):
      │   └─ review/agent/bootstrap.py
      │       ├─ extracts protocol sections (skip-list)
      │       ├─ runs review/agent/scope.py (domain-filtered diff)
      │       └─ builds the structured prompt (see Bootstrap prompt order)
      │
      ├─ Step 7: review/agents_status.py --wait (the watchdog) exits
      │   0 ALL_DONE | 2 not done | 3 window expired | 4 SLOT_FREE (launch queued)
      │
      ├─ Step 8: readiness gate, then review/reconciliation_context.py
      │   → reconciliation-context.json (the reconciliator's single input)
      │
      ├─ review-reconciliator agent → findings_save.py validates and writes
      │   review-findings.json (the findings ledger)
      │
      ├─ Step 9: renders review-findings.md from the ledger, aggregates the run's
      │   file review, assembles review-record.md (the machine projection the
      │   critic reads); the orchestrator writes nothing here
      │
      ├─ decision-reviewer agent → critic.py --save commits findings + proposal +
      │   a digest-bound STAND/REVISE/ESCALATE marker
      │
      ├─ Step 10 REVISE (or STAND with wording corrections): the orchestrator
      │   probes each proposal entry and submits
      │   verified/refuted ids to critic_adjustments.py adjudicate, which applies
      │   the proposal to the ledger in one locked write
      │
      └─ Step 11, two passes (see below)
```

`pipeline.py` is the executable facade: conditions, routing, state I/O, output formatting, telemetry and Git identity, and the CLI. All three review commands call it with `--mode`, and the generated Codex adapters add `--host codex`.

## Steps 6 and 7: dispatch waves and the quiet wait

The host caps how many subagents run at once, so step 6 launches reviewers in waves. `pipeline_contract.resolve_reviewer_cap()` resolves the cap in this order: `PIRATEGOAT_MAX_CONCURRENT_REVIEWERS` (a positive integer, either host); on Claude Code, `CLAUDE_CODE_MAX_CONCURRENT_SUBAGENTS`, else 20; on Codex, unbounded (Codex does not expose its agent-thread limit). An invalid value falls through to the next source and the step-6 situation names it. Step 6's orchestration then writes the plan-level `dispatch_waves` record (`dispatch_status.DISPATCH_WAVES_KEY`, built by `build_dispatch_waves()`): the first `cap` dispatched rows in plan order are `wave_1`, the rest `queued`. The rows are untouched, so plan validation, the step-5 baseline and adjustment comparisons are unaffected, and re-running step 6 restamps the record.

The step-6 briefing tells the orchestrator to launch wave 1 in one message and lists the queued agents' dispatch calls under a heading that says to launch them only when step 7 reports a free slot. A Claude Code launch rejected with `Lock file is already being held` is retried once, with every other lock rejection, in one follow-up message; `Concurrent subagent limit reached` is not retried, and neither is a Codex `spawn_agent` rejected for the agent-thread limit. Anything still rejected stays NOT_DISPATCHED for step 7.

The queue is NOT_DISPATCHED rows, read through one definition (`dispatch_status.queue_state()`): a row listed as `queued`, or a `wave_1` row still without a started marker once `grace_seconds` (`DISPATCH_WAVES_GRACE_SECONDS`, 180) have passed since `stamped_at`. A `wave_1` row inside the grace window is pending: just launched, it occupies a slot without being queued. `agents_status.check_status()` reports `queued`, `pending`, `cap`, `slots` (`cap - running - pending`) and `slot_free` (a non-empty queue with a free slot, or any queue when unbounded), and `all_done` is false while anything is running, pending or queued. `--wait` exits `EXIT_SLOT_FREE` (4) once `slot_free` has held for `SLOT_FREE_SETTLE_SECONDS` (30), so a reviewer launched moments ago has time to write its started marker; the no-wait call exits 4 on `slot_free` at once but is read-only: it releases nothing, and its NOTE tells the orchestrator to leave the launch to the `--wait` watchdog. The printed envelope adds `QUEUED:` and `SLOTS:` (a count, or `all`). A legacy plan without the record has no queue and no exit 4.

On a `--wait` exit 4 the watchdog releases the first `SLOTS:` queued names (all of them when unbounded): under the output-directory lock it re-reads the plan, keeps only names the reloaded record still lists in `wave_1` or `queued` (a step-6 restamp may have dropped one; with none left it exits 2), stamps each into the record's `released` map (latest release time, release count, and counted releases, see below), validates the new record before the atomic write (an invalid one is not written, with a stderr WARNING), and records `last_release` (`at`, and `terminal_count`, the FINISHED, INVALID_OUTPUT, TIMED_OUT and BOOTSTRAP_ERROR total). `QUEUED:` on that exit prints exactly the released names, so the orchestrator launches exactly those. A released name without a started marker is pending for `grace_seconds` from its release, so it holds its slot (an accepted launch that is slow to start is not replaced by another reviewer); past that it is queued again. After a release, `slot_free` also needs progress (`agents_status._progress_since_release`): the terminal count grew since `last_release`, or nothing is running or pending. A wave-1 rejection that crosses its grace after a release waits for one of these too (at most until the agent timeout turns every running reviewer TIMED_OUT). Without the gate, a launch the host keeps rejecting (Codex's agent-thread limit) re-fired exit 4 every settle window. Step 6 restamps a fresh record without release fields.

A launched reviewer that never starts is abandoned (`queue_state()`), so step 7 always reaches exit 0 and step 8 closes intake without the escalation window, as over a legacy NOT_DISPATCHED row: once it has `MAX_RELEASES` (2) counted releases and its latest release's grace window has passed. Nothing is abandoned inside a grace window. A release counts (`dispatch_status.record_release()`) only when the host held nothing of this review's: nothing RUNNING or pending when it was decided, and the terminal count unchanged since the previous release (since step 6 for the first), so no reviewer started and ended in between. A refusal by a host full of this review's RUNNING or pending reviewers (our cap, Codex's thread limit, a one-thread host that ran the co-released reviewer) therefore never abandons a healthy reviewer; a reviewer refused beside co-released names the host accepted can gain one count, never two, since those names run or end before its next release. Termination: running reviewers turn TIMED_OUT at the agent timeout and the terminal count stops growing, after which every release counts. An abandoned row holds no slot, does not block `all_done`, prints `ABANDONED (never started after N launch attempts)` (N counts the step-6 launch of a `wave_1` row) and is listed in the "never started" NOTE; the step-8 WAITING briefing does not offer it for dispatch, and the manifest counts it in `never_started`. A late start still reads as RUNNING.

Known limitation: the counting rule sees only this review's RUNNING and pending reviewers, not the host's threads. A host thread still held by a TIMED_OUT reviewer (past the agent timeout but not exited), by an accepted launch still bootstrapping past its grace window, or by a hung accepted launch is invisible to it, so under a very small host limit (one or two threads) a refusal caused by that contention can count toward abandonment and a healthy reviewer can be abandoned. The grace window also runs from the release stamp, not from the moment the orchestrator actually launched the reviewer. An abandoned reviewer that starts before step 8 closes intake still counts: it reads as RUNNING and is waited for like any other. This is accepted rather than hardened: abandonment exists so step 7 always ends, and a false abandonment costs one reviewer, reported as ABANDONED.

Step 7 is a quiet wait. On Claude Code the wake-up table is: watchdog exit 4, dispatch the agents on `QUEUED:` (at most `SLOTS:`, skipping any whose Agent call was already accepted), launch a fresh watchdog and end the turn; exit 0, proceed to step 8; exit 2 or 3, launch a fresh watchdog and end the turn; a notification beginning `STATUS: FINISHED`, produce no text and no tool call, because results are read from disk at step 8 and a summary re-reads the whole context for nothing; any other notification, run `agents_status.py` once. Foreground sleeps, keepalive turns, status calls without a wake-up and per-reviewer summaries are never allowed. On Codex the `--wait --max-seconds 60` loop gains exit 4 (spawn the queued agents that fit, then re-run the call), and no iteration carries commentary. The step-8 WAITING briefing carries the same rules for both hosts.

The reviewer return signal is two parts and nothing else: `STATUS: FINISHED`, then `OUTPUT_FILES:` with the final review path. Bootstrap's output instructions teach it, and its empty-scope stub (`no_domain_files_signal()`) prints it, because counts, verdict and skip reason are in the review file the orchestrator reads at step 8. `analysis/session_metrics.py` reads a reviewer's verdict and counts from the builder's `DRAFT SAVED:` / `DRAFT TOTALS:` receipts instead, with the old `VERDICT:` / `COUNTS:` lines as the fallback for older transcripts.

## Step 9: reconciliation verification

Step 9 measures the reconciliator's repository reads from its transcript and records `reconciliation_verification` (verified, unverified, or unmeasured) in state; the record, the critic prompt, `pipeline-result.json` and the manifest carry it. The read detector is `review_transcript._bash_read_paths`. It recognises the Read tool and literal reader commands (`cat`, `head`, `tail`, `wc`, `sed`, `grep` and its variants, `nl`, `git show`, `git diff --`), including inside `cd`, `&&`, `;` and newline compounds, and nothing whose success the call's exit status does not certify (a `||` chain, a backgrounded list, a reader piped into another stage, anything after an `exit`). The exact grammar is pinned by its tests. A measured zero is worded "no read observed", never "read nothing".

## Step 8: readiness gate

Before reconciliation, step 8 checks dispatched agents through `review/agents_status.py`. A canonical schema-2 final review is `FINISHED`; an invalid final filename is terminal process evidence (`INVALID_OUTPUT`) but contributes no verdict, finding counts, or reviewed files. A reviewer whose bootstrap exited with STATUS: ERROR leaves a `bootstrap-error` failure record instead of `started` and reads as `BOOTSTRAP_ERROR`, terminal in the same way, and the step-7 briefing tells the orchestrator not to dispatch it again; a `started` marker beside the record supersedes it, whichever was written first. `ALL_DONE` means only that nothing remains to wait for, so invalid output does not hang the gate. A non-empty queue or a pending (launched, not yet started) reviewer (see Steps 6 and 7) also keeps the gate closed, whatever `all_done` says, and the waiting state records `queued` and `pending` beside `running` and `not_dispatched`; the WAITING briefing names pending reviewers as starting up, with no action, and never offers them for dispatch. While agents are still running, pending or queued, step 8 returns a WAITING briefing and tracks `first_waiting_at` in pipeline state; once the wait exceeds `agent_timeout_seconds + 60s`, it clears the waiting state and proceeds with the available results after instructing the orchestrator to TaskStop stuck agents. Once the gate proceeds, orchestration materializes `reviewers/<reviewer>/review.md` from every settled canonical JSON before building the reconciliation context, and records the complete, partial, or failed outcome in pipeline state and the run manifest.

## Step 11: two-pass publication

Step 11 is re-entrant by design. The first pass records `publication_pending: true`, fingerprints the exact record and ledger bytes plus the terminal presentation facts, blocks progress, leaves step 11 out of `completed_steps`, and writes no `pipeline-result.json`; a missing `review-report.md` is the expected handoff state, not a degradation. The blocking briefing has the orchestrator author `review-report.md` once from the settled record and re-run step 11. The second pass repeats settlement idempotently, publishes atomically only when settlement still matches the prepared fingerprint and the report is not byte-identical to one already rejected as stale, writes `pipeline-result.json` with that exact `report_path`, closes the handoff, and only then completes the step (bot mode ends; interactive mode routes to step 12). A changed source, an unchanged stale report, or a pre-existing unbound report regenerates the handoff instead of exposing a terminal marker.

The first pass also reads `critic_adjustments.adjudication_state()`: a REVISE (or corrections-carrying STAND) proposal never adjudicated is recorded as a degradation, never applied on the orchestrator's behalf. Step-11-owned degradation records carry stable producer codes across the handoff in first-seen order and project back to the public string-list contract; fingerprints use those ordered identities rather than prose. The one mutating measurement, the probe-residue sweep, accumulates removed paths in `worktree-hygiene.json`; a private hash of the sorted unique path set makes any newly swept path invalidate the prepared report without exposing path provenance.

## Briefing design

The step briefings in `briefings.py` follow `docs/patterns/curated-context-pipeline.md` (repo root). The inline rules:

- **Identity anchoring.** `_PIPELINE_MISSION` is the orchestrator's mission statement; step 1 prepends it to `situation`. Review the pattern doc's "Pipeline Identity Anchoring" principle before editing it.
- **Phase transitions.** `_PHASE_TRANSITIONS` injects a contextual reminder at each phase-entry step: EXECUTION at step 5 (precision in dispatch), SYNTHESIS at step 8 (faithful synthesis), VALIDATION at step 10 (stress-test before a human sees it), OUTPUT at step 11 (complete delivery). Each is a variation on the mission tied to what is about to happen, not a repetition.
- **Artifact discipline.** File-producing steps follow write, verify, proceed. `handoff` is the sole gate mechanism: an artifact the next step requires goes in `handoff`, never buried in `actions`. Steps 3/4, 8, 10 and 11 gate on their output files; step 9 has no gate on purpose, since it asks the orchestrator for no artifact. JSON examples use schema form (`{"verdict": "<APPROVE | REQUEST_CHANGES | COMMENT>"}`), never copyable placeholder values.
- **Voice.** A senior reviewer briefing the orchestrator: authority on process, trust on execution. The voice lives inside the SITUATION / ACTIONS / HANDOFF sections; the headers stay rigid as machine-readable landmarks.

## Shared protocols and the skip-list

`agents/shared/reviewer-protocol.md` holds the behavioral rules for every reviewer. Bootstrap includes it by a skip-list (the sections are named in `AGENTS.md` § Rules): sections it replaces with concrete values are excluded, everything else is included automatically, so a new protocol section reaches reviewers without a code change. One section is neither: `## Host Context Usage` sits on the skip list and `main()` hands it to `build_output()` through `extract_protocol_section()`, which renders it right after the Host Context section when a host manifest is present, so its rules travel with the hosts they govern and a run without hosts does not carry them.

Text added to a skipped section reaches zero agents while passing review and shipping in a changelog, which is why the rule in `AGENTS.md` forbids policy there. Policy about what the agent must do with a result belongs in `bootstrap.build_output()`, which knows the concrete budget and paths; `TestReviewClaimableContractIsDelivered` in `tests/review/agent/test_bootstrap_integration.py` guards the review-claimable contract and is the template for any comparable one. The setup paragraph of an agent definition branches on STATUS before it tells the reviewer to read the briefing, because a reviewer follows its instructions in order and meets a branch placed after the read only once the read is done; `TestEveryReviewerMandatesBootstrap` pins that order and `TestBriefingFileDelivery` the stub's return signal.

One section also reaches the two participants that run code outside a reviewer's prompt: `## Empirical Probes` is embedded verbatim, through `protocol_sections.empirical_probe_rules()`, in the step-9 briefing (the orchestrator's spot-checks) and the step-10 dispatch prompt (the decision critic). The probe rules therefore exist once; the section carries no code fence, because the critic's copy sits inside a fenced prompt.

`tests-reviewer-protocol.md` is appended for agents with `"tests-reviewer"` in their `protocols` list.

## Bootstrap prompt order

Preserve this order when changing `agent/bootstrap.py` or the protocol files:

1. **REVIEW RULES** (top): behavioral steering by primacy.
2. **Context sections**: PR INTENT (title, author, linked issues, and either a pointer to the extracted author description or the whole HTML-comment-stripped body), REVIEW FOCUS (from `change-purpose.md`; only a structured purpose adds the two-tier instruction and the `verifies=` citation call; when the briefing would not fit one Read, `fit_briefing_to_one_read` evicts the purpose body, leaving the header, the two-tier instruction and a `REVIEW FOCUS CONTINUES IN FILE` block naming the file), REVIEWER-REQUESTED FOCUS (from `run-config.json`, only when steering keywords were provided), HOST CONTEXT (discovery availability and degradation, plus resolved upstream runtime-host and library-dep entries with version, commit, refresh date and declared minimum; a resolved entry is authoritative for its upstream surface), and REVIEW BUDGET (scope-proportionate tool-call calibration).
3. **REVIEW CONTENT** (middle): the scope's file list and review-claimable queue, ending with the `SCOPED DIFF IN FILE` block that names the reviewer's `scoped-diff.patch` and the exact Read calls that fetch all of it. The diff itself never rides in the briefing.
4. **OUTPUT INSTRUCTIONS** (bottom): format and paths, by recency.

## Pipeline-wide containment

`scripts/containment.py` is the single enforcement point for repo-boundary decisions (see its docstring for the resolved versus lexical callers); the rule in `AGENTS.md` and `tests/test_containment_contract.py` keep it that way.

## Trusted-branch dependency refresh (opt-in)

The pipeline never installs dependencies itself, because package managers execute configuration as code. When the requester opts in, per run with `--refresh-deps` or as a standing machine-local declaration in `~/.config/pirategoat/config.json` (`{"review": {"refresh_dependencies": true}}`, resolved by `user_settings.py`), the main orchestrator inspects the trusted worktree and refreshes dependencies adaptively. An explicit flag wins; an omitted flag falls back to the machine-local default; the effective value lands in `run-config.json` as `refresh_dependencies`. The standing declaration covers every interactive run the requester starts, including PR reviews of third-party branches: that is the requester's trust decision, made in a file the reviewed repo can never touch.

Responsibilities:

- **Pipeline config plus a tracked-Git precheck decide whether refresh may be offered.** Step 3 observes tracked state with `git status --porcelain --untracked-files=no --ignore-submodules=untracked`; a dirty or unknown observation fails closed and offers no commands or save handoff. This gate is separate from the whole-run hygiene baseline and never takes custody of the requester's tracked changes.
- **The orchestrator decides whether and what to run.** After a clean precheck it inspects the repository and the change, chooses lockfile-preserving commands without a manager or flag allowlist, refreshes host context after any installation (`context.py --refresh-host-context`), and writes a schema-1 request under the run's `tmp/`; `not_needed` with an empty command list is the required outcome when inspection finds no work.
- **`dependency_refresh.py save` validates and publishes.** It accepts exactly the request schema, records bounded final tracked-state evidence, and publishes the canonical `dependency-refresh.json` atomically. `completed`, `partial`, `failed`, and a dirty or unknown final state are all valid evidence; only invalid request input blocks publication. Reported command strings are evidence, never execution attestation.

At step 5 the pipeline reads the report through `load_dependency_refresh_report()`; a missing or malformed report after a clean precheck, or a dirty or unknown final state, becomes explicit degraded evidence before dispatch. The manifest preserves `requested`, `reported`, optional unsafe-precheck evidence, and the validated report fields.

**Hard-off for bots.** Step 1 forces `refresh_dependencies` off, with a stderr warning, for `interactive: false` runs whether it arrived via CLI or a pre-seeded `run-config.json`. A bot reviewing third-party PRs must never execute reviewed-branch code.

## Run directory layout

Interactive reviews keep durable state under `~/.pirategoat-tools/reviews/`; an absolute `$PIRATEGOAT_TOOLS_HOME` overrides `~/.pirategoat-tools`, and a relative override is ignored. Every session with the plugin loaded also writes its own `~/.pirategoat-tools/sessions/<session id>/plugin-root`, independent of any review: the PreToolUse hook records it before each Bash call, and bootstrap and the agent definitions read it back so a dev checkout and the installed release never mix.

```text
~/.pirategoat-tools/reviews/<kind>/<safe-repo>/<safe-target>/   # target directory: cross-run state
├── .branch-review-baseline.json                               # incremental reviews only
└── runs/
    └── <run-id>/                                              # one fresh directory per run; newest 10 kept
        ├── run-config.json                                    # seven root boundary files
        ├── review-context.json
        ├── pipeline-result.json
        ├── review-report.md
        ├── review-record.md
        ├── review-findings.json
        ├── review-findings.md
        ├── pipeline/                                          # orchestration state and measurements
        │   ├── pipeline-state.json
        │   ├── review-intake.json
        │   ├── dispatch-plan.json                             # carries dispatch_waves after step 6
        │   ├── dispatch-plan.initial.json
        │   ├── change-purpose.md
        │   ├── dependency-refresh.json
        │   ├── synthesis-agents.json
        │   ├── usage-snapshot.json
        │   ├── worktree-hygiene.json
        │   ├── .telemetry-log-path
        │   └── .worktree-baseline.json
        ├── reviewers/<reviewer>/                              # short reviewer identity
        │   ├── assignment.json
        │   ├── briefing.md                                    # what bootstrap delivered; stdout is a stub naming it
        │   ├── review.draft.json
        │   ├── review.json
        │   ├── review.md
        │   ├── scope-summary.json                             # plus scope-summary-<domain>.json
        │   ├── scoped-diff.patch                              # a line-number note and git's own diff output for the reviewer's scope, written whenever its scope fetched a diff (not tests-mutation-reviewer, which has no domain scope, nor a ref-mode adapter with no valid domain); the briefing names it with the exact Read calls that fetch all of it
        │   └── started
        ├── synthesis/
        │   ├── reconciliation-context.json                    # schema 4; carries orchestrator_notes
        │   ├── decision-critic-adjustments.json
        │   ├── decision-critic-findings.md
        │   ├── decision-critic-verdict.json
        │   └── <agent>.synthesis-started
        └── tmp/                                               # sanctioned scratch: reviewer probes, plus the reconciliator's staged ledger, the critic's drafts, and the adjudication and dependency-refresh requests
```

The target directory groups runs for one PR or branch and owns only cross-run state. A run directory is immutable in identity and never reused: its UTC run id sorts lexically, `latest` resolves the newest valid name without a symlink, and the allocator prunes older runs after keeping ten. The seven boundary files stay at the root because interactive commands and pirategoat-bot exchange them there. `run_paths.py` resolves shared artifacts and `reviewer_lifecycle.py` per-reviewer artifacts; callers never reconstruct these paths or spell filenames themselves.

## Output contract

Each reviewer publishes canonical state in `OUTPUT_DIR/reviewers/<reviewer>/`: `review.draft.json`, the mutable artifact replaced by `builder.save_draft()` until the exact printed receipt command finalizes it, and `review.json`, the immutable final artifact published by `finalize_review()` (types in `schemas/review-output.ts`). The reviewer-facing builder contract is stated once in `agents/shared/reviewer-protocol.md` (§Canonical Draft Lifecycle, §ReviewOutputBuilder API) and the implementation contract once in `scripts/review/agent/output.py`.

`review.md` beside the final JSON is derived, never written by reviewers: the step 8 gate materializes it through `review_markdown.materialize_markdown()`, and it stays renderable on demand with `python3 scripts/review/review_markdown.py render|materialize`, the recovery command step 11 prints. Every live reader of final-review contents calls `load_review_document(path, reviewer)`, which delegates to the schema-2 `validate_review_document()` authority; existence-only scans may observe process evidence but cannot project findings, verdicts, reviewed files, or completion.

`review-findings.md` follows the same rule one level up: the reconciliator publishes `review-findings.json` and nothing else, and the pipeline renders the Markdown through the same materializer at step 9 and again at step 11, after the critic adjustments apply. Every section a hand-written report once carried has a structured home: `assessment`, `checks`, `meta.reconciliation`, `recommendations`, `observations`, and `host_context_banner`. Both `review-findings.md` and `review-record.md` show source and severity annotations, dropped findings and checks with reasons and evidence, and answered orchestrator notes. A render failure is a degradation note, never an exception and never a file that disagrees with its JSON.

Findings carry stable `fN` ids and checks stable `cN` ids. A finding names its sources; a source is a reviewer finding or, for a concern only the orchestrator raised and the reconciliator confirmed, that note, cited as `{"reviewer": "orchestrator", "id": "nN"}`. A check has exactly `id`, `question`, `method`, `result`, and `source_reviewers`, plus an optional `verifies` list of the change purpose's Verify item ids it settles; it records verification evidence and never affects verdict counts. Raw reviewers own findings, checks, observations, positives, recommendations, confidence, and reviewed-file claims. Only the reconciliator authors the initial nullable `assessment`; an applied critic adjustment that moves the ledger (`critic_adjustments.entry_moves_ledger`) invalidates it, and the orchestrator's `adjudicate` request may carry a revised assessment, which replaces it. A wording-only batch leaves it standing, and a revised assessment supplied there invalidates it the same way. The invalidated text stays in `invalidated_assessments`; revised text over a null assessment records `text: null` there, since that record is what attributes the standing text to the orchestrator.

The verdict is computed by `verdict_for_counts()` in `verdict_rules.py` (the thresholds are in its docstring), shared with `critic_adjustments.py`, which recomputes the ledger verdict after every applying critic batch. The outer-pipeline verdict (`APPROVE`/`COMMENT`/`REQUEST_CHANGES` in `pipeline-result.json`) is derived from the reconciled ledger at step 11 through `verdict_rules.publish_verdict()`, and a critic `ESCALATE` overrides it to `COMMENT` in `orchestration.py`. Derivation settles on the prepare pass and publishes only when the report handoff completes.

The critic lifecycle has exactly three owners (see the module docstring of `critic_adjustments.py`), and the findings ledger has exactly one write path (see `findings_save.py`).
