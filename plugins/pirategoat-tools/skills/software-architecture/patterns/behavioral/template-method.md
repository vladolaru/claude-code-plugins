# Template Method Pattern

## Intent

Define the skeleton of an algorithm in an abstract base class, letting subclasses provide specific implementations for certain steps without changing the algorithm's structure.

## Classification

**Behavioral Pattern** - Defines how objects interact and distribute responsibility through polymorphism while maintaining control over the overall algorithm structure.

## Motivation

The Template Method pattern is closely related to Strategy, but with a key difference:

- **Strategy**: Declares a contract (often via interface) which is wholly implemented within extending classes. Each implementation has complete freedom.
- **Template Method**: Declares a contract via an abstract base class that retains most of the implementation. Specific details are delegated to extending classes through abstract protected methods.

Think of it like a Mad Libs game - most of the story exists in the template, but there are strategic gaps (identified by their parts of speech) where each player provides their own words, making each story similar yet different.

## Structure

```
┌─────────────────────┐
│  AbstractClass      │
├─────────────────────┤
│ + templateMethod()  │◄───────── Public contract method
│ # step1()           │◄───────── Protected abstract hook
│ # step2()           │◄───────── Protected abstract hook
│ # step3()           │◄───────── Protected abstract hook
│ - privateHelper()   │◄───────── Private implementation
└─────────────────────┘
         △
         │ extends
    ┌────┴────┐
    │         │
┌───┴────┐ ┌──┴──────┐
│ ConcreteA│ │ConcreteB│
├─────────┤ ├─────────┤
│ # step1()│ │ # step1()│
│ # step2()│ │ # step2()│
│ # step3()│ │ # step3()│
└─────────┘ └─────────┘
```

**Key Components:**

- **AbstractClass**: Contains the `templateMethod()` which defines the algorithm skeleton
- **templateMethod()**: Public method that orchestrates the algorithm by calling abstract and concrete methods in a specific order
- **Abstract Methods**: Protected methods that subclasses must implement (the "hooks")
- **Concrete Methods**: Private/protected methods with fixed behavior that cannot be overridden
- **ConcreteA/ConcreteB**: Subclasses that provide specific implementations for the abstract steps

**Important:** The client only accesses `templateMethod()`. The step methods are implementation details, encapsulated from external access.

## Primary OO Mechanism

**Polymorphism** - The abstract base class delegates to protected abstract methods, which are implemented differently by each extending class, allowing for different behaviors within the same algorithm structure.

## PHP Implementation

### Example 1: Hot Beverage Shop

