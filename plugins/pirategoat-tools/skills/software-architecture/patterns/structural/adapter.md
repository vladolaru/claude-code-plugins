# Adapter Pattern

## Overview

The Adapter Design Pattern bridges the communication gap between two classes whose APIs don't directly allow them to interact. Like electrical plug adapters that accommodate different outlet shapes, voltages, and current frequencies across countries, the software Adapter pattern enables two incompatible interfaces to work together without modifying their source code.

Adapter is fundamentally about **a change in the contract interface but not a change in behavior**. It's a translator that converts the nomenclature of one system to match the expectations of another, allowing existing functionality to be reused in contexts where it wouldn't normally fit.

**Category:** Structural Pattern

**Core Mechanism:** Polymorphism combined with either delegation (Object Adapter) or inheritance (Class Adapter)

## When to Use

Use the Adapter pattern when you encounter these situations:

### API Incompatibility Scenarios

**External Library Integration**
- You need to use a third-party library or service with a different interface than your code expects
- The external element is beyond your control and cannot be modified
- You want to isolate your codebase from direct dependencies on external APIs

**Legacy Code Integration**
- You have legacy code with valuable functionality but incompatible interfaces
- The legacy code is used by other parts of the system and shouldn't be touched
- You want to reuse existing behavior without rewriting or refactoring working code

**API Evolution**
- You need to support multiple versions of an API simultaneously
- You're gradually migrating from an old interface to a new one
- You want to absorb the impact of interface changes in one location

### Design Principle Alignment

**When Programming to Interfaces**
- Your client code follows the principle "Program to an interface, not an implementation"
- You have a well-defined target interface that multiple implementations plug into
- Adding a new implementation would require matching an existing interface contract

**Maintaining Loose Coupling**
- You want to keep different subsystems modular and independent
- You need to prevent ripple effects when one subsystem's interface changes
- You want client code to remain unaware of specific service implementations

**Plugin Architecture**
- You have a plugin-based system with defined extension points
- New plugins need to conform to existing interfaces
- You want to integrate components that weren't originally designed to work together

### Behavioral Compatibility with Interface Mismatch

**Critical Requirement:** The adapted class must be similar enough in intent that using an Adapter makes sense. If the behaviors are fundamentally different, Adapter is the wrong pattern.

## When NOT to Use

### Wrong Problem Type

**Behavioral Differences**
- When the underlying behaviors are fundamentally different, not just the interfaces
- When you need to change what a class does, not just how it's called
- When the semantic meaning of operations differs significantly

**Direct Access is Simple**
- When you can modify the client to work with the service directly
- When the client doesn't need to work with multiple implementations
- When violating "Program to an interface" doesn't create maintenance problems

**Service Can Be Modified**
- When you have full control over the service class
- When changing the service won't break other code
- When the service isn't widely used or in a stable API

### Better Pattern Available

**When Behavior Needs Enhancement**
- Use **Decorator** if you need to add responsibilities dynamically
- Use **Proxy** if you need access control, lazy initialization, or logging
- Use **Facade** if you're simplifying a complex subsystem interface

**When Creation Logic is Complex**
- Use **Factory** patterns if the main challenge is object construction
- The Adapter still needs field attributes resolved, often requiring Factory Method or Dependency Injection

**When Interface Change Isn't the Issue**
- Use **Strategy** if you're selecting between algorithms
- Use **Command** if you're encapsulating requests
- Use **Bridge** if you're separating abstraction from implementation

### Over-Engineering Risk

**Single, Simple Use Case**
- Creating an Adapter for a one-time usage may be unnecessary ceremony
- Consider direct coupling if the cost of change is low

**Performance Critical Code**
- Every adapter adds a layer of indirection
- In tight loops or performance-critical paths, the overhead may matter
- Profile before optimizing, but be aware of the cost

## Structure

The Adapter pattern exists in two variations, both solving the same problem through different mechanisms:

### Object Adapter (Composition-Based)

**Uses delegation and composition**

```
┌─────────┐         ┌────────────┐
│ Client  │────────>│  «interface»│
└─────────┘         │   Target   │
                    │            │
                    │ +request() │
                    └────────────┘
                          △
                          │ implements
                          │
                    ┌─────┴──────┐
                    │  Adapter   │
                    │            │
                    │ +request() │───────┐
                    └────────────┘       │ delegates to
                                         │
                                         ▽
                                   ┌──────────┐
                                   │ Service  │
                                   │          │
                                   │ +action()│
                                   └──────────┘
```

**Key Characteristics:**
- Adapter implements Target interface
- Adapter has a reference to Service (composition)
- Adapter's `request()` method delegates to `service.action()`
- Requires field attribute resolution (via Factory or Dependency Injection)
- More flexible - can adapt multiple services through inheritance

### Class Adapter (Inheritance-Based)

**Uses inheritance and traditional polymorphism**

