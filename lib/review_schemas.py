"""
Python Pydantic Models for Review Agent Structured Output

These models provide runtime validation for review agent JSON outputs,
ensuring data integrity and enabling reliable automation.

Implements: Proposal #3 (Structured Output) from Tier 1 agentic patterns

Usage:
    from review_schemas import ReviewOutput, SecurityIssue

    # Create review output
    review = ReviewOutput(
        pr_id="123",
        reviewer="security",
        verdict="block",
        ...
    )

    # Validate and serialize
    json_output = review.model_dump_json(indent=2)
"""

from pydantic import BaseModel, Field, field_validator, model_validator
from typing import List, Optional, Literal, Dict
from datetime import datetime
from enum import Enum

# ============================================================================
# Enums
# ============================================================================

class Severity(str, Enum):
    CRITICAL = "critical"
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"
    INFO = "info"

class Verdict(str, Enum):
    BLOCK = "block"
    REQUEST_CHANGES = "request_changes"
    APPROVE = "approve"
    COMMENT = "comment"

# ============================================================================
# Base Models
# ============================================================================

class Issue(BaseModel):
    """Base issue structure common to all review types."""

    id: str = Field(..., description="Unique identifier for this issue")
    category: str = Field(..., description="Issue category: bug, security, performance, architecture, style, test_quality, pattern_consistency")
    severity: Severity
    title: str = Field(..., min_length=1, max_length=200, description="Short description")
    description: str = Field(..., min_length=1, description="Detailed explanation")
    file: str = Field(..., description="Path to file")
    line: Optional[int] = Field(None, ge=1, description="Line number")
    code_snippet: Optional[str] = Field(None, description="Relevant code")
    recommendation: str = Field(..., min_length=1, description="How to fix")
    confidence: float = Field(..., ge=0.0, le=1.0, description="Confidence score 0.0-1.0")
    references: Optional[List[str]] = Field(None, description="Links to docs, patterns, skills")

    @field_validator('confidence')
    @classmethod
    def confidence_must_be_valid(cls, v):
        if not 0.0 <= v <= 1.0:
            raise ValueError('Confidence must be between 0.0 and 1.0')
        return v

class SecurityIssue(Issue):
    """Security-specific issue with exploitation details."""

    category: Literal['security'] = 'security'
    vulnerability_type: str = Field(..., description="sql_injection, xss, csrf, broken_access_control, etc.")
    cvss_score: Optional[float] = Field(None, ge=0.0, le=10.0, description="CVSS score 0.0-10.0")
    attack_complexity: Literal['low', 'medium', 'high']
    requires_auth: bool
    exploitation_example: Optional[str] = Field(None, description="curl command or attack vector")
    mitigations_present: List[str] = Field(default_factory=list, description="Existing security controls")
    mitigations_missing: List[str] = Field(default_factory=list, description="Missing security controls")

class PerformanceIssue(Issue):
    """Performance-specific issue with scale impact."""

    category: Literal['performance'] = 'performance'
    issue_type: str = Field(..., description="n_plus_one, missing_cache, inefficient_query, etc.")
    current_impact: str = Field(..., description="Impact at current scale")
    scale_10x: str = Field(..., description="Impact at 10x scale")
    scale_100x: str = Field(..., description="Impact at 100x scale")
    optimization_potential: str = Field(..., description="Improvement possible")
    caching_applicable: bool

class ArchitectureIssue(Issue):
    """Architecture-specific issue with pattern recommendations."""

    category: Literal['architecture'] = 'architecture'
    issue_type: str = Field(..., description="solid_violation, tight_coupling, god_object, etc.")
    solid_principles_violated: Optional[List[Literal['SRP', 'OCP', 'LSP', 'ISP', 'DIP']]] = None
    pattern_opportunity: Optional[str] = Field(None, description="Strategy pattern recommended")
    pattern_reference: Optional[str] = Field(None, description="patterns/behavioral/strategy.md")
    refactoring_effort: Literal['low', 'medium', 'high']
    testability_impact: str = Field(..., description="Before → After testability")