```php
<?php

/**
 * Abstract base class defining the hot beverage preparation template
 */
abstract class HotDrink
{
    /**
     * Template method - defines the algorithm skeleton
     *
     * This is FINAL to prevent subclasses from changing the algorithm structure
     */
    final public function makeRecipe(): void
    {
        $this->boilWater();
        $this->brew();
        $this->pourInCup();
        $this->addCondiments();
        $this->displayRecipe();
    }

    /**
     * Concrete method - behavior never changes
     */
    private function boilWater(): void
    {
        echo "Boiling water...\n";
    }

    /**
     * Concrete method - behavior never changes
     */
    private function pourInCup(): void
    {
        echo "Pouring into cup...\n";
    }

    /**
     * Abstract hook - must be implemented by subclasses
     */
    abstract protected function brew(): void;

    /**
     * Abstract hook - must be implemented by subclasses
     */
    abstract protected function addCondiments(): void;

    /**
     * Concrete method that uses template information
     */
    private function displayRecipe(): void
    {
        echo $this->getDrinkName() . " is ready!\n\n";
    }

    /**
     * Abstract hook for drink identification
     */
    abstract protected function getDrinkName(): string;
}

/**
 * Concrete implementation for Coffee
 */
class Coffee extends HotDrink
{
    protected function brew(): void
    {
        echo "Dripping coffee through filter...\n";
    }

    protected function addCondiments(): void
    {
        echo "Adding sugar and milk...\n";
    }

    protected function getDrinkName(): string
    {
        return "Coffee";
    }
}

/**
 * Concrete implementation for Tea
 */
class Tea extends HotDrink
{
    protected function brew(): void
    {
        echo "Steeping tea bag...\n";
    }

    protected function addCondiments(): void
    {
        echo "Adding lemon...\n";
    }

    protected function getDrinkName(): string
    {
        return "Tea";
    }
}

/**
 * Concrete implementation for Hot Cocoa
 */
class HotCocoa extends HotDrink
{
    protected function brew(): void
    {
        echo "Mixing cocoa powder...\n";
    }

    protected function addCondiments(): void
    {
        echo "Adding marshmallows and whipped cream...\n";
    }

    protected function getDrinkName(): string
    {
        return "Hot Cocoa";
    }
}

// Client code
class DrinkFactory
{
    public static function acquire(string $type): HotDrink
    {
        switch ($type) {
            case 'coffee':
                return new Coffee();
            case 'tea':
                return new Tea();
            case 'cocoa':
                return new HotCocoa();
            default:
                throw new InvalidArgumentException("Unknown drink type: {$type}");
        }
    }
}

// Usage
$drink = DrinkFactory::acquire('coffee');
$drink->makeRecipe();

$drink = DrinkFactory::acquire('tea');
$drink->makeRecipe();

$drink = DrinkFactory::acquire('cocoa');
$drink->makeRecipe();
```

**Output:**
```
Boiling water...
Dripping coffee through filter...
Pouring into cup...
Adding sugar and milk...
Coffee is ready!

Boiling water...
Steeping tea bag...
Pouring into cup...
Adding lemon...
Tea is ready!

Boiling water...
Mixing cocoa powder...
Pouring into cup...
Adding marshmallows and whipped cream...
Hot Cocoa is ready!
```

### Example 2: WordPress Data Import (Enforcing Authentication/Authorization)

