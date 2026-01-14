# SOLID Principles: Comprehensive Reference

**Sources:** Design Pattern Principles, Gang of Four Patterns, and synthesized best practices from industry experts.

## Overview

SOLID principles are five design guidelines that make software more understandable, flexible, and maintainable. They're not just theoretical concepts - they're practical solutions to recurring design problems that lead to rigid, fragile code.

**Critical insight:** _"SOLID principles are the foundation; design patterns are the tactical implementations."_

Each principle addresses a specific design force - understanding these forces helps you recognize when to apply each principle.

## The Five SOLID Principles (Quick Reference)

| Principle | Focus | Violation Symptom | Pattern Solutions |
|-----------|-------|-------------------|-------------------|
| **Single Responsibility** | One reason to change | Changes ripple through class | Façade, Mediator, Command |
| **Open/Closed** | Extend without modifying | Modifying code to add features | Strategy, Decorator, Template Method |
| **Liskov Substitution** | Subtypes are substitutable | Type checking, broken inheritance | Composition over Inheritance |
| **Interface Segregation** | Focused interfaces | Fat interfaces, unused methods | Multiple specific interfaces |
| **Dependency Inversion** | Depend on abstractions | Tight coupling to concrete classes | Dependency Injection, Factory, Abstract Factory |

## S - Single Responsibility Principle (SRP)

### Definition

> _A class should have one, and only one, reason to change._

**Translation:** Each class should have a single, well-defined responsibility. If you can identify multiple distinct reasons why a class might need to change, it violates SRP.

### Why It Matters

| Benefit | Description |
|---------|-------------|
| **Easier to understand** | Small, focused classes are easier to comprehend |
| **Easier to test** | Single responsibility = simpler test scenarios |
| **Easier to change** | Changes affect only relevant classes |
| **Better reusability** | Focused components can be reused in different contexts |
| **Less coupling** | Classes with single responsibility have fewer dependencies |

**Key insight:** SRP isn't about "doing one thing" - it's about having one reason to change. A class can have multiple methods, but they should all relate to the same responsibility.

### Code Smells (SRP Violations)

#### God Object (Class Does Everything)

```php
// VIOLATION: Multiple responsibilities
class OrderProcessor {
    // Responsibility 1: Order validation
    public function validate_order( $order ) {
        if ( empty( $order['items'] ) ) {
            return false;
        }
        return true;
    }

    // Responsibility 2: Payment processing
    public function process_payment( $order ) {
        $gateway = new PaymentGateway();
        return $gateway->charge( $order['total'] );
    }

    // Responsibility 3: Inventory management
    public function update_inventory( $order ) {
        foreach ( $order['items'] as $item ) {
            $this->db->query( "UPDATE products SET stock = stock - 1 WHERE id = {$item['id']}" );
        }
    }

    // Responsibility 4: Email notifications
    public function send_confirmation( $order ) {
        $mailer = new Mailer();
        $mailer->send( $order['customer_email'], 'Order Confirmed', '...' );
    }

    // Responsibility 5: Logging
    public function log_order( $order ) {
        file_put_contents( 'orders.log', json_encode( $order ) . PHP_EOL, FILE_APPEND );
    }
}
```

**Problems:**
- Changes to email format require modifying OrderProcessor
- Changes to inventory logic require modifying OrderProcessor
- Changes to payment gateway require modifying OrderProcessor
- Hard to test each responsibility in isolation
- Can't reuse validation logic elsewhere

#### Fix: Split into Focused Classes

```php
// FIXED: Each class has single responsibility

// Responsibility 1: Order validation
class OrderValidator {
    public function validate( Order $order ): bool {
        if ( $order->get_items()->is_empty() ) {
            return false;
        }
        return true;
    }
}

// Responsibility 2: Payment processing
class PaymentProcessor {
    private PaymentGateway $gateway;

    public function __construct( PaymentGateway $gateway ) {
        $this->gateway = $gateway;
    }

    public function process( Order $order ): PaymentResult {
        return $this->gateway->charge( $order->get_total() );
    }
}

// Responsibility 3: Inventory management
class InventoryManager {
    private InventoryRepository $repository;

    public function __construct( InventoryRepository $repository ) {
        $this->repository = $repository;
    }

    public function reserve_items( Order $order ): void {
        foreach ( $order->get_items() as $item ) {
            $this->repository->decrement_stock( $item->get_product_id(), $item->get_quantity() );
        }
    }
}

// Responsibility 4: Email notifications
class OrderNotifier {
    private Mailer $mailer;

    public function __construct( Mailer $mailer ) {
        $this->mailer = $mailer;
    }

    public function send_confirmation( Order $order ): void {
        $this->mailer->send(
            $order->get_customer_email(),
            'Order Confirmed',
            $this->build_confirmation_message( $order )
        );
    }

    private function build_confirmation_message( Order $order ): string {
        // Email template logic
    }
}

// Responsibility 5: Logging
class OrderLogger {
    private Logger $logger;

    public function __construct( Logger $logger ) {
        $this->logger = $logger;
    }

    public function log( Order $order ): void {
        $this->logger->info( 'Order processed', [
            'order_id' => $order->get_id(),
            'total' => $order->get_total(),
            'items_count' => $order->get_items()->count(),
        ] );
    }
}

// Coordinator: Composes focused services
class OrderService {
    private OrderValidator $validator;
    private PaymentProcessor $payment_processor;
    private InventoryManager $inventory_manager;
    private OrderNotifier $notifier;
    private OrderLogger $logger;

    public function __construct(
        OrderValidator $validator,
        PaymentProcessor $payment_processor,
        InventoryManager $inventory_manager,
        OrderNotifier $notifier,
        OrderLogger $logger
    ) {
        $this->validator = $validator;
        $this->payment_processor = $payment_processor;
        $this->inventory_manager = $inventory_manager;
        $this->notifier = $notifier;
        $this->logger = $logger;
    }

    public function process( Order $order ): OrderResult {
        if ( ! $this->validator->validate( $order ) ) {
            return OrderResult::invalid();
        }

        $payment_result = $this->payment_processor->process( $order );
        if ( ! $payment_result->is_success() ) {
            return OrderResult::payment_failed( $payment_result );
        }

        $this->inventory_manager->reserve_items( $order );
        $this->notifier->send_confirmation( $order );
        $this->logger->log( $order );

        return OrderResult::success( $order );
    }
}
```