```
┌─────────┐         ┌────────────┐
│ Client  │────────>│  «interface»│
└─────────┘         │   Target   │
                    │            │
                    │ +request() │
                    └────────────┘
                          △
                          │ implements
                          │
                    ┌─────┴──────┐
                    │  Adapter   │────extends────> ┌──────────┐
                    │            │                  │ Service  │
                    │ +request() │                  │          │
                    └────────────┘                  │ +action()│
                                                    └──────────┘
```

**Key Characteristics:**
- Adapter implements Target interface
- Adapter extends Service (inheritance)
- Adapter's `request()` method calls inherited `action()` method
- Less setup required - no external Service reference needed
- Less flexible - cannot adapt multiple services simultaneously
- Not possible in languages that don't support multiple inheritance

### Comparison

| Aspect | Object Adapter | Class Adapter |
|--------|----------------|---------------|
| Mechanism | Composition + Delegation | Inheritance |
| Flexibility | Can adapt multiple services | Limited to single inheritance chain |
| Setup | Requires dependency injection | Simpler setup |
| Service Access | Via reference field | Via inherited methods |
| Language Support | All OOP languages | Requires multiple inheritance |
| Preference | Generally preferred | Works but less flexible |

## How It Works

### The Communication Gap Problem

You start with a scenario where:

1. **Client** expects to work with **Target** interface
2. **Target** declares a method `request()`
3. **Service** has the desired behavior but with a different method `action()`
4. Client follows "Program to an interface" principle and cannot be changed
5. Service cannot be modified (external, legacy, or widely used)

### The Adapter Solution

**Object Adapter Flow:**

1. Client calls `target.request()`
2. At runtime, `target` reference points to an Adapter instance
3. Adapter's `request()` is invoked
4. Adapter delegates to `service.action()` via its Service reference
5. Service executes and returns result
6. Adapter returns result to Client (possibly with translation)

**Class Adapter Flow:**

1. Client calls `target.request()`
2. At runtime, `target` reference points to an Adapter instance
3. Adapter's `request()` is invoked
4. Adapter calls its inherited `action()` method
5. Service code executes via inheritance chain
6. Result flows back through Adapter to Client

### Beyond Simple Method Forwarding

While diagrams show simple one-line delegation, real Adapters often need to:

**Translate Argument Types**
```php
// Target expects different parameter types than Service provides
public function request(string $data): Result {
    $converted = $this->convertToServiceFormat($data);
    $serviceResult = $this->service->action($converted);
    return $this->convertToTargetFormat($serviceResult);
}
```

**Translate Return Types**
```php
// Service returns one type, Target expects another
public function request(): TargetResponse {
    $serviceData = $this->service->action();
    return new TargetResponse($serviceData);
}
```

**Handle Parameter Count Differences**
```php
// Target has more/fewer parameters than Service
public function request(string $id, array $options): void {
    // Combine parameters or extract what Service needs
    $this->service->action($id);
}
```

### Maintaining Modularity

The Adapter design preserves loose coupling:

- **Client/Target code** remains unaware of Service
- **Service code** remains unaware of Client/Target
- If Service contract changes, only the Adapter needs updating
- Client/Target ecosystem can evolve independently

## Real-World Examples

### Electrical Plug Adapters

**The Canonical Example:**
- You travel internationally with electrical devices
- Different countries have different outlet shapes, voltages, frequencies
- Adapter plugs into wall outlet, device plugs into adapter
- Adapter translates electrical standards without modifying device or building wiring

### Legacy Database Integration

**Scenario:**
```php
// Modern code expects PSR-3 logger interface
interface LoggerInterface {
    public function error(string $message, array $context = []): void;
}

// Legacy logging system has different API
class LegacyFileLogger {
    public function logError(string $msg): void {
        file_put_contents('errors.log', $msg . "\n", FILE_APPEND);
    }
}

// Object Adapter bridges the gap
class LegacyLoggerAdapter implements LoggerInterface {
    private LegacyFileLogger $legacy;

    public function __construct(LegacyFileLogger $legacy) {
        $this->legacy = $legacy;
    }

    public function error(string $message, array $context = []): void {
        $formatted = $message;
        if (!empty($context)) {
            $formatted .= ' ' . json_encode($context);
        }
        $this->legacy->logError($formatted);
    }
}
```

### Third-Party Payment Gateway

**Scenario:**
```php
// Your e-commerce system expects this interface
interface PaymentGatewayInterface {
    public function processPayment(Order $order): PaymentResult;
}

// Third-party service has its own API
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

### Multiple API Versions

**Scenario:**
```php
// Your code works with current API
interface NotificationServiceInterface {
    public function send(string $recipient, string $message): bool;
}

// Version 1 of external service (deprecated but still used)
class NotificationServiceV1 {
    public function sendMessage(string $to, string $body): int {
        // Returns status code
    }
}

// Version 2 of external service (new)
class NotificationServiceV2 {
    public function notify(array $params): array {
        // Returns detailed response array
    }
}

// Adapter for V1
class NotificationV1Adapter implements NotificationServiceInterface {
    private NotificationServiceV1 $service;

    public function send(string $recipient, string $message): bool {
        $statusCode = $this->service->sendMessage($recipient, $message);
        return $statusCode === 200;
    }
}

