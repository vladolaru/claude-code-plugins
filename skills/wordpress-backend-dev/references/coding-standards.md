# WordPress PHP Coding Standards (WPCS)

Reference for WordPress PHP coding standards based on the official WordPress Coding Standards.

## Naming Conventions

### Functions
- Use lowercase letters
- Separate words with underscores
- Prefix with plugin/theme slug to avoid conflicts

```php
// Good
function myplugin_get_items() {}
function myplugin_process_request() {}

// Bad
function getItems() {}  // camelCase
function get_items() {} // No prefix
```

### Classes
- Use capitalized words separated by underscores
- Prefix with plugin slug
- File name: `class-{class-name}.php`

```php
// Good - in file: class-my-plugin-handler.php
class My_Plugin_Handler {}

// Good - in file: class-my-plugin-api-client.php
class My_Plugin_API_Client {}

// Bad
class MyPluginHandler {}  // No underscores
class Handler {}          // No prefix
```

### Constants
- Use uppercase letters
- Separate words with underscores
- Prefix with plugin slug

```php
// Good
define( 'MYPLUGIN_VERSION', '1.0.0' );
define( 'MYPLUGIN_PLUGIN_PATH', plugin_dir_path( __FILE__ ) );
const MYPLUGIN_MIN_PHP_VERSION = '7.4';

// Bad
define( 'version', '1.0.0' );    // Lowercase
define( 'PLUGIN_PATH', '...' );  // No prefix
```

### Variables
- Use lowercase letters
- Separate words with underscores
- Use descriptive names

```php
// Good
$order_id = 123;
$payment_method = 'card';
$customer_email = 'test@example.com';

// Bad
$orderId = 123;      // camelCase
$pm = 'card';        // Unclear abbreviation
$e = 'test@...';     // Single letter
```

## Formatting

### Indentation
- Use TABS, not spaces
- One tab per indentation level

```php
// Good
function myplugin_example() {
	if ( $condition ) {
		do_something();
	}
}

// Bad (spaces)
function myplugin_example() {
    if ( $condition ) {
        do_something();
    }
}
```

### Braces
- Opening brace on same line as declaration
- Closing brace on its own line
- Always use braces, even for single-line blocks

```php
// Good
function myplugin_example() {
	if ( $condition ) {
		return true;
	}
	return false;
}

// Bad - opening brace on new line
function myplugin_example()
{
	if ( $condition )
	{
		return true;
	}
}

// Bad - no braces for single line
if ( $condition )
	return true;
```

### Spacing

**Inside parentheses**:
```php
// Good - spaces inside parentheses
if ( $condition ) {}
foreach ( $items as $item ) {}
function_call( $arg1, $arg2 );
array( 'key' => 'value' );

// Bad - no spaces
if ($condition) {}
foreach($items as $item) {}
function_call($arg1, $arg2);
```

**Around operators**:
```php
// Good
$sum = $a + $b;
$result = $condition ? 'yes' : 'no';
$array['key'] = $value;

// Bad
$sum=$a+$b;
$result=$condition?'yes':'no';
```

**After control structure keywords**:
```php
// Good
if ( $condition ) {}
for ( $i = 0; $i < 10; $i++ ) {}
while ( $condition ) {}
switch ( $value ) {}

// Bad - no space after keyword
if( $condition ) {}
for( $i = 0; $i < 10; $i++ ) {}
```

### Yoda Conditions

Place the constant or literal on the left side of comparisons:

```php
// Good - Yoda style
if ( 'active' === $status ) {}
if ( null === $value ) {}
if ( 0 === $count ) {}
if ( true === $is_enabled ) {}

// Bad - variable on left
if ( $status === 'active' ) {}
if ( $value === null ) {}
```

**Rationale**: Prevents accidental assignment (`=` instead of `===`) since `'active' = $status` throws an error.

### Arrays

**Short array syntax** (PHP 5.4+):
```php
// Good
$array = [
	'key1' => 'value1',
	'key2' => 'value2',
];

// Also acceptable for simple arrays
$simple = [ 'item1', 'item2', 'item3' ];

// Long arrays should have trailing comma
$config = [
	'option1' => true,
	'option2' => false,
	'option3' => 'value',  // Trailing comma
];
```

### Strings

**Single vs double quotes**:
```php
// Single quotes for strings without variables
$string = 'Hello World';

// Double quotes when including variables
$greeting = "Hello {$name}";

// Concatenation for complex strings
$message = 'Hello ' . esc_html( $name ) . ', welcome!';
```

## Type Declarations

Use type declarations when possible (PHP 7.0+):

