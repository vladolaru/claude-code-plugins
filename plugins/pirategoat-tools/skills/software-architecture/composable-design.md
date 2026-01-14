# Composable Design Patterns Reference

> **E Pluribus Unum** - From many, one. Behavior emerges from the interaction of cohesive objects organized in a pattern.

## Overview

### What is Composable Design?

Composable design patterns feature **object composition** where behavior emerges from the interaction of a set of objects organized in a cohesive pattern, rather than from a single monolithic class.

Think of logic gates: binary addition emerges from the interaction among logic gates. Logic gates can be organized in different combinations from which emerges all behaviors electronic computers depend upon. Similarly, composable design patterns use low-level objects as building blocks that can be assembled in different combinations to produce different emergent behaviors.

### Composition vs Inheritance

Both inheritance and composition feature code reuse, but they achieve it in fundamentally different ways:

| Aspect | Inheritance | Composition |
|--------|-------------|-------------|
| **Reuse Type** | Vertical single static class reuse | Horizontal multiple dynamic object reuse |
| **Configuration** | Compile time via language constructs | Runtime via object delegation |
| **Flexibility** | Statically locked into place | Dynamic and reconfigurable |
| **Direction** | Vertical in UML diagrams | Horizontal in UML diagrams |
| **Encapsulation** | Violates encapsulation | Preserves encapsulation |
| **Changes** | Ancestor changes can break descendants | Objects remain independent |
| **LSP Risk** | High when overriding behavior | Low with proper interfaces |

### The GoF Design Principles

The Gang of Four (GoF) approached reuse via two design principles:

1. **Program to an interface, not an implementation**
2. **Favor object composition over class inheritance**

Note: The GoF chose "favor" deliberately. Do not eschew inheritance but compare it against object composition. Favor object composition, but inheritance is still an option.

### Implementation Inheritance vs Interface Inheritance

The issues with inheritance reside primarily with **implementation inheritance**:
- Reuse is statically locked into place
- Inheritance hierarchies become unyieldingly large
- Violates encapsulation
- Changes in ancestor classes can break previously working behavior
- Overriding behavior risks violating Liskov Substitution Principle

**Interface inheritance** does not have these issues and is fundamental to composable patterns.

## When to Use Composition

### Ideal Use Cases

1. **Rule/Policy-Based Behaviors**
   - Customized rule or policy-based behaviors
   - Single code base supporting different policy requirements for different customers or users
   - Policy behavior can be changed without changing the implementation
   - Not the same as config values, feature flags, or branching logic

2. **Insurance Industry**
   - Support many types of insurance with potential additional riders
   - Handle highly regulated environments with varying regulations by jurisdiction
   - Each policy holder can have unique policy combination
   - Support individualized billing, policy statements, and other insurance elements
   - Configuration updates are faster than implementation updates

3. **User Data Rights Regulations**
   - Highly rule/policy based systems subject to external forces
   - Segregated and segmented requirements (GDPR, California, etc.)
   - Prone to change with tight inflexible deadlines
   - Handle vague, ambiguous, or incomplete regulations
   - Quick updates via configuration rather than implementation

4. **Self-Service Apps and Kiosks**
   - Allow customers to customize their orders beyond static menu options
   - Design-your-own configurations (sandwiches, products, services)
   - Predefined configurations with optional customizations
   - Customer self-service composition

5. **Systems Requiring Runtime Flexibility**
   - Multiple configuration options presented to customers or users
   - Different compositions yield different behaviors without code changes
   - Customer or user self-service configurations
   - Behaviors that cannot all be tested in advance

### When Composition is Superior to Inheritance

Use composition when you need:
- **Dynamic behavior changes at runtime**
- **Multiple behaviors combined in various ways**
- **Behavior changes without recompiling**
- **Customer or user-driven configuration**
- **Policy-based systems that change frequently**
- **Code reuse across unrelated class hierarchies**
- **Concurrent execution with stateless components**

## Core Composable Pattern Concepts

