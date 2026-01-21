# JSON Output Integration: Testing & PR-Reviewer Update

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Validate JSON output integration in 5 agents, then update pr-reviewer/reconciliator to aggregate JSON outputs.

**Architecture:** Three-phase approach - validate individual agents first, then integrate aggregation, then end-to-end test.

**Tech Stack:** Python ReviewOutputBuilder (plugins/pirategoat-tools/lib/review_output_simple.py), Claude Code agents, JSON schema validation

---

## Phase 1: Validate Individual Agent JSON Output

**Goal:** Test each of the 5 updated agents to ensure they correctly use ReviewOutputBuilder and output valid JSON.

### Task 1.1: Create Test Harness

**Files:**
- Create: `test-samples/json-output-test/test-pr-simple.diff`
- Create: `scripts/test-agent-json.sh`

**Step 1: Create simple test PR diff**

Create a minimal diff with one security issue for testing:

```bash
mkdir -p test-samples/json-output-test

cat > test-samples/json-output-test/test-pr-simple.diff << 'EOF'
diff --git a/src/UserHandler.php b/src/UserHandler.php
--- a/src/UserHandler.php
+++ b/src/UserHandler.php
@@ -10,5 +10,8 @@
 class UserHandler {
+    public function delete_user() {
+        $id = $_GET['user_id'];
+        $wpdb->query("DELETE FROM users WHERE id = $id");
+    }
 }
EOF
```

**Step 2: Create agent testing script**

```bash
cat > scripts/test-agent-json.sh << 'SCRIPT'
#!/bin/bash
# Test agent JSON output integration
#
# Usage: ./test-agent-json.sh <agent-name> <test-diff-file>

set -e

AGENT_NAME="${1:-security-reviewer}"
TEST_DIFF="${2:-test-samples/json-output-test/test-pr-simple.diff}"
OUTPUT_DIR="/tmp/test-agent-json-$$"

echo "Testing agent: $AGENT_NAME"
echo "Test diff: $TEST_DIFF"
echo "Output dir: $OUTPUT_DIR"

# Create output dir
mkdir -p "$OUTPUT_DIR"

# Copy test diff to output dir (agent may need it)
cp "$TEST_DIFF" "$OUTPUT_DIR/test.diff"

# Spawn agent with test context
# NOTE: This is a placeholder - actual agent spawning depends on your environment
echo "
To test manually:
1. Spawn $AGENT_NAME agent
2. Provide context:
   - PR_ID: test-123
   - OUTPUT_DIR: $OUTPUT_DIR
   - Test diff at: $OUTPUT_DIR/test.diff
3. Check for outputs:
   - $OUTPUT_DIR/<agent>-review.json
   - $OUTPUT_DIR/<agent>-review.md
"

# Wait for agent completion
read -p "Press enter when agent completes..."

# Validate outputs exist
echo ""
echo "=== Validating outputs ==="

JSON_FILE="$OUTPUT_DIR/${AGENT_NAME//-reviewer/}-review.json"
MD_FILE="$OUTPUT_DIR/${AGENT_NAME//-reviewer/}-review.md"

if [ -f "$JSON_FILE" ]; then
    echo "✓ JSON file exists: $JSON_FILE"

    # Validate JSON syntax
    if python3 -m json.tool "$JSON_FILE" > /dev/null 2>&1; then
        echo "✓ JSON is valid"

        # Show summary
        echo ""
        echo "=== JSON Summary ==="
        python3 -c "
import json
with open('$JSON_FILE') as f:
    data = json.load(f)
print(f\"Reviewer: {data['reviewer']}\")
print(f\"Verdict: {data['verdict']}\")
print(f\"Total issues: {data['summary']['total_issues']}\")
print(f\"By severity: {data['summary']['by_severity']}\")
"
    else
        echo "✗ JSON is invalid!"
        exit 1
    fi
else
    echo "✗ JSON file missing: $JSON_FILE"
    exit 1
fi

if [ -f "$MD_FILE" ]; then
    echo "✓ Markdown file exists: $MD_FILE"
    echo ""
    echo "=== Markdown Preview (first 30 lines) ==="
    head -30 "$MD_FILE"
else
    echo "✗ Markdown file missing: $MD_FILE"
    exit 1
fi

echo ""
echo "=== Test passed! ==="
echo "Output files available in: $OUTPUT_DIR"
SCRIPT

chmod +x scripts/test-agent-json.sh
```

