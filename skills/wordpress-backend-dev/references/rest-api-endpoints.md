# REST API Endpoints

Detailed guide to registering routes, handling parameters, and validation.

## Route Registration

### Basic Registration

Register routes on the `rest_api_init` hook to avoid unnecessary processing:

```php
add_action( 'rest_api_init', 'myplugin_register_rest_routes' );

function myplugin_register_rest_routes() {
	register_rest_route(
		'myplugin/v1',                    // Namespace (vendor/version)
		'/status',                      // Route path
		[
			'methods'             => WP_REST_Server::READABLE,
			'callback'            => 'myplugin_get_status',
			'permission_callback' => '__return_true',
		]
	);
}

function myplugin_get_status( WP_REST_Request $request ) {
	return rest_ensure_response( [ 'status' => 'active' ] );
}
```

### Route with Path Parameters

Use regex patterns to capture dynamic values:

```php
register_rest_route(
	'myplugin/v1',
	'/orders/(?P<id>\d+)',  // (?P<name>pattern) captures as 'name'
	[
		'methods'             => WP_REST_Server::READABLE,
		'callback'            => 'myplugin_get_order',
		'permission_callback' => 'myplugin_can_manage_orders',
		'args'                => [
			'id' => [
				'description'       => __( 'Order ID.', 'my-plugin' ),
				'type'              => 'integer',
				'required'          => true,
				'validate_callback' => function( $param ) {
					return is_numeric( $param ) && $param > 0;
				},
				'sanitize_callback' => 'absint',
			],
		],
	]
);

function myplugin_get_order( WP_REST_Request $request ) {
	$order_id = $request->get_param( 'id' );
	// Or: $order_id = $request['id'];

	$order = wc_get_order( $order_id );

	if ( ! $order ) {
		return new WP_Error(
			'myplugin_order_not_found',
			__( 'Order not found.', 'my-plugin' ),
			[ 'status' => 404 ]
		);
	}

	return rest_ensure_response( myplugin_format_order( $order ) );
}
```

### Common Regex Patterns

```php
// Integer only
'/items/(?P<id>\d+)'

// Alphanumeric slug
'/items/(?P<slug>[a-zA-Z0-9-]+)'

// UUID
'/items/(?P<uuid>[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})'

// Multiple parameters
'/orders/(?P<order_id>\d+)/items/(?P<item_id>\d+)'
```

### Multiple HTTP Methods on Same Route

```php
register_rest_route(
	'myplugin/v1',
	'/settings',
	[
		// GET endpoint
		[
			'methods'             => WP_REST_Server::READABLE,
			'callback'            => 'myplugin_get_settings',
			'permission_callback' => 'myplugin_can_manage_settings',
		],
		// POST/PUT endpoint
		[
			'methods'             => WP_REST_Server::EDITABLE,
			'callback'            => 'myplugin_update_settings',
			'permission_callback' => 'myplugin_can_manage_settings',
			'args'                => myplugin_get_settings_args(),
		],
		// Schema for discovery
		'schema' => 'myplugin_get_settings_schema',
	]
);
```

## Permission Callbacks

**CRITICAL**: Every endpoint MUST have a `permission_callback` (required since WordPress 5.5).

### Common Patterns

```php
// Public endpoint (no authentication required)
'permission_callback' => '__return_true'

// Logged-in users only
'permission_callback' => 'is_user_logged_in'

// Specific capability
'permission_callback' => function() {
	return current_user_can( 'manage_woocommerce' );
}

// Object-specific permission
'permission_callback' => function( WP_REST_Request $request ) {
	$order_id = $request->get_param( 'id' );
	$order = wc_get_order( $order_id );

	if ( ! $order ) {
		return false;
	}

	// User owns the order OR is admin
	return current_user_can( 'manage_woocommerce' ) ||
	       get_current_user_id() === $order->get_customer_id();
}
```

### Returning Detailed Errors

Return `WP_Error` for specific error messages:

```php
function myplugin_can_manage_orders( WP_REST_Request $request ) {
	if ( ! is_user_logged_in() ) {
		return new WP_Error(
			'myplugin_rest_unauthorized',
			__( 'You must be logged in.', 'my-plugin' ),
			[ 'status' => 401 ]
		);
	}

	if ( ! current_user_can( 'manage_woocommerce' ) ) {
		return new WP_Error(
			'myplugin_rest_forbidden',
			__( 'You do not have permission.', 'my-plugin' ),
			[ 'status' => 403 ]
		);
	}

	return true;
}
```

## Request Handling

