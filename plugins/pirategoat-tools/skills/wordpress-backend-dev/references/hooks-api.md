# WordPress Hooks API

Comprehensive guide to WordPress actions and filters.

## Overview

WordPress hooks allow you to modify or extend functionality without editing core files:

- **Actions**: Execute code at specific points (do something)
- **Filters**: Modify data as it passes through (change something)

## Actions

### Registering Actions

```php
/**
 * Syntax: add_action( $hook_name, $callback, $priority, $accepted_args )
 *
 * @param string   $hook_name     The name of the action hook.
 * @param callable $callback      The function to execute.
 * @param int      $priority      Optional. Execution order. Default 10.
 * @param int      $accepted_args Optional. Number of arguments. Default 1.
 */

// Basic registration
add_action( 'init', 'myplugin_init' );

// With priority (lower = earlier)
add_action( 'init', 'myplugin_early_init', 5 );
add_action( 'init', 'myplugin_late_init', 20 );

// With multiple arguments
add_action( 'save_post', 'myplugin_on_save_post', 10, 3 );

function myplugin_on_save_post( $post_id, $post, $update ) {
	// All three arguments available
}

// Class method
add_action( 'init', [ $this, 'init' ] );
add_action( 'init', [ 'My_Plugin_Manager', 'init' ] ); // Static
```

### Firing Actions

```php
/**
 * Fires after payment is processed.
 *
 * @since 1.0.0
 *
 * @param int    $order_id The order ID.
 * @param string $status   The payment status.
 */
do_action( 'myplugin_payment_processed', $order_id, $status );

// With reference (rarely needed)
do_action_ref_array( 'myplugin_process_items', [ &$items, $order ] );
```

### Removing Actions

```php
// Remove a function
remove_action( 'init', 'myplugin_init' );

// Must match priority
remove_action( 'init', 'myplugin_early_init', 5 );

// Remove class method
remove_action( 'init', [ $instance, 'init' ] );

// Remove all callbacks for a hook
remove_all_actions( 'myplugin_custom_hook' );
```

### Checking Actions

```php
// Check if action is registered
if ( has_action( 'init', 'myplugin_init' ) ) {
	// Action exists
}

// Check if currently running
if ( doing_action( 'init' ) ) {
	// Inside init action
}

// Check if action has run
if ( did_action( 'init' ) ) {
	// init has already fired
}

// Get number of times action fired
$count = did_action( 'init' );
```

## Filters

### Registering Filters

```php
/**
 * Syntax: add_filter( $hook_name, $callback, $priority, $accepted_args )
 */

// Basic registration
add_filter( 'the_title', 'myplugin_modify_title' );

function myplugin_modify_title( $title ) {
	return $title . ' - Modified';
}

// With additional arguments
add_filter( 'the_title', 'myplugin_modify_title_for_post', 10, 2 );

function myplugin_modify_title_for_post( $title, $post_id ) {
	if ( 'shop_order' === get_post_type( $post_id ) ) {
		return 'Order: ' . $title;
	}
	return $title;
}
```

### Applying Filters

```php
/**
 * Filters the payment method title.
 *
 * @since 1.0.0
 *
 * @param string $title     The payment method title.
 * @param string $method_id The payment method ID.
 * @return string Filtered title.
 */
$title = apply_filters( 'myplugin_payment_method_title', $title, $method_id );

// With array reference
$items = apply_filters_ref_array( 'myplugin_cart_items', [ &$items, $cart ] );
```

### Removing Filters

```php
// Remove a filter
remove_filter( 'the_title', 'myplugin_modify_title' );

// Must match priority
remove_filter( 'the_title', 'myplugin_modify_title', 10 );

// Remove all filters
remove_all_filters( 'the_title' );
```

### Checking Filters

```php
// Check if filter is registered
if ( has_filter( 'the_title', 'myplugin_modify_title' ) ) {
	// Filter exists
}

// Get current filter being executed
$current = current_filter();

// Check if filter is being applied
if ( doing_filter( 'the_title' ) ) {
	// Inside the_title filter
}
```

## Common WordPress Hooks

### Initialization Hooks

```php
// Plugin/theme loaded (earliest, no translations)
add_action( 'muplugins_loaded', 'myplugin_mu_loaded' );
add_action( 'plugins_loaded', 'myplugin_plugins_loaded' );

// After WordPress setup, before headers
add_action( 'init', 'myplugin_init' );

// After all plugins initialized
add_action( 'wp_loaded', 'myplugin_loaded' );
```

