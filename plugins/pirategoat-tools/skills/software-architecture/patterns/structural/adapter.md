# Adapter Pattern

## Quick Reference

| Aspect | Detail |
|--------|--------|
| Intent | Translate one interface to another without changing behavior |
| When to Use | Incompatible interface on a class whose behavior you need |
| Key Benefit | Isolates interface changes to a single translation layer |

## When to Use

- Third-party library/service has different interface than your code expects
- Legacy code has valuable functionality but incompatible API
- Supporting multiple versions of an external API simultaneously
- Plugin architecture where new plugins must conform to existing interfaces
- You want to absorb the impact of external API changes in one place
- **Critical:** The adapted class must be behaviorally compatible -- Adapter changes contracts, not behavior

## When NOT to Use

- Behaviors are fundamentally different (not just interfaces) -- wrong pattern
- You can modify the service directly without breaking other consumers
- Simple one-time usage where direct coupling costs less than abstraction
- You need to add behavior (use Decorator), simplify subsystems (use Facade), or control access (use Proxy)
- Performance-critical tight loops where indirection overhead matters

## WordPress/PHP

### Payment Gateway Adapter

```php
// Your e-commerce system expects this interface
interface PaymentGatewayInterface {
    public function processPayment(Order $order): PaymentResult;
}

// Third-party Stripe has its own API (cannot modify)
class StripePaymentService {
    public function charge(int $amountInCents, string $currency, string $token): array {
        // Stripe-specific implementation
    }
}

// Adapter translates between your interface and Stripe's
class StripeAdapter implements PaymentGatewayInterface {
    private StripePaymentService $stripe;

    public function __construct(StripePaymentService $stripe) {
        $this->stripe = $stripe;
    }

    public function processPayment(Order $order): PaymentResult {
        // ONLY translation logic -- no business logic here
        $amountInCents = (int)($order->getTotal() * 100);
        $result = $this->stripe->charge(
            $amountInCents,
            $order->getCurrency(),
            $order->getPaymentToken()
        );

        return new PaymentResult(
            success: $result['status'] === 'succeeded',
            transactionId: $result['id']
        );
    }
}
```

### API Version Adapter

```php
// Unified interface for your code
interface NotificationServiceInterface {
    public function send(string $recipient, string $message): bool;
}

// Adapter for deprecated V1
class NotificationV1Adapter implements NotificationServiceInterface {
    private NotificationServiceV1 $service;

    public function send(string $recipient, string $message): bool {
        $statusCode = $this->service->sendMessage($recipient, $message);
        return $statusCode === 200;
    }
}

// Adapter for new V2 (different parameter shape)
class NotificationV2Adapter implements NotificationServiceInterface {
    private NotificationServiceV2 $service;

    public function send(string $recipient, string $message): bool {
        $result = $this->service->notify([
            'recipient' => $recipient,
            'message' => $message,
            'priority' => 'normal'
        ]);
        return $result['status'] === 'sent';
    }
}
```

## Common Mistakes

- **WRONG:** Adapting semantically incompatible behaviors (e.g., wrapping a notification service as a UserRepository)
  **RIGHT:** Only adapt when the underlying operations align in meaning

- **WRONG:** Putting business logic (validation, discounts, logging) in the adapter
  **RIGHT:** Keep adapters thin -- only interface translation; business logic belongs in a service layer

- **WRONG:** Letting service-specific exceptions leak through the adapter
  **RIGHT:** Catch service exceptions and translate to interface-appropriate behavior or interface-defined exceptions

- **WRONG:** Exposing service implementation details in the target interface (e.g., `$s3Bucket` parameter)
  **RIGHT:** Keep the target interface generic; hide service-specific details as adapter constructor args

- **WRONG:** Hard-coding the service inside the adapter constructor (`new Service()`)
  **RIGHT:** Inject dependencies so the adapter is testable and configurable

## Relationships

- Adapter vs **Decorator** -- Adapter changes the interface; Decorator keeps the same interface but adds behavior
- Adapter vs **Facade** -- Adapter translates one-to-one; Facade simplifies many-to-one
- Adapter vs **Proxy** -- Adapter changes the interface; Proxy keeps the same interface but controls access
- Adapter vs **Strategy** -- Nearly identical structure, but Strategy selects algorithms while Adapter integrates existing classes
- Adapter vs **Bridge** -- Adapter retrofits after design; Bridge separates abstraction/implementation from the start
- Often paired with **Factory Method** or **DI containers** to create and wire adapters
