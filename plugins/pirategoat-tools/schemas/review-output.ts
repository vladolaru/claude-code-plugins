/**
 * TypeScript Type Definitions for Review Agent Outputs
 *
 * These schemas define the structured output format for all review agents,
 * enabling reliable parsing, automation, and integration.
 *
 * SCHEMA MAINTENANCE: the artifacts declared here carry an integer `schema`
 * field — REVIEW_OUTPUT_SCHEMA in scripts/review/agent/output.py for
 * ReviewDocument (current value 2), LEDGER_SCHEMA in
 * scripts/review/findings_ledger.py for FindingsLedger (current value 3).
 * When either shape changes — a key added, removed, or re-typed — bump the
 * matching constant in the SAME commit as the change, update the interface
 * below to match, and note the bump in the changelog. A schema number that
 * lags the shape is worse than none: it states a compatibility guarantee
 * the producer is not honoring.
 * One carve-out, spelled out beside REVIEW_OUTPUT_SCHEMA/LEDGER_SCHEMA and
 * in AGENTS.md: a shape change made inside the SAME unreleased version that
 * introduced the current number updates this file without moving the
 * number, because the number only guarantees anything once released.
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
 * Canonical ledger identifiers. Runtime validation narrows these template
 * forms to positive integers with no leading zero.
 */
export type FindingId = `f${number}`;
export type CheckId = `c${number}`;

/**
 * Verdict-bearing finding common to all review types.
 */
export interface Finding {
    id: FindingId; // Monotonic canonical identifier: fN
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
    critic_adjustment?: FindingCriticAdjustment;
}

/**
 * Auditable verification work carried through reconciliation structurally.
 */
export interface ReviewCheck {
    id: CheckId; // Monotonic canonical identifier: cN
    question: string;
    method: string;
    result: string;
    source_reviewers: string[];
    // Present only after critic_adjustments.py corrected this check, or on a
    // complete check moved into checks_removed_by_critic.
    critic_adjustment?: CheckCriticAdjustment;
}

export interface InvalidatedAssessment {
    text: string;
    invalidated_by_critic_adjustment_ids: string[];
}

type AtLeastOne<T> = {
    [Key in keyof T]-?: Required<Pick<T, Key>> & Partial<Omit<T, Key>>;
}[keyof T];

type FindingPatchFields = Pick<Finding, 'severity' | 'title' | 'description' | 'recommendation' | 'file' | 'line' | 'category' | 'confidence'>;
export type FindingCorrectionFields = AtLeastOne<FindingPatchFields>;
export type CheckCorrectionFields = AtLeastOne<Pick<ReviewCheck, 'question' | 'method' | 'result'>>;
export type FindingAddFields = Pick<Finding, 'severity' | 'title' | 'file' | 'description' | 'recommendation'> & Partial<Pick<Finding, 'line' | 'category' | 'confidence'>>;

export type FindingTarget = { kind: 'finding'; id: FindingId };
export type CheckTarget = { kind: 'check'; id: CheckId };
export type FindingAddTarget = { kind: 'finding'; id?: never };

/**
 * The exact adjustment shapes accepted from the decision critic. Actions,
 * targets, and fields stay correlated so an actor cannot supply ledger-owned
 * identity, lifecycle, or provenance fields.
 */
export type CriticProposalAdjustment =
    | { action: 'add'; target: FindingAddTarget; fields: FindingAddFields; rationale: string }
    | { action: 'promote' | 'demote'; target: FindingTarget; fields: Pick<Finding, 'severity'>; rationale: string }
    | { action: 'rescope'; target: FindingTarget; fields: Pick<Finding, 'file' | 'line'>; rationale: string }
    | { action: 'correct'; target: FindingTarget; fields: FindingCorrectionFields; rationale: string }
    | { action: 'correct'; target: CheckTarget; fields: CheckCorrectionFields; rationale: string }
    | { action: 'remove'; target: FindingTarget; fields: Record<string, never>; rationale: string }
    | { action: 'remove'; target: CheckTarget; fields: Record<string, never>; rationale: string };

type WithAdjustmentId<Adjustment> = Adjustment extends unknown
    ? Adjustment & { adjustment_id: string }
    : never;
export type PreparedCriticAdjustment = WithAdjustmentId<CriticProposalAdjustment>;

