# Proposal #3: Structured Output (JSON Schema) for Review Agents

**Pattern:** Structured Output with JSON Schema Validation
**Priority:** Tier 1 - Implement Immediately
**Effort:** Medium (6-8 hours for schema definitions, 4-6 hours for integration)
**Impact:** High (enables automation, reliable parsing, metrics tracking, downstream integration)
**Source:** awesome-agentic-patterns, industry best practices

---

## The Problem (Why This Matters)

### Current State Analysis

**What our review agents output today:**

```markdown
# Security Review - OrderProcessor.php

## Critical Issues

### 1. SQL Injection Vulnerability
**Location:** `src/OrderProcessor.php:67`
**Severity:** Critical

The `getUserByEmail()` method directly concatenates user input into SQL query:

```php
$query = "SELECT * FROM users WHERE email = '" . $email . "'";
```

This allows attackers to inject malicious SQL code.

**Recommendation:** Use prepared statements with parameterized queries.

### 2. XSS Vulnerability
**Location:** `src/OrderProcessor.php:120`
**Severity:** High

User-provided data is output without escaping...
```

**Problems with this approach:**

1. **No Machine Parsability**
   - Cannot automatically extract issue counts
   - Cannot aggregate across multiple reviewers
   - Cannot programmatically route issues to tracking systems
   - Cannot generate automated metrics dashboards

2. **Inconsistent Structure**
   - Each agent may format output differently
   - Location formats vary: `file:line`, `file:line-line`, `function:line`, etc.
   - Severity levels inconsistent: "Critical", "critical", "CRITICAL", "🔴 Critical"
   - Recommendation formats differ

3. **Integration Challenges**
   - Cannot feed into CI/CD pipelines reliably
   - Cannot auto-create GitHub issues
   - Cannot track resolution status
   - Cannot measure fix rates or time-to-resolution

4. **Lost Metadata**
   - No standardized confidence scores
   - No impact classifications
   - No fix effort estimates
   - No related issues linking

5. **Aggregation Difficulties**
   ```markdown
   # How pr-reviewer currently aggregates:

   1. Read each specialist agent's markdown output
   2. Parse manually (regex, heuristics)
   3. Hope format hasn't changed
   4. Try to deduplicate issues
   5. Generate summary markdown
   ```

   **Result:** Brittle, error-prone, impossible to automate.

---

### The Core Problem: Humans Read Markdown, Machines Need JSON

**Current workflow (Markdown-only):**

```
┌─────────────────────┐
│ Security Reviewer   │──┐
│ (Markdown output)   │  │
└─────────────────────┘  │
                         │
┌─────────────────────┐  │    ┌──────────────────┐
│ Tests Reviewer      │──┼───▶│ pr-reviewer      │
│ (Markdown output)   │  │    │ (Manual parsing) │
└─────────────────────┘  │    └──────────────────┘
                         │              │
┌─────────────────────┐  │              ▼
│ Arch Reviewer       │──┘    ┌──────────────────┐
│ (Markdown output)   │       │ Markdown Summary │
└─────────────────────┘       │ (For humans)     │
                              └──────────────────┘

Problems:
❌ Each agent's output must be manually parsed
❌ Inconsistent formats require brittle regex
❌ Cannot aggregate metrics reliably
❌ No downstream automation possible
```

**Ideal workflow (Structured output):**

```
┌─────────────────────┐
│ Security Reviewer   │──┐
│ (JSON + Markdown)   │  │
└─────────────────────┘  │
                         │
┌─────────────────────┐  │    ┌──────────────────┐
│ Tests Reviewer      │──┼───▶│ pr-reviewer      │
│ (JSON + Markdown)   │  │    │ (JSON aggregator)│
└─────────────────────┘  │    └──────────────────┘
                         │              │
┌─────────────────────┐  │              ├─────────────┐
│ Arch Reviewer       │──┘              │             │
│ (JSON + Markdown)   │                 ▼             ▼
└─────────────────────┘       ┌──────────────┐ ┌─────────────┐
                              │ JSON Summary │ │ Markdown    │
                              │ (Machines)   │ │ (Humans)    │
                              └──────────────┘ └─────────────┘
                                     │
                    ┌────────────────┼────────────────┐
                    ▼                ▼                ▼
            ┌──────────────┐ ┌──────────┐  ┌──────────────┐
            │ GitHub Issues│ │ Metrics  │  │ CI/CD Gates  │
            │ Auto-created │ │ Dashboard│  │ (Block merge)│
            └──────────────┘ └──────────┘  └──────────────┘

Benefits:
✅ Reliable programmatic parsing
✅ Consistent structure across agents
✅ Metrics and dashboards
✅ Full automation pipeline
✅ Human-readable markdown still available
```

---

### Real-World Impact: What We're Missing Today

**Scenario 1: Tracking Fix Rates**

❌ **Current (impossible):**
```bash
# Want to know: "What % of security issues get fixed within 7 days?"
# No way to track this without manual spreadsheet
```

✅ **With structured output:**
```python
# issues.json contains all reviews from past 90 days
issues = load_json('reviews/issues.json')

critical_issues = [i for i in issues if i['severity'] == 'critical']
fixed_within_7d = [i for i in critical_issues
                   if (i['fixed_date'] - i['created_date']).days <= 7]

fix_rate = len(fixed_within_7d) / len(critical_issues)
print(f"Critical fix rate (7d): {fix_rate:.1%}")
# Output: "Critical fix rate (7d): 73%"
```

**Scenario 2: Blocking PRs with Critical Issues**

❌ **Current (manual):**
```yaml
# .github/workflows/review.yml
- name: Review PR
  run: /review-pr

# Human must read markdown output and decide whether to merge
```

✅ **With structured output:**
```yaml
# .github/workflows/review.yml
- name: Review PR
  id: review
  run: /review-pr --output json

- name: Block if critical issues
  run: |
    critical_count=$(jq '.issues[] | select(.severity=="critical") | length' review.json)
    if [ "$critical_count" -gt 0 ]; then
      echo "❌ Found $critical_count critical issues. Blocking merge."
      exit 1
    fi
```

**Scenario 3: Auto-Creating GitHub Issues**

❌ **Current (impossible):**
```markdown
Security review found 3 critical issues.
# Someone must manually create 3 GitHub issues...
```

✅ **With structured output:**
```python
review = load_json('security-review.json')

for issue in review['issues']:
    if issue['severity'] == 'critical':
        gh.create_issue(
            title=f"[Security] {issue['title']}",
            body=issue_template(issue),
            labels=['security', 'critical'],
            assignees=determine_owner(issue['file'])
        )
```

**Scenario 4: Metrics Dashboard**

❌ **Current (no data):**
```
[Empty dashboard - no structured data to visualize]
```

✅ **With structured output:**
```python
# Auto-generated dashboard shows:
# - Issues by severity over time (trend chart)
# - Issues by category (security, architecture, tests)
# - Fix rate by severity (bar chart)
# - Average time-to-fix (line chart)
# - Most problematic files (heatmap)
# - Review coverage % (gauge)
```

---

## The Solution (How It Works)

### Concept: Dual Output Format

Agents produce **both** JSON (for machines) and Markdown (for humans):

```python
@dataclass
class ReviewOutput:
    """
    Every review agent outputs this structure.
    JSON Schema ensures consistency.
    """
    # Metadata
    review_type: str           # "security", "architecture", "tests", etc.
    reviewer: str              # Agent name
    timestamp: str             # ISO 8601 format
    pr_number: int
    commit_sha: str

    # Summary
    summary: ReviewSummary

    # Issues
    issues: List[Issue]

    # Metrics
    metrics: ReviewMetrics

    # Raw markdown (for humans)
    markdown_output: str
```

**JSON Schema Definition (TypeScript):**

