---
name: software-architecture
description: Use when designing software solutions, choosing design patterns, refactoring toward better architecture, or facing tight coupling, rigid designs, or unclear responsibilities (covers GoF patterns, SOLID principles, hexagonal architecture, and composable designs)
---

# Software Architect

Guide for designing maintainable, extensible, and testable software systems using design patterns, architectural principles, and refactoring strategies.

## When to Use This Skill

Use when you have a **concrete design problem**: tight coupling, rigid designs, unclear responsibilities, hard-to-test code, or choosing between patterns. NOT for abstract pattern learning.

## SOLID Principles (Quick Reference)

| Principle | Violation Symptom |
|-----------|-------------------|
| **Single Responsibility** | Changes in one feature require modifying the class |
| **Open/Closed** | Adding features requires changing existing code |
| **Liskov Substitution** | Conditional checks for specific subtypes |
| **Interface Segregation** | Clients depend on methods they don't use |
| **Dependency Inversion** | High-level logic directly instantiates low-level classes |

Deep dive: `patterns/architectural/solid-principles.md`

## Code Smell -> Pattern Routing (Section-Targeted References)

When you identify a code smell, read ONLY the specified sections from the reference file. Do NOT read full files.

| Code Smell / Symptom | Reference File | Sections to Read |
|---------------------|---------------|-----------------|
| Switch/if-else chains on types | `patterns/behavioral/strategy.md` | `## Quick Reference` + `## When to Use` + `## When NOT to Use` + `## Common Mistakes` |
| Direct instantiation of dependencies | `patterns/creational/dependency-injection.md` | `## The Core Problem` + `## Dependency Injection - The Solution` |
| Complex object creation, long constructor | `patterns/creational/factory.md` | `## Overview` + `## The Problem` + `## Factory Method` (first 100L) |
| Business logic mixed with infrastructure | `patterns/architectural/hexagonal-architecture.md` | `## Overview` + `## When to Use Hexagonal Architecture` + `## Structure` (first 100L) |
| Deep inheritance hierarchy, rigid hierarchy | `patterns/structural/decorator.md` | `## Intent` + `## Problem` + `## Solution` + `## When to Use` |
| Complex subsystem exposed to clients | `patterns/structural/facade.md` | `## The Core Problem` + `## What is Facade?` + `## When to Use Facade` |
| Algorithm steps fixed, details vary | `patterns/behavioral/template-method.md` | `## Intent` + `## Motivation` + `## When to Use Template Method` |
| Need to encapsulate requests for undo/queue | `patterns/behavioral/command.md` | `## Overview` + `## When to Use` + `## When NOT to Use` |
| God object (5+ responsibilities) | No file needed | Split into classes with single responsibility |
| Feature envy (method uses another class's data) | No file needed | Move method to the class whose data it uses |
| Shotgun surgery (one change, many files) | No file needed | Extract scattered logic to single class |
| Primitive obsession (too many primitive params) | No file needed | Introduce Value Objects or Parameter Object |
| Architectural code smells catalog | `patterns/architectural/code-smells.md` | `## Quick Reference` or specific smell section |
| Refactoring recipes | `patterns/architectural/refactoring-strategies.md` | Section matching your specific smell |

**Fallback:** For patterns not listed, read the reference file's `## Quick Reference` + `## When to Use` sections only. If those headings don't exist, read the first 100 lines.

**How to read sections:** Grep for the start heading to find its line number, then Read with offset+limit to the next `## ` heading. This loads ~100-200 lines instead of ~2,000.

## When NOT to Apply Patterns

- **Three strikes rule:** Wait for third duplication before abstracting
- **Patterns emerge from code under pressure** - don't impose them prematurely
- **If it feels like boilerplate, you're applying too early**
- **YAGNI:** Add flexibility when needed, not before
- **Design for today, refactor for tomorrow**

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
- [ ] No pattern overuse (keep it simple)

### Coupling & Cohesion
- [ ] Low coupling (few dependencies between modules)
- [ ] High cohesion (related things together)
- [ ] Dependencies point toward stability (depend on abstractions)
- [ ] No circular dependencies

### Testability
- [ ] Dependencies injected, not hidden
- [ ] Easy to create test doubles
- [ ] Domain logic separate from infrastructure

### Flexibility
- [ ] Can swap implementations without changing clients
- [ ] Can add new features without modifying existing code
- [ ] Composable components over inheritance hierarchies

## Pattern Selection (Quick Decision Matrix)

| Problem | Pattern Category | Specific Patterns |
|---------|------------------|-------------------|
| Hard to swap implementations | Creational | Factory, Dependency Injection |
| Complex object creation | Creational | Builder, Factory Method |
| Incompatible interfaces | Structural | Adapter, Facade |
| Need to add behavior dynamically | Structural | Decorator, Strategy |
| Algorithm varies by context | Behavioral | Strategy, State |
| Giant if/else or switch | Behavioral | Strategy, State, Chain of Responsibility |
| Steps fixed, details vary | Behavioral | Template Method |
| Need to encapsulate requests | Behavioral | Command |

## Reference Library

```
patterns/behavioral/     # Strategy, Command, Template Method, Observer, State, etc.
patterns/structural/     # Adapter, Decorator, Facade, Composite, etc.
patterns/creational/     # Factory, Builder, Dependency Injection, etc.
patterns/architectural/  # Hexagonal, SOLID, Code Smells, Refactoring Strategies
```

Each pattern file includes intent, structure, when to use/not use, implementation examples, and related patterns. Use the routing table above to read only relevant sections.
