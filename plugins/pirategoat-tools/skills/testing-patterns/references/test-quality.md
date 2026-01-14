# Test Quality Principles

The five pillars of high-quality tests: Independence, Determinism, Speed, Readability, and Single Concern.

## Quick Reference

| Pillar | Violation Symptom | Fix |
|--------|-------------------|-----|
| Independence | Tests pass/fail based on run order | Reset state in setUp/beforeEach |
| Determinism | Random failures, CI inconsistency | Mock time, seed random, mock I/O |
| Speed | Slow suite, developers skip tests | Mock I/O, parallelize, split suites |
| Readability | "What does this test?" questions | Better names, AAA structure |
| Single Concern | Multiple behaviors per test | Split into focused tests |

---

## 1. Independence

**Principle:** Each test must run in complete isolation. No test should depend on another test running first, and no test should affect subsequent tests.

### Common Violations

```php
// WRONG: Test depends on previous test's state
class OrderTest extends WP_UnitTestCase {
    private static $order_id;

    public function test_create_order() {
        self::$order_id = $this->factory->order->create();
        $this->assertNotEmpty( self::$order_id );
    }

    public function test_update_order() {
        // FAILS if test_create_order didn't run first!
        $order = wc_get_order( self::$order_id );
        $order->set_status( 'completed' );
        $this->assertTrue( $order->save() );
    }
}
```

```php
// CORRECT: Each test creates its own state
class OrderTest extends WP_UnitTestCase {
    public function test_create_order() {
        $order_id = $this->factory->order->create();
        $this->assertNotEmpty( $order_id );
    }

    public function test_update_order() {
        // Creates its own order - no dependency
        $order = $this->factory->order->create_and_get();
        $order->set_status( 'completed' );
        $this->assertTrue( $order->save() );
    }
}
```

### Database Cleanup Strategies

**WordPress:** Uses database transactions - rolls back after each test automatically.

```php
// WP_UnitTestCase handles this automatically
class MyTest extends WP_UnitTestCase {
    // Each test runs in a transaction that gets rolled back
}
```

**Jest/Vitest:** Reset mocks and clear state explicitly.

```javascript
describe('UserService', () => {
    let userService;

    beforeEach(() => {
        jest.clearAllMocks();
        userService = new UserService();
    });

    afterEach(() => {
        // Clean up any side effects
        userService.cleanup();
    });
});
```

### File System Isolation

```php
// WRONG: Tests share real file system
public function test_save_file() {
    file_put_contents( '/tmp/test.txt', 'data' );
    // Other tests might find this file!
}

// CORRECT: Use unique temp directories
public function test_save_file() {
    $temp_dir = sys_get_temp_dir() . '/' . uniqid( 'test_', true );
    mkdir( $temp_dir );
    try {
        file_put_contents( $temp_dir . '/test.txt', 'data' );
        // assertions...
    } finally {
        $this->cleanup_directory( $temp_dir );
    }
}
```

---

## 2. Determinism

**Principle:** Given the same inputs, a test must always produce the same result. No randomness, no timing issues, no external dependencies.

### Time Dependencies

```php
// WRONG: Test will fail after expiry date
public function test_coupon_is_valid() {
    $coupon = new Coupon( [ 'expires' => '2024-12-31' ] );
    $this->assertTrue( $coupon->is_valid() );
}

// CORRECT: Mock the current time
public function test_coupon_is_valid_before_expiry() {
    // Using Brain Monkey or similar
    \Brain\Monkey\Functions\when( 'current_time' )->justReturn( '2024-06-15' );

    $coupon = new Coupon( [ 'expires' => '2024-12-31' ] );
    $this->assertTrue( $coupon->is_valid() );
}

public function test_coupon_is_invalid_after_expiry() {
    \Brain\Monkey\Functions\when( 'current_time' )->justReturn( '2025-01-15' );

    $coupon = new Coupon( [ 'expires' => '2024-12-31' ] );
    $this->assertFalse( $coupon->is_valid() );
}
```

```javascript
// WRONG: Time-dependent test
it('should check if token is expired', () => {
    const token = { expiresAt: Date.now() + 1000 };
    expect(isExpired(token)).toBe(false);
});

// CORRECT: Mock time
it('should check if token is expired', () => {
    jest.useFakeTimers();
    jest.setSystemTime(new Date('2024-01-15'));

    const token = { expiresAt: new Date('2024-01-14').getTime() };
    expect(isExpired(token)).toBe(true);

    jest.useRealTimers();
});
```

### Random Values

```php
// WRONG: Random failures possible
public function test_random_selection() {
    $items = [ 'a', 'b', 'c' ];
    $selected = random_select( $items );
    $this->assertContains( $selected, $items );
}

// CORRECT: Seed the random generator or mock
public function test_random_selection_with_seed() {
    mt_srand( 12345 ); // Predictable "random"
    $items = [ 'a', 'b', 'c' ];
    $selected = random_select( $items );
    $this->assertSame( 'b', $selected ); // Always 'b' with this seed
}
```

### External Service Dependencies

```javascript
// WRONG: Real API call can fail for unrelated reasons
it('should fetch user data', async () => {
    const user = await api.getUser(123);
    expect(user.name).toBe('John');
});

// CORRECT: Mock the external service
it('should fetch user data', async () => {
    mockApi.getUser.mockResolvedValue({ id: 123, name: 'John' });

    const user = await api.getUser(123);

    expect(user.name).toBe('John');
});
```

### Flaky Test Diagnosis

| Symptom | Likely Cause | Fix |
|---------|--------------|-----|
| Fails intermittently | Time dependency | Mock time |
| Fails only in CI | Environment difference | Check paths, timezone |
| Fails when run with others | Shared state | Isolate with setup/teardown |
| Fails after timeout | Async race condition | Use proper waiting |

