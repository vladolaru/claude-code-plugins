# REST API Controller Classes

Guide to structuring REST API endpoints using controller classes.

## Why Use Controllers?

Controller classes provide:

1. **Namespace Management** - Avoid global function naming conflicts
2. **Property Caching** - Store computed values like schemas for performance
3. **Code Organization** - Group related functionality logically
4. **Consistent Patterns** - Follow WordPress core conventions

## WP_REST_Controller

All WordPress core endpoints extend the abstract `WP_REST_Controller` class.

### Key Properties

```php
class WP_REST_Controller {
	protected $namespace;     // e.g., 'wp/v2' or 'myplugin/v1'
	protected $rest_base;     // e.g., 'posts' or 'payments'
	protected $schema;        // Cached schema (for performance)
}
```

### Key Methods

| Method | Purpose |
|--------|---------|
| `register_routes()` | Register all endpoints for this controller |
| `get_items()` | Handle GET collection requests |
| `get_item()` | Handle GET single item requests |
| `create_item()` | Handle POST requests |
| `update_item()` | Handle PUT/PATCH requests |
| `delete_item()` | Handle DELETE requests |
| `get_item_schema()` | Return resource JSON Schema |
| `get_collection_params()` | Return collection query params |
| `prepare_item_for_response()` | Format item for response |

### Inherited Helper Methods

```php
// Format items for collection responses
$this->prepare_response_for_collection( $response );

// Add registered REST fields to response
$this->add_additional_fields_to_object( $data, $request );

// Get fields to include based on _fields param
$this->get_fields_for_response( $request );

// Get context parameter value
$this->get_context_param( $args );

// Filter response based on context (view, edit, embed)
$this->filter_response_by_context( $response_data, $context );

// Get standard collection params (page, per_page, etc.)
$this->get_collection_params();
```

## Basic Controller Implementation

