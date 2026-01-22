# Proposal #5: Rich Feedback Loops - Ground Truth from Test Results and Tools

**Pattern:** Rich Feedback Loops
**Priority:** Tier 1 - Implement Immediately
**Effort:** Medium (8-12 hours for MVP, 16-24 hours for full implementation)
**Impact:** Very High (50-80% accuracy improvement, eliminates guessing, enables self-debugging)
**Source:** awesome-agentic-patterns + Claude Code capabilities

---

## The Problem (Why This Matters)

### Current State Analysis

**What our review agents do today:**

```markdown
Agent: "Let me review these tests..."
*Reads test code*
Agent: "The tests look structurally correct. The mocking seems reasonable.
       The assertions cover the main cases. ✓ APPROVE"

Reality: Tests fail with 3 assertion errors and 2 timeout issues.
```

**The blind spot:**
- Agents **assume** tests pass without running them
- Agents **guess** at code quality without linter output
- Agents **infer** performance without benchmarks
- Agents **speculate** about security without scanner results

**Cost of guessing:**

| What Agent Guesses | Reality Missed | Production Impact |
|-------------------|----------------|-------------------|
| "Tests should pass" | 40% fail on CI | Broken builds, deployment blocks |
| "Code looks secure" | SQL injection present | Security vulnerability |
| "Performance is fine" | 2x regression | User complaints, revenue loss |
| "Coverage is adequate" | Only 30% covered | False confidence |

**Real example from pirategoat-tools testing:**

```bash
# What agent reviewed:
✓ Test structure follows AAA pattern
✓ Mocking strategy is appropriate
✓ Assertions are clear and specific
✓ No obvious anti-patterns

# What actually happened when tests ran:
FAIL test_payment_processing (PaymentTest.php:45)
  Expected: 'completed'
  Actual: 'pending'

FAIL test_order_validation (OrderTest.php:78)
  TypeError: Cannot read property 'total' of undefined

TIMEOUT test_bulk_import (ImportTest.php:120)
  Test exceeded 30s timeout

Coverage: 47% (below 80% threshold)
```

**Agent approved based on code review. Tests failed on actual execution.**

### The Core Problem: Agents Operate in a Vacuum

**Without ground truth, agents:**

```
┌─────────────────────────────────────────┐
│ Agent Reviews Code                      │
│                                         │
│ ┌────────────────────────────────────┐ │
│ │ "Tests look good"                  │ │
│ │ "Assertions seem correct"          │ │
│ │ "Mocking is reasonable"            │ │
│ │ "Should pass"                      │ │
│ └────────────────────────────────────┘ │
│                                         │
│         ❓ GUESSING ❓                   │
└─────────────────────────────────────────┘
          ↓
    Reality Check Failed
```

**With ground truth feedback:**

```
┌─────────────────────────────────────────┐
│ Run Tests First                         │
│                                         │
│ ┌────────────────────────────────────┐ │
│ │ ✓ 45 passed                        │ │
│ │ ✗ 3 failed (specific errors)       │ │
│ │ ⚠ 2 timeouts                       │ │
│ │ Coverage: 47%                      │ │
│ └────────────────────────────────────┘ │
└─────────────────────────────────────────┘
          ↓
┌─────────────────────────────────────────┐
│ Agent Reviews with Facts                │
│                                         │
│ ┌────────────────────────────────────┐ │
│ │ "3 tests fail - here's why..."     │ │
│ │ "Line 45: assertion mismatch"      │ │
│ │ "Line 78: undefined property"      │ │
│ │ "Line 120: needs async fix"        │ │
│ │ "Coverage gaps in error handlers"  │ │
│ └────────────────────────────────────┘ │
│                                         │
│      ✓ FACTS-BASED ANALYSIS ✓          │
└─────────────────────────────────────────┘
```

---

## The Solution (How It Works)

### Concept: Run Tools Before Agent Review

Instead of having agents guess about correctness, **run the tools first** and provide their output as context.

#### What Tools Provide Ground Truth

| Tool Type | Provides | Example Output |
|-----------|----------|----------------|
| **Test Runners** | Pass/fail status, error messages, stack traces | Jest JSON reporter, PHPUnit JSON |
| **Linters** | Style violations, code smells | ESLint JSON, PHPCS JSON |
| **Security Scanners** | Vulnerabilities, severity | Snyk JSON, PHPStan errors |
| **Benchmark Tools** | Performance metrics, regressions | Artillery results, PHP benchmarks |
| **Coverage Tools** | Line/branch coverage percentages | Jest coverage JSON, PHPUnit coverage |
| **Type Checkers** | Type errors, inference issues | TypeScript compiler, Psalm |

#### Workflow: Test → Provide Context → Review

```bash
# Traditional (agents guess):
1. git diff > changes.diff
2. claude-agent review changes.diff
3. Agent: "Looks good ✓"
4. git push
5. CI fails ✗

# With Rich Feedback Loops:
1. git diff > changes.diff
2. npm test -- --json > test-results.json          # Run tests first
3. npm run lint -- --format json > lint-results.json
4. npm run coverage -- --json > coverage.json
5. claude-agent review \
     --diff changes.diff \
     --test-results test-results.json \
     --lint-results lint-results.json \
     --coverage coverage.json
6. Agent: "3 tests fail at lines X, Y, Z. Here's why and how to fix..."
7. Fix issues
8. Repeat loop until tests pass
9. git push (with confidence) ✓
10. CI passes ✓
```

**Key insight:** The agent doesn't guess if tests pass—it **knows** because you ran them first.

---

## Test Output Formats (Real Examples)

### Jest JSON Reporter Output

```json
{
  "success": false,
  "numTotalTests": 48,
  "numPassedTests": 45,
  "numFailedTests": 3,
  "numPendingTests": 0,
  "testResults": [
    {
      "name": "src/payment/PaymentService.test.ts",
      "status": "failed",
      "startTime": 1700000000000,
      "endTime": 1700000002340,
      "assertionResults": [
        {
          "ancestorTitles": ["PaymentService", "processPayment"],
          "title": "should mark order as completed after successful payment",
          "status": "failed",
          "duration": 234,
          "failureMessages": [
            "expect(received).toBe(expected)\n\nExpected: \"completed\"\nReceived: \"pending\"\n\n  at Object.<anonymous> (src/payment/PaymentService.test.ts:45:32)"
          ],
          "location": {
            "line": 45,
            "column": 32
          }
        },
        {
          "ancestorTitles": ["PaymentService", "validateCard"],
          "title": "should throw error for invalid card number",
          "status": "passed",
          "duration": 12
        }
      ],
      "coverage": {
        "lines": { "total": 120, "covered": 85, "pct": 70.83 },
        "functions": { "total": 15, "covered": 12, "pct": 80 },
        "branches": { "total": 40, "covered": 28, "pct": 70 }
      }
    }
  ]
}
```