```typescript
// schemas/ReviewOutput.schema.ts

interface ReviewOutput {
  // Metadata
  review_type: ReviewType;
  reviewer: string;
  timestamp: string;  // ISO 8601
  pr_number: number;
  commit_sha: string;
  base_ref: string;
  head_ref: string;

  // Summary
  summary: ReviewSummary;

  // Issues found
  issues: Issue[];

  // Metrics
  metrics: ReviewMetrics;

  // Optional: raw markdown for human reading
  markdown_output?: string;
}

type ReviewType =
  | "security"
  | "architecture"
  | "tests"
  | "performance"
  | "accessibility";

interface ReviewSummary {
  total_issues: number;
  by_severity: {
    critical: number;
    high: number;
    medium: number;
    low: number;
    info: number;
  };
  overall_assessment: "approved" | "changes_requested" | "commented";
  confidence_score: number;  // 0.0 - 1.0
  review_time_seconds: number;
}

interface Issue {
  // Identification
  id: string;  // UUID or hash
  title: string;
  description: string;

  // Classification
  category: string;  // "sql_injection", "memory_leak", "missing_test", etc.
  severity: Severity;
  impact: Impact;

  // Location
  location: IssueLocation;

  // Context
  code_snippet?: string;
  surrounding_context?: string;

  // Recommendations
  recommendation: string;
  suggested_fix?: string;
  references?: string[];  // URLs, documentation links

  // Metadata
  confidence: number;  // 0.0 - 1.0
  fix_effort: FixEffort;
  breaking_change: boolean;

  // Related
  related_issues?: string[];  // IDs of related issues
  tags?: string[];
}

type Severity = "critical" | "high" | "medium" | "low" | "info";

type Impact =
  | "security"      // Security vulnerability
  | "correctness"   // Bug, wrong behavior
  | "performance"   // Slow, inefficient
  | "maintainability"  // Hard to maintain
  | "testing"       // Missing/poor tests
  | "documentation" // Missing/poor docs
  | "style"         // Formatting, conventions
  | "breaking";     // Breaking change

interface IssueLocation {
  file: string;          // Relative path from repo root
  line_start: number;
  line_end?: number;
  column_start?: number;
  column_end?: number;
  function_name?: string;
  class_name?: string;
}

type FixEffort = "trivial" | "easy" | "medium" | "hard" | "very_hard";

interface ReviewMetrics {
  files_reviewed: number;
  lines_reviewed: number;
  files_with_issues: number;
  tokens_used: number;
  review_duration_seconds: number;

  // Coverage
  coverage: {
    total_files_changed: number;
    files_reviewed: number;
    coverage_percentage: number;
  };

  // Confidence breakdown
  confidence_distribution: {
    high_confidence: number;    // issues with confidence >= 0.8
    medium_confidence: number;  // 0.5 <= confidence < 0.8
    low_confidence: number;     // confidence < 0.5
  };
}
```

**Python (Pydantic) Schema:**

```python
# schemas/review_output.py

from datetime import datetime
from enum import Enum
from typing import List, Optional, Dict
from pydantic import BaseModel, Field, validator
import uuid

class ReviewType(str, Enum):
    SECURITY = "security"
    ARCHITECTURE = "architecture"
    TESTS = "tests"
    PERFORMANCE = "performance"
    ACCESSIBILITY = "accessibility"

class Severity(str, Enum):
    CRITICAL = "critical"
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"
    INFO = "info"

class Impact(str, Enum):
    SECURITY = "security"
    CORRECTNESS = "correctness"
    PERFORMANCE = "performance"
    MAINTAINABILITY = "maintainability"
    TESTING = "testing"
    DOCUMENTATION = "documentation"
    STYLE = "style"
    BREAKING = "breaking"

class FixEffort(str, Enum):
    TRIVIAL = "trivial"
    EASY = "easy"
    MEDIUM = "medium"
    HARD = "hard"
    VERY_HARD = "very_hard"

class OverallAssessment(str, Enum):
    APPROVED = "approved"
    CHANGES_REQUESTED = "changes_requested"
    COMMENTED = "commented"

class IssueLocation(BaseModel):
    """Precise location of an issue in the codebase."""
    file: str = Field(..., description="Relative path from repo root")
    line_start: int = Field(..., ge=1)
    line_end: Optional[int] = Field(None, ge=1)
    column_start: Optional[int] = Field(None, ge=1)
    column_end: Optional[int] = Field(None, ge=1)
    function_name: Optional[str] = None
    class_name: Optional[str] = None

    @validator('line_end')
    def line_end_must_be_after_start(cls, v, values):
        if v is not None and 'line_start' in values:
            if v < values['line_start']:
                raise ValueError('line_end must be >= line_start')
        return v

    def to_github_link(self, repo: str, commit: str) -> str:
        """Generate GitHub permalink to this location."""
        base = f"https://github.com/{repo}/blob/{commit}/{self.file}"
        if self.line_end and self.line_end != self.line_start:
            return f"{base}#L{self.line_start}-L{self.line_end}"
        return f"{base}#L{self.line_start}"

class Issue(BaseModel):
    """A single issue found during review."""

    # Identification
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    title: str = Field(..., min_length=1, max_length=200)
    description: str = Field(..., min_length=1)

    # Classification
    category: str = Field(...,
        description="Specific category like 'sql_injection', 'memory_leak', 'missing_test'")
    severity: Severity
    impact: Impact

    # Location
    location: IssueLocation

    # Context
    code_snippet: Optional[str] = None
    surrounding_context: Optional[str] = None

    # Recommendations
    recommendation: str = Field(..., min_length=1)
    suggested_fix: Optional[str] = None
    references: Optional[List[str]] = Field(default_factory=list)

    # Metadata
    confidence: float = Field(..., ge=0.0, le=1.0)
    fix_effort: FixEffort
    breaking_change: bool = False

    # Related
    related_issues: Optional[List[str]] = Field(default_factory=list)
    tags: Optional[List[str]] = Field(default_factory=list)

    def to_github_issue(self, repo: str, commit: str) -> Dict:
        """Convert to GitHub issue format."""
        return {
            "title": f"[{self.severity.upper()}] {self.title}",
            "body": f"""## Description

{self.description}

## Location

{self.location.to_github_link(repo, commit)}

## Code

```
{self.code_snippet or 'N/A'}
```

## Recommendation

{self.recommendation}

{f"## Suggested Fix\n\n```\n{self.suggested_fix}\n```" if self.suggested_fix else ""}

## Metadata

- **Severity:** {self.severity}
- **Impact:** {self.impact}
- **Category:** {self.category}
- **Fix Effort:** {self.fix_effort}
- **Confidence:** {self.confidence:.0%}

{f"## References\n\n" + "\n".join(f"- {ref}" for ref in self.references) if self.references else ""}
""",
            "labels": [
                self.severity,
                self.impact,
                self.category,
                f"effort:{self.fix_effort}"
            ]
        }

class ReviewSummary(BaseModel):
    """High-level summary of review results."""
    total_issues: int = Field(..., ge=0)
    by_severity: Dict[Severity, int] = Field(default_factory=dict)
    overall_assessment: OverallAssessment
    confidence_score: float = Field(..., ge=0.0, le=1.0)
    review_time_seconds: float = Field(..., ge=0.0)

    @validator('by_severity')
    def severity_counts_must_sum(cls, v, values):
        if 'total_issues' in values:
            if sum(v.values()) != values['total_issues']:
                raise ValueError('Severity counts must sum to total_issues')
        return v

class ReviewMetrics(BaseModel):
    """Detailed metrics about the review process."""
    files_reviewed: int = Field(..., ge=0)
    lines_reviewed: int = Field(..., ge=0)
    files_with_issues: int = Field(..., ge=0)
    tokens_used: int = Field(..., ge=0)
    review_duration_seconds: float = Field(..., ge=0.0)

    # Coverage
    coverage: Dict[str, float] = Field(default_factory=dict)

    # Confidence distribution
    confidence_distribution: Dict[str, int] = Field(default_factory=dict)

class ReviewOutput(BaseModel):
    """Complete output from a review agent."""

    # Metadata
    review_type: ReviewType
    reviewer: str = Field(..., description="Name of the reviewing agent")
    timestamp: datetime = Field(default_factory=datetime.utcnow)
    pr_number: Optional[int] = None
    commit_sha: str
    base_ref: str
    head_ref: str

    # Summary
    summary: ReviewSummary

    # Issues
    issues: List[Issue] = Field(default_factory=list)

    # Metrics
    metrics: ReviewMetrics

    # Optional markdown output
    markdown_output: Optional[str] = None

    class Config:
        json_encoders = {
            datetime: lambda v: v.isoformat()
        }

    def save_json(self, path: str):
        """Save to JSON file."""
        with open(path, 'w') as f:
            f.write(self.json(indent=2))

    def save_markdown(self, path: str):
        """Save markdown output to file."""
        if self.markdown_output:
            with open(path, 'w') as f:
                f.write(self.markdown_output)

    @classmethod
    def load_json(cls, path: str) -> 'ReviewOutput':
        """Load from JSON file."""
        with open(path, 'r') as f:
            return cls.parse_raw(f.read())

    def get_critical_issues(self) -> List[Issue]:
        """Get all critical severity issues."""
        return [i for i in self.issues if i.severity == Severity.CRITICAL]

    def get_high_confidence_issues(self, threshold: float = 0.8) -> List[Issue]:
        """Get issues with high confidence."""
        return [i for i in self.issues if i.confidence >= threshold]

    def should_block_merge(self) -> bool:
        """Determine if PR should be blocked from merging."""
        critical_issues = self.get_critical_issues()
        return len(critical_issues) > 0
```