### WP_REST_Request Methods

```php
function myplugin_handle_request( WP_REST_Request $request ) {
	// URL parameters (from route regex)
	$id = $request->get_param( 'id' );
	$id = $request['id'];  // Alternative syntax

	// Query parameters (?key=value)
	$page = $request->get_param( 'page' );
	$per_page = $request->get_param( 'per_page' );

	// Body parameters (POST/PUT data)
	$data = $request->get_param( 'data' );

	// Get all parameters merged
	$params = $request->get_params();

	// Get only URL params
	$url_params = $request->get_url_params();

	// Get only query params
	$query_params = $request->get_query_params();

	// Get only body params
	$body_params = $request->get_body_params();

	// Get JSON body as array
	$json = $request->get_json_params();

	// Get raw body
	$raw = $request->get_body();

	// Get file uploads
	$files = $request->get_file_params();

	// Headers
	$content_type = $request->get_header( 'content-type' );
	$all_headers = $request->get_headers();

	// HTTP method
	$method = $request->get_method();

	// Check if parameter exists
	if ( $request->has_param( 'filter' ) ) {
		$filter = $request->get_param( 'filter' );
	}

	// Get route
	$route = $request->get_route();

	// Get matched route attributes
	$attributes = $request->get_attributes();
}
```

## Handling JSON Request Bodies

**CRITICAL**: WordPress REST API automatically parses JSON bodies when `Content-Type: application/json` is set. Use `args` to validate JSON properties just like query parameters.

### How JSON Body Processing Works

1. Client sends JSON body with `Content-Type: application/json`
2. WordPress parses JSON and merges into request parameters
3. `args` validation runs against merged parameters
4. Callback receives validated/sanitized data via `get_param()`

### Complete JSON Body Example

```php
register_rest_route(
	'myplugin/v1',
	'/preferences',
	[
		'methods'             => WP_REST_Server::CREATABLE,
		'callback'            => 'myplugin_save_preferences',
		'permission_callback' => function() {
			return current_user_can( 'manage_woocommerce' );
		},
		// Define args for JSON body properties - WordPress handles the rest
		'args'                => [
			'notifications' => [
				'description' => __( 'Notification settings.', 'my-plugin' ),
				'type'        => 'object',
				'required'    => true,
				'properties'  => [
					'email'   => [
						'type'    => 'boolean',
						'default' => true,
					],
					'sms'     => [
						'type'    => 'boolean',
						'default' => false,
					],
					'frequency' => [
						'type' => 'string',
						'enum' => [ 'instant', 'daily', 'weekly' ],
					],
				],
			],
			'theme' => [
				'description' => __( 'UI theme preference.', 'my-plugin' ),
				'type'        => 'string',
				'enum'        => [ 'light', 'dark', 'auto' ],
				'default'     => 'auto',
			],
		],
	]
);

function myplugin_save_preferences( WP_REST_Request $request ) {
	// Access JSON body properties directly via get_param()
	// WordPress already validated against the args schema above
	$notifications = $request->get_param( 'notifications' );
	$theme         = $request->get_param( 'theme' );

	// $notifications is already an array with validated structure:
	// [ 'email' => true, 'sms' => false, 'frequency' => 'daily' ]

	update_user_meta(
		get_current_user_id(),
		'_myplugin_preferences',
		[
			'notifications' => $notifications,
			'theme'         => $theme,
		]
	);

	return rest_ensure_response( [
		'success' => true,
		'message' => __( 'Preferences saved.', 'my-plugin' ),
	] );
}
```

**Client request:**
```bash
curl -X POST "https://example.com/wp-json/myplugin/v1/preferences" \
  -H "Authorization: Basic $(echo -n 'user:app_password' | base64)" \
  -H "Content-Type: application/json" \
  -d '{
    "notifications": {
      "email": true,
      "sms": false,
      "frequency": "daily"
    },
    "theme": "dark"
  }'
```

### Accessing Raw vs Parsed JSON

| Method | Returns | Use When |
|--------|---------|----------|
| `$request->get_param( 'key' )` | Single validated value | **Default** - use for validated args |
| `$request->get_json_params()` | Full parsed JSON array | Need entire body without args validation |
| `$request->get_body()` | Raw JSON string | Need to parse manually or log raw input |
| `$request->get_body_params()` | Form data (POST fields) | Non-JSON `application/x-www-form-urlencoded` |

### Validating Nested Objects

For complex nested structures, define nested `properties`:

```php
'args' => [
	'order' => [
		'type'       => 'object',
		'required'   => true,
		'properties' => [
			'items' => [
				'type'     => 'array',
				'required' => true,
				'items'    => [
					'type'       => 'object',
					'properties' => [
						'product_id' => [
							'type'     => 'integer',
							'required' => true,
						],
						'quantity'   => [
							'type'    => 'integer',
							'minimum' => 1,
							'default' => 1,
						],
					],
				],
			],
			'billing' => [
				'type'       => 'object',
				'properties' => [
					'email' => [
						'type'   => 'string',
						'format' => 'email',
					],
					'phone' => [
						'type' => 'string',
					],
				],
			],
		],
	],
],
```

### Custom Sanitization for JSON Properties

Add `sanitize_callback` at any level:

```php
'args' => [
	'metadata' => [
		'type'              => 'object',
		'sanitize_callback' => function( $value ) {
			// Sanitize entire object
			if ( ! is_array( $value ) ) {
				return [];
			}
			return array_map( 'sanitize_text_field', $value );
		},
	],
	'tags' => [
		'type'              => 'array',
		'items'             => [ 'type' => 'string' ],
		'sanitize_callback' => function( $value ) {
			// Sanitize array of strings
			return array_map( 'sanitize_key', (array) $value );
		},
	],
],
```

## Parameter Validation

### Argument Schema

Define parameters with validation in the `args` array:

```php
'args' => [
	// String with format
	'email' => [
		'description'       => __( 'Customer email.', 'my-plugin' ),
		'type'              => 'string',
		'format'            => 'email',
		'required'          => true,
		'sanitize_callback' => 'sanitize_email',
	],

	// Number with range
	'amount' => [
		'description'       => __( 'Payment amount.', 'my-plugin' ),
		'type'              => 'number',
		'minimum'           => 0.01,
		'maximum'           => 999999.99,
		'required'          => true,
		'sanitize_callback' => 'floatval',
	],

	// Enum (allowed values)
	'status' => [
		'description' => __( 'Order status.', 'my-plugin' ),
		'type'        => 'string',
		'enum'        => [ 'pending', 'processing', 'completed', 'cancelled' ],
		'default'     => 'pending',
	],

	// Boolean
	'notify' => [
		'description' => __( 'Send notification.', 'my-plugin' ),
		'type'        => 'boolean',
		'default'     => false,
	],

	// Array of integers
	'include' => [
		'description' => __( 'IDs to include.', 'my-plugin' ),
		'type'        => 'array',
		'items'       => [
			'type' => 'integer',
		],
		'default'     => [],
	],

	// Object
	'address' => [
		'description' => __( 'Billing address.', 'my-plugin' ),
		'type'        => 'object',
		'properties'  => [
			'line1'   => [ 'type' => 'string' ],
			'line2'   => [ 'type' => 'string' ],
			'city'    => [ 'type' => 'string' ],
			'state'   => [ 'type' => 'string' ],
			'zip'     => [ 'type' => 'string' ],
			'country' => [ 'type' => 'string' ],
		],
	],

	// Custom validation
	'card_number' => [
		'description'       => __( 'Card number.', 'my-plugin' ),
		'type'              => 'string',
		'validate_callback' => function( $value, $request, $key ) {
			if ( ! preg_match( '/^\d{13,19}$/', $value ) ) {
				return new WP_Error(
					'invalid_card_number',
					__( 'Invalid card number format.', 'my-plugin' )
				);
			}
			return true;
		},
		'sanitize_callback' => function( $value ) {
			return preg_replace( '/\D/', '', $value );
		},
	],
],
```

### Supported Types

JSON Schema primitive types:
- `string`
- `number`
- `integer`
- `boolean`
- `array`
- `object`
- `null`

### Format Validators

Built-in format validators for strings:
- `email` - Valid email address
- `uri` - Valid URI
- `ip` - Valid IP address (v4 or v6)
- `uuid` - Valid UUID
- `date-time` - ISO 8601 date-time
- `hex-color` - Hex color code

### Validation vs Sanitization

**Always validate first, then sanitize:**

1. `validate_callback` - Returns `true` or `WP_Error`
2. `sanitize_callback` - Cleans/transforms the value

```php
'args' => [
	'count' => [
		'type'              => 'integer',
		'validate_callback' => function( $value ) {
			// Check it's valid
			return is_numeric( $value ) && $value > 0 && $value <= 100;
		},
		'sanitize_callback' => function( $value ) {
			// Clean the value
			return absint( $value );
		},
	],
],
```

## Response Handling

### Success Responses