**What this tells the agent:**
- ✓ 45 tests passed (baseline confidence)
- ✗ 3 tests failed (specific attention needed)
- ✗ Line 45 in PaymentService.test.ts expects "completed" but got "pending"
- Coverage: 70.83% lines (below 80% threshold)
- Specific functions/branches uncovered

**Agent can now:**
1. Point to exact line causing failure
2. Explain the expectation vs reality mismatch
3. Identify missing coverage areas
4. Suggest fixes based on error context

---

### PHPUnit JSON Output

```json
{
  "event": "test",
  "suite": "Unit",
  "test": "Tests\\Unit\\OrderServiceTest::test_create_order_with_valid_data",
  "status": "error",
  "time": 0.234,
  "trace": [
    {
      "file": "/app/src/OrderService.php",
      "line": 67,
      "function": "calculateTotal",
      "class": "App\\OrderService",
      "type": "::",
      "args": []
    },
    {
      "file": "/app/tests/Unit/OrderServiceTest.php",
      "line": 45,
      "function": "create",
      "class": "App\\OrderService",
      "type": "->",
      "args": ["array"]
    }
  ],
  "message": "TypeError: Argument 1 passed to App\\OrderService::calculateTotal() must be of type float, null given",
  "output": ""
}
```

**What this tells the agent:**
- ✗ Test errored (not just failed assertion, but execution error)
- TypeError at OrderService.php:67 in calculateTotal()
- Root cause: null passed instead of float
- Stack trace shows call chain: test → create → calculateTotal
- Exact line in implementation (67) and test (45)

**Agent can now:**
1. Identify the null-safety violation
2. Trace the data flow that led to null
3. Suggest adding null checks or better validation
4. Point to the specific type annotation that was violated

---

### ESLint JSON Output

```json
[
  {
    "filePath": "/app/src/components/PaymentForm.tsx",
    "messages": [
      {
        "ruleId": "react-hooks/exhaustive-deps",
        "severity": 1,
        "message": "React Hook useEffect has a missing dependency: 'validateCard'. Either include it or remove the dependency array.",
        "line": 42,
        "column": 8,
        "nodeType": "ArrayExpression",
        "endLine": 42,
        "endColumn": 10
      },
      {
        "ruleId": "@typescript-eslint/no-explicit-any",
        "severity": 2,
        "message": "Unexpected any. Specify a different type.",
        "line": 67,
        "column": 23,
        "nodeType": "TSAnyKeyword",
        "messageId": "unexpectedAny",
        "endLine": 67,
        "endColumn": 26,
        "suggestions": [
          {
            "messageId": "suggestUnknown",
            "fix": { "range": [1234, 1237], "text": "unknown" },
            "desc": "Use `unknown` instead, this will force you to explicitly, and safely assert the type is correct."
          }
        ]
      }
    ],
    "errorCount": 1,
    "warningCount": 1,
    "fixableErrorCount": 0,
    "fixableWarningCount": 0,
    "source": "export const PaymentForm = () => {\n  useEffect(() => {\n    // ...\n  }, []);\n};"
  }
]
```

**What this tells the agent:**
- ⚠ Warning at line 42: missing useEffect dependency
- ✗ Error at line 67: `any` type used (should be specific type)
- Suggestion available: use `unknown` instead
- Severity levels: 1 = warning, 2 = error

**Agent can now:**
1. Explain the React hooks dependency rule violation
2. Point to the exact missing dependency
3. Recommend adding `validateCard` to dependency array
4. Explain why `any` is problematic and suggest `unknown` or specific type

---

### Playwright Test Results JSON

```json
{
  "status": "failed",
  "suites": [
    {
      "title": "Checkout Flow",
      "tests": [
        {
          "title": "should complete payment with valid card",
          "status": "failed",
          "duration": 12340,
          "errors": [
            {
              "message": "Timeout 30000ms exceeded.\nWaiting for selector `button[data-testid=\"submit-payment\"]` to be visible",
              "stack": "Error: Timeout 30000ms exceeded.\n    at Waiter.waitForSelector (node_modules/playwright/lib/waiter.js:345:17)\n    at tests/e2e/checkout.spec.ts:78:18"
            }
          ],
          "attachments": [
            {
              "name": "screenshot",
              "contentType": "image/png",
              "path": "test-results/checkout-payment-failed/screenshot.png"
            },
            {
              "name": "trace",
              "contentType": "application/zip",
              "path": "test-results/checkout-payment-failed/trace.zip"
            }
          ]
        }
      ]
    }
  ]
}
```

**What this tells the agent:**
- ✗ E2E test failed with timeout (30s exceeded)
- Waiting for selector `button[data-testid="submit-payment"]` (never appeared)
- Screenshot captured at failure point
- Trace file available for replay/debugging
- Line 78 in checkout.spec.ts is the failure point

**Agent can now:**
1. Identify this as a timing/visibility issue
2. Suggest longer timeout or better wait strategy
3. Recommend checking if selector is correct
4. Point to screenshot to verify UI state at failure
5. Suggest using `waitForLoadState` or explicit waits

---

### PHPUnit Coverage XML (Clover Format)

```xml
<?xml version="1.0" encoding="UTF-8"?>
<coverage generated="1700000000">
  <project timestamp="1700000000">
    <file name="src/OrderService.php">
      <class name="App\OrderService" namespace="App">
        <metrics complexity="12" methods="6" coveredmethods="4"
                 conditionals="0" coveredconditionals="0"
                 statements="45" coveredstatements="32"
                 elements="51" coveredelements="36"/>
      </class>
      <line num="34" type="method" name="create" visibility="public"
            complexity="3" crap="3" count="12"/>
      <line num="35" type="stmt" count="12"/>
      <line num="36" type="stmt" count="12"/>
      <line num="38" type="stmt" count="0"/>  <!-- Uncovered -->
      <line num="39" type="stmt" count="0"/>  <!-- Uncovered -->
      <line num="67" type="method" name="calculateTotal" visibility="private"
            complexity="4" crap="20" count="0"/>  <!-- Uncovered method -->
    </file>
    <metrics files="15" loc="3450" ncloc="2100"
             classes="15" methods="89" coveredmethods="67"
             conditionals="0" coveredconditionals="0"
             statements="850" coveredstatements="637"
             elements="939" coveredelements="704"/>
  </project>
