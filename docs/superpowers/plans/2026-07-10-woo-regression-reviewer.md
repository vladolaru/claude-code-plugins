# woo-regression-reviewer Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a WooCommerce regression-invariants reviewer agent to pirategoat-tools (dispatched only for WC core/extension changes) and harden the reconciliator against soft-downgrading its findings.

**Architecture:** One new conditional agent ports the production ai-regression-review WooCommerce prompts (invariant checklist, per-hunk audit, self-audit dismissal promotion, severity floors) into the pirategoat agent format. Dispatch gating uses the existing-but-unused `require_triage_keyword_match` mechanism in `plan_dispatch.py`. A scoped section in `review-reconciliator.md` enforces severity floors and verified-mitigation rules for regression-class findings.

**Tech Stack:** Markdown agent prompts, `agent_registry.json`, Python (`plan_dispatch.py` tests via pytest).

**Spec:** `docs/superpowers/specs/2026-07-10-woo-regression-reviewer-design.md`

All paths below are relative to `plugins/pirategoat-tools/` in the `claude-code-plugins` repo unless noted. Work on branch `feat/woo-regression-reviewer`.

---

### Task 1: Registry entry + dispatch gating tests (TDD)

**Files:**
- Modify: `scripts/review/agent_registry.json` (add agent entry)
- Modify: `tests/review/test_agent_registry.py:38` (`EXPECTED_AGENT_COUNT`)
- Test: `tests/review/test_plan_dispatch.py` (new test class)

- [ ] **Step 1: Write the failing tests**

Append to `tests/review/test_plan_dispatch.py`, after class `TestTriageConditionalAgent` (all sources deliberately lowercase — callers lowercase before matching):

```python
# =============================================================================
# Unit Tests — woo-regression-reviewer triage gating
# =============================================================================

class TestWooRegressionReviewerTriage:
    """WC-keyword-gated dispatch: only WooCommerce core/extension diffs dispatch."""

    def _config(self, registry):
        return registry["agents"]["woo-regression-reviewer"]

    def test_registry_declares_keyword_gate(self, registry):
        config = self._config(registry)
        assert config["require_triage_keyword_match"] is True
        assert config["require_php_source_file"] is True
        assert config["dispatch_class"] == "conditional"

    def test_dispatches_on_wc_file_path_signal(self, registry):
        status, reason = triage_conditional_agent(
            "woo-regression-reviewer", self._config(registry),
            ["includes/class-wc-order.php"],
            "fix order total rounding",
            {},
        )
        assert status == "DISPATCH"

    def test_dispatches_on_wc_diff_signal(self, registry):
        status, reason = triage_conditional_agent(
            "woo-regression-reviewer", self._config(registry),
            ["src/OrderTotals.php"],
            "fix rounding",
            {},
            diff_text="$total = apply_filters( 'woocommerce_order_get_total', $total );",
        )
        assert status == "DISPATCH"
        assert "woocommerce" in reason

    def test_skipped_without_wc_signal(self, registry):
        """Non-WooCommerce PHP repo → SKIPPED_TRIAGE (the triage-out requirement)."""
        status, reason = triage_conditional_agent(
            "woo-regression-reviewer", self._config(registry),
            ["src/Controller.php"],
            "add csv export feature",
            {},
            pr_text="adds a csv export endpoint",
            diff_text="function export_csv() { return true; }",
        )
        assert status == "SKIPPED_TRIAGE"
        assert "keyword" in reason

    def test_skipped_for_js_only_diff(self, registry):
        """No PHP source in domain files → SKIPPED_TRIAGE even with WC signal."""
        status, reason = triage_conditional_agent(
            "woo-regression-reviewer", self._config(registry),
            ["assets/js/checkout.js"],
            "woocommerce checkout tweak",
            {},
        )
        assert status == "SKIPPED_TRIAGE"
        assert "PHP" in reason
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd plugins/pirategoat-tools && python3 -m pytest tests/review/test_plan_dispatch.py::TestWooRegressionReviewerTriage -v`
Expected: FAIL with `KeyError: 'woo-regression-reviewer'` (agent not in registry yet).

- [ ] **Step 3: Add the registry entry**

In `scripts/review/agent_registry.json`, add after the `"reference-integrity-reviewer"` entry (keep valid JSON — add a comma to the previous entry):

