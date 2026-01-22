#!/bin/bash
#
# Run Security Scanners for PR Review
#
# Executes security scanners (Semgrep) and generates machine-readable
# findings for review agents to consume as ground truth.
#
# Usage:
#   ./run-security-scanners-for-review.sh [output_directory]
#
# Default output: /tmp/security-results-$(date +%s)
#
# Implements: Proposal #5 (Rich Feedback Loops) - Phase 4

set -e

OUTPUT_DIR="${1:-/tmp/security-results-$(date +%s)}"
mkdir -p "$OUTPUT_DIR"

echo "🔒 Running security scanners..."
echo "Output directory: $OUTPUT_DIR"
echo ""

# Track overall status
SCANS_RUN=false

# ============================================================================
# Semgrep (Multi-language Security Scanner)
# ============================================================================

if command -v semgrep &> /dev/null; then
    echo "→ Running Semgrep security scan..."

    # Run semgrep with security rulesets
    # --config=auto uses language-specific security rules
    if semgrep --config=auto --json --output="$OUTPUT_DIR/semgrep-results.json" . 2>&1 | tee "$OUTPUT_DIR/semgrep.log"; then
        echo "  ✅ Semgrep scan complete"
        SCANS_RUN=true
    else
        # Semgrep may exit non-zero if findings are present
        if [ -f "$OUTPUT_DIR/semgrep-results.json" ]; then
            echo "  ⚠️  Semgrep found security issues"
            SCANS_RUN=true
        else
            echo "  ❌ Semgrep scan failed"
        fi
    fi

    echo ""
else
    echo "⚠️  Semgrep not installed"
    echo "   Install: pip install semgrep  OR  brew install semgrep"
    echo ""
fi

# ============================================================================
# PHPCS Security (WordPress/PHP - via PHPCS if available)
# ============================================================================

# Note: PHPCS with WordPress-Extra already includes security checks
# Those results are captured in Phase 2 (Linter integration)
# No need to duplicate here

# ============================================================================
# Bandit (Python Security Scanner - Optional)
# ============================================================================

if command -v bandit &> /dev/null && find . -name "*.py" -not -path "*/node_modules/*" -not -path "*/vendor/*" | head -1 | grep -q .; then
    echo "→ Running Bandit (Python security)..."

    if bandit -r . -f json -o "$OUTPUT_DIR/bandit-results.json" 2>&1 | tee "$OUTPUT_DIR/bandit.log" || true; then
        if [ -f "$OUTPUT_DIR/bandit-results.json" ]; then
            echo "  ✅ Bandit scan complete"
            SCANS_RUN=true
        fi
    fi

    echo ""
fi

# ============================================================================
# Summary
# ============================================================================

echo "============================================"
echo "Security Scanning Complete"
echo "============================================"
echo ""
echo "Results written to: $OUTPUT_DIR/"
echo ""

ls -lh "$OUTPUT_DIR/"

echo ""
if $SCANS_RUN; then
    echo "✅ Security scans completed"
    echo ""
    echo "Scan results:"
    echo "  - $OUTPUT_DIR/semgrep-results.json (if Semgrep available)"
    echo "  - $OUTPUT_DIR/bandit-results.json (if Bandit available and Python files present)"
    echo "  - $OUTPUT_DIR/*.log (execution logs)"
    exit 0
else
    echo "⚠️  No security scanners available"
    echo ""
    echo "Install security scanners:"
    echo "  - Semgrep: pip install semgrep  OR  brew install semgrep"
    echo "  - Bandit (Python): pip install bandit"
    exit 1
fi
