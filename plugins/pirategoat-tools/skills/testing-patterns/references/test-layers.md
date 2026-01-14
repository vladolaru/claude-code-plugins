# Test Layers: Unit, Integration, and System Testing

**Source:** Synthesized from "Test Layers: From Unit to System" (jhumelsine.github.io)

## Overview

Different types of tests operate at different levels of scope, trading detail for breadth. Understanding when to use each layer prevents both under-testing (gaps) and over-testing (waste).

**Core insight:** "Unit tests confirm the nuts and bolts. Integration tests confirm the bolt screws into the nut."

## The Mars Climate Orbiter Lesson

**September 23, 1999:** NASA lost contact with Mars Climate Orbiter.

**Cost:** $327.6 million and 286 days of mission time.

**Root cause:** Lockheed Martin's software used imperial units (pound-force seconds). NASA's software expected metric units (newton-seconds).

**Why didn't tests catch it?**
- Unit tests for each component passed
- Both teams assumed their units of measure
- No integration test validated the contract between systems

**Lesson:** Unit tests alone aren't enough. You need tests that verify components work TOGETHER.

## The Three Layers

### Horizontal View: Scope

```
┌──────────────────────────────────────────────┐
│ SYSTEM TESTS                                 │
│ Scope: Entire application + external deps    │
│ Focus: End-to-end user workflows            │
│ Speed: Slow (minutes)                        │
└──────────────────────────────────────────────┘
          ▲
          │
┌──────────────────────────────────────────────┐
│ INTEGRATION/ACCEPTANCE TESTS                 │
│ Scope: Multiple components                   │
│ Focus: Component cooperation                 │
│ Speed: Medium (seconds-minutes)              │
└──────────────────────────────────────────────┘
          ▲
          │
┌──────────────────────────────────────────────┐
│ UNIT TESTS                                   │
│ Scope: Single class/function                 │
│ Focus: Logic correctness                     │
│ Speed: Fast (milliseconds)                   │
└──────────────────────────────────────────────┘
```

### Vertical View: Detail vs Scope Trade-off

Think of zooming in/out on a map:
- **Street view (Unit):** See every detail, limited scope
- **Neighborhood (Integration):** See how streets connect, buildings as shapes
- **City view (System):** See entire layout, lose building details

**You can't see everything at every level simultaneously.**

## Layer 1: Unit Tests (The Nuts and Bolts)

### Definition
Tests for a single class or function in isolation. All dependencies replaced with test doubles.

### Example Scope

```
┌──────────────────────────┐
│ OrderCalculator          │  ← Software Under Test (SUT)
│ ├─ calculateTotal()      │
│ ├─ applyDiscount()       │
│ └─ calculateTax()        │
└──────────────────────────┘
         │
         │ All dependencies mocked:
         ├─ TaxService (Mock)
         ├─ DiscountService (Mock)
         └─ PricingService (Mock)
```

### What Unit Tests Verify

**✓ Logic correctness:**
```php
public function test_calculates_total_for_multiple_items() {
    $calculator = new OrderCalculator();

    $total = $calculator->calculateTotal( [
        [ 'price' => 10, 'quantity' => 2 ],
        [ 'price' => 5, 'quantity' => 3 ],
    ] );

    $this->assertEquals( 35, $total ); // 10*2 + 5*3
}
```

**✓ Edge cases:**
```php
public function test_handles_empty_cart() {
    $calculator = new OrderCalculator();
    $total = $calculator->calculateTotal( [] );
    $this->assertEquals( 0, $total );
}

public function test_handles_negative_quantities() {
    $calculator = new OrderCalculator();
    $this->expectException( InvalidArgumentException::class );
    $calculator->calculateTotal( [ [ 'price' => 10, 'quantity' => -1 ] ] );
}
```

**✓ Error conditions:**
```php
public function test_throws_exception_for_invalid_price() {
    $calculator = new OrderCalculator();
    $this->expectException( InvalidArgumentException::class );
    $calculator->calculateTotal( [ [ 'price' => 'invalid', 'quantity' => 1 ] ] );
}
```

### Unit Test Characteristics

