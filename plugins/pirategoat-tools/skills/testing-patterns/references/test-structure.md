# Test Structure

Guidelines for organizing tests: the AAA pattern, naming conventions, and test organization strategies.

## Quick Reference

| Aspect | PHP Convention | JavaScript Convention |
|--------|----------------|----------------------|
| Naming | `test_[scenario]_[expectation]` | `should [expectation] when [condition]` |
| Structure | Arrange-Act-Assert | Arrange-Act-Assert |
| Grouping | Test class per class | `describe` block per module |
| Setup | `setUp()` method | `beforeEach()` function |
| Teardown | `tearDown()` method | `afterEach()` function |

---

## The AAA Pattern

Every test should have three distinct sections: **Arrange**, **Act**, **Assert**.

### Basic Structure

```php
public function test_calculate_discount_returns_ten_percent_for_premium_customers() {
    // Arrange - Set up test data and dependencies
    $customer = $this->factory->customer->create_and_get( [
        'membership' => 'premium',
    ] );
    $cart = $this->create_cart_with_total( 100.00 );

    // Act - Execute the code under test
    $discount = $this->calculator->calculate_discount( $cart, $customer );

    // Assert - Verify the result
    $this->assertSame( 10.00, $discount );
}
```

```javascript
it('should calculate 10% discount for premium customers', () => {
    // Arrange
    const customer = createCustomer({ membership: 'premium' });
    const cart = createCart({ total: 100 });
    const calculator = new DiscountCalculator();

    // Act
    const discount = calculator.calculateDiscount(cart, customer);

    // Assert
    expect(discount).toBe(10);
});
```

### Visual Separation

Use blank lines to clearly separate the three sections:

```php
// GOOD: Clear visual separation
public function test_order_status_changes_to_processing_after_payment() {
    $order = $this->factory->order->create_and_get( [ 'status' => 'pending' ] );
    $payment = $this->create_successful_payment( $order );

    $order->process_payment( $payment );

    $this->assertSame( 'processing', $order->get_status() );
}

// BAD: No separation - harder to read
public function test_order_status_changes_to_processing_after_payment() {
    $order = $this->factory->order->create_and_get( [ 'status' => 'pending' ] );
    $payment = $this->create_successful_payment( $order );
    $order->process_payment( $payment );
    $this->assertSame( 'processing', $order->get_status() );
}
```

### Comments vs Self-Documenting

For complex tests, comments help. For simple tests, they're noise.

```php
// GOOD: Comments for complex setup
public function test_bulk_import_handles_duplicate_skus_gracefully() {
    // Arrange: Create existing products that will conflict with import
    $existing_sku = 'SKU-001';
    $this->factory->product->create( [ 'sku' => $existing_sku ] );

    // Import file contains the same SKU
    $import_data = [
        [ 'sku' => $existing_sku, 'name' => 'Updated Product' ],
        [ 'sku' => 'SKU-002', 'name' => 'New Product' ],
    ];

    // Act
    $result = $this->importer->import( $import_data );

    // Assert: Should update existing, create new, report conflicts
    $this->assertSame( 1, $result->updated_count );
    $this->assertSame( 1, $result->created_count );
    $this->assertSame( 1, $result->conflict_count );
}

// GOOD: No comments needed - test is self-documenting
public function test_calculate_tax_returns_zero_when_disabled() {
    $settings = $this->create_settings( [ 'tax_enabled' => false ] );
    $calculator = new TaxCalculator( $settings );

    $tax = $calculator->calculate( 100.00 );

    $this->assertSame( 0.00, $tax );
}
```

---

## Test Naming Conventions

### PHP (PHPUnit)

**Pattern:** `test_[what_is_being_tested]_[expected_behavior]_[under_what_conditions]`

