# Specification Pattern

## Intent

Allow clients to select or filter objects with specific attribute property values through dynamically composable, client-defined specifications. Encapsulate business rules into reusable, combinable query objects that can be evaluated against domain entities.

## Problem

You need to:
- Select objects from a repository based on complex, client-defined criteria
- Filter objects in a collection, iterator, or stream dynamically
- Allow clients to build custom queries without modifying core logic
- Define alert criteria or notification subscriptions based on attribute thresholds
- Construct complex boolean logic (AND, OR, NOT) from simple attribute checks
- Reuse and combine business rules across different contexts

**Symptoms this pattern addresses:**
- Hardcoded query logic scattered throughout the codebase
- New filtering requirements require code changes
- Complex SQL-like queries needed but operating on objects, not database records
- Clients need to define their own selection criteria at runtime
- Business rules need to be composed dynamically

## Solution Structure

The Specification pattern extends Composite with Strategy at its core. It provides a tree structure for building complex boolean expressions from simple attribute specifications.

### Core Components

| Component | Role | Responsibility |
|-----------|------|----------------|
| **Specification (Interface)** | Contract | `isSatisfied(Context): boolean` - evaluates whether context matches criteria |
| **Leaf Specifications** | Attribute checks | Test single attribute values (ColorSpec, ShapeSpec, etc.) |
| **Composite Specifications** | Boolean operators | Combine specifications (AndSpec, OrSpec, NotSpec) |
| **Context** | Domain object | Object being evaluated against specification |
| **ContextManager** | Repository/Collection | Manages contexts, filters by specification |
| **Client** | Specification builder | Creates and composes specifications, requests filtered results |

### Class Structure

```
┌─────────────────────────┐
│   <<interface>>         │
│    Specification        │
├─────────────────────────┤
│ + isSatisfied(Context)  │
│   : boolean             │
└───────────▲─────────────┘
            │
            │ implements
     ┌──────┴──────┬──────────────┬─────────────────┐
     │             │              │                 │
┌────┴─────┐  ┌───┴──────┐  ┌────┴────────┐  ┌────┴────────────┐
│  Color   │  │  Shape   │  │   And       │  │    Or           │
│   Spec   │  │   Spec   │  │   Spec      │  │    Spec         │
├──────────┤  ├──────────┤  ├─────────────┤  ├─────────────────┤
│ -color   │  │ -shape   │  │ -specs[]    │  │  -specs[]       │
│          │  │          │  │             │  │                 │
│ +isSatis │  │ +isSatis │  │ +isSatis    │  │  +isSatis       │
│  fied()  │  │  fied()  │  │  fied()     │  │   fied()        │
│          │  │          │  │ +add(Spec)  │  │  +add(Spec)     │
└──────────┘  └──────────┘  └─────────────┘  └─────────────────┘

     ┌─────────────────────┐
     │     Not Spec        │
     ├─────────────────────┤
     │ -spec: Specification│
     │                     │
     │ +isSatisfied()      │
     └─────────────────────┘
```

### Key Mechanisms

**1. Strategy Foundation**
- `Specification` is an interface with one method: `isSatisfied(Context)`
- Leaf specifications implement simple attribute checks
- ContextManager depends only on the interface, not implementations

**2. Composite Boolean Operators**
- `AndSpecification` - Returns true when ALL contained specs return true
- `OrSpecification` - Returns true when ANY contained spec returns true
- `NotSpecification` - Inverts the result of its single contained spec

**3. Client-Defined Composition**
- Unlike most patterns, clients construct the specification tree themselves
- Clients inject specifications into ContextManager at runtime
- This is Dependency Injection, but inverted: client creates, manager consumes

**4. Immutability & Thread Safety**
- Leaf specifications use `final` attributes (Value Objects)
- Composite specs use 2-state machine: Initializing → Not Initializing
- Once activated (first `isSatisfied()` call), no more specs can be added
- Entire tree becomes immutable, making it thread-safe

## Implementation

### Basic Specification Interface

