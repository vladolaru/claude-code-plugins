# Factory Design Patterns

## Overview

Factory patterns solve the fundamental problem of object instantiation: **How do you create objects without tightly coupling client code to concrete classes?**

The core insight is captured by Steve Ardalis's phrase: **"New is Glue"**. Any code that instantiates an object via a constructor directly (using `new`) becomes glued to that specific class type, creating unwanted dependencies.

Factory patterns encapsulate object creation logic, allowing client code to depend on interfaces rather than implementations. They complete the story for pluggable design patterns (Command, Strategy, Template Method, Adapter) by providing a mechanism to instantiate concrete classes without direct knowledge of their types.

## The Problem

### Direct Instantiation Creates Coupling

```php
// BAD: Client code is glued to ConcretePaymentProcessor
class CheckoutController {
    public function processPayment(): void {
        // Direct dependency on concrete class
        $processor = new StripePaymentProcessor();
        $processor->process($this->order);
    }
}
```

This creates a compile-time dependency where the client code must know the exact class type.

### Programming to Interface is Incomplete

```php
// INCOMPLETE: Declaration uses interface, but instantiation uses concrete class
class CheckoutController {
    public function processPayment(): void {
        // Still calling new on concrete class
        PaymentProcessor $processor = new StripePaymentProcessor();
        $processor->process($this->order);
    }
}
```

The GoF's first design principle states: **"Program to an interface, not an implementation."** However, this principle alone doesn't solve the instantiation problem. Factory patterns complete the picture.

## Pattern Categories

Factory patterns come in three primary forms, each offering different levels of flexibility and abstraction:

1. **Factory Method** - Static method within the interface hierarchy
2. **Factory Class** - Separate static class for object creation
3. **Abstract Factory** - Interface-based factory for maximum flexibility

## Factory Method

### Intent

Provide a static method within the interface hierarchy that encapsulates object instantiation logic, returning interface references without exposing concrete class types.

### Structure

```
┌─────────────────────────────────────┐
│       Client Application             │
└────────────┬────────────────────────┘
             │ uses
             ↓
┌─────────────────────────────────────┐
│   <<interface>> PaymentProcessor    │
│  + static acquire(string): self     │◄──────────┐
│  + process(Order): Result           │           │ creates
└─────────────┬───────────────────────┘           │
              △                                    │
              │ implements                         │
    ┌─────────┴──────────┐                       │
    │                    │                        │
┌───┴──────────────┐ ┌──┴─────────────────┐     │
│ StripeProcessor  │ │ PayPalProcessor    │     │
│                  │ │                    │     │
└──────────────────┘ └────────────────────┘     │
             △                    △              │
             └────────────────────┴──────────────┘
```

### Implementation

```php
<?php

interface PaymentProcessor {
    /**
     * Factory method to acquire payment processor instance.
     *
     * @param string $type Processor type: 'stripe', 'paypal', 'square'
     * @return self Payment processor instance
     * @throws InvalidArgumentException If processor type is unknown
     */
    public static function acquire(string $type): self;

    /**
     * Process payment for given order.
     *
     * @param Order $order Order to process
     * @return ProcessingResult Result of payment processing
     */
    public function process(Order $order): ProcessingResult;
}

class StripeProcessor implements PaymentProcessor {
    private string $apiKey;

    public function __construct(string $apiKey) {
        $this->apiKey = $apiKey;
    }

    public static function acquire(string $type): PaymentProcessor {
        switch ($type) {
            case 'stripe':
                return new self(getenv('STRIPE_API_KEY'));
            case 'paypal':
                return new PayPalProcessor(getenv('PAYPAL_CLIENT_ID'));
            case 'square':
                return new SquareProcessor(getenv('SQUARE_ACCESS_TOKEN'));
            default:
                throw new InvalidArgumentException("Unknown processor type: {$type}");
        }
    }

    public function process(Order $order): ProcessingResult {
        // Stripe-specific processing logic
        return new ProcessingResult(true, 'Payment processed via Stripe');
    }
}

class PayPalProcessor implements PaymentProcessor {
    private string $clientId;

    public function __construct(string $clientId) {
        $this->clientId = $clientId;
    }

    public static function acquire(string $type): PaymentProcessor {
        // Delegates to StripeProcessor's implementation
        return StripeProcessor::acquire($type);
    }

    public function process(Order $order): ProcessingResult {
        // PayPal-specific processing logic
        return new ProcessingResult(true, 'Payment processed via PayPal');
    }
}

class SquareProcessor implements PaymentProcessor {
    private string $accessToken;

    public function __construct(string $accessToken) {
        $this->accessToken = $accessToken;
    }

    public static function acquire(string $type): PaymentProcessor {
        // Delegates to StripeProcessor's implementation
        return StripeProcessor::acquire($type);
    }

    public function process(Order $order): ProcessingResult {
        // Square-specific processing logic
        return new ProcessingResult(true, 'Payment processed via Square');
    }
}

// Client usage
class CheckoutController {
    public function processPayment(Order $order, string $processorType): void {
        // No direct dependency on concrete classes
        $processor = PaymentProcessor::acquire($processorType);
        $result = $processor->process($order);

        if ($result->isSuccessful()) {
            $this->completeOrder($order);
        }
    }
}
```

### Characteristics

**Advantages:**
- Simple and straightforward
- No additional classes required
- Factory method travels with the interface

**Disadvantages:**
- Introduces a base class solely for instantiation purposes
- PHP/Java don't support multiple inheritance, limiting flexibility
- Mixes interface contract concerns with object creation concerns
- All implementations must delegate to a common factory method

