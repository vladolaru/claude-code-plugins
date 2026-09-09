# pirategoat-tools — Agent Instructions

You are the maintainer of pirategoat-tools, a code review orchestration plugin. You dispatch domain-specific reviewer agents in parallel, reconcile their findings through semantic deduplication and verification, then stress-test conclusions through an independent decision critic.

This file holds the rules and the map. A fact about one module lives in that module's docstring; how a subsystem is built lives in `docs/`; which tests a change runs lives in `tests/TESTING.md`. The root `AGENTS.md` says what belongs where.

## Read When

| Before you | Read |
|---|---|
| Change a step's briefing, handoff, readiness gate, output artifact, or the shared reviewer protocols | `docs/review-pipeline.md` |
| Change the shape of any JSON artifact, or add one | `docs/artifact-schemas.md` |
| Touch `review_config.py`, the `repo-reviewer-adapter`, bootstrap ref-mode, or the advisory channel | `docs/repo-reviewers.md` |
| Change or use anything under `scripts/analysis/` | `docs/analysis-tools.md` |
| Exercise unreleased plugin changes against a real repository | `docs/dev-wrapper.md` |
| Change a briefing's prose or structure | `../../docs/patterns/curated-context-pipeline.md` (the design it follows) |
| Decide which tests to run for a change | `tests/TESTING.md` § Which tests to run |
| Defer a real finding instead of fixing it | `BACKLOG.md` (the committed place of record; `.claude/docs/` is gitignored) |

## Key Files

`scripts/review/agent_registry.json` is the single source of truth for reviewer configuration; every agent change starts and ends there. Each module below documents its own contract in its docstring.

| File | Role |
|---|---|
| `scripts/review/pipeline.py` | Executable facade for the 12-step review pipeline: conditions, routing, state I/O, CLI. All three review commands call it with `--mode pr\|full\|incremental`; Codex adapters add `--host codex`. |
| `scripts/review/pipeline_contract.py` | Shared path, host, step-sequence, timeout, and Git vocabulary. |
| `scripts/review/run_paths.py` | Sole authority for durable review locations, run allocation and retention, and the shared artifact filename registry. |
| `scripts/review/reviewer_lifecycle.py` | Per-reviewer artifact filenames and the review-intake lifecycle. |
| `scripts/review/briefings.py` | Pure step guidance, mission text, and output templates for the 12 steps. |
| `scripts/review/orchestration.py` | Side-effecting per-step work: subprocesses, dispatch-plan persistence, derived-Markdown materialization, `assemble_review_record()`, step 11's publication gate. |
| `scripts/review/context.py` | Step 3 context collection into `review-context.json`, including host-context discovery. |
| `scripts/review/plan_dispatch.py` | Deterministic dispatch planning from the registry and the changed files; each decision is `(status, reason, signal)`. |
| `scripts/review/triage_sources.py` | The prose the keyword triage reads, reduced to the author's words. Stdlib-only leaf. |
| `scripts/review/change_purpose.py` | Parser of the step-3 change purpose (Verify and Context items) and of who may cite a Verify item. Stdlib-only leaf. |
| `scripts/review/dispatch_adjust.py` | The orchestrator's one channel for step-5 dispatch overrides (`--skip`, `--dispatch`). |
| `scripts/review/dispatch_status.py` | Dispatch-status and signal vocabulary; `load_dispatch_plan()` is the one plan reader. |
| `scripts/review/agent/bootstrap.py` | Builds each reviewer's structured prompt: protocol extraction, scope, output instructions. |
| `scripts/review/agent/scope.py` | Domain-filtered diff scoping; language recognition lives in its `_*_LANGS` groups only. |
| `scripts/review/agent/output.py` | `ReviewOutputBuilder`: draft, finalize, and the `finalize-review` CLI. |
| `scripts/review/review_document.py` | The review document's shape authority and validators. Leaf. |
| `scripts/review/review_markdown.py` | The one JSON-to-Markdown projection (`render`, `materialize`). |
| `scripts/review/reviewer_names.py` | `derive_reviewer_name()` and its inverse. Stdlib-only leaf every artifact name is built from. |
| `scripts/review/reconciliation_context.py` | Builds `reconciliation-context.json`, the reconciliator's single input. |
| `scripts/review/reconciliation_notes.py` | Registers orchestrator notes the reconciliator must answer. |
| `scripts/review/findings_ledger.py` | `FindingsLedgerBuilder`, the reconciliator's builder. |
| `scripts/review/findings_save.py` | The ledger's only write channel; validates and stamps pipeline-owned facts. |
| `scripts/review/verdict_rules.py` | `verdict_for_counts()`, the one severity-to-verdict ladder. |
| `scripts/review/critic.py` | The decision critic's validating `--save` channel. |
| `scripts/review/critic_adjustments.py` | Critic lifecycle after authorship: proposal writer, `adjudicate()`, ledger read/write. |
| `scripts/review/atomic_io.py` | Atomic JSON writes and the output-directory lock. |
| `scripts/review/evidence_manifest.py` | The path- and prose-free evidence projection telemetry shares. |
| `scripts/review/manifest_sections.py` | Pure builders for every manifest section, `aggregate_file_review()`, and the host-context projection. |
| `scripts/review/synthesis_lifecycle.py` | Dispatch and completion measurement for the reconciliator and the critic. |
| `scripts/review/telemetry.py` | JSONL telemetry and the run manifest. |
| `scripts/review/telemetry_share.py` | Repository identity, sharing consent, redaction, and upload of a completed manifest. |
| `scripts/review/user_settings.py` | Requester-side machine-local settings (`~/.config/pirategoat/config.json`). |
| `scripts/review/dependency_refresh.py` | Validating save channel for the opt-in dependency refresh report. |
| `scripts/review/workspace_setup.py` | Worktree preparation for a review. |
| `scripts/containment.py` | The single repo-boundary decision; no inline containment checks anywhere else. |
| `scripts/git_paths.py` | Git C-quoted path grammar and `FULL_SHA_RE`. |
| `scripts/hosts/` | Upstream-host discovery: `host_context.py` CLI, `chain.py` resolver chain, `resolvers/`, `scan_roots.py`, `repo_config.py`, `identity.py`, `headers.py` (leaf), `cache/` ecosystem source cache. |
| `scripts/analysis/` | Run metrics, transcript enrichment, usage snapshots, session analyzers. See `docs/analysis-tools.md`. |
| `scripts/iterative_review/` | Multi-round independent review (Codex primary, Claude Code fallback): `python3 -m iterative_review --action review\|advance`. |
| `scripts/linear/pipeline.py`, `events.py` | The 15-step Linear issue pipeline and best-effort JSONL progress events. |
| `agents/shared/reviewer-protocol.md`, `tests-reviewer-protocol.md` | Behavioral rules for all reviewers, and the extra rules for test reviewers. |
| `schemas/review-output.ts` | Types for the structured review output and ledger. |
| `../../scripts/generate_codex_compat.py` | Generates the Codex command adapters and `.codex-plugin/plugin.json`. |

