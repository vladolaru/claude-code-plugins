# Test Coverage

What to test, what to skip, and how to prioritize test coverage effectively.

## Quick Reference

| Priority | What to Test | Why |
|----------|--------------|-----|
| Critical | Business logic, calculations | Core value of the application |
| High | Error handling, edge cases | User-facing failures |
| Medium | Integration points | Contract verification |
| Low | Simple getters/setters | Low value, high cost |
| Skip | Framework code, generated code | Already tested |

---

## Coverage Philosophy

### Coverage as a Guide, Not a Goal

```
✅ Good: "We have confidence our critical paths work"
❌ Bad: "We achieved 95% line coverage"
```

**High coverage with bad tests is worse than lower coverage with good tests.**

```php
// BAD: 100% coverage, zero value
public function test_constructor() {
    $order = new Order( [ 'id' => 1 ] );
    $this->assertInstanceOf( Order::class, $order );
}

// GOOD: Tests actual behavior
public function test_order_calculates_total_with_tax() {
    $order = new Order( [ 'subtotal' => 100, 'tax_rate' => 0.10 ] );
    $this->assertSame( 110.00, $order->get_total() );
}
```

### The Coverage Trap

Optimizing for coverage percentage leads to:
- Testing trivial code
- Tests that don't verify behavior
- False confidence

Instead, optimize for:
- Risk coverage (high-risk code is tested)
- Behavior coverage (important behaviors are verified)
- Confidence (team trusts the test suite)

---

## What to Test

### Business Logic (Always)

Core calculations, rules, and algorithms that define your application's value.

```php
// MUST TEST: Payment calculation
public function test_calculate_installment_payment() {
    $calculator = new PaymentCalculator();

    $installment = $calculator->calculate_installment(
        total: 1200.00,
        months: 12,
        interest_rate: 0.05
    );

    $this->assertSame( 105.00, $installment );
}

// MUST TEST: Business rule
public function test_order_requires_minimum_quantity_for_wholesale() {
    $order = new WholesaleOrder();

    $this->expectException( MinimumQuantityException::class );
    $order->add_item( $product, quantity: 5 );  // Minimum is 10
}
```

### Edge Cases (Always)

Boundary conditions where bugs hide.

```php
// Edge cases for pagination
public function test_pagination_edge_cases(): void {
    $paginator = new Paginator( total_items: 100, per_page: 10 );

    // First page
    $this->assertSame( 1, $paginator->get_previous_page() ); // Can't go before 1
    $this->assertSame( 2, $paginator->get_next_page() );

    // Last page
    $paginator->set_current_page( 10 );
    $this->assertSame( 9, $paginator->get_previous_page() );
    $this->assertSame( 10, $paginator->get_next_page() ); // Can't go past last

    // Beyond last page
    $paginator->set_current_page( 15 );
    $this->assertSame( 10, $paginator->get_current_page() ); // Clamped to last
}

// Edge case: empty input
public function test_calculate_average_with_empty_list() {
    $calculator = new StatsCalculator();

    $this->assertSame( 0.0, $calculator->average( [] ) );
}

// Edge case: single item
public function test_calculate_average_with_single_item() {
    $calculator = new StatsCalculator();

    $this->assertSame( 42.0, $calculator->average( [ 42 ] ) );
}
```

### Error Handling (Always)

How the system behaves when things go wrong.

```php
// Test error response
public function test_api_returns_error_for_invalid_input() {
    $response = $this->post_json( '/api/orders', [
        'items' => [],  // Invalid: empty order
    ] );

    $response->assertStatus( 400 );
    $response->assertJson( [
        'error' => 'validation_error',
        'message' => 'Order must have at least one item',
    ] );
}

// Test exception handling
public function test_payment_failure_logs_error_and_notifies() {
    $logger = $this->createMock( Logger::class );
    $logger->expects( $this->once() )
           ->method( 'error' )
           ->with( $this->stringContains( 'Payment failed' ) );

    $gateway = $this->createStub( PaymentGateway::class );
    $gateway->method( 'charge' )
            ->willThrowException( new PaymentDeclinedException() );

    $service = new OrderService( $gateway, $logger );

    $this->expectException( PaymentDeclinedException::class );
    $service->process_payment( $this->order );
}
```

### Security-Sensitive Code (Always)

Authentication, authorization, input validation.

```php
// Test authorization
public function test_non_admin_cannot_delete_users() {
    $this->actingAs( $this->regular_user );

    $response = $this->delete( '/api/users/123' );

    $response->assertStatus( 403 );
}

// Test input validation
public function test_sql_injection_is_prevented() {
    $input = "'; DROP TABLE users; --";

    $result = $this->search_service->search( $input );

    // Should not throw, should handle safely
    $this->assertIsArray( $result );
}
```

### Integration Points (Usually)

Where your code meets external systems.

```php
// Test API contract
public function test_payment_gateway_integration() {
    // Use test/sandbox API or mock
    $gateway = new StripeGateway( $this->test_api_key );

    $result = $gateway->charge( [
        'amount' => 1000,  // $10.00
        'currency' => 'usd',
        'source' => 'tok_visa',  // Test token
    ] );

    $this->assertTrue( $result->success );
    $this->assertNotEmpty( $result->transaction_id );
}
```

---

## What NOT to Test