```php
/**
 * REST API controller for payments.
 *
 * @since 1.0.0
 */
class My_Plugin_REST_Payments_Controller extends WP_REST_Controller {

	/**
	 * Constructor.
	 */
	public function __construct() {
		$this->namespace = 'myplugin/v1';
		$this->rest_base = 'payments';
	}

	/**
	 * Registers the routes for this controller.
	 *
	 * @since 1.0.0
	 */
	public function register_routes() {
		// Collection endpoint: /myplugin/v1/payments
		register_rest_route(
			$this->namespace,
			'/' . $this->rest_base,
			[
				[
					'methods'             => WP_REST_Server::READABLE,
					'callback'            => [ $this, 'get_items' ],
					'permission_callback' => [ $this, 'get_items_permissions_check' ],
					'args'                => $this->get_collection_params(),
				],
				[
					'methods'             => WP_REST_Server::CREATABLE,
					'callback'            => [ $this, 'create_item' ],
					'permission_callback' => [ $this, 'create_item_permissions_check' ],
					'args'                => $this->get_endpoint_args_for_item_schema( WP_REST_Server::CREATABLE ),
				],
				'schema' => [ $this, 'get_public_item_schema' ],
			]
		);

		// Single item endpoint: /myplugin/v1/payments/{id}
		register_rest_route(
			$this->namespace,
			'/' . $this->rest_base . '/(?P<id>[\d]+)',
			[
				'args' => [
					'id' => [
						'description' => __( 'Unique identifier.', 'my-plugin' ),
						'type'        => 'integer',
					],
				],
				[
					'methods'             => WP_REST_Server::READABLE,
					'callback'            => [ $this, 'get_item' ],
					'permission_callback' => [ $this, 'get_item_permissions_check' ],
				],
				[
					'methods'             => WP_REST_Server::EDITABLE,
					'callback'            => [ $this, 'update_item' ],
					'permission_callback' => [ $this, 'update_item_permissions_check' ],
					'args'                => $this->get_endpoint_args_for_item_schema( WP_REST_Server::EDITABLE ),
				],
				[
					'methods'             => WP_REST_Server::DELETABLE,
					'callback'            => [ $this, 'delete_item' ],
					'permission_callback' => [ $this, 'delete_item_permissions_check' ],
				],
				'schema' => [ $this, 'get_public_item_schema' ],
			]
		);
	}

	/**
	 * Checks if current user can list payments.
	 *
	 * @since 1.0.0
	 *
	 * @param WP_REST_Request $request Request object.
	 * @return bool|WP_Error True if allowed, WP_Error otherwise.
	 */
	public function get_items_permissions_check( $request ) {
		if ( ! current_user_can( 'manage_woocommerce' ) ) {
			return new WP_Error(
				'myplugin_rest_forbidden',
				__( 'You do not have permission to view payments.', 'my-plugin' ),
				[ 'status' => rest_authorization_required_code() ]
			);
		}
		return true;
	}

	/**
	 * Retrieves a collection of payments.
	 *
	 * @since 1.0.0
	 *
	 * @param WP_REST_Request $request Request object.
	 * @return WP_REST_Response|WP_Error Response or error.
	 */
	public function get_items( $request ) {
		$args = [
			'per_page' => $request->get_param( 'per_page' ),
			'page'     => $request->get_param( 'page' ),
			'status'   => $request->get_param( 'status' ),
			'orderby'  => $request->get_param( 'orderby' ),
			'order'    => $request->get_param( 'order' ),
		];

		$payments = $this->get_payments( $args );
		$total    = $this->count_payments( $args );

		$data = [];
		foreach ( $payments as $payment ) {
			$response = $this->prepare_item_for_response( $payment, $request );
			$data[]   = $this->prepare_response_for_collection( $response );
		}

		$response = rest_ensure_response( $data );

		// Add pagination headers.
		$response->header( 'X-WP-Total', $total );
		$response->header( 'X-WP-TotalPages', ceil( $total / $args['per_page'] ) );

		return $response;
	}

	/**
	 * Checks if current user can view a specific payment.
	 *
	 * @since 1.0.0
	 *
	 * @param WP_REST_Request $request Request object.
	 * @return bool|WP_Error True if allowed, WP_Error otherwise.
	 */
	public function get_item_permissions_check( $request ) {
		$payment = $this->get_payment( $request->get_param( 'id' ) );

		if ( ! $payment ) {
			return new WP_Error(
				'myplugin_rest_not_found',
				__( 'Payment not found.', 'my-plugin' ),
				[ 'status' => 404 ]
			);
		}

		if ( ! current_user_can( 'manage_woocommerce' ) ) {
			return new WP_Error(
				'myplugin_rest_forbidden',
				__( 'You do not have permission to view this payment.', 'my-plugin' ),
				[ 'status' => rest_authorization_required_code() ]
			);
		}

		return true;
	}

	/**
	 * Retrieves a single payment.
	 *
	 * @since 1.0.0
	 *
	 * @param WP_REST_Request $request Request object.
	 * @return WP_REST_Response|WP_Error Response or error.
	 */
	public function get_item( $request ) {
		$payment = $this->get_payment( $request->get_param( 'id' ) );

		if ( ! $payment ) {
			return new WP_Error(
				'myplugin_rest_not_found',
				__( 'Payment not found.', 'my-plugin' ),
				[ 'status' => 404 ]
			);
		}

		return $this->prepare_item_for_response( $payment, $request );
	}

	/**
	 * Checks if current user can create payments.
	 *
	 * @since 1.0.0
	 *
	 * @param WP_REST_Request $request Request object.
	 * @return bool|WP_Error True if allowed, WP_Error otherwise.
	 */
	public function create_item_permissions_check( $request ) {
		if ( ! current_user_can( 'manage_woocommerce' ) ) {
			return new WP_Error(
				'myplugin_rest_forbidden',
				__( 'You do not have permission to create payments.', 'my-plugin' ),
				[ 'status' => rest_authorization_required_code() ]
			);
		}
		return true;
	}

	/**
	 * Creates a new payment.
	 *
	 * @since 1.0.0
	 *
	 * @param WP_REST_Request $request Request object.
	 * @return WP_REST_Response|WP_Error Response or error.
	 */
	public function create_item( $request ) {
		$payment = $this->prepare_item_for_database( $request );

		if ( is_wp_error( $payment ) ) {
			return $payment;
		}

		$payment_id = $this->save_payment( $payment );

		if ( is_wp_error( $payment_id ) ) {
			return $payment_id;
		}

		$payment = $this->get_payment( $payment_id );
		$response = $this->prepare_item_for_response( $payment, $request );

		$response->set_status( 201 );
		$response->header(
			'Location',
			rest_url( sprintf( '%s/%s/%d', $this->namespace, $this->rest_base, $payment_id ) )
		);

		return $response;
	}

	/**
	 * Prepares a single payment for response.
	 *
	 * @since 1.0.0
	 *
	 * @param object          $payment Payment object.
	 * @param WP_REST_Request $request Request object.
	 * @return WP_REST_Response Response object.
	 */
	public function prepare_item_for_response( $payment, $request ) {
		$fields = $this->get_fields_for_response( $request );
		$data   = [];

		if ( rest_is_field_included( 'id', $fields ) ) {
			$data['id'] = $payment->id;
		}

		if ( rest_is_field_included( 'amount', $fields ) ) {
			$data['amount'] = $payment->amount;
		}

		if ( rest_is_field_included( 'currency', $fields ) ) {
			$data['currency'] = $payment->currency;
		}

		if ( rest_is_field_included( 'status', $fields ) ) {
			$data['status'] = $payment->status;
		}

		if ( rest_is_field_included( 'created', $fields ) ) {
			$data['created'] = mysql_to_rfc3339( $payment->created );
		}

		$context = $request->get_param( 'context' ) ?? 'view';
		$data    = $this->add_additional_fields_to_object( $data, $request );
		$data    = $this->filter_response_by_context( $data, $context );

		$response = rest_ensure_response( $data );
		$response->add_links( $this->prepare_links( $payment ) );

		return $response;
	}

	/**
	 * Prepares links for the response.
	 *
	 * @since 1.0.0
	 *
	 * @param object $payment Payment object.
	 * @return array Links array.
	 */
	protected function prepare_links( $payment ) {
		$base = sprintf( '%s/%s', $this->namespace, $this->rest_base );

		return [
			'self' => [
				'href' => rest_url( trailingslashit( $base ) . $payment->id ),
			],
			'collection' => [
				'href' => rest_url( $base ),
			],
		];
	}

	/**
	 * Retrieves the payment schema.
	 *
	 * @since 1.0.0
	 *
	 * @return array Schema array.
	 */
	public function get_item_schema() {
		// Cache for performance (up to 40% faster responses).
		if ( $this->schema ) {
			return $this->add_additional_fields_schema( $this->schema );
		}

		$this->schema = [
			'$schema'    => 'http://json-schema.org/draft-04/schema#',
			'title'      => 'myplugin_payment',
			'type'       => 'object',
			'properties' => [
				'id'       => [
					'description' => __( 'Unique identifier.', 'my-plugin' ),
					'type'        => 'integer',
					'context'     => [ 'view', 'edit', 'embed' ],
					'readonly'    => true,
				],
				'amount'   => [
					'description' => __( 'Payment amount in cents.', 'my-plugin' ),
					'type'        => 'integer',
					'context'     => [ 'view', 'edit' ],
					'required'    => true,
				],
				'currency' => [
					'description' => __( 'Three-letter currency code.', 'my-plugin' ),
					'type'        => 'string',
					'context'     => [ 'view', 'edit' ],
					'enum'        => [ 'USD', 'EUR', 'GBP' ],
					'default'     => 'USD',
				],
				'status'   => [
					'description' => __( 'Payment status.', 'my-plugin' ),
					'type'        => 'string',
					'context'     => [ 'view', 'edit' ],
					'enum'        => [ 'pending', 'processing', 'succeeded', 'failed' ],
					'default'     => 'pending',
				],
				'created'  => [
					'description' => __( 'Creation date in ISO 8601 format.', 'my-plugin' ),
					'type'        => 'string',
					'format'      => 'date-time',
					'context'     => [ 'view', 'edit' ],
					'readonly'    => true,
				],
			],
		];

		return $this->add_additional_fields_schema( $this->schema );
	}

	/**
	 * Retrieves collection parameters.
	 *
	 * @since 1.0.0
	 *
	 * @return array Collection parameters.
	 */
	public function get_collection_params() {
		$params = parent::get_collection_params();

		$params['status'] = [
			'description' => __( 'Filter by status.', 'my-plugin' ),
			'type'        => 'string',
			'enum'        => [ 'pending', 'processing', 'succeeded', 'failed' ],
		];

		$params['orderby'] = [
			'description' => __( 'Sort by attribute.', 'my-plugin' ),
			'type'        => 'string',
			'enum'        => [ 'id', 'amount', 'created' ],
			'default'     => 'created',
		];

		return $params;
	}

	// Private methods for data operations...

	private function get_payments( $args ) {
		// Implementation
	}

	private function get_payment( $id ) {
		// Implementation
	}

	private function count_payments( $args ) {
		// Implementation
	}

	private function save_payment( $data ) {
		// Implementation
	}

	private function prepare_item_for_database( $request ) {
		// Implementation
	}
}
```