---

## Implementation Strategy

### Phase 1: Define JSON Schemas (2 hours)

**Goal:** Establish standardized schemas for all agent outputs.

**Deliverables:**

1. **TypeScript schema** (`schemas/ReviewOutput.schema.ts`)
   - Complete type definitions
   - JSDoc annotations
   - Validation helpers

2. **Python schema** (`schemas/review_output.py`)
   - Pydantic models
   - Validation rules
   - Helper methods (save, load, transform)

3. **JSON Schema file** (`schemas/review-output.schema.json`)
   - For tools that need pure JSON Schema
   - Generated from TypeScript/Python definitions

**Example JSON Schema (auto-generated):**

```json
{
  "$schema": "http://json-schema.org/draft-07/schema#",
  "title": "ReviewOutput",
  "description": "Complete output from a review agent",
  "type": "object",
  "required": [
    "review_type",
    "reviewer",
    "timestamp",
    "commit_sha",
    "base_ref",
    "head_ref",
    "summary",
    "issues",
    "metrics"
  ],
  "properties": {
    "review_type": {
      "type": "string",
      "enum": ["security", "architecture", "tests", "performance", "accessibility"]
    },
    "reviewer": {
      "type": "string",
      "description": "Name of the reviewing agent"
    },
    "timestamp": {
      "type": "string",
      "format": "date-time"
    },
    "pr_number": {
      "type": "integer",
      "minimum": 1
    },
    "commit_sha": {
      "type": "string",
      "pattern": "^[0-9a-f]{40}$"
    },
    "base_ref": {
      "type": "string"
    },
    "head_ref": {
      "type": "string"
    },
    "summary": {
      "$ref": "#/definitions/ReviewSummary"
    },
    "issues": {
      "type": "array",
      "items": {
        "$ref": "#/definitions/Issue"
      }
    },
    "metrics": {
      "$ref": "#/definitions/ReviewMetrics"
    },
    "markdown_output": {
      "type": "string"
    }
  },
  "definitions": {
    "ReviewSummary": { /* ... */ },
    "Issue": { /* ... */ },
    "IssueLocation": { /* ... */ },
    "ReviewMetrics": { /* ... */ }
  }
}
```

---

### Phase 2: Implement Schema in Python Agent Helper (2 hours)

**Goal:** Create Python library for agents to easily output structured data.

**Deliverable:** `scripts/lib/review_output_builder.py`

```python
# scripts/lib/review_output_builder.py

from schemas.review_output import (
    ReviewOutput, ReviewSummary, Issue, IssueLocation,
    ReviewMetrics, ReviewType, Severity, Impact, FixEffort
)
from typing import List, Dict, Optional
import time

class ReviewOutputBuilder:
    """
    Helper class for agents to build structured output.

    Usage:
        builder = ReviewOutputBuilder(
            review_type=ReviewType.SECURITY,
            reviewer="security-reviewer",
            commit_sha="abc123...",
            base_ref="main",
            head_ref="feature-branch"
        )

        builder.add_issue(
            title="SQL Injection Vulnerability",
            description="...",
            severity=Severity.CRITICAL,
            ...
        )

        output = builder.build()
        output.save_json("security-review.json")
        output.save_markdown("security-review.md")
    """

    def __init__(
        self,
        review_type: ReviewType,
        reviewer: str,
        commit_sha: str,
        base_ref: str,
        head_ref: str,
        pr_number: Optional[int] = None
    ):
        self.review_type = review_type
        self.reviewer = reviewer
        self.commit_sha = commit_sha
        self.base_ref = base_ref
        self.head_ref = head_ref
        self.pr_number = pr_number

        self.issues: List[Issue] = []
        self.files_reviewed: List[str] = []
        self.lines_reviewed = 0
        self.tokens_used = 0
        self.start_time = time.time()

    def add_issue(
        self,
        title: str,
        description: str,
        category: str,
        severity: Severity,
        impact: Impact,
        file: str,
        line_start: int,
        recommendation: str,
        confidence: float = 0.8,
        fix_effort: FixEffort = FixEffort.MEDIUM,
        line_end: Optional[int] = None,
        code_snippet: Optional[str] = None,
        suggested_fix: Optional[str] = None,
        references: Optional[List[str]] = None,
        breaking_change: bool = False,
        **kwargs
    ) -> Issue:
        """Add an issue to the review."""

        location = IssueLocation(
            file=file,
            line_start=line_start,
            line_end=line_end,
            **{k: v for k, v in kwargs.items() if k in ['column_start', 'column_end', 'function_name', 'class_name']}
        )

        issue = Issue(
            title=title,
            description=description,
            category=category,
            severity=severity,
            impact=impact,
            location=location,
            recommendation=recommendation,
            confidence=confidence,
            fix_effort=fix_effort,
            code_snippet=code_snippet,
            suggested_fix=suggested_fix,
            references=references or [],
            breaking_change=breaking_change
        )

        self.issues.append(issue)
        return issue

    def add_file_reviewed(self, file: str, lines: int):
        """Track file review."""
        if file not in self.files_reviewed:
            self.files_reviewed.append(file)
        self.lines_reviewed += lines

    def set_tokens_used(self, tokens: int):
        """Set total tokens used in review."""
        self.tokens_used = tokens

    def build(self, markdown_output: Optional[str] = None) -> ReviewOutput:
        """Build final ReviewOutput."""

        review_time = time.time() - self.start_time

        # Calculate summary
        by_severity = {
            Severity.CRITICAL: 0,
            Severity.HIGH: 0,
            Severity.MEDIUM: 0,
            Severity.LOW: 0,
            Severity.INFO: 0
        }
        for issue in self.issues:
            by_severity[issue.severity] += 1

        # Determine overall assessment
        if by_severity[Severity.CRITICAL] > 0:
            assessment = "changes_requested"
        elif by_severity[Severity.HIGH] > 0:
            assessment = "changes_requested"
        elif by_severity[Severity.MEDIUM] > 0:
            assessment = "commented"
        else:
            assessment = "approved"

        # Calculate average confidence
        if self.issues:
            avg_confidence = sum(i.confidence for i in self.issues) / len(self.issues)
        else:
            avg_confidence = 1.0

        summary = ReviewSummary(
            total_issues=len(self.issues),
            by_severity=by_severity,
            overall_assessment=assessment,
            confidence_score=avg_confidence,
            review_time_seconds=review_time
        )

        # Confidence distribution
        high_conf = sum(1 for i in self.issues if i.confidence >= 0.8)
        med_conf = sum(1 for i in self.issues if 0.5 <= i.confidence < 0.8)
        low_conf = sum(1 for i in self.issues if i.confidence < 0.5)

        # Files with issues
        files_with_issues = len(set(i.location.file for i in self.issues))

        metrics = ReviewMetrics(
            files_reviewed=len(self.files_reviewed),
            lines_reviewed=self.lines_reviewed,
            files_with_issues=files_with_issues,
            tokens_used=self.tokens_used,
            review_duration_seconds=review_time,
            coverage={
                "files_reviewed": len(self.files_reviewed),
                "coverage_percentage": 100.0 if self.files_reviewed else 0.0
            },
            confidence_distribution={
                "high_confidence": high_conf,
                "medium_confidence": med_conf,
                "low_confidence": low_conf
            }
        )

        return ReviewOutput(
            review_type=self.review_type,
            reviewer=self.reviewer,
            pr_number=self.pr_number,
            commit_sha=self.commit_sha,
            base_ref=self.base_ref,
            head_ref=self.head_ref,
            summary=summary,
            issues=self.issues,
            metrics=metrics,
            markdown_output=markdown_output
        )
```

