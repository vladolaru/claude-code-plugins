# Proxy Design Pattern

> Place administrative wrapper objects around objects, often to help manage their complexity or resources.

## Intent

Provide a surrogate or placeholder for another object to control access to it. The Proxy pattern adds an administrative wrapper layer around basic functionality so that client code won't have to deal with administrative concerns directly.

Think of it like a diplomatic ambassador who acts as the proxy for the government they represent - they handle administrative protocols while representing the actual authority.

## Problem

Objects sometimes need additional administrative care beyond their core functionality:

- **Resource-intensive objects** that shouldn't be created until needed (lazy initialization)
- **Objects requiring access control** (permissions, authentication)
- **Remote objects** needing network communication abstraction
- **Objects requiring logging/caching** before/after operations
- **Memory management** for complex object lifecycles

The uncaring designer shifts this burden onto client developers, possibly clearing their conscience by describing what needs to be done in documentation.

### Problems with Documentation-Only Approach

| Problem | Impact |
|---------|--------|
| Client developer may not read documentation | Administrative needs ignored |
| Client may read but not understand/implement correctly | Inconsistent or buggy implementations |
| Infrastructure details obfuscate client code's true intent | Reduced code clarity |
| Administrative needs change in future releases | Who updates all existing client code? |

**If you know enough to describe administrative care in documentation, you know enough to provide an implementation solution.**

## Solution

The Proxy Design Pattern wraps the complex object with an administrative layer:

- Client code delegates to an interface (as always)
- Proxy implements that interface and adds administrative logic
- Proxy delegates to the real object for core functionality
- Client code is unaware of the proxy's existence

**Key insight:** Administrative concerns are encapsulated once in the Proxy, applied consistently, and can evolve independently.

## Structure

### Basic Structure (Improved over GoF)

```
┌─────────────┐
│   Client    │
└──────┬──────┘
       │ uses
       ▼
┌─────────────┐
│  <<Feature>>│  ← Interface
└─────────────┘
       △
       │ implements
       ├──────────────┬────────────────┐
       │              │                │
┌──────┴──────┐  ┌───┴────┐  ┌────────┴────────┐
│ConcreteFeature│  │ Proxy  │  │ OtherImplementation│
└─────────────┘  └───┬────┘  └─────────────────┘
                     │ delegates
                     │ (to Feature, not concrete!)
                     └────────────────────┐
                                          │
                                    ┌─────▼──────┐
                                    │  Feature   │
                                    │ instance   │
                                    └────────────┘
```

**Improvements over Gang of Four diagram:**

1. **Proxy delegates to interface (Feature), not concrete class** - enables composition
2. **No diamond connector** - single reference, not aggregation
3. **Configurer shown** - demonstrates object construction
4. **Programming to interface, not implementation** - follows GoF's own First Principle

### Key Elements

| Element | Role | Responsibility |
|---------|------|----------------|
| `Feature` | Interface | Declares common interface for ConcreteFeature and Proxy |
| `ConcreteFeature` | Real object | Implements core functionality (complex/resource-intensive) |
| `Proxy` | Administrative wrapper | Implements Feature, delegates to ConcreteFeature, adds administration |
| `Client` | Consumer | Only knows about Feature interface, unaware of proxy |
| `Configurer` | Constructor | Assembles proxy with concrete feature |

## Types of Proxies

### 1. Virtual Proxy (Lazy Initialization)

**Problem:** Object requires significant resources when instantiated, but may not be used immediately.

**Solution:** Defer object creation until first method call.