```php
// Scenario + expected outcome
public function test_get_price_returns_sale_price_when_on_sale() { }
public function test_get_price_returns_regular_price_when_not_on_sale() { }

// Edge cases
public function test_calculate_shipping_returns_zero_for_free_shipping_method() { }
public function test_calculate_shipping_throws_exception_for_invalid_address() { }

// Boundary conditions
public function test_apply_discount_returns_maximum_when_exceeds_cap() { }
public function test_apply_discount_returns_zero_when_below_minimum_order() { }
```

### JavaScript (Jest/Vitest)

**Pattern:** `should [expected behavior] when [condition]`

```javascript
describe('CartService', () => {
    describe('calculateTotal', () => {
        it('should return zero when cart is empty', () => { });
        it('should include tax when tax is enabled', () => { });
        it('should exclude tax when tax is disabled', () => { });
        it('should apply discount code when valid', () => { });
        it('should ignore discount code when expired', () => { });
    });
});
```

### Naming Anti-Patterns

```php
// BAD: Generic names
public function test_order() { }          // What about the order?
public function test_order_2() { }        // Even worse
public function test_it_works() { }       // What works?
public function test_bug_fix() { }        // What bug?

// BAD: Implementation details in name
public function test_calls_save_method() { }  // Tests implementation, not behavior
public function test_sets_variable() { }      // Same issue

// GOOD: Behavior-focused names
public function test_order_total_includes_shipping_for_physical_products() { }
public function test_order_total_excludes_shipping_for_digital_products() { }
```

---

## Test Organization

### PHP: One Test Class Per Class

```
tests/
├── Unit/
│   ├── Services/
│   │   ├── OrderServiceTest.php
│   │   └── PaymentServiceTest.php
│   └── Models/
│       ├── OrderTest.php
│       └── ProductTest.php
└── Integration/
    ├── API/
    │   └── OrdersEndpointTest.php
    └── Database/
        └── OrderRepositoryTest.php
```

```php
namespace Tests\Unit\Services;

class OrderServiceTest extends TestCase {
    private OrderService $service;

    public function setUp(): void {
        parent::setUp();
        $this->service = new OrderService();
    }

    public function test_create_order_with_valid_data() { }
    public function test_create_order_with_invalid_data_throws_exception() { }
    public function test_update_order_status() { }
}
```

### JavaScript: Describe Blocks Mirror Module Structure

```
src/
├── services/
│   └── orderService.ts
└── utils/
    └── pricing.ts

tests/
├── services/
│   └── orderService.test.ts
└── utils/
    └── pricing.test.ts
```

```javascript
// tests/services/orderService.test.ts
describe('OrderService', () => {
    describe('createOrder', () => {
        it('should create order with valid data', () => { });
        it('should throw when data is invalid', () => { });
    });

    describe('updateStatus', () => {
        it('should update status for existing order', () => { });
        it('should throw when order not found', () => { });
    });
});
```

### Grouping by Feature vs by Class

**By class (default):** Good for unit tests, mirrors source structure.

**By feature:** Good for integration tests, groups related behaviors.

```
tests/
├── unit/                    # By class
│   └── OrderService.test.ts
└── integration/            # By feature
    └── checkout/
        ├── guest-checkout.test.ts
        ├── member-checkout.test.ts
        └── express-checkout.test.ts
```

---

## Setup and Teardown

### Per-Test Setup (Most Common)

```php
class OrderTest extends WP_UnitTestCase {
    private Order $order;

    public function setUp(): void {
        parent::setUp();
        $this->order = $this->factory->order->create_and_get();
    }

    public function tearDown(): void {
        // Cleanup if needed (usually handled by WP_UnitTestCase)
        parent::tearDown();
    }
}
```

```javascript
describe('OrderService', () => {
    let orderService;
    let mockPaymentGateway;

    beforeEach(() => {
        mockPaymentGateway = createMockPaymentGateway();
        orderService = new OrderService(mockPaymentGateway);
    });

    afterEach(() => {
        jest.clearAllMocks();
    });
});
```

### When to Use Setup Methods

