---
name: wordpress-backend-dev
description: Use when writing or reviewing WordPress plugin/theme PHP code, fixing PHPCS errors, resolving XSS/SQL injection/CSRF vulnerabilities, implementing i18n translations, or building AJAX/REST handlers - provides WPCS coding standards, security patterns, hooks API, database operations, and admin interface patterns.
---

# WordPress Backend Development

This skill provides comprehensive guidance for WordPress backend PHP development, covering coding standards, internationalization, security, and best practices.

## When to Use This Skill

Use this skill when:
- Writing PHP code for WordPress plugins or themes
- Reviewing WordPress PHP code for quality
- Implementing internationalization (i18n) for translatable strings
- Working with WordPress hooks (actions and filters)
- Handling database operations with WPDB
- Building REST API endpoints
- Implementing AJAX handlers
- Creating admin menu pages and settings screens
- Ensuring security in WordPress code

## Common Errors This Skill Addresses

If you encounter these errors, this skill has the fix:

| Error/Symptom | Solution Location |
|---------------|-------------------|
| `WordPress.Security.EscapeOutput` | security.md → Output Escaping |
| `WordPress.Security.NonceVerification` | security.md → Nonces |
| `WordPress.Security.ValidatedSanitizedInput` | security.md → Input Sanitization |
| `WordPress.DB.PreparedSQL` | database.md → Prepared Statements |
| `WordPress.WP.I18n` errors | i18n.md → Core Functions |
| `Translators comment is malformed` | i18n.md → Translator Comments |
| "Security check failed" | security.md → Nonces |
| "Permission denied" | security.md → Capability Checks |
| XSS vulnerability in code review | security.md → Output Escaping |
| SQL injection warning | database.md → Prepared Statements |
| CSRF vulnerability | security.md → Nonces |
| "Nonce verification failed" | security.md → AJAX Nonces |
| `rest_forbidden` error | rest-api-authentication.md |
| AJAX returns 0 or -1 | ajax.md → Common Issues |

## FORBIDDEN Patterns (Quick Reference)

These patterns cause security vulnerabilities, PHPCS failures, or code review rejections:

| ❌ WRONG | ✅ CORRECT | Issue |
|----------|-----------|-------|
| `echo $user_input;` | `echo esc_html( $user_input );` | XSS vulnerability |
| `$wpdb->query("WHERE id=$id")` | `$wpdb->prepare("WHERE id=%d", $id)` | SQL injection |
| `__('Hello ') . $name` | `sprintf(__('Hello %s'), $name)` | Breaks i18n |
| `if ($value == true)` | `if ( true === $value )` | Use Yoda conditions |
| `if($condition)` | `if ( $condition )` | Spaces inside parentheses |
| `extract( $args )` | `$title = $args['title'] ?? ''` | Security risk |
| `$_REQUEST['data']` | `$_POST['data']` or `$_GET['data']` | Cookie override attack |
| Form without nonce | `wp_nonce_field()` + `wp_verify_nonce()` | CSRF vulnerability |
| Action without cap check | `current_user_can( 'capability' )` | Privilege escalation |
| `__( 'Text', $domain )` | `__( 'Text', 'literal-domain' )` | Variable text domain |

**RULE 0 (SECURITY)**: Every handler MUST follow this pattern:
```php
// 1. Verify nonce
check_ajax_referer( 'action_name', 'nonce' );
// 2. Check capability
if ( ! current_user_can( 'manage_woocommerce' ) ) {
    wp_die( esc_html__( 'Permission denied.', 'text-domain' ) );
}
// 3. Sanitize ALL input
$id = absint( $_POST['id'] );
// 4. Escape ALL output
echo esc_html( $value );
```

## Required Resources

Before beginning development work, read the relevant reference files:

```
references/coding-standards.md        # PHP coding standards (WPCS)
references/i18n.md                    # Internationalization best practices
references/security.md                # Security patterns and sanitization
references/hooks-api.md               # Actions, filters, and hook patterns
references/database.md                # WPDB and database operations
references/transients.md              # Transients API for caching
references/users.md                   # Users, roles, capabilities, user meta
references/ajax.md                    # AJAX handlers and JavaScript enqueuing
references/http-api.md                # HTTP requests to external APIs

# REST API (comprehensive coverage)
references/rest-api.md                # Overview, key concepts, global params
references/rest-api-endpoints.md      # Routes, parameters, validation
references/rest-api-authentication.md # Auth methods, nonces, app passwords
references/rest-api-controllers.md    # Controller classes and patterns

# Admin interface
references/admin-menus.md             # Admin menu pages and settings screens
```

## Core Principles (Quick Summary)

For complete patterns and examples, see the reference files listed above.

### 1. Coding Standards (WPCS)
See `coding-standards.md` for complete rules.

**Key rules:** Tabs not spaces, Yoda conditions (`'value' === $var`), spaces inside parentheses, prefix functions/classes with plugin slug, PHPDoc on everything.

### 2. Internationalization (i18n)
See `i18n.md` for complete patterns.