### Admin Hooks

```php
// Admin initialization
add_action( 'admin_init', 'myplugin_admin_init' );

// Admin menu
add_action( 'admin_menu', 'myplugin_admin_menu' );

// Admin scripts/styles
add_action( 'admin_enqueue_scripts', 'myplugin_admin_scripts' );

function myplugin_admin_scripts( $hook ) {
	// Only on specific pages
	if ( 'toplevel_page_wcpay' !== $hook ) {
		return;
	}
	wp_enqueue_script( 'wcpay-admin', ... );
}

// Admin notices
add_action( 'admin_notices', 'myplugin_admin_notices' );

// Save settings
add_action( 'admin_post_myplugin_save', 'myplugin_handle_save' );
```

### Frontend Hooks

```php
// Enqueue scripts/styles
add_action( 'wp_enqueue_scripts', 'myplugin_frontend_scripts' );

// Head section
add_action( 'wp_head', 'myplugin_add_meta' );

// Footer
add_action( 'wp_footer', 'myplugin_footer_scripts' );

// Template redirect (before output)
add_action( 'template_redirect', 'myplugin_check_access' );
```

### Post Hooks

```php
// Before save
add_action( 'wp_insert_post_data', 'myplugin_modify_post_data', 10, 2 );

// After save
add_action( 'save_post', 'myplugin_on_save', 10, 3 );
add_action( 'save_post_shop_order', 'myplugin_on_save_order', 10, 3 );

// Before delete
add_action( 'before_delete_post', 'myplugin_before_delete' );

// After delete
add_action( 'deleted_post', 'myplugin_after_delete' );

// Status transitions
add_action( 'transition_post_status', 'myplugin_status_change', 10, 3 );
add_action( 'publish_post', 'myplugin_on_publish', 10, 2 );
```

### User Hooks

```php
// User registration
add_action( 'user_register', 'myplugin_on_register' );

// User login
add_action( 'wp_login', 'myplugin_on_login', 10, 2 );

// User logout
add_action( 'wp_logout', 'myplugin_on_logout' );

// Profile update
add_action( 'profile_update', 'myplugin_on_profile_update', 10, 2 );
```

### AJAX Hooks

```php
// For logged-in users
add_action( 'wp_ajax_myplugin_action', 'myplugin_handle_ajax' );

// For non-logged-in users
add_action( 'wp_ajax_nopriv_myplugin_action', 'myplugin_handle_ajax' );
```

### REST API Hooks

```php
// Register routes
add_action( 'rest_api_init', 'myplugin_register_routes' );

// Before/after REST request
add_action( 'rest_pre_dispatch', 'myplugin_before_rest', 10, 3 );
add_action( 'rest_post_dispatch', 'myplugin_after_rest', 10, 3 );
```

### WooCommerce Hooks

```php
// Order processing
add_action( 'woocommerce_checkout_order_processed', 'myplugin_order_processed', 10, 3 );
add_action( 'woocommerce_payment_complete', 'myplugin_payment_complete' );
add_action( 'woocommerce_order_status_changed', 'myplugin_status_changed', 10, 4 );

// Checkout
add_action( 'woocommerce_checkout_update_order_meta', 'myplugin_save_checkout_meta' );
add_action( 'woocommerce_review_order_before_submit', 'myplugin_before_submit' );

// Cart
add_filter( 'woocommerce_add_to_cart_validation', 'myplugin_validate_cart', 10, 5 );
add_action( 'woocommerce_cart_updated', 'myplugin_cart_updated' );

// Payment gateways
add_filter( 'woocommerce_payment_gateways', 'myplugin_add_gateway' );
add_filter( 'woocommerce_available_payment_gateways', 'myplugin_filter_gateways' );

// Product
add_action( 'woocommerce_product_options_general_product_data', 'myplugin_product_options' );
add_action( 'woocommerce_process_product_meta', 'myplugin_save_product_meta' );

// Admin
add_action( 'woocommerce_admin_order_data_after_billing_address', 'myplugin_order_meta_box' );
add_filter( 'woocommerce_admin_order_actions', 'myplugin_order_actions', 10, 2 );
```

## Hook Best Practices

### Naming Conventions

```php
// Use plugin prefix
do_action( 'myplugin_payment_processed', $order_id );
apply_filters( 'myplugin_api_endpoint', $endpoint );

// Use descriptive names: {plugin}_{object}_{action}
do_action( 'myplugin_order_refunded', $order_id, $amount );
do_action( 'myplugin_customer_created', $customer_id );
```

