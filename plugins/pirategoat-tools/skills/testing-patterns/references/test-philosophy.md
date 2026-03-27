# Test Philosophy & Quality Principles

**Source:** Synthesized from jhumelsine.github.io testing series

## Quick Reference

| Principle | What It Means | Violation Symptom |
|---|---|---|
| Tests are specifications | Define what code should do, not verify what it does | Tests break during refactoring |
| Tests are experiments | Subject code to adversarial conditions | Only happy path tested |
| Tests are future-focused | Prevent bugs later, not find bugs now | No regression tests after fixes |
| Tests document assumptions | Capture invariants and business rules | "Why does this check exist?" |
| Independent | Each test runs in complete isolation | Tests pass/fail based on run order |
| Deterministic | Same inputs always produce same result | Random failures, CI inconsistency |
| Fast | Quick enough to run frequently | Developers skip running tests |
| Readable | Clear what is being tested and why | "What does this test verify?" |
| Single Concern | One behavior per test | Multiple behaviors fail together |

## The Fundamental Shift

**The old mindset (WRONG):**
- Write code -> test it to confirm it works
- Tests exercise implementation
- Tests verify what the code does

**The new mindset (CORRECT):**
- Specify behavior via tests -> implement to make tests pass
- Tests define requirements
- Tests specify what the code should do

**Why this matters:**
- Specification-first tests survive refactoring
- Verification-after tests break during refactoring
- Specifications focus on behavior (stable)
- Verification focuses on implementation (unstable)

> _The purpose of tests is not to confirm the implementation, but to specify behavior._

## Four Core Principles

### 1. Tests as Codified Specifications

Tests are living, executable documentation of system behavior. Unlike Word docs or comments:
- They cannot be vague or ambiguous
- They execute and confirm consistency
- They fail when spec and implementation diverge
- They're always up-to-date (or they fail)

| Traditional (Word/Jira) | Test Specifications |
|---|---|
| Interpreted by reader | Executed by machine |
| Can be ambiguous | Must be precise |
| No consistency checking | Fails if inconsistent |
| Easily outdated | Always current or red |
| Separate from code | Lives with code |

```javascript
// Traditional spec: "The system shall validate email format"
// Test spec (unambiguous):
describe('Email validation', () => {
    it('should accept valid email format', () => {
        expect(isValidEmail('user@example.com')).toBe(true);
    });
    it('should reject email without @ symbol', () => {
        expect(isValidEmail('userexample.com')).toBe(false);
    });
});
```

### 2. Tests as Experiments

**The mindset:** "I'm not testing to show my code works. I'm testing to try to break it."

- Subject code to extreme conditions
- Make test doubles throw exceptions
- Test edge cases that "should never happen"
- Use adversarial testing approaches

> _Tests don't break your code; they break your illusions about the quality of that code._ -- Maaret Pyhajarvi

```php
// Don't just test happy path -- test the "should never happens"
public function test_handles_null_order_gracefully() {
    $result = $this->processor->process(null);
    $this->assertTrue($result->is_error());
}

public function test_handles_database_timeout() {
    $this->db_mock->shouldThrow(TimeoutException::class);
    $result = $this->processor->process($this->create_valid_order());
    $this->assertTrue($result->is_error());
}
```

### 3. Tests Are Future-Focused

Tests don't find bugs now. They prevent bugs later. Code usually works when first written (fresh in mind) but breaks when modified months later (context lost). Tests capture the original intent and fail when modifications violate it.

```
Today:                              3 Months Later (Different Developer):
  Write test specifying behavior X    Modifies code to add feature Y
  Implement X to make test pass       Unknowingly violates behavior X
  Code works, test passes             Test for X fails immediately
                                      Developer adjusts to preserve X
                                      Both X and Y work
```

### 4. Tests Document Assumptions and Invariants

Developer knowledge lives in code (shows HOW, not WHY), comments (drift out of sync), and heads (developers leave or forget). Tests document the WHY and enforce it forever.

```java
// Code shows HOW but not WHY:
public boolean processPayment(Order order) {
    if (order.getTotal() <= 0) return false;
}

// Test documents the WHY (business rule):
@Test
public void test_rejects_zero_dollar_orders() {
    // Business rule: don't process $0 orders to avoid
    // payment processor fees and accounting complications
    assertFalse(processor.processPayment(createOrder(0.00)));
}
```

When another developer tries to remove the check 6 months later, tests fail with a clear message explaining the business rule.

## The Behavior vs Implementation Distinction

**Behavior = Observable outcomes from a black box perspective**

**Not behavior:** How many helper methods are called, whether a cache is checked, internal state transitions, which algorithm is used.

**Is behavior:** What value is returned, what exception is thrown, what side effects occur (DB writes, API calls), what messages are logged.

**Testing implementation (WRONG):**
```javascript
it('should call cache.get() before database.query()', () => {
    service.getUser(123);
    expect(cache.get).toHaveBeenCalledBefore(db.query); // Implementation detail!
});
```

