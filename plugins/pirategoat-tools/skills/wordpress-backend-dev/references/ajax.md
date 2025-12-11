# WordPress AJAX API

Comprehensive guide to implementing AJAX in WordPress plugins, including JavaScript enqueuing, request handling, and security.

## Overview

AJAX (Asynchronous JavaScript And XML) enables web pages to communicate with the server without full page reloads. In WordPress:

- All AJAX requests go through `wp-admin/admin-ajax.php`
- PHP handlers use WordPress action hooks
- Full WordPress API is available in AJAX handlers
- JSON is the preferred response format (not XML)

**Benefits:**
- Improved user experience with immediate feedback
- Reduced data transfer (only relevant data exchanged)
- Full access to WordPress functions in handlers
- Integrated security via nonces

## How WordPress AJAX Works

```
1. User Action → JavaScript gathers data
2. JavaScript → HTTP POST to admin-ajax.php
3. admin-ajax.php → Fires wp_ajax_{action} hook
4. PHP Handler → Processes request, returns response
5. JavaScript → Receives response, updates page
```

## Enqueuing JavaScript

### wp_enqueue_script()

```php
/**
 * Enqueues a script.
 *
 * @param string           $handle    Script identifier.
 * @param string           $src       Script URL.
 * @param array            $deps      Dependencies array.
 * @param string|bool|null $ver       Version number.
 * @param array|bool       $args      Footer/strategy options or boolean for footer.
 */
wp_enqueue_script( $handle, $src, $deps, $ver, $args );
```

**Basic Example:**

```php
/**
 * Enqueues admin scripts.
 *
 * @since 1.0.0
 *
 * @param string $hook The current admin page hook.
 */
function myplugin_enqueue_admin_scripts( $hook ) {
    // Only load on our plugin pages
    if ( 'woocommerce_page_wc-admin' !== $hook ) {
        return;
    }

    wp_enqueue_script(
        'wcpay-admin',
        plugins_url( 'assets/js/admin.js', MYPLUGIN_PLUGIN_FILE ),
        [ 'jquery' ],
        MYPLUGIN_VERSION,
        [ 'in_footer' => true ]
    );
}
add_action( 'admin_enqueue_scripts', 'myplugin_enqueue_admin_scripts' );
```

### Enqueue Hooks

| Hook | When to Use | Parameter |
|------|-------------|-----------|
| `admin_enqueue_scripts` | Admin pages | `$hook` (page filename) |
| `wp_enqueue_scripts` | Frontend pages | None |
| `login_enqueue_scripts` | Login page | None |

**Conditional Loading (Admin):**

```php
/**
 * Enqueues scripts only on specific admin pages.
 *
 * @since 1.0.0
 *
 * @param string $hook The current admin page hook.
 */
function myplugin_admin_scripts( $hook ) {
    // Load only on our settings page
    $allowed_hooks = [
        'woocommerce_page_wc-settings',
        'toplevel_page_wcpay',
    ];

    if ( ! in_array( $hook, $allowed_hooks, true ) ) {
        return;
    }

    wp_enqueue_script( 'wcpay-settings', /* ... */ );
}
add_action( 'admin_enqueue_scripts', 'myplugin_admin_scripts' );
```

**Conditional Loading (Frontend):**

```php
/**
 * Enqueues checkout scripts.
 *
 * @since 1.0.0
 */
function myplugin_checkout_scripts() {
    // Only load on checkout page
    if ( ! is_checkout() ) {
        return;
    }

    wp_enqueue_script( 'wcpay-checkout', /* ... */ );
}
add_action( 'wp_enqueue_scripts', 'myplugin_checkout_scripts' );
```

### Script Loading Strategies (WordPress 6.3+)

Control when scripts execute using the `strategy` option:

```php
// Defer: Execute after DOM loads, maintains order
wp_enqueue_script(
    'wcpay-deferred',
    plugins_url( 'assets/js/deferred.js', MYPLUGIN_PLUGIN_FILE ),
    [],
    MYPLUGIN_VERSION,
    [
        'in_footer' => true,
        'strategy'  => 'defer',
    ]
);

// Async: Execute as soon as loaded, no guaranteed order
wp_enqueue_script(
    'wcpay-async',
    plugins_url( 'assets/js/analytics.js', MYPLUGIN_PLUGIN_FILE ),
    [],
    MYPLUGIN_VERSION,
    [
        'in_footer' => true,
        'strategy'  => 'async',
    ]
);
```