```php
<?php

/**
 * Abstract base class for data import operations
 * Enforces authentication and authorization before execution
 */
abstract class DataImporter
{
    /**
     * Template method with enforced security checks
     *
     * FINAL prevents subclasses from bypassing security
     */
    final public function import(array $data, WP_User $user): void
    {
        // These security checks cannot be overridden
        if (!$this->isAuthenticated($user)) {
            throw new Exception("User not authenticated");
        }

        if (!$this->isAuthorized($user)) {
            throw new Exception("User not authorized for this operation");
        }

        // Execute the import operation
        $this->validateData($data);
        $this->prepareImport($data);
        $this->executeImport($data);
        $this->finalizeImport($data);
        $this->logImport($user, $data);
    }

    /**
     * Concrete security check - cannot be overridden
     */
    private function isAuthenticated(WP_User $user): bool
    {
        return is_user_logged_in() && $user->exists();
    }

    /**
     * Concrete security check - cannot be overridden
     */
    private function isAuthorized(WP_User $user): bool
    {
        return user_can($user, 'import');
    }

    /**
     * Concrete logging - cannot be overridden
     */
    private function logImport(WP_User $user, array $data): void
    {
        error_log(sprintf(
            'Import completed by user %d: %s (%d records)',
            $user->ID,
            $this->getImportType(),
            count($data)
        ));
    }

    /**
     * Hook with default implementation - can be overridden
     */
    protected function validateData(array $data): void
    {
        if (empty($data)) {
            throw new InvalidArgumentException("Import data cannot be empty");
        }
    }

    /**
     * Abstract hook - must be implemented by subclasses
     */
    abstract protected function prepareImport(array $data): void;

    /**
     * Abstract hook - must be implemented by subclasses
     */
    abstract protected function executeImport(array $data): void;

    /**
     * Hook with default implementation - can be overridden
     */
    protected function finalizeImport(array $data): void
    {
        // Default: clear caches
        wp_cache_flush();
    }

    /**
     * Abstract hook for import identification
     */
    abstract protected function getImportType(): string;
}

/**
 * Concrete implementation for WooCommerce product import
 */
class ProductImporter extends DataImporter
{
    protected function validateData(array $data): void
    {
        parent::validateData($data); // Call parent validation

        // Additional product-specific validation
        foreach ($data as $product) {
            if (empty($product['name']) || empty($product['price'])) {
                throw new InvalidArgumentException(
                    "Product data must include name and price"
                );
            }
        }
    }

    protected function prepareImport(array $data): void
    {
        // Prepare product categories
        foreach ($data as $product) {
            if (!empty($product['category'])) {
                $this->ensureCategoryExists($product['category']);
            }
        }
    }

    protected function executeImport(array $data): void
    {
        foreach ($data as $product) {
            $product_id = wp_insert_post([
                'post_title'   => $product['name'],
                'post_type'    => 'product',
                'post_status'  => 'publish',
            ]);

            update_post_meta($product_id, '_regular_price', $product['price']);

            if (!empty($product['category'])) {
                wp_set_object_terms(
                    $product_id,
                    $product['category'],
                    'product_cat'
                );
            }
        }
    }

    protected function finalizeImport(array $data): void
    {
        parent::finalizeImport($data); // Call parent finalization

        // Clear WooCommerce-specific caches
        WC_Cache_Helper::invalidate_cache_group('products');

        // Trigger reindex if using search plugins
        do_action('woocommerce_products_imported', count($data));
    }

    protected function getImportType(): string
    {
        return 'WooCommerce Products';
    }

    private function ensureCategoryExists(string $category): void
    {
        if (!term_exists($category, 'product_cat')) {
            wp_insert_term($category, 'product_cat');
        }
    }
}

/**
 * Concrete implementation for WordPress user import
 */
class UserImporter extends DataImporter
{
    protected function validateData(array $data): void
    {
        parent::validateData($data);

        foreach ($data as $user) {
            if (empty($user['username']) || empty($user['email'])) {
                throw new InvalidArgumentException(
                    "User data must include username and email"
                );
            }

            if (!is_email($user['email'])) {
                throw new InvalidArgumentException(
                    "Invalid email address: {$user['email']}"
                );
            }
        }
    }

    protected function prepareImport(array $data): void
    {
        // Check for duplicate usernames/emails
        foreach ($data as $user) {
            if (username_exists($user['username'])) {
                throw new Exception(
                    "Username already exists: {$user['username']}"
                );
            }

            if (email_exists($user['email'])) {
                throw new Exception(
                    "Email already exists: {$user['email']}"
                );
            }
        }
    }

    protected function executeImport(array $data): void
    {
        foreach ($data as $user) {
            wp_insert_user([
                'user_login' => $user['username'],
                'user_email' => $user['email'],
                'user_pass'  => wp_generate_password(),
                'role'       => $user['role'] ?? 'subscriber',
            ]);
        }
    }

    protected function finalizeImport(array $data): void
    {
        parent::finalizeImport($data);

        // Send welcome emails
        foreach ($data as $user) {
            wp_new_user_notification(
                username_exists($user['username']),
                null,
                'user'
            );
        }
    }

    protected function getImportType(): string
    {
        return 'WordPress Users';
    }
}

// Usage
$user = wp_get_current_user();

$products = [
    ['name' => 'Widget', 'price' => '19.99', 'category' => 'Gadgets'],
    ['name' => 'Gizmo', 'price' => '29.99', 'category' => 'Gadgets'],
];

try {
    $importer = new ProductImporter();
    $importer->import($products, $user);
    echo "Products imported successfully\n";
} catch (Exception $e) {
    echo "Import failed: " . $e->getMessage() . "\n";
}
```

### Example 3: WordPress REST API Response Builder