**When to Use:**
- Simple scenarios with few concrete implementations
- When you want creation logic bundled with the interface
- Legacy codebases where adding factory classes would be disruptive

## Factory Class

### Intent

Separate object creation concerns from interface contracts by providing a dedicated factory class with static methods that encapsulate instantiation logic.

### Structure

```
┌─────────────────────────────────────┐
│       Client Application             │
└─────┬───────────────────────┬───────┘
      │ uses                  │ uses
      ↓                       ↓
┌──────────────────────┐ ┌───────────────────────────┐
│ PaymentProcessor     │ │ PaymentProcessorFactory  │
│  + process(): Result │ │  + static create(): self  │
└─────────┬────────────┘ └─────────┬─────────────────┘
          △                         │ creates
          │ implements              ↓
    ┌─────┴──────────┐      ┌──────────────┐
    │                │      │              │
┌───┴──────────┐ ┌──┴─────────────┐       │
│ Stripe       │ │ PayPal         │       │
└──────────────┘ └────────────────┘       │
     △                    △                │
     └────────────────────┴────────────────┘
```

### Implementation

```php
<?php

/**
 * Payment processor interface.
 */
interface PaymentProcessor {
    public function process(Order $order): ProcessingResult;
    public function refund(string $transactionId, float $amount): ProcessingResult;
    public function validateCredentials(): bool;
}

/**
 * Stripe payment processor implementation.
 */
class StripeProcessor implements PaymentProcessor {
    private string $apiKey;
    private string $webhookSecret;

    public function __construct(string $apiKey, string $webhookSecret = '') {
        $this->apiKey = $apiKey;
        $this->webhookSecret = $webhookSecret;
    }

    public function process(Order $order): ProcessingResult {
        // Stripe API integration
        $charge = [
            'amount' => $order->getTotal() * 100, // Convert to cents
            'currency' => 'usd',
            'source' => $order->getPaymentToken(),
            'description' => "Order #{$order->getId()}",
        ];

        // Simulate API call
        return new ProcessingResult(
            true,
            'stripe_ch_' . uniqid(),
            'Payment processed successfully'
        );
    }

    public function refund(string $transactionId, float $amount): ProcessingResult {
        // Stripe refund logic
        return new ProcessingResult(true, $transactionId, 'Refund processed');
    }

    public function validateCredentials(): bool {
        // Validate Stripe API key
        return !empty($this->apiKey) && strlen($this->apiKey) > 20;
    }
}

/**
 * PayPal payment processor implementation.
 */
class PayPalProcessor implements PaymentProcessor {
    private string $clientId;
    private string $clientSecret;
    private bool $sandbox;

    public function __construct(string $clientId, string $clientSecret, bool $sandbox = false) {
        $this->clientId = $clientId;
        $this->clientSecret = $clientSecret;
        $this->sandbox = $sandbox;
    }

    public function process(Order $order): ProcessingResult {
        // PayPal SDK integration
        $payment = [
            'intent' => 'sale',
            'payer' => ['payment_method' => 'paypal'],
            'transactions' => [[
                'amount' => [
                    'total' => $order->getTotal(),
                    'currency' => 'USD',
                ],
            ]],
        ];

        return new ProcessingResult(
            true,
            'paypal_' . uniqid(),
            'Payment processed via PayPal'
        );
    }

    public function refund(string $transactionId, float $amount): ProcessingResult {
        // PayPal refund logic
        return new ProcessingResult(true, $transactionId, 'Refund processed');
    }

    public function validateCredentials(): bool {
        return !empty($this->clientId) && !empty($this->clientSecret);
    }
}

/**
 * Square payment processor implementation.
 */
class SquareProcessor implements PaymentProcessor {
    private string $accessToken;
    private string $locationId;

    public function __construct(string $accessToken, string $locationId) {
        $this->accessToken = $accessToken;
        $this->locationId = $locationId;
    }

    public function process(Order $order): ProcessingResult {
        // Square API integration
        $payment = [
            'source_id' => $order->getPaymentToken(),
            'amount_money' => [
                'amount' => $order->getTotal() * 100,
                'currency' => 'USD',
            ],
            'location_id' => $this->locationId,
        ];

        return new ProcessingResult(
            true,
            'square_' . uniqid(),
            'Payment processed via Square'
        );
    }

    public function refund(string $transactionId, float $amount): ProcessingResult {
        return new ProcessingResult(true, $transactionId, 'Refund processed');
    }

    public function validateCredentials(): bool {
        return !empty($this->accessToken) && !empty($this->locationId);
    }
}

/**
 * Factory class for creating payment processors.
 *
 * Separates object creation concerns from business logic.
 */
class PaymentProcessorFactory {
    /**
     * Create payment processor instance.
     *
     * @param string $type Processor type: 'stripe', 'paypal', 'square'
     * @param array $config Configuration array for the processor
     * @return PaymentProcessor Configured processor instance
     * @throws InvalidArgumentException If processor type is unknown
     */
    public static function create(string $type, array $config = []): PaymentProcessor {
        switch ($type) {
            case 'stripe':
                return new StripeProcessor(
                    $config['api_key'] ?? getenv('STRIPE_API_KEY'),
                    $config['webhook_secret'] ?? getenv('STRIPE_WEBHOOK_SECRET')
                );

            case 'paypal':
                return new PayPalProcessor(
                    $config['client_id'] ?? getenv('PAYPAL_CLIENT_ID'),
                    $config['client_secret'] ?? getenv('PAYPAL_CLIENT_SECRET'),
                    $config['sandbox'] ?? false
                );

            case 'square':
                return new SquareProcessor(
                    $config['access_token'] ?? getenv('SQUARE_ACCESS_TOKEN'),
                    $config['location_id'] ?? getenv('SQUARE_LOCATION_ID')
                );

            default:
                throw new InvalidArgumentException("Unknown processor type: {$type}");
        }
    }

    /**
     * Create processor from configuration array.
     *
     * @param array $config Must include 'type' key
     * @return PaymentProcessor Configured processor instance
     */
    public static function createFromConfig(array $config): PaymentProcessor {
        if (!isset($config['type'])) {
            throw new InvalidArgumentException('Configuration must include "type" key');
        }

        $type = $config['type'];
        unset($config['type']);

        return self::create($type, $config);
    }

    /**
     * Get list of supported processor types.
     *
     * @return array List of supported types
     */
    public static function getSupportedTypes(): array {
        return ['stripe', 'paypal', 'square'];
    }
}

// Client usage
class CheckoutController {
    public function processPayment(Order $order): void {
        // Factory class separates creation from usage
        $processor = PaymentProcessorFactory::create(
            $order->getPreferredPaymentMethod(),
            ['sandbox' => WP_DEBUG]
        );

        if (!$processor->validateCredentials()) {
            throw new RuntimeException('Invalid payment processor credentials');
        }

        $result = $processor->process($order);

        if ($result->isSuccessful()) {
            $order->setTransactionId($result->getTransactionId());
            $this->completeOrder($order);
        } else {
            $this->handlePaymentFailure($order, $result);
        }
    }

    public function processRefund(Order $order, float $amount): void {
        // Same factory, different context
        $processor = PaymentProcessorFactory::create(
            $order->getPaymentMethod()
        );

        $result = $processor->refund($order->getTransactionId(), $amount);

        if ($result->isSuccessful()) {
            $order->recordRefund($amount);
        }
    }
}

// Configuration-driven usage
class PaymentService {
    private PaymentProcessor $defaultProcessor;

    public function __construct() {
        // Create from application configuration
        $this->defaultProcessor = PaymentProcessorFactory::createFromConfig([
            'type' => get_option('default_payment_processor', 'stripe'),
            'api_key' => get_option('payment_api_key'),
            'webhook_secret' => get_option('payment_webhook_secret'),
        ]);
    }

    public function processWithDefault(Order $order): ProcessingResult {
        return $this->defaultProcessor->process($order);
    }
}
```

