---
name: software-architecture
description: Use when designing software solutions, choosing design patterns, refactoring toward better architecture, or facing tight coupling, rigid designs, or unclear responsibilities (covers GoF patterns, SOLID principles, hexagonal architecture, and composable designs)
---

# Software Architect

Comprehensive guide for designing maintainable, extensible, and testable software systems. Focuses on design patterns, architectural principles, and refactoring strategies to build composable, loosely-coupled systems.

## When to Use This Skill

Use this skill when:
- Designing new features or systems
- Refactoring tightly coupled code
- Code has too many responsibilities (God objects)
- Hard to test code (tight coupling, hidden dependencies)
- Need to make code extensible without modifying existing code
- Rigid designs that resist change
- Choosing between design patterns
- Breaking down complex systems into components
- Addressing architectural code smells

**NOT for:** Learning what specific patterns are in abstract terms - use this when you have a concrete design problem to solve.

## Architectural Philosophy (Core Mindset)

**Critical shift:** Code is not just "working" vs "broken" - it exists on a spectrum from rigid/fragile to flexible/maintainable.

| Principle | Meaning |
|-----------|---------|
| **Design for change** | Requirements will evolve. Build systems that accommodate change without major rewrites. |
| **Composition over inheritance** | Build complex behavior from simple, reusable pieces rather than rigid hierarchies. |
| **Dependencies point inward** | High-level policy should not depend on low-level details. Details depend on abstractions. |
| **Explicit over implicit** | Make dependencies, behaviors, and responsibilities visible in the code structure. |

**If you think patterns are boilerplate → you'll over-engineer.**
**If you think patterns solve flexibility problems → you'll apply them strategically.**

## Core Principles (The Foundation)

### The Two GoF Principles

Every design pattern derives from these two principles:

| Principle | Meaning | Violation Symptom |
|-----------|---------|-------------------|
| **Program to interface, not implementation** | Depend on abstractions (interfaces), not concrete classes | Changes in one class break many others |
| **Favor composition over inheritance** | Build behavior by combining objects, not extending classes | Deep inheritance hierarchies, brittle hierarchies |

### SOLID Principles (Quick Reference)

| Principle | Description | Violation Symptom |
|-----------|-------------|-------------------|
| **Single Responsibility** | A class should have one reason to change | Changes in one feature require modifying the class |
| **Open/Closed** | Open for extension, closed for modification | Adding features requires changing existing code |
| **Liskov Substitution** | Subtypes must be substitutable for base types | Conditional checks for specific subtypes |
| **Interface Segregation** | Many specific interfaces > one general interface | Clients forced to depend on methods they don't use |
| **Dependency Inversion** | Depend on abstractions, not concretions | High-level logic directly instantiates low-level classes |

**→ Deep dive:** See `patterns/architectural/solid-principles.md` for detailed explanations, examples, and refactoring strategies.

## Pattern Selection Guide (Decision Matrix)

When facing a design problem, use this table to identify pattern categories:

| Problem/Symptom | Root Cause | Pattern Category | Specific Patterns |
|-----------------|------------|------------------|-------------------|
| **Hard to swap implementations** | Tight coupling to concrete classes | Creational | Factory, Abstract Factory, Dependency Injection |
| **Complex object creation** | Constructor with too many parameters | Creational | Builder, Factory Method |
| **Need single instance** | Uncontrolled object creation | Creational | Singleton (use sparingly) |
| **Incompatible interfaces** | Third-party/legacy code doesn't match your interface | Structural | Adapter, Façade |
| **Complex subsystem** | Too many moving parts exposed to clients | Structural | Façade |
| **Need to add behavior dynamically** | Static inheritance too rigid | Structural | Decorator, Strategy |
| **Algorithm varies by context** | Conditional logic selecting behavior | Behavioral | Strategy, State |
| **Need to encapsulate requests** | Direct method calls, no undo/logging/queuing | Behavioral | Command |
| **Need common interface for tree/leaf** | Treating individual objects differently from collections | Structural | Composite |
| **Many objects observing state** | Tight coupling between state holder and observers | Behavioral | Observer |
| **Steps are fixed, details vary** | Duplicated algorithm structure | Behavioral | Template Method |
| **Traverse collection without exposing internals** | Direct access to internal structures | Behavioral | Iterator |
| **Complex conditional logic** | Giant if/else or switch statements | Behavioral | Strategy, State, Chain of Responsibility |
| **Need to coordinate complex workflow** | Scattered orchestration logic | Behavioral | Command, Mediator |

