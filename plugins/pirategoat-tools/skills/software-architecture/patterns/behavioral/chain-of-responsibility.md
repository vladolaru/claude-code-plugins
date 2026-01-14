# Chain of Responsibility Pattern

## Intent

Delegate a request through a linked chain of handlers until one of the handlers can complete the request.

## Problem

You need to process requests through multiple potential handlers where:
- Not every handler can process every request
- You want to avoid hardcoding which handler processes which request
- Handlers should be organized from least to most resource-intensive
- The coupling between sender and receiver should be minimized
- The set of handlers and their order should be configurable at runtime

Traditional solutions using `switch` statements or cascading `if/else-if/else` blocks have drawbacks:
- **Static and inflexible**: Handler selection is hardcoded
- **Code duplication**: Same control structures replicated across codebase
- **Difficult to maintain**: Adding/removing handlers requires code changes
- **Type-based branching**: Often switches on types rather than encapsulating behavior
- **Violation of OCP**: Cannot extend without modification

## Solution

Create a chain of handler objects, each capable of either:
1. Processing the request and returning
2. Passing the request to the next handler in the chain

Key participants:
- **Handler interface**: Defines common interface for handling requests
- **Abstract handler**: Contains chain reference and delegation logic
- **Concrete handlers**: Implement specific handling logic
- **Anchor handler**: Provides default behavior when no handler processes request
- **Client**: Initiates request without knowing which handler will process it
- **Configurer**: Assembles handler chain based on runtime conditions

## Structure

### Basic Structure (GoF Approach)

```php
<?php

interface RequestHandler {
    public function handleRequest(Request $request): Response;
}

abstract class DelegatingRequestHandler implements RequestHandler {
    protected ?RequestHandler $nextHandler = null;

    public function __construct(?RequestHandler $nextHandler = null) {
        $this->nextHandler = $nextHandler;
    }

    public function handleRequest(Request $request): Response {
        // Concrete classes must implement this logic:
        // if (canHandle($request)) {
        //     return processRequest($request);
        // } else {
        //     return $this->nextHandler?->handleRequest($request)
        //         ?? throw new UnhandledRequestException();
        // }
    }
}

class ConcreteHandlerA extends DelegatingRequestHandler {
    public function handleRequest(Request $request): Response {
        if ($this->canHandleA($request)) {
            return $this->processWithA($request);
        }

        if ($this->nextHandler !== null) {
            return $this->nextHandler->handleRequest($request);
        }

        throw new UnhandledRequestException();
    }

    private function canHandleA(Request $request): bool {
        // A-specific logic
    }

    private function processWithA(Request $request): Response {
        // A-specific processing
    }
}

class ConcreteHandlerB extends DelegatingRequestHandler {
    public function handleRequest(Request $request): Response {
        if ($this->canHandleB($request)) {
            return $this->processWithB($request);
        }

        if ($this->nextHandler !== null) {
            return $this->nextHandler->handleRequest($request);
        }

        throw new UnhandledRequestException();
    }

    private function canHandleB(Request $request): bool {
        // B-specific logic
    }

    private function processWithB(Request $request): Response {
        // B-specific processing
    }
}
```

**Problems with GoF approach:**
- Relies on developers to correctly implement `if/else` delegation logic
- Violates encapsulation with `super.handleRequest()` calls
- No guarantee handlers are chained correctly
- Null reference issues if chain ends without anchor
- Duplicated delegation logic in every concrete handler

### Improved Structure (Template Method Approach)

