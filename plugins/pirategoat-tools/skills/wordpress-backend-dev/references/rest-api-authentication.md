# REST API Authentication

Complete guide to authentication methods for the WordPress REST API.

## Overview

The REST API supports multiple authentication approaches depending on your use case:

| Method | Use Case | Security |
|--------|----------|----------|
| Cookie + Nonce | Same-origin JS requests | High |
| Application Passwords | External applications | High (HTTPS required) |
| OAuth | Third-party integrations | High |
| Basic Auth | Development only | Low |

## Cookie Authentication (Default)

Standard authentication for JavaScript within WordPress admin or front-end.

### How It Works

When users log into WordPress, authentication cookies are set. The REST API validates these cookies for same-origin requests. However, to prevent CSRF attacks, you must also include a **nonce**.

### Nonce Requirement

**CRITICAL**: Without a valid nonce, the API treats requests as unauthenticated (user ID = 0), even if the user is logged in.

The nonce action must be `wp_rest`.

### Setup

```php
// Enqueue your script with REST API data
add_action( 'wp_enqueue_scripts', 'myplugin_enqueue_scripts' );

function myplugin_enqueue_scripts() {
	wp_enqueue_script(
		'myplugin-app',
		MYPLUGIN_PLUGIN_URL . 'assets/js/app.js',
		[ 'wp-api-fetch' ],  // Optional: use WordPress fetch wrapper
		MYPLUGIN_VERSION,
		true
	);

	wp_localize_script( 'myplugin-app', 'mypluginApi', [
		'root'  => esc_url_raw( rest_url( 'myplugin/v1' ) ),
		'nonce' => wp_create_nonce( 'wp_rest' ),
	] );
}
```

### JavaScript Usage

**Using Fetch API:**

```javascript
// GET request
fetch( mypluginApi.root + '/orders', {
	headers: {
		'X-WP-Nonce': mypluginApi.nonce,
	},
} )
.then( response => response.json() )
.then( data => console.log( data ) );

// POST request
fetch( mypluginApi.root + '/orders', {
	method: 'POST',
	headers: {
		'X-WP-Nonce': mypluginApi.nonce,
		'Content-Type': 'application/json',
	},
	body: JSON.stringify( { amount: 100 } ),
} );
```

**Using jQuery:**

```javascript
$.ajax( {
	url: mypluginApi.root + '/orders',
	method: 'POST',
	beforeSend: function( xhr ) {
		xhr.setRequestHeader( 'X-WP-Nonce', mypluginApi.nonce );
	},
	data: { amount: 100 },
} ).done( function( response ) {
	console.log( response );
} );
```

**Using WordPress apiFetch (Recommended):**

```javascript
import apiFetch from '@wordpress/api-fetch';

// Nonce is automatically included
apiFetch( { path: '/myplugin/v1/orders' } )
	.then( data => console.log( data ) );

apiFetch( {
	path: '/myplugin/v1/orders',
	method: 'POST',
	data: { amount: 100 },
} );
```

### Nonce Delivery Methods

**Header (Recommended, especially for DELETE):**
```javascript
headers: { 'X-WP-Nonce': mypluginApi.nonce }
```

**Query Parameter:**
```
/wp-json/myplugin/v1/orders?_wpnonce=abc123
```

**POST Body:**
```javascript
data: { _wpnonce: mypluginApi.nonce, amount: 100 }
```

### Nonce Expiration

Nonces are valid for 24 hours (two 12-hour periods). For long-running applications, refresh nonces periodically:

```javascript
// Refresh nonce via AJAX
function refreshNonce() {
	fetch( ajaxurl, {
		method: 'POST',
		headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
		body: 'action=myplugin_refresh_nonce&_wpnonce=' + mypluginApi.nonce,
	} )
	.then( response => response.json() )
	.then( data => {
		mypluginApi.nonce = data.nonce;
	} );
}

// PHP handler
add_action( 'wp_ajax_myplugin_refresh_nonce', function() {
	wp_send_json( [ 'nonce' => wp_create_nonce( 'wp_rest' ) ] );
} );
```

## Application Passwords (WordPress 5.6+)

For external applications and server-to-server communication.

### Generating Passwords

Users create application passwords from:
- **Users > Profile > Application Passwords**
- Or programmatically via REST API

### Usage

Application passwords use **HTTP Basic Authentication** (RFC 7617):

```
Authorization: Basic base64(username:password)
```

**cURL Example:**

```bash
# Direct credentials
curl --user "admin:xxxx xxxx xxxx xxxx xxxx xxxx" \
  https://example.com/wp-json/wp/v2/posts

# Base64 encoded
curl -H "Authorization: Basic YWRtaW46eHh4eCB4eHh4IHh4eHggeHh4eCB4eHh4IHh4eHg=" \
  https://example.com/wp-json/wp/v2/posts
```

**PHP Example:**

