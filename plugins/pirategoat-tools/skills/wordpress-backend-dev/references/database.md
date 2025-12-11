# WordPress Database Operations

Comprehensive guide to using $wpdb for safe and efficient database operations.

## WPDB Basics

### Accessing $wpdb

```php
global $wpdb;

// Table names with prefix
$table = $wpdb->prefix . 'myplugin_transactions';  // e.g., wp_myplugin_transactions
$users_table = $wpdb->users;                     // Core users table
$posts_table = $wpdb->posts;                     // Core posts table
```

### Format Specifiers

| Specifier | Type | Example |
|-----------|------|---------|
| `%d` | Integer | `123` |
| `%f` | Float | `12.34` |
| `%s` | String | `'hello'` |

**CRITICAL**: Always use the correct specifier to prevent SQL injection.

## Prepared Statements

**ALWAYS use `$wpdb->prepare()` for queries with variables.**

### Basic Usage

```php
global $wpdb;

// Simple query with one variable
$status = $wpdb->get_var(
	$wpdb->prepare(
		"SELECT status FROM {$wpdb->prefix}myplugin_orders WHERE id = %d",
		$order_id
	)
);

// Query with multiple variables
$row = $wpdb->get_row(
	$wpdb->prepare(
		"SELECT * FROM {$wpdb->prefix}myplugin_orders WHERE user_id = %d AND status = %s",
		$user_id,
		$status
	)
);
```

### Unprepared Queries (No Variables)

When a query has NO external variables, `prepare()` is not needed:

```php
// Safe - no external variables
$count = $wpdb->get_var( "SELECT COUNT(*) FROM {$wpdb->prefix}myplugin_orders" );

// Safe - hardcoded values
$active = $wpdb->get_results( "SELECT * FROM {$wpdb->prefix}myplugin_orders WHERE status = 'active'" );
```

## SELECT Queries

### Get Single Value

```php
// Get single value
$count = $wpdb->get_var(
	$wpdb->prepare(
		"SELECT COUNT(*) FROM {$wpdb->prefix}myplugin_orders WHERE user_id = %d",
		$user_id
	)
);

// Get specific column value
$status = $wpdb->get_var(
	$wpdb->prepare(
		"SELECT status FROM {$wpdb->prefix}myplugin_orders WHERE id = %d",
		$order_id
	)
);
```

### Get Single Row

```php
// As object (default)
$order = $wpdb->get_row(
	$wpdb->prepare(
		"SELECT * FROM {$wpdb->prefix}myplugin_orders WHERE id = %d",
		$order_id
	)
);
echo $order->status;

// As associative array
$order = $wpdb->get_row(
	$wpdb->prepare(
		"SELECT * FROM {$wpdb->prefix}myplugin_orders WHERE id = %d",
		$order_id
	),
	ARRAY_A
);
echo $order['status'];

// As numeric array
$order = $wpdb->get_row(
	$wpdb->prepare(
		"SELECT * FROM {$wpdb->prefix}myplugin_orders WHERE id = %d",
		$order_id
	),
	ARRAY_N
);
echo $order[0]; // First column
```

### Get Column

```php
// Get all values from one column
$ids = $wpdb->get_col(
	$wpdb->prepare(
		"SELECT id FROM {$wpdb->prefix}myplugin_orders WHERE status = %s",
		'pending'
	)
);
// Returns: [1, 5, 12, 23]
```

### Get Multiple Rows

```php
// As array of objects (default)
$orders = $wpdb->get_results(
	$wpdb->prepare(
		"SELECT * FROM {$wpdb->prefix}myplugin_orders WHERE user_id = %d ORDER BY created_at DESC",
		$user_id
	)
);

foreach ( $orders as $order ) {
	echo $order->id . ': ' . $order->status;
}

// As array of associative arrays
$orders = $wpdb->get_results(
	$wpdb->prepare(
		"SELECT * FROM {$wpdb->prefix}myplugin_orders WHERE user_id = %d",
		$user_id
	),
	ARRAY_A
);

foreach ( $orders as $order ) {
	echo $order['id'] . ': ' . $order['status'];
}
```

