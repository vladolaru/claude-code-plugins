# WordPress Security Best Practices

Comprehensive guide to writing secure WordPress PHP code, based on the official WordPress Security API documentation.

## Quick Reference

### Most Common Functions

| Operation | Function | Example |
|-----------|----------|---------|
| Sanitize text | `sanitize_text_field()` | `$name = sanitize_text_field( wp_unslash( $_POST['name'] ) );` |
| Sanitize int | `absint()` | `$id = absint( $_POST['id'] );` |
| Sanitize key | `sanitize_key()` | `$action = sanitize_key( $_POST['action'] );` |
| Escape HTML | `esc_html()` | `echo esc_html( $text );` |
| Escape attr | `esc_attr()` | `<input value="<?php echo esc_attr( $v ); ?>">` |
| Escape URL | `esc_url()` | `<a href="<?php echo esc_url( $url ); ?>">` |
| Verify nonce | `check_ajax_referer()` | `check_ajax_referer( 'action', 'nonce' );` |
| Check capability | `current_user_can()` | `if ( ! current_user_can( 'manage_woocommerce' ) )` |

### Complete Handler Pattern

**CRITICAL**: Every form/AJAX handler MUST include ALL of these steps:

```php
function myplugin_handle_request() {
    // 1. VERIFY NONCE (CSRF protection)
    if ( ! wp_verify_nonce( sanitize_key( $_POST['nonce'] ), 'myplugin_action' ) ) {
        wp_die( esc_html__( 'Security check failed.', 'my-plugin' ) );
    }

    // 2. CHECK CAPABILITY (authorization)
    if ( ! current_user_can( 'manage_woocommerce' ) ) {
        wp_die( esc_html__( 'Permission denied.', 'my-plugin' ) );
    }

    // 3. SANITIZE INPUT
    $id = absint( $_POST['id'] );
    $name = sanitize_text_field( wp_unslash( $_POST['name'] ) );

    // 4. PROCESS (your logic here)
    $result = myplugin_process( $id, $name );

    // 5. ESCAPE OUTPUT
    wp_send_json_success( [
        'message' => esc_html( $result['message'] ),
    ] );
}
```

### Escaping by Context

| Context | Function | When |
|---------|----------|------|
| HTML content | `esc_html()` | `<p><?php echo esc_html( $text ); ?></p>` |
| HTML attribute | `esc_attr()` | `value="<?php echo esc_attr( $val ); ?>"` |
| URL | `esc_url()` | `href="<?php echo esc_url( $url ); ?>"` |
| JavaScript | `esc_js()` | `onclick="alert('<?php echo esc_js( $msg ); ?>');"` |
| Textarea | `esc_textarea()` | `<textarea><?php echo esc_textarea( $content ); ?></textarea>` |
| JSON in attr | `esc_attr( wp_json_encode() )` | `data-config="<?php echo esc_attr( wp_json_encode( $cfg ) ); ?>"` |

---

## Core Security Principles

WordPress defines five essential rules for secure development:

1. **Never trust user input** - All data from users, URLs, cookies, databases, or third-party sources is untrusted
2. **Escape late** - Apply escaping at output time, not when storing data
3. **Escape everything from untrusted sources** - Including databases and third-party APIs
4. **Never assume anything** - Validate all assumptions about data
5. **Sanitization is okay, but validation/rejection is better** - Prefer strict validation over permissive sanitization

### The Three-Pillar Security Mindset

- **Don't trust data** - Verify all inputs and third-party sources
- **Rely on WordPress APIs** - Leverage built-in validation and sanitization functions
- **Keep code current** - Maintain regular updates as threats evolve

## Data Validation

Validation should be performed **as early as possible**, before any actions are taken.

### Safelist Validation (Preferred)

Accept only values from a known, trusted set. Use strict type checking.

```php
// Strict comparison operator (prevents type coercion attacks)
if ( 1 === $untrusted_input ) {
	echo '<p>Valid data</p>';
} else {
	wp_die( esc_html__( 'Invalid data.', 'my-plugin' ) );
}

// in_array with strict mode (CRITICAL: always use true as third parameter)
$safe_values = [ 'author', 'post_author', 'date', 'post_date' ];
$orderby = sanitize_key( $_POST['orderby'] );

if ( in_array( $orderby, $safe_values, true ) ) {
	// Safe to use $orderby
}

// switch statement with explicit comparison
switch ( true ) {
	case 'publish' === $status:
		// Handle publish
		break;
	case 'draft' === $status:
		// Handle draft
		break;
	default:
		wp_die( esc_html__( 'Invalid status.', 'my-plugin' ) );
}
```

