# Software Architecture Patterns Library

**A comprehensive guide to design patterns and architectural principles for building maintainable, scalable software systems.**

This library organizes patterns by their primary purpose and provides clear guidance on when and how to apply each pattern. Every pattern is presented with real-world examples, implementation guidance, and common pitfalls to avoid.

## Quick Navigation by Category

### 🏗️ Architectural Patterns

Large-scale system organization patterns:

- **[architectural/layered.md](./architectural/layered.md)** - Presentation, Business, Data layers
- **[architectural/hexagonal.md](./architectural/hexagonal.md)** - Ports and Adapters architecture
- **[architectural/clean-architecture.md](./architectural/clean-architecture.md)** - Dependency Rule and boundaries
- **[architectural/microservices.md](./architectural/microservices.md)** - Service decomposition strategies
- **[architectural/event-driven.md](./architectural/event-driven.md)** - Event sourcing and CQRS
- **[architectural/plugin.md](./architectural/plugin.md)** - Extensible architecture through plugins

### 🎨 Creational Patterns

Object creation and initialization:

- **[creational/factory.md](./creational/factory.md)** - Encapsulate object creation logic
- **[creational/abstract-factory.md](./creational/abstract-factory.md)** - Families of related objects
- **[creational/builder.md](./creational/builder.md)** - Complex object construction
- **[creational/prototype.md](./creational/prototype.md)** - Clone existing objects
- **[creational/singleton.md](./creational/singleton.md)** - Single instance management (use cautiously)
- **[creational/dependency-injection.md](./creational/dependency-injection.md)** - Invert control of dependencies

### 🔧 Structural Patterns

Object composition and relationships:

- **[structural/adapter.md](./structural/adapter.md)** - Interface compatibility layer
- **[structural/bridge.md](./structural/bridge.md)** - Decouple abstraction from implementation
- **[structural/composite.md](./structural/composite.md)** - Tree structures and recursive composition
- **[structural/decorator.md](./structural/decorator.md)** - Add behavior without inheritance
- **[structural/facade.md](./structural/facade.md)** - Simplified interface to complex subsystems
- **[structural/proxy.md](./structural/proxy.md)** - Control access to objects
- **[structural/flyweight.md](./structural/flyweight.md)** - Share common state efficiently

### 🎯 Behavioral Patterns

Object interaction and responsibility:

- **[behavioral/strategy.md](./behavioral/strategy.md)** - Interchangeable algorithms
- **[behavioral/observer.md](./behavioral/observer.md)** - Event notification system
- **[behavioral/command.md](./behavioral/command.md)** - Encapsulate requests as objects
- **[behavioral/template-method.md](./behavioral/template-method.md)** - Algorithm skeleton with hooks
- **[behavioral/state.md](./behavioral/state.md)** - Object behavior changes with state
- **[behavioral/chain-of-responsibility.md](./behavioral/chain-of-responsibility.md)** - Pass requests along a chain
- **[behavioral/iterator.md](./behavioral/iterator.md)** - Sequential access to collections
- **[behavioral/mediator.md](./behavioral/mediator.md)** - Centralize complex communications
- **[behavioral/memento.md](./behavioral/memento.md)** - Capture and restore object state
- **[behavioral/visitor.md](./behavioral/visitor.md)** - Add operations to object structures

## Reading Paths

### Path 1: New to Design Patterns

**Goal:** Build foundational understanding of patterns and when to use them.

1. **Start with fundamentals**
   - Read **creational/factory.md** - Understand basic object creation patterns
   - Study **structural/adapter.md** - Learn interface compatibility
   - Review **behavioral/strategy.md** - Grasp behavior encapsulation

2. **Understand composition**
   - Read **structural/decorator.md** - Behavior extension without inheritance
   - Study **structural/composite.md** - Tree structures and recursion
   - Review **behavioral/observer.md** - Event-driven communication

3. **Learn dependency management**
   - Read **creational/dependency-injection.md** - Modern IoC patterns
   - Study **architectural/hexagonal.md** - Ports and adapters
   - Review **architectural/clean-architecture.md** - Dependency rule

4. **Practice with real code**
   - Identify patterns in your current codebase
   - Refactor one small area using a pattern
   - Document why you chose that pattern

### Path 2: Refactoring Existing Code

**Goal:** Identify problems and apply appropriate patterns to fix them.

