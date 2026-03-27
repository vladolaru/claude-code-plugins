# Test Data Management

Strategies for managing test data: fixtures, factories, and builders.

## Quick Reference

| Strategy | Best For | Example |
|----------|----------|---------|
| Fixtures | Static, reusable data | JSON config, sample files |
| Factories | Dynamic objects with defaults | `$this->factory->post->create()` |
| Builders | Complex objects, fluent API | `OrderBuilder()->withItems()->build()` |

---

## Test Data Strategy Selection

```
What kind of data do you need?
├── Static config/sample data? ──────────────> Fixtures
├── Simple objects with variations? ──────────> Factories
├── Complex objects with many options? ───────> Builders
└── Database records? ────────────────────────> Factories + Transactions
```

---

## Fixtures

**Definition:** Pre-defined, static test data loaded before tests run.

**When to use:** Configuration files, sample JSON/XML responses, static reference data, consistent test inputs.

### Array Fixtures

```php
trait OrderFixtures {
    protected function get_sample_order_data(): array {
        return [
            'status' => 'pending',
            'total'  => 100.00,
            'items'  => [
                [ 'product_id' => 1, 'quantity' => 2, 'price' => 25.00 ],
                [ 'product_id' => 2, 'quantity' => 1, 'price' => 50.00 ],
            ],
        ];
    }
}
```

### Fixture Anti-Patterns

```php
// BAD: Shared state mutated across tests
class OrderTest extends TestCase {
    private static array $order_data;
    public function test_one() {
        self::$order_data['status'] = 'completed';  // Mutates shared fixture!
    }
    public function test_two() {
        // Now status is 'completed', not what we expected
    }
}

// GOOD: Return fresh copy each time
protected function get_order_data(): array {
    return [ 'status' => 'pending', /* ... */ ];
}
```

---

## Factories

**Definition:** Functions/classes that create objects with sensible defaults, allowing overrides for test-specific data.

### WordPress Factory Pattern

```php
class ProductTest extends WP_UnitTestCase {
    public function test_product_has_default_stock() {
        $product_id = $this->factory->post->create( [ 'post_type' => 'product' ] );

        $product = $this->factory->post->create_and_get( [
            'post_type'  => 'product',
            'post_title' => 'Test Product',
        ] );

        $product_ids = $this->factory->post->create_many( 5, [ 'post_type' => 'product' ] );
    }
}
```

### WooCommerce Factories

```php
class OrderTest extends WC_Unit_Test_Case {
    public function test_order_calculations() {
        $customer_id = $this->factory->customer->create();
        $product = $this->factory->product->create_and_get( [ 'regular_price' => '100.00' ] );
        $order = $this->factory->order->create_and_get( [
            'customer_id' => $customer_id,
            'status'      => 'pending',
        ] );
        $order->add_product( $product, 2 );
        $order->calculate_totals();

        $this->assertEquals( 200.00, $order->get_total() );
    }
}
```

### Custom Factory Pattern

```php
class TestFactory {
    public static function create_order( array $overrides = [] ): Order {
        $defaults = [
            'status'      => 'pending',
            'total'       => 100.00,
            'customer_id' => 1,
            'created_at'  => '2024-01-15 10:00:00',
        ];
        return new Order( array_merge( $defaults, $overrides ) );
    }

    public static function create_product( array $overrides = [] ): Product {
        $defaults = [
            'name'  => 'Test Product',
            'price' => 25.00,
            'sku'   => 'TEST-' . uniqid(),
            'stock' => 100,
        ];
        return new Product( array_merge( $defaults, $overrides ) );
    }
}

// Usage
public function test_order_with_specific_status() {
    $order = TestFactory::create_order( [ 'status' => 'completed' ] );
    $this->assertSame( 'completed', $order->status );
}
```

### JavaScript Factory Pattern

```javascript
export function createOrder(overrides = {}) {
    return {
        id: Math.random().toString(36).substr(2, 9),
        status: 'pending',
        total: 100,
        items: [],
        ...overrides,
    };
}

// Usage
it('should calculate order total', () => {
    const order = createOrder({
        items: [createProduct({ price: 30 }), createProduct({ price: 70 })],
    });
    expect(calculateTotal(order)).toBe(100);
});
```