**Benefits:**
- Each class has single reason to change
- Easy to test each responsibility
- Can reuse components independently
- Changes to email don't affect payment processing
- Clear separation of concerns

### Design Patterns Supporting SRP

| Pattern | How It Supports SRP |
|---------|-------------------|
| **Façade** | Provides simple interface to complex subsystem; each subsystem class has single responsibility |
| **Command** | Encapsulates single action/request as object |
| **Mediator** | Centralizes communication logic in single place |
| **Proxy** | Separates access control from business logic |

### How to Identify SRP Violations

Ask these questions about each class:

1. **The "And" Test:** Can you describe the class responsibility with "and" or "or"?
   - Example: "This class validates orders AND sends emails" → SRP violation

2. **The Change Test:** List reasons the class might need to change:
   - If you identify 2+ distinct reasons → SRP violation

3. **The Method Grouping Test:** Can you group methods by different concerns?
   - If yes → Each group is a separate responsibility

4. **The Reusability Test:** Would you want to reuse parts of this class elsewhere?
   - If you want only some methods → Those methods belong in separate class

### Guideline: When to Split

**Don't split prematurely:**
- Small classes (< 100 lines, < 10 methods) rarely need splitting
- Wait for actual design pressure (need to change for different reasons)
- Rule of Three: Split on third violation, not first

**Do split when:**
- Class has grown large (> 200 lines)
- Multiple reasons to change have materialized
- Hard to name the class without "and" or "Manager"
- You're constantly scrolling to find methods

## O - Open/Closed Principle (OCP)

### Definition

> _Software entities should be open for extension, but closed for modification._

**Translation:** You should be able to add new functionality without changing existing code. Extend behavior through composition and polymorphism, not by modifying working code.

### Why It Matters

| Benefit | Description |
|---------|-------------|
| **Reduces regression risk** | Don't break working code when adding features |
| **Enables parallel development** | Multiple developers can extend without conflicts |
| **Promotes reusability** | Base functionality remains stable and reusable |
| **Easier testing** | Test new behavior without retesting old behavior |

**Key insight:** OCP is about managing change. Design should anticipate variation points and provide extension mechanisms (interfaces, hooks, strategies).

### Code Smells (OCP Violations)

#### Conditional Logic for Extension

```php
// VIOLATION: Must modify code to add new discount types
class DiscountCalculator {
    public function calculate( Order $order, string $discount_type ): float {
        $total = $order->get_total();

        // Must add new case for each discount type
        switch ( $discount_type ) {
            case 'percentage':
                return $total * 0.1;
            case 'fixed':
                return 5.0;
            case 'bogo':
                return $this->calculate_bogo_discount( $order );
            case 'seasonal':
                return $this->calculate_seasonal_discount( $order );
            // Adding new discount? Modify this switch!
            default:
                return 0.0;
        }
    }
}
```

**Problems:**
- Adding new discount type requires modifying existing code
- Risk of breaking existing discount calculations
- Can't test new discount without accessing this class
- Violates Single Responsibility (knows about all discount types)

#### Fix: Strategy Pattern (Open for Extension)