### Built-in Validation Functions

| Function | Purpose |
|----------|---------|
| `is_email()` | Validates email address format |
| `term_exists()` | Checks if taxonomy term exists |
| `username_exists()` | Confirms username availability |
| `validate_file()` | Verifies file path validity |
| `is_numeric()` | Checks if value is numeric |
| `in_array()` | Checks array membership (use strict mode!) |

### Custom Validation Example

```php
/**
 * Validates a US ZIP code.
 *
 * @since 1.0.0
 *
 * @param string $zip_code The ZIP code to validate.
 * @return bool True if valid, false otherwise.
 */
function myplugin_is_valid_us_zip_code( string $zip_code ): bool {
	if ( empty( $zip_code ) ) {
		return false;
	}

	if ( 10 < strlen( trim( $zip_code ) ) ) {
		return false;
	}

	if ( ! preg_match( '/^\d{5}(-?\d{4})?$/', $zip_code ) ) {
		return false;
	}

	return true;
}
```

## Input Sanitization

Use these functions when RECEIVING data. Sanitization cleans data by removing or modifying dangerous elements.

### Text Sanitization

```php
// Plain text (removes HTML, newlines)
$name = sanitize_text_field( wp_unslash( $_POST['name'] ) );

// Multiline text (preserves newlines)
$description = sanitize_textarea_field( wp_unslash( $_POST['description'] ) );

// Title (preserves some formatting)
$title = sanitize_title( wp_unslash( $_POST['title'] ) );

// File name (removes dangerous characters)
$filename = sanitize_file_name( $_POST['filename'] );

// Key (lowercase alphanumeric, dashes, underscores)
$key = sanitize_key( $_POST['key'] );

// Slug (for URLs)
$slug = sanitize_title_with_dashes( wp_unslash( $_POST['slug'] ) );

// MIME type
$mime = sanitize_mime_type( $_POST['mime_type'] );

// SQL orderby clause
$orderby = sanitize_sql_orderby( $_POST['orderby'] );
```

### Numeric Sanitization

```php
// Positive integer (absolute value)
$order_id = absint( $_POST['order_id'] );

// Integer (can be negative)
$amount = intval( $_POST['amount'] );

// Float
$price = floatval( $_POST['price'] );

// Formatted number for display
$formatted = number_format_i18n( $price, 2 );

// Type casting for trusted numeric validation
if ( is_numeric( $_POST['quantity'] ) ) {
	$quantity = (int) $_POST['quantity'];
}
```

### Email and URLs

```php
// Email address
$email = sanitize_email( $_POST['email'] );

// URL for display (encodes entities)
$url = esc_url( $_POST['website'] );

// URL for database storage (no encoding)
$url = esc_url_raw( $_POST['website'] );
```

### HTML Content

```php
// Strip all HTML
$text = wp_strip_all_tags( $_POST['content'] );

// Allow specific HTML tags
$allowed_html = [
	'a'      => [
		'href'  => [],
		'title' => [],
		'class' => [],
	],
	'strong' => [],
	'em'     => [],
	'p'      => [],
	'br'     => [],
];
$content = wp_kses( $_POST['content'], $allowed_html );

// Allow post-like HTML (what's allowed in post content)
$content = wp_kses_post( $_POST['content'] );

// Allow only data attributes (minimal HTML for comments)
$content = wp_kses_data( $_POST['content'] );
```

### Arrays

```php
// Sanitize array of integers
$ids = array_map( 'absint', (array) $_POST['ids'] );

// Sanitize array of strings
$names = array_map( 'sanitize_text_field', wp_unslash( (array) $_POST['names'] ) );

// Deep sanitize associative array
$data = map_deep( wp_unslash( $_POST['data'] ), 'sanitize_text_field' );
```

## Output Escaping

Use these functions when DISPLAYING data. **Escape late** - at the point of output.

### Core Escaping Functions