1. **Diagnose the problem**
   - **If creation logic is scattered:** creational/factory.md or creational/builder.md
   - **If conditional logic is complex:** behavioral/strategy.md or behavioral/state.md
   - **If classes are tightly coupled:** creational/dependency-injection.md
   - **If interfaces don't match:** structural/adapter.md or structural/facade.md
   - **If behavior needs extension:** structural/decorator.md or behavioral/chain-of-responsibility.md

2. **Apply the pattern incrementally**
   - Start with one use case or module
   - Write tests before refactoring
   - Refactor in small, safe steps
   - Verify tests pass after each step

3. **Review architectural alignment**
   - Does the refactored code fit your architecture?
   - Read **architectural/layered.md** or **architectural/hexagonal.md**
   - Ensure dependencies flow correctly

4. **Document the change**
   - Update architecture diagrams
   - Add pattern references to code comments
   - Document trade-offs and decisions

### Path 3: Designing New System

**Goal:** Make informed architectural decisions from the start.

1. **Define boundaries and layers**
   - Read **architectural/layered.md** - Traditional layer separation
   - Study **architectural/hexagonal.md** - Port-based boundaries
   - Review **architectural/clean-architecture.md** - Dependency direction
   - Choose your architectural style based on requirements

2. **Plan extension points**
   - Read **architectural/plugin.md** - Extensibility strategies
   - Study **behavioral/strategy.md** - Algorithm variation points
   - Review **behavioral/observer.md** - Event-driven hooks
   - Identify where variation will occur

3. **Design object creation**
   - Read **creational/dependency-injection.md** - IoC container setup
   - Study **creational/factory.md** - Creation abstraction
   - Review **creational/builder.md** - Complex construction
   - Plan your DI/IoC strategy

4. **Consider scalability**
   - Read **architectural/microservices.md** - Service decomposition
   - Study **architectural/event-driven.md** - Async communication
   - Review **structural/proxy.md** - Access control and caching
   - Plan for growth from day one

5. **Validate with prototypes**
   - Build a vertical slice using your patterns
   - Test the boundaries and interactions
   - Adjust based on learnings
   - Document architectural decisions

### Path 4: Fixing Architectural Problems

**Goal:** Identify and resolve systemic issues in existing architecture.

1. **Diagnose the root cause**
   - **Tight coupling between layers:** architectural/hexagonal.md or architectural/clean-architecture.md
   - **Difficult to test:** creational/dependency-injection.md
   - **Hard to extend behavior:** structural/decorator.md or behavioral/strategy.md
   - **Complex conditional logic:** behavioral/state.md or behavioral/command.md
   - **Monolithic codebase:** architectural/microservices.md or architectural/plugin.md
   - **Scattered business logic:** architectural/layered.md

2. **Plan the refactoring strategy**
   - Identify the pattern that addresses the root cause
   - Map current code to pattern structure
   - Define migration steps with tests
   - Plan for incremental rollout

3. **Apply strangler fig pattern**
   - Implement new pattern alongside old code
   - Gradually migrate functionality
   - Keep system running throughout
   - Remove old code when safe

4. **Verify improvements**
   - Measure before and after metrics
   - Run full test suite
   - Monitor production behavior
   - Document lessons learned

## Pattern Taxonomy

### Essential Patterns (Start Here)

**Every developer should know these:**

1. **Dependency Injection** (creational/dependency-injection.md)
   - Foundation of testable, maintainable code
   - Prerequisite for most other patterns
   - Modern IoC container usage

2. **Factory** (creational/factory.md)
   - Basic object creation abstraction
   - Encapsulates construction logic
   - Foundation for other creational patterns

3. **Strategy** (behavioral/strategy.md)
   - Encapsulate algorithms/behaviors
   - Fundamental to polymorphism
   - Enables runtime behavior changes

4. **Adapter** (structural/adapter.md)
   - Interface compatibility
   - Integration with third-party code
   - API evolution management

5. **Layered Architecture** (architectural/layered.md)
   - System organization basics
   - Separation of concerns
   - Foundation for other architectures

### Intermediate Patterns (Build On Essentials)

**Apply these when essentials aren't sufficient:**

6. **Decorator** (structural/decorator.md)
   - Extend behavior without inheritance
   - Composable enhancements
   - Alternative to deep inheritance trees

7. **Observer** (behavioral/observer.md)
   - Event-driven communication
   - Decoupled notifications
   - Basis for reactive systems

8. **Builder** (creational/builder.md)
   - Complex object construction
   - Fluent interfaces
   - Handles optional parameters elegantly

9. **Facade** (structural/facade.md)
   - Simplify complex subsystems
   - API usability layer
   - Hide implementation complexity