/**
 * The committed critic proposal — decision-critic-adjustments.json — schema
 * 2 (ADJUSTMENTS_SCHEMA in scripts/review/critic_adjustments.py). The
 * critic never authors adjudication state; that lives only in the ledger
 * (see CriticAppliedAdjustment / CriticRejectedAdjustment below) and in the
 * orchestrator's one-shot AdjudicationRequest.
 */
export type CriticAdjustmentsDocument = {
    schema: 2;
    adjustments: PreparedCriticAdjustment[];
};

/**
 * The changed-fields-only provenance carried by accepted ledger operations.
 * `critic_adjustments.py` is the sole writer.
 */
export type FindingCriticAdjustment =
    | { action: 'add' | 'remove'; rationale: string; prior?: never }
    | { action: 'promote' | 'demote'; rationale: string; prior: Pick<Finding, 'severity'> }
    | { action: 'rescope'; rationale: string; prior: AtLeastOne<Pick<Finding, 'file' | 'line'>> }
    | { action: 'correct'; rationale: string; prior: FindingCorrectionFields };

export type CheckCriticAdjustment =
    | { action: 'remove'; rationale: string; prior?: never }
    | { action: 'correct'; rationale: string; prior: CheckCorrectionFields };

type CriticActionTarget<Adjustment> = Adjustment extends {
    action: infer Action;
    target: infer Target;
}
    ? { action: Action; target: Target }
    : never;

/**
 * The three outcomes an adjudicated critic decision can land in — OUTCOMES
 * in scripts/review/critic_adjustments.py. `not_checked` is the
 * script-derived default for every committed adjustment id the
 * orchestrator's AdjudicationRequest did not name.
 */
export type AdjudicationOutcome = 'verified' | 'refuted' | 'not_checked';

export type CriticRejectedAdjustment = CriticActionTarget<CriticProposalAdjustment> & {
    adjustment_id: string;
    outcome: Extract<AdjudicationOutcome, 'refuted'>;
    rejection_reason: string;
};

export interface CriticAppliedAdjustment {
    adjustment_id: string;
    outcome: Exclude<AdjudicationOutcome, 'refuted'>;
}

/**
 * The orchestrator's settle request — the sole input to
 * critic_adjustments.adjudicate(). Schema 2 (ADJUDICATION_SCHEMA). Every
 * committed adjustment id absent from both `verified` and `refuted` is
 * recorded as 'not_checked'.
 */
export interface AdjudicationRequest {
    schema: 2;
    verified: string[];
    refuted: Array<{ adjustment_id: string; rejection_reason: string }>;
    revised_assessment: string | null;
}

/**
 * Reviewer-authored bookkeeping common to every content-bearing review
 * artifact.
 */
export interface ReviewMeta {
    // Milliseconds from this actor's dispatch marker to serialization.
    // Null when no marker was found (hand-rolled builder, standalone
    // use, unreadable stamp) — the builder has no clock of its own that
    // spans the review, so absence is reported as absence.
    review_duration_ms: number | null;
    confidence_score: ConfidenceScore; // Overall confidence
    next_finding_number: number;
    next_check_number: number;
}

/**
 * Review content shared by every content-bearing review artifact — exactly
 * the fields REVIEW_CONTENT_FIELDS (plus optional skip_reason) requires in
 * scripts/review/agent/output.py — before either the reviewer envelope
 * (ReviewDocument) or the reconciliation extensions (FindingsLedger) are
 * layered on.
 */
export interface ReviewContent {
    pr_id: string;
    timestamp: string; // ISO 8601
    plugin_version: string | null; // pirategoat-tools version that produced this artifact; null when the producer could not name itself
    schema: number; // 2 on ReviewDocument, 3 on FindingsLedger; see SCHEMA MAINTENANCE above

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

    // File-level informational notes that do NOT count toward the verdict.
    // The reconciliator records verified, maintainer-intended tradeoffs here
    // (category: "tradeoff") — trigger condition, affected population
    // verified at file:line, and why the compromise is intentional. The key
    // is always present; null when there is nothing to record.
    observations: Array<{ file: string; note: string; category: string }> | null;

    // The key is always present; null when the producer proposed none.
    recommendations: {
        immediate: string[]; // Must fix before merge
        important: string[]; // Should fix soon
        suggestions: string[]; // Nice to have
    } | null;

    // The key is always present; null when the producer offered none.
    positive_observations: string[] | null;

