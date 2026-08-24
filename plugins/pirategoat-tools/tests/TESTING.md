# Testing Framework

Testing for pirategoat-tools uses fast, deterministic code-based graders — no model calls. All tests are pytest-based. The full suite takes roughly 80-95 seconds on a warm run (measured 2026-08-20, three runs) — re-measure both runtime and counts yourself (`time pytest plugins/pirategoat-tools/tests/ -q`, `pytest plugins/pirategoat-tools/tests/ --collect-only -q`) rather than trusting written numbers, here or in the class tables below.

## Architecture Overview

Audited against `ls -R plugins/pirategoat-tools/tests/` on 2026-08-20 (excludes `__pycache__/` and `.pytest_cache/`, which are build artifacts, not test files). **No `__init__.py` anywhere in this tree** — see [Package namespace rule](#package-namespace-rule) below; `test_pytest_layout.py` enforces it repo-wide.

```
tests/
├── TESTING.md                        # This file
├── conftest.py                       # Shared fixtures + sys.path setup (SCRIPTS_DIR on path)
├── test_annotation_evaluation.py     # Deferred-annotation (PEP 649) drift guard for every scripts/ module
├── test_codex_marketplace.py         # Generated Codex marketplace/plugin.json compatibility tests
├── test_containment_contract.py      # scripts/containment.py repo-boundary contract tests
├── test_git_paths.py                 # Shared Git C-quoted path grammar tests
├── test_pytest_layout.py             # Repo-wide guard: no __init__.py under any plugin's tests/
├── review/                           # Tests for scripts/review/
│   ├── test_pipeline.py              # briefings.py through the pipeline.py compatibility facade
│   ├── test_pipeline_infra.py        # pipeline.py + pipeline_contract.py routing, state, and CLI
│   ├── test_pipeline_integration.py  # orchestration.py through the pipeline.py compatibility facade
│   ├── test_plan_dispatch.py         # Dispatch planning tests
│   ├── test_context.py               # Review context collection tests
│   ├── test_agent_registry.py        # agent_registry.json schema/completeness/cross-reference tests
│   ├── test_agents_status.py         # Agent readiness gate tests
│   ├── test_atomic_io.py             # Shared atomic-JSON-write primitive tests
│   ├── test_bootstrap_host_injection.py  # Host Context injection in agent bootstrap
│   ├── test_criteria_coverage.py     # Every registry triage criterion has a dispatching probe
│   ├── test_critic.py                # Decision critic tests
│   ├── test_critic_adjustments.py    # decision-critic-adjustments.json -> review-findings.json writer
│   ├── test_dependency_refresh.py    # Stale dependency-root detection tests
│   ├── test_findings_save.py         # findings_save.py validating save-channel tests
│   ├── test_orchestration_hygiene.py # Step-3 hygiene baseline + step-11 sweep/usage-capture tests
│   ├── test_reconciliation_context.py  # reconciliation_context.py tests
│   ├── test_report_assembly.py       # review-record.md assembler tests
│   ├── test_registry_docs.py         # AGENTS.md registry reference pinned to the registry
│   ├── test_review_config.py         # Repo-contributed review config loader tests
│   ├── test_synthesis_lifecycle.py   # Reconciliator/critic lifecycle measurement
│   ├── test_telemetry.py             # Telemetry logging + manifest-section tests
│   ├── test_user_settings.py         # Requester-side machine-local settings tests
│   ├── test_workspace_setup.py       # Workspace setup tests
│   └── agent/                        # Tests for scripts/review/agent/
│       ├── test_bootstrap.py         # Bootstrap unit tests (direct imports)
│       ├── test_bootstrap_integration.py  # Bootstrap integration (smoke + category reps + build_output)
│       ├── test_bootstrap_repo_rules.py   # Repo-contributed review-rule injection tests
│       ├── test_diff_noise_filter.py # Semantic diff noise filter tests
│       ├── test_ecosystem_integration_reviewer.py  # ecosystem-integration-reviewer compliance test
│       ├── test_output.py            # ReviewOutputBuilder unit tests
│       ├── test_scope.py             # Scope filtering unit tests
│       └── test_scope_routing.py     # Domain routing (direct function calls + branch freshness)
├── linear/                           # Tests for scripts/linear/
│   ├── test_pipeline.py              # Linear issue pipeline tests
│   ├── test_pipeline_guidance.py     # Linear pipeline briefing tests
│   └── test_events.py               # Pipeline events tests
├── iterative_review/                 # Tests for scripts/iterative_review/
│   ├── test_briefing.py              # Iterative review briefing tests
│   ├── test_cli.py                   # CLI argument tests
│   ├── test_codex.py                 # Codex integration tests
│   ├── test_effort.py                # Adaptive reasoning-effort resolution tests
│   ├── test_loop.py                  # Review loop tests
│   └── test_telemetry.py            # Iterative review telemetry tests
├── analysis/                         # Tests for scripts/analysis/
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
│   ├── test_types.py                 # Host-context data type tests
│   ├── cache/
│   │   └── test_manager.py           # Ecosystem cache manager tests
│   ├── fixtures/                     # Resolver test fixtures (.keep only — populated at test time)
│   └── resolvers/                    # Tests for scripts/hosts/resolvers/
│       ├── test_docker_compose.py    # docker-compose resolver tests
│       ├── test_ecosystem_cache.py   # ecosystem-cache resolver tests
│       ├── test_explicit.py          # .pirategoat/config.json resolver tests
│       ├── test_plugin_headers.py    # plugin-headers resolver tests
│       ├── test_sibling.py           # sibling-convention resolver tests
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
│   └── pipeline_process.py           # Shared subprocess helper for invoking review/pipeline.py
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
    └── multi-file-realistic.diff     # 7 files across all 9 domains
```

### Review Pipeline Tests

The review pipeline tests load `scripts/review/pipeline.py` as the stable compatibility facade, then divide assertions along the same ownership boundaries as production:

| Source module | Concern | Test file |
|---|---|---|
| `scripts/review/pipeline.py` | Conditions, routing, state I/O, output formatting, telemetry/Git identity, CLI | `review/test_pipeline_infra.py` |
| `scripts/review/pipeline_contract.py` | Shared host, step-sequence, timeout, path, and Git vocabulary | All three pipeline test files |
| `scripts/review/briefings.py` | Pure guidance and briefing formatting | `review/test_pipeline.py` |
| `scripts/review/orchestration.py` | Side-effecting per-step subprocess and artifact work | `review/test_pipeline_integration.py` |
| `scripts/review/manifest_sections.py` | Run-manifest section projections (reached through `telemetry.py`, not the facade) | `review/test_telemetry.py` |

`pipeline_mod` preserves the facade's re-export contract for existing callers. Tests that patch a name resolved by orchestration use `orchestration_mod`, so the patch targets the caller's module globals.

### Bootstrap Unit Tests (`review/agent/test_bootstrap.py`)

Deterministic pytest suite. Tests `review/agent/bootstrap.py` pure functions by importing them directly — `extract_protocol_sections`, `build_output`, `compute_review_budget`, `load_pr_intent`, and others. No network or model calls.

### Bootstrap Integration Tests (`review/agent/test_bootstrap_integration.py`)

Integration tests that run `review/agent/bootstrap.py` via subprocess against a temp git repo (created from `multi-file-realistic.diff`, isolated from real repo state). Uses category representatives (principle §6) and right-layer testing (principle §7). Parameterized classes expand into more than one collected test per row (`TestSmokeAllAgents` is one method run over every registered agent) — run `pytest --collect-only` for real counts rather than trusting written numbers here.

| Class | What it verifies |
|---|---|
| `TestCategoryRepresentatives` | One comprehensive test per agent category: standard, test-agent, exploration, null-domain, history+override. Each verifies section structure, conditional sections, personalization, and budget in one shot. |
| `TestArchitecturalInvariants` | REVIEW RULES identical across 3 representative agents; DOMAIN RULES identical across 2 test agents |
| `TestSmokeAllAgents` | Every registered agent exits 0 — the ONE legitimate ALL_AGENTS parameterization (validates registry correctness) |
| `TestErrorCases` | Unknown agent exits 1 with structured error output |
| `TestReviewOutputBuilderAPIExample` | Section 3 includes complete builder API usage example (direct `build_output()` call) |
| `TestBootstrapOutputSizeCap` | Large scope truncated with file reference; small scope inline (direct `build_output()` call) |
| `TestDynamicDispatchRisk` | dead-code-reviewer gets DYNAMIC_DISPATCH_RISK from the caller's `has_php` fact (PHP → high, no PHP → low); rendered scope text can't drive the decision in either direction (direct `build_output()` call); 3 end-to-end subprocess tests cover `main()`'s own `has_php` derivation, which the direct calls can't reach — including that a domain-excluded PHP test file (under `=== SKIPPED ===`) must not force `high` |
| `TestOutputFilenameConsistency` | `save()` stages `<reviewer>-review.candidate.json`, then finalization publishes the canonical `<reviewer>-review.json` that bootstrap `OUTPUT_FILES` and reconciliation expect; delivered guidance names canonical output, never the candidate or derived Markdown |
| `TestBootstrapImportDoesNotBreakTelemetry` | Importing `review.agent.bootstrap` first (package-qualified) must leave a working `ReviewTelemetry` — pins the exact import-cycle regression `derive_reviewer_name`'s extraction to `reviewer_names.py` fixed (a same-package caller importing the name FROM bootstrap re-entered bootstrap mid-initialization and silently broke the telemetry load). Runs in a fresh subprocess since in-process `sys.modules` caching from other tests would mask it. |
| `TestNotDiffedContractIsDelivered` | The NOT DIFFED positive-claim/derived-complement contract survives protocol stripping through `build_output()`, while rendered scope and source guidance never instruct reviewers to declare gaps. Guards the 1.108.0 failure where a mandatory contract reached zero agents. |
| `TestEmpiricalProbeContract` | The `pirategoat-probe` naming convention survives protocol stripping into built prompts, and the section is not on the skip-list. The step-11 residue sweep only ever fires on files an agent named this way, so a stripped section makes the enforcement half inert. |
| `TestNotApplicableCompletionContract` | The shared protocol is the sole executable abstention recipe — a reviewer that finds nothing must abstain the one prescribed way. |
| `TestRepoRuleAndRefModeSelection` | Repo rules reach the reviewers they target (effective identity, complete scope); adapter instances receive their declared path scope; an explicit isolation request never runs inline. |
| `TestVerificationMethodContract` | Verification-method rules ported from ai-regression-review's triage.md — the half the 2026-07-15 dismissal port did not cover. |
| `TestDismissalDisciplineContract` | Dismissal/mitigation verification applies to ALL findings, not a subset. |
| `TestCanonicalExecutableBuilderSource` | Bootstrap is the sole executable `ReviewOutputBuilder` command source, and its envelope carries the producing plugin version (read from the run-config stamp, emitted empty when unknown so the envelope's five-assignment shape stays constant for the transcript analyzers). |

### Domain Routing Evals (`review/agent/test_scope_routing.py`)

Deterministic pytest suite that verifies `review/agent/scope.py` domain routing logic by calling `filter_noise()` + `filter_domain()` directly (pure functions, no subprocess). For each fixture, creates a temp git repo, gets the changed file list via `git diff --name-only`, and runs the filter functions for each domain.

Uses a `ROUTING_MATRIX` dict mapping fixture → expected domain results. Parameterized across all 14 domains and all 12 fixtures (168 test cases). Repos and file lists are cached per fixture.

Also includes `TestBranchFreshness` — 6 integration tests that run `review/agent/scope.py` via subprocess to verify merge-base detection, stale branch warnings, and range rebasing (these need the full pipeline).

**Fixture domain coverage:** See `ROUTING_MATRIX` dict in `review/agent/test_scope_routing.py` for the complete 12×14 matrix. Each entry maps `(fixture, domain) → "OK" | "NO_DOMAIN_FILES"`.

### Command Structure Evals (`commands/test_commands.py`)

Deterministic pytest suite that validates structural properties of command files. Shared helpers live in `helpers/command_helpers.py`. No network or model calls. `TestAllCommandsStructural` is parameterized over every registered command (`ALL_COMMANDS`) — this is where per-command structural checks for `pr-update.md`, `switch-to.md`, and every other non-review command live today; there is no longer a dedicated `TestPrUpdate`/`TestSwitchTo` class per command.

| Class | What it verifies |
|---|---|
| `TestFrontmatter` | All review commands have valid YAML frontmatter with a `description` field |
| `TestAllCommandsStructural` | Every registered command file exists, has valid frontmatter with a real `description`, and is registered in `marketplace.json`; non-review commands are asserted absent from `ALL_REVIEW_COMMANDS` |
| `TestScriptReferences` | Review commands reference `review/pipeline.py`, which exists on disk |
| `TestReviewCommandsReferenceUnifiedScript` | Each review command passes the correct `--mode` to `review/pipeline.py` (`pr-review.md` → `pr`, `full-code-review.md` → `full`, `code-review.md` → computed incremental/full) |
| `TestReviewRunIdentity` | Review commands link pipeline telemetry to the active Claude session |
| `TestMarketplaceRegistration` | Review commands are registered in `marketplace.json` |
| `TestCodeReviewIterative` | `code-review.md` has incremental mode, full/reset option, baseline reference |
| `TestFullCodeReview` | `full-code-review.md` has full mode |
| `TestUnifiedMission` | All review commands reference the unified pipeline mission |
| `TestDependencyRefreshFlagDocumented` | Every review command documents the `--refresh-deps` opt-in |

### ReviewOutputBuilder Unit Tests (`review/agent/test_output.py`)

Direct unit tests on the `ReviewOutputBuilder` class from `scripts/review/agent/output.py`. Tests cover initialization, issue addition with validation, recommendations, verdict calculation, serialization (dict, markdown), file output, the NOT DIFFED coverage APIs, advisory-channel accounting, and the reconciliator-facing rendering this class grew once `review-findings.md` became a mechanical render of the JSON (Task 7) rather than reconciliator-written prose.

| Class | What it verifies |
|---|---|
| `TestAddIssue` | Returns 8-char ID, stores all fields, severity case-insensitive, invalid severity raises, confidence boundaries, extra kwargs, defaults |
| `TestAddClearance` | `add_clearance()` records auditable "nothing depends on this" claims |
| `TestAddRecommendation` | Valid priorities store, invalid silently ignored, multiple per bucket |
| `TestNonStringFieldCoercion` | `add_issue()` coerces free-form text fields to strings rather than rejecting non-string input |
| `TestSetConfidence` | Valid range works, invalid raises ValueError |
| `TestAddToolResult` | Stores tool names, deduplicates |
| `TestCalculateVerdict` | All 9 verdict boundaries (approve/comment/request_changes/block) |
| `TestToDict` | All top-level keys, severity counts, meta structure, None for empty fields, `schema: 1` (no retired `version` string), and `plugin_version` resolution — envelope variable first, then the run-config stamp for envelope-bypassing callers, null when neither is readable |
| `TestToMarkdown` | Header format, issues grouped by severity, positive observations |
| `TestRenderMarkdown` | Markdown is a pure function of the canonical JSON dict — same dict in, same Markdown out |
| `TestMaterializeMarkdown` | The on-demand `materialize` CLI/function reads finalized canonical JSON and writes its derived Markdown |
| `TestSave` | `save()` publishes a replaceable candidate JSON, echoes its exact finalization command, and returns the candidate path plus digest; it never publishes canonical JSON or Markdown |
| `TestFileScopedIssues` | `line=None` records a first-class file-scoped issue (`scope: "file"`) that still counts toward the verdict — no silent demotion |
| `TestLineRequired` | Invalid line values still raise for point defects — the file-scoped path never becomes a way to skip validation |
| `TestAddObservation` | `add_observation()` stores file-level notes outside the finding pipeline, in insertion order |
| `TestAddDeferredReviewed` | Explicit positive claims of NOT DIFFED files actually read: canonical path grammar, add-time membership validation against the authoritative deferred sidecar, no verdict effect, all-or-nothing batch validation, and duplicate/already-recorded dedup semantics |
| `TestNotApplicable` | `mark_not_applicable()` produces a `not_applicable` verdict with `skip_reason`, zero findings |
| `TestAdvisoryChannel` | Advisory-channel findings are listed but never gate the verdict; entitlement and suppression accounting |
| `TestDerivedDeferredCoverage` | Every candidate save derives validated deferred claims, their unreviewed complement, and the inline-plus-claims reviewed count from the authoritative sidecar; re-saving recomputes from scratch and finalized JSON preserves the derived values |
| `TestBudgetTargetEcho` | The call-budget target reaches the reviewer where it can still act on it: `save()` echoes one TARGET line exactly when the saved output records unreviewed files and the envelope carries a positive target, and stays silent otherwise (no unreviewed files, no envelope, malformed value) |
| `TestMetaIsNeverFakeZero` | `meta.files_reviewed` derives from inline scope plus validated deferred claims, while `meta.review_duration_ms` derives from the actor's dispatch marker — the `<agent>.started` and `<agent>.synthesis-started` families both — with null for a missing, unparsable, or future-stamped marker |
| `TestTypeScriptContractLockstep` | `schemas/review-output.ts` and the serialized artifact describe one shape: the identity block (`pr_id`/`reviewer`/`timestamp`/`plugin_version`/`schema`) is declared and emitted, the retired `version` field is gone from both, `schema` is typed `number`, and `plugin_version` is typed nullable |
| `TestNarrativeSummary` | The reconciliator's overall-state prose (`narrative_summary`) gets a structured home in `to_dict()`/`to_markdown()`, including the withdrawn-summary audit record left by an applying critic adjustment |
| `TestReconciliationSectionsRender` | Every section the reconciliator's old hand-written narrative template carried (recommendations, observations, host context banner, `meta.reconciliation`) now has a rendered home |
| `TestMaterializeFindingsMarkdown` | One materializer, parameterized — `review-findings.md` and `<reviewer>-review.md` share the same render path, never a second one |
| `TestAssessmentProvenance` | `## Assessment` is prose about a ledger that keeps changing after critic adjustments — provenance is pinned so a stale claim can't outlive the finding it described |
| `TestRemovedByCriticSection` | The ledger deliberately keeps what the critic took out, rendered as an audit section rather than silently vanishing |
| `TestRendererFaithfulness` | Minors that all share one failure mode: the renderer showing content that contradicts what the JSON actually says (e.g. a header claiming a section exists over content that was dropped) |

### Reconciliation Context Tests (`review/test_reconciliation_context.py`)

Direct unit tests on `scripts/review/reconciliation_context.py` — agent-finding loading, scope and hunk checking, source-snippet extraction, and severity normalization. The module builds `reconciliation-context.json` and nothing else now: its two Markdown renderers (`to_markdown` for the reconciliator, `build_critic_context` for the decision critic) were projections whose only readers were agents, and both are gone — the agents read the JSON, and the decision critic reads `review-record.md` beside it. The deferred-coverage accounting classes are listed here because they carry the NOT DIFFED honesty contract from reviewer output into the reconciliation view; the remaining classes follow the same direct-unit-test pattern.

| Class | What it verifies |
|---|---|
| `TestAggregateInlineCoverage` | `aggregate_inline_coverage()` uses the shared coverage authority for each finalized reviewer's validated positive claims and derived complement; malformed claims fail closed, and one reviewer's claim cannot conceal another reviewer's gap |
| `TestUnscopedFiles` | `files_unscoped` — the changed files no reviewer's scope contained in ANY form (inline, deferred, or name-only), the population every other bucket structurally cannot see because each is keyed on a file some sidecar mentions. Pins the union across all three sidecar file lists, the measured-empty case, and `None` (not `[]`) when no changed-file list was supplied, so "not measured" can never read as "none found" |
| `TestAgentsReportingCountsAgents` | `agents_reporting` counts DISTINCT agent names, not scope-summary files — three reviewers ship a second `-config-ops` sidecar, which made a 19-agent field run report 22 |
| `TestMissingAgentDetection` | `compute_missing_agents()` keeps dispatched-minus-reporting a MEASUREMENT rather than the reconciliator's arithmetic — sorted for stable diffs, `None` (never `[]`) when dispatch is unknown, measured-empty for an explicitly empty dispatch, and no negative population from an undispatched reporter. Crossed through the CLI to the JSON both ways |
| `TestPrefilterAnnotation` | The two structurally-certain out-of-scope statuses are adjudicated by the pipeline and annotated in place, never deleted (`agent_findings` is the record of what each reviewer said, and its metrics are counted from it). `not_in_hunk` is deliberately never annotated — it is the one out-of-scope status that IS a judgment call. Owns the key, so a stale marker on in-scope input is cleared rather than silently deleting a real finding; malformed shapes are skipped, not raised |

### Review Record Assembly Tests (`review/test_report_assembly.py`)

Direct unit tests on `orchestration.assemble_review_record()` — the machine projection of the findings ledger the pipeline writes at step 9 and re-assembles at step 11. No LLM writes or edits `review-record.md`, which is what makes it safe to hand the decision critic and what lets `review-report.md` be authored once, after validation.

| Class | What it verifies |
|---|---|
| `TestRecordAssembly` | Section order, the header's verdict and severity counts, and the two byte-identity contracts that keep the record from disagreeing with anything else: its findings body IS `render_review_body()` and its coverage section IS `_render_review_coverage_section()`. Also the record's own new prose — the run notes (dependency refresh, dispatch) and the closing verdict line, which names the ledger layer the verdict was computed at and the published layer it maps onto |
| `TestRecordIsAProjection` | Re-assembly after `apply_adjustments()` shows the post-critic ledger: adjusted severities, the recomputed verdict, the checkpointed adjudication narrative, one accounting line for every applied or refuted critic decision, and — when no replacement narrative was written — the explicit withdrawal notice rather than the retracted text presented as current |
| `TestRecordSanitization` | Prose `Severity-floor:` markers are stripped before the record renders them (they read to the critic as an instruction not to demote), the STRUCTURED floor still renders, `review-findings.json` on disk keeps the reviewer's own words, and a non-string finding field costs a rendering nicety rather than the artifact |
| `TestBriefingsAreConstantSize` | Briefings are O(1) in changed-file count while the record is O(n): a 500-file coverage state renders a step-9 briefing under 8KB with all 500 lines in the record, and the briefing is byte-identical at 1 file and at 500. Pins the class of guarantee the record artifact buys, not a single fact about step 9 |
| `TestRecordFailureModes` | A run with no ledger reports a measured zero, not a failure (that is the degraded path step 9 routes to manual synthesis); an unreadable or shape-invalid ledger reports `failed` with the reason and writes nothing |
| `TestRecordWriteIsAtomic` | The write goes through `atomic_write_text`, a failing render leaves the previous record byte-identical rather than half-replacing it, and no temp file survives a successful assembly |

### Critic Adjustments Tests (`review/test_critic_adjustments.py`)

Direct contract tests for the three-owner lifecycle: `critic proposal -> critic.py --save -> committed proposal snapshot`; `orchestrator spot checks -> critic_adjustments.py settle -> adjudication checkpoint`; `ledger mutation -> apply_adjustments() -> derived findings/verdict/provenance`. `write_adjustments()` is the sole adjustments-artifact writer and `_apply_adjustments_locked()` the sole post-reconciliation ledger mutator.

| Class | What it verifies |
|---|---|
| `TestApplyAdjustments` | The happy paths and the loud failures: `promote`, `add`, and `remove` land with `critic_adjustment` provenance and a summary recounted from the resulting population; a missing adjustments file is a no-op, `rejected` entries are skipped, and a second run is idempotent — including the `add` action's own round trip (generated id, provenance, recount) and its own reapply-idempotence, not just `promote`'s; an unknown action, an unknown target id, or a non-adjustable field fails the whole call with nothing written |
| `TestRejectionAudit` | A script-derived refutation lands a `rejected_critic_adjustments` audit record (`adjustment_id`, `action`, `target_id`, `spot_check: refuted`, `rejection_reason`) in `review-findings.json`, where the shared renderer projects it beside applied decisions while the finding itself remains unmodified; retries do not duplicate records, later settlements append, pure refutations report `nothing_pending`, and mixed settlements apply verified or unchecked entries while auditing refuted ones; malformed lifecycle documents are rejected before mutation |
| `TestCrashSafety` | Application recorded on both sides — stable IDs allocated before proposal publication and the `applied_critic_adjustments` record — so a crash between the findings and applied-flag writes converges without double-applying; duplicate IDs are rejected and no temp file survives either a success or a rejection |
| `TestProposalPreparation` | Critic proposal validation rejects every lifecycle field, assigns unique stable IDs without mutating temp input, digest-projects only immutable proposal facts, and requires unique IDs in persisted lifecycle documents |
| `TestAdjudicationRequest` | The exact orchestrator request accepts only verified IDs, refuted IDs with non-empty reasons, and a non-empty revised narrative; omitted committed IDs become `not_checked`, counts are derived, and every invalid request leaves proposal and ledger byte-identical |
| `TestSourceBindingAndRecovery` | Immutable edits break the verdict marker's proposal digest; adjudication checkpoints before apply; a crash at that boundary resumes once; identical retries are byte-stable; different retries refuse; defensive apply records visible `not_checked` provenance; and one output lock spans checkpoint plus apply, including concurrent critic publication |
| `TestAdjudicationCLI` | `settle` consumes one stdin object and echoes the checkpoint/count/digest/apply contract; exact retries report `ALREADY SETTLED` and `ALREADY APPLIED`; bare implicit apply is retired while explicit `apply` remains available for recovery |
| `TestBatchCoherence` | All-or-nothing batch validation with nothing written: duplicate targets, an entry targeting a finding an earlier entry removes, an entry with no usable id, an unaddressable finding, an `add` that assigns its own id (both spellings), a pre-existing severity outside the vocabulary, and a findings file that is not a JSON object |
| `TestAdjustmentsSchemaValidation` | The adjustments doc's own `schema` field is validated, not just taught: `schema: 1` proceeds, `schema: 2` refuses the whole batch naming `schema`, a missing `schema` key refuses with the same message shape rather than defaulting to version 1, `"1"` (string) is not type-coerced into the accepted integer, and a non-object doc (`[]`, `"hello"`, `5`) is diagnosed as a shape error — not a schema error — mirroring `read_findings_file()`'s FINDINGS_READ_NOT_OBJECT handling |
| `TestScopeLinePairing` | `scope`/`line` stay the pair `schemas/review-output.ts` declares and `output.py`'s renderer branches on, and patched lines keep the builder's positive 1-indexed invariant |
| `TestCLI` | The explicit recovery `apply` process contract: exit status plus the stdout/stderr channel split |
| `TestCriticVerdictGate` | `apply_adjustments()`'s own authority gate, exercised directly and via the CLI subprocess: a missing verdict file, an unparseable one, a present STAND verdict, and three near-miss REVISE spellings (`revise`, ` REVISE `, `REVISE\n`) all refuse with `{"status": "refused", ...}` and write nothing (byte-identical findings, untouched adjustments file) — the gate is exact-match, not case- or whitespace-tolerant; a REVISE verdict proceeds normally; and the CLI exits a distinct nonzero code (`REFUSAL_EXIT_CODE`) on refusal, separate from 0 (success) and 1 (validation/IO error), with the result JSON still reaching stdout |
| `TestReadCriticVerdict` | The shared reader `read_critic_verdict()` the gate is built on: a missing file, unparseable JSON, a non-object payload, a non-string `verdict` field, and a missing `verdict` key all return `None`; every verdict string on the module's vocabulary (`REVISE`, `STAND`, `ESCALATE`, `SKIPPED`) round-trips as-is |
| `TestStepElevenAppliesAdjustments` | Step 11 recovers pending REVISE checkpoints before deriving the ledger verdict. If it must create a `defensive_apply` checkpoint, it records stable `critic_adjudication_missing` degradation provenance and never duplicates the public note; malformed or unbound snapshots degrade without mutation, and an unexpected authority refusal remains visible |
| `TestNarrativeSummaryInvalidation` | The one part of the ledger the critic can invalidate but not correct. `narrative_summary` is ledger-level prose no adjustment addresses, so an applying batch withdraws it: the text moves to `withdrawn_narrative_summary` beside the ids of the decisions that withdrew it (a list, so a second round appends rather than erasing the first), and a batch that applies nothing — refused, settled, or fully rejected — leaves the assessment untouched. A ledger with no summary records no withdrawal rather than a fabricated empty one
| `TestStepElevenWithdrawsContradictedProse` | The reproduced defect end to end: a critical finding described in the Assessment and demoted by the critic used to render the demotion in the issue list with the stale "one CRITICAL blocker" claim printed directly above it. Step 11 now renders the withdrawal notice instead
| `TestStepElevenRerendersFindingsMarkdown` | Step 11 re-renders `review-findings.md` from the FINAL ledger — after the adjustments apply — so a REVISE demote reaches the Markdown instead of leaving the pre-adjustment severity showing, the field-proven staleness this closes by construction. A render failure is one degradation note and never an exception out of finalize; a run with no ledger renders nothing and adds no note; and `report_path` resolves report → record → findings Markdown, so the run always names the newest complete account it has — including at the normal instant when the report has not been authored yet, which is no longer a degradation
| `TestCriticInputRoundTrip` | The seam none of the three modules' own tests span: the id the critic can see must resolve in the ledger when step 11 applies it. The critic is handed `review-findings.json` itself now, so the only key its view offers IS the ledger key — and the record is pinned to offer no rival positional handle, which is the gap where an F-label was the critic's only visible id and every REVISE run shipped degraded with "no issue with id 'F1'" |
| `TestDerivedVerdict` | Step 11 DERIVES the published verdict from `review-findings.json` — the one artifact whose verdict was actually computed from findings — instead of transcribing one out of `review-verdict.json` and syncing it back over the ledger. All FIVE ledger verdicts map (`block` included: it is what any critical finding produces, and omitting it published COMMENT for a critical-finding review), casing and padding are tolerated, and a critic `ESCALATE` overrides to COMMENT while `STAND` does not. Every unusable ledger — absent, non-object, unparseable, null/unknown/missing `verdict` — falls back to COMMENT with `verdict_source` and a degradation note saying so, never a crash and never a confident value. A stale `review-verdict.json` left in the directory cannot reach the published verdict, and finalize no longer writes the ledger at all |
| `TestCriticAbsenceHonesty` | A critic that was dispatched and produced nothing is a run that lost its stress test; a critic never dispatched is quick mode working as designed. `critic_verdict_for_state()` collapses both into `"unavailable"` — right for pirategoat-bot, blind for the run's own status — so the dispatch marker separates them: a marker with no verdict artifact appends `critic produced no verdict artifact` and degrades, while no marker, an answered critic, and an explicit SKIPPED artifact are all silent. The missing critique never costs the review the verdict its findings earned |
| `TestReconciliatorWritePathPin` | Writer #1 is an agent following a Markdown snippet, so a test is the only thing holding it to the sanctioned write path: `agents/review-reconciliator.md` must save through `scripts/review/findings_save.py` (the reconciliator's sibling to `critic.py --save`; see `tests/review/test_findings_save.py`) and must not carry any spelling that writes `review-findings.json` directly, including a direct `write_findings()` call. Drift back to the bare atomic write it carried two commits earlier — or to calling `write_findings()` straight from the snippet, unvalidated — would give the ledger an unvalidated write path again with the rest of the suite green |
| `TestClearancePassthrough` | The ledger's `clearances` survives every writer after the reconciliator — `apply_adjustments()`, the whole-document `write_findings()`, and the renderer that produces `## Clearances (verified absences)`. The field run only ever carried `clearances: null`, so a write path that quietly dropped unknown-to-it keys would have looked identical |
| `TestReconciliatorClearancePin` | The same agent-follows-a-snippet problem one field over: `agents/review-reconciliator.md` must teach `add_clearance(claim, method, evidence)`, must exclude void and method-correlated-duplicate clearances from it, and must list it in the structured-home table. Without this the ledger's `clearances` stays null and step 9 rebuilds "what held" from memory |

### Orchestration Hygiene Tests (`review/test_orchestration_hygiene.py`)

Direct unit tests on the finalize-side accounting in `scripts/review/orchestration.py` — the step-3 hygiene baseline snapshot, the step-11 compare-and-sweep, the degradation notes step 11 derives from the result, and the step-11 token-usage capture. Each test runs against a throwaway git repo as CWD, because the hygiene code under test resolves and mutates the repository it is standing in.

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
| `TestDependencyRefreshManifest` | `build_dependency_refresh_manifest()` sanitizes the trusted-branch refresh report — requested/reported flags, the mutually exclusive skipped/verification shapes, and the status/commands group that only appears once the self-report was read. Task 13 added `test_availability_flag_tracks_the_payload`, pinning `availability.dependency_refresh` beside the section — the flag did not exist before this task even though the section itself always did |
| `TestReviewerMarkdownManifest` | `build_reviewer_markdown_manifest()` projects step 8's per-reviewer render outcome via the shared `_sanitize_derived_markdown_outcome` validator. Task 13 added `test_availability_flag_tracks_the_payload`, the same before-this-task gap `TestDependencyRefreshManifest` closes |
| `TestFindingsMarkdownManifest` | New in Task 13, closing the Task 7 deferral: `build_findings_markdown_manifest()` projects steps 9/11's `review-findings.md` render outcome from `state["findings_markdown"]`, sharing its validator (and its written/expected/status vocabulary) with `TestReviewerMarkdownManifest`'s sibling rather than restating it |

**Historical-data note:** `thoughts_length` was removed from live events (`test_telemetry.py::TestNoFabricatedMeasurements`, elsewhere in this file), but manifests and JSONL logs written before that fix still carry `args.thoughts_length: 0` on every `step`/`pipeline_end` event — a measurement that never happened, published as a measured zero, on every pre-fix run. Nothing reads the key today. Any future historical-cohort work over pre-fix logs must treat `thoughts_length` as unmeasured noise, not data — do not average it, do not use its presence/absence to date a run, and do not infer anything from its value being 0.

### Synthesis Agent Lifecycle Tests (`review/test_synthesis_lifecycle.py`)

Direct unit tests on `scripts/review/synthesis_lifecycle.py` and its five orchestration seams. The review-reconciliator (step 8) and the decision critic (step 10) never run `agent/bootstrap.py`, never write a `<agent>-review.json`, and are never in `dispatch-plan.json` — the only list `agents_status.py` iterates — so the reviewer lifecycle machinery structurally cannot see them. This suite pins the measurement that replaces it.

| Class | What it verifies |
|---|---|
| `TestDispatchMarker` | `mark_dispatched()` writes bootstrap's marker BODY (one aware UTC ISO timestamp) under a namespaced NAME, `<agent>.synthesis-started`. The suffix keeps these markers out of the reviewer `*.started` contract other tools scan — pirategoat-bot's resume path treated every such hit as a reviewer and renamed synthesis markers away as orphans, erasing the stall signal in the one window where the marker is the only record of a dispatch. Writer and reader resolve the path through the same `MARKER_SUFFIX` constant, so a marker the writer creates is always one the reader finds; an unwritable marker costs a measurement, never the review |
| `TestAvailability` | A run with no markers records no rows at all — a pre-feature run and a quick-mode-skipped critic are both ABSENT, never a zero-duration row for a phase nobody measured |
| `TestCompletionObservation` | `duration_ms` comes from the completion artifact's mtime, not from observation time (which would inflate the critic's phase by however long finalize took to arrive); the completion artifacts are the handoff-GATED ones, and `decision-critic-findings.md` deliberately is not — it exists only when the critic produced a critique, so keying on it would report a crashed critic as still running |
| `TestStallDetection` | Only `finalize=True` adjudicates a stall; a marker with no completion artifact then records `stalled: true` plus the elapsed stall length. An artifact predating its dispatch is not that dispatch's output (no borrowed durations), and an unreadable or naive-timestamp marker still counts as dispatched — reporting "not dispatched" there would hide a stall |
| `TestIdempotence` | A completed entry is preserved verbatim across re-observation — step 9's reading is the tightest bound the run will ever have, and finalize must not push its `observed_at` minutes later. An incomplete entry IS re-observed, and a corrupt or foreign-schema prior artifact is re-derived rather than trusted |
| `TestArtifactEnvelope` | `synthesis-agents.json` carries `schema: 1`, the returned payload is what landed on disk, and rows carry exactly `ROW_KEYS`. ONE clock is pinned as an absence: neither the row nor the section records when the script looked, only when the agent finished |
| `TestVerdictCapture` | The completion artifact's own `verdict` rides the row, because it changes what the duration beside it MEANS: a critic row reading `SKIPPED` spans dispatch to orchestrator-gave-up, not a critique. Unreadable, non-string, and verdict-less artifacts all yield `None`, and an artifact discarded as predating its dispatch contributes no verdict either — a stale conclusion must never be paired with a live phase |
| `TestStepEightDispatchMarker` | The reconciliator marker is stamped only where step 8 actually hands off: not on the readiness gate's WAITING return, and not when the reconciliation-context gate raises — a marker on either path would make a run that dispatched nothing read as a stalled agent |
| `TestStepTenDispatchMarker` | The critic marker is written on exactly the branch whose briefing dispatches one; the quick-mode skip branch writes none, so the skipped critic earns no lifecycle row |
| `TestStepTenReEntryDoesNotManufactureAStall` | The reproduced defect and its fix. Step 10 IS re-entered after a completed critic (a rerun once the reconciled verdict escalates) and nothing observes between step 10 and finalize, so a bare re-stamp of the dispatch marker moved the clock past the critic's already-written verdict file — finalize then read that file as predating its dispatch and published a finished 665s critique as `stalled: true` with no duration. Step 10 now observes before it re-stamps, on BOTH the dispatch and skip branches, which also closes the REVISE window on the reconciliator: the orchestrator's adjustment apply rewrites `review-findings.json` between step 10 and finalize, and without this reading finalize would fold the critic's phase into the reconciliator's duration |
| `TestStepNineObservation` | Step 9 records the reconciliator's completion — the earliest moment the script re-enters after step 8's handoff |
| `TestStepElevenObservation` | Finalize records the critic's duration and adjudicates stalls, and it observes BEFORE its own write to `review-findings.json` (the adjustments apply): observing after them would report the reconciliator as having finished at finalize time — the run's whole wall clock instead of its synthesis phase |

### Registry Documentation Tests (`review/test_registry_docs.py`)

Two module-level guards pinning the plugin `AGENTS.md` agent-registry reference to `scripts/review/agent_registry.json` in both directions: every `model_tier` the registry actually uses must appear in the documented vocabulary, and the vocabulary must not teach a tier no agent uses. `"inherit"` is excepted as a routing keyword — legitimate to document with zero users. The row had drifted to `inherit`/`sonnet`/`haiku` while five agents ran at `opus`, so a cold agent reading the canonical reference learned a vocabulary the machine does not use.

Three more module-level guards pin `README.md`'s "#### Model Tiers" section — the same drift, one level up: the README hand-summarizes registry `model_tier` counts and names example agents per tier in prose, and nothing tied that prose to the registry either. It drifted to `opus (4 agents)` while the registry carried five, silently omitting `woo-regression-reviewer` from the paragraph. The three guards parse the README's own agent tables (Domain Review / Pipeline / Cross-Validators / Utility) and its Model Tiers bullets, then check: every registry agent's README table row matches its registry `model_tier`; each tier's `(N agents)` figure equals what the README's own tables tag with that tier; and every registry agent at the `opus`/`haiku` tiers (small enough that the README names each one individually) is named by its exact slug in that tier's bullet prose. `sonnet` (22 agents) is deliberately written as category-grouped prose rather than an exhaustive per-agent listing, so only its count is checked — naming every one of 22 agents individually is not the convention this guard protects.

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
| `grade_review_json(path)` | Path to `{reviewer}-review.json` | File exists, valid JSON, required fields (`pr_id`, `reviewer`, `verdict`, `summary`, `issues`, `meta`), valid severities, valid verdict, issue schema, summary structure |
| `grade_review_markdown(path)` | Path to `{reviewer}-review.md` | File exists, `# ... Review` header, `## Executive Summary`, `**Verdict:**` — rendered from the JSON when absent |
| `grade_signal_format(text)` | Return signal text | `STATUS: FINISHED`, `OUTPUT_FILES:`, `COUNTS:`, `VERDICT:`, `SUMMARY:` |
| `grade_no_domain_files(text)` | Agent output for no-code scenario | APPROVE verdict, zero findings |
| `grade_error_exit(text)` | Agent output for error scenario | Error indication, no STATUS: FINISHED |
| `grade_output_pair(output_dir, reviewer_name)` | Output directory + reviewer name | Both `.json` and `.md` exist, delegates to json + markdown graders, reviewer name matches |
| `grade_review_baseline(path)` | Path to `.branch-review-baseline.json` | File exists, valid JSON, required fields (`last_reviewed_sha`, `last_reviewed_at`, `review_type`, `review_count`, `base_ref`, `git_range_used`), SHA format (7-40 hex), positive review_count, range contains `..` |

### Agent Compliance Grading (`grading/eval_agent_compliance.py`)

Offline grading tool for review output files — not part of the pytest suite.

- **`--grade-only /path/to/output`** — Scans an existing output directory for finalized `*-review.json` files and grades each JSON/Markdown pair, materializing the derived Markdown from canonical JSON first. Fast, no model calls. Use after a real review run to validate agent output format.

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
| `required_findings` | Each spec must be matched by some issue (recall) — a miss fails the entry. |
| `acceptable_findings` | Secondary findings the key pre-declares; matching them never punishes or rewards the entry, and they are excluded from `max_unexpected`. |
| `max_severity` | False-positive precision cap: no issue may rank above this severity — the gate the clean-code probes rely on. |
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

**Report schema** (`--report-out`, dispatch mode only): top-level `mode`,
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

`detail` shapes follow the entry: single-run graded entries carry
`{verdict, match, gates, compliance_passed, output_dir, models, status}`
(abstention keys carry `issue_count` and `match: null`, and no `gates`
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
checks per schema-valid issue and would score a more verbose reviewer
higher for identical detection performance.

## Design Principles

These principles guide all testing decisions. Follow them when adding or modifying tests.

### 1. Code-based graders, not model-based

All graders are deterministic Python functions. No LLM calls in the grading path. This keeps tests fast (~80-95s for the full suite as of 2026-08-20 — see the header above), reproducible (same input = same result), and cheap (no API costs).

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

Shared test utilities live in `tests/helpers/`. Unlike `scripts/` (added to
`sys.path` once, in `conftest.py`), `conftest.py` does NOT add `tests/`
itself — every caller inserts `TESTS_DIR` onto `sys.path` before importing
from `helpers/`. `grading/test_graders.py` does this:

```python
TESTS_DIR = Path(__file__).resolve().parent.parent  # grading/ -> tests/
sys.path.insert(0, str(TESTS_DIR))
```

Then import as normal:

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

These are the canonical valid values used by graders. If the review output schema changes, update both the source (`review/agent/output.py`) and the grader constants (`helpers/graders.py`).

| Constant | Values | Source |
|---|---|---|
| `VALID_SEVERITIES` | `critical`, `high`, `medium`, `low`, `info` | `ReviewOutputBuilder.add_issue()` |
| `VALID_VERDICTS` | `approve`, `block`, `request_changes`, `comment`, `not_applicable` | `ReviewOutputBuilder._calculate_verdict()` |
| `REQUIRED_JSON_TOP_FIELDS` | `pr_id`, `reviewer`, `verdict`, `summary`, `issues`, `meta` | `ReviewOutputBuilder.to_dict()` |
| `REQUIRED_ISSUE_FIELDS` | `id`, `severity`, `title`, `file`, `description`, `recommendation` | `ReviewOutputBuilder.add_issue()` |
| `REQUIRED_STATE_FIELDS` | `last_reviewed_sha`, `last_reviewed_at`, `review_count`, `base_ref`, `git_range_used` | `code-review.md` Step 5 |