### Emergent Behavior

**Key Principle:** Behavior emerges from the collective cohesive objects, not from the implementation within one individual class.

- No individual object is responsible for the entire behavior
- Yet all of them are responsible for it collectively
- Low-level elements can be reused in different combinations to define different emergent behaviors
- Behavior is distributed across multiple cohesive objects

**Challenge:** The most challenging aspect of composable design patterns is comprehension of distributed behavior across multiple objects. The challenge is not in the implementation itself, but in understanding the distributed behavior.

### Atoms and Molecules Analogy

Think of composable design like chemistry:
- **Atoms** are low-level objects (like elements on the Periodic Table)
- Each has a unique simple behavior
- **Molecules** emerge when atoms are assembled together
- Complex behaviors spring forth from finite set of low-level objects
- Just as many different complex molecules spring forth from the finite set of elements in the Periodic Table, many different complex behaviors can spring forth from the finite set of low-level objects

### Minecraft Redstone Example

Minecraft demonstrates composability through redstone material:
- Used to make primitive mechanical devices, electrical circuits, and logic gates
- Allows construction of many complex systems
- Users have built functional virtual computers within Minecraft
- Working hard drives, 8-bit virtual computers, even Minecraft within Minecraft
- Command blocks used to create emulators for Atari 2600 and Game Boy Advance

### Composition Structure

**Key Difference from Traditional Data Structures:**
- Traditional data structures contain **data**
- Composable design patterns data structures contain **functional behaviors**
- Same implementation of classes can yield different behavior based upon different compositions of objects
- Not different behavior via `if`/`switch` statements or feature flags
- Configurable behavior emerges from object composition rather than different execution paths

### Self-Referential Delegation

The defining characteristic of composable patterns:
- Concrete classes implement an interface/abstract class
- They also delegate to that same interface/abstract class
- This is **not** a circular dependency (follow the dependency arrows - they both flow in same direction)
- Allows instances to delegate to other instances of the same type
- Configuration flexibility allows sets of composed objects to be as small or as large as needed

## Pattern Progression

### The Composable Design Patterns

Listed in order from least complexity to most complexity. Several patterns expand upon concepts that first appear in previous ones:

#### 1. Proxy
**Purpose:** Place administrative wrapper objects around objects to help manage their complexity or resources.

**Key Features:**
- Can defer creation (virtual proxy)
- Control access (protection proxy)
- Add behavior (remote proxy)

**Advantages:**
- Reduces resource usage
- Adds security or logging
- Can act as stand-in for remote objects

**Disadvantages:**
- Adds complexity
- May introduce latency if misused

**Common Use Cases:**
- Lazy-loading large objects
- Access control
- Remote service calls

#### 2. Decorator
**Purpose:** Layer additional behaviors upon core features dynamically without modifying their code.

**Key Features:**
- Wraps objects in decorator classes
- Each adds behavior before/after delegating

**Advantages:**
- Promotes flexibility
- Avoids subclass explosion

**Disadvantages:**
- Many small classes can make code harder to follow

**Common Use Cases:**
- UI element styling
- I/O stream enhancements
- Logging wrappers

#### 3. Chain of Responsibility
**Purpose:** Delegate a request through a linked chain of handlers until one can complete the request.

**Key Features:**
- Each handler decides to process the request or pass it on

**Advantages:**
- Decouples sender from receiver
- Flexible request handling

**Disadvantages:**
- May lead to requests going unhandled
- Hard to debug long chains

**Common Use Cases:**
- Event handling
- Middleware pipelines
- Technical support escalation

#### 4. Composite
**Purpose:** Configure behavior emerging from a group of snippet behavior objects organized in a tree structure.

**Key Features:**
- Tree structure where leaf and composite nodes share same interface
- Treats individual objects and object compositions uniformly

**Advantages:**
- Simplifies client code
- Easy to add new components

**Disadvantages:**
- Can make design overly general
- Harder to restrict certain compositions

**Common Use Cases:**
- GUI components
- File systems
- Organization hierarchies