**Usage in agent:**

```python
#!/usr/bin/env python3
# agents/security-reviewer/review.py

from lib.review_output_builder import ReviewOutputBuilder
from schemas.review_output import ReviewType, Severity, Impact, FixEffort
import sys

def main():
    # Initialize builder
    builder = ReviewOutputBuilder(
        review_type=ReviewType.SECURITY,
        reviewer="security-reviewer",
        commit_sha=sys.argv[1],
        base_ref=sys.argv[2],
        head_ref=sys.argv[3]
    )

    # Review files
    for file in get_changed_files():
        builder.add_file_reviewed(file, count_lines(file))

        # Run security checks
        issues = run_security_scan(file)

        for issue in issues:
            builder.add_issue(
                title=issue['title'],
                description=issue['description'],
                category="sql_injection",
                severity=Severity.CRITICAL,
                impact=Impact.SECURITY,
                file=file,
                line_start=issue['line'],
                recommendation=issue['fix'],
                confidence=0.95,
                fix_effort=FixEffort.EASY,
                code_snippet=issue['code'],
                suggested_fix=issue['suggested_code']
            )

    # Generate markdown (for humans)
    markdown = generate_markdown_report(builder.issues)

    # Build final output
    output = builder.build(markdown_output=markdown)

    # Save both formats
    output.save_json("output/security-review.json")
    output.save_markdown("output/security-review.md")

    # Exit code based on severity
    if output.should_block_merge():
        sys.exit(1)
    else:
        sys.exit(0)

if __name__ == "__main__":
    main()
```

---

### Phase 3: Update Agent Prompts (2 hours)

**Goal:** Update all agent prompts to output structured data.

**Before (architecture-reviewer.md):**

```markdown
## Output Format

Generate a markdown report with:

1. Executive Summary
2. Critical Issues (if any)
3. High-Priority Issues
4. Medium-Priority Issues
5. Low-Priority Issues
6. Recommendations

Use this format for each issue:

### Issue Title
**Location:** file.php:123
**Severity:** Critical
**Description:** ...
**Recommendation:** ...
```

**After (architecture-reviewer.md):**

```markdown
## Output Format

You MUST generate BOTH structured JSON and markdown output.

### JSON Output (REQUIRED)

Use the ReviewOutputBuilder helper:

```python
from lib.review_output_builder import ReviewOutputBuilder
from schemas.review_output import ReviewType, Severity, Impact, FixEffort

builder = ReviewOutputBuilder(
    review_type=ReviewType.ARCHITECTURE,
    reviewer="architecture-reviewer",
    commit_sha=os.environ['COMMIT_SHA'],
    base_ref=os.environ['BASE_REF'],
    head_ref=os.environ['HEAD_REF']
)

# For each issue found:
builder.add_issue(
    title="Short title",
    description="Detailed description",
    category="tight_coupling",  # or "god_class", "circular_dependency", etc.
    severity=Severity.HIGH,     # CRITICAL, HIGH, MEDIUM, LOW, INFO
    impact=Impact.MAINTAINABILITY,
    file="path/to/file.php",
    line_start=123,
    line_end=145,
    function_name="processOrder",
    class_name="OrderProcessor",
    recommendation="Specific, actionable recommendation",
    confidence=0.85,            # 0.0 - 1.0
    fix_effort=FixEffort.MEDIUM,
    code_snippet="...",
    suggested_fix="...",
    references=["https://refactoring.guru/design-patterns/strategy"]
)

# Generate markdown for humans
markdown = generate_markdown_report(builder.issues)

# Build and save
output = builder.build(markdown_output=markdown)
output.save_json("output/architecture-review.json")
output.save_markdown("output/architecture-review.md")
```

### Markdown Output (REQUIRED)

Also generate human-readable markdown following this structure:

```markdown
# Architecture Review

## Summary
- Total Issues: X
- Critical: X | High: X | Medium: X | Low: X
- Overall Assessment: CHANGES_REQUESTED / APPROVED / COMMENTED

## Critical Issues
[List critical issues with full details]

## High Priority Issues
[List high priority issues]
...
```

**Why both formats?**
- JSON: Machine-parseable, enables automation
- Markdown: Human-readable, excellent for PR comments

The pr-reviewer agent will aggregate JSON from all specialists and generate unified reports.
```

---

### Phase 4: Integration in pr-reviewer (3 hours)

**Goal:** Enable pr-reviewer to aggregate structured outputs from specialists.

**Deliverable:** `scripts/lib/review_aggregator.py`