### Characteristics

**Advantages:**
- Clear separation of concerns (interface contract vs. object creation)
- No need for inheritance solely for instantiation
- Factory can be modified independently of interface implementations
- Can implement additional factory methods for different creation strategies
- Easier to test (mock the factory separately)

**Disadvantages:**
- Additional class to maintain
- Slightly more verbose than Factory Method

**When to Use:**
- Most production scenarios
- When you need clear separation between interface and creation logic
- When creation logic is complex or may change independently
- WordPress plugin development (standard pattern)

**WordPress Specific Considerations:**

```php
<?php
/**
 * WordPress-specific factory for payment processors.
 */
class WC_Payment_Processor_Factory {
    /**
     * Create processor from WooCommerce payment gateway settings.
     *
     * @param string $gateway_id WooCommerce gateway ID
     * @return PaymentProcessor Configured processor
     */
    public static function from_gateway(string $gateway_id): PaymentProcessor {
        $gateways = WC()->payment_gateways()->payment_gateways();

        if (!isset($gateways[$gateway_id])) {
            throw new InvalidArgumentException("Gateway not found: {$gateway_id}");
        }

        $gateway = $gateways[$gateway_id];
        $settings = $gateway->settings;

        // Map gateway ID to processor type
        $processor_map = [
            'stripe' => 'stripe',
            'paypal' => 'paypal',
            'square' => 'square',
        ];

        $type = $processor_map[$gateway_id] ?? $gateway_id;

        return PaymentProcessorFactory::create($type, [
            'api_key' => $settings['api_key'] ?? '',
            'webhook_secret' => $settings['webhook_secret'] ?? '',
            'sandbox' => $gateway->testmode === 'yes',
        ]);
    }
}
```

## Abstract Factory

### Intent

Provide an interface for creating families of related or dependent objects without specifying their concrete classes. Abstract Factory adds a layer of indirection by making the factory itself implement an interface, enabling runtime selection of factory implementations.

### Structure

```
                    ┌─────────────────────────┐
                    │   Client Application    │
                    └──────────┬──────────────┘
                               │ uses
                               ↓
                    ┌──────────────────────────┐
                    │ ProcessorFactory         │
                    │  + acquire(): Processor  │◄─────────┐
                    └──────────┬───────────────┘          │
                               △                          │
                               │ implements               │
              ┌────────────────┴─────────────┐            │
              │                              │            │
    ┌─────────┴──────────┐      ┌───────────┴────────┐   │
    │ ProductionFactory  │      │ TestFactory        │   │
    └────────────────────┘      └────────────────────┘   │
                                                          │
    ════════════════════════════════════════════════════════
    Business Logic Abstraction (above line)
    Implementation Details (below line)
    ════════════════════════════════════════════════════════
                                                          │
                    ┌──────────────────────────┐          │
                    │ <<interface>>            │          │
                    │ PaymentProcessor         │◄─────────┘
                    └──────────┬───────────────┘
                               △
                               │ implements
              ┌────────────────┴─────────────┐
              │                              │
    ┌─────────┴──────────┐      ┌───────────┴────────┐
    │ StripeProcessor    │      │ MockProcessor      │
    └────────────────────┘      └────────────────────┘
```