#### 5. Specification
**Purpose:** Allow a Client to select or filter objects with specific attribute property values as specified by the Client.

**Key Features:**
- Encapsulates business rules as reusable, combinable objects
- Combines simple specifications with logical operators (AND, OR, NOT)

**Advantages:**
- Improves maintainability
- Supports dynamic rule composition

**Disadvantages:**
- Can become complex with deeply nested combinations

**Common Use Cases:**
- Filtering in repositories
- Business rule validation
- Query building

#### 6. Interpreter
**Purpose:** Given a language, define a representation for its grammar along with an interpreter that uses the representation to interpret sentences in the language.

**Key Features:**
- Represents grammar elements as class hierarchies
- Uses `interpret` method
- Defines a grammar and uses classes to represent and evaluate language rules

**Advantages:**
- Easy to extend with new grammar rules
- Works well for small languages

**Disadvantages:**
- Inefficient for complex languages
- Class explosion for large grammars

**Common Use Cases:**
- Scripting engines
- Expression evaluation
- DSL processing

### Pattern Categories

**Structural Patterns** (focus on object composition and structure):
- Proxy - controls access
- Decorator - adds behavior
- Composite - manages hierarchies

**Behavioral Patterns** (focus on interaction and decision-making):
- Chain of Responsibility - passes requests
- Specification - defines rules
- Interpreter - parses/evaluates

### Supporting Creational Patterns

Two additional creational patterns are useful for composable patterns:

- **Builder** - Parse a complex representation, create one of several targets
- **Prototype** - Specify the kinds of objects to create using a prototypical instance, and create new objects by copying this prototype

## Computation and Coordination Model

### Two Separate Pieces

> "We can build a complete programming model out of two separate pieces - the **computation model** and the **coordination model**."
>
> - "Coordination Languages and their Significance", David Gelernter and Nicholas Carriero, Communications of the ACM, 1992

### The Division

Composable patterns are divided into two concepts:
1. **Computation** - Code reuse using delegation
2. **Coordination** - Organization of the objects that comprise the composition

**Keep these two concepts separate:**
- Computation resides in the class implementation
- Coordination resides with the Configurer

### Computation/Coordination in Various Layers

This concept appears throughout computing:

| Layer | Computation Model | Coordination Model |
|-------|-------------------|-------------------|
| **Programming Languages** | Finite set of computational components (classes, methods, variables) | Developers coordinate components by writing programs |
| **Unix/Linux** | Small independent elementary OS behaviors | Developers coordinate features via pipes or shell constructs |
| **Microservices** | Services that do one thing well | Systems built upon coordination of interacting microservices |
| **Design Patterns** | Programming to an interface - raw and reusable potential | Object composition - instantiating and assembling to produce desired behaviors |

### Coordination Spectrum

Behavior emerges from coordination/composition of objects. Composition management may reside on a spectrum:

1. **Development Organization Composition** - Developers control all configuration
2. **Customer [Support] Organization Composition** - Support teams configure for customers
3. **Customer Self-Service Composition** - Customers configure their own systems
4. **Individual User Self-Service Composition** - End users configure their experience

As composition management moves away from development and closer to the customer and user:
- Definition of desired behavior moves from development to customer/user
- Responsibility for desired behavior moves from development to customer/user

### The Achilles Heel

While some compositions can be tested for sanity:
- It may not be possible to test all possible behaviors
- We may never know the behaviors composed by customers or users via self-service
- This is a trade-off for the flexibility composable patterns provide

## Benefits

### Code Reuse Benefits

1. **True Business Domain Reuse**
   - Generic reuse through frameworks and utility libraries is solved
   - Composable patterns enable reuse of business domain concepts
   - Revolutionary code, not just evolutionary code

2. **Modular and Flexible**
   - Relatively small implementation provides significant functional potential
   - Potential means of code reuse for new features
   - Possibility of customized features for customers and users