10. **Template Method** (behavioral/template-method.md)
    - Algorithm structure with variation points
    - Framework extension pattern
    - Hook methods for customization

11. **Hexagonal Architecture** (architectural/hexagonal.md)
    - Ports and adapters
    - Business logic isolation
    - Better testability than layered

### Advanced Patterns (Specific Use Cases)

**Apply these for specific architectural challenges:**

12. **Command** (behavioral/command.md)
    - Undo/redo functionality
    - Transaction management
    - Request queuing and logging

13. **State** (behavioral/state.md)
    - Complex state machines
    - State-dependent behavior
    - Alternative to complex conditionals

14. **Composite** (structural/composite.md)
    - Tree structures
    - Recursive composition
    - Uniform interface to parts and wholes

15. **Chain of Responsibility** (behavioral/chain-of-responsibility.md)
    - Request processing pipeline
    - Middleware patterns
    - Flexible request handling

16. **Proxy** (structural/proxy.md)
    - Access control
    - Lazy loading
    - Caching and logging

17. **Clean Architecture** (architectural/clean-architecture.md)
    - Strict dependency rules
    - Framework independence
    - Maximum testability

18. **Abstract Factory** (creational/abstract-factory.md)
    - Families of related objects
    - Platform-specific implementations
    - Ensures consistent object sets

### Specialized Patterns (Rare But Powerful)

**Apply these only when specifically needed:**

19. **Mediator** (behavioral/mediator.md)
    - Complex many-to-many interactions
    - Centralized communication logic
    - Reduce coupling in complex systems

20. **Bridge** (structural/bridge.md)
    - Separate abstraction from implementation
    - Multiple orthogonal hierarchies
    - Platform independence

21. **Flyweight** (structural/flyweight.md)
    - Memory optimization
    - Share common state
    - Large numbers of similar objects

22. **Visitor** (behavioral/visitor.md)
    - Add operations to stable structures
    - Double dispatch technique
    - Complex AST traversals

23. **Memento** (behavioral/memento.md)
    - State capture and restoration
    - Undo mechanisms
    - Checkpoint systems

24. **Iterator** (behavioral/iterator.md)
    - Sequential collection access
    - Usually language-provided
    - Custom traversal algorithms

25. **Prototype** (creational/prototype.md)
    - Clone existing objects
    - Dynamic object creation
    - Alternative to factories

26. **Singleton** (creational/singleton.md)
    - ⚠️ Use with extreme caution
    - Global state problems
    - Better alternatives: DI with singleton scope

### Architectural Patterns (System-Level)

**Apply these for large-scale system organization:**

27. **Microservices** (architectural/microservices.md)
    - Service decomposition
    - Independent deployment
    - Scalability and resilience

28. **Event-Driven Architecture** (architectural/event-driven.md)
    - Asynchronous communication
    - Event sourcing
    - CQRS pattern

29. **Plugin Architecture** (architectural/plugin.md)
    - Runtime extensibility
    - Third-party integrations
    - Marketplace ecosystems

## Pattern Relationships

### Patterns That Build On Each Other

**Factory → Abstract Factory → Builder**
- Factory: Basic creation abstraction
- Abstract Factory: Families of related objects
- Builder: Complex multi-step construction

**Strategy → State → Command**
- Strategy: Interchangeable algorithms
- State: State-dependent behavior (strategies selected by state)
- Command: Encapsulate requests (strategies for operations)

**Layered → Hexagonal → Clean Architecture**
- Layered: Basic separation of concerns
- Hexagonal: Explicit ports and adapters
- Clean: Strict dependency rules and framework independence

**Decorator → Chain of Responsibility**
- Decorator: Wrap single object with behavior
- Chain: Pass through multiple handlers (decorators with conditional application)

### Patterns That Work Well Together

**Dependency Injection + Factory**
- DI provides factories to classes
- Factories use DI to resolve dependencies
- Clean separation of construction concerns

**Strategy + Factory**
- Factory selects appropriate strategy
- Strategies injected via DI
- Runtime behavior selection

**Observer + Mediator**
- Mediator centralizes observer management
- Reduces observer coupling
- Cleaner event flow

**Adapter + Facade**
- Adapter converts individual interfaces
- Facade provides unified interface
- Clean third-party integration layer

**Decorator + Composite**
- Decorator adds behavior to leaves
- Composite structures the tree
- Flexible component enhancement

**Command + Memento**
- Command encapsulates operations
- Memento stores state for undo
- Complete undo/redo system

**Template Method + Strategy**
- Template Method defines algorithm structure
- Strategy provides pluggable steps
- Flexible algorithm composition