**Step 3: Commit test harness**

```bash
git add test-samples/json-output-test/ scripts/test-agent-json.sh
git commit -m "test: add JSON output validation harness"
```

---

### Task 1.2: Test security-reviewer

**Step 1: Run security-reviewer on test case**

Manually spawn security-reviewer with:
- PR_ID: "test-123"
- OUTPUT_DIR: "/tmp/test-security-json"
- Provide test diff with SQL injection

**Step 2: Validate outputs**

```bash
./scripts/test-agent-json.sh security-reviewer
```

Expected:
- ✓ security-review.json exists and is valid
- ✓ security-review.md exists
- ✓ JSON contains critical SQL injection issue
- ✓ Verdict is "block"

**Step 3: Fix any issues found**

If agent doesn't use builder correctly:
- Update security-reviewer.md instructions
- Re-test
- Iterate until working

---

### Task 1.3: Test architecture-reviewer

**Step 1: Create architecture test case**

```bash
cat > test-samples/json-output-test/test-pr-architecture.diff << 'EOF'
diff --git a/src/OrderProcessor.php b/src/OrderProcessor.php
--- a/src/OrderProcessor.php
+++ b/src/OrderProcessor.php
@@ -5,3 +5,7 @@
 class OrderProcessor {
+    public function process($order) {
+        $gateway = new PaymentGateway();  // Direct instantiation
+        $gateway->charge($order->total);
+    }
 }
EOF
```

**Step 2: Run architecture-reviewer**

Spawn with test diff, validate outputs using test-agent-json.sh

**Step 3: Fix issues if found**

---

### Task 1.4: Test performance-reviewer

**Step 1: Create performance test case**

```bash
cat > test-samples/json-output-test/test-pr-performance.diff << 'EOF'
diff --git a/includes/class-product-list.php b/includes/class-product-list.php
--- a/includes/class-product-list.php
+++ b/includes/class-product-list.php
@@ -10,5 +10,9 @@
 public function render_products() {
+    $products = get_posts(['post_type' => 'product', 'posts_per_page' => -1]);
+    foreach ($products as $product) {
+        $meta = get_post_meta($product->ID);  // N+1 query
+    }
 }
EOF
```

**Step 2: Run and validate**

---

### Task 1.5: Test tests-reviewer

**Step 1: Create test quality test case**

```bash
cat > test-samples/json-output-test/test-pr-tests.diff << 'EOF'
diff --git a/tests/UserTest.php b/tests/UserTest.php
--- a/tests/UserTest.php
+++ b/tests/UserTest.php
@@ -5,3 +5,6 @@
 class UserTest extends TestCase {
+    public function test_user_creation() {
+        $user = new User();  // No assertions!
+    }
 }
EOF
```

**Step 2: Run and validate**

---

### Task 1.6: Test patterns-reviewer

**Step 1: Create patterns test case**

```bash
cat > test-samples/json-output-test/test-pr-patterns.diff << 'EOF'
diff --git a/src/helpers/format-price.php b/src/helpers/format-price.php
--- a/src/helpers/format-price.php
+++ b/src/helpers/format-price.php
@@ -1,3 +1,7 @@
 <?php
+// New helper - different naming from existing formatCurrency(), formatAmount()
+function format_price($amount) {
+    return '$' . number_format($amount, 2);
+}
EOF
```

**Step 2: Run and validate**

---

## Phase 2: Update pr-reviewer/reconciliator for JSON Aggregation

**Goal:** Update review-reconciliator to read JSON outputs and aggregate them.

### Task 2.1: Update review-reconciliator to Read JSON

**Files:**
- Modify: `plugins/pirategoat-tools/agents/review-reconciliator.md`

**Step 1: Add JSON import and aggregation instructions**

Add to review-reconciliator.md after "Context You Will Receive":

```markdown
## JSON-Based Reconciliation (REQUIRED)

**You MUST read JSON outputs from specialist agents for structured aggregation.**

### Setup

```python
import sys
import os
import json

