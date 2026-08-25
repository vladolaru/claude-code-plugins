/**
 * TypeScript Type Definitions for Review Agent Outputs
 *
 * These schemas define the structured output format for all review agents,
 * enabling reliable parsing, automation, and integration.
 *
 * SCHEMA MAINTENANCE: the artifacts declared here carry an integer `schema`
 * field (REVIEW_OUTPUT_SCHEMA in scripts/review/agent/output.py). When their
 * shape changes — a key added, removed, or re-typed — bump it in the SAME
 * commit as the change, update the interface below to match, and note the
 * bump in the changelog. A schema number that lags the shape is worse than
 * none: it states a compatibility guarantee the producer is not honoring.
 * One carve-out, spelled out beside REVIEW_OUTPUT_SCHEMA and in AGENTS.md: a
 * shape change made inside the SAME unreleased version that introduced the
 * current number updates this file without moving the number, because the
 * number only guarantees anything once released.
 * Other artifact families carry their own `schema` constants; see the
 * Artifact Schemas section of the plugin's AGENTS.md for the full list and
 * for which artifacts deliberately carry no schema at all.
 *
 * Implements: Proposal #3 (Structured Output) from Tier 1 agentic patterns
 */

/**
 * Severity levels for issues
 */
export type Severity = 'critical' | 'high' | 'medium' | 'low' | 'info';

/**
 * Verdict options for reviews
 */
export type Verdict = 'block' | 'request_changes' | 'approve' | 'comment' | 'not_applicable';

/**
 * Confidence score (0.0 to 1.0)
 */
export type ConfidenceScore = number; // 0.0 - 1.0

/**
 * A decision-critic action, and the provenance it leaves on the finding it
 * touched. `critic_adjustments.py` writes this directly onto the target
 * issue object — for `add`, onto the newly created issue; for
 * `promote`/`demote`/`rescope`/`correct`, onto the finding it patched
 * in-place (still inside `issues`); for `remove`, onto the finding as it is
 * moved into `removed_by_critic`. Never written by anything else.
 */
export interface CriticAdjustment {
    action: 'promote' | 'demote' | 'rescope' | 'correct' | 'add' | 'remove';
    rationale: string; // The critic's stated reason; '' when none was given.
    // The pre-patch value of just the fields this adjustment changed —
    // present only for promote/demote/rescope/correct. Absent for `add`
    // (nothing pre-existed) and `remove` (the whole finding is the change).
    prior?: Partial<Pick<Issue, 'severity' | 'title' | 'description' | 'recommendation' | 'file' | 'line' | 'category' | 'confidence'>>;
}

/**
 * Base issue structure common to all review types
 */
export interface Issue {
    id: string; // Unique identifier for this issue
    category: string; // bug | security | performance | architecture | style
    severity: Severity;
    severity_floor?: Severity; // Lowest severity allowed after verification/reconciliation
    title: string; // Short description
    description: string; // Detailed explanation
    file: string; // Path to file
    line: number | null; // Line number. null for file-scoped findings (missing coverage, precedent, cross-file architecture)
    scope?: 'file'; // Present (with line: null) when the finding is file-scoped rather than line-anchored
    code_snippet?: string; // Relevant code (optional)
    recommendation: string; // How to fix
    confidence: ConfidenceScore;
    references?: string[]; // Links to docs, patterns, skills
    behavior_evidence?: 'cited' | 'inferred';
    source_cited?: string; // "<file>:<line>" pointer to upstream evidence
    channel?: 'blocking' | 'advisory'; // Exact accepted input vocabulary. 'blocking' is the default and is canonicalized to absence; entitled 'advisory' findings remain listed but are excluded from the verdict.
    // Present when a decision-critic batch touched this finding: promoted,
    // demoted, rescoped, corrected, or added it, or (on entries moved into
    // `removed_by_critic` below) removed it. Absent on every finding no
    // critic round has adjusted.
    critic_adjustment?: CriticAdjustment;
}

/**
 * Auditable absence claim: "nothing depends on this" with the exact
 * verification method stated so downstream stages can judge coverage.
 * A negative search proves only that the searched pattern is absent —
 * the method field is what makes that limit visible.
 */