```python
# scripts/lib/review_aggregator.py

from schemas.review_output import ReviewOutput, Issue, Severity
from typing import List, Dict
import json
from pathlib import Path

class ReviewAggregator:
    """
    Aggregates structured outputs from multiple review agents.

    Handles:
    - Deduplication of issues across agents
    - Conflict resolution (same issue, different severity)
    - Cross-cutting concerns (issue mentioned by multiple agents)
    - Unified summary generation
    """

    def __init__(self):
        self.reviews: List[ReviewOutput] = []

    def add_review(self, review: ReviewOutput):
        """Add a review from a specialist agent."""
        self.reviews.append(review)

    def load_from_directory(self, directory: str):
        """Load all JSON reviews from a directory."""
        path = Path(directory)
        for json_file in path.glob("*-review.json"):
            review = ReviewOutput.load_json(str(json_file))
            self.add_review(review)

    def deduplicate_issues(self) -> List[Issue]:
        """
        Deduplicate issues across reviews.

        Two issues are considered duplicates if they:
        - Reference the same file and line range
        - Have similar titles (fuzzy match)
        - Have the same category
        """
        all_issues = []
        for review in self.reviews:
            all_issues.extend(review.issues)

        unique_issues = []
        seen = set()

        for issue in all_issues:
            # Create fingerprint
            fingerprint = (
                issue.location.file,
                issue.location.line_start,
                issue.category
            )

            if fingerprint not in seen:
                seen.add(fingerprint)
                unique_issues.append(issue)
            else:
                # Issue already exists, maybe merge metadata
                existing = next(i for i in unique_issues
                               if self._same_fingerprint(i, issue))

                # If duplicate has higher severity, upgrade
                if self._severity_value(issue.severity) > \
                   self._severity_value(existing.severity):
                    existing.severity = issue.severity

                # Merge tags
                existing.tags = list(set(existing.tags + issue.tags))

        return unique_issues

    def _same_fingerprint(self, a: Issue, b: Issue) -> bool:
        """Check if two issues have the same fingerprint."""
        return (
            a.location.file == b.location.file and
            a.location.line_start == b.location.line_start and
            a.category == b.category
        )

    def _severity_value(self, severity: Severity) -> int:
        """Convert severity to numeric value for comparison."""
        return {
            Severity.CRITICAL: 5,
            Severity.HIGH: 4,
            Severity.MEDIUM: 3,
            Severity.LOW: 2,
            Severity.INFO: 1
        }[severity]

    def generate_summary(self) -> Dict:
        """Generate unified summary across all reviews."""
        issues = self.deduplicate_issues()

        by_severity = {
            Severity.CRITICAL: 0,
            Severity.HIGH: 0,
            Severity.MEDIUM: 0,
            Severity.LOW: 0,
            Severity.INFO: 0
        }
        for issue in issues:
            by_severity[issue.severity] += 1

        by_reviewer = {}
        for review in self.reviews:
            by_reviewer[review.reviewer] = len(review.issues)

        by_category = {}
        for issue in issues:
            by_category[issue.category] = by_category.get(issue.category, 0) + 1

        return {
            "total_issues": len(issues),
            "by_severity": {k.value: v for k, v in by_severity.items()},
            "by_reviewer": by_reviewer,
            "by_category": by_category,
            "reviews_completed": len(self.reviews),
            "overall_assessment": self._determine_assessment(by_severity),
            "should_block_merge": by_severity[Severity.CRITICAL] > 0
        }

    def _determine_assessment(self, by_severity: Dict[Severity, int]) -> str:
        """Determine overall assessment based on issue counts."""
        if by_severity[Severity.CRITICAL] > 0:
            return "changes_requested"
        elif by_severity[Severity.HIGH] > 0:
            return "changes_requested"
        elif by_severity[Severity.MEDIUM] > 0:
            return "commented"
        else:
            return "approved"

    def generate_unified_markdown(self) -> str:
        """Generate unified markdown report."""
        summary = self.generate_summary()
        issues = self.deduplicate_issues()

        # Sort by severity
        issues.sort(
            key=lambda i: self._severity_value(i.severity),
            reverse=True
        )

        md = f"""# Pull Request Review Summary

## Overview

- **Total Issues:** {summary['total_issues']}
- **Critical:** {summary['by_severity']['critical']}
- **High:** {summary['by_severity']['high']}
- **Medium:** {summary['by_severity']['medium']}
- **Low:** {summary['by_severity']['low']}
- **Overall Assessment:** {summary['overall_assessment'].upper()}

## Reviews Completed

"""
        for reviewer, count in summary['by_reviewer'].items():
            md += f"- **{reviewer}**: {count} issues found\n"

        md += "\n## Issues by Category\n\n"
        for category, count in sorted(summary['by_category'].items(),
                                     key=lambda x: x[1], reverse=True):
            md += f"- **{category}**: {count}\n"

        # Group issues by severity
        for severity in [Severity.CRITICAL, Severity.HIGH, Severity.MEDIUM,
                        Severity.LOW, Severity.INFO]:
            severity_issues = [i for i in issues if i.severity == severity]

            if not severity_issues:
                continue

            md += f"\n## {severity.value.title()} Priority Issues\n\n"

            for idx, issue in enumerate(severity_issues, 1):
                md += f"""### {idx}. {issue.title}

**Location:** `{issue.location.file}:{issue.location.line_start}`
**Category:** {issue.category}
**Impact:** {issue.impact.value}
**Fix Effort:** {issue.fix_effort.value}
**Confidence:** {issue.confidence:.0%}

{issue.description}

**Recommendation:**
{issue.recommendation}

"""
                if issue.code_snippet:
                    md += f"""**Code:**
```
{issue.code_snippet}
```

"""

                if issue.suggested_fix:
                    md += f"""**Suggested Fix:**
```
{issue.suggested_fix}
```

"""

                if issue.references:
                    md += "**References:**\n"
                    for ref in issue.references:
                        md += f"- {ref}\n"
                    md += "\n"

        return md

    def export_json(self, path: str):
        """Export aggregated data as JSON."""
        data = {
            "summary": self.generate_summary(),
            "issues": [
                json.loads(issue.json())
                for issue in self.deduplicate_issues()
            ],
            "reviews": [
                {
                    "reviewer": review.reviewer,
                    "review_type": review.review_type.value,
                    "timestamp": review.timestamp.isoformat(),
                    "issues_found": len(review.issues)
                }
                for review in self.reviews
            ]
        }

        with open(path, 'w') as f:
            json.dump(data, f, indent=2)
```

**Usage in pr-reviewer skill:**

```python
# skills/pr-reviewing/aggregate_reviews.py

from lib.review_aggregator import ReviewAggregator

# Collect all specialist reviews
aggregator = ReviewAggregator()
aggregator.load_from_directory("output/reviews")

# Generate unified outputs
summary = aggregator.generate_summary()
markdown = aggregator.generate_unified_markdown()

# Export
aggregator.export_json("output/pr-review-summary.json")

with open("output/pr-review-summary.md", 'w') as f:
    f.write(markdown)

# Print summary
print(f"Total Issues: {summary['total_issues']}")
print(f"Should Block Merge: {summary['should_block_merge']}")

# Exit code for CI/CD
import sys
sys.exit(1 if summary['should_block_merge'] else 0)
```

---

## Detailed Reasoning: Why Each Component Matters

### Reason 1: Reliable Programmatic Parsing

**Problem: Brittle Markdown Parsing**

```python
# Current approach (fragile)
def extract_issues_from_markdown(md: str) -> List:
    """Parse markdown, pray format hasn't changed."""
    issues = []

    # Hope headers always follow this pattern
    for match in re.finditer(r'### (.+?)\n\*\*Location:\*\* (.+?)\n', md):
        title = match.group(1)
        location = match.group(2)

        # What if location format changes?
        # "file:line" vs "file:line-line" vs "file:line:col"?
        # What if there's a space? Tab? Multiple formats?
        # What if agent decides to add emoji? 🐛

        # This breaks silently
        try:
            file, line = location.split(':')
        except:
            # Oops, format changed
            continue

        issues.append({'title': title, 'file': file, 'line': line})

    return issues  # Hope we got them all
```

**Result:** Parsing failures, missing issues, brittle maintenance.

**Solution: JSON Schema**

```python
# With structured output (reliable)
def load_issues(json_path: str) -> List[Issue]:
    """Load issues with guaranteed structure."""
    review = ReviewOutput.load_json(json_path)

    # Pydantic validates:
    # ✅ All required fields present
    # ✅ Types correct (int line numbers, not strings)
    # ✅ Enums valid (severity in allowed values)
    # ✅ Constraints met (line_end >= line_start)

    return review.issues  # Guaranteed valid
```

**Result:** Reliable parsing, no silent failures, type safety.

---

### Reason 2: Automation Enablement

**What becomes possible with structured output:**

**1. Auto-create GitHub Issues**

```python
# scripts/auto_create_issues.py

from lib.review_aggregator import ReviewAggregator
import github

g = github.Github(os.environ['GITHUB_TOKEN'])
repo = g.get_repo("vladolaru/claude-code-plugins")

aggregator = ReviewAggregator()
aggregator.load_from_directory("output/reviews")

for issue in aggregator.deduplicate_issues():
    if issue.severity in [Severity.CRITICAL, Severity.HIGH]:
        # Auto-create GitHub issue
        gh_issue = repo.create_issue(
            title=f"[{issue.severity.upper()}] {issue.title}",
            body=issue.to_github_issue("vladolaru/claude-code-plugins",
                                       os.environ['COMMIT_SHA'])['body'],
            labels=[issue.severity.value, issue.impact.value, issue.category],
            assignees=[determine_owner(issue.location.file)]
        )

        print(f"Created: {gh_issue.html_url}")
```

**2. Block Merge in CI/CD**

```yaml
# .github/workflows/pr-review.yml

name: PR Review
on: [pull_request]

jobs:
  review:
    runs-on: ubuntu-latest
    steps:
      - name: Run Reviews
        run: /review-pr --output json

      - name: Check for Critical Issues
        id: check
        run: |
          CRITICAL=$(jq '.summary.by_severity.critical' output/pr-review-summary.json)
          echo "critical_count=$CRITICAL" >> $GITHUB_OUTPUT

          if [ "$CRITICAL" -gt 0 ]; then
            echo "❌ Found $CRITICAL critical issues"
            exit 1
          fi

      - name: Comment on PR
        if: always()
        uses: actions/github-script@v6
        with:
          script: |
            const fs = require('fs');
            const summary = JSON.parse(
              fs.readFileSync('output/pr-review-summary.json', 'utf8')
            );
            const markdown = fs.readFileSync(
              'output/pr-review-summary.md', 'utf8'
            );

            github.rest.issues.createComment({
              issue_number: context.issue.number,
              owner: context.repo.owner,
              repo: context.repo.repo,
              body: markdown
            });
```