```php
/**
 * Processes a payment.
 *
 * @param int    $order_id Order ID.
 * @param string $method   Payment method.
 * @return bool Whether payment succeeded.
 */
function myplugin_process_payment( int $order_id, string $method = 'card' ): bool {
	// Implementation
	return true;
}
```

**Nullable types** (PHP 7.1+):
```php
function myplugin_get_customer( int $id ): ?My_Plugin_Customer {
	// Returns My_Plugin_Customer or null
}
```

## Error Handling

### WP_Error

Use `WP_Error` for recoverable errors:

```php
function myplugin_validate_payment( $data ) {
	if ( empty( $data['amount'] ) ) {
		return new WP_Error(
			'missing_amount',
			__( 'Payment amount is required.', 'my-plugin' )
		);
	}

	if ( $data['amount'] <= 0 ) {
		return new WP_Error(
			'invalid_amount',
			__( 'Payment amount must be positive.', 'my-plugin' ),
			[ 'amount' => $data['amount'] ]
		);
	}

	return true;
}

// Usage
$result = myplugin_validate_payment( $data );
if ( is_wp_error( $result ) ) {
	$error_code = $result->get_error_code();
	$error_message = $result->get_error_message();
	$error_data = $result->get_error_data();
}
```

### Exceptions

Use exceptions for unrecoverable errors in OOP code:

```php
class My_Plugin_API_Exception extends Exception {
	protected $error_code;

	public function __construct( $message, $error_code, $http_code = 0 ) {
		parent::__construct( $message, $http_code );
		$this->error_code = $error_code;
	}

	public function get_error_code() {
		return $this->error_code;
	}
}

// Usage
try {
	$response = $api->request( $endpoint );
} catch ( My_Plugin_API_Exception $e ) {
	myplugin_log( 'API Error: ' . $e->getMessage() );
	return new WP_Error( $e->get_error_code(), $e->getMessage() );
}
```

## File Organization

### File Headers

```php
<?php
/**
 * Payment gateway functionality.
 *
 * Handles payment processing, refunds, and transaction management.
 *
 * @package My_Plugin\Gateway
 * @since   1.0.0
 */

defined( 'ABSPATH' ) || exit;
```

### Class Files

```php
<?php
/**
 * My_Plugin_Payment_Gateway class.
 *
 * @package My_Plugin
 * @since   1.0.0
 */

defined( 'ABSPATH' ) || exit;

/**
 * Handles payment gateway functionality.
 *
 * @since 1.0.0
 */
class My_Plugin_Payment_Gateway extends WC_Payment_Gateway {

	/**
	 * Gateway ID.
	 *
	 * @var string
	 */
	public $id = 'myplugin';

	/**
	 * Constructor.
	 *
	 * @since 1.0.0
	 */
	public function __construct() {
		// Initialize
	}
}
```

## Deprecated Code

When deprecating functions:

```php
/**
 * Gets the payment status.
 *
 * @since      1.0.0
 * @deprecated 2.0.0 Use myplugin_get_order_status() instead.
 * @see        myplugin_get_order_status()
 *
 * @param int $order_id Order ID.
 * @return string Payment status.
 */
function myplugin_get_payment_status( $order_id ) {
	_deprecated_function( __FUNCTION__, '2.0.0', 'myplugin_get_order_status()' );
	return myplugin_get_order_status( $order_id );
}
```

## PHPCS Configuration

Use the WordPress-Extra ruleset for comprehensive checking:

```xml
<?xml version="1.0"?>
<ruleset name="My_Plugin">
	<description>WordPress Coding Standards for My Plugin</description>

	<rule ref="WordPress-Extra"/>
	<rule ref="WordPress-Docs"/>

	<config name="minimum_supported_wp_version" value="6.0"/>
	<config name="testVersion" value="7.4-"/>

	<rule ref="WordPress.WP.I18n">
		<properties>
			<property name="text_domain" type="array">
				<element value="my-plugin"/>
			</property>
		</properties>
	</rule>

	<exclude-pattern>/vendor/*</exclude-pattern>
	<exclude-pattern>/node_modules/*</exclude-pattern>
</ruleset>
```

## Common PHPCS Fixes

### Missing doc comment
```php
// Before (error)
function myplugin_example() {}

// After (fixed)
/**
 * Example function description.
 *
 * @since 1.0.0
 */
function myplugin_example() {}
```

### Loose comparison
```php
// Before (error)
if ( $value == 'test' ) {}

// After (fixed)
if ( 'test' === $value ) {}
```

### Missing escape
```php
// Before (error)
echo $variable;

// After (fixed)
echo esc_html( $variable );
```

### Missing text domain
```php
// Before (error)
__( 'Hello World' );

// After (fixed)
__( 'Hello World', 'my-plugin' );
```