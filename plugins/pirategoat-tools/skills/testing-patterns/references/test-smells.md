# Test Smells: Diagnostic Guide

**Source:** Synthesized from "Testing Concerns" and "TDD, Where Did It All Go Wrong?" (jhumelsine.github.io)

Test smells indicate problems in BOTH tests AND implementation. This guide diagnoses root causes and applies correct fixes.

## Quick Reference

| Smell | Root Cause | Primary Fix | Secondary Fix |
|-------|------------|-------------|---------------|
| **Flaky** | Race conditions, time, randomness, external deps | Fix implementation | Mock non-determinism |
| **Brittle** | Testing implementation details | Test behavior through public API | Reduce mocking |
| **Slow** | Real I/O (DB, network, files) | Mock I/O boundaries | Use in-memory implementations |
| **Complex** | SRP violation (class too big) | Refactor implementation | Split into focused tests |
| **False Positive** | Missing assertions | Add meaningful assertions | Verify all outcomes |
| **Over-Mocked** | Tight coupling | Refactor for loose coupling | Mock only boundaries |

**If tests are hard to write, the code is hard to use. Tests are the first client of your API.**

## The Six Major Test Smells

### 1. Flaky Tests (Random Pass/Fail)

**Symptom:** Test sometimes passes, sometimes fails, with no code changes.

**CRITICAL:** Flaky tests usually reveal implementation problems, not test problems!

#### Root Causes & Fixes

**A. Race Conditions (Implementation Bug)**

```javascript
// FLAKY: Race condition in implementation
class DataProcessor {
    async process(data) {
        this.startBackgroundTask(data); // Async, no await!
        return this.results; // Results might not be ready!
    }
    startBackgroundTask(data) {
        setTimeout(() => { this.results = transform(data); }, 10);
    }
}

// FIXED: Implementation handles async properly
class DataProcessor {
    async process(data) {
        return await this.startBackgroundTask(data);
    }
    async startBackgroundTask(data) {
        return new Promise(resolve => {
            setTimeout(() => { resolve(transform(data)); }, 10);
        });
    }
}
```

**B. Time Dependencies**

```php
// FLAKY: Depends on current time
public function test_coupon_is_valid() {
    $coupon = new Coupon( [ 'expires_at' => strtotime( '2024-12-31' ) ] );
    $this->assertTrue( $coupon->is_valid() ); // Fails after 2024-12-31!
}

// FIXED: Mock time for deterministic results
public function test_coupon_is_valid_before_expiry() {
    $this->mock_time( '2024-06-15' );
    $coupon = new Coupon( [ 'expires_at' => strtotime( '2024-12-31' ) ] );
    $this->assertTrue( $coupon->is_valid() ); // Always passes
}

public function test_coupon_is_invalid_after_expiry() {
    $this->mock_time( '2025-01-15' );
    $coupon = new Coupon( [ 'expires_at' => strtotime( '2024-12-31' ) ] );
    $this->assertFalse( $coupon->is_valid() ); // Always passes
}
```

Other common root causes: non-deterministic randomness (fix: seed random), external service flakiness (fix: mock external boundaries).

#### Flaky Test Investigation Protocol

1. **Reproduce:** Run test 100+ times. Does it ever fail?
2. **Isolate:** Run alone. Still flaky?
3. **Pattern:** When does it fail? (Time of day? Load? Specific environment?)
4. **Root cause:** Async not awaited? Time dependency? Randomness? External service? Shared state?
5. **Fix implementation FIRST**
6. **Then fix test**

**Do NOT just add retries or longer timeouts!** That masks the real problem.

### 2. Brittle Tests (Break During Refactoring)

**Symptom:** Tests fail when refactoring, even though behavior didn't change.

**Root cause:** Testing implementation details instead of behavior.

```javascript
// BRITTLE: Tests implementation
it('should use cache before database', () => {
    const service = new UserService(cache, db);
    service.getUser(123);
    expect(cache.get).toHaveBeenCalledBefore(db.query); // Tests HOW
});

// ROBUST: Tests behavior
it('should return user data', async () => {
    const service = new UserService(cache, db);
    const user = await service.getUser(123);
    expect(user.id).toBe(123);       // Tests WHAT
    expect(user.name).toBe('John');
});
```

**Ian Cooper's key insights ("TDD, Where Did It All Go Wrong?"):**