| Function | Use Case | Example |
|----------|----------|---------|
| `esc_html()` | HTML element content | `<h4><?php echo esc_html( $title ); ?></h4>` |
| `esc_attr()` | HTML attribute values | `<input value="<?php echo esc_attr( $value ); ?>">` |
| `esc_url()` | URLs in href/src | `<a href="<?php echo esc_url( $url ); ?>">` |
| `esc_url_raw()` | URLs for database/redirects | Database storage, `wp_safe_redirect()` |
| `esc_js()` | Inline JavaScript strings | `onclick="alert('<?php echo esc_js( $msg ); ?>');"` |
| `esc_textarea()` | Textarea content | `<textarea><?php echo esc_textarea( $text ); ?></textarea>` |
| `esc_xml()` | XML blocks | `<loc><?php echo esc_xml( $url ); ?></loc>` |

### HTML Content

```php
// Escape for HTML content
echo '<p>' . esc_html( $user_input ) . '</p>';

// For HTML attributes
echo '<input type="text" value="' . esc_attr( $value ) . '" name="field">';

// For URLs
echo '<a href="' . esc_url( $link ) . '">' . esc_html( $text ) . '</a>';

// For JavaScript strings
echo '<script>var name = "' . esc_js( $name ) . '";</script>';

// For textarea content
echo '<textarea name="content">' . esc_textarea( $content ) . '</textarea>';
```

### Combined Localization and Escaping

These functions translate AND escape in one call:

```php
// esc_html__() - Returns escaped translated string
$message = esc_html__( 'Settings saved.', 'my-plugin' );

// esc_html_e() - Echoes escaped translated string
esc_html_e( 'Settings saved.', 'my-plugin' );

// esc_html_x() - With context
$label = esc_html_x( 'Post', 'noun', 'my-plugin' );

// esc_attr__() - For attributes
echo '<input placeholder="' . esc_attr__( 'Enter name', 'my-plugin' ) . '">';

// esc_attr_e() - Echoes for attributes
echo '<input placeholder="';
esc_attr_e( 'Enter name', 'my-plugin' );
echo '">';

// esc_attr_x() - Attribute with context
$title = esc_attr_x( 'Close', 'button label', 'my-plugin' );
```

### JSON Output

```php
// For embedding JSON in HTML data attributes
echo '<div data-config="' . esc_attr( wp_json_encode( $config ) ) . '">';

// For inline script
echo '<script>var data = ' . wp_json_encode( $data ) . ';</script>';

// For AJAX responses (handles encoding automatically)
wp_send_json( $data );
wp_send_json_success( $data );
wp_send_json_error( $data );
```

### HTML with Allowed Tags

```php
// Output HTML with specific allowed tags
$allowed_tags = [
	'a'      => [ 'href' => [], 'class' => [], 'target' => [] ],
	'strong' => [],
	'em'     => [],
	'code'   => [],
];
echo wp_kses( $html_content, $allowed_tags );

// Post-like content
echo wp_kses_post( $content );
```

### Escaping Complete Attributes

**Important**: When combining variables with static text in attributes, escape the entire result as one unit:

```php
// CORRECT - Escape the complete attribute value
echo '<div id="' . esc_attr( $prefix . '-box-' . $id ) . '">';

// WRONG - Can break escaping of delimiter characters
echo '<div id="' . esc_attr( $prefix ) . '-box-' . esc_attr( $id ) . '">';
```

### Exception: Pre-Escaped Variables

When functions cannot escape at output time, prefix variables to indicate safety:

```php
// Prefix with _escaped, _safe, or _clean
$_escaped_content = esc_html( $content );
// ... later in template ...
echo $_escaped_content;
```

## Nonce Verification (CSRF Protection)

Nonces are security tokens that verify request authenticity and user intent.

### How Nonces Work

- **Lifetime**: 24 hours by default, divided into two 12-hour "ticks"
- **Scope**: Unique per user session and action
- **Validation**: Nonces validate against current and previous tick (12-24 hour window)
- **Not authentication**: Nonces verify intent, not identity - always combine with capability checks

### Form Nonces

```php
// In the form
<form method="post">
	<?php wp_nonce_field( 'myplugin_save_settings', 'myplugin_nonce' ); ?>
	<input type="text" name="api_key">
	<button type="submit">Save</button>
</form>

// When processing
if ( ! isset( $_POST['myplugin_nonce'] ) || ! wp_verify_nonce( sanitize_key( $_POST['myplugin_nonce'] ), 'myplugin_save_settings' ) ) {
	wp_die( esc_html__( 'Security check failed.', 'my-plugin' ) );
}
```

