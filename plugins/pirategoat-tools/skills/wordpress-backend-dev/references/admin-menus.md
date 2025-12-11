# WordPress Administration Menus

Guide to creating admin menu pages for WordPress plugins.

Based on official WordPress handbook: https://developer.wordpress.org/plugins/administration-menus/

## Overview

Administration menus are the interfaces displayed in the WordPress admin dashboard. They allow plugins to add option pages and settings screens.

**Menu Types:**
- **Top-Level Menus** - Primary menu items in the left sidebar
- **Sub-Menus** - Nested items under top-level menus

**Best Practice**: For plugins with a single option page, add it as a sub-menu to an existing menu (Settings, Tools) rather than creating a new top-level menu. This reduces admin clutter.

## Sub-Menus (Recommended Approach)

### add_submenu_page()

```php
add_submenu_page(
	string $parent_slug,    // Parent menu slug
	string $page_title,     // Browser title bar text
	string $menu_title,     // Menu label text
	string $capability,     // Required capability
	string $menu_slug,      // Unique identifier
	callable $callback = '' // Content render function
);
```

### Basic Example

```php
add_action( 'admin_menu', 'myplugin_add_settings_page' );

function myplugin_add_settings_page() {
	add_submenu_page(
		'woocommerce',                                    // Parent slug
		__( 'My Plugin', 'my-plugin' ),
		__( 'Payments', 'my-plugin' ),
		'manage_woocommerce',
		'myplugin-settings',
		'myplugin_render_settings_page'
	);
}

function myplugin_render_settings_page() {
	// Security check
	if ( ! current_user_can( 'manage_woocommerce' ) ) {
		return;
	}
	?>
	<div class="wrap">
		<h1><?php echo esc_html( get_admin_page_title() ); ?></h1>
		<!-- Page content -->
	</div>
	<?php
}
```

### Predefined Parent Slugs

| Parent Menu | Slug | Helper Function |
|-------------|------|-----------------|
| Dashboard | `index.php` | `add_dashboard_page()` |
| Posts | `edit.php` | `add_posts_page()` |
| Media | `upload.php` | `add_media_page()` |
| Pages | `edit.php?post_type=page` | `add_pages_page()` |
| Comments | `edit-comments.php` | `add_comments_page()` |
| Appearance | `themes.php` | `add_theme_page()` |
| Plugins | `plugins.php` | `add_plugins_page()` |
| Users | `users.php` | `add_users_page()` |
| Tools | `tools.php` | `add_management_page()` |
| Settings | `options-general.php` | `add_options_page()` |
| WooCommerce | `woocommerce` | - |

### Helper Functions

Use these instead of `add_submenu_page()` for common parent menus:

```php
// Add to Settings menu
add_options_page(
	__( 'Plugin Settings', 'text-domain' ),  // Page title
	__( 'My Plugin', 'text-domain' ),         // Menu title
	'manage_options',                          // Capability
	'my-plugin-settings',                      // Menu slug
	'my_plugin_settings_page'                  // Callback
);

// Add to Tools menu
add_management_page(
	__( 'Plugin Tools', 'text-domain' ),
	__( 'My Plugin', 'text-domain' ),
	'manage_options',
	'my-plugin-tools',
	'my_plugin_tools_page'
);
```

### Custom Post Type Sub-Menu

```php
// Add sub-menu to a custom post type
add_submenu_page(
	'edit.php?post_type=myplugin_transaction',   // CPT edit screen
	__( 'Transaction Settings', 'my-plugin' ),
	__( 'Settings', 'my-plugin' ),
	'manage_woocommerce',
	'myplugin-transaction-settings',
	'myplugin_transaction_settings_page'
);
```

## Top-Level Menus

Only create top-level menus for plugins with multiple related pages.

### add_menu_page()

```php
add_menu_page(
	string $page_title,     // Browser title bar text
	string $menu_title,     // Menu label text
	string $capability,     // Required capability
	string $menu_slug,      // Unique identifier
	callable $callback = '',// Content render function
	string $icon_url = '',  // Menu icon
	int $position = null    // Menu position
);
```

### Basic Example

```php
add_action( 'admin_menu', 'myplugin_add_admin_menu' );

function myplugin_add_admin_menu() {
	add_menu_page(
		__( 'My Plugin', 'my-plugin' ),
		__( 'Payments', 'my-plugin' ),
		'manage_woocommerce',
		'myplugin',
		'myplugin_render_main_page',
		'dashicons-money-alt',
		56  // After WooCommerce
	);
}

function myplugin_render_main_page() {
	if ( ! current_user_can( 'manage_woocommerce' ) ) {
		return;
	}
	?>
	<div class="wrap">
		<h1><?php echo esc_html( get_admin_page_title() ); ?></h1>
		<!-- Page content -->
	</div>
	<?php
}
```

