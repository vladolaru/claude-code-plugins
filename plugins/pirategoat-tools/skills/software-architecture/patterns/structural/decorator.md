# Decorator Design Pattern

## Intent

Layer additional behaviors upon core features dynamically through object composition, providing a flexible alternative to inheritance for extending functionality.

## Metaphor: Mr. Potato Head

Mr. Potato Head perfectly demonstrates the Decorator pattern:
- **Core Feature**: The plain potato (plastic or real)
- **Decorators**: Caricature appendages (eyes, mouth, nose, ears, feet, hands, hats)
- **Flexibility**: Mix and match appendages to create different expressions
- **Dynamic**: Add or remove features at will
- **Creativity**: Put noses in ear holes, hands in hat holes - combine in unexpected ways

The plain potato alone isn't much fun. The magic happens when you decorate it with various appendages, creating unlimited combinations of Mr. Potato Head personalities.

## Problem

Inheritance has significant limitations when extending functionality:

| Limitation | Description | Impact |
|------------|-------------|--------|
| **Tight coupling** | Child classes depend on and have knowledge of parent classes | Changes in parent break children |
| **Static behavior** | Behavior fixed at compile time, cannot change at runtime | No dynamic configuration |
| **Single inheritance** | Java/PHP allow only one parent class | Cannot mix multiple behaviors easily |
| **Multiple inheritance issues** | C++ allows it but creates diamond problem | Complex to manage |
| **Explosion of classes** | Need a class for every combination | Exponential growth |

**Example:** Coffee shop offers 4 coffee types and 6 condiments. That's 24 combinations if using inheritance per combination - and grows exponentially with each new option.

## Solution

Decorator addresses inheritance limitations through delegation:

| Inheritance Approach | Decorator Approach |
|---------------------|-------------------|
| Static delegation up ancestor classes | Dynamic delegation through linked list |
| Behavior locked at compile time | Behavior configured at runtime |
| Single/problematic multiple inheritance | Unlimited combination of decorators |
| Child depends on parent | Concrete classes independent |
| Fixed behavior tree | Mix and match behaviors |

**Key concept:** Instead of one object calling another's different method, Decorator chains objects that all call the **same method** down the chain.

```
Decorator A → Decorator B → Decorator C → Core Feature
   ↓              ↓              ↓              ↓
execute()      execute()      execute()      execute()
```

## Real-World Examples

### Food Orders
- **Ice cream sundae**: Vanilla ice cream (core) + toppings (decorators)
- **Pizza**: Crust + sauce + cheese (core) + pepperoni, mushrooms, olives, pineapple (decorators)

### Visual Layering
- **Multiplane Camera**: Background image (core) + cel layers (decorators) - creates 3D animation effect
- **Photoshop Layers**: Background (core) + stacked image layers (decorators)
- **SLR Camera**: Camera body (core) + lenses + filters (decorators)

### Key Pattern
In each example:
1. Core feature provides basic functionality
2. Decorators add optional enhancements
3. Decorators can be mixed and matched
4. Order may matter (camera body MUST have lens; ice cream before toppings)

## Structure

### Evolution from Proxy to Decorator

Proxy pattern with `Feature` interface delegation is actually Decorator with one Proxy instance. The difference:
- **Proxy**: Single proxy delegates to core feature
- **Decorator**: Multiple decorators chain delegation

### GoF Decorator Structure

```
┌─────────────────┐
│   <<interface>> │
│     Feature     │
├─────────────────┤
│ + execute()     │
└─────────────────┘
         △
         │ implements
    ┌────┴────────────────┐
    │                     │
┌───┴────────┐    ┌──────┴─────────┐
│CoreFeature │    │   <<abstract>> │
├────────────┤    │    Decorator   │
│+ execute() │    ├────────────────┤
└────────────┘    │- feature       │
                  │+ execute()     │
                  └────────────────┘
                          △
                          │ extends
                ┌─────────┴─────────┐
                │                   │
        ┌───────┴────────┐  ┌──────┴────────┐
        │  DecoratorA    │  │  DecoratorB   │
        ├────────────────┤  ├───────────────┤
        │+ execute()     │  │+ execute()    │
        └────────────────┘  └───────────────┘
```

