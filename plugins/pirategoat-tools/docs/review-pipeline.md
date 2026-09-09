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
      ├─ Step 6: for each agent (parallel):
      │   └─ review/agent/bootstrap.py
      │       ├─ extracts protocol sections (skip-list)
      │       ├─ runs review/agent/scope.py (domain-filtered diff)
      │       └─ builds the structured prompt (see Bootstrap prompt order)
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
      ├─ Step 10 REVISE: the orchestrator probes each proposal entry and submits
      │   verified/refuted ids to critic_adjustments.py adjudicate, which applies
      │   the proposal to the ledger in one locked write
      │
      └─ Step 11, two passes (see below)
```

`pipeline.py` is the executable facade: conditions, routing, state I/O, output formatting, telemetry and Git identity, and the CLI. All three review commands call it with `--mode`, and the generated Codex adapters add `--host codex`.

## Step 9: reconciliation verification

Step 9 measures the reconciliator's repository reads from its transcript and records `reconciliation_verification` (verified, unverified, or unmeasured) in state; the record, the critic prompt, `pipeline-result.json` and the manifest carry it. The read detector is `review_transcript._bash_read_paths`. It recognises the Read tool and literal reader commands (`cat`, `head`, `tail`, `wc`, `sed`, `grep` and its variants, `nl`, `git show`, `git diff --`), including inside `cd`, `&&`, `;` and newline compounds, and nothing whose success the call's exit status does not certify (a `||` chain, a backgrounded list, a reader piped into another stage, anything after an `exit`). The exact grammar is pinned by its tests. A measured zero is worded "no read observed", never "read nothing".

## Step 8: readiness gate

Before reconciliation, step 8 checks dispatched agents through `review/agents_status.py`. A canonical schema-2 final review is `FINISHED`; an invalid final filename is terminal process evidence (`INVALID_OUTPUT`) but contributes no verdict, finding counts, or reviewed files. `ALL_DONE` means only that nothing remains to wait for, so invalid output does not hang the gate. While agents are still running, step 8 returns a WAITING briefing and tracks `first_waiting_at` in pipeline state; once the wait exceeds `agent_timeout_seconds + 60s`, it clears the waiting state and proceeds with the available results after instructing the orchestrator to TaskStop stuck agents. Once the gate proceeds, orchestration materializes `reviewers/<reviewer>/review.md` from every settled canonical JSON before building the reconciliation context, and records the complete, partial, or failed outcome in pipeline state and the run manifest.

## Step 11: two-pass publication

Step 11 is re-entrant by design. The first pass records `publication_pending: true`, fingerprints the exact record and ledger bytes plus the terminal presentation facts, blocks progress, leaves step 11 out of `completed_steps`, and writes no `pipeline-result.json`; a missing `review-report.md` is the expected handoff state, not a degradation. The blocking briefing has the orchestrator author `review-report.md` once from the settled record and re-run step 11. The second pass repeats settlement idempotently, publishes atomically only when settlement still matches the prepared fingerprint and the report is not byte-identical to one already rejected as stale, writes `pipeline-result.json` with that exact `report_path`, closes the handoff, and only then completes the step (bot mode ends; interactive mode routes to step 12). A changed source, an unchanged stale report, or a pre-existing unbound report regenerates the handoff instead of exposing a terminal marker.

The first pass also reads `critic_adjustments.adjudication_state()`: a REVISE proposal never adjudicated is recorded as a degradation, never applied on the orchestrator's behalf. Step-11-owned degradation records carry stable producer codes across the handoff in first-seen order and project back to the public string-list contract; fingerprints use those ordered identities rather than prose. The one mutating measurement, the probe-residue sweep, accumulates removed paths in `worktree-hygiene.json`; a private hash of the sorted unique path set makes any newly swept path invalidate the prepared report without exposing path provenance.

## Briefing design

The step briefings in `briefings.py` follow `docs/patterns/curated-context-pipeline.md` (repo root). The inline rules:

- **Identity anchoring.** `_PIPELINE_MISSION` is the orchestrator's mission statement; step 1 prepends it to `situation`. Review the pattern doc's "Pipeline Identity Anchoring" principle before editing it.
- **Phase transitions.** `_PHASE_TRANSITIONS` injects a contextual reminder at each phase-entry step: EXECUTION at step 5 (precision in dispatch), SYNTHESIS at step 8 (faithful synthesis), VALIDATION at step 10 (stress-test before a human sees it), OUTPUT at step 11 (complete delivery). Each is a variation on the mission tied to what is about to happen, not a repetition.
- **Artifact discipline.** File-producing steps follow write, verify, proceed. `handoff` is the sole gate mechanism: an artifact the next step requires goes in `handoff`, never buried in `actions`. Steps 3/4, 8, 10 and 11 gate on their output files; step 9 has no gate on purpose, since it asks the orchestrator for no artifact. JSON examples use schema form (`{"verdict": "<APPROVE | REQUEST_CHANGES | COMMENT>"}`), never copyable placeholder values.
- **Voice.** A senior reviewer briefing the orchestrator: authority on process, trust on execution. The voice lives inside the SITUATION / ACTIONS / HANDOFF sections; the headers stay rigid as machine-readable landmarks.
- **Tests pin keywords** in briefing text (`"review-reconciliator" in text`, `"STAND" in text`). Preserve them when rewriting prose and run the relevant `TestStep*` class afterwards.

## Shared protocols and the skip-list

`agents/shared/reviewer-protocol.md` holds the behavioral rules for every reviewer. Bootstrap includes it by a skip-list: sections it replaces with concrete values are excluded, everything else is included automatically, so a new protocol section reaches reviewers without a code change. The skipped sections are `## Step 0` (plugin root), `## Scope Discovery` (bootstrap ran scope.py), `## Output Directory`, `## ReviewOutputBuilder API` (bootstrap provides a pre-filled snippet), and `## File-Based Output` (concrete paths).

