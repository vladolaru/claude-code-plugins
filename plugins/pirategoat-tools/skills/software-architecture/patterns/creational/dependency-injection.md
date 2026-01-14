# Dependency Injection Pattern - Deep Dive Reference

## Pattern Classification
- **Type**: Creational Design Pattern
- **Purpose**: Resolve object dependencies cleanly while maintaining flexibility and testability
- **Related Patterns**: Factory, Abstract Factory, Adapter, Façade
- **Principles**: Dependency Inversion Principle (DIP), Inversion of Control (IoC)

## The Core Problem

### Tight Coupling Through Object Resolution

Even when following good design principles like "program to an interface, not an implementation," code can still be tightly coupled through **object resolution**. Consider this scenario:

```php
// ClientApplication depends on MyInterface
class ClientApplication {
    private MyInterface $myInterface;

    public function __construct() {
        // Problem: Direct dependency on Factory
        $this->myInterface = MyInterfaceFactory::create();
    }

    public function doWork() {
        $this->myInterface->doThis();
    }
}
```

**The Hidden Dependency Chain:**
1. `ClientApplication` → `MyInterfaceFactory` (direct instantiation)
2. `MyInterfaceFactory` → `MyClass` (factory knows concrete implementation)
3. Result: `ClientApplication` still depends on `MyClass`, just indirectly

### Why This Matters for Testing

When you need to test edge cases (like exception handling), you face a painful workflow:
1. Create a test double class that throws the exception
2. **Modify the Factory** to return your test double
3. Run your test
4. **Remember to revert the Factory** back to original implementation

This is:
- **Brittle** - Easy to forget reverting changes
- **Slow** - Requires recompilation, redeployment
- **Intrusive** - Modifies production code for testing
- **Error-prone** - Changes affect all code using that factory

## The Dependency Inversion Principle

### What It Means

The **Dependency Inversion Principle** (DIP) states:
- High-level modules should not depend on low-level modules
- Both should depend on abstractions
- Abstractions should not depend on details
- Details should depend on abstractions

### Visual Understanding

**Traditional Tight Coupling** (dependencies flow downward):
```
Policy Layer → Mechanism Layer → Utility Layer
```
Each layer depends on the layer below it. Changes ripple upward.

**Inverted Dependencies** (dependencies flow toward abstractions):
```
Policy Layer → Interface
                    ↑
            Mechanism Layer → Interface
                                  ↑
                              Utility Layer
```

Each layer depends on interfaces, not concrete implementations. Implementations point **up** to their interfaces.

### The Missing Piece

Programming to interfaces inverts **execution dependencies**, but without Dependency Injection, **resolution dependencies** remain tightly coupled.

## Dependency Injection - The Solution

### Core Concept

> **Dependency Injection** is a technique where an object is passed into a class (injected) instead of having the class create and store the object itself.
>
> — Martin Fowler, 2004

### You Already Know This Pattern

You've been injecting data forever:

```php
class Person {
    private string $name;

    public function __construct(string $name) {
        $this->name = $name;  // Injecting data
    }
}

$person = new Person("Tommy");
```

**Dependency Injection is the same thing, but with objects instead of primitives:**

```php
class ClientApplication {
    private MyInterface $myInterface;

    public function __construct(MyInterface $myInterface) {
        $this->myInterface = $myInterface;  // Injecting dependency
    }

    public function doWork() {
        $this->myInterface->doThis();
    }
}

// Usage
$clientApplication = new ClientApplication(new MyClass());
```

### The Three Injection Methods

#### 1. Constructor Injection (Most Common)

```php
class UserService {
    private UserRepository $repository;
    private EmailService $emailService;

    public function __construct(
        UserRepository $repository,
        EmailService $emailService
    ) {
        $this->repository = $repository;
        $this->emailService = $emailService;
    }
}

// Usage
$service = new UserService(
    new MySQLUserRepository(),
    new SmtpEmailService()
);
```

**Advantages:**
- Dependencies are immutable after construction
- Required dependencies are explicit
- Object is always in valid state

#### 2. Setter Injection

```php
class ReportGenerator {
    private DataSource $dataSource;
    private Formatter $formatter;

    public function setDataSource(DataSource $dataSource): void {
        $this->dataSource = $dataSource;
    }

    public function setFormatter(Formatter $formatter): void {
        $this->formatter = $formatter;
    }
}

// Usage
$generator = new ReportGenerator();
$generator->setDataSource(new DatabaseDataSource());
$generator->setFormatter(new PDFFormatter());
```

**Advantages:**
- Optional dependencies
- Can change dependencies after construction

**Disadvantages:**
- Object can be in invalid state if dependencies not set
- Dependencies can be changed unexpectedly

#### 3. Interface Injection (Rarely Used)

```php
interface DataSourceInjector {
    public function injectDataSource(DataSource $dataSource): void;
}

class ReportGenerator implements DataSourceInjector {
    private DataSource $dataSource;

    public function injectDataSource(DataSource $dataSource): void {
        $this->dataSource = $dataSource;
    }
}
```

**Use Cases:**
- Framework-driven dependency injection
- When you need to enforce injection capability through type system

## The Configurer Concept

### What Is a Configurer?

The **Configurer** is the crucial missing concept from most DI explanations. It's the entity that:
- **Knows the entire architecture** and how pieces fit together
- **Creates and wires dependencies** without containing business logic
- **Sits at the lowest architectural layer** (often in `main()` or bootstrap)
- **Remains invisible** to the rest of the system

