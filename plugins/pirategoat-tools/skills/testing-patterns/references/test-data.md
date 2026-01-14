# Test Data Management

Strategies for managing test data: fixtures, factories, and builders.

## Quick Reference

| Strategy | Best For | Example |
|----------|----------|---------|
| Fixtures | Static, reusable data | JSON config, sample files |
| Factories | Dynamic objects with defaults | `$this->factory->post->create()` |
| Builders | Complex objects, fluent API | `OrderBuilder().withItems().build()` |

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

### When to Use

- Configuration files
- Sample JSON/XML responses
- Static reference data
- Consistent test inputs

### File-Based Fixtures

```
tests/
└── fixtures/
    ├── sample-product.json
    ├── sample-order.json
    └── api-responses/
        ├── success.json
        └── error.json
```

```php
// Load fixture in test
public function test_import_processes_product_json() {
    $json = file_get_contents( __DIR__ . '/fixtures/sample-product.json' );
    $data = json_decode( $json, true );

    $result = $this->importer->import( $data );

    $this->assertTrue( $result->success );
}
```

### Array Fixtures

```php
// Defined in test class or trait
trait OrderFixtures {
    protected function get_sample_order_data(): array {
        return [
            'status' => 'pending',
            'total' => 100.00,
            'items' => [
                [ 'product_id' => 1, 'quantity' => 2, 'price' => 25.00 ],
                [ 'product_id' => 2, 'quantity' => 1, 'price' => 50.00 ],
            ],
        ];
    }
}
```

### Fixture Anti-Patterns

```php
// BAD: Fixtures modified during tests
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
    return [
        'status' => 'pending',
        // ...
    ];
}
```

---

## Factories

**Definition:** Functions/classes that create objects with sensible defaults, allowing overrides for test-specific data.

### WordPress Factory Pattern

WordPress test framework provides factories for common objects:

```php
class ProductTest extends WP_UnitTestCase {
    public function test_product_has_default_stock() {
        // Create with defaults
        $product_id = $this->factory->post->create( [
            'post_type' => 'product',
        ] );

        // Create and get the object
        $product = $this->factory->post->create_and_get( [
            'post_type' => 'product',
            'post_title' => 'Test Product',
        ] );

        // Create multiple
        $product_ids = $this->factory->post->create_many( 5, [
            'post_type' => 'product',
        ] );
    }
}
```

### WooCommerce Factories

```php
class OrderTest extends WC_Unit_Test_Case {
    public function test_order_calculations() {
        // Create customer
        $customer_id = $this->factory->customer->create();

        // Create product
        $product = $this->factory->product->create_and_get( [
            'regular_price' => '100.00',
        ] );

        // Create order
        $order = $this->factory->order->create_and_get( [
            'customer_id' => $customer_id,
            'status' => 'pending',
        ] );

        // Add item to order
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
            'status' => 'pending',
            'total' => 100.00,
            'customer_id' => 1,
            'created_at' => '2024-01-15 10:00:00',
        ];

        return new Order( array_merge( $defaults, $overrides ) );
    }

    public static function create_product( array $overrides = [] ): Product {
        $defaults = [
            'name' => 'Test Product',
            'price' => 25.00,
            'sku' => 'TEST-' . uniqid(),
            'stock' => 100,
        ];

        return new Product( array_merge( $defaults, $overrides ) );
    }
}

// Usage in tests
public function test_order_with_specific_status() {
    $order = TestFactory::create_order( [ 'status' => 'completed' ] );
    $this->assertSame( 'completed', $order->status );
}
```

### JavaScript Factory Pattern

```javascript
// factories.js
export function createOrder(overrides = {}) {
    return {
        id: Math.random().toString(36).substr(2, 9),
        status: 'pending',
        total: 100,
        items: [],
        createdAt: new Date().toISOString(),
        ...overrides,
    };
}

export function createProduct(overrides = {}) {
    return {
        id: Math.random().toString(36).substr(2, 9),
        name: 'Test Product',
        price: 25,
        stock: 100,
        ...overrides,
    };
}

// Usage in tests
it('should calculate order total', () => {
    const order = createOrder({
        items: [
            createProduct({ price: 30 }),
            createProduct({ price: 70 }),
        ],
    });

    expect(calculateTotal(order)).toBe(100);
});
```

---

## Builders

**Definition:** Fluent interface for constructing complex objects step by step.

### When to Use Builders

- Objects with many optional fields
- Complex object graphs
- When test readability is important
- When construction logic is non-trivial

### PHP Builder Pattern