**Key rules:** ALL user-facing strings must use `__()` or `_e()`. NEVER concatenate strings. ALWAYS add translator comments for placeholders. Comment MUST immediately precede the translation function.

**Functions:** `__()`, `_e()`, `esc_html__()`, `esc_attr__()`, `_n()` (plurals), `_x()` (context)

### 3. Security
See `security.md` for complete patterns (Quick Reference at top).

**Key rules:** Sanitize input → Validate → Escape output. Every handler needs: nonce + capability + sanitization.

**Sanitize:** `sanitize_text_field()`, `absint()`, `sanitize_key()`, `sanitize_email()`
**Escape:** `esc_html()`, `esc_attr()`, `esc_url()`, `esc_js()`
**Auth:** `wp_verify_nonce()`, `check_ajax_referer()`, `current_user_can()`

### 4. WordPress Hooks
See `hooks-api.md` for complete patterns.

**Actions:** `add_action()` / `do_action()` - execute code at specific points
**Filters:** `add_filter()` / `apply_filters()` - modify data
**Naming:** `{plugin}_{object}_{action}`, always document with PHPDoc

### 5. Database Operations
See `database.md` for complete patterns.

**Key rule:** ALWAYS use `$wpdb->prepare()` for queries with variables.

**Methods:** `$wpdb->get_row()`, `$wpdb->get_results()`, `$wpdb->insert()`, `$wpdb->update()`, `$wpdb->delete()`
**Specifiers:** `%d` (int), `%s` (string), `%f` (float)

### 6. REST API
See `rest-api.md`, `rest-api-endpoints.md`, `rest-api-authentication.md`, `rest-api-controllers.md`.

**Key rules:** Always set `permission_callback`. Use `validate_callback` and `sanitize_callback` for args. Return `rest_ensure_response()`.

### 7. AJAX Handlers
See `ajax.md` for complete patterns.

**Hooks:** `wp_ajax_{action}` (logged-in), `wp_ajax_nopriv_{action}` (guests)
**Response:** `wp_send_json_success()`, `wp_send_json_error()`
**Security:** `check_ajax_referer()` + `current_user_can()` + sanitization

### 8. Admin Menus
See `admin-menus.md` for complete patterns.

**Functions:** `add_menu_page()`, `add_submenu_page()`, `add_options_page()`
**Key rules:** Set capability requirement, use `wp_nonce_field()` in forms, `wp_safe_redirect()` after processing.

## PHPDoc Standards

**Required:** `@since` (3-digit), `@param`, `@return`
**Summary:** Third-person singular ("Retrieves", "Processes"), one sentence, ends with period.

## Code Review Checklist

When reviewing WordPress PHP code, verify:

### Coding Standards
- [ ] Uses tabs for indentation
- [ ] Follows WordPress naming conventions
- [ ] Uses Yoda conditions
- [ ] Has proper spacing around operators and control structures
- [ ] Has complete PHPDoc blocks

### Internationalization
- [ ] All user-facing strings are translatable
- [ ] Correct text domain is used
- [ ] No string concatenation in translations
- [ ] Placeholders have translator comments
- [ ] Plurals use `_n()` correctly

### Security
- [ ] All input is sanitized
- [ ] All output is escaped
- [ ] Nonces are verified for form submissions
- [ ] Capability checks are in place
- [ ] No direct database queries without preparation

### Database
- [ ] Uses `$wpdb->prepare()` for all queries with variables
- [ ] Uses appropriate format specifiers
- [ ] Tables use `$wpdb->prefix`

### Performance
- [ ] No unnecessary database queries in loops
- [ ] Uses transients for expensive operations
- [ ] Hooks registered at appropriate priority

## Common Anti-Patterns

**AVOID**:

```php
// WRONG: Direct echo without escaping
echo $user_input;

// WRONG: Unprepared SQL query
$wpdb->query( "SELECT * FROM table WHERE id = $id" );

// WRONG: Missing nonce verification
if ( isset( $_POST['action'] ) ) { /* process */ }

// WRONG: String concatenation in translations
__( 'Hello ' ) . $name;

// WRONG: Checking boolean with loose comparison
if ( $value == true )

// WRONG: Using extract()
extract( $args );
```

**CORRECT**:

```php
// RIGHT: Escaped output
echo esc_html( $user_input );

// RIGHT: Prepared statement
$wpdb->query( $wpdb->prepare( "SELECT * FROM table WHERE id = %d", $id ) );

// RIGHT: Nonce verification
if ( isset( $_POST['action'] ) && wp_verify_nonce( $_POST['nonce'], 'action' ) ) { /* process */ }

// RIGHT: Placeholder in translation
sprintf( __( 'Hello %s' ), $name );

// RIGHT: Strict boolean comparison
if ( true === $value )

// RIGHT: Explicit variable assignment
$title = isset( $args['title'] ) ? $args['title'] : '';
```

## Notes

- Always check WordPress Coding Standards documentation for edge cases
- Use PHPCS with WordPress-Extra ruleset to catch issues automatically
- When in doubt, prioritize security over convenience
- Test translations by running `wp i18n make-pot` to verify strings are extracted correctly