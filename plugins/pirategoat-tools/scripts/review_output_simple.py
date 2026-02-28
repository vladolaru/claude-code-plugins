"""
Simple Review Output Builder (No Dependencies)

Lightweight version without Pydantic for immediate use.
Provides structure and basic validation using plain Python.

Usage:
    from review_output_simple import ReviewOutputBuilder

    builder = ReviewOutputBuilder(pr_id="123", reviewer="security")
    builder.add_issue(
        severity="critical",
        title="SQL Injection",
        file="src/User.php",
        line=42,
        description="...",
        recommendation="..."
    )
    json_output = builder.to_json()
    markdown_output = builder.to_markdown()
"""

import json
import uuid
from datetime import datetime
from typing import List, Optional, Dict, Any

class ReviewOutputBuilder:
    """Simple builder for structured review outputs."""

    def __init__(self, pr_id: str, reviewer: str):
        self.pr_id = pr_id
        self.reviewer = reviewer
        self.timestamp = datetime.now().isoformat()
        self.issues = []
        self.recommendations = {'immediate': [], 'important': [], 'suggestions': []}
        self.positive_observations = []
        self.files_reviewed = 0
        self.review_start = datetime.now()
        self.tool_results_used = []
        self.overall_confidence = 0.95

    def add_issue(
        self,
        severity: str,
        title: str,
        file: str,
        description: str,
        recommendation: str,
        category: str = "general",
        line: Optional[int] = None,
        confidence: float = 0.95,
        **extra_fields
    ) -> str:
        """Add an issue. Returns issue ID."""
        # Validate severity
        valid_severities = ['critical', 'high', 'medium', 'low', 'info']
        if severity.lower() not in valid_severities:
            raise ValueError(f"Invalid severity: {severity}. Must be one of {valid_severities}")

        # Validate confidence
        if not 0.0 <= confidence <= 1.0:
            raise ValueError(f"Confidence must be 0.0-1.0, got {confidence}")

        issue_id = str(uuid.uuid4())[:8]

        issue = {
            'id': issue_id,
            'category': category,
            'severity': severity.lower(),
            'title': title,
            'description': description,
            'file': file,
            'line': line,
            'recommendation': recommendation,
            'confidence': confidence,
            **extra_fields
        }

        self.issues.append(issue)
        return issue_id

    def add_recommendation(self, priority: str, text: str):
        """Add recommendation (priority: immediate, important, suggestions)."""
        if priority in self.recommendations:
            self.recommendations[priority].append(text)

    def add_positive(self, observation: str):
        """Add positive observation."""
        self.positive_observations.append(observation)

    def set_files_reviewed(self, count: int):
        """Set number of files reviewed."""
        self.files_reviewed = count

    def set_confidence(self, score: float):
        """Set overall confidence score."""
        if not 0.0 <= score <= 1.0:
            raise ValueError(f"Confidence must be 0.0-1.0, got {score}")
        self.overall_confidence = score

    def add_tool_result(self, tool_name: str):
        """Record tool result used."""
        if tool_name not in self.tool_results_used:
            self.tool_results_used.append(tool_name)

    def _calculate_verdict(self) -> str:
        """Auto-calculate verdict from issues."""
        counts = {'critical': 0, 'high': 0, 'medium': 0}

        for issue in self.issues:
            sev = issue['severity']
            if sev in counts:
                counts[sev] += 1

        if counts['critical'] > 0:
            return 'block'
        if counts['high'] >= 3:
            return 'block'
        if counts['high'] > 0 or counts['medium'] >= 5:
            return 'request_changes'
        if counts['medium'] > 0:
            return 'comment'

        return 'approve'

    def to_dict(self) -> Dict:
        """Build as dictionary."""
        review_duration = int((datetime.now() - self.review_start).total_seconds() * 1000)

        severity_counts = {'critical': 0, 'high': 0, 'medium': 0, 'low': 0, 'info': 0}
        for issue in self.issues:
            severity_counts[issue['severity']] += 1

        return {
            'pr_id': self.pr_id,
            'reviewer': self.reviewer,
            'timestamp': self.timestamp,
            'version': '1.0.0',
            'verdict': self._calculate_verdict(),
            'summary': {
                'total_issues': len(self.issues),
                'by_severity': severity_counts
            },
            'issues': self.issues,
            'recommendations': self.recommendations if any(self.recommendations.values()) else None,
            'positive_observations': self.positive_observations if self.positive_observations else None,
            'meta': {
                'files_reviewed': self.files_reviewed,
                'review_duration_ms': review_duration,
                'confidence_score': self.overall_confidence,
                'tool_results_used': self.tool_results_used if self.tool_results_used else None
            }
        }

    def to_json(self, indent: int = 2) -> str:
        """Generate JSON string."""
        return json.dumps(self.to_dict(), indent=indent, ensure_ascii=False)

    def to_markdown(self) -> str:
        """Generate human-readable markdown."""
        data = self.to_dict()
        md = []

        md.append(f"# {self.reviewer.title()} Review - PR #{self.pr_id}\n\n")
        md.append("## Executive Summary\n\n")
        md.append(f"**Verdict:** {data['verdict'].upper()}\n")
        md.append(f"**Total Issues:** {data['summary']['total_issues']}\n\n")

        if data['summary']['total_issues'] > 0:
            counts = data['summary']['by_severity']
            md.append(f"- Critical: {counts['critical']}\n")
            md.append(f"- High: {counts['high']}\n")
            md.append(f"- Medium: {counts['medium']}\n\n")

        # Issues
        for sev in ['critical', 'high', 'medium', 'low']:
            sev_issues = [i for i in data['issues'] if i['severity'] == sev]

            if sev_issues:
                md.append(f"## {sev.title()} Issues\n\n")

                for issue in sev_issues:
                    md.append(f"### {issue['title']}\n\n")
                    md.append(f"**File:** `{issue['file']}`" + (f" line {issue['line']}" if issue['line'] else "") + "\n\n")
                    md.append(f"{issue['description']}\n\n")
                    md.append(f"**Fix:** {issue['recommendation']}\n\n")

        # Positive
        if data['positive_observations']:
            md.append("## Positive Observations\n\n")
            for obs in data['positive_observations']:
                md.append(f"- {obs}\n")

        return ''.join(md)

    def save(self, output_dir: str):
        """Save both JSON and markdown."""
        import os
        os.makedirs(output_dir, exist_ok=True)

        json_path = os.path.join(output_dir, f"{self.reviewer}-review.json")
        md_path = os.path.join(output_dir, f"{self.reviewer}-review.md")

        with open(json_path, 'w') as f:
            f.write(self.to_json())

        with open(md_path, 'w') as f:
            f.write(self.to_markdown())

        return {'json': json_path, 'markdown': md_path}

# Test
if __name__ == '__main__':
    builder = ReviewOutputBuilder(pr_id="123", reviewer="security")

    builder.add_issue(
        severity="critical",
        title="SQL Injection",
        file="src/User.php",
        line=42,
        description="Direct $_GET input in query",
        recommendation="Use $wpdb->prepare()",
        category="security",
        vulnerability_type="sql_injection"
    )

    builder.set_files_reviewed(1)

    print("=== JSON ===")
    print(builder.to_json())

    print("\n=== MARKDOWN ===")
    print(builder.to_markdown())
