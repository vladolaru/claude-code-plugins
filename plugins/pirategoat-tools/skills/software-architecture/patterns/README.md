# Software Architecture Patterns Library

Directory index for design patterns and architectural principles.

## File Listing

### Architectural Patterns

- **[architectural/hexagonal-architecture.md](./architectural/hexagonal-architecture.md)** -- Ports and Adapters architecture

### Creational Patterns

- **[creational/factory.md](./creational/factory.md)** -- Encapsulate object creation logic
- **[creational/dependency-injection.md](./creational/dependency-injection.md)** -- Invert control of dependencies

### Structural Patterns

- **[structural/adapter.md](./structural/adapter.md)** -- Interface compatibility layer
- **[structural/composite.md](./structural/composite.md)** -- Tree structures and recursive composition
- **[structural/decorator.md](./structural/decorator.md)** -- Add behavior without inheritance
- **[structural/facade.md](./structural/facade.md)** -- Simplified interface to complex subsystems
- **[structural/proxy.md](./structural/proxy.md)** -- Control access to objects

### Behavioral Patterns

- **[behavioral/strategy.md](./behavioral/strategy.md)** -- Interchangeable algorithms
- **[behavioral/command.md](./behavioral/command.md)** -- Encapsulate requests as objects
- **[behavioral/template-method.md](./behavioral/template-method.md)** -- Algorithm skeleton with hooks
- **[behavioral/chain-of-responsibility.md](./behavioral/chain-of-responsibility.md)** -- Pass requests along a chain
- **[behavioral/specification.md](./behavioral/specification.md)** -- Composable business rule filters

## Problem -> Pattern Decision Table

| Problem | Pattern |
|---------|---------|
| Hard to swap implementations | Factory, Dependency Injection |
| Complex object creation | Builder, Factory Method |
| Incompatible interfaces | Adapter, Facade |
| Add behavior dynamically | Decorator, Strategy |
| Algorithm varies by context | Strategy, State |
| Giant if/else or switch | Strategy, State, Chain of Responsibility |
| Steps fixed, details vary | Template Method |
| Need to encapsulate requests / undo | Command |
| Event notifications | Observer |
| Tree structures | Composite |
| Memory optimization (many similar objects) | Flyweight |
| Lazy loading / access control | Proxy |
| Separate abstraction from implementation | Bridge |
| Complex many-to-many interactions | Mediator |
| State capture and restoration | Memento |

## Pattern Relationships

### Build-On-Each-Other Chains

| Chain | Progression |
|-------|-------------|
| Object creation | Factory -> Abstract Factory -> Builder |
| Behavior encapsulation | Strategy -> State -> Command |
| Architecture | Layered -> Hexagonal -> Clean Architecture |
| Wrapping | Decorator -> Chain of Responsibility |

### Patterns That Pair Well

| Combination | Why |
|-------------|-----|
| DI + Factory | DI provides factories to classes; factories use DI to resolve deps |
| Strategy + Factory | Factory selects appropriate strategy at runtime |
| Observer + Mediator | Mediator centralizes observer management |
| Adapter + Facade | Adapter converts interfaces; Facade unifies them |
| Decorator + Composite | Decorator adds behavior to leaves; Composite structures the tree |
| Command + Memento | Command encapsulates ops; Memento stores state for undo |
| Template Method + Strategy | Template defines structure; Strategy provides pluggable steps |

### Patterns Solving Similar Problems

| Dimension | Alternatives |
|-----------|-------------|
| Inheritance vs Composition | Template Method (rigid structure) vs Strategy (flexible) vs Decorator (composable) |
| Object creation | Factory (single type) vs Abstract Factory (families) vs Builder (complex) vs Prototype (clone) |
| Interface adaptation | Adapter (convert) vs Facade (simplify) vs Bridge (separate hierarchies) |
| Request handling | Chain of Responsibility (pipeline) vs Command (encapsulate) vs Strategy (select handler) |

## Common Pattern Stacks

| Stack | Patterns | Use Case |
|-------|----------|----------|
| Classic Web | Layered + DI + Repository (Adapter) | Standard web app |
| DDD | Hexagonal + Factory + Strategy + Observer | Domain-driven design |
| Extension Framework | Plugin + Factory + Strategy + Observer | Extensible plugin systems |
| Command Pipeline | Chain of Responsibility + Command + Strategy | Middleware processing |
| UI Components | Composite + Decorator + Observer | Component architectures |
| Data Access | Adapter + Proxy + Factory | Database abstraction |

## Anti-Patterns to Avoid

- **Over-engineering** -- apply patterns when pain points emerge, not before
- **Pattern obsession** -- ask "what problem does this solve?" not "which pattern is proper?"
- **Singleton abuse** -- use DI with singleton scope instead
- **Deep inheritance** -- prefer composition (Strategy, Decorator, Composite)
- **Premature abstraction** -- wait for 3 examples; wrong abstractions are worse than duplication
- **Pattern mixing** -- max 1-2 patterns per component