3. **Dynamic Configuration**
   - Same objects in different compositions yield different behaviors
   - Single code base can support different policy requirements for different customers or users
   - Configuration changes without implementation changes

### Technical Benefits

1. **Encapsulation**
   - Client has knowledge of single interface reference
   - No knowledge whatsoever of design or configuration beyond interface
   - Composition may consist of single object or thousands of objects
   - Client has no dependencies beyond the contract

2. **Testability**
   - All classes can be easily unit tested
   - Non-Configurer concrete classes only know about interfaces
   - Pure function composition makes testing straightforward

3. **Extensibility**
   - New concrete classes can be added without affecting existing classes or configurations
   - Adding new low-level behaviors doesn't require touching existing code
   - New compositions can be created from existing building blocks

4. **Concurrency**
   - Classes tend to be stateless
   - Each can be viewed as pure function
   - Composition can be viewed as composition of pure functions
   - Ideal for concurrent implementations
   - Multiple threads can execute simultaneously within set of objects
   - Composition might only need to be composed once and used repeatedly by many threads

## Trade-offs

### Disadvantages

1. **Complexity of Understanding**
   - Distributed behavior across multiple objects is challenging to comprehend
   - Many small classes can make code harder to follow
   - Hard to debug long chains or deeply nested combinations
   - May make design overly general

2. **Runtime Overhead**
   - More runtime responsibility than inheritance
   - Composition won't happen by default
   - May introduce latency if misused
   - Each pattern adds flexibility through composition but may add runtime overhead

3. **Testing Challenges**
   - May not be possible to test all possible behaviors
   - May never know behaviors composed by customers or users
   - Requests may go unhandled in chain patterns
   - Inefficient for complex languages/grammars

4. **Design Considerations**
   - Adds complexity
   - Harder to restrict certain compositions
   - Can become complex with deeply nested combinations
   - Class explosion for large grammars in Interpreter pattern

### When to Avoid

Consider alternatives to composition when:
- Single static behavior is sufficient
- Performance is critical and overhead cannot be tolerated
- Implementation will never change or be customized
- Inheritance hierarchy is simple and stable
- Behavior does not need runtime configuration

## Implementation Principles

### The Basic Structure

Every composable design pattern follows this general structure:

```
┌─────────────┐
│  <<interface>>  │
│   Feature   │
│─────────────│
│ + execute() │
└─────────────┘
      ▲
      │ implements
      │
  ┌───┴──────────────────┐
  │                      │
┌─────────────┐    ┌──────────────┐
│   Simple    │    │  Composable  │
│─────────────│    │──────────────│
│ + execute() │    │ - feature    │─────┐
└─────────────┘    │ + execute()  │     │
                   └──────────────┘     │
                          │              │
                          └──────────────┘
                           delegates to
                           Feature
```

Key elements:
1. **Feature Interface/Abstract Class** - Root element defining contract
2. **Simple Implementations** - Concrete classes implementing Feature
3. **Composable Implementations** - Concrete classes that both implement Feature AND delegate to Feature
4. **Self-Referential Delegation** - Composable classes delegate to the same interface they implement

### The Critical Configurer

The **Configurer** is critical to composable design patterns and never featured by the GoF:

**Responsibilities:**
- Instantiates the objects
- Assembles their configuration
- Injects the "root" instance into the Client

**Importance:**
- Manages the entire design
- Without it, classes in these patterns only have potential
- Knows the context of each application
- Instantiates and assembles low-level objects to satisfy that context

**Separation of Concerns:**
- Computation resides in class implementation
- Coordination resides with the Configurer
- Keep these two concepts separate

### Key Implementation Guidelines

1. **Program to Interfaces**
   - Non-Configurer concrete classes only know about interfaces
   - Client depends only on the interface contract
   - New concrete classes can be added without affecting existing code

2. **Favor Stateless Classes**
   - Classes tend to be stateless
   - Each can be viewed as pure function
   - Enables composition to be viewed as composition of pure functions
   - Ideal for concurrent implementations