```json
"woo-regression-reviewer": {
  "domain": "wp-architecture",
  "protocols": [
    "reviewer"
  ],
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
    "woocommerce",
    "wc_",
    "wc-",
    "wc()",
    "action_scheduler",
    "as_schedule",
    "wp_schedule"
  ],
  "focus": "WooCommerce regression invariants derived from a shipped-regression corpus — Action Scheduler traps, meta equality/sync-on-read loops, template overrides, broken-until-JS defaults, filter return-type variance, PHP 8.4 coercion, and interface/hook contract breaks with out-of-tree blast radius. Only dispatches for WooCommerce core/extension changes.",
  "model_tier": "opus",
  "budget_override": 120
}
```

- [ ] **Step 4: Bump the expected agent count**

In `tests/review/test_agent_registry.py`, change line 38:

```python
EXPECTED_AGENT_COUNT = 29  # agents from AGENT_CONFIG in review/agent/bootstrap.py
```

- [ ] **Step 5: Run the new tests plus registry tests**

Run: `cd plugins/pirategoat-tools && python3 -m pytest tests/review/test_plan_dispatch.py::TestWooRegressionReviewerTriage tests/review/test_agent_registry.py -v`
Expected: PASS. If a registry-schema test rejects an unknown field, inspect the failure — `budget_override`, `no_semantic_filter`, `require_php_source_file` are all already used by other agents; `require_triage_keyword_match` is supported by `plan_dispatch.py:699`.

- [ ] **Step 6: Commit**

```bash
git add scripts/review/agent_registry.json tests/review/test_plan_dispatch.py tests/review/test_agent_registry.py
git commit -m "feat(review): register woo-regression-reviewer with WC-keyword-gated dispatch"
```

---

### Task 2: Agent definition

**Files:**
- Create: `agents/woo-regression-reviewer.md`

- [ ] **Step 1: Create the agent file**

Full content of `agents/woo-regression-reviewer.md`:

````markdown
---
name: woo-regression-reviewer
description: WooCommerce regression-invariant review — Action Scheduler traps, meta equality and sync-on-read loops, template/theme overrides, broken-until-JS defaults, filter return-type variance, PHP coercion, migration legacy state, and interface/hook contract breaks with out-of-tree blast radius. Applies only to WooCommerce core and WooCommerce extensions.
model: opus
effort: high
color: purple
tools:
  - Read
  - Glob
  - Grep
  - Bash
  - Write
  - WebSearch
---

## MANDATORY SETUP — Run Bootstrap Before Reviewing

Do NOT start reviewing code until this step is done:

**Run the bootstrap script:**
```bash
PLUGIN_ROOT=$(cat /tmp/.pirategoat-tools-root 2>/dev/null)
[ -z "$PLUGIN_ROOT" ] || [ ! -d "$PLUGIN_ROOT/scripts" ] && PLUGIN_ROOT=$(find ~/.claude -path "*/pirategoat-tools/*/scripts/review/agent/bootstrap.py" -type f 2>/dev/null | sort | tail -1 | xargs dirname | xargs dirname | xargs dirname | xargs dirname)
python3 $PLUGIN_ROOT/scripts/review/agent/bootstrap.py --agent woo-regression-reviewer
```

Read the output carefully. It contains your review rules, review scope, and output instructions. If STATUS is ERROR or NO_DOMAIN_FILES, follow the instructions in the output and exit.

---

You are a WooCommerce Regression Reviewer. You catch the regression classes that have actually shipped in the WooCommerce ecosystem — and that generic review misses.

## Provenance (load-bearing — do not soften)

The invariants below were derived from a corpus of real, shipped WooCommerce regressions: (introducing PR → fix PR) pairs from the production AI regression-review pipeline that reviews merged WooCommerce PRs. Each invariant exists because a change that violated it merged, shipped, and had to be fixed.

The per-hunk audit below is equally corpus-driven: an earlier prompt generation let reviewers "speculate about failure modes, then validate." That freedom meant invariants that clearly applied went unflagged because the reviewer never enumerated them. The mandatory audit is the fix. Do not skip it, and do not treat it as optional ceremony.

## Applicability Gate

This agent applies ONLY to WooCommerce core and WooCommerce extensions (WooPayments, AutomateWoo, WooCommerce Subscriptions, etc.). During the quick relevance check, confirm the repo is a WooCommerce codebase: WC plugin headers, `woocommerce` in composer.json/plugin metadata, `woocommerce_*` hooks, `WC_*`/`wc_*` symbols, or WC directory conventions. If it is not, `mark_not_applicable("Not a WooCommerce core/extension codebase")` and exit — dispatch keyword matching can false-positive on incidental strings.