### URL Nonces

```php
// Create URL with nonce
$delete_url = wp_nonce_url(
	admin_url( 'admin.php?page=wcpay&action=delete&id=' . $id ),
	'myplugin_delete_' . $id,
	'_myplugin_nonce'
);

// Verify in handler
if ( ! isset( $_GET['_myplugin_nonce'] ) || ! wp_verify_nonce( sanitize_key( $_GET['_myplugin_nonce'] ), 'myplugin_delete_' . $id ) ) {
	wp_die( esc_html__( 'Security check failed.', 'my-plugin' ) );
}
```

### Admin Referer Check

```php
// For admin screens (checks nonce AND referrer)
check_admin_referer( 'myplugin_action', 'myplugin_nonce' );
// Dies with 403 on failure
```

### AJAX Nonces

```php
// Enqueue with nonce
wp_localize_script( 'wcpay-admin', 'wcpay', [
	'ajax_url' => admin_url( 'admin-ajax.php' ),
	'nonce'    => wp_create_nonce( 'myplugin_ajax' ),
] );

// In JavaScript
jQuery.post( wcpay.ajax_url, {
	action: 'myplugin_process',
	nonce: wcpay.nonce,
	data: formData
} );

// In AJAX handler
add_action( 'wp_ajax_myplugin_process', 'myplugin_ajax_process' );

function myplugin_ajax_process() {
	// check_ajax_referer dies on failure by default
	check_ajax_referer( 'myplugin_ajax', 'nonce' );

	// Or for custom handling
	if ( ! wp_verify_nonce( sanitize_key( $_POST['nonce'] ), 'myplugin_ajax' ) ) {
		wp_send_json_error( [ 'message' => __( 'Invalid security token.', 'my-plugin' ) ], 403 );
	}

	// Process request...
}
```

### Nonce Best Practices

1. **Make actions specific**: Use patterns like `'myplugin_delete_' . $item_id` rather than generic `'myplugin_delete'`
2. **Never substitute for auth**: Nonces verify intent, not identity - always check capabilities too
3. **Include item identifiers**: Prevents token reuse across different items

```php
// GOOD - Specific action with item ID
wp_nonce_field( 'myplugin_edit_payment_' . $payment_id, 'myplugin_nonce' );

// BAD - Generic action (nonce could be reused for any payment)
wp_nonce_field( 'myplugin_edit_payment', 'myplugin_nonce' );
```

### Modifying Nonce Lifetime

```php
// Extend nonce lifetime to 4 hours
add_filter( 'nonce_life', function() {
	return 4 * HOUR_IN_SECONDS;
} );
```

## Capability Checks

**Always verify user permissions before performing sensitive operations.** Capability checks are separate from nonce verification.

### User Roles vs Capabilities

- **Roles**: Group classifications (Administrator, Editor, Author, Contributor, Subscriber)
- **Capabilities**: Granular permissions assigned to roles
- **Hierarchy**: Higher roles inherit capabilities from lower roles

### Basic Capability Check

```php
// Check before performing admin action
if ( ! current_user_can( 'manage_options' ) ) {
	wp_die( esc_html__( 'Permission denied.', 'my-plugin' ) );
}

// WooCommerce specific
if ( ! current_user_can( 'manage_woocommerce' ) ) {
	wp_die( esc_html__( 'Permission denied.', 'my-plugin' ) );
}

// Edit others' posts (Editors and above)
if ( ! current_user_can( 'edit_others_posts' ) ) {
	wp_die( esc_html__( 'Permission denied.', 'my-plugin' ) );
}
```

### Object-Specific Capabilities

```php
// Check for specific post
if ( ! current_user_can( 'edit_post', $post_id ) ) {
	wp_die( esc_html__( 'You cannot edit this post.', 'my-plugin' ) );
}

// Check for specific user
if ( ! current_user_can( 'edit_user', $user_id ) ) {
	wp_die( esc_html__( 'You cannot edit this user.', 'my-plugin' ) );
}

// Check for specific term
if ( ! current_user_can( 'edit_term', $term_id ) ) {
	wp_die( esc_html__( 'You cannot edit this term.', 'my-plugin' ) );
}
```