```php
class OrderBuilder {
    private array $data = [
        'status' => 'pending',
        'items' => [],
        'discounts' => [],
    ];

    public function withStatus( string $status ): self {
        $this->data['status'] = $status;
        return $this;
    }

    public function withItem( Product $product, int $quantity = 1 ): self {
        $this->data['items'][] = [
            'product' => $product,
            'quantity' => $quantity,
        ];
        return $this;
    }

    public function withDiscount( string $code, float $amount ): self {
        $this->data['discounts'][] = [
            'code' => $code,
            'amount' => $amount,
        ];
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

### JavaScript Builder Pattern

```javascript
class OrderBuilder {
    constructor() {
        this.data = {
            status: 'pending',
            items: [],
            discounts: [],
        };
    }

    withStatus(status) {
        this.data.status = status;
        return this;
    }

    withItem(product, quantity = 1) {
        this.data.items.push({ product, quantity });
        return this;
    }

    withDiscount(code, amount) {
        this.data.discounts.push({ code, amount });
        return this;
    }

    build() {
        return new Order(this.data);
    }
}

// Usage
it('should apply discount to order total', () => {
    const order = new OrderBuilder()
        .withItem(createProduct({ price: 50 }), 2)
        .withDiscount('SAVE10', 10)
        .build();

    expect(order.getTotal()).toBe(90); // 100 - 10
});
```

### Builder + Factory Combination

```php
class OrderBuilder {
    private array $items = [];

    public function withRandomProducts( int $count ): self {
        for ( $i = 0; $i < $count; $i++ ) {
            $this->items[] = TestFactory::create_product();
        }
        return $this;
    }

    public function withSpecificProduct( array $attributes ): self {
        $this->items[] = TestFactory::create_product( $attributes );
        return $this;
    }

    public function build(): Order {
        $order = TestFactory::create_order();
        foreach ( $this->items as $item ) {
            $order->add_item( $item );
        }
        return $order;
    }
}
```

---

## Test Data Anti-Patterns

### 1. Shared Mutable Fixtures

```php
// BAD: Shared state gets modified
class TestCase extends WP_UnitTestCase {
    protected static Order $shared_order;

    public static function setUpBeforeClass(): void {
        self::$shared_order = self::create_order();
    }

    public function test_one() {
        self::$shared_order->set_status( 'completed' );
        // Now $shared_order is modified for all other tests
    }
}

// GOOD: Fresh data per test
public function test_one() {
    $order = $this->create_order();
    $order->set_status( 'completed' );
    // Only affects this test
}
```

### 2. Over-Specified Test Data

```php
// BAD: Too much irrelevant data
public function test_order_status_change() {
    $order = TestFactory::create_order( [
        'id' => 12345,
        'customer_id' => 67890,
        'billing_email' => 'john@example.com',
        'billing_first_name' => 'John',
        'billing_last_name' => 'Doe',
        'billing_address_1' => '123 Main St',
        'billing_city' => 'New York',
        'billing_state' => 'NY',
        'billing_postcode' => '10001',
        'billing_country' => 'US',
        'shipping_method' => 'flat_rate',
        'payment_method' => 'stripe',
        'status' => 'pending',
    ] );

    $order->set_status( 'completed' );

    $this->assertSame( 'completed', $order->get_status() );
}

// GOOD: Only what matters
public function test_order_status_change() {
    $order = TestFactory::create_order( [ 'status' => 'pending' ] );

    $order->set_status( 'completed' );

    $this->assertSame( 'completed', $order->get_status() );
}
```

### 3. Magic Values Without Context

```php
// BAD: What do these numbers mean?
public function test_pricing() {
    $order = TestFactory::create_order( [
        'subtotal' => 147.50,
        'tax_rate' => 0.0825,
        'shipping' => 12.99,
    ] );

    $this->assertSame( 172.66, $order->get_total() );
}

// GOOD: Named values explain intent
public function test_pricing_includes_tax_and_shipping() {
    $subtotal = 147.50;
    $tax_rate = 0.0825;  // Texas state tax
    $shipping = 12.99;   // Standard shipping rate

    $order = TestFactory::create_order( [
        'subtotal' => $subtotal,
        'tax_rate' => $tax_rate,
        'shipping' => $shipping,
    ] );

    $expected_tax = $subtotal * $tax_rate;  // 12.17
    $expected_total = $subtotal + $expected_tax + $shipping;

    $this->assertSame( $expected_total, $order->get_total() );
}
```

### 4. Tight Coupling to Database IDs

```php
// BAD: Assumes specific IDs exist
public function test_order_customer() {
    $order = TestFactory::create_order( [ 'customer_id' => 42 ] );
    // What if customer 42 doesn't exist?
}

// GOOD: Create what you need
public function test_order_customer() {
    $customer = $this->factory->customer->create_and_get();
    $order = TestFactory::create_order( [ 'customer_id' => $customer->get_id() ] );

    $this->assertSame( $customer->get_id(), $order->get_customer_id() );
}
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