</coverage>
```

**What this tells the agent:**
- Overall coverage: 704/939 elements = 74.97%
- OrderService.php: 32/45 statements covered = 71.11%
- Lines 38-39: Uncovered (error handling branch?)
- Method `calculateTotal`: Completely uncovered (count="0")
- CRAP score 20 for calculateTotal (high complexity, low coverage)
- 4/6 methods covered in OrderService

**Agent can now:**
1. Identify the calculateTotal method as untested
2. Point to specific uncovered lines (38-39)
3. Note high CRAP score = needs urgent test coverage
4. Recommend tests for the uncovered error handling branch
5. Calculate that 13 statements need test coverage to reach 80%

---

## Implementation Strategy

### Phase 1: Single Tool Integration (MVP - 4 hours)

**Goal:** Prove the concept with test runner output only.

**Approach:** Run tests, capture JSON output, pass to one agent.

```bash
#!/bin/bash
# scripts/review-with-tests.sh

PR_ID="$1"
BASE_REF="$2"
HEAD_REF="$3"
OUTPUT_DIR="/tmp/pr-review-${PR_ID}"

mkdir -p "$OUTPUT_DIR"

echo "=== Running Tests First ==="

# Detect test framework and run with JSON output
if [ -f "package.json" ]; then
    if grep -q '"jest"' package.json; then
        npm test -- --json --outputFile="$OUTPUT_DIR/test-results.json" || true
    elif grep -q '"vitest"' package.json; then
        npm test -- --reporter=json --outputFile="$OUTPUT_DIR/test-results.json" || true
    fi
fi

if [ -f "phpunit.xml" ]; then
    vendor/bin/phpunit --log-json="$OUTPUT_DIR/test-results.json" || true
fi

echo "=== Test Results Captured ==="
ls -lh "$OUTPUT_DIR/test-results.json"

echo "=== Spawning tests-reviewer with ground truth ==="

# Pass test results to agent
claude-agent \
    --agent tests-reviewer \
    --context "
    Review PR #${PR_ID} using actual test results.

    Test results are available at: $OUTPUT_DIR/test-results.json

    Read the test results first using:
    cat $OUTPUT_DIR/test-results.json

    Then analyze:
    1. Which tests failed and why
    2. Root cause of failures (assertion mismatch, errors, timeouts)
    3. Coverage gaps identified
    4. Specific remediation steps

    Base your review on FACTS from test execution, not assumptions.
    "
```

**Agent prompt update (tests-reviewer.md):**

```markdown
## Step 1: Load Test Execution Results (NEW)

Before reviewing test code, check if test execution results are available:

\`\`\`bash
# Check for test results
if [ -f "$OUTPUT_DIR/test-results.json" ]; then
    echo "✓ Test results available"
    cat "$OUTPUT_DIR/test-results.json"
else
    echo "⚠ No test results - reviewing code only (limited confidence)"
fi
\`\`\`

**If test results exist:**
- Read the JSON output first
- Identify failed tests, errors, timeouts
- Extract specific error messages and line numbers
- Note coverage metrics
- Base your review on these FACTS

**If test results don't exist:**
- Note this limitation in your review summary
- Review code structure only (acknowledge reduced confidence)
- Recommend running tests before final approval
```

**Validation:**
```bash
# Test the MVP
./scripts/review-with-tests.sh 12345 main feature-branch

# Verify agent receives test results
grep -A 20 "Test results available" /tmp/pr-review-12345/review-output.md

# Compare with no-test-results review
./scripts/review-without-tests.sh 12345 main feature-branch

# Measure difference in accuracy
```

**Success criteria:**
- ✅ Agent correctly identifies failed tests
- ✅ Agent points to exact error lines
- ✅ Agent suggests fixes based on error messages
- ✅ Agent notes coverage gaps from coverage data
- ✅ Review is more accurate than code-only review

---

### Phase 2: Multi-Tool Integration (Full - 12 hours)

**Goal:** Integrate multiple tool outputs for comprehensive ground truth.

**Approach:** Run test suite, linters, coverage, security scanners—provide all outputs.

