# Composite Pattern

Compose objects into tree structures to represent part-whole hierarchies. Composite lets clients treat individual objects and compositions of objects uniformly.

## Quick Reference: When to Use

| Use When | Don't Use When |
|----------|----------------|
| Representing part-whole hierarchies | Fixed, static HAS-A relationships (Car HAS-A Engine) |
| Tree structures (file systems, org charts) | No need for recursive composition |
| Clients should treat leaves and composites uniformly | Leaf behavior differs significantly from composite behavior |
| Runtime composition of behavior snippets | Simple one-level aggregation suffices |
| Aggregating behavior from components | Single core feature with linear decorators (use Decorator) |

**Key Insight**: Composite is about emergence - behavior emerges from the shape and organization of the tree, not from any single node.

---

## Core Concepts

### Structure Components

1. **Component Interface**: Common interface for both leaves and composites
2. **Leaf**: Terminal nodes with atomic behavior (Lego bricks, logic gates, atoms)
3. **Composite**: Non-terminal nodes that contain other components
4. **Configurer**: (Outside pattern) Builds the tree structure - the "brain"

### Tree Terminology

- **Leaf/Terminal/Atomic Node**: Cannot be decomposed further within the domain
- **Composite Node**: Contains other components (leaves or composites)
- **Root**: Entry point for the client
- **Self-referential**: Composites can contain composites (turtles all the way down)

### Key Properties

- No fixed core feature - all nodes are components
- Tree can be one object or thousands
- Tree depth and width determined at runtime
- Client doesn't know if dealing with leaf or composite
- Behavior emerges from configuration, not from individuals

---

## Design Pattern

### Class Structure

```
Component (interface)
├── execute(): Result

Leaf implements Component
└── execute(): Result

Composite implements Component
├── components: List<Component>
├── add(Component): void
├── execute(): Result
    └── for each component: component.execute()
```

### Structural Decisions

**Where should `add()` reside?**

