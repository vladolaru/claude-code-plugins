# Specification Pattern

## Quick Reference

| Aspect | Detail |
|--------|--------|
| Intent | Encapsulate business rules into reusable, composable query objects evaluated against domain entities |
| When to Use | Clients need to define custom, dynamic filtering criteria with boolean composition |
| Key Benefit | Client-driven composition of AND/OR/NOT from simple attribute checks |

## When to Use

- Clients need to define custom filtering criteria **at runtime**
- Selection criteria are complex boolean expressions (AND/OR/NOT)
- Business rules need to be reused and combined across contexts
- Building query/search/filter for in-memory objects (not DB records)
- Alert or notification criteria vary by user
- Selection logic would otherwise be hardcoded and scattered

## When NOT to Use

- Simple static filtering suffices (`array_filter` with closures)
- Working with database records (use SQL/query builder instead)
- Only one or two fixed filtering criteria exist
- Performance critical with large collections (use indexed DB queries)
- Clients should not control filtering logic

## WordPress/PHP: WooCommerce Product Filtering

```php
interface Specification {
    public function isSatisfied(object $context): bool;
}

// Leaf specifications -- single attribute checks
class ProductCategorySpecification implements Specification {
    public function __construct(private string $category) {}

    public function isSatisfied(object $product): bool {
        return has_term($this->category, 'product_cat', $product->get_id());
    }
}

class PriceRangeSpecification implements Specification {
    public function __construct(private float $min, private float $max) {}

    public function isSatisfied(object $product): bool {
        $price = (float) $product->get_price();
        return $price >= $this->min && $price <= $this->max;
    }
}

class InStockSpecification implements Specification {
    public function isSatisfied(object $product): bool {
        return $product->is_in_stock();
    }
}

// Composite specifications -- boolean operators
class AndSpecification implements Specification {
    /** @var Specification[] */
    private array $specs = [];

    public function add(Specification $spec): void {
        $this->specs[] = $spec;
    }

    public function isSatisfied(object $context): bool {
        foreach ($this->specs as $spec) {
            if (!$spec->isSatisfied($context)) return false; // Short-circuit
        }
        return true;
    }
}

class OrSpecification implements Specification {
    /** @var Specification[] */
    private array $specs = [];

    public function add(Specification $spec): void {
        $this->specs[] = $spec;
    }

    public function isSatisfied(object $context): bool {
        foreach ($this->specs as $spec) {
            if ($spec->isSatisfied($context)) return true; // Short-circuit
        }
        return false;
    }
}

class NotSpecification implements Specification {
    public function __construct(private Specification $spec) {}

    public function isSatisfied(object $context): bool {
        return !$this->spec->isSatisfied($context);
    }
}

// Repository filters by specification
class ProductRepository {
    public function findBy(Specification $spec): array {
        $all = wc_get_products(['limit' => -1]);
        return array_filter($all, fn($p) => $spec->isSatisfied($p));
    }
}

// Client composes specifications dynamically
$spec = new AndSpecification();
$spec->add(new ProductCategorySpecification('electronics'));
$spec->add(new PriceRangeSpecification(100, 500));
$spec->add(new InStockSpecification());

// Extend via hooks
$spec = apply_filters('my_plugin_product_spec', $spec, $request);

$products = $repository->findBy($spec);
```

**Performance tip:** Order specs for optimal short-circuiting. In AND, place most-likely-to-fail first. In OR, place most-likely-to-succeed first. Put expensive checks last.

## Common Mistakes

- **WRONG:** Using Specification for database queries (use SQL/query builder for indexed data)
  **RIGHT:** Use for in-memory object filtering where SQL is not applicable

- **WRONG:** No validation of client-built specification trees (contradictions, empty composites)
  **RIGHT:** Validate specs before evaluation; provide clear error messages

- **WRONG:** Ignoring evaluation order (expensive checks first in AND)
  **RIGHT:** Place cheap/likely-to-short-circuit checks first

- **WRONG:** Mutable specification trees after evaluation begins (thread-safety issues)
  **RIGHT:** Lock specs after first `isSatisfied()` call or use immutable construction

## Relationships

- **Strategy** -- Specification's foundation; `isSatisfied()` is the strategy method
- **Composite** -- Specification is a specialized Composite tree with boolean operators
- **Interpreter** -- both build expression trees; Specification is specialized for filtering
- **Builder** -- consider fluent builder for complex specification construction
- **Dependency Injection** -- clients inject specifications into repositories/managers