## Rules

Each rule names the test that holds it where one exists. One clause of why; the docstring or doc has the rest.

**Dual host.** `commands/*.md` are canonical; `codex-skills/` adapters are generated and marked `GENERATED FILE - DO NOT EDIT`. The generator also surfaces shared skills a command references (copied to `codex-skills/<name>/` with their `references/`), so fix a skill in `skills/`, never in the copy. Canonical commands use `${CLAUDE_PLUGIN_ROOT}`; the generator prepends the Codex assignment. Shared skills use `$SKILL_DIR`, never `${CLAUDE_SKILL_DIR}`. Codex briefings dispatch native subagents that read the canonical `agents/*.md`; Claude model labels never map to another host.

**Artifact schemas.** An artifact with a `schema` field bumps it in the same commit as any shape change (key added, removed, or re-typed), updating `schemas/review-output.ts` and the changelog. The key is the integer `schema`, never `schema_version` or a `version` string. One carve-out (a change inside the same unreleased window that introduced the number) and the `version: 1` the bot owns in `review-context.json` and `issue-context.json` are in `docs/artifact-schemas.md`.

**Shared protocols.** Bootstrap includes `reviewer-protocol.md` by a skip-list of the sections it replaces with concrete values (`## Step 0`, `## Scope Discovery`, `## Output Directory`, `## ReviewOutputBuilder API`, `## File-Based Output`). Text in a skipped section reaches no reviewer, so behavioral policy never goes there; policy about what an agent does with a scope result belongs in `bootstrap.build_output()`. `TestNotDiffedContractIsDelivered` guards this.

**Bootstrap facts arrive as parameters.** `build_output()` never re-derives a fact from the rendered `scope_output` text; every fact it needs (`review_claimable_count`, `has_php`, and whatever comes next) is a required parameter computed from a structured source, so a reformat of scope.py's text cannot flip a reviewer's briefing. `TestDynamicDispatchRisk` guards this.