```php
<?php
/**
 * Specification interface - evaluates whether a context satisfies criteria
 */
interface Specification {
    /**
     * Check if context satisfies this specification
     *
     * @param Context $context The object to evaluate
     * @return bool True if context satisfies specification
     */
    public function isSatisfied(Context $context): bool;
}
```

### Leaf Specifications (Attribute Checks)

```php
<?php
/**
 * Color specification - checks if context has specified color
 */
class ColorSpecification implements Specification {
    private string $color;

    public function __construct(string $color) {
        $this->color = $color;
    }

    public function isSatisfied(Context $context): bool {
        return $context->getColor() === $this->color;
    }
}

/**
 * Shape specification - checks if context has specified shape
 */
class ShapeSpecification implements Specification {
    private string $shape;

    public function __construct(string $shape) {
        $this->shape = $shape;
    }

    public function isSatisfied(Context $context): bool {
        return $context->getShape() === $this->shape;
    }
}

/**
 * Rating specification - checks if context has specified rating
 */
class RatingSpecification implements Specification {
    private int $rating;

    public function __construct(int $rating) {
        $this->rating = $rating;
    }

    public function isSatisfied(Context $context): bool {
        return $context->getRating() === $this->rating;
    }
}
```

### Composite Specifications (Boolean Operators)

```php
<?php
/**
 * Abstract base for composite specifications with state management
 */
abstract class SpecificationComposite implements Specification {
    /** @var Specification[] */
    protected array $specifications = [];
    private bool $isInitializing = true;

    /**
     * Add a specification to this composite
     *
     * @param Specification $spec The specification to add
     * @throws InvalidStateException If called after activation
     */
    public function add(Specification $spec): void {
        if (!$this->isInitializing) {
            throw new InvalidStateException(
                'Cannot add specifications after activation'
            );
        }
        $this->specifications[] = $spec;
    }

    /**
     * Template method - activates composite on first call
     */
    public function isSatisfied(Context $context): bool {
        $this->isInitializing = false; // Transition to immutable state
        return $this->evaluateSpecifications($context);
    }

    /**
     * Subclasses implement specific boolean logic
     */
    abstract protected function evaluateSpecifications(Context $context): bool;
}

/**
 * AND specification - all contained specs must be satisfied
 */
class AndSpecification extends SpecificationComposite {
    protected function evaluateSpecifications(Context $context): bool {
        foreach ($this->specifications as $spec) {
            if (!$spec->isSatisfied($context)) {
                return false; // Short-circuit on first failure
            }
        }
        return true;
    }
}

/**
 * OR specification - at least one contained spec must be satisfied
 */
class OrSpecification extends SpecificationComposite {
    protected function evaluateSpecifications(Context $context): bool {
        foreach ($this->specifications as $spec) {
            if ($spec->isSatisfied($context)) {
                return true; // Short-circuit on first success
            }
        }
        return false;
    }
}

/**
 * NOT specification - inverts contained specification result
 */
class NotSpecification implements Specification {
    private Specification $specification;

    public function __construct(Specification $specification) {
        $this->specification = $specification;
    }

    public function isSatisfied(Context $context): bool {
        return !$this->specification->isSatisfied($context);
    }
}
```

### Context Manager (Repository/Filter)

```php
<?php
/**
 * Manages contexts and filters by specification
 */
class ContextManager {
    /** @var Context[] */
    private array $contexts = [];

    public function addContext(Context $context): void {
        $this->contexts[] = $context;
    }

    /**
     * Get all contexts satisfying the specification
     *
     * @param Specification $specification Filter criteria
     * @return Context[] Matching contexts
     */
    public function getContextsBy(Specification $specification): array {
        $satisfiedContexts = [];

        foreach ($this->contexts as $context) {
            if ($specification->isSatisfied($context)) {
                $satisfiedContexts[] = $context;
            }
        }

        return $satisfiedContexts;
    }
}
```

### Client Usage Examples