// Adapter for V2
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

## Implementation Guide (PHP)

### Object Adapter Implementation

**Step 1: Define Target Interface**

```php
<?php

namespace App\Contracts;

/**
 * Target interface that clients expect to work with
 */
interface DataStorageInterface
{
    /**
     * Store data and return storage identifier
     */
    public function store(string $key, array $data): string;

    /**
     * Retrieve data by identifier
     */
    public function retrieve(string $key): ?array;
}
```

**Step 2: Service with Incompatible Interface**

```php
<?php

namespace Vendor\CloudStorage;

/**
 * Third-party cloud storage service we want to use
 * (Cannot be modified - external library)
 */
class CloudStorageClient
{
    public function upload(string $filename, string $content): bool
    {
        // Cloud-specific upload logic
        return true;
    }

    public function download(string $filename): ?string
    {
        // Cloud-specific download logic
        return '{"some":"data"}';
    }

    public function getLastUploadId(): string
    {
        return 'cloud-id-12345';
    }
}
```

**Step 3: Create Object Adapter**

```php
<?php

namespace App\Adapters;

use App\Contracts\DataStorageInterface;
use Vendor\CloudStorage\CloudStorageClient;

/**
 * Object Adapter using composition and delegation
 */
class CloudStorageAdapter implements DataStorageInterface
{
    private CloudStorageClient $cloudClient;
    private string $basePath;

    public function __construct(CloudStorageClient $cloudClient, string $basePath = 'data/')
    {
        $this->cloudClient = $cloudClient;
        $this->basePath = $basePath;
    }

    /**
     * Adapts DataStorageInterface::store() to CloudStorageClient::upload()
     * Translates parameter types and return values
     */
    public function store(string $key, array $data): string
    {
        // Convert array to JSON string (type translation)
        $jsonContent = json_encode($data);

        // Build filename compatible with cloud storage
        $filename = $this->basePath . $key . '.json';

        // Delegate to cloud client with adapted parameters
        $success = $this->cloudClient->upload($filename, $jsonContent);

        if (!$success) {
            throw new \RuntimeException("Failed to store data for key: {$key}");
        }

        // Return cloud-specific identifier
        return $this->cloudClient->getLastUploadId();
    }

    /**
     * Adapts DataStorageInterface::retrieve() to CloudStorageClient::download()
     */
    public function retrieve(string $key): ?array
    {
        $filename = $this->basePath . $key . '.json';

        // Delegate to cloud client
        $jsonContent = $this->cloudClient->download($filename);

        if ($jsonContent === null) {
            return null;
        }

        // Convert JSON string back to array (type translation)
        return json_decode($jsonContent, true);
    }
}
```

**Step 4: Client Usage**

```php
<?php

namespace App\Services;

use App\Contracts\DataStorageInterface;

/**
 * Client that programs to the interface
 */
class DataManager
{
    private DataStorageInterface $storage;

    public function __construct(DataStorageInterface $storage)
    {
        // Client doesn't know or care about adapter or cloud client
        $this->storage = $storage;
    }

    public function saveUserData(string $userId, array $userData): void
    {
        $storageId = $this->storage->store($userId, $userData);
        echo "Saved user data with ID: {$storageId}\n";
    }

    public function loadUserData(string $userId): ?array
    {
        return $this->storage->retrieve($userId);
    }
}
```

**Step 5: Dependency Injection Setup**

```php
<?php

use App\Adapters\CloudStorageAdapter;
use App\Services\DataManager;
use Vendor\CloudStorage\CloudStorageClient;

// Wire up dependencies
$cloudClient = new CloudStorageClient();
$adapter = new CloudStorageAdapter($cloudClient, 'users/');
$dataManager = new DataManager($adapter);

// Use through common interface
$dataManager->saveUserData('user123', [
    'name' => 'John Doe',
    'email' => 'john@example.com'
]);

$userData = $dataManager->loadUserData('user123');
```

### Class Adapter Implementation

**Note:** PHP supports single inheritance only. For true Class Adapter with multiple inheritance, use languages like Python or C++. In PHP, we can approximate with traits or accept limitations.

**Using Inheritance (Single Service)**

```php
<?php

namespace App\Adapters;

use App\Contracts\DataStorageInterface;
use Vendor\CloudStorage\CloudStorageClient;

/**
 * Class Adapter using inheritance
 * Limitation: Can only adapt CloudStorageClient, not other services
 */
class CloudStorageClassAdapter extends CloudStorageClient implements DataStorageInterface
{
    private string $basePath;

    public function __construct(string $basePath = 'data/')
    {
        parent::__construct();
        $this->basePath = $basePath;
    }

    /**
     * Implements Target interface by calling inherited methods
     */
    public function store(string $key, array $data): string
    {
        $jsonContent = json_encode($data);
        $filename = $this->basePath . $key . '.json';

        // Call inherited method directly
        $success = $this->upload($filename, $jsonContent);

        if (!$success) {
            throw new \RuntimeException("Failed to store data for key: {$key}");
        }

        return $this->getLastUploadId();
    }

    public function retrieve(string $key): ?array
    {
        $filename = $this->basePath . $key . '.json';
        $jsonContent = $this->download($filename);

        if ($jsonContent === null) {
            return null;
        }

        return json_decode($jsonContent, true);
    }
}
```