**Key elements:**
- `Feature` interface defines contract
- `CoreFeature` implements base functionality
- Abstract `Decorator` holds reference to `Feature` and delegates
- Concrete decorators extend `Decorator` and add behavior

**Delegation chain:**
```
DecoratorA.execute() {
    // Pre-processing
    feature.execute()  // Could be DecoratorB or CoreFeature
    // Post-processing
}
```

### Issues with Basic GoF Decorator

**Problem:** Developers must remember to call `super.execute()` in concrete decorators. No compile-time enforcement.

```php
// Easy to forget or misplace
class ConcreteDecorator extends Decorator {
    public function execute() {
        // Oops, forgot to call parent!
        $this->doMyStuff();
    }
}
```

### Improved: Decorator with Template Method

Combine Decorator with Template Method pattern to **force** correct delegation:

```
┌────────────────────────────┐
│     <<abstract>>           │
│       Decorator            │
├────────────────────────────┤
│ - feature: Feature         │
│ + execute() [final]        │
│ # preExecute() [abstract]  │
│ # postExecute() [abstract] │
└────────────────────────────┘
          △
          │ extends
┌─────────┴─────────┐
│                   │
┌────────────────┐  ┌────────────────┐
│  DecoratorA    │  │  DecoratorB    │
├────────────────┤  ├────────────────┤
│# preExecute()  │  │# preExecute()  │
│# postExecute() │  │# postExecute() │
└────────────────┘  └────────────────┘
```

**Template Method in abstract Decorator:**
```php
abstract class Decorator implements Feature {
    private $feature;

    public function __construct(Feature $feature) {
        $this->feature = $feature;
    }

    // FINAL - cannot be overridden
    final public function execute() {
        $this->preExecute();
        $this->feature->execute();
        $this->postExecute();
    }

    // Concrete decorators MUST implement these
    abstract protected function preExecute();
    abstract protected function postExecute();
}
```

**Benefits:**
1. **Enforced delegation** - `execute()` is `final`, always calls delegate
2. **Clear extension points** - Only override `preExecute()` and `postExecute()`
3. **Cannot break chain** - Even accidentally
4. **Empty implementations allowed** - If no pre/post behavior needed

## Implementation

### Basic Example: Starbuzz Coffee Labels

**Scenario:** Coffee shop needs to print drink order labels with various flavor combinations.

#### Interface and Core Features

```php
/**
 * DrinkOrder feature interface
 */
interface DrinkOrder {
    public function getLabel(): string;
}

/**
 * Core Feature: Coffee
 */
class Coffee implements DrinkOrder {
    public function getLabel(): string {
        return "Coffee";
    }
}

/**
 * Core Feature: Tea
 */
class Tea implements DrinkOrder {
    public function getLabel(): string {
        return "Tea";
    }
}
```

#### Abstract Decorator with Template Method

```php
/**
 * Abstract Decorator using Template Method
 * Ensures delegation always happens correctly
 */
abstract class Flavor implements DrinkOrder {
    private $drinkOrder;

    public function __construct(DrinkOrder $drinkOrder) {
        $this->drinkOrder = $drinkOrder;
    }

    /**
     * FINAL - cannot be overridden
     * Template Method that enforces delegation
     */
    final public function getLabel(): string {
        // Get delegate's label
        $label = $this->drinkOrder->getLabel();

        // Add separator
        $label .= ", ";

        // Add our flavor
        $label .= $this->getFlavorLabel();

        return $label;
    }

    /**
     * Concrete decorators implement this
     */
    abstract protected function getFlavorLabel(): string;
}
```

#### Concrete Decorators

```php
class Sugar extends Flavor {
    protected function getFlavorLabel(): string {
        return "Sugar";
    }
}

class Milk extends Flavor {
    protected function getFlavorLabel(): string {
        return "Milk";
    }
}

class Lemon extends Flavor {
    protected function getFlavorLabel(): string {
        return "Lemon";
    }
}

class PumpkinSpice extends Flavor {
    protected function getFlavorLabel(): string {
        return "Pumpkin Spice";
    }
}
```