## INSERT Operations

### Using insert()

```php
// Basic insert
$result = $wpdb->insert(
	$wpdb->prefix . 'myplugin_orders',
	[
		'user_id'    => $user_id,
		'amount'     => $amount,
		'status'     => 'pending',
		'created_at' => current_time( 'mysql' ),
	],
	[ '%d', '%f', '%s', '%s' ]  // Format specifiers
);

// Check result
if ( false === $result ) {
	// Insert failed
	error_log( 'Insert failed: ' . $wpdb->last_error );
} else {
	// Get inserted ID
	$new_id = $wpdb->insert_id;
}
```

### Using Prepared Statement

```php
$result = $wpdb->query(
	$wpdb->prepare(
		"INSERT INTO {$wpdb->prefix}myplugin_orders (user_id, amount, status, created_at) VALUES (%d, %f, %s, %s)",
		$user_id,
		$amount,
		'pending',
		current_time( 'mysql' )
	)
);

$new_id = $wpdb->insert_id;
```

## UPDATE Operations

### Using update()

```php
// Update with conditions
$result = $wpdb->update(
	$wpdb->prefix . 'myplugin_orders',
	// Data to update
	[
		'status'     => 'completed',
		'updated_at' => current_time( 'mysql' ),
	],
	// WHERE conditions
	[
		'id' => $order_id,
	],
	// Data format
	[ '%s', '%s' ],
	// WHERE format
	[ '%d' ]
);

// Check result
if ( false === $result ) {
	error_log( 'Update failed: ' . $wpdb->last_error );
} else {
	// $result = number of rows updated (can be 0 if no change)
	echo "Updated $result rows";
}
```

### Using Prepared Statement

```php
$result = $wpdb->query(
	$wpdb->prepare(
		"UPDATE {$wpdb->prefix}myplugin_orders SET status = %s, updated_at = %s WHERE id = %d",
		'completed',
		current_time( 'mysql' ),
		$order_id
	)
);
```

## DELETE Operations

### Using delete()

```php
$result = $wpdb->delete(
	$wpdb->prefix . 'myplugin_orders',
	[ 'id' => $order_id ],
	[ '%d' ]
);

if ( false === $result ) {
	error_log( 'Delete failed: ' . $wpdb->last_error );
}
```

### Using Prepared Statement

```php
$result = $wpdb->query(
	$wpdb->prepare(
		"DELETE FROM {$wpdb->prefix}myplugin_orders WHERE id = %d",
		$order_id
	)
);
```

## REPLACE Operations

Insert or update if exists (based on PRIMARY KEY or UNIQUE):

```php
$result = $wpdb->replace(
	$wpdb->prefix . 'myplugin_settings',
	[
		'setting_key'   => 'api_mode',
		'setting_value' => 'live',
	],
	[ '%s', '%s' ]
);
```

## Complex Queries

### IN Clause

```php
$ids = [ 1, 5, 12, 23 ];

// Generate placeholders
$placeholders = implode( ', ', array_fill( 0, count( $ids ), '%d' ) );

$orders = $wpdb->get_results(
	$wpdb->prepare(
		"SELECT * FROM {$wpdb->prefix}myplugin_orders WHERE id IN ($placeholders)",
		...$ids  // Spread operator
	)
);
```

### LIKE Queries

```php
// Escape LIKE wildcards in search term
$search = '%' . $wpdb->esc_like( $search_term ) . '%';

$results = $wpdb->get_results(
	$wpdb->prepare(
		"SELECT * FROM {$wpdb->prefix}myplugin_orders WHERE customer_name LIKE %s",
		$search
	)
);
```

### JOIN Queries