export interface Clearance {
    claim: string; // The absence being asserted
    method: string; // Exact searches run / files read (required)
    // Hit counts, file:line list, and — in a reconciled ledger — the
    // agents the clearance came from ("per security-reviewer,
    // concurrency-reviewer — 0 in-tree consumers"). Attribution rides in
    // this free-text field by convention, with no field of its own:
    // reconciliation collapses method-correlated clearances into ONE
    // entry, so the names of every agent that ran the shared probe are
    // what has to survive the merge. Unvalidated by construction — the
    // contract lives in agents/review-reconciliator.md.
    evidence?: string | null;
}

/**
 * Common review output structure for all agents
 */
export interface ReviewOutput {
    // Metadata
    pr_id: string;
    reviewer: string; // 'architecture' | 'security' | 'performance' | 'tests' | 'patterns'
    timestamp: string; // ISO 8601
    plugin_version: string | null; // pirategoat-tools version that produced this artifact; null when the producer could not name itself
    schema: number; // Shape of this artifact — see SCHEMA MAINTENANCE above

    // Summary
    verdict: Verdict;
    skip_reason?: string; // Why the agent did not review (only when verdict is 'not_applicable')
    summary: {
        total_issues: number;
        by_severity: {
            critical: number;
            high: number;
            medium: number;
            low: number;
            info: number;
        };
        advisory_suppressed: number; // Advisory-tagged findings excluded from the verdict; always present, including 0 (and 0 for not_applicable).
        verdict_without_advisory?: Verdict; // Present only when advisory_suppressed > 0 and the verdict over all findings would be stricter.
    };

    // Issues
    issues: Issue[]; // Findings recorded by the producer, possibly carrying a critic_adjustment (see Issue above) if a critic round has touched them.

    // Canonical reviewed-file accounting derived from the system-authored
    // accounting input and the reviewer's validated positive claims.
    review_claimable_files: string[];
    reviewed_file_claims: string[];
    unclaimed_review_files: string[];
    inline_diff_file_count: number;
    review_accounted_file_count: number;
    in_scope_review_file_count: number;

    // Recommendations (optional)
    recommendations?: {
        immediate: string[]; // Must fix before merge
        important: string[]; // Should fix soon
        suggestions: string[]; // Nice to have
    };

    // Positive observations (optional)
    positive_observations?: string[];

    // File-level informational notes that do NOT count toward the verdict.
    // The reconciliator records verified, maintainer-intended tradeoffs here
    // (category: "tradeoff") — trigger condition, affected population
    // verified at file:line, and why the compromise is intentional.
    observations?: Array<{ file: string; note: string; category: string }> | null;

    // The producer's own reading of the change as a whole — two or three
    // sentences the list of findings cannot express. Always present, null
    // when the producer said nothing. Rendered as the "## Assessment"
    // section of the derived Markdown.
    //
    // Also null when a decision-critic batch WITHDREW it: the critic's
    // adjustment vocabulary addresses issues, not ledger-level prose, so an
    // assessment the applied batch may have contradicted is retracted
    // rather than corrected. The retracted text moves to
    // withdrawn_narrative_summary below; a non-empty
    // withdrawn_narrative_summary is what the renderer reads as "withdrawn"
    // rather than "never written".
    //
    // Non-null BESIDE a withdrawal record means the orchestrator supplied a
    // post-critic assessment through the validated adjudication request. The
    // settle command checkpoints it under `adjudication.revised_narrative`,
    // and apply_adjustments() moves it into this field. The renderer
    // attributes it accordingly — the reconciler's marker would credit prose
    // that was retracted a step earlier.
    narrative_summary: string | null;

    // Clearances (optional) — auditable absence claims ("nothing depends on
    // the removed X") carrying the verification method. Unlike positives,
    // these flow into the reconciliation context so clearance-vs-finding
    // conflicts are visible downstream.
    clearances?: Clearance[];

