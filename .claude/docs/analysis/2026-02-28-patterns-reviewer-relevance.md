# Patterns Reviewer: Improving Pattern Relevance & Reducing False Positives

**Date:** 2026-02-28
**Subject:** `plugins/pirategoat-tools/agents/patterns-reviewer.md`
**Goal:** Make the reviewer's analysis focus on patterns from similar areas of the codebase, avoid false positives from legacy/unrelated code, and discount patterns from non-sibling areas.
**Status:** Tier A implemented in v1.36.0 (`f2f0bf5`). Tier B deferred pending evidence.

---

## The Core Problem

The current patterns reviewer treats **all discovered patterns as equally authoritative**. When it finds a pattern via `git grep`, the source doesn't matter — a pattern from a legacy module, a gap-filler hack in a custom integration, and a deliberate design system convention all carry the same weight. This produces false positives in two directions:

1. **Enforcing bad/legacy patterns** — "We found this approach in `legacy-module/`, so align with it"
2. **Enforcing distant patterns** — "This approach is used in an unrelated subsystem, so match it" when sibling code does something different for good reason

---

## Gap Analysis

### Gap 1: No Concept of Pattern Proximity

The reviewer's search strategy is **flat** — it searches the entire base ref equally:

```bash
git grep -n "<pattern>" <base_ref> -- "*.php" | head -20
```

This finds matches everywhere. If you're modifying a design system component and there's a matching pattern in both:
- `design-system/components/Button.tsx` (sibling, authoritative)
- `integrations/custom-checkout/HackyButton.tsx` (distant, gap-filler)

Both appear equally in results. The reviewer has no framework for saying "the design system pattern is the one that matters here."

**What's needed:** A concentric-circle search strategy that discovers patterns from nearest to farthest, and weights them accordingly.

### Gap 2: No Upstream/Downstream Awareness

Codebases have hierarchy. In CIAB:
- **Design system components** = upstream (authoritative, intentional, reviewed)
- **Custom integrations** = downstream (consumers, may contain workarounds)

The current reviewer can't distinguish these. Worse, it might enforce a downstream workaround onto upstream code. The CIAB example is instructive but the principle is general:

| Working on... | Pattern authority order |
|---|---|
| Upstream (library, design system, shared utils) | 1. Same upstream module > 2. Other upstream modules > 3. *(skip downstream entirely)* |
| Downstream (integration, consumer, custom solution) | 1. Upstream patterns > 2. Same-type downstream siblings > 3. Other downstream *(with skepticism)* |