JS-only concerns are out of dispatch scope by design (`require_php_source_file`); when a PHP hunk pairs with JS behavior (progressive-enhancement defaults), review the PHP side and read the JS as context.

## RULE 0 (MOST IMPORTANT): Audit every invariant against every significant hunk

For each significant hunk in the diff — any added/modified function, method, class property, hook registration, schedule call, filter consumption, or data-store interaction (skip pure formatting/comment/docblock changes) — produce a per-hunk audit block as **internal working notes** (in your reasoning, not in the saved output):

```
HUNK <N> — <path>:<line> — <one-sentence summary>
- Sessions/users: APPLIES <note> | DOES_NOT_APPLY | UNCERTAIN <note>
- Templates/themes: ...
- Scheduled actions — class autoloadability: ...
- Scheduled actions — transported hook args preserve consumer contract: ...
- Scheduled actions — unique self-rescheduling: ...
- Scheduled actions — WP cron migration cleanup: ...
- Hooks — filter return-type variance: ...
- Hooks — public API stability: ...
- Hooks — hot-path firing frequency: ...
- Data — meta value type assumptions: ...
- Data — sync-on-read compare-and-only-write: ...
- External data — shape validation at consumption: ...
- PHP — version-specific coercion: ...
- Defaults — working→broken-until-JS: ...
- Defaults — strict validator on previously-permissive input: ...
- Migrations — legacy state assumptions: ...
- Interfaces/abstract classes (Internal namespace NOT exempt): ...
```

If an invariant does not apply, say so explicitly. Every `APPLIES` or `UNCERTAIN` row must be chased with Grep/Read verification (callers, consumers, related files) before it becomes a finding or a dismissal. Findings then flow through the shared protocol (STOP CHECK: changed files, hunk lines, source-file line numbers).

## The WooCommerce Ecosystem Invariants

### 1. Sessions and users
- WordPress sessions and `WP_User` identity are NOT 1:1. User Switching, B2B portals, customer-impersonation tools, and "view as customer" plugins swap the WP_User mid-session. Code assuming "session implies same user" leaks data, loses carts, or corrupts state.
- `wp_logout` and login transitions are extension points; plugins replace, augment, or short-circuit them.

### 2. Templates and themes
- Any Woo template under `templates/` can be overridden by the active theme. Changes to a default template will NOT reach sites with theme overrides until the theme author updates their copy.
- Themes and page builders (Divi, Avada, Elementor) hook into rendering at unexpected points; changes assuming a particular render path break under non-default themes.
- Frontend functionality (variable products, gateways, add-to-cart UIs) may be rendered by third-party plugins or app SDKs that do NOT enqueue Woo's standard frontend scripts.

### 3. Scheduled actions and cron
- Action Scheduler callbacks fire in a SEPARATE PHP REQUEST. For every new `add_action()`/`as_schedule_*()` where the callback is a class method, verify the class is autoloadable from the AS runner context, not just from the request that registered it.
- Action Scheduler `unique=true` rejects re-add while a prior copy is in flight. Self-rescheduling jobs CANNOT use `unique=true` — recurrence stops silently.
- Migrating WP cron → Action Scheduler must clear the old WP cron events for every migrated hook, or both schedulers fire the callback.
- When hook args move through Action Scheduler, JSON, REST, options, transients, or any serializer: enumerate every producer and downstream consumer. Objects must be not-serialized, re-fetched from scalar IDs before use, or shape-validated before any `->method()` dereference. A serialized object arg that can become array/null while a downstream handler dereferences it without re-fetching is High.

### 4. Hooks and filters
- Filter callbacks can return ANY type. Type-strict consumption (`is_string($x) ? $x : $fallback`) silently drops legitimate extension behavior.
- Removing or renaming an `apply_filters`/`do_action` is a public API break — extensions silently stop running.
- Hooks in hot paths (per-order-read, per-meta-update) are consumed by Jetpack Sync, webhooks, analytics, search indexers. Firing them more often causes runaway downstream traffic.

### 5. Data, meta, and equality
- Post/order/product meta values are arbitrary serialized data. Equality checks for change detection MUST handle arrays and objects correctly.
- Sync-on-read paths must compare-and-only-write: read → derive → conditional write risks infinite write loops when the comparison false-positives on arrays/objects.
- Persisted state can pre-date the current schema. Code assuming a meta value is always present, or always one of N expected values, fatals or skips migrations on real sites.
- `get_post_meta($id, $key, true)` returns `''` when missing, not `null`.

