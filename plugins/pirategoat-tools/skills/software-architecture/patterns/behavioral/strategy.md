# Strategy Design Pattern

A comprehensive deep-dive reference for the Strategy design pattern, covering when to use it, how to implement it, common pitfalls, and relationships with other patterns.

## Quick Reference

| Aspect | Description |
|--------|-------------|
| **Pattern Type** | Behavioral |
| **Intent** | Define a family of algorithms, encapsulate each one, and make them interchangeable |
| **Key Principle** | Separate **what** the client wants to accomplish from **how** it will be accomplished |
| **Main Benefit** | Runtime algorithm selection without tight coupling |
| **Trade-off** | Increased number of classes |
| **Also Known As** | Policy Pattern |

---

## Overview

The Strategy pattern encapsulates interchangeable behaviors behind a common interface, allowing the client application to delegate behavior without depending on concrete implementations. Like choosing different routes to reach the same destination, Strategy lets you swap algorithms at runtime while keeping the client code unchanged.

### Core Concept

> "There's more than one way to skin a cat" - but the client shouldn't need to know how each method works.

Strategy leverages **Separation of Concerns** and **Modularity** to separate:
- **What** the client application wishes to accomplish (defined by the interface)
- **How** it will be accomplished (implemented by concrete strategies)

### Visual Metaphor: Settlers of Catan

Victory in Settlers of Catan can be achieved through multiple strategies:
- Building the longest road
- Creating the largest army
- Settling cities and collecting development cards

Each strategy leads to victory points, but players choose different paths. The Strategy pattern supports multiple paths to accomplish a goal similarly.

---

## When to Use

### Primary Use Cases

1. **External Dependencies**
   - Database access (MySQL, PostgreSQL, MongoDB)
   - API integrations (REST, GraphQL, SOAP)
   - File storage (local, S3, cloud storage)
   - Payment gateways (Stripe, PayPal, Square)

2. **Multiple Algorithms**
   - Sorting strategies (bubble sort, quick sort, merge sort)
   - Compression algorithms (gzip, zip, tar)
   - Search algorithms (linear, binary, hash-based)
   - Validation rules (email, phone, tax ID)

3. **Cross-Team Dependencies**
   - Services provided by other teams
   - Third-party libraries that may change
   - Components in different system layers

4. **Testing Support**
   - Enable test doubles (mocks, stubs, fakes)
   - Decouple client code from concrete implementations
   - Test client-strategy interaction independently

5. **Configuration-Driven Behavior**
   - User-selected preferences
   - Environment-specific implementations
   - Feature flags and A/B testing

### When Consider Strategy

```php
// Consider Strategy when:
// ✓ Delegating to behavior with external dependencies
class OrderProcessor {
    public function __construct(
        private PaymentGateway $gateway,      // Strategy for payments
        private ShippingProvider $shipping,   // Strategy for shipping
        private TaxCalculator $taxCalc        // Strategy for tax
    ) {}
}

// ✓ Multiple implementations exist or are likely
interface CacheStrategy {
    public function get(string $key): mixed;
    public function set(string $key, mixed $value, int $ttl): void;
}
// Implementations: RedisCache, MemcachedCache, FileCache, DatabaseCache

// ✓ Algorithm complexity warrants isolation
interface ValidationStrategy {
    public function validate(array $data): ValidationResult;
}
// Implementations: EmailValidator, PhoneValidator, AddressValidator
```

### Size and Sophistication Threshold

**Do use Strategy for:**
- Large, sophisticated classes with complex behavior
- Classes with multiple conditional branches for different algorithms
- Behavior that requires external resources or network calls
- Code that needs to be tested independently

**Consider skipping Strategy for:**
- Simple utility classes (String, Math, Date)
- Pure functions with no dependencies
- Internal helper methods within your control
- Trivial operations that rarely change

---

## When NOT to Use

### Anti-Patterns and Misuse

1. **Over-Engineering Simple Code**
```php
// BAD: Overkill for a simple operation
interface StringCaseStrategy {
    public function convert(string $text): string;
}

class UpperCaseStrategy implements StringCaseStrategy {
    public function convert(string $text): string {
        return strtoupper($text);
    }
}

// GOOD: Just use the built-in function
$uppercased = strtoupper($text);
```

2. **Premature Abstraction**
```php
// BAD: Creating Strategy before you need multiple implementations
interface LoggerStrategy {
    public function log(string $message): void;
}

// GOOD: Start simple, refactor to Strategy when second implementation needed
class Logger {
    public function log(string $message): void {
        error_log($message);
    }
}
```

3. **Static Utility Classes**
```php
// DON'T use Strategy for stateless utility methods
class StringUtils {
    public static function isEmpty(string $str): bool {
        return strlen($str) === 0;
    }
}
```

4. **Single Implementation with No Variation**
- If there will only ever be one way to do something, Strategy adds unnecessary complexity
- Wait until you have a concrete need for variation

---

## Structure

### UML Class Diagram

```
┌─────────────────┐
│     Context     │
│  (Client App)   │
├─────────────────┤
│ - strategy      │◆───────► ┌──────────────────┐
├─────────────────┤           │   «interface»    │
│ + operation()   │           │     Strategy     │
└─────────────────┘           ├──────────────────┤
                              │ + algorithm()    │
                              └──────────────────┘
                                       △
                                       │
                      ┌────────────────┼────────────────┐
                      │                │                │
              ┌───────┴──────┐  ┌──────┴──────┐  ┌─────┴────────┐
              │ConcreteStratA│  │ConcreteStratB│  │ConcreteStratC│
              ├──────────────┤  ├─────────────┤  ├──────────────┤
              │+ algorithm() │  │+ algorithm()│  │+ algorithm() │
              └──────────────┘  └─────────────┘  └──────────────┘
```

