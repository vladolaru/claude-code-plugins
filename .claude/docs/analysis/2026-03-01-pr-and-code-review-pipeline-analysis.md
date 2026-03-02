# pirategoat-tools: Deep Analysis of Code-Review and PR-Review Pipelines

Last updated: 2026-03-01 19:15

## Scope

This analysis covers the implemented review flows and supporting infrastructure in:

- `plugins/pirategoat-tools/README.md`
- `plugins/pirategoat-tools/commands/{full-code-review,code-review,pr-review,ingest-code-review}.md`
- `plugins/pirategoat-tools/skills/pr-reviewing/SKILL.md`
- `plugins/pirategoat-tools/agents/*` and shared protocols
- `plugins/pirategoat-tools/scripts/{review-scope,bootstrap-reviewer,ingest-code-review,review_output_simple}.py`
- Relevant tests and planning docs

Goal: identify where the system can be simplified without lowering review quality, and identify meaningful review-coverage gaps through the lens of wholesome, high-quality code review.

## Development Model

This project is AI-written and AI-maintained with human guidance and decisions. Implementation bandwidth is high (AI execution); the bottleneck is human decision bandwidth. This means:

- Structural refactors are low-cost if the design is sound.
- Multiple workstreams can run in parallel.
- "Is it worth the engineering effort?" is rarely the right question — "Is the design correct?" is.

## Intent (from README)

The intended product is clear and strong:

- Parallel specialist review with high breadth (`README.md`: "18 Review Agents", parallel by default).
- Context-aware, evidence-driven findings.
- Optional "ground truth" (tests/linters/coverage/security scanners) feeding reviewer confidence.
- Dual output (`.json` + `.md`) for machine and human consumption.

The intended bar is high: fewer false positives, stronger confidence calibration, and end-to-end PR context before code judgment.

## As-Built Pipeline

### 1. Branch-Level Reviews

#### `/full-code-review`

Flow:

1. Detect range and guardrails.
2. Scope summary + preflight (`review-scope.py --preflight`).
3. Adaptive triage for 6 conditional agents.
4. Dispatch 13 reviewers in parallel.
5. Reconcile findings.
6. Auto-run ingest workflow.

Notes:

- Dispatcher table currently includes 13 default review agents. The broader 18-agent catalog includes on-demand/independent agents that are intentionally not part of everyday dispatch.
- Reconciler runs as a separate step.

#### `/code-review` (incremental)

Flow mirrors `/full-code-review` with one additional concern:

- Persistent incremental state in `.review-state.json` with rebase/ancestor guards.

This is a good operational feature and likely one of the strongest practical value multipliers in the plugin.

### 2. PR-Level Reviews

#### `/pr-review`

This is an orchestrator-of-orchestrators:

- Phase 1: delegates to `pr-reviewing` skill, overriding interactive behavior and forcing full dispatch path.
- Phase 2: delegates to ingest validation.
- Phase 3: writes `review-report.md` and asks whether to restore branch.

The PR path is functionally rich, but composition complexity is high because it references behavior in other prompts by step number (for example, "use `/full-code-review` dispatch steps 3.5-5").

### 3. Agent Runtime Structure

All specialist agents follow a bootstrap pattern:

- `bootstrap-reviewer.py` injects shared protocol + domain-specific scope + output instructions.
- `review-scope.py` is the central scope/filter engine (domain catalog, noise filtering, merge-base rebasing, preflight).

This is a strong architectural decision and one of the best simplification anchors already present.

### 4. Post-Processing

- `review-reconciliator` aggregates outputs.
- `ingest-code-review` then validates findings for scope and false-positive handling via a 6-step guided process.

Current ingest implementation is prompt-injection guidance only (`ingest-code-review.py` prints instructions, not deterministic validation logic).

## What Is Working Well

1. Strong scope discipline and anti-noise guidance.
2. Explicit anti-false-positive protocol (`STOP CHECK`, factual-claim verification, preference filtering).
3. Incremental review support with branch-history safety checks.
4. Adaptive triage to reduce unnecessary dispatch cost.
5. Rich domain-specific checklists (security, tests, WP architecture, accessibility).

## Decision Critic: Verified Claims

The following claims were verified against the actual codebase (not just the analysis narrative):

