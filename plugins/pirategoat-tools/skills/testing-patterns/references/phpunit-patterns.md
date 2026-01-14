# PHPUnit Patterns

PHPUnit patterns for PHP testing, including WordPress and WooCommerce specific utilities.

## Quick Reference: Assertions

| Assertion | Use When |
|-----------|----------|
| `assertSame($expected, $actual)` | Exact equality (type + value) |
| `assertEquals($expected, $actual)` | Loose equality (value only) |
| `assertTrue($condition)` | Boolean true |
| `assertFalse($condition)` | Boolean false |
| `assertNull($value)` | Checking for null |
| `assertInstanceOf($class, $object)` | Type checking |
| `assertCount($count, $array)` | Array length |
| `assertContains($needle, $array)` | Array contains value |
| `assertArrayHasKey($key, $array)` | Array has key |
| `assertStringContainsString($needle, $haystack)` | String contains |

**Prefer `assertSame()` over `assertEquals()`** - stricter is better.

---

## Basic Test Structure

```php
namespace Tests\Unit;

use PHPUnit\Framework\TestCase;

class OrderServiceTest extends TestCase {
    private OrderService $service;

    protected function setUp(): void {
        parent::setUp();
        $this->service = new OrderService();
    }

    protected function tearDown(): void {
        // Cleanup if needed
        parent::tearDown();
    }

    public function test_create_order_returns_order_instance(): void {
        $order = $this->service->create( [ 'items' => [ $this->item ] ] );

        $this->assertInstanceOf( Order::class, $order );
    }
}
```

---

## Data Providers

Use data providers for parameterized tests - same test logic, different inputs.

```php
class PriceCalculatorTest extends TestCase {
    /**
     * @dataProvider price_calculation_data
     */
    public function test_calculate_price(
        float $base_price,
        float $tax_rate,
        float $expected
    ): void {
        $calculator = new PriceCalculator();

        $result = $calculator->calculate( $base_price, $tax_rate );

        $this->assertSame( $expected, $result );
    }

    public static function price_calculation_data(): array {
        return [
            'no tax' => [ 100.00, 0.00, 100.00 ],
            'standard tax' => [ 100.00, 0.10, 110.00 ],
            'high tax' => [ 100.00, 0.25, 125.00 ],
            'zero price' => [ 0.00, 0.10, 0.00 ],
        ];
    }
}
```

### Named Data Sets

```php
public static function order_status_transitions(): array {
    return [
        'pending to processing' => [ 'pending', 'processing', true ],
        'pending to completed' => [ 'pending', 'completed', false ],
        'processing to completed' => [ 'processing', 'completed', true ],
        'completed to pending' => [ 'completed', 'pending', false ],
    ];
}
```

---

## Exception Testing

```php
public function test_invalid_quantity_throws_exception(): void {
    $this->expectException( InvalidArgumentException::class );
    $this->expectExceptionMessage( 'Quantity must be positive' );

    $this->service->add_item( $product, quantity: -1 );
}

// More control with try/catch
public function test_exception_contains_product_id(): void {
    try {
        $this->service->add_item( $product, quantity: -1 );
        $this->fail( 'Expected exception was not thrown' );
    } catch ( InvalidArgumentException $e ) {
        $this->assertStringContainsString(
            $product->get_id(),
            $e->getMessage()
        );
    }
}
```

---

## Mocking in PHPUnit

### Creating Mocks

```php
// Mock with no method stubs (all methods return null)
$mock = $this->createMock( ServiceInterface::class );

// Stub specific method
$mock = $this->createStub( ServiceInterface::class );
$mock->method( 'getValue' )->willReturn( 42 );

// Partial mock (only mock specified methods)
$mock = $this->getMockBuilder( Service::class )
             ->onlyMethods( [ 'externalCall' ] )
             ->getMock();
```

### Stubbing Return Values

```php
// Simple return
$mock->method( 'get' )->willReturn( 'value' );

// Return argument
$mock->method( 'process' )->willReturnArgument( 0 );

// Return self (for fluent interfaces)
$mock->method( 'withOption' )->willReturnSelf();

// Consecutive returns
$mock->method( 'getNext' )
     ->willReturnOnConsecutiveCalls( 'first', 'second', 'third' );

// Callback
$mock->method( 'transform' )
     ->willReturnCallback( fn( $value ) => strtoupper( $value ) );

// Exception
$mock->method( 'fail' )
     ->willThrowException( new RuntimeException( 'Error' ) );
```

### Setting Expectations

```php
// Called exactly once
$mock->expects( $this->once() )
     ->method( 'save' );

// Called with specific arguments
$mock->expects( $this->once() )
     ->method( 'save' )
     ->with( $this->equalTo( 'data' ), $this->anything() );

// Called specific number of times
$mock->expects( $this->exactly( 3 ) )
     ->method( 'log' );

// Never called
$mock->expects( $this->never() )
     ->method( 'delete' );
```

---

## WordPress Testing

### WP_UnitTestCase

WordPress provides `WP_UnitTestCase` with automatic database transactions.

```php
class ProductTest extends WP_UnitTestCase {
    public function test_create_product(): void {
        $product_id = $this->factory->post->create( [
            'post_type' => 'product',
            'post_title' => 'Test Product',
        ] );

        $this->assertGreaterThan( 0, $product_id );
    }
}
```