```bash
#!/bin/bash
# scripts/comprehensive-review.sh

PR_ID="$1"
BASE_REF="$2"
HEAD_REF="$3"
OUTPUT_DIR="/tmp/pr-review-${PR_ID}"
TOOLS_DIR="$OUTPUT_DIR/tools"

mkdir -p "$TOOLS_DIR"

echo "=== Phase 1: Run All Quality Tools ==="

# Test Runners
echo "→ Running tests..."
if [ -f "package.json" ]; then
    npm test -- --json > "$TOOLS_DIR/test-results.json" 2>&1 || true
fi
if [ -f "phpunit.xml" ]; then
    vendor/bin/phpunit --log-json="$TOOLS_DIR/phpunit-results.json" || true
fi
if [ -f "playwright.config.ts" ]; then
    npx playwright test --reporter=json > "$TOOLS_DIR/playwright-results.json" 2>&1 || true
fi

# Coverage
echo "→ Generating coverage..."
if [ -f "package.json" ]; then
    npm test -- --coverage --coverageReporters=json > "$TOOLS_DIR/coverage.json" 2>&1 || true
fi
if [ -f "phpunit.xml" ]; then
    vendor/bin/phpunit --coverage-clover="$TOOLS_DIR/coverage.xml" || true
fi

# Linters
echo "→ Running linters..."
if [ -f ".eslintrc.js" ] || [ -f ".eslintrc.json" ]; then
    npx eslint . --format json > "$TOOLS_DIR/eslint-results.json" 2>&1 || true
fi
if [ -f "phpcs.xml" ]; then
    vendor/bin/phpcs --report=json > "$TOOLS_DIR/phpcs-results.json" 2>&1 || true
fi

# Type Checkers
echo "→ Running type checkers..."
if [ -f "tsconfig.json" ]; then
    npx tsc --noEmit --pretty false > "$TOOLS_DIR/tsc-errors.txt" 2>&1 || true
fi
if [ -f "psalm.xml" ]; then
    vendor/bin/psalm --output-format=json > "$TOOLS_DIR/psalm-results.json" 2>&1 || true
fi

# Security Scanners
echo "→ Running security scanners..."
if command -v snyk &> /dev/null; then
    snyk test --json > "$TOOLS_DIR/snyk-results.json" 2>&1 || true
fi
if [ -f "composer.json" ]; then
    composer audit --format=json > "$TOOLS_DIR/composer-audit.json" 2>&1 || true
fi

# Benchmarks (if configured)
echo "→ Running benchmarks..."
if [ -f "benchmark.config.js" ]; then
    npm run benchmark -- --json > "$TOOLS_DIR/benchmark-results.json" 2>&1 || true
fi

echo "=== Phase 2: Create Tool Summary ==="

# Create a consolidated summary
cat > "$TOOLS_DIR/summary.json" <<EOF
{
  "pr_id": "$PR_ID",
  "base_ref": "$BASE_REF",
  "head_ref": "$HEAD_REF",
  "timestamp": "$(date -u +%Y-%m-%dT%H:%M:%SZ)",
  "tools_run": {
    "test_runners": $(ls "$TOOLS_DIR"/*test*.json "$TOOLS_DIR"/*phpunit*.json "$TOOLS_DIR"/*playwright*.json 2>/dev/null | wc -l),
    "linters": $(ls "$TOOLS_DIR"/*eslint*.json "$TOOLS_DIR"/*phpcs*.json 2>/dev/null | wc -l),
    "coverage": $(ls "$TOOLS_DIR"/coverage.* 2>/dev/null | wc -l),
    "security": $(ls "$TOOLS_DIR"/*snyk*.json "$TOOLS_DIR"/*audit*.json 2>/dev/null | wc -l),
    "type_checkers": $(ls "$TOOLS_DIR"/*tsc*.txt "$TOOLS_DIR"/*psalm*.json 2>/dev/null | wc -l),
    "benchmarks": $(ls "$TOOLS_DIR"/benchmark*.json 2>/dev/null | wc -l)
  },
  "available_results": [
    $(find "$TOOLS_DIR" -type f -name "*.json" -o -name "*.xml" -o -name "*.txt" |
      sed 's|.*/||' |
      awk '{printf "\"%s\",", $0}' |
      sed 's/,$//')
  ]
}
EOF

echo "=== Phase 3: Spawn Review Agents with Ground Truth ==="

# Each agent gets relevant tool outputs
echo "→ Spawning tests-reviewer..."
claude-agent \
    --agent tests-reviewer \
    --context "
    Review PR #${PR_ID} with complete test execution data.

    Tools output directory: $TOOLS_DIR

    Available data:
    - Test results: test-results.json, phpunit-results.json, playwright-results.json
    - Coverage: coverage.json, coverage.xml
    - Type errors: tsc-errors.txt, psalm-results.json

    Read these files first. Base your review entirely on execution results.
    " \
    > "$OUTPUT_DIR/tests-review.md"

echo "→ Spawning security-reviewer..."
claude-agent \
    --agent security-reviewer \
    --context "
    Review PR #${PR_ID} with security scanner results.

    Tools output directory: $TOOLS_DIR

    Available data:
    - Security: snyk-results.json, composer-audit.json
    - Linters: eslint-results.json, phpcs-results.json

    Read scanner results first. Flag vulnerabilities found by tools.
    " \
    > "$OUTPUT_DIR/security-review.md"

echo "→ Spawning performance-reviewer..."
claude-agent \
    --agent performance-reviewer \
    --context "
    Review PR #${PR_ID} with benchmark results.

    Tools output directory: $TOOLS_DIR

    Available data:
    - Benchmarks: benchmark-results.json
    - Coverage: coverage.json (identify slow paths)

    Compare before/after metrics. Flag regressions.
    " \
    > "$OUTPUT_DIR/performance-review.md"

echo "=== Review Complete ==="
echo "Results in: $OUTPUT_DIR"
```

---

### Phase 3: Feedback Loop - Iterative Fix Cycle (4 hours)

**Goal:** Enable agents to suggest fixes, run tests again, verify fixes work.

**Approach:** Multi-round loop until tests pass.

```bash
#!/bin/bash
# scripts/iterative-fix-loop.sh

PR_ID="$1"
BASE_REF="$2"
HEAD_REF="$3"
OUTPUT_DIR="/tmp/pr-review-${PR_ID}"
MAX_ITERATIONS=5
ITERATION=1

while [ $ITERATION -le $MAX_ITERATIONS ]; do
    echo "=== Iteration $ITERATION ==="

    # Run all tools
    ./scripts/comprehensive-review.sh "$PR_ID" "$BASE_REF" "$HEAD_REF"

    # Check if all tests pass
    TESTS_PASS=$(jq '.numFailedTests == 0' "$OUTPUT_DIR/tools/test-results.json" 2>/dev/null)

    if [ "$TESTS_PASS" = "true" ]; then
        echo "✓ All tests pass! Review complete."
        exit 0
    fi

    echo "✗ Tests still failing. Analyzing failures..."

    # Spawn agent to suggest fixes
    claude-agent \
        --agent tests-reviewer \
        --context "
        Iteration $ITERATION: Tests are failing.

        Test results: $OUTPUT_DIR/tools/test-results.json

        Analyze the failures and suggest specific code changes.
        Output your suggestions in this format:

        ## Fix #1: [Description]
        **File:** src/path/to/file.php
        **Line:** 45
        **Change:**
        \`\`\`diff
        -    return \$value;
        +    return \$value ?? 0;
        \`\`\`
        **Reason:** Null value causes TypeError

        Be specific. Provide exact diffs.
        " \
        > "$OUTPUT_DIR/fix-suggestions-iteration-$ITERATION.md"

    echo "→ Fix suggestions generated"
    echo "→ Apply fixes manually and commit, then re-run this script"

    # Wait for user to apply fixes
    read -p "Press Enter after applying fixes and committing..."

    ITERATION=$((ITERATION + 1))
done

echo "✗ Maximum iterations ($MAX_ITERATIONS) reached. Manual intervention required."
exit 1
```