# Import ReviewOutputBuilder from lib
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '../lib'))
from review_output_simple import ReviewOutputBuilder

# Initialize aggregated builder
builder = ReviewOutputBuilder(pr_id=PR_ID, reviewer="reconciliator")
```

### Reading Agent JSON Outputs

```python
# Read each agent's JSON output
agent_outputs = {}
agent_names = ['security', 'architecture', 'performance', 'tests', 'patterns']

for agent_name in agent_names:
    json_path = f"{output_dir}/{agent_name}-review.json"

    if os.path.exists(json_path):
        with open(json_path, 'r') as f:
            agent_outputs[agent_name] = json.load(f)
    else:
        print(f"⚠️ {agent_name} review not found")
```

### Aggregating Issues

```python
# Aggregate all issues from all agents
all_issues = []

for agent_name, output in agent_outputs.items():
    for issue in output.get('issues', []):
        # Add source attribution
        issue['source_agent'] = agent_name
        all_issues.append(issue)

# Sort by severity (critical > high > medium > low)
severity_order = {'critical': 0, 'high': 1, 'medium': 2, 'low': 3, 'info': 4}
all_issues.sort(key=lambda x: severity_order.get(x['severity'], 5))

# Add to aggregated builder
for issue in all_issues:
    builder.add_issue(
        severity=issue['severity'],
        title=f"[{issue['source_agent']}] {issue['title']}",
        file=issue['file'],
        line=issue.get('line'),
        description=issue['description'],
        recommendation=issue['recommendation'],
        category=issue.get('category', 'general'),
        confidence=issue.get('confidence', 0.9),
        source_agent=issue['source_agent']  # Extra field
    )
```

### Output Aggregated Review

```python
# Generate aggregated outputs
json_output = builder.to_json()
markdown_output = builder.to_markdown()

# Write both files
Write(f"{output_dir}/reconciled.json", json_output)
Write(f"{output_dir}/reconciled.md", markdown_output)
```
```

**Step 2: Update file reading logic**

Change from:
```python
# Old approach
Read(f"{output_dir}/security.md")  # Parse markdown manually
```

To:
```python
# New approach
# Read JSON (already structured, no parsing needed)
```

**Step 3: Commit reconciliator update**

```bash
git add plugins/pirategoat-tools/agents/review-reconciliator.md
git commit -m "feat(reconciliator): read JSON outputs for aggregation"
```

---

### Task 2.2: Test Reconciliator with Multiple Agents

**Step 1: Run all 5 agents on test case**

Spawn all 5 agents in parallel on test PR:
- security-reviewer
- architecture-reviewer
- performance-reviewer
- tests-reviewer
- patterns-reviewer

Each outputs to same output_dir.

**Step 2: Run reconciliator**

Spawn review-reconciliator with same output_dir.

**Expected outputs:**
- `reconciled.json` - Aggregated JSON with issues from all 5 agents
- `reconciled.md` - Human-readable summary

**Step 3: Validate aggregation**

```bash
# Check reconciled JSON
python3 -c "
import json
with open('/tmp/test-output/reconciled.json') as f:
    data = json.load(f)
print(f\"Total aggregated issues: {data['summary']['total_issues']}\")
print(f\"Sources: {set(i['source_agent'] for i in data['issues'])}\")
"
```

Expected:
- Issues from all 5 agents present
- Sorted by severity
- Source attribution correct

**Step 4: Fix issues if found**

---

## Phase 3: End-to-End Testing on Real PR

**Goal:** Test complete workflow on actual WooCommerce PR.

### Task 3.1: Test on Small Real PR

**Step 1: Find small WooCommerce PR**

```bash
cd /Users/vladolaru/Work/a8c/woocommerce-develop
git log --oneline --no-merges HEAD~50..HEAD | grep -i "fix\|feat" | head -5
```

Pick a small PR (50-150 lines).

**Step 2: Run full review workflow**

Using pr-reviewing skill:
1. Spawn all 5 specialist agents
2. Let each output JSON + Markdown
3. Spawn reconciliator to aggregate
4. Check final outputs