### Advanced: Two-Way Adapter

**When both direction adaptations are needed:**

```php
<?php

namespace App\Adapters;

use App\Contracts\DataStorageInterface;
use Vendor\CloudStorage\CloudStorageInterface as CloudInterface;

/**
 * Two-way adapter that implements both interfaces
 */
class TwoWayStorageAdapter implements DataStorageInterface, CloudInterface
{
    private $wrappedService;

    public function __construct($service)
    {
        $this->wrappedService = $service;
    }

    // DataStorageInterface methods
    public function store(string $key, array $data): string
    {
        if ($this->wrappedService instanceof CloudInterface) {
            return $this->wrappedService->cloudStore($key, $data);
        }
        return $this->wrappedService->store($key, $data);
    }

    public function retrieve(string $key): ?array
    {
        if ($this->wrappedService instanceof CloudInterface) {
            return $this->wrappedService->cloudRetrieve($key);
        }
        return $this->wrappedService->retrieve($key);
    }

    // CloudInterface methods
    public function cloudStore(string $key, array $data): string
    {
        return $this->store($key, $data);
    }

    public function cloudRetrieve(string $key): ?array
    {
        return $this->retrieve($key);
    }
}
```

### Testing Considerations

**Test the Adapter in Isolation**

```php
<?php

namespace Tests\Unit\Adapters;

use App\Adapters\CloudStorageAdapter;
use PHPUnit\Framework\TestCase;
use Vendor\CloudStorage\CloudStorageClient;

class CloudStorageAdapterTest extends TestCase
{
    public function test_store_delegates_to_cloud_client(): void
    {
        // Mock the service
        $mockClient = $this->createMock(CloudStorageClient::class);
        $mockClient->expects($this->once())
            ->method('upload')
            ->with(
                $this->equalTo('data/test-key.json'),
                $this->equalTo('{"test":"data"}')
            )
            ->willReturn(true);

        $mockClient->expects($this->once())
            ->method('getLastUploadId')
            ->willReturn('cloud-123');

        // Test adapter
        $adapter = new CloudStorageAdapter($mockClient);
        $result = $adapter->store('test-key', ['test' => 'data']);

        $this->assertEquals('cloud-123', $result);
    }

    public function test_retrieve_translates_json_to_array(): void
    {
        $mockClient = $this->createMock(CloudStorageClient::class);
        $mockClient->method('download')
            ->willReturn('{"name":"John","age":30}');

        $adapter = new CloudStorageAdapter($mockClient);
        $result = $adapter->retrieve('user-id');

        $this->assertEquals(['name' => 'John', 'age' => 30], $result);
    }

    public function test_retrieve_returns_null_when_not_found(): void
    {
        $mockClient = $this->createMock(CloudStorageClient::class);
        $mockClient->method('download')
            ->willReturn(null);

        $adapter = new CloudStorageAdapter($mockClient);
        $result = $adapter->retrieve('missing-key');

        $this->assertNull($result);
    }
}
```

## Benefits

### Code Reusability

**Leverages Existing Functionality**
- No need to rewrite behavior that already exists
- Reduces duplication and maintenance burden
- Protects investment in working code

**Adapts Rather Than Recreates**
- Wraps external libraries without forking them
- Integrates legacy systems without refactoring them
- Uses third-party services as-is

### Maintains Design Principles

**Preserves Programming to Interfaces**
- Client code continues working with abstract contracts
- No violation of dependency inversion principle
- Supports polymorphic substitution

**Keeps Loose Coupling**
- Client unaware of Service implementation
- Service unaware of Client requirements
- Changes isolated to Adapter layer

**Single Responsibility Principle**
- Adapter has one job: translate interfaces
- Client focuses on its domain logic
- Service focuses on its implementation

### Flexibility and Maintainability

**Isolates Interface Changes**
- If Service API changes, update only the Adapter
- Client/Target code remains stable
- Reduces ripple effects across codebase

**Enables Multiple Implementations**
- Different Adapters for different Services
- Easy to swap implementations via dependency injection
- Supports A/B testing and gradual migrations

**Facilitates Testing**
- Client can be tested with mock Targets
- Service can be tested independently
- Adapter itself is easily unit tested

### Gradual Migration

**Supports API Evolution**
- Run old and new APIs side-by-side
- Migrate clients incrementally
- Maintain backward compatibility during transitions

**Risk Mitigation**
- Small, focused changes in Adapter layer
- Easy to roll back if issues arise
- Can deploy and monitor progressively

## Trade-offs

### Complexity

**Additional Layer of Indirection**
- Every method call goes through Adapter
- More classes to understand and maintain
- Can obscure the flow of execution

**Setup Overhead**
- Requires dependency injection or factory patterns
- Configuration can become complex with many adapters
- Initial development time investment

