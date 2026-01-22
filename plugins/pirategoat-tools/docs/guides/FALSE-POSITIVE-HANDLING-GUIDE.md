# False Positive Handling Guide
## For Review Agents Using Ground Truth Tools

---

## Philosophy

**Scanner findings are ground truth about PATTERNS, not always about VULNERABILITIES.**

A scanner says: "I detected pattern X"
The agent must determine: "Is pattern X a real issue in this context?"

---

## Decision Tree for Scanner Findings

```
Scanner Finding Detected
    ↓
Is the pattern actually present? ─── NO ──→ Tool bug (rare, report)
    ↓ YES
    ↓
Is there mitigating context? ─────────────→ Check these:
    ↓                                       - Input already sanitized?
    ↓                                       - Constant, not user input?
    ↓                                       - Protected by capability check?
    ↓                                       - Inside test/mock code?
    ↓
    ↓── NO mitigating context ──→ REAL ISSUE (report to user)
    ↓
    ↓── YES, mitigating context found
        ↓
        POTENTIAL FALSE POSITIVE
        ↓
        Agent Decision:
        1. Report as "Possible false positive"
        2. Explain the mitigation
        3. Ask user to verify
        4. Suggest suppression if confirmed false

```

---

## Agent Response Templates

### For Likely Real Issue (No Mitigation Found)

```markdown
### 🚨 SQL Injection Detected (High Confidence)

**GROUND TRUTH:** Semgrep detected `tainted-sql-string` at line 156

**Pattern Found:**
```php
$wpdb->query("DELETE FROM users WHERE id = " . $_GET['user_id']);
```

**Analysis:**
- User input (`$_GET['user_id']`) flows directly to SQL
- No sanitization detected
- No use of `$wpdb->prepare()`

**Verdict:** CRITICAL - Real vulnerability
**Confidence:** 95% (scanner + manual verification)
**Recommendation:** Use `$wpdb->prepare("DELETE FROM users WHERE id = %d", $_GET['user_id'])`
```

---

### For Suspected False Positive (Mitigation Found)

```markdown
### ⚠️ SQL Injection Pattern Detected (Needs Verification)

**GROUND TRUTH:** Semgrep detected `tainted-sql-string` at line 203

**Pattern Found:**
```php
$wpdb->query("DELETE FROM {$wpdb->prefix}options WHERE id = " . $option_id);
```

**Mitigating Context Found:**
- `$option_id` comes from `absint()` on line 200 (sanitized to integer)
- Scanner doesn't track sanitization across lines

**Verdict:** POSSIBLE FALSE POSITIVE
**Confidence:** 60% (scanner detected pattern, but context suggests safe)
**Recommendation:**
1. Verify `$option_id` is always sanitized before this line
2. If confirmed safe, suppress with: `// nosemgrep: php.lang.security.injection.tainted-sql-string`
3. Add comment explaining why: `// $option_id sanitized with absint() above`
4. Consider refactoring to `$wpdb->prepare()` anyway (defense in depth)
```

---

### For Clear False Positive (Wrong Context)

```markdown
### ℹ️ Security Pattern in Test Code

**GROUND TRUTH:** Semgrep detected `tainted-sql-string` at line 45

**Pattern Found:**
```php
// In tests/test-order.php
$wpdb->query("DELETE FROM {$wpdb->prefix}posts WHERE ID = " . $test_post_id);
```

**Context:**
- File: `tests/test-order.php` (test file)
- Variable: `$test_post_id` is test fixture data, not user input
- Purpose: Test cleanup

**Verdict:** FALSE POSITIVE (test code, not production)
**Confidence:** 95%
**Recommendation:**
1. Suppress in test files: Add to `.semgrepignore`:
   ```
   tests/**
   ```
2. Or suppress inline with reason:
   ```php
   // Test cleanup - not user input
   // nosemgrep: php.lang.security.injection.tainted-sql-string
   ```
```

---

## Common False Positive Scenarios

### 1. Input Sanitized Earlier

**Pattern:** Scanner detects tainted input, but it was sanitized before use

**Example:**
```php
// Line 100: Sanitization
$safe_id = absint($_GET['id']);

// Line 105: Use (scanner flags this)
$wpdb->query("SELECT * FROM posts WHERE ID = " . $safe_id);
```

**Why False Positive:** Scanners often don't track sanitization across lines

**Resolution:**
- Verify sanitization is correct
- Still recommend `$wpdb->prepare()` (best practice)
- If keeping current code, suppress with documentation

---

### 2. Constants, Not User Input

**Pattern:** Scanner flags concatenation that looks like injection

**Example:**
```php
define('TABLE_PREFIX', 'wp_custom_');
$wpdb->query("SELECT * FROM " . TABLE_PREFIX . "data");
```

**Why False Positive:** TABLE_PREFIX is a constant, not user input

**Resolution:**
- Confirm it's truly a constant
- Suppress with reason: `// Constant, not user input`