### Common WordPress Capabilities

| Capability | Who Has It | Use For |
|------------|-----------|---------|
| `manage_options` | Administrator | Site settings, plugin options |
| `manage_woocommerce` | Shop Manager, Admin | WooCommerce settings |
| `edit_posts` | Contributor+ | Creating content |
| `edit_others_posts` | Editor+ | Editing others' content |
| `publish_posts` | Author+ | Publishing content |
| `delete_posts` | Contributor+ | Deleting own content |
| `upload_files` | Author+ | Media uploads |
| `edit_users` | Administrator | User management |

### Menu and Screen Access

```php
// Register menu with capability requirement
add_menu_page(
	__( 'My_Plugin Settings', 'my-plugin' ),
	__( 'My_Plugin', 'my-plugin' ),
	'manage_woocommerce', // Required capability
	'wcpay-settings',
	'myplugin_render_settings_page'
);

// Submenu with different capability
add_submenu_page(
	'wcpay-settings',
	__( 'Advanced', 'my-plugin' ),
	__( 'Advanced', 'my-plugin' ),
	'manage_options', // Higher capability required
	'wcpay-advanced',
	'myplugin_render_advanced_page'
);
```

### Complete Security Pattern

**Always combine capability checks with nonce verification:**

```php
function myplugin_process_settings() {
	// 1. Verify nonce (CSRF protection)
	if ( ! isset( $_POST['myplugin_nonce'] ) || ! wp_verify_nonce( sanitize_key( $_POST['myplugin_nonce'] ), 'myplugin_save_settings' ) ) {
		wp_die( esc_html__( 'Security check failed.', 'my-plugin' ) );
	}

	// 2. Check capabilities (authorization)
	if ( ! current_user_can( 'manage_woocommerce' ) ) {
		wp_die( esc_html__( 'Permission denied.', 'my-plugin' ) );
	}

	// 3. Sanitize input
	$api_key = sanitize_text_field( wp_unslash( $_POST['api_key'] ) );

	// 4. Process the request
	update_option( 'myplugin_api_key', $api_key );

	// 5. Redirect with success message
	wp_safe_redirect( add_query_arg( 'message', 'saved', admin_url( 'admin.php?page=wcpay-settings' ) ) );
	exit;
}
```

## Database Security

### Prepared Statements

**ALWAYS use `$wpdb->prepare()` for queries with variables** to prevent SQL injection:

```php
global $wpdb;

// SELECT with prepared statement
$result = $wpdb->get_var(
	$wpdb->prepare(
		"SELECT status FROM {$wpdb->prefix}myplugin_orders WHERE order_id = %d",
		$order_id
	)
);

// SELECT multiple rows
$results = $wpdb->get_results(
	$wpdb->prepare(
		"SELECT * FROM {$wpdb->prefix}myplugin_orders WHERE status = %s AND amount > %f",
		$status,
		$min_amount
	)
);

// INSERT with prepare
$wpdb->query(
	$wpdb->prepare(
		"INSERT INTO {$wpdb->prefix}myplugin_logs (message, level, created_at) VALUES (%s, %s, %s)",
		$message,
		$level,
		current_time( 'mysql' )
	)
);

// UPDATE with prepare
$wpdb->query(
	$wpdb->prepare(
		"UPDATE {$wpdb->prefix}myplugin_orders SET status = %s WHERE order_id = %d",
		$new_status,
		$order_id
	)
);

// DELETE with prepare
$wpdb->query(
	$wpdb->prepare(
		"DELETE FROM {$wpdb->prefix}myplugin_logs WHERE created_at < %s",
		$cutoff_date
	)
);
```

### Format Specifiers

| Specifier | Type | Example |
|-----------|------|---------|
| `%d` | Integer | `WHERE id = %d` |
| `%f` | Float | `WHERE amount = %f` |
| `%s` | String | `WHERE name = %s` |

### IN Clause with Multiple Values

```php
$ids = [ 1, 2, 3, 4, 5 ];
$placeholders = implode( ', ', array_fill( 0, count( $ids ), '%d' ) );

$results = $wpdb->get_results(
	$wpdb->prepare(
		"SELECT * FROM {$wpdb->prefix}myplugin_orders WHERE order_id IN ($placeholders)",
		...$ids
	)
);
```