**Not Always Obvious**
- Developers may not realize Adapter is in use
- Can make debugging more challenging
- Requires documentation and naming conventions

### Performance

**Runtime Cost**
- Extra method call per operation
- Type translations add processing time
- In tight loops, overhead may be measurable

**Memory Overhead**
- Adapter instances consume memory
- May hold references to heavy Service objects
- Multiple layers of wrapping increase footprint

**When It Matters**
- High-frequency operations (thousands per second)
- Embedded systems with constrained resources
- Real-time systems with strict latency requirements

**Mitigation**
- Profile before optimizing
- Cache translated values when possible
- Consider alternative patterns for critical paths

### Maintenance

**Keeps Multiple Interfaces in Sync**
- If Target interface evolves, Adapters must follow
- If Service interface changes, Adapters must be updated
- Breaking changes require coordinated updates

**Can Proliferate**
- Many services may need many adapters
- Risk of adapter explosion in large systems
- Requires disciplined architecture to manage

**Implementation Errors**
- Translation logic can have bugs
- Parameter mapping mistakes can cause subtle errors
- Return type conversions may lose information

### Limited Scope

**Doesn't Fix Fundamental Incompatibilities**
- Can only adapt similar behaviors
- Cannot make incompatible semantics compatible
- Wrong pattern if operations have different meanings

**Simple Delegations Get Tedious**
- Many small methods that just forward calls
- Can feel like boilerplate for simple cases
- May be over-engineering for trivial adaptations

## Common Mistakes

### Adapting Incompatible Behaviors

**Mistake:**
```php
// UserRepository expects CRUD operations
interface UserRepository {
    public function save(User $user): void;
    public function find(int $id): ?User;
}

// NotificationService sends messages - fundamentally different!
class EmailNotificationService {
    public function sendEmail(string $to, string $subject, string $body): bool;
}

// This makes no semantic sense
class NotificationRepositoryAdapter implements UserRepository {
    public function save(User $user): void {
        // What does "save a user" mean for notifications?
        $this->service->sendEmail($user->email, "Welcome", "...");
    }
}
```

**Why It's Wrong:**
- Behaviors are semantically incompatible
- Adapter cannot meaningfully translate operations
- Violates principle that Adapter is for interface changes, not behavior changes

**Solution:**
- Use the right abstraction for each service
- Don't force incompatible concepts together
- Consider if you need an adapter at all

### Over-Adapting

**Mistake:**
```php
// Creating adapter for every tiny thing
class StringAdapter implements StringInterface {
    private string $value;

    public function getValue(): string {
        return $this->value; // Pointless wrapper
    }
}
```

**Why It's Wrong:**
- Adds complexity without value
- Makes code harder to follow
- Performance overhead for no benefit

**Solution:**
- Use adapters only when there's actual incompatibility
- Don't adapt for the sake of patterns
- Prefer simplicity when interfaces already match

### Fat Adapters with Business Logic

**Mistake:**
```php
class PaymentAdapter implements PaymentGatewayInterface {
    public function processPayment(Order $order): PaymentResult {
        // TOO MUCH LOGIC IN ADAPTER

        // Validate order (business logic - doesn't belong here)
        if ($order->getTotal() < 0) {
            throw new InvalidOrderException();
        }

        // Apply discounts (business logic - doesn't belong here)
        $discountedTotal = $this->applyDiscounts($order);

        // Log to database (side effect - doesn't belong here)
        $this->logger->logPaymentAttempt($order);

        // Finally delegate to service
        return $this->service->charge($discountedTotal);
    }
}
```

**Why It's Wrong:**
- Adapter should translate interfaces, not contain business logic
- Violates Single Responsibility Principle
- Makes testing and maintenance harder
- Obscures where business rules live

**Solution:**
```php
class PaymentAdapter implements PaymentGatewayInterface {
    public function processPayment(Order $order): PaymentResult {
        // ONLY translation logic
        $amountInCents = (int)($order->getTotal() * 100);
        $result = $this->service->charge($amountInCents, $order->getCurrency());

        return new PaymentResult(
            success: $result['status'] === 'succeeded',
            transactionId: $result['id']
        );
    }
}

// Business logic belongs in a service layer
class PaymentService {
    public function process(Order $order): PaymentResult {
        $this->validateOrder($order);
        $adjustedOrder = $this->applyDiscounts($order);
        $this->logger->logPaymentAttempt($order);

        return $this->gateway->processPayment($adjustedOrder);
    }
}
```

### Not Handling Exceptions Properly

**Mistake:**
```php
class CacheAdapter implements CacheInterface {
    public function get(string $key): ?string {
        // Service throws RedisException, but interface expects null
        return $this->redis->get($key); // Exception leaks through!
    }
}
```

**Why It's Wrong:**
- Service exceptions leak to client
- Violates the contract of the Target interface
- Client doesn't expect to handle service-specific exceptions

**Solution:**
```php
class CacheAdapter implements CacheInterface {
    public function get(string $key): ?string {
        try {
            return $this->redis->get($key);
        } catch (RedisException $e) {
            // Translate to interface-appropriate behavior
            $this->logger->error("Cache error: " . $e->getMessage());
            return null; // or throw interface-defined exception
        }
    }
}
```