```php
<?php

/**
 * Abstract base class for building REST API responses
 * Ensures consistent response structure and error handling
 */
abstract class REST_Response_Builder
{
    /**
     * Template method for building REST responses
     *
     * Ensures consistent structure: validation → data fetching → formatting → caching
     */
    final public function build_response(WP_REST_Request $request): WP_REST_Response
    {
        try {
            // Fixed steps that cannot be changed
            $this->validate_request($request);
            $raw_data = $this->fetch_data($request);
            $formatted_data = $this->format_data($raw_data, $request);
            $this->cache_response($formatted_data, $request);

            return new WP_REST_Response(
                $this->wrap_response($formatted_data),
                200
            );
        } catch (Exception $e) {
            return $this->handle_error($e);
        }
    }

    /**
     * Concrete method - standardized validation
     */
    private function validate_request(WP_REST_Request $request): void
    {
        if (!$this->is_request_valid($request)) {
            throw new InvalidArgumentException(
                $this->get_validation_error_message()
            );
        }
    }

    /**
     * Concrete method - standardized error handling
     */
    private function handle_error(Exception $e): WP_REST_Response
    {
        return new WP_REST_Response(
            [
                'error' => [
                    'code'    => $e->getCode() ?: 'internal_error',
                    'message' => $e->getMessage(),
                    'type'    => $this->get_endpoint_type(),
                ],
            ],
            $e->getCode() >= 400 && $e->getCode() < 600 ? $e->getCode() : 500
        );
    }

    /**
     * Concrete method - standardized response wrapping
     */
    private function wrap_response(array $data): array
    {
        return [
            'data'     => $data,
            'meta'     => [
                'endpoint' => $this->get_endpoint_type(),
                'version'  => '1.0',
                'cached'   => $this->is_cached_response(),
            ],
        ];
    }

    /**
     * Hook with default implementation
     */
    protected function cache_response(array $data, WP_REST_Request $request): void
    {
        $cache_key = $this->get_cache_key($request);
        $cache_duration = $this->get_cache_duration();

        if ($cache_key && $cache_duration > 0) {
            wp_cache_set($cache_key, $data, $this->get_endpoint_type(), $cache_duration);
        }
    }

    /**
     * Hook with default implementation
     */
    protected function get_cache_key(WP_REST_Request $request): string
    {
        return md5(serialize($request->get_params()));
    }

    /**
     * Hook with default implementation
     */
    protected function get_cache_duration(): int
    {
        return HOUR_IN_SECONDS;
    }

    /**
     * Hook with default implementation
     */
    protected function is_cached_response(): bool
    {
        return false;
    }

    /**
     * Hook with default implementation
     */
    protected function get_validation_error_message(): string
    {
        return 'Invalid request parameters';
    }

    /**
     * Abstract hook - request-specific validation
     */
    abstract protected function is_request_valid(WP_REST_Request $request): bool;

    /**
     * Abstract hook - data fetching logic
     */
    abstract protected function fetch_data(WP_REST_Request $request): array;

    /**
     * Abstract hook - data formatting logic
     */
    abstract protected function format_data(array $raw_data, WP_REST_Request $request): array;

    /**
     * Abstract hook - endpoint identification
     */
    abstract protected function get_endpoint_type(): string;
}

/**
 * Concrete implementation for products endpoint
 */
class Products_Response_Builder extends REST_Response_Builder
{
    protected function is_request_valid(WP_REST_Request $request): bool
    {
        $per_page = $request->get_param('per_page');
        return !$per_page || ($per_page > 0 && $per_page <= 100);
    }

    protected function get_validation_error_message(): string
    {
        return 'per_page must be between 1 and 100';
    }

    protected function fetch_data(WP_REST_Request $request): array
    {
        $args = [
            'post_type'      => 'product',
            'posts_per_page' => $request->get_param('per_page') ?: 10,
            'paged'          => $request->get_param('page') ?: 1,
            'post_status'    => 'publish',
        ];

        if ($category = $request->get_param('category')) {
            $args['tax_query'] = [
                [
                    'taxonomy' => 'product_cat',
                    'field'    => 'slug',
                    'terms'    => $category,
                ],
            ];
        }

        return get_posts($args);
    }

    protected function format_data(array $raw_data, WP_REST_Request $request): array
    {
        return array_map(function($product) {
            return [
                'id'          => $product->ID,
                'name'        => $product->post_title,
                'slug'        => $product->post_name,
                'price'       => get_post_meta($product->ID, '_regular_price', true),
                'sale_price'  => get_post_meta($product->ID, '_sale_price', true),
                'description' => $product->post_excerpt,
                'link'        => get_permalink($product->ID),
            ];
        }, $raw_data);
    }

    protected function get_endpoint_type(): string
    {
        return 'products';
    }

    protected function get_cache_duration(): int
    {
        return 15 * MINUTE_IN_SECONDS;
    }
}

/**
 * Concrete implementation for orders endpoint with no caching
 */
class Orders_Response_Builder extends REST_Response_Builder
{
    protected function is_request_valid(WP_REST_Request $request): bool
    {
        $customer_id = $request->get_param('customer_id');
        return $customer_id && is_numeric($customer_id) && $customer_id > 0;
    }

    protected function get_validation_error_message(): string
    {
        return 'customer_id is required and must be a positive integer';
    }

    protected function fetch_data(WP_REST_Request $request): array
    {
        $customer_id = $request->get_param('customer_id');

        $orders = wc_get_orders([
            'customer_id' => $customer_id,
            'limit'       => $request->get_param('per_page') ?: 10,
            'page'        => $request->get_param('page') ?: 1,
            'orderby'     => 'date',
            'order'       => 'DESC',
        ]);

        return $orders;
    }

    protected function format_data(array $raw_data, WP_REST_Request $request): array
    {
        return array_map(function($order) {
            return [
                'id'            => $order->get_id(),
                'order_number'  => $order->get_order_number(),
                'status'        => $order->get_status(),
                'total'         => $order->get_total(),
                'currency'      => $order->get_currency(),
                'date_created'  => $order->get_date_created()->date('c'),
                'payment_method'=> $order->get_payment_method_title(),
            ];
        }, $raw_data);
    }

    protected function get_endpoint_type(): string
    {
        return 'orders';
    }

    protected function get_cache_duration(): int
    {
        // Orders should not be cached - they change frequently
        return 0;
    }
}

// Register REST routes
add_action('rest_api_init', function() {
    register_rest_route('myshop/v1', '/products', [
        'methods'  => 'GET',
        'callback' => function($request) {
            $builder = new Products_Response_Builder();
            return $builder->build_response($request);
        },
        'permission_callback' => '__return_true',
    ]);

    register_rest_route('myshop/v1', '/orders', [
        'methods'  => 'GET',
        'callback' => function($request) {
            $builder = new Orders_Response_Builder();
            return $builder->build_response($request);
        },
        'permission_callback' => function() {
            return current_user_can('manage_woocommerce');
        },
    ]);
});
```