```php
<?php

interface RequestHandler {
    public function handleRequest(Request $request): Response;
}

abstract class DelegatingRequestHandler implements RequestHandler {
    private RequestHandler $nextHandler; // Non-nullable

    public function __construct(RequestHandler $nextHandler) {
        $this->nextHandler = $nextHandler;
    }

    /**
     * Template Method: delegation logic is fixed
     * Concrete handlers cannot override this
     */
    final public function handleRequest(Request $request): Response {
        if ($this->canHandle($request)) {
            return $this->processRequest($request);
        }

        return $this->nextHandler->handleRequest($request);
    }

    /**
     * Hook: Can this handler process the request?
     */
    abstract protected function canHandle(Request $request): bool;

    /**
     * Hook: Process the request
     */
    abstract protected function processRequest(Request $request): Response;
}

class ConcreteHandlerA extends DelegatingRequestHandler {
    protected function canHandle(Request $request): bool {
        // A-specific capability check
        return $request->getType() === 'typeA';
    }

    protected function processRequest(Request $request): Response {
        // A-specific processing
        return new Response('Processed by A');
    }
}

class ConcreteHandlerB extends DelegatingRequestHandler {
    protected function canHandle(Request $request): bool {
        // B-specific capability check
        return $request->getType() === 'typeB';
    }

    protected function processRequest(Request $request): Response {
        // B-specific processing
        return new Response('Processed by B');
    }
}

/**
 * Anchor handler: provides default behavior when no handler processes request
 */
class AnchoringRequestHandler implements RequestHandler {
    public function handleRequest(Request $request): Response {
        // Options for default behavior:
        // 1. Throw exception
        throw new UnhandledRequestException($request);

        // 2. Return default/null response
        // return Response::createDefault();

        // 3. Log and return error response
        // $this->logger->error('Unhandled request', ['request' => $request]);
        // return Response::createError('No handler available');
    }
}

/**
 * Configurer: assembles the chain
 */
class HandlerConfigurer {
    public function createHandler(): RequestHandler {
        // Build chain from end to beginning
        $anchor = new AnchoringRequestHandler();
        $handlerB = new ConcreteHandlerB($anchor);
        $handlerA = new ConcreteHandlerA($handlerB);

        return $handlerA; // Return first handler
    }
}

// Client usage
class Client {
    private RequestHandler $handler;

    public function __construct(RequestHandler $handler) {
        $this->handler = $handler;
    }

    public function processRequest(Request $request): Response {
        return $this->handler->handleRequest($request);
    }
}

// Bootstrap
$configurer = new HandlerConfigurer();
$handler = $configurer->createHandler();
$client = new Client($handler);

$response = $client->processRequest($request);
```

**Advantages of Template Method approach:**
- **Delegation logic centralized**: Only in abstract class, not duplicated
- **Cannot be broken**: `final` prevents override of delegation logic
- **Encapsulation**: Concrete handlers don't know about chain
- **Separation of concerns**: Handlers only worry about their capability
- **Safe**: Non-nullable next handler enforces proper chain construction
- **Testable**: Only two test cases for abstract handler logic

## Real-World Analogies

### Customer Support Escalation
Front-line representatives handle most issues with their authority level. Complex issues escalate to managers, then senior managers. Each level in the chain has more authority but fewer personnel. Request travels up chain until someone can resolve it or chain exhausts.

### Judicial System
Legal disputes can appeal from lower courts to higher courts. Each court level has more authority. Case proceeds until resolved, refused, or reaches highest court.

### Unix/Linux $PATH
When you type a command, the shell searches directories in `$PATH` sequentially. First match executes. This is classic Chain of Responsibility behavior.

### Caches
Memory cache → Database cache → Database. Quick but incomplete sources checked first, expensive authoritative sources checked last.

### Bloom Filters
Probabilistic filter provides fast negative confirmation (100% accurate). If filter says "maybe present", check expensive data store. Two-level chain: filter → data store.

### Switch/If-Else Statements
Traditional control structures are static Chain of Responsibility. Flow proceeds through conditions until first match executes. CoR provides dynamic, runtime-configurable alternative.

## Implementation

### Example: Address Book with Multiple Data Sources

Real-world scenario: Organizational hierarchy stored in multiple sources (cache, database, webservice) with varying accessibility per user.

Requirements:
- Users may have access to database only, webservice only, or both
- Webservice is source of truth, database is a copy
- Want caching for performance
- Design should accommodate future data sources

