# SOLID Principles Reference

## Quick Reference

| Principle | Violation Symptom | Fix |
|-----------|-------------------|-----|
| **Single Responsibility (SRP)** | Changes ripple through class; name has "and"/"Manager" | Split into focused classes; Facade to coordinate |
| **Open/Closed (OCP)** | Modifying existing code to add features; growing switch | Strategy, Decorator, Template Method |
| **Liskov Substitution (LSP)** | `instanceof` checks; subclass throws in inherited method | Composition over inheritance; honor contracts |
| **Interface Segregation (ISP)** | Stub/empty methods; unused interface methods | Split into focused interfaces |
| **Dependency Inversion (DIP)** | Can't test in isolation; hard to swap implementations | Depend on abstractions; inject dependencies |

## S - Single Responsibility Principle

**Rule:** Each class has one reason to change. Multiple methods fine if they serve same responsibility.

**Violation detection:** "And" test (describe with "and"/"or"?), Change test (2+ reasons to change?), Reusability test (want only some methods elsewhere?)

### WordPress Example: God Object → Focused Classes

```php
// VIOLATION: OrderProcessor handles validation, payment, inventory, email, logging
class OrderProcessor {
    public function validate_order( $order ) { /* ... */ }
    public function process_payment( $order ) { $gateway = new PaymentGateway(); /* ... */ }
    public function update_inventory( $order ) { /* raw SQL */ }
    public function send_confirmation( $order ) { $mailer = new Mailer(); /* ... */ }
}

// FIXED: Focused services composed by coordinator
class OrderValidator { public function validate( Order $order ): bool { /* ... */ } }
class PaymentProcessor {
    public function __construct( PaymentGateway $gateway ) {}
    public function process( Order $order ): PaymentResult { /* ... */ }
}

class OrderService {
    public function __construct(
        OrderValidator $validator, PaymentProcessor $payment, InventoryManager $inventory,
        OrderNotifier $notifier, OrderLogger $logger
    ) {}

    public function process( Order $order ): OrderResult {
        if ( ! $this->validator->validate( $order ) ) return OrderResult::invalid();
        $payment = $this->payment->process( $order );
        if ( ! $payment->is_success() ) return OrderResult::payment_failed( $payment );
        $this->inventory->reserve_items( $order );
        $this->notifier->send_confirmation( $order );
        return OrderResult::success( $order );
    }
}
```

**When to split:** Class > 200 lines, multiple change reasons materialized, hard to name without "and".
**Don't split:** < 100 lines / < 10 methods, no actual design pressure (Rule of Three).

## O - Open/Closed Principle

**Rule:** Extend behavior through composition and polymorphism, not by modifying working code.

### WordPress Example: Switch → Strategy

```php
// VIOLATION: Must modify to add discount types
class DiscountCalculator {
    public function calculate( Order $order, string $type ): float {
        switch ( $type ) {
            case 'percentage': return $order->get_total() * 0.1;
            case 'fixed': return 5.0;
            case 'bogo': return $this->calculate_bogo( $order );
        }
    }
}

// FIXED: Open for extension, closed for modification
interface DiscountStrategy {
    public function calculate( Order $order ): float;
}

class PercentageDiscount implements DiscountStrategy {
    public function __construct( private float $pct ) {}
    public function calculate( Order $order ): float { return $order->get_total() * $this->pct; }
}
// Add VipDiscount without modifying any existing code
class VipDiscount implements DiscountStrategy {
    public function calculate( Order $order ): float { return $order->get_total() * 0.25; }
}
```

### WordPress Hooks: Built-in OCP

```php
// Core CLOSED for modification, OPEN via extension point
do_action( 'publish_post', $post->ID, $post );
// Plugins extend without modifying core
add_action( 'publish_post', 'send_notification_email' );
add_action( 'publish_post', 'track_analytics_event' ); // NEW - no existing code touched
```

**Apply proactively:** Known variation points, external boundaries, plugin architecture.
**Apply reactively:** Second modification for similar reason, growing switch.
**Skip:** No variation expected, private internal code (YAGNI).

## L - Liskov Substitution Principle

**Rule:** Subtypes substitutable for base types without breaking correctness. Behavioral subtyping, not just structural.

| Violation | Fix |
|-----------|-----|
| Strengthens preconditions | Relax in child |
| Weakens postconditions | Strengthen in child |
| Throws new exceptions | Don't add checked exceptions |
| Changes invariants | Preserve invariants |
| Refuses inherited behavior | Use composition |

### WordPress Example

```php
// VIOLATION: Square changes Rectangle behavior — test_rectangle(new Square()) breaks
class Square extends Rectangle {
    public function set_width( float $w ): void { $this->width = $this->height = $w; }
}

// FIXED: Shared interface, independent implementations
interface Shape { public function get_area(): float; }
class Rectangle implements Shape {
    public function __construct( private float $w, private float $h ) {}
    public function get_area(): float { return $this->w * $this->h; }
}
class Square implements Shape {
    public function __construct( private float $side ) {}
    public function get_area(): float { return $this->side * $this->side; }
}
```