#### Builder (Configurer)

```php
/**
 * DrinkOrderBuilder - constructs decorated drinks
 * More than a factory - builds custom orders
 */
class DrinkOrderBuilder {
    /**
     * Build drink order from ingredient list
     *
     * @param string $ingredients Space-separated: "Coffee Sugar Sugar Milk"
     * @return DrinkOrder
     */
    public function buildDrinkOrder(string $ingredients): DrinkOrder {
        $parts = explode(' ', trim($ingredients));

        // IMPORTANT: Iterate backwards to build chain correctly
        // "Coffee Sugar Milk" should be: Milk -> Sugar -> Coffee
        $drinkOrder = null;

        for ($i = count($parts) - 1; $i >= 0; $i--) {
            $ingredient = $parts[$i];
            $drinkOrder = $this->acquire($ingredient, $drinkOrder);
        }

        return $drinkOrder;
    }

    /**
     * Acquire single ingredient
     */
    private function acquire(string $ingredient, ?DrinkOrder $drinkOrder): DrinkOrder {
        switch($ingredient) {
            // Core features - no delegate needed
            case 'Coffee':
                return new Coffee();
            case 'Tea':
                return new Tea();

            // Decorators - require delegate
            case 'Sugar':
                if ($drinkOrder === null) {
                    throw new InvalidArgumentException("Sugar requires a base drink");
                }
                return new Sugar($drinkOrder);

            case 'Milk':
                if ($drinkOrder === null) {
                    throw new InvalidArgumentException("Milk requires a base drink");
                }
                return new Milk($drinkOrder);

            case 'Lemon':
                if ($drinkOrder === null) {
                    throw new InvalidArgumentException("Lemon requires a base drink");
                }
                return new Lemon($drinkOrder);

            case 'PumpkinSpice':
                if ($drinkOrder === null) {
                    throw new InvalidArgumentException("Pumpkin Spice requires a base drink");
                }
                return new PumpkinSpice($drinkOrder);

            default:
                throw new InvalidArgumentException("Unknown ingredient: {$ingredient}");
        }
    }
}
```

#### Label Printer (Client)

```php
/**
 * LabelPrinter - prints drink order labels
 */
class LabelPrinter {
    private $drinkOrderBuilder;

    public function __construct(DrinkOrderBuilder $builder) {
        $this->drinkOrderBuilder = $builder;
    }

    /**
     * Print label for ingredient list
     */
    public function printLabel(string $ingredients): void {
        $drinkOrder = $this->drinkOrderBuilder->buildDrinkOrder($ingredients);
        echo $drinkOrder->getLabel() . "\n";
    }
}
```

#### Usage Examples

```php
$builder = new DrinkOrderBuilder();
$printer = new LabelPrinter($builder);

// Simple coffee
$printer->printLabel("Coffee");
// Output: Coffee

// Coffee with sugar
$printer->printLabel("Coffee Sugar");
// Output: Coffee, Sugar

// Coffee with double sugar and milk
$printer->printLabel("Coffee Sugar Sugar Milk");
// Output: Coffee, Sugar, Sugar, Milk

// Tea with lemon
$printer->printLabel("Tea Lemon");
// Output: Tea, Lemon

// Seasonal drink
$printer->printLabel("Coffee Milk PumpkinSpice");
// Output: Coffee, Milk, Pumpkin Spice
```

#### Object Chain Visualization

For "Coffee Sugar Sugar Milk":

```
┌──────────┐     ┌──────────┐     ┌──────────┐     ┌──────────┐
│   Milk   │────▶│  Sugar   │────▶│  Sugar   │────▶│  Coffee  │
└──────────┘     └──────────┘     └──────────┘     └──────────┘
     │                │                │                │
 getLabel()       getLabel()       getLabel()       getLabel()
     │                │                │                │
     └────────────────┴────────────────┴────────────▶ "Coffee, Sugar, Sugar, Milk"
```