---

## Builders

**Definition:** Fluent interface for constructing complex objects step by step.

**When to use:** Objects with many optional fields, complex object graphs, when test readability matters, when construction logic is non-trivial.

### PHP Builder Pattern

```php
class OrderBuilder {
    private array $data = [
        'status'    => 'pending',
        'items'     => [],
        'discounts' => [],
    ];

    public function withStatus( string $status ): self {
        $this->data['status'] = $status;
        return $this;
    }

    public function withItem( Product $product, int $quantity = 1 ): self {
        $this->data['items'][] = [ 'product' => $product, 'quantity' => $quantity ];
        return $this;
    }

    public function withDiscount( string $code, float $amount ): self {
        $this->data['discounts'][] = [ 'code' => $code, 'amount' => $amount ];
        return $this;
    }

    public function withCustomer( Customer $customer ): self {
        $this->data['customer'] = $customer;
        return $this;
    }

    public function build(): Order {
        return new Order( $this->data );
    }
}

// Usage
public function test_order_with_discount() {
    $order = ( new OrderBuilder() )
        ->withStatus( 'pending' )
        ->withItem( $this->product, 2 )
        ->withDiscount( 'SAVE10', 10.00 )
        ->withCustomer( $this->customer )
        ->build();

    $this->assertSame( 40.00, $order->get_total() ); // 50 - 10
}
```

---

## Test Data Anti-Patterns

### 1. Shared Mutable Fixtures

```php
// WRONG: Shared state modified across tests
class TestCase extends WP_UnitTestCase {
    protected static Order $shared_order;
    public static function setUpBeforeClass(): void { self::$shared_order = self::create_order(); }
    public function test_one() { self::$shared_order->set_status( 'completed' ); }
}

// CORRECT: Fresh data per test
public function test_one() {
    $order = $this->create_order();
    $order->set_status( 'completed' );
}
```

### 2. Over-Specified Test Data

```php
// WRONG: Irrelevant fields obscure test intent
$order = TestFactory::create_order( [
    'id' => 12345, 'customer_id' => 67890, 'billing_email' => 'john@example.com',
    'billing_first_name' => 'John', 'billing_city' => 'New York',
    'shipping_method' => 'flat_rate', 'payment_method' => 'stripe', 'status' => 'pending',
] );

// CORRECT: Only what matters
$order = TestFactory::create_order( [ 'status' => 'pending' ] );
$order->set_status( 'completed' );
$this->assertSame( 'completed', $order->get_status() );
```

### 3. Magic Values Without Context

```php
// WRONG: What do these numbers mean?
$order = TestFactory::create_order( [ 'subtotal' => 147.50, 'tax_rate' => 0.0825, 'shipping' => 12.99 ] );
$this->assertSame( 172.66, $order->get_total() );

// CORRECT: Named values explain intent
$subtotal = 147.50;
$tax_rate = 0.0825;  // Texas state tax
$shipping = 12.99;   // Standard shipping rate
$order = TestFactory::create_order( compact( 'subtotal', 'tax_rate', 'shipping' ) );
$expected_total = $subtotal + ( $subtotal * $tax_rate ) + $shipping;
$this->assertSame( $expected_total, $order->get_total() );
```

### 4. Tight Coupling to Database IDs

```php
// WRONG: Assumes specific IDs exist
$order = TestFactory::create_order( [ 'customer_id' => 42 ] );

// CORRECT: Create what you need
$customer = $this->factory->customer->create_and_get();
$order = TestFactory::create_order( [ 'customer_id' => $customer->get_id() ] );
$this->assertSame( $customer->get_id(), $order->get_customer_id() );
```

---

## Best Practices Summary

| Practice | Why |
|----------|-----|
| Use factories for most objects | Provides defaults, reduces boilerplate |
| Use builders for complex objects | Improves readability, handles many options |
| Use fixtures for static data | Reusable, easy to maintain |
| Create fresh data per test | Prevents state leakage |
| Only specify relevant data | Makes tests clearer, more maintainable |
| Name magic values | Self-documenting tests |
| Create dependencies in test | Avoids coupling to external data |
