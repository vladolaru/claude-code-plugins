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
 * Severity levels for findings
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
 * A decision-critic action, and the provenance it leaves on the finding or
 * check it touched. `critic_adjustments.py` is the sole writer.
 */
export interface CriticAdjustment {
    action: 'promote' | 'demote' | 'rescope' | 'correct' | 'add' | 'remove';
    rationale: string; // The critic's stated reason; '' when none was given.
    // The pre-patch value of just the fields this adjustment changed —
    // present only for promote/demote/rescope/correct. Absent for `add`
    // (nothing pre-existed) and `remove` (the whole finding is the change).
    prior?: Partial<Pick<Finding, 'severity' | 'title' | 'description' | 'recommendation' | 'file' | 'line' | 'category' | 'confidence'>> & Partial<Pick<ReviewCheck, 'question' | 'method' | 'result'>>;
}

/**
 * Verdict-bearing finding common to all review types.
 */
export interface Finding {
    id: string; // Monotonic canonical identifier: fN
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
    // `findings_removed_by_critic` below) removed it. Absent on every finding no
    // critic round has adjusted.
    critic_adjustment?: CriticAdjustment;
}

/**
 * Auditable verification work carried through reconciliation structurally.
 */
export interface ReviewCheck {
    id: string; // Monotonic canonical identifier: cN
    question: string;
    method: string;
    result: string;
    source_reviewers: string[];
    // Present only after critic_adjustments.py corrected this check, or on a
    // complete check moved into checks_removed_by_critic.
    critic_adjustment?: CriticAdjustment;
}

export interface InvalidatedAssessment {
    text: string;
    invalidated_by_critic_adjustment_ids: string[];
}

export type CriticTarget =
    | { kind: 'finding'; id: string }
    | { kind: 'check'; id: string }
    | { kind: 'finding' }; // `add`, before ledger-owned fN allocation

/**
 * Common review output structure for all agents
 */
export interface ReviewOutput {
    // Metadata
    pr_id: string;
    reviewer: string; // 'architecture' | 'security' | 'performance' | 'tests' | 'patterns'
    timestamp: string; // ISO 8601
    plugin_version: string | null; // pirategoat-tools version that produced this artifact; null when the producer could not name itself
    schema: number; // Current producer value is 2; see SCHEMA MAINTENANCE above

    // Summary
    verdict: Verdict;
    skip_reason?: string; // Why the agent did not review (only when verdict is 'not_applicable')
    summary: {
        total_findings: number;
        by_severity: {
            critical: number;
            high: number;
            medium: number;
            low: number;
            info: number;
        };
        suppressed_advisory_finding_count: number; // Advisory-tagged findings excluded from the verdict; always present, including 0 (and 0 for not_applicable).
        verdict_without_advisory?: Verdict; // Present only when suppression is non-zero and the verdict over all findings would be stricter.
    };

    findings: Finding[];
    checks: ReviewCheck[]; // Always present, including an empty array.

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
    // Also null when an applied decision-critic batch invalidated it. A
    // non-null value beside invalidated_assessments is the orchestrator's
    // revised_assessment installed by the adjustment applier.
    assessment: string | null;

    // Metadata
    meta: {
        // Milliseconds from this actor's dispatch marker to serialization.
        // Null when no marker was found (hand-rolled builder, standalone
        // use, unreadable stamp) — the builder has no clock of its own that
        // spans the review, so absence is reported as absence.
        review_duration_ms: number | null;
        confidence_score: ConfidenceScore; // Overall confidence
        next_finding_number: number;
        next_check_number: number;

        // Reconciliation accounting — present only on review-findings.json,
        // written by the review-reconciliator after semantic dedup, scope
        // checking, and fact verification. Renders as the "**Pipeline:**"
        // line and the not-applicable coverage line.
        reconciliation?: {
            input_finding_count: number;
            contributing_agent_count: number;
            grouped_concern_count: number;
            false_positive_finding_count: number;
            out_of_scope_finding_count: number;
            verified_finding_count: number;
            deduplication_ratio: number;
            not_applicable_agent_count: number;
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
    applied_critic_adjustments?: Array<{
        adjustment_id: string;
        spot_check: 'verified' | 'not_checked';
    }>;

    // The ledger's verdict BEFORE any critic batch applied, recorded the
    // first time an applying batch changed it — first time only, so a
    // second round names what the ledger came in as rather than what the
    // previous round left behind. `verdict` itself is recomputed from the
    // post-batch severities through the shared ladder in
    // scripts/review/verdict_rules.py, because step 11 DERIVES the
    // published pipeline verdict from it.
    verdict_before_adjustments?: Verdict | null;

    // Findings the critic removed. Moved out of `findings` rather than
    // deleted, each carrying the `critic_adjustment` record (see Finding
    // above) that removed it, so the decision stays auditable. Rendered as
    // the "## Removed by the Decision Critic" section.
    findings_removed_by_critic?: Finding[];

    // Checks removed by an applied critic batch remain auditable here.
    checks_removed_by_critic?: ReviewCheck[];

    // Critic decisions the orchestrator's adjudication request refuted. The
    // settle command derives `rejected: true` plus `rejection_reason` in the
    // checkpointed decision-critic-adjustments.json document.
    // Present after the first batch that settled at least one rejection.
    // A rejected entry is never applied to `findings` — the target finding
    // is never mutated — so this is the canonical place a rejection is
    // auditable. The shared Markdown renderer projects each record as an
    // explicit `adjustment_id — refuted` line.
    // Cumulative across every batch the ledger absorbs, the same way
    // applied_critic_adjustments is; apply_adjustments() dedupes by
    // adjustment_id so a resumed or repeated apply never appends a duplicate.
    rejected_critic_adjustments?: Array<{
        adjustment_id: string;
        action: 'promote' | 'demote' | 'rescope' | 'correct' | 'add' | 'remove';
        target: CriticTarget;
        spot_check: 'refuted';
        rejection_reason: string;
    }>;

    // Assessments invalidated by an applying batch, oldest first.
    invalidated_assessments?: InvalidatedAssessment[];
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