### Menu Icons

**Dashicons (Recommended):**
```php
'dashicons-money-alt'
'dashicons-chart-bar'
'dashicons-admin-settings'
'dashicons-admin-tools'
```

See full list: https://developer.wordpress.org/resource/dashicons/

**Custom Icon URL:**
```php
plugin_dir_url( __FILE__ ) . 'assets/images/icon.png'
```

**Base64 SVG:**
```php
'data:image/svg+xml;base64,' . base64_encode( $svg_content )
```

**No Icon:**
```php
'none'  // or 'div' for CSS-only styling
```

### Menu Position

Default positions of WordPress menus:

| Position | Menu Item |
|----------|-----------|
| 2 | Dashboard |
| 4 | Separator |
| 5 | Posts |
| 10 | Media |
| 15 | Links |
| 20 | Pages |
| 25 | Comments |
| 59 | Separator |
| 60 | Appearance |
| 65 | Plugins |
| 70 | Users |
| 75 | Tools |
| 80 | Settings |
| 99 | Separator |

**WooCommerce position:** 55-56

Use decimals to insert between items: `55.5`

### Adding Sub-Menus to Custom Top-Level

```php
add_action( 'admin_menu', 'myplugin_add_menus' );

function myplugin_add_menus() {
	// Add top-level menu
	add_menu_page(
		__( 'My Plugin', 'my-plugin' ),
		__( 'Payments', 'my-plugin' ),
		'manage_woocommerce',
		'myplugin',
		'myplugin_render_overview_page',
		'dashicons-money-alt',
		56
	);

	// Add sub-menus (use same slug as parent for first item)
	add_submenu_page(
		'myplugin',
		__( 'Overview', 'my-plugin' ),
		__( 'Overview', 'my-plugin' ),
		'manage_woocommerce',
		'myplugin',  // Same as parent = replaces default
		'myplugin_render_overview_page'
	);

	add_submenu_page(
		'myplugin',
		__( 'Transactions', 'my-plugin' ),
		__( 'Transactions', 'my-plugin' ),
		'manage_woocommerce',
		'myplugin-transactions',
		'myplugin_render_transactions_page'
	);

	add_submenu_page(
		'myplugin',
		__( 'Settings', 'my-plugin' ),
		__( 'Settings', 'my-plugin' ),
		'manage_woocommerce',
		'myplugin-settings',
		'myplugin_render_settings_page'
	);
}
```

## Page Rendering

### Basic Structure

```php
function myplugin_render_settings_page() {
	// Security check
	if ( ! current_user_can( 'manage_woocommerce' ) ) {
		wp_die( esc_html__( 'Permission denied.', 'my-plugin' ) );
	}
	?>
	<div class="wrap">
		<h1><?php echo esc_html( get_admin_page_title() ); ?></h1>

		<?php settings_errors(); ?>

		<form method="post" action="options.php">
			<?php
			settings_fields( 'myplugin_settings' );
			do_settings_sections( 'myplugin_settings' );
			submit_button();
			?>
		</form>
	</div>
	<?php
}
```

### With Tabs

```php
function myplugin_render_settings_page() {
	if ( ! current_user_can( 'manage_woocommerce' ) ) {
		return;
	}

	$active_tab = isset( $_GET['tab'] ) ? sanitize_key( $_GET['tab'] ) : 'general';
	?>
	<div class="wrap">
		<h1><?php echo esc_html( get_admin_page_title() ); ?></h1>

		<nav class="nav-tab-wrapper">
			<a href="?page=myplugin-settings&tab=general"
			   class="nav-tab <?php echo 'general' === $active_tab ? 'nav-tab-active' : ''; ?>">
				<?php esc_html_e( 'General', 'my-plugin' ); ?>
			</a>
			<a href="?page=myplugin-settings&tab=advanced"
			   class="nav-tab <?php echo 'advanced' === $active_tab ? 'nav-tab-active' : ''; ?>">
				<?php esc_html_e( 'Advanced', 'my-plugin' ); ?>
			</a>
		</nav>

		<div class="tab-content">
			<?php
			switch ( $active_tab ) {
				case 'advanced':
					myplugin_render_advanced_settings();
					break;
				default:
					myplugin_render_general_settings();
			}
			?>
		</div>
	</div>
	<?php
}
```