### Components

1. **Strategy Interface**
   - Declares the contract from the client's perspective
   - Defines methods the client needs, not what implementations can provide
   - May contain multiple cohesive methods (not just one)
   - Example: Persistence strategy with CRUD operations

2. **Context (Client Application)**
   - Maintains a reference to a Strategy object
   - Delegates behavior to the Strategy
   - Knows the Strategy interface, not concrete implementations
   - Programs to an interface, not an implementation

3. **Concrete Strategies**
   - Implement the Strategy interface
   - Provide specific algorithms/behaviors
   - Independent of client application
   - Can be added without modifying existing code

---

## How It Works

### Basic Flow

1. **Client application** holds a reference to the Strategy interface
2. When behavior is needed, **client delegates** to the Strategy
3. **Strategy implementation** performs the work
4. **Result** (if any) is returned to the client
5. Strategy reference can be **changed at runtime** for different behavior

### Reference Resolution

The Gang of Four provide vague guidance on how concrete Strategy references are resolved. Common approaches:

1. **Constructor Injection** (Dependency Injection)
```php
class OrderProcessor {
    public function __construct(
        private PaymentGateway $gateway
    ) {}
}

// Resolved at creation
$processor = new OrderProcessor(new StripeGateway());
```

2. **Factory Method**
```php
class PaymentGatewayFactory {
    public function create(string $type): PaymentGateway {
        return match($type) {
            'stripe' => new StripeGateway(),
            'paypal' => new PayPalGateway(),
            default => throw new InvalidArgumentException()
        };
    }
}
```

3. **Setter Injection** (Runtime Changes)
```php
class Report {
    private OutputStrategy $formatter;

    public function setFormatter(OutputStrategy $formatter): void {
        $this->formatter = $formatter;
    }

    public function generate(): string {
        return $this->formatter->format($this->data);
    }
}

$report->setFormatter(new PdfFormatter());
$pdf = $report->generate();

$report->setFormatter(new ExcelFormatter());
$excel = $report->generate();
```

4. **Strategy Collections**
```php
class Renderer {
    /** @var Shape[] */
    private array $shapes;

    public function addShape(Shape $shape): void {
        $this->shapes[] = $shape;
    }

    public function render(): void {
        foreach ($this->shapes as $shape) {
            $shape->draw();  // Each shape has its own strategy
        }
    }
}
```

---

## Real-World Examples

### Example 1: CAD Application - Shape Rendering

A Computer Aided Design (CAD) application needs to render different shapes. Each shape knows how to draw itself.

```php
/**
 * Strategy interface - defines what the client needs
 */
interface Shape {
    public function draw(Canvas $canvas): void;
    public function area(): float;
    public function perimeter(): float;
}

/**
 * Concrete Strategy - Circle
 */
class Circle implements Shape {
    public function __construct(
        private float $radius,
        private Point $center
    ) {}

    public function draw(Canvas $canvas): void {
        $canvas->drawCircle(
            $this->center->x,
            $this->center->y,
            $this->radius
        );
    }

    public function area(): float {
        return M_PI * $this->radius ** 2;
    }

    public function perimeter(): float {
        return 2 * M_PI * $this->radius;
    }
}

/**
 * Concrete Strategy - Rectangle
 */
class Rectangle implements Shape {
    public function __construct(
        private float $width,
        private float $height,
        private Point $topLeft
    ) {}

    public function draw(Canvas $canvas): void {
        $canvas->drawRectangle(
            $this->topLeft->x,
            $this->topLeft->y,
            $this->width,
            $this->height
        );
    }

    public function area(): float {
        return $this->width * $this->height;
    }

    public function perimeter(): float {
        return 2 * ($this->width + $this->height);
    }
}

/**
 * Concrete Strategy - Triangle
 */
class Triangle implements Shape {
    public function __construct(
        private Point $p1,
        private Point $p2,
        private Point $p3
    ) {}

    public function draw(Canvas $canvas): void {
        $canvas->drawPolygon([$this->p1, $this->p2, $this->p3]);
    }

    public function area(): float {
        // Heron's formula
        $a = $this->distance($this->p1, $this->p2);
        $b = $this->distance($this->p2, $this->p3);
        $c = $this->distance($this->p3, $this->p1);
        $s = ($a + $b + $c) / 2;
        return sqrt($s * ($s - $a) * ($s - $b) * ($s - $c));
    }

    public function perimeter(): float {
        return $this->distance($this->p1, $this->p2)
             + $this->distance($this->p2, $this->p3)
             + $this->distance($this->p3, $this->p1);
    }

    private function distance(Point $p1, Point $p2): float {
        return sqrt(($p2->x - $p1->x) ** 2 + ($p2->y - $p1->y) ** 2);
    }
}

/**
 * Client Application - doesn't depend on concrete shapes
 */
class ComputerAidedDesign {
    /** @var Shape[] */
    private array $shapes = [];

    public function addShape(Shape $shape): void {
        $this->shapes[] = $shape;
    }

    public function render(Canvas $canvas): void {
        foreach ($this->shapes as $shape) {
            $shape->draw($canvas);
        }
    }

    public function calculateTotalArea(): float {
        return array_reduce(
            $this->shapes,
            fn($total, $shape) => $total + $shape->area(),
            0.0
        );
    }
}

// Usage
$cad = new ComputerAidedDesign();
$cad->addShape(new Circle(5.0, new Point(10, 10)));
$cad->addShape(new Rectangle(20, 15, new Point(0, 0)));
$cad->addShape(new Triangle(
    new Point(0, 0),
    new Point(10, 0),
    new Point(5, 10)
));

$canvas = new Canvas(800, 600);
$cad->render($canvas);

// Easy to add new shapes without changing existing code
class Oval implements Shape {
    // ... implementation
}
$cad->addShape(new Oval(10, 15, new Point(50, 50)));
```