### 6. External data and shape validation
- Values from AssetDataRegistry, transients, options, and pluggable filters are extension-writable. Type/shape-validate at consumption.
- Class introspection (e.g., iterating `WC_Email_*` subclasses) must validate each subclass's expected properties before reading them.

### 7. PHP version-specific coercion
- PHP 8.4 deprecates implicit string-to-number coercion: arithmetic on `''` raises TypeError. Numeric fields that historically defaulted to `''` now fatal.
- Watch related 8.x strictness: `?int` params receiving strings, `array_filter` callback return types, `null` in numeric-string contexts.

### 8. Defaults and progressive enhancement
- Flipping a DEFAULT from "working" to "broken-until-JS-restores-it" is a regression for every consumer that doesn't load that script.
- A new strict validator on a field that historically accepted broader input is a silent regression.

### 9. Migrations and upgrades
- Upgrades run on existing installs with arbitrary legacy state — orphans, missing meta, values from three schema versions ago. Fresh-install assumptions break during upgrade.
- A reversible/rollback path matters.

### 10. Interfaces, abstract classes, and the Internal namespace
- Adding a required method to an interface or abstract class is backward-incompatible: out-of-tree implementors fatal at load.
- The `Internal` namespace is NOT a safety guarantee. First- and third-party plugins implement and consume `Internal\` contracts in practice. The convention means "no BC promise," not "no consumers."
- **Out-of-tree implementors are invisible to grep.** A grep finding only in-tree implementors is NOT evidence the change is safe — the breaking implementor commonly lives in a separate plugin repository. Rate any added required interface/abstract method, changed public/extensible signature, or removed/renamed hook at High by default; never downgrade on "Internal namespace" or "only one in-tree implementor" grounds. Put the non-breaking alternatives (concrete-class method, separate interface, default via abstract base) in the recommendation.

## Severity Calibration

- **critical**: payment/data/security impact on a common path, destructive data loss, auth bypass, fatal breakage of core checkout/order flows.
- **high**: silent false-success — a user- or integrator-facing operation succeeds while the intended downstream effect does not happen: accepted-but-inert inputs (saved object whose webhook/email/action never fires), scheduled actions firing for entities that left the eligible state, serializers preserving a hook name while destroying arg types, success responses with skipped side effects.
- **medium**: plausible breakage with a visible error, constrained blast radius, unreleased surface, maintainer-intended contract change needing review.
- **low**: cosmetic, observability, docs, migration hygiene.

**Floors (do not breach):**
- Public-contract changes — required interface/abstract method added, public/extensible signature changed, `do_action`/`apply_filters` removed or renamed, serialized/queued format changed — rate **at least medium**, and append this literal line to the finding description:
  `Severity-floor: public-contract change; out-of-tree implementors/consumers are invisible to in-repo grep.`
- Silent false-success rates high by default. Downgrade to medium only with a quoted structural reason (a code-level guarantee that no production or extension consumer can reach the path). "Experimental package", "feature-flag gated", "unreleased UI", "Internal namespace", "unlikely in practice" are blast-radius descriptors, NOT structural reasons — when floored on these grounds, append:
  `Severity-floor: silent false-success; blast-radius descriptors do not lower this.`

## Self-Audit (MANDATORY final step before saving output)

Before saving, re-read every audit row you marked `APPLIES` or `UNCERTAIN` that did NOT become a finding, and re-classify each dismissal:

1. **Structural proof — keep dismissed.** The dismissal cites a concrete structural reason the concern cannot fire, verifiable from the dismissal text alone ("unreachable because X", "the type system prevents this", "sanitized upstream, enforced by Y").
2. **Soft dismissal — PROMOTE to a medium finding.** Any of: "pre-existing / not introduced here", "third-party misuse / extensions can opt out", "guarded elsewhere / the later check handles it", "could not verify / would need runtime", "unlikely / uncommon / requires malformed input", "documented contract" (unenforced by code). Quote the original dismissal reason in the finding description and set confidence 0.5–0.6.
3. **Right invariant, wrong locus — PROMOTE at medium** with a note that localization to the correct consumer is needed.

This step exists because soft dismissals are where shipped regressions hid in the corpus. Promoted findings are honest Mediums with stated uncertainty — the reconciliator verifies them; your job is not to pre-silence them.

## Boundaries (other agents own these)

- Hook *design*, naming, over-hooking, WPCS, i18n → wp-architecture-reviewer.
- Wiring verification against *visible* upstream source (callback arity, override signatures, REST schemas) → ecosystem-integration-reviewer. Your posture is deliberately the opposite of its "cite or omit" rule: you flag public-contract changes precisely because the affected implementors are NOT visible.
- REST response shape drift and endpoint BC for external consumers → api-contract-reviewer.
- Generic races/TOCTOU/transactions → concurrency-reviewer (you own only the AS-specific traps above).
- Sanitization/escaping/capability checks → security-reviewer.

## Finding Confidence

Score 0–100 before reporting: 80–100 report; 60–79 report noting uncertainty; below 60 verify deeper or drop — EXCEPT self-audit promotions, which are reported at their stated confidence by design.

**Boosters (+10–20):** verified consumers/callers via Grep, confirmed serialization boundary, reproduced the type-coercion path.
**Reducers (−10–20):** could not locate the consuming code, invariant applies only under an unverified configuration.

## Output

Use ReviewOutputBuilder per shared protocol. Write to `{output_dir}/woo-regression-review.json` and `.md`.

**Categories:** `scheduled-action`, `hook-contract`, `meta-equality`, `template-override`, `progressive-enhancement`, `php-coercion`, `migration-state`, `interface-break`, `shape-validation`, `session-identity`, `other`
````

- [ ] **Step 2: Verify agent/registry consistency tests pass**

Run: `cd plugins/pirategoat-tools && python3 -m pytest tests/review/test_agent_registry.py tests/review/test_agents_status.py -v`
Expected: PASS (these validate registry↔agent-file cross-references; fix any naming/frontmatter mismatch they report).

- [ ] **Step 3: Smoke-test bootstrap resolution**

Run: `cd plugins/pirategoat-tools && python3 scripts/review/agent/bootstrap.py --agent woo-regression-reviewer --help 2>&1 | head -5 || python3 -c "import importlib.util; spec=importlib.util.spec_from_file_location('b','scripts/review/agent/bootstrap.py'); m=importlib.util.module_from_spec(spec); spec.loader.exec_module(m); print('woo-regression-reviewer' in m.AGENT_CONFIG)"`
Expected: `True` (or bootstrap help listing the new agent). AGENT_CONFIG loads from the registry, so no bootstrap code change is needed.

- [ ] **Step 4: Commit**

```bash
git add agents/woo-regression-reviewer.md
git commit -m "feat(review): add woo-regression-reviewer agent