## Form Handling

**CRITICAL**: All form handlers MUST follow the 5-step security pattern from `security.md#complete-handler-pattern`:
1. Verify nonce → 2. Check capability → 3. Sanitize input → 4. Process → 5. Redirect/respond

### Using the Hook Name

```php
add_action( 'admin_menu', 'myplugin_add_settings_page' );

function myplugin_add_settings_page() {
	$hook = add_submenu_page(
		'options-general.php',
		__( 'My Plugin Settings', 'my-plugin' ),
		__( 'My Plugin', 'my-plugin' ),
		'manage_options',
		'myplugin-settings',
		'myplugin_render_settings_page'
	);

	// Handle form submission before page loads
	add_action( 'load-' . $hook, 'myplugin_handle_settings_submission' );
}

function myplugin_handle_settings_submission() {
	if ( ! isset( $_POST['myplugin_settings_nonce'] ) ) {
		return;
	}

	// Verify nonce
	if ( ! wp_verify_nonce( sanitize_key( $_POST['myplugin_settings_nonce'] ), 'myplugin_save_settings' ) ) {
		wp_die( esc_html__( 'Security check failed.', 'my-plugin' ) );
	}

	// Check capability
	if ( ! current_user_can( 'manage_options' ) ) {
		wp_die( esc_html__( 'Permission denied.', 'my-plugin' ) );
	}

	// Sanitize and save
	$api_key = isset( $_POST['myplugin_api_key'] )
		? sanitize_text_field( wp_unslash( $_POST['myplugin_api_key'] ) )
		: '';

	update_option( 'myplugin_api_key', $api_key );

	// Redirect with success message
	wp_safe_redirect( add_query_arg( 'updated', 'true', menu_page_url( 'myplugin-settings', false ) ) );
	exit;
}
```

### Form with Nonce

```php
function myplugin_render_settings_page() {
	if ( ! current_user_can( 'manage_options' ) ) {
		return;
	}

	$api_key = get_option( 'myplugin_api_key', '' );
	?>
	<div class="wrap">
		<h1><?php echo esc_html( get_admin_page_title() ); ?></h1>

		<?php if ( isset( $_GET['updated'] ) ) : ?>
			<div class="notice notice-success is-dismissible">
				<p><?php esc_html_e( 'Settings saved.', 'my-plugin' ); ?></p>
			</div>
		<?php endif; ?>

		<form method="post" action="">
			<?php wp_nonce_field( 'myplugin_save_settings', 'myplugin_settings_nonce' ); ?>

			<table class="form-table">
				<tr>
					<th scope="row">
						<label for="myplugin_api_key">
							<?php esc_html_e( 'API Key', 'my-plugin' ); ?>
						</label>
					</th>
					<td>
						<input type="text"
						       id="myplugin_api_key"
						       name="myplugin_api_key"
						       value="<?php echo esc_attr( $api_key ); ?>"
						       class="regular-text">
					</td>
				</tr>
			</table>

			<?php submit_button(); ?>
		</form>
	</div>
	<?php
}
```

## Removing Menu Items

### Remove Sub-Menu

```php
add_action( 'admin_menu', 'myplugin_remove_menus', 999 );

function myplugin_remove_menus() {
	// Remove a sub-menu
	remove_submenu_page( 'tools.php', 'unwanted-tool' );

	// Remove Settings > Writing
	remove_submenu_page( 'options-general.php', 'options-writing.php' );
}
```

### Remove Top-Level Menu

```php
add_action( 'admin_menu', 'myplugin_remove_menus', 999 );

function myplugin_remove_menus() {
	remove_menu_page( 'edit-comments.php' );  // Comments
	remove_menu_page( 'tools.php' );           // Tools
}
```

### Hide for Specific Users

```php
add_action( 'admin_menu', 'myplugin_hide_menus_for_non_admins', 999 );

function myplugin_hide_menus_for_non_admins() {
	if ( ! current_user_can( 'manage_options' ) ) {
		remove_menu_page( 'tools.php' );
		remove_menu_page( 'options-general.php' );
	}
}
```

## Conditional Menu Display