**Key Points:**
- CAD doesn't depend on concrete shape classes
- New shapes can be added without modifying CAD
- Each shape encapsulates its own drawing logic
- Collection of different shapes works seamlessly

### Example 2: Payment Processing

Different payment gateways with a common interface.

```php
/**
 * Strategy interface
 */
interface PaymentGateway {
    public function authorize(Order $order): AuthorizationResult;
    public function capture(string $authorizationId, float $amount): CaptureResult;
    public function refund(string $transactionId, float $amount): RefundResult;
}

/**
 * Concrete Strategy - Stripe
 */
class StripeGateway implements PaymentGateway {
    public function __construct(
        private string $apiKey,
        private StripeClient $client
    ) {}

    public function authorize(Order $order): AuthorizationResult {
        $intent = $this->client->paymentIntents->create([
            'amount' => $order->getTotal() * 100, // Stripe uses cents
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

        return new CaptureResult(
            success: true,
            transactionId: $intent->id
        );
    }

    public function refund(string $transactionId, float $amount): RefundResult {
        $refund = $this->client->refunds->create([
            'payment_intent' => $transactionId,
            'amount' => $amount * 100,
        ]);

        return new RefundResult(
            success: true,
            refundId: $refund->id
        );
    }
}

/**
 * Concrete Strategy - PayPal
 */
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
                'amount' => [
                    'currency_code' => 'USD',
                    'value' => $order->getTotal()
                ]
            ]]
        ];

        $response = $this->client->execute($request);

        return new AuthorizationResult(
            success: true,
            authorizationId: $response->result->id,
            message: 'Authorized via PayPal'
        );
    }

    public function capture(string $authorizationId, float $amount): CaptureResult {
        $request = new AuthorizationsCaptureRequest($authorizationId);
        $response = $this->client->execute($request);

        return new CaptureResult(
            success: true,
            transactionId: $response->result->id
        );
    }

    public function refund(string $transactionId, float $amount): RefundResult {
        $request = new CapturesRefundRequest($transactionId);
        $request->body = [
            'amount' => [
                'value' => $amount,
                'currency_code' => 'USD'
            ]
        ];

        $response = $this->client->execute($request);

        return new RefundResult(
            success: true,
            refundId: $response->result->id
        );
    }
}

/**
 * Client Application
 */
class OrderProcessor {
    public function __construct(
        private PaymentGateway $gateway
    ) {}

    public function processOrder(Order $order): ProcessResult {
        // Authorize payment
        $auth = $this->gateway->authorize($order);
        if (!$auth->success) {
            return ProcessResult::failed('Authorization failed');
        }

        try {
            // Fulfill order
            $order->fulfill();

            // Capture payment
            $capture = $this->gateway->capture(
                $auth->authorizationId,
                $order->getTotal()
            );

            if ($capture->success) {
                $order->complete($capture->transactionId);
                return ProcessResult::success($capture->transactionId);
            }

            return ProcessResult::failed('Capture failed');

        } catch (Exception $e) {
            // Refund if fulfillment fails
            $this->gateway->refund(
                $auth->authorizationId,
                $order->getTotal()
            );

            return ProcessResult::failed($e->getMessage());
        }
    }
}

// Usage - Strategy selected at runtime based on customer preference
$gateway = match($order->getPaymentMethod()) {
    'stripe' => new StripeGateway($stripeKey, $stripeClient),
    'paypal' => new PayPalGateway($paypalId, $paypalSecret, $paypalClient),
    default => throw new InvalidArgumentException('Unknown payment method')
};

$processor = new OrderProcessor($gateway);
$result = $processor->processOrder($order);
```

### Example 3: Sorting Strategies

Different sorting algorithms for different scenarios.

```php
/**
 * Strategy interface
 */
interface SortStrategy {
    /**
     * @param array $items Items to sort
     * @return array Sorted items
     */
    public function sort(array $items): array;
}

/**
 * Concrete Strategy - Quick Sort (fast for large datasets)
 */
class QuickSort implements SortStrategy {
    public function sort(array $items): array {
        if (count($items) <= 1) {
            return $items;
        }

        $pivot = $items[0];
        $left = $right = [];

        for ($i = 1; $i < count($items); $i++) {
            if ($items[$i] < $pivot) {
                $left[] = $items[$i];
            } else {
                $right[] = $items[$i];
            }
        }

        return array_merge(
            $this->sort($left),
            [$pivot],
            $this->sort($right)
        );
    }
}

/**
 * Concrete Strategy - Bubble Sort (simple, good for small/nearly sorted)
 */
class BubbleSort implements SortStrategy {
    public function sort(array $items): array {
        $n = count($items);
        for ($i = 0; $i < $n - 1; $i++) {
            $swapped = false;
            for ($j = 0; $j < $n - $i - 1; $j++) {
                if ($items[$j] > $items[$j + 1]) {
                    $temp = $items[$j];
                    $items[$j] = $items[$j + 1];
                    $items[$j + 1] = $temp;
                    $swapped = true;
                }
            }
            if (!$swapped) break;
        }
        return $items;
    }
}

/**
 * Concrete Strategy - Built-in Sort (optimized native implementation)
 */
class NativeSort implements SortStrategy {
    public function sort(array $items): array {
        sort($items);
        return $items;
    }
}

/**
 * Client Application - Smart Sorter
 */
class SmartSorter {
    private SortStrategy $strategy;

    public function __construct(?SortStrategy $strategy = null) {
        $this->strategy = $strategy ?? new NativeSort();
    }

    public function setStrategy(SortStrategy $strategy): void {
        $this->strategy = $strategy;
    }

    public function sort(array $items): array {
        return $this->strategy->sort($items);
    }

    /**
     * Automatically select best strategy based on data size
     */
    public function sortOptimal(array $items): array {
        $this->strategy = match(true) {
            count($items) < 10 => new BubbleSort(),      // Small - simple is fast
            count($items) < 1000 => new NativeSort(),    // Medium - native optimized
            default => new QuickSort()                   // Large - quick sort scales
        };

        return $this->strategy->sort($items);
    }
}

// Usage
$sorter = new SmartSorter();

// Manual strategy selection
$sorter->setStrategy(new QuickSort());
$sorted = $sorter->sort($largeArray);

// Automatic strategy selection
$sorted = $sorter->sortOptimal($unknownSizeArray);
```