### Safe Table Names

```php
// Always use $wpdb->prefix
$table = $wpdb->prefix . 'myplugin_orders';

// NEVER hardcode prefix
$table = 'wp_myplugin_orders'; // WRONG - assumes prefix is 'wp_'
```

### LIKE Queries

```php
// Escape LIKE wildcards to prevent injection
$search = '%' . $wpdb->esc_like( $search_term ) . '%';

$results = $wpdb->get_results(
	$wpdb->prepare(
		"SELECT * FROM {$wpdb->prefix}myplugin_orders WHERE customer_name LIKE %s",
		$search
	)
);
```

### Prefer WordPress Functions

Use WordPress API functions when available instead of raw SQL:

```php
// BETTER: Use WordPress functions
add_post_meta( $post_id, 'myplugin_payment_id', $payment_id );
update_post_meta( $post_id, 'myplugin_status', $status );
get_post_meta( $post_id, 'myplugin_payment_id', true );
delete_post_meta( $post_id, 'myplugin_payment_id' );

// Use get_posts() or WP_Query instead of raw SELECT
$orders = get_posts( [
	'post_type'   => 'shop_order',
	'post_status' => 'wc-processing',
	'meta_query'  => [
		[
			'key'   => 'myplugin_payment_id',
			'value' => $payment_id,
		],
	],
] );
```

## File Security

### File Upload Validation

```php
/**
 * Handles secure file upload.
 *
 * @since 1.0.0
 *
 * @param array $file The $_FILES array element.
 * @return array|WP_Error Upload result on success, WP_Error on failure.
 */
function myplugin_handle_upload( $file ) {
	// Check for upload errors
	if ( UPLOAD_ERR_OK !== $file['error'] ) {
		return new WP_Error( 'upload_error', __( 'Upload failed.', 'my-plugin' ) );
	}

	// Validate file type by extension AND MIME type
	$allowed_types = [
		'jpg|jpeg|jpe' => 'image/jpeg',
		'png'          => 'image/png',
		'gif'          => 'image/gif',
		'pdf'          => 'application/pdf',
	];

	$file_type = wp_check_filetype_and_ext( $file['tmp_name'], $file['name'], $allowed_types );

	if ( ! $file_type['ext'] || ! $file_type['type'] ) {
		return new WP_Error( 'invalid_type', __( 'Invalid file type.', 'my-plugin' ) );
	}

	// Validate file size (5MB max)
	$max_size = 5 * MB_IN_BYTES;
	if ( $file['size'] > $max_size ) {
		return new WP_Error( 'file_too_large', __( 'File exceeds maximum size.', 'my-plugin' ) );
	}

	// Use WordPress upload handling
	require_once ABSPATH . 'wp-admin/includes/file.php';

	$upload = wp_handle_upload( $file, [ 'test_form' => false ] );

	if ( isset( $upload['error'] ) ) {
		return new WP_Error( 'upload_error', $upload['error'] );
	}

	return $upload;
}
```

### Safe File Paths

```php
// Validate and sanitize file path
$file = sanitize_file_name( $_GET['file'] );
$path = WCPAY_PLUGIN_PATH . 'exports/' . $file;

// Prevent directory traversal
$real_path = realpath( $path );
$base_path = realpath( WCPAY_PLUGIN_PATH . 'exports/' );

// Verify path is within allowed directory
if ( false === $real_path || 0 !== strpos( $real_path, $base_path ) ) {
	wp_die( esc_html__( 'Invalid file path.', 'my-plugin' ) );
}

// Additional extension check
$extension = pathinfo( $real_path, PATHINFO_EXTENSION );
if ( ! in_array( $extension, [ 'csv', 'txt' ], true ) ) {
	wp_die( esc_html__( 'Invalid file type.', 'my-plugin' ) );
}
```

### File Inclusion Safety

```php
// NEVER include user-supplied paths directly
// VULNERABLE:
include $_GET['template']; // Remote code execution!

// SAFE: Whitelist allowed files
$allowed_templates = [ 'header', 'footer', 'sidebar', 'content' ];
$template = sanitize_key( $_GET['template'] );

if ( in_array( $template, $allowed_templates, true ) ) {
	$template_path = WCPAY_PLUGIN_PATH . 'templates/' . $template . '.php';

	if ( file_exists( $template_path ) ) {
		include $template_path;
	}
}
```

