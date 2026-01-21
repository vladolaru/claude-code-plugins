"""
Review Output Builder - Helper for Agents to Create Structured Output

Simplifies the process of creating valid ReviewOutput JSON while maintaining
the human-readable markdown format.

Usage:
    from review_output_builder import ReviewOutputBuilder

    builder = ReviewOutputBuilder(
        pr_id="123",
        reviewer="security"
    )

    builder.add_issue(
        category="security",
        severity="critical",
        title="SQL Injection",
        description="Direct user input in query",
        file="src/User.php",
        line=42,
        recommendation="Use $wpdb->prepare()"
    )

    # Generate outputs
    json_output = builder.to_json()
    markdown_output = builder.to_markdown()

    # Or both
    builder.save_dual_output("/tmp/review-123")
"""

import json
import uuid
from datetime import datetime
from typing import List, Optional, Dict, Any
from review_schemas import (
    ReviewOutput,
    Issue,
    SecurityIssue,
    PerformanceIssue,
    ArchitectureIssue,
    TestIssue,
    PatternIssue,
    ReviewSummary,
    ReviewMeta,
    Verdict,
    Severity,
    SCHEMA_VERSION
)

class ReviewOutputBuilder:
    """Builder for creating structured review outputs with validation."""

    def __init__(self, pr_id: str, reviewer: str):
        """
        Initialize builder.

        Args:
            pr_id: PR number or identifier
            reviewer: Agent name (architecture, security, performance, tests, patterns)
        """
        self.pr_id = pr_id
        self.reviewer = reviewer
        self.timestamp = datetime.utcnow().isoformat()
        self.issues: List[Issue] = []
        self.recommendations = {
            'immediate': [],
            'important': [],
            'suggestions': []
        }
        self.positive_observations = []
        self.files_reviewed = 0
        self.review_start = datetime.utcnow()
        self.tool_results_used = []
        self.overall_confidence = 1.0

    def add_issue(
        self,
        category: str,
        severity: str,
        title: str,
        description: str,
        file: str,
        recommendation: str,
        line: Optional[int] = None,
        confidence: float = 0.95,
        **kwargs
    ) -> str:
        """
        Add an issue to the review.

        Args:
            category: Issue category
            severity: critical, high, medium, low, info
            title: Short description
            description: Detailed explanation
            file: File path
            recommendation: How to fix
            line: Line number (optional)
            confidence: 0.0-1.0
            **kwargs: Category-specific fields

        Returns:
            issue_id: Unique ID for this issue
        """
        issue_id = str(uuid.uuid4())[:8]

        # Create appropriate issue type based on category
        issue_data = {
            'id': issue_id,
            'category': category,
            'severity': severity,
            'title': title,
            'description': description,
            'file': file,
            'line': line,
            'recommendation': recommendation,
            'confidence': confidence,
            **kwargs
        }

        # Validate and create typed issue
        if category == 'security':
            issue = SecurityIssue(**issue_data)
        elif category == 'performance':
            issue = PerformanceIssue(**issue_data)
        elif category == 'architecture':
            issue = ArchitectureIssue(**issue_data)
        elif category == 'test_quality':
            issue = TestIssue(**issue_data)
        elif category == 'pattern_consistency':
            issue = PatternIssue(**issue_data)
        else:
            issue = Issue(**issue_data)

        self.issues.append(issue)
        return issue_id

    def add_recommendation(self, priority: str, recommendation: str):
        """
        Add a recommendation.

        Args:
            priority: immediate, important, suggestions
            recommendation: The recommendation text
        """
        if priority in self.recommendations:
            self.recommendations[priority].append(recommendation)

    def add_positive_observation(self, observation: str):
        """Add a positive observation about the code."""
        self.positive_observations.append(observation)

    def set_files_reviewed(self, count: int):
        """Set number of files reviewed."""
        self.files_reviewed = count

    def add_tool_result(self, tool_name: str):
        """Record that a tool result was used."""
        if tool_name not in self.tool_results_used:
            self.tool_results_used.append(tool_name)

    def set_overall_confidence(self, confidence: float):
        """Set overall review confidence (0.0-1.0)."""
        if not 0.0 <= confidence <= 1.0:
            raise ValueError("Confidence must be between 0.0 and 1.0")
        self.overall_confidence = confidence

    def _calculate_verdict(self) -> Verdict:
        """Calculate verdict based on issues."""
        severity_counts = self._count_by_severity()

        if severity_counts['critical'] > 0:
            return Verdict.BLOCK

        if severity_counts['high'] >= 3:
            return Verdict.BLOCK

        if severity_counts['high'] > 0 or severity_counts['medium'] >= 5:
            return Verdict.REQUEST_CHANGES

        if severity_counts['medium'] > 0:
            return Verdict.COMMENT

        return Verdict.APPROVE

    def _count_by_severity(self) -> Dict[str, int]:
        """Count issues by severity."""
        counts = {
            'critical': 0,
            'high': 0,
            'medium': 0,
            'low': 0,
            'info': 0
        }

        for issue in self.issues:
            counts[issue.severity.value] += 1

        return counts

    def build(self) -> ReviewOutput:
        """
        Build the final ReviewOutput model.

        Returns:
            Validated ReviewOutput ready for serialization
        """
        review_duration = int((datetime.utcnow() - self.review_start).total_seconds() * 1000)

        review = ReviewOutput(
            pr_id=self.pr_id,
            reviewer=self.reviewer,
            timestamp=self.timestamp,
            version=SCHEMA_VERSION,
            verdict=self._calculate_verdict(),
            summary=ReviewSummary(
                total_issues=len(self.issues),
                by_severity=self._count_by_severity()
            ),
            issues=self.issues,
            recommendations=self.recommendations if any(self.recommendations.values()) else None,
            positive_observations=self.positive_observations if self.positive_observations else None,
            meta=ReviewMeta(
                files_reviewed=self.files_reviewed,
                review_duration_ms=review_duration,
                confidence_score=self.overall_confidence,
                tool_results_used=self.tool_results_used if self.tool_results_used else None
            )
        )

        return review

    def to_json(self, indent: int = 2) -> str:
        """
        Generate JSON output.

        Returns:
            Formatted JSON string
        """
        review = self.build()
        return review.model_dump_json(indent=indent)

    def to_markdown(self) -> str:
        """
        Generate markdown output (human-readable).

        Returns:
            Formatted markdown string
        """
        review = self.build()

        # Build markdown
        md = []

        md.append(f"# {self.reviewer.title()} Review - PR #{self.pr_id}\n")

        md.append("## Executive Summary\n")
        md.append(f"**Verdict:** {review.verdict.value.upper()}\n")
        md.append(f"**Issues found:** {review.summary.total_issues}\n")

        if review.summary.total_issues > 0:
            md.append(f"- Critical: {review.summary.by_severity['critical']}\n")
            md.append(f"- High: {review.summary.by_severity['high']}\n")
            md.append(f"- Medium: {review.summary.by_severity['medium']}\n")

        md.append(f"\n**Confidence:** {int(review.meta.confidence_score * 100)}%\n")

        # Issues by severity
        for sev in ['critical', 'high', 'medium', 'low']:
            issues_at_level = [i for i in review.issues if i.severity == sev]

            if issues_at_level:
                md.append(f"\n## {sev.title()} Issues\n")

                for issue in issues_at_level:
                    md.append(f"\n### {issue.title}\n")
                    md.append(f"**Location:** `{issue.file}`" + (f":{issue.line}" if issue.line else "") + "\n")
                    md.append(f"**Confidence:** {int(issue.confidence * 100)}%\n\n")
                    md.append(f"{issue.description}\n\n")
                    md.append(f"**Recommendation:** {issue.recommendation}\n")

        # Positive observations
        if review.positive_observations:
            md.append("\n## Positive Observations\n")
            for obs in review.positive_observations:
                md.append(f"- {obs}\n")

        # Recommendations
        if review.recommendations:
            md.append("\n## Recommendations\n")

            if review.recommendations.get('immediate'):
                md.append("\n### Immediate (Must Fix)\n")
                for rec in review.recommendations['immediate']:
                    md.append(f"- {rec}\n")

            if review.recommendations.get('important'):
                md.append("\n### Important (Should Fix)\n")
                for rec in review.recommendations['important']:
                    md.append(f"- {rec}\n")

        return ''.join(md)

    def save_dual_output(self, output_dir: str, filename_base: str = None):
        """
        Save both JSON and Markdown outputs.

        Args:
            output_dir: Directory to save files
            filename_base: Base filename (default: reviewer name)
        """
        import os

        if filename_base is None:
            filename_base = self.reviewer

        os.makedirs(output_dir, exist_ok=True)

        # Save JSON
        json_path = os.path.join(output_dir, f"{filename_base}.json")
        with open(json_path, 'w') as f:
            f.write(self.to_json())

        # Save Markdown
        md_path = os.path.join(output_dir, f"{filename_base}.md")
        with open(md_path, 'w') as f:
            f.write(self.to_markdown())

        return {
            'json': json_path,
            'markdown': md_path
        }