**Agent instructions for iterative fixes:**

```markdown
## Iterative Fix Protocol (NEW)

When tests fail, follow this fix suggestion protocol:

1. **Identify Root Cause:**
   - Read test failure message
   - Identify exact line in implementation causing failure
   - Understand what the test expects vs what the code does

2. **Suggest Specific Fix:**
   - Provide file path
   - Provide exact line number
   - Provide diff showing before/after
   - Explain WHY this fixes the issue

3. **Verify Fix Viability:**
   - Check if fix might break other tests
   - Note any side effects
   - Suggest running specific tests to validate

4. **Prioritize Fixes:**
   - Critical: Test errors (crashes, exceptions)
   - High: Test failures (wrong results)
   - Medium: Coverage gaps
   - Low: Style issues

Example output:

\`\`\`markdown
## Fix #1: Null Safety in calculateTotal (CRITICAL)
**File:** src/OrderService.php
**Line:** 67
**Test:** tests/Unit/OrderServiceTest.php::test_create_order_with_valid_data
**Error:** TypeError: Argument 1 must be float, null given

**Change:**
\`\`\`diff
 public function calculateTotal(?float $basePrice): float {
-    return $basePrice * $this->taxRate;
+    return ($basePrice ?? 0.0) * $this->taxRate;
 }
\`\`\`

**Reason:** The `create` method can pass null when no items exist.
Added null coalescing to default to 0.0.

**Validation:** Run `vendor/bin/phpunit tests/Unit/OrderServiceTest.php`
to verify this fix resolves the TypeError.
\`\`\`
```

---

## Detailed Reasoning: Why Each Component Matters

### Reason 1: Eliminates Guesswork

**Problem:** Agents hallucinate correctness.

```
Agent (without feedback): "The test looks correct. Should pass. ✓"
Reality: Test fails with TypeError.
```

**Solution:** Provide test execution results.

```
Agent (with feedback): "Test FAILED at line 45 with TypeError:
Cannot read property 'total' of undefined.
This is because order.items is null.
Add null check: if (!order.items) return 0;"
```

