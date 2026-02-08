# Shared Reviewer Protocol

Standard protocol for all review agents. Read this FIRST before starting your review.

## Context Format

The main session provides: **PR ID**, **Output Directory**, **Git Range** (base..head refs), and optional **Focus Areas**.

## RULE: Changed Code Only

Review ONLY code in the PR diff. For every finding, verify:

1. **Is this in the changed code?** Issues in unchanged code are NOT findings.
2. **Is this new or pre-existing?** Only report issues INTRODUCED by this PR.
3. **Would I bet my reputation on this?** If uncertain, verify deeper or drop it.
4. **Am I reviewing the change, or the codebase?** Evaluate THIS CHANGE, not the entire codebase.

## ReviewOutputBuilder API

```python
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '../scripts'))
from review_output_simple import ReviewOutputBuilder

builder = ReviewOutputBuilder(pr_id=PR_ID, reviewer="REVIEWER_NAME")
```

**Core methods:**
- `builder.add_issue(severity, title, file, description, recommendation, category="general", confidence=0.9, line=None)` - Add finding
- `builder.set_files_reviewed(N)` - Track files reviewed
- `builder.add_tool_result("ToolName")` - Track tools used
- `builder.set_confidence(0.0-1.0)` - Set overall confidence
- `builder.add_positive("observation")` - Note good patterns
- `builder.to_json()` / `builder.to_markdown()` - Generate outputs

**Valid severities:** `critical`, `high`, `medium`, `low`, `info`

## File-Based Output

Write both outputs, then return signals only:

```python
json_output = builder.to_json()
markdown_output = builder.to_markdown()
# Write to: {output_dir}/{reviewer}-review.json and .md
```

**Return signal format:**
```
STATUS: FINISHED
OUTPUT_FILES:
  - {output_dir}/{reviewer}-review.json
  - {output_dir}/{reviewer}-review.md
COUNTS:
  critical: N
  high: N
  medium: N
VERDICT: <verdict>
SUMMARY: <one sentence>
```

Do NOT return full review text. The reconciliator reads your files.

## Project-Specific Knowledge (MUST DO FIRST)

Before reviewing, search for project-specific documentation:

```bash
find . -type f \( -name "CLAUDE.md" -o -name "*.md" \) -path "*/.claude/*" 2>/dev/null | head -20
```

Look for: `CLAUDE.md`, `.claude/skills/`, `.claude/docs/`, ADRs, architecture docs. Read and apply project-specific standards before generic patterns.

## Ground Truth Data Loading

When the main session provides linter/scanner/test/coverage results, load them as ground truth:

```python
import json, os
results_file = f"{output_dir}/{results_type}-unified.json"
if os.path.exists(results_file):
    with open(results_file) as f:
        results = json.load(f)
```

Available result types: `lint-results`, `security-results`, `test-results`, `coverage-results`. Treat tool findings as **definitive**. Use them as supporting evidence, not duplicating them unless they have domain significance.

## Verbose Reasoning Mode

When VERBOSE=true, include `<details>` blocks for each finding with:
- Detection process (grep/search commands)
- Analysis specific to your domain
- Confidence score rationale (what you verified vs didn't)
- Alternative interpretations

Be factual: reference actual code lines, show actual commands. Admit what you didn't check.
