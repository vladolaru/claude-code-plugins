# Template Method Pattern

## Intent

Define the skeleton of an algorithm in an abstract base class, letting subclasses provide specific implementations for certain steps without changing the algorithm's structure.

## Motivation

Template Method is closely related to Strategy, with a key difference:

- **Strategy**: Interface wholly implemented by each class. Complete freedom per implementation.
- **Template Method**: Abstract base class retains most of the implementation. Only specific "hook" steps are delegated to subclasses.

The base class controls the algorithm flow and enforces invariants (security, ordering, logging). Subclasses customize only the designated extension points.

**Primary mechanism:** Polymorphism -- abstract base class calls protected abstract methods implemented differently by each subclass.

**Key access modifier strategy:**
- `final public` -- template method itself (prevents subclasses from changing algorithm structure)
- `private` -- fixed behavior that cannot be overridden (security checks, logging)
- `protected abstract` -- hooks that subclasses must implement
- `protected` with default -- optional hooks subclasses may override

## When to Use Template Method

- **Multiple classes share a similar algorithm** but differ in specific steps
- **You need to enforce a specific execution order** (temporal coupling)
- **Critical operations must not be overridden** (security, validation, logging)
- **You're building a framework** where application code plugs in at specific points
- **You want to avoid code duplication** while allowing customization

### Consider Strategy Instead When

- Entire algorithm varies (not just steps)
- You need runtime algorithm switching (Template Method is fixed at instantiation)
- You prefer composition over inheritance
- Base class would be mostly empty (all behavior in subclasses)

### Avoid When

- Only one implementation exists
- Steps have no natural order
- Every method would be abstract (just use an interface/Strategy)

## WordPress/PHP Example: WP_List_Table Style

```php
/**
 * Abstract base: enforces security + algorithm structure.
 * Subclasses cannot bypass authentication or change step order.
 */
abstract class DataImporter
{
    final public function import(array $data, WP_User $user): void
    {
        // Fixed security steps -- cannot be overridden
        if (!$this->isAuthenticated($user)) {
            throw new Exception("User not authenticated");
        }
        if (!$this->isAuthorized($user)) {
            throw new Exception("User not authorized for this operation");
        }

        // Delegated steps
        $this->validateData($data);
        $this->prepareImport($data);
        $this->executeImport($data);
        $this->finalizeImport($data);
        $this->logImport($user, $data);
    }

    private function isAuthenticated(WP_User $user): bool {
        return is_user_logged_in() && $user->exists();
    }

    private function isAuthorized(WP_User $user): bool {
        return user_can($user, 'import');
    }

    private function logImport(WP_User $user, array $data): void {
        error_log(sprintf('Import by user %d: %s (%d records)',
            $user->ID, $this->getImportType(), count($data)));
    }

    // Hook with default -- can be overridden
    protected function validateData(array $data): void {
        if (empty($data)) {
            throw new InvalidArgumentException("Import data cannot be empty");
        }
    }

    // Hooks -- must be implemented
    abstract protected function prepareImport(array $data): void;
    abstract protected function executeImport(array $data): void;
    abstract protected function getImportType(): string;

    // Hook with default
    protected function finalizeImport(array $data): void {
        wp_cache_flush();
    }
}

/**
 * Concrete: WooCommerce product import
 */
class ProductImporter extends DataImporter
{
    protected function validateData(array $data): void {
        parent::validateData($data);
        foreach ($data as $product) {
            if (empty($product['name']) || empty($product['price'])) {
                throw new InvalidArgumentException("Product must include name and price");
            }
        }
    }

    protected function prepareImport(array $data): void {
        foreach ($data as $product) {
            if (!empty($product['category']) && !term_exists($product['category'], 'product_cat')) {
                wp_insert_term($product['category'], 'product_cat');
            }
        }
    }

    protected function executeImport(array $data): void {
        foreach ($data as $product) {
            $product_id = wp_insert_post([
                'post_title'  => $product['name'],
                'post_type'   => 'product',
                'post_status' => 'publish',
            ]);
            update_post_meta($product_id, '_regular_price', $product['price']);
            if (!empty($product['category'])) {
                wp_set_object_terms($product_id, $product['category'], 'product_cat');
            }
        }
    }

    protected function finalizeImport(array $data): void {
        parent::finalizeImport($data);
        WC_Cache_Helper::invalidate_cache_group('products');
        do_action('woocommerce_products_imported', count($data));
    }

    protected function getImportType(): string { return 'WooCommerce Products'; }
}
```

**WordPress/WooCommerce core examples of this pattern:**
- `WP_List_Table` -- admin list tables with customizable columns and actions
- `WP_Widget` -- widget registration/display with customizable `form()` and `update()`
- `WC_Email` -- email template with customizable content, subject, headers
- `WC_Payment_Gateway` -- payment processing with customizable `process_payment()`
- `WP_REST_Controller` -- REST endpoints with customizable `get_items()`, `create_item()`

## Common Mistakes

- **WRONG:** Making security methods `protected` (subclass can bypass authentication)
  **RIGHT:** Use `private` for security checks, `final` for the template method

- **WRONG:** Not making the template method `final` (subclass can rewrite the algorithm)
  **RIGHT:** `final public function templateMethod()` to lock the structure

- **WRONG:** Making every step abstract when most share the same implementation
  **RIGHT:** Provide default implementations for common steps; only abstract the varying ones

- **WRONG:** Using Template Method when entire algorithm varies per implementation
  **RIGHT:** Use Strategy (composition) when there's no shared skeleton to preserve

## Relationships

- **Strategy** -- both use polymorphism for behavioral variation; Strategy uses composition (more flexible, runtime swappable), Template Method uses inheritance (enforces structure, controls ordering)
- **Factory Method** -- a specialized Template Method where the hook creates objects
- **Command** -- Commands can use Template Method for their execution flow
- **Hook pattern** -- Template Method is essentially a sophisticated hook system (Hollywood Principle: "Don't call us, we'll call you")