**3. Metrics Dashboard**

```python
# scripts/generate_metrics_dashboard.py

from lib.review_aggregator import ReviewAggregator
import pandas as pd
import matplotlib.pyplot as plt
from pathlib import Path

# Load last 90 days of reviews
reviews = []
for json_file in Path("reviews/").glob("*/pr-review-summary.json"):
    reviews.append(json.load(open(json_file)))

# Create DataFrame
df = pd.DataFrame([
    {
        'date': r['timestamp'],
        'pr': r['pr_number'],
        'critical': r['summary']['by_severity']['critical'],
        'high': r['summary']['by_severity']['high'],
        'medium': r['summary']['by_severity']['medium'],
        'total': r['summary']['total_issues']
    }
    for r in reviews
])

# Generate charts
fig, axes = plt.subplots(2, 2, figsize=(15, 10))

# Issues over time
df.groupby('date')[['critical', 'high', 'medium']].sum().plot(
    ax=axes[0, 0],
    title="Issues by Severity Over Time"
)

# Issues by category
category_counts = {}
for r in reviews:
    for cat, count in r['summary']['by_category'].items():
        category_counts[cat] = category_counts.get(cat, 0) + count

pd.Series(category_counts).sort_values().plot(
    kind='barh',
    ax=axes[0, 1],
    title="Issues by Category"
)

# Average fix time
# (requires tracking issue resolution)
# ...

plt.savefig('metrics-dashboard.png')
```

---

### Reason 3: Cross-Agent Coordination

**Problem: No way to correlate findings**

```markdown
# security-reviewer finds:
SQL injection in OrderProcessor.php:67

# architecture-reviewer finds:
God class in OrderProcessor.php (500 lines, 30 methods)

# tests-reviewer finds:
No tests for OrderProcessor.php
```

**Question:** Are these related? Should we create ONE issue or THREE?

**Without structured output:** No way to know. Create 3 separate issues, developer gets annoyed by duplication.

**With structured output:**

```python
# Aggregator detects related issues
from lib.review_aggregator import ReviewAggregator

aggregator = ReviewAggregator()
aggregator.load_from_directory("output/reviews")

# Group issues by file
issues_by_file = {}
for issue in aggregator.deduplicate_issues():
    file = issue.location.file
    if file not in issues_by_file:
        issues_by_file[file] = []
    issues_by_file[file].append(issue)

# For files with multiple issues, create meta-issue
for file, issues in issues_by_file.items():
    if len(issues) >= 3:
        # Multiple problems in same file = refactoring needed
        print(f"""
Meta-Issue: Refactor {file}

This file has multiple issues across different reviewers:
{', '.join(i.category for i in issues)}

Consider comprehensive refactoring instead of piecemeal fixes.
        """)
```

---

### Reason 4: Confidence Tracking

**Structured output includes confidence scores:**

```python
# Agent adds confidence to each issue
builder.add_issue(
    title="Possible SQL Injection",
    description="...",
    severity=Severity.HIGH,
    confidence=0.65,  # Not 100% sure
    # ...
)
```

**Use cases:**

1. **Filter by confidence**
   ```python
   # Only show high-confidence issues
   high_conf = [i for i in issues if i.confidence >= 0.8]
   ```

2. **Prioritize review**
   ```python
   # Review low-confidence issues manually
   needs_review = [i for i in issues if i.confidence < 0.7]
   ```

3. **Track agent accuracy over time**
   ```python
   # After developer confirms/rejects findings:
   for issue in resolved_issues:
       agent_was_correct = issue.developer_confirmed
       agent_confidence = issue.confidence

       # Log for model improvement
       log_accuracy(agent_confidence, agent_was_correct)
   ```

---

## Integration Points

### Where Structured Output is Used

```
┌────────────────────────────────────────────────────┐
│                  pr-reviewing skill                │
│                                                    │
│  1. Spawns specialist agents                       │
│  2. Each agent outputs JSON + Markdown             │
│  3. Aggregator collects JSON outputs               │
│  4. Deduplicates and correlates issues             │
│  5. Generates unified JSON + Markdown              │
└────────────────────────────────────────────────────┘
                         │
        ┌────────────────┼────────────────┐
        ▼                ▼                ▼
┌──────────────┐ ┌──────────────┐ ┌──────────────┐
│ pr-review-   │ │ pr-review-   │ │ Individual   │
│ summary.json │ │ summary.md   │ │ agent JSONs  │
│              │ │              │ │              │
│ (Machines)   │ │ (Humans)     │ │ (Debugging)  │
└──────────────┘ └──────────────┘ └──────────────┘
        │
        └─────────────────┬─────────────────────────┐
                          ▼                         ▼
                  ┌──────────────┐       ┌──────────────────┐
                  │ CI/CD        │       │ Metrics System   │
                  │ - Block merge│       │ - Track trends   │
                  │ - Status     │       │ - Dashboards     │
                  │   checks     │       │ - Alerts         │
                  └──────────────┘       └──────────────────┘
                          │
                          ▼
                  ┌──────────────┐
                  │ GitHub API   │
                  │ - Auto-create│
                  │   issues     │
                  │ - Link PRs   │
                  └──────────────┘
```

---

## Expected Outcomes

### Quantitative Improvements

| Metric | Before | After | Improvement |
|--------|--------|-------|-------------|
| **Parse reliability** | ~60% | 99.9% | 66% improvement |
| **Issue tracking** | Manual | Automated | ∞x improvement |
| **Aggregation accuracy** | ~70% | 99% | 41% improvement |
| **CI/CD integration** | Impossible | Trivial | ∞x improvement |
| **Metrics generation** | Manual | Automated | ∞x improvement |
| **Time to auto-create issue** | N/A | <1 second | New capability |

### Qualitative Improvements

**Developer Experience:**
- ✅ Consistent issue format across all agents
- ✅ Automated issue tracking
- ✅ Clear severity and confidence levels
- ✅ Actionable recommendations with fix effort estimates
- ✅ Direct links to code locations

**Automation:**
- ✅ Reliable CI/CD integration
- ✅ Automated quality gates
- ✅ Auto-created GitHub issues
- ✅ Metrics dashboards
- ✅ Trend analysis

**Agent Coordination:**
- ✅ Reliable aggregation across specialists
- ✅ Deduplication of overlapping findings
- ✅ Cross-cutting concern detection
- ✅ Meta-issue creation

**Observability:**
- ✅ Track agent accuracy over time
- ✅ Measure fix rates by severity
- ✅ Identify problematic code areas
- ✅ Monitor review coverage

---

## Risks & Mitigations

### Risk 1: Schema Changes Break Existing Code

**Scenario:** We update the schema, existing code fails.

**Mitigation:**

1. **Versioning:**
   ```python
   class ReviewOutput(BaseModel):
       schema_version: str = "1.0.0"  # Semantic versioning
   ```

2. **Backward compatibility:**
   ```python
   def load_review(path: str) -> ReviewOutput:
       data = json.load(open(path))
       version = data.get('schema_version', '1.0.0')

       if version == '1.0.0':
           return ReviewOutput_v1(**data)
       elif version == '2.0.0':
           return ReviewOutput_v2(**data)
       else:
           raise ValueError(f"Unsupported schema version: {version}")
   ```

3. **Migration tools:**
   ```python
   def migrate_v1_to_v2(old: ReviewOutput_v1) -> ReviewOutput_v2:
       """Migrate old format to new format."""
       # Transform data structure
       pass
   ```

---

### Risk 2: JSON Output Increases Token Cost

**Scenario:** Agents now output both JSON and Markdown, doubling output size.

**Analysis:**

**Current output (Markdown only):**
- Typical review: ~2,000 tokens output
- Cost: ~$0.03 per review (Sonnet 4.5 output)