3. **Keep Classes Independent**
   - Objects remain independent of their dependencies
   - Raw and reusable potential
   - Changes to one class don't affect others

4. **Separate Computation from Coordination**
   - Computation model - programming to interface, keeping objects independent
   - Coordination model - object composition, instantiating and assembling
   - Don't mix these concerns

5. **Enable Reusability**
   - Low-level objects are building blocks
   - Can be assembled in different combinations
   - Same objects yield different behaviors in different compositions

## PHP Examples

### Basic Composable Structure in PHP

```php
<?php

/**
 * Feature Interface - The contract all implementations follow
 */
interface Feature {
    public function execute(): string;
}

/**
 * Simple Implementation - Does one thing
 */
class SimpleFeature implements Feature {
    private string $value;

    public function __construct(string $value) {
        $this->value = $value;
    }

    public function execute(): string {
        return $this->value;
    }
}

/**
 * Composable Implementation - Delegates to Feature
 * This is self-referential: implements Feature AND delegates to Feature
 */
class ComposableFeature implements Feature {
    private Feature $feature;

    public function __construct(Feature $feature) {
        $this->feature = $feature;
    }

    public function execute(): string {
        // Add behavior before/after delegation
        return "Composed: " . $this->feature->execute();
    }
}

/**
 * Configurer - Assembles the composition
 */
class Configurer {
    public static function createFeature(): Feature {
        // Create composition
        $simple = new SimpleFeature("Hello World");
        $composed = new ComposableFeature($simple);
        $doubleComposed = new ComposableFeature($composed);

        return $doubleComposed;
    }
}

/**
 * Client - Only knows about Feature interface
 */
class Client {
    private Feature $feature;

    public function __construct(Feature $feature) {
        $this->feature = $feature;
    }

    public function doWork(): string {
        // Client has no knowledge of composition
        return $this->feature->execute();
    }
}

// Usage
$feature = Configurer::createFeature();
$client = new Client($feature);
echo $client->doWork();
// Output: Composed: Composed: Hello World
```

### Decorator Pattern in PHP (WordPress Context)

```php
<?php

/**
 * Content Filter Interface
 */
interface ContentFilter {
    public function filter(string $content): string;
}

/**
 * Base Content - No filtering
 */
class BaseContent implements ContentFilter {
    public function filter(string $content): string {
        return $content;
    }
}

/**
 * Sanitize Decorator - Adds sanitization
 */
class SanitizeDecorator implements ContentFilter {
    private ContentFilter $filter;

    public function __construct(ContentFilter $filter) {
        $this->filter = $filter;
    }

    public function filter(string $content): string {
        // Add sanitization behavior
        $content = $this->filter->filter($content);
        return wp_kses_post($content);
    }
}

/**
 * Shortcode Decorator - Processes shortcodes
 */
class ShortcodeDecorator implements ContentFilter {
    private ContentFilter $filter;

    public function __construct(ContentFilter $filter) {
        $this->filter = $filter;
    }

    public function filter(string $content): string {
        // Add shortcode processing behavior
        $content = $this->filter->filter($content);
        return do_shortcode($content);
    }
}

/**
 * Autop Decorator - Adds paragraph tags
 */
class AutopDecorator implements ContentFilter {
    private ContentFilter $filter;

    public function __construct(ContentFilter $filter) {
        $this->filter = $filter;
    }

    public function filter(string $content): string {
        // Add auto-paragraph behavior
        $content = $this->filter->filter($content);
        return wpautop($content);
    }
}

/**
 * WordPress Content Configurer
 */
class ContentFilterConfigurer {
    /**
     * Create standard WordPress content filter chain
     */
    public static function createStandardFilter(): ContentFilter {
        $filter = new BaseContent();
        $filter = new SanitizeDecorator($filter);
        $filter = new ShortcodeDecorator($filter);
        $filter = new AutopDecorator($filter);

        return $filter;
    }

    /**
     * Create minimal filter for admin display
     */
    public static function createAdminFilter(): ContentFilter {
        $filter = new BaseContent();
        $filter = new SanitizeDecorator($filter);

        return $filter;
    }
}

// Usage in WordPress
add_filter('the_content', function($content) {
    $filter = ContentFilterConfigurer::createStandardFilter();
    return $filter->filter($content);
});
```