```php
// FIXED: Open for extension, closed for modification

// Abstraction for discount strategies
interface DiscountStrategy {
    public function calculate( Order $order ): float;
}

// Concrete strategies (can add new ones without modifying existing code)
class PercentageDiscount implements DiscountStrategy {
    private float $percentage;

    public function __construct( float $percentage ) {
        $this->percentage = $percentage;
    }

    public function calculate( Order $order ): float {
        return $order->get_total() * $this->percentage;
    }
}

class FixedDiscount implements DiscountStrategy {
    private float $amount;

    public function __construct( float $amount ) {
        $this->amount = $amount;
    }

    public function calculate( Order $order ): float {
        return $this->amount;
    }
}

class BuyOneGetOneDiscount implements DiscountStrategy {
    public function calculate( Order $order ): float {
        $discount = 0.0;
        foreach ( $order->get_items() as $item ) {
            if ( $item->get_quantity() >= 2 ) {
                $discount += $item->get_unit_price();
            }
        }
        return $discount;
    }
}

class SeasonalDiscount implements DiscountStrategy {
    private float $winter_multiplier = 0.15;
    private float $summer_multiplier = 0.10;

    public function calculate( Order $order ): float {
        $season = date( 'n' ) <= 3 || date( 'n' ) >= 11 ? 'winter' : 'summer';
        $multiplier = $season === 'winter' ? $this->winter_multiplier : $this->summer_multiplier;
        return $order->get_total() * $multiplier;
    }
}

// Calculator: CLOSED for modification, OPEN for extension
class DiscountCalculator {
    private DiscountStrategy $strategy;

    public function __construct( DiscountStrategy $strategy ) {
        $this->strategy = $strategy;
    }

    public function calculate( Order $order ): float {
        return $this->strategy->calculate( $order );
    }

    // Can change strategy at runtime
    public function set_strategy( DiscountStrategy $strategy ): void {
        $this->strategy = $strategy;
    }
}

// Usage: Add new discount without touching existing code
$order = new Order();

// Use different strategies
$percentage_calc = new DiscountCalculator( new PercentageDiscount( 0.10 ) );
$percentage_discount = $percentage_calc->calculate( $order );

$bogo_calc = new DiscountCalculator( new BuyOneGetOneDiscount() );
$bogo_discount = $bogo_calc->calculate( $order );

// NEW: Add VIP discount without modifying any existing code
class VipDiscount implements DiscountStrategy {
    public function calculate( Order $order ): float {
        return $order->get_total() * 0.25; // 25% for VIP
    }
}

$vip_calc = new DiscountCalculator( new VipDiscount() );
$vip_discount = $vip_calc->calculate( $order );
```

**Benefits:**
- Add new discount types without modifying existing code
- Each discount strategy independently testable
- No risk of breaking existing discount calculations
- Can compose discounts (Decorator pattern)

### Design Patterns Supporting OCP

| Pattern | How It Supports OCP |
|---------|-------------------|
| **Strategy** | Encapsulate varying algorithms; add new strategies without modifying context |
| **Decorator** | Add responsibilities dynamically; stack decorators without modifying original |
| **Template Method** | Define algorithm skeleton; extend by overriding steps |
| **Factory Method** | Defer instantiation to subclasses; add new product types |
| **Chain of Responsibility** | Add new handlers without modifying existing chain |

### Real-World Example: WordPress Hooks (Built-in OCP)

WordPress hooks system is OCP in action:

```php
// Core WordPress code: CLOSED for modification
function process_post_publication( $post ) {
    save_post_to_database( $post );

    // Extension point: OPEN for extension
    do_action( 'publish_post', $post->ID, $post );

    return $post;
}

// Extend behavior WITHOUT modifying WordPress core
add_action( 'publish_post', 'send_notification_email' );
add_action( 'publish_post', 'update_search_index' );
add_action( 'publish_post', 'clear_cache' );
add_action( 'publish_post', 'post_to_social_media' );

// NEW: Add analytics WITHOUT touching existing code
add_action( 'publish_post', 'track_analytics_event' );
```

### How to Identify OCP Violations

1. **The Modification Test:** To add feature, do you modify existing class?
   - If yes → OCP violation

2. **The Switch/If-Else Test:** Adding new case requires modifying switch/if-else?
   - If yes → Replace with Strategy or polymorphism

3. **The Version Control Test:** New features show up as modifications to existing files?
   - If yes → Should show up as new files (new strategies, decorators, handlers)

### Guideline: When to Apply OCP

**Apply proactively when:**
- Known variation point (algorithms, behaviors, formats)
- External boundaries (payment gateways, shipping providers)
- Plugin/extension architecture

**Apply reactively when:**
- Second time you modify same code for similar reason
- Switch statement growing with new cases
- Conditional logic scattered across multiple methods

**Don't apply when:**
- Single implementation with no variation expected
- Private internal code unlikely to vary
- Premature abstraction (YAGNI)

## L - Liskov Substitution Principle (LSP)

### Definition

> _Subtypes must be substitutable for their base types without altering the correctness of the program._

**Translation:** If class B inherits from class A, you should be able to replace A with B without breaking the program. Subclasses should honor the contract of their parent class.

### Why It Matters

| Benefit | Description |
|---------|-------------|
| **Prevents broken inheritance** | Subclasses don't violate parent contracts |
| **Enables polymorphism** | Can use base type references confidently |
| **Improves reliability** | No unexpected behavior from subclasses |
| **Clearer contracts** | Forces explicit behavioral contracts |

**Key insight:** LSP is about behavioral subtyping, not just structural inheritance. A subclass can have the same method signatures but still violate LSP if it changes expected behavior.

### Code Smells (LSP Violations)

#### Type Checking (Instanceof)

```php
// VIOLATION: Need type checking to handle subclasses differently
abstract class Shape {
    abstract public function get_area(): float;
}

class Rectangle extends Shape {
    protected float $width;
    protected float $height;

    public function set_width( float $width ): void {
        $this->width = $width;
    }

    public function set_height( float $height ): void {
        $this->height = $height;
    }

    public function get_area(): float {
        return $this->width * $this->height;
    }
}

class Square extends Rectangle {
    // LSP VIOLATION: Changes parent behavior
    public function set_width( float $width ): void {
        $this->width = $width;
        $this->height = $width; // Couples width and height
    }

    public function set_height( float $height ): void {
        $this->width = $height; // Couples width and height
        $this->height = $height;
    }
}

// Client code breaks with Square
function test_rectangle( Rectangle $rect ) {
    $rect->set_width( 5 );
    $rect->set_height( 4 );

    // Expect 20, but Square returns 16!
    assert( $rect->get_area() === 20 );
}

$rectangle = new Rectangle();
test_rectangle( $rectangle ); // Passes

$square = new Square();
test_rectangle( $square ); // FAILS! LSP violation
```