    // Metadata
    meta: {
        // Milliseconds from this actor's dispatch marker to serialization.
        // Null when no marker was found (hand-rolled builder, standalone
        // use, unreadable stamp) — the builder has no clock of its own that
        // spans the review, so absence is reported as absence.
        review_duration_ms: number | null;
        confidence_score: ConfidenceScore; // Overall confidence
        tool_results_used?: string[]; // e.g., ['test-results', 'semgrep']

        // Reconciliation accounting — present only on review-findings.json,
        // written by the review-reconciliator after semantic dedup, scope
        // checking, and fact verification. Renders as the "**Pipeline:**"
        // line and the not-applicable coverage line.
        reconciliation?: {
            input_findings_count: number;
            agents_contributing: number;
            concerns_after_grouping: number;
            false_positives_dropped: number;
            out_of_scope_dropped: number;
            verified_concerns: number;
            merge_ratio: number;
            not_applicable_count: number;
            not_applicable_agents: Array<{ name: string; skip_reason: string }>;
            reviewing_agents: string[];
            dispatched_agents: string[];
            missing_agents: string[];
        };
    };

    // Host context banner — present only on review-findings.json, copied
    // through by the reconciliator when upstream host discovery was
    // degraded. Rendered as a blockquote directly under the H1.
    host_context_banner?: HostContextBanner | null;

    // Decision-critic provenance — present only on review-findings.json,
    // and only once critic_adjustments.py has applied a batch.

    // One record per adjustment this ledger already contains. Present after
    // the first applied batch. `adjustment_id` is the idempotence
    // bookkeeping — a crash between the two writes converges on it —
    // and `spot_check` is script-derived from the orchestrator's exact
    // settlement request, checkpointed in decision-critic-adjustments.json,
    // and carried here on apply. IDs omitted from the positive verified and
    // refuted claims are derived as "not_checked". Step 11's defensive
    // recovery records every entry that way when no adjudication exists.
    // Rendered with rejected decisions in the "## Critic Adjustment
    // Decisions" list.
    //
    // Readers must tolerate a bare-string entry: that is the shape this key
    // held earlier in the same unreleased window, and apply_adjustments()
    // still reads it (as "not_checked") so a ledger written then keeps its
    // idempotence. Nothing writes that shape any more.
    applied_critic_adjustments?: Array<
        { adjustment_id: string; spot_check: 'verified' | 'refuted' | 'not_checked' }
        | string
    >;

    // The ledger's verdict BEFORE any critic batch applied, recorded the
    // first time an applying batch changed it — first time only, so a
    // second round names what the ledger came in as rather than what the
    // previous round left behind. `verdict` itself is recomputed from the
    // post-batch severities through the shared ladder in
    // scripts/review/verdict_rules.py, because step 11 DERIVES the
    // published pipeline verdict from it.
    verdict_before_adjustments?: Verdict | null;

    // Findings the critic removed. Moved out of `issues` rather than
    // deleted, each carrying the `critic_adjustment` record (see Issue
    // above) that removed it, so the decision stays auditable. Rendered as
    // the "## Removed by the Decision Critic" section.
    removed_by_critic?: Issue[];

    // Critic decisions the orchestrator's adjudication request refuted. The
    // settle command derives `rejected: true` plus `rejection_reason` in the
    // checkpointed decision-critic-adjustments.json document.
    // Present after the first batch that settled at least one rejection.
    // A rejected entry is never applied to `issues` — the target finding
    // is never mutated — so this is the canonical place a rejection is
    // auditable. The shared Markdown renderer projects each record as an
    // explicit `adjustment_id — refuted` line.
    // Cumulative across every batch the ledger absorbs, the same way
    // applied_critic_adjustments is; apply_adjustments() dedupes by
    // adjustment_id so a resumed or repeated apply never appends a duplicate.
    rejected_critic_adjustments?: Array<{
        adjustment_id: string;
        action: string | null;
        target_id: string | null; // null for a rejected `add` (no target)
        // absent on legacy schema-1 records; this bucket implies refuted
        spot_check?: 'refuted';
        rejection_reason: string;
    }>;

    // Assessments retracted by an applying batch, oldest first. Each entry
    // keeps the prose and the ids of the decisions that cost it its
    // standing — withdrawn, never silently dropped.
    withdrawn_narrative_summary?: Array<{
        text: string;
        withdrawn_by: string[];
    }>;
}

/**
 * Host Context Banner — present when upstream discovery was degraded.
 * Reviewers' claims that depend on unresolved hosts must be read in light
 * of this banner.
 */
export interface HostContextBanner {
    degraded: boolean;
    reason: "partial_unresolved" | "fully_unavailable";
    message: string;
    unresolved: Array<{ name: string; reason: string; source?: string }>;
}