```php
<?php
// Simple specifications
$redContexts = $contextManager->getContextsBy(
    new ColorSpecification('RED')
);

$circleContexts = $contextManager->getContextsBy(
    new ShapeSpecification('CIRCLE')
);

// Composite specification: BLUE AND SQUARE
$blueSquareSpec = new AndSpecification();
$blueSquareSpec->add(new ColorSpecification('BLUE'));
$blueSquareSpec->add(new ShapeSpecification('SQUARE'));
$blueSquareContexts = $contextManager->getContextsBy($blueSquareSpec);

// Complex specification: (SLENDER AND PALE AND SCHOLAR AND RICH) OR HANDSOME
// From "Matchmaker, Matchmaker" example
$idealHusbandSpec = new AndSpecification();
$idealHusbandSpec->add(new BuildSpecification('SLENDER'));
$idealHusbandSpec->add(new ComplexionSpecification('PALE'));
$idealHusbandSpec->add(new EducationSpecification('SCHOLAR'));
$idealHusbandSpec->add(new WealthSpecification('RICH'));

$husbandSpec = new OrSpecification();
$husbandSpec->add($idealHusbandSpec);
$husbandSpec->add(new LooksSpecification('HANDSOME'));

$husbandCandidates = $matchmaker->getContextsBy($husbandSpec);

// Optimized version: OR at top for short-circuit efficiency
// If HANDSOME, skip expensive checks
$optimizedSpec = new OrSpecification();
$optimizedSpec->add(new LooksSpecification('HANDSOME')); // Check this first

$attributesSpec = new AndSpecification();
$attributesSpec->add(new WealthSpecification('RICH')); // Expensive check first
$attributesSpec->add(new EducationSpecification('SCHOLAR')); // Also expensive
$attributesSpec->add(new BuildSpecification('SLENDER')); // Cheaper checks last
$attributesSpec->add(new ComplexionSpecification('PALE'));
$optimizedSpec->add($attributesSpec);

// NOT example: Exclude certain artists
$acceptedArtists = new AndSpecification();
$acceptedArtists->add(
    new NotSpecification(new ArtistSpecification('The Rolling Stones'))
);
$acceptedArtists->add(
    new NotSpecification(new ArtistSpecification('U2'))
);
```

## Real-World Use Cases

### Smart Playlists (iTunes/Apple Music)

**Problem:** Users want dynamic playlists that automatically include new tracks matching criteria.

**Solution:** Each smart playlist is a Specification tree.

```php
<?php
// Smart Playlist: Alternative, Rock, or New Wave 5-star tracks
//                 excluding Rolling Stones and U2

$genres = new OrSpecification(); // ANY of these genres
$genres->add(new GenreSpecification('Alternative'));
$genres->add(new GenreSpecification('Rock'));
$genres->add(new GenreSpecification('New Wave'));

$acceptedArtists = new AndSpecification(); // NONE of these artists
$acceptedArtists->add(
    new NotSpecification(new ArtistSpecification('The Rolling Stones'))
);
$acceptedArtists->add(
    new NotSpecification(new ArtistSpecification('U2'))
);

$fiveStarAltRock = new AndSpecification(); // ALL conditions
$fiveStarAltRock->add($genres);
$fiveStarAltRock->add(new RatingSpecification(5));
$fiveStarArtRock->add($acceptedArtists);

// Automatically updates as tracks are added/changed
$playlistTracks = $trackManager->getContextsBy($fiveStarAltRock);
```

**GUI Interaction:** The GUI builds the specification tree incrementally as users interact with dropdowns, checkboxes, and add/remove buttons.

### Google Job Search

**Problem:** Job seekers need customizable search criteria with saved alerts.

**Solution:** Each job search is a Specification. Save it for alerts.

```php
<?php
$jobSpec = new AndSpecification();
$jobSpec->add(new LocationSpecification('San Francisco'));
$jobSpec->add(new TitleSpecification('Senior PHP Developer'));
$jobSpec->add(new SalaryRangeSpecification(120000, 180000));
$jobSpec->add(new RemoteSpecification(true));

// Immediate search
$jobs = $jobSearchManager->getContextsBy($jobSpec);

// Save as alert for new matching jobs
$alertManager->createAlert($userId, $jobSpec);
```

