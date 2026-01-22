# Guides

Comprehensive guides for using and understanding the pirategoat-tools plugin capabilities.

---

## Rich Feedback Loops

### [FALSE-POSITIVE-HANDLING-GUIDE.md](./FALSE-POSITIVE-HANDLING-GUIDE.md)

**Complete guide for agents on handling false positives from static analysis tools.**

**Topics covered:**
- Philosophy: Patterns vs. vulnerabilities
- Decision tree for scanner findings
- Agent response templates (real issue, suspected FP, clear FP)
- Common false positive scenarios
- Suppression best practices
- Agent checklist
- Tracking and documentation
- Communication with users

**Use when:** Agent encounters security scanner, linter, or coverage findings that might be false positives.

---

### [REAL-EXAMPLE-ANALYSIS.md](./REAL-EXAMPLE-ANALYSIS.md)

**Real-world example of false positive analysis from WooCommerce security scan.**

**Analyzes:** SQL injection pattern detected by Semgrep in `class-wc-admin-api-keys-table-list.php`

**Shows:**
- Why scanner flagged it (correct pattern detection)
- Why it's actually safe (multiple sanitization layers)
- Why it's still non-ideal (should use `$wpdb->prepare()`)
- How agent should classify it (suspected false positive)
- Recommended actions (suppress with docs OR refactor)

**Use when:** Learning how to analyze context around scanner findings.

---

## Contributing

When adding new guides:

1. Place in `plugins/pirategoat-tools/docs/guides/`
2. Use descriptive filename
3. Add entry to this README
4. Link from relevant agent documentation
5. Include real examples where possible

---

## Related Documentation

- **Agent Specs:** `../../agents/`
- **Scripts:** `../../scripts/`
- **Skills:** `../../skills/`
- **Research:** `../research/` (proposals and analysis)
- **Progress:** `../progress/` (implementation logs)
- **Plans:** `../plans/` (implementation plans)