### Chain of Responsibility in PHP (REST API Validation)

```php
<?php

/**
 * Request Handler Interface
 */
interface RequestHandler {
    public function setNext(RequestHandler $handler): RequestHandler;
    public function handle(WP_REST_Request $request): ?WP_Error;
}

/**
 * Abstract Handler - Base implementation
 */
abstract class AbstractRequestHandler implements RequestHandler {
    private ?RequestHandler $next = null;

    public function setNext(RequestHandler $handler): RequestHandler {
        $this->next = $handler;
        return $handler;
    }

    public function handle(WP_REST_Request $request): ?WP_Error {
        if ($this->next !== null) {
            return $this->next->handle($request);
        }

        return null; // No error, request valid
    }
}

/**
 * Nonce Validation Handler
 */
class NonceValidationHandler extends AbstractRequestHandler {
    public function handle(WP_REST_Request $request): ?WP_Error {
        $nonce = $request->get_header('X-WP-Nonce');

        if (!wp_verify_nonce($nonce, 'wp_rest')) {
            return new WP_Error(
                'invalid_nonce',
                __('Invalid nonce', 'textdomain'),
                array('status' => 403)
            );
        }

        return parent::handle($request);
    }
}

/**
 * Permission Check Handler
 */
class PermissionCheckHandler extends AbstractRequestHandler {
    private string $capability;

    public function __construct(string $capability) {
        $this->capability = $capability;
    }

    public function handle(WP_REST_Request $request): ?WP_Error {
        if (!current_user_can($this->capability)) {
            return new WP_Error(
                'forbidden',
                __('You do not have permission', 'textdomain'),
                array('status' => 403)
            );
        }

        return parent::handle($request);
    }
}

/**
 * Rate Limit Handler
 */
class RateLimitHandler extends AbstractRequestHandler {
    private int $maxRequests;
    private int $timeWindow;

    public function __construct(int $maxRequests, int $timeWindow) {
        $this->maxRequests = $maxRequests;
        $this->timeWindow = $timeWindow;
    }

    public function handle(WP_REST_Request $request): ?WP_Error {
        $user_id = get_current_user_id();
        $transient_key = "rate_limit_{$user_id}";

        $request_count = get_transient($transient_key) ?: 0;

        if ($request_count >= $this->maxRequests) {
            return new WP_Error(
                'rate_limit_exceeded',
                __('Too many requests', 'textdomain'),
                array('status' => 429)
            );
        }

        set_transient($transient_key, $request_count + 1, $this->timeWindow);

        return parent::handle($request);
    }
}

/**
 * Request Validation Configurer
 */
class RequestValidationConfigurer {
    public static function createStandardValidation(): RequestHandler {
        $nonceHandler = new NonceValidationHandler();
        $permissionHandler = new PermissionCheckHandler('edit_posts');
        $rateLimitHandler = new RateLimitHandler(100, HOUR_IN_SECONDS);

        // Build chain
        $nonceHandler
            ->setNext($permissionHandler)
            ->setNext($rateLimitHandler);

        return $nonceHandler;
    }

    public static function createAdminValidation(): RequestHandler {
        $nonceHandler = new NonceValidationHandler();
        $permissionHandler = new PermissionCheckHandler('manage_options');

        // Admin doesn't need rate limiting
        $nonceHandler->setNext($permissionHandler);

        return $nonceHandler;
    }
}

// Usage in REST API endpoint
register_rest_route('myplugin/v1', '/data', array(
    'methods' => 'POST',
    'callback' => 'myplugin_handle_request',
    'permission_callback' => function(WP_REST_Request $request) {
        $validator = RequestValidationConfigurer::createStandardValidation();
        $error = $validator->handle($request);

        return $error === null; // true if valid, false if error
    }
));
```