- Don't test private methods or make methods public just to test them
- Don't use test doubles to verify implementation details
- Test the public API (the contract)
- Did behavior change? Update test. Did only structure change? Test shouldn't break.

### 3. Slow Tests (> Few Seconds Per Suite)

**Symptom:** Developers avoid running tests because they're too slow.

**Most common cause: Real database operations**

```javascript
// SLOW: Real DB (500ms per test)
it('should find users by email', async () => {
    await db.users.create({ email: 'test@example.com' }); // DB write!
    const user = await userRepo.findByEmail('test@example.com'); // DB read!
    expect(user).toBeDefined();
});

// FAST: Mock repository (5ms per test)
it('should find users by email', async () => {
    mockRepo.findByEmail.mockResolvedValue({ email: 'test@example.com' });
    const user = await userService.findByEmail('test@example.com');
    expect(user).toBeDefined();
});
```

**Note:** Use real DB for integration tests, mocks for unit tests. Same principle applies to file I/O (mock file contents) and sleep/delays (use fake timers).

### 4. Complex Tests (Many Setups/Assertions)

**Symptom:** Tests are hard to understand, require extensive setup, have many assertions.

**Root cause:** Implementation has too many responsibilities (SRP violation). Complex tests reveal complex implementation!

```php
// COMPLEX: 7 mocks, 20+ assertions = SRP violation
public function test_processes_order() {
    $customer = $this->create_customer_with_billing_and_shipping();
    $payment  = $this->mock_payment_gateway_with_responses();
    $inventory = $this->mock_inventory_with_stock_levels();
    $shipping = $this->mock_shipping_calculator();
    $tax      = $this->mock_tax_calculator();
    $loyalty  = $this->mock_loyalty_program();
    $email    = $this->mock_email_service();
    $order    = $this->create_order_with_items();

    $result = $this->processor->process( $order, $customer );
    // 20 assertions follow...
}

// FIXED: Decompose into focused services with focused tests
public function test_processes_payment() {
    $order = $this->create_order();
    $result = $this->payment_service->process( $order );
    $this->assertTrue( $result->is_success() );
}

public function test_reserves_inventory() {
    $order = $this->create_order();
    $result = $this->inventory_service->reserve( $order );
    $this->assertTrue( $result->is_reserved() );
}
```

**Guideline:** If test setup > 10 lines or > 3 mocks, redesign the implementation.

### 5. False Positive Tests (No Real Assertions)

**Symptom:** Tests pass but don't actually verify anything.

```php
// FALSE POSITIVE: Only checks it doesn't crash
public function test_processes_order() {
    $order = $this->create_order();
    $this->processor->process( $order );
    // No assertions!
}

// MEANINGFUL: Verifies behavior
public function test_processes_order_and_returns_confirmation() {
    $order = $this->create_order();
    $result = $this->processor->process( $order );
    $this->assertTrue( $result->is_success() );
    $this->assertNotEmpty( $result->confirmation_number );
    $this->assertEquals( 'processed', $result->status );
}
```

**Red flag patterns:**
- Tests with no assertions
- Tests that only call methods without checking results
- Tests with only `assertNotNull` or `assertTrue(true)`

### 6. Excessive Test Doubles (Over-Mocking)

**Symptom:** Tests require mocking most internal classes.

**Root cause:** Tight coupling in implementation. Don't mock what you own!

```javascript
// OVER-MOCKED: 5 internal mocks = tight coupling
const calculator = new ShippingCalculator(
    mockAddressValidator, mockDistanceCalculator,
    mockRateTable, mockZoneDetector, mockWeightCalculator
);
const cost = calculator.calculate(package, address);

// BETTER: Only mock external boundaries
mockRatesApi.getRates.mockResolvedValue({ zoneA: 10, zoneB: 15 });
const calculator = new ShippingCalculator(mockRatesApi);
const cost = calculator.calculate(package, address);
```

**Mock at boundaries, use real internal classes:**
- Mock: HTTP, database, file system, time, external APIs
- Don't mock: Your own domain classes, value objects, DTOs
- If you're mocking your own classes, redesign for loose coupling

## The Test Quality Diagnostic Process

When tests are problematic:

1. **Identify the smell:** Which category?
2. **Find root cause:** Test problem or implementation problem?
3. **Fix implementation first:** Most test smells reveal code smells
4. **Then fix test:** Proper assertions, mocking, structure
5. **Refactor:** Simplify both test and implementation

**Remember:** If tests are hard to write, the code is hard to use. Tests are the first client of your API.
