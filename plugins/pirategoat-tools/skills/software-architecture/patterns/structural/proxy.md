# Proxy Pattern

## Quick Reference

| Aspect | Detail |
|--------|--------|
| Intent | Provide a surrogate that controls access to another object through the same interface |
| When to Use | Administrative concerns (lazy-loading, caching, access control) should not burden client code |
| Key Benefit | Encapsulates administrative logic once, applied consistently, evolves independently |

## When to Use

- Object is expensive to create and may not be needed (Virtual/Lazy Proxy)
- Access control based on permissions or authentication (Protection Proxy)
- Object lives in a different address space or service (Remote Proxy)
- Repeated expensive operations with same parameters (Caching Proxy)
- Resource cleanup must be guaranteed regardless of code path (Resource Proxy)
- You need transparent logging/auditing without changing the real object

## When NOT to Use

- Object is simple and lightweight -- unnecessary overhead
- No administrative concerns exist -- adds complexity for no benefit
- You need to add multiple stackable responsibilities (use Decorator)
- Client must be aware of proxy behavior (violates transparency -- use explicit wrapper or Facade)

## WordPress/PHP

### Lazy-Loading Post Meta

```php
interface PostMeta {
    public function get(string $key): mixed;
    public function getAll(): array;
}

class DatabasePostMeta implements PostMeta {
    private array $meta;

    public function __construct(private int $postId) {
        // Expensive: loads ALL meta for this post
        $this->meta = get_post_meta($postId);
    }

    public function get(string $key): mixed {
        return $this->meta[$key][0] ?? null;
    }

    public function getAll(): array {
        return $this->meta;
    }
}

class LazyPostMetaProxy implements PostMeta {
    private ?PostMeta $real = null;

    public function __construct(private int $postId) {}

    public function get(string $key): mixed {
        return $this->load()->get($key);
    }

    public function getAll(): array {
        return $this->load()->getAll();
    }

    private function load(): PostMeta {
        if ($this->real === null) {
            $this->real = new DatabasePostMeta($this->postId);
        }
        return $this->real;
    }
}

// Client code unchanged -- proxy is transparent
function displayProduct(PostMeta $meta): void {
    echo $meta->get('_price');  // meta loaded only now, on first access
}
```

### WooCommerce Caching Proxy (Transients)

```php
interface PriceCalculator {
    public function calculate(int $productId, int $qty): float;
}

class CachedPriceProxy implements PriceCalculator {
    public function __construct(
        private PriceCalculator $real,
        private int $ttl = 3600
    ) {}

    public function calculate(int $productId, int $qty): float {
        $key = "price_{$productId}_{$qty}";
        $cached = get_transient($key);

        if ($cached !== false) {
            return (float) $cached;
        }

        $price = $this->real->calculate($productId, $qty);
        set_transient($key, $price, $this->ttl);
        return $price;
    }
}
```

## Common Mistakes

- **WRONG:** Proxy delegates to a concrete class instead of the interface
  **RIGHT:** Delegate to the interface so proxies can chain (logging -> caching -> remote -> real)

- **WRONG:** Client code type-hints the Proxy class directly
  **RIGHT:** Client depends on the interface; proxy is transparent and swappable

- **WRONG:** Putting business logic in the proxy (validation, transformation)
  **RIGHT:** Proxy handles only administrative concerns (caching, access, lazy-loading)

- **WRONG:** Using Proxy when you need multiple stackable behaviors
  **RIGHT:** Use Decorator for stackable behavior chains; Proxy is typically a single wrapper for one concern

## Relationships

- Proxy vs **Decorator** -- Both wrap with the same interface, but Proxy controls access (single concern); Decorator adds behavior (stackable)
- Proxy vs **Adapter** -- Proxy keeps the same interface; Adapter changes it
- Proxy vs **Facade** -- Proxy controls one object; Facade simplifies an entire subsystem
- Proxy is structurally identical to Decorator with one wrapper -- the difference is intent
- If you need multiple administrative concerns, chain proxies via the interface or switch to Decorator
