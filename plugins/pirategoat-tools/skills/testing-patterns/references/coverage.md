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

Instead of chasing coverage percentage, optimize for:
- **Risk coverage** — high-risk code is tested
- **Behavior coverage** — important behaviors are verified
- **Confidence** — team trusts the test suite

---

## What to Test

### Business Logic (Always)

Core calculations, rules, and algorithms that define your application's value.

```php
public function test_calculate_installment_payment() {
    $calculator = new PaymentCalculator();
    $installment = $calculator->calculate_installment(
        total: 1200.00, months: 12, interest_rate: 0.05
    );
    $this->assertSame( 105.00, $installment );
}
```

### Edge Cases (Always)

Boundary conditions where bugs hide.

```php
public function test_pagination_edge_cases(): void {
    $paginator = new Paginator( total_items: 100, per_page: 10 );

    // First page — can't go before 1
    $this->assertSame( 1, $paginator->get_previous_page() );
    $this->assertSame( 2, $paginator->get_next_page() );

    // Last page — can't go past last
    $paginator->set_current_page( 10 );
    $this->assertSame( 10, $paginator->get_next_page() );

    // Beyond last page — clamped
    $paginator->set_current_page( 15 );
    $this->assertSame( 10, $paginator->get_current_page() );
}
```

### Error Handling (Always)

How the system behaves when things go wrong.

```php
public function test_api_returns_error_for_invalid_input() {
    $response = $this->post_json( '/api/orders', [ 'items' => [] ] );

    $response->assertStatus( 400 );
    $response->assertJson( [
        'error'   => 'validation_error',
        'message' => 'Order must have at least one item',
    ] );
}
```

### Security-Sensitive Code (Always)

Authentication, authorization, input validation.

```php
public function test_non_admin_cannot_delete_users() {
    $this->actingAs( $this->regular_user );
    $response = $this->delete( '/api/users/123' );
    $response->assertStatus( 403 );
}
```

### Integration Points (Usually)

Where your code meets external systems.

```php
public function test_payment_gateway_integration() {
    $gateway = new StripeGateway( $this->test_api_key );
    $result = $gateway->charge( [
        'amount'   => 1000,
        'currency' => 'usd',
        'source'   => 'tok_visa',
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
// WRONG: Tests Laravel, not your code
public function test_required_validation() {
    $validator = Validator::make( [ 'name' => '' ], [ 'name' => 'required' ] );
    $this->assertTrue( $validator->fails() );
}

// CORRECT: Test your validation rules are correctly defined
public function test_order_request_validates_required_fields() {
    $response = $this->post( '/api/orders', [] );
    $response->assertJsonValidationErrors( [ 'items', 'customer_id' ] );
}
```

### Simple Getters/Setters

No logic = nothing to test.

```php
// WRONG: No logic to test
public function test_get_name() {
    $product = new Product();
    $product->setName( 'Test' );
    $this->assertSame( 'Test', $product->getName() );
}

// CORRECT: Test getters/setters WITH logic
public function test_set_price_applies_minimum() {
    $product = new Product();
    $product->setPrice( -10 );  // Negative should become 0
    $this->assertSame( 0.0, $product->getPrice() );
}
```

### Generated Code

Test the generator, not the output.

```php
// WRONG: Testing generated migrations
public function test_migration_creates_column() { /* The migration tool already tests this */ }

// CORRECT: Test that your schema is correct after migration
public function test_products_table_has_required_columns() {
    $columns = Schema::getColumnListing( 'products' );
    $this->assertContains( 'sku', $columns );
    $this->assertContains( 'price', $columns );
}
```

### Third-Party Code

Don't test code you don't own.

```php
// WRONG: Testing Stripe's SDK
public function test_stripe_creates_customer() {
    $stripe = new StripeClient( $api_key );
    $customer = $stripe->customers->create( [ 'email' => 'test@example.com' ] );
    $this->assertNotNull( $customer->id );
}

// CORRECT: Test your integration wrapper
public function test_customer_service_creates_stripe_customer() {
    $stripe = $this->createMock( StripeClient::class );
    $stripe->expects( $this->once() )->method( 'customers' )->willReturn( $this->mock_customers );
    $service = new CustomerService( $stripe );
    $result = $service->create( $this->user );
    $this->assertTrue( $result->success );
}
```

---

## Coverage Metrics

**Line coverage** measures percentage of lines executed. **Branch coverage** measures percentage of decision branches (if/else) taken. Branch coverage is more valuable — it catches untested paths that line coverage misses.

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

// 100% branch coverage requires testing all three paths
public function test_categorize() {
    $this->assertSame( 'negative', categorize( -5 ) );
    $this->assertSame( 'zero', categorize( 0 ) );
    $this->assertSame( 'positive', categorize( 5 ) );
}
```

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
                         |
    +--------------------+--------------------+
    |                    |                    |
    |  Nice to Have      |   Must Test        |
    |  (Admin pages,     |   (Payments, auth, |
    |   settings)        |   core business)   |
    |                    |                    |
Low +--------------------+--------------------+ High
Complexity              |                    Complexity
    |                    |                    |
    |  Skip or Manual    |   Should Test      |
    |  (Trivial getters, |   (Complex         |
    |   generated code)  |   algorithms)      |
    |                    |                    |
    +--------------------+--------------------+
                         |
                    Low Impact
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