## Registering Controllers

```php
add_action( 'rest_api_init', 'myplugin_register_rest_controllers' );

function myplugin_register_rest_controllers() {
	$controllers = [
		'My_Plugin_REST_Payments_Controller',
		'My_Plugin_REST_Customers_Controller',
		'My_Plugin_REST_Settings_Controller',
	];

	foreach ( $controllers as $controller_class ) {
		$controller = new $controller_class();
		$controller->register_routes();
	}
}
```

## Extending Core Controllers

**Important**: Avoid deep inheritance hierarchies. Don't extend a posts controller for a different post type. Instead, create a shared base or handle multiple types in one class.

### Good Pattern: Shared Base Controller

```php
/**
 * Base controller with shared functionality.
 */
abstract class My_Plugin_REST_Base_Controller extends WP_REST_Controller {

	/**
	 * Checks if user can manage WooCommerce.
	 *
	 * @return bool|WP_Error
	 */
	protected function check_manage_permission() {
		if ( ! current_user_can( 'manage_woocommerce' ) ) {
			return new WP_Error(
				'myplugin_rest_forbidden',
				__( 'Permission denied.', 'my-plugin' ),
				[ 'status' => rest_authorization_required_code() ]
			);
		}
		return true;
	}

	/**
	 * Formats a date for response.
	 *
	 * @param string $date MySQL datetime.
	 * @return string|null ISO 8601 date or null.
	 */
	protected function format_date( $date ) {
		if ( empty( $date ) || '0000-00-00 00:00:00' === $date ) {
			return null;
		}
		return mysql_to_rfc3339( $date );
	}

	/**
	 * Gets error response for invalid ID.
	 *
	 * @param string $resource Resource name.
	 * @return WP_Error Error object.
	 */
	protected function get_not_found_error( $resource ) {
		return new WP_Error(
			'myplugin_rest_not_found',
			/* translators: %s: Resource name */
			sprintf( __( '%s not found.', 'my-plugin' ), $resource ),
			[ 'status' => 404 ]
		);
	}
}

/**
 * Payments controller extending base.
 */
class My_Plugin_REST_Payments_Controller extends My_Plugin_REST_Base_Controller {

	public function __construct() {
		$this->namespace = 'myplugin/v1';
		$this->rest_base = 'payments';
	}

	public function get_items_permissions_check( $request ) {
		return $this->check_manage_permission();
	}

	// ...
}
```