**Call flow:**
1. `Milk.getLabel()` → calls `Sugar.getLabel()` + adds ", Milk"
2. `Sugar.getLabel()` → calls `Sugar.getLabel()` + adds ", Sugar"
3. `Sugar.getLabel()` → calls `Coffee.getLabel()` + adds ", Sugar"
4. `Coffee.getLabel()` → returns "Coffee"
5. Unwinds: "Coffee" → "Coffee, Sugar" → "Coffee, Sugar, Sugar" → "Coffee, Sugar, Sugar, Milk"

### Advanced Example: Enhanced Features

Extend the pattern to support cost calculation and calorie counting:

```php
interface DrinkOrder {
    public function getLabel(): string;
    public function getCost(): float;
    public function getCalories(): int;
}

class Coffee implements DrinkOrder {
    public function getLabel(): string {
        return "Coffee";
    }

    public function getCost(): float {
        return 2.00;
    }

    public function getCalories(): int {
        return 5;
    }
}

abstract class Flavor implements DrinkOrder {
    private $drinkOrder;

    public function __construct(DrinkOrder $drinkOrder) {
        $this->drinkOrder = $drinkOrder;
    }

    final public function getLabel(): string {
        return $this->drinkOrder->getLabel() . ", " . $this->getFlavorLabel();
    }

    final public function getCost(): float {
        return $this->drinkOrder->getCost() + $this->getFlavorCost();
    }

    final public function getCalories(): int {
        return $this->drinkOrder->getCalories() + $this->getFlavorCalories();
    }

    abstract protected function getFlavorLabel(): string;
    abstract protected function getFlavorCost(): float;
    abstract protected function getFlavorCalories(): int;
}

class Sugar extends Flavor {
    protected function getFlavorLabel(): string {
        return "Sugar";
    }

    protected function getFlavorCost(): float {
        return 0.10;
    }

    protected function getFlavorCalories(): int {
        return 16;
    }
}

class Milk extends Flavor {
    protected function getFlavorLabel(): string {
        return "Milk";
    }

    protected function getFlavorCost(): float {
        return 0.50;
    }

    protected function getFlavorCalories(): int {
        return 40;
    }
}

// Usage
$drink = new Milk(new Sugar(new Sugar(new Coffee())));
echo $drink->getLabel();      // Coffee, Sugar, Sugar, Milk
echo $drink->getCost();       // 2.70 (2.00 + 0.10 + 0.10 + 0.50)
echo $drink->getCalories();   // 77 (5 + 16 + 16 + 40)
```

### WordPress Example: Content Filters

WordPress hooks system is essentially a Decorator implementation:

```php
/**
 * Content rendering with decorators
 */
interface ContentRenderer {
    public function render(string $content): string;
}

/**
 * Core feature - basic rendering
 */
class BasicContentRenderer implements ContentRenderer {
    public function render(string $content): string {
        return $content;
    }
}

/**
 * Abstract decorator for content filters
 */
abstract class ContentFilter implements ContentRenderer {
    private $renderer;

    public function __construct(ContentRenderer $renderer) {
        $this->renderer = $renderer;
    }

    final public function render(string $content): string {
        $content = $this->preFilter($content);
        $content = $this->renderer->render($content);
        $content = $this->postFilter($content);
        return $content;
    }

    protected function preFilter(string $content): string {
        return $content; // Override if needed
    }

    protected function postFilter(string $content): string {
        return $content; // Override if needed
    }
}

/**
 * Concrete decorators
 */
class ShortcodeFilter extends ContentFilter {
    protected function postFilter(string $content): string {
        return do_shortcode($content);
    }
}

class AutoParagraphFilter extends ContentFilter {
    protected function postFilter(string $content): string {
        return wpautop($content);
    }
}

class EmojiFilter extends ContentFilter {
    protected function postFilter(string $content): string {
        return wp_staticize_emoji($content);
    }
}

// Usage
$renderer = new BasicContentRenderer();
$renderer = new ShortcodeFilter($renderer);
$renderer = new AutoParagraphFilter($renderer);
$renderer = new EmojiFilter($renderer);

$output = $renderer->render($post_content);
```