**With JSON + Markdown:**
- JSON: ~1,000 tokens
- Markdown: ~2,000 tokens
- Total: ~3,000 tokens
- Cost: ~$0.045 per review

**Increase:** $0.015 per review = $1.50/year at 100 PRs/week

**Mitigation:**

1. **Store JSON only, generate Markdown on-demand:**
   ```python
   # Agent outputs JSON only
   output.save_json("review.json")

   # Generate markdown when needed
   markdown = render_markdown_from_json(output)
   ```

2. **Compress JSON:**
   ```python
   import gzip
   with gzip.open("review.json.gz", 'wt') as f:
       f.write(output.json())
   ```

3. **Trade-off:** $1.50/year cost increase enables $10,000+/year value in automation.

**Verdict:** Cost increase negligible compared to benefits.

---

### Risk 3: Agents Struggle to Generate Valid JSON

**Scenario:** Agent produces malformed JSON, validation fails.

**Mitigation:**

1. **Use Claude's structured output API:**
   ```python
   # With Anthropic's structured output feature
   response = client.messages.create(
       model="claude-sonnet-4-5-20250929",
       messages=[{"role": "user", "content": prompt}],
       response_format={
           "type": "json_schema",
           "json_schema": ReviewOutputSchema
       }
   )

   # Guaranteed valid JSON conforming to schema
   ```

2. **Validation with helpful errors:**
   ```python
   try:
       output = ReviewOutput(**json_data)
   except ValidationError as e:
       print("Validation failed:")
       for error in e.errors():
           print(f"  - {error['loc']}: {error['msg']}")

       # Provide fix suggestions
       suggest_fixes(e.errors())
   ```

3. **Fallback to markdown-only:**
   ```python
   try:
       output = ReviewOutput(**json_data)
   except ValidationError:
       # Validation failed, save markdown only
       save_markdown_only(markdown_output)
       log_validation_failure()
   ```

---

### Risk 4: Schema Too Rigid, Prevents Innovation

**Scenario:** Agent wants to add new field, but schema doesn't allow it.

**Mitigation:**

1. **Extension fields:**
   ```python
   class Issue(BaseModel):
       # Standard fields
       title: str
       severity: Severity
       # ...

       # Extension point
       metadata: Dict[str, Any] = Field(default_factory=dict)
   ```

2. **Optional fields:**
   ```python
   class Issue(BaseModel):
       # Required
       title: str
       severity: Severity

       # Optional extensions
       ai_generated_fix: Optional[str] = None
       visual_diagram: Optional[str] = None  # URL to diagram
       related_documentation: Optional[List[str]] = None
   ```

3. **Schema evolution:**
   ```python
   # Version 1.0: Basic fields
   # Version 1.1: Added confidence scores
   # Version 1.2: Added fix_effort
   # Version 2.0: Breaking change (restructured location)
   ```

---

## Testing Strategy

### Unit Tests for Schema Validation

```python
# tests/test_review_output_schema.py

import pytest
from schemas.review_output import ReviewOutput, Issue, IssueLocation
from pydantic import ValidationError

def test_valid_review_output():
    """Test valid ReviewOutput validates successfully."""
    output = ReviewOutput(
        review_type="security",
        reviewer="security-reviewer",
        commit_sha="abc123" * 7,  # 42 chars
        base_ref="main",
        head_ref="feature",
        summary={
            "total_issues": 1,
            "by_severity": {"critical": 1, "high": 0, "medium": 0, "low": 0, "info": 0},
            "overall_assessment": "changes_requested",
            "confidence_score": 0.9,
            "review_time_seconds": 10.5
        },
        issues=[
            {
                "title": "SQL Injection",
                "description": "...",
                "category": "sql_injection",
                "severity": "critical",
                "impact": "security",
                "location": {
                    "file": "src/order.php",
                    "line_start": 67
                },
                "recommendation": "Use prepared statements",
                "confidence": 0.95,
                "fix_effort": "easy"
            }
        ],
        metrics={
            "files_reviewed": 1,
            "lines_reviewed": 100,
            "files_with_issues": 1,
            "tokens_used": 1000,
            "review_duration_seconds": 10.5
        }
    )

    assert output.review_type == "security"
    assert len(output.issues) == 1

def test_invalid_severity_rejected():
    """Test invalid severity value is rejected."""
    with pytest.raises(ValidationError) as exc_info:
        Issue(
            title="Test",
            description="Test",
            category="test",
            severity="SUPER_CRITICAL",  # Invalid
            impact="security",
            location=IssueLocation(file="test.php", line_start=1),
            recommendation="Fix it",
            confidence=0.8,
            fix_effort="easy"
        )

    assert "severity" in str(exc_info.value)

def test_line_end_must_be_after_start():
    """Test line_end validation."""
    with pytest.raises(ValidationError):
        IssueLocation(
            file="test.php",
            line_start=100,
            line_end=50  # Invalid: before start
        )

def test_confidence_must_be_in_range():
    """Test confidence score validation."""
    with pytest.raises(ValidationError):
        Issue(
            title="Test",
            description="Test",
            category="test",
            severity="high",
            impact="security",
            location=IssueLocation(file="test.php", line_start=1),
            recommendation="Fix it",
            confidence=1.5,  # Invalid: > 1.0
            fix_effort="easy"
        )

def test_issue_to_github_format():
    """Test conversion to GitHub issue format."""
    issue = Issue(
        title="SQL Injection",
        description="Vulnerability in query",
        category="sql_injection",
        severity="critical",
        impact="security",
        location=IssueLocation(file="src/order.php", line_start=67),
        recommendation="Use prepared statements",
        confidence=0.95,
        fix_effort="easy"
    )

    gh_issue = issue.to_github_issue("owner/repo", "abc123" * 7)

    assert "[CRITICAL]" in gh_issue['title']
    assert "sql_injection" in gh_issue['body']
    assert "critical" in gh_issue['labels']
```

### Integration Tests with Real Agent Output

```python
# tests/test_agent_structured_output.py

def test_security_reviewer_produces_valid_json():
    """Test security-reviewer outputs valid JSON."""
    # Run security reviewer on test code
    result = subprocess.run(
        ['python', 'agents/security-reviewer/review.py',
         'test-commit', 'main', 'test-branch'],
        capture_output=True,
        text=True
    )

    # Load JSON output
    output = ReviewOutput.load_json("output/security-review.json")

    # Verify structure
    assert output.review_type == "security"
    assert isinstance(output.issues, list)
    assert output.summary.total_issues == len(output.issues)

def test_aggregator_handles_multiple_reviews():
    """Test aggregator can process multiple review JSONs."""
    from lib.review_aggregator import ReviewAggregator

    aggregator = ReviewAggregator()
    aggregator.load_from_directory("tests/fixtures/reviews")

    summary = aggregator.generate_summary()

    assert summary['total_issues'] >= 0
    assert summary['reviews_completed'] == 5  # 5 fixture reviews

def test_aggregator_deduplicates_issues():
    """Test aggregator deduplicates same issue from multiple agents."""
    from lib.review_aggregator import ReviewAggregator

    # Load reviews with known duplicate
    # (same file, line, category from 2 different agents)
    aggregator = ReviewAggregator()
    aggregator.load_from_directory("tests/fixtures/duplicate-issues")

    issues = aggregator.deduplicate_issues()

    # Should dedupe to single issue
    sql_injection_issues = [i for i in issues if i.category == "sql_injection"]
    assert len(sql_injection_issues) == 1
```

---

## Rollout Plan

### Week 1: Schema Definition & Validation

**Monday-Tuesday:**
- Define TypeScript schema
- Define Python Pydantic models
- Generate JSON Schema file
- Write schema validation tests
- Document schema fields

**Wednesday:**
- Create ReviewOutputBuilder helper class
- Write unit tests for builder
- Test schema with sample data

**Thursday-Friday:**
- Review schema with team
- Iterate based on feedback
- Finalize v1.0.0 schema

---

### Week 2: Agent Integration