```php
<?php

/**
 * Domain entity
 */
class Group {
    public function __construct(
        private string $name,
        private array $members,
        private ?Group $parent = null
    ) {}

    public function getName(): string {
        return $this->name;
    }

    // Other group methods...
}

/**
 * Request/Response wrappers
 */
class GroupLookupRequest {
    public function __construct(
        private string $groupName
    ) {}

    public function getGroupName(): string {
        return $this->groupName;
    }
}

/**
 * Handler interface
 */
interface AddressBook {
    /**
     * Look up group by name
     * Returns null if not found (this handler + rest of chain)
     */
    public function getGroup(string $name): ?Group;
}

/**
 * Abstract handler with Template Method
 */
abstract class AddressBookHandler implements AddressBook {
    private AddressBook $nextAddressBook;

    public function __construct(AddressBook $nextAddressBook) {
        $this->nextAddressBook = $nextAddressBook;
    }

    /**
     * Template Method: controls chain delegation
     */
    final public function getGroup(string $name): ?Group {
        // Try to get from this handler
        $group = $this->retrieveGroup($name);

        if ($group !== null) {
            // Found it, return immediately
            return $group;
        }

        // Not found, delegate to next in chain
        $group = $this->nextAddressBook->getGroup($name);

        // If next handler found it, cache locally (Decorator pattern)
        if ($group !== null) {
            $this->storeGroup($name, $group);
        }

        return $group; // May still be null
    }

    /**
     * Hook: Try to retrieve group from this handler's source
     */
    abstract protected function retrieveGroup(string $name): ?Group;

    /**
     * Hook: Store group in this handler's source
     * (Used for cache population)
     */
    abstract protected function storeGroup(string $name, Group $group): void;
}

/**
 * Concrete Handler: In-memory cache
 */
class CacheAddressBook extends AddressBookHandler {
    private array $cache = [];

    protected function retrieveGroup(string $name): ?Group {
        return $this->cache[$name] ?? null;
    }

    protected function storeGroup(string $name, Group $group): void {
        $this->cache[$name] = $group;
    }

    /**
     * Cache invalidation support
     */
    public function invalidate(string $name): void {
        unset($this->cache[$name]);
    }
}

/**
 * Concrete Handler: Database (Adapter pattern)
 */
class DatabaseAddressBook extends AddressBookHandler {
    public function __construct(
        private PDO $database,
        AddressBook $nextAddressBook
    ) {
        parent::__construct($nextAddressBook);
    }

    protected function retrieveGroup(string $name): ?Group {
        $stmt = $this->database->prepare(
            'SELECT * FROM groups WHERE name = :name'
        );
        $stmt->execute(['name' => $name]);

        $row = $stmt->fetch(PDO::FETCH_ASSOC);

        if ($row === false) {
            return null;
        }

        return $this->hydrateGroup($row);
    }

    protected function storeGroup(string $name, Group $group): void {
        // Read-only database managed by another system
        // This method required by interface but does nothing
    }

    private function hydrateGroup(array $row): Group {
        // Build Group from database row
        return new Group(
            $row['name'],
            json_decode($row['members'], true),
            // parent fetching logic...
        );
    }
}

/**
 * Concrete Handler: Web Service (Adapter pattern)
 */
class WebServiceAddressBook extends AddressBookHandler {
    public function __construct(
        private HttpClient $httpClient,
        private string $serviceUrl,
        AddressBook $nextAddressBook
    ) {
        parent::__construct($nextAddressBook);
    }

    protected function retrieveGroup(string $name): ?Group {
        try {
            $response = $this->httpClient->get(
                $this->serviceUrl . '/groups/' . urlencode($name)
            );

            if ($response->getStatusCode() === 404) {
                return null;
            }

            $data = json_decode($response->getBody(), true);
            return $this->hydrateGroup($data);

        } catch (HttpException $e) {
            // Log error, return null to try next handler
            error_log("WebService lookup failed: " . $e->getMessage());
            return null;
        }
    }

    protected function storeGroup(string $name, Group $group): void {
        // Read-only webservice managed by another system
        // This method required by interface but does nothing
    }

    private function hydrateGroup(array $data): Group {
        return new Group(
            $data['name'],
            $data['members'],
            // parent fetching logic...
        );
    }
}

/**
 * Anchor Handler: No group found in entire chain
 */
class GroupNotFound implements AddressBook {
    public function getGroup(string $name): ?Group {
        // Default behavior: return null
        // Alternative: throw exception
        // throw new GroupNotFoundException($name);
        return null;
    }
}

/**
 * Configurer: Builds chain based on environment
 */
class AddressBookConfigurer {
    public function __construct(
        private array $config,
        private PDO $database,
        private HttpClient $httpClient
    ) {}

    public function createAddressBook(): AddressBook {
        // Always end with anchor
        $chain = new GroupNotFound();

        // Add WebService if available
        if ($this->config['webservice_enabled'] ?? false) {
            $chain = new WebServiceAddressBook(
                $this->httpClient,
                $this->config['webservice_url'],
                $chain
            );
        }

        // Add Database if available
        if ($this->config['database_enabled'] ?? false) {
            $chain = new DatabaseAddressBook(
                $this->database,
                $chain
            );
        }

        // Always add cache as first handler
        $cache = new CacheAddressBook($chain);

        // Set up cache invalidation subscriptions
        if ($this->config['cache_invalidation_enabled'] ?? false) {
            $this->setupCacheInvalidation($cache);
        }

        return $cache;
    }

    private function setupCacheInvalidation(CacheAddressBook $cache): void {
        // Observer pattern: subscribe to update notifications
        // When Group is updated/deleted in DB or WebService,
        // invalidate it from cache

        // Pseudocode:
        // $this->database->subscribe('group_updated', fn($name) => $cache->invalidate($name));
        // $this->webService->subscribe('group_updated', fn($name) => $cache->invalidate($name));
    }
}

// Usage
$configurer = new AddressBookConfigurer(
    config: [
        'database_enabled' => true,
        'webservice_enabled' => true,
        'webservice_url' => 'https://api.example.com',
        'cache_invalidation_enabled' => true
    ],
    database: $pdo,
    httpClient: $httpClient
);

$addressBook = $configurer->createAddressBook();

// Client code has no knowledge of chain composition
$group = $addressBook->getGroup('engineering');

if ($group !== null) {
    echo "Found group: " . $group->getName();
} else {
    echo "Group not found";
}
```