| Strategy | Behavior | Use Case |
|----------|----------|----------|
| `defer` | Executes after DOM ready, maintains order | Most scripts, DOM-dependent code |
| `async` | Executes immediately when loaded | Analytics, independent scripts |

### wp_register_script()

Pre-register scripts without immediately enqueuing:

```php
/**
 * Registers scripts for later use.
 *
 * @since 1.0.0
 */
function myplugin_register_scripts() {
    wp_register_script(
        'wcpay-shared',
        plugins_url( 'assets/js/shared.js', MYPLUGIN_PLUGIN_FILE ),
        [ 'jquery' ],
        MYPLUGIN_VERSION,
        true
    );
}
add_action( 'init', 'myplugin_register_scripts' );

// Later, enqueue when needed
function myplugin_maybe_enqueue_shared() {
    if ( some_condition() ) {
        wp_enqueue_script( 'wcpay-shared' );
    }
}
```

## Passing Data to JavaScript

### wp_localize_script()

Pass PHP data to JavaScript by creating a global object:

```php
/**
 * Enqueues scripts with localized data.
 *
 * @since 1.0.0
 */
function myplugin_enqueue_with_data() {
    wp_enqueue_script(
        'wcpay-admin',
        plugins_url( 'assets/js/admin.js', MYPLUGIN_PLUGIN_FILE ),
        [ 'jquery' ],
        MYPLUGIN_VERSION,
        true
    );

    wp_localize_script(
        'wcpay-admin',
        'myplugin_ajax_obj',
        [
            'ajax_url' => admin_url( 'admin-ajax.php' ),
            'nonce'    => wp_create_nonce( 'myplugin_admin_action' ),
            'strings'  => [
                'confirm_delete' => __( 'Are you sure?', 'my-plugin' ),
                'processing'     => __( 'Processing...', 'my-plugin' ),
                'error'          => __( 'An error occurred.', 'my-plugin' ),
            ],
            'settings' => [
                'currency'   => get_woocommerce_currency(),
                'debug_mode' => defined( 'WP_DEBUG' ) && WP_DEBUG,
            ],
        ]
    );
}
add_action( 'admin_enqueue_scripts', 'myplugin_enqueue_with_data' );
```

**In JavaScript:**

```javascript
// Access localized data
console.log( myplugin_ajax_obj.ajax_url );      // admin-ajax.php URL
console.log( myplugin_ajax_obj.nonce );          // Security nonce
console.log( myplugin_ajax_obj.strings.error );  // Translated string
console.log( myplugin_ajax_obj.settings.currency ); // 'USD'
```

### wp_add_inline_script()

Add inline JavaScript before or after an enqueued script:

```php
wp_enqueue_script( 'wcpay-checkout', /* ... */ );

// Add configuration before the script
wp_add_inline_script(
    'wcpay-checkout',
    'var wcpayConfig = ' . wp_json_encode( [
        'publishableKey' => $publishable_key,
        'accountId'      => $account_id,
    ] ) . ';',
    'before'
);

// Add initialization after the script
wp_add_inline_script(
    'wcpay-checkout',
    'wcpayCheckout.init();',
    'after'
);
```

## AJAX URL

### Admin Pages

In admin, `ajaxurl` is globally available:

```javascript
// Admin pages - ajaxurl is predefined
jQuery.post( ajaxurl, { action: 'my_action' } );
```

### Frontend Pages

On the frontend, pass the URL via `wp_localize_script()`:

```php
wp_localize_script( 'my-script', 'my_obj', [
    'ajax_url' => admin_url( 'admin-ajax.php' ),
] );
```

```javascript
// Frontend - use localized URL
jQuery.post( my_obj.ajax_url, { action: 'my_action' } );
```

## AJAX Handlers (PHP)

### Hook Structure

WordPress fires action hooks based on the `action` parameter:

| User Type | Hook Format |
|-----------|-------------|
| Logged-in users | `wp_ajax_{action}` |
| Non-logged-in users | `wp_ajax_nopriv_{action}` |

### Basic Handler Pattern

**CRITICAL**: Every AJAX handler MUST follow the 5-step security pattern from `security.md#complete-handler-pattern`:

1. **Verify nonce** - `check_ajax_referer()`
2. **Check capability** - `current_user_can()`
3. **Sanitize input** - `absint()`, `sanitize_text_field()`, etc.
4. **Process request** - Your business logic
5. **Send response** - `wp_send_json_success()` / `wp_send_json_error()`

```php
add_action( 'wp_ajax_myplugin_get_transactions', 'myplugin_ajax_get_transactions' );

function myplugin_ajax_get_transactions() {
    check_ajax_referer( 'myplugin_admin_action', 'nonce' );  // 1. Nonce

    if ( ! current_user_can( 'manage_woocommerce' ) ) {       // 2. Capability
        wp_send_json_error( [ 'message' => 'Permission denied.' ], 403 );
    }

    $page = absint( $_POST['page'] ?? 1 );                   // 3. Sanitize
    $status = sanitize_key( $_POST['status'] ?? '' );

    $result = myplugin_fetch_transactions( $page, $status ); // 4. Process

    if ( is_wp_error( $result ) ) {
        wp_send_json_error( [ 'message' => $result->get_error_message() ] );
    }

    wp_send_json_success( $result );                         // 5. Respond
}
```

### Handler for Both Logged-in and Non-logged-in Users

```php
// Both hooks for public functionality
add_action( 'wp_ajax_myplugin_check_status', 'myplugin_ajax_check_status' );
add_action( 'wp_ajax_nopriv_myplugin_check_status', 'myplugin_ajax_check_status' );

/**
 * AJAX handler for checking payment status.
 *
 * @since 1.0.0
 */
function myplugin_ajax_check_status() {
    // Verify nonce
    check_ajax_referer( 'myplugin_public_action', 'nonce' );

    // Sanitize input
    $order_key = isset( $_POST['order_key'] ) ? sanitize_text_field( wp_unslash( $_POST['order_key'] ) ) : '';

    if ( empty( $order_key ) ) {
        wp_send_json_error( [ 'message' => __( 'Invalid order.', 'my-plugin' ) ] );
    }

    // Get order by key (works for guests)
    $order_id = wc_get_order_id_by_order_key( $order_key );
    $order = wc_get_order( $order_id );

    if ( ! $order ) {
        wp_send_json_error( [ 'message' => __( 'Order not found.', 'my-plugin' ) ] );
    }

    wp_send_json_success( [
        'status' => $order->get_status(),
    ] );
}
```

## Response Functions

### wp_send_json_success()

```php
/**
 * Sends a JSON success response.
 *
 * @param mixed $data    Data to encode (appears in response.data).
 * @param int   $status  Optional. HTTP status code. Default 200.
 * @param int   $options Optional. JSON encoding options.
 */
wp_send_json_success( $data, $status, $options );
```

**Response format:**
```json
{
    "success": true,
    "data": { /* your data */ }
}
```

### wp_send_json_error()

```php
/**
 * Sends a JSON error response.
 *
 * @param mixed $data   Data to encode (appears in response.data).
 * @param int   $status Optional. HTTP status code. Default 200.
 * @param int   $options Optional. JSON encoding options.
 */
wp_send_json_error( $data, $status, $options );
```

**Response format:**
```json
{
    "success": false,
    "data": { /* your data */ }
}
```

### wp_send_json()

For custom response format:

```php
wp_send_json( [
    'status' => 'pending',
    'items'  => $items,
] );
```

**Note:** All `wp_send_json_*` functions call `wp_die()` automatically - no code runs after them.

## JavaScript Implementation

### jQuery AJAX