### Avoid: Deep Inheritance

```php
// DON'T DO THIS
class My_Custom_Posts_Controller extends WP_REST_Posts_Controller {
	// Extending posts controller for a different purpose
}

class My_Specific_Posts_Controller extends My_Custom_Posts_Controller {
	// Even deeper inheritance - hard to maintain
}
```

## Action Endpoints

For endpoints that perform actions rather than CRUD operations:

```php
class My_Plugin_REST_Actions_Controller extends WP_REST_Controller {

	public function __construct() {
		$this->namespace = 'myplugin/v1';
		$this->rest_base = 'payments';
	}

	public function register_routes() {
		// Standard CRUD routes...

		// Action endpoint: /myplugin/v1/payments/{id}/capture
		register_rest_route(
			$this->namespace,
			'/' . $this->rest_base . '/(?P<id>[\d]+)/capture',
			[
				'methods'             => WP_REST_Server::CREATABLE,
				'callback'            => [ $this, 'capture_payment' ],
				'permission_callback' => [ $this, 'capture_payment_permissions_check' ],
				'args'                => [
					'id' => [
						'description' => __( 'Payment ID.', 'my-plugin' ),
						'type'        => 'integer',
						'required'    => true,
					],
					'amount' => [
						'description' => __( 'Amount to capture (optional).', 'my-plugin' ),
						'type'        => 'integer',
					],
				],
			]
		);

		// Action endpoint: /myplugin/v1/payments/{id}/refund
		register_rest_route(
			$this->namespace,
			'/' . $this->rest_base . '/(?P<id>[\d]+)/refund',
			[
				'methods'             => WP_REST_Server::CREATABLE,
				'callback'            => [ $this, 'refund_payment' ],
				'permission_callback' => [ $this, 'refund_payment_permissions_check' ],
				'args'                => [
					'id' => [
						'description' => __( 'Payment ID.', 'my-plugin' ),
						'type'        => 'integer',
						'required'    => true,
					],
					'amount' => [
						'description' => __( 'Amount to refund.', 'my-plugin' ),
						'type'        => 'integer',
						'required'    => true,
					],
					'reason' => [
						'description' => __( 'Refund reason.', 'my-plugin' ),
						'type'        => 'string',
					],
				],
			]
		);
	}

	public function capture_payment( $request ) {
		$payment_id = $request->get_param( 'id' );
		$amount     = $request->get_param( 'amount' );

		$result = myplugin_capture_payment( $payment_id, $amount );

		if ( is_wp_error( $result ) ) {
			return $result;
		}

		return rest_ensure_response( [
			'success' => true,
			'payment' => $this->prepare_item_for_response( $result, $request ),
		] );
	}
}
```

## Batch Operations