```php
function myplugin_get_orders( WP_REST_Request $request ) {
	$orders = myplugin_fetch_orders();

	// Simple response (auto-wrapped)
	return rest_ensure_response( $orders );

	// With custom status and headers
	$response = new WP_REST_Response( $orders, 200 );
	$response->header( 'X-Custom-Header', 'value' );
	return $response;
}
```

### Error Responses

```php
function myplugin_get_order( WP_REST_Request $request ) {
	$order = wc_get_order( $request['id'] );

	// 404 Not Found
	if ( ! $order ) {
		return new WP_Error(
			'myplugin_order_not_found',
			__( 'Order not found.', 'my-plugin' ),
			[ 'status' => 404 ]
		);
	}

	// 400 Bad Request (validation error)
	if ( 'completed' === $order->get_status() ) {
		return new WP_Error(
			'myplugin_invalid_operation',
			__( 'Cannot modify completed orders.', 'my-plugin' ),
			[
				'status'       => 400,
				'order_status' => $order->get_status(),
			]
		);
	}

	return rest_ensure_response( myplugin_format_order( $order ) );
}
```

### HTTP Status Codes

| Code | Meaning | Use Case |
|------|---------|----------|
| 200 | OK | Successful GET, PUT |
| 201 | Created | Successful POST |
| 204 | No Content | Successful DELETE |
| 400 | Bad Request | Validation error |
| 401 | Unauthorized | Not logged in |
| 403 | Forbidden | Insufficient permissions |
| 404 | Not Found | Resource doesn't exist |
| 500 | Internal Server Error | Server-side error |

### Pagination Response

```php
function myplugin_get_orders( WP_REST_Request $request ) {
	$page = $request->get_param( 'page' ) ?? 1;
	$per_page = $request->get_param( 'per_page' ) ?? 10;

	$orders = myplugin_query_orders( $page, $per_page );
	$total = myplugin_count_orders();
	$total_pages = ceil( $total / $per_page );

	$response = rest_ensure_response( $orders );

	// Required pagination headers
	$response->header( 'X-WP-Total', $total );
	$response->header( 'X-WP-TotalPages', $total_pages );

	// Link header for pagination
	$base = rest_url( 'myplugin/v1/orders' );
	$links = [];

	if ( $page > 1 ) {
		$links[] = '<' . add_query_arg( 'page', $page - 1, $base ) . '>; rel="prev"';
	}
	if ( $page < $total_pages ) {
		$links[] = '<' . add_query_arg( 'page', $page + 1, $base ) . '>; rel="next"';
	}

	if ( ! empty( $links ) ) {
		$response->header( 'Link', implode( ', ', $links ) );
	}

	return $response;
}
```

## Schema Definition

Define resource schema for documentation and validation:

```php
register_rest_route(
	'myplugin/v1',
	'/orders',
	[
		[
			'methods'             => WP_REST_Server::READABLE,
			'callback'            => 'myplugin_get_orders',
			'permission_callback' => 'myplugin_can_manage_orders',
		],
		'schema' => 'myplugin_get_order_schema',
	]
);

function myplugin_get_order_schema() {
	return [
		'$schema'    => 'http://json-schema.org/draft-04/schema#',
		'title'      => 'myplugin_order',
		'type'       => 'object',
		'properties' => [
			'id' => [
				'description' => __( 'Unique identifier.', 'my-plugin' ),
				'type'        => 'integer',
				'context'     => [ 'view', 'edit' ],
				'readonly'    => true,
			],
			'status' => [
				'description' => __( 'Order status.', 'my-plugin' ),
				'type'        => 'string',
				'enum'        => [ 'pending', 'processing', 'completed' ],
				'context'     => [ 'view', 'edit' ],
			],
			'total' => [
				'description' => __( 'Order total.', 'my-plugin' ),
				'type'        => 'string',
				'context'     => [ 'view', 'edit' ],
				'readonly'    => true,
			],
			'customer' => [
				'description' => __( 'Customer data.', 'my-plugin' ),
				'type'        => 'object',
				'context'     => [ 'view', 'edit' ],
				'properties'  => [
					'id'    => [ 'type' => 'integer' ],
					'email' => [ 'type' => 'string', 'format' => 'email' ],
					'name'  => [ 'type' => 'string' ],
				],
			],
		],
	];
}
```

### Context

The `context` property controls which fields appear in different views:
- `view` - Public read context
- `edit` - Authenticated/admin context
- `embed` - When embedded via `_embed`

## Custom Content Types

### Expose Custom Post Type

