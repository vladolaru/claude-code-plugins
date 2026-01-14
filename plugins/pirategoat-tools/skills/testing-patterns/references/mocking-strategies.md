# Mocking Strategies

When and how to use mocks, stubs, fakes, and other test doubles effectively.

## Quick Reference: When to Mock

| Dependency Type | Unit Test | Integration Test |
|-----------------|-----------|------------------|
| HTTP/API calls | Always mock | Mock or test server |
| Database | Mock | Real (with transactions) |
| File system | Mock | Temp directories |
| Time/clock | Always mock | Always mock |
| Random/UUID | Seed or mock | Seed or mock |
| Third-party services | Always mock | Always mock |
| Internal classes | Rarely | Never |
| Framework code | Never | Never |

---

## The Mocking Decision Framework

### Mock When:

1. **Slow** - Network calls, database, file I/O
2. **Non-deterministic** - Time, random, external services
3. **Has side effects** - Sends emails, charges cards
4. **Not available in test** - Production APIs, hardware
5. **Hard to trigger** - Errors, edge cases

### Don't Mock When:

1. **Simple value objects** - Just use the real thing
2. **Internal implementation details** - Leads to brittle tests
3. **You own the code and it's fast** - Test the real behavior
4. **Framework code** - Trust the framework

### Decision Flow

```
Is the dependency...
├── Slow (network, DB, files)? ───────────────> Mock
├── Non-deterministic (time, random)? ────────> Mock
├── Has side effects (email, payments)? ──────> Mock
├── Simple value object? ─────────────────────> Don't mock
├── Internal implementation you own? ─────────> Usually don't mock
└── Framework/library code? ──────────────────> Never mock
```

---

## Types of Test Doubles

### Dummy

Objects passed around but never used. Fill parameter lists.

```php
public function test_create_order_assigns_customer() {
    $dummy_logger = $this->createMock( Logger::class );
    // Logger is required but not used in this test

    $service = new OrderService( $dummy_logger );
    $order = $service->create( $this->cart, $this->customer );

    $this->assertSame( $this->customer->id, $order->customer_id );
}
```

### Stub

Pre-programmed responses. Used when you need controlled input.

```php
public function test_calculate_tax_uses_customer_location() {
    // Stub: Returns predetermined value
    $tax_service = $this->createStub( TaxService::class );
    $tax_service->method( 'get_rate' )
                ->willReturn( 0.08 ); // Always returns 8%

    $calculator = new OrderCalculator( $tax_service );
    $total = $calculator->calculate_with_tax( 100.00 );

    $this->assertSame( 108.00, $total );
}
```

```javascript
// Jest stub
const taxService = {
    getRate: jest.fn().mockReturnValue(0.08),
};
```

### Spy

Records calls for later verification. Used when you need to verify interactions.

```php
public function test_order_creation_logs_event() {
    $logger_spy = $this->createMock( Logger::class );

    // Spy: Records that this was called
    $logger_spy->expects( $this->once() )
               ->method( 'log' )
               ->with( 'order_created', $this->anything() );

    $service = new OrderService( $logger_spy );
    $service->create( $this->cart, $this->customer );
    // Verification happens automatically at end of test
}
```

```javascript
// Jest spy
const logger = { log: jest.fn() };
const service = new OrderService(logger);

service.create(cart, customer);

expect(logger.log).toHaveBeenCalledWith('order_created', expect.any(Object));
```

### Mock

Pre-programmed expectations that verify interactions.

```php
public function test_payment_gateway_is_called_with_correct_amount() {
    $gateway = $this->createMock( PaymentGateway::class );

    // Mock: Expects specific call
    $gateway->expects( $this->once() )
            ->method( 'charge' )
            ->with( 100.00, $this->anything() )
            ->willReturn( new PaymentResult( 'success' ) );

    $service = new OrderService( $gateway );
    $service->process_payment( $this->order );
}
```

### Fake

Working implementation with shortcuts. Best for complex collaborators.

```php
// Fake: Real implementation, simplified for testing
class FakePaymentGateway implements PaymentGatewayInterface {
    public array $charges = [];

    public function charge( float $amount, array $card ): PaymentResult {
        $this->charges[] = [
            'amount' => $amount,
            'card' => $card,
        ];
        return new PaymentResult( 'success', 'fake-transaction-' . count( $this->charges ) );
    }
}

public function test_order_processes_payment() {
    $fake_gateway = new FakePaymentGateway();
    $service = new OrderService( $fake_gateway );

    $service->process( $this->order );

    $this->assertCount( 1, $fake_gateway->charges );
    $this->assertSame( $this->order->total, $fake_gateway->charges[0]['amount'] );
}
```

### When to Use Each

| Double | Use When | Example |
|--------|----------|---------|
| Dummy | Need to fill a parameter | Unused logger |
| Stub | Need controlled input | Tax rate lookup |
| Spy | Need to verify a call happened | Audit logging |
| Mock | Need to verify call with specific args | Payment gateway |
| Fake | Complex behavior, multiple interactions | In-memory database |

---

## Mock Verification Guidelines

### Verify Only What Matters

```php
// BAD: Over-verification (testing implementation)
$service->expects( $this->once() )
        ->method( 'process' )
        ->with(
            $this->equalTo( 100 ),
            $this->equalTo( 'USD' ),
            $this->equalTo( 'card' ),
            $this->anything(),
            $this->anything()
        );

// GOOD: Verify only what this test cares about
$service->expects( $this->once() )
        ->method( 'process' )
        ->with( 100, $this->anything(), $this->anything() );
```

### State vs Behavior Verification

**State verification:** Assert on the result (preferred).

