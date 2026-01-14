# Test Smells: Diagnostic Guide

**Source:** Synthesized from "Testing Concerns" and "TDD, Where Did It All Go Wrong?" (jhumelsine.github.io)

## Overview

Test smells indicate problems. Unlike code smells, test smells often reveal issues in BOTH tests AND implementation. This guide helps diagnose root causes and apply correct fixes.

**Critical insight:** _"Tests don't break your code; they break your illusions about the quality of that code."_

## The Six Major Test Smells

### 1. Flaky Tests (Random Pass/Fail)

**Symptom:** Test sometimes passes, sometimes fails, even with no code changes.

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
        setTimeout(() => {
            this.results = transform(data);
        }, 10);
    }
}

// Test fails randomly
it('should process data', async () => {
    const processor = new DataProcessor();
    const result = await processor.process(testData);
    expect(result).toBeDefined(); // Sometimes undefined!
});
```

**Fix: Fix the implementation first!**
```javascript
// FIXED: Implementation handles async properly
class DataProcessor {
    async process(data) {
        return await this.startBackgroundTask(data); // Now properly awaits
    }

    async startBackgroundTask(data) {
        return new Promise(resolve => {
            setTimeout(() => {
                resolve(transform(data));
            }, 10);
        });
    }
}

// Test now deterministic
it('should process data', async () => {
    const processor = new DataProcessor();
    const result = await processor.process(testData);
    expect(result).toBeDefined(); // Always works
});
```

**B. Time Dependencies**

```php
// FLAKY: Depends on current time
class Coupon {
    public function is_valid() {
        return time() < $this->expires_at;
    }
}