Text added to a skipped section reaches zero agents while passing review and shipping in a changelog, which is why the rule in `AGENTS.md` forbids policy there. Policy about what the agent must do with a result belongs in `bootstrap.build_output()`, which knows the concrete budget and paths; `TestNotDiffedContractIsDelivered` in `tests/review/agent/test_bootstrap_integration.py` guards the NOT DIFFED contract and is the template for any comparable one.

`tests-reviewer-protocol.md` is appended for agents with `"tests-reviewer"` in their `protocols` list.

## Bootstrap prompt order

Preserve this order when changing `agent/bootstrap.py` or the protocol files:

1. **REVIEW RULES** (top): behavioral steering by primacy.
2. **Context sections**: PR INTENT (title, author, linked issues, and either a pointer to the extracted author description or the whole HTML-comment-stripped body), REVIEW FOCUS (from `change-purpose.md`; only a structured purpose adds the two-tier instruction and the `verifies=` citation call), REVIEWER-REQUESTED FOCUS (from `run-config.json`, only when steering keywords were provided), HOST CONTEXT (discovery availability and degradation, plus resolved upstream runtime-host and library-dep entries with version, commit, refresh date and declared minimum; a resolved entry is authoritative for its upstream surface), and REVIEW BUDGET (scope-proportionate tool-call calibration).
3. **REVIEW CONTENT** (middle): the scoped diff.
4. **OUTPUT INSTRUCTIONS** (bottom): format and paths, by recency.

## Pipeline-wide containment

`scripts/containment.py` is the single enforcement point for repo-boundary decisions. Advisory host resolvers use `contains()` so first-party code is never presented as an independent runtime host; repo-contributed review configuration uses the same resolved-path primitive before reading rule or reviewer text that may execute with real tools. Telemetry is the deliberate lexical caller: `contains_posix_lexically()` canonicalizes recorded measurement paths without resolving symlinks or touching paths that may no longer exist. Neither lexical primitive may authorize a filesystem read or an execution. `tests/test_containment_contract.py` scans every Python file under `scripts/` for inline containment spellings (`commonpath`, `is_relative_to`, `commonprefix`); only the shared module is exempt.

## Trusted-branch dependency refresh (opt-in)

The pipeline never installs dependencies itself, because package managers execute configuration as code. When the requester opts in, per run with `--refresh-deps` or as a standing machine-local declaration in `~/.config/pirategoat/config.json` (`{"review": {"refresh_dependencies": true}}`, resolved by `user_settings.py`), the main orchestrator inspects the trusted worktree and refreshes dependencies adaptively. An explicit flag wins; an omitted flag falls back to the machine-local default; the effective value lands in `run-config.json` as `refresh_dependencies`. The standing declaration covers every interactive run the requester starts, including PR reviews of third-party branches: that is the requester's trust decision, made in a file the reviewed repo can never touch.

Responsibilities:

- **Pipeline config plus a tracked-Git precheck decide whether refresh may be offered.** Step 3 observes tracked state with `git status --porcelain --untracked-files=no --ignore-submodules=untracked`; a dirty or unknown observation fails closed and offers no commands or save handoff. This gate is separate from the whole-run hygiene baseline and never takes custody of the requester's tracked changes.
- **The orchestrator decides whether and what to run.** After a clean precheck it inspects the repository and the change, chooses lockfile-preserving commands without a manager or flag allowlist, refreshes host context after any installation (`context.py --refresh-host-context`), and writes a schema-1 request under `$TMPDIR`; `not_needed` with an empty command list is the required outcome when inspection finds no work.
- **`dependency_refresh.py save` validates and publishes.** It accepts exactly the request schema, records bounded final tracked-state evidence, and publishes the canonical `dependency-refresh.json` atomically. `completed`, `partial`, `failed`, and a dirty or unknown final state are all valid evidence; only invalid request input blocks publication. Reported command strings are evidence, never execution attestation.