### Documentation

```php
/**
 * Fires after a payment intent is created.
 *
 * Allows extensions to perform additional actions after the payment
 * intent has been successfully created with the payment processor.
 *
 * @since 1.0.0
 * @since 1.5.0 Added $metadata parameter.
 *
 * @param string $intent_id The payment intent ID.
 * @param int    $order_id  The WooCommerce order ID.
 * @param array  $metadata  Additional payment metadata.
 */
do_action( 'myplugin_payment_intent_created', $intent_id, $order_id, $metadata );

/**
 * Filters the payment method configuration.
 *
 * @since 1.0.0
 *
 * @param array  $config    The payment method configuration.
 * @param string $method_id The payment method ID.
 * @return array Modified configuration.
 */
$config = apply_filters( 'myplugin_payment_method_config', $config, $method_id );
```

### Priority Guidelines

```php
// Default priority is 10
// Lower number = earlier execution

// Very early (before most plugins)
add_action( 'init', 'myplugin_very_early', 1 );

// Early
add_action( 'init', 'myplugin_early', 5 );

// Default
add_action( 'init', 'myplugin_normal', 10 );

// Late
add_action( 'init', 'myplugin_late', 20 );

// Very late (after most plugins)
add_action( 'init', 'myplugin_very_late', 99 );

// Extremely late (cleanup)
add_action( 'init', 'myplugin_cleanup', PHP_INT_MAX );
```

### Return Values in Filters

```php
// ALWAYS return a value from filters
add_filter( 'the_title', function( $title ) {
	// WRONG: Missing return
	$title = 'Modified: ' . $title;

	// CORRECT: Return value
	return 'Modified: ' . $title;
} );

// Return original if not modifying
add_filter( 'the_content', function( $content ) {
	if ( ! is_single() ) {
		return $content; // Return unmodified
	}
	return $content . '<p>Additional content</p>';
} );
```

### Conditional Hook Registration

```php
// Only register if condition met
if ( is_admin() ) {
	add_action( 'admin_init', 'myplugin_admin_init' );
}

// Or check inside callback
add_action( 'init', function() {
	if ( ! is_admin() ) {
		return;
	}
	// Admin-only code
} );
```

### Removing Third-Party Hooks

```php
// Wait until plugins_loaded to ensure target is registered
add_action( 'plugins_loaded', function() {
	// Remove with matching signature
	remove_action( 'woocommerce_checkout_process', 'other_plugin_validation' );
}, 20 );

// For class methods, need instance reference
add_action( 'plugins_loaded', function() {
	global $other_plugin;
	if ( isset( $other_plugin ) ) {
		remove_action( 'init', [ $other_plugin, 'init' ] );
	}
}, 20 );
```

## Creating Extensible Code

### Allow Customization

```php
class My_Plugin_Gateway {
	public function get_title() {
		$title = $this->title;

		/**
		 * Filters the gateway title.
		 *
		 * @param string         $title   The gateway title.
		 * @param My_Plugin_Gateway $gateway The gateway instance.
		 */
		return apply_filters( 'myplugin_gateway_title', $title, $this );
	}

	public function process_payment( $order_id ) {
		/**
		 * Fires before payment processing.
		 *
		 * @param int           $order_id The order ID.
		 * @param My_Plugin_Gateway $gateway  The gateway instance.
		 */
		do_action( 'myplugin_before_process_payment', $order_id, $this );

		// Process payment...

		/**
		 * Fires after successful payment.
		 *
		 * @param int           $order_id The order ID.
		 * @param array         $result   The payment result.
		 * @param My_Plugin_Gateway $gateway  The gateway instance.
		 */
		do_action( 'myplugin_after_process_payment', $order_id, $result, $this );
	}
}
```

### Short-Circuit Pattern

```php
function myplugin_get_customer( $user_id ) {
	/**
	 * Short-circuits customer retrieval.
	 *
	 * Return a non-null value to skip default retrieval.
	 *
	 * @param My_Plugin_Customer|null $customer Pre-fetched customer or null.
	 * @param int                  $user_id  The user ID.
	 */
	$pre = apply_filters( 'myplugin_pre_get_customer', null, $user_id );

	if ( null !== $pre ) {
		return $pre;
	}

	// Default retrieval logic
	return myplugin_fetch_customer( $user_id );
}
```