## When to Use

### Use Decorator When

| Scenario | Why Decorator Fits |
|----------|-------------------|
| **Dynamic behavior addition** | Need to add/remove responsibilities at runtime |
| **Multiple optional features** | Customer/user selects from many combinations |
| **Inheritance explosion** | Too many subclasses for all combinations |
| **Transparent wrapping** | Decorators should be invisible to clients |
| **Single Responsibility** | Each decorator adds one specific feature |
| **Mix and match** | Features can be combined in any order |

**Examples:**
- UI components with borders, scrollbars, shadows (Swing/AWT)
- I/O streams with buffering, compression, encryption
- Text processing with formatting, validation, sanitization
- HTTP request/response middleware
- Logging with timestamps, severity levels, formatting

### Don't Use Decorator When

| Scenario | Why Decorator Fails | Use Instead |
|----------|-------------------|-------------|
| **Need new methods** | Decorator can't add new interface methods | Inheritance or Adapter |
| **Only one option** | No combination needed | Simple subclass |
| **Order-dependent with strict rules** | Complex ordering constraints | Chain of Responsibility |
| **Decorators need coordination** | Features interact with each other | Mediator or Strategy |
| **Deep introspection needed** | Can't easily identify decorator types | Direct composition |

**Anti-patterns:**
- Adding methods not in interface (breaks Liskov Substitution)
- Decorators depending on each other (tight coupling)
- Too many tiny decorators (premature abstraction)
- Using Decorator when simple boolean flags suffice

## Pros and Cons

### Pros

| Benefit | Description | Example |
|---------|-------------|---------|
| **Flexible behavior** | Add/remove responsibilities at runtime | Turn off seasonal flavors |
| **Single Responsibility** | Each decorator has one clear purpose | Sugar only adds sugar |
| **Open/Closed** | Extend without modifying existing classes | Add new flavor without changing existing |
| **Composable** | Mix and match decorators freely | Any combination of toppings |
| **Testable** | Each decorator tested independently | Unit test just Sugar decorator |
| **No class explosion** | Avoid subclass for every combination | 4 coffees × 6 condiments = 10 classes, not 24 |
| **Transparent** | Clients use decorated objects like originals | Same interface as core feature |
| **Easy to add** | New decorator = one new class | Add Honey class, done |
| **Easy to remove** | Delete deprecated decorator class | Remove PumpkinSpice after season |

**Key advantage:** Behavior configured at runtime through composition, not locked at compile time through inheritance.

### Cons

| Challenge | Description | Mitigation |
|-----------|-------------|-----------|
| **Cannot add new methods** | Limited to interface methods | Use Adapter or extend interface |
| **Difficult to diagnose** | Behavior distributed across many objects | Good logging, clear naming |
| **Testing complexity** | Need integration tests for combinations | Test elements thoroughly + guardrails |
| **Ordering matters** | Some combinations make sense, others don't | Validate in builder |
| **Identity problems** | Hard to identify specific decorator in chain | Avoid relying on type checks |
| **Lots of small classes** | Many decorator classes | Accept it - better than inheritance |
| **Configuration responsibility** | Builder must compose correctly | Good builder tests |

**Key challenge:** Can't test all combinations (infinite possibilities). Solution: Test individual decorators thoroughly + validate sensible compositions in builder.

## Comparison with Related Patterns

### Decorator vs Proxy

| Aspect | Decorator | Proxy |
|--------|-----------|-------|
| **Intent** | Add functionality | Control access |
| **Number of wrappers** | Multiple in chain | Typically one |
| **Awareness** | Client knows it's decorating | Client thinks it's the real object |
| **Purpose** | Enhance behavior | Manage lifecycle/access |
| **Runtime composition** | Yes - dynamic chains | Usually static |

**Relationship:** Proxy is Decorator with one instance. Decorator generalizes Proxy to multiple wrappers.

