# Test Philosophy: The Mental Model

**Source:** Synthesized from jhumelsine.github.io testing series

## The Fundamental Shift

### Tests Are Specifications, Not Verification

**The old mindset (WRONG):**
- Write code → test it to confirm it works
- Tests exercise implementation
- Tests verify what the code does

**The new mindset (CORRECT):**
- Specify behavior via tests → implement to make tests pass
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

**What it means:**
Tests are living, executable documentation of system behavior. Unlike Word docs or comments:
- They cannot be vague or ambiguous
- They execute and confirm consistency
- They fail when spec and implementation diverge
- They're always up-to-date (or they fail)

**Traditional specs vs Test specs:**

| Traditional (Word/Jira) | Test Specifications |
|--------------------------|---------------------|
| Interpreted by reader | Executed by machine |
| Can be ambiguous | Must be precise |
| No consistency checking | Fails if inconsistent |
| Easily outdated | Always current or red |
| Separate from code | Lives with code |

**Example:**
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

    it('should reject email without domain', () => {
        expect(isValidEmail('user@')).toBe(false);
    });
});
```

**Key insight:** Each test is a specific, executable requirement that cannot be misinterpreted.

### 2. Tests as Experiments

**The mindset:** "I'm not testing to show my code works. I'm testing to try to break it."

**Hard in training, easy in battle:**
- Subject code to extreme conditions in tests
- Make test doubles throw exceptions
- Test edge cases that "should never happen"
- Use adversarial testing approaches

**Why experiment rather than confirm:**
- Confirmation bias makes us blind to flaws
- We write tests that we expect to pass
- Adversarial testing reveals hidden assumptions
- Stress testing builds genuine confidence

> _Tests don't break your code; they break your illusions about the quality of that code._ — Maaret Pyhäjärvi

**Example adversarial tests:**
```php
// Don't just test happy path
public function test_processes_valid_order() {
    $order = $this->create_valid_order();
    $result = $this->processor->process($order);
    $this->assertTrue($result->is_success());
}

// Test the "should never happens"
public function test_handles_null_order_gracefully() {
    $result = $this->processor->process(null);
    $this->assertTrue($result->is_error());
}

public function test_handles_database_timeout() {
    $this->db_mock->shouldThrow(TimeoutException::class);
    $result = $this->processor->process($this->create_valid_order());
    $this->assertTrue($result->is_error());
    $this->assertContains('timeout', $result->get_message());
}

public function test_handles_concurrent_modification() {
    // Another process modified the order
    $this->db_mock->shouldReturn(['version' => 2]);
    $result = $this->processor->process($this->order_v1);
    $this->assertTrue($result->is_conflict());
}
```

### 3. Tests Are Future-Focused

**The paradox:** Tests don't find bugs now. They prevent bugs later.

**When code works vs when it breaks:**
- Code usually works when first written (fresh in mind)
- Code breaks when modified months later (context lost)
- Tests capture the original intent
- Tests fail when modifications violate intent

**The future bug prevention mechanism:**

```
Today:
  ✓ Write test specifying behavior X
  ✓ Implement X to make test pass
  ✓ Code works, test passes

3 Months Later (Different Developer):
  → Modifies code to add feature Y
  → Unknowingly violates behavior X
  ✗ Test for X fails immediately
  → Developer sees: "Oh, X must still work"
  → Adjusts approach to preserve X
  ✓ Both X and Y work
```

**Without tests:**
```
3 Months Later:
  → Modifies code to add feature Y
  → Unknowingly violates behavior X
  → Commits code
  → Bug reaches production
  → User reports issue
  → Hours debugging to understand X
  → Hours fixing both X and Y
```

**Story from the field:**
> A developer was maintaining code I wrote. He noticed an asymmetry: one field persisted on create but not on update. He "fixed" it by adding the field to updates. A test failed: "creation timestamp cannot be changed after entity is created." He undid his change and thanked me for the test.

**Key insight:** The test prevented a future bug that would have been subtle and hard to debug.

### 4. Tests Document Assumptions and Invariants

**The problem:** Developer knowledge lives in three places:
1. **In code** - but code shows HOW, not WHY
2. **In comments** - but comments drift out of sync
3. **In heads** - but developers leave or forget

**The solution:** Tests document the WHY and enforce it forever.

**Example: Documenting invariants**
```java
// Code shows HOW but not WHY:
public boolean processPayment(Order order) {
    if (order.getTotal() <= 0) {
        return false;
    }
    // ... processing logic
}

// Test documents the WHY (business rule):
@Test
public void test_rejects_zero_dollar_orders() {
    // Business rule: We don't process $0 orders to avoid
    // payment processor fees and accounting complications
    Order zeroOrder = createOrder(0.00);

    assertFalse(processor.processPayment(zeroOrder));
}

