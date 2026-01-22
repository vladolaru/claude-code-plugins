#!/bin/bash
#
# Run Tests for PR Review with JSON Output
#
# Executes all test suites and generates machine-readable JSON results
# for review agents to consume as ground truth.
#
# Usage:
#   ./run-tests-for-review.sh [output_directory]
#
# Default output: /tmp/test-results-$(date +%s)
#
# Implements: Proposal #5 (Rich Feedback Loops) - Phase 1

set -e

OUTPUT_DIR="${1:-/tmp/test-results-$(date +%s)}"
mkdir -p "$OUTPUT_DIR"

echo "🧪 Running test suites with JSON output..."
echo "Output directory: $OUTPUT_DIR"
echo ""

# Track overall status
ALL_PASSED=true

# ============================================================================
# Jest (JavaScript/TypeScript)
# ============================================================================

if [ -f "package.json" ] && grep -q '"jest"' package.json 2>/dev/null; then
    echo "→ Running Jest tests..."

    if npm test -- --json --outputFile="$OUTPUT_DIR/jest-results.json" 2>&1 | tee "$OUTPUT_DIR/jest.log"; then
        echo "  ✅ Jest tests passed"
    else
        echo "  ❌ Jest tests failed"
        ALL_PASSED=false
    fi

    # Also generate coverage if configured
    if grep -q '"coverage"' package.json 2>/dev/null; then
        echo "→ Generating Jest coverage..."
        npm test -- --coverage --json 2>&1 > "$OUTPUT_DIR/jest-coverage.json" || true
    fi

    echo ""
fi

# ============================================================================
# PHPUnit (PHP)
# ============================================================================

if [ -f "phpunit.xml" ] || [ -f "phpunit.xml.dist" ]; then
    echo "→ Running PHPUnit tests..."

    if phpunit --log-json "$OUTPUT_DIR/phpunit-results.json" 2>&1 | tee "$OUTPUT_DIR/phpunit.log"; then
        echo "  ✅ PHPUnit tests passed"
    else
        echo "  ❌ PHPUnit tests failed"
        ALL_PASSED=false
    fi

    # Generate coverage if Xdebug is available
    if php -m | grep -q xdebug; then
        echo "→ Generating PHPUnit coverage..."
        phpunit --coverage-clover "$OUTPUT_DIR/coverage.xml" 2>&1 || true
    fi

    echo ""
fi

# ============================================================================
# Playwright (E2E)
# ============================================================================

if [ -f "playwright.config.ts" ] || [ -f "playwright.config.js" ]; then
    echo "→ Running Playwright tests..."

    if npx playwright test --reporter=json 2>&1 | tee "$OUTPUT_DIR/playwright-results.json"; then
        echo "  ✅ Playwright tests passed"
    else
        echo "  ❌ Playwright tests failed"
        ALL_PASSED=false
    fi

    echo ""
fi

# ============================================================================
# Summary
# ============================================================================

echo "============================================"
echo "Test Execution Complete"
echo "============================================"
echo ""
echo "Results written to: $OUTPUT_DIR/"
echo ""

ls -lh "$OUTPUT_DIR/"

echo ""
if $ALL_PASSED; then
    echo "✅ All test suites PASSED"
    exit 0
else
    echo "❌ Some test suites FAILED"
    echo ""
    echo "Review test results in:"
    echo "  - $OUTPUT_DIR/*-results.json (machine-readable)"
    echo "  - $OUTPUT_DIR/*.log (human-readable)"
    exit 1
fi
