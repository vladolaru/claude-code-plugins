# Shared Reviewer Protocol

Standard protocol for all review agents. Read this FIRST before starting your review.

## Step 0: Locate Plugin Root

**Preferred: Use the bootstrap script** which handles all setup (plugin root, protocol, scope discovery) in a single command:

```bash
PLUGIN_ROOT=$(cat /tmp/.pirategoat-tools-root 2>/dev/null)
[ -z "$PLUGIN_ROOT" ] || [ ! -d "$PLUGIN_ROOT/scripts" ] && PLUGIN_ROOT=$(find ~/.claude -path "*/pirategoat-tools/*/scripts/bootstrap-reviewer.py" -type f 2>/dev/null | sort | tail -1 | xargs dirname | xargs dirname)
python3 $PLUGIN_ROOT/scripts/bootstrap-reviewer.py --agent <agent-name>
```

If the bootstrap script is not available, locate the plugin root manually:

```bash
PLUGIN_ROOT=$(cat /tmp/.pirategoat-tools-root 2>/dev/null)
# Fallback if hook hasn't run yet
[ -z "$PLUGIN_ROOT" ] || [ ! -d "$PLUGIN_ROOT/scripts" ] && PLUGIN_ROOT=$(find ~/.claude -path "*/pirategoat-tools/*/scripts/review-scope.py" -type f 2>/dev/null | sort | tail -1 | xargs dirname | xargs dirname)
echo "PLUGIN_ROOT=$PLUGIN_ROOT"
```

If this fails, fall back to the manual scope discovery at the end of this section.

Store `PLUGIN_ROOT` — you'll use it for:
- `python3 $PLUGIN_ROOT/scripts/review-scope.py` — scope discovery
- Reading reference files like `$PLUGIN_ROOT/agents/shared/*.md`, `$PLUGIN_ROOT/skills/*/references/*.md`

## Scope Discovery (Do This FIRST)

Use `review-scope.py` to efficiently determine your review scope. It handles range detection, noise filtering, domain filtering, context budgeting, and output directory detection in a single call.

```bash
# Your Scope section specifies which --domain to use
python3 $PLUGIN_ROOT/scripts/review-scope.py --domain <your-domain>

# With explicit range (when provided by caller)
python3 $PLUGIN_ROOT/scripts/review-scope.py --domain <your-domain> --range "main..feature-branch"

# For large PRs: get diffstat overview, then selectively read diffs
python3 $PLUGIN_ROOT/scripts/review-scope.py --domain <your-domain> --summary

# For agents exploring preexisting code (patterns-reviewer, history-insights-reviewer)
python3 $PLUGIN_ROOT/scripts/review-scope.py --domain <your-domain> --base-ref-only
```

### Reading the Output

The script outputs structured text. Parse these key fields from the header:

| Field | Use |
|-------|-----|
| `STATUS` | `OK`, `NO_DOMAIN_FILES`, or `ERROR` |
| `RANGE` | The git range used (for manual diff reads if needed) |
| `BASE_REF` | Base branch ref (for exploring preexisting code) |
| `OUTPUT_DIR` | Where to write review output files |
| `PR_NUMBER` | PR number (if detected) |
| `BUDGET_EXCEEDED` | Files listed but not diffed due to context budget |

**On `STATUS: ERROR`:** Report the error to the caller. Do NOT proceed with review.

**On `STATUS: NO_DOMAIN_FILES`:** Report "No [domain] files to review" → APPROVE → exit.

**On `STATUS: OK`:** The `=== DIFFS ===` section contains filtered diffs for matched files within the context budget. Files are sorted smallest-first (focused changes before large files). If many files exceed the budget, the `=== NOT DIFFED ===` section shows them with diffstat so you can selectively `git diff <range> -- <file>` the most important ones.

**For large PRs (100+ matched files):** Use `--summary` to get a diffstat overview of ALL files without any diffs. Pick the most important files and read their diffs selectively. This is more context-efficient than the default mode for very large PRs.

### When You Need More Context

The script provides diffs. If a finding needs surrounding context:
```bash
# Read specific lines around a finding
# Use the Read tool with offset+limit, not cat/head/tail
```

### If the Script Is Not Available

Fall back to manual commands:
```bash
DEFAULT_BRANCH=$(git symbolic-ref refs/remotes/origin/HEAD 2>/dev/null | sed 's@^refs/remotes/origin/@@')
[ -z "$DEFAULT_BRANCH" ] && DEFAULT_BRANCH="main"
RANGE="${DEFAULT_BRANCH}..HEAD"
git diff --name-only $RANGE | grep -v -E '\.(lock|png|jpg|jpeg|gif|svg|ico|woff|woff2|ttf|eot|map)$' \
  | grep -v -E '(^|/)(vendor|node_modules)/' \
  | grep -v -E '\.min\.(js|css)$' \
  | grep -v -E '(^dist/|^build/|^\.idea/|^\.vscode/|\.DS_Store$)'
# Then apply your domain filter (see Scope section)
# Then: git diff $RANGE -- <file> for each matched file
```

## RULE: Reviewing vs Exploring

Two distinct activities with different rules:

| Activity | Purpose | Scope | Generates findings? |
|----------|---------|-------|---------------------|
| **Reviewing** | Analyze code for issues | Changed files only (the diff) | YES |
| **Exploring** | Understand context | Any file in the codebase | NO |