At step 5 the pipeline reads the report through `load_dependency_refresh_report()`; a missing or malformed report after a clean precheck, or a dirty or unknown final state, becomes explicit degraded evidence before dispatch. The manifest preserves `requested`, `reported`, optional unsafe-precheck evidence, and the validated report fields.

**Hard-off for bots.** Step 1 forces `refresh_dependencies` off, with a stderr warning, for `interactive: false` runs whether it arrived via CLI or a pre-seeded `run-config.json`. A bot reviewing third-party PRs must never execute reviewed-branch code.

## Run directory layout

Interactive reviews keep durable state under `~/.pirategoat-tools/reviews/`; an absolute `$PIRATEGOAT_TOOLS_HOME` overrides `~/.pirategoat-tools`, and a relative override is ignored.

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
        │   ├── dispatch-plan.json
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
        │   ├── review.draft.json
        │   ├── review.json
        │   ├── review.md
        │   ├── scope-summary.json                             # plus scope-summary-<domain>.json
        │   ├── scoped-diff.patch
        │   └── started
        ├── synthesis/
        │   ├── reconciliation-context.json                    # schema 4; carries orchestrator_notes
        │   ├── decision-critic-adjustments.json
        │   ├── decision-critic-findings.md
        │   ├── decision-critic-verdict.json
        │   └── <agent>.synthesis-started
        └── tmp/                                               # sanctioned reviewer probe scratch
```

The target directory groups runs for one PR or branch and owns only cross-run state. A run directory is immutable in identity and never reused: its UTC run id sorts lexically, `latest` resolves the newest valid name without a symlink, and the allocator prunes older runs after keeping ten. The seven boundary files stay at the root because interactive commands and pirategoat-bot exchange them there. `run_paths.py` resolves shared artifacts and `reviewer_lifecycle.py` per-reviewer artifacts; callers never reconstruct these paths or spell filenames themselves.

## Output contract

Each reviewer publishes canonical state in `OUTPUT_DIR/reviewers/<reviewer>/`: `review.draft.json`, the mutable artifact replaced by `builder.save_draft()` until the exact printed receipt command finalizes it, and `review.json`, the immutable final artifact published by `finalize_review()` (types in `schemas/review-output.ts`). The reviewer-facing builder contract is stated once in `agents/shared/reviewer-protocol.md` (§Canonical Draft Lifecycle, §ReviewOutputBuilder API) and the implementation contract once in `scripts/review/agent/output.py`.

`review.md` beside the final JSON is derived, never written by reviewers: the step 8 gate materializes it through `review_markdown.materialize_markdown()`, and it stays renderable on demand with `python3 scripts/review/review_markdown.py render|materialize`, the recovery command step 11 prints. Every live reader of final-review contents calls `load_review_document(path, reviewer)`, which delegates to the schema-2 `validate_review_document()` authority; existence-only scans may observe process evidence but cannot project findings, verdicts, reviewed files, or completion.

`review-findings.md` follows the same rule one level up: the reconciliator publishes `review-findings.json` and nothing else, and the pipeline renders the Markdown through the same materializer at step 9 and again at step 11, after the critic adjustments apply. Every section a hand-written report once carried has a structured home: `assessment`, `checks`, `meta.reconciliation`, `recommendations`, `observations`, and `host_context_banner`. Both `review-findings.md` and `review-record.md` show source and severity annotations, dropped findings and checks with reasons and evidence, and answered orchestrator notes. A render failure is a degradation note, never an exception and never a file that disagrees with its JSON.

Findings carry stable `fN` ids and checks stable `cN` ids. A check has exactly `id`, `question`, `method`, `result`, and `source_reviewers`, plus an optional `verifies` list of the change purpose's Verify item ids it settles; it records verification evidence and never affects verdict counts. Raw reviewers own findings, checks, observations, positives, recommendations, confidence, and reviewed-file claims. Only the reconciliator authors the initial nullable `assessment`; a real critic mutation invalidates it unless the orchestrator's `adjudicate` request carries a revised assessment.

The verdict is computed by `verdict_for_counts()` in `verdict_rules.py`, shared with `critic_adjustments.py`, which recomputes the ledger verdict after every applying critic batch: any critical is `block`; three or more highs is `block`; any high, or five or more mediums, is `request_changes`; any medium is `comment`; otherwise `approve`. The outer-pipeline verdict (`APPROVE`/`COMMENT`/`REQUEST_CHANGES` in `pipeline-result.json`) is derived from the reconciled ledger at step 11, `orchestration.py` owns the mapping, `block` maps to `REQUEST_CHANGES`, and a critic `ESCALATE` overrides it to `COMMENT`. Derivation settles on the prepare pass and publishes only when the report handoff completes.

The critic lifecycle has exactly three owners (see the module docstring of `critic_adjustments.py`), and the findings ledger has exactly one write path (see `findings_save.py`).