### Matchmaker (Filtering Candidates)

**Problem:** Find candidates matching complex personal criteria.

**Solution:** Express preferences as composable specifications.

```php
<?php
// "Slender and pale, scholar for Papa, rich for Mama, or just handsome"
$idealMatch = new AndSpecification();
$idealMatch->add(new BuildSpecification('SLENDER'));
$idealMatch->add(new ComplexionSpecification('PALE'));
$idealMatch->add(new EducationSpecification('SCHOLAR'));
$idealMatch->add(new WealthSpecification('RICH'));

$acceptableMatch = new OrSpecification();
$acceptableMatch->add($idealMatch);
$acceptableMatch->add(new LooksSpecification('HANDSOME'));

$candidates = $matchmaker->getContextsBy($acceptableMatch);
```

### Product Filtering (E-commerce)

**Problem:** Customers need flexible product filtering with multiple criteria.

**Solution:** Each filter combination is a Specification.

```php
<?php
// Products: Category=Electronics, Price=$500-$1000, Rating>=4, InStock
$productSpec = new AndSpecification();
$productSpec->add(new CategorySpecification('Electronics'));
$productSpec->add(new PriceRangeSpecification(500, 1000));
$productSpec->add(new MinRatingSpecification(4));
$productSpec->add(new InStockSpecification(true));

$products = $productRepository->getContextsBy($productSpec);
```

### Monitoring & Alerts

**Problem:** Send notifications when metrics cross thresholds or match patterns.

**Solution:** Alert criteria are Specifications evaluated continuously.

```php
<?php
// Alert: CPU > 80% AND Memory > 90% OR DiskSpace < 10%
$resourceCrisis = new OrSpecification();

$highLoad = new AndSpecification();
$highLoad->add(new CpuThresholdSpecification(80, 'ABOVE'));
$highLoad->add(new MemoryThresholdSpecification(90, 'ABOVE'));
$resourceCrisis->add($highLoad);

$resourceCrisis->add(new DiskSpaceThresholdSpecification(10, 'BELOW'));

// Subscribe to alerts matching this specification
$alertManager->subscribe($server, $resourceCrisis, $notificationHandler);
```

## Benefits

| Benefit | Description |
|---------|-------------|
| **Client Control** | Clients define their own specifications without modifying core code |
| **Reusability** | Leaf specifications reused across different composite trees |
| **Composability** | Boolean operators allow infinite combinations from finite specs |
| **Open/Closed Principle** | Add new leaf specs without changing composites or manager |
| **Separation of Concerns** | Business rules separated from filtering mechanism |
| **Thread Safety** | Immutable tree structure (after activation) is thread-safe |
| **Testability** | Each specification can be unit tested in isolation |
| **Declarative** | Query logic is declarative, not procedural |

## Drawbacks

| Drawback | Description | Mitigation |
|----------|-------------|------------|
| **Client Complexity** | Clients must understand how to compose specifications | Provide builder/DSL for common patterns |
| **Performance** | Evaluates every object in collection | Use with indexed collections or lazy evaluation |
| **Debugging** | Complex trees hard to debug when logic is wrong | Provide toString() methods for visualization |
| **Type Safety** | Wrong attribute specs for context type (caught at runtime) | Use generics or type hints in modern PHP |
| **Client Blame** | When clients configure mistakes, they blame you first | Clear documentation, validation, helpful errors |

## When to Use

Use Specification when:
- Clients need to define custom filtering criteria at runtime
- Selection criteria are complex boolean expressions
- Business rules need to be reused and combined
- You're building query/search/filter functionality for objects (not DB records)
- Alert or notification criteria vary by user
- Selection logic would otherwise be scattered across codebase

## When NOT to Use