**Problems:**
- Square changes behavior of set_width/set_height
- Client code that works with Rectangle breaks with Square
- Need type checking to handle Square differently

#### Fix: Composition Over Inheritance

```php
// FIXED: Use composition instead of inheritance

interface Shape {
    public function get_area(): float;
}

class Rectangle implements Shape {
    private float $width;
    private float $height;

    public function __construct( float $width, float $height ) {
        $this->width = $width;
        $this->height = $height;
    }

    public function set_width( float $width ): void {
        $this->width = $width;
    }

    public function set_height( float $height ): void {
        $this->height = $height;
    }

    public function get_area(): float {
        return $this->width * $this->height;
    }
}

class Square implements Shape {
    private float $side;

    public function __construct( float $side ) {
        $this->side = $side;
    }

    public function set_side( float $side ): void {
        $this->side = $side;
    }

    public function get_area(): float {
        return $this->side * $this->side;
    }
}

// Client code works with Shape interface
function calculate_total_area( array $shapes ): float {
    $total = 0;
    foreach ( $shapes as $shape ) {
        $total += $shape->get_area();
    }
    return $total;
}

// Both work correctly through common interface
$shapes = [
    new Rectangle( 5, 4 ),
    new Square( 3 ),
];
$total = calculate_total_area( $shapes ); // Works correctly
```

**Benefits:**
- No unexpected behavior changes
- Each shape has appropriate interface
- No need for type checking
- Client code works with any Shape implementation

#### Refused Bequest (Subclass Throws in Inherited Method)

```php
// VIOLATION: Subclass refuses parent capability
interface Bird {
    public function fly(): void;
    public function eat(): void;
}

class Sparrow implements Bird {
    public function fly(): void {
        echo "Sparrow flying\n";
    }

    public function eat(): void {
        echo "Sparrow eating\n";
    }
}

class Penguin implements Bird {
    public function fly(): void {
        // LSP VIOLATION: Can't fulfill contract
        throw new Exception( "Penguins can't fly!" );
    }

    public function eat(): void {
        echo "Penguin eating\n";
    }
}

// Client expects all birds can fly
function migrate_birds( array $birds ) {
    foreach ( $birds as $bird ) {
        $bird->fly(); // Crashes on Penguin!
    }
}
```

#### Fix: Interface Segregation

```php
// FIXED: Segregate interfaces by capability

interface Animal {
    public function eat(): void;
}

interface FlyingAnimal extends Animal {
    public function fly(): void;
}

class Sparrow implements FlyingAnimal {
    public function fly(): void {
        echo "Sparrow flying\n";
    }

    public function eat(): void {
        echo "Sparrow eating\n";
    }
}

class Penguin implements Animal {
    // No fly method - doesn't promise to fly
    public function eat(): void {
        echo "Penguin eating\n";
    }
}

// Client code respects capabilities
function migrate_birds( array $birds ) {
    foreach ( $birds as $bird ) {
        if ( $bird instanceof FlyingAnimal ) {
            $bird->fly();
        }
    }
}
```

**Benefits:**
- No broken promises (throwing exceptions)
- Interfaces match actual capabilities
- Type system prevents invalid usage

### Design Patterns Supporting LSP

| Pattern | How It Supports LSP |
|---------|-------------------|
| **Decorator** | Decorators must honor wrapped object's contract |
| **Strategy** | All strategies must fulfill same behavioral contract |
| **Template Method** | Subclasses extend without violating base behavior |
| **Composition over Inheritance** | Avoid inheritance hierarchies that violate LSP |

### Liskov Substitution Rules

A subtype violates LSP if it:

| Violation | Example | Fix |
|-----------|---------|-----|
| **Strengthens preconditions** | Parent accepts any string, child requires non-empty | Relax preconditions in child |
| **Weakens postconditions** | Parent guarantees non-null, child can return null | Strengthen postconditions in child |
| **Throws new exceptions** | Parent doesn't throw, child throws | Don't throw new checked exceptions |
| **Changes invariants** | Parent maintains state, child breaks it | Preserve invariants |
| **Refuses inherited behavior** | Parent has method, child throws exception | Use composition, not inheritance |

### How to Identify LSP Violations