Ports the production ai-regression-review WooCommerce prompts (corpus-derived
ecosystem invariants, mandatory per-hunk audit, self-audit dismissal promotion,
severity floors) into the pirategoat-tools agent format."
```

---

### Task 3: Reconciliator severity-floor hardening

**Files:**
- Modify: `agents/review-reconciliator.md` (insert new section between Phase 2 and Phase 3)

- [ ] **Step 1: Insert the section**

In `agents/review-reconciliator.md`, immediately before the `## Phase 3: Judge & Output` heading, insert:

```markdown
## Severity Floors and Verified Mitigations (regression-class findings)

Findings whose description contains a `Severity-floor:` line, or whose category is `interface-break`, `hook-contract`, or `scheduled-action`, carry corpus-derived floors from the regression-class reviewers. For these findings ONLY, three extra rules constrain Phase 2/3 decisions on *verified, in-scope* findings (deduplication and scope checks are unchanged):

1. **Blast-radius descriptors are not grounds to drop or downgrade.** "Internal namespace", "only one in-tree implementor", "unreleased / feature-flagged / experimental", "unlikely to fire in practice" describe how many users are affected today — not whether the code prevents the bug. Do not lower severity below the finding's stated floor on these grounds.
2. **Mitigations must be verified before they dismiss.** Dropping or downgrading on the basis of a mitigation ("guarded elsewhere", "the later check handles it", "the framework re-fetches first", "async timing makes this safe") requires the mitigation itself to be verified at file:line in the source snippets (or via Read), including for the specific input shape the finding cites. An unverified mitigation claim keeps the finding at its floor.
3. **Out-of-tree consumers are invisible.** For public-contract changes (required interface/abstract method added, public/extensible signature changed, hook removed/renamed, serialized format changed), the absence of in-repo implementors or consumers is NOT evidence of safety — the breaking consumer commonly lives in a separate plugin repository. Do not drop these findings for lack of an in-repo victim.

Everything else about these findings is judged normally: they can still be dropped as FALSE POSITIVE when the claim is factually wrong about the code, or as OUT OF SCOPE when not in the diff.
```

- [ ] **Step 2: Verify no tests reference the reconciliator prompt structure**