class TestIssue(Issue):
    """Test quality-specific issue."""

    category: Literal['test_quality'] = 'test_quality'
    issue_type: str = Field(..., description="false_confidence, flaky, brittle, slow, etc.")
    test_principle_violated: Optional[List[str]] = Field(None, description="behavior_based, independent, deterministic, etc.")
    root_cause: Literal['test_problem', 'implementation_problem', 'both']
    fix_complexity: Literal['trivial', 'moderate', 'complex']

class PatternIssue(Issue):
    """Pattern consistency issue."""

    category: Literal['pattern_consistency'] = 'pattern_consistency'
    issue_type: str = Field(..., description="duplication, inconsistency, naming_deviation, etc.")
    existing_pattern: Optional[str] = Field(None, description="Reference to existing implementation")
    git_history_reference: Optional[str] = Field(None, description="Commit hash")
    consistency_score: Optional[float] = Field(None, ge=0.0, le=1.0, description="Consistency ratio")
    consolidation_benefit: Optional[str] = Field(None, description="-89 lines of duplicate code")

# ============================================================================
# Review Output Models
# ============================================================================

class ReviewSummary(BaseModel):
    """Summary statistics for review."""

    total_issues: int = Field(..., ge=0)
    by_severity: Dict[str, int] = Field(
        default_factory=lambda: {
            'critical': 0,
            'high': 0,
            'medium': 0,
            'low': 0,
            'info': 0
        }
    )

class ReviewMeta(BaseModel):
    """Metadata about the review process."""

    files_reviewed: int = Field(..., ge=0)
    review_duration_ms: Optional[int] = Field(None, ge=0)
    confidence_score: float = Field(..., ge=0.0, le=1.0, description="Overall review confidence")
    tool_results_used: Optional[List[str]] = Field(None, description="e.g., ['test-results', 'semgrep']")

class ReviewOutput(BaseModel):
    """Complete review output from a single agent."""

    # Metadata
    pr_id: str
    reviewer: str # architecture | security | performance | tests | patterns
    timestamp: str = Field(default_factory=lambda: datetime.utcnow().isoformat())
    version: str = Field(default="1.0.0", description="Schema version")

    # Summary
    verdict: Verdict
    summary: ReviewSummary

    # Issues (can be base Issue or specialized types)
    issues: List[Issue] = Field(default_factory=list)

    # Recommendations
    recommendations: Optional[Dict[str, List[str]]] = Field(
        None,
        description="immediate, important, suggestions"
    )

    # Positive observations
    positive_observations: Optional[List[str]] = None

    # Metadata
    meta: ReviewMeta

    @model_validator(mode='after')
    def validate_issue_counts(self):
        """Ensure summary counts match actual issues."""
        expected_total = len(self.issues)
        if self.summary.total_issues != expected_total:
            raise ValueError(f"Summary total_issues ({self.summary.total_issues}) doesn't match actual issues ({expected_total})")
        return self

class AggregatedReview(BaseModel):
    """Aggregated review from multiple agents."""

    pr_id: str
    timestamp: str = Field(default_factory=lambda: datetime.utcnow().isoformat())
    version: str = Field(default="1.0.0")

    # Overall verdict (most restrictive wins)
    overall_verdict: Verdict

    # Aggregated summary
    summary: Dict[str, any]

    # All issues
    all_issues: List[Issue]

    # Individual reviews
    reviewers: Dict[str, ReviewOutput]

    # Meta
    meta: Dict[str, any]

# ============================================================================
# Schema Version
# ============================================================================

SCHEMA_VERSION = "1.0.0"

__all__ = [
    'Severity',
    'Verdict',
    'Issue',
    'SecurityIssue',
    'PerformanceIssue',
    'ArchitectureIssue',
    'TestIssue',
    'PatternIssue',
    'ReviewOutput',
    'ReviewSummary',
    'ReviewMeta',
    'AggregatedReview',
    'SCHEMA_VERSION'
]