| Aspect | Unit Tests |
|--------|------------|
| **Scope** | Single class/function |
| **Speed** | < 10ms per test, < 10s full suite |
| **Dependencies** | All mocked via test doubles |
| **Coverage** | Deep: all branches, edge cases |
| **When fails** | Easy to pinpoint (small scope) |
| **Fragility** | Can break during refactoring (if testing implementation) |
| **Creation effort** | Low (simple setup) |
| **Maintenance** | Medium (may need updates during refactoring) |

### When Unit Tests Aren't Enough

**What they miss:**
- Integration bugs (components don't communicate correctly)
- Configuration issues
- Deployment problems
- System-level behavior

**Example: Unit tests pass, integration fails**
```php
// Unit test: Passes
public function test_sends_order_confirmation() {
    $mockMailer = $this->createMock( Mailer::class );
    $mockMailer->expects( $this->once() )
               ->method( 'send' )
               ->with( 'john@example.com', 'Order Confirmed' );

    $service = new OrderService( $mockMailer );
    $service->confirmOrder( $order );
}

// But in production:
// - Mailer expects array, receives string
// - No email sent!
// - Unit test didn't catch type mismatch
```

## Layer 2: Integration/Acceptance Tests (The Fit)

### Definition
Tests multiple components working together. Some dependencies mocked (external services), others real (internal components).

### Example Scope

```
┌────────────────────────────────────────────┐
│ Order Processing Package (SUT)             │
│ ├─ OrderService                            │
│ ├─ OrderCalculator                         │
│ ├─ OrderValidator                          │
│ └─ OrderRepository                         │
└────────────────────────────────────────────┘
         │
         │ External dependencies mocked:
         ├─ PaymentGateway (Mock)
         ├─ EmailService (Mock)
         └─ InventoryAPI (Mock)
```

### What Integration Tests Verify

**✓ Component cooperation:**
```php
public function test_order_processing_flow() {
    // Real components work together
    $calculator = new OrderCalculator();
    $validator = new OrderValidator();
    $service = new OrderService( $calculator, $validator, $mockGateway );

    $result = $service->processOrder( $order );

    $this->assertTrue( $result->isSuccess() );
    $this->assertNotEmpty( $result->getConfirmationNumber() );
}
```

**✓ Contract validation:**
```php
public function test_repository_saves_order_with_correct_structure() {
    $repository = new OrderRepository( $database );

    $orderId = $repository->save( $order );

    $retrieved = $repository->find( $orderId );
    $this->assertEquals( $order->getTotal(), $retrieved->getTotal() );
    $this->assertEquals( $order->getStatus(), $retrieved->getStatus() );
}
```

**✓ Data flow:**
```php
public function test_discount_flows_through_order_pipeline() {
    $service = new OrderService( $calculator, $validator, $discountService );

    $order = $service->processOrder( $cartWithCoupon );

    $this->assertEquals( 90, $order->getTotal() ); // 100 - 10% discount
}
```

### Acceptance Tests (Subset of Integration)

**Definition:** Integration tests that specify user-desired behavior (from User Story acceptance criteria).

**Example:**
```gherkin
Feature: VIP Customer Discount

  Scenario: VIP customer gets 10% discount on orders
    Given a customer with VIP status
    And a cart with $100 of items
    When the order is processed
    Then the final total should be $90
    And the order should show "VIP Discount: $10"
```

**As code:**
```php
public function test_vip_customer_receives_discount() {
    // Acceptance criteria from user story
    $vipCustomer = $this->createVipCustomer();
    $cart = $this->createCartWithTotal( 100 );

    $order = $this->orderService->process( $cart, $vipCustomer );

    $this->assertEquals( 90, $order->getTotal() );
    $this->assertEquals( 10, $order->getDiscount()->getAmount() );
    $this->assertEquals( 'VIP Discount', $order->getDiscount()->getDescription() );
}
```

### Integration Test Characteristics

| Aspect | Integration Tests |
|--------|-------------------|
| **Scope** | Multiple components (package/module) |
| **Speed** | Medium (100ms - 2s per test) |
| **Dependencies** | Internal real, external mocked |
| **Coverage** | Breadth: happy paths, key scenarios |
| **When fails** | Moderate effort to pinpoint |
| **Fragility** | Low (tests behavior, not structure) |
| **Creation effort** | Medium (more setup) |
| **Maintenance** | Low (behavior-focused) |

## Layer 3: System Tests (The Whole Product)

### Definition
Tests entire system including UI, database, and potentially real external services. Simulates real user behavior.

### Example Scope

```
┌─────────────────────────────────────────────────┐
│ Complete E-commerce Application (SUT)          │
│ ├─ Frontend (React)                             │
│ ├─ API Layer                                    │
│ ├─ Business Logic                               │
│ ├─ Database                                     │
│ └─ Background Jobs                              │
└─────────────────────────────────────────────────┘
         │
         │ Some externals mocked:
         ├─ Payment Gateway (Mock or Test Mode)
         ├─ Email Service (Mock or Catch-all)
         └─ Shipping API (Mock)
```

### What System Tests Verify

**✓ End-to-end workflows:**
```javascript
// Playwright E2E test
test('complete order flow', async ({ page }) => {
    // User journey from start to finish
    await page.goto('/products');

    // Add to cart
    await page.click('[data-product-id="123"]');
    await page.click('button:text("Add to Cart")');

    // Checkout
    await page.click('a:text("Checkout")');
    await page.fill('[name="email"]', 'test@example.com');
    await page.fill('[name="cardNumber"]', '4242424242424242');

    // Complete order
    await page.click('button:text("Place Order")');

    // Verify confirmation
    await expect(page.locator('.confirmation')).toContainText('Order Confirmed');
});
```

**✓ UI integration:**
```javascript
test('displays validation errors', async ({ page }) => {
    await page.goto('/checkout');
    await page.click('button:text("Place Order")'); // Submit empty form

    // Verify error messages display
    await expect(page.locator('.error-email')).toBeVisible();
    await expect(page.locator('.error-card')).toBeVisible();
});
```

**✓ Cross-system behavior:**
```javascript
test('order creates record in database and sends email', async ({ page, request }) => {
    await page.goto('/checkout');
    // ... complete order flow

    // Verify database record
    const response = await request.get('/api/orders/latest');
    const order = await response.json();
    expect(order.status).toBe('pending');

    // Verify email sent (check mock or email catcher)
    // ...
});
```

### System Test Characteristics

| Aspect | System Tests |
|--------|--------------|
| **Scope** | Entire application |
| **Speed** | Slow (5-30s per test) |
| **Dependencies** | All real (or production-like) |
| **Coverage** | Shallow: critical paths only |
| **When fails** | Hard to pinpoint (large scope) |
| **Fragility** | High (UI changes break tests) |
| **Creation effort** | High (complex setup) |
| **Maintenance** | High (sensitive to changes) |

## Test Strategy Comparison

### Ice Cream Cone (Anti-Pattern)

```
     ┌────────────────────────┐
     │   System Tests         │
     │   (Manual QA)          │
     │                        │
     ├────────────────────────┤
     │ Integration Tests      │
     ├────────────────────────┤
     │ Unit Tests             │
     └────────────────────────┘
```

**Problems:**
- Slow feedback (days to find bugs)
- Manual testing doesn't scale
- QA becomes bottleneck
- Expensive to fix bugs late

**When it's all you have:**
- Legacy codebases without test infrastructure
- Transitioning from manual QA culture

### Pyramid (Recommended for Most)

```
     ┌────────────────────┐
     │  System Tests      │
     ├────────────────────┤
     │                    │
     │  Integration Tests │
     │                    │
     ├────────────────────┤
     │                    │
     │                    │
     │    Unit Tests      │
     │                    │
     │                    │
     └────────────────────┘
```

**Benefits:**
- Fast feedback (seconds)
- High coverage at low cost
- Easy to pinpoint failures
- Supports TDD workflow

**Best for:**
- New projects
- Modular architectures
- Teams practicing TDD

### Trophy (Alternative)

```
     ┌────────────────────┐
     │  System Tests      │
     ├────────────────────┤
     │                    │
     │                    │
     │  Integration Tests │
     │                    │
     │                    │
     ├────────────────────┤
     │  Unit Tests        │
     ├────────────────────┤
     │  Static Analysis   │
     └────────────────────┘
```

**Benefits:**
- Focuses on user-facing behavior
- Tests are less brittle (behavior vs structure)
- Good for API-heavy architectures

**Best for:**
- Microservices
- API-first designs
- Teams that refactor frequently

### Comparison Table

| Strategy | Unit | Integration | System | Best For |
|----------|------|-------------|--------|----------|
| **Ice Cream Cone** | Few | Few | Many (manual) | Legacy transition |
| **Pyramid** | Many | Some | Few | TDD, modular code |
| **Trophy** | Some | Many | Few | APIs, microservices |

## Choosing the Right Layer

### Decision Matrix

| What You're Testing | Layer | Rationale |
|---------------------|-------|-----------|
| Pure function logic | Unit | Fast, isolated, deterministic |
| Algorithm edge cases | Unit | Need to test all branches |
| Class with dependencies | Unit | Mock dependencies for isolation |
| Database queries | Integration | Need real DB behavior |
| API endpoint contract | Integration | Verify request/response |
| Multiple services cooperating | Integration | Verify communication |
| User workflow | System | Verify end-to-end experience |
| UI layout/styling | System (visual regression) | Screenshot comparison |

### Coverage Guidelines

**Unit tests:**
- All business logic
- All edge cases
- All error conditions
- Algorithm variations

**Integration tests:**
- Component boundaries
- Data flow between components
- Contract validation
- Database operations

**System tests:**
- Critical user journeys (checkout, signup, etc.)
- Cross-cutting concerns (auth, permissions)
- Smoke tests (basic functionality works)

**Don't test at every layer:**
- Edge case testing → Unit (comprehensive)
- Edge case verification → Integration (spot check)
- Edge case validation → System (skip)

## The Composable Design Challenge

**Special case:** When behavior emerges from runtime composition (Interpreter pattern, DSLs, composable designs).

**Problem:** Infinite possible combinations can't all be tested.

**Example: Compiler**
- Can test each language feature (unit tests)
- Can test feature combinations (integration tests)
- Can't test every possible program (infinite)

**Solution:**
1. **Test elements thoroughly** (unit tests for each component)
2. **Test common compositions** (integration tests for typical patterns)
3. **Add guardrails** (validate compositions are legal)
4. **Trust the math** (if elements work and composition rules are sound, combinations work)

**Like LEGO:**
- Test each brick type (unit)
- Test common structures (integration)
- Define connection rules (validation)
- Trust: if bricks work individually and connections are valid, any structure works

## Common Mistakes

### Mistake 1: Only Testing Happy Path

```php
// INCOMPLETE: Only tests success case
public function test_processes_order() {
    $result = $service->process( $validOrder );
    $this->assertTrue( $result->isSuccess() );
}

// COMPLETE: Tests failure cases too
public function test_rejects_invalid_order() {
    $result = $service->process( $invalidOrder );
    $this->assertFalse( $result->isSuccess() );
    $this->assertContains( 'validation error', $result->getMessage() );
}
```

### Mistake 2: Testing Same Thing at Multiple Layers

```php
// Unit test: Calculates discount
public function test_calculates_vip_discount() {
    $calculator = new DiscountCalculator();
    $this->assertEquals( 10, $calculator->calculate( $vipCustomer, 100 ) );
}

// Integration test: Same calculation in context
public function test_applies_vip_discount_to_order() {
    $order = $service->processOrder( $cart, $vipCustomer );
    $this->assertEquals( 90, $order->getTotal() );
}

// System test: Not needed!
// Don't test discount calculation again at system level
```

**Better:** Test calculation deeply at unit level, verify it integrates at integration level, skip at system level.

### Mistake 3: No Integration Tests

```
Unit tests: ✅ All pass
System tests: ✅ All pass
Production: ❌ Mars Orbiter crashes

Missing: Integration tests for component contracts
```

## Quotes

> _Unit tests confirm the nuts and bolts. Integration tests confirm the bolt screws into the nut._ — Unknown

> _Testing can show the presence of bugs, but not their absence._ — Edsger W. Dijkstra

> _Write tests until fear is transformed into boredom._ — Kent Beck

## Further Reading

- "Test Layers: From Unit to System" - jhumelsine.github.io
- "The Practical Test Pyramid" - Martin Fowler
- "Testing Strategies in Microservices" - Martin Fowler
- "Ice Cream Cone Anti-Pattern" - Alister Scott
- "The Testing Trophy" - Kent C. Dodds