## REST API Security

### Permission Callbacks (Required)

```php
register_rest_route(
	'myplugin/v1',
	'/settings',
	[
		'methods'             => WP_REST_Server::EDITABLE,
		'callback'            => 'myplugin_update_settings',
		'permission_callback' => function( $request ) {
			return current_user_can( 'manage_woocommerce' );
		},
	]
);

// For public endpoints, explicitly allow access
register_rest_route(
	'myplugin/v1',
	'/public-info',
	[
		'methods'             => WP_REST_Server::READABLE,
		'callback'            => 'myplugin_get_public_info',
		'permission_callback' => '__return_true', // Explicitly public
	]
);
```

### Input Validation and Sanitization

```php
register_rest_route(
	'myplugin/v1',
	'/payments/(?P<id>\d+)',
	[
		'methods'             => WP_REST_Server::READABLE,
		'callback'            => 'myplugin_get_payment',
		'permission_callback' => 'myplugin_rest_permission_check',
		'args'                => [
			'id' => [
				'required'          => true,
				'type'              => 'integer',
				'minimum'           => 1,
				'validate_callback' => function( $param, $request, $key ) {
					return is_numeric( $param ) && $param > 0;
				},
				'sanitize_callback' => 'absint',
			],
			'include_meta' => [
				'type'              => 'boolean',
				'default'           => false,
				'sanitize_callback' => 'rest_sanitize_boolean',
			],
		],
	]
);

function myplugin_get_payment( WP_REST_Request $request ) {
	$id = $request->get_param( 'id' );
	$include_meta = $request->get_param( 'include_meta' );

	// Fetch data...

	return rest_ensure_response( $data );
}
```

## Sensitive Data Handling

### Don't Log Sensitive Data

```php
// WRONG - logs credit card number
myplugin_log( 'Processing payment with card: ' . $card_number );

// CORRECT - mask sensitive data
myplugin_log( 'Processing payment with card ending in: ' . substr( $card_number, -4 ) );

// CORRECT - use placeholders for sensitive values
myplugin_log( 'Processing payment for customer [REDACTED]' );
```

### Don't Expose in Errors

```php
// WRONG - exposes internal details
wp_die( 'Database error: ' . $wpdb->last_error );

// CORRECT - generic message to user, log details internally
if ( $wpdb->last_error ) {
	myplugin_log( 'Database error: ' . $wpdb->last_error, 'error' );
	wp_die( esc_html__( 'An error occurred. Please try again.', 'my-plugin' ) );
}
```

### Secure Option Storage

```php
// For sensitive options, consider encryption
function myplugin_save_api_key( $key ) {
	if ( defined( 'WCPAY_ENCRYPTION_KEY' ) && extension_loaded( 'openssl' ) ) {
		$iv = openssl_random_pseudo_bytes( 16 );
		$encrypted = openssl_encrypt( $key, 'AES-256-CBC', WCPAY_ENCRYPTION_KEY, 0, $iv );
		update_option( 'myplugin_api_key', base64_encode( $iv . $encrypted ) );
	} else {
		update_option( 'myplugin_api_key', $key );
	}
}

function myplugin_get_api_key() {
	$stored = get_option( 'myplugin_api_key', '' );

	if ( defined( 'WCPAY_ENCRYPTION_KEY' ) && extension_loaded( 'openssl' ) && ! empty( $stored ) ) {
		$data = base64_decode( $stored );
		$iv = substr( $data, 0, 16 );
		$encrypted = substr( $data, 16 );
		return openssl_decrypt( $encrypted, 'AES-256-CBC', WCPAY_ENCRYPTION_KEY, 0, $iv );
	}

	return $stored;
}
```

## Common Vulnerabilities

### SQL Injection

```php
// VULNERABLE
$wpdb->query( "SELECT * FROM {$wpdb->users} WHERE ID = " . $_GET['id'] );

// SAFE
$wpdb->query(
	$wpdb->prepare(
		"SELECT * FROM {$wpdb->users} WHERE ID = %d",
		absint( $_GET['id'] )
	)
);
```

### XSS (Cross-Site Scripting)