For handling multiple items in one request:

```php
class My_Plugin_REST_Batch_Controller extends WP_REST_Controller {

	public function register_routes() {
		register_rest_route(
			'myplugin/v1',
			'/batch',
			[
				'methods'             => WP_REST_Server::CREATABLE,
				'callback'            => [ $this, 'batch_items' ],
				'permission_callback' => [ $this, 'batch_items_permissions_check' ],
				'args'                => [
					'create' => [
						'description' => __( 'Items to create.', 'my-plugin' ),
						'type'        => 'array',
						'default'     => [],
					],
					'update' => [
						'description' => __( 'Items to update.', 'my-plugin' ),
						'type'        => 'array',
						'default'     => [],
					],
					'delete' => [
						'description' => __( 'IDs to delete.', 'my-plugin' ),
						'type'        => 'array',
						'items'       => [ 'type' => 'integer' ],
						'default'     => [],
					],
				],
			]
		);
	}

	public function batch_items( $request ) {
		$results = [
			'create' => [],
			'update' => [],
			'delete' => [],
		];

		// Process creates
		foreach ( $request->get_param( 'create' ) as $item ) {
			$create_request = new WP_REST_Request( 'POST' );
			$create_request->set_body_params( $item );
			$results['create'][] = $this->create_item( $create_request );
		}

		// Process updates
		foreach ( $request->get_param( 'update' ) as $item ) {
			$update_request = new WP_REST_Request( 'PUT' );
			$update_request->set_param( 'id', $item['id'] );
			$update_request->set_body_params( $item );
			$results['update'][] = $this->update_item( $update_request );
		}

		// Process deletes
		foreach ( $request->get_param( 'delete' ) as $id ) {
			$delete_request = new WP_REST_Request( 'DELETE' );
			$delete_request->set_param( 'id', $id );
			$results['delete'][] = $this->delete_item( $delete_request );
		}

		return rest_ensure_response( $results );
	}
}
```

## Performance Tips

### 1. Cache Schema

```php
public function get_item_schema() {
	// Schema caching can improve response generation by up to 40%
	if ( $this->schema ) {
		return $this->add_additional_fields_schema( $this->schema );
	}

	$this->schema = [ /* ... */ ];

	return $this->add_additional_fields_schema( $this->schema );
}
```

### 2. Use get_fields_for_response

```php
public function prepare_item_for_response( $item, $request ) {
	$fields = $this->get_fields_for_response( $request );

	// Only compute fields that are requested
	if ( rest_is_field_included( 'expensive_field', $fields ) ) {
		$data['expensive_field'] = $this->compute_expensive_field( $item );
	}
}
```

### 3. Lazy Load Related Data

```php
public function prepare_item_for_response( $item, $request ) {
	$context = $request->get_param( 'context' );

	// Only load related data when editing
	if ( 'edit' === $context ) {
		$data['metadata'] = $this->get_metadata( $item->id );
	}
}
```

## Testing Controllers

```php
class My_Plugin_REST_Payments_Controller_Test extends WP_Test_REST_Controller_Testcase {

	protected $controller;
	protected $admin_id;

	public function setUp(): void {
		parent::setUp();

		$this->controller = new My_Plugin_REST_Payments_Controller();
		$this->controller->register_routes();

		$this->admin_id = $this->factory->user->create( [
			'role' => 'administrator',
		] );
	}

	public function test_register_routes() {
		$routes = rest_get_server()->get_routes();

		$this->assertArrayHasKey( '/myplugin/v1/payments', $routes );
		$this->assertArrayHasKey( '/myplugin/v1/payments/(?P<id>[\d]+)', $routes );
	}

	public function test_get_items() {
		wp_set_current_user( $this->admin_id );

		$request  = new WP_REST_Request( 'GET', '/myplugin/v1/payments' );
		$response = rest_get_server()->dispatch( $request );

		$this->assertEquals( 200, $response->get_status() );
	}

	public function test_get_item_schema() {
		$request  = new WP_REST_Request( 'OPTIONS', '/myplugin/v1/payments' );
		$response = rest_get_server()->dispatch( $request );
		$data     = $response->get_data();

		$this->assertArrayHasKey( 'schema', $data );
		$this->assertEquals( 'myplugin_payment', $data['schema']['title'] );
	}

	public function test_context_param() {
		wp_set_current_user( $this->admin_id );

		// Create test payment...

		$request = new WP_REST_Request( 'GET', '/myplugin/v1/payments/1' );
		$request->set_param( 'context', 'edit' );
		$response = rest_get_server()->dispatch( $request );

		// Verify edit context fields are included
	}
}
```