---

## Implementation Guide

### Step 1: Define the Strategy Interface

```php
/**
 * Define from CLIENT'S perspective, not implementation's
 *
 * Good: "What does the client need?"
 * Bad: "What can the implementation provide?"
 */
interface CacheStrategy {
    /**
     * Retrieve value from cache
     *
     * @param string $key Cache key
     * @return mixed|null Value if found, null if not found
     */
    public function get(string $key): mixed;

    /**
     * Store value in cache
     *
     * @param string $key Cache key
     * @param mixed $value Value to store
     * @param int $ttl Time to live in seconds
     */
    public function set(string $key, mixed $value, int $ttl = 3600): void;

    /**
     * Remove value from cache
     *
     * @param string $key Cache key
     * @return bool True if removed, false if not found
     */
    public function delete(string $key): bool;

    /**
     * Check if key exists
     *
     * @param string $key Cache key
     * @return bool True if exists, false otherwise
     */
    public function has(string $key): bool;
}
```

### Step 2: Implement Concrete Strategies

```php
/**
 * Redis Implementation
 */
class RedisCache implements CacheStrategy {
    public function __construct(
        private Redis $redis,
        private string $prefix = 'app:'
    ) {}

    public function get(string $key): mixed {
        $value = $this->redis->get($this->prefix . $key);
        return $value === false ? null : unserialize($value);
    }

    public function set(string $key, mixed $value, int $ttl = 3600): void {
        $this->redis->setex(
            $this->prefix . $key,
            $ttl,
            serialize($value)
        );
    }

    public function delete(string $key): bool {
        return $this->redis->del($this->prefix . $key) > 0;
    }

    public function has(string $key): bool {
        return $this->redis->exists($this->prefix . $key) > 0;
    }
}

/**
 * File-based Implementation
 */
class FileCache implements CacheStrategy {
    public function __construct(
        private string $cachePath
    ) {
        if (!is_dir($cachePath)) {
            mkdir($cachePath, 0755, true);
        }
    }

    public function get(string $key): mixed {
        $file = $this->getFilePath($key);

        if (!file_exists($file)) {
            return null;
        }

        $data = unserialize(file_get_contents($file));

        if ($data['expires_at'] < time()) {
            $this->delete($key);
            return null;
        }

        return $data['value'];
    }

    public function set(string $key, mixed $value, int $ttl = 3600): void {
        $file = $this->getFilePath($key);
        $data = [
            'value' => $value,
            'expires_at' => time() + $ttl
        ];

        file_put_contents($file, serialize($data), LOCK_EX);
    }

    public function delete(string $key): bool {
        $file = $this->getFilePath($key);

        if (file_exists($file)) {
            return unlink($file);
        }

        return false;
    }

    public function has(string $key): bool {
        return $this->get($key) !== null;
    }

    private function getFilePath(string $key): string {
        $hash = md5($key);
        return $this->cachePath . '/' . $hash . '.cache';
    }
}

/**
 * In-memory Implementation (for testing or single-request cache)
 */
class MemoryCache implements CacheStrategy {
    private array $cache = [];
    private array $expirations = [];

    public function get(string $key): mixed {
        if (!isset($this->cache[$key])) {
            return null;
        }

        if (isset($this->expirations[$key]) && $this->expirations[$key] < time()) {
            $this->delete($key);
            return null;
        }

        return $this->cache[$key];
    }

    public function set(string $key, mixed $value, int $ttl = 3600): void {
        $this->cache[$key] = $value;
        $this->expirations[$key] = time() + $ttl;
    }

    public function delete(string $key): bool {
        if (!isset($this->cache[$key])) {
            return false;
        }

        unset($this->cache[$key], $this->expirations[$key]);
        return true;
    }

    public function has(string $key): bool {
        return $this->get($key) !== null;
    }
}
```

### Step 3: Use in Client Application

```php
/**
 * Client Application
 */
class ProductRepository {
    public function __construct(
        private Database $db,
        private CacheStrategy $cache
    ) {}

    public function findById(int $id): ?Product {
        $cacheKey = "product:{$id}";

        // Try cache first
        if ($this->cache->has($cacheKey)) {
            return $this->cache->get($cacheKey);
        }

        // Fetch from database
        $product = $this->db->fetchOne(
            'SELECT * FROM products WHERE id = ?',
            [$id]
        );

        if ($product === null) {
            return null;
        }

        // Store in cache for 1 hour
        $this->cache->set($cacheKey, $product, 3600);

        return $product;
    }

    public function save(Product $product): void {
        $this->db->save($product);

        // Invalidate cache
        $cacheKey = "product:{$product->id}";
        $this->cache->delete($cacheKey);
    }
}

// Usage - Strategy resolved via Dependency Injection
$cache = match(getenv('CACHE_DRIVER')) {
    'redis' => new RedisCache(new Redis()),
    'file' => new FileCache('/tmp/cache'),
    'memory' => new MemoryCache(),
    default => new MemoryCache()
};

$repository = new ProductRepository($db, $cache);
$product = $repository->findById(123);
```

