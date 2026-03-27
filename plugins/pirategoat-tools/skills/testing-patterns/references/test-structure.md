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

Complex setup benefits from AAA comments. Simple, self-documenting tests don't need them.

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

| Anti-Pattern | Example | Problem |
|--------------|---------|---------|
| Generic name | `test_order()`, `test_it_works()` | Unclear what failed or what behavior is tested |
| Numbered suffix | `test_order_2()` | No semantic meaning |
| Bug reference only | `test_bug_fix()` | Meaningless after the fix ships |
| Implementation detail | `test_calls_save_method()` | Tests implementation, not behavior |
| Variable reference | `test_sets_variable()` | Couples name to internals |

Good names are behavior-focused: `test_order_total_includes_shipping_for_physical_products()`.

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

### When to Use Setup Methods

| Use setUp/beforeEach | Use inline setup |
|---------------------|------------------|
| Same setup for multiple tests | Unique setup per test |
| Creating service instances | Test-specific data |
| Resetting mocks | Highlighting test-specific conditions |

**Guideline:** Common infrastructure goes in setUp/beforeEach. Test-specific conditions stay inline where they're visible and explicit — the reader should see the key condition without jumping to setup.

### Global Setup (Use Sparingly)

Global setup (`setUpBeforeClass`/`beforeAll`) is for expensive one-time resources (database connections, compiled assets). Most test suites don't need it. If you reach for it, verify the resource truly can't be per-test — shared state between tests is a flakiness vector.

---

## Test Data Patterns

See `test-data.md` for fixtures, factories, builders, and test data management.

---

## Test Categories

See `test-layers.md` for unit/integration/system layer strategy.