1. **The Type Check Test:** Do you check instanceof before using object?
   - If yes → LSP violation (shouldn't need type checking)

2. **The Substitution Test:** Can you replace parent with child without breaking tests?
   - If no → LSP violation

3. **The Exception Test:** Does subclass throw exceptions parent doesn't?
   - If yes → LSP violation

4. **The Empty Method Test:** Does subclass have empty/do-nothing overrides?
   - If yes → LSP violation (refused bequest)

### Guideline: When to Use Inheritance

**Use inheritance when:**
- True "is-a" relationship (car IS-A vehicle)
- Subclass honors parent contract completely
- Behavior can be safely substituted

**Use composition when:**
- "Has-a" or "uses-a" relationship
- Subclass would violate parent contract
- Need to vary behavior independently

**Quote:**
> _Inheritance is not for code reuse. Inheritance is for polymorphic substitutability._ — Robert C. Martin

## I - Interface Segregation Principle (ISP)

### Definition

> _Clients should not be forced to depend on interfaces they do not use._

**Translation:** Many small, specific interfaces are better than one large, general-purpose interface. Don't force clients to implement methods they don't need.

### Why It Matters

| Benefit | Description |
|---------|-------------|
| **Reduces coupling** | Clients depend only on methods they use |
| **Clearer contracts** | Focused interfaces communicate intent |
| **Easier implementation** | Implement only needed methods |
| **Better composability** | Combine small interfaces as needed |

**Key insight:** ISP is about decoupling clients from interfaces. Fat interfaces create unnecessary dependencies and force implementations to provide stub methods they don't need.

### Code Smells (ISP Violations)

#### Fat Interface (Too Many Methods)

```php
// VIOLATION: Monolithic interface forces all implementations to provide all methods
interface Worker {
    public function work(): void;
    public function eat(): void;
    public function sleep(): void;
    public function get_salary(): float;
    public function take_vacation(): void;
    public function attend_meeting(): void;
    public function submit_report(): void;
}

class HumanWorker implements Worker {
    // Implements all methods naturally
    public function work(): void { /* ... */ }
    public function eat(): void { /* ... */ }
    public function sleep(): void { /* ... */ }
    public function get_salary(): float { /* ... */ }
    public function take_vacation(): void { /* ... */ }
    public function attend_meeting(): void { /* ... */ }
    public function submit_report(): void { /* ... */ }
}

class RobotWorker implements Worker {
    public function work(): void { /* ... */ }

    // FORCED to implement methods it doesn't need
    public function eat(): void {
        throw new Exception( "Robots don't eat!" );
    }

    public function sleep(): void {
        throw new Exception( "Robots don't sleep!" );
    }

    public function get_salary(): float {
        return 0; // Stub implementation
    }

    public function take_vacation(): void {
        // Do nothing - robots don't take vacation
    }

    public function attend_meeting(): void {
        throw new Exception( "Robots don't attend meetings!" );
    }

    public function submit_report(): void {
        throw new Exception( "Robots don't submit reports!" );
    }
}
```

**Problems:**
- RobotWorker forced to implement methods it doesn't use
- Violates LSP (throws exceptions for inherited methods)
- Clients depending on Worker see methods that don't apply
- Changes to Worker interface affect all implementations

#### Fix: Segregate into Focused Interfaces

```php
// FIXED: Small, focused interfaces

interface Workable {
    public function work(): void;
}

interface Feedable {
    public function eat(): void;
}

interface Sleepable {
    public function sleep(): void;
}

interface Payable {
    public function get_salary(): float;
}

interface TimeOffable {
    public function take_vacation(): void;
}

interface Meetable {
    public function attend_meeting(): void;
}

interface Reportable {
    public function submit_report(): void;
}

// Human implements relevant interfaces
class HumanWorker implements Workable, Feedable, Sleepable, Payable, TimeOffable, Meetable, Reportable {
    public function work(): void { /* ... */ }
    public function eat(): void { /* ... */ }
    public function sleep(): void { /* ... */ }
    public function get_salary(): float { /* ... */ }
    public function take_vacation(): void { /* ... */ }
    public function attend_meeting(): void { /* ... */ }
    public function submit_report(): void { /* ... */ }
}

// Robot implements ONLY relevant interfaces
class RobotWorker implements Workable {
    public function work(): void { /* ... */ }
    // No forced stub methods!
}

// Contractor: Different combination
class Contractor implements Workable, Payable, Reportable {
    public function work(): void { /* ... */ }
    public function get_salary(): float { /* ... */ }
    public function submit_report(): void { /* ... */ }
    // No eat, sleep, vacation, meetings
}

// Client code depends only on what it needs
class WorkScheduler {
    public function schedule( Workable $worker ): void {
        $worker->work(); // Only needs Workable
    }
}

class PayrollProcessor {
    public function process_payroll( Payable $employee ): void {
        $salary = $employee->get_salary(); // Only needs Payable
    }
}
```

**Benefits:**
- Each class implements only relevant interfaces
- No stub methods or thrown exceptions
- Clients declare precise dependencies
- Can compose interfaces flexibly

### Design Patterns Supporting ISP

| Pattern | How It Supports ISP |
|---------|-------------------|
| **Adapter** | Adapts fat interface to client-specific thin interface |
| **Façade** | Provides simplified interface to complex subsystem |
| **Proxy** | Implements only relevant subset of target interface |
| **Role Interface** | Define interfaces by client role, not by class structure |

### Real-World Example: WordPress Interfaces

```php
// WordPress uses ISP implicitly through focused interfaces

// Focused interface for cacheable objects
interface Cacheable {
    public function get_cache_key(): string;
    public function get_cache_data(): array;
}

// Focused interface for searchable content
interface Searchable {
    public function get_search_content(): string;
    public function get_search_title(): string;
}

// Focused interface for REST-exposed resources
interface REST_Resource {
    public function get_rest_schema(): array;
    public function prepare_for_rest(): array;
}

// Post implements only relevant interfaces
class Post implements Cacheable, Searchable, REST_Resource {
    // Implements all three - posts are cached, searchable, and REST-exposed
}

// Comment implements subset
class Comment implements Cacheable, Searchable {
    // Implements two - comments are cached and searchable, but not REST-exposed
}

// Option implements single interface
class Option implements Cacheable {
    // Implements one - options are cached but not searchable or REST-exposed
}
```

### How to Identify ISP Violations

1. **The Empty Method Test:** Do implementations have empty or stub methods?
   - If yes → Interface too fat

2. **The Exception Throwing Test:** Do implementations throw "not supported" exceptions?
   - If yes → Interface includes methods that don't apply

3. **The Client Dependency Test:** Do clients depend on interface methods they don't use?
   - If yes → Interface too general

4. **The Implementation Test:** Do different implementations use different subsets?
   - If yes → Split into focused interfaces

### Guideline: Interface Granularity

**Prefer smaller interfaces when:**
- Clients have different needs
- Implementations vary in capabilities
- Composability matters

**Single larger interface acceptable when:**
- All clients need all methods
- All implementations provide all methods
- Interface represents cohesive concept

**Quote:**
> _Make interfaces that are client-specific rather than general-purpose._ — Robert C. Martin

## D - Dependency Inversion Principle (DIP)

### Definition

> _A. High-level modules should not depend on low-level modules. Both should depend on abstractions._
>
> _B. Abstractions should not depend on details. Details should depend on abstractions._

**Translation:** Your business logic shouldn't depend on infrastructure details. Instead, both should depend on interfaces. Depend on abstractions (interfaces), not concrete implementations.

### Why It Matters

| Benefit | Description |
|---------|-------------|
| **Testability** | Mock dependencies easily in tests |
| **Flexibility** | Swap implementations without changing high-level code |
| **Maintainability** | Changes to low-level details don't affect business logic |
| **Parallel development** | Develop against interfaces before implementations exist |
| **Framework independence** | Business logic doesn't depend on frameworks |

**Key insight:** DIP inverts traditional procedural dependency flow where high-level code calls low-level code directly. Instead, high-level code defines the interfaces it needs, and low-level code implements those interfaces.

### Code Smells (DIP Violations)

#### Direct Dependency on Concrete Classes

```php
// VIOLATION: High-level class depends on low-level concrete classes
class OrderService {
    private MySQLDatabase $database;
    private SmtpMailer $mailer;
    private StripePaymentGateway $payment_gateway;

    public function __construct() {
        // Direct instantiation of concrete classes
        $this->database = new MySQLDatabase( 'localhost', 'orders_db' );
        $this->mailer = new SmtpMailer( 'smtp.example.com', 587 );
        $this->payment_gateway = new StripePaymentGateway( 'sk_test_123' );
    }

    public function process_order( array $order_data ): void {
        // High-level business logic tightly coupled to low-level details
        $this->database->insert( 'orders', $order_data );
        $this->payment_gateway->charge( $order_data['total'], $order_data['card'] );
        $this->mailer->send( $order_data['email'], 'Order Confirmed', '...' );
    }
}
```

**Problems:**
- Can't test without real database, payment gateway, email server
- Can't swap MySQL for PostgreSQL without changing OrderService
- Can't swap Stripe for PayPal without changing OrderService
- Can't swap SMTP for SendGrid without changing OrderService
- OrderService knows about infrastructure details it shouldn't care about

#### Fix: Depend on Abstractions (Dependency Injection)

```php
// FIXED: Depend on abstractions, inject dependencies

// Define abstractions (interfaces) based on high-level needs
interface OrderRepository {
    public function save( Order $order ): void;
}

interface PaymentProcessor {
    public function process_payment( float $amount, PaymentMethod $method ): PaymentResult;
}

interface Mailer {
    public function send( string $to, string $subject, string $body ): void;
}

// High-level class depends ONLY on abstractions
class OrderService {
    private OrderRepository $repository;
    private PaymentProcessor $payment_processor;
    private Mailer $mailer;

    // Dependencies injected (Dependency Injection pattern)
    public function __construct(
        OrderRepository $repository,
        PaymentProcessor $payment_processor,
        Mailer $mailer
    ) {
        $this->repository = $repository;
        $this->payment_processor = $payment_processor;
        $this->mailer = $mailer;
    }

    // Business logic depends only on abstractions
    public function process_order( Order $order ): OrderResult {
        $this->repository->save( $order );

        $payment_result = $this->payment_processor->process_payment(
            $order->get_total(),
            $order->get_payment_method()
        );

        if ( ! $payment_result->is_success() ) {
            return OrderResult::payment_failed( $payment_result->get_error() );
        }

        $this->mailer->send(
            $order->get_customer_email(),
            'Order Confirmed',
            $this->build_confirmation_message( $order )
        );

        return OrderResult::success( $order->get_id() );
    }

    private function build_confirmation_message( Order $order ): string {
        // Template logic
    }
}

// Low-level implementations depend on abstractions (implement interfaces)
class MySQLOrderRepository implements OrderRepository {
    private MySQLDatabase $database;

    public function __construct( MySQLDatabase $database ) {
        $this->database = $database;
    }

    public function save( Order $order ): void {
        $this->database->insert( 'orders', [
            'id' => $order->get_id(),
            'customer_id' => $order->get_customer_id(),
            'total' => $order->get_total(),
            'items' => json_encode( $order->get_items() ),
        ] );
    }
}

class StripePaymentProcessor implements PaymentProcessor {
    private StripePaymentGateway $gateway;

    public function __construct( StripePaymentGateway $gateway ) {
        $this->gateway = $gateway;
    }

    public function process_payment( float $amount, PaymentMethod $method ): PaymentResult {
        try {
            $charge = $this->gateway->charge( $amount, $method->get_token() );
            return PaymentResult::success( $charge->id );
        } catch ( StripeException $e ) {
            return PaymentResult::failed( $e->getMessage() );
        }
    }
}

class SmtpMailer implements Mailer {
    private string $host;
    private int $port;

    public function __construct( string $host, int $port ) {
        $this->host = $host;
        $this->port = $port;
    }

    public function send( string $to, string $subject, string $body ): void {
        // SMTP implementation
    }
}

// Composition root: Wire dependencies (only place that knows concrete classes)
$database = new MySQLDatabase( 'localhost', 'orders_db' );
$repository = new MySQLOrderRepository( $database );

$stripe_gateway = new StripePaymentGateway( 'sk_test_123' );
$payment_processor = new StripePaymentProcessor( $stripe_gateway );

$mailer = new SmtpMailer( 'smtp.example.com', 587 );

// Inject dependencies
$order_service = new OrderService( $repository, $payment_processor, $mailer );

// Now can easily swap implementations
// PostgreSQL instead of MySQL
$pg_database = new PostgreSQLDatabase( 'localhost', 'orders_db' );
$pg_repository = new PostgreSQLOrderRepository( $pg_database );

// PayPal instead of Stripe
$paypal_gateway = new PayPalGateway( 'client_id', 'secret' );
$paypal_processor = new PayPalPaymentProcessor( $paypal_gateway );

// SendGrid instead of SMTP
$sendgrid_mailer = new SendGridMailer( 'api_key' );

// OrderService unchanged!
$order_service = new OrderService( $pg_repository, $paypal_processor, $sendgrid_mailer );
```

**Benefits:**
- OrderService testable with mocks
- Can swap implementations without changing OrderService
- Business logic independent of infrastructure
- Clear separation between policy (business logic) and details (infrastructure)

### Design Patterns Supporting DIP

| Pattern | How It Supports DIP |
|---------|-------------------|
| **Dependency Injection** | Inject dependencies instead of creating them |
| **Factory** | Create objects through abstraction, not directly |
| **Abstract Factory** | Create families of related objects through abstraction |
| **Strategy** | Inject algorithm instead of hardcoding |
| **Repository** | Abstract data access behind interface |
| **Adapter** | Adapt third-party code to your abstractions |

### Dependency Injection Patterns

#### Constructor Injection (Preferred)

```php
// BEST: Dependencies explicit and immutable
class UserService {
    private UserRepository $repository;
    private PasswordHasher $hasher;

    public function __construct( UserRepository $repository, PasswordHasher $hasher ) {
        $this->repository = $repository;
        $this->hasher = $hasher;
    }
}
```

**Pros:** Dependencies clear, object always valid, testable
**Cons:** Many dependencies = long constructor (indicates SRP violation)

#### Setter Injection (For Optional Dependencies)

```php
// OK: For optional dependencies
class EmailService {
    private Mailer $mailer;
    private ?Logger $logger = null; // Optional

    public function __construct( Mailer $mailer ) {
        $this->mailer = $mailer;
    }

    public function set_logger( Logger $logger ): void {
        $this->logger = $logger;
    }
}
```

**Pros:** Optional dependencies clear
**Cons:** Object can be in invalid state if setters not called

#### Property Injection (WordPress Style)

```php
// WordPress convention: Public properties for optional dependencies
class WC_Payment_Gateway {
    public $title;
    public $description;
    public $enabled;

    // Can be overridden by subclasses or set externally
}
```

**Pros:** Flexible, easy to extend
**Cons:** No encapsulation, dependencies not enforced

### Testing with Dependency Injection

```php
// Production: Real implementations
$real_repository = new MySQLUserRepository( $database );
$real_mailer = new SmtpMailer( 'smtp.example.com' );
$service = new UserService( $real_repository, $real_mailer );

// Test: Mock implementations
class TestUserService extends TestCase {
    public function test_creates_user_successfully() {
        // Mock dependencies
        $mock_repository = $this->createMock( UserRepository::class );
        $mock_repository->expects( $this->once() )
                       ->method( 'save' )
                       ->with( $this->isInstanceOf( User::class ) );

        $mock_mailer = $this->createMock( Mailer::class );
        $mock_mailer->expects( $this->once() )
                    ->method( 'send' );

        // Inject mocks
        $service = new UserService( $mock_repository, $mock_mailer );

        // Test business logic in isolation
        $result = $service->create_user( 'test@example.com', 'password' );

        $this->assertTrue( $result->is_success() );
    }
}
```

### How to Identify DIP Violations

1. **The "new" Keyword Test:** Do classes create dependencies with `new`?
   - If yes → Should inject dependencies instead

2. **The Static Call Test:** Do classes call static methods on concrete classes?
   - If yes → Depend on abstraction instead

3. **The Framework Test:** Does business logic import framework classes?
   - If yes → Wrap framework behind abstractions

4. **The Test Difficulty Test:** Hard to test class in isolation?
   - If yes → Hidden dependencies violating DIP

### Guideline: Where to Apply DIP

**Always apply DIP for:**
- External services (APIs, databases, file systems)
- Third-party libraries
- Framework dependencies
- Infrastructure concerns

**Can skip DIP for:**
- Value objects (no behavior)
- DTOs (data transfer objects)
- Pure functions
- Standard library primitives

**Quote:**
> _The most flexible systems are those in which source code dependencies refer only to abstractions, not to concretions._ — Robert C. Martin

## SOLID Principles & Design Patterns Cross-Reference

| Design Pattern | Primary SOLID Principle | How They Connect |
|----------------|------------------------|------------------|
| **Strategy** | Open/Closed, Dependency Inversion | Algorithms interchangeable; inject strategy instead of hardcoding |
| **Factory Method** | Dependency Inversion | Depend on factory abstraction, not concrete classes |
| **Abstract Factory** | Dependency Inversion | Create families through abstraction |
| **Decorator** | Open/Closed, Liskov Substitution | Add behavior without modifying; decorators substitutable |
| **Adapter** | Interface Segregation, Dependency Inversion | Adapt fat interface to client-specific thin interface |
| **Façade** | Single Responsibility, Interface Segregation | Simplify complex subsystem; focused interface |
| **Template Method** | Open/Closed | Algorithm skeleton fixed, steps extendable |
| **Command** | Single Responsibility | Each command encapsulates single request |
| **Dependency Injection** | Dependency Inversion | Core implementation of DIP |
| **Repository** | Dependency Inversion | Abstract data access behind interface |
| **Observer** | Open/Closed | Add observers without modifying subject |
| **Composite** | Liskov Substitution | Leaf and composite both substitutable |
| **Proxy** | Open/Closed, Single Responsibility | Add behavior (caching, access control) without modifying original |

## SOLID Principles Summary

### Decision Matrix: Which Principle Am I Violating?

| Code Smell | Violated Principle | Pattern Fix |
|------------|-------------------|-------------|
| Class does too many things | **Single Responsibility** | Split into focused classes; use Façade to coordinate |
| Modifying code to add features | **Open/Closed** | Strategy, Decorator, Template Method |
| Type checking before method calls | **Liskov Substitution** | Composition over inheritance; honor contracts |
| Stub methods, unused interface methods | **Interface Segregation** | Split into focused interfaces |
| Can't test in isolation | **Dependency Inversion** | Dependency Injection; depend on abstractions |
| Hard to swap implementations | **Dependency Inversion** | Inject dependencies; use Factory |
| Changes cascade through system | **Single Responsibility + Dependency Inversion** | Decouple with interfaces; inject dependencies |

### The SOLID Hierarchy

**Foundation (Build on This First):**
1. **Single Responsibility** - Each class has one reason to change
2. **Dependency Inversion** - Depend on abstractions, inject dependencies

**Extension (Enables Flexibility):**
3. **Open/Closed** - Extend without modifying (requires DIP)
4. **Liskov Substitution** - Subtypes substitutable (required for OCP)

**Refinement (Optimizes Interfaces):**
5. **Interface Segregation** - Focused, client-specific interfaces

**Key insight:** Start with SRP and DIP. They enable the other principles.

## Quotes

> _A class should have only one reason to change._ — Robert C. Martin on SRP

> _Software entities should be open for extension, but closed for modification._ — Bertrand Meyer on OCP

> _Subtypes must be substitutable for their base types._ — Barbara Liskov on LSP

> _Make fine-grained interfaces that are client-specific._ — Robert C. Martin on ISP

> _Depend on abstractions, not on concretions._ — Robert C. Martin on DIP

> _The most flexible systems are those in which source code dependencies refer only to abstractions, not to concretions._ — Robert C. Martin

> _Design patterns are descriptions of communicating objects and classes that are customized to solve a general design problem in a particular context._ — Gang of Four

## Further Reading

- **Software Architecture:** `patterns/architectural/hexagonal-architecture.md` - How SOLID principles shape entire system architecture
- **Code Smells:** `patterns/architectural/code-smells.md` - Catalog of SOLID violations and fixes
- **Design Patterns:** `patterns/behavioral/`, `patterns/structural/`, `patterns/creational/` - Pattern implementations supporting SOLID
- **Refactoring:** `patterns/architectural/refactoring-strategies.md` - Step-by-step recipes for fixing SOLID violations

## Sources

This reference synthesizes insights from:
- [SOLID Design Principles Explained: Building Better Software Architecture | DigitalOcean](https://www.digitalocean.com/community/conceptual-articles/s-o-l-i-d-the-first-five-principles-of-object-oriented-design)
- [SOLID Principles in Design Patterns: Applying Principles for Better Code Design - Mindful Chase](https://www.mindfulchase.com/deep-dives/the-art-of-design-patterns/solid-principles-in-design-patterns-applying-principles-for-better-code-design.html)
- [SOLID Design Principles and Design Patterns with Examples - DEV Community](https://dev.to/burakboduroglu/solid-design-principles-and-design-patterns-crash-course-2d1c)
- [Design Patterns vs SOLID Principles Where They Intersect | by Tolga YILDIZ | Medium](https://medium.com/@tolgayildiz91/design-patterns-vs-solid-principles-where-they-intersect-3cb2b78a60df)
- [Solid PHP - SOLID principles in PHP | Accesto Blog](https://accesto.com/blog/solid-php-solid-principles-in-php/)
- [S.O.L.I.D: The First 5 Principles of Object Oriented Design with PHP | by Successive Digital | Medium](https://medium.com/successivetech/s-o-l-i-d-the-first-5-principles-of-object-oriented-design-with-php-b6d2742c90d7)
- Gang of Four Design Patterns
- Robert C. Martin (Uncle Bob) - Clean Architecture and SOLID Principles
- Industry best practices and real-world examples