### Step 4: Enable Testing with Test Doubles

```php
/**
 * Test Double - Spy Cache
 */
class SpyCache implements CacheStrategy {
    public array $getCalls = [];
    public array $setCalls = [];
    public array $deleteCalls = [];
    private array $storage = [];

    public function get(string $key): mixed {
        $this->getCalls[] = $key;
        return $this->storage[$key] ?? null;
    }

    public function set(string $key, mixed $value, int $ttl = 3600): void {
        $this->setCalls[] = ['key' => $key, 'value' => $value, 'ttl' => $ttl];
        $this->storage[$key] = $value;
    }

    public function delete(string $key): bool {
        $this->deleteCalls[] = $key;
        unset($this->storage[$key]);
        return true;
    }

    public function has(string $key): bool {
        return isset($this->storage[$key]);
    }
}

/**
 * Test for ProductRepository
 */
class ProductRepositoryTest extends TestCase {
    public function test_findById_uses_cache_when_available(): void {
        // Arrange
        $spy = new SpyCache();
        $product = new Product(id: 123, name: 'Test Product');
        $spy->set('product:123', $product);

        $db = $this->createMock(Database::class);
        $db->expects($this->never())->method('fetchOne'); // Should not query DB

        $repository = new ProductRepository($db, $spy);

        // Act
        $result = $repository->findById(123);

        // Assert
        $this->assertSame($product, $result);
        $this->assertContains('product:123', $spy->getCalls);
    }

    public function test_findById_queries_db_and_caches_on_miss(): void {
        // Arrange
        $spy = new SpyCache();
        $product = new Product(id: 123, name: 'Test Product');

        $db = $this->createMock(Database::class);
        $db->expects($this->once())
           ->method('fetchOne')
           ->willReturn($product);

        $repository = new ProductRepository($db, $spy);

        // Act
        $result = $repository->findById(123);

        // Assert
        $this->assertSame($product, $result);
        $this->assertCount(1, $spy->setCalls);
        $this->assertEquals('product:123', $spy->setCalls[0]['key']);
        $this->assertSame($product, $spy->setCalls[0]['value']);
    }

    public function test_save_invalidates_cache(): void {
        // Arrange
        $spy = new SpyCache();
        $product = new Product(id: 123, name: 'Test Product');

        $db = $this->createMock(Database::class);
        $repository = new ProductRepository($db, $spy);

        // Act
        $repository->save($product);

        // Assert
        $this->assertContains('product:123', $spy->deleteCalls);
    }
}
```

### JavaScript Implementation Example

```javascript
/**
 * Strategy Interface (implicit in JavaScript/TypeScript)
 */
interface ValidationStrategy {
    validate(value: string): ValidationResult;
}

/**
 * Concrete Strategy - Email Validation
 */
class EmailValidator implements ValidationStrategy {
    validate(value: string): ValidationResult {
        const pattern = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;
        const isValid = pattern.test(value);

        return {
            valid: isValid,
            errors: isValid ? [] : ['Invalid email format']
        };
    }
}

/**
 * Concrete Strategy - Phone Validation
 */
class PhoneValidator implements ValidationStrategy {
    validate(value: string): ValidationResult {
        // Remove non-numeric characters
        const cleaned = value.replace(/\D/g, '');
        const isValid = cleaned.length === 10 || cleaned.length === 11;

        return {
            valid: isValid,
            errors: isValid ? [] : ['Phone must be 10-11 digits']
        };
    }
}

/**
 * Concrete Strategy - Required Field
 */
class RequiredValidator implements ValidationStrategy {
    validate(value: string): ValidationResult {
        const isValid = value.trim().length > 0;

        return {
            valid: isValid,
            errors: isValid ? [] : ['This field is required']
        };
    }
}

/**
 * Client Application - Form Field
 */
class FormField {
    private validators: ValidationStrategy[] = [];

    constructor(
        private name: string,
        private value: string
    ) {}

    addValidator(validator: ValidationStrategy): this {
        this.validators.push(validator);
        return this;
    }

    validate(): ValidationResult {
        const errors: string[] = [];

        for (const validator of this.validators) {
            const result = validator.validate(this.value);
            if (!result.valid) {
                errors.push(...result.errors);
            }
        }

        return {
            valid: errors.length === 0,
            errors
        };
    }
}

// Usage
const emailField = new FormField('email', 'test@example.com')
    .addValidator(new RequiredValidator())
    .addValidator(new EmailValidator());

const phoneField = new FormField('phone', '555-1234')
    .addValidator(new RequiredValidator())
    .addValidator(new PhoneValidator());

const emailResult = emailField.validate();
const phoneResult = phoneField.validate();

console.log(emailResult);  // { valid: true, errors: [] }
console.log(phoneResult);  // { valid: false, errors: ['Phone must be 10-11 digits'] }
```

---

## Benefits

### 1. Flexibility and Runtime Behavior Change

```php
// Can change strategy at runtime
$report = new Report($data);

$report->setFormatter(new PdfFormatter());
$pdf = $report->generate();

$report->setFormatter(new HtmlFormatter());
$html = $report->generate();

$report->setFormatter(new ExcelFormatter());
$excel = $report->generate();
```

### 2. Open/Closed Principle

New strategies can be added without modifying existing code.

```php
// Add new strategy - no changes to Report or other formatters
class MarkdownFormatter implements OutputFormatter {
    public function format(array $data): string {
        // Convert data to Markdown
    }
}

$report->setFormatter(new MarkdownFormatter());
```

### 3. Single Responsibility Principle

Each strategy has one reason to change - its own algorithm.

```php
// CsvFormatter only changes if CSV format requirements change
// PdfFormatter only changes if PDF generation requirements change
// Report class only changes if reporting logic changes
```

### 4. Easier Testing