```php
add_action( 'init', 'myplugin_register_cpt' );

function myplugin_register_cpt() {
	register_post_type( 'myplugin_transaction', [
		'public'       => false,
		'show_in_rest' => true,                    // Enable REST API
		'rest_base'    => 'transactions',          // URL path
		'rest_controller_class' => 'WP_REST_Posts_Controller',
		'supports'     => [ 'title', 'custom-fields' ],
	] );
}
```

### Expose Custom Taxonomy

```php
register_taxonomy( 'transaction_type', 'myplugin_transaction', [
	'show_in_rest'          => true,
	'rest_base'             => 'transaction-types',
	'rest_controller_class' => 'WP_REST_Terms_Controller',
] );
```

### Add REST Support to Existing Types

```php
add_filter( 'register_post_type_args', 'myplugin_modify_cpt_args', 10, 2 );

function myplugin_modify_cpt_args( $args, $post_type ) {
	if ( 'existing_cpt' === $post_type ) {
		$args['show_in_rest'] = true;
		$args['rest_base']    = 'custom-items';
	}
	return $args;
}
```

## Modifying Responses

### Add Custom Fields

```php
add_action( 'rest_api_init', 'myplugin_register_rest_fields' );

function myplugin_register_rest_fields() {
	register_rest_field(
		'post',                          // Object type
		'myplugin_custom_data',             // Field name in response
		[
			'get_callback'    => function( $post_arr ) {
				return get_post_meta( $post_arr['id'], '_myplugin_data', true );
			},
			'update_callback' => function( $value, $post ) {
				update_post_meta( $post->ID, '_myplugin_data', sanitize_text_field( $value ) );
			},
			'schema'          => [
				'type'        => 'string',
				'description' => __( 'Custom WCPay data.', 'my-plugin' ),
				'context'     => [ 'view', 'edit' ],
			],
		]
	);
}
```

### Expose Meta Fields

```php
register_post_meta(
	'post',
	'_myplugin_transaction_id',
	[
		'type'         => 'string',
		'single'       => true,
		'show_in_rest' => true,
		'auth_callback' => function() {
			return current_user_can( 'edit_posts' );
		},
	]
);
```

### Complex Meta (Objects/Arrays)

```php
register_post_meta(
	'post',
	'_myplugin_settings',
	[
		'single'       => true,
		'type'         => 'object',
		'show_in_rest' => [
			'schema' => [
				'type'       => 'object',
				'properties' => [
					'enabled' => [ 'type' => 'boolean' ],
					'mode'    => [ 'type' => 'string', 'enum' => [ 'test', 'live' ] ],
					'limit'   => [ 'type' => 'integer' ],
				],
			],
		],
	]
);
```

## Testing Endpoints

### WP-CLI

```bash
# GET request
wp rest get myplugin/v1/orders --user=admin

# POST request
wp rest post myplugin/v1/orders --user=admin amount=100 status=pending

# With JSON body
wp rest post myplugin/v1/orders --user=admin --json='{"amount":100}'
```

### cURL

```bash
# GET with application password
curl -X GET "https://example.com/wp-json/myplugin/v1/orders" \
  -H "Authorization: Basic $(echo -n 'user:app_password' | base64)"

# POST with JSON
curl -X POST "https://example.com/wp-json/myplugin/v1/orders" \
  -H "Authorization: Basic $(echo -n 'user:app_password' | base64)" \
  -H "Content-Type: application/json" \
  -d '{"amount": 100, "currency": "USD"}'
```

### PHPUnit

```php
class My_Plugin_REST_Test extends WP_Test_REST_Controller_Testcase {

	public function test_get_orders_success() {
		wp_set_current_user( $this->admin_id );

		$request = new WP_REST_Request( 'GET', '/myplugin/v1/orders' );
		$response = rest_get_server()->dispatch( $request );

		$this->assertEquals( 200, $response->get_status() );
		$this->assertIsArray( $response->get_data() );
	}

	public function test_get_orders_unauthorized() {
		wp_set_current_user( 0 );

		$request = new WP_REST_Request( 'GET', '/myplugin/v1/orders' );
		$response = rest_get_server()->dispatch( $request );

		$this->assertEquals( 401, $response->get_status() );
	}

	public function test_create_order_validation() {
		wp_set_current_user( $this->admin_id );

		$request = new WP_REST_Request( 'POST', '/myplugin/v1/orders' );
		$request->set_body_params( [ 'amount' => -100 ] );  // Invalid
		$response = rest_get_server()->dispatch( $request );

		$this->assertEquals( 400, $response->get_status() );
	}
}
```