### Implementation

```php
<?php

/**
 * Payment processor interface.
 */
interface PaymentProcessor {
    public function process(Order $order): ProcessingResult;
    public function refund(string $transactionId, float $amount): ProcessingResult;
    public function validateCredentials(): bool;
}

/**
 * Abstract factory interface for creating payment processors.
 *
 * This interface defines the contract for creating payment processors
 * without exposing concrete implementation details to client code.
 */
interface ProcessorFactory {
    /**
     * Acquire payment processor instance.
     *
     * @param string $type Processor type identifier
     * @param array $config Configuration options
     * @return PaymentProcessor Configured processor instance
     * @throws InvalidArgumentException If type is not supported
     */
    public function acquire(string $type, array $config = []): PaymentProcessor;

    /**
     * Check if factory supports given processor type.
     *
     * @param string $type Processor type to check
     * @return bool True if supported
     */
    public function supports(string $type): bool;

    /**
     * Get list of supported processor types.
     *
     * @return array List of supported types
     */
    public function getSupportedTypes(): array;
}

/**
 * Production factory implementation.
 *
 * Creates real payment processor instances for production use.
 */
class ProductionProcessorFactory implements ProcessorFactory {
    private array $config;

    public function __construct(array $config = []) {
        $this->config = $config;
    }

    public function acquire(string $type, array $config = []): PaymentProcessor {
        // Merge instance config with method config
        $finalConfig = array_merge($this->config, $config);

        switch ($type) {
            case 'stripe':
                return new StripeProcessor(
                    $finalConfig['stripe_api_key'] ?? getenv('STRIPE_API_KEY'),
                    $finalConfig['stripe_webhook_secret'] ?? getenv('STRIPE_WEBHOOK_SECRET')
                );

            case 'paypal':
                return new PayPalProcessor(
                    $finalConfig['paypal_client_id'] ?? getenv('PAYPAL_CLIENT_ID'),
                    $finalConfig['paypal_client_secret'] ?? getenv('PAYPAL_CLIENT_SECRET'),
                    $finalConfig['sandbox'] ?? false
                );

            case 'square':
                return new SquareProcessor(
                    $finalConfig['square_access_token'] ?? getenv('SQUARE_ACCESS_TOKEN'),
                    $finalConfig['square_location_id'] ?? getenv('SQUARE_LOCATION_ID')
                );

            default:
                throw new InvalidArgumentException("Unsupported processor type: {$type}");
        }
    }

    public function supports(string $type): bool {
        return in_array($type, $this->getSupportedTypes(), true);
    }

    public function getSupportedTypes(): array {
        return ['stripe', 'paypal', 'square'];
    }
}

/**
 * Test factory implementation.
 *
 * Creates mock payment processor instances for testing.
 * Always returns successful results without actual API calls.
 */
class TestProcessorFactory implements ProcessorFactory {
    private bool $shouldSucceed;
    private array $mockResults;

    public function __construct(bool $shouldSucceed = true, array $mockResults = []) {
        $this->shouldSucceed = $shouldSucceed;
        $this->mockResults = $mockResults;
    }

    public function acquire(string $type, array $config = []): PaymentProcessor {
        return new MockProcessor(
            $type,
            $this->shouldSucceed,
            $this->mockResults[$type] ?? []
        );
    }

    public function supports(string $type): bool {
        // Test factory supports all processor types
        return true;
    }

    public function getSupportedTypes(): array {
        return ['stripe', 'paypal', 'square', 'test'];
    }
}

/**
 * Mock payment processor for testing.
 */
class MockProcessor implements PaymentProcessor {
    private string $type;
    private bool $shouldSucceed;
    private array $mockResults;

    public function __construct(string $type, bool $shouldSucceed = true, array $mockResults = []) {
        $this->type = $type;
        $this->shouldSucceed = $shouldSucceed;
        $this->mockResults = $mockResults;
    }

    public function process(Order $order): ProcessingResult {
        if ($this->shouldSucceed) {
            return new ProcessingResult(
                true,
                "mock_{$this->type}_" . uniqid(),
                $this->mockResults['process_message'] ?? 'Mock payment successful'
            );
        }

        return new ProcessingResult(
            false,
            '',
            $this->mockResults['error_message'] ?? 'Mock payment failed'
        );
    }

    public function refund(string $transactionId, float $amount): ProcessingResult {
        return new ProcessingResult(
            $this->shouldSucceed,
            $transactionId,
            $this->shouldSucceed ? 'Mock refund successful' : 'Mock refund failed'
        );
    }

    public function validateCredentials(): bool {
        return $this->shouldSucceed;
    }
}

/**
 * Real implementations of payment processors.
 */
class StripeProcessor implements PaymentProcessor {
    private string $apiKey;
    private string $webhookSecret;

    public function __construct(string $apiKey, string $webhookSecret = '') {
        $this->apiKey = $apiKey;
        $this->webhookSecret = $webhookSecret;
    }

    public function process(Order $order): ProcessingResult {
        // Real Stripe API integration
        try {
            // \Stripe\Charge::create([...])
            return new ProcessingResult(
                true,
                'stripe_ch_' . uniqid(),
                'Payment processed via Stripe'
            );
        } catch (Exception $e) {
            return new ProcessingResult(false, '', $e->getMessage());
        }
    }

    public function refund(string $transactionId, float $amount): ProcessingResult {
        // Real Stripe refund logic
        return new ProcessingResult(true, $transactionId, 'Refund processed');
    }

    public function validateCredentials(): bool {
        return !empty($this->apiKey) && strlen($this->apiKey) > 20;
    }
}

class PayPalProcessor implements PaymentProcessor {
    private string $clientId;
    private string $clientSecret;
    private bool $sandbox;

    public function __construct(string $clientId, string $clientSecret, bool $sandbox = false) {
        $this->clientId = $clientId;
        $this->clientSecret = $clientSecret;
        $this->sandbox = $sandbox;
    }

    public function process(Order $order): ProcessingResult {
        // Real PayPal SDK integration
        return new ProcessingResult(
            true,
            'paypal_' . uniqid(),
            'Payment processed via PayPal'
        );
    }

    public function refund(string $transactionId, float $amount): ProcessingResult {
        return new ProcessingResult(true, $transactionId, 'Refund processed');
    }

    public function validateCredentials(): bool {
        return !empty($this->clientId) && !empty($this->clientSecret);
    }
}

class SquareProcessor implements PaymentProcessor {
    private string $accessToken;
    private string $locationId;

    public function __construct(string $accessToken, string $locationId) {
        $this->accessToken = $accessToken;
        $this->locationId = $locationId;
    }

    public function process(Order $order): ProcessingResult {
        // Real Square API integration
        return new ProcessingResult(
            true,
            'square_' . uniqid(),
            'Payment processed via Square'
        );
    }

    public function refund(string $transactionId, float $amount): ProcessingResult {
        return new ProcessingResult(true, $transactionId, 'Refund processed');
    }

    public function validateCredentials(): bool {
        return !empty($this->accessToken) && !empty($this->locationId);
    }
}

/**
 * Supporting classes.
 */
class ProcessingResult {
    private bool $successful;
    private string $transactionId;
    private string $message;

    public function __construct(bool $successful, string $transactionId, string $message) {
        $this->successful = $successful;
        $this->transactionId = $transactionId;
        $this->message = $message;
    }

    public function isSuccessful(): bool {
        return $this->successful;
    }

    public function getTransactionId(): string {
        return $this->transactionId;
    }

    public function getMessage(): string {
        return $this->message;
    }
}

// Client usage - Production
class CheckoutController {
    private ProcessorFactory $factory;

    /**
     * Constructor accepts factory interface, not concrete implementation.
     * Factory is injected via dependency injection (see next pattern).
     */
    public function __construct(ProcessorFactory $factory) {
        $this->factory = $factory;
    }

    public function processPayment(Order $order): void {
        $processorType = $order->getPreferredPaymentMethod();

        if (!$this->factory->supports($processorType)) {
            throw new RuntimeException("Unsupported payment method: {$processorType}");
        }

        // Factory interface hides implementation details
        $processor = $this->factory->acquire($processorType);

        if (!$processor->validateCredentials()) {
            throw new RuntimeException('Invalid payment processor credentials');
        }

        $result = $processor->process($order);

        if ($result->isSuccessful()) {
            $order->setTransactionId($result->getTransactionId());
            $this->completeOrder($order);
        } else {
            $this->handlePaymentFailure($order, $result);
        }
    }
}

// Production bootstrap
function bootstrap_production(): CheckoutController {
    $factory = new ProductionProcessorFactory([
        'stripe_api_key' => getenv('STRIPE_API_KEY'),
        'stripe_webhook_secret' => getenv('STRIPE_WEBHOOK_SECRET'),
        'sandbox' => false,
    ]);

    return new CheckoutController($factory);
}

// Test bootstrap
function bootstrap_test(): CheckoutController {
    // Same client code, different factory implementation
    $factory = new TestProcessorFactory(
        shouldSucceed: true,
        mockResults: [
            'stripe' => ['process_message' => 'Test Stripe payment'],
            'paypal' => ['process_message' => 'Test PayPal payment'],
        ]
    );

    return new CheckoutController($factory);
}

// WordPress plugin integration
class WC_Checkout_Manager {
    private ProcessorFactory $factory;

    public function __construct() {
        // Factory selection based on environment
        if (defined('WP_ENV') && WP_ENV === 'test') {
            $this->factory = new TestProcessorFactory();
        } else {
            $this->factory = new ProductionProcessorFactory([
                'sandbox' => get_option('wc_payment_sandbox_mode', false),
            ]);
        }
    }

    public function process_checkout(WC_Order $wc_order): void {
        $controller = new CheckoutController($this->factory);
        $order = Order::from_wc_order($wc_order);
        $controller->processPayment($order);
    }
}
```