Avoid Specification when:
- Simple static filtering suffices (use array_filter with closures)
- Working with database records (use SQL/query builder)
- Only one or two fixed filtering criteria exist
- Performance is critical and collection is large (use indexing/DB queries)
- Clients should not have control over filtering logic

## Related Patterns

| Pattern | Relationship |
|---------|--------------|
| **Strategy** | Specification's foundation - `isSatisfied()` is the strategy |
| **Composite** | Specification is a specialized Composite tree |
| **Template Method** | Used in `SpecificationComposite` for state management |
| **Dependency Injection** | Clients inject specifications into ContextManager |
| **Interpreter** | Similar concept - both build expression trees |
| **Visitor** | Can be used to traverse/analyze specification trees |
| **Builder** | Consider for complex specification construction |

## Implementation Considerations

### Performance Optimization

**Order matters:** Place most likely to fail/succeed specs first for short-circuiting.

```php
<?php
// GOOD: Check expensive criteria last
$spec = new AndSpecification();
$spec->add(new InStockSpecification(true)); // Fast check
$spec->add(new PriceRangeSpecification(100, 200)); // Fast check
$spec->add(new ComplexCalculationSpecification()); // Expensive - last

// GOOD: Check most likely to succeed first in OR
$spec = new OrSpecification();
$spec->add(new PopularCategorySpecification()); // 80% of products match
$spec->add(new RareAttributeSpecification()); // 2% match
```

### Specification Validation

Add validation before allowing client-constructed specs.

```php
<?php
class SpecificationValidator {
    public function validate(Specification $spec): ValidationResult {
        // Check for empty composites
        // Check for overly complex trees (max depth)
        // Check for contradictions (X AND NOT X)
        // Return errors/warnings
    }
}
```

### Specification DSL/Builder

Reduce client complexity with fluent interface.

```php
<?php
class SpecificationBuilder {
    private Specification $spec;

    public static function where(Specification $spec): self {
        $builder = new self();
        $builder->spec = $spec;
        return $builder;
    }

    public function and(Specification $spec): self {
        $newSpec = new AndSpecification();
        $newSpec->add($this->spec);
        $newSpec->add($spec);
        $this->spec = $newSpec;
        return $this;
    }

    public function or(Specification $spec): self {
        $newSpec = new OrSpecification();
        $newSpec->add($this->spec);
        $newSpec->add($spec);
        $this->spec = $newSpec;
        return $this;
    }

    public function not(): self {
        $this->spec = new NotSpecification($this->spec);
        return $this;
    }

    public function build(): Specification {
        return $this->spec;
    }
}

// Usage
$spec = SpecificationBuilder::where(new ColorSpecification('BLUE'))
    ->and(new ShapeSpecification('CIRCLE'))
    ->build();
```

### Specification Persistence

Save specifications for later use (saved searches, alerts).

```php
<?php
interface SpecificationSerializer {
    public function serialize(Specification $spec): string;
    public function deserialize(string $data): Specification;
}

// JSON example
class JsonSpecificationSerializer implements SpecificationSerializer {
    public function serialize(Specification $spec): string {
        // Convert tree to JSON structure
    }

    public function deserialize(string $json): Specification {
        // Rebuild tree from JSON
    }
}
```

### Debugging & Visualization

Provide human-readable representation of specification trees.

```php
<?php
interface Specification {
    public function isSatisfied(Context $context): bool;
    public function toString(int $depth = 0): string; // For debugging
}

class ColorSpecification implements Specification {
    // ...
    public function toString(int $depth = 0): string {
        $indent = str_repeat('  ', $depth);
        return "{$indent}Color == {$this->color}";
    }
}

class AndSpecification extends SpecificationComposite {
    // ...
    public function toString(int $depth = 0): string {
        $indent = str_repeat('  ', $depth);
        $lines = ["{$indent}AND ("];
        foreach ($this->specifications as $spec) {
            $lines[] = $spec->toString($depth + 1);
        }
        $lines[] = "{$indent})";
        return implode("\n", $lines);
    }
}
```

## Testing Strategies

### Unit Testing Leaf Specifications

