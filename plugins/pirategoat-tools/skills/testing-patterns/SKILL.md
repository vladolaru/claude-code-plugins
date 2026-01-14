---
name: testing-patterns
description: Use when reviewing tests for quality, writing new tests, or identifying test anti-patterns (flaky, brittle, over-mocked) - covers test philosophy, quality principles, and language-specific patterns for PHPUnit/WordPress, Jest/Vitest, and Playwright E2E.
---

# Testing Patterns

Comprehensive guide for writing and reviewing high-quality tests across PHP, JavaScript, and E2E testing frameworks. Focuses on test quality patterns, not TDD methodology.

## When to Use This Skill

Use this skill when:
- Reviewing test code for quality issues
- Writing new tests (structure, assertions, fixtures)
- Identifying test anti-patterns
- Setting up test data (fixtures, factories, builders)
- Choosing mocking strategies
- Evaluating test coverage decisions

**NOT for:** TDD methodology (red-green-refactor workflow) - that's a separate skill.

## Test Philosophy (Core Mindset)

**Critical shift:** Tests are **specifications**, not verification. They define what the code should do, then confirm it.

| Principle | Meaning |
|-----------|---------|
| **Specifications first** | Tests codify behavior requirements. Implementation is challenged against these specs. |
| **Tests as experiments** | Write tests that try to break code. "Hard in training, easy in battle." |
| **Future-focused** | Tests don't find bugs now—they prevent future bugs during refactoring/enhancement. |
| **Document assumptions** | Tests capture developer intentions that would otherwise live only in heads/comments. |

**If you think tests verify implementation → you'll write brittle tests.**
**If you think tests specify behavior → you'll write maintainable tests.**

**→ Deep dive:** See `references/test-philosophy.md` for complete mental models, behavior vs implementation distinction, common traps, and real-world examples.

## What Makes a Good Test (Quick Reference)

| Principle | Description | Violation Symptom |
|-----------|-------------|-------------------|
| Behavior-Based | Tests specify **what**, not **how**. Should survive refactoring. | Tests break when refactoring without behavior changes |
| Independent | No shared state, runs in isolation | Tests pass/fail based on run order |
| Deterministic | Same input = same output, no flaky tests | Random failures, CI inconsistency |
| Fast | Minimize I/O, mock external dependencies | Slow test suite, developer frustration |
| Readable | Test name describes scenario and expectation | "What does this test verify?" |
| Single Concern | One logical assertion per test | Unclear which behavior failed |
| Declarative | Test name/structure declares context and intent | Missing Given/When/Then structure |
| Complete | Covers all scenarios including edge/error cases | "This should never happen" occurs in production |
| Maintainable | DRY setup, clear arrange-act-assert | Brittle tests, high maintenance |

## FORBIDDEN Patterns (Quick Reference)

| :x: WRONG | :white_check_mark: CORRECT | Issue |
|-----------|----------------------------|-------|
| Tests depend on execution order | Each test sets up own state | Coupling |
| Multiple assertions testing different things | One logical assertion | Unclear failures |
| Testing implementation details | Testing behavior/outcomes | Brittle tests |
| Hard-coded dates/times | Time mocking or relative dates | Flaky tests |
| Real HTTP calls in unit tests | Mock HTTP client | Slow/unreliable |
| Asserting on exact error messages | Assert error type/code | Localization issues |
| Shared mutable fixtures | Fresh fixtures per test | Test pollution |
| `sleep()` / `setTimeout()` for sync | Proper async waiting | Flaky, slow |

## Test Smells (Diagnosis Guide)

When reviewing tests, these smells indicate problems:

| Smell | Likely Root Cause | Fix |
|-------|-------------------|-----|
| **Slow tests** (> few seconds for suite) | Coupled to external dependencies (DB, HTTP, filesystem) | Mock external boundaries |
| **Flaky tests** (random pass/fail) | **Implementation problem**: race conditions, timing, non-determinism | Fix implementation, not just test. May need design change. |
| **Brittle tests** (break during refactoring) | Testing implementation details, not behavior | Rewrite to test public API/behavior |
| **Complex tests** (many setups, assertions) | **Code smell**: SUT has too many responsibilities | Simplify SUT, then tests become simple |
| **False positive tests** (no real assertions) | Test only confirms "doesn't crash" | Add meaningful assertions |
| **Tests requiring extensive Test Doubles** | Tight coupling in SUT | Refactor for loose coupling |

**Key insight from blog:** _"Tests don't break your code; they break your illusions about the quality of that code."_

**Special note on flaky tests:** Don't just "fix the test." Flaky tests often reveal concurrency/timing bugs in implementation.

**→ Deep dive:** See `references/test-smells.md` for complete diagnostic protocols, investigation steps, and detailed fix strategies for each smell (16KB guide).

## Common Anti-Patterns This Skill Addresses