### Characteristics

**Advantages:**
- Maximum flexibility - swap entire factory implementations at runtime
- Clear architectural boundary between business logic and implementation details
- Perfect for testing - easily swap production factories with test factories
- Enables consistent object families (ensures related objects work together)
- Supports multiple creation strategies without changing client code

**Disadvantages:**
- Most complex of the three factory patterns
- Additional interface and implementation classes
- May be overkill for simple scenarios

**When to Use:**
- You need to swap entire creation strategies (production vs. test, cloud vs. local)
- Multiple related objects need to be created consistently as a family
- Plugin systems where creation logic is provided by third parties
- Testing scenarios where you need complete control over object creation
- Large applications with clear architectural boundaries

### The Architectural Boundary

The curved line in the structure diagram represents a critical architectural boundary:

**Above the line (Business Logic):**
- Client application code
- Interface contracts
- Business rules
- Use cases

**Below the line (Implementation Details):**
- Concrete factory implementations
- Concrete processor implementations
- External API integrations
- Framework-specific code

This separation provides tremendous freedom to change implementation details without affecting business logic. You can swap database implementations, switch from REST to GraphQL APIs, or replace third-party services entirely - all without modifying client code.

## Combining Factory Patterns

Factory patterns are not mutually exclusive. They can be combined and nested for complex creation scenarios.