## Key Concepts

### 1. Polymorphism as the Core Mechanism

The abstract base class calls abstract methods that will be implemented by subclasses, allowing different behaviors while maintaining the same algorithm structure.

### 2. Great Power, Great Responsibility

Template Method gives significant control to the base class. Use it appropriately:

- **Good use**: Consolidating similar behaviors, enforcing security, maintaining temporal coupling
- **Bad use**: Forcing unnecessary constraints, making the template too rigid, overcomplicating simple scenarios

### 3. Hollywood Principle: "Don't Call Us, We'll Call You"

Template Method is a framework pattern. The framework (abstract class) calls the application code (subclass methods), not vice versa.

**Two Code Reuse Techniques:**

1. **Libraries**: Application code delegates to library methods (you call the library)
2. **Frameworks**: Application code implements methods that the framework calls (framework calls you)

Template Method is a framework pattern. Examples include:

- Android Activity lifecycle callbacks (`onCreate()`, `onPause()`, `onResume()`)
- WordPress hooks and filters (WordPress core calls your registered functions)
- Servlet lifecycle methods (`init()`, `service()`, `destroy()`)

### 4. Temporal Coupling

Template Method ensures operations execute in a specific order. The base class controls the sequence, preventing subclasses from executing steps out of order.

Example: In `HotDrink`, you can't pour water before boiling it - the template enforces the correct sequence.