# ============================================================================
# Helper Functions
# ============================================================================

def validate_review_json(json_str: str) -> bool:
    """
    Validate that JSON string matches ReviewOutput schema.

    Args:
        json_str: JSON string to validate

    Returns:
        True if valid, raises exception if invalid
    """
    data = json.loads(json_str)
    review = ReviewOutput(**data)
    return True

# ============================================================================
# Example Usage
# ============================================================================

if __name__ == '__main__':
    # Example: Build a security review
    builder = ReviewOutputBuilder(pr_id="123", reviewer="security")

    builder.add_issue(
        category="security",
        severity="critical",
        title="SQL Injection",
        description="Direct user input in SQL query without sanitization",
        file="src/UserController.php",
        line=42,
        recommendation="Use $wpdb->prepare() with placeholders",
        confidence=0.99,
        # Security-specific fields
        vulnerability_type="sql_injection",
        cvss_score=9.8,
        attack_complexity="low",
        requires_auth=False,
        exploitation_example="curl '...?id=1 OR 1=1'",
        mitigations_present=[],
        mitigations_missing=["nonce", "capability_check", "prepared_statement", "sanitization"]
    )

    builder.add_recommendation("immediate", "Fix SQL injection on line 42")
    builder.set_files_reviewed(1)

    # Generate outputs
    print("=== JSON OUTPUT ===")
    print(builder.to_json())

    print("\n=== MARKDOWN OUTPUT ===")
    print(builder.to_markdown())

    # Save both
    paths = builder.save_dual_output("/tmp/example-review")
    print(f"\n=== SAVED TO ===")
    print(f"JSON: {paths['json']}")
    print(f"Markdown: {paths['markdown']}")