### Forgetting Parameter/Return Type Translation

**Mistake:**
```php
class DateAdapter implements DateServiceInterface {
    public function getCurrentDate(): DateTime {
        // Service returns timestamp string, not DateTime!
        return $this->service->getTime(); // Type mismatch!
    }
}
```

**Why It's Wrong:**
- Type mismatch causes runtime errors
- Doesn't fulfill the contract of Target interface
- Defeats the purpose of the adapter

**Solution:**
```php
class DateAdapter implements DateServiceInterface {
    public function getCurrentDate(): DateTime {
        $timestamp = $this->service->getTime();
        return new DateTime('@' . $timestamp);
    }
}
```

### Creating Leaky Abstractions

**Mistake:**
```php
interface DataStorageInterface {
    // Leaking AWS S3 implementation details!
    public function store(string $s3Bucket, string $s3Key, array $data): void;
}

class S3Adapter implements DataStorageInterface {
    // Now interface is tied to S3, defeating the purpose
}
```

**Why It's Wrong:**
- Target interface exposes Service implementation details
- Cannot substitute different Service implementations
- Client becomes coupled to Service through the interface

**Solution:**
```php
interface DataStorageInterface {
    // Generic, implementation-agnostic interface
    public function store(string $key, array $data): void;
}

class S3Adapter implements DataStorageInterface {
    private string $bucket;

    public function __construct(S3Client $client, string $bucket) {
        $this->client = $client;
        $this->bucket = $bucket; // S3 details hidden in adapter
    }

    public function store(string $key, array $data): void {
        // Translate generic interface to S3 specifics
        $this->client->putObject([
            'Bucket' => $this->bucket,
            'Key' => $key,
            'Body' => json_encode($data)
        ]);
    }
}
```

### Not Using Dependency Injection

**Mistake:**
```php
class ServiceAdapter implements TargetInterface {
    private Service $service;

    public function __construct() {
        // Hard-coded dependency!
        $this->service = new Service();
    }
}
```

**Why It's Wrong:**
- Cannot test with mock Service
- Cannot configure Service
- Violates Dependency Inversion Principle
- Creates tight coupling

**Solution:**
```php
class ServiceAdapter implements TargetInterface {
    private Service $service;

    public function __construct(Service $service) {
        // Inject dependency
        $this->service = $service;
    }
}
```

## Pattern Relationships

### Adapter Pairs with Strategy

**Adapter is Strategy with Extra Steps**

The Adapter pattern is essentially a Strategy pattern with additional implementation detail. The structural similarity is so close that Google Image searches for "Adapter Design Pattern UML" show diagrams nearly identical to Strategy patterns.

**Key Similarity:**
- Both use polymorphism to allow substitutable implementations
- Both have client code programming to an interface
- Both support multiple concrete implementations

**Key Difference:**
- Strategy focuses on encapsulating algorithms/behaviors
- Adapter focuses on translating between incompatible interfaces
- Adapter typically delegates to another class; Strategy contains the algorithm

**When to Use Which:**
- Use **Strategy** when you're selecting between different approaches to the same problem
- Use **Adapter** when you're integrating an existing class with an incompatible interface

### Adapter vs. Decorator

**Both wrap objects, different purposes:**

| Aspect | Adapter | Decorator |
|--------|---------|-----------|
| Purpose | Change interface | Add functionality |
| Interface | Implements different interface | Implements same interface |
| Behavior | Delegates to adapted object | Enhances wrapped object |
| Direction | Converts one interface to another | Wraps and extends |

**Example:**
```php
// Adapter: Changes interface
class PaymentAdapter implements PaymentGatewayInterface {
    public function processPayment(Order $order): PaymentResult {
        return $this->stripeService->charge(...); // Different interface
    }
}

// Decorator: Same interface, adds behavior
class LoggingPaymentGateway implements PaymentGatewayInterface {
    public function processPayment(Order $order): PaymentResult {
        $this->logger->info("Processing payment");
        $result = $this->wrapped->processPayment($order); // Same interface
        $this->logger->info("Payment processed");
        return $result;
    }
}
```

### Adapter vs. Facade

**Both simplify interaction, different contexts:**

| Aspect | Adapter | Facade |
|--------|---------|--------|
| Problem | Incompatible interface | Complex subsystem |
| Scope | Single class/interface | Multiple classes/subsystem |
| Goal | Make existing interface work | Provide simpler interface |
| Direction | One-to-one mapping | Many-to-one simplification |

**Example:**
```php
// Adapter: One service, interface translation
class LegacyAuthAdapter implements AuthInterface {
    public function authenticate(string $user, string $pass): bool {
        return $this->legacyAuth->doLogin($user, $pass) === 1;
    }
}

// Facade: Multiple services, simplified interface
class AuthenticationFacade {
    public function login(string $user, string $pass): User {
        // Coordinates multiple subsystems
        $this->sessionManager->start();
        $this->validator->checkCredentials($user, $pass);
        $userData = $this->userRepository->findByUsername($user);
        $this->permissionLoader->loadPermissions($userData);
        $this->auditLogger->logLogin($user);
        return new User($userData);
    }
}
```