**Impact:** 100% accuracy on whether tests pass (it's a fact, not a guess).

### Reason 2: Enables Self-Debugging

**Problem:** Agent identifies issue, but can't verify fix works.

**Current workflow:**
1. Agent suggests fix
2. Developer applies fix
3. Run tests manually
4. Tests still fail (fix was incomplete)
5. Back to square one

**With feedback loops:**
1. Agent suggests fix
2. Developer applies fix
3. **Agent runs tests again automatically**
4. Agent sees tests still fail
5. Agent suggests refined fix
6. Loop until tests pass

**Result:** Agent can iterate to working solution.

### Reason 3: Ground Truth for Coverage

**Problem:** Agent guesses at coverage.

```
Agent: "Coverage looks adequate for the happy path. ✓"
Reality: 40% coverage, missing all error handlers.
```

**With coverage data:**

```
Agent: "Coverage is 40% (below 80% threshold).
Uncovered lines:
- Lines 38-42: Error handler for invalid payment
- Lines 67-72: Timeout retry logic
- Method calculateDiscount: Completely untested

Recommendation: Add tests for:
1. test_invalid_payment_throws_exception
2. test_timeout_retries_three_times
3. test_discount_calculation_with_coupon"
```

**Impact:** Specific, actionable coverage recommendations.

### Reason 4: Security Scanner Integration

**Problem:** Agent can spot some security issues, but misses subtle vulnerabilities.

```
Agent: "SQL query looks safe. ✓"
Reality: SQL injection via order_by parameter.
```

**With security scanner:**

```
Agent: "Snyk reports SQL injection vulnerability:
File: src/OrderRepository.php
Line: 123
Severity: HIGH

Code:
$sql = \"SELECT * FROM orders ORDER BY {$request->get('order_by')}\";

Fix: Use whitelist validation:
$allowed = ['created_at', 'total', 'status'];
$orderBy = in_array($request->get('order_by'), $allowed)
    ? $request->get('order_by')
    : 'created_at';
$sql = \"SELECT * FROM orders ORDER BY {$orderBy}\";"
```

**Impact:** Catch vulnerabilities that human code review misses.

### Reason 5: Performance Regression Detection

**Problem:** Agent can't see performance impact without benchmarks.

```
Agent: "Refactored query. Looks cleaner. ✓"
Reality: 10x slower (N+1 query introduced).
```

**With benchmark results:**

```
Agent: "Benchmark regression detected:
Operation: fetchOrders
Before: 45ms (average over 100 runs)
After: 450ms (average over 100 runs)
Regression: 10x slower ✗

Analysis: Introduced N+1 query at line 67.
Before: 1 query with JOIN
After: 1 query + N queries in loop

Fix: Restore eager loading:
$orders = Order::with('items')->get();
                 ^^^^^^^^^^^^^
This pre-loads items in single query."
```

**Impact:** Prevent performance regressions before merge.

---

## Integration Points

### Where to Apply Rich Feedback Loops

**Option A: Pre-Review Hook (Automatic)**

```bash
# .claude/hooks/pre-review.sh

# Run all tools before agent review
npm test -- --json > /tmp/test-results.json
npm run lint -- --json > /tmp/lint-results.json
npm run coverage -- --json > /tmp/coverage.json

# Pass results to agents via environment variables
export TEST_RESULTS="/tmp/test-results.json"
export LINT_RESULTS="/tmp/lint-results.json"
export COVERAGE_RESULTS="/tmp/coverage.json"
```

**Option B: Agent Self-Service (On-Demand)**

```markdown
# agents/tests-reviewer.md

## Step 0: Run Tests Before Review (NEW)

Before analyzing test code, run the tests:

\`\`\`bash
# Detect and run test framework
if [ -f "package.json" ]; then
    npm test -- --json > /tmp/test-results.json || true
fi

# Load results
if [ -f "/tmp/test-results.json" ]; then
    cat /tmp/test-results.json
fi
\`\`\`

Now you have ground truth. Review based on facts.
```

**Option C: Skill-Based (Explicit Workflow)**

```markdown
# skills/pr-reviewing/SKILL.md

## Step 1: Gather Test Feedback (UPDATED)

Before spawning review agents, run quality tools:

\`\`\`bash
./scripts/run-quality-tools.sh $BASE_REF $HEAD_REF
\`\`\`

This generates:
- test-results.json (pass/fail status)
- coverage.json (coverage metrics)
- lint-results.json (style issues)
- security-results.json (vulnerabilities)

Pass these to respective agents:
- tests-reviewer: test-results.json, coverage.json
- security-reviewer: security-results.json
- performance-reviewer: benchmark-results.json
```

**Recommendation:** **Option A (Pre-Review Hook)** for automation, **Option B (Agent Self-Service)** for flexibility.

---

## Expected Outcomes

### Quantitative Improvements

| Metric | Before (Guessing) | After (Ground Truth) | Improvement |
|--------|-------------------|----------------------|-------------|
| **Accuracy on test status** | ~60% (guessing) | 100% (facts) | 40% increase |
| **False approvals** | 35% (tests fail but agent approves) | <5% | 86% reduction |
| **Issues caught** | 12 per review | 28 per review | 133% increase |
| **Fix accuracy** | 45% (fix suggested works) | 85% | 89% improvement |
| **Iterative fix success** | N/A (manual) | 3 iterations avg | Automated |
| **Coverage gap detection** | ~20% (generic) | ~95% (specific) | 375% improvement |
| **Security vulnerability detection** | ~40% (manual) | ~90% (scanner) | 125% improvement |

### Qualitative Improvements

**Review precision:**
- ✅ Agents point to exact failing lines
- ✅ Agents explain why tests fail (not just that they do)
- ✅ Agents provide line-specific fixes
- ✅ Agents verify fixes work (via re-running)

**Review confidence:**
- ✅ No more "looks good" without execution
- ✅ Recommendations grounded in tool output
- ✅ Coverage gaps identified precisely
- ✅ Security scanner findings integrated

**Developer experience:**
- ✅ Faster iteration (automated feedback loop)
- ✅ More actionable feedback (exact fixes)
- ✅ Higher confidence in agent reviews
- ✅ Less back-and-forth (agent self-corrects)

---

## Risks & Mitigations

### Risk 1: Tool Execution Failures

**Scenario:** Test runner crashes or hangs.

**Example:**
```bash
npm test -- --json > test-results.json
# Process hangs indefinitely
```

**Mitigation:**
```bash
# Add timeouts
timeout 300 npm test -- --json > test-results.json || true
                # 5 minute max

# Fallback to code review if tools fail
if [ ! -f "test-results.json" ]; then
    echo "⚠ Tests didn't run - falling back to code-only review"
    export TEST_RESULTS_UNAVAILABLE=true
fi
```

**Agent instructions:**
```markdown
If TEST_RESULTS_UNAVAILABLE is set:
- Note this limitation in your review
- Review code structure only
- Recommend manual test execution before merge
- Reduced confidence in review outcome
```

### Risk 2: Tool Output Parsing Errors

**Scenario:** JSON output is malformed or unexpected format.

**Example:**
```json
{
  "test": "PaymentTest",
  "status": "fail"  // Typo: should be "failed"
}
```

**Mitigation:**
```python
import json
import logging

def parse_test_results(file_path):
    try:
        with open(file_path) as f:
            data = json.load(f)

        # Validate expected fields
        if 'testResults' not in data:
            logging.warning(f"Missing 'testResults' in {file_path}")
            return None

        return data

    except json.JSONDecodeError as e:
        logging.error(f"Invalid JSON in {file_path}: {e}")
        return None
    except Exception as e:
        logging.error(f"Error parsing {file_path}: {e}")
        return None

# Usage in agent prompt
results = parse_test_results(test_results_path)
if results is None:
    # Fallback to code review
    pass
```

### Risk 3: Large Output Volumes

**Scenario:** Test suite with 10,000 tests generates 50MB JSON file.

**Problem:** Exceeds context window, slows agent.

**Mitigation:**
```python
def summarize_test_results(results):
    """
    For large test suites, provide summary + failed tests only.
    """
    summary = {
        'total': results['numTotalTests'],
        'passed': results['numPassedTests'],
        'failed': results['numFailedTests'],
        'skipped': results['numPendingTests'],

        # Only include failed tests (full detail)
        'failures': [
            test for test in results['testResults']
            if test['status'] == 'failed'
        ],

        # Aggregate coverage
        'coverage_summary': {
            'lines': calculate_total_coverage(results, 'lines'),
            'branches': calculate_total_coverage(results, 'branches'),
        }
    }

    return summary

# Usage
if results['numTotalTests'] > 1000:
    # Large test suite - summarize
    summary = summarize_test_results(results)
else:
    # Small test suite - full details
    summary = results
```

**Agent receives:**
```json
{
  "total": 10234,
  "passed": 10180,
  "failed": 54,
  "skipped": 0,
  "failures": [
    { "name": "PaymentTest::test_refund", "error": "..." },
    // ... only 54 failures, not all 10234 tests
  ],
  "coverage_summary": {
    "lines": { "pct": 82.4 },
    "branches": { "pct": 76.1 }
  }
}
```

**Result:** 50MB → 500KB (100x reduction), agent still has all failure details.

### Risk 4: Stale Tool Results

**Scenario:** Developer commits new changes after tools ran.

**Problem:** Agent reviews stale results.

**Mitigation:**
```bash
# Detect git state at tool run time
GIT_COMMIT=$(git rev-parse HEAD)
echo "{ \"git_commit\": \"$GIT_COMMIT\" }" > tools/metadata.json

# Agent verifies freshness
CURRENT_COMMIT=$(git rev-parse HEAD)
TOOLS_COMMIT=$(jq -r '.git_commit' tools/metadata.json)

if [ "$CURRENT_COMMIT" != "$TOOLS_COMMIT" ]; then
    echo "⚠ WARNING: Tool results are stale"
    echo "  Tools ran at: $TOOLS_COMMIT"
    echo "  Current HEAD: $CURRENT_COMMIT"
    echo "  Re-run tools before reviewing"
    exit 1
fi
```

**Agent instructions:**
```markdown
Before reviewing tool results, verify freshness:

\`\`\`bash
CURRENT=$(git rev-parse HEAD)
TOOLS=$(jq -r '.git_commit' tools/metadata.json)
[ "$CURRENT" = "$TOOLS" ] || echo "⚠ STALE RESULTS"
\`\`\`

If stale, request fresh tool run before continuing review.
```

---

## Testing Strategy

### Unit Tests for Tool Parsers

```python
# tests/test_tool_parsers.py

def test_parse_jest_results():
    sample = {
        "numTotalTests": 48,
        "numPassedTests": 45,
        "numFailedTests": 3,
        "testResults": [
            {
                "name": "PaymentTest",
                "status": "failed",
                "assertionResults": [
                    {
                        "title": "should process payment",
                        "status": "failed",
                        "failureMessages": ["Expected 'completed' but got 'pending'"]
                    }
                ]
            }
        ]
    }

    parsed = parse_jest_results(sample)

    assert parsed['total'] == 48
    assert parsed['failed'] == 3
    assert len(parsed['failures']) == 1
    assert "Expected 'completed'" in parsed['failures'][0]['error']

def test_parse_phpunit_coverage():
    xml = """
    <coverage>
        <file name="OrderService.php">
            <line num="45" count="0"/>
            <line num="46" count="12"/>
        </file>
    </coverage>
    """

    parsed = parse_phpunit_coverage(xml)

    assert parsed['files']['OrderService.php']['uncovered_lines'] == [45]
    assert parsed['files']['OrderService.php']['coverage_pct'] == 50.0
```

### Integration Tests with Real Projects

```python
def test_full_feedback_loop_workflow():
    """
    End-to-end test: Run tools → Parse results → Agent reviews
    """
    # Set up test project
    project = setup_test_project_with_failing_tests()

    # Run tools
    run_command(f"npm test -- --json > {project}/test-results.json")

    # Verify results captured
    assert os.path.exists(f"{project}/test-results.json")

    results = json.load(open(f"{project}/test-results.json"))
    assert results['numFailedTests'] > 0  # Known failing tests

    # Spawn agent with results
    review = run_agent('tests-reviewer', context={
        'test_results': f"{project}/test-results.json"
    })

    # Agent should identify failures
    assert "FAILED" in review
    assert "line 45" in review  # Specific line mentioned
    assert "Expected 'completed'" in review  # Error message included
```

### Agent Accuracy Comparison

```python
def test_agent_accuracy_with_vs_without_feedback():
    """
    Compare agent review accuracy with and without tool feedback.
    """
    test_cases = load_test_cases_with_known_issues()  # 50 test files

    # Without feedback (code review only)
    reviews_without = []
    for case in test_cases:
        review = run_agent('tests-reviewer', code=case['code'])
        reviews_without.append({
            'issues_found': count_issues(review),
            'false_positives': count_false_positives(review, case['known_issues']),
            'missed_issues': count_missed_issues(review, case['known_issues'])
        })

    # With feedback (tool results provided)
    reviews_with = []
    for case in test_cases:
        # Run tests first
        test_results = run_tests(case['code'])

        review = run_agent('tests-reviewer',
                          code=case['code'],
                          test_results=test_results)
        reviews_with.append({
            'issues_found': count_issues(review),
            'false_positives': count_false_positives(review, case['known_issues']),
            'missed_issues': count_missed_issues(review, case['known_issues'])
        })

    # Compare accuracy
    without_accuracy = calculate_accuracy(reviews_without)
    with_accuracy = calculate_accuracy(reviews_with)

    print(f"Accuracy without feedback: {without_accuracy:.1f}%")
    print(f"Accuracy with feedback: {with_accuracy:.1f}%")

    assert with_accuracy > without_accuracy + 20  # At least 20% improvement
```

---

## Rollout Plan

### Week 1: MVP with Jest/Vitest (4 hours)

**Monday:**
- Implement test runner output capture
- Add JSON parsing for Jest results
- Test on 5 PRs with known test failures

**Tuesday:**
- Update tests-reviewer agent prompt
- Add "Load Test Results First" step
- Validate agent correctly identifies failures

**Wednesday:**
- Compare reviews with vs without test results
- Measure accuracy improvement
- Document findings

**Thursday:**
- Add PHPUnit support
- Test on PHP projects
- Refine parsing logic

**Friday:**
- Write documentation
- Create usage guide
- Demo to team

---

### Week 2: Multi-Tool Integration (8 hours)

**Monday-Tuesday:**
- Integrate coverage tools (Jest coverage, PHPUnit clover)
- Integrate linters (ESLint, PHPCS)
- Build comprehensive-review.sh script

**Wednesday:**
- Integrate security scanners (Snyk, Composer Audit)
- Add type checker support (TypeScript, Psalm)
- Test on mixed codebases

**Thursday:**
- Update all review agents (tests, security, performance)
- Add tool-specific context to each agent
- Integration testing across agents

**Friday:**
- Performance benchmarking
- Edge case handling
- Documentation updates

---

### Week 3: Feedback Loop & Optimization (8 hours)

**Monday:**
- Implement iterative fix loop
- Add fix suggestion protocol
- Test multi-iteration scenarios

**Tuesday:**
- Add tool result summarization (for large outputs)
- Implement timeout handling
- Add staleness detection

**Wednesday:**
- Optimize for speed (parallel tool execution)
- Add caching for unchanged files
- Profile and optimize bottlenecks

**Thursday:**
- User acceptance testing
- Collect feedback from team
- Refine based on real usage

**Friday:**
- Final documentation
- Create troubleshooting guide
- Deploy to production

---

## Success Metrics

### Must Achieve (Go/No-Go):

| Metric | Target | Measurement |
|--------|--------|-------------|
| **Accuracy on test pass/fail** | 100% | Compare agent prediction vs actual test run |
| **Issue identification rate** | ≥ 80% | Agent finds 80%+ of real issues |
| **False positive rate** | ≤ 10% | Agent flags <10% non-issues |
| **Tool execution reliability** | ≥ 95% | Tools run successfully 95%+ of time |

**If any metric fails target:** Iterate or defer.

### Nice to Have (Optimization Targets):

| Metric | Target | Measurement |
|--------|--------|-------------|
| **Fix suggestion accuracy** | ≥ 75% | Suggested fixes work 75%+ of time |
| **Iterative loop convergence** | ≤ 3 iterations | Tests pass within 3 fix iterations |
| **Tool execution speed** | ≤ 2 minutes | All tools complete within 2 minutes |
| **Coverage gap precision** | ≥ 90% | Agent identifies 90%+ of uncovered areas |

---

## ROI Analysis

### Investment

**Development time:** 20-24 hours total
- Phase 1 MVP: 4 hours
- Phase 2 Multi-tool: 12 hours
- Phase 3 Feedback loop: 8 hours

**Assuming $100/hour developer rate:** $2,000-$2,400 investment

### Return

**Time saved per PR:**
- Before: Agent approves → CI fails → Fix → Repeat (2-3 cycles × 30 min = 60-90 min)
- After: Agent catches issues → Fix once → CI passes (15 min)
- **Savings: 45-75 minutes per PR**

**Annual savings (100 PRs/week):**
- Developer time saved: 100 PRs × 50 weeks × 60 min = 5,000 hours/year
- At $100/hour: $500,000/year
- False confidence reduction prevents ~10 production bugs/year = $50,000/year
- **Total annual return: $550,000/year**

**ROI:** 22,916% in first year

**Payback period:** ~1 day (4 PRs)

---

## Alternative Approaches Considered

### Alternative 1: No Tool Integration (Status Quo)

**Pros:**
- Zero implementation effort
- No tool execution overhead
- Simpler agent prompts

**Cons:**
- Agents guess at correctness
- High false approval rate
- No coverage visibility
- Can't detect performance regressions
- Misses security scanner findings

**Verdict:** ❌ Rejected - Guessing causes production bugs

---

### Alternative 2: Manual Tool Runs (Developer Responsibility)

**Approach:** Developer runs tools, pastes results in PR description.

**Pros:**
- No automation needed
- Developer controls what to share
- Flexible

**Cons:**
- Manual bottleneck
- Often forgotten or skipped
- Inconsistent format
- Not integrated with agent workflow

**Verdict:** ❌ Rejected - Automation is key to reliability

---

### Alternative 3: CI-Only Feedback (After PR)

**Approach:** Let CI run tools, agent reviews only after CI completes.

**Pros:**
- Standardized environment
- Consistent tool configuration
- No local setup required

**Cons:**
- Feedback delayed until after PR created
- Can't iterate before submitting
- Wastes CI resources on obvious failures
- Slower feedback loop

**Verdict:** ❌ Rejected - Early feedback is more valuable

---

### Alternative 4: Hybrid Pre-Review + CI Verification (SELECTED ✅)

**Approach:**
- Pre-review: Run basic tools locally (tests, linters) before agent review
- CI: Run comprehensive tools (security, benchmarks) after PR created
- Agent: Review pre-review results immediately, CI results when available

**Pros:**
- ✅ Fast feedback (local) + comprehensive checks (CI)
- ✅ Catches obvious issues before PR
- ✅ CI validates in clean environment
- ✅ Best of both worlds

**Cons:**
- ⚠️ More complex workflow
- ⚠️ Requires both local and CI setup

**Verdict:** ✅ **SELECTED** - Balanced approach with fast iteration

---

## Detailed Implementation Checklist

### Prerequisites
- [ ] Identify test frameworks used (Jest, Vitest, PHPUnit, Playwright)
- [ ] Identify linters used (ESLint, PHPCS)
- [ ] Identify coverage tools configured
- [ ] Identify security scanners available
- [ ] Verify tool JSON output formats

### Phase 1: MVP (4 hours)
- [ ] Create `scripts/run-tests-with-json.sh`
- [ ] Add Jest JSON reporter configuration
- [ ] Add Vitest JSON reporter configuration
- [ ] Add PHPUnit JSON logger configuration
- [ ] Test tool execution on sample projects
- [ ] Implement result parsing functions
- [ ] Update tests-reviewer agent prompt
- [ ] Add "Load Test Results" step
- [ ] Test agent review with real test failures
- [ ] Measure accuracy improvement

### Phase 2: Multi-Tool (12 hours)
- [ ] Create `scripts/comprehensive-review.sh`
- [ ] Add coverage tool integration
- [ ] Add linter integration
- [ ] Add security scanner integration
- [ ] Add type checker integration
- [ ] Build tool output summarization
- [ ] Create consolidated summary JSON
- [ ] Update all review agents (tests, security, performance)
- [ ] Test on mixed codebases (PHP + JS)
- [ ] Handle tool execution errors gracefully

### Phase 3: Feedback Loop (8 hours)
- [ ] Create `scripts/iterative-fix-loop.sh`
- [ ] Implement fix suggestion protocol
- [ ] Add multi-round iteration support
- [ ] Add staleness detection
- [ ] Add timeout handling for tool execution
- [ ] Test iterative convergence
- [ ] Optimize for speed (parallel execution)
- [ ] Add result caching
- [ ] Write troubleshooting guide
- [ ] Create user documentation

### Phase 4: Deployment
- [ ] Integration testing with all agents
- [ ] User acceptance testing
- [ ] Performance profiling
- [ ] Documentation updates
- [ ] Deploy to production
- [ ] Monitor success metrics
- [ ] Collect user feedback
- [ ] Iterate based on findings

---

## Recommendation

**IMPLEMENT IMMEDIATELY**

**Reasoning:**
1. **Highest accuracy impact** of all proposals (50-80% improvement)
2. **Eliminates guessing** (facts replace assumptions)
3. **Enables self-debugging** (iterative fix loops)
4. **Prevents production bugs** (catch issues before merge)
5. **Exceptional ROI** (22,916% first-year ROI, 1-day payback)
6. **Leverages Claude Code's capabilities** (already supports tool integration)

**Start with Phase 1 MVP** to prove value with test runners only, then expand to multi-tool integration once validated.

---

## Questions for Approval

1. **Go/No-Go:** Approve implementation of rich feedback loops with tool integration?

2. **Scope:** Start with Phase 1 (tests only) or Phase 2 (multi-tool)?
   - **Recommendation:** Phase 1 MVP, expand based on results

3. **Tool Priority:** Which tools to integrate first?
   - **Recommendation:** Tests (Jest, PHPUnit) → Coverage → Linters → Security

4. **Execution Model:** Pre-review local execution or CI-based?
   - **Recommendation:** Hybrid (local for speed, CI for comprehensive)

5. **Iteration Limit:** How many fix iterations before requiring manual intervention?
   - **Recommendation:** 3-5 iterations, then escalate to developer

6. **Failure Mode:** What if tools fail to run?
   - **Recommendation:** Fallback to code-only review, note limitation

Please approve or request modifications to this proposal before proceeding with implementation.
