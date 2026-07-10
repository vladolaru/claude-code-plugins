# woo-regression-reviewer — Design

**Date:** 2026-07-10
**Status:** Approved (design LGTM'd by Vlad; spec transcribed from the approved design)
**Analysis:** `.claude/docs/analysis/2026-07-09-claude-woo-regression-agent-gap-analysis.md` (local, untracked)

## Problem

The production AI regression-review pipeline (`ai-review-cli/ai-regression-review`, tuned WooCommerce prompts at `prompts/woocommerce/woocommerce/`) catches regressions in merged WooCommerce PRs that the pirategoat-tools multi-agent review pipeline missed pre-merge. Gap analysis found ~12 WooCommerce ecosystem invariant families with no owner among the existing 30+ pirategoat reviewer agents, plus three structural mechanisms the regression pipeline relies on that pirategoat lacks:

1. **Mandatory per-hunk invariant audit** — forced `APPLIES | DOES_NOT_APPLY | UNCERTAIN` enumeration per invariant per hunk (the regression prompt's own provenance note credits this as the key lift).
2. **Dismissal auditing** — a second pass promotes soft dismissals ("pre-existing", "unlikely", "guarded elsewhere", "couldn't verify") to Medium findings.
3. **Severity floors + verified-mitigation rule** — no downgrades on blast-radius descriptors ("Internal namespace", "only one in-tree implementor", "unreleased", "rare in practice"); mitigations used to dismiss must be verified at file:line.

## Goal

A new pirategoat-tools reviewer agent that carries the WooCommerce regression invariants and methodology, dispatched only for WooCommerce core or WooCommerce extension changes (triaged out everywhere else via existing dispatch mechanics), plus a scoped hardening of the reconciliator so the agent's findings are not soft-downgraded downstream.

## Decisions (made with Vlad)

- **Single agent**, mirroring the proven regression prompt 1:1, rather than splitting invariant families across agents or patching existing agents.
- **Harden the reconciliator** with scoped severity-floor and verified-mitigation rules — without this, the new agent's findings risk dying downstream, the exact failure mode being fixed.
- **Design + implement** in this repo, ready for Vlad to sync to pirategoat-bot.

## Component 1: the agent — `plugins/pirategoat-tools/agents/woo-regression-reviewer.md`

Standard pirategoat agent format (frontmatter, mandatory bootstrap section, shared reviewer protocol via bootstrap).

**Frontmatter:** `name: woo-regression-reviewer`, `model: opus`, tools `Read, Glob, Grep, Bash, Write, WebSearch`.

**Identity & provenance.** WooCommerce ecosystem regression reviewer. The prompt keeps an explicit provenance note: the invariants were derived from a corpus of real, shipped WooCommerce regressions (introducing-PR → fix-PR pairs) via the production regression-review pipeline; the per-hunk audit exists because free-form "speculate then validate" review demonstrably skipped applicable invariants. This note is load-bearing — it prevents future editors from softening the mechanism.

**Invariant checklist** (ported from `review.md`, adapted to pirategoat conventions):

1. Sessions/users — WP sessions and `WP_User` identity are not 1:1 (User Switching, impersonation, B2B portals).
2. Templates/themes — `templates/` files are theme-overridable; changes don't reach overridden copies; non-default render paths (page builders, app SDKs).
3. Scheduled actions — callback class autoloadability from the Action Scheduler runner context.
4. Scheduled actions — `unique=true` silently kills self-rescheduling jobs.
5. Scheduled actions — WP-cron → Action Scheduler migration must clear old cron events.
6. Scheduled actions — serialized/transported hook args must preserve downstream consumer contracts (enumerate producers and consumers across the serialization boundary).
7. Hooks — filter return-type variance: type-strict consumption silently drops legitimate extension behavior.
8. Hooks — removing/renaming `apply_filters`/`do_action` is a public API break.
9. Hooks — hot-path firing frequency: downstream amplification (Jetpack Sync, webhooks, analytics).
10. Data/meta — meta values are arbitrary serialized data; equality checks must handle arrays/objects; sync-on-read must compare-and-only-write (infinite write-loop risk); `get_post_meta(..., true)` returns `''` not `null`; persisted state can pre-date the schema.
11. External data — shape-validate anything from AssetDataRegistry, transients, options, filterable surfaces; validate subclass properties in class introspection.
12. PHP version coercion — PHP 8.4 arithmetic on `''` fatals; related 8.x strictness changes.
13. Defaults — flipping a default from "working" to "broken-until-JS-restores-it" regresses every consumer that doesn't load the script.
14. Defaults — new strict validators on historically permissive input are silent regressions.
15. Migrations/upgrades — upgrades run on arbitrary legacy state; assume orphans and missing meta; rollback path matters.
16. Interfaces/abstract classes — adding a required method is a BC break; **Internal namespace is NOT exempt**; out-of-tree implementors are invisible to grep, so "only one in-tree implementor" is never evidence of safety.

**Per-hunk mandatory audit.** For each significant hunk (added/modified function, method, class property, hook registration, schedule call, filter consumption, data-store interaction; skip formatting/comment-only), the agent produces the explicit per-invariant verdict block (`APPLIES` with note / `DOES_NOT_APPLY` / `UNCERTAIN` with note) as **internal working notes** — not final output. `APPLIES`/`UNCERTAIN` rows must be chased with Grep/Read verification. Findings then flow through the normal `ReviewOutputBuilder` path, subject to the shared protocol's STOP CHECK (changed-code-only, source-line numbers).

**Self-audit pass** (folds the regression pipeline's `auditor.md` in, since pirategoat has no per-agent second stage). Before saving output, the agent re-reads every invariant it audited as `APPLIES`/`UNCERTAIN` but did not flag, and re-classifies each dismissal:

- Structural proof of impossibility (verifiable from the dismissal text alone) → stays dismissed.
- Soft dismissal ("pre-existing", "third-party misuse", "guarded elsewhere", "couldn't verify", "unlikely", "documented contract") → promoted to a Medium finding with the dismissal reason quoted.
- Right invariant, wrong locus → promoted at Medium with a needs-localization note.

**Severity calibration** (ported from the regression prompts, near-verbatim — these took several rollouts to tune):

- Critical: payment/data/security impact on a common path, destructive data loss, auth bypass, fatal breakage of core checkout/order flows.
- High: silent false-success — a user/integrator-facing operation succeeds while the intended downstream effect doesn't happen (inert accepted inputs, scheduled actions firing for ineligible entities, serializers destroying arg types, success responses with skipped side effects).
- Medium: visible breakage, constrained blast radius, unreleased surface, maintainer-intended contract change.
- Low: cosmetic, observability, docs, migration hygiene.
- Floors: public-contract changes (required interface/abstract method added, public/extensible signature changed, hook removed/renamed, serialized/queued format changed) rate **at least Medium** and carry a `severity_floor` marker; silent false-success rates High by default; downgrades require a quoted structural reason, never a blast-radius descriptor.

**Finding categories:** `scheduled-action`, `hook-contract`, `meta-equality`, `template-override`, `progressive-enhancement`, `php-coercion`, `migration-state`, `interface-break`, `shape-validation`, `session-identity`, `other`.

**`severity_floor` marker mechanics:** findings under a floor include a literal line `Severity-floor: <reason>` at the end of the description (e.g. `Severity-floor: public-contract change; out-of-tree implementors are invisible to grep`). The reconciliator keys off this marker.

**Boundary notes (in-prompt, to limit overlap):** hook *design*/naming/over-hooking → wp-architecture-reviewer; wiring verification against *visible* upstream source → ecosystem-integration-reviewer; REST response shape drift → api-contract-reviewer; generic race conditions → concurrency-reviewer. This agent owns regression invariants and invisible-consumer blast radius — deliberately the opposite posture from ecosystem-integration's "cite or omit" (that agent omits what it cannot verify against visible source; this agent flags public-contract changes precisely because the affected code is *not* visible).

**Not-applicable backstop:** quick relevance scan per shared protocol; if the diff has no WooCommerce surface (repo isn't WC core or a WC extension, or the changed hunks touch none of the invariant surfaces), `mark_not_applicable` and exit.

## Component 2: registry entry — `scripts/review/agent_registry.json`

```json
"woo-regression-reviewer": {
  "domain": "wp-architecture",
  "protocols": ["reviewer"],
  "scope_flags": [],
  "no_semantic_filter": true,
  "dispatch_class": "conditional",
  "require_php_source_file": true,
  "require_triage_keyword_match": true,
  "triage_criteria": [
    "Diff belongs to WooCommerce core or a WooCommerce extension (WooPayments, AutomateWoo, etc.)",
    "PHP changes touching hooks, scheduled actions, meta/options, templates, interfaces, or validators in a WooCommerce codebase"
  ],
  "triage_keywords": [
    "woocommerce", "woocommerce_", "wc_", "wc-", "wc()",
    "action_scheduler", "as_schedule", "wp_schedule"
  ],
  "focus": "WooCommerce regression invariants derived from shipped-regression corpus — Action Scheduler traps, meta equality/sync loops, template overrides, broken-until-JS defaults, filter type variance, PHP 8.4 coercion, interface/hook contract breaks with out-of-tree blast radius. Only dispatches for WooCommerce core/extension changes.",
  "model_tier": "opus",
  "budget_override": 120
}
```

Rationale:

- `domain: wp-architecture` (`\.(php|js|ts|jsx|tsx)$`) — invariants are PHP-centric but the broken-until-JS and template surfaces include JS.
- `require_triage_keyword_match: true` — the triage-out the user asked for. Already supported by `plan_dispatch.py` (Layer 3: no keyword match → `SKIPPED_TRIAGE`), currently unused by any agent — this is its first consumer. WC core and every WC extension necessarily match (hook names, `wc_*` functions, `class-wc-*.php` / `woocommerce-*` paths); non-WC repos won't.
- `require_php_source_file: true` — the invariants need a PHP surface; JS-only diffs are out (accepted tradeoff, noted in the agent prompt).
- `no_semantic_filter: true` — same as wp-architecture-reviewer; the per-hunk audit needs unfiltered hunks.
- `model_tier: opus`, `budget_override: 120` — the audit is reasoning-heavy and greps callers/consumers across the tree (matches ecosystem-integration-reviewer's budget).

## Component 3: reconciliator hardening — `agents/review-reconciliator.md`

New short section (in Phase 2/3 area), scoped to findings carrying a `Severity-floor:` marker or in categories `interface-break` / `hook-contract` / `scheduled-action`:

1. **Blast-radius descriptors are not grounds to drop or downgrade:** "Internal namespace", "only one in-tree implementor", "unreleased / feature-flagged / experimental", "unlikely to fire in practice". These describe how many users are affected today, not whether the code prevents the bug.
2. **Verified-mitigation rule:** dropping or downgrading a verified-risk finding on the basis of a mitigation ("guarded elsewhere", "the later check handles it", "framework re-fetches first") requires the mitigation itself to be verified at file:line in the source snippets (or via Read). Unverified mitigation → keep the finding.
3. **Out-of-tree invisibility:** for public-contract changes, absence of in-repo implementors/consumers is NOT evidence of safety; do not drop below the finding's floor on that basis.

Deduplication and scope-checking behavior is unchanged; floors constrain only severity/drop decisions on *verified, in-scope* findings.

## Component 4: housekeeping

- `CHANGELOG.md` entry.
- Any docs listing agents (check `docs/`, `README.md`, `AGENTS.md`) — add the new agent.
- Registry schema / tests: run existing test suite (`tests/`) and satisfy any registry validation.
- Sync to pirategoat-bot: **out of scope** — stays with Vlad (per his workflow rule that plugins→bot sync is a separate step).

## Error handling

- Non-WC repo with accidental keyword match (e.g. "wc-" in a CSS class) → wasted dispatch, backstopped by the agent's not-applicable quick scan. Accepted cost.
- `NO_DOMAIN_FILES` / `ERROR` from scope.py → standard shared-protocol exits.
- Missing bootstrap → standard fallback path in the shared protocol.

## Testing

- `plan_dispatch.py` dispatch behavior: add/extend unit tests for `require_triage_keyword_match` gating with the new agent — dispatches on a WC-signal diff, `SKIPPED_TRIAGE` on a non-WC diff.
- Registry validity: existing registry-loading tests must pass with the new entry.
- Prompt-level validation (replay against the regression corpus) is explicitly out of scope for this change — it requires the corpus and the Workflow fan-out from `ai-regression-review/AGENTS.md`; noted as a follow-up.