```php
<?php
class ColorSpecificationTest extends TestCase {
    public function test_satisfied_when_color_matches(): void {
        $spec = new ColorSpecification('RED');
        $context = $this->createContextWithColor('RED');

        $this->assertTrue($spec->isSatisfied($context));
    }

    public function test_not_satisfied_when_color_differs(): void {
        $spec = new ColorSpecification('RED');
        $context = $this->createContextWithColor('BLUE');

        $this->assertFalse($spec->isSatisfied($context));
    }
}
```

### Unit Testing Composite Specifications

```php
<?php
class AndSpecificationTest extends TestCase {
    public function test_satisfied_when_all_specs_satisfied(): void {
        $spec = new AndSpecification();
        $spec->add($this->createSatisfiedSpec());
        $spec->add($this->createSatisfiedSpec());

        $this->assertTrue($spec->isSatisfied($this->createContext()));
    }

    public function test_not_satisfied_when_any_spec_fails(): void {
        $spec = new AndSpecification();
        $spec->add($this->createSatisfiedSpec());
        $spec->add($this->createUnsatisfiedSpec());

        $this->assertFalse($spec->isSatisfied($this->createContext()));
    }

    public function test_throws_when_adding_after_activation(): void {
        $spec = new AndSpecification();
        $spec->add($this->createSatisfiedSpec());
        $spec->isSatisfied($this->createContext()); // Activates

        $this->expectException(InvalidStateException::class);
        $spec->add($this->createSatisfiedSpec()); // Should throw
    }
}
```

### Integration Testing with ContextManager

```php
<?php
class ContextManagerTest extends TestCase {
    public function test_filters_contexts_by_specification(): void {
        $manager = new ContextManager();
        $manager->addContext($this->createRedCircle());
        $manager->addContext($this->createBlueSquare());
        $manager->addContext($this->createRedSquare());

        $spec = new ColorSpecification('RED');
        $results = $manager->getContextsBy($spec);

        $this->assertCount(2, $results);
        $this->assertContains($this->createRedCircle(), $results);
        $this->assertContains($this->createRedSquare(), $results);
    }
}
```

## WordPress/PHP Considerations

### Using with WP_Query Alternative

When working with in-memory objects (not DB queries):

```php
<?php
// Instead of complex WP_Query args, use specifications for object filtering
class PostSpecification implements Specification {
    private string $postType;

    public function __construct(string $postType) {
        $this->postType = $postType;
    }

    public function isSatisfied(Context $post): bool {
        return $post->post_type === $this->postType;
    }
}

// Filter already-loaded posts
$spec = new AndSpecification();
$spec->add(new PostSpecification('product'));
$spec->add(new PostStatusSpecification('publish'));
$spec->add(new PostMetaSpecification('featured', true));

$filteredPosts = $postManager->getContextsBy($spec);
```

### Hook-Based Dynamic Filtering

Allow plugins to modify specifications via hooks:

```php
<?php
$spec = new ProductSpecification();
$spec = apply_filters('my_plugin_product_spec', $spec, $context);
$products = $repository->getContextsBy($spec);
```

### WooCommerce Product Filtering

```php
<?php
// Complex product filtering beyond WC_Product_Query
$spec = new AndSpecification();
$spec->add(new ProductCategorySpecification('electronics'));
$spec->add(new PriceRangeSpecification(100, 500));
$spec->add(new InStockSpecification(true));
$spec->add(new OnSaleSpecification(true));

// Add custom attribute specifications
$spec->add(new ProductAttributeSpecification('pa_color', 'blue'));

$products = $productRepository->getContextsBy($spec);
```

## Comparison to Similar Patterns

### Specification vs Strategy

| Aspect | Specification | Strategy |
|--------|---------------|----------|
| **Purpose** | Filter/select objects by criteria | Encapsulate interchangeable algorithms |
| **Structure** | Composite tree of strategies | Single strategy instance |
| **Client Role** | Builds specification tree | Chooses one strategy |
| **Composition** | Boolean operators (AND/OR/NOT) | No composition |