### WordPress Factories

```php
// Create post
$post_id = $this->factory->post->create();
$post = $this->factory->post->create_and_get();
$post_ids = $this->factory->post->create_many( 5 );

// Create user
$user_id = $this->factory->user->create( [
    'role' => 'administrator',
] );

// Create term
$term_id = $this->factory->term->create( [
    'taxonomy' => 'category',
    'name' => 'Test Category',
] );

// Create comment
$comment_id = $this->factory->comment->create( [
    'comment_post_ID' => $post_id,
] );
```

### Testing Hooks

```php
public function test_action_is_triggered_on_save(): void {
    $callback = $this->getMockBuilder( stdClass::class )
                     ->addMethods( [ 'on_save' ] )
                     ->getMock();

    $callback->expects( $this->once() )
             ->method( 'on_save' );

    add_action( 'my_plugin_order_saved', [ $callback, 'on_save' ] );

    $this->service->save( $order );
}

public function test_filter_modifies_price(): void {
    add_filter( 'my_plugin_price', fn( $price ) => $price * 2 );

    $result = $this->calculator->get_price( 100 );

    $this->assertSame( 200.0, $result );
}
```

### Testing AJAX Handlers

```php
public function test_ajax_handler_returns_success(): void {
    // Set up request
    $_POST['action'] = 'my_action';
    $_POST['nonce'] = wp_create_nonce( 'my_action_nonce' );
    $_POST['data'] = 'test';

    // Capture output
    ob_start();
    try {
        do_action( 'wp_ajax_my_action' );
    } catch ( WPDieException $e ) {
        // wp_send_json calls wp_die
    }
    $output = ob_get_clean();

    $response = json_decode( $output, true );
    $this->assertTrue( $response['success'] );
}
```

### Testing REST Endpoints

```php
public function test_rest_endpoint_returns_products(): void {
    // Create test data
    $this->factory->post->create_many( 3, [ 'post_type' => 'product' ] );

    // Make request
    $request = new WP_REST_Request( 'GET', '/my-plugin/v1/products' );
    $response = rest_do_request( $request );

    $this->assertSame( 200, $response->get_status() );
    $this->assertCount( 3, $response->get_data() );
}

public function test_rest_endpoint_requires_authentication(): void {
    $request = new WP_REST_Request( 'POST', '/my-plugin/v1/products' );
    $response = rest_do_request( $request );

    $this->assertSame( 401, $response->get_status() );
}
```

---

## WooCommerce Testing

### WC_Unit_Test_Case

```php
class OrderTest extends WC_Unit_Test_Case {
    public function test_order_total(): void {
        $product = $this->factory->product->create_and_get( [
            'regular_price' => '100.00',
        ] );

        $order = $this->factory->order->create_and_get();
        $order->add_product( $product, 2 );
        $order->calculate_totals();

        $this->assertEquals( 200.00, $order->get_total() );
    }
}
```

### WooCommerce Factories

```php
// Simple product
$product = $this->factory->product->create_and_get( [
    'name' => 'Test Product',
    'regular_price' => '25.00',
    'sku' => 'TEST-001',
] );

// Variable product
$variable = $this->factory->product_variable->create_and_get();

// Order
$order = $this->factory->order->create_and_get( [
    'status' => 'pending',
    'customer_id' => $customer_id,
] );

// Customer
$customer_id = $this->factory->customer->create( [
    'email' => 'test@example.com',
] );
```

### Testing Payment Gateways

```php
public function test_payment_gateway_processes_payment(): void {
    $order = $this->factory->order->create_and_get( [
        'status' => 'pending',
    ] );
    $order->set_total( 100.00 );
    $order->save();

    $gateway = new My_Payment_Gateway();
    $result = $gateway->process_payment( $order->get_id() );

    $this->assertSame( 'success', $result['result'] );

    // Refresh order from database
    $order = wc_get_order( $order->get_id() );
    $this->assertSame( 'processing', $order->get_status() );
}
```

---

## Brain Monkey for Isolation

Brain Monkey allows testing WordPress code without loading WordPress.

```php
use Brain\Monkey;
use Brain\Monkey\Functions;

class PluginTest extends TestCase {
    protected function setUp(): void {
        parent::setUp();
        Monkey\setUp();
    }

    protected function tearDown(): void {
        Monkey\tearDown();
        parent::tearDown();
    }

    public function test_uses_get_option(): void {
        Functions\when( 'get_option' )
            ->justReturn( 'stored_value' );

        $result = $this->service->get_setting( 'my_option' );

        $this->assertSame( 'stored_value', $result );
    }

    public function test_calls_update_option(): void {
        Functions\expect( 'update_option' )
            ->once()
            ->with( 'my_option', 'new_value' )
            ->andReturn( true );

        $this->service->save_setting( 'my_option', 'new_value' );
    }
}
```

---

## Best Practices Summary

| Do | Don't |
|----|-------|
| Use `assertSame()` for strict equality | Use `assertEquals()` by default |
| Use data providers for variations | Copy-paste tests with different data |
| Test hooks with callbacks | Assume hooks fire correctly |
| Use Brain Monkey for isolation | Load full WordPress for unit tests |
| Use factories for test data | Hard-code IDs and data |
| Clean up in tearDown | Leave test artifacts |
