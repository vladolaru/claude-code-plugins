# Dependency Injection Pattern

- **Type**: Creational
- **Related Patterns**: Factory, Abstract Factory, Adapter, Facade
- **Principles**: Dependency Inversion Principle (DIP), Inversion of Control (IoC)

## The Core Problem

Even when programming to interfaces, code stays tightly coupled through **object resolution**:

```php
class ClientApplication {
    private MyInterface $myInterface;

    public function __construct() {
        // Problem: Direct dependency on Factory (and transitively on MyClass)
        $this->myInterface = MyInterfaceFactory::create();
    }
}
```

Testing becomes painful: you must modify the Factory to return test doubles, then remember to revert it. This is brittle, slow, intrusive, and error-prone.

## Dependency Injection - The Solution

Pass dependencies into a class instead of having the class create them:

```php
class ClientApplication {
    public function __construct(private MyInterface $myInterface) {}

    public function doWork() {
        $this->myInterface->doThis();
    }
}

$app = new ClientApplication(new MyClass());
```

### Three Injection Methods

- **Constructor injection** (preferred) -- dependencies immutable, required deps explicit, object always valid
- **Setter injection** -- for optional deps; risk of invalid state if not set
- **Interface injection** (rare) -- framework-driven, enforces injection via type system

### The Configurer (Assembler / Wiring)

The crucial missing concept: the entity that knows the entire architecture, creates and wires dependencies, contains **no business logic**, and is invisible to the rest of the system.

```php
// Production Configurer
class ProductionConfigurer {
    public function createApp(): Application {
        return new Application(
            new MySQLDatabase(),
            new SmtpEmailService(),
            new S3FileStorage()
        );
    }
}

// Test Configurer - only one layer of test doubles needed
class TestConfigurer {
    public function createApp(): Application {
        return new Application(
            new InMemoryDatabase(),
            new MockEmailService(),
            new InMemoryFileStorage()
        );
    }
}
```

Configurers live at **bootstrap level** (e.g., `main()`, `index.php`). At this level, using `new` is safe. Each layer is tested independently -- interfaces act as firebreaks.

### Three-Zone Architecture

| Zone | Contents | Rule |
|------|----------|------|
| Business Logic | Domain objects, interfaces, use cases | Zero knowledge of concrete deps |
| Infrastructure | Concrete implementations (Adapters/Facades) | Know only their interface |
| Wiring/Bootstrap | Configurers | Know everything, no business logic |

## DI with Factories

DI and Factories complement each other. Inject the Factory **interface**, not a concrete factory:

```php
class DrawingApplication {
    public function __construct(private ShapeFactory $factory) {}

    public function drawShape(string $type) {
        $shape = $this->factory->createShape($type);
        $shape->draw();
    }
}
```

- **Factory handles:** Runtime polymorphism, object creation logic
- **DI handles:** Compile-time wiring, architectural dependencies

## DI Container Setup (PHP)

```php
// PHP-DI
$containerBuilder = new ContainerBuilder();
$containerBuilder->addDefinitions([
    UserRepository::class => DI\create(MySQLUserRepository::class),
    EmailService::class   => DI\create(SmtpEmailService::class),
    UserService::class    => DI\create()
        ->constructor(DI\get(UserRepository::class), DI\get(EmailService::class)),
]);
$container = $containerBuilder->build();
$userService = $container->get(UserService::class);
```

```yaml
# Symfony DI (Autowiring) -- services.yaml
services:
    _defaults: { autowire: true, autoconfigure: true }
    App\: { resource: '../src/' }
    App\Service\EmailService: { class: App\Service\SmtpEmailService }
```

Even with DI frameworks, tests often use **manual injection** for simplicity.

## TypeScript DI with Interfaces and Mocks

```typescript
// Interfaces -- service depends only on these
export interface UserRepository {
    findByEmail(email: string): Promise<User | null>;
}
export interface EmailService {
    send(to: string, subject: string, body: string): Promise<boolean>;
}

// Service with constructor injection
export class UserNotificationService {
    constructor(
        private userRepository: UserRepository,
        private emailService: EmailService
    ) {}
    async notifyUser(email: string, message: string): Promise<boolean> {
        const user = await this.userRepository.findByEmail(email);
        if (!user) return false;
        await this.emailService.send(user.email, 'Notification', message);
        return true;
    }
}

// Mock implementations for tests
class MockUserRepository implements UserRepository {
    async findByEmail(email: string) {
        return email === 'test@example.com'
            ? { id: '1', email: 'test@example.com', name: 'Test User' } : null;
    }
}
class MockEmailService implements EmailService {
    public sentEmails: Array<{to: string; subject: string; body: string}> = [];
    async send(to: string, subject: string, body: string) {
        this.sentEmails.push({ to, subject, body });
        return true;
    }
}

// Test configurer -- one layer of mocks, no real infrastructure
export class TestConfigurer {
    createNotificationService(): UserNotificationService {
        return new UserNotificationService(new MockUserRepository(), new MockEmailService());
    }
}
```

## When to Use / When NOT to Use

**Use DI when:**
- Testing matters (swap deps with test doubles)
- Multiple implementations exist (different environments)
- Dependencies are complex (DB, external services, caching)
- Clear separation between business logic and infrastructure

**Skip DI for:**
- Stable, built-in dependencies (standard library)
- Value objects, DTOs, primitives
- Cases where only one implementation will ever exist

## Common Mistakes

### Over-Injecting (God Object Signal)

```php
// WRONG: 7+ constructor params = split responsibilities
class UserService {
    public function __construct(
        private UserRepository $repo, private EmailService $email,
        private SmsService $sms, private NotificationService $notify,
        private AuditLogger $audit, private CacheManager $cache,
        private EventDispatcher $events, private ValidationService $validator
    ) {}
}
// RIGHT: Group related dependencies
class UserService {
    public function __construct(
        private UserRepository $repository,
        private UserNotifier $notifier  // encapsulates email/sms/notifications
    ) {}
}
```

### Other Common Mistakes

- **Injecting concrete types** -- use `OrderRepository` (interface), not `MySQLOrderRepository`
- **Service Locator** -- hidden deps via `ServiceLocator::get()` are untestable; use explicit constructor injection
- **Circular dependencies** -- extract a shared interface or use events to decouple
- **God Configurer** -- split into focused configurers per subsystem (`DatabaseConfigurer`, `CachingConfigurer`, etc.)

## DI vs DIP vs IoC

| Concept | What | How |
|---------|------|-----|
| **DI** (Dependency Injection) | Design pattern | Dependencies passed in (usually via constructor) |
| **DIP** (Dependency Inversion Principle) | SOLID principle | Both high/low-level depend on abstractions |
| **IoC** (Inversion of Control) | Control flow principle | Framework calls your code, not vice versa |

DI is a technique that helps achieve DIP. IoC containers often use DI, but they address different concerns.