### 5. Consolidating Similar Behaviors

When you discover multiple classes with similar-but-different implementations, Template Method can extract the similarities into a base class while allowing differences to remain in subclasses.

### 6. Don't Abdicate Responsibility

A pet peeve about some OO practices: making everything overridable by default. Template Method provides the best of both worlds:

- Critical behavior (authentication, authorization, ordering) cannot be overridden (private/final methods)
- Customizable behavior can still be overridden (protected abstract methods)

```php
// BAD: Everything overridable - security can be bypassed
class Behavior {
    public function doBehavior(User $user) {
        if (!$this->isAuthenticated($user)) throw new Exception();
        if (!$this->isAuthorized($user)) throw new Exception();
        $this->behavior($user);
    }

    // Subclass could override and bypass security!
    protected function isAuthenticated(User $user) { /* ... */ }
    protected function isAuthorized(User $user) { /* ... */ }
    protected function behavior(User $user) { /* ... */ }
}

// GOOD: Security enforced, behavior customizable
class Behavior {
    final public function doBehavior(User $user) {
        if (!$this->isAuthenticated($user)) throw new Exception();
        if (!$this->isAuthorized($user)) throw new Exception();
        $this->behavior($user);
    }

    // Cannot be overridden - security guaranteed
    private function isAuthenticated(User $user) { /* ... */ }
    private function isAuthorized(User $user) { /* ... */ }

    // Can be overridden - customization allowed
    protected function behavior(User $user) { /* ... */ }
}
```

## When to Use Template Method

### Use Template Method When

1. **Multiple classes share a similar algorithm** but differ in specific steps
2. **You need to enforce a specific execution order** (temporal coupling)
3. **Critical operations must not be overridden** (security, validation, logging)
4. **You're building a framework** where application code needs to plug in at specific points
5. **You want to avoid code duplication** while allowing customization

### Consider Strategy Pattern Instead When

1. **Entire algorithm varies** between implementations (not just steps)
2. **You need runtime algorithm switching** (Strategy can swap, Template Method cannot)
3. **You prefer composition over inheritance**
4. **Base class would be mostly empty** (all behavior in subclasses)

### Avoid Template Method When

1. **There's only one implementation** (no need for abstraction)
2. **Steps have no natural order** (temporal coupling not needed)
3. **Every method is abstract** (consider Strategy instead)
4. **The template is too rigid** (forcing unnecessary constraints)

## Template Method vs Strategy Comparison

Both patterns solve the same problem using polymorphism, but with different approaches:

| Aspect | Template Method | Strategy |
|--------|-----------------|----------|
| **Structure** | Abstract base class | Interface + implementations |
| **Control** | Base class controls algorithm | Client controls which strategy |
| **Implementation** | Partial in base class | Complete in each implementation |
| **Reuse** | Shared code in base class | No shared code (all in implementations) |
| **Flexibility** | Less flexible (inheritance) | More flexible (composition) |
| **Runtime Swap** | No (fixed at instantiation) | Yes (can swap strategies) |
| **Relationship** | IS-A (inheritance) | HAS-A (composition) |

**Converting Between Patterns:**

- **Template Method → Strategy**: Move template method into client, convert abstract protected methods into interface methods
- **Strategy → Template Method**: Move client logic into abstract base class, convert interface methods into abstract protected methods

## Real-World Examples

### WordPress/WooCommerce Examples