### Decorator vs Strategy

| Aspect | Decorator | Strategy |
|--------|-----------|---------|
| **Changes** | Object's skin (what it does) | Object's guts (how it does it) |
| **Addition** | Wraps object externally | Injected into object |
| **Composition** | Multiple decorators chain | Single strategy at a time |
| **Interface** | Same as wrapped object | Different (algorithm) interface |
| **Visibility** | Transparent to client | Client may configure |

### Decorator vs Adapter

| Aspect | Decorator | Adapter |
|--------|-----------|---------|
| **Purpose** | Add responsibilities | Change interface |
| **Interface** | Keeps same interface | Converts to different interface |
| **Enhancement** | Enhances behavior | Enables compatibility |
| **Chaining** | Multiple decorators | Typically single adapter |

### Decorator vs Composite

| Aspect | Decorator | Composite |
|--------|-----------|---------|
| **Structure** | Linear chain | Tree structure |
| **Purpose** | Add behavior | Treat one/many uniformly |
| **Focus** | Enhancement | Composition |
| **Leaf nodes** | Single core feature | Multiple leaves |
| **Common interface** | Yes | Yes |

**Note:** Both use recursive composition, but different structures and purposes.

## Design Considerations

### Decorator Order

Order matters when decorators have dependencies:

```php
// Wrong - might break encryption
$stream = new EncryptionDecorator(
    new CompressionDecorator($baseStream)
);
// Compresses, then encrypts compressed data

// Right - better compression of encrypted data
$stream = new CompressionDecorator(
    new EncryptionDecorator($baseStream)
);
// Encrypts, then compresses encrypted data
```

**Solution:** Document order requirements or enforce in builder.

### Empty Decorator Methods

Template Method version requires implementing all hooks:

```php
class SimpleDecorator extends Decorator {
    protected function preExecute() {
        // Empty - no pre-processing needed
    }

    protected function postExecute() {
        $this->doMyWork();
    }
}
```

**Alternative:** Use default empty implementations in abstract class:

```php
abstract class Decorator implements Feature {
    // ... same as before

    protected function preExecute() {
        // Default: do nothing
    }

    protected function postExecute() {
        // Default: do nothing
    }
}
```

Then concrete decorators only override what they need:

```php
class SimpleDecorator extends Decorator {
    protected function postExecute() {
        $this->doMyWork();
    }
    // No need to override preExecute()
}
```

### Validation in Builder

Protect against invalid combinations:

```php
class DrinkOrderBuilder {
    private function acquire(string $ingredient, ?DrinkOrder $drinkOrder): DrinkOrder {
        switch($ingredient) {
            case 'Lemon':
                // Prevent lemon in coffee
                if ($this->isCoffee($drinkOrder)) {
                    throw new InvalidArgumentException("Cannot add lemon to coffee");
                }
                return new Lemon($drinkOrder);
            // ... other cases
        }
    }

    private function isCoffee(?DrinkOrder $order): bool {
        while ($order !== null) {
            if ($order instanceof Coffee) {
                return true;
            }
            if ($order instanceof Flavor) {
                // Access protected property via reflection or add getter
                $order = $this->getWrappedOrder($order);
            } else {
                break;
            }
        }
        return false;
    }
}
```

### Testing Strategy

**Unit tests:** Test each class individually

```php
class SugarTest extends TestCase {
    public function test_sugar_adds_label() {
        $coffee = $this->createMock(DrinkOrder::class);
        $coffee->method('getLabel')->willReturn('Coffee');

        $sugar = new Sugar($coffee);

        $this->assertEquals('Coffee, Sugar', $sugar->getLabel());
    }
}
```

**Integration tests:** Test common combinations

```php
class DrinkOrderIntegrationTest extends TestCase {
    public function test_coffee_with_milk_and_sugar() {
        $builder = new DrinkOrderBuilder();
        $order = $builder->buildDrinkOrder('Coffee Sugar Milk');

        $this->assertEquals('Coffee, Sugar, Milk', $order->getLabel());
        $this->assertEquals(2.60, $order->getCost());
    }
}
```