| Symptom | Solution Location |
|---------|-------------------|
| Flaky tests (random pass/fail) | `test-quality.md` -> Determinism |
| Test depends on other tests | `test-quality.md` -> Independence |
| "What does this test actually verify?" | `test-structure.md` -> Naming |
| Excessive mocking hiding bugs | `mocking-strategies.md` -> Decision Framework |
| Slow test suite | `test-quality.md` -> Performance |
| Tests break when refactoring | `mocking-strategies.md` -> Mock at Boundaries |
| Hard to write tests for code | Code is tightly coupled (design smell) |
| Missing edge cases | `coverage.md` -> What to Test |
| Over-testing trivial code | `coverage.md` -> What NOT to Test |

## Reference Library

### Quick Reference (Tactical)

For immediate lookup during code review or test writing:

```
references/test-quality.md           # 12 attributes of effective tests
references/test-structure.md         # AAA pattern, naming, organization
references/mocking-strategies.md     # When/how to mock, decision framework
references/test-data.md              # Fixtures, factories, builders
references/coverage.md               # What to test, prioritization
```

### Deep Dives (Strategic)

For understanding concepts, fixing systemic issues, or learning:

```
references/README.md                 # Navigation guide with reading paths
references/test-philosophy.md        # Mental models, the fundamental shift
references/test-smells.md            # Diagnostic guide with root cause analysis
references/tdd-workflow.md           # Red-Green-Refactor methodology
references/test-layers.md            # Unit/Integration/System strategy
references/test-benefits.md          # 13 benefits, why testing matters
```

**When to use deep dives:**
- **test-philosophy.md** → Understanding the mindset shift (specs vs verification)
- **test-smells.md** → Debugging flaky, brittle, or slow tests
- **tdd-workflow.md** → Learning Test-Driven Development
- **test-layers.md** → Choosing testing strategy (Pyramid vs Trophy)
- **test-benefits.md** → Building case for testing investment

### Language-Specific Patterns

For framework-specific implementation details:

```
references/phpunit-patterns.md       # PHPUnit + WordPress + WooCommerce
references/jest-vitest-patterns.md   # Jest, Vitest, React Testing Library
references/playwright-patterns.md    # E2E patterns, Page Object Model
```

## Core Principles (Quick Summary)

### 1. Test Quality
See `test-quality.md` for complete patterns.

**The Five Pillars:** Independent, Deterministic, Fast, Readable, Single Concern.

**Key rule:** If a test can fail for reasons unrelated to the code under test, it's a bad test.

### 2. Test Structure (AAA)
See `test-structure.md` for complete patterns.

**Pattern:** Arrange -> Act -> Assert (with visual separation)

**Naming conventions:**
- PHP: `test_[scenario]_[expected_outcome]`
- JS: `should [expectation] when [condition]`

### 3. Mocking
See `mocking-strategies.md` for complete patterns.

**Key rules:**
- Mock at system boundaries (HTTP, database, file system, time)
- Don't mock what you own (usually a design smell)
- Prefer fakes over mocks for complex behavior
- Verify interactions only when behavior matters

### 4. Test Data
See `test-data.md` for complete patterns.

| Strategy | When to Use |
|----------|-------------|
| Fixtures | Static, reusable data (config, sample files) |
| Factories | Dynamic data generation with sensible defaults |
| Builders | Complex objects with fluent interface |

### 5. Coverage
See `coverage.md` for complete patterns.

**Test:** Business logic, edge cases, error paths, security-sensitive code
**Skip:** Framework code, trivial getters/setters, generated code

## Language-Specific Quick Reference

### PHP/PHPUnit
See `phpunit-patterns.md` for complete patterns.

```php
// Data provider for parameterized tests
public function data_provider_order_statuses(): array {
    return [
        'pending' => [ 'pending', false ],
        'completed' => [ 'completed', true ],
    ];
}

/** @dataProvider data_provider_order_statuses */
public function test_is_paid( string $status, bool $expected ): void {
    $order = $this->factory->order->create_and_get( [ 'status' => $status ] );
    $this->assertSame( $expected, $order->is_paid() );
}
```

**WordPress:** `WP_UnitTestCase`, `$this->factory`, database transactions
**WooCommerce:** `WC_Unit_Test_Case`, order/product/customer factories

### JavaScript (Jest/Vitest)
See `jest-vitest-patterns.md` for complete patterns.

```javascript
describe('OrderService', () => {
    beforeEach(() => {
        jest.clearAllMocks();
    });

    it('should calculate total with tax when tax is enabled', async () => {
        // Arrange
        mockTaxService.isEnabled.mockReturnValue(true);

        // Act
        const total = await orderService.calculateTotal(items);

        // Assert
        expect(total).toBe(110);
    });
});
```

**Async:** Use `async/await`, mock timers with `jest.useFakeTimers()`
**Snapshots:** Use sparingly, only for stable large outputs

### E2E (Playwright)
See `playwright-patterns.md` for complete patterns.

```typescript
// Page Object Model
class CheckoutPage {
    constructor(private page: Page) {}

    async fillBillingAddress(address: Address) {
        await this.page.getByLabel('First name').fill(address.firstName);
        // ... more fields
    }

    async placeOrder() {
        await this.page.getByRole('button', { name: 'Place order' }).click();
    }
}
```