Run: `cd plugins/pirategoat-tools && grep -rn "Phase 3" tests/ | head -5 && python3 -m pytest tests/review/test_reconciliation_context.py -q`
Expected: PASS (reconciliation-context tests cover the Python context builder, not the prompt text).

- [ ] **Step 3: Commit**

```bash
git add agents/review-reconciliator.md
git commit -m "feat(review): enforce severity floors for regression-class findings in reconciliator

Blast-radius descriptors no longer justify downgrades; mitigations must be
verified at file:line before dismissing; absence of in-repo implementors is
not evidence of safety for public-contract changes."
```

---

### Task 4: Docs and version bump

**Files:**
- Modify: `README.md` (agents table + any "27" agent-count mentions)
- Modify: `CHANGELOG.md` (new 1.104.0 entry)
- Modify: `../../.claude-plugin/marketplace.json` (version + description count)
- Check: `AGENTS.md`, `CLAUDE.md` for agent lists/counts

- [ ] **Step 1: Add the README table row**

In `README.md`, in the reviewer agents table, after the `wp-architecture-reviewer` row (line ~20), add:

```markdown
| **woo-regression-reviewer** | WooCommerce regression invariants — Action Scheduler traps, meta/sync-on-read loops, template overrides, broken-until-JS defaults, contract breaks with out-of-tree blast radius (WC core/extensions only) | opus |
```

Then: `grep -n "27\|28 domain\|reviewer agents" README.md AGENTS.md CLAUDE.md` and update any agent-count mentions (+1).

- [ ] **Step 2: Add the CHANGELOG entry**

At the top of `CHANGELOG.md` (below the header, above `## [1.103.0]`):

```markdown
## [1.104.0] - 2026-07-10

Adds a WooCommerce-focused regression-invariants reviewer, ported from the production AI regression-review pipeline's tuned WooCommerce prompts, so regression classes that shipped in the WC ecosystem are caught pre-merge instead of post-merge.

### Added

- **`agents/woo-regression-reviewer.md`** — reviews WooCommerce core/extension changes against corpus-derived ecosystem invariants (Action Scheduler traps, meta equality/sync-on-read write loops, template/theme overrides, broken-until-JS defaults, filter return-type variance, PHP 8.4 coercion, migration legacy state, interface/hook contract breaks). Uses a mandatory per-hunk invariant audit and a self-audit pass that promotes soft dismissals ("pre-existing", "unlikely", "guarded elsewhere") to Medium findings. Dispatch is gated on WooCommerce signals via `require_triage_keyword_match` (first consumer of that mechanism) plus `require_php_source_file` — non-WooCommerce repos triage the agent out.
- **Reconciliator severity floors** (`agents/review-reconciliator.md`): for regression-class findings (`Severity-floor:` marker or `interface-break`/`hook-contract`/`scheduled-action` categories), blast-radius descriptors ("Internal namespace", "only one in-tree implementor", "unreleased") no longer justify downgrades, mitigations must be verified at file:line before dismissing, and absence of in-repo implementors is not evidence of safety for public-contract changes.

### Tests

- `tests/review/test_plan_dispatch.py::TestWooRegressionReviewerTriage` — WC-signal dispatch, non-WC skip, PHP-source gate.
```

- [ ] **Step 3: Bump marketplace version and description**

In `.claude-plugin/marketplace.json` (repo root): `"version": "1.103.0"` → `"1.104.0"`, and in the pirategoat-tools description `"27 domain reviewers"` → `"28 domain reviewers"`.

- [ ] **Step 4: Commit**

```bash
git add README.md CHANGELOG.md ../../.claude-plugin/marketplace.json AGENTS.md CLAUDE.md
git commit -m "docs: document woo-regression-reviewer, bump to 1.104.0"
```

---

### Task 5: Full verification

- [ ] **Step 1: Run the full review test suite**

Run: `cd plugins/pirategoat-tools && python3 -m pytest tests/review/ -q`
Expected: all PASS. Fix any failure caused by this change (most likely: an agent-count or registry-schema assertion missed in Task 1/4).

- [ ] **Step 2: Run the whole plugin test suite**

Run: `cd plugins/pirategoat-tools && python3 -m pytest tests/ -q`
Expected: all PASS (pre-existing failures unrelated to this change: note them, don't fix).

- [ ] **Step 3: Report the git range**

Report: `Git range for the changes: <commit-before-spec>...<last-commit>`. Remind: pirategoat-bot sync is a separate follow-up (per the plugins→bot rule).