### Patterns That Solve Similar Problems

**When to choose which:**

**Inheritance vs Composition**
- **Template Method** - Use for rigid algorithm structure with hooks
- **Strategy** - Use for flexible, interchangeable behaviors
- **Decorator** - Use for optional, composable enhancements

**Object Creation**
- **Factory** - Simple creation, single object type
- **Abstract Factory** - Families of related objects
- **Builder** - Complex construction with many options
- **Prototype** - Clone existing objects

**Interface Adaptation**
- **Adapter** - Convert one interface to another
- **Facade** - Simplify complex subsystem
- **Bridge** - Separate abstraction from implementation

**Request Handling**
- **Chain of Responsibility** - Multiple handlers, first match wins
- **Command** - Encapsulate requests, support undo/redo
- **Strategy** - Select single handler

## Key Insights Across Patterns

### The Dependency Inversion Principle

**Appears in:** Clean Architecture, Hexagonal Architecture, Dependency Injection, Strategy, Observer

**Core insight:** High-level modules should not depend on low-level modules. Both should depend on abstractions.

**Why it matters:**
- Enables testing by swapping implementations
- Allows frameworks to be details, not foundations
- Makes business logic independent of infrastructure
- Facilitates change without cascade effects

### The Open/Closed Principle

**Appears in:** Strategy, Decorator, Template Method, Plugin Architecture, Observer

**Core insight:** Software entities should be open for extension, closed for modification.

**Why it matters:**
- Add new behavior without changing existing code
- Reduces regression risk
- Enables third-party extensions
- Supports long-term maintainability

### Composition Over Inheritance

**Appears in:** Decorator, Strategy, Bridge, Composite, Proxy

**Core insight:** Favor object composition over class inheritance for code reuse.

**Why it matters:**
- Inheritance creates tight coupling
- Composition provides flexibility
- Easier to test and mock
- Avoids fragile base class problem

### Interface Segregation

**Appears in:** Adapter, Facade, Hexagonal Architecture, Ports and Adapters

**Core insight:** Clients should not be forced to depend on interfaces they don't use.

**Why it matters:**
- Smaller, focused interfaces
- Easier to implement and test
- Reduces coupling
- Better separation of concerns

### Single Responsibility Principle

**Appears in:** Strategy, Command, Mediator, Layered Architecture, Microservices

**Core insight:** A class should have only one reason to change.

**Why it matters:**
- Easier to understand and modify
- Better testability
- Reduced coupling
- Clear separation of concerns

### The Boundary Concept

**Appears in:** Hexagonal Architecture, Clean Architecture, Microservices, Adapter, Facade

**Core insight:** Systems need clear boundaries between components with well-defined interfaces.

**Why it matters:**
- Isolates change impact
- Enables independent testing
- Supports team autonomy
- Facilitates technology changes

## When to Use Each Category

### Use Architectural Patterns When:

- **Starting a new system** - Establish structure from day one
- **System is becoming monolithic** - Time to decompose
- **Testing is too difficult** - Architecture may be wrong
- **Teams are stepping on each other** - Need better boundaries
- **Can't swap out frameworks** - Too tightly coupled

**Start with:** Layered Architecture → Consider Hexagonal → Evaluate Clean Architecture

### Use Creational Patterns When:

- **Object creation logic is scattered** - Centralize with Factory
- **Construction is complex** - Use Builder
- **Need different object families** - Use Abstract Factory
- **Testing is difficult due to tight coupling** - Use Dependency Injection
- **Need to control instantiation** - Consider Factory or Singleton (rarely)

**Start with:** Dependency Injection → Add Factory as needed → Builder for complex cases

### Use Structural Patterns When:

- **Interfaces don't match** - Use Adapter
- **Subsystem is too complex** - Use Facade
- **Need to add behavior without inheritance** - Use Decorator
- **Want to delay expensive operations** - Use Proxy
- **Building tree structures** - Use Composite
- **Need to vary implementation independently** - Use Bridge

**Start with:** Adapter and Facade → Add Decorator → Consider others for specific needs

### Use Behavioral Patterns When:

- **Algorithm needs to vary** - Use Strategy
- **Need event notifications** - Use Observer
- **State affects behavior significantly** - Use State
- **Need request handling pipeline** - Use Chain of Responsibility
- **Need undo/redo** - Use Command + Memento
- **Have algorithm template with variation** - Use Template Method
- **Complex object interactions** - Use Mediator

**Start with:** Strategy and Observer → Add Command for operations → Others for specific cases