```php
<?php

interface Image {
    public function display(): void;
    public function getMetadata(): array;
}

class HighResolutionImage implements Image {
    private string $filename;
    private $imageData; // Heavy resource

    public function __construct(string $filename) {
        $this->filename = $filename;
        // Expensive operation - load entire image into memory
        $this->imageData = $this->loadImageFromDisk($filename);
        error_log("HighResolutionImage loaded: {$filename} (EXPENSIVE!)");
    }

    public function display(): void {
        // Render $this->imageData
        echo "Displaying high-res image: {$this->filename}\n";
    }

    public function getMetadata(): array {
        return [
            'filename' => $this->filename,
            'size' => strlen($this->imageData),
            'type' => 'high-res'
        ];
    }

    private function loadImageFromDisk(string $filename) {
        // Simulate expensive disk I/O and memory allocation
        return str_repeat('x', 1024 * 1024); // 1MB of data
    }
}

class ImageProxy implements Image {
    private string $filename;
    private ?Image $realImage = null; // Lazy-loaded

    public function __construct(string $filename) {
        $this->filename = $filename;
        error_log("ImageProxy created: {$filename} (lightweight)");
    }

    public function display(): void {
        $this->ensureImageLoaded();
        $this->realImage->display();
    }

    public function getMetadata(): array {
        $this->ensureImageLoaded();
        return $this->realImage->getMetadata();
    }

    private function ensureImageLoaded(): void {
        if ($this->realImage === null) {
            error_log("Lazy loading image now: {$this->filename}");
            $this->realImage = new HighResolutionImage($this->filename);
        }
    }
}

// Client code
function displayGallery(array $images): void {
    echo "Gallery loaded with " . count($images) . " images\n";

    // Only display first 3
    for ($i = 0; $i < min(3, count($images)); $i++) {
        $images[$i]->display();
    }
}

// Without proxy - ALL images loaded immediately
$withoutProxy = [
    new HighResolutionImage('photo1.jpg'),
    new HighResolutionImage('photo2.jpg'),
    new HighResolutionImage('photo3.jpg'),
    new HighResolutionImage('photo4.jpg'), // Loaded but never used!
    new HighResolutionImage('photo5.jpg'), // Loaded but never used!
];
displayGallery($withoutProxy);

// With proxy - only displayed images loaded
$withProxy = [
    new ImageProxy('photo1.jpg'),
    new ImageProxy('photo2.jpg'),
    new ImageProxy('photo3.jpg'),
    new ImageProxy('photo4.jpg'), // Created but never loaded
    new ImageProxy('photo5.jpg'), // Created but never loaded
];
displayGallery($withProxy);
```

**Output demonstrates lazy loading:**
```
HighResolutionImage loaded: photo1.jpg (EXPENSIVE!)
HighResolutionImage loaded: photo2.jpg (EXPENSIVE!)
HighResolutionImage loaded: photo3.jpg (EXPENSIVE!)
HighResolutionImage loaded: photo4.jpg (EXPENSIVE!)  ← Wasted!
HighResolutionImage loaded: photo5.jpg (EXPENSIVE!)  ← Wasted!
Gallery loaded with 5 images
Displaying high-res image: photo1.jpg
Displaying high-res image: photo2.jpg
Displaying high-res image: photo3.jpg

ImageProxy created: photo1.jpg (lightweight)
ImageProxy created: photo2.jpg (lightweight)
ImageProxy created: photo3.jpg (lightweight)
ImageProxy created: photo4.jpg (lightweight)  ← Lightweight!
ImageProxy created: photo5.jpg (lightweight)  ← Lightweight!
Gallery loaded with 5 images
Lazy loading image now: photo1.jpg
HighResolutionImage loaded: photo1.jpg (EXPENSIVE!)
Displaying high-res image: photo1.jpg
Lazy loading image now: photo2.jpg
HighResolutionImage loaded: photo2.jpg (EXPENSIVE!)
Displaying high-res image: photo2.jpg
Lazy loading image now: photo3.jpg
HighResolutionImage loaded: photo3.jpg (EXPENSIVE!)
Displaying high-res image: photo3.jpg
```

**Key benefits:**
- Proxy is lightweight until first use
- Resources allocated only when needed
- Client code unchanged
- If client flow never executes certain code paths, those objects never instantiated

### 2. Protection Proxy (Access Control)

**Problem:** Need to control access to object based on permissions, authentication, or business rules.

**Solution:** Proxy checks permissions before delegating to real object.