```php
// VULNERABLE - Direct output
echo '<div>' . $_GET['message'] . '</div>';

// SAFE - Sanitize input AND escape output
$message = sanitize_text_field( wp_unslash( $_GET['message'] ) );
echo '<div>' . esc_html( $message ) . '</div>';

// VULNERABLE - In attributes
echo '<input value="' . $_GET['name'] . '">';

// SAFE
echo '<input value="' . esc_attr( sanitize_text_field( wp_unslash( $_GET['name'] ) ) ) . '">';

// VULNERABLE - In URLs
echo '<a href="' . $_GET['url'] . '">Link</a>';

// SAFE
echo '<a href="' . esc_url( $_GET['url'] ) . '">Link</a>';
```

### CSRF (Cross-Site Request Forgery)

```php
// VULNERABLE - No nonce verification
if ( isset( $_POST['delete'] ) ) {
	myplugin_delete_record( $_POST['id'] );
}

// SAFE - With nonce and capability check
if ( isset( $_POST['delete'] ) ) {
	if ( ! wp_verify_nonce( sanitize_key( $_POST['myplugin_nonce'] ), 'myplugin_delete' ) ) {
		wp_die( esc_html__( 'Security check failed.', 'my-plugin' ) );
	}

	if ( ! current_user_can( 'manage_woocommerce' ) ) {
		wp_die( esc_html__( 'Permission denied.', 'my-plugin' ) );
	}

	myplugin_delete_record( absint( $_POST['id'] ) );
}
```

### Privilege Escalation

```php
// VULNERABLE - No capability check
add_action( 'wp_ajax_myplugin_delete_all', function() {
	myplugin_delete_all_records(); // Any logged-in user can call this!
} );

// SAFE - With capability check
add_action( 'wp_ajax_myplugin_delete_all', function() {
	check_ajax_referer( 'myplugin_admin_actions', 'nonce' );

	if ( ! current_user_can( 'manage_options' ) ) {
		wp_send_json_error( [ 'message' => 'Unauthorized' ], 403 );
	}

	myplugin_delete_all_records();
	wp_send_json_success();
} );
```

### Object Injection

```php
// VULNERABLE - Unserialize untrusted data
$data = unserialize( $_POST['data'] ); // Object injection risk!

// SAFE - Use JSON instead
$data = json_decode( sanitize_text_field( wp_unslash( $_POST['data'] ) ), true );

// Or if unserialize is required, validate the data type
$data = maybe_unserialize( get_option( 'myplugin_settings' ) );
if ( ! is_array( $data ) ) {
	$data = [];
}
```

## Security Checklist

Before deploying WordPress code, verify:

### Input Handling
- [ ] All `$_GET`, `$_POST`, `$_REQUEST` values are sanitized
- [ ] `wp_unslash()` is used before sanitization for superglobals
- [ ] Arrays are sanitized with `array_map()` or `map_deep()`
- [ ] File uploads validate type, size, and extension

### Output Handling
- [ ] All dynamic output is escaped with appropriate function
- [ ] `esc_html()` for content, `esc_attr()` for attributes, `esc_url()` for URLs
- [ ] Combined localization functions used where appropriate
- [ ] Complete attributes are escaped as single units

### Authentication & Authorization
- [ ] All forms have nonce fields (`wp_nonce_field()`)
- [ ] All handlers verify nonces (`wp_verify_nonce()` or `check_ajax_referer()`)
- [ ] All admin actions check capabilities (`current_user_can()`)
- [ ] Nonce actions include item identifiers when applicable

### Database
- [ ] All queries with variables use `$wpdb->prepare()`
- [ ] Correct format specifiers (`%d`, `%s`, `%f`) are used
- [ ] LIKE queries use `$wpdb->esc_like()`
- [ ] Table names use `$wpdb->prefix`

### Files
- [ ] File paths are validated against base directory
- [ ] File inclusions use whitelists, not user input
- [ ] Uploads are restricted to allowed types

### REST API
- [ ] All routes have `permission_callback` defined
- [ ] Arguments have `validate_callback` and `sanitize_callback`
- [ ] Public endpoints explicitly use `__return_true`

### Sensitive Data
- [ ] No sensitive data in logs or error messages
- [ ] API keys and secrets are stored securely
- [ ] Error messages don't expose internal details