## Pattern Combinations That Work Well

### The Classic Web Stack

**Layered + Dependency Injection + Repository (Adapter)**
- Presentation, Business, Data layers
- DI wires everything together
- Repository adapts data access
- Clean, testable architecture

### The Domain-Driven Design Stack

**Hexagonal + Factory + Strategy + Observer**
- Hexagonal isolates domain
- Factory creates aggregates
- Strategy for business rules
- Observer for domain events

### The Extension Framework

**Plugin + Factory + Strategy + Observer**
- Plugin architecture for extensibility
- Factory instantiates plugins
- Strategy for plugin behaviors
- Observer for plugin communication

### The Command Processing Pipeline

**Chain of Responsibility + Command + Strategy**
- Chain processes commands
- Command encapsulates requests
- Strategy implements handlers
- Middleware-style processing

### The UI Component System

**Composite + Decorator + Observer**
- Composite for component tree
- Decorator for behavior enhancement
- Observer for events/updates
- Flexible component architecture

### The Data Access Layer

**Adapter + Proxy + Factory**
- Adapter for database abstraction
- Proxy for lazy loading/caching
- Factory for query objects
- Clean data layer

### The Service Layer

**Facade + Command + Strategy + Dependency Injection**
- Facade simplifies business operations
- Command represents operations
- Strategy varies business logic
- DI wires services together

## Anti-Patterns to Avoid

### Over-Engineering

**Symptom:** Applying patterns before they're needed

**Problem:** Unnecessary complexity, harder to understand

**Solution:**
- Start simple, refactor to patterns when pain points emerge
- YAGNI (You Aren't Gonna Need It)
- Wait for the second or third use case

### Pattern Obsession

**Symptom:** Using patterns because they're "proper" not because they solve problems

**Problem:** Code becomes over-abstracted and hard to follow

**Solution:**
- Ask "what problem does this solve?"
- Prefer simple solutions over pattern-based ones
- Patterns are tools, not goals

### Singleton Abuse

**Symptom:** Singletons everywhere for "convenience"

**Problem:** Global state, tight coupling, testing nightmares

**Solution:**
- Use Dependency Injection instead
- Singleton scope in DI container if truly needed
- Question whether you need a singleton at all

### Inheritance Hierarchies

**Symptom:** Deep inheritance trees with many levels

**Problem:** Fragile, hard to understand, tight coupling

**Solution:**
- Prefer composition over inheritance
- Use Strategy, Decorator, or Composite patterns
- Keep inheritance hierarchies shallow (max 2-3 levels)

### Premature Abstraction

**Symptom:** Creating abstractions before understanding variation

**Problem:** Wrong abstractions are worse than duplication

**Solution:**
- Wait for 3 examples before abstracting
- Duplication is cheaper than wrong abstraction
- Refactor to patterns when variation is clear

### Pattern Mixing

**Symptom:** Combining too many patterns in one component

**Problem:** Complexity explosion, hard to maintain

**Solution:**
- One or two patterns per component maximum
- Separate concerns into different layers
- Keep it simple

## Contributing

When adding new patterns to this library:

1. **Use consistent structure**
   - Intent and motivation
   - When to use / when not to use
   - Structure and participants
   - Real-world example
   - Implementation guidance
   - Common pitfalls
   - Related patterns

2. **Provide practical examples**
   - Real code, not toy examples
   - Multiple languages where relevant
   - Common use cases from actual projects

3. **Document trade-offs**
   - Benefits AND costs
   - When to use alternatives
   - Performance implications

4. **Link related patterns**
   - What patterns build on this one
   - What patterns solve similar problems
   - What patterns work well together

5. **Include anti-patterns**
   - Common mistakes
   - How to avoid them
   - Warning signs

## Further Reading

### Books
- **Design Patterns: Elements of Reusable Object-Oriented Software** - Gang of Four (GoF)
- **Patterns of Enterprise Application Architecture** - Martin Fowler
- **Clean Architecture** - Robert C. Martin
- **Building Microservices** - Sam Newman
- **Domain-Driven Design** - Eric Evans

### Online Resources
- **Refactoring.Guru** - Excellent pattern explanations with visuals
- **SourceMaking** - Comprehensive pattern catalog
- **Martin Fowler's Blog** - Enterprise patterns and architecture

### Related Skills
- **testing-patterns** - Test-driven development and testing strategies
- **refactoring-patterns** - Code transformation techniques
- **domain-modeling** - Domain-driven design patterns

## License

MIT - See root LICENSE file
