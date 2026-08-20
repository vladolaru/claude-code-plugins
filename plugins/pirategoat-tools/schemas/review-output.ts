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
    line?: number | null; // Line number. null for file-scoped findings (missing coverage, precedent, cross-file architecture)
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
    evidence?: string | null; // Hit counts, file:line list (optional)
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

    // Declared coverage gap (null when nothing declared) — canonical
    // repo-relative paths of in-scope NOT DIFFED files the reviewer could
    // not reach at budget exhaustion. Never counts toward the verdict.
    unreviewed: string[] | null;

    // Explicit deferred-review claims — ALWAYS present (never null; [] means
    // "claimed nothing"). Key presence distinguishes explicit-claims output
    // from legacy output where silence was read as a claim. A claim is a
    // statement, not proof of read, and never counts toward the verdict.
    deferred_reviewed: string[];

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
    narrative_summary: string | null;

    // Clearances (optional) — auditable absence claims ("nothing depends on
    // the removed X") carrying the verification method. Unlike positives,
    // these flow into the reconciliation context so clearance-vs-finding
    // conflicts are visible downstream.
    clearances?: Clearance[];

    // Metadata
    meta: {
        files_reviewed: number;
        // Subset of `unreviewed` the builder auto-declared at save time
        // because the reviewer neither claimed nor declared those deferred
        // files (null when nothing was auto-filled). Marked so metrics can
        // separate agent honesty from system honesty. Required going
        // forward; artifacts produced before save-time auto-fill carry no
        // such key, so consumers must tolerate its absence.
        unreviewed_autofilled: string[] | null;
        review_duration_ms?: number;
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

    // Ids of the adjustments this ledger already contains. Present after
    // the first applied batch; its non-emptiness beside a null
    // narrative_summary is what distinguishes a withdrawn assessment from
    // one the producer never wrote.
    applied_critic_adjustments?: string[];

    // Findings the critic removed. Moved out of `issues` rather than
    // deleted, each carrying the `critic_adjustment` record (see Issue
    // above) that removed it, so the decision stays auditable. Rendered as
    // the "## Removed by the Decision Critic" section.
    removed_by_critic?: Issue[];

    // Critic decisions the orchestrator's spot-check refuted (`rejected:
    // true` + `rejection_reason` in decision-critic-adjustments.json).
    // Present after the first batch that settled at least one rejection.
    // A rejected entry is never applied to `issues` — the target finding
    // is never mutated — so this is the ONLY place a rejection is
    // auditable. The source file it also lives in is read only by
    // apply_adjustments() itself, never by a downstream consumer.
    // Cumulative across every batch the ledger absorbs, the same way
    // applied_critic_adjustments is; apply_adjustments() dedupes by
    // adjustment_id so a re-run never appends a duplicate. An entry
    // carrying BOTH `applied: true` and `rejected: true` (a post-hoc hand
    // edit of decision-critic-adjustments.json) is never recorded here —
    // the applied mutation is ground truth, and auditing the coexisting
    // rejected flag would publish two contradictory outcomes for one
    // adjustment_id.
    rejected_critic_adjustments?: Array<{
        adjustment_id: string;
        action: string | null;
        target_id: string | null; // null for a rejected `add` (no target)
        rejection_reason: string;
    }>;

    // Assessments retracted by an applying batch, oldest first. Each entry
    // keeps the prose and the ids of the decisions that cost it its
    // standing — withdrawn, never silently dropped.
    withdrawn_narrative_summary?: Array<{
        text: string;
        withdrawn_by: string[];
    }>;

    // Tamper stamp — present only on review-findings.json, written by
    // critic_adjustments.write_findings(), which is the ONE sanctioned
    // write path for that file. All three of its writers go through it:
    // the review-reconciliator's first write, critic_adjustments.py
    // applying a batch, and the pipeline's Rule 23 verdict sync.
    //
    // Value: SHA-256 (hex) over the canonical serialization of this whole
    // object MINUS this field — JSON.stringify equivalent with sorted keys,
    // compact `,`/`:` separators, and UTF-8 (no \uXXXX escaping). Excluding
    // the field from its own input is what makes re-stamping unchanged
    // content a fixed point, so two in-channel writes in a row both verify.
    //
    // Step 11 recomputes it before and after finalize's own writes and
    // publishes the result in pipeline-result.json as `post_apply_integrity`
    // — `"intact"`, or `"modified_out_of_channel"` (plus a degradation note
    // and a degraded run status). The field is ABSENT from that result, not
    // null, in two cases a consumer cannot tell apart from the key alone:
    // no ledger existed to verify, or the ledger existed but could not be
    // read (unreadable bytes are not evidence of tampering — the fault is
    // recorded by its own degradation note instead). Either way a consumer
    // must not read absence as "verified intact".
    //
    // A findings file carrying no content_digest reads as
    // `"modified_out_of_channel"`, deliberately: within the version that
    // introduced this field every sanctioned writer stamps, so an unstamped
    // ledger at finalize means an out-of-channel rewrite dropped the key.
    // Absence of the stamp on a file the channel always stamps IS the
    // evidence — a naive hand-built `json.dump` looks exactly like this.
    content_digest?: string;
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