## Essential Patterns: DEMS D'FFACTS (Quick Reference)

The most frequently useful patterns for solving common design problems:

### Behavioral Patterns (DEMS)

| Pattern | Purpose | When to Use | When NOT to Use |
|---------|---------|-------------|-----------------|
| **Command** | Encapsulate request as object | Undo/redo, queuing, logging, macro commands | Simple direct method calls suffice |
| **Strategy** | Encapsulate interchangeable algorithms | Algorithm varies independently of client | Only one algorithm, no variation needed |
| **Template Method** | Define algorithm skeleton, defer steps to subclasses | Invariant structure, variant details | Need runtime algorithm switching (use Strategy) |
| **State** | Object behavior changes based on internal state | Complex state-dependent behavior | Simple boolean flag suffices |

### Structural Patterns (D'F)

| Pattern | Purpose | When to Use | When NOT to Use |
|---------|---------|-------------|-----------------|
| **Decorator** | Add responsibilities to objects dynamically | Need flexible combinations of features | Simple static subclassing works |
| **Façade** | Unified interface to subsystem | Simplify complex subsystem for common cases | Need fine-grained control over subsystem |

### Creational Patterns (FACTS)

| Pattern | Purpose | When to Use | When NOT to Use |
|---------|---------|-------------|-----------------|
| **Factory Method** | Defer instantiation to subclasses | Subclass decides which class to instantiate | Only one concrete class, no variation |
| **Abstract Factory** | Create families of related objects | Need consistency across product families | No related product families |
| **Dependency Injection** | Provide dependencies from outside | Enable testing, loose coupling | Dependencies never vary |

**Mnemonic:** **D**ecorator **E**ncapsulates **M**odifications **S**implify - **D**irectly **F**açading **F**lexible **A**bstractions **C**reating **T**estable **S**ystems

**→ Deep dives:** See pattern category directories for complete implementations, use cases, and trade-offs.

## Pattern Categories (Overview)

### Behavioral Patterns

Concerned with algorithms and assignment of responsibilities between objects.

**Core patterns:** Strategy, Command, Template Method, Observer, State, Chain of Responsibility, Visitor, Iterator, Mediator, Memento, Interpreter

**Common theme:** Make algorithms and responsibilities flexible and reusable.

**→ See:** `patterns/behavioral/` for detailed guides on each pattern.

### Structural Patterns

Concerned with how classes and objects are composed to form larger structures.

**Core patterns:** Adapter, Bridge, Composite, Decorator, Façade, Flyweight, Proxy

**Common theme:** Simplify relationships between entities, make structures flexible.

**→ See:** `patterns/structural/` for detailed guides on each pattern.

### Creational Patterns

Concerned with object creation mechanisms.

**Core patterns:** Factory Method, Abstract Factory, Builder, Prototype, Singleton, Dependency Injection

**Common theme:** Make object creation flexible and decoupled from usage.

**→ See:** `patterns/creational/` for detailed guides on each pattern.

### Architectural Patterns

Higher-level patterns that shape entire system structure.

**Core patterns:** Hexagonal Architecture (Ports & Adapters), Layered Architecture, Domain-Driven Design, Event-Driven Architecture

**Common theme:** Organize code for maintainability, testability, and clear boundaries.

**→ See:** `patterns/architectural/` for detailed guides on each pattern.

## Common Architectural Problems (Troubleshooting)

When reviewing code, these symptoms indicate architectural issues:

| Symptom | Root Cause | Pattern Solution | SOLID Violation |
|---------|------------|------------------|-----------------|
| **God Object** (class does everything) | Too many responsibilities | Split into smaller classes, use Façade | Single Responsibility |
| **Tight coupling** (changes ripple widely) | Direct dependencies on concrete classes | Dependency Injection, Abstract Factory | Dependency Inversion |
| **Rigid design** (hard to add features) | Modification required for extension | Strategy, Template Method, Decorator | Open/Closed |
| **Hard to test** (can't mock dependencies) | Hidden dependencies, static calls | Dependency Injection | Dependency Inversion |
| **Fragile base class** (subclass breaks when base changes) | Improper inheritance | Composition, Strategy, Decorator | Liskov Substitution |
| **Complex conditionals** (giant if/else, switch) | Algorithm selection in client code | Strategy, State, Chain of Responsibility | Open/Closed |
| **Code duplication** (same logic in multiple places) | Missing abstraction | Template Method, Strategy | DRY principle |
| **Shotgun surgery** (one change requires many edits) | Scattered responsibility | Façade, Mediator | Single Responsibility |
| **Feature envy** (method uses more of another class) | Wrong responsibility placement | Move method, Extract class | - |
| **Primitive obsession** (too many primitive parameters) | Missing domain objects | Value Objects, Builder | - |
| **Long parameter lists** | Too many dependencies, unclear intent | Builder, Introduce Parameter Object | - |
| **Refused bequest** (subclass doesn't need parent methods) | Wrong inheritance hierarchy | Composition over inheritance | Interface Segregation |

**Key insight:** Architectural problems are design smells. Patterns are the refactoring solutions.

**→ Deep dive:** See `patterns/architectural/code-smells.md` for complete catalog of smells, their root causes, and refactoring strategies.

## Design Pattern Combinations (Synergistic Patterns)

Patterns often work together. These combinations are especially powerful:

| Primary Pattern | Complementary Pattern | Purpose |
|-----------------|----------------------|---------|
| **Strategy** | Factory Method | Create strategy instances |
| **Strategy** | Dependency Injection | Inject strategy into context |
| **Command** | Composite | Build macro commands from primitives |
| **Command** | Memento | Implement undo/redo |
| **Decorator** | Factory | Create decorated objects |
| **Abstract Factory** | Singleton | Single factory instance per family |
| **Template Method** | Factory Method | Create objects in template steps |
| **Observer** | Mediator | Decouple observers from subject |
| **Façade** | Singleton | Single entry point to subsystem |
| **Iterator** | Factory Method | Create appropriate iterators |
| **State** | Singleton | Share state objects |
| **Adapter** | Façade | Adapt entire subsystem |

**Key insight:** Don't think of patterns in isolation. They compose to solve complex problems.

## Anti-Patterns (What NOT to Do)

Common mistakes when applying architectural principles:

| Anti-Pattern | Description | Better Approach |
|--------------|-------------|-----------------|
| **Pattern for pattern's sake** | Using patterns without design pressure | Wait for the pain point, then refactor |
| **Over-engineering** | Solving hypothetical future problems | YAGNI - add flexibility when needed |
| **Gold plating** | Adding unnecessary abstraction layers | Start simple, refactor when design forces emerge |
| **Architecture astronaut** | Over-abstracting everything | Balance abstraction with pragmatism |
| **Wrong pattern** | Using pattern that doesn't fit problem | Understand the forces each pattern addresses |
| **Premature abstraction** | Abstracting before understanding problem | Rule of three: abstract on third duplication |
| **Inheritance abuse** | Deep hierarchies, improper is-a relationships | Prefer composition, shallow hierarchies |
| **Singleton abuse** | Global state disguised as pattern | Use Dependency Injection instead |
| **Cargo cult patterns** | Copying pattern structure without understanding | Study the problem the pattern solves |
| **Big Design Up Front** | Designing entire system before coding | Evolutionary design - refactor continuously |

**Key rules:**
1. **Patterns emerge from code under design pressure** - don't impose them prematurely
2. **Three strikes rule** - wait for third duplication before abstracting
3. **Design for today, refactor for tomorrow** - add flexibility when you need it, not before
4. **Composable > Reusable** - favor loose coupling over inheritance hierarchies

## Composable Design (The Modern Approach)

Traditional OOP focuses on reuse through inheritance. Modern design emphasizes composition and flexibility.

### Composability vs Reusability

| Approach | Focus | Structure | Flexibility |
|----------|-------|-----------|-------------|
| **Reusable** | Share code through inheritance | Deep hierarchies, fixed relationships | Rigid - changes break hierarchy |
| **Composable** | Combine behaviors at runtime | Shallow hierarchies, loose coupling | Flexible - mix and match behaviors |

### Composability Principles

| Principle | Description | Example |
|-----------|-------------|---------|
| **Small, focused components** | Each component does one thing well | Strategy objects, Command objects |
| **Clear interfaces** | Components communicate through contracts | Dependency Injection |
| **No hidden dependencies** | All dependencies explicit | Constructor injection |
| **Immutable by default** | Avoid shared mutable state | Value Objects, pure functions |
| **Runtime composition** | Assemble behaviors dynamically | Decorator chains, Strategy injection |

**Key insight:** Composable designs don't test all combinations - they test elements thoroughly and add guardrails for valid compositions.

**→ Deep dive:** See `patterns/architectural/composable-design.md` for detailed principles, examples, and testing strategies.

## Hexagonal Architecture (Ports & Adapters)

The gold standard for maintainable, testable systems.

### Core Concept

| Layer | Description | Dependencies |
|-------|-------------|--------------|
| **Domain Core** | Business logic, entities, rules | None - pure domain logic |
| **Ports** | Interfaces defining how core talks to outside world | Domain types only |
| **Adapters** | Implementations connecting to external systems | Ports + external libraries |

**Key rules:**
1. **Domain Core has no dependencies** - not even on frameworks
2. **Ports defined by domain needs** - not by external system capabilities
3. **Adapters depend on ports** - not vice versa
4. **Dependencies point inward** - domain is center, adapters on edges

### Benefits

| Benefit | Description |
|---------|-------------|
| **Testability** | Test domain logic without external dependencies |
| **Flexibility** | Swap adapters without changing domain logic |
| **Framework independence** | Domain logic doesn't depend on frameworks |
| **Clear boundaries** | Explicit separation between business logic and infrastructure |

**→ Deep dive:** See `patterns/architectural/hexagonal-architecture.md` for complete implementation guide, examples, and migration strategies.

## Refactoring to Patterns (Tactical Guide)

When you identify an architectural problem, use this tactical guide:

### Step 1: Identify the Smell

Use the "Common Architectural Problems" table above to diagnose.

### Step 2: Understand the Forces

Before applying a pattern, understand what forces it addresses:
- What varies and what stays the same?
- What needs to be flexible?
- What should be hidden?
- What should be explicit?

### Step 3: Apply Pattern Incrementally

1. **Add abstraction** (interface/base class)
2. **Create implementations** (concrete classes)
3. **Introduce factory/injection** (decouple creation)
4. **Refactor clients** (use abstraction, not concrete classes)
5. **Add tests** (verify behavior unchanged)

### Step 4: Verify Improvement

- [ ] Easier to test?
- [ ] Easier to extend?
- [ ] Clearer responsibilities?
- [ ] Fewer dependencies?
- [ ] Less duplication?

**Key rule:** Refactor in small steps. Each step should leave code in working state.

**→ Deep dive:** See `patterns/architectural/refactoring-strategies.md` for detailed refactoring recipes.

## Architecture Review Checklist

### SOLID Compliance
- [ ] Each class has single, clear responsibility
- [ ] Can extend behavior without modifying existing code
- [ ] Subtypes are substitutable for base types
- [ ] Interfaces are focused and client-specific
- [ ] High-level code doesn't depend on low-level details

### Design Patterns
- [ ] Patterns applied address real design pressures, not hypothetical ones
- [ ] Pattern choice fits the problem forces
- [ ] Patterns compose cleanly
- [ ] No pattern overuse (keep it simple)

### Coupling & Cohesion
- [ ] Low coupling (few dependencies between modules)
- [ ] High cohesion (related things together, unrelated apart)
- [ ] Dependencies point toward stability (depend on abstractions)
- [ ] No circular dependencies

### Testability
- [ ] Dependencies injected, not hidden
- [ ] Easy to create test doubles
- [ ] Domain logic separate from infrastructure
- [ ] No global state or singletons

### Flexibility
- [ ] Can swap implementations without changing clients
- [ ] Can add new features without modifying existing code
- [ ] Clear extension points
- [ ] Composable components

## When to Apply Patterns (Timing Guide)

| Stage | Approach | Reasoning |
|-------|----------|-----------|
| **New greenfield code** | Start simple, no patterns | Patterns emerge from real needs, not speculation |
| **First duplication** | Note it, but don't abstract yet | Might be coincidence |
| **Second duplication** | Consider abstracting, but wait | Pattern becoming clearer |
| **Third duplication** | Refactor - apply pattern | Clear pattern, safe to abstract |
| **Complex logic** | Consider patterns proactively | Strategy, State, Command prevent conditionals |
| **External boundaries** | Apply immediately | Adapter, Façade protect from external changes |
| **Known variation points** | Apply proactively | Factory, Strategy for known flexibility needs |

**Key principle:** Let patterns emerge from code under pressure, but proactively protect boundaries and handle known variation.

## Reference Library

### Pattern Catalogs (Complete Implementations)

```
patterns/behavioral/          # Command, Strategy, Template Method, Observer, State, etc.
patterns/structural/          # Adapter, Decorator, Façade, Composite, Proxy, etc.
patterns/creational/          # Factory, Abstract Factory, Builder, Singleton, DI
patterns/architectural/       # Hexagonal, Layered, DDD, Event-Driven
```

**Each pattern file includes:**
- Intent and motivation
- Structure (UML-like)
- When to use / when NOT to use
- Implementation examples
- Related patterns
- Refactoring steps

### Architectural Guides (Strategic)

```
patterns/architectural/solid-principles.md         # SOLID deep dive
patterns/architectural/composable-design.md        # Modern composition patterns
patterns/architectural/hexagonal-architecture.md   # Ports & Adapters
patterns/architectural/code-smells.md              # Catalog of architectural smells
patterns/architectural/refactoring-strategies.md   # Step-by-step refactoring recipes
```

### Quick Reference (Tactical)

For immediate lookup during design or code review:

| Need | Reference |
|------|-----------|
| Pattern selection | "Pattern Selection Guide" (above) |
| Architectural problem diagnosis | "Common Architectural Problems" (above) |
| Anti-pattern warning signs | "Anti-Patterns" (above) |
| SOLID principles | "Core Principles" (above) |
| Pattern combinations | "Design Pattern Combinations" (above) |

## Language-Specific Considerations

### PHP/WordPress
- **Hooks system** = Observer pattern built-in
- **Factories** - Use WP factory system for testability
- **Dependency Injection** - Constructor injection preferred over globals
- **Singletons** - Avoid; WordPress core uses them, but they hinder testing
- **Façades** - Common for complex WP subsystems (WC_Cart, WC_Session)

### JavaScript/TypeScript
- **First-class functions** = Strategy pattern often unnecessary (pass functions)
- **Modules** = Built-in namespacing, no need for Singleton
- **Composition** - Use object composition and mixins over inheritance
- **Dependency Injection** - Constructor injection or React context
- **Immutability** - Emphasized in React/Redux - aids composability

### General OOP (Java, C#, Python)
- **Interfaces** - Use liberally for abstraction
- **Abstract classes** - Use for shared implementation + contract
- **Dependency Injection** - Frameworks like Spring, .NET, etc.
- **Generics** - Enable type-safe patterns (Factory, Builder)

## Using the Pattern Library

**Quick lookups:** Use the quick reference tables in this file for immediate tactical guidance during design or code review.

**Deep understanding:** When you need to understand a specific pattern or architectural concept:

- **Learning specific pattern?** → `patterns/[category]/[pattern-name].md`
- **Understanding SOLID principles?** → `patterns/architectural/solid-principles.md`
- **Fixing architectural code smells?** → `patterns/architectural/code-smells.md`
- **Learning refactoring techniques?** → `patterns/architectural/refactoring-strategies.md`
- **Understanding hexagonal architecture?** → `patterns/architectural/hexagonal-architecture.md`
- **Building composable systems?** → `patterns/architectural/composable-design.md`

**Pattern selection:** Start with the "Pattern Selection Guide" decision matrix above. It maps problems to pattern categories, then drill into specific patterns.

**Problem diagnosis:** Use "Common Architectural Problems" table to identify code smells, then follow pattern solution recommendations.

**Navigation:** Each pattern file is self-contained with complete explanations, examples, and related patterns.

## Key Takeaways

1. **Patterns are not goals** - they're solutions to recurring design problems
2. **Composition > Inheritance** - build flexibility through object composition, not deep hierarchies
3. **Dependencies point inward** - high-level policy doesn't depend on low-level details
4. **Design for today, refactor for tomorrow** - let patterns emerge from real needs
5. **SOLID principles guide good design** - they're the foundation, patterns are the tactics
6. **Architecture is about managing change** - build systems that accommodate evolution
7. **Testability is an architectural quality** - hard to test = poor architecture
8. **Simple first, complex later** - start with the simplest thing that works, refactor when forces emerge

## Notes

- Architecture quality matters more than pattern quantity
- If patterns feel like boilerplate, you're applying them too early
- Good architecture makes change cheap - that's the entire goal
- Patterns are discovered through refactoring, not imposed up front
- When in doubt, favor simplicity over abstraction