### Framework Code

The framework is already tested. Trust it.

```php
// SKIP: Testing Laravel's validation works
public function test_required_validation() {
    $validator = Validator::make(
        [ 'name' => '' ],
        [ 'name' => 'required' ]
    );

    $this->assertTrue( $validator->fails() );  // Tests Laravel, not your code
}

// INSTEAD: Test your validation rules are correctly defined
public function test_order_request_validates_required_fields() {
    $response = $this->post( '/api/orders', [] );

    $response->assertJsonValidationErrors( [ 'items', 'customer_id' ] );
}
```

### Simple Getters/Setters

No logic = nothing to test.

```php
// SKIP: No logic to test
public function test_get_name() {
    $product = new Product();
    $product->setName( 'Test' );
    $this->assertSame( 'Test', $product->getName() );
}

// INSTEAD: Test getters/setters with logic
public function test_set_price_applies_minimum() {
    $product = new Product();
    $product->setPrice( -10 );  // Negative should become 0
    $this->assertSame( 0.0, $product->getPrice() );
}
```

### Generated Code

If code is generated, test the generator, not the output.

```php
// SKIP: Testing generated migrations
public function test_migration_creates_column() {
    // The migration tool already tests this
}

// INSTEAD: Test that your schema is correct after migration
public function test_products_table_has_required_columns() {
    $columns = Schema::getColumnListing( 'products' );

    $this->assertContains( 'sku', $columns );
    $this->assertContains( 'price', $columns );
}
```

### Third-Party Code

Don't test code you don't own.

```php
// SKIP: Testing Stripe's SDK
public function test_stripe_creates_customer() {
    $stripe = new StripeClient( $api_key );
    $customer = $stripe->customers->create( [ 'email' => 'test@example.com' ] );
    $this->assertNotNull( $customer->id );
}

// INSTEAD: Test your integration wrapper
public function test_customer_service_creates_stripe_customer() {
    $stripe = $this->createMock( StripeClient::class );
    $stripe->expects( $this->once() )
           ->method( 'customers' )
           ->willReturn( $this->mock_customers );

    $service = new CustomerService( $stripe );
    $result = $service->create( $this->user );

    $this->assertTrue( $result->success );
}
```

---

## Coverage Metrics

### Line Coverage vs Branch Coverage

**Line coverage:** Percentage of lines executed by tests.
**Branch coverage:** Percentage of decision branches (if/else) taken.

```php
function categorize( int $value ): string {
    if ( $value < 0 ) {
        return 'negative';
    } elseif ( $value === 0 ) {
        return 'zero';
    } else {
        return 'positive';
    }
}

// 100% line coverage requires testing all three branches
public function test_categorize() {
    $this->assertSame( 'negative', categorize( -5 ) );
    $this->assertSame( 'zero', categorize( 0 ) );
    $this->assertSame( 'positive', categorize( 5 ) );
}
```

**Branch coverage is more valuable than line coverage.**

### Interpreting Coverage Reports

| Coverage | Meaning |
|----------|---------|
| 0-30% | Critical gaps, need immediate attention |
| 30-60% | Basic coverage, focus on critical paths |
| 60-80% | Good coverage, continue adding tests |
| 80-90% | Strong coverage, focus on edge cases |
| 90%+ | Excellent, watch for diminishing returns |

**Warning:** High coverage doesn't mean high quality. A test that runs code without asserting anything counts for coverage but provides no value.

---

## Test Prioritization

### Risk-Based Prioritization

| Risk Level | Examples | Test Priority |
|------------|----------|---------------|
| Critical | Payments, auth, data integrity | Must have tests |
| High | Core features, user-facing flows | Should have tests |
| Medium | Admin features, reporting | Nice to have tests |
| Low | Utility functions, logging | Test if time permits |

### Value Matrix

```
                    High Impact
                         │
    ┌────────────────────┼────────────────────┐
    │                    │                    │
    │  Nice to Have      │   Must Test        │
    │  (Admin pages,     │   (Payments, auth, │
    │   settings)        │   core business)   │
    │                    │                    │
Low ├────────────────────┼────────────────────┤ High
Complexity              │                    Complexity
    │                    │                    │
    │  Skip or Manual    │   Should Test      │
    │  (Trivial getters, │   (Complex         │
    │   generated code)  │   algorithms)      │
    │                    │                    │
    └────────────────────┼────────────────────┘
                         │
                    Low Impact
```

### Regression Tests

When a bug is found and fixed:

1. Write a test that would have caught the bug
2. Verify the test fails without the fix
3. Apply the fix
4. Verify the test passes

```php
/**
 * Regression test for issue #1234
 * Bug: Division by zero when cart has zero items
 */
public function test_calculate_average_item_price_with_empty_cart() {
    $cart = new Cart();

    // Before fix: threw DivisionByZeroError
    // After fix: returns 0.00
    $this->assertSame( 0.00, $cart->get_average_item_price() );
}
```

---

## Best Practices Summary

| Do | Don't |
|----|-------|
| Test business logic thoroughly | Chase coverage percentage |
| Test edge cases and boundaries | Test trivial getters/setters |
| Test error handling | Test framework code |
| Test security-sensitive code | Test third-party libraries |
| Write regression tests for bugs | Test generated code |
| Prioritize by risk and value | Test everything equally |
