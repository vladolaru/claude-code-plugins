# Strategy Pattern

## Quick Reference

| Aspect | Detail |
|--------|--------|
| **Intent** | Define a family of algorithms, encapsulate each one, and make them interchangeable at runtime |
| **Key Principle** | Separate **what** the client wants from **how** it gets accomplished |
| **Main Benefit** | Runtime algorithm selection without tight coupling |
| **Trade-off** | Increased number of classes |

## When to Use

- **External dependencies** -- database, API, file storage, payment gateways need swappable implementations
- **Multiple algorithms** -- sorting, compression, validation, formatting with >1 approach
- **Complex conditionals** -- large if/else or switch selecting between algorithms
- **Testing** -- need test doubles (mocks, stubs, spies) for client isolation
- **Configuration-driven behavior** -- user preferences, environment-specific implementations, feature flags
- **Cross-team boundaries** -- services from other teams or third-party libs that may change

**Size threshold:** Use for large classes with complex behavior or external resources. Skip for simple utility classes, pure functions, or trivial operations that rarely change.

## When NOT to Use

- **Single implementation** -- no variation exists or is likely; premature abstraction adds complexity
- **Simple utility functions** -- built-in functions suffice (`strtoupper`, `array_filter`)
- **Trivial operations** -- abstraction costs more than it provides
- **Performance-critical hot paths** -- polymorphism overhead is unacceptable

```php
// BAD: Strategy for a one-liner
interface StringCaseStrategy {
    public function convert(string $text): string;
}
// GOOD: Just use the function
$uppercased = strtoupper($text);
```

## WordPress/PHP Example: Payment Processing

```php
interface PaymentGateway {
    public function authorize(Order $order): AuthorizationResult;
    public function capture(string $authorizationId, float $amount): CaptureResult;
    public function refund(string $transactionId, float $amount): RefundResult;
}

class StripeGateway implements PaymentGateway {
    public function __construct(
        private string $apiKey,
        private StripeClient $client
    ) {}

    public function authorize(Order $order): AuthorizationResult {
        $intent = $this->client->paymentIntents->create([
            'amount' => $order->getTotal() * 100,
            'currency' => 'usd',
            'capture_method' => 'manual',
        ]);
        return new AuthorizationResult(
            success: true,
            authorizationId: $intent->id,
            message: 'Authorized via Stripe'
        );
    }

    public function capture(string $authorizationId, float $amount): CaptureResult {
        $intent = $this->client->paymentIntents->capture(
            $authorizationId,
            ['amount_to_capture' => $amount * 100]
        );
        return new CaptureResult(success: true, transactionId: $intent->id);
    }

    public function refund(string $transactionId, float $amount): RefundResult {
        $refund = $this->client->refunds->create([
            'payment_intent' => $transactionId,
            'amount' => $amount * 100,
        ]);
        return new RefundResult(success: true, refundId: $refund->id);
    }
}

class PayPalGateway implements PaymentGateway {
    public function __construct(
        private string $clientId,
        private string $clientSecret,
        private PayPalHttpClient $client
    ) {}

    public function authorize(Order $order): AuthorizationResult {
        $request = new OrdersCreateRequest();
        $request->body = [
            'intent' => 'AUTHORIZE',
            'purchase_units' => [[
                'amount' => ['currency_code' => 'USD', 'value' => $order->getTotal()]
            ]]
        ];
        $response = $this->client->execute($request);
        return new AuthorizationResult(
            success: true,
            authorizationId: $response->result->id,
            message: 'Authorized via PayPal'
        );
    }

    // capture() and refund() follow same adapter pattern...
}

// Context: delegates to strategy without knowing implementation
class OrderProcessor {
    public function __construct(private PaymentGateway $gateway) {}

    public function processOrder(Order $order): ProcessResult {
        $auth = $this->gateway->authorize($order);
        if (!$auth->success) {
            return ProcessResult::failed('Authorization failed');
        }
        try {
            $order->fulfill();
            $capture = $this->gateway->capture($auth->authorizationId, $order->getTotal());
            if ($capture->success) {
                $order->complete($capture->transactionId);
                return ProcessResult::success($capture->transactionId);
            }
            return ProcessResult::failed('Capture failed');
        } catch (Exception $e) {
            $this->gateway->refund($auth->authorizationId, $order->getTotal());
            return ProcessResult::failed($e->getMessage());
        }
    }
}

// Strategy resolved at runtime
$gateway = match($order->getPaymentMethod()) {
    'stripe' => new StripeGateway($stripeKey, $stripeClient),
    'paypal' => new PayPalGateway($paypalId, $paypalSecret, $paypalClient),
    default => throw new InvalidArgumentException('Unknown payment method')
};
$processor = new OrderProcessor($gateway);
$result = $processor->processOrder($order);
```