Client and strategies can be tested independently.

```php
// Test client with test double
$spy = new SpyFormatter();
$report = new Report($data, $spy);
$report->generate();
assert($spy->formatWasCalled === true);

// Test strategy in isolation
$formatter = new PdfFormatter();
$result = $formatter->format(['title' => 'Test']);
assertStringContains('Test', $result);
```

### 5. Reduced Conditional Complexity

Replace conditional logic with polymorphism.

```php
// Before Strategy - complex conditionals
class ShippingCalculator {
    public function calculate(Order $order, string $method): float {
        if ($method === 'standard') {
            // Standard shipping logic
        } elseif ($method === 'express') {
            // Express shipping logic
        } elseif ($method === 'overnight') {
            // Overnight shipping logic
        } elseif ($method === 'international') {
            // International shipping logic
        }
        // ... more conditions
    }
}

// After Strategy - clean delegation
interface ShippingStrategy {
    public function calculate(Order $order): float;
}

class ShippingCalculator {
    public function __construct(
        private ShippingStrategy $strategy
    ) {}

    public function calculate(Order $order): float {
        return $this->strategy->calculate($order);
    }
}
```

### 6. Configuration-Driven Behavior

Easy to configure behavior via external configuration.

```php
// config.php
return [
    'cache' => 'redis',
    'payment' => 'stripe',
    'storage' => 's3',
];

// Application bootstrap
$strategies = [
    'cache' => match($config['cache']) {
        'redis' => new RedisCache(),
        'file' => new FileCache(),
        default => new MemoryCache()
    },
    'payment' => match($config['payment']) {
        'stripe' => new StripeGateway(),
        'paypal' => new PayPalGateway(),
        default => new MockPaymentGateway()
    },
];
```

---

## Trade-offs and Limitations

### 1. Increased Number of Classes

```
Before Strategy:
- 1 class with multiple methods/conditions

After Strategy:
- 1 interface
- 1 client class
- N concrete strategy classes
```

**Mitigation:** This is a feature, not a bug. More classes with focused responsibilities are easier to understand and maintain than one large class with multiple responsibilities.

### 2. Client Must Be Aware of Strategies

The client needs to know which strategy to use (or have it injected).

```php
// Client needs to select appropriate strategy
$gateway = match($order->getPaymentMethod()) {
    'stripe' => new StripeGateway(),
    'paypal' => new PayPalGateway(),
    // ...
};
```

**Mitigation:** Use Factory Method or Dependency Injection to centralize strategy selection.

```php
class PaymentGatewayFactory {
    public function createForOrder(Order $order): PaymentGateway {
        return match($order->getPaymentMethod()) {
            'stripe' => $this->createStripeGateway(),
            'paypal' => $this->createPayPalGateway(),
            default => throw new UnsupportedPaymentMethodException()
        };
    }
}
```

### 3. Communication Overhead

Strategies may need data from the client.

```php
// Pass all data the strategy might need
interface TaxStrategy {
    public function calculate(
        float $subtotal,
        string $country,
        string $state,
        string $customerType,
        array $lineItems
    ): float;
}

// Or pass a context object
interface TaxStrategy {
    public function calculate(TaxContext $context): float;
}
```

### 4. Runtime Selection Complexity

Choosing the right strategy at runtime can introduce complexity.

```php
// Simple selection is easy
$strategy = $config['type'] === 'fast' ? new FastSort() : new SafeSort();

// Complex selection requires careful design
$strategy = match(true) {
    $size < 10 => new BubbleSort(),
    $size < 1000 && $isNearlySorted => new InsertionSort(),
    $size < 1000 => new NativeSort(),
    $isParallel => new ParallelQuickSort(),
    default => new QuickSort()
};
```

### 5. Interface Changes Impact All Strategies

If the Strategy interface changes, all concrete implementations must change.

```php
// Adding a method to the interface
interface CacheStrategy {
    public function get(string $key): mixed;
    public function set(string $key, mixed $value, int $ttl): void;
    public function delete(string $key): bool;

    // New method - all implementations must add it
    public function clear(): void;
}
```

**Mitigation:** Design interfaces carefully upfront. Use interface segregation if needed.

```php
// Split into focused interfaces if needed
interface CacheReader {
    public function get(string $key): mixed;
    public function has(string $key): bool;
}

interface CacheWriter {
    public function set(string $key, mixed $value, int $ttl): void;
    public function delete(string $key): bool;
}

interface CacheManager extends CacheReader, CacheWriter {
    public function clear(): void;
}
```

---

## Common Mistakes

### 1. Leaking Implementation Details into Interface

```php
// BAD - Interface exposes Redis-specific details
interface CacheStrategy {
    public function get(string $key): mixed;
    public function set(string $key, mixed $value, int $ttl): void;
    public function getRedisConnection(): Redis; // ❌ Redis-specific
    public function executeCommand(string $cmd): mixed; // ❌ Too low-level
}

// GOOD - Interface defines behavior, not implementation
interface CacheStrategy {
    public function get(string $key): mixed;
    public function set(string $key, mixed $value, int $ttl): void;
    public function delete(string $key): bool;
    public function has(string $key): bool;
}
```

### 2. Creating Strategy for Everything

```php
// BAD - Unnecessary Strategy for simple operations
interface StringUppercaseStrategy {
    public function uppercase(string $str): string;
}

class PhpUppercaseStrategy implements StringUppercaseStrategy {
    public function uppercase(string $str): string {
        return strtoupper($str);
    }
}

// GOOD - Just use the function
$uppercase = strtoupper($str);
```

### 3. Designing Interface from Implementation Perspective