**Relationship:** Specification uses Strategy as its foundation but adds composability.

### Specification vs Interpreter

| Aspect | Specification | Interpreter |
|--------|---------------|-------------|
| **Purpose** | Evaluate objects against criteria | Evaluate expressions in a language |
| **Domain** | Object filtering/selection | Language parsing/evaluation |
| **Composition** | Boolean operators | Grammar rules |
| **Complexity** | Simpler, focused use case | More general, complex |

**Relationship:** Both build expression trees, but Specification is specialized for filtering.

## Advanced Techniques

### Lazy Evaluation

For large collections, evaluate specifications lazily:

```php
<?php
class LazySpecificationResult implements Iterator {
    private Iterator $source;
    private Specification $spec;

    public function current(): Context {
        return $this->source->current();
    }

    public function next(): void {
        do {
            $this->source->next();
        } while (
            $this->source->valid() &&
            !$this->spec->isSatisfied($this->source->current())
        );
    }

    // ... other Iterator methods
}
```

### Specification Caching

Cache expensive specification evaluations:

```php
<?php
class CachedSpecification implements Specification {
    private Specification $spec;
    private array $cache = [];

    public function isSatisfied(Context $context): bool {
        $key = $context->getId();
        if (!isset($this->cache[$key])) {
            $this->cache[$key] = $this->spec->isSatisfied($context);
        }
        return $this->cache[$key];
    }
}
```

### Specification Negation Optimization

Optimize double negation:

```php
<?php
class NotSpecification implements Specification {
    public function __construct(Specification $spec) {
        // Unwrap double negation: NOT(NOT(X)) = X
        if ($spec instanceof NotSpecification) {
            $this->specification = $spec->getInnerSpecification();
            $this->isNegated = false;
        } else {
            $this->specification = $spec;
            $this->isNegated = true;
        }
    }
}
```

## Key Takeaways

1. **Client-Driven Composition** - Unlike most patterns, clients build the structure themselves
2. **Boolean Logic Composability** - AND, OR, NOT operations allow infinite combinations
3. **Immutability After Activation** - Once used, specification trees are thread-safe
4. **Strategy + Composite** - Combines two GoF patterns into specialized filtering tool
5. **Object-Level Queries** - Like SQL for objects, not database records
6. **Reusable Business Rules** - Encapsulate and compose domain logic
7. **Performance Matters** - Order specifications for optimal short-circuiting
8. **Validation Important** - Client-built structures need validation/safeguards
9. **Not a Silver Bullet** - Use for dynamic filtering; static cases don't need this complexity
10. **Testing is Straightforward** - Test leaf specs and boolean operators independently

## References

- [Specifications - Original article by Eric Evans and Martin Fowler](https://martinfowler.com/apsupp/spec.pdf)
- [Wikipedia: Specification Pattern](https://en.wikipedia.org/wiki/Specification_pattern)
- [DevIQ: Specification Pattern](https://deviq.com/design-patterns/specification-pattern)
- [Design Pattern Specification Blog](https://marcaube.ca/2015/05/specifications)
- [InfoWorld: How to use the specification design pattern in C#](https://www.infoworld.com/article/3710289/how-to-use-the-specification-design-pattern-in-c-sharp.html)
- [Composable Design Patterns](https://jhumelsine.github.io/2024/01/03/composable-design-patterns-basic-concepts.html)

## Summary

The Specification pattern allows clients to define complex, composable filtering criteria for objects. It extends Composite with boolean operators (AND, OR, NOT) built on Strategy's foundation. Clients construct specification trees dynamically and inject them into repositories or managers for filtering.

**Use when:** Clients need dynamic, customizable filtering with complex boolean logic.

**Avoid when:** Simple static filtering suffices or you're working with database queries (use SQL).

**Key insight:** Specification shifts query construction power to clients while keeping filtering mechanism centralized and reusable. The pattern's flexibility is both its greatest strength and potential weakness - provide validation and clear documentation.