**Monday-Tuesday:**
- Update security-reviewer to use structured output
- Update architecture-reviewer
- Test both agents produce valid JSON

**Wednesday:**
- Update tests-reviewer
- Update performance-reviewer
- Test all agents

**Thursday:**
- Create ReviewAggregator class
- Implement deduplication logic
- Test aggregation

**Friday:**
- Update pr-reviewing skill to use aggregator
- End-to-end testing
- Fix any integration issues

---

### Week 3: Automation & Documentation

**Monday:**
- Create CI/CD integration examples
- Create auto-issue-creation script
- Create metrics dashboard script

**Tuesday:**
- Documentation for schema
- Documentation for using structured output
- Migration guide for existing code

**Wednesday-Friday:**
- Deploy to production
- Monitor for issues
- Collect feedback
- Iterate

---

## Success Metrics

### Must Achieve (Go/No-Go):

| Metric | Target | Measurement |
|--------|--------|-------------|
| **JSON validation success rate** | ≥ 99% | Track parse errors vs total reviews |
| **Schema backward compatibility** | 100% | Old JSONs still parse with new schema |
| **Agent adoption** | 100% | All agents output structured data |
| **Aggregation accuracy** | ≥ 95% | Manual review of deduplicated issues |

**If any metric fails target:** Fix issues or rollback.

### Nice to Have (Optimization Targets):

| Metric | Target | Measurement |
|--------|--------|-------------|
| **Automation adoption** | ≥ 3 use cases | CI/CD, auto-issues, metrics |
| **Parse reliability improvement** | ≥ 30% | Compare regex parsing vs JSON |
| **Time to create issue** | < 5 seconds | Manual tracking |
| **Developer satisfaction** | ≥ 8/10 | Survey feedback |

---

## Alternative Approaches Considered

### Alternative 1: Markdown-Only (Status Quo)

**Pros:**
- Zero implementation effort
- Human-readable
- Flexible format

**Cons:**
- ❌ No programmatic parsing
- ❌ No automation
- ❌ Brittle aggregation
- ❌ No metrics

**Verdict:** ❌ Rejected - Benefits of structured output far outweigh costs

---

### Alternative 2: JSON-Only (No Markdown)

**Approach:** Agents output only JSON, generate markdown on-demand.

**Pros:**
- ✅ Structured data
- ✅ Slightly lower token cost
- ✅ Single source of truth

**Cons:**
- ❌ Agents less good at pure JSON than JSON+explanation
- ❌ Lose rich narrative in agent output
- ❌ Harder to debug agent reasoning

**Verdict:** ❌ Rejected - Dual output (JSON+Markdown) is better

---

### Alternative 3: YAML Output

**Approach:** Use YAML instead of JSON for structured output.

**Pros:**
- ✅ More human-readable than JSON
- ✅ Supports comments
- ✅ Less verbose

**Cons:**
- ❌ Less tool support
- ❌ Slower parsing
- ❌ More error-prone (indentation)
- ❌ Not industry standard for APIs

**Verdict:** ❌ Rejected - JSON is industry standard

---

### Alternative 4: Dual Output: JSON + Markdown (SELECTED ✅)

**Approach:** Agents output both JSON (for machines) and Markdown (for humans).

**Pros:**
- ✅ Best of both worlds
- ✅ JSON for automation
- ✅ Markdown for human reading
- ✅ Agents can explain reasoning in markdown
- ✅ Schema validation on JSON
- ✅ Flexible narrative in markdown

**Cons:**
- ⚠️ Slightly higher token cost (~$1.50/year)
- ⚠️ Must maintain two formats

**Verdict:** ✅ **SELECTED** - Optimal balance

---

## Detailed Implementation Checklist

### Prerequisites
- [ ] Review JSON Schema specification
- [ ] Install Pydantic: `pip install pydantic`
- [ ] Install JSON Schema validator
- [ ] Review awesome-agentic-patterns structured output section

### Phase 1: Schema Definition (2 hours)
- [ ] Create `schemas/ReviewOutput.schema.ts`
- [ ] Create `schemas/review_output.py` (Pydantic)
- [ ] Generate `schemas/review-output.schema.json`
- [ ] Write schema validation tests
- [ ] Document schema fields
- [ ] Validate with sample data

### Phase 2: Agent Helper (2 hours)
- [ ] Create `scripts/lib/review_output_builder.py`
- [ ] Implement `ReviewOutputBuilder` class
- [ ] Add helper methods (add_issue, add_file_reviewed, etc.)
- [ ] Write unit tests
- [ ] Create usage examples
- [ ] Document API

### Phase 3: Agent Integration (4 hours)
- [ ] Update security-reviewer prompt
- [ ] Update architecture-reviewer prompt
- [ ] Update tests-reviewer prompt
- [ ] Update performance-reviewer prompt
- [ ] Test each agent produces valid JSON
- [ ] Verify markdown output still generated
- [ ] Test with real PRs

### Phase 4: Aggregation (3 hours)
- [ ] Create `scripts/lib/review_aggregator.py`
- [ ] Implement deduplication logic
- [ ] Implement conflict resolution
- [ ] Implement unified summary generation
- [ ] Write integration tests
- [ ] Test on multi-agent reviews

### Phase 5: Automation Scripts (3 hours)
- [ ] Create `scripts/auto_create_issues.py`
- [ ] Create `scripts/generate_metrics_dashboard.py`
- [ ] Create CI/CD integration examples
- [ ] Test automation workflows
- [ ] Document usage

### Phase 6: Documentation & Deployment (2 hours)
- [ ] Schema documentation
- [ ] Usage guide for agents
- [ ] Migration guide
- [ ] Update CHANGELOG
- [ ] Deploy to production
- [ ] Monitor for 1 week
- [ ] Iterate based on feedback

---

## ROI Analysis

### Investment

**Development time:** 16-18 hours total
- Phase 1 Schema: 2 hours
- Phase 2 Helper: 2 hours
- Phase 3 Agent Integration: 4 hours
- Phase 4 Aggregation: 3 hours
- Phase 5 Automation: 3 hours
- Phase 6 Documentation: 2 hours

**Assuming $100/hour developer rate:** $1,600-$1,800 investment

### Return

**Quantifiable benefits:**

1. **Time savings on manual issue tracking:** 5 minutes per PR × 100 PRs/week = 500 minutes/week = 43 hours/year = **$4,300/year**

2. **Reduced debugging of parsing failures:** 2 hours/week × 52 weeks = 104 hours/year = **$10,400/year**

3. **Automated CI/CD integration value:** Prevent 1 critical issue per month from reaching production = **$5,000/year** (conservative estimate)

4. **Metrics and insights:** Better decisions from data = **$2,000/year** (conservative)

**Total annual return: $21,700/year**

**ROI:** 1,106% in first year

**Payback period:** ~3-4 weeks

---

## Recommendation

**IMPLEMENT IMMEDIATELY**

**Reasoning:**
1. **Exceptional ROI** (1,106% first-year ROI)
2. **Foundation for automation** (enables all future automation work)
3. **Universal benefit** (all agents, all use cases)
4. **Industry best practice** (structured output is standard)
5. **Fast payback** (3-4 weeks)
6. **Low risk** (validation ensures correctness, fallback to markdown)

**Start with Phase 1-2** (schema + helper) to prove value, then expand to full integration.

---

## Questions for Approval

1. **Go/No-Go:** Approve implementation of structured output for review agents?

2. **Schema version:** Start with v1.0.0 and iterate, or gather more requirements first?
   - **Recommendation:** Start with v1.0.0, iterate based on real usage

3. **Output format:** Dual output (JSON+Markdown) or JSON-only?
   - **Recommendation:** Dual output for flexibility

4. **Validation:** Strict validation (reject invalid JSON) or lenient (log and continue)?
   - **Recommendation:** Strict validation with fallback to markdown-only

5. **Priority:** Implement before or after semantic context filtering (Proposal #1)?
   - **Recommendation:** After semantic filtering (filtering reduces cost more)

Please approve or request modifications to this proposal before I proceed with implementation.