**Prompt order.** Bootstrap's prompt is REVIEW RULES, context sections, REVIEW CONTENT, OUTPUT INSTRUCTIONS, in that order (primacy for rules, recency for output). Keep it when editing `bootstrap.py` or the protocols.

**The ledger has one write path.** `review-findings.json` is written only through `findings_save.py` (the reconciliator) and `critic_adjustments.write_findings()` (adjudication), never with a bare `atomic_write_json`. The critic never authors ids or adjudication state, and the orchestrator never edits the committed proposal.

**The verdict ladder lives once.** `verdict_rules.verdict_for_counts()` is shared by `agent/output.py` and `critic_adjustments.py`; step 11 derives the published pipeline verdict from the ledger, so a second copy that drifts reaches GitHub.

**Derived Markdown is never hand-written.** `reviewers/<reviewer>/review.md`, `review-findings.md`, and `review-record.md` are rendered from their JSON by the pipeline. A render failure is a degradation note, never a file that disagrees with its JSON.

**Briefing prose is test-pinned.** Tests check keywords in briefing text (`"review-reconciliator"`, `"STAND"`); preserve them when rewriting and run the relevant `TestStep*` class. `handoff` is the only gate mechanism for an artifact the next step needs.

**Containment.** `tests/test_containment_contract.py` fails on any `commonpath`, `is_relative_to`, or `commonprefix` outside `scripts/containment.py`. Do not add inline checks or an allowlist.