```php
// State verification - simpler, less brittle
public function test_order_total_is_calculated() {
    $order = $this->service->create( $this->cart );
    $this->assertSame( 110.00, $order->total );
}
```

**Behavior verification:** Assert that something was called (use sparingly).

```php
// Behavior verification - use when the call itself is the behavior
public function test_order_creation_sends_notification() {
    $notifier = $this->createMock( Notifier::class );
    $notifier->expects( $this->once() )->method( 'send' );

    $service = new OrderService( $notifier );
    $service->create( $this->cart );
}
```

### When Behavior Verification is Appropriate

1. **Side effects are the behavior** - Sending emails, webhooks
2. **No observable state change** - Fire-and-forget operations
3. **Verifying integration points** - API was called correctly

---

## Common Mocking Mistakes

### 1. Mocking Implementation Details

```php
// BAD: Testing that a private method is called
$service = $this->getMockBuilder( OrderService::class )
                ->onlyMethods( [ 'validateCart' ] )
                ->getMock();
$service->expects( $this->once() )->method( 'validateCart' );
// This test breaks if you refactor the implementation!

// GOOD: Test the behavior
public function test_create_order_rejects_invalid_cart() {
    $invalid_cart = $this->create_empty_cart();

    $this->expectException( InvalidCartException::class );
    $this->service->create( $invalid_cart );
}
```

### 2. Testing Mocks Instead of Code

```php
// BAD: Only testing the mock returns what we told it to
$calculator = $this->createStub( Calculator::class );
$calculator->method( 'add' )->willReturn( 5 );

$result = $calculator->add( 2, 3 );
$this->assertSame( 5, $result );  // Of course it's 5 - we said so!

// GOOD: Mock the dependency, test the real code
$tax_service = $this->createStub( TaxService::class );
$tax_service->method( 'get_rate' )->willReturn( 0.10 );

$calculator = new PriceCalculator( $tax_service );  // Real code!
$result = $calculator->calculate_total( 100 );

$this->assertSame( 110, $result );  // Tests real logic
```

### 3. Mock Leaking Between Tests

```javascript
// BAD: Mocks persist across tests
jest.mock('./api');

it('test one', () => {
    api.fetch.mockReturnValue('result1');
    // ...
});

it('test two', () => {
    // Still has mockReturnValue from test one!
});

// GOOD: Clear mocks in beforeEach
beforeEach(() => {
    jest.clearAllMocks();
});
```

### 4. Over-Mocking (The "Mock Everything" Trap)

```php
// BAD: So many mocks, what are we even testing?
public function test_process_order() {
    $cart_mock = $this->createMock( Cart::class );
    $customer_mock = $this->createMock( Customer::class );
    $product_mock = $this->createMock( Product::class );
    $tax_mock = $this->createMock( TaxCalculator::class );
    $shipping_mock = $this->createMock( ShippingCalculator::class );
    $payment_mock = $this->createMock( PaymentGateway::class );
    $notification_mock = $this->createMock( Notifier::class );
    $logger_mock = $this->createMock( Logger::class );

    // At this point, we're testing nothing useful
}

// GOOD: Only mock what needs to be mocked
public function test_process_order_charges_payment() {
    $payment_gateway = $this->createMock( PaymentGateway::class );
    $payment_gateway->expects( $this->once() )
                    ->method( 'charge' )
                    ->with( 100.00 );

    // Use real objects for everything else
    $service = new OrderService( $payment_gateway );
    $service->process( $this->create_real_order() );
}
```

---

## Language-Specific Patterns

### PHP: PHPUnit Mocks

```php
// Create mock
$mock = $this->createMock( ServiceInterface::class );

// Stub a method
$mock->method( 'getValue' )->willReturn( 42 );

// Set expectation
$mock->expects( $this->once() )
     ->method( 'doSomething' )
     ->with( $this->equalTo( 'arg' ) );

// Consecutive returns
$mock->method( 'getNext' )
     ->willReturnOnConsecutiveCalls( 1, 2, 3 );

// Throw exception
$mock->method( 'fail' )
     ->willThrowException( new RuntimeException() );
```

### PHP: Brain Monkey for WordPress

```php
// Mock WordPress functions
use Brain\Monkey\Functions;

Functions\when( 'get_option' )->justReturn( 'value' );
Functions\expect( 'update_option' )->once()->with( 'key', 'value' );
Functions\when( 'current_time' )->justReturn( '2024-01-15' );
```

### JavaScript: Jest

```javascript
// Module mock
jest.mock('./api');
api.fetch.mockResolvedValue({ data: 'value' });

// Function mock
const callback = jest.fn();
callback.mockReturnValue(42);

// Spy on method
const spy = jest.spyOn(object, 'method');

// Clear between tests
beforeEach(() => {
    jest.clearAllMocks();
});

// Verify calls
expect(callback).toHaveBeenCalledWith('arg');
expect(callback).toHaveBeenCalledTimes(1);
```

### JavaScript: Vitest

```javascript
import { vi } from 'vitest';

// Function mock
const mock = vi.fn();
mock.mockReturnValue(42);

// Module mock
vi.mock('./api', () => ({
    fetch: vi.fn().mockResolvedValue({ data: 'value' }),
}));

// Spy
const spy = vi.spyOn(object, 'method');
```

---

## Best Practices Summary

| Do | Don't |
|----|-------|
| Mock at boundaries | Mock internal implementation |
| Verify important interactions | Verify every call |
| Use fakes for complex behavior | Over-mock simple objects |
| Clear mocks between tests | Let mocks leak |
| Mock slow/non-deterministic | Mock fast, deterministic code |
| Prefer state verification | Over-use behavior verification |
| Keep mocks focused | Create god-mocks that do everything |