Exploration is expected and encouraged: reading project conventions, understanding call sites, checking how similar code works elsewhere.

**STOP CHECK — before every `add_issue()` call:**

State the file path and line number for this finding. Then answer two questions:
1. Is this file in `CHANGED_FILES`? (If NO → drop: not in diff)
2. Is this line in a diff hunk? (If NO → drop: pre-existing code)

If either answer is NO, this is exploration context, not a finding. Do NOT call `add_issue()`. This check is mandatory — findings on unchanged code are false positives.

**CRITICAL — line numbers must be SOURCE FILE line numbers:**

When you read a diff file (like `scoped-diff.patch`) with the Read tool, the tool adds its own display line numbers (e.g., `227→+class Foo`). These are the line numbers **within the patch file**, NOT the source file line numbers. Do NOT use them in `add_issue(line=...)`.

To find the correct source file line number, use the `@@ ... @@` hunk headers in the diff:
- `@@ -0,0 +1,116 @@` means the new file starts at source line 1
- `@@ -20,6 +20,11 @@` means the changed section starts at source line 20
- Count forward from the hunk header's `+N` value through `+` and ` ` (context) lines to find the source line for any specific change

If you are unsure of the source line number, read the actual file with the Read tool to confirm.

For every finding that passes the STOP CHECK, also verify:
1. **Is this in the changed code?** Issues in unchanged code are NOT findings.
2. **Is this new or pre-existing?** Only report issues INTRODUCED by this change.
3. **Would I bet my reputation on this?** If uncertain, verify deeper or drop it.
4. **Am I reviewing the change, or the codebase?** Evaluate THIS CHANGE, not the entire codebase.
5. **Is this a bug or a preference?** For LOW and MEDIUM findings: if this is a formatting choice, naming opinion, code organization style, or "I would have done it differently" without a concrete defect, regression, or security concern — it's a preference. Drop it.
6. **Did I verify my factual claim?** If your finding says code does or doesn't do something specific (missing close, missing attribute, missing null check, O(N^2) complexity), you MUST read the actual implementation lines with the Read tool to confirm. Do not infer behavior from context or variable names. 47% of false positives come from factual claims that don't match the actual code.
7. **Can I cite my source?** When stating specific facts — numbers, counts, line references, API behaviors, git metadata — cite the tool output that produced them. If you didn't run a command or read a file to obtain a fact, do not present it as verified.

<example type="CORRECT">
Finding: "process_payment() at line 42 concatenates user input into SQL query — this line was ADDED in this PR."
Reason: Changed code, new issue, verified in diff.
</example>

<example type="INCORRECT">
Finding: "validate_email() at line 200 is missing sanitization — found while exploring the file for context."
Reason: Unchanged code, pre-existing issue, discovered during exploration.
</example>

**Agents that explore preexisting code** (patterns-reviewer, history-insights-reviewer): when searching for what already exists, search the **base ref state** (`git grep <pattern> <base_ref>`, `git show <base_ref>:<path>`), not HEAD. HEAD includes the PR's own changes — searching HEAD would find the very code you're supposed to be comparing against.

## Output Directory

**If Output Directory was provided:** use it (`mkdir -p` if needed).

**If not provided:** use the `OUTPUT_DIR` from `review-scope.py` output. The script auto-detects PR number via `gh` (github.com) or `ghe` (github.a8c.com) and creates `/tmp/pr-review-{N}`. Falls back to `/tmp/` when no PR is found.

**If the script was not available:**
```bash
PR_NUM=$(gh pr view --json number -q .number 2>/dev/null || ghe pr view --json number -q .number 2>/dev/null || echo "")
if [ -n "$PR_NUM" ]; then
  OUTPUT_DIR="/tmp/pr-review-${PR_NUM}"
else
  OUTPUT_DIR="/tmp"
fi
mkdir -p "$OUTPUT_DIR"
```

**Note on GHE:** For repos hosted on `github.a8c.com`, the `ghe` CLI is used (requires SOCKS5 proxy). The `review-scope.py` script handles this automatically by detecting the remote URL.

## ReviewOutputBuilder API

```python
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '../scripts'))
from review_output_simple import ReviewOutputBuilder

builder = ReviewOutputBuilder(pr_id=PR_ID, reviewer="REVIEWER_NAME")
```

**Core methods:**
- `builder.add_issue(severity, title, file, description, recommendation, category="general", line=<required>, confidence=0.9)` - Add diff-anchored finding (line is required)
- `builder.add_observation(file, note, category="general")` - Add file-level note (no line, doesn't affect verdict)
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

**When using `/tmp/` directly** (no PR number detected), append a timestamp to avoid collisions: `{reviewer}-review-{YYYYMMDD-HHMMSS}.json` and `.md`.

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

## Project-Specific Knowledge

Before reviewing, search for and READ project-specific documentation:

```bash
find . -type f \( -name "CLAUDE.md" -o -name "*.md" \) -path "*/.claude/*" 2>/dev/null | head -20
```

Look for: `CLAUDE.md`, `.claude/skills/`, `.claude/docs/`, ADRs, architecture docs. Read and apply project-specific standards before generic patterns. **Project standards override generic patterns.** Apply project conventions before your own domain expertise. This is **exploration** — it informs your review but is not itself reviewable.

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