**Testing behavior (CORRECT):**
```javascript
it('should return user when user exists', () => {
    const user = service.getUser(123);
    expect(user.id).toBe(123);
    expect(user.name).toBe('John Doe');
});
```

Can switch from cache to in-memory map, optimize cache strategy, or remove cache entirely -- behavioral tests still pass (unless performance requirement is violated).

## Common Mental Traps

| Trap | Reality | Recommendation |
|---|---|---|
| "I need code before I can test" | You need requirements, not implementation. Requirement: "System should X when Y" -> test: given Y, expect X -> implement. | Write tests from requirements, not from code. |
| "Tests after achieve the same result" | Test-first asks "What SHOULD this do?" (specification). Test-after asks "What DOES this do?" (verification). Test-first finds design issues early; test-after works around locked-in designs. | Test-first produces simpler tests and better interfaces. |
| "Testing slows me down" | Testing speeds you up: reduces debugging time (fail fast vs. hunt for bug), prevents rework (catch bugs before merging), enables confident refactoring (safety net), documents API usage (living examples). | Investment pays back in same-day bug detection vs. multi-day debug cycles. |

## Quality Pillars

### Independence

Each test must run in complete isolation. No test should depend on another test running first, and no test should affect subsequent tests.

```php
// WRONG: Test depends on previous test's state
class OrderTest extends WP_UnitTestCase {
    private static $order_id;
    public function test_create_order() {
        self::$order_id = $this->factory->order->create();
    }
    public function test_update_order() {
        // FAILS if test_create_order didn't run first!
        $order = wc_get_order( self::$order_id );
    }
}

// CORRECT: Each test creates its own state
public function test_update_order() {
    $order = $this->factory->order->create_and_get();
    $order->set_status( 'completed' );
    $this->assertTrue( $order->save() );
}
```

### Determinism

Given the same inputs, a test must always produce the same result. No randomness, no timing issues, no external dependencies.

```php
// WRONG: Test will fail after expiry date
public function test_coupon_is_valid() {
    $coupon = new Coupon( [ 'expires' => '2024-12-31' ] );
    $this->assertTrue( $coupon->is_valid() );
}

// CORRECT: Mock the current time
public function test_coupon_is_valid_before_expiry() {
    \Brain\Monkey\Functions\when( 'current_time' )->justReturn( '2024-06-15' );
    $coupon = new Coupon( [ 'expires' => '2024-12-31' ] );
    $this->assertTrue( $coupon->is_valid() );
}
```

**Flaky Test Diagnosis:**

| Symptom | Likely Cause | Fix |
|---|---|---|
| Fails intermittently | Time dependency | Mock time |
| Fails only in CI | Environment difference | Check paths, timezone |
| Fails when run with others | Shared state | Isolate with setup/teardown |
| Fails after timeout | Async race condition | Use proper waiting |

### Speed

Tests should run fast enough that developers run them frequently. Slow tests get skipped.

| Test Type | Target Time | Acceptable |
|---|---|---|
| Unit test | < 10ms | < 100ms |
| Integration test | < 500ms | < 2s |
| E2E test | < 5s | < 30s |

```php
// SLOW: Creates 100 real database records
public function test_product_search() {
    for ( $i = 0; $i < 100; $i++ ) {
        $this->factory->product->create();
    }
    $results = search_products( 'test' );
}

// FAST: Use minimum data needed
public function test_product_search() {
    $this->factory->product->create( [ 'name' => 'test-product' ] );
    $results = search_products( 'test' );
    $this->assertCount( 1, $results );
}
```

### Readability

Tests are documentation. A developer should understand what's being tested without reading the implementation.

```php
// BAD: Unclear what's being tested
public function test_order() { }
public function test_order_2() { }

// GOOD: Describes scenario and expectation
public function test_calculate_total_returns_zero_for_empty_cart() { }
public function test_calculate_total_includes_tax_when_enabled() { }
```

```php
// BAD: What do these numbers mean?
$this->assertSame( 550, $product->calculate_total() );

// GOOD: Named values with explanation
$base_price = 100;
$quantity = 5;  // Minimum for bulk discount
$expected_total = ( $base_price * $quantity ) * 1.1; // With 10% tax
$this->assertSame( $expected_total, $product->calculate_total() );
```

### Single Concern

Each test should verify one specific behavior. When a test fails, you should immediately know what broke.

```php
// BAD: Which assertion failed?
public function test_order_processing() {
    $order = $this->process_order( $this->cart );
    $this->assertNotNull( $order );
    $this->assertSame( 'pending', $order->status );
    $this->assertSame( 100.00, $order->total );
    $this->assertCount( 2, $order->items );
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
```

**Exception:** Multiple assertions on the same logical concept are fine (e.g., verifying all fields of a copied address).