```php
<?php

interface BankAccount {
    public function getBalance(): float;
    public function withdraw(float $amount): bool;
    public function deposit(float $amount): void;
    public function getTransactionHistory(): array;
}

class RealBankAccount implements BankAccount {
    private float $balance;
    private array $transactions = [];
    private string $accountNumber;

    public function __construct(string $accountNumber, float $initialBalance) {
        $this->accountNumber = $accountNumber;
        $this->balance = $initialBalance;
    }

    public function getBalance(): float {
        return $this->balance;
    }

    public function withdraw(float $amount): bool {
        if ($amount > $this->balance) {
            return false;
        }
        $this->balance -= $amount;
        $this->transactions[] = ['type' => 'withdrawal', 'amount' => $amount, 'time' => time()];
        return true;
    }

    public function deposit(float $amount): void {
        $this->balance += $amount;
        $this->transactions[] = ['type' => 'deposit', 'amount' => $amount, 'time' => time()];
    }

    public function getTransactionHistory(): array {
        return $this->transactions;
    }
}

class ProtectedBankAccountProxy implements BankAccount {
    private BankAccount $realAccount;
    private string $currentUserRole;
    private bool $isAuthenticated;

    public function __construct(BankAccount $realAccount, string $userRole, bool $authenticated) {
        $this->realAccount = $realAccount;
        $this->currentUserRole = $userRole;
        $this->isAuthenticated = $authenticated;
    }

    public function getBalance(): float {
        if (!$this->isAuthenticated) {
            throw new \Exception("Access denied: Not authenticated");
        }

        // Any authenticated user can check balance
        return $this->realAccount->getBalance();
    }

    public function withdraw(float $amount): bool {
        if (!$this->isAuthenticated) {
            throw new \Exception("Access denied: Not authenticated");
        }

        if ($this->currentUserRole !== 'owner' && $this->currentUserRole !== 'authorized_user') {
            throw new \Exception("Access denied: Insufficient permissions for withdrawal");
        }

        // Additional business rule: authorized_user has withdrawal limit
        if ($this->currentUserRole === 'authorized_user' && $amount > 500) {
            throw new \Exception("Access denied: Withdrawal limit exceeded for authorized users");
        }

        error_log("Withdrawal authorized for {$this->currentUserRole}: \${$amount}");
        return $this->realAccount->withdraw($amount);
    }

    public function deposit(float $amount): void {
        if (!$this->isAuthenticated) {
            throw new \Exception("Access denied: Not authenticated");
        }

        // Any authenticated user can deposit
        error_log("Deposit authorized for {$this->currentUserRole}: \${$amount}");
        $this->realAccount->deposit($amount);
    }

    public function getTransactionHistory(): array {
        if (!$this->isAuthenticated) {
            throw new \Exception("Access denied: Not authenticated");
        }

        if ($this->currentUserRole !== 'owner') {
            throw new \Exception("Access denied: Only account owner can view transaction history");
        }

        return $this->realAccount->getTransactionHistory();
    }
}

// Usage
$realAccount = new RealBankAccount('12345', 1000.0);

// Scenario 1: Owner access
$ownerAccount = new ProtectedBankAccountProxy($realAccount, 'owner', true);
echo "Balance: $" . $ownerAccount->getBalance() . "\n";
$ownerAccount->withdraw(200);
$ownerAccount->deposit(100);
print_r($ownerAccount->getTransactionHistory()); // Allowed

// Scenario 2: Authorized user (limited access)
$authorizedUserAccount = new ProtectedBankAccountProxy($realAccount, 'authorized_user', true);
$authorizedUserAccount->withdraw(300); // Allowed (under limit)
try {
    $authorizedUserAccount->withdraw(600); // Denied (over limit)
} catch (\Exception $e) {
    echo "Error: {$e->getMessage()}\n";
}
try {
    $authorizedUserAccount->getTransactionHistory(); // Denied (not owner)
} catch (\Exception $e) {
    echo "Error: {$e->getMessage()}\n";
}

// Scenario 3: Viewer (read-only)
$viewerAccount = new ProtectedBankAccountProxy($realAccount, 'viewer', true);
echo "Balance: $" . $viewerAccount->getBalance() . "\n"; // Allowed
try {
    $viewerAccount->withdraw(50); // Denied
} catch (\Exception $e) {
    echo "Error: {$e->getMessage()}\n";
}

// Scenario 4: Unauthenticated
$unauthenticatedAccount = new ProtectedBankAccountProxy($realAccount, 'guest', false);
try {
    $unauthenticatedAccount->getBalance(); // Denied
} catch (\Exception $e) {
    echo "Error: {$e->getMessage()}\n";
}
```

**Key benefits:**
- Access control logic centralized in proxy
- Real object remains focused on core functionality
- Security rules can evolve without touching core logic
- Easy to add logging, audit trails
- Client code doesn't need to know about security

### 3. Remote Proxy (Network Communication)

**Problem:** Object exists in different address space (remote server, microservice, external API) - need to abstract network communication.

**Solution:** Proxy handles network calls, serialization, error handling, making remote object appear local.