### Specification Pattern in PHP (Product Filtering)

```php
<?php

/**
 * Specification Interface
 */
interface Specification {
    public function isSatisfiedBy($candidate): bool;
    public function and(Specification $other): Specification;
    public function or(Specification $other): Specification;
    public function not(): Specification;
}

/**
 * Abstract Specification - Base implementation with logical operations
 */
abstract class AbstractSpecification implements Specification {
    abstract public function isSatisfiedBy($candidate): bool;

    public function and(Specification $other): Specification {
        return new AndSpecification($this, $other);
    }

    public function or(Specification $other): Specification {
        return new OrSpecification($this, $other);
    }

    public function not(): Specification {
        return new NotSpecification($this);
    }
}

/**
 * Composite Specifications
 */
class AndSpecification extends AbstractSpecification {
    private Specification $one;
    private Specification $other;

    public function __construct(Specification $one, Specification $other) {
        $this->one = $one;
        $this->other = $other;
    }

    public function isSatisfiedBy($candidate): bool {
        return $this->one->isSatisfiedBy($candidate)
            && $this->other->isSatisfiedBy($candidate);
    }
}

class OrSpecification extends AbstractSpecification {
    private Specification $one;
    private Specification $other;

    public function __construct(Specification $one, Specification $other) {
        $this->one = $one;
        $this->other = $other;
    }

    public function isSatisfiedBy($candidate): bool {
        return $this->one->isSatisfiedBy($candidate)
            || $this->other->isSatisfiedBy($candidate);
    }
}

class NotSpecification extends AbstractSpecification {
    private Specification $specification;

    public function __construct(Specification $specification) {
        $this->specification = $specification;
    }

    public function isSatisfiedBy($candidate): bool {
        return !$this->specification->isSatisfiedBy($candidate);
    }
}

/**
 * Concrete Specifications for WooCommerce Products
 */
class InStockSpecification extends AbstractSpecification {
    public function isSatisfiedBy($product): bool {
        return $product->is_in_stock();
    }
}

class PriceRangeSpecification extends AbstractSpecification {
    private float $min;
    private float $max;

    public function __construct(float $min, float $max) {
        $this->min = $min;
        $this->max = $max;
    }

    public function isSatisfiedBy($product): bool {
        $price = (float) $product->get_price();
        return $price >= $this->min && $price <= $this->max;
    }
}

class CategorySpecification extends AbstractSpecification {
    private array $categories;

    public function __construct(array $categories) {
        $this->categories = $categories;
    }

    public function isSatisfiedBy($product): bool {
        $product_cats = wp_get_post_terms($product->get_id(), 'product_cat', array('fields' => 'slugs'));
        return !empty(array_intersect($this->categories, $product_cats));
    }
}

class OnSaleSpecification extends AbstractSpecification {
    public function isSatisfiedBy($product): bool {
        return $product->is_on_sale();
    }
}

/**
 * Product Repository with Specification filtering
 */
class ProductRepository {
    public function findBySpecification(Specification $specification): array {
        $products = wc_get_products(array('limit' => -1));
        $results = array();

        foreach ($products as $product) {
            if ($specification->isSatisfiedBy($product)) {
                $results[] = $product;
            }
        }

        return $results;
    }
}

// Usage Examples
$repository = new ProductRepository();

// Find in-stock products between $10-$50
$spec = (new InStockSpecification())
    ->and(new PriceRangeSpecification(10.0, 50.0));

$products = $repository->findBySpecification($spec);

// Find products that are (on sale OR in electronics category) AND in stock
$spec = (new OnSaleSpecification())
    ->or(new CategorySpecification(array('electronics')))
    ->and(new InStockSpecification());

$products = $repository->findBySpecification($spec);

// Find products NOT in stock
$spec = (new InStockSpecification())->not();
$products = $repository->findBySpecification($spec);
```

## Key Quotes

