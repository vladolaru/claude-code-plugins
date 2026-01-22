# Real False Positive Example from WooCommerce Scan

## Finding from Semgrep

**File:** `class-wc-admin-api-keys-table-list.php`
**Lines:** 260, 265
**Rule:** `php.lang.security.injection.tainted-sql-string`
**Severity:** HIGH
**CWE:** CWE-89 (SQL Injection)

---

## Code Flagged by Scanner

```php
// Line 255-256: Building $search variable
if (!empty($_REQUEST['s'])) {
    $search = "AND description LIKE '%" . esc_sql($wpdb->esc_like(wc_clean(wp_unslash($_REQUEST['s'])))) . "%' ";
    // WPCS: input var okay, CSRF ok.
}

// Line 260: First flagged usage
$keys = $wpdb->get_results(
    "SELECT key_id, user_id, description, permissions, truncated_key, last_access
     FROM {$wpdb->prefix}woocommerce_api_keys
     WHERE 1 = 1 {$search}" .
    $wpdb->prepare('ORDER BY key_id DESC LIMIT %d OFFSET %d;', $per_page, $offset),
    ARRAY_A
); // WPCS: unprepared SQL ok.

// Line 265: Second flagged usage
$count = $wpdb->get_var(
    "SELECT COUNT(key_id)
     FROM {$wpdb->prefix}woocommerce_api_keys
     WHERE 1 = 1 {$search};"
); // WPCS: unprepared SQL ok.
```

---

## Analysis: FALSE POSITIVE ✅

### Why Semgrep Flagged This

Semgrep detected:
1. User input from `$_REQUEST['s']`
2. Concatenated into SQL string via `$search` variable
3. No `$wpdb->prepare()` used at point of concatenation

**Pattern detection is correct** - this IS user input in SQL without prepare().

---

### Why It's Actually Safe

**Multiple layers of sanitization:**

1. **`wp_unslash($_REQUEST['s'])`** - Removes slashes added by WordPress
2. **`wc_clean()`** - WooCommerce sanitization (removes HTML, trims whitespace)
3. **`$wpdb->esc_like()`** - Escapes SQL LIKE wildcards (%, _)
4. **`esc_sql()`** - Escapes SQL special characters (quotes, backslashes)

**Code flow:**
```
User Input → wp_unslash() → wc_clean() → esc_like() → esc_sql() → SQL
```

**Developer acknowledgment:**
- Comment: `// WPCS: input var okay, CSRF ok.`
- Comment: `// WPCS: unprepared SQL ok.`
- Developers explicitly reviewed and approved this pattern

---

### Is This Best Practice?

**No.** Even though safe, better approach:

```php
// BETTER: Use $wpdb->prepare() throughout
if (!empty($_REQUEST['s'])) {
    $search_term = wc_clean(wp_unslash($_REQUEST['s']));
    $search = $wpdb->prepare(
        "AND description LIKE %s",
        '%' . $wpdb->esc_like($search_term) . '%'
    );
}

$keys = $wpdb->get_results(
    $wpdb->prepare(
        "SELECT * FROM {$wpdb->prefix}woocommerce_api_keys WHERE 1 = 1 {$search} ORDER BY key_id DESC LIMIT %d OFFSET %d",
        $per_page,
        $offset
    ),
    ARRAY_A
);
```

**Why better:**
- Single sanitization point (clearer)
- Uses WordPress recommended `$wpdb->prepare()`
- Easier to audit
- No scanner warnings

---

## Agent Decision Process

### Step 1: Initial Detection
```
🚨 Scanner Alert: SQL Injection detected
Confidence: 100% (pattern exists)
```

### Step 2: Context Analysis
```
✓ Reading 10 lines before/after
✓ Found sanitization: esc_sql($wpdb->esc_like(wc_clean(wp_unslash())))
✓ Found developer comments: "WPCS: unprepared SQL ok"
✓ Multiple sanitization layers detected
```

### Step 3: Classification
```
⚠️ SUSPECTED FALSE POSITIVE
- Pattern: User input in SQL (confirmed)
- Mitigation: Multiple sanitization functions (confirmed)
- Developer acknowledgment: Yes (WPCS comments)
- Confidence: 70% safe, but non-standard approach
```

### Step 4: Agent Recommendation

```markdown
### ⚠️ SQL Injection Pattern (Suspected False Positive - Needs Review)

**GROUND TRUTH:** Semgrep detected SQL injection pattern at lines 260, 265

**Finding:**
User input from `$_REQUEST['s']` concatenated into SQL query.

**Mitigating Factors Found:**
✓ Input sanitized with `esc_sql()`, `$wpdb->esc_like()`, `wc_clean()`, `wp_unslash()`
✓ Developer comments indicate intentional: `// WPCS: unprepared SQL ok.`
✓ Multiple layers of defense

**Verdict:** LIKELY FALSE POSITIVE, but non-standard pattern

**Confidence:** 70% (safe due to sanitization, but best practice would use $wpdb->prepare())

**Recommendations:**
1. **If you trust existing sanitization:**
   - Add Semgrep suppression:
     ```php
     // Sanitized with esc_sql + esc_like + wc_clean
     // nosemgrep: php.lang.security.injection.tainted-sql-string
     ```

2. **For best practice (RECOMMENDED):**
   - Refactor to use `$wpdb->prepare()` throughout
   - See example in REAL-EXAMPLE-ANALYSIS.md
   - Eliminates scanner warnings
   - Clearer security posture

3. **Document decision:**
   - Add to `.semgrep/false-positives.md`
   - Include reasoning and review date
```

---

## Lessons for False Positive Handling

### 1. Scanner is Right About Pattern
✅ User input IS in SQL without prepare()
✅ Pattern detection is accurate

### 2. Context Determines Safety
✅ Sanitization functions prevent exploitation
✅ Developer review confirmed safe

### 3. Still Worth Fixing
⚠️ Even if safe, non-standard approach
⚠️ Better to use recommended `$wpdb->prepare()`
⚠️ Eliminates security questions

### 4. Documentation Matters
✅ WPCS comments show intentional decision
✅ But Semgrep doesn't read those comments
✅ Need Semgrep-specific suppression

---

## Action Items

**For WooCommerce maintainers:**

1. **Short-term:** Add Semgrep suppressions to documented safe patterns
   ```php
   // Input sanitized with esc_sql + esc_like + wc_clean (security reviewed 2026-01-22)
   // nosemgrep: php.lang.security.injection.tainted-sql-string
   ```

2. **Long-term:** Refactor to `$wpdb->prepare()` (best practice, removes warnings)

3. **Documentation:** Create `.semgrep/false-positives.md` tracking all suppressions

**For agents:**
- Report as "suspected false positive with mitigation"
- Explain the sanitization chain
- Recommend suppression OR refactoring
- Let user decide