| Use setUp/beforeEach | Use inline setup |
|---------------------|------------------|
| Same setup for multiple tests | Unique setup per test |
| Creating service instances | Test-specific data |
| Resetting mocks | Highlighting test-specific conditions |

```php
// GOOD: Common setup in setUp()
public function setUp(): void {
    parent::setUp();
    $this->service = new OrderService();
}

// GOOD: Test-specific setup inline (visible and explicit)
public function test_order_requires_shipping_for_physical_products() {
    $product = $this->factory->product->create_and_get( [
        'type' => 'physical',  // This is the key condition for this test
    ] );
    // ...
}
```

### Global Setup (Use Sparingly)

```php
// PHPUnit: setUpBeforeClass for expensive one-time setup
public static function setUpBeforeClass(): void {
    parent::setUpBeforeClass();
    self::$expensive_resource = create_expensive_resource();
}

public static function tearDownAfterClass(): void {
    self::$expensive_resource->cleanup();
    parent::tearDownAfterClass();
}
```

```javascript
// Jest: beforeAll/afterAll
beforeAll(async () => {
    await database.connect();
});

afterAll(async () => {
    await database.disconnect();
});
```

---

## Test Data Patterns

### Factory Pattern

Create data with sensible defaults, override what matters for the test.

```php
public function test_order_total_includes_tax() {
    // Only specify what's relevant to this test
    $order = $this->factory->order->create_and_get( [
        'subtotal' => 100.00,
        'tax_rate' => 0.10,
    ] );

    $this->assertSame( 110.00, $order->get_total() );
}
```

### Builder Pattern

For complex objects with many optional fields.

```javascript
const order = new OrderBuilder()
    .withCustomer(customer)
    .withItems([item1, item2])
    .withShipping('express')
    .withDiscount('SAVE10')
    .build();
```

### Test Data Should Be Minimal

```php
// BAD: Too much irrelevant data
public function test_order_status_changes() {
    $order = $this->factory->order->create_and_get( [
        'customer_id' => 123,
        'billing_email' => 'test@example.com',
        'billing_first_name' => 'John',
        'billing_last_name' => 'Doe',
        'billing_address_1' => '123 Main St',
        'billing_city' => 'New York',
        'billing_state' => 'NY',
        'billing_postcode' => '10001',
        'shipping_method' => 'flat_rate',
        'payment_method' => 'stripe',
        'status' => 'pending',  // Only this matters!
    ] );

    $order->update_status( 'processing' );

    $this->assertSame( 'processing', $order->get_status() );
}

// GOOD: Only relevant data
public function test_order_status_changes() {
    $order = $this->factory->order->create_and_get( [ 'status' => 'pending' ] );

    $order->update_status( 'processing' );

    $this->assertSame( 'processing', $order->get_status() );
}
```

---

## Test Categories

### Unit Tests

- Test single units in isolation
- Mock all dependencies
- Very fast (< 10ms per test)
- High volume (most tests should be unit tests)

### Integration Tests

- Test multiple units working together
- May use real database, file system
- Slower (100ms - 2s per test)
- Fewer than unit tests

### End-to-End Tests

- Test complete user flows
- Real browser, real services
- Slowest (seconds to minutes)
- Fewest tests, highest value paths only

### Test Pyramid

```
        /\
       /  \  E2E (few)
      /----\
     /      \  Integration (some)
    /--------\
   /          \  Unit (many)
  /------------\
```

---

## Anti-Patterns Summary

| Anti-Pattern | Problem | Fix |
|--------------|---------|-----|
| No AAA separation | Hard to read | Add blank lines between sections |
| Vague test names | Unclear what failed | Use `scenario_expectedBehavior` |
| Setup in tests | Duplication | Use setUp/beforeEach |
| Too much setup | Slow, fragile | Use factories with defaults |
| Tests in wrong category | Slow suite | Unit test when possible |
| Nested describes (deep) | Hard to navigate | Max 2-3 levels |
| Shared mutable state | Flaky tests | Reset in setUp |