```php
$response = wp_remote_get(
	'https://example.com/wp-json/wp/v2/posts',
	[
		'headers' => [
			'Authorization' => 'Basic ' . base64_encode( 'admin:xxxx xxxx xxxx xxxx xxxx xxxx' ),
		],
	]
);
```

**JavaScript Example:**

```javascript
const credentials = btoa( 'admin:xxxx xxxx xxxx xxxx xxxx xxxx' );

fetch( 'https://example.com/wp-json/wp/v2/posts', {
	headers: {
		'Authorization': `Basic ${credentials}`,
	},
} );
```

### Security Requirements

1. **HTTPS Only** - Application passwords only work over encrypted connections
2. **Unique per Application** - Generate separate passwords for each integration
3. **Revocable** - Users can revoke individual application passwords

### Programmatic Management

```php
// Create application password
$user_id = get_current_user_id();
$app_pass = WP_Application_Passwords::create_new_application_password(
	$user_id,
	[
		'name' => 'My Integration',
	]
);

// Returns: [ 'password' => 'xxxx xxxx...', 'uuid' => '...' ]

// List application passwords
$passwords = WP_Application_Passwords::get_user_application_passwords( $user_id );

// Delete application password
WP_Application_Passwords::delete_application_password( $user_id, $uuid );
```

### REST API Endpoints

```
GET    /wp/v2/users/<user_id>/application-passwords
POST   /wp/v2/users/<user_id>/application-passwords
DELETE /wp/v2/users/<user_id>/application-passwords/<uuid>
```

## Custom Authentication

Implement custom authentication for specialized use cases.

### Authentication Filter

```php
add_filter( 'rest_authentication_errors', 'myplugin_custom_auth', 10, 1 );

function myplugin_custom_auth( $result ) {
	// If another auth method already handled this, respect it
	if ( null !== $result ) {
		return $result;
	}

	// Check for custom auth header
	$auth_header = isset( $_SERVER['HTTP_X_MYPLUGIN_API_KEY'] )
		? sanitize_text_field( wp_unslash( $_SERVER['HTTP_X_MYPLUGIN_API_KEY'] ) )
		: '';

	if ( empty( $auth_header ) ) {
		// No custom auth provided, let other methods handle it
		return null;
	}

	// Validate the API key
	$user_id = myplugin_validate_api_key( $auth_header );

	if ( ! $user_id ) {
		return new WP_Error(
			'myplugin_invalid_api_key',
			__( 'Invalid API key.', 'my-plugin' ),
			[ 'status' => 401 ]
		);
	}

	// Set the current user
	wp_set_current_user( $user_id );

	return true;
}

function myplugin_validate_api_key( $key ) {
	global $wpdb;

	$user_id = $wpdb->get_var(
		$wpdb->prepare(
			"SELECT user_id FROM {$wpdb->usermeta}
			WHERE meta_key = '_myplugin_api_key'
			AND meta_value = %s",
			hash( 'sha256', $key )
		)
	);

	return $user_id ? absint( $user_id ) : false;
}
```

### Token-Based Authentication

```php
add_filter( 'rest_authentication_errors', 'myplugin_jwt_auth', 10, 1 );

function myplugin_jwt_auth( $result ) {
	if ( null !== $result ) {
		return $result;
	}

	$auth_header = isset( $_SERVER['HTTP_AUTHORIZATION'] )
		? sanitize_text_field( wp_unslash( $_SERVER['HTTP_AUTHORIZATION'] ) )
		: '';

	// Check for Bearer token
	if ( 0 !== strpos( $auth_header, 'Bearer ' ) ) {
		return null;
	}

	$token = substr( $auth_header, 7 );

	try {
		$payload = myplugin_decode_jwt( $token );

		// Validate expiration
		if ( $payload['exp'] < time() ) {
			return new WP_Error(
				'myplugin_token_expired',
				__( 'Token has expired.', 'my-plugin' ),
				[ 'status' => 401 ]
			);
		}

		wp_set_current_user( $payload['user_id'] );
		return true;

	} catch ( Exception $e ) {
		return new WP_Error(
			'myplugin_invalid_token',
			__( 'Invalid token.', 'my-plugin' ),
			[ 'status' => 401 ]
		);
	}
}
```

## Determining Current User

### In Endpoint Callbacks

```php
function myplugin_get_orders( WP_REST_Request $request ) {
	// Get current user (authenticated via any method)
	$user_id = get_current_user_id();

	if ( 0 === $user_id ) {
		// Unauthenticated request
		return new WP_Error(
			'myplugin_not_authenticated',
			__( 'Authentication required.', 'my-plugin' ),
			[ 'status' => 401 ]
		);
	}

	// Get user object
	$user = wp_get_current_user();

	// Check capabilities
	if ( ! current_user_can( 'manage_woocommerce' ) ) {
		return new WP_Error(
			'myplugin_forbidden',
			__( 'Insufficient permissions.', 'my-plugin' ),
			[ 'status' => 403 ]
		);
	}

	// Proceed with request...
}
```

### Authentication Context