**Detection:** `instanceof` checks, broken substitution tests, new exceptions, empty overrides.
**Inheritance:** True "is-a", honors parent contract completely.
**Composition:** "Has-a", would violate contract, independent behavior variation.

## I - Interface Segregation Principle

**Rule:** Many small interfaces beat one fat interface. Don't force unused method implementations.

### WordPress Example

```php
// VIOLATION: Fat interface
interface Worker { public function work(); public function eat(); public function get_salary(); }
class RobotWorker implements Worker {
    public function work() { /* ... */ }
    public function eat() { throw new Exception( "Robots don't eat!" ); } // Forced stub
}

// FIXED: Segregated
interface Workable { public function work(): void; }
interface Feedable { public function eat(): void; }
interface Payable  { public function get_salary(): float; }

class RobotWorker implements Workable { public function work(): void { /* ... */ } }
class HumanWorker implements Workable, Feedable, Payable { /* all three */ }
```

### WordPress ISP in Practice

```php
interface Cacheable { public function get_cache_key(): string; public function get_cache_data(): array; }
interface Searchable { public function get_search_content(): string; }
interface REST_Resource { public function get_rest_schema(): array; }

class Post implements Cacheable, Searchable, REST_Resource { /* all three */ }
class Comment implements Cacheable, Searchable { /* two */ }
class Option implements Cacheable { /* one */ }
```

**Detection:** Empty/stub methods, "not supported" exceptions, different implementations use different subsets.

## D - Dependency Inversion Principle

**Rule:** High-level and low-level modules both depend on abstractions. Business logic defines needed interfaces; infrastructure implements them.

### WordPress Example

```php
// VIOLATION: Hardwired to concrete details
class OrderService {
    public function __construct() {
        $this->database = new MySQLDatabase( 'localhost', 'orders_db' );
        $this->mailer = new SmtpMailer( 'smtp.example.com', 587 );
    }
}

// FIXED: Depend on abstractions, inject
interface OrderRepository { public function save( Order $order ): void; }
interface Mailer { public function send( string $to, string $subject, string $body ): void; }

class OrderService {
    public function __construct( private OrderRepository $repo, private Mailer $mailer ) {}
    public function process_order( Order $order ): OrderResult { /* depends only on abstractions */ }
}

// Composition root: only place knowing concrete classes
$svc = new OrderService( new MySQLOrderRepository( $db ), new SmtpMailer( 'smtp.example.com', 587 ) );
// Swap freely
$svc = new OrderService( new PostgreSQLOrderRepository( $pg ), new SendGridMailer( 'key' ) );
```

| Injection Pattern | When | Trade-off |
|-------------------|------|-----------|
| Constructor (preferred) | Required deps | Many params = SRP smell |
| Setter | Optional deps | Object may be invalid |
| Property (WP style) | WP convention | No encapsulation |

**Detection:** `new` in classes, static calls to concretions, framework imports in biz logic, hard to test.
**Always apply:** External services, third-party libs, framework deps, infrastructure.
**Skip:** Value objects, DTOs, pure functions, stdlib primitives.

## SOLID → Design Patterns Cross-Reference

| Pattern | Primary Principle | Connection |
|---------|-------------------|------------|
| **Strategy** | OCP, DIP | Interchangeable algorithms; inject instead of hardcode |
| **Decorator** | OCP, LSP | Add behavior without modifying; substitutable |
| **Adapter** | ISP, DIP | Thin interface; wrap external behind abstraction |
| **Facade** | SRP, ISP | Simplify subsystem; focused interface |
| **Template Method** | OCP | Skeleton fixed, steps extendable |
| **Command** | SRP | Single request encapsulated |
| **Factory** | DIP | Create through abstraction |
| **Repository** | DIP | Abstract data access |
| **Observer** | OCP | Add observers without modifying subject |
| **Composite** | LSP | Leaf and composite substitutable |

## Decision Matrix

| Code Smell | Principle | Pattern Fix |
|------------|-----------|-------------|
| Class does too many things | SRP | Split; Facade to coordinate |
| Modifying code to add features | OCP | Strategy, Decorator, Template Method |
| Type checking before method calls | LSP | Composition over inheritance |
| Stub methods, unused interface methods | ISP | Split into focused interfaces |
| Can't test in isolation / swap impls | DIP | Inject dependencies; Factory |
| Changes cascade through system | SRP + DIP | Decouple with interfaces |

## SOLID Hierarchy

**Foundation:** 1. SRP (one reason to change) → 2. DIP (depend on abstractions)
**Extension:** 3. OCP (extend without modifying, requires DIP) → 4. LSP (substitutable, required for OCP)
**Refinement:** 5. ISP (focused, client-specific interfaces)