---

### 3. Test/Mock Code

**Pattern:** Scanner flags test fixtures or mock data

**Example:**
```php
// In tests/
public function test_delete_order() {
    $wpdb->query("DELETE FROM orders WHERE id = " . $this->test_order_id);
}
```

**Why False Positive:** Test data, not production code

**Resolution:**
- Exclude test directories from scans
- Or suppress in test files

---

### 4. Already Protected by Capability Checks

**Pattern:** Scanner flags potential vulnerability, but access is protected

**Example:**
```php
// Only admins can reach this code
if (!current_user_can('manage_options')) {
    wp_die('Unauthorized');
}

// Scanner flags this, but only admins can execute
$wpdb->query("DELETE FROM {$wpdb->prefix}options WHERE id = " . $_POST['id']);
```

**Why Still Problematic:** Capability checks are NOT sufficient for SQL injection
- Admin accounts can be compromised
- Defense in depth requires sanitization regardless of capabilities

**Resolution:** Usually NOT a false positive - fix it anyway

---

## Suppression Best Practices

### ✅ Good Suppression

```php
/**
 * Delete custom table row.
 * $row_id is validated as integer by absint() on line 142.
 * Using direct concatenation here for performance (called in tight loop).
 * Confirmed safe by security review 2026-01-22.
 */
// nosemgrep: php.lang.security.injection.tainted-sql-string
$wpdb->query("DELETE FROM {$wpdb->prefix}custom WHERE id = " . $row_id);
```

**Why good:**
- Documents the sanitization
- Explains why current approach is kept
- Dated security review
- Clear reasoning

---

### ❌ Bad Suppression

```php
// nosemgrep
$wpdb->query("DELETE FROM users WHERE id = " . $_GET['id']);
```

**Why bad:**
- No explanation
- No verification documented
- Could be hiding real vulnerability

---

## Agent Checklist for Each Finding

When agent encounters a scanner finding:

```
□ Read the code at the flagged location
□ Read 10 lines before and after for context
□ Check if input is sanitized earlier
□ Check if variable is constant or hardcoded
□ Check if code is in test/mock files
□ Check if there's existing suppression comment
□ Grep for sanitization functions (absint, sanitize_*, esc_*)
□ Look for capability checks (not sufficient alone)

If mitigation found:
□ Verify mitigation is correct
□ Report as "possible false positive"
□ Explain mitigation to user
□ Ask user to verify
□ Recommend suppression with documentation

If no mitigation:
□ Report as real issue
□ High confidence
□ Provide fix recommendation
```

---

## Tracking False Positives

### Repository Documentation

Create `.semgrep/false-positives.md`:

```markdown
# Known False Positives

## SQL Injection Warnings

### class-wc-admin.php:162
- **Finding:** tainted-sql-string
- **Status:** False positive
- **Reason:** $option_id sanitized with absint() on line 158
- **Reviewed:** 2026-01-22 by @vladolaru
- **Suppression:** Inline comment

### wc-template-functions.php:3393
- **Finding:** tainted-sql-string
- **Status:** Under review
- **Reason:** Investigating if $post_id is always sanitized
- **Action:** Need to trace data flow
```

---

## Communication with User

### When Reporting Possible False Positive

```markdown
⚠️ **Note:** This finding might be a false positive. I detected mitigating factors:
- [Explain mitigation]

**Your action required:**
1. Verify the mitigation is correct
2. If confirmed safe, add suppression comment with reason
3. If uncertain, treat as real issue (safer)

**Remember:** Even if false positive, consider fixing anyway for defense in depth.
```

---

## Escalation

If agent is uncertain about false positive:

```markdown
🤔 **Uncertain Finding**

Scanner detected: [pattern]
Possible mitigation: [what I found]
But I'm not confident because: [reason for uncertainty]

**Recommended:**
- Manual security review by developer
- Test with actual exploit attempt
- Consult with security team if high-risk code

**For now:** Treating as REAL ISSUE (safer to be cautious)
```

---

## Summary

**Agent Philosophy:**
1. **Scanner findings = patterns detected (ground truth)**
2. **Context determines if pattern = vulnerability**
3. **When in doubt, report as real issue (safer)**
4. **False positives should be documented, not silently ignored**
5. **Suppression requires explanation**

**User Responsibility:**
- Verify suspected false positives
- Add suppression comments with reasoning
- Keep documentation updated
- Consider fixing even if false positive (defense in depth)