```php
$results = $wpdb->get_results(
	$wpdb->prepare(
		"SELECT o.*, u.user_email
		FROM {$wpdb->prefix}myplugin_orders o
		INNER JOIN {$wpdb->users} u ON o.user_id = u.ID
		WHERE o.status = %s",
		'pending'
	)
);
```

### Pagination

```php
$page = max( 1, absint( $_GET['paged'] ?? 1 ) );
$per_page = 20;
$offset = ( $page - 1 ) * $per_page;

// Get paginated results
$results = $wpdb->get_results(
	$wpdb->prepare(
		"SELECT * FROM {$wpdb->prefix}myplugin_orders
		ORDER BY created_at DESC
		LIMIT %d OFFSET %d",
		$per_page,
		$offset
	)
);

// Get total count
$total = $wpdb->get_var( "SELECT COUNT(*) FROM {$wpdb->prefix}myplugin_orders" );
$total_pages = ceil( $total / $per_page );
```

## Table Management

### Create Table

```php
function myplugin_create_tables() {
	global $wpdb;

	$charset_collate = $wpdb->get_charset_collate();
	$table_name = $wpdb->prefix . 'myplugin_transactions';

	$sql = "CREATE TABLE $table_name (
		id bigint(20) unsigned NOT NULL AUTO_INCREMENT,
		order_id bigint(20) unsigned NOT NULL,
		transaction_id varchar(100) NOT NULL,
		amount decimal(10,2) NOT NULL,
		currency varchar(3) NOT NULL DEFAULT 'USD',
		status varchar(20) NOT NULL DEFAULT 'pending',
		created_at datetime NOT NULL DEFAULT CURRENT_TIMESTAMP,
		updated_at datetime DEFAULT NULL ON UPDATE CURRENT_TIMESTAMP,
		PRIMARY KEY  (id),
		KEY order_id (order_id),
		KEY transaction_id (transaction_id),
		KEY status (status)
	) $charset_collate;";

	require_once ABSPATH . 'wp-admin/includes/upgrade.php';
	dbDelta( $sql );
}

// Run on plugin activation
register_activation_hook( __FILE__, 'myplugin_create_tables' );
```

### Check Table Exists

```php
function myplugin_table_exists( $table_name ) {
	global $wpdb;

	$query = $wpdb->prepare(
		'SHOW TABLES LIKE %s',
		$wpdb->esc_like( $table_name )
	);

	return $wpdb->get_var( $query ) === $table_name;
}
```

### Drop Table

```php
function myplugin_drop_tables() {
	global $wpdb;

	// Only drop if uninstalling completely
	$wpdb->query( "DROP TABLE IF EXISTS {$wpdb->prefix}myplugin_transactions" );
}

// Run on plugin uninstall
register_uninstall_hook( __FILE__, 'myplugin_drop_tables' );
```

## Error Handling

### Check for Errors

```php
global $wpdb;

$result = $wpdb->query(
	$wpdb->prepare(
		"UPDATE {$wpdb->prefix}myplugin_orders SET status = %s WHERE id = %d",
		$status,
		$order_id
	)
);

if ( false === $result ) {
	// Query failed
	$error = $wpdb->last_error;
	myplugin_log( 'Database error: ' . $error );
	return new WP_Error( 'db_error', __( 'Database operation failed.', 'my-plugin' ) );
}
```

### Debug Queries

```php
// Enable query logging (development only)
define( 'SAVEQUERIES', true );

// After queries run
global $wpdb;
print_r( $wpdb->queries ); // Array of all queries with time and caller

// Get last query
echo $wpdb->last_query;

// Get last error
echo $wpdb->last_error;

// Get rows affected
echo $wpdb->rows_affected;
```

### Suppress Errors

```php
// Suppress errors for specific query
$wpdb->suppress_errors();
$result = $wpdb->query( $query );
$wpdb->suppress_errors( false );

// Or hide errors
$wpdb->hide_errors();
$result = $wpdb->query( $query );
$wpdb->show_errors();
```