```php
<?php

interface WeatherService {
    public function getCurrentTemperature(string $city): float;
    public function getForecast(string $city, int $days): array;
}

class RealWeatherService implements WeatherService {
    private string $apiEndpoint;

    public function __construct(string $apiEndpoint) {
        $this->apiEndpoint = $apiEndpoint;
    }

    public function getCurrentTemperature(string $city): float {
        // Direct API implementation (if this were a local service)
        // In reality, this would live on a remote server
        error_log("RealWeatherService: Fetching temperature for {$city}");
        return 72.5;
    }

    public function getForecast(string $city, int $days): array {
        error_log("RealWeatherService: Fetching {$days}-day forecast for {$city}");
        return array_fill(0, $days, ['high' => 75, 'low' => 60, 'conditions' => 'sunny']);
    }
}

class RemoteWeatherServiceProxy implements WeatherService {
    private string $remoteHost;
    private int $remotePort;
    private int $timeout;

    public function __construct(string $host, int $port, int $timeout = 5) {
        $this->remoteHost = $host;
        $this->remotePort = $port;
        $this->timeout = $timeout;
    }

    public function getCurrentTemperature(string $city): float {
        $request = [
            'method' => 'getCurrentTemperature',
            'params' => ['city' => $city]
        ];

        $response = $this->makeRemoteCall($request);
        return (float) $response['result'];
    }

    public function getForecast(string $city, int $days): array {
        $request = [
            'method' => 'getForecast',
            'params' => ['city' => $city, 'days' => $days]
        ];

        $response = $this->makeRemoteCall($request);
        return $response['result'];
    }

    private function makeRemoteCall(array $request): array {
        error_log("RemoteProxy: Connecting to {$this->remoteHost}:{$this->remotePort}");

        try {
            // Serialize request
            $serializedRequest = json_encode($request);
            error_log("RemoteProxy: Sending request - {$serializedRequest}");

            // Simulate network call
            $response = $this->sendOverNetwork($serializedRequest);

            // Deserialize response
            $deserializedResponse = json_decode($response, true);
            error_log("RemoteProxy: Received response");

            if (isset($deserializedResponse['error'])) {
                throw new \Exception("Remote error: {$deserializedResponse['error']}");
            }

            return $deserializedResponse;

        } catch (\Exception $e) {
            error_log("RemoteProxy: Network error - {$e->getMessage()}");
            throw new \Exception("Failed to communicate with remote service: {$e->getMessage()}");
        }
    }

    private function sendOverNetwork(string $data): string {
        // Simulate HTTP request, socket communication, gRPC call, etc.
        // In reality, this would use curl, Guzzle, or socket functions

        // Simulate network latency
        usleep(50000); // 50ms

        // Simulate response
        $requestData = json_decode($data, true);
        $method = $requestData['method'];
        $params = $requestData['params'];

        // Simulate service response
        if ($method === 'getCurrentTemperature') {
            return json_encode(['result' => 72.5]);
        } elseif ($method === 'getForecast') {
            $forecast = array_fill(0, $params['days'], [
                'high' => 75,
                'low' => 60,
                'conditions' => 'sunny'
            ]);
            return json_encode(['result' => $forecast]);
        }

        return json_encode(['error' => 'Unknown method']);
    }
}

// Client code - same interface, different implementation contexts
function displayWeather(WeatherService $service, string $city): void {
    // Client doesn't know or care if service is local or remote
    echo "Current temperature in {$city}: " . $service->getCurrentTemperature($city) . "°F\n";
    $forecast = $service->getForecast($city, 3);
    echo "3-day forecast: " . count($forecast) . " days\n";
}

// Local service (direct)
$localService = new RealWeatherService('http://localhost/api');
displayWeather($localService, 'San Francisco');

echo "\n--- Using Remote Proxy ---\n\n";

// Remote service (via proxy - abstracts network complexity)
$remoteService = new RemoteWeatherServiceProxy('api.weather.com', 443, 5);
displayWeather($remoteService, 'San Francisco');
```

**Key benefits:**
- Network complexity hidden from client
- Serialization/deserialization encapsulated
- Error handling centralized
- Can add retry logic, caching, connection pooling
- Easy to switch between local/remote implementations
- Can add offline mode, fallback servers

### 4. Caching Proxy

**Problem:** Expensive operations (DB queries, API calls) called repeatedly with same parameters.

**Solution:** Proxy caches results and returns cached data when appropriate.