```javascript
(function($) {
    'use strict';

    /**
     * Handle form submission via AJAX.
     */
    $('#wcpay-form').on('submit', function(e) {
        e.preventDefault();

        var $form = $(this);
        var $button = $form.find('button[type="submit"]');
        var $message = $form.find('.wcpay-message');

        // Disable button during request
        $button.prop('disabled', true).text(myplugin_ajax_obj.strings.processing);

        $.ajax({
            url: myplugin_ajax_obj.ajax_url,
            type: 'POST',
            data: {
                action: 'myplugin_process_payment',
                nonce: myplugin_ajax_obj.nonce,
                order_id: $form.find('#order_id').val(),
                amount: $form.find('#amount').val()
            },
            success: function(response) {
                if (response.success) {
                    $message
                        .removeClass('error')
                        .addClass('success')
                        .text(response.data.message)
                        .show();

                    // Redirect on success
                    if (response.data.redirect_url) {
                        window.location.href = response.data.redirect_url;
                    }
                } else {
                    $message
                        .removeClass('success')
                        .addClass('error')
                        .text(response.data.message)
                        .show();
                }
            },
            error: function(xhr, status, error) {
                $message
                    .removeClass('success')
                    .addClass('error')
                    .text(myplugin_ajax_obj.strings.error)
                    .show();

                console.error('AJAX error:', status, error);
            },
            complete: function() {
                // Re-enable button
                $button.prop('disabled', false).text(myplugin_ajax_obj.strings.submit);
            }
        });
    });

})(jQuery);
```

### Vanilla JavaScript (Fetch API)

```javascript
/**
 * Process payment via AJAX.
 *
 * @param {Object} data Payment data.
 * @returns {Promise} Response promise.
 */
async function wcpayProcessPayment(data) {
    const formData = new FormData();
    formData.append('action', 'myplugin_process_payment');
    formData.append('nonce', myplugin_ajax_obj.nonce);
    formData.append('order_id', data.orderId);
    formData.append('amount', data.amount);

    try {
        const response = await fetch(myplugin_ajax_obj.ajax_url, {
            method: 'POST',
            credentials: 'same-origin',
            body: formData
        });

        const result = await response.json();

        if (result.success) {
            return result.data;
        } else {
            throw new Error(result.data.message || 'Unknown error');
        }
    } catch (error) {
        console.error('Payment error:', error);
        throw error;
    }
}

// Usage
document.getElementById('pay-button').addEventListener('click', async function(e) {
    e.preventDefault();

    this.disabled = true;
    this.textContent = myplugin_ajax_obj.strings.processing;

    try {
        const result = await wcpayProcessPayment({
            orderId: document.getElementById('order_id').value,
            amount: document.getElementById('amount').value
        });

        if (result.redirect_url) {
            window.location.href = result.redirect_url;
        }
    } catch (error) {
        document.getElementById('error-message').textContent = error.message;
    } finally {
        this.disabled = false;
        this.textContent = myplugin_ajax_obj.strings.submit;
    }
});
```

### jQuery $.post() Shorthand

```javascript
$.post(myplugin_ajax_obj.ajax_url, {
    action: 'myplugin_quick_action',
    nonce: myplugin_ajax_obj.nonce,
    item_id: itemId
}, function(response) {
    if (response.success) {
        console.log('Success:', response.data);
    } else {
        console.error('Error:', response.data.message);
    }
});
```

## Security

### Nonces

**Create nonce (PHP):**

```php
// In wp_localize_script
'nonce' => wp_create_nonce( 'myplugin_action' ),

// In forms
wp_nonce_field( 'myplugin_action', 'myplugin_nonce' );

// URL parameter
$url = wp_nonce_url( $url, 'myplugin_action', '_myplugin_nonce' );
```

**Verify nonce (PHP):**

```php
// In AJAX handler - dies on failure
check_ajax_referer( 'myplugin_action', 'nonce' );

// Or with custom handling
if ( ! wp_verify_nonce( sanitize_key( $_POST['nonce'] ), 'myplugin_action' ) ) {
    wp_send_json_error( [ 'message' => 'Invalid security token' ], 403 );
}
```

**Send nonce (JavaScript):**

```javascript
// POST data
{
    action: 'myplugin_action',
    nonce: myplugin_ajax_obj.nonce,  // or _ajax_nonce
    // ... other data
}
```

### Capability Checks

Always verify user capabilities:

```php
function myplugin_ajax_admin_action() {
    check_ajax_referer( 'myplugin_admin', 'nonce' );

    // Must have capability
    if ( ! current_user_can( 'manage_woocommerce' ) ) {
        wp_send_json_error( [ 'message' => 'Unauthorized' ], 403 );
    }

    // Process...
}
```

### Input Sanitization

Sanitize ALL input data:

```php
function myplugin_ajax_handler() {
    check_ajax_referer( 'myplugin_action', 'nonce' );

    // Integers
    $order_id = isset( $_POST['order_id'] ) ? absint( $_POST['order_id'] ) : 0;

    // Strings
    $note = isset( $_POST['note'] ) ? sanitize_textarea_field( wp_unslash( $_POST['note'] ) ) : '';

    // Keys/slugs
    $status = isset( $_POST['status'] ) ? sanitize_key( $_POST['status'] ) : '';

    // Email
    $email = isset( $_POST['email'] ) ? sanitize_email( $_POST['email'] ) : '';

    // Validate required fields
    if ( ! $order_id ) {
        wp_send_json_error( [ 'message' => 'Order ID required' ] );
    }

    // Process...
}
```

### Avoid $_REQUEST

Use `$_POST` or `$_GET` specifically - never `$_REQUEST`:

```php
// WRONG - vulnerable to cookie override
$action = $_REQUEST['action'];

// CORRECT - specific superglobal
$action = isset( $_POST['action'] ) ? sanitize_key( $_POST['action'] ) : '';
```

## Complete Implementation Example

### PHP (Plugin File)

```php
/**
 * Enqueues AJAX scripts and handlers.
 *
 * @since 1.0.0
 */
class My_Plugin_AJAX {

    /**
     * Initializes AJAX functionality.
     */
    public function __construct() {
        add_action( 'admin_enqueue_scripts', [ $this, 'enqueue_admin_scripts' ] );
        add_action( 'wp_enqueue_scripts', [ $this, 'enqueue_frontend_scripts' ] );

        // Admin AJAX handlers
        add_action( 'wp_ajax_myplugin_refund', [ $this, 'handle_refund' ] );
        add_action( 'wp_ajax_myplugin_capture', [ $this, 'handle_capture' ] );

        // Frontend AJAX handlers (logged-in and guest)
        add_action( 'wp_ajax_myplugin_update_cart', [ $this, 'handle_update_cart' ] );
        add_action( 'wp_ajax_nopriv_myplugin_update_cart', [ $this, 'handle_update_cart' ] );
    }

    /**
     * Enqueues admin scripts.
     *
     * @since 1.0.0
     *
     * @param string $hook Current admin page.
     */
    public function enqueue_admin_scripts( $hook ) {
        if ( 'woocommerce_page_wc-orders' !== $hook ) {
            return;
        }

        wp_enqueue_script(
            'wcpay-admin-orders',
            plugins_url( 'assets/js/admin-orders.js', MYPLUGIN_PLUGIN_FILE ),
            [ 'jquery' ],
            MYPLUGIN_VERSION,
            true
        );

        wp_localize_script(
            'wcpay-admin-orders',
            'myplugin_admin',
            [
                'ajax_url' => admin_url( 'admin-ajax.php' ),
                'nonce'    => wp_create_nonce( 'myplugin_admin_orders' ),
                'strings'  => [
                    'confirm_refund'  => __( 'Process this refund?', 'my-plugin' ),
                    'confirm_capture' => __( 'Capture this payment?', 'my-plugin' ),
                    'processing'      => __( 'Processing...', 'my-plugin' ),
                    'success'         => __( 'Success!', 'my-plugin' ),
                    'error'           => __( 'An error occurred.', 'my-plugin' ),
                ],
            ]
        );
    }

    /**
     * Enqueues frontend scripts.
     *
     * @since 1.0.0
     */
    public function enqueue_frontend_scripts() {
        if ( ! is_cart() && ! is_checkout() ) {
            return;
        }

        wp_enqueue_script(
            'wcpay-cart',
            plugins_url( 'assets/js/cart.js', MYPLUGIN_PLUGIN_FILE ),
            [ 'jquery' ],
            MYPLUGIN_VERSION,
            true
        );

        wp_localize_script(
            'wcpay-cart',
            'myplugin_cart',
            [
                'ajax_url' => admin_url( 'admin-ajax.php' ),
                'nonce'    => wp_create_nonce( 'myplugin_cart' ),
            ]
        );
    }

    /**
     * Handles refund AJAX request.
     *
     * @since 1.0.0
     */
    public function handle_refund() {
        check_ajax_referer( 'myplugin_admin_orders', 'nonce' );

        if ( ! current_user_can( 'manage_woocommerce' ) ) {
            wp_send_json_error( [ 'message' => __( 'Permission denied.', 'my-plugin' ) ], 403 );
        }

        $order_id = isset( $_POST['order_id'] ) ? absint( $_POST['order_id'] ) : 0;
        $amount = isset( $_POST['amount'] ) ? floatval( $_POST['amount'] ) : 0;
        $reason = isset( $_POST['reason'] ) ? sanitize_textarea_field( wp_unslash( $_POST['reason'] ) ) : '';

        if ( ! $order_id || $amount <= 0 ) {
            wp_send_json_error( [ 'message' => __( 'Invalid refund data.', 'my-plugin' ) ] );
        }

        $order = wc_get_order( $order_id );
        if ( ! $order ) {
            wp_send_json_error( [ 'message' => __( 'Order not found.', 'my-plugin' ) ] );
        }

        // Process refund
        $result = $this->process_refund( $order, $amount, $reason );

        if ( is_wp_error( $result ) ) {
            wp_send_json_error( [ 'message' => $result->get_error_message() ] );
        }

        wp_send_json_success( [
            'message'    => __( 'Refund processed successfully.', 'my-plugin' ),
            'refund_id'  => $result,
            'new_status' => $order->get_status(),
        ] );
    }

    /**
     * Handles capture AJAX request.
     *
     * @since 1.0.0
     */
    public function handle_capture() {
        check_ajax_referer( 'myplugin_admin_orders', 'nonce' );

        if ( ! current_user_can( 'manage_woocommerce' ) ) {
            wp_send_json_error( [ 'message' => __( 'Permission denied.', 'my-plugin' ) ], 403 );
        }

        $order_id = isset( $_POST['order_id'] ) ? absint( $_POST['order_id'] ) : 0;

        if ( ! $order_id ) {
            wp_send_json_error( [ 'message' => __( 'Invalid order ID.', 'my-plugin' ) ] );
        }

        $order = wc_get_order( $order_id );
        if ( ! $order ) {
            wp_send_json_error( [ 'message' => __( 'Order not found.', 'my-plugin' ) ] );
        }

        // Process capture
        $result = $this->capture_payment( $order );

        if ( is_wp_error( $result ) ) {
            wp_send_json_error( [ 'message' => $result->get_error_message() ] );
        }

        wp_send_json_success( [
            'message'    => __( 'Payment captured successfully.', 'my-plugin' ),
            'new_status' => $order->get_status(),
        ] );
    }

    /**
     * Handles cart update AJAX request.
     *
     * @since 1.0.0
     */
    public function handle_update_cart() {
        check_ajax_referer( 'myplugin_cart', 'nonce' );

        $cart_item_key = isset( $_POST['cart_item_key'] ) ? sanitize_key( $_POST['cart_item_key'] ) : '';
        $quantity = isset( $_POST['quantity'] ) ? absint( $_POST['quantity'] ) : 0;

        if ( empty( $cart_item_key ) ) {
            wp_send_json_error( [ 'message' => __( 'Invalid cart item.', 'my-plugin' ) ] );
        }

        WC()->cart->set_quantity( $cart_item_key, $quantity );

        wp_send_json_success( [
            'cart_total' => WC()->cart->get_cart_total(),
            'item_count' => WC()->cart->get_cart_contents_count(),
        ] );
    }
}

// Initialize
new My_Plugin_AJAX();
```