```php
add_action( 'admin_menu', 'myplugin_conditional_menus' );

function myplugin_conditional_menus() {
	// Only show if WooCommerce is active
	if ( ! class_exists( 'WooCommerce' ) ) {
		return;
	}

	// Only show if plugin is configured
	if ( ! get_option( 'myplugin_configured' ) ) {
		// Show setup wizard instead
		add_menu_page(
			__( 'Setup My Plugin', 'my-plugin' ),
			__( 'Payments Setup', 'my-plugin' ),
			'manage_woocommerce',
			'myplugin-setup',
			'myplugin_render_setup_wizard',
			'dashicons-money-alt'
		);
		return;
	}

	// Show full menu
	add_menu_page( /* ... */ );
}
```

## Admin Notices

```php
add_action( 'admin_notices', 'myplugin_admin_notices' );

function myplugin_admin_notices() {
	// Only show on our settings page
	$screen = get_current_screen();
	if ( 'settings_page_myplugin-settings' !== $screen->id ) {
		return;
	}

	if ( ! get_option( 'myplugin_api_key' ) ) {
		?>
		<div class="notice notice-warning">
			<p>
				<?php
				printf(
					/* translators: %s: Settings page URL */
					esc_html__( 'Please configure your %s to start accepting payments.', 'my-plugin' ),
					'<a href="' . esc_url( admin_url( 'admin.php?page=myplugin-settings' ) ) . '">' .
					esc_html__( 'API settings', 'my-plugin' ) . '</a>'
				);
				?>
			</p>
		</div>
		<?php
	}
}
```

## Enqueuing Scripts/Styles

```php
add_action( 'admin_menu', 'myplugin_add_settings_page' );

function myplugin_add_settings_page() {
	$hook = add_submenu_page( /* ... */ );

	// Enqueue only on this page
	add_action( 'admin_enqueue_scripts', function( $current_hook ) use ( $hook ) {
		if ( $current_hook !== $hook ) {
			return;
		}

		wp_enqueue_style(
			'myplugin-admin',
			MYPLUGIN_PLUGIN_URL . 'assets/css/admin.css',
			[],
			MYPLUGIN_VERSION
		);

		wp_enqueue_script(
			'myplugin-admin',
			MYPLUGIN_PLUGIN_URL . 'assets/js/admin.js',
			[ 'jquery' ],
			MYPLUGIN_VERSION,
			true
		);

		wp_localize_script( 'myplugin-admin', 'mypluginAdmin', [
			'ajaxUrl' => admin_url( 'admin-ajax.php' ),
			'nonce'   => wp_create_nonce( 'myplugin_admin' ),
		] );
	} );
}
```

## Capabilities

### Common Capabilities

| Capability | Description |
|------------|-------------|
| `manage_options` | Administrator - site settings |
| `manage_woocommerce` | Shop Manager - WooCommerce settings |
| `edit_posts` | Editor/Author - content management |
| `read` | Subscriber - basic dashboard access |

### Custom Capability Check

```php
add_menu_page(
	__( 'Payments', 'my-plugin' ),
	__( 'Payments', 'my-plugin' ),
	'myplugin_manage_payments',  // Custom capability
	'myplugin',
	'myplugin_render_page'
);

// Add capability to administrator role on activation
register_activation_hook( __FILE__, function() {
	$admin = get_role( 'administrator' );
	$admin->add_cap( 'myplugin_manage_payments' );
} );
```

## Best Practices

1. **Use sub-menus** for single-page plugins
2. **Always wrap content** in `<div class="wrap">`
3. **Use `get_admin_page_title()`** for consistent titles
4. **Check capabilities** in both registration and render callbacks
5. **Use Settings API** for options pages when possible
6. **Enqueue assets conditionally** only on your pages
7. **Sanitize tab/action parameters** from `$_GET`/`$_POST`
8. **Use `wp_safe_redirect()`** after form processing
9. **Add nonces** to all forms
10. **Use late priority** (999) when removing menus

## Quick Reference

```php
// Add to Settings menu
add_options_page( $title, $menu, $cap, $slug, $callback );

// Add to Tools menu
add_management_page( $title, $menu, $cap, $slug, $callback );

// Add top-level menu
add_menu_page( $title, $menu, $cap, $slug, $callback, $icon, $pos );

// Add sub-menu to custom menu
add_submenu_page( $parent, $title, $menu, $cap, $slug, $callback );

// Get page URL
menu_page_url( $menu_slug, $echo );

// Get current screen
$screen = get_current_screen();
$screen->id;  // e.g., 'settings_page_myplugin-settings'

// Remove menus
remove_menu_page( $menu_slug );
remove_submenu_page( $parent_slug, $menu_slug );
```