**Configuration Scenarios:**

```php
// Scenario 1: All sources available
// Cache → Database → WebService → GroupNotFound

// Scenario 2: WebService only
// Cache → WebService → GroupNotFound

// Scenario 3: Database only
// Cache → Database → GroupNotFound

// Scenario 4: Future - add LDAP
// Cache → Database → WebService → LDAP → GroupNotFound
```

### Example: WordPress Image Size Handler

WordPress generates multiple image sizes. Find the best match for requested dimensions.

```php
<?php

/**
 * Request for image of specific dimensions
 */
class ImageRequest {
    public function __construct(
        private int $attachmentId,
        private int $requestedWidth,
        private int $requestedHeight
    ) {}

    public function getAttachmentId(): int {
        return $this->attachmentId;
    }

    public function getRequestedWidth(): int {
        return $this->requestedWidth;
    }

    public function getRequestedHeight(): int {
        return $this->requestedHeight;
    }
}

/**
 * Handler interface
 */
interface ImageSizeHandler {
    public function getImage(ImageRequest $request): ?string;
}

/**
 * Abstract handler
 */
abstract class BaseImageSizeHandler implements ImageSizeHandler {
    private ImageSizeHandler $nextHandler;

    public function __construct(ImageSizeHandler $nextHandler) {
        $this->nextHandler = $nextHandler;
    }

    final public function getImage(ImageRequest $request): ?string {
        if ($this->canProvideImage($request)) {
            return $this->provideImage($request);
        }

        return $this->nextHandler->getImage($request);
    }

    abstract protected function canProvideImage(ImageRequest $request): bool;
    abstract protected function provideImage(ImageRequest $request): string;
}

/**
 * Concrete Handler: Thumbnail size
 */
class ThumbnailImageHandler extends BaseImageSizeHandler {
    private const MAX_WIDTH = 150;
    private const MAX_HEIGHT = 150;

    protected function canProvideImage(ImageRequest $request): bool {
        return $request->getRequestedWidth() <= self::MAX_WIDTH
            && $request->getRequestedHeight() <= self::MAX_HEIGHT;
    }

    protected function provideImage(ImageRequest $request): string {
        return wp_get_attachment_image_url(
            $request->getAttachmentId(),
            'thumbnail'
        );
    }
}

/**
 * Concrete Handler: Medium size
 */
class MediumImageHandler extends BaseImageSizeHandler {
    private const MAX_WIDTH = 300;
    private const MAX_HEIGHT = 300;

    protected function canProvideImage(ImageRequest $request): bool {
        return $request->getRequestedWidth() <= self::MAX_WIDTH
            && $request->getRequestedHeight() <= self::MAX_HEIGHT;
    }

    protected function provideImage(ImageRequest $request): string {
        return wp_get_attachment_image_url(
            $request->getAttachmentId(),
            'medium'
        );
    }
}

/**
 * Concrete Handler: Large size
 */
class LargeImageHandler extends BaseImageSizeHandler {
    private const MAX_WIDTH = 1024;
    private const MAX_HEIGHT = 1024;

    protected function canProvideImage(ImageRequest $request): bool {
        return $request->getRequestedWidth() <= self::MAX_WIDTH
            && $request->getRequestedHeight() <= self::MAX_HEIGHT;
    }

    protected function provideImage(ImageRequest $request): string {
        return wp_get_attachment_image_url(
            $request->getAttachmentId(),
            'large'
        );
    }
}

/**
 * Anchor Handler: Full size
 */
class FullSizeImageHandler implements ImageSizeHandler {
    public function getImage(ImageRequest $request): ?string {
        return wp_get_attachment_image_url(
            $request->getAttachmentId(),
            'full'
        );
    }
}

/**
 * Configurer
 */
class ImageSizeHandlerFactory {
    public function create(): ImageSizeHandler {
        // Build chain: smallest to largest
        $full = new FullSizeImageHandler();
        $large = new LargeImageHandler($full);
        $medium = new MediumImageHandler($large);
        $thumbnail = new ThumbnailImageHandler($medium);

        return $thumbnail;
    }
}

// Usage
$factory = new ImageSizeHandlerFactory();
$handler = $factory->create();

$request = new ImageRequest(
    attachmentId: 123,
    requestedWidth: 250,
    requestedHeight: 250
);

$imageUrl = $handler->getImage($request);
```

### Example: Payment Gateway Chain

