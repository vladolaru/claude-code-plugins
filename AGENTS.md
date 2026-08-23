# AGENTS.md

You maintain a dual-host Claude Code and Codex plugin marketplace. This file is your operating manual - follow it when working on any plugin in this repo.

## Repository Overview

This is **vladolaru-claude-code-plugins** - Vlad Olaru's personal Claude Code and Codex plugin marketplace featuring specialized plugins for development workflows, WordPress backend development, and AI-powered tools.

## Development Model

This project is AI-written and AI-maintained. The human (Vlad) sets direction, makes architectural decisions, and reviews work. Claude Code agents do the implementation, testing, analysis, and maintenance. "Single maintainer" does not mean capacity-constrained — it means single human decision-maker with AI execution capacity. Do not assume limited implementation bandwidth when reasoning about priorities or feasibility.

No agent carries context between sessions — every agent reads the code cold. This has practical implications:

- **Prefer single canonical implementations** over duplicated patterns. An agent will copy whichever pattern it encounters first; if two conventions exist for the same thing, drift is inevitable.
- **Constants, types, and named helpers are discovery mechanisms.** They are more valuable here than in a human-authored codebase — they are the primary way agents find the "right" way to do something.
- **Consolidating duplicated logic is drift prevention**, not polish. Treat it accordingly when prioritizing work.

## Architecture

### Plugin Structure

```text
vladolaru-claude-code-plugins/
├── .agents/
│   └── plugins/
│       └── marketplace.json      # Generated Codex marketplace
├── .claude-plugin/
│   └── marketplace.json          # Canonical marketplace registry
├── CLAUDE.md                     # Claude Code shim -> @AGENTS.md
├── AGENTS.md                     # Canonical shared instructions
├── plugins/
│   └── plugin-name/
│       ├── .codex-plugin/
│       │   └── plugin.json       # Generated Codex manifest
│       ├── codex-skills/         # Generated command adapters (optional)
│       ├── CHANGELOG.md          # Version history
│       ├── agents/               # Subagent definitions (optional)
│       │   └── agent-name.md
│       ├── commands/             # Slash commands (optional)
│       │   └── command.md
│       ├── skills/               # Skills with SKILL.md files (optional)
│       │   └── skill-name/
│       │       └── SKILL.md
│       └── scripts/              # Helper scripts (optional)
├── scripts/
│   └── generate_codex_compat.py  # Deterministic host adapter generator
├── LICENSE
└── README.md
```

### Dual-Host Source Policy

Claude Code marketplace entries and command files are the canonical source.
Codex packaging is generated deterministically:

| Canonical source | Generated Codex output |
|---|---|
| `.claude-plugin/marketplace.json` | `.agents/plugins/marketplace.json` and each `.codex-plugin/plugin.json` |
| `plugins/*/commands/*.md` | `plugins/*/codex-skills/<command>/SKILL.md` and `agents/openai.yaml` |

Never hand-edit files marked `GENERATED FILE - DO NOT EDIT`. After changing a
plugin entry or command, run:

```bash
python3 scripts/generate_codex_compat.py
python3 scripts/generate_codex_compat.py --check
```

Shared skills, scripts, hooks, and agent definitions remain canonical files
used directly by both hosts. Use `$SKILL_DIR` in shared skill prose and define
it as the directory containing the current `SKILL.md`. Host adapters resolve
the actual absolute path.

Codex command adapters use explicit invocation by default. Do not copy Claude
model names into Codex configuration. The pirategoat review pipeline passes
canonical reviewer definitions to native Codex subagents at runtime.

### Skills Specification

All skills must have a `SKILL.md` file with YAML frontmatter:

- **Required frontmatter fields**:
  - `name` - hyphen-case, lowercase alphanumeric + hyphens
  - `description` - when the host should use this skill
- **Optional frontmatter fields**:
  - `license`
  - `metadata` - custom key-value pairs
- **Body**: Markdown instructions, examples, and guidelines