```php
<?php

interface ProductRepository {
    public function findById(int $id): ?array;
    public function findByCategory(string $category): array;
}

class DatabaseProductRepository implements ProductRepository {
    private \PDO $db;

    public function __construct(\PDO $db) {
        $this->db = $db;
    }

    public function findById(int $id): ?array {
        error_log("DB QUERY: SELECT * FROM products WHERE id = {$id}");
        // Simulate expensive database query
        usleep(100000); // 100ms query time

        // Simulate result
        return [
            'id' => $id,
            'name' => "Product {$id}",
            'price' => 99.99,
            'category' => 'electronics'
        ];
    }

    public function findByCategory(string $category): array {
        error_log("DB QUERY: SELECT * FROM products WHERE category = '{$category}'");
        usleep(200000); // 200ms query time

        return [
            ['id' => 1, 'name' => 'Product 1', 'category' => $category],
            ['id' => 2, 'name' => 'Product 2', 'category' => $category],
        ];
    }
}

class CachingProductRepositoryProxy implements ProductRepository {
    private ProductRepository $realRepository;
    private array $cache = [];
    private int $ttl; // Time to live in seconds

    public function __construct(ProductRepository $realRepository, int $ttl = 60) {
        $this->realRepository = $realRepository;
        $this->ttl = $ttl;
    }

    public function findById(int $id): ?array {
        $cacheKey = "product_id_{$id}";

        // Check cache
        if ($this->isCached($cacheKey)) {
            error_log("CACHE HIT: {$cacheKey}");
            return $this->cache[$cacheKey]['data'];
        }

        // Cache miss - fetch from real repository
        error_log("CACHE MISS: {$cacheKey}");
        $product = $this->realRepository->findById($id);

        // Store in cache
        $this->cache[$cacheKey] = [
            'data' => $product,
            'timestamp' => time()
        ];

        return $product;
    }

    public function findByCategory(string $category): array {
        $cacheKey = "products_category_{$category}";

        if ($this->isCached($cacheKey)) {
            error_log("CACHE HIT: {$cacheKey}");
            return $this->cache[$cacheKey]['data'];
        }

        error_log("CACHE MISS: {$cacheKey}");
        $products = $this->realRepository->findByCategory($category);

        $this->cache[$cacheKey] = [
            'data' => $products,
            'timestamp' => time()
        ];

        return $products;
    }

    private function isCached(string $key): bool {
        if (!isset($this->cache[$key])) {
            return false;
        }

        // Check if cache entry expired
        $age = time() - $this->cache[$key]['timestamp'];
        if ($age > $this->ttl) {
            error_log("CACHE EXPIRED: {$key} (age: {$age}s, ttl: {$this->ttl}s)");
            unset($this->cache[$key]);
            return false;
        }

        return true;
    }

    public function clearCache(): void {
        error_log("CACHE CLEARED");
        $this->cache = [];
    }
}

// Usage
$db = new \PDO('sqlite::memory:'); // Placeholder
$realRepo = new DatabaseProductRepository($db);
$cachedRepo = new CachingProductRepositoryProxy($realRepo, 60);

// First call - cache miss
$product = $cachedRepo->findById(42);
echo "Product: {$product['name']}\n";

// Second call - cache hit (no DB query)
$product = $cachedRepo->findById(42);
echo "Product: {$product['name']}\n";

// Third call - cache hit
$product = $cachedRepo->findById(42);
echo "Product: {$product['name']}\n";

// Different product - cache miss
$product = $cachedRepo->findById(99);
echo "Product: {$product['name']}\n";
```

**Output:**
```
DB QUERY: SELECT * FROM products WHERE id = 42
CACHE MISS: product_id_42
Product: Product 42
CACHE HIT: product_id_42
Product: Product 42
CACHE HIT: product_id_42
Product: Product 42
DB QUERY: SELECT * FROM products WHERE id = 99
CACHE MISS: product_id_99
Product: Product 99
```

**Key benefits:**
- Transparent caching - client unaware
- Performance improvement without changing client code
- Cache strategy encapsulated (TTL, eviction, invalidation)
- Easy to disable caching (just use real repository)
- Can add cache warming, statistics, invalidation rules

## Memory Management Use Case: acquire() and release()

### The Problem: Who Manages Resources?

In languages requiring manual memory management (C++, older PHP, etc.), or when dealing with resources requiring cleanup (database connections, file handles), who is responsible for cleanup?

**Traditional approach problems:**

| Problem | Impact |
|---------|--------|
| Client must remember to release | Relies on developer discipline |
| Premature returns skip release | Resource leaks |
| Exceptions skip release | Resource leaks |
| Documentation-only | Inconsistent implementation |
| Changing creation mechanism | Who updates all client code? |

### Solution: Acquire/Release Pattern with Proxy

**Principle:** Client code knows when it's done with an object, even if it doesn't know how to manage resources.