Try multiple payment gateways in order of preference.

```php
<?php

/**
 * Payment request
 */
class PaymentRequest {
    public function __construct(
        private float $amount,
        private string $currency,
        private array $paymentMethod
    ) {}

    public function getAmount(): float {
        return $this->amount;
    }

    public function getCurrency(): string {
        return $this->currency;
    }

    public function getPaymentMethod(): array {
        return $this->paymentMethod;
    }
}

/**
 * Payment response
 */
class PaymentResponse {
    public function __construct(
        private bool $success,
        private ?string $transactionId = null,
        private ?string $error = null
    ) {}

    public function isSuccess(): bool {
        return $this->success;
    }
}

/**
 * Handler interface
 */
interface PaymentGateway {
    public function processPayment(PaymentRequest $request): PaymentResponse;
}

/**
 * Abstract handler
 */
abstract class BasePaymentGateway implements PaymentGateway {
    private PaymentGateway $nextGateway;

    public function __construct(PaymentGateway $nextGateway) {
        $this->nextGateway = $nextGateway;
    }

    final public function processPayment(PaymentRequest $request): PaymentResponse {
        if ($this->canProcess($request)) {
            try {
                return $this->attemptPayment($request);
            } catch (GatewayException $e) {
                // Log error and try next gateway
                error_log("Gateway failed: " . $e->getMessage());
            }
        }

        return $this->nextGateway->processPayment($request);
    }

    abstract protected function canProcess(PaymentRequest $request): bool;
    abstract protected function attemptPayment(PaymentRequest $request): PaymentResponse;
}

/**
 * Concrete Handler: Stripe
 */
class StripeGateway extends BasePaymentGateway {
    protected function canProcess(PaymentRequest $request): bool {
        // Check if Stripe supports this currency and payment method
        return in_array($request->getCurrency(), ['USD', 'EUR', 'GBP']);
    }

    protected function attemptPayment(PaymentRequest $request): PaymentResponse {
        // Stripe API call
        $stripe = new \Stripe\StripeClient($this->apiKey);

        $intent = $stripe->paymentIntents->create([
            'amount' => $request->getAmount() * 100,
            'currency' => strtolower($request->getCurrency()),
            'payment_method' => $request->getPaymentMethod()['id'],
        ]);

        return new PaymentResponse(
            success: true,
            transactionId: $intent->id
        );
    }
}

/**
 * Concrete Handler: PayPal
 */
class PayPalGateway extends BasePaymentGateway {
    protected function canProcess(PaymentRequest $request): bool {
        // PayPal supports more currencies
        return true;
    }

    protected function attemptPayment(PaymentRequest $request): PaymentResponse {
        // PayPal API call
        // Implementation details...

        return new PaymentResponse(
            success: true,
            transactionId: 'PAYPAL-123'
        );
    }
}

/**
 * Anchor Handler: All gateways failed
 */
class PaymentFailedHandler implements PaymentGateway {
    public function processPayment(PaymentRequest $request): PaymentResponse {
        return new PaymentResponse(
            success: false,
            error: 'No payment gateway available'
        );
    }
}

/**
 * Configurer
 */
class PaymentGatewayFactory {
    public function create(array $config): PaymentGateway {
        $failed = new PaymentFailedHandler();

        $chain = $failed;

        // Add gateways in reverse order of preference
        if ($config['paypal_enabled'] ?? false) {
            $chain = new PayPalGateway($chain);
        }

        if ($config['stripe_enabled'] ?? false) {
            $chain = new StripeGateway($chain);
        }

        return $chain;
    }
}

// Usage
$factory = new PaymentGatewayFactory();
$gateway = $factory->create([
    'stripe_enabled' => true,
    'paypal_enabled' => true,
]);

$request = new PaymentRequest(
    amount: 99.99,
    currency: 'USD',
    paymentMethod: ['type' => 'card', 'id' => 'pm_123']
);

$response = $gateway->processPayment($request);
```

### Example: WordPress Hook Priority Chain

Simulate WordPress hook priorities as Chain of Responsibility.