**Selectors priority:** `data-testid` > role > text > CSS > XPath
**Waiting:** Let Playwright auto-wait, avoid explicit sleeps

## Test Review Checklist

### Quality
- [ ] Tests are independent (no shared mutable state)
- [ ] Tests are deterministic (no time/random dependencies without mocking)
- [ ] Tests are fast (I/O is mocked in unit tests)
- [ ] Tests are readable (clear names, AAA structure)

### Structure
- [ ] Clear arrange-act-assert sections
- [ ] Descriptive test names (scenario + expectation)
- [ ] Appropriate grouping/organization
- [ ] No unnecessary setup duplication (use fixtures/factories)

### Mocking
- [ ] Mocking at appropriate boundaries
- [ ] Not over-mocking (testing implementation details)
- [ ] Mocks verified only when behavior matters
- [ ] Proper cleanup/reset between tests

### Assertions
- [ ] Single logical assertion per test
- [ ] Testing outcomes, not implementation
- [ ] Helpful failure messages
- [ ] Edge cases covered

### Coverage
- [ ] Happy path covered
- [ ] Error cases covered
- [ ] Boundary conditions covered
- [ ] Not testing trivial/framework code

## Test Layer Context (When to Use Which)

This skill focuses on test quality patterns across layers. For choosing the right layer:

| Layer | Scope | Speed | Purpose | When to Use |
|-------|-------|-------|---------|-------------|
| **Unit** | Single class/function | Fast (seconds) | Test individual "nuts and bolts" | Default for logic, edge cases, error paths |
| **Integration** | Multiple components | Medium (seconds-minutes) | Test that "bolt screws into nut" | Verify components cooperate correctly |
| **E2E** | Full system | Slow (minutes) | Test real user workflows | Smoke tests, critical user journeys |

**Key insight:** Lower tests give confidence in components. Higher tests give confidence in system.

**Strategy note:** Most projects benefit from **Pyramid** (many unit, fewer integration, fewest E2E) or **Trophy** (strong integration focus).

**Composable designs caveat:** When behavior emerges from runtime composition (interpreters, DSLs), you cannot test all combinations—test individual elements thoroughly, add guardrails for valid compositions.

**→ Deep dive:** See `references/test-layers.md` for the Mars Orbiter lesson, Pyramid/Trophy/Ice Cream Cone strategies, detailed comparison tables, and when to use which layer (17KB guide).

## When to Mock (Decision Table)

### Mocking Principles (Read First)

**Before consulting the decision table, internalize these rules:**

| Principle | Explanation |
|-----------|-------------|
| **Mock at boundaries** | Mock HTTP, database, filesystem, time, external APIs—not your own domain logic |
| **Don't mock what you own** | Mocking internal classes is usually a design smell (tight coupling) |
| **Prefer fakes over mocks** | For complex behavior, use a lightweight fake implementation rather than brittle mocks |
| **Verify behavior, not implementation** | Mock to isolate, not to verify internal method calls |

**Red flag:** If you're mocking extensively within your own codebase → redesign for loose coupling first.

**→ See also:** `references/mocking-strategies.md` for complete decision framework, test double types (Dummy, Stub, Mock, Spy, Fake), and dependency injection patterns.

| Scenario | Unit Test | Integration Test |
|----------|-----------|------------------|
| HTTP calls | Always mock | Mock or use test server |
| Database | Mock | Real (with transactions) |
| File system | Mock | Temp directory |
| Time/dates | Always mock | Always mock |
| Random values | Seed or mock | Seed or mock |
| Third-party APIs | Always mock | Always mock |
| Internal classes | Rarely (design smell) | No |

## Test Type Selection

| What to Test | Test Type | Why |
|--------------|-----------|-----|
| Pure function logic | Unit test | Fast, isolated |
| Class with dependencies | Unit test + mocks | Control dependencies |
| Database interactions | Integration test | Real behavior |
| API endpoint behavior | Integration test | Real request/response |
| User workflow | E2E test | Real browser behavior |
| Visual appearance | Visual regression | Screenshot comparison |

## Notes

- Test quality matters more than test quantity
- If tests are hard to write, the code is probably hard to use
- Flaky tests erode trust and should be fixed or removed
- Tests are documentation - write them for future developers

## Using the Reference Library

**Quick lookups:** Use the quick reference files above for immediate tactical guidance during code review.

**Deep understanding:** When you need to understand concepts deeply or fix systemic issues, use the deep-dive references:

- **Struggling with testing mindset?** → `references/test-philosophy.md` (12KB)
- **Tests are flaky, brittle, or slow?** → `references/test-smells.md` (16KB)
- **Learning TDD methodology?** → `references/tdd-workflow.md` (15KB)
- **Choosing testing strategy?** → `references/test-layers.md` (17KB)
- **Building case for testing?** → `references/test-benefits.md` (17KB)

**Navigation guide:** Start with `references/README.md` for organized reading paths based on your needs (new to testing, debugging problems, strategy planning, etc.).

**All references synthesized from:** jhumelsine.github.io testing series - comprehensive software architecture blog with real-world examples and battle-tested insights.