1. **WP_REST_Controller**: Abstract base class with template methods for handling REST requests
2. **WP_List_Table**: Template for rendering admin list tables with customizable columns and actions
3. **WP_Widget**: Widget registration and display with customizable form and update methods
4. **WC_Abstract_Order**: Order processing with customizable payment and fulfillment steps
5. **WC_Payment_Gateway**: Payment processing template with customizable payment methods

### Android Example

**Activity Lifecycle**: The Android framework calls lifecycle methods (`onCreate()`, `onStart()`, `onResume()`, `onPause()`, `onStop()`, `onDestroy()`). Developers override these methods to manage resources, but never call them directly.

The Activity state machine controls when these methods are called based on the app's state (visible, hidden, destroyed, etc.).

## Benefits

1. **Code Reuse**: Common algorithm structure defined once in base class
2. **Consistent Structure**: All implementations follow the same pattern
3. **Enforced Ordering**: Steps execute in the correct sequence
4. **Encapsulation**: Implementation details hidden from clients
5. **Extensibility**: New implementations easily added by extending base class
6. **Security**: Critical operations cannot be bypassed
7. **Framework Support**: Natural fit for framework development

## Drawbacks

1. **Inflexibility**: Algorithm structure fixed at design time
2. **Inheritance Required**: Cannot use composition
3. **Limited Customization**: Subclasses can only override designated steps
4. **Potential Over-Engineering**: Can be overkill for simple scenarios
5. **Liskov Substitution Risk**: Must ensure subclasses honor base class contract
6. **Template Method Bloat**: Base class can become complex with many hooks

## Implementation Guidelines

### 1. Make Template Method Final

Prevent subclasses from changing the algorithm structure:

```php
final public function templateMethod(): void
{
    $this->step1();
    $this->step2();
    $this->step3();
}
```

### 2. Use Access Modifiers Appropriately

- **Public**: Template method only
- **Protected**: Abstract methods that subclasses must implement
- **Private**: Fixed behavior that cannot be overridden

### 3. Provide Hook Methods with Default Implementations

Allow optional customization:

```php
protected function optionalHook(): void
{
    // Default implementation - subclasses can override if needed
}
```

### 4. Document the Template Method Algorithm

Clearly explain the algorithm structure and each step's purpose:

```php
/**
 * Template method for processing orders.
 *
 * Algorithm:
 * 1. Validate order data
 * 2. Calculate totals
 * 3. Process payment
 * 4. Update inventory
 * 5. Send confirmation
 *
 * Subclasses must implement:
 * - processPayment(): Payment-gateway-specific logic
 * - sendConfirmation(): Notification-channel-specific logic
 */
final public function processOrder(Order $order): void
{
    $this->validateOrder($order);
    $this->calculateTotals($order);
    $this->processPayment($order);
    $this->updateInventory($order);
    $this->sendConfirmation($order);
}
```

### 5. Validate Subclass Implementations

Consider adding assertions or exceptions to ensure subclasses honor contracts:

```php
abstract protected function calculateTotal(): float;

private function process(): void
{
    $total = $this->calculateTotal();

    if ($total < 0) {
        throw new LogicException(
            get_class($this) . '::calculateTotal() returned negative value'
        );
    }

    // Continue processing...
}
```

### 6. Consider Using Traits for Shared Utilities

If multiple template hierarchies need the same utilities, use traits:

```php
trait SecurityValidation
{
    private function isAuthenticated(WP_User $user): bool
    {
        return is_user_logged_in() && $user->exists();
    }

    private function isAuthorized(WP_User $user): bool
    {
        return user_can($user, $this->getRequiredCapability());
    }

    abstract protected function getRequiredCapability(): string;
}

abstract class SecureDataImporter extends DataImporter
{
    use SecurityValidation;
}
```

## Related Patterns