**Why skip downstream when working upstream?** Downstream code adapts to upstream constraints. If downstream has a pattern, it's either (a) following upstream's lead (redundant to find) or (b) working around a limitation (don't propagate workarounds back upstream).

### Gap 3: No Establishment Threshold

The reviewer treats a single instance as a "pattern." But:

| Count | What it is | Reviewer should... |
|---|---|---|
| 1 | An instance, not a pattern | Note it exists, don't enforce it |
| 2 | Possible emerging pattern or coincidence | Mention it, low confidence |
| 3+ | Established pattern | Enforce with confidence |
| 3+ with declining usage | Dying pattern | Flag as potentially superseded, verify |

Currently, the reviewer's Step 2 just searches and reports whatever it finds. There's no counting step, no threshold evaluation. One instance in the codebase becomes "there's an existing pattern you should follow."

**Exception:** Patterns in explicitly authoritative locations (documented conventions, design system foundations) may count even at 1-2 instances if they represent deliberate architectural decisions.

### Gap 4: No Freshness/Trajectory Analysis

The reviewer has "Pattern Evolution Questions" (lines 153-156) which ask the right questions:

> 1. Is this still the current approach? (Check for later refactors)
> 2. Why did it change? (Read commit messages)
> 3. Should the PR follow old or new pattern?

But these are advisory questions, not a systematic process. There's no concrete mechanism for:
- Measuring adoption trajectory (is usage growing or shrinking?)
- Detecting migration in progress (commits that replace pattern A with pattern B)
- Distinguishing "old but still valid" from "old and superseded"

Important nuance: **old doesn't always mean bad**. Some foundational patterns remain valid for years. What matters is whether newer code still adopts the pattern, or whether it's being left behind.

### Gap 5: No "Gap-Filler" Detection

In the CIAB example, custom integrations sometimes introduce specific solutions to fill gaps in the design system. These are:
- Intentionally local (not meant to be generalized)
- Often inferior to a proper upstream solution (they're workarounds)
- Potentially numerous (multiple integrations hit the same gap)

The reviewer might see 3+ integrations all using the same gap-filler pattern and conclude "this is an established pattern — follow it." But the correct conclusion is "this gap should be filled upstream, and these workarounds should eventually be replaced."

**Detection signals:**
- Pattern exists only in downstream/consumer code, never in upstream
- Pattern duplicates functionality that partially exists upstream
- Git history shows the pattern appearing independently in multiple places (convergent evolution around a gap)

---

## Concrete Proposals

### Proposal 1: Add a "Codebase Topology Discovery" Step

> **Status: DEFERRED** — Decision-critic analysis found that most target codebases (WordPress plugins) are too flat for upstream/downstream topology to be meaningful (C6 UNCERTAIN, A4 UNCERTAIN). Import-direction analysis is feasible but expensive. Revisit if Tier A changes prove insufficient for hierarchical codebases.

Before searching for patterns, the reviewer should understand the structure of where it's working. This becomes a new **Step 0** in the Pattern Discovery Process.

**Step 0: Map the Territory**

1. Identify which module/area the changed files belong to
2. Look for structural signals of hierarchy:
   - Directory naming (e.g., `components/`, `design-system/`, `shared/`, `lib/` = upstream; `integrations/`, `custom/`, `features/` = downstream)
   - Package boundaries (`package.json`, `composer.json` at different levels)
   - Import direction (who imports whom — upstream is imported, downstream imports)
3. Classify the changed files' zone as upstream, downstream, or peer
4. Identify any areas marked as deprecated or legacy (README notes, `@deprecated` tags, directory names like `legacy/`, `v1/`)

**Implication:** Adds upfront exploration cost, but it's a one-time cost per review that dramatically improves the relevance of all subsequent pattern searches.

### Proposal 2: Replace Flat Search with Concentric-Circle Search

> **Status: PARTIALLY IMPLEMENTED** — Instead of replacing the flat search with a tiered search strategy, proximity was integrated as a confidence modifier on the existing scoring system (+15 same module, +5 same layer, -15 distant). This achieves the core insight (proximate patterns matter more) without requiring the agent to execute multi-tier searches or maintain state across tiers. See decision-critic finding C3 (LLM unreliability with multi-tier search execution).

Replace the current "search everything" approach with a tiered strategy:

**Tier 1 — Same Directory / Module (highest authority)**
```bash
# Search sibling files in the same directory tree
git grep -n "<pattern>" <base_ref> -- "path/to/same/module/*.ext"
```

**Tier 2 — Same Architectural Layer**
```bash
# If working on upstream: search other upstream modules
# If working on downstream: search upstream first, then sibling downstream
git grep -n "<pattern>" <base_ref> -- "path/to/upstream/**/*.ext"
```

**Tier 3 — Broader Codebase (with skepticism)**
```bash
# Full search, but results from this tier carry lower weight
git grep -n "<pattern>" <base_ref> -- "*.ext"
```

For each tier, count instances and classify. Stop reporting a pattern as "established" if it only appears in Tier 3 with low counts.

### Proposal 3: Introduce Pattern Relevance Scoring

> **Status: DEFERRED (replaced by simpler approach)** — Decision-critic analysis found that LLMs are inconsistent at multi-factor numerical scoring (C3 UNCERTAIN). A 4-factor weighted score creates an illusion of precision that masks arbitrary threshold effects. Instead, proximity was integrated as a confidence modifier on the existing scoring system, avoiding a second orthogonal score. The existing confidence system (0-100 with 60 threshold) already provides the control mechanism.

Add a scoring framework alongside the existing confidence score. Confidence measures "am I sure this pattern exists?" — Relevance measures "should this pattern be enforced here?"

**Pattern Relevance Score (0-100):**

| Factor | Weight | How to assess |
|---|---|---|
| Proximity to changed code | 30pts | Same module=30, same layer=20, same project=10, distant=0 |
| Authority direction | 25pts | Upstream=25, peer=15, downstream=5, legacy=0 |
| Establishment (usage count) | 25pts | 5+=25, 3-4=20, 2=10, 1=5 |
| Freshness (recent adoption) | 20pts | Active adoption=20, stable=15, declining=5, abandoned=0 |

**Enforcement thresholds:**

| Score | Action |
|---|---|
| 70-100 | Strong recommendation to align |
| 40-69 | Mention as "consider aligning" — pattern exists but isn't strongly authoritative for this context |
| 0-39 | Do NOT report as a pattern to follow — too distant, too rare, or too stale |

### Proposal 4: Add the "3+ Usage Rule" as a Hard Gate

> **Status: IMPLEMENTED** in v1.36.0 as RULE 1. Added with authoritative-location exception and small-codebase adjustment (~15% threshold for codebases with <20 relevant files).

Make this explicit in the agent instructions:

> **RULE: A pattern requires 3+ independent usages to be considered established.**
>
> Before reporting a pattern as something the PR should follow:
> 1. Count independent usages in the base ref (same snippet copy-pasted doesn't count — look for independent implementations of the same approach)
> 2. If count < 3: Do NOT report as "established pattern." You may mention it as "one existing approach" at reduced confidence, but do not recommend alignment
> 3. If count >= 3: Verify it's not a dying pattern (check freshness)
>
> **Exception:** Patterns in explicitly authoritative code (design system foundations, documented conventions, architectural decision records) may be enforced at any count if they represent deliberate decisions. The authority must be verifiable — a comment, ADR, or README that establishes the pattern as intentional.

**Small codebase adjustment:** In a codebase with fewer than ~20 relevant files, 2 usages may suffice. The threshold should be proportional — pattern should appear in at least ~10-15% of places where it could apply, or 3 independent usages, whichever is lower.

### Proposal 5: Add Supersession Detection

> **Status: IMPLEMENTED (corrected)** in v1.36.0 as Step 5: Staleness Check. The original git commands were technically incorrect — `--diff-filter=D` filters for deleted *files*, not deleted pattern instances within files (C5 FAILED in decision-critic verification). The implementation uses the simpler and correct `git log --oneline -S "<pattern>" -- "*.php" | head -10` approach, examining the most recent commits to determine adoption trajectory.

Add a concrete process for detecting when a pattern has been replaced:

**Supersession Signals:**
1. Git history shows commits that explicitly replace pattern A with pattern B (look for commit messages with "refactor", "migrate", "replace", "modernize")
2. Newer files exclusively use pattern B while older files use pattern A
3. Pattern A usage count is declining over time (more removals than additions in recent commits)
4. There's a newer abstraction that makes pattern A unnecessary

**Process:**
```bash
# INCORRECT — --diff-filter applies to whole files, not pattern instances
# git log --oneline --all -S "<old_pattern>" --diff-filter=D | head -10  # removals
# git log --oneline --all -S "<old_pattern>" --diff-filter=A | head -10  # additions

# CORRECT — -S finds commits where the pattern count changed; examine the diff to determine direction
git log --oneline -S "<pattern>" -- "*.php" | head -10
```

When a superseded pattern is detected, the finding should recommend the **newer** pattern, not the older one, and the verdict should note the evolution.

### Proposal 6: Add Gap-Filler Detection for Hierarchical Codebases

> **Status: DEFERRED** — Too specific to hierarchical codebases (CIAB-like). Most target codebases lack the upstream/downstream distinction needed for this to be meaningful. Revisit if Tier B (topology discovery) is implemented.

When the reviewer discovers a pattern that exists only in downstream/consumer code:

> **Gap-Filler Check:** If a pattern is found exclusively in downstream/consumer code (not in upstream/shared code):
> 1. It may be a workaround for an upstream limitation — do NOT enforce it as an established pattern
> 2. If the PR is in upstream code: ignore this pattern entirely
> 3. If the PR is in downstream code: mention it as "common workaround used by N integrations" but note it's not an upstream-blessed approach
> 4. Consider flagging it as a consolidation opportunity (perhaps it should be elevated to upstream)

### Proposal 7: Update Verdicts with Contextual Qualifiers

> **Status: IMPLEMENTED** in v1.36.0. Verdicts now require usage counts, area context, and freshness indicators. Added "declining pattern" and "distant-only pattern" qualifiers.

Current verdicts: `REUSE`, `ALIGN`, `CONSOLIDATE`, `APPROVE`

The verdicts themselves are fine, but findings should include contextual qualifiers in their descriptions:

| Situation | Verdict | Qualifier |
|---|---|---|
| Established upstream pattern exists | `ALIGN` | "Established upstream pattern (N usages)" |
| Sibling pattern exists, 3+ usages | `ALIGN` | "Established sibling pattern (N usages in same layer)" |
| Pattern exists but only 1-2 usages | `APPROVE` (or don't report) | If mentioned: "Possible emerging pattern, not yet established" |
| Pattern exists but declining | `APPROVE` | "Existing pattern appears to be in decline — new approach may be appropriate" |
| Gap-filler pattern from downstream | `APPROVE` or `CONSOLIDATE` | "Workaround pattern (N downstream usages) — consider upstream solution" |

---

## Decision-Critic Analysis

A structured decision-critic review (7-step workflow: decompose, classify, verify, challenge, reframe, synthesize) was applied to this document before implementation. Key findings:

### Verification Results

| ID | Claim | Status | Finding |
|---|---|---|---|
| C1 | Current agent treats all patterns equally | VERIFIED | Confirmed by reading agent prompt — no proximity, hierarchy, or frequency filtering |
| C2 | Proximity awareness will dramatically reduce false positives | UNCERTAIN | "Dramatically" is unquantified; the 30-point proximity weight in Proposal 3 could suppress valid cross-cutting patterns |
| C3 | LLM can reliably execute tiered search and numerical scoring | UNCERTAIN | Tiered search feasible; numerical scoring is the weak link — LLMs are inconsistent at multi-factor quantitative scoring |
| C4 | 3+ usage threshold is sound | VERIFIED | Reasonable heuristic with necessary exception clause for authoritative locations |
| C5 | Supersession detection via git history signals | FAILED | `git log -S` with `--diff-filter=D` finds deleted *files*, not deleted pattern instances — commands are technically incorrect |
| C6 | Codebases have discoverable upstream/downstream topology | UNCERTAIN | Works for layered architectures but many WordPress plugins are too flat |
| C7 | Doubling instruction size won't degrade performance | UNCERTAIN | Would make patterns-reviewer ~40% longer than the current longest agent (254 lines) |

### Verdict: REVISE

Split into two tiers based on risk/impact analysis:

**Tier A (implemented):** Low risk, high impact, +51 lines
- Proposal 4: 3+ usage gate
- Proposal 5: Staleness check (with corrected git commands)
- Proximity as confidence modifiers (simplified from Proposals 2/3)
- Proposal 7: Contextual verdict qualifiers

**Tier B (deferred):** High cost, uncertain benefit
- Proposal 1: Topology discovery
- Proposal 3: Separate relevance scoring system
- Proposal 6: Gap-filler detection

### Trigger for Tier B

Run Tier A for 5-10 actual reviews. If >30% of remaining false positives trace to topology/hierarchy issues (distant patterns from wrong architectural layer), Tier B is justified.

---

## Implications and Trade-offs

### What We Gain

1. **Fewer false positives** — No more "align with this one-off pattern from a legacy module"
2. **Establishment discipline** — Patterns must earn the label through repeated independent adoption
3. **Freshness awareness** — Declining patterns flagged instead of enforced
4. **More useful findings** — Verdicts include usage counts and proximity context

### What We Risk

1. ~~**Increased exploration cost**~~ — Mitigated by not implementing topology discovery (Tier B)
2. **Over-filtering** — Proximity penalty (-15) combined with low establishment could suppress valid distant patterns. *Mitigation:* Graceful degradation note — patterns below 60 only due to proximity are noted as "existing approach in distant module" rather than silently dropped.
3. ~~**Complexity of instructions**~~ — Mitigated by keeping the agent at 228 lines (was 177), well under the 254-line ceiling of the longest agent
4. ~~**Codebase-specific knowledge needed**~~ — Mitigated by deferring topology discovery

### Edge Cases

**"Old but gold" patterns:** The staleness check distinguishes "stable, not declining" (report normally) from "declining, being replaced" (reduce confidence by 15). Stable is good.

**Small codebases:** The 3+ usage rule adjusts to ~15% threshold for codebases with <20 relevant files.

**Greenfield areas:** When the PR creates a new module/area, there may be no proximate patterns. The proximity modifier doesn't penalize this case — it only penalizes when patterns *are found* in distant locations.

**Multiple valid patterns:** Proximity is the tiebreaker — the pattern nearest to the changed files takes precedence.

---

## Implementation Summary

| # | Change | Status | Where in agent file | Impact |
|---|---|---|---|---|
| 1 | Add "Codebase Topology Discovery" step | DEFERRED | — | Most codebases too flat |
| 2 | Replace flat search with concentric-circle search | SIMPLIFIED | Finding Confidence section (proximity modifiers) | Core insight preserved via confidence modifiers |
| 3 | Add Pattern Relevance Score (separate from confidence) | DEFERRED | — | LLMs unreliable at multi-factor scoring |
| 4 | Add "3+ usage rule" as hard gate | **IMPLEMENTED** | New RULE 1 after RULE 0 | Prevents one-off enforcement |
| 5 | Add supersession detection process | **IMPLEMENTED** (corrected) | New Step 5 in Pattern Discovery | Prevents propagating dead patterns |
| 6 | Add gap-filler detection | DEFERRED | — | Too CIAB-specific |
| 7 | Update verdicts with contextual qualifiers | **IMPLEMENTED** | Output section | More actionable recommendations |
