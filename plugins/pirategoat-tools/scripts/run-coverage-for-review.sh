#!/bin/bash
#
# Run Coverage Analysis for PR Review
#
# Executes test suites with coverage instrumentation and generates machine-readable
# coverage reports for review agents to consume as ground truth.
#
# Usage:
#   ./run-coverage-for-review.sh [output_directory]
#
# Default output: /tmp/coverage-results-$(date +%s)
#
# Implements: Proposal #5 (Rich Feedback Loops) - Phase 3

set -e

OUTPUT_DIR="${1:-/tmp/coverage-results-$(date +%s)}"
mkdir -p "$OUTPUT_DIR"

echo "📊 Running test coverage analysis..."
echo "Output directory: $OUTPUT_DIR"
echo ""

# Track overall status
COVERAGE_GENERATED=false

# ============================================================================
# Jest Coverage (JavaScript/TypeScript)
# ============================================================================

if [ -f "package.json" ] && grep -q '"jest"' package.json 2>/dev/null; then
    echo "→ Generating Jest coverage..."

    # Run Jest with coverage
    if npm test -- --coverage --coverageReporters=json --coverageReporters=lcov --coverageDirectory="$OUTPUT_DIR/jest-coverage" 2>&1 | tee "$OUTPUT_DIR/jest-coverage.log"; then
        echo "  ✅ Jest coverage generated"
        COVERAGE_GENERATED=true

        # Copy JSON summary if it exists
        if [ -f "$OUTPUT_DIR/jest-coverage/coverage-summary.json" ]; then
            cp "$OUTPUT_DIR/jest-coverage/coverage-summary.json" "$OUTPUT_DIR/jest-coverage-summary.json"
        fi
    else
        echo "  ⚠️  Jest coverage generation had issues (tests may have failed)"
        # Don't fail overall - coverage data might still be useful
    fi

    echo ""
fi

# ============================================================================
# PHPUnit Coverage (PHP)
# ============================================================================

if [ -f "phpunit.xml" ] || [ -f "phpunit.xml.dist" ]; then
    # Check if Xdebug or PCOV is available
    if php -m | grep -q -E 'xdebug|pcov'; then
        echo "→ Generating PHPUnit coverage..."

        if phpunit --coverage-clover "$OUTPUT_DIR/phpunit-coverage.xml" --coverage-html "$OUTPUT_DIR/phpunit-coverage-html" 2>&1 | tee "$OUTPUT_DIR/phpunit-coverage.log"; then
            echo "  ✅ PHPUnit coverage generated"
            COVERAGE_GENERATED=true
        else
            echo "  ⚠️  PHPUnit coverage generation had issues (tests may have failed)"
        fi
    else
        echo "⚠️  PHPUnit coverage skipped - Xdebug or PCOV not available"
        echo "   Install Xdebug or PCOV to generate PHP coverage"
    fi

    echo ""
fi

# ============================================================================
# Playwright Coverage (E2E)
# ============================================================================

if [ -f "playwright.config.ts" ] || [ -f "playwright.config.js" ]; then
    echo "→ Generating Playwright coverage (if configured)..."

    # Check if coverage is configured in playwright.config
    if grep -q "coverage" playwright.config.* 2>/dev/null; then
        if npx playwright test --reporter=json 2>&1 | tee "$OUTPUT_DIR/playwright-coverage.log"; then
            echo "  ✅ Playwright coverage generated"
            COVERAGE_GENERATED=true

            # Check for V8 coverage output
            if [ -d ".nyc_output" ] || [ -d "coverage" ]; then
                cp -r .nyc_output "$OUTPUT_DIR/" 2>/dev/null || true
                cp -r coverage/coverage-final.json "$OUTPUT_DIR/playwright-coverage.json" 2>/dev/null || true
            fi
        else
            echo "  ⚠️  Playwright coverage generation had issues"
        fi
    else
        echo "  ℹ️  Playwright coverage not configured in playwright.config"
    fi

    echo ""
fi

# ============================================================================
# Summary
# ============================================================================

echo "============================================"
echo "Coverage Analysis Complete"
echo "============================================"
echo ""
echo "Results written to: $OUTPUT_DIR/"
echo ""

ls -lh "$OUTPUT_DIR/"

echo ""
if $COVERAGE_GENERATED; then
    echo "✅ Coverage reports generated"
    echo ""
    echo "Coverage files:"
    echo "  - $OUTPUT_DIR/jest-coverage-summary.json (Jest, if applicable)"
    echo "  - $OUTPUT_DIR/phpunit-coverage.xml (PHPUnit, if applicable)"
    echo "  - $OUTPUT_DIR/*.log (execution logs)"
    exit 0
else
    echo "⚠️  No coverage generated"
    echo ""
    echo "Possible reasons:"
    echo "  - No test frameworks configured"
    echo "  - PHP coverage requires Xdebug or PCOV"
    echo "  - Tests failed during execution"
    exit 1
fi