- **Strategy Pattern**: Both use polymorphism for behavioral variation, but Strategy uses composition while Template Method uses inheritance
- **Factory Method**: A specialized Template Method where the factory method is the hook
- **Command Pattern**: Can be used together - Command encapsulates the request, Template Method defines how to execute it
- **Hook Pattern**: Template Method is essentially a sophisticated hook system
- **Dependency Inversion**: Template Method depends on abstractions (abstract methods) rather than concrete implementations

## Testing Strategy

### Test the Template Method

```php
class HotDrinkTest extends TestCase
{
    public function test_make_recipe_calls_methods_in_correct_order(): void
    {
        $drink = $this->createMock(HotDrink::class);

        $drink->expects($this->exactly(1))
              ->method('brew')
              ->willReturnCallback(function() use (&$callOrder) {
                  $callOrder[] = 'brew';
              });

        $drink->expects($this->exactly(1))
              ->method('addCondiments')
              ->willReturnCallback(function() use (&$callOrder) {
                  $callOrder[] = 'addCondiments';
              });

        $drink->makeRecipe();

        $this->assertEquals(['brew', 'addCondiments'], $callOrder);
    }
}
```

### Test Concrete Implementations

```php
class CoffeeTest extends TestCase
{
    public function test_coffee_adds_sugar_and_milk(): void
    {
        $coffee = new Coffee();

        ob_start();
        $coffee->makeRecipe();
        $output = ob_get_clean();

        $this->assertStringContainsString('Adding sugar and milk', $output);
    }

    public function test_coffee_identifies_correctly(): void
    {
        $coffee = new Coffee();

        // Use reflection to access protected method
        $method = new ReflectionMethod($coffee, 'getDrinkName');
        $method->setAccessible(true);

        $this->assertEquals('Coffee', $method->invoke($coffee));
    }
}
```

### Test Security Enforcement

```php
class DataImporterSecurityTest extends TestCase
{
    public function test_import_rejects_unauthenticated_user(): void
    {
        $this->expectException(Exception::class);
        $this->expectExceptionMessage('User not authenticated');

        $user = $this->createMock(WP_User::class);
        $user->method('exists')->willReturn(false);

        $importer = new ProductImporter();
        $importer->import([], $user);
    }

    public function test_import_rejects_unauthorized_user(): void
    {
        $this->expectException(Exception::class);
        $this->expectExceptionMessage('User not authorized');

        $user = $this->createMock(WP_User::class);
        $user->method('exists')->willReturn(true);

        // Mock user_can() to return false
        WP_Mock::userFunction('user_can', [
            'args'   => [$user, 'import'],
            'return' => false,
        ]);

        $importer = new ProductImporter();
        $importer->import([], $user);
    }
}
```

## References

### Free Resources

- [Wikipedia Template Method Design Pattern](https://en.wikipedia.org/wiki/Template_method_pattern)
- [Source Making Template Method Design Pattern](https://sourcemaking.com/design_patterns/template-method)
- [Refactoring Guru Template Method Design Pattern](https://refactoring.guru/design-patterns/template-method)
- [DoFactory Template Method Design Pattern](https://www.dofactory.com/net/template-method-design-pattern)
- [Project Management Institute Template Method Design Pattern](https://www.pmi.org/disciplined-agile/the-design-patterns-repository/the-template-method-pattern)
- [Hollywood Principle Explained](https://dzone.com/articles/the-hollywood-principle)
- [When to use Template Method vs Strategy](https://stackoverflow.com/questions/672083/when-to-use-template-method-vs-strategy)

### Books

- **Design Patterns: Elements of Reusable Object-Oriented Software** by Gang of Four - Chapter on Template Method
- **Agile Principles, Patterns, and Practices in C#** by Robert C. Martin - Chapter 22
- **Head First Design Patterns** by Freeman & Robson - Chapter 8
- **Clean Code: Design Patterns** by Robert C. Martin - Episode 27

---

*This pattern reference synthesizes insights from James Humelsine's Template Method Design Pattern article.*