GoF presents a tradeoff:
- **In Component**: Uniform interface (transparency) but unsafe (can't add to Leaf)
- **In Composite**: Type-safe but structure management missing from interface

**Recommended: Place in Composite** for:
- **Separation of concerns**: Client handles computation, Configurer handles coordination
- **Liskov Substitution Principle**: Leaf shouldn't declare behavior it can't implement
- **Safety**: Prevents attempting to add components to leaves

### Typical Object Trees

```
// Simplest: single leaf
client → leaf

// Complex: multiple levels
client → composite
         ├── leaf
         ├── leaf
         └── composite
             ├── leaf
             └── composite
                 └── leaf
```

---

## Real-World Analogies

### Unix Filesystem

- **Files**: Leaf nodes (terminal)
- **Directories**: Composite nodes (can contain files/directories)
- **Everything is a file**: Common interface
- **Recursive commands**: `rm -r` propagates through entire tree

### Lego Sets

- **Individual bricks**: Leaves (limited agency alone)
- **Assemblies**: Composites (engines, cockpit, landing gear)
- **Complete model**: Root composite (Millennium Falcon)
- **Common interface**: Snapping mechanism works across all bricks
- **Emergence**: Form doesn't exist until bricks are assembled

### Logic Gates

- **AND/OR/NOT gates**: Leaf components
- **Full Adder**: Composite of gates
- **Multi-bit Adder**: Composite of Full Adders
- **Computer**: Composite of logical components
- **Meaning by configuration**: Gates only have meaning in specific arrangements

### Biological Composition

```
Organism (composite)
└── Systems (composites) - nervous, circulatory, pulmonary
    └── Tissues (composites) - nerves, muscle, arteries
        └── Cells (composites)
            └── Organelles (composites)
                └── Molecules (composites)
                    └── Atoms (leaves in biology domain)
```

**When to stop decomposing?**
Stop at the atomic behavior snippet within your domain.

> "If you wish to make an apple pie from scratch, you must first invent the universe." - Carl Sagan

But your recipe doesn't need to start with "Trigger the Big Bang."

---

## PHP Implementation: Fast Food Menu

### Problem: Secret Menu System

In-N-Out Burger offers customizable items:
- **3x3**: Triple cheeseburger
- **4x4**: Quadruple cheeseburger
- **Flying Dutchman**: Bun-less double cheeseburger
- **Protein Style**: Lettuce-wrapped burger
- **Animal Fries**: Fries topped with burger condiments
- **Cheese Fries**: Self-explanatory
- **Roadkill Fries**: Animal Fries topped with Flying Dutchman

### Design Evolution

#### Version 1: Basic Composite (Hardcoded)

```php
interface FoodItem {
    public function getCalories(): int;
}

// Leaf nodes - atomic ingredients
class Burger implements FoodItem {
    public function getCalories(): int {
        return 250;
    }
}

class Cheese implements FoodItem {
    public function getCalories(): int {
        return 100;
    }
}

class Bun implements FoodItem {
    public function getCalories(): int {
        return 150;
    }
}

class Lettuce implements FoodItem {
    public function getCalories(): int {
        return 5;
    }
}

class Tomato implements FoodItem {
    public function getCalories(): int {
        return 10;
    }
}

class Pickle implements FoodItem {
    public function getCalories(): int {
        return 5;
    }
}

class Onions implements FoodItem {
    public function getCalories(): int {
        return 15;
    }
}

class Fries implements FoodItem {
    public function getCalories(): int {
        return 400;
    }
}

class Mustard implements FoodItem {
    public function getCalories(): int {
        return 10;
    }
}
```

#### Composite Implementation

```php
class FoodComposite implements FoodItem {
    /** @var FoodItem[] */
    private array $components = [];

    public function add(FoodItem $component): void {
        $this->components[] = $component;
    }

    public function getCalories(): int {
        $total = 0;
        foreach ($this->components as $component) {
            $total += $component->getCalories();
        }
        return $total;
    }
}
```

#### Predefined Menu Items

```php
class Cheeseburger implements FoodItem {
    private FoodComposite $composite;

    public function __construct() {
        // Acts as Configurer
        $this->composite = new FoodComposite();
        $this->composite->add(new Burger());
        $this->composite->add(new Cheese());
        $this->composite->add(new Bun());
        $this->composite->add(new Lettuce());
        $this->composite->add(new Tomato());
        $this->composite->add(new Onions());
        $this->composite->add(new Pickle());
    }

    public function getCalories(): int {
        return $this->composite->getCalories();
    }
}

class AnimalFries implements FoodItem {
    private FoodComposite $composite;

    public function __construct() {
        $this->composite = new FoodComposite();
        $this->composite->add(new Fries());
        $this->composite->add(new Mustard());
        $this->composite->add(new Onions());
        $this->composite->add(new Cheese());
    }

    public function getCalories(): int {
        return $this->composite->getCalories();
    }
}

class FlyingDutchman implements FoodItem {
    private FoodComposite $composite;

    public function __construct() {
        $this->composite = new FoodComposite();
        $this->composite->add(new Burger());
        $this->composite->add(new Burger());
        $this->composite->add(new Cheese());
        $this->composite->add(new Cheese());
    }

    public function getCalories(): int {
        return $this->composite->getCalories();
    }
}
```

#### Composites of Composites

```php
// Option 1: Decompose to ingredients
class RoadkillFries1 implements FoodItem {
    private FoodComposite $composite;

    public function __construct() {
        $this->composite = new FoodComposite();
        // Animal Fries components
        $this->composite->add(new Fries());
        $this->composite->add(new Mustard());
        $this->composite->add(new Onions());
        $this->composite->add(new Cheese());
        // Flying Dutchman components
        $this->composite->add(new Burger());
        $this->composite->add(new Burger());
        $this->composite->add(new Cheese());
        $this->composite->add(new Cheese());
    }

    public function getCalories(): int {
        return $this->composite->getCalories();
    }
}

// Option 2: Compose from existing items (preferred)
class RoadkillFries2 implements FoodItem {
    private FoodComposite $composite;

    public function __construct() {
        $this->composite = new FoodComposite();
        $this->composite->add(new AnimalFries());
        $this->composite->add(new FlyingDutchman());
    }

    public function getCalories(): int {
        return $this->composite->getCalories();
    }
}
```

**Key Insight**: Option 2 leverages the self-referential nature of Composite - composites can contain composites.

#### Version 2: Dynamic Configuration (Factory)

For runtime flexibility without creating a class per menu item:

```php
class FoodItemFactory {
    public static function acquire(string $name): FoodItem {
        return match ($name) {
            'Burger' => new Burger(),
            'Cheese' => new Cheese(),
            'Bun' => new Bun(),
            'Lettuce' => new Lettuce(),
            'Tomato' => new Tomato(),
            'Pickle' => new Pickle(),
            'Onions' => new Onions(),
            'Fries' => new Fries(),
            'Mustard' => new Mustard(),
            default => self::buildFromSpec($name),
        };
    }

    private static function buildFromSpec(string $name): FoodItem {
        $spec = FoodItemSpec::getIngredients($name);
        return new FoodItemBuilder($spec);
    }
}

class FoodItemSpec {
    private static array $specs = [
        '3x3' => ['Burger', 'Burger', 'Burger', 'Cheese', 'Cheese', 'Cheese',
                  'Bun', 'Lettuce', 'Tomato', 'Onions', 'Pickle'],
        '4x4' => ['Burger', 'Burger', 'Burger', 'Burger', 'Cheese', 'Cheese',
                  'Cheese', 'Cheese', 'Bun', 'Lettuce', 'Tomato', 'Onions', 'Pickle'],
        'FlyingDutchman' => ['Burger', 'Burger', 'Cheese', 'Cheese'],
        'ProteinStyle' => ['Burger', 'Lettuce'],
        'AnimalFries' => ['Fries', 'Mustard', 'Onions', 'Cheese'],
        'CheeseFries' => ['Fries', 'Cheese'],
        'RoadkillFries' => ['AnimalFries', 'FlyingDutchman'],
    ];

    public static function getIngredients(string $name): array {
        if (!isset(self::$specs[$name])) {
            throw new InvalidArgumentException("Unknown food item: {$name}");
        }
        return self::$specs[$name];
    }
}

class FoodItemBuilder implements FoodItem {
    private FoodComposite $composite;

    public function __construct(array $ingredientNames) {
        $this->composite = new FoodComposite();

        foreach ($ingredientNames as $name) {
            $item = FoodItemFactory::acquire($name);
            $this->composite->add($item);
        }
    }

    public function getCalories(): int {
        return $this->composite->getCalories();
    }
}
```

#### Usage

```php
// Hardcoded classes
$burger = new Cheeseburger();
echo $burger->getCalories(); // 535

$fries = new RoadkillFries2();
echo $fries->getCalories(); // 940

// Dynamic factory
$item = FoodItemFactory::acquire('3x3');
echo $item->getCalories(); // 1285

$custom = FoodItemFactory::acquire('RoadkillFries');
echo $custom->getCalories(); // 940

// Recursive resolution
// RoadkillFries => AnimalFries|FlyingDutchman
// AnimalFries => Fries|Mustard|Onions|Cheese
// FlyingDutchman => Burger|Burger|Cheese|Cheese
// Factory recursively builds tree from specs
```

---

## Computation and Coordination

### Separation of Concerns

| Role | Responsibility | Tools |
|------|---------------|-------|
| **Client** | Uses behavior | `component.execute()` |
| **Configurer** | Builds structure | `composite.add(component)` |

Client doesn't know or care about tree construction. Configurer doesn't know about execution.

### Types of Configurers

1. **Developer Configurer**
   - Domain expert with system knowledge
   - May have written Composite implementation
   - Example: Logic gate assemblies

2. **Customer Consultant**
   - Trained support staff
   - Configures on behalf of customers
   - Needs UI/UX wrapper (not a developer)
   - Works with dev team to understand composition

3. **Customer/User**
   - Self-service configuration
   - Requires user-friendly UI/UX
   - Achieves goals without support

---

## Multiple Behaviors in One Composite

The food example focused on calories, but the same structure supports multiple behaviors:

```php
interface FoodItem {
    public function getCalories(): int;
    public function getPrice(): float;
    public function getLabel(): string;
}

class Burger implements FoodItem {
    public function getCalories(): int { return 250; }
    public function getPrice(): float { return 2.50; }
    public function getLabel(): string { return 'Beef Patty'; }
}

class FoodComposite implements FoodItem {
    private array $components = [];

    public function add(FoodItem $component): void {
        $this->components[] = $component;
    }

    public function getCalories(): int {
        return array_sum(
            array_map(fn($c) => $c->getCalories(), $this->components)
        );
    }

    public function getPrice(): float {
        return array_sum(
            array_map(fn($c) => $c->getPrice(), $this->components)
        );
    }

    public function getLabel(): string {
        return implode(', ',
            array_map(fn($c) => $c->getLabel(), $this->components)
        );
    }
}

// Usage
$burger = new Cheeseburger();
echo "Calories: " . $burger->getCalories() . "\n";  // 535
echo "Price: $" . $burger->getPrice() . "\n";        // $4.85
echo "Contains: " . $burger->getLabel() . "\n";      // Beef Patty, Cheese, Bun...
```

---

## WordPress/WooCommerce Examples

### Product Bundle with Options

```php
interface BundleComponent {
    public function getPrice(): float;
    public function getWeight(): float;
    public function getDescription(): string;
}

class SimpleProduct implements BundleComponent {
    private WC_Product $product;

    public function __construct(WC_Product $product) {
        $this->product = $product;
    }

    public function getPrice(): float {
        return (float) $this->product->get_price();
    }

    public function getWeight(): float {
        return (float) $this->product->get_weight();
    }

    public function getDescription(): string {
        return $this->product->get_name();
    }
}

class ProductOption implements BundleComponent {
    private string $name;
    private float $price;
    private float $weight;

    public function __construct(string $name, float $price, float $weight) {
        $this->name = $name;
        $this->price = $price;
        $this->weight = $weight;
    }

    public function getPrice(): float {
        return $this->price;
    }

    public function getWeight(): float {
        return $this->weight;
    }

    public function getDescription(): string {
        return $this->name;
    }
}

class ProductBundle implements BundleComponent {
    private array $components = [];

    public function add(BundleComponent $component): void {
        $this->components[] = $component;
    }

    public function getPrice(): float {
        return array_sum(
            array_map(fn($c) => $c->getPrice(), $this->components)
        );
    }

    public function getWeight(): float {
        return array_sum(
            array_map(fn($c) => $c->getWeight(), $this->components)
        );
    }

    public function getDescription(): string {
        return implode(' + ',
            array_map(fn($c) => $c->getDescription(), $this->components)
        );
    }
}

// Usage
$bundle = new ProductBundle();
$bundle->add(new SimpleProduct($laptop));
$bundle->add(new ProductOption('Extended Warranty', 99.99, 0));
$bundle->add(new ProductOption('Laptop Bag', 29.99, 0.5));

echo "Bundle: " . $bundle->getDescription() . "\n";
echo "Total: $" . $bundle->getPrice() . "\n";
echo "Weight: " . $bundle->getWeight() . " kg\n";
```

### Category Tree Navigation

```php
interface CategoryComponent {
    public function getProductCount(): int;
    public function getSubcategoryCount(): int;
    public function getName(): string;
}

class ProductCategory implements CategoryComponent {
    private WP_Term $term;

    public function __construct(WP_Term $term) {
        $this->term = $term;
    }

    public function getProductCount(): int {
        return $this->term->count;
    }

    public function getSubcategoryCount(): int {
        $children = get_term_children($this->term->term_id, 'product_cat');
        return count($children);
    }

    public function getName(): string {
        return $this->term->name;
    }
}

class CategoryGroup implements CategoryComponent {
    private array $categories = [];

    public function add(CategoryComponent $category): void {
        $this->categories[] = $category;
    }

    public function getProductCount(): int {
        return array_sum(
            array_map(fn($c) => $c->getProductCount(), $this->categories)
        );
    }

    public function getSubcategoryCount(): int {
        return array_sum(
            array_map(fn($c) => $c->getSubcategoryCount(), $this->categories)
        );
    }

    public function getName(): string {
        return 'Category Group';
    }
}
```

### Permission Tree

```php
interface PermissionComponent {
    public function canAccess(WP_User $user): bool;
    public function getDescription(): string;
}

class RolePermission implements PermissionComponent {
    private string $role;
    private string $description;

    public function __construct(string $role, string $description) {
        $this->role = $role;
        $this->description = $description;
    }

    public function canAccess(WP_User $user): bool {
        return in_array($this->role, $user->roles, true);
    }

    public function getDescription(): string {
        return $this->description;
    }
}

class CapabilityPermission implements PermissionComponent {
    private string $capability;
    private string $description;

    public function __construct(string $capability, string $description) {
        $this->capability = $capability;
        $this->description = $description;
    }

    public function canAccess(WP_User $user): bool {
        return $user->has_cap($this->capability);
    }

    public function getDescription(): string {
        return $this->description;
    }
}

class CompositePermission implements PermissionComponent {
    private array $permissions = [];
    private string $operator; // 'AND' or 'OR'

    public function __construct(string $operator = 'AND') {
        $this->operator = $operator;
    }

    public function add(PermissionComponent $permission): void {
        $this->permissions[] = $permission;
    }

    public function canAccess(WP_User $user): bool {
        if (empty($this->permissions)) {
            return false;
        }

        if ($this->operator === 'OR') {
            foreach ($this->permissions as $permission) {
                if ($permission->canAccess($user)) {
                    return true;
                }
            }
            return false;
        }

        // AND
        foreach ($this->permissions as $permission) {
            if (!$permission->canAccess($user)) {
                return false;
            }
        }
        return true;
    }

    public function getDescription(): string {
        $descriptions = array_map(
            fn($p) => $p->getDescription(),
            $this->permissions
        );
        return '(' . implode(" {$this->operator} ", $descriptions) . ')';
    }
}

// Usage
$adminAccess = new CompositePermission('OR');
$adminAccess->add(new RolePermission('administrator', 'Admin Role'));
$adminAccess->add(new CapabilityPermission('manage_options', 'Manage Options'));

$shopManagerAccess = new CompositePermission('AND');
$shopManagerAccess->add(new RolePermission('shop_manager', 'Shop Manager'));
$shopManagerAccess->add(new CapabilityPermission('edit_shop_orders', 'Edit Orders'));

$fullAccess = new CompositePermission('OR');
$fullAccess->add($adminAccess);
$fullAccess->add($shopManagerAccess);

if ($fullAccess->canAccess($current_user)) {
    // Grant access
}
```

---

## Comparison with Other Patterns

### vs Decorator

| Aspect | Composite | Decorator |
|--------|-----------|-----------|
| Structure | Tree (any depth/width) | Linear list |
| Core | No single core | One core feature |
| Purpose | Aggregate behaviors | Enhance one behavior |
| Metaphor | In-N-Out Burger (any combo) | Burger King (core + toppings) |

**Example**:
- **Decorator**: Burger + [Cheese, Lettuce, Tomato] - always has burger base
- **Composite**: Can be Fries + [Cheese, Onions] - no required core

### vs Chain of Responsibility

| Aspect | Composite | Chain of Responsibility |
|--------|-----------|------------------------|
| Structure | Tree | Linear chain |
| Execution | All nodes contribute | First match wins |
| Return | Aggregated result | Single handler result |
| Purpose | Combine behaviors | Select handler |

### vs Strategy

| Aspect | Composite | Strategy |
|--------|-----------|----------|
| Structure | Tree of components | Single algorithm swap |
| Runtime | Build tree structure | Choose algorithm |
| Complexity | Many objects | One object |

---

## Pros and Cons

### Pros

1. **Flexible Composition**
   - Support any number of composable configurations
   - As few or many objects as needed
   - Each configuration has specific behavior

2. **Easy Construction**
   - Relatively simple to compose objects
   - Multiple Configurer types possible:
     - Developer (domain + system expertise)
     - Consultant (trained, uses UI/UX)
     - Customer (self-service with UI/UX)

3. **Uniform Treatment**
   - Client treats leaves and composites the same
   - Simplifies client code

4. **Scalable**
   - Add new leaf types without changing existing code
   - Add new composite types easily

### Cons

1. **Configuration Complexity**
   - All structure, behavior from composition
   - Composite accepts any structurally valid configuration
   - **No guarantee configuration makes logical sense**
   - Like random Lego bricks snapped together

2. **Configuration Bugs**
   - Configurer can create logical bugs in structure
   - Users blame implementation before admitting configuration error
   - "The fault, dear Brutus, lies not in our stars, but in ourselves"

3. **Testing Challenges**
   - Integration tests can't catch user configuration errors
   - Need validation layer for user configurations
   - May need documentation or templates for valid patterns

4. **Overly General**
   - Can represent things that shouldn't be represented
   - May need domain-specific constraints
   - Consider adding validation in Composite.add()

---

## When to Stop Decomposing

### The Universe Problem

> "If you wish to make an apple pie from scratch, you must first invent the universe." - Carl Sagan

Technically true, but impractical. Apple pie recipe doesn't start with "Trigger the Big Bang."

### Stop Criteria

Stop decomposing when you've reached **atomic behavior within your domain**:

| Domain | Atomic Unit |
|--------|-------------|
| Cooking | Ingredients (not molecules) |
| Electronics | Logic gates (not quarks) |
| Biology | Cells/molecules (not subatomic particles) |
| File system | Files (not disk sectors) |
| UI components | Buttons/inputs (not pixels) |

**Rule**: Stop at the smallest meaningful behavior snippet for your use case.

---

## Common Pitfalls

### 1. Mixing Concerns

```php
// Wrong: Structure and behavior mixed
class FoodComposite implements FoodItem {
    private array $components = [];
    private float $discount = 0; // Business logic leaking in

    public function getPrice(): float {
        $total = array_sum(array_map(fn($c) => $c->getPrice(), $this->components));
        return $total * (1 - $this->discount); // Wrong place for this
    }
}

// Right: Keep composite focused on structure
class FoodComposite implements FoodItem {
    private array $components = [];

    public function getPrice(): float {
        return array_sum(
            array_map(fn($c) => $c->getPrice(), $this->components)
        );
    }
}

// Apply discounts in a wrapper or separate concern
class DiscountedItem implements FoodItem {
    private FoodItem $item;
    private float $discount;

    public function __construct(FoodItem $item, float $discount) {
        $this->item = $item;
        $this->discount = $discount;
    }

    public function getPrice(): float {
        return $this->item->getPrice() * (1 - $this->discount);
    }
}
```

### 2. Forgetting the Client Perspective

```php
// Wrong: Exposing structure to client
$burger = new FoodComposite();
$burger->add(new Burger());
$burger->add(new Cheese());
// ... client knows too much

// Right: Client only sees interface
$burger = FoodItemFactory::acquire('Cheeseburger');
echo $burger->getCalories(); // Client doesn't know structure
```

### 3. Inappropriate Leaf Methods

```php
// Wrong: Leaf implements composite methods
class Burger implements FoodItem {
    public function getCalories(): int {
        return 250;
    }

    public function add(FoodItem $item): void {
        throw new LogicException("Cannot add to Burger"); // Violation
    }
}

// Right: Only Composite has add
// Burger doesn't declare or implement add()
```

### 4. Not Validating Configuration

```php
// Wrong: Accept any configuration
class ProductBundle implements BundleComponent {
    public function add(BundleComponent $component): void {
        $this->components[] = $component; // No validation
    }
}

// Better: Add domain constraints
class ProductBundle implements BundleComponent {
    private const MAX_COMPONENTS = 10;

    public function add(BundleComponent $component): void {
        if (count($this->components) >= self::MAX_COMPONENTS) {
            throw new InvalidArgumentException('Bundle cannot exceed 10 items');
        }
        if ($this->wouldCreateCycle($component)) {
            throw new InvalidArgumentException('Cannot add component - creates cycle');
        }
        $this->components[] = $component;
    }

    private function wouldCreateCycle(BundleComponent $component): bool {
        // Check for circular references
        return $component === $this ||
               ($component instanceof ProductBundle &&
                $component->contains($this));
    }
}
```

---

## Implementation Checklist

- [ ] Define common `Component` interface
- [ ] Create `Leaf` classes for atomic behaviors
- [ ] Implement `Composite` with component list
- [ ] Place structural methods (`add`) only in `Composite`
- [ ] Ensure `Composite` propagates through all components
- [ ] Create `Configurer` to build tree structures
- [ ] Consider factory for dynamic composition
- [ ] Add validation for domain constraints
- [ ] Document valid configuration patterns
- [ ] Test edge cases (empty composite, single leaf, deep nesting)
- [ ] Prevent circular references if necessary
- [ ] Consider multiple behaviors in same structure

---

## Further Reading

- **Gang of Four**: Design Patterns, p. 163
- **Head First Design Patterns**: Chapter 9 (O'Reilly)
- **Agile Principles, Patterns, and Practices in C#**: Chapter 31
- [Wikipedia: Composite Pattern](https://en.wikipedia.org/wiki/Composite_pattern)
- [Refactoring Guru: Composite](https://refactoring.guru/design-patterns/composite)

---

## Summary

Composite lets you build tree structures where:
- **Leaves** provide atomic behavior snippets
- **Composites** aggregate behavior from children
- **Behavior emerges** from tree shape and organization
- **Clients** treat leaves and composites uniformly
- **Configurers** determine what behavior emerges

**Core Truth**: The whole is greater than the sum of its parts - but only when the parts are composed correctly.