```php
<?php

/**
 * Hook callback with priority
 */
class HookCallback {
    public function __construct(
        private Closure $callback,
        private int $priority,
        private int $acceptedArgs
    ) {}

    public function getPriority(): int {
        return $this->priority;
    }

    public function execute(array $args): mixed {
        return call_user_func_array(
            $this->callback,
            array_slice($args, 0, $this->acceptedArgs)
        );
    }
}

/**
 * Handler interface
 */
interface HookHandler {
    public function applyFilters(string $tag, mixed $value, array $args = []): mixed;
}

/**
 * Abstract handler for filter chains
 */
abstract class BaseHookHandler implements HookHandler {
    private ?HookHandler $nextHandler = null;

    public function __construct(?HookHandler $nextHandler = null) {
        $this->nextHandler = $nextHandler;
    }

    final public function applyFilters(string $tag, mixed $value, array $args = []): mixed {
        // Apply this handler's filter
        $value = $this->applyFilter($tag, $value, $args);

        // Continue to next handler if exists
        if ($this->nextHandler !== null) {
            return $this->nextHandler->applyFilters($tag, $value, $args);
        }

        return $value;
    }

    abstract protected function applyFilter(string $tag, mixed $value, array $args): mixed;
}

/**
 * Concrete Handler: Single callback
 */
class CallbackHookHandler extends BaseHookHandler {
    public function __construct(
        private HookCallback $callback,
        ?HookHandler $nextHandler = null
    ) {
        parent::__construct($nextHandler);
    }

    protected function applyFilter(string $tag, mixed $value, array $args): mixed {
        return $this->callback->execute([$value, ...$args]);
    }
}

/**
 * Anchor: No more filters
 */
class TerminalHookHandler implements HookHandler {
    public function applyFilters(string $tag, mixed $value, array $args = []): mixed {
        return $value;
    }
}

/**
 * Configurer: Builds chain from hook callbacks
 */
class HookChainBuilder {
    /**
     * Build chain from array of callbacks sorted by priority
     */
    public function build(array $callbacks): HookHandler {
        // Sort by priority
        usort($callbacks, fn($a, $b) => $a->getPriority() <=> $b->getPriority());

        // Build chain from end to beginning
        $chain = new TerminalHookHandler();

        foreach (array_reverse($callbacks) as $callback) {
            $chain = new CallbackHookHandler($callback, $chain);
        }

        return $chain;
    }
}

// Usage
$builder = new HookChainBuilder();

$callbacks = [
    new HookCallback(
        callback: fn($value) => strtoupper($value),
        priority: 10,
        acceptedArgs: 1
    ),
    new HookCallback(
        callback: fn($value) => trim($value),
        priority: 5,
        acceptedArgs: 1
    ),
    new HookCallback(
        callback: fn($value) => str_replace(' ', '-', $value),
        priority: 20,
        acceptedArgs: 1
    ),
];

$chain = $builder->build($callbacks);

$result = $chain->applyFilters('the_title', '  Hello World  ');
// Result: "HELLO-WORLD"
// Execution order: trim (5) → uppercase (10) → replace spaces (20)
```

## Chain of Responsibility vs Decorator

Both patterns have nearly identical structure but different behavior:

### Similarities
- Both use linked lists via delegation
- Both have abstract class with self-reference
- Both can share same delegate reference
- UML diagrams look almost identical

### Differences

| Aspect | Chain of Responsibility | Decorator |
|--------|------------------------|-----------|
| **Traversal** | Stops at first handler that processes request | Always traverses entire chain |
| **Execution** | Only one handler executes | All decorators execute |
| **Return path** | May return early | Always reaches anchor and returns |
| **Purpose** | Find appropriate handler | Add layers of behavior |
| **Behavior** | Mutually exclusive handlers | Cumulative behavior |
| **Example** | Exception handlers: first catch processes | Logging: all loggers execute |

```php
<?php

// Chain of Responsibility: stops at first match
abstract class ChainHandler {
    final public function handle($request) {
        if ($this->canHandle($request)) {
            return $this->process($request); // STOP HERE
        }
        return $this->next->handle($request);
    }
}

// Decorator: always continues
abstract class Decorator {
    final public function execute($request) {
        // Do something before
        $result = $this->next->execute($request); // ALWAYS CONTINUE
        // Do something after
        return $result;
    }
}
```

## Testing

### Unit Testing Abstract Handler

```php
<?php

use PHPUnit\Framework\TestCase;

class DelegatingRequestHandlerTest extends TestCase {
    private RequestHandler $mockNextHandler;
    private DelegatingRequestHandler $handler;

    protected function setUp(): void {
        $this->mockNextHandler = $this->createMock(RequestHandler::class);

        // Create anonymous concrete class for testing
        $this->handler = new class($this->mockNextHandler) extends DelegatingRequestHandler {
            private bool $canHandleResponse = false;
            private ?Response $processResponse = null;

            public function setCanHandle(bool $value): void {
                $this->canHandleResponse = $value;
            }

            public function setProcessResponse(Response $response): void {
                $this->processResponse = $response;
            }

            protected function canHandle(Request $request): bool {
                return $this->canHandleResponse;
            }

            protected function processRequest(Request $request): Response {
                return $this->processResponse;
            }
        };
    }

    public function testProcessesRequestWhenCanHandle(): void {
        $request = new Request('test');
        $expectedResponse = new Response('handled');

        $this->handler->setCanHandle(true);
        $this->handler->setProcessResponse($expectedResponse);

        // Next handler should NOT be called
        $this->mockNextHandler
            ->expects($this->never())
            ->method('handleRequest');

        $response = $this->handler->handleRequest($request);

        $this->assertSame($expectedResponse, $response);
    }

    public function testDelegatesToNextHandlerWhenCannotHandle(): void {
        $request = new Request('test');
        $expectedResponse = new Response('delegated');

        $this->handler->setCanHandle(false);

        // Next handler SHOULD be called
        $this->mockNextHandler
            ->expects($this->once())
            ->method('handleRequest')
            ->with($request)
            ->willReturn($expectedResponse);

        $response = $this->handler->handleRequest($request);

        $this->assertSame($expectedResponse, $response);
    }
}
```