### Factory Method within Factory Class

```php
<?php

interface PaymentProcessor {
    public function process(Order $order): ProcessingResult;
}

class PaymentProcessorFactory {
    /**
     * Create processor using Factory Class pattern.
     */
    public static function create(string $type): PaymentProcessor {
        switch ($type) {
            case 'stripe':
                // Each case could use Factory Method
                return StripeProcessor::acquire();
            case 'paypal':
                return PayPalProcessor::acquire();
            default:
                throw new InvalidArgumentException("Unknown type: {$type}");
        }
    }
}

class StripeProcessor implements PaymentProcessor {
    /**
     * Factory Method for acquiring StripeProcessor instances.
     */
    public static function acquire(): self {
        // Singleton-like behavior within Factory Method
        static $instance = null;
        if ($instance === null) {
            $instance = new self(getenv('STRIPE_API_KEY'));
        }
        return $instance;
    }

    private function __construct(private string $apiKey) {}

    public function process(Order $order): ProcessingResult {
        // Implementation
    }
}
```

### Abstract Factory with Multiple Product Families

```php
<?php

/**
 * Abstract Factory that creates families of related objects.
 */
interface PaymentSystemFactory {
    public function createProcessor(): PaymentProcessor;
    public function createLogger(): PaymentLogger;
    public function createNotifier(): PaymentNotifier;
}

/**
 * Stripe family factory.
 */
class StripeSystemFactory implements PaymentSystemFactory {
    public function createProcessor(): PaymentProcessor {
        return new StripeProcessor(getenv('STRIPE_API_KEY'));
    }

    public function createLogger(): PaymentLogger {
        return new StripeLogger('/var/log/stripe.log');
    }

    public function createNotifier(): PaymentNotifier {
        return new StripeWebhookNotifier(getenv('STRIPE_WEBHOOK_URL'));
    }
}

/**
 * PayPal family factory.
 */
class PayPalSystemFactory implements PaymentSystemFactory {
    public function createProcessor(): PaymentProcessor {
        return new PayPalProcessor(
            getenv('PAYPAL_CLIENT_ID'),
            getenv('PAYPAL_CLIENT_SECRET')
        );
    }

    public function createLogger(): PaymentLogger {
        return new PayPalLogger('/var/log/paypal.log');
    }

    public function createNotifier(): PaymentNotifier {
        return new PayPalIpnNotifier(getenv('PAYPAL_IPN_URL'));
    }
}

/**
 * Client code that uses consistent family of objects.
 */
class PaymentService {
    private PaymentProcessor $processor;
    private PaymentLogger $logger;
    private PaymentNotifier $notifier;

    public function __construct(PaymentSystemFactory $factory) {
        // All objects come from same factory - guaranteed consistency
        $this->processor = $factory->createProcessor();
        $this->logger = $factory->createLogger();
        $this->notifier = $factory->createNotifier();
    }

    public function process(Order $order): void {
        $this->logger->info("Processing order {$order->getId()}");
        $result = $this->processor->process($order);

        if ($result->isSuccessful()) {
            $this->notifier->notifySuccess($order, $result);
        } else {
            $this->logger->error("Failed: {$result->getMessage()}");
            $this->notifier->notifyFailure($order, $result);
        }
    }
}
```

## Common Pitfalls and Solutions

### Pitfall 1: Breaking Encapsulation with Method Names

**Problem:** Using creation-mechanism-specific method names breaks encapsulation.

```php
// BAD: Method name reveals creation mechanism
$processor = ProcessorFactory::createNew('stripe');
$singleton = Logger::getInstance();
$copy = $document->clone();
```

**Solution:** Use consistent, mechanism-agnostic naming.

```php
// GOOD: Method name focuses on intent, not mechanism
$processor = ProcessorFactory::acquire('stripe');
$logger = LoggerFactory::acquire();
$copy = DocumentFactory::acquire($document);
```

### Pitfall 2: Memory Leaks with Singletons and Flyweights

**Problem:** Singleton and Flyweight patterns can leak memory since objects are never released.

```php
// Memory leak - instances never released
class Cache {
    private static array $instances = [];

    public static function get(string $key): mixed {
        if (!isset(self::$instances[$key])) {
            self::$instances[$key] = new CacheEntry($key);
        }
        return self::$instances[$key];
    }
}
```

**Solution:** Use weak references in PHP 7.4+.

```php
class Cache {
    private static array $instances = [];

    public static function get(string $key): mixed {
        if (!isset(self::$instances[$key]) || self::$instances[$key]->get() === null) {
            $entry = new CacheEntry($key);
            self::$instances[$key] = WeakReference::create($entry);
            return $entry;
        }
        return self::$instances[$key]->get();
    }
}
```

### Pitfall 3: Factory Knows Too Much

**Problem:** Factory becomes a God Object with too many responsibilities.

```php
// BAD: Factory knows about database, config, logging, etc.
class UserFactory {
    public static function create(array $data): User {
        $db = Database::connect();
        $config = Config::load();
        $logger = new Logger();

        $user = new User($data);
        $db->save($user);
        $logger->info("Created user {$user->getId()}");
        Config::set('last_user_id', $user->getId());

        return $user;
    }
}
```

**Solution:** Keep factory focused on object creation. Delegate other concerns.

