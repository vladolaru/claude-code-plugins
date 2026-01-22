#!/bin/bash
#
# Run Linters for PR Review with JSON Output
#
# Executes linters (ESLint, PHPCS) and generates machine-readable JSON results
# for review agents to consume as ground truth.
#
# Usage:
#   ./run-linters-for-review.sh [output_directory]
#
# Default output: /tmp/lint-results-$(date +%s)
#
# Implements: Proposal #5 (Rich Feedback Loops) - Phase 2

set -e

OUTPUT_DIR="${1:-/tmp/lint-results-$(date +%s)}"
mkdir -p "$OUTPUT_DIR"

echo "🔍 Running linters with JSON output..."
echo "Output directory: $OUTPUT_DIR"
echo ""

# Track overall status
ALL_PASSED=true

# ============================================================================
# ESLint (JavaScript/TypeScript)
# ============================================================================

if [ -f ".eslintrc.js" ] || [ -f ".eslintrc.json" ] || [ -f ".eslintrc.yml" ] || grep -q '"eslint"' package.json 2>/dev/null; then
    echo "→ Running ESLint..."

    if npx eslint . --format json --output-file "$OUTPUT_DIR/eslint-results.json" 2>&1 | tee "$OUTPUT_DIR/eslint.log"; then
        echo "  ✅ ESLint passed"
    else
        echo "  ❌ ESLint found violations"
        ALL_PASSED=false
    fi

    echo ""
fi

# ============================================================================
# PHPCS (PHP Coding Standards)
# ============================================================================

if command -v phpcs &> /dev/null; then
    echo "→ Running PHPCS..."

    # Try WordPress-Extra standard first, fall back to PSR12
    if phpcs --standard=WordPress-Extra --report=json --report-file="$OUTPUT_DIR/phpcs-results.json" . 2>&1 | tee "$OUTPUT_DIR/phpcs.log"; then
        echo "  ✅ PHPCS passed"
    else
        # PHPCS failed with WordPress-Extra, check if it's because standard doesn't exist
        if ! phpcs -i | grep -q "WordPress-Extra"; then
            echo "  ℹ️  WordPress-Extra standard not found, trying PSR12..."
            if phpcs --standard=PSR12 --report=json --report-file="$OUTPUT_DIR/phpcs-results.json" . 2>&1 | tee "$OUTPUT_DIR/phpcs.log"; then
                echo "  ✅ PHPCS (PSR12) passed"
            else
                echo "  ❌ PHPCS found violations"
                ALL_PASSED=false
            fi
        else
            echo "  ❌ PHPCS found violations"
            ALL_PASSED=false
        fi
    fi

    echo ""
fi

# ============================================================================
# Prettier (Code Formatting - Optional)
# ============================================================================

if grep -q '"prettier"' package.json 2>/dev/null; then
    echo "→ Running Prettier check..."

    if npx prettier --check . 2>&1 | tee "$OUTPUT_DIR/prettier.log"; then
        echo "  ✅ Prettier formatting correct"
    else
        echo "  ❌ Prettier found formatting issues"
        # Don't fail overall for formatting (treat as warnings)
    fi

    echo ""
fi

# ============================================================================
# Summary
# ============================================================================

echo "============================================"
echo "Linter Execution Complete"
echo "============================================"
echo ""
echo "Results written to: $OUTPUT_DIR/"
echo ""

ls -lh "$OUTPUT_DIR/"

echo ""
if $ALL_PASSED; then
    echo "✅ All linters PASSED"
    exit 0
else
    echo "❌ Some linters found violations"
    echo ""
    echo "Review linter results in:"
    echo "  - $OUTPUT_DIR/*-results.json (machine-readable)"
    echo "  - $OUTPUT_DIR/*.log (human-readable)"
    exit 1
fi