> "We can build a complete programming model out of two separate pieces - the **computation model** and the **coordination model**."
> - David Gelernter and Nicholas Carriero, "Coordination Languages and their Significance", Communications of the ACM, 1992

> "Program to an interface, not an implementation"
> - Gang of Four Design Principle

> "Favor object composition over class inheritance"
> - Gang of Four Design Principle

> "Behavior emerges from the collective cohesive objects and not from the implementation within one individual class."
> - Core principle of composable design

> "The same implementation of classes can yield different behavior based upon different compositions of objects instantiated from those classes."
> - Key insight about composition flexibility

> "Different compositions of objects yield different behaviors. Composition isn't about the classes. It's about the composition of the objects instantiated from the classes."
> - Understanding the distinction

> "No individual object is responsible for the entire behavior and yet all of them are responsible for it collectively."
> - Distributed responsibility in composable patterns

> "As the composition management moves away from development and closer to the customer and user, the definition and responsibility of desired behavior moves further from development and closer to the customer and user."
> - The spectrum of composition control

## Further Reading

### Original Sources
- **"Coordination Languages and their Significance"** - David Gelernter and Nicholas Carriero, Communications of the ACM, 1992
- **Design Patterns: Elements of Reusable Object-Oriented Software** - Gang of Four (Gamma, Helm, Johnson, Vlissides)

### Related Articles
- Design Pattern Principles: https://jhumelsine.github.io/2023/09/06/design-pattern-principles.html
- Essential Design Patterns: https://jhumelsine.github.io/2023/09/07/essential-design-patterns.html
- Contracts: https://jhumelsine.github.io/2025/06/10/contracts.html
- Unit Testing: https://jhumelsine.github.io/2024/06/07/unit-test-convert.html
- Test Layers - The Achilles Heel: https://jhumelsine.github.io/2025/06/23/test-layers.html#the-achilles-heel-when-test-layers-arent-enough
- Dependency Injection: https://jhumelsine.github.io/2023/10/09/dependency-injection-design-pattern.html
- Creational Design Patterns: https://jhumelsine.github.io/2025/07/18/creational-design-patterns.html

### Pattern-Specific Articles
- Proxy: https://jhumelsine.github.io/2024/02/01/proxy-design-pattern.html
- Decorator: https://jhumelsine.github.io/2024/02/08/decorator-design-pattern.html
- Chain of Responsibility: https://jhumelsine.github.io/2024/02/20/chain-of-responsibility-design-pattern.html
- Composite: https://jhumelsine.github.io/2024/02/27/composite-design-pattern.html
- Specification: https://jhumelsine.github.io/2024/03/06/specification-design-pattern.html
- Interpreter: https://jhumelsine.github.io/2024/03/12/interpreter-design-pattern-introduction.html
- Builder: https://jhumelsine.github.io/2025/08/08/builder-introduction.html
- Prototype: https://jhumelsine.github.io/2025/12/23/prototype.html

### Video Resources
- **"Old is the New New"** - Kevlin Henney, GOTO 2018: https://www.youtube.com/watch?v=AbgsfeGvg3E&t=2830s

### Podcast Resources
- **"No-code platforms and the art of the possible"** - Thoughtworks Pragmatism in Practice Podcast with Gary Hoberman (CEO of Unqork), discussing insurance industry applications starting at minute 25:00

### Additional Resources
- **Composable Design Patterns AI Notebook**: https://notebooklm.google.com/notebook/bc19b872-144e-4218-8622-023e963becdf
- **Minecraft Redstone and Computing**: https://en.wikipedia.org/wiki/Minecraft
- **Logic Gates**: https://www.wikiwand.com/en/Logic_gate
- **Liskov Substitution Principle**: https://en.wikipedia.org/wiki/Liskov_substitution_principle
- **GDPR**: https://en.wikipedia.org/wiki/General_Data_Protection_Regulation

---

**License:** This reference is derived from "Composable Design Patterns – Basic Concepts" by James Humelsine, available at https://jhumelsine.github.io/