**Don't:** Try to test all possible combinations (infinite).

**Do:** Test individual elements thoroughly + validate builder composition logic.

## Real-World Applications

### Java I/O Streams

Classic Decorator example:

```java
// Core feature
InputStream fileStream = new FileInputStream("data.txt");

// Decorated with buffering
InputStream bufferedStream = new BufferedInputStream(fileStream);

// Decorated with gzip decompression
InputStream gzipStream = new GZIPInputStream(bufferedStream);

// All have same InputStream interface
```

### Web Middleware

HTTP request processing:

```php
// Request/Response interface
interface Middleware {
    public function handle(Request $request, callable $next): Response;
}

// Core handler
class CoreHandler implements Middleware {
    public function handle(Request $request, callable $next): Response {
        return new Response($this->processRequest($request));
    }
}

// Decorators
class AuthenticationMiddleware implements Middleware {
    public function handle(Request $request, callable $next): Response {
        if (!$this->authenticate($request)) {
            return new Response('Unauthorized', 401);
        }
        return $next($request);
    }
}

class LoggingMiddleware implements Middleware {
    public function handle(Request $request, callable $next): Response {
        $this->logRequest($request);
        $response = $next($request);
        $this->logResponse($response);
        return $response;
    }
}

// Chain them
$handler = new LoggingMiddleware(
    new AuthenticationMiddleware(
        new CoreHandler()
    )
);
```

### UI Components

Swing/AWT components:

```java
// Core component
JTextArea textArea = new JTextArea();

// Add scroll bars
JScrollPane scrollPane = new JScrollPane(textArea);

// Add border
JPanel panel = new JPanel();
panel.setBorder(BorderFactory.createLineBorder(Color.BLACK));
panel.add(scrollPane);
```

### WordPress Content Filters

```php
// WordPress applies decorators via hooks
apply_filters('the_content', $content);

// Internally chains filters:
// $content -> wpautop() -> do_shortcode() -> wp_make_content_images_responsive()
```

## Summary

Decorator provides a flexible alternative to inheritance for extending functionality:

**Core principles:**
1. **Composition over inheritance** - Build features by wrapping, not extending
2. **Dynamic behavior** - Configure at runtime, not compile time
3. **Single Responsibility** - Each decorator adds one feature
4. **Open/Closed** - Add features without modifying existing code
5. **Transparent** - Same interface as wrapped object

**When to use:**
- Multiple optional features that can be combined
- Dynamic addition/removal of responsibilities
- Avoiding inheritance explosion

**Key trade-off:**
- **Flexibility** (add/remove features dynamically)
- vs **Complexity** (distributed behavior, integration testing)

**Remember:** Decorator is about layering behaviors on existing functionality within the same interface. It cannot add new methods, only enhance existing ones.

**Best with Template Method:** Combining Decorator with Template Method prevents developers from breaking the delegation chain by forcing correct execution order.

## References

### Online Resources
- [Wikipedia Decorator Pattern](https://en.wikipedia.org/wiki/Decorator_pattern)
- [Refactoring Guru Decorator](https://refactoring.guru/design-patterns/decorator)
- [Source Making Decorator](https://sourcemaking.com/design_patterns/decorator)
- [Baeldung Decorator Pattern](https://www.baeldung.com/java-decorator-pattern)

### Books
- **Gang of Four** - Design Patterns, page 175
- **Head First Design Patterns** - Chapter 3 (Starbuzz Coffee example)
- **Agile Principles, Patterns, and Practices in C#** - Chapter 35

### Related Patterns
- **Proxy** - Decorator with one wrapper, controls access
- **Strategy** - Changes behavior algorithm, not wrapper
- **Adapter** - Changes interface, not behavior
- **Composite** - Tree structure vs linear chain
- **Template Method** - Enforces delegation order
- **Chain of Responsibility** - Similar structure, different intent

### Source Blog
- [Decorator Design Pattern](https://jhumelsine.github.io/2024/02/08/decorator-design-pattern.html) by James Humelsine