```php
// BAD - Interface designed around what implementation can do
interface DatabaseStrategy {
    public function executeQuery(string $sql, array $params): array;
    public function getConnection(): PDO;
}

// GOOD - Interface designed around what client needs
interface ProductRepository {
    public function findById(int $id): ?Product;
    public function findAll(): array;
    public function save(Product $product): void;
    public function delete(int $id): void;
}
```

### 4. Tight Coupling Through Concrete Types

```php
// BAD - Client depends on concrete strategy
class OrderProcessor {
    private StripeGateway $gateway; // ❌ Concrete type

    public function __construct(StripeGateway $gateway) {
        $this->gateway = $gateway;
    }
}

// GOOD - Client depends on interface
class OrderProcessor {
    private PaymentGateway $gateway; // ✓ Interface type

    public function __construct(PaymentGateway $gateway) {
        $this->gateway = $gateway;
    }
}
```

### 5. Stateful Strategies Shared Across Instances

```php
// BAD - Strategy holds state, causes bugs when shared
class CountingSort implements SortStrategy {
    private int $comparisons = 0; // ❌ Shared state

    public function sort(array $items): array {
        $this->comparisons = 0;
        // sorting logic that increments $this->comparisons
        return $sorted;
    }
}

// GOOD - Strategy is stateless or state is method-scoped
class CountingSort implements SortStrategy {
    public function sort(array $items): array {
        $comparisons = 0; // ✓ Local state
        // sorting logic that increments $comparisons
        return $sorted;
    }
}
```

### 6. Not Validating Strategy Selection

```php
// BAD - No validation, runtime errors
$gateway = $_POST['payment_method']; // Could be anything!
$processor = new OrderProcessor(new $gateway()); // ❌ Dangerous

// GOOD - Validated strategy selection
$gatewayClass = match($_POST['payment_method']) {
    'stripe' => StripeGateway::class,
    'paypal' => PayPalGateway::class,
    default => throw new InvalidPaymentMethodException()
};
$processor = new OrderProcessor(new $gatewayClass());
```

---

## Pattern Relationships

### Strategy vs Command

**Similarities:**
- Both use polymorphism
- Both encapsulate behavior behind an interface
- Both allow runtime selection

**Differences:**

| Aspect | Strategy | Command |
|--------|----------|---------|
| **Intent** | Select algorithm to accomplish a goal | Objectify a function/request |
| **Focus** | What to accomplish | How to encapsulate action |
| **Interface** | May have multiple cohesive methods | Usually single execute() method |
| **Context** | Client knows it's choosing an algorithm | Client may not know it's executing command |
| **State** | Often stateless | Often holds request parameters |
| **Usage** | Accomplish client's goal | Defer/queue/log/undo operations |

```php
// Strategy - focused on accomplishing client's goal
interface PaymentGateway {
    public function authorize(Order $order): Result;
    public function capture(string $id): Result;
    public function refund(string $id): Result;
}

// Command - focused on objectifying an action
interface Command {
    public function execute(): void;
    public function undo(): void;
}

class ProcessPaymentCommand implements Command {
    public function __construct(
        private Order $order,
        private PaymentGateway $gateway // Strategy used within Command!
    ) {}

    public function execute(): void {
        $this->gateway->authorize($this->order);
    }
}
```

### Strategy vs State

Both use polymorphism for behavior variation, but context differs.

| Aspect | Strategy | State |
|--------|----------|-------|
| **What varies** | Algorithm/behavior | Object state |
| **Who controls** | Client selects strategy | State transitions itself |
| **Purpose** | Do same thing differently | Do different things based on state |
| **Replacement** | Client changes strategy | State changes itself |

```php
// Strategy - client controls which algorithm
$report->setFormatter(new PdfFormatter());

// State - state controls its own transitions
class Order {
    private OrderState $state;

    public function ship(): void {
        $this->state = $this->state->ship(); // State changes itself
    }
}
```

### Strategy vs Template Method

Both vary parts of an algorithm, but approach differs.

| Aspect | Strategy | Template Method |
|--------|----------|-----------------|
| **Technique** | Composition | Inheritance |
| **Flexibility** | High - runtime change | Low - compile-time |
| **Coupling** | Loose - interface | Tight - subclass |
| **Granularity** | Whole algorithm | Steps within algorithm |

```php
// Strategy - whole algorithm varies
interface SortStrategy {
    public function sort(array $items): array;
}

// Template Method - steps vary
abstract class DataProcessor {
    public function process(array $data): array {
        $validated = $this->validate($data);   // Step 1 - may vary
        $processed = $this->transform($validated); // Step 2 - may vary
        return $this->format($processed);      // Step 3 - may vary
    }

    abstract protected function validate(array $data): array;
    abstract protected function transform(array $data): array;
    protected function format(array $data): array {
        return $data; // Default implementation
    }
}
```

### Strategy Can Use Factory Method

Factory Method can help resolve concrete Strategy references.

```php
interface ShapeFactory {
    public function createShape(string $type): Shape;
}

class SimpleShapeFactory implements ShapeFactory {
    public function createShape(string $type): Shape {
        return match($type) {
            'circle' => new Circle(5.0),
            'rectangle' => new Rectangle(10, 20),
            'triangle' => new Triangle(/* points */),
            default => throw new InvalidArgumentException()
        };
    }
}
```

### Strategy Benefits from Dependency Injection

DI frameworks can automatically resolve Strategy dependencies.

```php
// Constructor injection
class OrderProcessor {
    public function __construct(
        private PaymentGateway $gateway,    // Resolved by DI
        private ShippingProvider $shipping, // Resolved by DI
        private TaxCalculator $taxCalc      // Resolved by DI
    ) {}
}

// Framework resolves based on configuration
$container->bind(PaymentGateway::class, StripeGateway::class);
$container->bind(ShippingProvider::class, FedExProvider::class);

$processor = $container->make(OrderProcessor::class);
```

---