```php
function myplugin_check_auth_context( WP_REST_Request $request ) {
	// Check if authenticated via application password
	if ( $request->get_header( 'authorization' ) ) {
		// Likely application password or basic auth
	}

	// Check if authenticated via nonce (same-origin)
	$nonce = $request->get_header( 'x-wp-nonce' );
	if ( $nonce && wp_verify_nonce( $nonce, 'wp_rest' ) ) {
		// Cookie + nonce authentication
	}
}
```

## Security Best Practices

### 1. Always Use HTTPS

```php
// Require HTTPS for sensitive endpoints
function myplugin_require_https( WP_REST_Request $request ) {
	if ( ! is_ssl() && ! defined( 'WP_DEBUG' ) ) {
		return new WP_Error(
			'myplugin_https_required',
			__( 'HTTPS is required.', 'my-plugin' ),
			[ 'status' => 403 ]
		);
	}
	return true;
}
```

### 2. Rate Limiting

```php
add_filter( 'rest_pre_dispatch', 'myplugin_rate_limit', 10, 3 );

function myplugin_rate_limit( $result, $server, $request ) {
	// Only apply to wcpay routes
	if ( 0 !== strpos( $request->get_route(), '/myplugin/' ) ) {
		return $result;
	}

	$user_id = get_current_user_id();
	$key = 'myplugin_rate_' . ( $user_id ?: md5( myplugin_get_client_ip() ) );

	$count = (int) get_transient( $key );

	if ( $count >= 100 ) {
		return new WP_Error(
			'myplugin_rate_limited',
			__( 'Too many requests. Please try again later.', 'my-plugin' ),
			[ 'status' => 429 ]
		);
	}

	set_transient( $key, $count + 1, MINUTE_IN_SECONDS );

	return $result;
}

function myplugin_get_client_ip() {
	$ip = '';

	if ( ! empty( $_SERVER['HTTP_X_FORWARDED_FOR'] ) ) {
		$ip = sanitize_text_field( wp_unslash( $_SERVER['HTTP_X_FORWARDED_FOR'] ) );
		$ip = explode( ',', $ip )[0];
	} elseif ( ! empty( $_SERVER['REMOTE_ADDR'] ) ) {
		$ip = sanitize_text_field( wp_unslash( $_SERVER['REMOTE_ADDR'] ) );
	}

	return $ip;
}
```

### 3. Validate Permissions Per-Request

```php
// Always check permissions, even for authenticated users
'permission_callback' => function( WP_REST_Request $request ) {
	// Verify user can access this specific resource
	$order_id = $request->get_param( 'id' );
	$order = wc_get_order( $order_id );

	if ( ! $order ) {
		return new WP_Error( 'not_found', 'Order not found', [ 'status' => 404 ] );
	}

	// Admin can access any order
	if ( current_user_can( 'manage_woocommerce' ) ) {
		return true;
	}

	// User can only access their own orders
	return get_current_user_id() === $order->get_customer_id();
}
```

### 4. Log Authentication Failures

```php
add_filter( 'rest_authentication_errors', 'myplugin_log_auth_failures', 999, 1 );

function myplugin_log_auth_failures( $result ) {
	if ( is_wp_error( $result ) ) {
		myplugin_log(
			sprintf(
				'REST API auth failure: %s (IP: %s)',
				$result->get_error_message(),
				myplugin_get_client_ip()
			),
			'warning'
		);
	}
	return $result;
}
```

### 5. Don't Expose Sensitive Data in Errors

```php
// Bad - exposes internal details
return new WP_Error(
	'auth_failed',
	'User admin@example.com not found in database',
	[ 'status' => 401 ]
);

// Good - generic message
return new WP_Error(
	'auth_failed',
	__( 'Invalid credentials.', 'my-plugin' ),
	[ 'status' => 401 ]
);
```

## Authentication Plugins

For additional authentication methods:

- **JWT Authentication** - JSON Web Tokens
- **OAuth 1.0a Server** - OAuth provider for WordPress
- **OAuth 2.0** - Modern OAuth implementation

**Note**: The Basic Authentication plugin is for **development only** - never use in production.

## Disabling Authentication

To disable REST API authentication for specific routes (public data only):

```php
// Always return true for permission callback
'permission_callback' => '__return_true'

// Or implement selective public access
'permission_callback' => function() {
	// Public for GET, authenticated for modifications
	return 'GET' === $_SERVER['REQUEST_METHOD'] || current_user_can( 'edit_posts' );
}
```

## Debugging Authentication Issues

```php
// Add to wp-config.php for debugging
define( 'WP_DEBUG', true );
define( 'WP_DEBUG_LOG', true );

// Log authentication state
add_action( 'rest_api_init', function() {
	error_log( sprintf(
		'REST API Init - User: %d, Nonce: %s',
		get_current_user_id(),
		isset( $_SERVER['HTTP_X_WP_NONCE'] ) ? 'present' : 'missing'
	) );
} );
```