@Test
public void test_rejects_negative_orders() {
    // Safety invariant: Negative totals indicate a bug
    // earlier in the order calculation pipeline
    Order negativeOrder = createOrder(-10.00);

    assertFalse(processor.processPayment(negativeOrder));
}
```

**When another developer tries to "fix" this:**
```java
// 6 months later: "Why are we rejecting $0 orders? Let me fix that..."
public boolean processPayment(Order order) {
    // Removed check - now processes $0 orders
    // ... processing logic
}
```

**Result:** Tests fail with clear message explaining the business rule. Developer understands why the check exists.

## The Behavior vs Implementation Distinction

### What is Behavior?

**Behavior = Observable outcomes from a black box perspective**

**Not behavior:**
- How many helper methods are called
- Whether a cache is checked
- Internal state transitions
- Which algorithm is used

**Is behavior:**
- What value is returned
- What exception is thrown
- What side effects occur (DB writes, API calls)
- What messages are logged

### Example: Behavior vs Implementation

**Testing implementation (WRONG):**
```javascript
it('should call cache.get() before database.query()', () => {
    const cache = createMock();
    const db = createMock();
    const service = new UserService(cache, db);

    service.getUser(123);

    expect(cache.get).toHaveBeenCalledBefore(db.query); // Implementation detail!
});
```

**Testing behavior (CORRECT):**
```javascript
it('should return user when user exists', () => {
    const service = new UserService(cache, db);

    const user = service.getUser(123);

    expect(user.id).toBe(123);
    expect(user.name).toBe('John Doe');
});

it('should be fast for repeated requests', () => {
    const service = new UserService(cache, db);

    const start1 = Date.now();
    service.getUser(123); // First call
    const duration1 = Date.now() - start1;

    const start2 = Date.now();
    service.getUser(123); // Second call
    const duration2 = Date.now() - start2;

    expect(duration2).toBeLessThan(duration1 / 10); // Second call should be >10x faster
});
```

**Why the second version is better:**
- Can switch from cache to in-memory map → tests still pass
- Can optimize cache strategy → tests still pass
- Can remove cache entirely → tests fail (performance requirement violated)
- Tests document WHAT the system does, not HOW

## Common Mental Traps

### Trap 1: "I need code before I can test"

**The trap:** Thinking you need implementation to know what to test.

**The reality:** You need requirements to write tests. Implementation comes after.

**Example:**
```
❌ Wrong order:
  1. Write code that does X
  2. Figure out how to test it
  3. Write test that exercises the code

✅ Correct order:
  1. Understand requirement: "System should X when Y"
  2. Write test: given Y, expect X
  3. Write minimal code to make test pass
```

### Trap 2: "Tests after achieve the same result"

**The trap:** Thinking test-after is equivalent to test-first.

**The difference:**

| Test-First | Test-After |
|------------|------------|
| "What SHOULD this do?" | "What DOES this do?" |
| Specification mindset | Verification mindset |
| Often finds design issues early | Design is already locked in |
| Tests guide implementation | Tests describe implementation |
| Usually simple tests | Often complex tests (matching complex code) |

**Why test-first is different:**
- Test-first: You write the test you WISH you could write (ideal interface)
- Test-after: You write the test you CAN write (working around existing interface)

### Trap 3: "Testing slows me down"

**The trap:** Thinking test-writing time is wasted.

**The reality:** Testing speeds you up by:
- Reducing debugging time (fail fast vs. hunt for bug)
- Preventing rework (catch bugs before merging)
- Enabling confident refactoring (safety net)
- Documenting API usage (living examples)

**Time breakdown comparison:**

Without tests:
```
Write code: 2 hours
Manual testing: 30 minutes
Bug escapes to QA:
  - QA finds bug: 2 days later
  - Debug investigation: 1 hour
  - Fix bug: 30 minutes
  - Re-test: 30 minutes
Total: ~5 hours spread over 3 days
```

With tests:
```
Write test: 15 minutes
Write code: 2 hours
Test fails (bug caught immediately):
  - Debug investigation: 10 minutes (small scope)
  - Fix bug: 15 minutes
  - Re-run tests: 5 seconds
Total: ~3 hours in same day
```

## Quotes to Remember

> _When programmers do their jobs, testers find nothing._ — Robert C. Martin

> _Tests don't break your code; they break your illusions about the quality of that code._ — Maaret Pyhäjärvi

> _Hard in training; easy in battle._ — Alexander Suvorov

> _As the tests get more specific, the code gets more generic._ — Robert C. Martin

> _Testing can show the presence of bugs, but not their absence._ — Edsger W. Dijkstra

> _Beware of bugs in the above code; I have only proved it correct, not tried it._ — Donald Knuth

## Adoption Journey

Most developers follow this progression:

**Stage 1: No tests** → "I don't have time"
**Stage 2: Tests after** → "I'll test it after I code it"
**Stage 3: Tests first (forced)** → "This feels backward but I'll try"
**Stage 4: Tests first (comfortable)** → "This actually helps design"
**Stage 5: Tests first (advocate)** → "I can't imagine coding without tests"

Each stage requires:
- Overcoming mental resistance
- Building new habits
- Experiencing the benefits personally
- Practicing with katas (not production code)

**Recommendation:** Practice TDD with code katas for 2 weeks before trying on production code. Common katas:
- Bowling Score
- Roman Numerals
- FizzBuzz (extended)
- String Calculator
- Prime Factors

## Further Reading

From the blog series:
- "Writing Tests Before the Implementation" - TDD methodology
- "Testing Benefits" - Why tests are specifications
- "Testing Concerns" - Addressing objections
- "Attributes of Effective Unit Tests" - What makes tests good
- "Test Doubles" - Isolating code for testing
