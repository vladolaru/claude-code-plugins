# Decorator Pattern

## Intent

Layer additional behaviors upon core features dynamically through object composition, providing a flexible alternative to inheritance for extending functionality.

## Problem

Inheritance creates tight coupling, locks behavior at compile time, and causes class explosion when combining optional features. A coffee shop with 4 coffee types and 6 condiments needs 24 subclasses via inheritance -- and grows exponentially with each new option.

## Solution

Chain objects that all implement the **same interface**, where each decorator wraps the next and adds behavior before/after delegating. Behavior is configured at runtime through composition, not locked at compile time through inheritance.

```
Decorator A -> Decorator B -> Decorator C -> Core Feature
   |               |               |               |
execute()      execute()      execute()      execute()
```

**Best practice:** Combine Decorator with Template Method to enforce correct delegation:

```php
abstract class Decorator implements Feature {
    private Feature $delegate;

    public function __construct(Feature $delegate) {
        $this->delegate = $delegate;
    }

    // FINAL prevents subclasses from breaking the chain
    final public function execute(): Result {
        $this->preExecute();
        $result = $this->delegate->execute();
        $this->postExecute();
        return $result;
    }

    protected function preExecute(): void {}   // override if needed
    protected function postExecute(): void {}  // override if needed
}
```

## When to Use

- Multiple optional features that can be mixed and matched at runtime
- Avoiding inheritance explosion for combinations
- Each added responsibility follows Single Responsibility Principle
- Clients should use decorated objects transparently (same interface)
- Adding/removing behavior without modifying existing classes (Open/Closed)

### When NOT to Use

- You need to add new methods not in the interface (use inheritance or Adapter)
- Only one fixed combination exists (simple subclass suffices)
- Decorators need to coordinate with each other (use Mediator or Strategy)
- Simple boolean flags would solve the problem without the abstraction

## WordPress/PHP

### WooCommerce Price Decoration

```php
interface PriceCalculator {
    public function getPrice(WC_Product $product): float;
}

class BasePriceCalculator implements PriceCalculator {
    public function getPrice(WC_Product $product): float {
        return (float) $product->get_regular_price();
    }
}

abstract class PriceDecorator implements PriceCalculator {
    public function __construct(private PriceCalculator $next) {}

    final public function getPrice(WC_Product $product): float {
        $price = $this->next->getPrice($product);
        return $this->adjustPrice($price, $product);
    }

    abstract protected function adjustPrice(float $price, WC_Product $product): float;
}

class SaleDecorator extends PriceDecorator {
    protected function adjustPrice(float $price, WC_Product $product): float {
        $sale = $product->get_sale_price();
        return $sale ? min($price, (float) $sale) : $price;
    }
}

class TaxDecorator extends PriceDecorator {
    protected function adjustPrice(float $price, WC_Product $product): float {
        $rate = WC_Tax::get_rates($product->get_tax_class());
        return $price * (1 + array_sum(wp_list_pluck($rate, 'rate')) / 100);
    }
}

class BulkDiscountDecorator extends PriceDecorator {
    public function __construct(PriceCalculator $next, private int $qty) {
        parent::__construct($next);
    }

    protected function adjustPrice(float $price, WC_Product $product): float {
        return $this->qty >= 10 ? $price * 0.9 : $price;
    }
}

// Runtime composition -- order matters
$calculator = new TaxDecorator(
    new BulkDiscountDecorator(
        new SaleDecorator(
            new BasePriceCalculator()
        ),
        $quantity
    )
);
$finalPrice = $calculator->getPrice($product);
```

## JS/TS

### React Higher-Order Component (HOC) / Wrapper

```tsx
// Decorator pattern in React: HOCs wrap components to add behavior
// while preserving the original component's interface (props).

function withLoading<P extends object>(
  WrappedComponent: React.ComponentType<P>
): React.FC<P & { isLoading: boolean }> {
  return function LoadingWrapper({ isLoading, ...props }) {
    if (isLoading) return <Spinner />;
    return <WrappedComponent {...(props as P)} />;
  };
}

function withErrorBoundary<P extends object>(
  WrappedComponent: React.ComponentType<P>
): React.FC<P> {
  return class extends React.Component<P> {
    state = { hasError: false };
    static getDerivedStateFromError() { return { hasError: true }; }
    render() {
      if (this.state.hasError) return <ErrorFallback />;
      return <WrappedComponent {...this.props} />;
    }
  };
}

// Chain decorators -- each wraps the previous
const EnhancedProductCard = withErrorBoundary(
  withLoading(ProductCard)
);
```

## Common Mistakes

- **WRONG:** Forgetting to call the delegate's method (breaks the chain silently)
  **RIGHT:** Use Template Method with `final` to guarantee delegation always happens

- **WRONG:** Adding business logic unrelated to the decoration concern
  **RIGHT:** Each decorator should have one clear, single responsibility

- **WRONG:** Decorator order is random when order matters (e.g., encrypt then compress vs compress then encrypt)
  **RIGHT:** Document ordering constraints; enforce in a builder/configurer

- **WRONG:** Using `instanceof` checks to identify specific decorators in the chain
  **RIGHT:** Treat the chain as opaque; if you need introspection, reconsider the design

## Relationships

- Decorator vs **Proxy** -- Proxy is effectively Decorator with one wrapper that controls access; Decorator chains multiple wrappers for behavior
- Decorator vs **Strategy** -- Decorator wraps externally (skin); Strategy injects internally (guts)
- Decorator vs **Adapter** -- Decorator keeps the same interface; Adapter changes it
- Decorator vs **Composite** -- Decorator is a linear chain; Composite is a tree with multiple leaves
- Decorator + **Template Method** -- Combining them enforces correct delegation order