    // The producer's own reading of the change as a whole — two or three
    // sentences the list of findings cannot express. Always present, null
    // when the producer said nothing. Rendered as the "## Assessment"
    // section of the derived Markdown.
    //
    // On FindingsLedger, also null when an applied decision-critic batch
    // invalidated it — see FindingsLedger.invalidated_assessments below. A
    // non-null value beside it is the orchestrator's revised_assessment
    // installed by the adjustment applier.
    assessment: string | null;

    meta: ReviewMeta;
}

/**
 * One reviewer's immutable final artifact — <reviewer>-review.json — schema
 * 2. ReviewContent plus the reviewer identity and the six canonical
 * reviewed-file fields (REVIEWER_FIELDS in
 * scripts/review/agent/output.py), derived from the system-authored
 * assignment and the reviewer's validated positive claims.
 */
export interface ReviewDocument extends ReviewContent {
    schema: 2;
    reviewer: string; // 'architecture' | 'security' | 'performance' | 'tests' | 'patterns'
    review_claimable_files: string[];
    reviewed_file_claims: string[];
    unclaimed_review_files: string[];
    inline_diff_file_count: number;
    reviewed_file_count: number;
    in_scope_review_file_count: number;
}

/**
 * The reconciler never abstains — review-findings.json always carries one
 * of the four gating verdicts, never 'not_applicable'.
 */
export type LedgerVerdict = Exclude<Verdict, 'not_applicable'>;

/**
 * The reconciliation accounting stamped onto the ledger's meta —
 * RECONCILIATION_FIELDS in scripts/review/findings_ledger.py. Renders as
 * the "**Pipeline:**" line and the not-applicable coverage line. The four
 * judgment counts (grouped/verified/false_positive/out_of_scope) are the
 * reconciliator's own; the remaining six are pipeline-measured and stitched
 * on by findings_save.py — the builder never authors them.
 */
export interface Reconciliation {
    grouped_concern_count: number;
    verified_concern_count: number;
    false_positive_concern_count: number;
    out_of_scope_concern_count: number;
    input_finding_count: number;
    contributing_agent_count: number;
    reviewing_agents: string[];
    dispatched_agents: string[] | null;
    missing_agents: string[] | null;
    not_applicable_agents: Array<{ name: string; skip_reason: string }>;
}

/**
 * The reconciled findings ledger — review-findings.json — schema 3.
 * ReviewContent plus the required reconciliation block and the
 * decision-critic's applied/rejected provenance extensions, each present
 * only once the matching stage has actually run.
 */
export interface FindingsLedger extends ReviewContent {
    schema: 3;
    verdict: LedgerVerdict;
    meta: ReviewMeta & { reconciliation: Reconciliation };

    // Host context banner — present only when upstream host discovery was
    // degraded, copied through by the reconciliator. Rendered as a
    // blockquote directly under the H1.
    host_context_banner?: HostContextBanner | null;

    // Decision-critic provenance — present only once critic_adjustments.py
    // has applied a batch.

    // One record per adjustment this ledger already contains. Present after
    // the first applied batch. `adjustment_id` is the idempotence
    // bookkeeping — a crash between the two writes converges on it —
    // and `outcome` is script-derived from the orchestrator's exact
    // AdjudicationRequest, checkpointed in decision-critic-adjustments.json,
    // and carried here on apply. IDs omitted from the positive verified and
    // refuted claims are derived as "not_checked". Step 11's defensive
    // recovery records every entry that way when no adjudication exists.
    // Rendered with rejected decisions in the "## Critic Adjustment
    // Decisions" list.
    applied_critic_adjustments?: CriticAppliedAdjustment[];

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

    // Critic decisions the orchestrator's AdjudicationRequest refuted. The
    // settle command derives `outcome: 'refuted'` plus `rejection_reason`
    // in the checkpointed decision-critic-adjustments.json document.
    // Present after the first batch that settled at least one rejection.
    // A rejected entry is never applied to `findings` — the target finding
    // is never mutated — so this is the canonical place a rejection is
    // auditable. The shared Markdown renderer projects each record as an
    // explicit `adjustment_id — refuted` line.
    // Cumulative across every batch the ledger absorbs, the same way
    // applied_critic_adjustments is; apply_adjustments() dedupes by
    // adjustment_id so a resumed or repeated apply never appends a duplicate.
    rejected_critic_adjustments?: CriticRejectedAdjustment[];

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