### Integration Testing Chain

```php
<?php

use PHPUnit\Framework\TestCase;

class AddressBookChainTest extends TestCase {
    private PDO $database;
    private HttpClient $httpClient;

    public function testFindsGroupInCache(): void {
        $configurer = new AddressBookConfigurer(
            config: ['database_enabled' => false, 'webservice_enabled' => false],
            database: $this->database,
            httpClient: $this->httpClient
        );

        $addressBook = $configurer->createAddressBook();

        // Pre-populate cache
        $group = new Group('engineering', ['user1', 'user2']);
        $addressBook->storeGroup('engineering', $group);

        // Should find in cache without hitting database/webservice
        $found = $addressBook->getGroup('engineering');

        $this->assertNotNull($found);
        $this->assertEquals('engineering', $found->getName());
    }

    public function testFallsBackToDatabaseWhenNotInCache(): void {
        // Mock database to return group
        $this->database
            ->expects($this->once())
            ->method('prepare')
            ->willReturn($stmt);

        $configurer = new AddressBookConfigurer(
            config: ['database_enabled' => true, 'webservice_enabled' => false],
            database: $this->database,
            httpClient: $this->httpClient
        );

        $addressBook = $configurer->createAddressBook();

        $found = $addressBook->getGroup('marketing');

        $this->assertNotNull($found);
    }

    public function testReturnsNullWhenGroupNotFoundAnywhere(): void {
        $configurer = new AddressBookConfigurer(
            config: ['database_enabled' => true, 'webservice_enabled' => true],
            database: $this->database,
            httpClient: $this->httpClient
        );

        $addressBook = $configurer->createAddressBook();

        $found = $addressBook->getGroup('nonexistent');

        $this->assertNull($found);
    }

    public function testPopulatesCacheFromDatabase(): void {
        // First call hits database
        $addressBook = $this->createAddressBook();
        $group = $addressBook->getGroup('sales');
        $this->assertNotNull($group);

        // Second call should hit cache, not database
        // (Would need to mock database to verify no second call)
        $cachedGroup = $addressBook->getGroup('sales');
        $this->assertSame($group, $cachedGroup);
    }
}
```

## When to Use

Use Chain of Responsibility when:

1. **Multiple handlers** can process a request, but only one should
2. **Handler selection** should be determined at runtime
3. **Request sender** shouldn't know which specific handler processes it
4. **Handler set** can change dynamically
5. **Resource optimization** - check cheap handlers before expensive ones
6. **Separation of concerns** - each handler focuses on one responsibility

Common use cases:
- **Exception handling** - catch specific exceptions at appropriate levels
- **Caching layers** - memory cache → disk cache → database
- **Authentication/authorization** - check permissions at different levels
- **Validation** - apply different validation rules in sequence
- **Logging** - route messages to appropriate log handlers
- **Request processing** - HTTP middleware, API gateways
- **Event handling** - propagate events through handler hierarchy
- **Command processing** - parse and route commands to handlers
- **Customer support** - escalate through support tiers

## When NOT to Use

Avoid Chain of Responsibility when:

1. **Every handler must execute** - use Decorator or Composite instead
2. **Handler order doesn't matter** - use Strategy or collection of handlers
3. **Single handler is always known** - use direct invocation
4. **Performance critical** - chain traversal adds overhead
5. **Simple if/else sufficient** - don't over-engineer

## Advantages

1. **Decoupling**: Sender doesn't know which handler processes request
2. **Flexibility**: Add/remove/reorder handlers dynamically
3. **Single Responsibility**: Each handler has one concern
4. **Open/Closed Principle**: Add handlers without modifying existing code
5. **Runtime configuration**: Build different chains for different contexts
6. **Resource optimization**: Check cheap handlers first
7. **Testability**: Test handlers independently
8. **No replicated conditionals**: Avoid duplicated switch/if-else blocks