```php
<?php

interface DatabaseConnection {
    public function query(string $sql): array;
    public function execute(string $sql): bool;
}

class RealDatabaseConnection implements DatabaseConnection {
    private $handle;
    private string $connectionString;

    public function __construct(string $connectionString) {
        $this->connectionString = $connectionString;
        $this->handle = $this->connect();
        error_log("Database connection opened: {$connectionString}");
    }

    public function query(string $sql): array {
        // Execute query, return results
        return [['id' => 1, 'name' => 'Example']];
    }

    public function execute(string $sql): bool {
        // Execute statement
        return true;
    }

    public function close(): void {
        if ($this->handle) {
            // Close database connection
            error_log("Database connection closed: {$this->connectionString}");
            $this->handle = null;
        }
    }

    private function connect() {
        // Simulate connection
        return new \stdClass();
    }
}

// BAD: Client must remember to release
class DatabaseFactory {
    public static function acquire(): DatabaseConnection {
        return new RealDatabaseConnection('mysql://localhost/mydb');
    }

    public static function release(DatabaseConnection $conn): void {
        if ($conn instanceof RealDatabaseConnection) {
            $conn->close();
        }
    }
}

// Problem: Developer forgets to release
function badClientCode(): void {
    $db = DatabaseFactory::acquire();
    $results = $db->query('SELECT * FROM users');

    if (empty($results)) {
        return; // LEAK! Forgot to release before return
    }

    // Process results...

    DatabaseFactory::release($db); // Only called if results not empty
}

// GOOD: Proxy with automatic cleanup via try-finally or destructor
class DatabaseConnectionProxy implements DatabaseConnection {
    private ?RealDatabaseConnection $realConnection;

    public function __construct(string $connectionString) {
        $this->realConnection = new RealDatabaseConnection($connectionString);
    }

    public function query(string $sql): array {
        return $this->realConnection->query($sql);
    }

    public function execute(string $sql): bool {
        return $this->realConnection->execute($sql);
    }

    // Destructor automatically called when object destroyed
    public function __destruct() {
        if ($this->realConnection) {
            error_log("Proxy destructor: cleaning up connection");
            $this->realConnection->close();
        }
    }
}

// Better: Client code with automatic cleanup
function goodClientCode(): void {
    $db = new DatabaseConnectionProxy('mysql://localhost/mydb');
    $results = $db->query('SELECT * FROM users');

    if (empty($results)) {
        return; // No leak! Destructor called automatically
    }

    // Process results...

    // No manual cleanup needed - destructor handles it
}

echo "=== Bad Client Code ===\n";
badClientCode();

echo "\n=== Good Client Code ===\n";
goodClientCode();

echo "\n=== Proof: Multiple early returns ===\n";
function multipleReturns(): void {
    $db = new DatabaseConnectionProxy('mysql://localhost/mydb');

    $results = $db->query('SELECT * FROM users WHERE active = 1');
    if (count($results) > 100) {
        return; // Cleanup happens automatically
    }

    $results = $db->query('SELECT * FROM orders');
    if (count($results) === 0) {
        return; // Cleanup happens automatically
    }

    // More logic...

    // Cleanup happens automatically at end too
}
multipleReturns();
```

**Output:**
```
=== Bad Client Code ===
Database connection opened: mysql://localhost/mydb
(No close message - resource leaked!)

=== Good Client Code ===
Database connection opened: mysql://localhost/mydb
Proxy destructor: cleaning up connection
Database connection closed: mysql://localhost/mydb

=== Proof: Multiple early returns ===
Database connection opened: mysql://localhost/mydb
Proxy destructor: cleaning up connection
Database connection closed: mysql://localhost/mydb
```

### PHP: Leveraging try-with-resources Pattern

PHP doesn't have Java's try-with-resources or C++'s RAII stack allocation, but we can simulate it:

```php
<?php

interface AutoCloseable {
    public function close(): void;
}

// Proxy makes non-AutoCloseable resource AutoCloseable
class ExternalResourceProxy implements AutoCloseable {
    private $externalResource;

    public function __construct($externalResource) {
        $this->externalResource = $externalResource;
    }

    public function doWork(): void {
        // Delegate to external resource
        $this->externalResource->performAction();
    }

    public function close(): void {
        // Call external resource's non-standard cleanup
        if (method_exists($this->externalResource, 'cleanup')) {
            $this->externalResource->cleanup();
        } elseif (method_exists($this->externalResource, 'releaseResources')) {
            $this->externalResource->releaseResources();
        } elseif (method_exists($this->externalResource, 'destroy')) {
            $this->externalResource->destroy();
        }

        error_log("ExternalResourceProxy: Resource released");
    }
}

// Helper for try-with-resources pattern
function using(AutoCloseable $resource, callable $block): void {
    try {
        $block($resource);
    } finally {
        $resource->close();
    }
}

// Usage
class ExternalLibraryResource {
    public function performAction(): void {
        echo "Performing action\n";
    }

    public function releaseResources(): void {
        echo "External resource released\n";
    }
}

// Client code with guaranteed cleanup
using(new ExternalResourceProxy(new ExternalLibraryResource()), function($proxy) {
    $proxy->doWork();

    if (rand(0, 1)) {
        return; // Early return - cleanup still happens
    }

    $proxy->doWork();
    // Cleanup happens even if exception thrown
});
```

**Key insight:** Proxy converts resource management from documentation requirement to automatic enforcement.

## When to Use Proxy

| Scenario | Proxy Type | Reason |
|----------|------------|--------|
| Object expensive to create | Virtual Proxy | Defer creation until needed |
| Need access control | Protection Proxy | Centralize security logic |
| Object in different address space | Remote Proxy | Abstract network complexity |
| Repeated expensive operations | Caching Proxy | Store and reuse results |
| Resource cleanup required | Resource Proxy | Automatic cleanup on destroy |
| Need logging/auditing | Logging Proxy | Transparent logging layer |
| Need to count references | Smart Reference | Track usage, cleanup when unused |

## When NOT to Use Proxy