**Step 3: Validate all outputs**

Check that these files exist and are valid:
```
/tmp/pr-review-<id>/
├── security-review.json ✓
├── security-review.md ✓
├── architecture-review.json ✓
├── architecture-review.md ✓
├── performance-review.json ✓
├── performance-review.md ✓
├── tests-review.json ✓
├── tests-review.md ✓
├── patterns-review.json ✓
├── patterns-review.md ✓
├── reconciled.json ✓
└── reconciled.md ✓
```

**Step 4: Validate JSON schema compliance**

```bash
# Validate each JSON against schema
for file in /tmp/pr-review-*/\*-review.json; do
    echo "Validating $file"
    python3 -m json.tool "$file" > /dev/null && echo "✓ Valid" || echo "✗ Invalid"
done
```

**Step 5: Compare Markdown quality**

Read markdown outputs, verify they're human-readable and contain:
- Summary with severity counts
- Issues grouped by severity
- Specific file locations and line numbers
- Clear recommendations
- Verdict

---

### Task 3.2: Document Testing Results

**Files:**
- Create: `docs/progress/2026-01-21-json-integration-validation.md`

**Step 1: Document test results**

Include:
- Which agents tested
- Sample JSON output snippets
- Sample Markdown output
- Issues found and fixes applied
- Schema validation results
- Performance impact (if any)

**Step 2: Update SESSION-HANDOFF.md**

Update integration status from:
```
- ⏳ Agents don't automatically output JSON yet
```

To:
```
- ✅ All 5 agents output JSON + Markdown (v1.9.0)
- ✅ Reconciliator aggregates JSON outputs
```

**Step 3: Commit documentation**

```bash
git add docs/progress/2026-01-21-json-integration-validation.md docs/SESSION-HANDOFF.md
git commit -m "docs: JSON integration validation results"
```

---

## Success Criteria

**Phase 1 (Individual agents):**
- ✅ Each of 5 agents outputs valid JSON
- ✅ Each of 5 agents outputs readable Markdown
- ✅ JSON matches schema (can be parsed)
- ✅ Verdicts auto-calculated correctly
- ✅ No errors during agent execution

**Phase 2 (Aggregation):**
- ✅ Reconciliator reads JSON from all agents
- ✅ Aggregated JSON contains issues from all sources
- ✅ Source attribution correct (source_agent field)
- ✅ Reconciled markdown is clear and actionable

**Phase 3 (End-to-end):**
- ✅ Full PR review workflow produces 12 files (5 agents × 2 formats + reconciled × 2)
- ✅ All JSON files valid
- ✅ All Markdown files readable
- ✅ No regression in review quality
- ✅ Review process completes successfully

---

## Rollback Plan

If JSON integration causes issues:

**Step 1: Revert agent updates**

```bash
git revert <commit-hash>
```

**Step 2: Agents fall back to Markdown only**

Remove JSON output instructions, keep existing markdown format.

**Step 3: Investigate and fix**

Identify root cause:
- Import path issues?
- ReviewOutputBuilder bugs?
- Agent instruction clarity?

**Step 4: Re-implement with fixes**

---

## Estimated Time

- Task 1.1: Create test harness (30 min)
- Task 1.2-1.6: Test 5 agents (1 hour)
- Task 2.1: Update reconciliator (1 hour)
- Task 2.2: Test reconciliation (30 min)
- Task 3.1: End-to-end test (1 hour)
- Task 3.2: Documentation (30 min)

**Total: 4.5 hours**

---

## Dependencies

**Required:**
- plugins/pirategoat-tools/lib/review_output_simple.py (exists)
- All 5 agents updated with JSON instructions (done)
- Python 3 available

**Optional:**
- JSON schema validation tool (for schema compliance testing)
- jq (for JSON querying)

---

## Notes

**Testing philosophy:** Test each component individually before integration. Catch issues early at agent level before they propagate to aggregation.

**Conservative approach:** If any agent fails to produce JSON, that's okay - reconciliator should handle missing agents gracefully.

**Documentation:** Capture learnings about what works and what doesn't. This is the first time agents use structured output - expect iteration.

---

**Status:** Plan complete. Ready for execution.