**Registry `focus` and agent `description` stay aligned.** `focus` (5 to 10 keywords, shown in the step-5 dispatch summary) and the agent `.md` frontmatter `description` (a sentence, shown in the host's agent catalog) must cover the same capabilities whenever an agent's specialization changes.

**Subprocess tests isolate from the real repo.** A test that runs a pipeline script through `subprocess.run()` passes `cwd=tmp_path` with a temp git repo, so a git-mutating script cannot stash, checkout, or reset the working tree.

**Run tests from the repository root** after modifying scripts, agents, or commands: `pytest plugins/pirategoat-tools/tests/ -q` (about two minutes for the whole tree).

## Agent Registry

`scripts/review/agent_registry.json` configures every reviewer. Fields per entry:

| Field | Required | Description |
|---|---|---|
| `domain` | yes | Scope domain for `agent/scope.py` filtering; `null` for agents that discover their own scope (tests-mutation-reviewer). |
| `protocols` | yes | Protocol files to include: `"reviewer"` (all agents), `"tests-reviewer"` (test agents). |
| `scope_flags` | yes | Extra flags for `agent/scope.py` (for example `["--max-lines", "500"]`); `[]` for defaults. |
| `dispatch_class` | yes | `always` (every review), `conditional` (triage criteria match; dispatch-by-default when the domain has files and no evidence gate fires; skips stay visible in `agent_signals` for override), `manual` (explicit request only), `special` (synthesis agents, never triaged). |
| `focus` | yes | One-line keyword focus surfaced in the step-5 dispatch summary. Keep aligned with the agent's `description`. |
| `model_tier` | yes | `"inherit"` (caller's model), `"sonnet"`, `"opus"`, or `"haiku"`, matched to the reasoning depth needed. `tests/review/test_registry_docs.py` pins this vocabulary to the registry in both directions. |
| `triage_criteria` | conditional | Required for `conditional`. Every bullet is an executable contract: `tests/review/test_criteria_coverage.py` needs one probe diff per criterion that dispatches through the real pipeline, so a criterion without a keyword or check to back it is reworded or given one. One signal-able clause per bullet, and probes are text-neutral (`TestProbeNeutrality` blanks commit and PR text) unless the criterion is about that text. |
| `triage_keywords` | optional | Whole-word keywords (a trailing `*` declares a prefix) matched against commit messages without trailers, changed paths without repo-structural segments, the PR title and author-written body, the branch slug, linked-issue titles, and scoped patch text. Language-structural terms (`function`, `class`, `remove`) are banned by a registry test; use `triage_checks` for structure. The audited runs replay in `tests/review/test_triage_run_regressions.py`. |
| `triage_checks` | optional | Structural signals evaluated over the diff, for criteria a keyword cannot express. |
| `require_triage_keyword_match` | optional | Evidence gate: skip unless a keyword or a `triage_checks` entry fired. |
| `require_php_source_file` | optional | Evidence gate: skip unless the domain's scope holds at least one non-test `.php` file (`plan_dispatch.py` layer 2). Used by the WordPress and WooCommerce reviewers. |
| `triage_repository_keywords` | optional | Ambient repository-identity keywords matched against fetch remotes and the checkout basename. Opt in only when repository membership alone is sufficient for applicability. |
| `min_added_lines` | optional | Skip when the non-test in-scope additions fall below this count. |
| `secondary_domains`, `extra_scope`, `budget_override`, `file_history`, `max_history_commits` | optional | Extra scope domains; extra scope invocations (`["--base-ref-only"]`); a fixed tool-call budget for agents whose work does not scale with the diff; per-file git history in the prompt, and how many commits (default 5). |

Adding a reviewer:

1. Create `agents/<name>.md`, opening with the `## MANDATORY SETUP — Run Bootstrap Before Reviewing` section copied from an existing reviewer with the `--agent` name changed; `tests/review/agent/test_bootstrap_integration.py` asserts that heading verbatim.
2. Add the registry entry: domain, protocols, dispatch class, model tier, focus, and (if conditional) criteria.
3. For a conditional agent, add one probe per criterion to `tests/review/test_criteria_coverage.py`; the meta-test fails until every criterion dispatches.
4. Add the agent to `.claude-plugin/marketplace.json`, update the README table, run the generator, and run `pytest plugins/pirategoat-tools/tests/ -q` (parameterized tests include new agents automatically).

Adding a command: create `commands/<name>.md` (an orchestrator that dispatches agents; use `plan_dispatch.py` for triage decisions), add it to `marketplace.json`, add a `TestXxx` class in `tests/commands/test_commands.py`, run `python3 scripts/generate_codex_compat.py` from the repository root and commit the adapter, and update the README table. Adding a skill: `skills/<name>/SKILL.md` with `name` and `description` frontmatter, registered the same way.

## Cross-Repo Contracts: pirategoat-bot

The `pirategoat-bot` Slack bot (`~/Work/a8c/pirategoat-bot`) wraps this plugin's review and Linear pipelines. **Before changing any integration surface here, read the bot's code first**: `src/orchestrator.js` (writes `review-context.json`, reads review output), `src/github.js` (maps verdicts to GitHub actions), `scripts/pr-review.py` (outer-pipeline prompt and verdict), `src/orchestrator-linear.js` and `src/messages-linear.js` (Linear results). The shared surfaces:

- `review-context.json` and `issue-context.json`: the bot writes them; this plugin reads and enriches them. Field names, nesting, required paths, and the bot-owned `version: 1` key must match.
- Outer-pipeline verdicts (`APPROVE`/`COMMENT`/`REQUEST_CHANGES`) are the bot's layer; this plugin's `block`/`request_changes`/`comment`/`approve` are the ledger's. `orchestration.py` maps between them at step 11.
- Prompt template variables (`{{MERGE_BASE}}`, `{{GIT_RANGE}}`, and others in the bot's `prompts/`) reach this plugin through the review context.
- Terminal marker: the bot treats any `pipeline-result.json` as complete and then reads `review-report.md`, so step 11 creates `pipeline-result.json` only after that exact report exists, with `report_path` naming it; `review-record.md` and `review-findings.md` are never terminal fallbacks.
- Linear: the bot reads `pipeline-result.json` (`status`, `verdict`, `clarity_gate`, `clarity_gate_overridden`) and `clarity-assessment.json` (`summary`, `questions_for_author`). Step 8's clarity gate blocks implementation (`status: "blocked"`, `verdict: "needs_clarification"`) when a hard gate fails; the bot can override with `skip_clarity_gate: true` in `run-config.json`. `needs_more_info` means the investigation was inconclusive; `needs_clarification` means it succeeded but the issue lacks implementation clarity.
- Reviewer `*.started` markers are a contract the bot's resume path scans; synthesis agents use a different suffix (`synthesis_lifecycle.MARKER_SUFFIX`) so they are never mistaken for reviewers.

## Expected Failures

These are normal; handle them, do not stop or apologize:

- `agent/scope.py` returns an empty scope: no files match this agent's domain. Skip the agent; that is correct triage.
- Tests fail after your change: read the output, fix the root cause, re-run. Failures are feedback.
- `agent/bootstrap.py` cannot find the plugin root: run from inside the repository; it walks up from CWD looking for `.claude-plugin/`.