```php
// GOOD: Factory only creates objects
class UserFactory {
    public static function create(array $data): User {
        return new User(
            $data['id'] ?? null,
            $data['name'],
            $data['email']
        );
    }

    public static function createFromDatabase(int $id, PDO $db): User {
        $row = $db->query("SELECT * FROM users WHERE id = {$id}")->fetch();
        return self::create($row);
    }
}

// Other concerns handled by appropriate services
class UserService {
    public function registerUser(array $data): User {
        $user = UserFactory::create($data);
        $this->repository->save($user);
        $this->logger->info("Created user {$user->getId()}");
        $this->eventDispatcher->dispatch(new UserRegistered($user));
        return $user;
    }
}
```

### Pitfall 4: Tight Coupling via Type Parameters

**Problem:** Using enums or constants for type parameters creates coupling.

```php
// BAD: Client code knows about processor types
class CheckoutController {
    public function processPayment(Order $order): void {
        // Tight coupling to ProcessorType enum
        $processor = ProcessorFactory::create(ProcessorType::STRIPE);
        $processor->process($order);
    }
}
```

**Solution:** Accept type as string or derive from context.

```php
// GOOD: Type determined by order data, not hardcoded
class CheckoutController {
    public function processPayment(Order $order): void {
        // Type comes from order context
        $processor = ProcessorFactory::create($order->getPaymentMethod());
        $processor->process($order);
    }
}

// BETTER: Abstract factory removes type knowledge entirely
class CheckoutController {
    public function __construct(private ProcessorFactory $factory) {}

    public function processPayment(Order $order): void {
        // No type knowledge at all - factory determines everything
        $processor = $this->factory->acquire($order->getPaymentMethod());
        $processor->process($order);
    }
}
```

## WordPress Integration Patterns

### WooCommerce Payment Gateway Factory

```php
<?php
/**
 * Factory for WooCommerce payment gateways.
 */
class WC_Gateway_Factory {
    /**
     * Create processor from WooCommerce gateway instance.
     */
    public static function acquire(WC_Payment_Gateway $gateway): PaymentProcessor {
        switch ($gateway->id) {
            case 'stripe':
                return new StripeProcessor(
                    $gateway->get_option('api_key'),
                    $gateway->get_option('webhook_secret')
                );

            case 'paypal':
                return new PayPalProcessor(
                    $gateway->get_option('client_id'),
                    $gateway->get_option('client_secret'),
                    $gateway->testmode === 'yes'
                );

            default:
                throw new InvalidArgumentException(
                    "Unsupported gateway: {$gateway->id}"
                );
        }
    }

    /**
     * Create processor from gateway ID.
     */
    public static function acquire_by_id(string $gateway_id): PaymentProcessor {
        $gateways = WC()->payment_gateways()->payment_gateways();

        if (!isset($gateways[$gateway_id])) {
            throw new InvalidArgumentException("Gateway not found: {$gateway_id}");
        }

        return self::acquire($gateways[$gateway_id]);
    }
}
```

### Plugin-Based Abstract Factory

```php
<?php
/**
 * Extensible factory that allows plugins to register processors.
 */
class WP_Payment_Processor_Factory implements ProcessorFactory {
    private static array $registered_processors = [];

    /**
     * Register a processor creator callback.
     */
    public static function register(string $type, callable $creator): void {
        self::$registered_processors[$type] = $creator;
    }

    public function acquire(string $type, array $config = []): PaymentProcessor {
        if (!isset(self::$registered_processors[$type])) {
            throw new InvalidArgumentException("Unknown processor type: {$type}");
        }

        $creator = self::$registered_processors[$type];
        return $creator($config);
    }

    public function supports(string $type): bool {
        return isset(self::$registered_processors[$type]);
    }

    public function getSupportedTypes(): array {
        return array_keys(self::$registered_processors);
    }
}

// Plugin registration
add_action('plugins_loaded', function() {
    WP_Payment_Processor_Factory::register('stripe', function($config) {
        return new StripeProcessor(
            $config['api_key'] ?? get_option('stripe_api_key'),
            $config['webhook_secret'] ?? get_option('stripe_webhook_secret')
        );
    });

    WP_Payment_Processor_Factory::register('paypal', function($config) {
        return new PayPalProcessor(
            $config['client_id'] ?? get_option('paypal_client_id'),
            $config['client_secret'] ?? get_option('paypal_client_secret'),
            $config['sandbox'] ?? get_option('paypal_sandbox') === 'yes'
        );
    });
});

// Third-party plugin can add support
add_action('plugins_loaded', function() {
    WP_Payment_Processor_Factory::register('custom_gateway', function($config) {
        return new CustomGatewayProcessor($config);
    });
}, 20); // Later priority
```

## Testing with Factory Patterns

### Test Doubles via Abstract Factory