| ID | Claim | Verification Status | Key Evidence |
|----|-------|-------------------|--------------|
| C1 | Commands duplicate large blocks of orchestration logic | **Partially verified** | 18% duplication (128/724 lines) — triage criteria, dispatch table, reconciliator invocation. 82% is context-appropriate unique logic (state tracking, rebase detection, conditional stale-branch). "Large blocks" overstates it. |
| C2 | Moving to scripts reduces prompt drift | **Failed** | Zero conflicting instructions found across all three commands today. Drift is a predicted risk, not an observed problem. |
| C3 | Ingest pipeline is purely instructional | **Verified** | `ingest-code-review.py` is a 405-line instruction printer. Zero deterministic operations. ~42% of steps are mechanical (automatable); ~58% require genuine LLM reasoning (question generation, code investigation). |
| C4 | Scope catalog omits config/CI/infra files | **Verified** | Domain catalog includes 12 source extensions (php, js, ts, jsx, tsx, css, scss, py, java, rb, go, sql). YAML, Terraform, Docker, CI workflows are not in any domain. No agent covers them. |
| C5 | Ground-truth tooling is optional, not integrated | **Verified** | No command invokes ground-truth tools. `parse-*-results.py` scripts exist but are not part of the pipeline. Agents produce full, meaningful output without ground-truth data via static code analysis. |
| A2 | Deterministic scripts can replace LLM for mechanical checks | **Partially verified** | True for ~42% of ingest (file reads, JSON parsing, scope checks, decision-table mapping). False for ~58% (creative question generation, investigative code reasoning). |
| A3 | Current false-positive rate and quality variance are material | **Uncertain** | No empirical data exists. Structurally plausible but unsubstantiated. Existing anti-FP protocol may already be sufficient. |

## Simplification Opportunities

### 1) Collapse duplicated orchestrator logic

Problem:

- `/full-code-review` and `/code-review` share ~128 lines of identical content (triage criteria, dispatch table, reconciliation contract).
- `pr-review` references behavior in other prompts by step numbers.
- While no drift exists today (C2), fixes must currently be applied in 2-3 places.

Simplification:

- Move review-plan generation into one deterministic script, e.g. `scripts/plan-review-dispatch.py`.
- Commands become thin wrappers:
  - `--mode full`
  - `--mode incremental`
  - `--mode pr`
- Single source of truth for triage criteria and dispatch policy.

Note: This is a design-cleanliness improvement, not an urgent fix. The current approach works and has no drift.

### 2) Unify agent registry and dispatch policy in code, not markdown tables

Problem:

- Agent/domain/conditional rules are duplicated across command files and docs.
- `bootstrap-reviewer.py` already has `AGENT_CONFIG`, but command dispatch tables remain separate.

Simplification:

- Add a machine-readable registry (JSON/YAML or Python module) with:
  - agent id
  - domain
  - dispatch class (`always`, `conditional`, `manual`)
  - default enablement
- Generate command docs from this registry.

### 3) Automate the mechanical portion of ingest; keep LLM for reasoning

Problem:

- `ingest-code-review.py` does not perform validation; it emits instructions for the model to execute manually over six calls.

Simplification:

- Implement deterministic preprocessing for the ~42% that is mechanical:
  - finding normalization (Step 2)
  - changed-file and changed-hunk scope checks (Step 3)
  - source attribution normalization (Step 2)
  - category pre-classification via decision table (Step 6)
- Keep LLM for the ~58% that genuinely requires reasoning:
  - verification question generation (Step 4)
  - factored code investigation and verification (Step 5)

This is a hybrid design — more architecturally complex than the current pure-prompt approach, but more reliable for the mechanical portions.

### 4) Make reconciliator deterministic for merge/dedup, model for narrative only

Problem:

- Reconciliator prompt mixes algorithmic merge behavior with narrative synthesis.
- Example snippet uses a likely incorrect import path (`repo_root/lib`), indicating spec/runtime drift.

Simplification:

- Add `scripts/reconcile-reviews.py` that does deterministic:
  - schema validation
  - dedup clustering
  - severity conflict resolution
  - source aggregation
- Reconciliator agent then only writes human summary and recommendations.

### 5) Clarify and codify agent tiers

Current state:

- The plugin has intentional tiering: everyday defaults, conditionally triaged specialists, and on-demand/independent agents.
- `gemini`, `codex`, and mutation testing are intentionally optional/manual.

Simplification:

- Keep explicit tiers and make them the canonical policy source:
  - Core default: high-signal internal reviewers
  - Conditional default: triaged specialists
  - Optional on-demand: external AI and mutation
- This is mostly product clarity and policy centralization, not a behavior change.

### 6) Integrate ground-truth tooling as a first-class pipeline stage

Problem:

- Agents repeatedly say "when available" for unified tool outputs.
- Commands do not currently run `run-*` + `parse-*` scripts.

Simplification:

- Add one pre-review stage that conditionally runs relevant tools and writes unified results into `OUTPUT_DIR`.
- Scope to changed files/ranges where possible to keep runtime bounded.

Note: Agents already produce meaningful output without ground-truth (C5 verified). This is an additive improvement, not a critical gap.

## Review Coverage Gaps (Wholesome Quality Lens)

These are material gaps not fully covered by current reviewer set and routing.

### Gap 1: Important non-code/config changes can be unreviewed

Evidence:

- `review-scope.py` domain catalog covers 12 source extensions and omits common high-risk files:
  - CI workflows (`.github/workflows/*.yml`)
  - infra/config (`*.yml`, `*.yaml`, `Dockerfile`, `*.tf`)
  - runtime config/manifest changes (`*.json` outside source)

Risk:

- Security and reliability regressions in deployment, CI permissions, or runtime config can slip through.

Recommendation:

- Add a `config-ops` domain + reviewer (or extend security/architecture scope) for YAML/infra/CI manifests.

### Gap 2: Ground truth is optional in practice, not integrated by default

Evidence:

- Agent protocols rely on `*-results-unified.json` "when available".
- Dispatcher commands do not orchestrate tool execution/parsing.

Risk:

- Review quality fluctuates by manual prep behavior.
- Potential stale data consumption if old unified files are reused.

Recommendation:

- Automatic tool stage with freshness marker tied to current `GIT_RANGE` and timestamp.

### Gap 3: Minor documentation mismatch on dispatched counts

Evidence:

- `pr-review` report template still says "Agents dispatched: <N> / 12".

Risk:

- Confusion when validating pipeline output against expected dispatch totals.

Recommendation:

- Keep tiered dispatch as-is, but enforce one canonical source for default dispatched-count constants and report template counters.

### Gap 4: Test adequacy for changed production code is partially covered

Current coverage:

- Test reviewers are strong when test files are in diff.

Missing:

- No dedicated enforcement that production behavior changes require corresponding tests.

Risk:

- Logic-heavy PRs with no tests may pass specialist checks if generalist misses test-gap severity.

Recommendation:

- Add deterministic policy check: if production code changes in risk domains and no tests changed, emit at least medium-severity test-gap unless explicitly waived.

### Gap 5: Operational resilience/observability is not first-class

Current reviewers cover security/performance/architecture/testing/a11y well.

Missing explicit lens:

- Logging/metrics/alerts
- rollback/migration safety
- feature flags / kill-switches
- failure-mode handling in distributed integrations

Recommendation:

- Either add a lightweight `reliability-reviewer` or extend `pr-reviewer` with explicit operational checklist triggers.

### Gap 6: External cross-validation governance is under-specified

Current state:

- Gemini/Codex are optional and can ingest diff/code context.

Missing:

- Clear policy gate for sensitive repos/data classes (PII/secrets/compliance boundaries).

Recommendation:

- Add a policy gate before external dispatch:
  - repo classification
  - secret/privacy check
  - explicit opt-in defaults per project.

### Gap 7: Reconciliation quality depends heavily on prompt obedience

Risk:

- Without deterministic dedup/merge logic, multi-agent overlap can still produce noisy summaries or conflicting priorities.

Recommendation:

- Deterministic merge core + model narrative layer (as above).

## Can We Simplify While Keeping Quality High?

Yes. The safest simplification strategy is:

1. Keep the agent specialization model.
2. Move orchestration mechanics from markdown prompts into deterministic scripts.
3. Reserve LLM judgment for ambiguous reasoning and prioritization.

This keeps quality high while reducing prompt-level complexity and documentation drift.

## Decisions (2026-03-01)

Each proposed change was reviewed individually with the following decisions:

| # | Item | Decision | Approach | Priority |
|---|------|----------|----------|----------|
| P0-1 | Fix doc/policy consistency | **Approved** | Fix hardcoded agent counts and tier definitions across all commands | P0 |
| P0-2 | Add config/CI/infra to scope | **Approved** | Add `config-ops` domain; extend security-reviewer and architecture-reviewer scopes (no new agent) | P0 |
| P0-3 | Quality metrics | **Approved** | Extend existing session analysis tooling (`analyze-reviewer-sessions.py`) with quality metrics extraction from JSONL logs. No pipeline changes, no workflow interruption. | P0 |
| P1-1 | Unified dispatch planner | **Approved** | Full `plan-review-dispatch.py` script. Commands become thin wrappers (`--mode full\|incremental\|pr`). | P1 |
| P1-2 | Agent registry | **Approved** | JSON file format (`agent-registry.json`). Single source of truth for all scripts. | P1 |
| P1-3 | Deterministic reconcile | **Approved** | Full dedup/merge/severity engine. Validated via deterministic test suite + shadow comparison against past reconciliator outputs. | P1 |
| P1-4 | Split ingest | **Approved** | Deterministic preprocessor (`ingest-preprocess.py`) for ~42% mechanical work + simplified ~3-step LLM reasoning for ~58% creative work. | P1 |
| P2-1 | Ground-truth integration | **Deferred** | Revisit after quality metrics (P0-3) provide data on whether ground-truth would help. | — |
| P2-2 | Reliability/observability | **Approved** | New dedicated `reliability-reviewer` agent (not checklist extension to pr-reviewer). | P2 |
| P2-3 | Test adequacy enforcement | **Approved** | Advisory/informational level only (not medium-severity finding). Deterministic detection, soft signal. | P2 |
| P2-4 | External AI governance | **Skipped** | External AI agents are already manual opt-in. Governance adds bureaucracy for little current value. | — |

### Design Decisions of Note

- **P0-2:** Extending existing agents rather than creating a new config-ops-reviewer. Security and architecture reviewers already have the domain expertise for config/CI/infra risks.
- **P0-3:** Quality metrics via post-hoc session log analysis, not inline pipeline instrumentation. Review flow must not be interrupted for metrics gathering.
- **P1-2:** JSON format chosen over Python module for portability — registry can be read by scripts in any language and by documentation generators.
- **P1-3:** Shadow comparison against real past review outputs is required before switching to deterministic reconciliation. Conservative dedup clustering preferred over aggressive.
- **P2-2:** Dedicated agent preferred over pr-reviewer extension for operational resilience. Keeps specialist focus clean.
- **P2-3:** Advisory level to avoid false-positive noise from refactors and documentation changes in production file extensions.

## Priority Plan

With AI execution capacity, multiple workstreams can run in parallel. Sequencing is driven by value and design readiness, not implementation cost.

### P0 (Parallel — do all simultaneously)

1. **Fix doc/policy consistency** (P0-1):
   - Canonical tier definitions, default dispatch count constants, report template counters.
2. **Add config/CI/infra to scope catalog** (P0-2):
   - New `config-ops` domain in `review-scope.py`. Extend security-reviewer and architecture-reviewer.
3. **Quality metrics via session analysis** (P0-3):
   - Extend `analyze-reviewer-sessions.py` with `--quality-metrics` mode. Extract FP rates, agent survival rates, overlap patterns from existing session JSONL logs.

### P1 (Next wave)

1. **Agent registry in JSON** (P1-2):
   - Implement first — P1-1 and P1-3 depend on it.
2. **Unified dispatch planner** (P1-1):
   - `plan-review-dispatch.py` reads registry. Commands become thin wrappers.
3. **Deterministic reconcile engine** (P1-3):
   - `reconcile-reviews.py` with test suite + shadow validation. Reconciliator agent becomes narrative-only.
4. **Deterministic ingest preprocessor** (P1-4):
   - `ingest-preprocess.py` for mechanical checks. `ingest-code-review.py` simplified to ~3 LLM reasoning steps.

### P2 (Later)

1. **Reliability-reviewer agent** (P2-2):
   - New agent for logging, rollback safety, feature flags, failure-mode handling.
2. **Test adequacy advisory** (P2-3):
   - Deterministic detection of production changes without test changes. Informational finding.

### Deferred

- **Ground-truth integration** (P2-1): Revisit after P0-3 quality metrics provide data.

### Skipped

- **External AI governance** (P2-4): Manual opt-in is sufficient governance for current usage.

## Direct Answer to the Core Question

There are clear simplification opportunities without sacrificing quality, and real coverage gaps to address.

The verified gaps today are:

- Config/CI/infra files excluded from scope (C4 verified).
- No quality metrics to measure pipeline effectiveness (A3 uncertain).
- Ingest is purely instructional with no deterministic preprocessing (C3 verified, 42% automatable).
- Reconciliation lacks deterministic dedup/merge (Gap 7).
- Doc/count mismatches (Gap 3).

The claimed-but-unverified concerns:

- Prompt drift (C2 failed — no drift exists today, but centralization is still cleaner design).
- Quality variance in current pipeline (A3 uncertain — no data; instrument before assuming).

The existing architecture has strong foundations (`review-scope.py`, bootstrap protocol, adaptive triage). The priority plan sequences work by design readiness and dependency order, not by implementation cost — which is low across the board with AI execution.
