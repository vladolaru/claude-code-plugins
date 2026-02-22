# Composite Pattern

## Quick Reference

| Aspect | Detail |
|--------|--------|
| Intent | Compose objects into tree structures so clients treat leaves and composites uniformly |
| When to Use | Part-whole hierarchies where behavior emerges from tree shape |
| Key Benefit | Client code works identically whether dealing with one object or thousands |

## When to Use

- Representing part-whole hierarchies (file systems, org charts, menu trees)
- Tree structures where recursive composition is natural
- Clients should not need to distinguish between leaf and composite nodes
- Runtime composition of behavior snippets (menus, permissions, product bundles)
- Behavior emerges from the shape and organization of the tree, not from any single node

## When NOT to Use

- Fixed, static HAS-A relationships (Car HAS-A Engine) -- no need for uniform interface
- Simple one-level aggregation that does not recurse
- Leaf behavior differs significantly from composite behavior
- Single core feature with linear enhancements (use Decorator instead)
- You need strict ordering or single-handler dispatch (use Chain of Responsibility)

## WordPress/PHP

### Menu / Navigation Walker

```php
interface MenuComponent {
    public function render(): string;
    public function getItemCount(): int;
}

// Leaf: single menu item
class MenuItem implements MenuComponent {
    public function __construct(
        private string $label,
        private string $url
    ) {}

    public function render(): string {
        return "<li><a href=\"{$this->url}\">{$this->label}</a></li>";
    }

    public function getItemCount(): int {
        return 1;
    }
}

// Composite: submenu containing other items or submenus
class SubMenu implements MenuComponent {
    private array $children = [];

    public function __construct(private string $label) {}

    public function add(MenuComponent $child): void {
        $this->children[] = $child;
    }

    public function render(): string {
        $inner = implode("\n", array_map(
            fn(MenuComponent $c) => $c->render(),
            $this->children
        ));
        return "<li>{$this->label}\n<ul>\n{$inner}\n</ul>\n</li>";
    }

    public function getItemCount(): int {
        return array_sum(array_map(
            fn(MenuComponent $c) => $c->getItemCount(),
            $this->children
        ));
    }
}

// Configurer builds the tree; client just calls render()
$nav = new SubMenu('Main Nav');
$nav->add(new MenuItem('Home', '/'));

$shop = new SubMenu('Shop');
$shop->add(new MenuItem('All Products', '/shop'));
$shop->add(new MenuItem('Sale', '/shop/sale'));
$nav->add($shop);

echo $nav->render();          // recursive render
echo $nav->getItemCount();    // 3
```

**Other WP uses:** Permission trees (AND/OR composites of role/capability checks), WooCommerce product bundles (price/weight aggregated recursively), category hierarchies with aggregate counts.

## JS/TS

### React Component Tree

```tsx
// React's component model is inherently Composite:
// nodes render children uniformly via recursive composition.

interface NavNode {
  label: string;
  href?: string;
  children?: NavNode[];
}

function NavTree({ nodes }: { nodes: NavNode[] }) {
  return (
    <ul>
      {nodes.map((node) =>
        node.children ? (
          <li key={node.label}>
            <span>{node.label}</span>
            <NavTree nodes={node.children} />  {/* Composite: recurse */}
          </li>
        ) : (
          <li key={node.label}>
            <a href={node.href}>{node.label}</a>  {/* Leaf: terminal */}
          </li>
        )
      )}
    </ul>
  );
}

// Data drives tree shape; component renders uniformly
const menu: NavNode[] = [
  { label: 'Home', href: '/' },
  { label: 'Shop', children: [
    { label: 'All Products', href: '/shop' },
    { label: 'Sale', href: '/shop/sale' },
  ]},
];
```

## Common Mistakes

- **WRONG:** Putting `add()` on the Component interface so leaves must throw on it
  **RIGHT:** Only Composite declares `add()`; preserves Liskov Substitution Principle

- **WRONG:** Mixing business logic (discounts, special pricing) into the Composite's aggregation methods
  **RIGHT:** Keep Composite focused on structure; layer business rules via Decorator or a separate service

- **WRONG:** Accepting any structurally valid configuration without validation
  **RIGHT:** Add domain constraints in `add()` (max children, cycle detection, type restrictions)

- **WRONG:** Exposing tree construction details to the client
  **RIGHT:** Use a Configurer (factory/builder) to build the tree; client only calls `execute()` / `render()`

## Relationships

- Composite vs **Decorator** -- Composite is a tree (any depth/width, no core); Decorator is a linear chain around one core
- Composite vs **Chain of Responsibility** -- Composite aggregates all results; CoR dispatches to first matching handler
- Composite vs **Strategy** -- Composite combines many components at runtime; Strategy swaps one algorithm
- Often combined with **Iterator** to traverse the tree uniformly
- Often built by **Factory** or **Builder** patterns acting as Configurers