**Other Names:**
- **Assembler** (Martin Fowler's term)
- **Orchestrator** (implies coordination)
- **Dependency Injector** (describes the technique)
- **Composer** (implies assembly)
- **Wiring** (infrastructure term)

**Why "Configurer"?**
It conveys **design intent** - this code configures the system architecture.

### Configurer Characteristics

#### 1. Knowledge Boundaries

```php
// Configurer has knowledge of everything but restrains itself
class ProductionConfigurer {
    public function createClientApplication(): ClientApplication {
        // Knows concrete implementations
        $impl3 = new Implementation3();
        $impl2 = new Implementation2();
        $impl1 = new Implementation1($impl2, $impl3);

        // Assembles the graph
        return new ClientApplication($impl1);
    }
}
```

**Key Points:**
- Configurer knows about all concrete classes
- Configurer knows how dependencies connect
- Configurer contains **no business logic**
- Configurer is **invisible** to configured classes

#### 2. No Business Logic

```php
// ❌ WRONG - Business logic in Configurer
class BadConfigurer {
    public function createUserService(): UserService {
        $repository = new MySQLUserRepository();

        // Don't do this! This is business logic!
        if ($repository->getUserCount() > 1000) {
            $cache = new RedisCache();
        } else {
            $cache = new MemoryCache();
        }

        return new UserService($repository, $cache);
    }
}

// ✅ RIGHT - Pure assembly
class GoodConfigurer {
    public function createUserService(): UserService {
        // Just create and wire
        return new UserService(
            new MySQLUserRepository(),
            new RedisCache()
        );
    }
}
```

#### 3. Multiple Configurers

Different contexts need different configurations:

```php
// Production configuration
class ProductionConfigurer {
    public function createApp(): Application {
        return new Application(
            new MySQLDatabase(),
            new SmtpEmailService(),
            new S3FileStorage()
        );
    }
}

// Test configuration
class TestConfigurer {
    public function createApp(): Application {
        return new Application(
            new InMemoryDatabase(),
            new MockEmailService(),
            new InMemoryFileStorage()
        );
    }
}

// Development configuration
class DevelopmentConfigurer {
    public function createApp(): Application {
        return new Application(
            new SQLiteDatabase(),
            new LoggingEmailService(),  // Logs instead of sending
            new LocalFileStorage()
        );
    }
}
```

**Configurers are invisible to each other** - they don't know about each other's existence.

## How Dependency Injection Supports DIP

### Breaking the Dependency Chain

**Without DI:**
```
ClientApplication → Factory → MyClass
```
Dependency chain flows through object resolution.

**With DI:**
```
ClientApplication → MyInterface ← MyClass
                         ↑
                    Configurer knows both
```
No dependency chain - Configurer is external to the architecture.

### Architectural Boundaries

Using DI creates three distinct architectural zones:

#### Zone 1: Business Logic (Upper Right)
- Contains `ClientApplication`, domain objects, interfaces
- **Zero knowledge** of concrete dependencies
- **All arrows point inward** across the boundary
- Pure business logic, easily testable

#### Zone 2: Infrastructure (Lower Right)
- Contains concrete implementations (`MyClass`, `MyTestDouble`)
- **Knows only about interfaces** they implement
- Often small Adapter or Façade implementations
- **Can be swapped** without affecting business logic

#### Zone 3: Wiring/Bootstrap (Left)
- Contains Configurers
- **Knows everything** but restrains itself to assembly
- **No business logic**
- **Invisible** to Zones 1 and 2

### Visual Architecture

```
┌─────────────────────────────────────────────────────┐
│                                                     │
│  ┌────────────────────┐         ┌────────────────┐ │
│  │   Configurer       │─────────│  TestConfigurer│ │
│  │  (Production)      │         │                │ │
│  └────────────────────┘         └────────────────┘ │
│           │                              │         │
│           │ knows everything             │         │
│           │ but no business logic        │         │
└───────────┼──────────────────────────────┼─────────┘
            │                              │
   ┌────────┼──────────────────────────────┼────────┐
   │        ↓                              ↓        │
   │  ╔═══════════════════════════════════════╗    │
   │  ║  BUSINESS LOGIC (No dependencies)     ║    │
   │  ║  ┌──────────────┐  ┌──────────────┐  ║    │
   │  ║  │ClientApp     │→ │ MyInterface  │  ║    │
   │  ║  └──────────────┘  └──────────────┘  ║    │
   │  ╚═══════════════════════════════════════╝    │
   │                          ↑         ↑           │
   │  ╔═══════════════════════╪═════════╪═════╗    │
   │  ║  IMPLEMENTATIONS      │         │     ║    │
   │  ║  ┌──────────────┐     │  ┌──────────┐ ║    │
   │  ║  │   MyClass    │─────┘  │TestDouble│ ║    │
   │  ║  └──────────────┘        └──────────┘ ║    │
   │  ╚═══════════════════════════════════════╝    │
   └───────────────────────────────────────────────┘
```

## Production vs Test Configuration

### Production Configuration - Full Dependency Graph

Production requires resolving **all layers** of dependencies:

```php
class ProductionConfigurer {
    public function createClientApplication(): ClientApplication {
        // Layer 3 - Deepest dependencies
        $impl3 = new Implementation3();
        $impl2 = new Implementation2();

        // Layer 2 - Mid-level dependencies
        $impl1 = new Implementation1($impl2, $impl3);

        // Layer 1 - Top-level object
        return new ClientApplication($impl1);
    }
}
```

**Production graph:**
```
ClientApplication
    └─→ Implementation1
            ├─→ Implementation2
            └─→ Implementation3
```

The Configurer needs **architectural knowledge** but not **implementation details**.

### Test Configuration - Single Layer Only

Testing requires resolving **only the first layer**:

```php
class ClientApplicationTestConfigurer {
    public function createClientApplication(): ClientApplication {
        // Only need one layer of test doubles
        $testDouble1 = new TestDouble1();

        return new ClientApplication($testDouble1);
    }
}
```

**Test graph:**
```
ClientApplication
    └─→ TestDouble1 (stops here - no further dependencies)
```

#### Why Only One Layer?

The **interface is a contract**. It encapsulates all implementation details. Test doubles only need to:
- Implement the interface contract
- Provide the specific behavior needed for this test
- Nothing more

#### Testing Deeper Layers

Each layer can be tested independently:

```php
// Test ClientApplication
class ClientApplicationTest {
    public function testExceptionHandling() {
        $throwingDouble = new ThrowingTestDouble();
        $app = new ClientApplication($throwingDouble);

        // Test how app handles exceptions
        $result = $app->doWork();
        $this->assertTrue($result->hasError());
    }
}

// Test Implementation1 separately
class Implementation1Test {
    public function testCombinesResults() {
        $double2 = new TestDouble2();
        $double3 = new TestDouble3();
        $impl = new Implementation1($double2, $double3);

        // Test impl1's specific logic
        $result = $impl->process();
        $this->assertEquals('combined', $result);
    }
}
```

Each test configures **only what it needs**.

## Turtles All the Way Down - Where Does It End?

### The Mental Block

When first learning DI, two questions arise:

1. **Do I have to inject everything?** Even in tests?
2. **Where does it end?** Aren't we just kicking the can down the road?

### Answer: Yes and It Ends at Bootstrap

#### For Production: Yes, Inject Everything

Production configuration resolves all dependencies, but:
- It's **only creating instances and wiring them**
- No business logic involved
- Usually done by architect/team lead who knows the architecture
- Can be done once and reused

#### For Tests: Only Inject What You Test

Test configuration resolves **only the immediate dependencies**:
- Interfaces act as **firebreaks** stopping the dependency chain
- Test doubles provide minimal behavior
- Each layer can be tested independently

#### Where It Ends: Bootstrap

Configurers live at the **lowest architectural level**:
- Often in `main()` or application bootstrap
- At the boundary of your system
- Where it's **safe to use `new`**
- No more layers below to worry about

```php
// index.php or main.php - The bootstrap
require_once 'autoload.php';

$configurer = new ProductionConfigurer();
$app = $configurer->createApplication();
$app->run();
```

**Bob Martin's perspective** (Clean Architecture):
- Business logic is **cocooned** with no concrete dependencies
- Concrete implementations are **small and peripheral** (Adapters/Façades)
- Configurers sit at the **very bottom** of the architecture
- At this level, `new` is perfectly acceptable

## Dependency Injection with Factories

DI doesn't eliminate Factories - they work together beautifully.

### DI with Simple Factory

When you need runtime polymorphism:

```php
interface ShapeFactory {
    public function createShape(string $type): Shape;
}

class ConcreteShapeFactory implements ShapeFactory {
    public function createShape(string $type): Shape {
        return match($type) {
            'circle' => new Circle(),
            'square' => new Square(),
            'triangle' => new Triangle(),
            default => throw new InvalidArgumentException()
        };
    }
}

class DrawingApplication {
    private ShapeFactory $factory;

    public function __construct(ShapeFactory $factory) {
        $this->factory = $factory;  // Inject the factory
    }

    public function drawShape(string $type) {
        $shape = $this->factory->createShape($type);
        $shape->draw();
    }
}

// Configurer
class ProductionConfigurer {
    public function createDrawingApp(): DrawingApplication {
        return new DrawingApplication(
            new ConcreteShapeFactory()
        );
    }
}

// Test Configurer
class TestConfigurer {
    public function createDrawingApp(): DrawingApplication {
        return new DrawingApplication(
            new MockShapeFactory()  // Returns test doubles
        );
    }
}
```

**Key insight:** Inject the Factory interface, not concrete factory.

### DI with Abstract Factory

When you need families of related objects:

```php
interface UIFactory {
    public function createButton(): Button;
    public function createCheckbox(): Checkbox;
    public function createTextbox(): Textbox;
}

class WindowsUIFactory implements UIFactory {
    public function createButton(): Button {
        return new WindowsButton();
    }

    public function createCheckbox(): Checkbox {
        return new WindowsCheckbox();
    }

    public function createTextbox(): Textbox {
        return new WindowsTextbox();
    }
}

class MacUIFactory implements UIFactory {
    public function createButton(): Button {
        return new MacButton();
    }

    public function createCheckbox(): Checkbox {
        return new MacCheckbox();
    }

    public function createTextbox(): Textbox {
        return new MacTextbox();
    }
}

class Application {
    private UIFactory $uiFactory;

    public function __construct(UIFactory $uiFactory) {
        $this->uiFactory = $uiFactory;
    }

    public function createUI() {
        $button = $this->uiFactory->createButton();
        $checkbox = $this->uiFactory->createCheckbox();
        // Use consistent family of UI components
    }
}

// Configurer
class ProductionConfigurer {
    public function createApplication(string $os): Application {
        $factory = match($os) {
            'windows' => new WindowsUIFactory(),
            'mac' => new MacUIFactory(),
            default => throw new InvalidArgumentException()
        };

        return new Application($factory);
    }
}
```

**Key insight:** The Configurer decides which Abstract Factory to inject based on configuration.

### Why Combine DI and Factories?

- **Factory handles:** Runtime polymorphism, object creation logic
- **DI handles:** Compile-time wiring, architectural dependencies
- **Together:** Maximum flexibility and testability

## Dependency Injection Frameworks

### Manual DI (What We've Shown)

**Advantages:**
- Complete control
- Easy to understand and debug
- No framework dependency
- Explicit wiring

**Disadvantages:**
- Verbose for large applications
- Manual maintenance of Configurers

### Framework-Based DI (e.g., PHP-DI, Symfony DI)

#### PHP-DI Example

```php
use DI\ContainerBuilder;

// Define dependencies
$containerBuilder = new ContainerBuilder();
$containerBuilder->addDefinitions([
    UserRepository::class => DI\create(MySQLUserRepository::class),
    EmailService::class => DI\create(SmtpEmailService::class),
    UserService::class => DI\create()
        ->constructor(
            DI\get(UserRepository::class),
            DI\get(EmailService::class)
        ),
]);

$container = $containerBuilder->build();
$userService = $container->get(UserService::class);
```

#### Symfony DI with Autowiring

```php
// services.yaml
services:
    _defaults:
        autowire: true
        autoconfigure: true

    App\:
        resource: '../src/'
        exclude:
            - '../src/Entity/'

    # Override specific services
    App\Service\EmailService:
        class: App\Service\SmtpEmailService
```

```php
// Usage - framework handles injection
class UserController {
    public function __construct(
        private UserService $userService,
        private EmailService $emailService
    ) {
        // Dependencies automatically injected
    }
}
```

### JavaScript/TypeScript Example (InversifyJS)

```typescript
import { Container, injectable, inject } from "inversify";

// Define interfaces
interface UserRepository {
    findUser(id: string): User;
}

interface EmailService {
    send(to: string, message: string): void;
}

// Mark classes as injectable
@injectable()
class MySQLUserRepository implements UserRepository {
    findUser(id: string): User {
        // Implementation
    }
}

@injectable()
class SmtpEmailService implements EmailService {
    send(to: string, message: string): void {
        // Implementation
    }
}

@injectable()
class UserService {
    constructor(
        @inject("UserRepository") private repository: UserRepository,
        @inject("EmailService") private emailService: EmailService
    ) {}

    notifyUser(userId: string) {
        const user = this.repository.findUser(userId);
        this.emailService.send(user.email, "Hello!");
    }
}

// Configure container
const container = new Container();
container.bind<UserRepository>("UserRepository").to(MySQLUserRepository);
container.bind<EmailService>("EmailService").to(SmtpEmailService);
container.bind<UserService>("UserService").to(UserService);

// Resolve
const userService = container.get<UserService>("UserService");
```

### Testing with Frameworks

Most DI frameworks make testing easier:

```php
// PHPUnit test
class UserServiceTest extends TestCase {
    public function testNotifyUser() {
        // Create test doubles
        $mockRepository = $this->createMock(UserRepository::class);
        $mockRepository->method('findUser')
            ->willReturn(new User('test@example.com'));

        $mockEmail = $this->createMock(EmailService::class);
        $mockEmail->expects($this->once())
            ->method('send');

        // Manual injection - no framework needed
        $service = new UserService($mockRepository, $mockEmail);
        $service->notifyUser('123');
    }
}
```

**Key Point:** Even with DI frameworks, test code often uses manual injection for simplicity.

## Common Patterns and Best Practices

### 1. Prefer Constructor Injection

```php
// ✅ GOOD - Required dependencies in constructor
class OrderService {
    public function __construct(
        private OrderRepository $repository,
        private PaymentGateway $gateway,
        private EmailService $emailService
    ) {}
}

// ❌ BAD - Setter injection for required dependencies
class OrderService {
    private OrderRepository $repository;

    public function setRepository(OrderRepository $repository): void {
        $this->repository = $repository;
    }

    public function process() {
        // What if repository was never set?
        $this->repository->save();  // Potential null reference
    }
}
```

### 2. Keep Dependencies Minimal

```php
// ❌ BAD - Too many dependencies (God Object)
class UserService {
    public function __construct(
        private UserRepository $userRepo,
        private EmailService $emailService,
        private SmsService $smsService,
        private NotificationService $notificationService,
        private AuditLogger $auditLogger,
        private CacheManager $cacheManager,
        private EventDispatcher $eventDispatcher,
        private ValidationService $validator
    ) {}
}

// ✅ GOOD - Split responsibilities
class UserService {
    public function __construct(
        private UserRepository $repository,
        private UserNotifier $notifier  // Encapsulates email/sms/notifications
    ) {}
}

class UserNotifier {
    public function __construct(
        private EmailService $emailService,
        private SmsService $smsService,
        private NotificationService $notificationService
    ) {}
}
```

**Rule of Thumb:** If constructor has more than 3-4 parameters, consider refactoring.

### 3. Inject Interfaces, Not Implementations

```php
// ❌ BAD - Coupled to concrete implementation
class OrderService {
    public function __construct(
        private MySQLOrderRepository $repository  // Concrete class
    ) {}
}

// ✅ GOOD - Depends on abstraction
class OrderService {
    public function __construct(
        private OrderRepository $repository  // Interface
    ) {}
}
```

### 4. Avoid Service Locator Anti-Pattern

```php
// ❌ BAD - Service Locator (anti-pattern)
class OrderService {
    public function process() {
        $repository = ServiceLocator::get(OrderRepository::class);
        $gateway = ServiceLocator::get(PaymentGateway::class);
        // Hidden dependencies, hard to test
    }
}

// ✅ GOOD - Explicit dependencies
class OrderService {
    public function __construct(
        private OrderRepository $repository,
        private PaymentGateway $gateway
    ) {
        // Dependencies are obvious
    }
}
```

**Why Service Locator is bad:**
- Hides dependencies (not obvious what class needs)
- Hard to test (requires global state)
- Runtime errors instead of compile-time errors
- Couples to ServiceLocator

### 5. Don't Inject Configuration Primitives

```php
// ❌ BAD - Injecting primitives
class EmailService {
    public function __construct(
        private string $smtpHost,
        private int $smtpPort,
        private string $smtpUsername,
        private string $smtpPassword,
        private bool $useTls
    ) {}
}

// ✅ GOOD - Inject configuration object
class SmtpConfig {
    public function __construct(
        public readonly string $host,
        public readonly int $port,
        public readonly string $username,
        public readonly string $password,
        public readonly bool $useTls
    ) {}
}

class EmailService {
    public function __construct(
        private SmtpConfig $config
    ) {}
}
```

### 6. Use Factory for Complex Object Creation

```php
// ❌ BAD - Complex creation in Configurer
class Configurer {
    public function createEmailService(): EmailService {
        $config = new SmtpConfig(
            host: getenv('SMTP_HOST'),
            port: (int)getenv('SMTP_PORT'),
            username: getenv('SMTP_USER'),
            password: getenv('SMTP_PASS'),
            useTls: getenv('SMTP_TLS') === 'true'
        );

        $connection = new SmtpConnection($config);
        $connection->connect();
        $connection->authenticate();

        return new EmailService($connection);
    }
}

// ✅ GOOD - Use Factory for complexity
class EmailServiceFactory {
    public function create(): EmailService {
        $config = $this->loadConfig();
        $connection = $this->createConnection($config);
        return new EmailService($connection);
    }

    private function loadConfig(): SmtpConfig {
        return new SmtpConfig(
            host: getenv('SMTP_HOST'),
            port: (int)getenv('SMTP_PORT'),
            username: getenv('SMTP_USER'),
            password: getenv('SMTP_PASS'),
            useTls: getenv('SMTP_TLS') === 'true'
        );
    }

    private function createConnection(SmtpConfig $config): SmtpConnection {
        $connection = new SmtpConnection($config);
        $connection->connect();
        $connection->authenticate();
        return $connection;
    }
}

class Configurer {
    public function createEmailService(): EmailService {
        $factory = new EmailServiceFactory();
        return $factory->create();
    }
}
```

## Real-World WordPress/PHP Examples

### Example 1: WordPress Plugin with DI

```php
<?php
/**
 * Plugin Name: My Plugin
 * Description: Example using Dependency Injection
 */

namespace MyPlugin;

// Interfaces
interface Logger {
    public function log(string $message): void;
}

interface UserRepository {
    public function findByEmail(string $email): ?array;
}

// Implementations
class WordPressLogger implements Logger {
    public function log(string $message): void {
        error_log('[MyPlugin] ' . $message);
    }
}

class WordPressUserRepository implements UserRepository {
    public function findByEmail(string $email): ?array {
        $user = get_user_by('email', $email);
        return $user ? [
            'ID' => $user->ID,
            'email' => $user->user_email,
            'name' => $user->display_name,
        ] : null;
    }
}

// Service using DI
class UserNotificationService {
    public function __construct(
        private UserRepository $userRepository,
        private Logger $logger
    ) {}

    public function notifyUser(string $email, string $message): bool {
        $user = $this->userRepository->findByEmail($email);

        if (!$user) {
            $this->logger->log("User not found: {$email}");
            return false;
        }

        // Send notification
        $sent = wp_mail($user['email'], 'Notification', $message);

        if ($sent) {
            $this->logger->log("Notification sent to: {$email}");
        } else {
            $this->logger->log("Failed to send notification to: {$email}");
        }

        return $sent;
    }
}

// Configurer (Plugin Bootstrap)
class PluginConfigurer {
    private static ?self $instance = null;

    public static function getInstance(): self {
        if (self::$instance === null) {
            self::$instance = new self();
        }
        return self::$instance;
    }

    public function createNotificationService(): UserNotificationService {
        return new UserNotificationService(
            new WordPressUserRepository(),
            new WordPressLogger()
        );
    }
}

// Plugin initialization
add_action('plugins_loaded', function() {
    $configurer = PluginConfigurer::getInstance();
    $notificationService = $configurer->createNotificationService();

    // Register REST endpoint
    add_action('rest_api_init', function() use ($notificationService) {
        register_rest_route('myplugin/v1', '/notify', [
            'methods' => 'POST',
            'callback' => function($request) use ($notificationService) {
                $email = $request->get_param('email');
                $message = $request->get_param('message');

                $result = $notificationService->notifyUser($email, $message);

                return [
                    'success' => $result,
                ];
            },
        ]);
    });
});
```

### Example 2: Testing WordPress Plugin with DI

```php
<?php
// tests/UserNotificationServiceTest.php

namespace MyPlugin\Tests;

use PHPUnit\Framework\TestCase;
use MyPlugin\UserNotificationService;
use MyPlugin\UserRepository;
use MyPlugin\Logger;

class UserNotificationServiceTest extends TestCase {
    public function testNotifyUserSuccess() {
        // Create test doubles
        $mockRepository = $this->createMock(UserRepository::class);
        $mockRepository->method('findByEmail')
            ->willReturn([
                'ID' => 1,
                'email' => 'test@example.com',
                'name' => 'Test User',
            ]);

        $mockLogger = $this->createMock(Logger::class);
        $mockLogger->expects($this->once())
            ->method('log')
            ->with($this->stringContains('Notification sent'));

        // Inject test doubles
        $service = new UserNotificationService($mockRepository, $mockLogger);

        // Test
        $result = $service->notifyUser('test@example.com', 'Hello!');
        $this->assertTrue($result);
    }

    public function testNotifyUserNotFound() {
        // Create test doubles for "user not found" scenario
        $mockRepository = $this->createMock(UserRepository::class);
        $mockRepository->method('findByEmail')
            ->willReturn(null);

        $mockLogger = $this->createMock(Logger::class);
        $mockLogger->expects($this->once())
            ->method('log')
            ->with($this->stringContains('User not found'));

        // Inject test doubles
        $service = new UserNotificationService($mockRepository, $mockLogger);

        // Test
        $result = $service->notifyUser('nonexistent@example.com', 'Hello!');
        $this->assertFalse($result);
    }
}
```

### Example 3: E-commerce Order Processing

```php
<?php

namespace MyShop;

// Interfaces
interface PaymentGateway {
    public function charge(float $amount, string $token): bool;
}

interface OrderRepository {
    public function save(array $order): int;
    public function updateStatus(int $orderId, string $status): void;
}

interface InventoryService {
    public function reserve(array $items): bool;
    public function release(array $items): void;
}

interface EmailService {
    public function sendOrderConfirmation(string $email, array $order): void;
}

// Implementations
class StripePaymentGateway implements PaymentGateway {
    public function __construct(private string $apiKey) {}

    public function charge(float $amount, string $token): bool {
        // Stripe API call
        return true;
    }
}

class MySQLOrderRepository implements OrderRepository {
    public function __construct(private \wpdb $wpdb) {}

    public function save(array $order): int {
        $this->wpdb->insert(
            $this->wpdb->prefix . 'shop_orders',
            $order
        );
        return $this->wpdb->insert_id;
    }

    public function updateStatus(int $orderId, string $status): void {
        $this->wpdb->update(
            $this->wpdb->prefix . 'shop_orders',
            ['status' => $status],
            ['id' => $orderId]
        );
    }
}

// Service with complex dependencies
class OrderProcessor {
    public function __construct(
        private PaymentGateway $paymentGateway,
        private OrderRepository $orderRepository,
        private InventoryService $inventoryService,
        private EmailService $emailService
    ) {}

    public function processOrder(array $orderData): array {
        // Reserve inventory
        if (!$this->inventoryService->reserve($orderData['items'])) {
            return ['success' => false, 'error' => 'Items not available'];
        }

        try {
            // Charge payment
            $charged = $this->paymentGateway->charge(
                $orderData['total'],
                $orderData['payment_token']
            );

            if (!$charged) {
                $this->inventoryService->release($orderData['items']);
                return ['success' => false, 'error' => 'Payment failed'];
            }

            // Save order
            $orderId = $this->orderRepository->save($orderData);
            $this->orderRepository->updateStatus($orderId, 'completed');

            // Send confirmation
            $this->emailService->sendOrderConfirmation(
                $orderData['customer_email'],
                array_merge($orderData, ['id' => $orderId])
            );

            return ['success' => true, 'order_id' => $orderId];

        } catch (\Exception $e) {
            // Rollback inventory
            $this->inventoryService->release($orderData['items']);
            throw $e;
        }
    }
}

// Production Configurer
class ShopConfigurer {
    public function createOrderProcessor(): OrderProcessor {
        global $wpdb;

        return new OrderProcessor(
            new StripePaymentGateway(getenv('STRIPE_API_KEY')),
            new MySQLOrderRepository($wpdb),
            new WooCommerceInventoryService(),
            new WPMailEmailService()
        );
    }
}

// Test Configurer
class OrderProcessorTestConfigurer {
    public function createOrderProcessor(
        ?PaymentGateway $paymentGateway = null,
        ?OrderRepository $orderRepository = null,
        ?InventoryService $inventoryService = null,
        ?EmailService $emailService = null
    ): OrderProcessor {
        return new OrderProcessor(
            $paymentGateway ?? new MockPaymentGateway(),
            $orderRepository ?? new InMemoryOrderRepository(),
            $inventoryService ?? new MockInventoryService(),
            $emailService ?? new MockEmailService()
        );
    }
}

// Usage in tests
class OrderProcessorTest extends TestCase {
    public function testSuccessfulOrder() {
        $configurer = new OrderProcessorTestConfigurer();

        // Can inject specific test doubles
        $mockPayment = new MockPaymentGateway();
        $mockPayment->setNextChargeResult(true);

        $processor = $configurer->createOrderProcessor(
            paymentGateway: $mockPayment
        );

        $result = $processor->processOrder([
            'items' => [['sku' => 'ABC', 'qty' => 1]],
            'total' => 99.99,
            'payment_token' => 'tok_test',
            'customer_email' => 'test@example.com',
        ]);

        $this->assertTrue($result['success']);
    }

    public function testPaymentFailureReleasesInventory() {
        $configurer = new OrderProcessorTestConfigurer();

        // Set up payment to fail
        $mockPayment = new MockPaymentGateway();
        $mockPayment->setNextChargeResult(false);

        // Track inventory calls
        $mockInventory = new MockInventoryService();

        $processor = $configurer->createOrderProcessor(
            paymentGateway: $mockPayment,
            inventoryService: $mockInventory
        );

        $result = $processor->processOrder([
            'items' => [['sku' => 'ABC', 'qty' => 1]],
            'total' => 99.99,
            'payment_token' => 'tok_test',
            'customer_email' => 'test@example.com',
        ]);

        $this->assertFalse($result['success']);
        $this->assertTrue($mockInventory->wasReleaseCalled());
    }
}
```

## JavaScript/TypeScript Adaptations

### Basic DI in JavaScript

```javascript
// interfaces.js
export class UserRepository {
    async findByEmail(email) {
        throw new Error('Not implemented');
    }
}

export class EmailService {
    async send(to, subject, body) {
        throw new Error('Not implemented');
    }
}

// implementations.js
import { UserRepository, EmailService } from './interfaces.js';

export class MongoUserRepository extends UserRepository {
    constructor(mongoClient) {
        super();
        this.client = mongoClient;
    }

    async findByEmail(email) {
        const db = this.client.db('myapp');
        return await db.collection('users').findOne({ email });
    }
}

export class SendGridEmailService extends EmailService {
    constructor(apiKey) {
        super();
        this.apiKey = apiKey;
    }

    async send(to, subject, body) {
        // SendGrid API call
        console.log(`Sending email to ${to}: ${subject}`);
        return true;
    }
}

// service.js
export class UserNotificationService {
    constructor(userRepository, emailService) {
        this.userRepository = userRepository;
        this.emailService = emailService;
    }

    async notifyUser(email, message) {
        const user = await this.userRepository.findByEmail(email);

        if (!user) {
            console.log(`User not found: ${email}`);
            return false;
        }

        await this.emailService.send(
            user.email,
            'Notification',
            message
        );

        return true;
    }
}

// configurer.js
import { MongoClient } from 'mongodb';
import { MongoUserRepository, SendGridEmailService } from './implementations.js';
import { UserNotificationService } from './service.js';

export class ProductionConfigurer {
    async createNotificationService() {
        const mongoClient = new MongoClient(process.env.MONGO_URL);
        await mongoClient.connect();

        return new UserNotificationService(
            new MongoUserRepository(mongoClient),
            new SendGridEmailService(process.env.SENDGRID_KEY)
        );
    }
}

// main.js
import { ProductionConfigurer } from './configurer.js';

const configurer = new ProductionConfigurer();
const service = await configurer.createNotificationService();

await service.notifyUser('user@example.com', 'Hello!');
```

### TypeScript with Proper Types

```typescript
// interfaces.ts
export interface User {
    id: string;
    email: string;
    name: string;
}

export interface UserRepository {
    findByEmail(email: string): Promise<User | null>;
}

export interface EmailService {
    send(to: string, subject: string, body: string): Promise<boolean>;
}

// implementations.ts
import { MongoClient, Db } from 'mongodb';
import { UserRepository, EmailService, User } from './interfaces';

export class MongoUserRepository implements UserRepository {
    constructor(private client: MongoClient) {}

    async findByEmail(email: string): Promise<User | null> {
        const db: Db = this.client.db('myapp');
        const doc = await db.collection('users').findOne({ email });

        if (!doc) return null;

        return {
            id: doc._id.toString(),
            email: doc.email,
            name: doc.name,
        };
    }
}

export class SendGridEmailService implements EmailService {
    constructor(private apiKey: string) {}

    async send(to: string, subject: string, body: string): Promise<boolean> {
        console.log(`Sending email to ${to}: ${subject}`);
        return true;
    }
}

// service.ts
import { UserRepository, EmailService } from './interfaces';

export class UserNotificationService {
    constructor(
        private userRepository: UserRepository,
        private emailService: EmailService
    ) {}

    async notifyUser(email: string, message: string): Promise<boolean> {
        const user = await this.userRepository.findByEmail(email);

        if (!user) {
            console.log(`User not found: ${email}`);
            return false;
        }

        await this.emailService.send(
            user.email,
            'Notification',
            message
        );

        return true;
    }
}

// configurer.ts
import { MongoClient } from 'mongodb';
import { MongoUserRepository, SendGridEmailService } from './implementations';
import { UserNotificationService } from './service';

export class ProductionConfigurer {
    async createNotificationService(): Promise<UserNotificationService> {
        const mongoClient = new MongoClient(process.env.MONGO_URL!);
        await mongoClient.connect();

        return new UserNotificationService(
            new MongoUserRepository(mongoClient),
            new SendGridEmailService(process.env.SENDGRID_KEY!)
        );
    }
}

// test-configurer.ts
import { UserRepository, EmailService } from './interfaces';
import { UserNotificationService } from './service';

class MockUserRepository implements UserRepository {
    async findByEmail(email: string) {
        if (email === 'test@example.com') {
            return {
                id: '1',
                email: 'test@example.com',
                name: 'Test User',
            };
        }
        return null;
    }
}

class MockEmailService implements EmailService {
    public sentEmails: Array<{to: string, subject: string, body: string}> = [];

    async send(to: string, subject: string, body: string): Promise<boolean> {
        this.sentEmails.push({ to, subject, body });
        return true;
    }
}

export class TestConfigurer {
    createNotificationService(): UserNotificationService {
        return new UserNotificationService(
            new MockUserRepository(),
            new MockEmailService()
        );
    }
}

// test.spec.ts
import { TestConfigurer } from './test-configurer';

describe('UserNotificationService', () => {
    test('notifies existing user', async () => {
        const configurer = new TestConfigurer();
        const service = configurer.createNotificationService();

        const result = await service.notifyUser(
            'test@example.com',
            'Hello!'
        );

        expect(result).toBe(true);
    });

    test('returns false for non-existent user', async () => {
        const configurer = new TestConfigurer();
        const service = configurer.createNotificationService();

        const result = await service.notifyUser(
            'nonexistent@example.com',
            'Hello!'
        );

        expect(result).toBe(false);
    });
});
```

## Conceptual Distinctions

### Dependency Injection vs Dependency Inversion Principle vs Inversion of Control

These three concepts have similar names but are distinct:

#### Dependency Injection (DI)
- **What:** A design pattern/technique
- **How:** Dependencies are passed into a class (usually via constructor)
- **Purpose:** Resolve object dependencies without tight coupling

```php
// DI - injecting dependencies
class OrderService {
    public function __construct(
        private OrderRepository $repository  // Dependency injected
    ) {}
}
```

#### Dependency Inversion Principle (DIP)
- **What:** A design principle (the "D" in SOLID)
- **How:** Classes depend on abstractions, not concrete implementations
- **Purpose:** Prevent tight coupling between layers

```php
// DIP - depend on abstraction
class OrderService {
    public function __construct(
        private OrderRepository $repository  // Interface, not concrete class
    ) {}
}

// Concrete implementation depends on abstraction
class MySQLOrderRepository implements OrderRepository {
    // Implementation details
}
```

**Key insight:** The dependency arrow **inverts** - instead of OrderService → MySQLOrderRepository, we have OrderService → OrderRepository ← MySQLOrderRepository

#### Inversion of Control (IoC)
- **What:** A principle about control flow
- **How:** Framework calls your code, not vice versa
- **Purpose:** Framework controls the flow of execution

```php
// IoC - framework calls your code
class MyController {
    // Framework will call this method
    public function handleRequest(Request $request): Response {
        // Your code
        return new Response('Hello');
    }
}

// Traditional flow: Your code calls framework
$framework->route('/hello', function() {
    // Your code calls framework methods
});

// IoC: Framework calls your code
// Framework internally does: $controller->handleRequest($request)
```

**Relationship:**
- **DI helps achieve DIP** - by injecting abstractions, we invert dependencies
- **IoC containers often use DI** - frameworks inject dependencies they manage
- **But they're not the same** - DI is about objects, IoC is about control flow

### Visual Comparison

```
Traditional Dependency (Tight Coupling):
   OrderService ──────→ MySQLOrderRepository
                        (knows concrete class)

Dependency Inversion Principle:
   OrderService ──────→ OrderRepository (interface)
                              ↑
                MySQLOrderRepository
                (implementation points up to interface)

Dependency Injection:
   Configurer creates: new OrderService(new MySQLOrderRepository())
   OrderService receives dependency, doesn't create it

Inversion of Control:
   Framework ──────→ Your Code
   (framework calls your methods, not vice versa)
```

## When to Use Dependency Injection

### Use DI When:

1. **Testing is important** - Need to swap dependencies with test doubles
2. **Multiple implementations exist** - Different environments, configurations
3. **Dependencies are complex** - Database connections, external services
4. **Code will change** - Need flexibility to swap implementations
5. **Working in layers** - Clear separation between business logic and infrastructure

### Don't Use DI When:

1. **Dependency is stable** - Built-in language features, standard library
2. **Dependency is simple** - Value objects, data structures
3. **No variation needed** - Only one implementation will ever exist
4. **Performance critical** - DI adds indirection (though usually negligible)

### Examples of Good DI Candidates:

```php
// ✅ GOOD - External dependencies
- Database connections
- API clients
- Email services
- File storage
- Caching systems
- Logging services
- Authentication providers

// ✅ GOOD - Business logic
- Repositories
- Domain services
- Use case handlers
- Workflow orchestrators
```

### Examples of Bad DI Candidates:

```php
// ❌ BAD - Don't inject these
- DateTime objects
- Arrays/Collections
- Primitive values
- Standard library classes
- Value objects
- DTOs (Data Transfer Objects)
- Entities (domain objects with state)

// Example of what NOT to do
class UserService {
    // Don't inject DateTime - it's a value
    public function __construct(private DateTime $currentTime) {}

    // Don't inject arrays - they're data
    public function __construct(private array $config) {}
}

// Better approach
class UserService {
    // Create DateTime when needed
    public function createUser(string $name): User {
        $user = new User($name);
        $user->setCreatedAt(new DateTime());  // Create inline
        return $user;
    }

    // Pass data as method parameters
    public function configure(array $config): void {
        // Process configuration
    }
}
```

## Common Pitfalls and Solutions

### Pitfall 1: Over-Injecting

```php
// ❌ PROBLEM - Injecting too much
class ReportGenerator {
    public function __construct(
        private DataSource $dataSource,
        private Formatter $formatter,
        private Validator $validator,
        private Logger $logger,
        private Cache $cache,
        private EventDispatcher $events,
        private MetricsCollector $metrics
    ) {}
}

// ✅ SOLUTION - Group related dependencies
class ReportGenerator {
    public function __construct(
        private DataSource $dataSource,
        private ReportFormatter $formatter,  // Encapsulates formatting + validation
        private ReportingInfrastructure $infrastructure  // Encapsulates logging, cache, events, metrics
    ) {}
}
```

### Pitfall 2: Circular Dependencies

```php
// ❌ PROBLEM - A depends on B, B depends on A
class UserService {
    public function __construct(private OrderService $orderService) {}
}

class OrderService {
    public function __construct(private UserService $userService) {}
}

// ✅ SOLUTION 1 - Extract shared dependency
interface UserProvider {
    public function getUser(int $id): User;
}

class UserService implements UserProvider {
    public function getUser(int $id): User {
        // Implementation
    }
}

class OrderService {
    public function __construct(private UserProvider $userProvider) {}
}

// ✅ SOLUTION 2 - Use events to decouple
class UserService {
    public function __construct(private EventDispatcher $events) {}

    public function createUser(array $data): User {
        $user = new User($data);
        $this->events->dispatch(new UserCreated($user));
        return $user;
    }
}

class OrderService {
    public function __construct(private EventDispatcher $events) {
        $events->listen(UserCreated::class, [$this, 'onUserCreated']);
    }

    public function onUserCreated(UserCreated $event): void {
        // Handle user creation
    }
}
```

### Pitfall 3: New Operator Scattered Everywhere

```php
// ❌ PROBLEM - Creating objects directly
class OrderProcessor {
    public function process(array $orderData): Order {
        $order = new Order($orderData);  // Creating directly
        $validator = new OrderValidator();  // Creating directly

        if ($validator->validate($order)) {
            $repository = new OrderRepository();  // Creating directly
            $repository->save($order);
        }

        return $order;
    }
}

// ✅ SOLUTION - Inject dependencies
class OrderProcessor {
    public function __construct(
        private OrderFactory $orderFactory,  // Handles Order creation
        private OrderValidator $validator,
        private OrderRepository $repository
    ) {}

    public function process(array $orderData): Order {
        $order = $this->orderFactory->create($orderData);

        if ($this->validator->validate($order)) {
            $this->repository->save($order);
        }

        return $order;
    }
}
```

### Pitfall 4: God Configurer

```php
// ❌ PROBLEM - Single configurer creates everything
class GodConfigurer {
    public function configure(): Application {
        // 500 lines of object creation
        $db = new Database(...);
        $cache = new Redis(...);
        $logger = new Logger(...);
        // ... 100 more objects

        return new Application(...);
    }
}

// ✅ SOLUTION - Split into focused configurers
class DatabaseConfigurer {
    public function createDatabase(): Database {
        return new Database(
            host: getenv('DB_HOST'),
            username: getenv('DB_USER'),
            password: getenv('DB_PASS')
        );
    }
}

class CachingConfigurer {
    public function createCache(): Cache {
        return new Redis(
            host: getenv('REDIS_HOST'),
            port: (int)getenv('REDIS_PORT')
        );
    }
}

class ApplicationConfigurer {
    public function createApplication(): Application {
        $dbConfigurer = new DatabaseConfigurer();
        $cacheConfigurer = new CachingConfigurer();

        return new Application(
            $dbConfigurer->createDatabase(),
            $cacheConfigurer->createCache()
        );
    }
}
```

### Pitfall 5: Injecting Concrete Types

```php
// ❌ PROBLEM - Injecting concrete implementations
class OrderService {
    public function __construct(
        private MySQLOrderRepository $repository,  // Concrete!
        private SmtpEmailService $emailService     // Concrete!
    ) {}
}

// ✅ SOLUTION - Inject interfaces
class OrderService {
    public function __construct(
        private OrderRepository $repository,    // Interface
        private EmailService $emailService      // Interface
    ) {}
}
```

## Summary and Key Takeaways

### The Essential Insights

1. **DI solves object resolution coupling** - Not just execution coupling
2. **The Configurer is crucial** - It's the missing piece in most DI explanations
3. **Configurers sit at the architectural bottom** - Where `new` is safe
4. **Test configuration is simple** - Only one layer of dependencies needed
5. **DI works beautifully with Factories** - They complement each other

### The Three-Zone Architecture

```
Zone 1: Business Logic (Cocooned, Zero Dependencies)
Zone 2: Infrastructure (Small, Interface-Based)
Zone 3: Wiring (Knows Everything, No Business Logic)
```

### The Configurer Pattern

```php
// Characteristics of a good Configurer:
// 1. Knows entire architecture
// 2. No business logic
// 3. Only creates and wires
// 4. Invisible to configured code
// 5. Lives at bootstrap level

class ProductionConfigurer {
    public function createApplication(): Application {
        // Create dependencies
        $repo = new MySQLRepository();
        $cache = new RedisCache();
        $service = new UserService($repo, $cache);

        // Wire together
        return new Application($service);
    }
}
```

### Testing with DI

```php
// Testing is simple - one layer of test doubles
class TestConfigurer {
    public function createService(): UserService {
        return new UserService(
            new MockRepository(),    // Test double
            new MockCache()          // Test double
        );
    }
}
```

### When You See This Pattern

If you need to:
- Test edge cases (exceptions, errors, boundary conditions)
- Swap implementations (different databases, caching strategies)
- Isolate business logic from infrastructure
- Make architectural changes without ripple effects

**Then Dependency Injection with Configurers is your solution.**

## References

### Core Concepts
- [Dependency Injection - Wikipedia](https://en.wikipedia.org/wiki/Dependency_injection)
- [Dependency Inversion Principle - Wikipedia](https://en.wikipedia.org/wiki/Dependency_inversion_principle)
- [Inversion of Control - Wikipedia](https://en.wikipedia.org/wiki/Inversion_of_control)

### Essential Reading
- [Inversion of Control Containers and the Dependency Injection pattern](https://martinfowler.com/articles/injection.html) by Martin Fowler
- [Clean Architecture](https://www.amazon.com/Clean-Architecture-Craftsmans-Software-Structure/dp/0134494164/) Chapter 11 by Robert Martin
- [API Design for C++](https://www.amazon.com/API-Design-C-Martin-Reddy/dp/0123850037/) Chapter 3 by Martin Reddy

### Practical Guides
- [A Practical Introduction To Dependency Injection](https://www.smashingmagazine.com/2020/12/practical-introduction-dependency-injection/) by Jamie Corkhill
- [Java Dependency Injection Tutorial](https://www.digitalocean.com/community/tutorials/java-dependency-injection-design-pattern-example-tutorial) by Pankaj
- [System Design: Dependency Inversion Principle](https://www.baeldung.com/cs/dip) by Baeldung

### Framework-Specific
- [PHP-DI Documentation](https://php-di.org/)
- [Symfony Dependency Injection](https://symfony.com/doc/current/components/dependency_injection.html)
- [InversifyJS Documentation](http://inversify.io/)
- [Spring Dependency Injection](https://www.baeldung.com/inversion-control-and-dependency-injection-in-spring)

### Related Patterns
- [Factory Design Patterns](./factory.md)
- [Abstract Factory](./abstract-factory.md)
- [Adapter Pattern](../structural/adapter.md)
- [Façade Pattern](../structural/facade.md)

---

**Last Updated:** 2026-01-14
**Pattern Family:** Creational Patterns
**Complexity:** Medium
**Maturity:** Mature (widely adopted since ~2004)