---

## 3. Speed

**Principle:** Tests should run fast enough that developers run them frequently. Slow tests get skipped.

### Performance Guidelines

| Test Type | Target Time | Acceptable |
|-----------|-------------|------------|
| Unit test | < 10ms | < 100ms |
| Integration test | < 500ms | < 2s |
| E2E test | < 5s | < 30s |

### I/O is the Enemy

```php
// SLOW: Real file operations
public function test_process_large_file() {
    $result = process_file( '/path/to/large-file.csv' );
    $this->assertCount( 10000, $result );
}

// FAST: Mock file contents
public function test_process_large_file() {
    $mock_content = $this->generate_csv_content( 100 ); // Small sample
    $result = process_csv_content( $mock_content );
    $this->assertCount( 100, $result );
}
```

### Database Operations

```php
// SLOW: Creates real database records
public function test_product_search() {
    for ( $i = 0; $i < 100; $i++ ) {
        $this->factory->product->create();
    }
    $results = search_products( 'test' );
    $this->assertNotEmpty( $results );
}

// FASTER: Use minimum data needed
public function test_product_search() {
    $this->factory->product->create( [ 'name' => 'test-product' ] );
    $results = search_products( 'test' );
    $this->assertCount( 1, $results );
}
```

### Network Calls

Always mock HTTP in unit tests. Use test servers or contract testing for integration tests.

```javascript
// Unit test: Mock network
beforeEach(() => {
    global.fetch = jest.fn();
});

it('should handle API errors gracefully', async () => {
    global.fetch.mockRejectedValue(new Error('Network error'));

    const result = await fetchData();

    expect(result).toBeNull();
});
```

---

## 4. Readability

**Principle:** Tests are documentation. A developer should understand what's being tested without reading the implementation.

### Test Naming

```php
// BAD: Unclear what's being tested
public function test_order() { }
public function test_order_2() { }
public function test_edge_case() { }

// GOOD: Describes scenario and expectation
public function test_calculate_total_returns_zero_for_empty_cart() { }
public function test_calculate_total_includes_tax_when_enabled() { }
public function test_calculate_total_excludes_tax_when_disabled() { }
```

```javascript
// BAD: Unclear
it('works', () => { });
it('handles the thing', () => { });

// GOOD: scenario + expectation
it('should return empty array when cart has no items', () => { });
it('should calculate tax based on shipping address', () => { });
```

### Magic Values

```php
// BAD: What do these numbers mean?
public function test_pricing() {
    $product = new Product( [ 'price' => 100, 'quantity' => 5 ] );
    $this->assertSame( 550, $product->calculate_total() );
}

// GOOD: Named values with explanation
public function test_pricing_applies_bulk_discount_for_five_or_more() {
    $base_price = 100;
    $quantity = 5;  // Minimum for bulk discount
    $expected_discount = 10; // 10% bulk discount

    $product = new Product( [
        'price' => $base_price,
        'quantity' => $quantity,
    ] );

    // 100 * 5 = 500, minus 10% = 450... wait, that's not 550!
    // The test itself reveals a bug or misunderstanding
    $expected_total = ( $base_price * $quantity ) * 1.1; // With 10% tax
    $this->assertSame( $expected_total, $product->calculate_total() );
}
```

---

## 5. Single Concern

**Principle:** Each test should verify one specific behavior. When a test fails, you should immediately know what broke.

### Multiple Assertions Problem

```php
// BAD: Which assertion failed?
public function test_order_processing() {
    $order = $this->process_order( $this->cart );

    $this->assertNotNull( $order );
    $this->assertSame( 'pending', $order->status );
    $this->assertSame( 100.00, $order->total );
    $this->assertCount( 2, $order->items );
    $this->assertSame( 'john@example.com', $order->email );
    $this->assertTrue( $order->has_shipping );
}

// GOOD: Focused tests
public function test_process_order_creates_order_with_pending_status() {
    $order = $this->process_order( $this->cart );
    $this->assertSame( 'pending', $order->status );
}

public function test_process_order_calculates_correct_total() {
    $order = $this->process_order( $this->cart );
    $this->assertSame( 100.00, $order->total );
}

public function test_process_order_includes_all_cart_items() {
    $cart = $this->create_cart_with_items( 2 );
    $order = $this->process_order( $cart );
    $this->assertCount( 2, $order->items );
}
```

### When Multiple Assertions Are Acceptable

**Multiple assertions on the same logical concept are fine:**

```php
// ACCEPTABLE: Verifying one object's complete state
public function test_order_address_is_copied_from_customer() {
    $customer = $this->create_customer_with_address();
    $order = $this->process_order( $this->cart, $customer );

    // All assertions verify the same concept: address was copied
    $this->assertSame( $customer->address->street, $order->address->street );
    $this->assertSame( $customer->address->city, $order->address->city );
    $this->assertSame( $customer->address->zip, $order->address->zip );
}
```

---

## Anti-Pattern Summary

| Anti-Pattern | Problem | Fix |
|--------------|---------|-----|
| Shared static state | Test coupling | Use instance variables, reset in setUp |
| Real time | Flaky tests | Mock time consistently |
| Real network | Slow, unreliable | Mock HTTP clients |
| Database without cleanup | State leaks | Use transactions, tearDown |
| Vague test names | Unclear failures | Name with scenario + expectation |
| Testing multiple behaviors | Unclear failures | Split into focused tests |
| Production database | Dangerous, slow | Use test database |
| Hardcoded file paths | Environment specific | Use temp directories |
