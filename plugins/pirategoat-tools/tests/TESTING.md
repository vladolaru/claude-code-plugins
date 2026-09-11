# Testing Framework

Testing for pirategoat-tools uses fast, deterministic code-based graders — no model calls. All tests are pytest-based. The full suite takes roughly 59-65 seconds on a warm run (measured 2026-09-11, three runs) — re-measure both runtime and counts yourself (`time pytest plugins/pirategoat-tools/tests/ -q`, `pytest plugins/pirategoat-tools/tests/ --collect-only -q`) rather than trusting written numbers, here or in the class tables below.

## Architecture Overview

Audited against `ls -R plugins/pirategoat-tools/tests/` on 2026-08-26 (excludes `__pycache__/` and `.pytest_cache/`, which are build artifacts, not test files). **No `__init__.py` anywhere in this tree** — see [Package namespace rule](#package-namespace-rule) below; `test_pytest_layout.py` enforces it repo-wide.

```
tests/
├── TESTING.md                        # This file
├── conftest.py                       # Shared fixtures + sys.path setup (SCRIPTS_DIR on path)
├── test_annotation_evaluation.py     # Deferred-annotation (PEP 649) drift guard for every scripts/ module
├── test_codex_marketplace.py         # Generated Codex marketplace/plugin.json compatibility tests
├── test_containment_contract.py      # scripts/containment.py repo-boundary contract tests
├── test_instruction_budget.py        # Byte budgets for every AGENTS.md and the CLAUDE.md shims (Codex 32 KiB chain)
├── test_git_paths.py                 # Shared Git C-quoted path grammar tests
├── test_pytest_layout.py             # Repo-wide guard: no __init__.py under any plugin's tests/
├── review/                           # Tests for scripts/review/
│   ├── test_pipeline.py              # briefings.py through the pipeline.py compatibility facade
│   ├── test_pipeline_infra.py        # pipeline.py + pipeline_contract.py routing, state, and CLI
│   ├── test_pipeline_integration.py  # orchestration.py through the pipeline.py compatibility facade
│   ├── test_run_paths.py             # Durable review location, allocation, retention, and layout authority
│   ├── test_plan_dispatch.py         # Dispatch planning tests
│   ├── test_context.py               # Review context collection tests
│   ├── test_agent_registry.py        # agent_registry.json schema/completeness/cross-reference tests
│   ├── test_agents_status.py         # Agent readiness gate tests
│   ├── test_atomic_io.py             # Shared atomic-JSON-write primitive tests
│   ├── test_bootstrap_host_injection.py  # Host Context injection in agent bootstrap
│   ├── test_criteria_coverage.py     # Every registry triage criterion has a dispatching probe
│   ├── test_critic.py                # Decision critic tests
│   ├── test_critic_adjustments.py    # Critic proposal adjudication -> review-findings.json ledger writer
│   ├── test_dependency_refresh.py    # Dependency-refresh validating save-channel tests
│   ├── test_dispatch_adjust.py       # dispatch_adjust.py: the orchestrator's validating, atomic dispatch-adjustment CLI
│   ├── test_evidence_manifest.py     # Prose-free evidence projection and telemetry boundary tests
│   ├── test_findings_ledger.py       # FindingsLedgerBuilder ledger-content builder tests
│   ├── test_findings_save.py         # findings_save.py validating save-channel tests
│   ├── test_orchestration_hygiene.py # Step-3 hygiene baseline + step-11 sweep/usage-capture tests
│   ├── test_reconciliation_context.py  # reconciliation_context.py tests
│   ├── test_report_assembly.py       # review-record.md assembler tests
│   ├── test_registry_docs.py         # AGENTS.md registry reference pinned to the registry
│   ├── test_review_config.py         # Repo-contributed review config loader tests
│   ├── test_review_document.py       # review_document.py's validate_review_document rejection-branch tests
│   ├── test_review_run_fixtures.py   # Sanitized audited-run capture/replay, privacy, and projection tests
│   ├── test_reviewer_lifecycle.py    # Mutable draft, intake close, immutable finalization tests
│   ├── test_reviewer_names.py        # agent_name_from_review_stem() inverse-derivation tests
│   ├── test_step_11.py               # Step-11 orchestration: derived verdict, critic absence, adjudication state, findings-markdown re-render
│   ├── test_synthesis_lifecycle.py   # Reconciliator/critic lifecycle measurement
│   ├── test_telemetry.py             # Telemetry logging + manifest-section tests
│   ├── test_telemetry_share.py       # Consent store, repository identity, redaction, upload tests
│   ├── test_user_settings.py         # Requester-side machine-local settings tests
│   ├── test_verdict_rules.py         # Shared finding-severity verdict ladder tests
│   ├── test_workspace_setup.py       # Workspace setup tests
│   └── agent/                        # Tests for scripts/review/agent/
│       ├── test_bootstrap.py         # Bootstrap unit tests (direct imports)
│       ├── test_bootstrap_integration.py  # Bootstrap integration (smoke + category reps + build_output)
│       ├── test_bootstrap_repo_rules.py   # Repo-contributed review-rule injection tests
│       ├── test_diff_noise_filter.py # Semantic diff noise filter tests
│       ├── test_ecosystem_integration_reviewer.py  # ecosystem-integration-reviewer compliance test
│       ├── test_history_insights_reviewer.py # Scenario-budget and parallel-branch detection contract tests
│       ├── test_output.py            # ReviewOutputBuilder unit tests
│       ├── test_review_assignment.py # Reviewer assignment + reviewed-file derivation tests
│       ├── test_scope.py             # Scope filtering unit tests
│       └── test_scope_routing.py     # Domain routing (direct function calls, one 14-domain vector per fixture)
├── linear/                           # Tests for scripts/linear/
│   ├── test_pipeline.py              # Linear issue pipeline tests
│   ├── test_pipeline_guidance.py     # Linear pipeline briefing tests
│   └── test_events.py               # Pipeline events tests
├── iterative_review/                 # Tests for scripts/iterative_review/
│   ├── test_briefing.py              # Iterative review briefing tests
│   ├── test_claude.py                # Claude Code fallback backend output-parsing tests
│   ├── test_cli.py                   # CLI argument tests
│   ├── test_codex.py                 # Codex integration tests
│   ├── test_effort.py                # Adaptive reasoning-effort resolution tests
│   ├── test_loop.py                  # Review loop tests
│   └── test_telemetry.py            # Iterative review telemetry tests
├── analysis/                         # Tests for scripts/analysis/
│   ├── test_codex_rollout.py         # Codex rollout parsing tests
│   ├── test_codex_session_scripts.py # Codex session analyzer/metrics CLI tests
│   ├── test_review_run_metrics.py    # review_run_metrics.py / review_metrics/ package tests
│   ├── test_review_transcript.py     # Privacy-preserving transcript enrichment tests
│   ├── test_session_analyzer.py      # Session analyzer tests
│   ├── test_session_metrics.py       # Session metrics extraction tests
│   └── test_usage_snapshot.py        # Durable token-usage snapshot CLI tests
├── hosts/                            # Tests for scripts/hosts/
│   ├── conftest.py                   # Shared host-resolver fixtures
│   ├── test_chain.py                 # Resolver chain composition tests
│   ├── test_ecosystem_cache_cli.py   # ecosystem_cache.py CLI tests
│   ├── test_host_context.py          # host_context.py CLI tests
│   ├── test_identity.py              # identity.py: a local checkout's declared version and git identity
│   ├── test_types.py                 # Host-context data type tests
│   ├── cache/
│   │   └── test_manager.py           # Ecosystem cache manager tests
│   ├── fixtures/                     # Resolver test fixtures (.keep only — populated at test time)
│   └── resolvers/                    # Tests for scripts/hosts/resolvers/
│       ├── test_docker_compose.py    # docker-compose resolver tests
│       ├── test_ecosystem_cache.py   # ecosystem-cache resolver tests
│       ├── test_explicit.py          # .pirategoat/config.json resolver tests
│       ├── test_plugin_headers.py    # plugin-headers resolver tests
│       ├── test_vendor.py            # vendor/node_modules library-dep resolver tests
│       └── test_wp_env.py            # wp-env resolver tests
├── commands/                         # Tests for commands/
│   └── test_commands.py              # Structural + review command tests (incl. pr-update, switch-to)
├── grading/                          # Test graders and offline compliance grading
│   ├── test_graders.py               # Tests for the graders themselves
│   ├── test_answer_keys.py           # Detection-benchmark answer-key validation against fixtures
│   ├── test_eval_agent_compliance.py # Offline grading tool's own harness tests
│   └── eval_agent_compliance.py      # Offline grading tool for review output files
├── helpers/                          # Shared test utilities
│   ├── graders.py                    # Shared grading functions
│   ├── command_helpers.py            # Shared helpers for command tests
│   ├── context_fixtures.py          # Review context fixture generators
│   ├── pipeline_process.py           # Shared subprocess helper for invoking review/pipeline.py
│   ├── gh_shim.py                    # `gh api` protocol double, its install, and the machine-local config writer
│   ├── telemetry_run.py              # Drives ReviewTelemetry through one complete run for redaction fixtures
│   ├── triage_run_fixture.py         # Captures one audited run's planner inputs from a clone; replays them through build_dispatch_plan without git
│   ├── review_run_fixture.py          # Captures sanitized complete audited runs for generated replay fixtures
│   ├── review_fixtures.py            # Canonical finalized-review/ledger fixtures for consumer-boundary tests
│   ├── critic_seeds.py               # Ledger/proposal seed helpers shared by test_critic_adjustments.py and test_step_11.py
│   └── ts_schema.py                  # Shared reader for schemas/review-output.ts, for TS-contract-lockstep tests
└── fixtures/
    ├── no-code-changes.diff          # Docs-only diff for NO_DOMAIN_FILES tests
    ├── php-source.diff               # PHP source: SQL injection, tight coupling
    ├── php-clean-source.diff         # PHP source with no findings (false-positive probe)
    ├── php-test-only.diff            # PHP tests: missing assertions, over-mocking
    ├── php-with-ci-config.diff       # PHP source alongside CI config changes
    ├── js-ts-source.diff             # JS/TS source: XSS, hardcoded API key
    ├── js-clean-source.diff          # JS source with no findings (false-positive probe)
    ├── js-test-only.diff             # JS tests: snapshot overuse, weak assertions
    ├── go-source.diff                # Go source domain-routing fixture
    ├── go-test-only.diff             # Go tests domain-routing fixture
    ├── e2e-test-only.diff            # E2E tests: hard-coded waits
    ├── ci-config-changes.diff        # CI/toolchain-only config diff
    ├── mixed-code-and-tests.diff     # Cart logic + PHP/JS tests
    ├── wp-hooks-and-i18n.diff        # WP plugin: hooks, i18n, escaping, $wpdb
    ├── triage-runs/                  # Three audited review runs' planner inputs (e582, 6e6a on its correct range, 3725); re-capture, never hand-edit
    ├── review-runs/                  # Generated-only sanitized complete audited-run fixtures; re-capture through review_run_fixture.py, never hand-edit
    └── multi-file-realistic.diff     # 7 files across all 9 domains
```

`fixtures/review-runs/` is generated-only capture output: never hand-edit it. Re-capture through `helpers/review_run_fixture.py` from the source run, which redacts prose and local/session data before binding the critic verdict to the redacted proposal; `review/test_review_run_fixtures.py` pins that contract and deterministic replay.

### Review Pipeline Tests

The review pipeline tests load `scripts/review/pipeline.py` as the stable compatibility facade, then divide assertions along the same ownership boundaries as production:

`tests/review/test_triage_run_regressions.py` pins the planner's agent-by-agent decisions on three real runs; every keyword or hygiene change must show its effect there.

| Source module | Concern | Test file |
|---|---|---|
| `scripts/review/pipeline.py` | Conditions, routing, state I/O, output formatting, telemetry/Git identity, CLI | `review/test_pipeline_infra.py` |
| `scripts/review/pipeline_contract.py` | Shared host, step-sequence, timeout, path, and Git vocabulary | All three pipeline test files |
| `scripts/review/run_paths.py` | Durable review location, fresh allocation, retention, and grouped artifact paths | `pytest plugins/pirategoat-tools/tests/review/test_run_paths.py -v` |
| `scripts/review/briefings.py` | Pure guidance and briefing formatting | `review/test_pipeline.py` |
| `scripts/review/orchestration.py` | Side-effecting per-step subprocess and artifact work | `review/test_pipeline_integration.py` |
| `scripts/review/manifest_sections.py` | Run-manifest section projections (reached through `telemetry.py`, not the facade) | `review/test_telemetry.py` |
| `scripts/review/telemetry_share.py` | Repository identity, machine-local consent, payload redaction, and the consent-gated upload | `review/test_telemetry_share.py` |

`pipeline_mod` preserves the facade's re-export contract for existing callers. Tests that patch a name resolved by orchestration use `orchestration_mod`, so the patch targets the caller's module globals.

### Dependency Refresh Tests (`review/test_dependency_refresh.py`)

Direct contract tests preserve the three-owner boundary: pipeline config plus the tracked-Git precheck decide whether refresh actions may be offered; the main orchestrator decides whether and what to run and reports the outcome; `dependency_refresh.py save` validates the schema-1 request, observes final tracked state, and atomically publishes the only canonical `dependency-refresh.json`. The suite covers clean, dirty, and unknown tracked observations; every publishable status; arbitrary printable command evidence without manager policy or execution claims; strict request and canonical validation; bounded file/list/string inputs; malformed UTF-8/JSON; atomic preservation on invalid input or write failure; and the exact CLI error contract. Pipeline briefing and integration tests separately pin the adaptive clean-precheck handoff, the run-relative `SAVED pipeline/dependency-refresh.json` echo and its match with the step-3 briefing's gate literal, the no-action dirty/unknown paths, direct step-5 consumption, and the permanent interactive-only hard-off.

### Bootstrap Unit Tests (`review/agent/test_bootstrap.py`)

Deterministic pytest suite. Tests `review/agent/bootstrap.py` pure functions by importing them directly — `extract_protocol_sections`, `build_output`, `compute_review_budget`, `load_pr_intent`, and others. No network or model calls.

### Bootstrap Integration Tests (`review/agent/test_bootstrap_integration.py`)

Integration tests that run `review/agent/bootstrap.py` via subprocess against a temp git repo (created from `multi-file-realistic.diff`, isolated from real repo state). Uses category representatives (principle §6) and right-layer testing (principle §7). Parameterized classes expand into more than one collected test per row (`TestSmokeAllAgents` is one method run over every registered agent) — run `pytest --collect-only` for real counts rather than trusting written numbers here.

| Class | What it verifies |
|---|---|
| `TestCategoryRepresentatives` | One comprehensive test per agent category: standard, test-agent, exploration, null-domain, history+override. Each verifies section structure, conditional sections, personalization, and budget in one shot. |
| `TestArchitecturalInvariants` | REVIEW RULES identical across 3 representative agents; DOMAIN RULES identical across 2 test agents; every `## `/`### ` heading of the real `reviewer-protocol.md` outside `REVIEWER_PROTOCOL_SKIP_SECTIONS` reaches the built prompt verbatim (replaces the former per-section delivery guards) |
| `TestSmokeAllAgents` | Every registered agent exits 0 — the ONE legitimate ALL_AGENTS parameterization (validates registry correctness) |
| `TestErrorCases` | Unknown agent exits 1 with structured error output |
| `TestReviewOutputBuilderAPIExample` | Section 3 includes complete builder API usage example (direct `build_output()` call) |
| `TestBootstrapOutputSizeCap` | Large scope truncated with file reference; small scope inline (direct `build_output()` call) |
| `TestDynamicDispatchRisk` | dead-code-reviewer gets DYNAMIC_DISPATCH_RISK from the caller's `has_php` fact (PHP → high, no PHP → low); rendered scope text can't drive the decision in either direction (direct `build_output()` call); 3 end-to-end subprocess tests cover `main()`'s own `has_php` derivation, which the direct calls can't reach — including that a domain-excluded PHP test file (under `=== SKIPPED ===`) must not force `high` |
| `TestOutputFilenameConsistency` | `save_draft()` replaces `reviewers/<reviewer>/review.draft.json`, then finalization publishes immutable `reviewers/<reviewer>/review.json` that bootstrap `OUTPUT_FILES` and reconciliation expect; delivered guidance names final output, never the draft or derived Markdown |
| `TestBootstrapImportDoesNotBreakTelemetry` | Importing `review.agent.bootstrap` first (package-qualified) must leave a working `ReviewTelemetry` — pins the exact import-cycle regression `derive_reviewer_name`'s extraction to `reviewer_names.py` fixed (a same-package caller importing the name FROM bootstrap re-entered bootstrap mid-initialization and silently broke the telemetry load). Runs in a fresh subprocess since in-process `sys.modules` caching from other tests would mask it. |
| `TestNotDiffedContractIsDelivered` | The NOT DIFFED positive-claim/derived-complement contract survives protocol stripping through `build_output()`, while rendered scope and source guidance never instruct reviewers to declare gaps. Guards the 1.108.0 failure where a mandatory contract reached zero agents. |
| `TestEmpiricalProbeContract` | The `pirategoat-probe` naming convention survives protocol stripping into built prompts, and the section is not on the skip-list. The step-11 residue sweep only ever fires on files an agent named this way, so a stripped section makes the enforcement half inert. |
| `TestNotApplicableCompletionContract` | The shared protocol is the sole executable abstention recipe — a reviewer that finds nothing must abstain the one prescribed way. |
| `TestRepoRuleAndRefModeSelection` | Repo rules reach the reviewers they target (effective identity, complete scope); adapter instances receive their declared path scope; an explicit isolation request never runs inline. |
| `TestCanonicalExecutableBuilderSource` | Bootstrap is the sole executable `ReviewOutputBuilder` command source, and its envelope carries the producing plugin version (read from the run-config stamp, emitted empty when unknown so the envelope's five-assignment shape stays constant for the transcript analyzers). |

### Domain Routing Evals (`review/agent/test_scope_routing.py`)

Deterministic pytest suite that verifies `review/agent/scope.py` domain routing logic by calling `filter_noise()` + `filter_domain()` directly (pure functions, no subprocess, no temp git repo — each fixture's changed-file list is read straight from its `+++ b/` headers). One test asserts the whole 14-domain routing vector for each fixture in one row, rather than parameterizing per `(fixture, domain)` pair.

Merge-base detection, stale-branch warnings, and range rebasing (formerly `TestBranchFreshness`, a subprocess suite here) are now pinned by `review/agent/test_scope.py::TestMergeBaseGatingIntegration`, which drives `build_scope()` directly against real temp git repos.

**Fixture domain coverage:** See `ROUTING_MATRIX` dict in `review/agent/test_scope_routing.py` for the complete matrix — one 14-domain vector per fixture, across 10 fixtures. Each entry maps `(fixture, domain) → "OK" | "NO_DOMAIN_FILES"`.

### Command Structure Evals (`commands/test_commands.py`)

Deterministic pytest suite that validates structural properties of command files. Shared helpers live in `helpers/command_helpers.py`. No network or model calls. `TestAllCommandsStructural` is parameterized over every registered command (`ALL_COMMANDS`) — this is where per-command structural checks for `pr-update.md`, `switch-to.md`, and every other non-review command live today; there is no longer a dedicated `TestPrUpdate`/`TestSwitchTo` class per command.

| Class | What it verifies |
|---|---|
| `TestAllCommandsStructural` | Every registered command file exists, has valid frontmatter with a real `description`, and is registered in `marketplace.json` — the superset that also covers review-command frontmatter |
| `TestScriptReferences` | Review commands reference `review/pipeline.py` |
| `TestReviewCommandsReferenceUnifiedScript` | Each review command passes the correct `--mode` to `review/pipeline.py` (`pr-review.md` → `pr`, `full-code-review.md` → `full`, `code-review.md` → computed incremental/full) |
| `TestReviewRunIdentity` | Review commands link pipeline telemetry to the active Claude session |
| `TestUnifiedMission` | Review commands share the "code review orchestrator" identity language |
| `TestDependencyRefreshFlagDocumented` | Every review command documents the `--refresh-deps` opt-in |
| `TestDurableReviewRunDirectories` | Interactive review commands allocate a distinct durable run directory through `run_paths.py` |

### ReviewOutputBuilder Unit Tests (`review/agent/test_output.py`)

Direct unit tests on the `ReviewOutputBuilder` class from `scripts/review/agent/output.py`, including the finding/check-shape validators (`validate_finding_shape`, `validate_check_shape`) exercised through `add_finding`/`record_check` — their real consumer boundary, so a validator change cannot pass its own suite while breaking the boundary that depends on it. Ledger adjudication's validator boundary is `test_critic_adjustments.py`. `review_markdown.py`'s projection has its own suite below in `test_review_markdown.py`. `validate_review_document`'s whole-document rejection branches live in `review/test_review_document.py` (see below), not here: a 2026-09 audit found those branches were pinned only through `grading/test_graders.py`'s `grade_review_json`, a test harness rather than a real caller, so nothing failed if the validator diverged from what publication and ledger adjudication actually enforce. Tests here cover the schema-2 findings/checks/assessment domain, mutable whole-state drafts, the six canonical reviewed-file fields, and verdict derivation.

| Class | What it verifies |
|---|---|
| `TestFindingAndCheckDomainModel` | Stable `fN`/`cN` IDs, non-recycling counters, exact check shape, and absence of every retired field/API |
| `TestAddFinding` | Finding field validation, severity normalization, advisory vocabulary, stable IDs, and file/line invariants |
| `TestRecordCheck` | Material checks record `question`, `method`, `result`, and `source_reviewers` without affecting verdicts |
| `TestAddRecommendation` | Valid priorities store, invalid silently ignored, multiple per bucket |
| `TestNonStringFieldCoercion` | Finding/check free-form text fields coerce to strings where the runtime contract permits |
| `TestSetConfidence` | Valid range works, invalid raises ValueError |
| `TestDerivedVerdict` | All 9 verdict boundaries (approve/comment/request_changes/block) |
| `TestToDict` | Exact schema-2 top-level shape, summary, counters, reviewed-file placeholders before publication, and plugin-version resolution |
| `TestSaveDraft` | `save_draft()` atomically replaces the complete mutable draft, emits exact totals/change receipt/finalization command, and never publishes final JSON or Markdown |
| `TestFileScopedFindings` | `line=None` records a first-class file-scoped finding (`scope: "file"`) that still counts toward the verdict |
| `TestLineRequired` | Invalid line values still raise for point defects — the file-scoped path never becomes a way to skip validation |
| `TestAddObservation` | `add_observation()` stores file-level notes outside the finding pipeline, in insertion order |
| `TestReviewedFileClaims` | Only files the bound assignment offers can be claimed, claims and retractions are all-or-nothing, and no claim moves the verdict |
| `TestNotApplicable` | `mark_not_applicable()` produces a `not_applicable` verdict with `skip_reason`, zero findings |
| `TestAdvisoryChannel` | A finding's channel must be one the bound assignment grants the reviewer — enforced at add time, again at publication, and fails open only when there is no readable input to consult; advisory findings are listed but never gate the verdict |
| `TestDerivedReviewedFiles` | Every draft save derives the six canonical top-level reviewed-file fields from the schema-4 assignment and reviewed-file claims; re-saving recomputes from scratch and finalized JSON preserves the derived values |
| `TestBudgetTargetEcho` | `save_draft()` echoes the call-budget target exactly when the canonical derivation still has unclaimed work |
| `TestDraftFileGapReceipt` | The receipt reports the complete unclaimed population compactly without turning filenames into mutable agent-authored state |
| `TestMetaIsNeverFakeZero` | An unmeasured count stays unmeasured: no reviewed-file count without an authoritative assignment, no duration without a readable dispatch marker |
| `TestTypeScriptContractLockstep` | `schemas/review-output.ts` and the Python validators describe one field set — a field added on either side without the other fails here |
| `TestAssessment` | The reconciliator-owned nullable assessment serializes and renders, while raw reviewer use is blocked by protocol and bootstrap contracts |
| `TestReviewerFilePartition` | The six-field reviewed-files envelope validates itself: a coherent claimed/unclaimed partition of the assignment's claimable files passes, and every incoherent variant (unknown claim, wrong unclaimed set, mismatched count, duplicate claim) is rejected |

### Review Document Validator Tests (`review/test_review_document.py`)

Raw-dict tests on `scripts/review/review_document.py`'s `validate_review_document`: build one structurally valid document with `helpers/review_fixtures.py::canonical_review_document`, mutate one field, and assert the exact `ValueError` message — no file round-trip and no grader in between. Covers malformed summaries, non-canonical or duplicate finding/check ids, exhausted id counters, unexpected top-level fields, invalid finding severities, and verdict/findings mismatches. `grading/test_graders.py` keeps exactly one test (`test_validator_rejection_becomes_failed_grade`) proving a validator `ValueError` becomes a failed `GradeResult` — everything else about the validator's own branches belongs here.

### Review Markdown Tests (`review/test_review_markdown.py`)

Direct unit tests on `scripts/review/review_markdown.py` — the one JSON-to-Markdown projection shared by `reviewers/<reviewer>/review.md` and `review-findings.md`. Split out of `review/agent/test_output.py` when the renderers left `agent/output.py`; the builder appears here only as a document factory. What is under test is the rendering: the sections a document produces, the `render`/`materialize` CLI, and the materializer that writes derived Markdown beside the JSON it came from.

| Class | What it verifies |
|---|---|
| `TestRenderMarkdown` | Markdown is a pure function of the canonical JSON dict — same dict in, same Markdown out |
| `TestMaterializeMarkdown` | The on-demand `materialize` CLI/function reads finalized canonical JSON and writes its derived Markdown |
| `TestReconciliationSectionsRender` | Every section the reconciliator's old hand-written narrative template carried (recommendations, observations, host context banner, `meta.reconciliation`) now has a rendered home |
| `TestMaterializeFindingsMarkdown` | One materializer, parameterized — `review-findings.md` and `reviewers/<reviewer>/review.md` share the same render path, never a second one |
| `TestAssessmentProvenance` | `## Assessment` is prose about a ledger that keeps changing after critic adjustments — provenance is pinned so a stale claim can't outlive the finding it described |
| `TestRemovedByCriticSection` | The ledger deliberately keeps what the critic took out, rendered as an audit section rather than silently vanishing |
| `TestRendererFaithfulness` | Minors that all share one failure mode: the renderer showing content that contradicts what the JSON actually says (e.g. a header claiming a section exists over content that was dropped) |

### Reviewer Lifecycle Tests (`review/test_reviewer_lifecycle.py`)

Direct tests for the mutable-draft/immutable-final state machine and schema-2 review-intake boundary.

| Class | What it verifies |
|---|---|
| `TestReviewPaths` | One safe reviewer identity maps to exactly one draft, final, and schema-4 assignment path |
| `TestDraftOpenAndReplacement` | `open()` creates or completely rehydrates a draft; optimistic saves reject stale writers and preserve the prior bytes |
| `TestFinalization` | Only the exact digest printed by `save_draft()` can atomically publish immutable final JSON, and finalization is idempotent only for that same content; also covers synthesis closing schema-2 `review-intake.json` — recording finalized and discarded-draft reviewers and blocking every later save/finalize transition (formerly a separate `TestReviewIntakeClose` class, merged here) |
| `TestFinalizationTelemetry` | Draft saves and finalization emit distinct schema-3 lifecycle telemetry without treating a draft as reviewer completion |

### Reconciliation Context Tests (`review/test_reconciliation_context.py`)

Direct unit tests on `scripts/review/reconciliation_context.py` — finalized-review loading, scope and hunk checking, source-snippet extraction, and severity normalization. The module builds schema-4 `reconciliation-context.json` and nothing else now: its two Markdown renderers (`to_markdown` for the reconciliator, `build_critic_context` for the decision critic) were projections whose only readers were agents, and both are gone — the agents read the JSON, and the decision critic reads `review-record.md` beside it.

| Class | What it verifies |
|---|---|
| `TestLoadAgentReviews` | Only immutable finalized reviewer JSON enters synthesis; drafts, the reconciled ledger, and pipeline artifacts are excluded |
| `TestSeverityFloorNormalization` | The floor is the structured field only — description prose never promotes a finding, and the prose marker is stripped before the critic reads it |
| `TestExtractReferences` | Source references are extracted only from canonical finding fields |
| `TestReadSourceSnippets` | Repository reads stay bounded, normalized, and honest for missing or binary source; overlapping and boundary-clamped windows merge deterministically without losing referenced lines |
| `TestCheckScope` | File and line scope annotations preserve the finding while describing its diff relationship |
| `TestFilterInScopeReferences` | In-scope references are selected without mutating the reviewer record |
| `TestCheckScopeHunkLevel` | Hunk proximity remains a review aid, not an automatic out-of-scope verdict, through the public `diff_hunks=` parameter |
| `TestParseDiffHunks` | Unified-diff hunk ranges and quoted paths parse into deterministic source coordinates |
| `TestFullScript` | The CLI writes exact schema-4 reconciliation context from finalized schema-2 reviews and canonical assignments, with the change purpose's `verify_items` carrying the reviewer checks that cite each |
| `TestMissingAgentDetection` | Dispatched-minus-reporting is a measurement, with unknown dispatch (`null`) distinct from a measured-empty dispatch (`[]`), through the CLI and back |
| `TestPrefilterAnnotation` | Structurally-certain out-of-scope findings are annotated in place with a checkable count, never deleted, and `not_in_hunk` is never annotated |
| `TestReviewStem` | Reviewer artifact stems are derived through the shared trailing-`-reviewer` rule |

### Change Purpose Tests (`review/test_change_purpose.py`)

Direct unit tests on `scripts/review/change_purpose.py` — the parser behind the step-5 warnings, bootstrap's REVIEW FOCUS tiers, the reconciliation context's `verify_items`, and the record's Verify-items table.

| Class | What it verifies |
|---|---|
| `TestParse` | Explicit ids, the `— source:` provenance, the `(carried over)` marker, continuation lines, and the extracted author description are read as written; a purpose without the headings is `structured: False` with no problems |
| `TestProblems` | The parse facts reported as problems and nothing else: a missing heading, an item without a source, an inferred Context item, a duplicate id, an id under the other tier's heading, a tier body that parses to no item without reading `None.`, and more than eight Verify items |
| `TestChecksSettling` | Checks are grouped by the Verify item their `verifies` list cites; `undeclared_citations` names every citation of an id the purpose does not declare |

### Run-Level File Review Tests (`review/test_file_review.py`)

Direct unit tests on `manifest_sections.aggregate_file_review()` — the run-level file review pipeline step 9 publishes into `state["file_review"]` for the review record. It reads the scope-summary sidecars and the finalized review documents, never the reconciliation context, and lives beside the assignment manifest that reads the same artifacts over a different population.

| Class | What it verifies |
|---|---|
| `TestAggregateReviewedFiles` | `aggregate_file_review()` carries inline-diff receipt per agent from the scope sidecars and each reviewer's claimed/unclaimed files from its finalized review, never re-derived from the assignment sidecar; a malformed document receives no credit, and one reviewer's claim cannot conceal another reviewer's unclaimed work |
| `TestUnscopedFiles` | `unscoped_files` — the changed files no reviewer's scope contained in any form, with the measured-empty case distinct from `None` when no changed-file list was supplied; `noise_filtered_files` is the planner's by-design exclusions, measured only when both the changed and the reviewable lists were supplied |
| `TestAgentsReportingCountsAgents` | `scope_reporting_agent_count` counts distinct agent names, not scope-summary files — reviewers with a secondary `-config-ops` summary still count once |

### Review Record Assembly Tests (`review/test_report_assembly.py`)

Direct unit tests on `orchestration.assemble_review_record()` — the machine projection of the findings ledger the pipeline writes at step 9 and re-assembles at step 11. No LLM writes or edits `review-record.md`, which is what makes it safe to hand the decision critic and what lets `review-report.md` be authored once, after validation.

| Class | What it verifies |
|---|---|
| `TestRecordAssembly` | Section order, the header's verdict and severity counts, and the two byte-identity contracts that keep the record from disagreeing with anything else: its findings body IS `render_review_body()` and its file-review section IS `_render_file_review_section()`. Also the record's own new prose — the run notes (dependency refresh, dispatch) and the closing verdict line, which names the ledger layer the verdict was computed at and the published layer it maps onto |
| `TestRecordIsAProjection` | Re-assembly after `adjudicate()` shows the post-critic ledger: adjusted severities, the recomputed verdict, one adjudication line for every verified/refuted/not_checked decision, and — when no replacement narrative was written — the explicit withdrawal notice rather than the retracted text presented as current |
| `TestRecordSanitization` | Prose `Severity-floor:` markers are stripped before the record renders them (they read to the critic as an instruction not to demote), the STRUCTURED floor still renders, `review-findings.json` on disk keeps the reviewer's own words, and a non-string finding field costs a rendering nicety rather than the artifact |
| `TestBriefingsAreConstantSize` | Briefings are O(1) in changed-file count while the record is O(n): a 500-file coverage state renders a step-9 briefing under 8KB with all 500 lines in the record, and the briefing is byte-identical at 1 file and at 500. Pins the class of guarantee the record artifact buys, not a single fact about step 9 |
| `TestRecordFailureModes` | A run with no ledger reports a measured zero, not a failure (that is the degraded path step 9 routes to manual synthesis); an unreadable or shape-invalid ledger reports `failed` with the reason and writes nothing |
| `TestRecordWriteIsAtomic` | The write goes through `atomic_write_text`, a failing render leaves the previous record byte-identical rather than half-replacing it, and no temp file survives a successful assembly |
| `TestPreparedReportSourceFingerprint` | The final report binds to the exact settled record, findings ledger, critic state, and coverage source it describes |

### Critic Adjustments Tests (`review/test_critic_adjustments.py`)

Direct contract tests for the three-owner lifecycle: `critic proposal -> critic.py --save -> committed proposal (never rewritten)`; `orchestrator adjudication -> critic_adjustments.py adjudicate -> ledger provenance`; `ledger content -> reconciliator via findings_save.py`. `write_critic_verdict()` is the sole writer of the proposal and its digest-bound marker, and `adjudicate()` is the sole writer that carries an adjudication's outcomes into `review-findings.json`.

| Class | What it verifies |
|---|---|
| `TestCanonicalFindingsReader` | The reader boundary rejects any ledger a live consumer cannot trust: reviewer-envelope fields on the ledger, reconciliation counts that do not partition, agent names outside the dispatch grammar, unbounded skip reasons, and a live entry carrying a removal adjustment |
| `TestAdjudicateWritesTheLedgerOnce` | The whole adjudication lifecycle in one lock: verified/refuted/unchecked land as outcomes, a second adjudication of the same proposal is refused, state stays `pending` until adjudicated, a non-REVISE verdict cannot be adjudicated, and a tampered (post-verdict-edited) proposal is refused |
| `TestApplyAdjustments` | The happy paths and the loud failures: `promote`, `add`, and `remove` land with `critic_adjustment` provenance and a summary recounted from the resulting population; refuted entries are skipped; an unknown id or an invalid action/field fails the whole call with nothing written |
| `TestRejectionAudit` | A refuted decision lands a `rejected_critic_adjustments` audit record in `review-findings.json` while the finding itself remains unmodified; a later round appends rather than replacing; mixed batches apply the verified/unchecked half and audit the refuted half; a missing or blank rejection reason refuses the whole request |
| `TestBatchCoherence` | All-or-nothing batch validation with nothing written: duplicate targets, an entry targeting a finding an earlier entry in the same batch removed, an entry with no usable id, an unaddressable finding, an `add` that carries a critic-supplied id (both spellings), a pre-existing severity outside the vocabulary, and a findings file that is not a JSON object |
| `TestValidateProposalInput` | Direct unit coverage for the critic-owned proposal validator: a valid batch returns no problems, while a non-object payload, wrong schema, non-list adjustments, a missing `adjustments` key, or a malformed entry each returns one |
| `TestAdjustmentsSchemaValidation` | The adjustments doc accepts only the exact schema `decision-reviewer.md`'s template writes and rejects an out-of-template schema, a non-object document, a prepared entry carrying only its proposal fields, and duplicate adjustment ids |
| `TestScopeLinePairing` | `scope`/`line` stay the pair `schemas/review-output.ts` declares and `output.py`'s renderer branches on: `add` without a line is file-scoped, rescoping toward or away from a line keeps the marker consistent, a patch that leaves `line` alone leaves `scope` alone, and an out-of-range line is rejected |
| `TestReadCriticVerdict` | Unit coverage for the reader `adjudicate()`'s gate is built on: a missing file, malformed JSON, non-object payload, non-string or missing `verdict`, and a lifecycle field on a proposal entry all return `None` or fail closed |
| `TestAssessmentInvalidation` | A real applying batch withdraws the reconciler's assessment into the append-only `invalidated_assessments` audit; a refused, wholly refuted, or semantic no-op batch leaves it untouched |
| `TestCheckPassthrough` | The ledger's `checks` survive adjudication and `write_findings()` unfiltered, and the rendered Markdown carries the checks section |
| `TestReconciliatorWritePathPin` | Writer #1 is an agent following a Markdown snippet, so a test is the only thing holding it to the sanctioned path: the snippet builds the ledger with `FindingsLedgerBuilder` and saves it only through `findings_save.py`, never a bare write |
| `TestOutcomeVocabulary` | The per-entry `outcome` is script-derived and validated: every value in `OUTCOMES` is accepted, an unknown value rejects the ledger, an applied record may not claim `refuted`, and a rejected record must |
| `TestRevisedAssessment` | The orchestrator's post-critic assessment: a non-string value rejects the whole request without mutation, it becomes the ledger assessment only when an operation actually applies, and the withdrawal record survives the replacement |
| `TestLedgerVerdictRecompute` | Applied mutations recompute channel-aware summary counts and verdict through the shared authority; the pre-apply verdict is preserved only on the first change; a stale ledger verdict is refused at the reader |
| `TestSchemaTwoTargetUnion` | Schema-2 proposals target the tagged finding/check union: mutations require kind and id, `add` never carries a caller-supplied id, and checks permit only correction or removal |
| `TestProposalPreparation` | `prepare_proposal()` assigns unique stable ids (retrying an improbable UUID collision), rejects duplicate targets and lifecycle/non-proposal fields before assigning ids, and its digest covers every byte of the published proposal |
| `TestAdjudicationRequest` | `adjudicate()` derives the `not_checked` complement of the ids it was handed; an invalid request, an unknown or duplicate ledger target, or a malformed ledger leaves the proposal and ledger byte-identical with nothing written |
| `TestPublicationAndAdjudicationShareOneLock` | `critic.py --save` and `adjudicate()` hold the same output-directory lock, so a save and an adjudication can never interleave snapshots |
| `TestAdjudicationCLI` | The `adjudicate` subcommand step 10's REVISE briefing shells out to: echoes the derived counts and the ledger verdict, reports an omitted assessment as absent, refuses a second adjudication on stdout, rejects invalid or unparseable stdin cleanly, and `adjudicate` is the only subcommand the CLI exposes |

### Step 11 Tests (`review/test_step_11.py`)

`orchestration._orchestrate_step_11`'s own module — derived verdict, critic absence, adjudication state, and the findings-Markdown re-render. Adjudication and ledger contracts stay in `test_critic_adjustments.py`; this file exists because those five classes cost most of that file's wall time exercising the same step-11 seam.

| Class | What it verifies |
|---|---|
| `TestDerivedVerdict` | Step 11 derives the published verdict from `review-findings.json`: every canonical ledger verdict maps, casing/padding fail closed, a critic ESCALATE overrides to COMMENT while STAND does not, and an unusable ledger falls back to COMMENT with a note |
| `TestCriticAbsenceHonesty` | A critic that was DISPATCHED and produced no usable verdict (nothing written, or recorded SKIPPED) degrades the run; the pipeline's own quick skip and a critic that answered stay silent |
| `TestCriticInputRoundTrip` | The stable id the critic reads off the handed ledger is the same one `adjudicate()` later mutates and the same one the derived verdict reflects; the record offers no rival positional handle |
| `TestStepElevenReportsUnadjudicatedProposal` | Step 11 does not adjudicate on the orchestrator's behalf — it reports: a pending REVISE proposal degrades the run stably across the publication handoff, and an unreadable proposal or malformed ledger degrades instead of crashing |
| `TestStepElevenRerendersFindingsMarkdown` | `review-findings.md` must describe the FINAL ledger: a demoted severity and the ledger's own verdict reach the re-rendered Markdown, a render failure is a degradation note not an exception, a run with no ledger renders nothing, `report_path` resolves report → record → findings Markdown, and the private step-11 degradation-record merge keeps first-seen order, drops a malformed private record, and honors only one code's discriminator shape |

### Findings Ledger Tests (`review/test_findings_ledger.py`)

Direct unit tests on `FindingsLedgerBuilder` — the reconciliator's `ReviewOutputBuilder` subclass: review content plus the four `set_reconciliation()` judgment counts, no reviewer identity and no reviewed-file lifecycle. Flat module-level functions, not a class:

- `test_ledger_dict_is_content_plus_reconciliation` — `to_dict()` is exactly `REVIEW_CONTENT_FIELDS` at `LEDGER_SCHEMA`, carries no `reviewer` key, and nests the four judgment counts under `meta.reconciliation`
- `test_ledger_content_validates_as_content` — the content half (with `reconciliation` stripped back out) passes `validate_review_content(schema=LEDGER_SCHEMA)`
- `test_ledger_requires_reconciliation_before_serializing` — `to_dict()` without a prior `set_reconciliation()` call raises
- `test_ledger_has_no_reviewer_lifecycle` / `test_ledger_has_no_open_classmethod` — one representative lifecycle name (`save_draft`) and `open()` both raise `TypeError` rather than silently inheriting reviewer behavior the ledger does not have; the other three lifecycle names bind to the same `_no_lifecycle` function object
- `test_ledger_reads_plugin_version_from_the_bound_run`, `test_ledger_duration_spans_the_reconciliator_dispatch` — the ledger inherits the same run-bound facts a reviewer draft does, keyed on the `review-reconciliator` dispatch marker rather than the `reconciliator` actor name
- `test_reconciliation_counts_must_be_non_negative_integers`, `test_reconciliation_judgments_must_partition_the_grouped_concerns` — `set_reconciliation()` rejects a negative count and any four that do not sum to `grouped_concern_count`
- `test_ledger_renders_without_a_reviewer_title` — the shared Markdown renderer produces a PR-titled report with no reviewer byline
- `test_the_taught_snippet_calls_only_methods_the_builder_has` — every `builder.<method>()` call in `agents/review-reconciliator.md` resolves to a real, non-refusing `FindingsLedgerBuilder` method (never `open()` or a draft-lifecycle name), so a renamed or deleted builder method breaks the taught snippet loudly instead of at review time

### Findings Save Tests (`review/test_findings_save.py`)

Sibling design to `review/test_critic.py`'s `TestCriticSave`: `findings_save.py` is the ONLY channel `agents/review-reconciliator.md` may write `review-findings.json` through. `TestFindingsSave` runs the CLI as a subprocess against one default reconciliation context and covers the canonical-shape and producer-boundary rejections: missing or wrong schema, retired tool metadata, malformed findings/checks/assessment, a verdict that does not match its findings or is not lowercase, a count or by-severity mismatch, non-object/non-list inputs, an absent or unreadable `--findings` file, multiple simultaneous problems all echoed as separate `REJECTED:` lines with nothing written, and — the critic-boundary cases — `test_rejects_actor_supplied_critic_lifecycle_fields` and `test_rejects_actor_supplied_critic_provenance`, which reject an agent-authored critic-owned lifecycle field or a hand-added `critic_adjustment` provenance record before anything is written.

A set of module-level functions cover what is specific to this channel rather than the shared document validator:

- `test_save_stamps_pipeline_facts_from_context` — every `RECONCILIATION_PIPELINE_FIELDS` value (input finding count, contributing agent count, reviewing/not-applicable/dispatched/missing agents) is filled from `reconciliation-context.json`, never authored by the agent
- `test_save_rejects_verified_count_that_disagrees_with_findings`, `test_save_rejects_grouped_count_above_the_input_population` — the agent's own judgment counts are cross-checked against the findings it actually recorded and the input population the context measured
- `test_save_rejects_pipeline_fields_authored_by_the_agent`, `test_save_rejects_a_host_context_banner_authored_by_the_agent` — an agent attempting to author a pipeline-owned reconciliation field or the host banner is rejected before the canonical validator runs
- `test_save_copies_degraded_host_banner`, `test_save_leaves_an_undegraded_host_banner_off_the_ledger` — the banner reaches the ledger only from the context, and only when it is actually degraded
- `test_save_rejects_advisory_finding_without_advisory_source`, `test_save_accepts_an_advisory_finding_a_source_review_carried` — an advisory-channel finding must trace back to a source review that itself carried the advisory channel
- `test_save_rejects_a_run_with_no_reconciliation_context` — a missing `reconciliation-context.json` refuses the save outright, since there is nothing to stamp the ledger's pipeline-owned facts from

### Orchestration Hygiene Tests (`review/test_orchestration_hygiene.py`)

Direct unit tests on the finalize-side hygiene in `scripts/review/orchestration.py` — the step-3 hygiene baseline snapshot, the step-11 compare-and-sweep, the degradation notes step 11 derives from the result, and the step-11 token-usage capture. Each test runs against a throwaway git repo as CWD, because the hygiene code under test resolves and mutates the repository it is standing in.

| Class | What it verifies |
|---|---|
| `TestBaselineCapture` | `_capture_worktree_baseline()` records the porcelain entries AND the repo root it measured; a clean tree writes an empty entry list (a measured zero), and a failed capture writes nothing at all rather than a baseline that would license a sweep |
| `TestHygieneCheck` | `_check_worktree_hygiene()` sweeps ONLY untracked files whose basename carries the probe marker, inside a baseline whose recorded repo root is provably the one it is standing in. Porcelain paths are decoded through the shared C-quoting grammar, so a probe git prints quoted (non-ASCII bytes) is still swept, and a malformed quoted line fails closed — reported, never acted on. Foreign new files, preexisting dirt, a marker-named directory's ordinary contents, a marker-named symlink to a directory, and tracked marker files are reported, never deleted; a missing, foreign, or repo-root-less baseline reports `unknown` and deletes nothing |
| `TestStepElevenHygieneNotes` | Only swept probe residue degrades the run — a requester editing their own tree during a review is measured, not blamed, because `status` is a bot contract meaning "the review underperformed". A non-git CWD adds no notes |
| `TestStepElevenUsageSnapshot` | The capture is a subprocess seam (so `scripts/review/` never imports `scripts/analysis/`) whose failure is deliberately quiet: an absent or unreadable snapshot reads as unmeasured — `usage: null`, status untouched, no note — because a Codex host and every pre-feature run legitimately have no Claude-format transcripts. A measured snapshot projects into the compact `usage` block with both availability halves intact plus `window_closed`, and a measured-missing half publishes nulls rather than zeros |

### Pipeline Infrastructure Tests (`review/test_pipeline_infra.py`)

Tests on `scripts/review/pipeline.py` and `pipeline_contract.py` — step sequence, routing, state I/O, output formatting, telemetry/Git identity, and the CLI. The step-skip class is documented here because its records are what make a run auditable at all — reconciling 12 contract steps against 9 completions otherwise takes source archaeology; the remaining classes follow the routing/state/CLI split the module table above describes.

| Class | What it verifies |
|---|---|
| `TestSkippedStepRecording` | The router records each step it passes over — number, title, and the gating condition — into `pipeline-state.json` at the moment it decides: a PR-only step passed over in branch mode, a trailing skip recorded by the last active step, one record per step across re-invocations, and never a step the router actually ran |

### Telemetry Tests (`review/test_telemetry.py`)

Direct unit tests on `scripts/review/telemetry.py` and the run-manifest projections in `manifest_sections.py` — start/step/finalize events, structured filenames, snapshots, and each manifest section beside its availability flag. The skip-ledger, token-usage, synthesis-agent, dependency-refresh, reviewer-Markdown, and findings-Markdown projections are documented here because each carries an audit contract from a run artifact into the manifest; `TestOptionalSectionAvailabilityKeysContract` pins the producer-declared `OPTIONAL_SECTION_AVAILABILITY_KEYS` tuple against what `_build_manifest` actually assigns, in both directions; the remaining classes follow the same direct-unit-test pattern.

| Class | What it verifies |
|---|---|
| `TestSkippedStepsManifest` | `build_skipped_steps_manifest()` keeps three outcomes apart: `None` when the run never recorded skips (state absent, key-less, malformed, or a non-list value), `[]` as a measured zero, and the projected records otherwise — entries without a usable integer step are dropped and absent prose defaults to empty, and the manifest carries the section beside `availability.skipped_steps` |
| `TestSynthesisAgentsManifest` | `build_synthesis_agents_manifest()` projects the reconciliator/critic lifecycle as its OWN family, never folded into `manifest["agents"]`. Same three outcomes: `None` when the run never measured (artifact absent, or announcing a schema this builder cannot vouch for), a measured empty `agents` list (finalize looked and found no dispatch markers), and the rows otherwise. `stalled` is true only for an explicit `True` — a stall accuses the run, so an unreadable flag falls to the weaker claim, the same rule usage's `window.closed` follows — and an unusable duration stays `None` rather than becoming a zero that would read as "the phase finished instantly". The non-interference pin lives here too: a 19-reviewer cohort's started/completed/incomplete projection is byte-identical whether or not the synthesis section exists beside it |
| `TestSynthesisAgentsManifestShape` | Row-shape parity against `synthesis_lifecycle.ROW_KEYS`: three modules write this shape (producer, this builder, the metrics sanitizer), so an undeclared key is dropped and a key taught to only one of the three fails loudly instead of vanishing green. `verdict` falls to `None` on unusable evidence |
| `TestUsageManifest` | `build_usage_manifest()` keeps the same three outcomes apart for the step-11 token-usage snapshot: `None` when the run never measured usage (artifact absent or malformed), a section carrying its own `missing` availability when the capture ran and found no transcripts, and the projection otherwise. The two availability halves stay separate (subagents complete at finalize, orchestrator partial by construction) beside `window.closed`, which is what tells a reader whether "partial" means a substituted bound or damaged evidence — an unreadable flag falls to the weaker claim. An unrecognized label reads `missing`, a damaged usage map is dropped rather than zeroed, and an unknown snapshot schema reads as unmeasured |
| `TestDependencyRefreshManifest` | `build_dependency_refresh_manifest()` projects the trusted-branch refresh boundary without inventing execution claims: requested/reported flags, optional dirty-or-unknown precheck refusal evidence, and the status/commands/final tracked-state group only from a validated canonical report. Historical verification blocks remain a bounded analysis-reader concern and are never emitted by the live producer. `test_availability_flag_tracks_the_payload` pins `availability.dependency_refresh` beside the section |
| `TestReviewerMarkdownManifest` | `build_reviewer_markdown_manifest()` projects step 8's per-reviewer render outcome via the shared `_sanitize_derived_markdown_outcome` validator. Task 13 added `test_availability_flag_tracks_the_payload`, the same before-this-task gap `TestDependencyRefreshManifest` closes |
| `TestFindingsMarkdownManifest` | New in Task 13, closing the Task 7 deferral: `build_findings_markdown_manifest()` projects steps 9/11's `review-findings.md` render outcome from `state["findings_markdown"]`, sharing its validator (and its written/expected/status vocabulary) with `TestReviewerMarkdownManifest`'s sibling rather than restating it |

**Historical-data note:** `thoughts_length` was removed from live events (`test_telemetry.py::TestNoFabricatedMeasurements`, elsewhere in this file), but manifests and JSONL logs written before that fix still carry `args.thoughts_length: 0` on every `step`/`pipeline_end` event — a measurement that never happened, published as a measured zero, on every pre-fix run. Nothing reads the key today. Any future historical-cohort work over pre-fix logs must treat `thoughts_length` as unmeasured noise, not data — do not average it, do not use its presence/absence to date a run, and do not infer anything from its value being 0.

### Synthesis Agent Lifecycle Tests (`review/test_synthesis_lifecycle.py`)

Direct unit tests on `scripts/review/synthesis_lifecycle.py` and its five orchestration seams. The review-reconciliator (step 8) and the decision critic (step 10) never run `agent/bootstrap.py`, never write `reviewers/<agent>/review.json`, and are never in `dispatch-plan.json` — the only list `agents_status.py` iterates — so the reviewer lifecycle machinery structurally cannot see them. This suite pins the measurement that replaces it.

| Class | What it verifies |
|---|---|
| `TestDispatchMarker` | `mark_dispatched()` writes bootstrap's marker BODY (one aware UTC ISO timestamp) under a namespaced NAME, `<agent>.synthesis-started`. The suffix keeps these markers out of the reviewer `*.started` contract other tools scan — pirategoat-bot's resume path treated every such hit as a reviewer and renamed synthesis markers away as orphans, erasing the stall signal in the one window where the marker is the only record of a dispatch. Writer and reader resolve the path through the same `MARKER_SUFFIX` constant, so a marker the writer creates is always one the reader finds; an unwritable marker costs a measurement, never the review |
| `TestAvailability` | A run with no markers records no rows at all — a pre-feature run and a quick-mode skip that commits `SKIPPED` without dispatching the critic are both ABSENT from lifecycle, never a zero-duration row for a phase nobody measured |
| `TestCompletionObservation` | `duration_ms` comes from the completion artifact's mtime, not from observation time (which would inflate the critic's phase by however long finalize took to arrive); the completion artifacts are the handoff-GATED ones, and `decision-critic-findings.md` deliberately is not — it exists only when the critic produced a critique, so keying on it would report a crashed critic as still running |
| `TestStallDetection` | Only `finalize=True` adjudicates a stall; a marker with no completion artifact then records `stalled: true` plus the elapsed stall length. An artifact predating its dispatch is not that dispatch's output (no borrowed durations), and an unreadable or naive-timestamp marker still counts as dispatched — reporting "not dispatched" there would hide a stall |
| `TestIdempotence` | A completed entry is preserved verbatim across re-observation — step 9's reading is the tightest bound the run will ever have, and finalize must not push its `observed_at` minutes later. An incomplete entry IS re-observed, and a corrupt or foreign-schema prior artifact is re-derived rather than trusted |
| `TestArtifactEnvelope` | `synthesis-agents.json` carries `schema: 1`, the returned payload is what landed on disk, and rows carry exactly `ROW_KEYS`. ONE clock is pinned as an absence: neither the row nor the section records when the script looked, only when the agent finished |
| `TestVerdictCapture` | The completion artifact's own `verdict` rides the row because it changes what the duration beside it means. Current quick-mode `SKIPPED` commits have no dispatch marker and therefore no row; historical `SKIPPED` rows remain readable and excluded from critique-duration statistics. Unreadable, non-string, and verdict-less artifacts all yield `None`, and an artifact discarded as predating its dispatch contributes no verdict either — a stale conclusion must never be paired with a live phase |
| `TestStepEightDispatchMarker` | The reconciliator marker is stamped only where step 8 actually hands off: not on the readiness gate's WAITING return, and not when the reconciliation-context gate raises — a marker on either path would make a run that dispatched nothing read as a stalled agent |
| `TestStepTenDispatchMarker` | The critic marker is written on exactly the branch whose briefing dispatches one; the quick-mode skip branch (no marker, no lifecycle row) is `TestAvailability::test_undispatched_agent_absent_not_zero`'s contract, exercised through the real step here in `TestStepTenRedispatchStartsFreshAttempt::test_the_skip_branch_observes_too` |
| `TestStepTenRedispatchStartsFreshAttempt` | Step 10 observes a completed critic before re-entry, then retires that attempt's findings, proposal, verdict, and dispatch marker before starting the replacement. If the replacement never saves, finalize reports the current critic unavailable and stalled instead of borrowing the prior verdict; the same first observation closes the REVISE window on the reconciliator before critic adjustment rewrites its artifact — these two tests are also where the step-11 critic-duration, stall, and pre-write-observation seams are pinned, end to end through the real step 10 → 11 sequence |
| `TestStepNineObservation` | Step 9 records the reconciliator's completion — the earliest moment the script re-enters after step 8's handoff |
| `TestStepElevenObservation` | Finalize on a run with no markers at all records a measured emptiness, never a fabricated zero-duration row |

### Registry Documentation Tests (`review/test_registry_docs.py`)

Two module-level guards pinning the plugin `AGENTS.md` agent-registry reference to `scripts/review/agent_registry.json` in both directions: every `model_tier` the registry actually uses must appear in the documented vocabulary, and the vocabulary must not teach a tier no agent uses. `"inherit"` is excepted as a routing keyword — legitimate to document with zero users. The row had drifted to `inherit`/`sonnet`/`haiku` while five agents ran at `opus`, so a cold agent reading the canonical reference learned a vocabulary the machine does not use.

Three more module-level guards pin `README.md`'s "#### Model Tiers" section — the same drift, one level up: the README hand-summarizes registry `model_tier` counts and names example agents per tier in prose, and nothing tied that prose to the registry either. It drifted to `opus (4 agents)` while the registry carried five, silently omitting `woo-regression-reviewer` from the paragraph. The three guards parse the README's own agent tables (Domain Review / Pipeline / Cross-Validators / Utility) and its Model Tiers bullets, then check: every registry agent's README table row matches its registry `model_tier`; each tier's `(N agents)` figure equals what the README's own tables tag with that tier; and every registry agent at the `opus`/`haiku` tiers (small enough that the README names each one individually) is named by its exact slug in that tier's bullet prose. `sonnet` (22 agents) is deliberately written as category-grouped prose rather than an exhaustive per-agent listing, so only its count is checked — naming every one of 22 agents individually is not the convention this guard protects.

The same file also carries an unrelated block, one pin per contract, under its own `# Agent-definition contract pins` heading comment: direct regression guards on `agents/*.md` prose and the shared protocols, not on the registry vocabulary. `test_reviewer_protocol_says_a_mounted_host_is_not_always_upstream`, `test_woo_invariant_rows` and `test_wp_architecture_reviewer_audits_half_deprecations` are plain functions (the last came in with PR #15 and pins each clause of the Deprecation Rule's half-deprecation paragraph, by its author's design); `TestAPIContractReviewerReturnSideHooks` (established runtime behavior is contract, not just the untouched `apply_filters()` call), `TestDismissalDisciplineContract` (dismissal/mitigation verification applies to every finding, not a floored subset), `TestVerificationMethodContract` (the reconciliator weighs checks by method, and reviewers record absence claims as checks), and `TestUnchangedCallerScopeContract` (an unchanged caller of a changed contract stays in scope, and a debug-only log is a resilience gap rather than detection) are classes. These four moved here from `review/agent/test_bootstrap_integration.py`, which now covers only protocol-delivery mechanics (see above).

### Shared Graders (`helpers/graders.py`)

Reusable grading functions for review output files. Used by both `grading/test_graders.py` (validates the graders themselves) and `grading/eval_agent_compliance.py` (grades actual agent output).

Every grader returns a `GradeResult`:

```python
@dataclass
class GradeResult:
    passed: bool        # All checks passed
    score: float        # 0.0-1.0 (checks_passed / checks_run)
    failures: list      # Description of each failure
    checks_run: int
    checks_passed: int
```

**Available graders:**

| Function | Input | Checks |
|---|---|---|
| `grade_review_json(path)` | Path to `reviewers/<reviewer>/review.json` | File exists, valid JSON, required fields (`pr_id`, `reviewer`, `schema`, `verdict`, `summary`, `findings`, `checks`, `assessment`, the reviewed-file fields, `meta`), valid severities, exact schema 2, finding/check schemas, reviewed-file coherence, summary structure |
| `grade_review_markdown(path)` | Path to `reviewers/<reviewer>/review.md` | File exists, `# ... Review` header, `## Executive Summary`, `**Verdict:**` — rendered from the JSON when absent |
| `grade_signal_format(text)` | Return signal text | `STATUS: FINISHED`, `OUTPUT_FILES:`, `COUNTS:`, `VERDICT:`, `SUMMARY:` |
| `grade_no_domain_files(text)` | Agent output for no-code scenario | APPROVE verdict, zero findings |
| `grade_error_exit(text)` | Agent output for error scenario | Error indication, no STATUS: FINISHED |
| `grade_output_pair(output_dir, reviewer_name)` | Output directory + reviewer name | Both `.json` and `.md` exist, delegates to json + markdown graders, reviewer name matches |
| `grade_review_baseline(path)` | Path to `.branch-review-baseline.json` | File exists, valid JSON, required fields (`last_reviewed_sha`, `last_reviewed_at`, `review_type`, `review_count`, `base_ref`, `git_range_used`), SHA format (7-40 hex), positive review_count, range contains `..` |

### Agent Compliance Grading (`grading/eval_agent_compliance.py`)

Offline grading tool for review output files — not part of the pytest suite.

- **`--grade-only /path/to/run`** — Scans an existing run directory for finalized `reviewers/<reviewer>/review.json` files and grades each JSON/Markdown pair, materializing the derived Markdown from canonical JSON first. Fast, no model calls. Use after a real review run to validate agent output format.

### Detection Benchmark (live model calls)

Beyond protocol compliance, dispatch mode scores **detection quality** against
per-scenario answer keys (`SCENARIOS[...]["expected"]` in
`grading/eval_agent_compliance.py`). Keys assert required findings (recall),
acceptable secondary findings (never punished), severity ceilings and verdict
sets (precision), and `expect_not_applicable` (correct abstention). Clean-code
scenarios (`php_clean_review`, `js_clean_review`) are pure false-positive
probes.

**Answer-key fields:**

| Field | Gates |
|---|---|
| `required_findings` | Each spec must be matched by some finding (recall) — a miss fails the entry. |
| `acceptable_findings` | Secondary findings the key pre-declares; matching them never punishes or rewards the entry, and they are excluded from `max_unexpected`. |
| `max_severity` | False-positive precision cap: no finding may rank above this severity — the gate the clean-code probes rely on. |
| `max_unexpected` | Precision cap on how many findings are entirely unpredicted (`match["unexpected"]`) — contrast `max_severity`'s cap on how severe findings are. |
| `verdict_in` | The reviewer's verdict must be one of the listed values — derive from the agent's auto-verdict rules, not intuition (see below). |
| `expect_not_applicable` | Abstention keys: accepts `not_applicable` or `approve`, each with zero findings. Mutually exclusive with every other field in this table — see "Abstention keys" below. |

**`max_unexpected` is implemented but unused.** `grade_detection()` in
`helpers/graders.py` gates on it when present, `grading/test_graders.py`
tests it, and the answer-key guard (`grading/test_answer_keys.py`) validates
it — but no scenario currently sets it. It exists for a future key that
needs to bound total noise, not just its ceiling.

```bash
# Single benchmark run for one scenario
python3 tests/grading/eval_agent_compliance.py --dispatch --scenario standard_review

# Restrict to one agent of a scenario
python3 tests/grading/eval_agent_compliance.py --dispatch --scenario realistic_multi_file --agent php-tests-reviewer

# Nondeterminism-controlled benchmark: 3 dispatches, majority must pass outright
python3 tests/grading/eval_agent_compliance.py --dispatch --scenario standard_review --trials 3

# Structured report for cross-version comparison
python3 tests/grading/eval_agent_compliance.py --dispatch --report-out "$TMPDIR/detection-report.json"
```

**Dispatch identity.** Each dispatch runs `claude -p
--dangerously-skip-permissions --setting-sources project --plugin-dir <shim>
--agent pirategoat-tools:<name> --output-format json` — the session IS the
reviewer, so the canonical `agents/<name>.md` is its system prompt and its
full frontmatter contract (model, effort, tools) is applied natively by the
host with no re-encoding, and there is no orchestrating parent whose
artifacts could be misattributed to the agent. The reviewer runs bootstrap
itself, mirroring the production step 6 subagent prompt. The `<shim>` is a
per-process tempdir with a minimal plugin manifest and a symlink to the
WORKTREE `agents/` (`ensure_plugin_shim`) — the plugin directory itself
carries no manifest, so pointing `--plugin-dir` at it resolves nothing and
the user-scope INSTALLED plugin would silently answer instead
(sentinel-verified). `--setting-sources project` excludes that installed
copy plus user hooks and memory, making runs machine-independent. Model
routing is pinned to `agent_registry.json` (the single source of truth) at
three layers: `check_model_routing` refuses to dispatch when frontmatter
drifts from the registry tier, a `TestDispatchIdentity` guard runs that same
check against every registered agent's canonical definition, and each run's
JSON `modelUsage` is verified post-hoc — the PRIMARY model sums only `inputTokens`,
`outputTokens`, `cacheReadInputTokens`, and `cacheCreationInputTokens`, then
resolves `canonicalModel` before registry-tier validation (`modelUsage` is a
session accumulator that includes auxiliary calls). Any nonzero dispatch exit —
model mismatch, session error, timeout, non-JSON output — fails the entry
before grading (`dispatch_rejected` in its detail), because the reviewer
may have written a plausible artifact before the rejection surfaced. A run
that graded a bare-bootstrap generic session, or an unrepresentative model,
would measure the wrong instrument.

**Authoring answer keys: derive from the agent's doctrine, not intuition.**
The dispatched agent's `.md` states explicit severity doctrines (e.g.
performance-reviewer: missing `LIMIT` in raw queries is CRITICAL;
wp-architecture-reviewer: unprefixed global classes are CRITICAL). A key's
`verdict_in` and finding specs must be derived from what the *configured
reviewer* mandates for the fixture content — read the definition before
keying, and cite the doctrine in a key comment. A key written from generic
reviewer intuition can reject the agent's correct behavior (mandated `block`
not in `verdict_in`) or reward a miss (a required spec whose `match_any`
accepts a mere source token, e.g. `\$_GET`, lets an access-control finding
satisfy an injection spec — require technique/sink evidence). When the
doctrine mandates a severity class (SQL injection/XSS are CRITICAL for
security-reviewer), set `min_severity` on the required spec and derive
`verdict_in` from the builder's auto-verdict (any critical → `block`) — an
under-classified finding is a calibration miss the benchmark must measure,
not a match. Whenever a fixture, key, or agent definition changes, re-walk
this derivation.

Every finding spec with `min_severity` must also declare a
`severity_basis` and a non-empty `rationale`. The allowed bases are
`doctrine`, when the floor equals the configured reviewer's severity for
the defect class, and `evidence_capped`, when the available inputs cannot
support doctrine's higher classification; the rationale cites the doctrine
or names the missing proof and why the fixture withholds it. A floor below
doctrine requires an evidentiary reason (what the reviewer could not prove
from the given inputs), never an observational one (what the model happened
to output). The offline answer-key guard rejects missing or unknown bases
and empty rationales. This requirement applies only to `min_severity`;
`max_severity` remains a false-positive precision cap on clean-code probes.

**Changing any `min_severity` requires a dispatch run of the affected
scenario before the change is trusted.** The offline guard proves only that
a floor is a valid severity name — it cannot tell you the floor is the right
one. Two floors on `realistic_multi_file` were once raised on doctrine
readings that stretched their buckets, passed the whole offline suite, and
were falsified by one dispatch run each: the reviewer reported both findings
in 3/3 trials at the original severity, and the doctrine text did not in fact
cover the raised classification. Read the bucket's enumerated members, not
its title, and when a floor is deliberately *not* the adjacent bucket, say so
in the rationale — a negative claim ("NOT False Confidence: the assertion
exists and nothing is mocked") is the part a future reader cannot reconstruct.

Grading is deterministic (file + line-window + keyword regexes over
title/description/category — no model judge). A correct finding the patterns
miss shows up under `match.unexpected` in the report with its location and
the matcher-visible fields (title, category, truncated description); widen
that spec's `match_any` from what the reviewer actually wrote. Each detection
detail also records `output_dir` — the per-dispatch artifact directory
(review JSON, dispatch transcript) — so multi-trial misses are traceable to
their trial. Keys are
validated against their fixtures by `tests/grading/test_answer_keys.py` (pure
pytest, no model calls): files must exist in the diff, lines must be in range,
regexes must compile, fixtures must apply, and every key needs at least one
gate. When editing a fixture or key, run that guard first.

**Multi-trial semantics.** `--trials N` re-dispatches each *keyed* agent N
times (unkeyed agents always run once; the `Running:` line prints once, so
re-dispatches are silent). The aggregate passes when a strict majority of
trials (`N // 2 + 1`) passed outright — so `--trials 2` demands both trials
pass. There are no per-check votes: an outright majority implies a per-check
majority for every check (the same passing trials passed each one), and
per-trial diagnostics live in `per_trial_failures`. An unreadable or raising
trial is simply a failed trial. The aggregate is a single check, so its check
counts are not comparable with single-trial check counts — the comparative
metric remains per-entry `passed`.

**Abstention keys.** `expect_not_applicable` accepts BOTH `not_applicable`
and `approve` verdicts (each with zero findings): the shared reviewer
protocol mandates `mark_not_applicable` on `NO_DOMAIN_FILES` while the
tests-reviewer agent definitions instruct APPROVE on the same status — a
live doctrine conflict in the plugin's own definitions. Until that is
reconciled, punishing either compliant reading would grade a documentation
inconsistency, not reviewer quality. `expect_not_applicable` is also
mutually exclusive with every other answer-key field: `grade_detection`
short-circuits on it before `match_findings` runs, so the other fields
would be silently inert beside it, and the answer-key guard
(`grading/test_answer_keys.py`) rejects a key that combines it with any of
them.

**Report shape** (`--report-out`, dispatch mode only): top-level `mode`,
`trials` (the requested count), and `results[]` with `scenario`, `agent`,
`trials` (trial attempts run for this entry), `keyed` (whether an answer
key exists), `status`, `passed`, `checks_run`, `checks_passed`, `failures`,
`detail`.

`status` is the explicit per-entry outcome, stamped by the code path that
knows what happened — never inferred from evidence shape (the
`ENTRY_STATUSES` constant pins the vocabulary): `graded` (live run produced
a graded artifact), `bootstrap_only` (deterministic entry, no model call by
design — the no-domain-files and error-exit scenarios), pre-dispatch
refusals/failures (`agent_missing`, `routing_drift`, `bootstrap_failed`),
dispatch failures (`cli_missing`, `timed_out`, `dispatch_error`,
`model_mismatch`), `harness_error`, and — aggregates only — `degraded`
(not every trial reached `graded`; see `detail.per_trial_status`).
Reviewer-behavior pass rates filter on `status == "graded"`. `timed_out`
means model calls likely occurred (money spent) but produced no gradable
evidence — it is deliberately not conflated with never-dispatched. Status
describes gradability, not spend: a trial that dispatched and was then
rejected is not a graded trial.

The `detail` shapes follow the entry: single-run graded entries carry
`{verdict, match, gates, compliance_passed, output_dir, models, status}`
(abstention keys carry `finding_count` and `match: null`, and no `gates`
key — discriminate on `gates`, not on `match` presence; unkeyed
entries carry compliance detail plus `{output_dir, status}`); rejected
dispatches carry `{dispatch_rejected, dispatch_evidence, output_dir,
status}`; aggregates (result `trials` above 1) carry `{trials, per_trial,
per_trial_failures, per_trial_passed, per_trial_status, models}`. Exit
codes: 2 for any configuration error (unknown scenario, empty selection,
invalid flags, unwritable report path — always before artifacts exist),
1 when the eval ran and at least one entry failed, 0 on full pass. The
comparative metric is per-entry `passed` (and detection detail) — check
counts and ratios are per-entry diagnostics only, because compliance adds
checks per schema-valid finding and would score a more verbose reviewer
higher for identical detection performance.

## Which tests to run

The full plugin suite runs in about a minute (`pytest plugins/pirategoat-tools/tests/ -q` from the repository root), so running it whole is always acceptable. This table is for focused iteration: it names the suites a change is most likely to break and, in parentheses, the coupling that makes a distant suite relevant.

| Changed file | Run |
|---|---|
| Any `AGENTS.md` or `CLAUDE.md` in the repository | `pytest plugins/pirategoat-tools/tests/test_instruction_budget.py -v` (byte ceilings per file and for the root-plus-plugin chain, and the `@AGENTS.md` shim contract) |
| `scripts/review/agent/bootstrap.py` | `pytest plugins/pirategoat-tools/tests/review/agent/test_bootstrap.py plugins/pirategoat-tools/tests/review/agent/test_bootstrap_integration.py -v` |
| `agents/shared/reviewer-protocol.md` | `pytest plugins/pirategoat-tools/tests/review/agent/test_bootstrap_integration.py -v` |
| `agents/shared/tests-reviewer-protocol.md` | `pytest plugins/pirategoat-tools/tests/review/agent/test_bootstrap_integration.py -v` |
| `scripts/review/pipeline.py` | `pytest plugins/pirategoat-tools/tests/review/test_pipeline_infra.py plugins/pirategoat-tools/tests/review/test_pipeline.py -v` (the facade's two mirrored import blocks re-export briefing names such as `DISPATCH_PROMPT_LEAD` that `test_pipeline.py` pins through it; a briefing constant a test needs must be added to both blocks) |
| `scripts/review/pipeline_contract.py` | `pytest plugins/pirategoat-tools/tests/review/test_pipeline.py plugins/pirategoat-tools/tests/review/test_pipeline_infra.py plugins/pirategoat-tools/tests/review/test_pipeline_integration.py -v` |
| `scripts/review/run_paths.py` | `pytest plugins/pirategoat-tools/tests/review/test_run_paths.py plugins/pirategoat-tools/tests/review/test_telemetry.py plugins/pirategoat-tools/tests/review/test_telemetry_share.py plugins/pirategoat-tools/tests/analysis/test_review_run_metrics.py -v` (`telemetry_log_path` is shared by the telemetry producer and the uploader; `SAFE_RUN_ID_SEGMENT_RE` by the uploader and the shared-clone reader) |
| `scripts/review/briefings.py` | `pytest plugins/pirategoat-tools/tests/review/test_pipeline.py -v` |
| `scripts/review/orchestration.py` | `pytest plugins/pirategoat-tools/tests/review/test_pipeline_integration.py plugins/pirategoat-tools/tests/review/test_orchestration_hygiene.py plugins/pirategoat-tools/tests/review/test_critic_adjustments.py plugins/pirategoat-tools/tests/review/test_step_11.py plugins/pirategoat-tools/tests/review/test_synthesis_lifecycle.py plugins/pirategoat-tools/tests/review/test_report_assembly.py -v` (hygiene covers the step-3 baseline / step-11 sweep and the step-11 usage capture; critic-adjustments covers step 11's adjudication-state inspection and verdict sync; step_11 covers derived-verdict, critic-absence, adjudication-state, and findings-Markdown re-render; synthesis-lifecycle covers the step-8/10 dispatch markers and the step-9/11 observations; report-assembly covers the step-9/11 `review-record.md` assembly seams; hygiene also covers the step-9 reconciliation verification and the step-11 critic-prose path listing) |
| `scripts/review/orchestration.py` review-record assembler (`assemble_review_record`, `_render_run_notes`, `_render_record_verdict_line`) or `render_review_body` in `scripts/review/review_markdown.py` | `pytest plugins/pirategoat-tools/tests/review/test_report_assembly.py plugins/pirategoat-tools/tests/review/test_review_markdown.py -v` (the record's shared body IS `render_review_body`, so a change to either lands in both) |
| `scripts/review/dispatch_adjust.py` | `pytest plugins/pirategoat-tools/tests/review/test_dispatch_adjust.py plugins/pirategoat-tools/tests/review/test_pipeline.py plugins/pirategoat-tools/tests/review/test_import_graph.py -v` (the step-5 briefing names the command and the step-6 briefing repeats what it recorded, reading the `planner_status` the CLI stamps) |
| `scripts/review/dispatch_status.py` | `pytest plugins/pirategoat-tools/tests/review/test_agents_status.py plugins/pirategoat-tools/tests/review/test_pipeline.py plugins/pirategoat-tools/tests/review/test_pipeline_infra.py plugins/pirategoat-tools/tests/review/test_pipeline_integration.py plugins/pirategoat-tools/tests/review/test_plan_dispatch.py plugins/pirategoat-tools/tests/review/test_telemetry.py plugins/pirategoat-tools/tests/analysis/test_review_run_metrics.py plugins/pirategoat-tools/tests/review/test_review_run_fixtures.py plugins/pirategoat-tools/tests/review/test_evidence_manifest.py plugins/pirategoat-tools/tests/review/test_dispatch_adjust.py plugins/pirategoat-tools/tests/review/test_orchestration_hygiene.py -v` (`load_dispatch_plan` is the one plan reader: orchestration, agents_status, telemetry, the evidence manifest and dispatch_adjust all open the plan through it) |
| `scripts/review/evidence_manifest.py` | `pytest plugins/pirategoat-tools/tests/review/test_evidence_manifest.py plugins/pirategoat-tools/tests/review/test_telemetry.py plugins/pirategoat-tools/tests/review/test_telemetry_share.py plugins/pirategoat-tools/tests/analysis/test_review_run_metrics.py plugins/pirategoat-tools/tests/review/test_review_run_fixtures.py plugins/pirategoat-tools/tests/review/test_import_graph.py -v` |
| `scripts/containment.py` | `pytest plugins/pirategoat-tools/tests/test_containment_contract.py plugins/pirategoat-tools/tests/hosts/ plugins/pirategoat-tools/tests/review/test_review_config.py plugins/pirategoat-tools/tests/review/test_telemetry.py -v` |
| `scripts/hosts/**/*.py` | `pytest plugins/pirategoat-tools/tests/hosts/ plugins/pirategoat-tools/tests/review/test_context.py plugins/pirategoat-tools/tests/review/test_bootstrap_host_injection.py -v` (the chain, its resolvers, the scan roots, the cache manager, the header leaf and the path identity reader; `context.py` runs the chain in-process and bootstrap renders its manifest) |
| `scripts/hosts/cache/manager.py` (`KNOWN_ECOSYSTEM_REPOS`, `slot_identity`) | `pytest plugins/pirategoat-tools/tests/hosts/cache/ plugins/pirategoat-tools/tests/hosts/resolvers/test_ecosystem_cache.py plugins/pirategoat-tools/tests/hosts/resolvers/test_plugin_headers.py plugins/pirategoat-tools/tests/hosts/test_ecosystem_cache_cli.py -v` (`KNOWN_ECOSYSTEM_NAMES` is read by the plugin-headers and cache resolvers; the identity is what every consumer renders) |
| `scripts/review/atomic_io.py` | `pytest plugins/pirategoat-tools/tests/review/test_atomic_io.py -v` |
| `scripts/review/plan_dispatch.py` | `pytest plugins/pirategoat-tools/tests/review/test_plan_dispatch.py plugins/pirategoat-tools/tests/review/test_criteria_coverage.py plugins/pirategoat-tools/tests/review/test_triage_run_regressions.py -v` (the regressions file replays the three audited runs, so a keyword or hygiene change shows its effect on real PRs before an audit has to find it) |
| `scripts/review/triage_sources.py` | `pytest plugins/pirategoat-tools/tests/review/test_triage_sources.py plugins/pirategoat-tools/tests/review/test_plan_dispatch.py plugins/pirategoat-tools/tests/review/test_triage_run_regressions.py plugins/pirategoat-tools/tests/review/test_import_graph.py -v` (the planner's only prose-cleaning seam: `get_commit_messages` and `_build_pr_text` call it, the three audited-run fixtures replay through it, and it is a documented leaf of the import graph) |
| `scripts/review/change_purpose.py` | `pytest plugins/pirategoat-tools/tests/review/test_change_purpose.py plugins/pirategoat-tools/tests/review/test_pipeline.py plugins/pirategoat-tools/tests/review/test_pipeline_integration.py plugins/pirategoat-tools/tests/review/agent/test_bootstrap.py plugins/pirategoat-tools/tests/review/test_reconciliation_context.py plugins/pirategoat-tools/tests/review/test_report_assembly.py plugins/pirategoat-tools/tests/review/test_evidence_manifest.py plugins/pirategoat-tools/tests/review/test_import_graph.py -v` (the one parser behind the step-5 warnings, bootstrap's REVIEW FOCUS tiers and PR INTENT pointer, the reconciliation context's `verify_items`, and the record's Verify-items table; `ledger_citations` is the record's and the evidence manifest's one reader of checks and confirmed notes that cite an item; a documented leaf of the import graph) |
| `scripts/review/agent_registry.json` (triage criteria/keywords/checks) | `pytest plugins/pirategoat-tools/tests/review/test_criteria_coverage.py plugins/pirategoat-tools/tests/review/test_plan_dispatch.py plugins/pirategoat-tools/tests/review/test_triage_run_regressions.py -v` (every criterion bullet needs a dispatching probe; a keyword is a whole word unless it ends in *) |
| `plugins/pirategoat-tools/AGENTS.md` agent-registry reference (`model_tier` row) or `scripts/review/agent_registry.json` `model_tier` values | `pytest plugins/pirategoat-tools/tests/review/test_registry_docs.py -v` |
| `scripts/review/context.py` | `pytest plugins/pirategoat-tools/tests/review/test_context.py -v` |
| `scripts/review/dependency_refresh.py` | `pytest plugins/pirategoat-tools/tests/review/test_dependency_refresh.py -v` |
| `scripts/review/user_settings.py` | `pytest plugins/pirategoat-tools/tests/review/test_user_settings.py plugins/pirategoat-tools/tests/review/test_telemetry_share.py plugins/pirategoat-tools/tests/review/test_pipeline_infra.py plugins/pirategoat-tools/tests/review/test_import_graph.py -v` |
| `scripts/review/telemetry_share.py` | `pytest plugins/pirategoat-tools/tests/review/test_telemetry_share.py plugins/pirategoat-tools/tests/review/test_telemetry.py plugins/pirategoat-tools/tests/review/test_pipeline.py plugins/pirategoat-tools/tests/review/test_pipeline_integration.py plugins/pirategoat-tools/tests/analysis/test_review_run_metrics.py -v` (four importers: `telemetry.py` takes `repo_identity`, `pipeline.py` the upload seam and `UNOPTED_OUTCOMES`, `briefings.py` the consent disclosure, and the metrics contracts `LAYOUT_PREFIX`; the consent disclosure names the path-free host-context data) |
| `scripts/review/reconciliation_context.py` | `pytest plugins/pirategoat-tools/tests/review/test_reconciliation_context.py plugins/pirategoat-tools/tests/review/test_reconciliation_notes.py plugins/pirategoat-tools/tests/review/test_findings_save.py plugins/pirategoat-tools/tests/review/test_report_assembly.py plugins/pirategoat-tools/tests/review/test_pipeline_integration.py plugins/pirategoat-tools/tests/review/test_file_review.py -v` (reconciliation_notes.py and findings_save.py share `RECONCILIATION_CONTEXT_SCHEMA` and `validate_orchestrator_notes`, and a rebuild must preserve the claims already registered — `TestRegisteredNotesSurviveARebuild`; report-assembly covers `strip_severity_floor_markers`; pipeline-integration's `TestStep9CoverageMeasurement` and file-review cover `manifest_sections.aggregate_file_review`, which this module's callers still cross. The reconciliation context reads the complete local host map from `review-context.json` so host-qualified citations can be verified without argv transport or telemetry exposure). Changing `compute_missing_agents` or `annotate_prefiltered_findings` also means re-reading `agents/review-reconciliator.md`: the agent carries those measurements rather than recomputing them, so the contract and the computation are one change. |
| `scripts/review/reconciliation_notes.py` | `pytest plugins/pirategoat-tools/tests/review/test_reconciliation_notes.py plugins/pirategoat-tools/tests/review/test_findings_save.py -v` (the notes it writes are what the save gate requires outcomes for) |
| `scripts/review/agent/review_assignment.py` | `pytest plugins/pirategoat-tools/tests/review/agent/test_review_assignment.py plugins/pirategoat-tools/tests/review/agent/test_bootstrap_integration.py -v` |
| `scripts/review/findings_ledger.py` | `pytest plugins/pirategoat-tools/tests/review/test_findings_ledger.py plugins/pirategoat-tools/tests/review/test_critic_adjustments.py plugins/pirategoat-tools/tests/review/test_findings_save.py plugins/pirategoat-tools/tests/review/test_reconciliation_notes.py -v` (`read_reconciliation_context` is the one reader of the context for the save gate, the notes CLI and the builder; findings_save.py imports `RECONCILIATION_PIPELINE_FIELDS`, `DROP_REASONS_FINDING` and `DROP_REASONS_CHECK`, and names an unknown drop reason in its own rejection; the reader-boundary validators in critic_adjustments.py import the provenance constants; the ledger's `sources` grammar — `normalized_sources` — is what critic_adjustments.py validates ledger provenance with) |
| `scripts/review/reviewer_names.py` | `pytest plugins/pirategoat-tools/tests/review/test_reviewer_names.py plugins/pirategoat-tools/tests/review/agent/test_bootstrap_integration.py plugins/pirategoat-tools/tests/review/test_agents_status.py plugins/pirategoat-tools/tests/review/test_telemetry.py plugins/pirategoat-tools/tests/review/test_telemetry_share.py plugins/pirategoat-tools/tests/analysis/test_review_run_metrics.py -v` (`test_reviewer_names.py` pins the inverse derivation, `agent_name_from_review_stem`, directly; bootstrap pins the forward derivation, `derive_reviewer_name`, across every registered agent; `telemetry.py`, `telemetry_share.py`, and the metrics contracts import `agent_name_from_review_stem` to project ledger stems to registry names, and agents-status exercises `derive_reviewer_name` through `agents_status.py` and `manifest_sections.py`'s assignment builders) |
| `scripts/review/agents_status.py` | `pytest plugins/pirategoat-tools/tests/review/test_agents_status.py -v` |
| `scripts/review/agent/scope.py` | `pytest plugins/pirategoat-tools/tests/review/agent/test_scope.py plugins/pirategoat-tools/tests/review/agent/test_scope_routing.py plugins/pirategoat-tools/tests/review/test_plan_dispatch.py plugins/pirategoat-tools/tests/review/test_criteria_coverage.py -v` (`CHANGELOG_FRAGMENT_PATTERN` is shared with `plan_dispatch._has_documentation_files`, so run `test_plan_dispatch.py` and `test_criteria_coverage.py` too) |
| `scripts/review/agent/diff_noise_filter.py` | `pytest plugins/pirategoat-tools/tests/review/agent/test_diff_noise_filter.py -v` |
| `scripts/review/agent/output.py` | `pytest plugins/pirategoat-tools/tests/review/agent/test_output.py plugins/pirategoat-tools/tests/grading/test_graders.py -v` |
| `scripts/review/review_document.py` | `pytest plugins/pirategoat-tools/tests/review/test_review_document.py plugins/pirategoat-tools/tests/review/agent/test_output.py plugins/pirategoat-tools/tests/review/test_critic_adjustments.py plugins/pirategoat-tools/tests/grading/test_graders.py plugins/pirategoat-tools/tests/review/test_change_purpose.py -v` (`validate_review_document`'s own rejection branches, plus both of its consumer boundaries — reviewer publication and ledger adjudication; owns `VERIFY_ITEM_ID_RE`, the grammar of a check's optional `verifies` list, `HOST_CITATION_RE` and `cited_hosts`, the one parser of a host citation that the evidence manifest counts with, and `normalize_bounded_text`, the bounded-prose rule every ledger evidence, orchestrator note and dispatch-adjustment reason passes through — `dispatch_adjust.py` and `reconciliation_notes.py` import it, so `test_dispatch_adjust.py`'s and `test_reconciliation_notes.py`'s refusal fragments pin its message) |
| `scripts/review/review_markdown.py` | `pytest plugins/pirategoat-tools/tests/review/test_review_markdown.py plugins/pirategoat-tools/tests/review/test_report_assembly.py -v` (the renderer's own suite plus the review-record assembler that shares `render_review_body`; rendered findings preserve `source_cited` upstream evidence) |
| Any `scripts/review/**/*.py` import block, module-level or inside a function body | `pytest plugins/pirategoat-tools/tests/review/test_import_graph.py -v` (asserts the package's whole import graph — a depth-first walk of every module-level edge finds no cycle, the five documented leaf modules stay leaves, two layering directions an acyclic graph cannot expose stay one-way, and no undocumented function-body import exists; `review_document.py`'s, `review_markdown.py`'s, `critic_adjustments.py`'s, and `agent/output.py`'s rows above rely on this test for their import-graph invariants. `importlib.util.spec_from_file_location` loaders are invisible to it — `pipeline.py`, `agent/scope.py`, and `context.py` load modules that way, so those three are untracked here) |
| `scripts/review/telemetry.py` | `pytest plugins/pirategoat-tools/tests/review/test_telemetry.py plugins/pirategoat-tools/tests/review/test_telemetry_share.py plugins/pirategoat-tools/tests/analysis/test_review_run_metrics.py plugins/pirategoat-tools/tests/review/test_review_run_fixtures.py -v` |
| `scripts/review/synthesis_lifecycle.py` | `pytest plugins/pirategoat-tools/tests/review/test_synthesis_lifecycle.py -v` (the module plus its four orchestration seams) |
| `scripts/review/manifest_sections.py` | `pytest plugins/pirategoat-tools/tests/review/test_telemetry.py plugins/pirategoat-tools/tests/review/test_file_review.py plugins/pirategoat-tools/tests/review/test_report_assembly.py plugins/pirategoat-tools/tests/review/test_pipeline_integration.py plugins/pirategoat-tools/tests/review/test_review_run_fixtures.py -v` (`aggregate_file_review` also measures `noise_filtered_files` from the dispatch plan's reviewable list; `summarize_host_context` is the one projection behind the record's host line and telemetry's `host_context` section; `describe_reconciliation_verification` is pinned through the step-9/10 briefings in `test_pipeline.py` and the record in `test_report_assembly.py`) |
| `scripts/review/critic.py` | `pytest plugins/pirategoat-tools/tests/review/test_critic.py -v` |
| `scripts/review/critic_adjustments.py` | `pytest plugins/pirategoat-tools/tests/review/test_critic_adjustments.py plugins/pirategoat-tools/tests/review/test_step_11.py plugins/pirategoat-tools/tests/review/test_review_markdown.py plugins/pirategoat-tools/tests/review/test_findings_ledger.py -v` (the renderer shows `invalidated_recommendations`; the ledger tests round-trip provenance through the reader; step_11 recomputes the published verdict from a live ledger) |
| `scripts/review/verdict_rules.py` | `pytest plugins/pirategoat-tools/tests/review/test_verdict_rules.py plugins/pirategoat-tools/tests/review/agent/test_output.py plugins/pirategoat-tools/tests/review/test_step_11.py -v` (the shared ladder plus both callers — output.py publishing a review and step 11 recomputing the ledger verdict) |
| `scripts/review/findings_save.py` | `pytest plugins/pirategoat-tools/tests/review/test_findings_save.py plugins/pirategoat-tools/tests/review/test_findings_ledger.py -v` (the accounting gate compares against the ledger builder's provenance vocabulary — `DROP_REASONS_*`, `NOTE_OUTCOMES`, `SOURCE_ENTRY_FIELDS` — and against reconciliation_context.py's schema constant, and enforces a merged check's `verifies` union) |
| `scripts/review/workspace_setup.py` | `pytest plugins/pirategoat-tools/tests/review/test_workspace_setup.py -v` |
| `scripts/linear/pipeline.py` (routing, state, CLI) | `pytest plugins/pirategoat-tools/tests/linear/test_pipeline.py -v` |
| `scripts/linear/pipeline.py` (briefing text) | `pytest plugins/pirategoat-tools/tests/linear/test_pipeline_guidance.py -v` |
| `scripts/linear/events.py` | `pytest plugins/pirategoat-tools/tests/linear/test_events.py -v` |
| `scripts/iterative_review/__main__.py` | `pytest plugins/pirategoat-tools/tests/iterative_review/test_cli.py -v` |
| `scripts/iterative_review/briefing.py` | `pytest plugins/pirategoat-tools/tests/iterative_review/test_briefing.py -v` |
| `scripts/iterative_review/backends/codex.py` | `pytest plugins/pirategoat-tools/tests/iterative_review/test_codex.py -v` |
| `scripts/iterative_review/backends/claude.py` | `pytest plugins/pirategoat-tools/tests/iterative_review/test_claude.py -v` |
| `scripts/iterative_review/loop.py` | `pytest plugins/pirategoat-tools/tests/iterative_review/test_loop.py -v` |
| `scripts/iterative_review/effort.py` | `pytest plugins/pirategoat-tools/tests/iterative_review/test_effort.py -v` |
| `scripts/iterative_review/*.py` (other / multiple) | `pytest plugins/pirategoat-tools/tests/iterative_review/ -v` |
| `scripts/analysis/session_metrics.py` | `pytest plugins/pirategoat-tools/tests/analysis/test_session_metrics.py plugins/pirategoat-tools/tests/analysis/test_review_transcript.py -v` (agent metadata is authoritative before transcript inference, and both callers use the public one-parser usage wrapper) |
| `scripts/analysis/session_analyzer.py` | `pytest plugins/pirategoat-tools/tests/analysis/test_session_analyzer.py -v` |
| `scripts/analysis/review_transcript.py` | `pytest plugins/pirategoat-tools/tests/analysis/test_review_transcript.py plugins/pirategoat-tools/tests/analysis/test_session_metrics.py -v` (`usage_summary_for_transcript()` is the public one-parser token wrapper; agent_usage rows carry `repository_reads`, which usage_snapshot.py and step 9 consume) |
| `scripts/analysis/review_run_metrics.py` or `scripts/analysis/review_metrics/*.py` | `pytest plugins/pirategoat-tools/tests/analysis/test_review_run_metrics.py plugins/pirategoat-tools/tests/review/test_review_run_fixtures.py -v` |
| `scripts/analysis/codex_rollout.py` | `pytest plugins/pirategoat-tools/tests/analysis/test_codex_rollout.py -v` |
| `scripts/analysis/codex_session_analyzer.py` or `scripts/analysis/codex_session_metrics.py` | `pytest plugins/pirategoat-tools/tests/analysis/test_codex_session_scripts.py -v` |
| `scripts/analysis/usage_snapshot.py` | `pytest plugins/pirategoat-tools/tests/analysis/test_usage_snapshot.py plugins/pirategoat-tools/tests/review/test_orchestration_hygiene.py plugins/pirategoat-tools/tests/review/test_telemetry.py -v` (the CLI, the step-11 seam that invokes it, and `ReviewTelemetry.reproject_usage()` — the manifest's own out-of-band `usage` patch a manual re-run calls into; step 9's reconciliation-verification seam calls it with `--stdout`) |
| `tests/helpers/graders.py` | `pytest plugins/pirategoat-tools/tests/grading/test_graders.py -v` |
| `tests/helpers/triage_run_fixture.py` or `tests/fixtures/triage-runs/*.json` | `pytest plugins/pirategoat-tools/tests/review/test_triage_run_regressions.py -v` (never edit a fixture by hand — re-capture it with the helper's CLI from a clone that holds the range; the integrity tests pin file counts, patch coverage and the absence of session URLs) |
| `tests/helpers/review_run_fixture.py` or `tests/fixtures/review-runs/**` | `pytest plugins/pirategoat-tools/tests/review/test_review_run_fixtures.py -v` (never edit a fixture by hand — re-capture it through the helper from the source run; capture output is generated-only, privacy-redacted, and digest-bound) |
| `tests/helpers/critic_seeds.py` | `pytest plugins/pirategoat-tools/tests/review/test_critic_adjustments.py plugins/pirategoat-tools/tests/review/test_step_11.py -v` (both files build ledgers, proposals, and adjudications through these shared seed helpers rather than a per-file copy — import from here, never duplicate a helper's body into either test file) |
| `agents/history-insights-reviewer.md` | `pytest plugins/pirategoat-tools/tests/review/agent/test_history_insights_reviewer.py -v` |
| `tests/grading/eval_agent_compliance.py` | `pytest plugins/pirategoat-tools/tests/grading/test_eval_agent_compliance.py -v` |
| Any `SCENARIOS` answer key or `tests/fixtures/*.diff` | `pytest plugins/pirategoat-tools/tests/grading/test_answer_keys.py -v` |
| Any reviewer agent `.md` | `pytest plugins/pirategoat-tools/tests/review/agent/test_bootstrap_integration.py -v` (verifies agent config still works, and `TestEveryReviewerMandatesBootstrap` asserts the exact heading `## MANDATORY SETUP — Run Bootstrap Before Reviewing` plus a `bootstrap.py --agent <name>` line in every registry reviewer except those in `BOOTSTRAP_EXEMPT_AGENTS`; `agents/review-reconciliator.md` is also pinned by `tests/review/test_findings_ledger.py`'s snippet tests and `tests/review/test_critic_adjustments.py`) |
| New agent added to `AGENT_CONFIG` | `pytest plugins/pirategoat-tools/tests/review/agent/test_bootstrap_integration.py -v` (auto-included in all parameterized tests, including the mandatory-bootstrap heading check above) |
| Any review command `.md` | `pytest plugins/pirategoat-tools/tests/commands/test_commands.py -v` (validates structure, agent refs, script refs) |
| `commands/pr-update.md` | `pytest plugins/pirategoat-tools/tests/commands/test_commands.py -v` |
| `commands/switch-to.md` | `pytest plugins/pirategoat-tools/tests/commands/test_commands.py -v` |
| `.claude-plugin/marketplace.json` | `pytest plugins/pirategoat-tools/tests/commands/test_commands.py -v` (validates command registration, agent cross-refs) |
| Any marketplace entry, command, or dual-host adapter | `pytest plugins/pirategoat-tools/tests/test_codex_marketplace.py -v` |
| `scripts/generate_codex_compat.py` | `pytest plugins/pirategoat-tools/tests/test_codex_marketplace.py -v` |

## Design Principles

These principles guide all testing decisions. Follow them when adding or modifying tests.

### 1. Code-based graders, not model-based

All graders are deterministic Python functions. No LLM calls in the grading path. This keeps tests fast (see the header above for the current measured runtime), reproducible (same input = same result), and cheap (no API costs).

### 2. Grade outcomes, not paths

Tests verify what the output contains, not how it was produced. A test checks "the output has a `=== REVIEW RULES ===` section" not "the script called `extract_protocol_sections` with the right arguments." This makes tests resilient to refactoring.

### 3. Positive and negative cases

Every grader has tests for both:
- **Positive**: valid ReviewOutputBuilder output passes all checks
- **Negative**: missing fields fail, invalid values fail, empty files fail

### 4. Test the graders too

`grading/test_graders.py` validates that grading functions work correctly on synthetic inputs. This prevents false passes (grader too lenient) and false failures (grader too strict). A grader bug could silently undermine the entire eval system.

### 4a. Mutation-verify what a guard claims to pin

Trusting a test that "passed" is not the same as verifying it would fail if the behavior it claims to pin broke. This project mutation-verifies load-bearing guards — deliberately breaking the production code the test claims to cover and confirming the test goes red — before trusting a green run as evidence. Two failure modes from this session's own mutation passes are easy to reproduce if you skip the discipline below:

**Run mutation probes with `PYTHONDONTWRITEBYTECODE=1` and clear `__pycache__` before judging any failure.** A probe that edits production source leaves compiled bytecode behind; once the source is restored, a later test run can import the stale `.pyc` and fail (or pass) on code that no longer exists. This produced two independent phantom failures in one session — each costing a reviewer a debugging detour before the cause was found — and the failure it fabricates is indistinguishable from a real regression until the cache is cleared. Restore probes from `cp` backups (never `git checkout --`), export `PYTHONDONTWRITEBYTECODE=1` for the probe run, and `find … -name __pycache__ -exec rm -rf` before the verification rerun.

**Mutate each conjunct in isolation, not the predicate that contains it.** Mutating a compound condition as a whole (e.g. flipping `and` to `or`, or negating the whole expression) only proves that the mutation testing tool found *some* input where the test fails — and a short-circuiting boolean predicate fails on whichever operand it evaluates first, so a conjunct can be completely dead (never actually checked by any assertion) while the containing predicate's mutation still turns a test red for an unrelated reason. This session's `window_closed` conjunct was "verified" this way — the containing predicate's mutation failed a test, so the guard was marked covered — and `window_closed` itself was in fact unpinned; no assertion in the suite depended on its value. Mutate each conjunct of a compound condition on its own (flip just that one clause, leave the rest untouched) and confirm a *specific*, attributable test failure for each.

**A removal guard written from the writer's side is tautological — it must be broken from the reader's side.** When a field, key, or code path is deleted, the natural guard to write is "assert the writer no longer produces X." That guard can never fail once the writer has genuinely stopped writing X, regardless of whether anything downstream still tolerates, silently ignores, or mis-handles X's absence — it proves the deletion happened, not that removing it was safe. Mutate from the reader's side instead: reintroduce X at each site a prior writer used to emit it, one deletion site at a time, and confirm each reintroduction is independently caught. Testing all deletion sites by restoring them together only proves the union is caught, not that any individual site regressing alone would be. This session's projection-allowlist deletion was "verified" by a writer-side check that could never fail by construction — the reader-side reintroduction test was the one that actually mattered, and it had to be run once per deletion site to mean anything.

### 5. Skip-list resilience

Protocol extraction uses a skip-list (sections to exclude) rather than an include-list. New sections added to `reviewer-protocol.md` are automatically included in bootstrap output — and the test `test_new_section_auto_included` verifies this. If someone adds a section to the protocol, they don't need to update the bootstrap script or tests.

### 6. Parameterize on the axis of variation

Parameterize tests over the dimension that creates different code paths, not over the entire agent population.

**ALL_AGENTS parameterization is ONLY for smoke tests** — where each agent can independently fail due to config (bad domain, missing protocol file). The smoke test `TestSmokeAllAgents.test_exits_0` validates registry correctness.

**For everything else, use category representatives** — one agent per conditional path through the code. The five categories that cover all branches in `main()`:

| Category | Representative | Path covered |
|----------|---------------|-------------|
| Standard | `performance-reviewer` | Default path, no special flags |
| Test agent | `php-tests-reviewer` | `"tests-reviewer" in protocols` → DOMAIN RULES |
| Exploration | `patterns-reviewer` | `extra_scope` → EXPLORATION SCOPE |
| Null domain | `tests-mutation-reviewer` | `domain is None` → no scope discovery |
| History + override | `history-insights-reviewer` | `file_history` + `budget_override` |

**Adding a new agent?** It automatically appears in the smoke test. If it introduces a new conditional path (new protocol type, new flag), add it as a new category representative.

**Anti-patterns to avoid:**
- `@pytest.mark.parametrize("agent_name", ALL_AGENTS)` for template string assertions — the template doesn't vary by agent
- Parameterizing the "absent" case over all non-matching agents — 1-2 representatives suffice for an else-branch
- Testing `derive_reviewer_name()` output via subprocess for 21 agents — it's a pure function; if it works for 1, it works for all

### 7. Test at the right layer

If a function is importable and deterministic, test it as a unit test — don't invoke it via subprocess. Subprocess tests validate **orchestration**: "does `main()` correctly wire together plugin root discovery → protocol extraction → scope discovery → `build_output()`?" Unit tests validate **logic**: "does `build_output()` include DOMAIN RULES when `domain_rules` is not None?"

**Wrong layer:** Running a subprocess to verify that `"=== REVIEW RULES ===" in stdout` — this is a hardcoded string in `build_output()`, already covered by unit tests.

**Right layer:** Running a subprocess to verify that `returncode == 0` for every registered agent — this exercises the full `main()` orchestration path, which unit tests can't cover.

The `build_output()`-based test classes (`TestReviewOutputBuilderAPIExample`, `TestBootstrapOutputSizeCap`, `TestOutputFilenameConsistency`) demonstrate the right pattern: import the function, call it directly, assert on the result. Fast and focused.

`TestDynamicDispatchRisk` deliberately mixes both layers: its direct `build_output()` tests pin the rendering contract, and its three trailing subprocess tests pin `main()`'s own `has_php` derivation — a fact the direct tests structurally cannot reach, since they supply it as a parameter.

**When to keep subprocess tests:**
- Testing `sys.exit()` paths (CLI argument validation)
- Testing cross-process state persistence (e.g., marker files read by a separate process)
- Testing the full `main()` orchestration that can't be called directly (calls `sys.exit()`)
**When to replace subprocess with direct calls:**
- Config round-trip tests — call `write_config()` + `read_config()` directly instead of spawning two `pipeline.py` processes
- Context loading — call `load_and_fill()` directly
- Functions that need a git repo CWD — call `build_scope()` directly with `os.chdir()` (saves ~0.15s interpreter spawn per test while keeping real git behavior)
- Any test already covered by a unit test on the same function — delete the subprocess duplicate

**Example — config round-trip (from `TestStateManagement`):**
```python
# Wrong: two subprocess spawns to verify config persistence (~1.5s)
self._run("--step", "1", "--mode", "pr", "--output-dir", str(tmp_path), "--pr-number", "42", "--quick")
r = self._run("--step", "3", "--output-dir", str(tmp_path))
config = json.loads((tmp_path / "run-config.json").read_text())
assert config["quick"] is True

# Right: direct function calls (~0.001s)
mod.write_config(str(tmp_path), {"mode": "pr", "pr_number": "42", "interactive": True, "quick": True})
config = mod.read_config(str(tmp_path))
assert config["quick"] is True
```

**Example — git-dependent functions (from `TestMergeBaseGatingIntegration`):**
```python
# Wrong: subprocess spawn per test (~0.3s overhead on top of git ops)
result = subprocess.run([sys.executable, str(SCOPE_SCRIPT), "--domain", "code",
                         "--range", "main..HEAD", "--format", "json"], cwd=repo, ...)
data = json.loads(result.stdout)

# Right: direct call with os.chdir (~0.15s saved per test, real git behavior preserved)
saved_cwd = os.getcwd()
try:
    os.chdir(repo)
    scope = review_scope.build_scope(args)
finally:
    os.chdir(saved_cwd)
data = json.loads(review_scope.format_json_output(scope))
```

### 7a. Avoid `time.sleep()` — mock timestamps instead

`time.sleep()` adds real wall-clock delay to every test run. When tests need different timestamps (e.g., for filename uniqueness with 1-second resolution), mock `datetime.now()` instead.

**Pattern:** Subclass `datetime` (the C type can't be patched directly) and patch it on the module under test:

```python
from unittest.mock import patch

class FakeDatetime(datetime):
    _times = iter([...])
    @classmethod
    def now(cls, tz=None):
        return next(cls._times)

with patch.object(mod, "datetime", FakeDatetime):
    # Code that calls datetime.now() gets controlled timestamps
```

**Acceptable uses of `time.sleep()`:** Small sleeps (0.05s) to ensure measurable duration in tests that verify elapsed-time calculations (e.g., `TestLogStep.test_calculates_duration_since_prev`). These are testing that the code measures real time correctly, not just generating unique identifiers.

**Wrong:** `time.sleep(1.1)` to ensure 1-second-resolution timestamps differ (2.2s wasted per test pair).
**Right:** Mock `datetime.now()` to return timestamps 2 seconds apart (0.001s).

### 8. Tests read real protocol files

Integration tests run the actual bootstrap script against real `reviewer-protocol.md` and `tests-reviewer-protocol.md` files. This means tests catch heading drift (e.g., someone renames a section that the skip-list references).

### 8a. Pin the load-bearing token, not the sentence

A test that reads a briefing, an agent or protocol file, a command file or a CLI's stdout asserts the smallest thing the pipeline's other half depends on: a flag, a path, a marker word, an artifact name, a vocabulary value another script parses (`checks[].result`, `` `OPEN` ``, `Plugin scripts directory:`), or a value imported from production. It never pins a sentence of prose. Prose is rewritten whenever a briefing is improved, and a sentence pin turns every rewording into a test edit that protects nothing: the range that introduced this rule had to rewrite seven such pins for one wording change. When a behavior has no token — the instruction is the sentence — pin the negative that the change removed (`"If empty: STOP" not in content`) or the structural line the reader consumes, and leave the wording free.

### 9. Mock git repos, not the real repo

Integration tests that shell out to scripts (which run git commands) use temporary git repos created from `.diff` fixtures via `setup_temp_git_repo()` in `conftest.py`. This isolates tests from the real repository state — dirty working trees, recent commits, and branch structure don't affect results. The scripts resolve their plugin files via their own script path (`os.path.abspath(__file__)`), so changing `cwd` to a temp repo only affects git operations.

**Known latent risk — `tests/review/test_plan_dispatch.py`:** ~40 `build_dispatch_plan()` call sites run with no CWD control at all. This is verified INERT today (the unmocked sites assert only structural facts, not anything CWD-sensitive), but it is the exact same coupling shape a real pipeline test class once had that caused a deleted-user-files incident when a git-mutating call ran against a developer's real working tree. A future assertion added to this file without `monkeypatch.chdir` isolation — or a change that makes `build_dispatch_plan()` itself touch the filesystem beyond reading — can reintroduce that bug class. Isolate new assertions here the same way Principle 9 isolates everything else.

## How To

### Add a new reviewer agent

1. Add the agent to `scripts/review/agent_registry.json`
2. Create the agent `.md` file in `agents/`
3. Run `pytest plugins/pirategoat-tools/tests/review/agent/test_bootstrap_integration.py -v` — the `TestSmokeAllAgents` smoke test automatically picks up the new agent and validates it exits 0
4. If the agent introduces a **new conditional path** through `main()` (new protocol type, new flag like `file_history` or `extra_scope`), add a category representative test in `TestCategoryRepresentatives`
5. If the agent introduces a new domain, add it to `ALL_DOMAINS` in `review/agent/test_scope_routing.py` and update `ROUTING_MATRIX` for each fixture
6. **Do NOT** add `@pytest.mark.parametrize("agent_name", ALL_AGENTS)` tests for template assertions — the smoke test handles registry validation; category representatives handle conditional paths

### Add a new grader

1. Write the grading function in `helpers/graders.py` following the pattern:
   - Accept a path or text string
   - Build a list of `(condition, failure_message)` tuples
   - Return `_grade(checks)`
2. Add tests in `grading/test_graders.py` with at least one positive and one negative case
3. If the grader validates output files, use `ReviewOutputBuilder` from `scripts/review/agent/output.py` to create valid test fixtures

### Add a test for a new script

Follow the pattern in `review/agent/test_bootstrap.py`:

1. Use `importlib` to import from the hyphenated script filename
2. Write unit tests for pure functions with synthetic inputs (right layer — principle §7)
3. Write integration tests via subprocess only for orchestration paths that unit tests can't cover
4. Use `@pytest.mark.parametrize` only on the axis that creates variation (principle §6) — never ALL_AGENTS for invariant assertions
5. Derive `PLUGIN_ROOT` from the test file's location (no hardcoded paths)

### Add a new diff fixture

1. Create the `.diff` file in `tests/fixtures/`
2. **Use `new file mode 100644` with `--- /dev/null`** for all files in the diff. This ensures `git apply` works in a fresh temp repo (no context lines to match). Example:
   ```diff
   diff --git a/src/Example.php b/src/Example.php
   new file mode 100644
   --- /dev/null
   +++ b/src/Example.php
   @@ -0,0 +1,5 @@
   +<?php
   +class Example {
   +    // ...
   +}
   ```
3. Keep fixtures minimal — just enough to trigger the scenario
4. Add the fixture to `ROUTING_MATRIX` in `review/agent/test_scope_routing.py` with expected STATUS per domain
5. Run `pytest plugins/pirategoat-tools/tests/review/agent/test_scope_routing.py -v` to verify routing

## Conventions

### Importing from scripts/

Scripts are organized in domain packages (`review/`, `linear/`, `figma/`, `analysis/`). `conftest.py` adds `scripts/` to `sys.path`, so standard `from` imports work:

```python
from review.agent.output import ReviewOutputBuilder
from review.agent.scope import build_scope, filter_domain
```

For scripts with hyphenated filenames (not valid Python identifiers), use `importlib`:

```python
import importlib.util
_spec = importlib.util.spec_from_file_location("module_name", str(SCRIPTS_DIR / "review" / "agent" / "bootstrap.py"))
_mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_mod)
function_under_test = _mod.function_name
```

### Package namespace rule

**No directory under `plugins/*/tests/` may have an `__init__.py` file — not just the ones that mirror `scripts/` package names.**

`scripts/review/` is a Python package (has `__init__.py`). If `tests/review/` also has `__init__.py`, Python caches whichever `review` package it discovers first — making `from review.agent.output import ...` resolve to the wrong package depending on test execution order. That was the original, narrower reasoning. It has since been superseded by a repo-wide rule: this plugin is one of several under `plugins/*/tests/`, and an `__init__.py` at any `tests/` root makes that whole suite importable as the top-level package `tests` — the second plugin's suite then collides with the first's in the same pytest session (`ModuleNotFoundError`, or a conftest registered "under a different name"). `tests/helpers/` and `tests/commands/` have no same-named production package to shadow, but they still must not carry `__init__.py`, because the collision risk is repo-wide, not per-directory.

- `tests/review/` — NO `__init__.py` (would shadow `scripts/review/`)
- `tests/review/agent/` — NO `__init__.py` (same reason)
- `tests/helpers/`, `tests/commands/`, and every other subdirectory — NO `__init__.py` (repo-wide multi-plugin collision risk, not a per-directory judgment call)

`tests/test_pytest_layout.py::TestMultiPluginCollection` enforces this for the whole repo: it fails if any `__init__.py` exists under any `plugins/*/tests/` tree, and it pins the root `pytest.ini`'s `--import-mode=importlib` setting that makes namespace packages (no `__init__.py` required) work correctly for path-derived, cross-plugin-unique module names.

### Importing from helpers/

Shared test utilities live in `tests/helpers/`. `conftest.py` adds `tests/` to `sys.path` beside `scripts/`, and pytest loads it before any module under it, so a test module imports from `helpers/` directly — no per-file `sys.path.insert(0, str(TESTS_DIR))` is needed, and `test_pytest_layout.py::TestFocusedCollection` pins that a module importing `helpers` collects when named on its own. Older modules still carry the insert; it is redundant, not a convention to copy.

Import as normal:

```python
from helpers.graders import grade_review_json, grade_output_pair
from helpers.command_helpers import load_command, get_frontmatter
from helpers.context_fixtures import make_review_context
```

### Path resolution

Tests are organized in subdirectories mirroring `scripts/`. Path constants adapt based on subdirectory depth:

```python
# In tests/review/test_pipeline.py (one level deep)
TESTS_DIR = Path(__file__).resolve().parent.parent   # tests/
PLUGIN_ROOT = TESTS_DIR.parent                        # pirategoat-tools/
SCRIPTS_DIR = PLUGIN_ROOT / "scripts"

# In tests/review/agent/test_bootstrap.py (two levels deep)
TESTS_DIR = Path(__file__).resolve().parent.parent.parent  # tests/
PLUGIN_ROOT = TESTS_DIR.parent                              # pirategoat-tools/
SCRIPTS_DIR = PLUGIN_ROOT / "scripts"
```

Never hardcode absolute paths. Tests run from any working directory.

### Test data

- **Unit tests**: synthetic strings and dicts defined inline in the test class
- **Integration tests**: run the real script against real protocol files
- **Grader tests**: use `ReviewOutputBuilder` to create valid fixtures, hand-craft invalid ones
- **Compliance eval**: diff fixtures in `tests/fixtures/`, can reference `test-samples/` for existing diffs

### Dependencies

- **pytest** — the only external dependency (stdlib otherwise)
- **Zero model calls** in the entire test suite

## Valid Values Reference

These are the canonical valid values used by graders. Every vocabulary below except the last is imported from production rather than restated, so a schema or vocabulary change moves the graders without a second edit: `REQUIRED_JSON_TOP_FIELDS`, `REQUIRED_FINDING_FIELDS`, and `REQUIRED_CHECK_FIELDS` come from `review/review_document.py`, and `VALID_SEVERITIES`, `SEVERITY_RANK`, and `VALID_VERDICTS` from `review/verdict_rules.py`.

| Constant | Values | Source |
|---|---|---|
| `VALID_VERDICTS` | `approve`, `block`, `request_changes`, `comment`, `not_applicable` | `verdict_rules.REVIEW_VERDICTS` — the per-reviewer layer, broader than `LEDGER_VERDICTS`, which is the ladder |
| `SEVERITY_RANK` | `info` < `low` < `medium` < `high` < `critical` | `verdict_rules.SEVERITY_RANK`, derived from `VALID_SEVERITIES` |
| `REQUIRED_STATE_FIELDS` | `last_reviewed_sha`, `last_reviewed_at`, `review_count`, `base_ref`, `git_range_used` | `code-review.md` Step 5 |