## JS/TS Example: Form Validation

```typescript
interface ValidationStrategy {
    validate(value: string): ValidationResult;
}

class EmailValidator implements ValidationStrategy {
    validate(value: string): ValidationResult {
        const pattern = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;
        const isValid = pattern.test(value);
        return { valid: isValid, errors: isValid ? [] : ['Invalid email format'] };
    }
}

class PhoneValidator implements ValidationStrategy {
    validate(value: string): ValidationResult {
        const cleaned = value.replace(/\D/g, '');
        const isValid = cleaned.length === 10 || cleaned.length === 11;
        return { valid: isValid, errors: isValid ? [] : ['Phone must be 10-11 digits'] };
    }
}

class RequiredValidator implements ValidationStrategy {
    validate(value: string): ValidationResult {
        const isValid = value.trim().length > 0;
        return { valid: isValid, errors: isValid ? [] : ['This field is required'] };
    }
}

// Client: composable validation via strategy collection
class FormField {
    private validators: ValidationStrategy[] = [];

    constructor(private name: string, private value: string) {}

    addValidator(validator: ValidationStrategy): this {
        this.validators.push(validator);
        return this;
    }

    validate(): ValidationResult {
        const errors: string[] = [];
        for (const validator of this.validators) {
            const result = validator.validate(this.value);
            if (!result.valid) errors.push(...result.errors);
        }
        return { valid: errors.length === 0, errors };
    }
}

// Usage
const emailField = new FormField('email', 'test@example.com')
    .addValidator(new RequiredValidator())
    .addValidator(new EmailValidator());
```

## Common Mistakes

- **WRONG:** Leaking implementation details into interface (`getRedisConnection()` on a cache interface)
  **RIGHT:** Interface defines behavior from client's perspective (`get`, `set`, `delete`, `has`)

- **WRONG:** Creating Strategy for everything (`StringUppercaseStrategy` wrapping `strtoupper`)
  **RIGHT:** Use built-in functions for trivial operations

- **WRONG:** Designing interface from implementation perspective (`executeQuery(string $sql)`)
  **RIGHT:** Design from client's needs (`findById(int $id): ?Product`)

- **WRONG:** Client depends on concrete type (`private StripeGateway $gateway`)
  **RIGHT:** Client depends on interface (`private PaymentGateway $gateway`)

- **WRONG:** Stateful strategies shared across instances (mutable class properties reset per call)
  **RIGHT:** Keep strategies stateless or scope state to method locals

- **WRONG:** Unvalidated strategy selection from user input (`new $_POST['method']()`)
  **RIGHT:** Validated `match()` expression with explicit allowed values

## Relationships

- **Strategy vs Command** -- Strategy: family of interchangeable algorithms (multi-method interface). Command: objectified action for undo/queue/log (single `execute()` method). Commands often *use* strategies internally.
- **Strategy vs State** -- Strategy: client selects algorithm. State: object transitions itself. Strategy = "do same thing differently." State = "do different things based on state."
- **Strategy vs Template Method** -- Strategy: composition, whole algorithm varies, runtime swap. Template Method: inheritance, steps within algorithm vary, compile-time fixed.
- **Factory Method** -- helps resolve which concrete strategy to instantiate
- **Dependency Injection** -- the natural mechanism for providing strategies to clients