// Test fails after expiry date
public function test_coupon_is_valid() {
    $coupon = new Coupon( [ 'expires_at' => strtotime( '2024-12-31' ) ] );
    $this->assertTrue( $coupon->is_valid() ); // Flaky!
}
```

**Fix: Mock time**
```php
// FIXED: Deterministic time
public function test_coupon_is_valid_before_expiry() {
    // Mock current time
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

**C. Non-Deterministic Randomness**

```javascript
// FLAKY: True randomness
function generateCode() {
    return Math.random().toString(36).substring(7);
}

it('should generate unique codes', () => {
    const code1 = generateCode();
    const code2 = generateCode();
    expect(code1).not.toBe(code2); // Might collide!
});
```

**Fix: Seed random or use predictable generation**
```javascript
// FIXED: Deterministic randomness
class CodeGenerator {
    constructor(seed = Date.now()) {
        this.seed = seed;
    }

    generate() {
        // Seeded pseudo-random
        this.seed = (this.seed * 9301 + 49297) % 233280;
        return (this.seed / 233280).toString(36).substring(2, 9);
    }
}

it('should generate unique codes', () => {
    const generator = new CodeGenerator(12345); // Fixed seed
    const code1 = generator.generate();

    const generator2 = new CodeGenerator(54321); // Different seed
    const code2 = generator2.generate();

    expect(code1).not.toBe(code2); // Always different with different seeds
});
```

**D. External Service Flakiness**

```php
// FLAKY: Real API can fail
public function test_fetches_exchange_rate() {
    $rate = $this->api->get_exchange_rate( 'USD', 'EUR' );
    $this->assertGreaterThan( 0, $rate ); // Network failures!
}
```

**Fix: Mock external boundaries**
```php
// FIXED: Controlled test double
public function test_fetches_exchange_rate() {
    $mock_api = $this->createMock( ExchangeRateApi::class );
    $mock_api->method( 'get_exchange_rate' )
             ->willReturn( 0.85 );

    $service = new CurrencyService( $mock_api );
    $rate = $service->get_exchange_rate( 'USD', 'EUR' );

    $this->assertEquals( 0.85, $rate ); // Always deterministic
}
```

#### Flaky Test Investigation Protocol

1. **Reproduce:** Run test 100+ times. Does it ever fail?
2. **Isolate:** Run alone. Still flaky?
3. **Pattern:** When does it fail? (Time of day? Load? Specific environment?)
4. **Root cause:**
   - Async/promises not awaited?
   - Time dependencies?
   - Randomness?
   - External services?
   - Shared state?
5. **Fix implementation FIRST**
6. **Then fix test**

**Do NOT just add retries or longer timeouts!** That masks the real problem.

### 2. Brittle Tests (Break During Refactoring)

**Symptom:** Tests fail when refactoring, even though behavior didn't change.

**Root cause:** Testing implementation details instead of behavior.

#### Example: Testing Internal Implementation

```javascript
// BRITTLE: Tests implementation
it('should use cache before database', () => {
    const cache = createMock();
    const db = createMock();
    const service = new UserService(cache, db);

    service.getUser(123);

    // Tests HOW it works (implementation)
    expect(cache.get).toHaveBeenCalledBefore(db.query);
});

// Refactor: Remove cache optimization
// Result: Test breaks even though public API unchanged!
```

**Fix: Test behavior only**
```javascript
// ROBUST: Tests behavior
it('should return user data', async () => {
    const service = new UserService(cache, db);

    const user = await service.getUser(123);

    // Tests WHAT it does (behavior)
    expect(user.id).toBe(123);
    expect(user.name).toBe('John');
});

// Refactor: Remove cache
// Result: Test still passes (behavior preserved)
```

#### Ian Cooper's "TDD, Where Did It All Go Wrong?"

Key insights from Cooper's talk:

**Testing internals makes tests brittle:**
- Don't test private methods
- Don't make methods public just to test them
- Don't use test doubles to verify implementation details
- Test the public API (the contract)

**When refactoring affects tests, ask:**
- Did behavior change? → Update test (it's a spec)
- Did only structure change? → Test shouldn't break (it's brittle)

### 3. Slow Tests (> Few Seconds Per Suite)

**Symptom:** Developers avoid running tests because they're too slow.

**Root causes:**

#### A. Real I/O Operations

```php
// SLOW: Real file system I/O
public function test_processes_csv_file() {
    $result = process_file( '/path/to/large-file.csv' ); // 5MB file!
    $this->assertCount( 10000, $result );
}

// Time: 3 seconds
```

**Fix: Mock I/O boundaries**
```php
// FAST: Mock file contents
public function test_processes_csv_content() {
    $mock_content = $this->generate_sample_csv( 100 ); // Small sample
    $result = process_csv_content( $mock_content );
    $this->assertCount( 100, $result );
}

// Time: 10 milliseconds
```

#### B. Real Database Operations

```javascript
// SLOW: Real DB
it('should find users by email', async () => {
    await db.users.create({ email: 'test@example.com' }); // DB write!

    const user = await userRepo.findByEmail('test@example.com'); // DB read!

    expect(user).toBeDefined();
});

// Time: 500ms per test
```

**Fix: Use in-memory implementation or mocks for unit tests**
```javascript
// FAST: Mock repository
it('should find users by email', async () => {
    mockRepo.findByEmail.mockResolvedValue({ email: 'test@example.com' });

    const user = await userService.findByEmail('test@example.com');

    expect(user).toBeDefined();
});

// Time: 5ms per test
```

**Note:** Use real DB for integration tests, mocks for unit tests.

#### C. Sleep/Delays

```javascript
// SLOW: Real time delays
it('should process after delay', (done) => {
    service.scheduleTask();

    setTimeout(() => {
        expect(service.isComplete()).toBe(true);
        done();
    }, 5000); // 5 second wait!
});
```

**Fix: Mock timers**
```javascript
// FAST: Instant time travel
it('should process after delay', () => {
    jest.useFakeTimers();

    service.scheduleTask();

    jest.advanceTimersByTime(5000); // Instant!
    expect(service.isComplete()).toBe(true);

    jest.useRealTimers();
});

// Time: 1ms
```

### 4. Complex Tests (Many Setups/Assertions)

**Symptom:** Tests are hard to understand, require extensive setup, have many assertions.

**Root cause:** Implementation has too many responsibilities (SRP violation).

**Critical insight:** Complex tests reveal complex implementation!

#### Example

```php
// COMPLEX TEST = COMPLEX CODE
public function test_processes_order() {
    // Setup: 30 lines
    $customer = $this->create_customer_with_billing_and_shipping();
    $payment_gateway = $this->mock_payment_gateway_with_responses();
    $inventory = $this->mock_inventory_with_stock_levels();
    $shipping = $this->mock_shipping_calculator();
    $tax = $this->mock_tax_calculator();
    $loyalty = $this->mock_loyalty_program();
    $email = $this->mock_email_service();

    // More setup...
    $order = $this->create_order_with_items();

    // Act
    $result = $this->processor->process( $order, $customer );

    // Assert: 20 assertions
    $this->assertNotNull( $result );
    $this->assertTrue( $result->payment_processed );
    $this->assertTrue( $result->inventory_reserved );
    $this->assertNotNull( $result->shipping_label );
    $this->assertEquals( $expected_tax, $result->tax );
    $this->assertEquals( $expected_loyalty, $result->loyalty_points );
    $this->assertTrue( $result->email_sent );
    // ... 13 more assertions
}
```

**Problem:** OrderProcessor is doing too much!

**Fix: Refactor implementation (Single Responsibility Principle)**
```php
// SIMPLER: Each class has one job
class OrderProcessor {
    public function process( Order $order ) {
        // Just coordinates, doesn't do everything
        $payment = $this->payment_service->process( $order );
        $inventory = $this->inventory_service->reserve( $order );
        return new OrderResult( $payment, $inventory );
    }
}

// SIMPLER TESTS: Test each service independently
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

// Integration test: Verify coordination
public function test_coordinates_order_processing() {
    $order = $this->create_order();
    $result = $this->processor->process( $order );

    $this->assertTrue( $result->payment->is_success() );
    $this->assertTrue( $result->inventory->is_reserved() );
}
```

**Guideline:** If test setup > 10 lines or > 3 mocks → redesign the implementation.

### 5. False Positive Tests (No Real Assertions)

**Symptom:** Tests pass but don't actually verify anything.

```php
// FALSE POSITIVE: Only checks it doesn't crash
public function test_processes_order() {
    $order = $this->create_order();
    $this->processor->process( $order );
    // No assertions! Test passes if no exception thrown
}
```

**Fix: Assert meaningful outcomes**
```php
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

**Root cause:** Tight coupling in implementation.

**Critical insight:** Don't mock what you own!

#### Example: Over-Mocking

```javascript
// OVER-MOCKED: Mocking internal domain classes
it('should calculate shipping cost', () => {
    const mockAddressValidator = createMock();
    const mockDistanceCalculator = createMock();
    const mockRateTable = createMock();
    const mockZoneDetector = createMock();
    const mockWeightCalculator = createMock();

    mockAddressValidator.validate.mockReturnValue(true);
    mockDistanceCalculator.calculate.mockReturnValue(100);
    mockRateTable.getRate.mockReturnValue(10);
    mockZoneDetector.getZone.mockReturnValue('A');
    mockWeightCalculator.calculate.mockReturnValue(5);

    const calculator = new ShippingCalculator(
        mockAddressValidator,
        mockDistanceCalculator,
        mockRateTable,
        mockZoneDetector,
        mockWeightCalculator
    );

    const cost = calculator.calculate(package, address);
    expect(cost).toBe(50);
});
```

**Problems:**
1. Testing implementation structure, not behavior
2. Brittle - breaks when refactoring internal classes
3. Missing integration bugs (classes might not work together)

**Fix: Mock at boundaries, use real internal classes**
```javascript
// BETTER: Only mock external boundaries
it('should calculate shipping cost', () => {
    // Only mock external services
    mockRatesApi.getRates.mockResolvedValue({ zoneA: 10, zoneB: 15 });

    const calculator = new ShippingCalculator(mockRatesApi);

    const cost = calculator.calculate(package, address);
    expect(cost).toBe(50);
});
```

**Mocking guidelines:**
- ✅ Mock: HTTP, database, file system, time, external APIs
- ❌ Don't mock: Your own domain classes
- ❌ Don't mock: Value objects, DTOs
- ⚠️ If you're mocking your own classes → redesign for loose coupling

## Test Smell Summary Table

| Smell | Root Cause | Primary Fix | Secondary Fix |
|-------|------------|-------------|---------------|
| **Flaky** | Race conditions, time, randomness, external deps | Fix implementation | Mock non-determinism |
| **Brittle** | Testing implementation details | Test behavior through public API | Reduce mocking |
| **Slow** | Real I/O (DB, network, files) | Mock I/O boundaries | Use in-memory implementations |
| **Complex** | SRP violation (class too big) | Refactor implementation | Split into focused tests |
| **False Positive** | Missing assertions | Add meaningful assertions | Verify all outcomes |
| **Over-Mocked** | Tight coupling | Refactor for loose coupling | Mock only boundaries |

## The Test Quality Diagnostic Process

When tests are problematic:

1. **Identify the smell:** Which category?
2. **Find root cause:** Test problem or implementation problem?
3. **Fix implementation first:** Most test smells reveal code smells
4. **Then fix test:** Proper assertions, mocking, structure
5. **Refactor:** Simplify both test and implementation

**Remember:** If tests are hard to write, the code is hard to use. Tests are the first client of your API.

## Quotes

> _When test driven development goes wrong, it's not actually test driven development that's going wrong. It's that developers are testing the wrong things._ — Ian Cooper

> _If you're good at the debugger, it means you spent a lot of time debugging. I don't want you to be good at the debugger._ — Robert C. Martin

> _Tests don't break your code; they break your illusions about the quality of that code._ — Maaret Pyhäjärvi

## Further Reading

- "Testing Concerns" - Addressing common objections
- "TDD, Where Did It All Go Wrong?" - Ian Cooper's DevTernity talk
- "Test Doubles" - When and how to mock
- "Humble Object" - Pattern for testing difficult code