### Adapter vs. Proxy

**Both add indirection, different reasons:**

| Aspect | Adapter | Proxy |
|--------|---------|-------|
| Purpose | Interface compatibility | Access control/optimization |
| Interface | Changes to different interface | Keeps same interface |
| When | Service interface doesn't match | Service interface matches |
| Focus | Translation | Control, lazy loading, caching |

**Example:**
```php
// Adapter: Changes interface
class CacheAdapter implements CacheInterface {
    public function get(string $key): ?string {
        return $this->redis->fetchValue($key); // Different method name
    }
}

// Proxy: Same interface, adds control
class CachingProxy implements CacheInterface {
    public function get(string $key): ?string {
        if ($this->localCache->has($key)) {
            return $this->localCache->get($key); // Add caching layer
        }
        $value = $this->realCache->get($key); // Same interface
        $this->localCache->set($key, $value);
        return $value;
    }
}
```

### Adapter vs. Bridge

**Both separate concerns, different dimensions:**

| Aspect | Adapter | Bridge |
|--------|---------|--------|
| Intent | Work with existing classes | Design for future extension |
| Timing | Applied after design | Applied during design |
| Problem | Incompatibility | Avoiding class explosion |
| Structure | Single adaptation layer | Separate abstraction & implementation |

**Example:**
```php
// Adapter: Retrofit existing Service
class ExistingServiceAdapter implements TargetInterface {
    public function operation(): void {
        $this->existingService->doSomething(); // Adapt after the fact
    }
}

// Bridge: Design for extension from start
abstract class Notification {
    protected NotificationSender $sender; // Abstraction uses implementation

    abstract public function send(string $message): void;
}

class EmailNotification extends Notification {
    public function send(string $message): void {
        $this->sender->sendViaEmail($message); // Implementation can vary
    }
}
```

### Adapter with Factory Method

**Resolving Field Attributes:**

Adapters need Service references. Factory Method pattern helps create and configure adapters properly.

```php
// Factory creates properly configured adapters
interface StorageAdapterFactory {
    public function createAdapter(): DataStorageInterface;
}

class CloudStorageAdapterFactory implements StorageAdapterFactory {
    public function createAdapter(): DataStorageInterface {
        $cloudClient = new CloudStorageClient();
        $cloudClient->configure(['region' => 'us-east-1']);
        return new CloudStorageAdapter($cloudClient, 'production/');
    }
}

class LocalStorageAdapterFactory implements StorageAdapterFactory {
    public function createAdapter(): DataStorageInterface {
        $fileSystem = new LocalFileSystem('/var/data');
        return new LocalStorageAdapter($fileSystem);
    }
}
```

### Adapter with Dependency Injection

**Modern Approach to Wiring:**

Dependency Injection containers handle adapter configuration and injection.

```php
// Service provider registration
class AppServiceProvider {
    public function register(Container $container): void {
        // Bind interface to adapter
        $container->singleton(DataStorageInterface::class, function ($app) {
            $cloudClient = $app->make(CloudStorageClient::class);
            return new CloudStorageAdapter($cloudClient, config('storage.path'));
        });
    }
}

// Client receives adapter via constructor injection
class DataManager {
    public function __construct(
        private DataStorageInterface $storage // Adapter injected automatically
    ) {}
}
```

## Decision Criteria

### Choose Adapter When

**Interface Mismatch, Behavior Match**
- ✅ You have a service with the right behavior but wrong interface
- ✅ The semantic meaning of operations aligns
- ✅ You need to translate method names, parameter types, or return types

**Cannot Modify Service**
- ✅ Service is from an external library
- ✅ Service is legacy code you shouldn't touch
- ✅ Service is used elsewhere and changes would break other code

**Client Programming to Interface**
- ✅ Client follows "Program to interface, not implementation"
- ✅ You have an established Target interface
- ✅ Multiple implementations plug into the same interface

**Want Loose Coupling**
- ✅ You want to isolate interface changes
- ✅ You need to swap implementations easily
- ✅ You're building a plugin architecture

### Choose Something Else When

**Consider Strategy Instead:**
- ❌ You're selecting between different algorithms
- ❌ The problem is about behavior choice, not interface compatibility
- ❌ All implementations naturally share the same interface

**Consider Decorator Instead:**
- ❌ You need to add responsibilities dynamically
- ❌ You want to enhance existing behavior
- ❌ Interface is already compatible

**Consider Facade Instead:**
- ❌ You're simplifying a complex subsystem
- ❌ Multiple classes need coordination
- ❌ The problem is complexity, not incompatibility

**Consider Proxy Instead:**
- ❌ Interface already matches
- ❌ You need access control, lazy loading, or caching
- ❌ The problem is optimization or security, not compatibility

**Consider Bridge Instead:**
- ❌ You're designing from scratch to avoid class explosion
- ❌ You need abstraction and implementation to vary independently
- ❌ The problem is anticipated future variations