### Claude Code Agent Model Routing

Subagents can run on a different model than the parent session. A Sonnet parent can dispatch Opus subagents (and vice versa) — there is no restriction on upgrading or downgrading.

**Precedence order** (highest wins):

1. `model` parameter on the `Agent` tool call (e.g., `model: "opus"`)
2. `model` field in the agent definition frontmatter (e.g., `model: sonnet`)
3. `inherit` — use the parent session's model (this is the default)

**Practical implications:**

- Use `model: opus` in agent definitions or Agent calls when the task requires deep reasoning (judgment-heavy synthesis, complex architectural analysis)
- Use `model: sonnet` for standard analytical work (most review agents, pattern matching)
- Use `model: haiku` for mechanical tasks (shell command dispatch, template generation)
- Use `inherit` when the agent should match whatever the user chose for their session
- Billing reflects the actual model used per subagent, not the parent session model

**Built-in examples:** Claude Code's own `claude-code-guide` agent is hardcoded to Haiku; `statusline-setup` to Sonnet — these run at their specified model regardless of the parent.

**Full reference:** See the [Claude Code subagents documentation](https://code.claude.com/docs/en/sub-agents) for the complete specification — all frontmatter fields, tool restrictions, permission modes, hooks, MCP server scoping, persistent memory, and example subagents.

These model labels are Claude Code routing metadata. Codex review adapters
intentionally omit them and use the current Codex subagent configuration.

### Accessing Claude Code Documentation

Claude Code docs live at `https://code.claude.com/docs/en/`. Every page has a `.md` variant that returns raw markdown — much more efficient for agent consumption via `WebFetch` than the HTML page.

**Pattern:** append `.md` to the URL path.

| HTML page | Raw markdown |
|---|---|
| `https://code.claude.com/docs/en/sub-agents` | `https://code.claude.com/docs/en/sub-agents.md` |
| `https://code.claude.com/docs/en/skills` | `https://code.claude.com/docs/en/skills.md` |
| `https://code.claude.com/docs/en/hooks` | `https://code.claude.com/docs/en/hooks.md` |

**Full index:** Fetch `https://code.claude.com/docs/llms.txt` to discover all available documentation pages.

## Plugin Inventory

### pirategoat-tools

Code review orchestration with 34 agents (28 domain reviewers, 2 pipeline, 2 cross-validators, 2 utility), 21 shared skills, 7 commands, and 7 generated Codex command adapters covering security, architecture, testing, WordPress, WooCommerce regression invariants, accessibility, API contracts, data privacy, concurrency, code clarity, documentation drift, reference integrity, and more. Has its own `CLAUDE.md` and `AGENTS.md` with pipeline architecture, agent registry reference, and development workflows.

| Directory | Contents |
|---|---|
| `agents/` | 34 agent definitions (28 reviewers, 2 pipeline, 2 cross-validators, 2 utility) + 2 shared protocols in `agents/shared/` |
| `skills/` | 21 shared reference skills |
| `codex-skills/` | 7 generated Codex command adapters |
| `commands/` | 7 slash commands (`/pr-review`, `/full-code-review`, `/code-review`, `/iterative-review`, `/pr-update`, `/copy-as`, `/switch-to`) |
| `scripts/` | Domain packages: `review/` (pipeline facade, pipeline_contract, briefings, orchestration, plan_dispatch, dispatch_status, context, telemetry, synthesis_lifecycle, agents_status, critic, critic_adjustments, atomic_io, reviewer_names, workspace_setup, dependency_refresh, user_settings, agent_registry.json + `agent/` bootstrap, scope, output, diff_noise_filter), `hosts/` (host_context CLI, repo-signaled advisory chain for upstream runtime-hosts/library-deps, standalone resolver helpers, ecosystem_cache CLI for machine-wide WordPress/WooCommerce source cache management), `linear/` (pipeline, events), `figma/` (spec extraction, node parsing), `analysis/` (supported review-run/cohort metrics, privacy-preserving transcript enrichment, durable per-run token-usage snapshot, session analyzer, general metrics), `iterative_review/` (multi-round independent review — Codex primary, Claude Code fallback) |
| `schemas/` | TypeScript type definitions for structured review output |
| `tests/` | Deterministic eval suite — see [Testing](#pirategoat-tools-1) section |
| `AGENTS.md` | Full development instructions, architecture, agent registry reference |

**Dev notes:** Agents run in parallel by default. Model tier assignment matters (`inherit`/`sonnet`/`haiku` based on reasoning depth).

### dex

Knowledge capture system — turns lessons, patterns, decisions, and research into agent-first documents that compound engineering work.

| Directory | Contents |
|---|---|
| `commands/` | 7 commands: `/dex:grok`, `/dex:learn`, `/dex:pattern`, `/dex:research`, `/dex:sharpen`, `/dex:init`, `/dex:status` |
| `skills/` | 1 shared skill (`knowledge-capture`) |
| `codex-skills/` | 7 generated Codex command adapters + the surfaced `knowledge-capture` skill |
| `scripts/` | `analyze-subagents.py` — subagent dispatch analysis tool |
| `tests/` | Plugin structure tests, subagent analyzer tests, fixtures |

**Dev notes:** Adapts to host project automatically (`CLAUDE.md` + `.claude/docs/` or `AGENTS.md` + `.ai/docs/`). Instructions file has a 550-line budget enforced; promotes rules as one-liners with links to full docs. No external dependencies.

### prompt-engineer

Systematic prompt optimization with evidence-grounded technique recommendations from a 50+ technique reference library. Human-in-the-loop gates between phases.

| Directory | Contents |
|---|---|
| `skills/` | 1 shared skill (`prompt-engineer`) |
| `codex-skills/` | 1 generated Codex command adapter + the surfaced `prompt-engineer` skill |
| `commands/` | 1 command (`/optimize-prompt`) |

**Dev notes:** Simple prompts stay simple (triage filters out low-complexity cases). Works on any prompt format (SKILL.md, agent definitions, slash commands, CLAUDE.md, API prompts). No external dependencies.

### image-optimizer

Lossless image optimization for PNG, JPEG, GIF, SVG with before/after review and confirmation gate.

| Directory | Contents |
|---|---|
| `commands/` | 1 command (`/optimize-images`) |
| `codex-skills/` | 1 generated Codex command adapter |

**Dev notes:** Requires `imageoptim-cli` (macOS only, via npm) for raster images and/or `svgo` (cross-platform, via npm) for SVGs. At least one must be installed.

### yoloing-safe

YOLO mode safety net - PreToolUse hook that blocks destructive commands and
gates risky operations with host-aware behavior. Has its own `CLAUDE.md` and
`AGENTS.md` with detailed development instructions.

| File/Directory | Contents |
|---|---|
| `scripts/pre-tool-use-safety.py` | Runtime entrypoint — compatibility shim re-exporting from the internal package |
| `scripts/yoloing_safe/` | Internal implementation package: config, shell parsing, path handling, registry, runtime, and domain rule modules (`rules/filesystem.py`, `rules/git.py`, `rules/network.py`, `rules/system.py`) |
| `hooks/hooks.json` | Hook registration for PreToolUse event |
| `tests/` | Unit, integration, meta, and e2e tests |
| `AGENTS.md` | Full development instructions, rule workflows, testing |

**Dev notes:** Four tiers, first-match-wins: allowlist → block (exit 2) → ask (JSON permission prompt) → allow (silent). Self-protection: blocks its own config from modification (hardcoded, undisableable). Fails open — if the hook errors, the tool call proceeds. 5-second timeout. See [Testing](#yoloing-safe-1) section. Python stdlib only.

### caffeinate-claude

Keeps your Mac awake during Claude Code and Codex sessions using macOS
`caffeinate`. Supports multiple tabs and handles crashes gracefully.

| File/Directory | Contents |
|---|---|
| `hooks/` | `UserPromptSubmit` (starts caffeinate), `Stop` (kills when no sessions remain) |

**Dev notes:** Multi-tab support via session markers (`$PPID` per tab). Auto-prunes markers for dead processes. 1-hour safety timeout as failsafe. macOS-only. No external dependencies.

## Creating New Plugins

1. Create directory: `plugins/<name>/` with subdirs as needed (`skills/`, `commands/`, `agents/`, `scripts/`)
2. Add a `CHANGELOG.md` following [Keep a Changelog](https://keepachangelog.com/) format
3. Register in `.claude-plugin/marketplace.json` — follow the structure of existing entries
4. Update the [Plugin Inventory](#plugin-inventory) section in this file with counts and contents
5. Update `README.md` directory tree with the new plugin entry
6. Run `python3 scripts/generate_codex_compat.py` to create the Codex manifest and marketplace entry

## Adding Commands, Skills, or Agents to Existing Plugins

**Every new command, skill, or agent requires doc updates in multiple locations.** Plugins with their own `AGENTS.md` (like pirategoat-tools) document the full checklist there. The common locations are:

| # | File | What to update |
|---|------|----------------|
| 1 | `.claude-plugin/marketplace.json` | Add entry to the plugin's `commands`, `skills`, or `agents` array |
| 2 | Plugin's `README.md` | Update count + add to the relevant table |
| 3 | This file → [Plugin Inventory](#plugin-inventory) | Update summary count + contents row for the plugin |
| 4 | Root `README.md` | Update count in directory tree |
| 5 | Generated Codex adapters | Run `python3 scripts/generate_codex_compat.py` and commit the output |

Skipping these updates causes stale counts that mislead future agents and humans.

## Testing

### pirategoat-tools

The `plugins/pirategoat-tools/tests/` directory contains deterministic evals (no model calls) for plugin scripts. See `tests/TESTING.md` for the full framework documentation: architecture, design principles, how to add tests/graders/scenarios, and conventions.

**When to run tests:** After modifying any of these files, run the relevant test suite:

| Changed file | Run |
|---|---|
| `scripts/review/agent/bootstrap.py` | `pytest plugins/pirategoat-tools/tests/review/agent/test_bootstrap.py plugins/pirategoat-tools/tests/review/agent/test_bootstrap_integration.py -v` |
| `agents/shared/reviewer-protocol.md` | `pytest plugins/pirategoat-tools/tests/review/agent/test_bootstrap_integration.py -v` |
| `agents/shared/tests-reviewer-protocol.md` | `pytest plugins/pirategoat-tools/tests/review/agent/test_bootstrap_integration.py -v` |
| `scripts/review/pipeline.py` | `pytest plugins/pirategoat-tools/tests/review/test_pipeline_infra.py -v` |
| `scripts/review/pipeline_contract.py` | `pytest plugins/pirategoat-tools/tests/review/test_pipeline.py plugins/pirategoat-tools/tests/review/test_pipeline_infra.py plugins/pirategoat-tools/tests/review/test_pipeline_integration.py -v` |
| `scripts/review/briefings.py` | `pytest plugins/pirategoat-tools/tests/review/test_pipeline.py -v` |
| `scripts/review/orchestration.py` | `pytest plugins/pirategoat-tools/tests/review/test_pipeline_integration.py plugins/pirategoat-tools/tests/review/test_orchestration_hygiene.py plugins/pirategoat-tools/tests/review/test_critic_adjustments.py plugins/pirategoat-tools/tests/review/test_synthesis_lifecycle.py plugins/pirategoat-tools/tests/review/test_report_assembly.py -v` (hygiene covers the step-3 baseline / step-11 sweep and the step-11 usage capture; critic-adjustments covers step 11's defensive adjustment re-run and verdict sync; synthesis-lifecycle covers the step-8/10 dispatch markers and the step-9/11 observations; report-assembly covers the step-9/11 `review-record.md` assembly seams) |
| `scripts/review/orchestration.py` review-record assembler (`assemble_review_record`, `_render_run_notes`, `_render_record_verdict_line`) or `render_review_body` in `scripts/review/agent/output.py` | `pytest plugins/pirategoat-tools/tests/review/test_report_assembly.py plugins/pirategoat-tools/tests/review/agent/test_output.py -v` (the record's shared body IS `render_review_body`, so a change to either lands in both) |
| `scripts/review/dispatch_status.py` | `pytest plugins/pirategoat-tools/tests/review/test_agents_status.py plugins/pirategoat-tools/tests/review/test_pipeline.py plugins/pirategoat-tools/tests/review/test_pipeline_infra.py plugins/pirategoat-tools/tests/review/test_pipeline_integration.py plugins/pirategoat-tools/tests/review/test_plan_dispatch.py plugins/pirategoat-tools/tests/review/test_telemetry.py plugins/pirategoat-tools/tests/analysis/test_review_run_metrics.py -v` |
| `scripts/containment.py` | `pytest plugins/pirategoat-tools/tests/test_containment_contract.py plugins/pirategoat-tools/tests/hosts/ plugins/pirategoat-tools/tests/review/test_review_config.py plugins/pirategoat-tools/tests/review/test_telemetry.py -v` |
| `scripts/review/atomic_io.py` | `pytest plugins/pirategoat-tools/tests/review/test_atomic_io.py -v` |
| `scripts/review/plan_dispatch.py` | `pytest plugins/pirategoat-tools/tests/review/test_plan_dispatch.py plugins/pirategoat-tools/tests/review/test_criteria_coverage.py -v` |
| `scripts/review/agent_registry.json` (triage criteria/keywords/checks) | `pytest plugins/pirategoat-tools/tests/review/test_criteria_coverage.py plugins/pirategoat-tools/tests/review/test_plan_dispatch.py -v` (every criterion bullet needs a dispatching probe) |
| `plugins/pirategoat-tools/AGENTS.md` agent-registry reference (`model_tier` row) or `scripts/review/agent_registry.json` `model_tier` values | `pytest plugins/pirategoat-tools/tests/review/test_registry_docs.py -v` |
| `scripts/review/context.py` | `pytest plugins/pirategoat-tools/tests/review/test_context.py -v` |
| `scripts/review/dependency_refresh.py` | `pytest plugins/pirategoat-tools/tests/review/test_dependency_refresh.py -v` |
| `scripts/review/user_settings.py` | `pytest plugins/pirategoat-tools/tests/review/test_user_settings.py plugins/pirategoat-tools/tests/review/test_pipeline_infra.py -v` |
| `scripts/review/reconciliation_context.py` | `pytest plugins/pirategoat-tools/tests/review/test_reconciliation_context.py plugins/pirategoat-tools/tests/review/test_report_assembly.py -v` (report-assembly covers `strip_severity_floor_markers`, the module's one cross-module export). Changing `compute_missing_agents` or `annotate_prefiltered_findings` also means re-reading `agents/review-reconciliator.md`: the agent carries those measurements rather than recomputing them, so the contract and the computation are one change. |
| `scripts/review/reviewer_names.py` | `pytest plugins/pirategoat-tools/tests/review/agent/test_bootstrap_integration.py plugins/pirategoat-tools/tests/review/test_agents_status.py plugins/pirategoat-tools/tests/review/test_telemetry.py -v` (bootstrap pins the derivation directly; agents-status and telemetry exercise it indirectly through `agents_status.py` and `manifest_sections.py`'s coverage builders, its other importers) |
| `scripts/review/agents_status.py` | `pytest plugins/pirategoat-tools/tests/review/test_agents_status.py -v` |
| `scripts/review/agent/scope.py` | `pytest plugins/pirategoat-tools/tests/review/agent/test_scope.py plugins/pirategoat-tools/tests/review/agent/test_scope_routing.py -v` |
| `scripts/review/agent/diff_noise_filter.py` | `pytest plugins/pirategoat-tools/tests/review/agent/test_diff_noise_filter.py -v` |
| `scripts/review/agent/output.py` | `pytest plugins/pirategoat-tools/tests/review/agent/test_output.py plugins/pirategoat-tools/tests/grading/test_graders.py -v` |
| `scripts/review/telemetry.py` | `pytest plugins/pirategoat-tools/tests/review/test_telemetry.py -v` |
| `scripts/review/synthesis_lifecycle.py` | `pytest plugins/pirategoat-tools/tests/review/test_synthesis_lifecycle.py -v` (the module plus its four orchestration seams) |
| `scripts/review/manifest_sections.py` | `pytest plugins/pirategoat-tools/tests/review/test_telemetry.py -v` |
| `scripts/review/pipeline.py` stale-artifact sweep (`_SWEEP_ALLOWLIST`) | `pytest plugins/pirategoat-tools/tests/review/test_pipeline_infra.py -v` |
| `scripts/review/critic.py` | `pytest plugins/pirategoat-tools/tests/review/test_critic.py -v` |
| `scripts/review/critic_adjustments.py` | `pytest plugins/pirategoat-tools/tests/review/test_critic_adjustments.py -v` |
| `scripts/review/verdict_rules.py` | `pytest plugins/pirategoat-tools/tests/review/test_verdict_rules.py plugins/pirategoat-tools/tests/review/agent/test_output.py plugins/pirategoat-tools/tests/review/test_critic_adjustments.py -v` (the shared ladder plus both callers — output.py publishing a review and critic_adjustments recomputing the ledger verdict) |
| `scripts/review/findings_save.py` | `pytest plugins/pirategoat-tools/tests/review/test_findings_save.py -v` |
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
| `scripts/analysis/session_metrics.py` | `pytest plugins/pirategoat-tools/tests/analysis/test_session_metrics.py -v` |
| `scripts/analysis/session_analyzer.py` | `pytest plugins/pirategoat-tools/tests/analysis/test_session_analyzer.py -v` |
| `scripts/analysis/review_transcript.py` | `pytest plugins/pirategoat-tools/tests/analysis/test_review_transcript.py -v` |
| `scripts/analysis/review_run_metrics.py` or `scripts/analysis/review_metrics/*.py` | `pytest plugins/pirategoat-tools/tests/analysis/test_review_run_metrics.py -v` |
| `scripts/analysis/codex_rollout.py` | `pytest plugins/pirategoat-tools/tests/analysis/test_codex_rollout.py -v` |
| `scripts/analysis/codex_session_analyzer.py` or `scripts/analysis/codex_session_metrics.py` | `pytest plugins/pirategoat-tools/tests/analysis/test_codex_session_scripts.py -v` |
| `scripts/analysis/usage_snapshot.py` | `pytest plugins/pirategoat-tools/tests/analysis/test_usage_snapshot.py plugins/pirategoat-tools/tests/review/test_orchestration_hygiene.py plugins/pirategoat-tools/tests/review/test_telemetry.py -v` (the CLI, the step-11 seam that invokes it, and `ReviewTelemetry.reproject_usage()` — the manifest's own out-of-band `usage` patch a manual re-run calls into) |
| `tests/helpers/graders.py` | `pytest plugins/pirategoat-tools/tests/grading/test_graders.py -v` |
| `tests/grading/eval_agent_compliance.py` | `pytest plugins/pirategoat-tools/tests/grading/test_eval_agent_compliance.py -v` |
| Any `SCENARIOS` answer key or `tests/fixtures/*.diff` | `pytest plugins/pirategoat-tools/tests/grading/test_answer_keys.py -v` |
| Any reviewer agent `.md` | `pytest plugins/pirategoat-tools/tests/review/agent/test_bootstrap_integration.py -v` (verifies agent config still works) |
| New agent added to `AGENT_CONFIG` | `pytest plugins/pirategoat-tools/tests/review/agent/test_bootstrap_integration.py -v` (auto-included in all parameterized tests) |
| Any review command `.md` | `pytest plugins/pirategoat-tools/tests/commands/test_commands.py -v` (validates structure, agent refs, script refs) |
| `commands/pr-update.md` | `pytest plugins/pirategoat-tools/tests/commands/test_commands.py -v` |
| `commands/switch-to.md` | `pytest plugins/pirategoat-tools/tests/commands/test_commands.py -v` |
| `.claude-plugin/marketplace.json` | `pytest plugins/pirategoat-tools/tests/commands/test_commands.py -v` (validates command registration, agent cross-refs) |
| Any marketplace entry, command, or dual-host adapter | `pytest plugins/pirategoat-tools/tests/test_codex_marketplace.py -v` |
| `scripts/generate_codex_compat.py` | `pytest plugins/pirategoat-tools/tests/test_codex_marketplace.py -v` |

**Run all tests:** `pytest plugins/pirategoat-tools/tests/ -v 2>&1 | tail -30`

**Run every plugin's tests in one session:** `pytest plugins/ 2>&1 | tail -5` — this works because the root `pytest.ini` pins `--import-mode=importlib` and test trees carry no `__init__.py` (guarded by `plugins/pirategoat-tools/tests/test_pytest_layout.py`; see it before adding an `__init__.py` under any `tests/` tree).

**Test principles:**
- Code-based graders only (fast, deterministic, no model calls)
- Grade outcomes not paths
- Test both positive and negative cases
- Parameterize on the axis of variation — ALL_AGENTS only for smoke tests (see `tests/TESTING.md` §6-7)
- Test pure functions directly; use subprocess only for orchestration that unit tests can't cover

**Offline compliance grading** (no model calls):
```bash
# Grade existing review output files
python3 plugins/pirategoat-tools/tests/grading/eval_agent_compliance.py --grade-only /tmp/pr-review-<N>
```

### yoloing-safe

See `plugins/yoloing-safe/AGENTS.md` for testing instructions, rule workflows (add/remove/rename/retier), and the full RULES dict specification.

**Quick reference:** `pytest plugins/yoloing-safe/tests/ -v`

## Versioning & Releases

### RULE 0: Every Change Gets Documented

Every commit that modifies plugin behavior (features, fixes, refactors, performance) **must** include:

1. **CHANGELOG.md update** — Add an entry under the appropriate version section in the plugin's `CHANGELOG.md`
2. **Version bump in marketplace.json** — Update the plugin's `version` field following semver (`feat` = minor, `fix`/`refactor`/`perf` = patch, `BREAKING CHANGE` = major)

**Coalescing rule:** If the latest version bump has not been pushed to the remote yet, fold new changes into the same version entry rather than bumping again — provided they are of similar impact (e.g., two fixes, or a feature and a closely related fix). If the new change is a higher semver impact (e.g., existing unpushed patch + new feature), upgrade the version to match.

**Exempt from version bumps:** `docs`, `test`, `ci`, `style`, `chore` commits that don't change runtime behavior. Still add a changelog entry if the change is notable.

### Changelog Entry Style

The changelog's audience is a plugin user deciding whether a change affects
them. The why-narrative, evidence, field-run numbers, and mechanism tour live
in the commit body — git is the archive; never duplicate it into the changelog.

- **One bullet per user-visible behavior, not per commit.** A follow-up fix to
  an UNRELEASED entry folds into the bullet that introduced the behavior —
  edit that bullet; never append a correction trail beneath it.
- **One sentence per bullet; two at most**, and only when the second states a
  consequence the first cannot carry. No bold-lead paragraph essays, no test
  counts, no file-by-file tours.
- **Purely internal changes** (refactors, test estate, doc wording, analysis
  tooling performance) get no bullet unless a consumer would notice.
- **The two-sentence test:** if a bullet cannot be written in two sentences,
  it is either several behaviors (split it) or commit-body detail (cut it).

Why this is a rule and not taste: the unreleased 1.114.0 entry twice grew past
20KB of essay bullets and had to be distilled (121.6KB → 9.3KB → regrown to
24KB → distilled again). Agents copy whichever pattern the file already shows,
so the entry style is load-bearing — a single essay bullet re-seeds the drift.

### Plugin-Prefixed Tags

Since this repository may contain multiple plugins with independent version cycles, use **plugin-prefixed tags**:

**Tag Format:** `<plugin-name>/v<semver>`

**Examples:**
- `pirategoat-tools/v1.0.0`
- `pirategoat-tools/v1.1.0`

### Release Process

1. Update the plugin's `CHANGELOG.md` (Keep a Changelog format)
2. Bump `version` in `.claude-plugin/marketplace.json`
3. Run `python3 scripts/generate_codex_compat.py` to synchronize the Codex manifest
4. Commit, then tag: `<plugin-name>/vX.Y.Z`
5. Optionally create a GitHub Release with `gh release create`

## AI Artifacts

All AI-generated artifacts (plans, analysis, research, decisions, learnings) go under `.claude/docs/`:

```text
.claude/docs/
├── analysis/     # Research findings, investigations, session analysis
├── decisions/    # Architecture Decision Records
├── learnings/    # Debugging insights, gotchas, fixes
├── patterns/     # Reusable workflows, conventions, anti-patterns
├── plans/        # Implementation plans
└── research/     # Deep-dive research (e.g., a11y/, figma/)
```

**RULE:** Never create AI artifacts under `docs/` at the repo root or under `plugins/*/docs/`. Those locations are for committed documentation that ships with the plugin (guides, READMEs). Working artifacts from agent sessions go in `.claude/docs/`.

## Reference

**Design patterns** live in `docs/patterns/`. Consult them before implementing similar functionality:

| Pattern | When to use |
|---|---|
| [step-by-step-prompt-injection](docs/patterns/step-by-step-prompt-injection.md) | Multi-phase analytical workflows where later steps must be independent of earlier conclusions — e.g., verify before judge, gather before synthesize. Includes script template, skill file structure, testing checklist, and two reference implementations. |
| [curated-context-pipeline](docs/patterns/curated-context-pipeline.md) | Multi-mode LLM pipelines where a script acts as context curator — reads all state, presents pre-digested briefings, controls flow via condition-driven step routing, and manages split file-based state. Covers pipeline identity anchoring, artifact discipline, and conversational output. Evolves step-by-step prompt injection for operational workflows with shared logic across modes. |

**External tool references** live in `docs/`. Consult them when integrating with external CLIs:

| Reference | When to use |
|---|---|
| [codex-cli-reference](docs/codex-cli-reference.md) | Integrating with the OpenAI Codex CLI — prompting contracts, structured output, headless review invocation, cross-CLI comparison with Claude Code, sandbox behavior, and Structured Outputs schema constraints. Based on source analysis + runtime testing. |
| [claude-code-cli-reference](docs/claude-code-cli-reference.md) | Spawning Claude Code CLI as a subprocess — nesting guard status, isolation flags, structured output via `--json-schema`, settings hierarchy, Python integration pattern, and cross-CLI comparison with Codex. Based on v2.1.81 source analysis + runtime testing. |
| [plugin-env-vars-reference](docs/plugin-env-vars-reference.md) | Plugin environment variables — `CLAUDE_PLUGIN_ROOT`, `CLAUDE_SKILL_DIR`, `CLAUDE_PLUGIN_DATA`: substitution matrix (what works where), substitution order, decision guide, known bugs, and source code function references. Based on v2.1.87 source analysis. |

**Knowledge capture:** After significant debugging sessions, architectural decisions, or discovering non-obvious behavior, suggest using `/dex:grok` to capture the knowledge.

**License:** MIT — see LICENSE file.
