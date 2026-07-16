/**
 * TypeScript Type Definitions for Review Agent Outputs
 *
 * These schemas define the structured output format for all review agents,
 * enabling reliable parsing, automation, and integration.
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
}

/**
 * Security-specific issue with exploitation details
 */
export interface SecurityIssue extends Issue {
    category: 'security';
    vulnerability_type: 'sql_injection' | 'xss' | 'csrf' | 'broken_access_control' | 'sensitive_data_exposure' | 'other';
    cvss_score?: number; // 0.0 - 10.0
    attack_complexity: 'low' | 'medium' | 'high';
    requires_auth: boolean;
    exploitation_example?: string; // curl command or attack vector
    mitigations_present: string[]; // Existing security controls
    mitigations_missing: string[]; // Missing security controls
}

/**
 * Performance-specific issue with scale impact
 */
export interface PerformanceIssue extends Issue {
    category: 'performance';
    issue_type: 'n_plus_one' | 'missing_cache' | 'inefficient_query' | 'memory_leak' | 'slow_algorithm' | 'other';
    current_impact: string; // "2-5 seconds at current scale"
    scale_10x: string; // "20-50 seconds at 10x scale"
    scale_100x: string; // "Site crash at 100x scale"
    optimization_potential: string; // "101 queries → 1 query"
    caching_applicable: boolean;
}

/**
 * Architecture-specific issue with pattern recommendations
 */
export interface ArchitectureIssue extends Issue {
    category: 'architecture';
    issue_type: 'solid_violation' | 'tight_coupling' | 'god_object' | 'missing_abstraction' | 'pattern_misuse' | 'other';
    solid_principles_violated?: ('SRP' | 'OCP' | 'LSP' | 'ISP' | 'DIP')[];
    pattern_opportunity?: string; // "Strategy pattern recommended"
    pattern_reference?: string; // "patterns/behavioral/strategy.md"
    refactoring_effort: 'low' | 'medium' | 'high'; // Hours to fix
    testability_impact: string; // "0/10 → 9/10 after refactoring"
}

/**
 * Test quality-specific issue
 */
export interface TestIssue extends Issue {
    category: 'test_quality';
    issue_type: 'false_confidence' | 'flaky' | 'brittle' | 'slow' | 'poor_structure' | 'missing_coverage' | 'other';
    test_principle_violated?: ('behavior_based' | 'independent' | 'deterministic' | 'fast' | 'readable' | 'single_concern')[];
    root_cause: 'test_problem' | 'implementation_problem' | 'both';
    fix_complexity: 'trivial' | 'moderate' | 'complex';
}

/**
 * Pattern consistency issue
 */
export interface PatternIssue extends Issue {
    category: 'pattern_consistency';
    issue_type: 'duplication' | 'inconsistency' | 'naming_deviation' | 'consolidation_opportunity' | 'other';
    existing_pattern?: string; // Reference to existing implementation
    git_history_reference?: string; // Commit hash
    consistency_score?: number; // 0.0-1.0 (e.g., 0.965 = 96.5% consistent)
    consolidation_benefit?: string; // "-89 lines of duplicate code"
}

/**
 * Common review output structure for all agents
 */
export interface ReviewOutput {
    // Metadata
    pr_id: string;
    reviewer: string; // 'architecture' | 'security' | 'performance' | 'tests' | 'patterns'
    timestamp: string; // ISO 8601
    version: string; // Schema version for compatibility

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
    };

    // Issues
    issues: Issue[]; // Can be SecurityIssue, PerformanceIssue, etc.

    // Recommendations (optional)
    recommendations?: {
        immediate: string[]; // Must fix before merge
        important: string[]; // Should fix soon
        suggestions: string[]; // Nice to have
    };

    // Positive observations (optional)
    positive_observations?: string[];

    // Metadata
    meta: {
        files_reviewed: number;
        review_duration_ms?: number;
        confidence_score: ConfidenceScore; // Overall confidence
        tool_results_used?: string[]; // e.g., ['test-results', 'semgrep']
    };
}

/**
 * Aggregated review output from multiple agents
 */
export interface AggregatedReview {
    pr_id: string;
    timestamp: string;
    version: string;

    // Overall verdict (most restrictive wins)
    overall_verdict: Verdict;

    // Aggregated summary
    summary: {
        total_issues: number;
        by_reviewer: {
            [reviewer: string]: number;
        };
        by_severity: {
            critical: number;
            high: number;
            medium: number;
            low: number;
            info: number;
        };
    };

    // All issues from all reviewers
    all_issues: Issue[];

    // Individual reviewer outputs
    reviewers: {
        [reviewer: string]: ReviewOutput;
    };

    // Meta
    meta: {
        reviewers_completed: string[];
        reviewers_failed: string[];
        total_duration_ms: number;
        parallel_execution: boolean;
    };

    // Host context banner — forwarded from reconciliation when upstream discovery was degraded.
    host_context_banner?: HostContextBanner | null;
}

/**
 * Host Context Banner — present when upstream discovery was degraded.
 * Reviewers' claims that depend on unresolved hosts must be read in light
 * of this banner.
 */
export interface HostContextBanner {
    degraded: boolean;
    reason: "partial_unresolved" | "fully_unavailable" | "install_failed";
    message: string;
    unresolved: Array<{ name: string; reason: string; source?: string }>;
}

/**
 * Helper type guards for type narrowing
 */
export function isSecurityIssue(issue: Issue): issue is SecurityIssue {
    return issue.category === 'security';
}

export function isPerformanceIssue(issue: Issue): issue is PerformanceIssue {
    return issue.category === 'performance';
}

export function isArchitectureIssue(issue: Issue): issue is ArchitectureIssue {
    return issue.category === 'architecture';
}

export function isTestIssue(issue: Issue): issue is TestIssue {
    return issue.category === 'test_quality';
}

export function isPatternIssue(issue: Issue): issue is PatternIssue {
    return issue.category === 'pattern_consistency';
}