### JavaScript (admin-orders.js)

```javascript
(function($) {
    'use strict';

    var WCPayOrders = {
        init: function() {
            this.bindEvents();
        },

        bindEvents: function() {
            $(document).on('click', '.wcpay-refund-btn', this.handleRefund);
            $(document).on('click', '.wcpay-capture-btn', this.handleCapture);
        },

        handleRefund: function(e) {
            e.preventDefault();

            var $btn = $(this);
            var orderId = $btn.data('order-id');
            var amount = $('#wcpay-refund-amount').val();
            var reason = $('#wcpay-refund-reason').val();

            if (!confirm(myplugin_admin.strings.confirm_refund)) {
                return;
            }

            WCPayOrders.sendRequest('myplugin_refund', {
                order_id: orderId,
                amount: amount,
                reason: reason
            }, $btn);
        },

        handleCapture: function(e) {
            e.preventDefault();

            var $btn = $(this);
            var orderId = $btn.data('order-id');

            if (!confirm(myplugin_admin.strings.confirm_capture)) {
                return;
            }

            WCPayOrders.sendRequest('myplugin_capture', {
                order_id: orderId
            }, $btn);
        },

        sendRequest: function(action, data, $btn) {
            var originalText = $btn.text();

            $btn.prop('disabled', true).text(myplugin_admin.strings.processing);

            $.ajax({
                url: myplugin_admin.ajax_url,
                type: 'POST',
                data: $.extend({
                    action: action,
                    nonce: myplugin_admin.nonce
                }, data),
                success: function(response) {
                    if (response.success) {
                        WCPayOrders.showNotice('success', response.data.message);

                        // Refresh page to show updated status
                        if (response.data.new_status) {
                            location.reload();
                        }
                    } else {
                        WCPayOrders.showNotice('error', response.data.message);
                    }
                },
                error: function() {
                    WCPayOrders.showNotice('error', myplugin_admin.strings.error);
                },
                complete: function() {
                    $btn.prop('disabled', false).text(originalText);
                }
            });
        },

        showNotice: function(type, message) {
            var $notice = $('<div class="notice notice-' + type + ' is-dismissible"><p>' + message + '</p></div>');
            $('.wrap h1').after($notice);

            // Auto-dismiss after 5 seconds
            setTimeout(function() {
                $notice.fadeOut(function() {
                    $(this).remove();
                });
            }, 5000);
        }
    };

    $(document).ready(function() {
        WCPayOrders.init();
    });

})(jQuery);
```