```php
<?php

/**
 * Test case demonstrating Abstract Factory for testing.
 */
class CheckoutTest extends WP_UnitTestCase {
    private CheckoutController $controller;
    private MockProcessorFactory $mockFactory;

    public function setUp(): void {
        parent::setUp();

        // Inject test factory
        $this->mockFactory = new MockProcessorFactory();
        $this->controller = new CheckoutController($this->mockFactory);
    }

    public function test_successful_payment_completes_order(): void {
        // Configure mock to succeed
        $this->mockFactory->setSuccessful(true);

        $order = $this->create_test_order();
        $this->controller->processPayment($order);

        $this->assertTrue($order->isCompleted());
        $this->assertNotEmpty($order->getTransactionId());
    }

    public function test_failed_payment_marks_order_as_failed(): void {
        // Configure mock to fail
        $this->mockFactory->setSuccessful(false);

        $order = $this->create_test_order();
        $this->controller->processPayment($order);

        $this->assertTrue($order->isFailed());
        $this->assertEmpty($order->getTransactionId());
    }

    public function test_network_error_during_payment(): void {
        // Configure mock to throw exception
        $this->mockFactory->setException(new NetworkException('Connection timeout'));

        $order = $this->create_test_order();

        $this->expectException(NetworkException::class);
        $this->controller->processPayment($order);
    }
}

/**
 * Mock factory for testing.
 */
class MockProcessorFactory implements ProcessorFactory {
    private bool $successful = true;
    private ?Exception $exception = null;
    private array $capturedCalls = [];

    public function setSuccessful(bool $successful): void {
        $this->successful = $successful;
    }

    public function setException(Exception $exception): void {
        $this->exception = $exception;
    }

    public function acquire(string $type, array $config = []): PaymentProcessor {
        $this->capturedCalls[] = ['type' => $type, 'config' => $config];

        if ($this->exception !== null) {
            throw $this->exception;
        }

        return new MockProcessor($type, $this->successful);
    }

    public function supports(string $type): bool {
        return true;
    }

    public function getSupportedTypes(): array {
        return ['mock'];
    }

    public function getCapturedCalls(): array {
        return $this->capturedCalls;
    }
}
```

## Performance Considerations

### Lazy Initialization

```php
<?php
/**
 * Factory with lazy initialization to avoid upfront costs.
 */
class LazyProcessorFactory {
    private array $processors = [];
    private array $configs;

    public function __construct(array $configs) {
        $this->configs = $configs;
    }

    public function acquire(string $type): PaymentProcessor {
        // Create only when first requested
        if (!isset($this->processors[$type])) {
            $this->processors[$type] = $this->createProcessor($type);
        }

        return $this->processors[$type];
    }

    private function createProcessor(string $type): PaymentProcessor {
        $config = $this->configs[$type] ?? [];

        return match($type) {
            'stripe' => new StripeProcessor($config['api_key']),
            'paypal' => new PayPalProcessor($config['client_id'], $config['client_secret']),
            default => throw new InvalidArgumentException("Unknown type: {$type}"),
        };
    }
}
```

### Caching Expensive Factory Operations

```php
<?php
/**
 * Factory with caching for expensive initialization.
 */
class CachedProcessorFactory implements ProcessorFactory {
    private ProcessorFactory $innerFactory;
    private array $cache = [];
    private int $ttl;

    public function __construct(ProcessorFactory $innerFactory, int $ttl = 3600) {
        $this->innerFactory = $innerFactory;
        $this->ttl = $ttl;
    }

    public function acquire(string $type, array $config = []): PaymentProcessor {
        $key = $this->getCacheKey($type, $config);

        if ($this->isCached($key)) {
            return $this->cache[$key]['processor'];
        }

        $processor = $this->innerFactory->acquire($type, $config);

        $this->cache[$key] = [
            'processor' => $processor,
            'expires' => time() + $this->ttl,
        ];

        return $processor;
    }

    private function getCacheKey(string $type, array $config): string {
        return md5($type . serialize($config));
    }

    private function isCached(string $key): bool {
        return isset($this->cache[$key]) &&
               $this->cache[$key]['expires'] > time();
    }

    public function supports(string $type): bool {
        return $this->innerFactory->supports($type);
    }

    public function getSupportedTypes(): array {
        return $this->innerFactory->getSupportedTypes();
    }
}
```

## Summary

### Quick Reference

| Pattern | Structure | When to Use | Complexity |
|---------|-----------|-------------|------------|
| **Factory Method** | Static method in interface | Simple scenarios, creation bundled with interface | Low |
| **Factory Class** | Separate static factory class | Most production code, clear separation of concerns | Medium |
| **Abstract Factory** | Factory interface + implementations | Testing, multiple strategies, architectural boundaries | High |

### Key Principles

1. **"New is Glue"** - Direct constructor calls create tight coupling
2. **Encapsulation** - Hide concrete class types from client code
3. **Separation of Concerns** - Keep creation logic separate from business logic
4. **Consistent Naming** - Use `acquire()` instead of mechanism-specific names
5. **Architectural Boundaries** - Use Abstract Factory to separate abstraction from implementation

### Decision Tree

```
Do you need to create objects without exposing concrete types?
├─ Yes → Use a factory pattern
│  ├─ Is creation logic simple and tightly coupled to interface?
│  │  └─ Yes → Factory Method
│  │
│  ├─ Do you need clear separation between interface and creation?
│  │  └─ Yes → Factory Class
│  │
│  └─ Do you need to swap entire creation strategies?
│     └─ Yes → Abstract Factory
│
└─ No → Direct instantiation with `new` is fine
```

### WordPress Best Practices

1. **Use Factory Class** for most plugin development
2. **Register factories** via `plugins_loaded` hook
3. **Support testing** by allowing factory injection
4. **Leverage options API** for factory configuration
5. **Document factory methods** with clear PHPDoc
6. **Consider memory** with Singleton/Flyweight patterns

## References

- **Gang of Four**: Design Patterns: Elements of Reusable Object-Oriented Software (1994)
- **Steve Ardalis**: ["New is Glue"](https://ardalis.com/new-is-glue/) (2012)
- **Bob Martin**: Clean Architecture (2017) - Architectural boundaries concept
- **Refactoring Guru**: [Factory Patterns](https://refactoring.guru/design-patterns/creational-patterns)
- **Source Making**: [Creational Patterns](https://sourcemaking.com/design_patterns/creational_patterns)