| Scenario | Why Not | Alternative |
|----------|---------|-------------|
| Object is simple/lightweight | Unnecessary overhead | Use object directly |
| No administrative needs | Adds complexity for no benefit | Direct access |
| Need to add varying responsibilities | Proxy typically has one concern | Use Decorator pattern |
| Client must be aware of proxy behavior | Violates transparency principle | Explicit wrapper/facade |

## Relationship to Other Patterns

| Pattern | Relationship | Key Difference |
|---------|--------------|----------------|
| **Decorator** | Both wrap objects with interface | Decorator adds responsibilities, Proxy controls access |
| **Adapter** | Both wrap objects | Adapter changes interface, Proxy keeps same interface |
| **Facade** | Both simplify access | Facade simplifies subsystem, Proxy controls single object |
| **Strategy** | Similar structure | Strategy changes algorithm, Proxy adds administration |

**Key insight:** If you need multiple administrative concerns, consider Decorator (next pattern).

## Implementation Considerations

### 1. When to Create Real Object

```php
// Eager initialization (traditional proxy)
class EagerProxy implements Service {
    private Service $realService;

    public function __construct(Service $realService) {
        $this->realService = $realService; // Already created
    }

    public function execute(): void {
        $this->logAccess();
        $this->realService->execute();
    }
}

// Lazy initialization (virtual proxy)
class LazyProxy implements Service {
    private ?Service $realService = null;

    public function execute(): void {
        if ($this->realService === null) {
            $this->realService = new RealService(); // Created on first use
        }

        $this->logAccess();
        $this->realService->execute();
    }
}
```

### 2. Delegate to Interface vs Concrete Class

```php
// GOOD: Delegate to interface (composable)
class GoodProxy implements Feature {
    private Feature $delegate; // Interface!

    public function __construct(Feature $delegate) {
        $this->delegate = $delegate;
    }
}

// BAD: Delegate to concrete class (rigid)
class BadProxy implements Feature {
    private ConcreteFeature $delegate; // Concrete!

    public function __construct(ConcreteFeature $delegate) {
        $this->delegate = $delegate;
    }
}
```

**Why interface matters:** Allows proxy-to-proxy chaining (logging proxy → caching proxy → remote proxy → real object).

### 3. Transparent vs Aware Proxy

```php
// Transparent - client unaware
function transparentClient(Service $service): void {
    $service->execute(); // Could be proxy or real object
}

// Aware - client knows about proxy (usually bad)
function awareClient(ServiceProxy $proxy): void {
    $proxy->clearCache(); // Proxy-specific method
    $proxy->execute();
}
```

**Prefer transparent proxies** - client shouldn't know or care.

## Testing Proxy

```php
<?php

use PHPUnit\Framework\TestCase;

class ProxyTest extends TestCase {
    public function testVirtualProxyDefersInstantiation(): void {
        // Arrange
        $spy = new InstantiationSpy();
        $proxy = new VirtualProxyWithSpy($spy);

        // Assert: Real object not created yet
        $this->assertFalse($spy->wasInstantiated());

        // Act: First method call
        $proxy->execute();

        // Assert: Now it's created
        $this->assertTrue($spy->wasInstantiated());
    }

    public function testProtectionProxyEnforcesAccess(): void {
        $realService = new RealService();
        $proxy = new ProtectionProxy($realService, authenticated: false);

        $this->expectException(\Exception::class);
        $this->expectExceptionMessage('Access denied');

        $proxy->execute();
    }

    public function testCachingProxyReturnsCachedResults(): void {
        $expensive = $this->createMock(ExpensiveService::class);
        $expensive->expects($this->once()) // Only called once
            ->method('compute')
            ->with(42)
            ->willReturn('result');

        $proxy = new CachingProxy($expensive);

        // First call - hits real service
        $result1 = $proxy->compute(42);

        // Second call - returns cached
        $result2 = $proxy->compute(42);

        $this->assertEquals('result', $result1);
        $this->assertEquals('result', $result2);
        // Mock expectation of once() proves cache worked
    }
}
```

## WordPress/WooCommerce Examples

### 1. WooCommerce Transient Caching Proxy

```php
<?php

interface ProductPriceCalculator {
    public function calculatePrice(int $productId, int $quantity): float;
}

class ComplexPriceCalculator implements ProductPriceCalculator {
    public function calculatePrice(int $productId, int $quantity): float {
        // Complex logic: base price, tier pricing, discounts, tax, etc.
        $product = wc_get_product($productId);
        // ... expensive calculations ...
        return 99.99;
    }
}

class CachedPriceCalculatorProxy implements ProductPriceCalculator {
    private ProductPriceCalculator $calculator;
    private int $ttl;

    public function __construct(ProductPriceCalculator $calculator, int $ttl = 3600) {
        $this->calculator = $calculator;
        $this->ttl = $ttl;
    }

    public function calculatePrice(int $productId, int $quantity): float {
        $cacheKey = "product_price_{$productId}_{$quantity}";

        $cached = get_transient($cacheKey);
        if ($cached !== false) {
            return (float) $cached;
        }

        $price = $this->calculator->calculatePrice($productId, $quantity);
        set_transient($cacheKey, $price, $this->ttl);

        return $price;
    }
}
```