## Debugging AJAX

### PHP Debugging

```php
function myplugin_ajax_debug() {
    check_ajax_referer( 'myplugin_action', 'nonce' );

    // Log received data
    if ( defined( 'WP_DEBUG' ) && WP_DEBUG ) {
        error_log( 'AJAX Request Data: ' . print_r( $_POST, true ) );
    }

    // ... handler code ...

    // Log response
    $response = [ 'status' => 'success' ];
    if ( defined( 'WP_DEBUG' ) && WP_DEBUG ) {
        error_log( 'AJAX Response: ' . print_r( $response, true ) );
    }

    wp_send_json_success( $response );
}
```

### JavaScript Debugging

```javascript
$.ajax({
    url: myplugin_ajax_obj.ajax_url,
    type: 'POST',
    data: data,
    beforeSend: function(xhr, settings) {
        console.log('Request URL:', settings.url);
        console.log('Request Data:', settings.data);
    },
    success: function(response) {
        console.log('Response:', response);
    },
    error: function(xhr, status, error) {
        console.error('XHR:', xhr);
        console.error('Status:', status);
        console.error('Error:', error);
        console.error('Response Text:', xhr.responseText);
    }
});
```

### Common Issues

| Issue | Cause | Solution |
|-------|-------|----------|
| 0 response | Handler not registered or wrong action | Check hook name matches action parameter |
| -1 response | Nonce verification failed | Check nonce creation/verification match |
| 403 Forbidden | Capability check failed | Verify user has required capability |
| 400 Bad Request | Missing action parameter | Ensure `action` is in POST data |
| HTML in response | PHP error/warning | Check error logs, enable WP_DEBUG |

## Summary Checklist

### PHP Handler
- [ ] Register both `wp_ajax_` hooks (and `wp_ajax_nopriv_` if needed)
- [ ] Verify nonce with `check_ajax_referer()` or `wp_verify_nonce()`
- [ ] Check user capabilities with `current_user_can()`
- [ ] Sanitize ALL input from `$_POST`
- [ ] Validate required fields
- [ ] Use `wp_send_json_success()` / `wp_send_json_error()`
- [ ] Return appropriate HTTP status codes

### JavaScript
- [ ] Enqueue script with proper dependencies
- [ ] Pass `ajax_url` and `nonce` via `wp_localize_script()`
- [ ] Include `action` and `nonce` in request data
- [ ] Handle both success and error responses
- [ ] Provide user feedback (loading states, messages)
- [ ] Disable buttons during requests

### Enqueuing
- [ ] Use correct hook (`admin_enqueue_scripts` vs `wp_enqueue_scripts`)
- [ ] Load scripts conditionally (check page/hook)
- [ ] Set appropriate dependencies (e.g., `['jquery']`)
- [ ] Load in footer when possible (`'in_footer' => true`)
- [ ] Use `plugins_url()` for portable paths