## Disadvantages

1. **Debugging difficulty**: Request path through chain not obvious
2. **No guarantee of handling**: Request might go unhandled
3. **Performance overhead**: Traversing chain takes time
4. **Complex configuration**: Requires external configuration logic
5. **Integration testing needed**: Verify composed behavior
6. **More classes**: Each handler needs separate class

## Related Patterns

### Foundation: Strategy
Chain of Responsibility extends Strategy. Each handler is a strategy, but CoR chains them together so multiple strategies can be attempted sequentially.

### Structure: Template Method
Template Method moves delegation logic into abstract class, preventing concrete handlers from breaking the chain pattern.

### Structure: Decorator
Nearly identical structure but different behavior. Decorator always executes all elements; CoR stops at first match.

### Adapter
Often used within handlers to integrate external systems (databases, web services, APIs).

### Observer
Used for cache invalidation and keeping chain elements synchronized with external data sources.

### Composite
Similar tree-like structure, but Composite is for part-whole hierarchies where all components are treated uniformly.

### Command
Commands can be organized in a CoR chain for undo/redo functionality or command processing pipelines.

## Key Insights

### Dynamic Alternative to Static Control Structures
Switch statements and cascading if/else are static, compile-time Chain of Responsibility. The pattern provides runtime flexibility to:
- Change handler order without code changes
- Add/remove handlers dynamically
- Compose different chains for different contexts
- Test handlers independently

### Resource Optimization Through Ordering
Order handlers from least to most expensive:
- Memory cache → Disk cache → Database → Web service
- Front-line support → Manager → Senior manager → Executive
- Local search → Network search → AI search

### Encapsulation of Handler Logic
Concrete handlers don't know they're in a chain. They only answer two questions:
1. Can I handle this request?
2. How do I handle this request?

The abstract handler manages chain traversal, not concrete handlers.

### Guarantee Chain Completion with Anchor
Always end chain with anchor handler that provides default behavior:
- Return null/empty result
- Throw exception
- Return error response
- Log unhandled request

Never allow chain to end with null reference.

### Combining with Decorator
The Address Book example shows CoR and Decorator working together. When a handler finds data in a subsequent handler, it caches locally (Decorator behavior) while still maintaining CoR semantics (only one handler provides the data).

### Template Method Ensures Correctness
Moving delegation logic into abstract handler with Template Method:
- Prevents concrete handlers from breaking chain logic
- Eliminates duplicated delegation code
- Reduces testing burden (only two test cases for abstract handler)
- Removes reliance on developer discipline
- Makes pattern impossible to misuse

### Configuration is Unstable and Separate
Chain composition resides in Configurer, which is unstable/flexible. This is intentional:
- Business rules for chain composition change frequently
- Environment affects handler availability
- Same handlers, different compositions for different contexts
- Configuration changes don't affect stable handler implementations

### Multiple Patterns Working Together
Real-world CoR rarely works alone:
- **Strategy** - foundation pattern
- **Template Method** - structure for delegation
- **Decorator** - supplemental behavior on delegation path
- **Adapter** - integrate external systems
- **Observer** - synchronize chain elements
- **Factory** - create handler instances

### WordPress Context
WordPress core uses CoR-like patterns:
- **Plugin/theme hooks** - filters and actions form chains
- **Rewrite rules** - URL matching tries patterns in order
- **Taxonomy queries** - try different query strategies
- **Image size selection** - find best matching size
- **Authentication** - try different auth methods

## Summary

Chain of Responsibility delegates requests through a linked chain of handlers until one processes it. Each handler decides whether to process the request or delegate to the next handler.

Key characteristics:
- **Single handler executes** - stops at first match
- **Dynamic composition** - build chains at runtime
- **Resource optimization** - cheap handlers before expensive
- **Decoupling** - sender doesn't know which handler executes
- **Template Method structure** - delegation logic in abstract class

The pattern transforms static switch/if-else statements into flexible, testable, composable handler chains that can be reconfigured at runtime without code changes.

## References

### Articles
- [Wikipedia: Chain of Responsibility](https://en.wikipedia.org/wiki/Chain-of-responsibility_pattern)
- [Refactoring Guru: Chain of Responsibility](https://refactoring.guru/design-patterns/chain-of-responsibility)
- [Source Making: Chain of Responsibility](https://sourcemaking.com/design_patterns/chain_of_responsibility)

### Books
- Gang of Four: Design Patterns, page 223
- Clean Code: Design Patterns, Episode 34

### Original Source
- [Jim Humelsine's blog: Chain of Responsibility Design Pattern](https://jhumelsine.github.io/2024/02/20/chain-of-responsibility-design-pattern.html)