**Don't Use Patterns:**
- ❌ Direct coupling is simpler and sufficient
- ❌ One-time usage with no future needs
- ❌ The added complexity outweighs benefits

### Questions to Ask

1. **Is there an interface mismatch?**
   - If no, you probably don't need Adapter

2. **Can you modify the service?**
   - If yes, maybe just change the service directly

3. **Can you modify the client?**
   - If yes, and it's simple, maybe just update the client

4. **Do behaviors align semantically?**
   - If no, Adapter is the wrong pattern

5. **Is client programming to an interface?**
   - If no, you might not need the abstraction

6. **Will there be multiple implementations?**
   - If no, simpler coupling might be fine

7. **Is the adaptation one-time or ongoing?**
   - One-time might not justify pattern overhead

8. **How complex is the translation?**
   - Simple forwarding is good; complex logic suggests architectural issues

## Quotes

> "Adapter is about a change in the contract interface but not a change in behavior."
> — Design Patterns in Practice

> "Adapters are translators. They translate the nomenclature of the Client with the nomenclature of the Service."
> — Design Patterns in Practice

> "The Adapter Design Pattern does one more thing, which I haven't mentioned yet. It helps keep the implementation modular and loosely coupled. The Client/Target code still doesn't know about the Service code and vice versa even after being bridged by the Adapter."
> — Design Patterns in Practice

> "If Service changes its contract interface, then an Adapter based design may be able to absorb the impact of the interface change in the Adapter without any change to the Client/Target code."
> — Design Patterns in Practice

> "Adapter implementations tend to be small. Each method is usually only a few lines long."
> — Design Patterns in Practice

> "What we've got here is... failure to communicate."
> — Cool Hand Luke (Pattern Inspiration)

## Further Reading

### Free Online Resources

**Wikipedia**
- [Adapter Pattern](https://en.wikipedia.org/wiki/Adapter_pattern)
- Overview, structure, examples in multiple languages

**Source Making**
- [Adapter Design Pattern](https://sourcemaking.com/design_patterns/adapter)
- Clear explanations with diagrams and code examples

**Refactoring Guru**
- [Adapter Design Pattern](https://refactoring.guru/design-patterns/adapter)
- Excellent visual explanations and real-world analogies

**DoFactory**
- [Adapter Design Pattern](https://www.dofactory.com/net/adapter-design-pattern)
- .NET-focused but concepts apply universally

**Project Management Institute**
- [The Adapter Pattern](https://www.pmi.org/disciplined-agile/the-design-patterns-repository/the-adapter-pattern)
- Enterprise and agile perspectives

**Google Search**
- [Adapter Design Pattern](https://www.google.com/search?q=adapter+design+pattern)
- Browse multiple perspectives and examples

**Google Image Search**
- [Adapter Design Pattern UML](https://www.google.com/search?q=Adapter+Design+Pattern+UML+class+diagram&tbm=isch)
- Visual learning through various UML diagrams

### Books and Paid Resources

**Gang of Four (GoF)**
- Design Patterns: Elements of Reusable Object-Oriented Software
- [O'Reilly](https://learning.oreilly.com/library/view/design-patterns-elements/0201633612/ch04.html#page_141)
- The original and authoritative source

**Agile Principles, Patterns, and Practices**
- Robert C. Martin (Uncle Bob)
- Chapter 33 covers Adapter pattern
- [O'Reilly](https://learning.oreilly.com/library/view/agile-principles-patterns/0131857258/)
- [Amazon](https://www.amazon.com/Agile-Principles-Patterns-Practices-C/dp/0131857258)

**Clean Code: Design Patterns**
- Episode 34 video by Robert C. Martin
- [Clean Coders](https://cleancoders.com/episode/clean-code-episode-34)
- [O'Reilly](https://learning.oreilly.com/videos/clean-code-fundamentals/9780134661742/9780134661742-code_03_34_00/)
- Practical implementation and testing

**Head First Design Patterns**
- Chapter 7 covers Adapter (and Facade)
- [O'Reilly](https://learning.oreilly.com/library/view/head-first-design/9781492077992/ch07.html#adapter_pattern_defined)
- [Amazon](https://www.amazon.com/Head-First-Design-Patterns-Object-Oriented-ebook/dp/B08P3X99QP)
- Approachable, visual style

### Related Patterns to Explore

- **Strategy** - Similar structure, different focus
- **Decorator** - Also wraps objects, but enhances rather than adapts
- **Facade** - Simplifies complex systems rather than adapting interfaces
- **Proxy** - Same interface control rather than interface translation
- **Bridge** - Separates abstraction from implementation by design
- **Factory Method** - Often used to create adapters
- **Dependency Injection** - Modern way to wire adapters into clients

### Topics to Study

- **Interface Segregation Principle** - Keeping interfaces focused
- **Dependency Inversion Principle** - Programming to interfaces
- **Composition vs. Inheritance** - When to use each
- **Loose Coupling** - Reducing dependencies between components
- **API Versioning** - Managing interface evolution
- **Legacy Code Techniques** - Working with existing systems