### 2. WordPress Capability Protection Proxy

```php
<?php

interface PostEditor {
    public function updatePost(int $postId, array $data): bool;
    public function deletePost(int $postId): bool;
}

class CapabilityProtectedPostEditorProxy implements PostEditor {
    private PostEditor $editor;

    public function __construct(PostEditor $editor) {
        $this->editor = $editor;
    }

    public function updatePost(int $postId, array $data): bool {
        if (!current_user_can('edit_post', $postId)) {
            throw new \Exception('Insufficient permissions to edit post');
        }

        return $this->editor->updatePost($postId, $data);
    }

    public function deletePost(int $postId): bool {
        if (!current_user_can('delete_post', $postId)) {
            throw new \Exception('Insufficient permissions to delete post');
        }

        return $this->editor->deletePost($postId);
    }
}
```

## Summary

**Proxy provides a surrogate for another object to control access to it.**

| Aspect | Description |
|--------|-------------|
| **Core Principle** | Add administrative wrapper without burdening client |
| **Key Benefit** | Centralized, consistent administrative logic |
| **Transparency** | Client unaware proxy exists - uses same interface |
| **Composition** | Delegate to interface for flexibility |
| **Types** | Virtual, Protection, Remote, Caching, Smart Reference |

**Remember:**
- Proxy = same interface, added administration
- Decorator = same interface, added responsibilities (stackable)
- Adapter = different interface, translation
- If you need multiple concerns, use Decorator chains instead of multiple proxy types

**When in doubt:** Start without proxy. Add it when you feel the pain of scattered administrative code.

## References

### Free Resources
- [Wikipedia Proxy Design Pattern](https://en.wikipedia.org/wiki/Proxy_pattern)
- [Source Making Proxy Design Pattern](https://sourcemaking.com/design_patterns/proxy)
- [Refactoring Guru Proxy Design Pattern](https://refactoring.guru/design-patterns/proxy)
- [Project Management Institute Proxy Design Pattern](https://www.pmi.org/disciplined-agile/the-design-patterns-repository/the-proxy-pattern)
- [Learn CS Design Proxy Pattern](https://www.learncsdesign.com/learn-the-proxy-design-pattern/)
- [Baeldung Proxy Design Pattern](https://www.baeldung.com/java-proxy-pattern)
- [Dofactory C# Proxy Design Pattern](https://www.dofactory.com/net/proxy-design-pattern)
- [Patterns.dev Proxy Design Pattern](https://www.patterns.dev/vanilla/proxy-pattern/)
- [JavaScript Patterns Proxy Design Pattern](https://javascriptpatterns.vercel.app/patterns/design-patterns/proxy-pattern)
- Google: [Proxy Design Pattern](https://www.google.com/search?q=proxy+design+pattern)

### Books
- Gang of Four Proxy Design Pattern, page 207 ([O'Reilly](https://learning.oreilly.com/library/view/design-patterns-elements/0201633612/ch04.html#page_207) and [Amazon](https://www.amazon.com/Design-Patterns-Elements-Reusable-Object-Oriented/dp/0201633612))
- Agile Principles, Patterns, and Practices in C#, Chapter 34 ([O'Reilly](https://learning.oreilly.com/library/view/agile-principles-patterns/0131857258/ch34.xhtml) and [Amazon](https://www.amazon.com/Agile-Principles-Patterns-Practices-C/dp/0131857258))
- Head First Design Patterns, Chapter 11 ([O'Reilly](https://learning.oreilly.com/library/view/head-first-design/9781492077992/ch11.html) and [Amazon](https://www.amazon.com/Head-First-Design-Patterns-Object-Oriented-ebook/dp/B08P3X99QP))

### Videos
- Clean Code: Design Patterns, Episode 32 video ([Clean Coders](https://cleancoders.com/episode/clean-code-episode-32) and [O'Reilly](https://learning.oreilly.com/videos/clean-code-fundamentals/9780134661742/9780134661742-code_03_32_00/))

### Related Patterns in This Library
- See `patterns/structural/decorator.md` for adding stackable responsibilities
- See `patterns/structural/adapter.md` for interface translation
- See `patterns/structural/facade.md` for simplifying subsystems
- See `patterns/creational/factory-method.md` for creating proxy instances
- See `patterns/behavioral/strategy.md` for understanding base composition structure