## Decision Criteria

### Use Strategy When:

1. **Multiple algorithm variants exist**
   - Different ways to accomplish the same goal
   - Example: Multiple sorting algorithms, payment gateways, formatters

2. **Behavior changes at runtime**
   - User preferences
   - Configuration settings
   - A/B testing variants

3. **Complex conditionals exist**
   - Large if/else or switch statements selecting algorithms
   - Difficult to maintain conditional logic

4. **External dependencies are involved**
   - Database access
   - API calls
   - File system operations
   - Network services

5. **Testing requires decoupling**
   - Need to test client independently of concrete implementations
   - Want to use test doubles (mocks, stubs, spies)

6. **Open/Closed Principle is important**
   - Want to add new behaviors without modifying existing code
   - Extending functionality is frequent

### Don't Use Strategy When:

1. **Only one implementation**
   - No variation exists or is likely
   - Premature abstraction adds complexity

2. **Simple utility functions**
   - Built-in functions suffice
   - No dependencies or state

3. **Algorithms are tightly coupled**
   - Strategies would need extensive knowledge of client
   - Too much context passing required

4. **Performance is critical**
   - Polymorphism overhead is unacceptable
   - Direct calls are measurably faster

5. **Algorithm is trivial**
   - Abstraction costs more than it provides
   - Code is easier to understand without pattern

### Questions to Ask:

1. Are there multiple ways to accomplish this goal?
2. Might the algorithm/behavior change at runtime?
3. Do I need to test the client independently of the implementation?
4. Does this involve external dependencies (database, API, file system)?
5. Would adding new behaviors require modifying existing code?
6. Is there complex conditional logic selecting between algorithms?

**If you answer "yes" to 2+ questions, Strategy is likely appropriate.**

---

## Quotes

> "The Strategy pattern lets you separate the concerns of how something is done from the concern of selecting which approach to use."
> — **Freeman & Freeman, Head First Design Patterns**

> "Strategy is one of the cornerstones of object-oriented design. It allows you to define a family of algorithms, encapsulate each one, and make them interchangeable."
> — **Gang of Four, Design Patterns**

> "Program to an interface, not an implementation."
> — **Gang of Four Design Principle** (fundamental to Strategy)

> "Strategy and Command look similar because both can be used to parameterize an object with an action. However, they have different intents... Command's focus is on decoupling the sender and receiver. Strategy's focus is on providing a family of interchangeable algorithms."
> — **Gang of Four, Design Patterns**

---

## Further Reading

### Free Resources

- [Wikipedia: Strategy Pattern](https://en.wikipedia.org/wiki/Strategy_pattern) - Overview and examples
- [Refactoring Guru: Strategy](https://refactoring.guru/design-patterns/strategy) - Visual diagrams and multi-language examples
- [Source Making: Strategy](https://sourcemaking.com/design_patterns/strategy) - Detailed explanation with UML
- [DoFactory: Strategy Pattern](https://www.dofactory.com/net/strategy-design-pattern) - .NET-focused but concepts apply universally
- [PMI: Strategy Pattern](https://www.pmi.org/disciplined-agile/the-design-patterns-repository/the-strategy-pattern) - Project management perspective

### Books and Courses (Paid/Subscription)

- **Design Patterns: Elements of Reusable Object-Oriented Software** (Gang of Four)
  - [O'Reilly](https://learning.oreilly.com/library/view/design-patterns-elements/0201633612/ch05.html#page_315)
  - The original and definitive reference, Chapter 5

- **Head First Design Patterns** by Freeman & Freeman
  - [O'Reilly](https://learning.oreilly.com/library/view/head-first-design/9781492077992/ch01.html)
  - [Amazon](https://www.amazon.com/Head-First-Design-Patterns-Object-Oriented-ebook/dp/B08P3X99QP)
  - Excellent visual introduction, Chapter 1

- **Agile Principles, Patterns, and Practices in C#** by Robert C. Martin
  - [O'Reilly](https://learning.oreilly.com/library/view/agile-principles-patterns/0131857258/)
  - [Amazon](https://www.amazon.com/Agile-Principles-Patterns-Practices-C/dp/0131857258)
  - Chapter 22 covers Strategy

- **Clean Code: Design Patterns, Episode 27** by Robert C. Martin
  - [Clean Coders](https://cleancoders.com/episode/clean-code-episode-27)
  - [O'Reilly](https://learning.oreilly.com/videos/clean-code-fundamentals/9780134661742/9780134661742-code_03_27_00/)
  - Video format

### Related Patterns to Study

- **Command Pattern** - Similar structure, different intent
- **State Pattern** - Similar technique, different purpose
- **Template Method** - Alternative approach to varying algorithms
- **Factory Method** - Helps resolve Strategy references
- **Dependency Injection** - Provides Strategy dependencies

### Search

- Google: [Strategy Design Pattern](https://www.google.com/search?q=strategy+design+pattern)
- Google: [Strategy vs Command Pattern](https://www.google.com/search?q=strategy+vs+command+pattern)

---

## Summary

The Strategy pattern encapsulates interchangeable algorithms behind a common interface, enabling:

- **Flexibility** - Select or change algorithm at runtime
- **Modularity** - Separate client concerns from implementation details
- **Testability** - Test client and strategies independently
- **Extensibility** - Add new strategies without modifying existing code

**Key Principles:**
1. Define interface from client's perspective (what they need)
2. Program to interface, not implementation
3. Compose rather than inherit
4. Each strategy has single responsibility
5. Open for extension, closed for modification

**Remember:** Strategy separates **what** the client wants to accomplish from **how** it will be accomplished.

---

*This reference is based on the Strategy Design Pattern blog post by James Humelsine and supplemented with comprehensive implementation examples, common pitfalls, and practical guidance.*