## Transactions

```php
global $wpdb;

// Start transaction
$wpdb->query( 'START TRANSACTION' );

try {
	// Multiple operations
	$wpdb->insert( $wpdb->prefix . 'myplugin_orders', [ 'user_id' => $user_id ] );
	$order_id = $wpdb->insert_id;

	$wpdb->insert(
		$wpdb->prefix . 'myplugin_order_items',
		[ 'order_id' => $order_id, 'product_id' => $product_id ]
	);

	// All succeeded
	$wpdb->query( 'COMMIT' );

} catch ( Exception $e ) {
	// Something failed
	$wpdb->query( 'ROLLBACK' );
	throw $e;
}
```

## Performance Tips

### Use Specific Columns

```php
// BAD - fetches all columns
$orders = $wpdb->get_results( "SELECT * FROM {$wpdb->prefix}myplugin_orders" );

// GOOD - fetch only needed columns
$orders = $wpdb->get_results( "SELECT id, status, amount FROM {$wpdb->prefix}myplugin_orders" );
```

### Add Indexes

```php
// Add index for frequently queried columns
$sql = "CREATE TABLE $table_name (
	...
	KEY user_id (user_id),
	KEY status_date (status, created_at),
	KEY customer_email (customer_email(50))  // Partial index for long strings
) $charset_collate;";
```

### Avoid Queries in Loops

```php
// BAD - N+1 queries
foreach ( $order_ids as $order_id ) {
	$order = $wpdb->get_row(
		$wpdb->prepare( "SELECT * FROM {$wpdb->prefix}myplugin_orders WHERE id = %d", $order_id )
	);
}

// GOOD - single query
$placeholders = implode( ', ', array_fill( 0, count( $order_ids ), '%d' ) );
$orders = $wpdb->get_results(
	$wpdb->prepare(
		"SELECT * FROM {$wpdb->prefix}myplugin_orders WHERE id IN ($placeholders)",
		...$order_ids
	)
);
```

### Use Caching

```php
function myplugin_get_order_count( $user_id ) {
	$cache_key = 'myplugin_order_count_' . $user_id;
	$count = wp_cache_get( $cache_key );

	if ( false === $count ) {
		global $wpdb;

		$count = $wpdb->get_var(
			$wpdb->prepare(
				"SELECT COUNT(*) FROM {$wpdb->prefix}myplugin_orders WHERE user_id = %d",
				$user_id
			)
		);

		wp_cache_set( $cache_key, $count, '', HOUR_IN_SECONDS );
	}

	return $count;
}
```

## WordPress Meta Tables

For extensible data, use WordPress meta tables:

```php
// Post meta
update_post_meta( $post_id, '_myplugin_transaction_id', $transaction_id );
$transaction_id = get_post_meta( $post_id, '_myplugin_transaction_id', true );

// User meta
update_user_meta( $user_id, '_myplugin_customer_id', $customer_id );
$customer_id = get_user_meta( $user_id, '_myplugin_customer_id', true );

// Order meta (WooCommerce HPOS compatible)
$order = wc_get_order( $order_id );
$order->update_meta_data( '_myplugin_intent_id', $intent_id );
$order->save();
$intent_id = $order->get_meta( '_myplugin_intent_id' );

// Options (site-wide settings)
update_option( 'myplugin_api_key', $api_key );
$api_key = get_option( 'myplugin_api_key', '' );
```

## Security Checklist

- [ ] Always use `$wpdb->prepare()` for queries with variables
- [ ] Use correct format specifiers (`%d`, `%f`, `%s`)
- [ ] Use `$wpdb->prefix` for table names
- [ ] Escape LIKE wildcards with `$wpdb->esc_like()`
- [ ] Check query results for `false` to detect errors
- [ ] Use transactions for multi-step operations
- [ ] Validate and sanitize data before inserting
- [ ] Log errors without exposing sensitive details