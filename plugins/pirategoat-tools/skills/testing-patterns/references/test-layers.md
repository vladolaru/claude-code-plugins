# Test Layers: Unit, Integration, and System Testing

**Source:** Synthesized from "Test Layers: From Unit to System" (jhumelsine.github.io)

## Quick Reference

| What You're Testing | Layer | Why |
|---|---|---|
| Pure function logic | Unit | Fast, isolated, deterministic |
| Algorithm edge cases | Unit | Need to test all branches |
| Class with dependencies | Unit | Mock dependencies for isolation |
| Database queries | Integration | Need real DB behavior |
| API endpoint contract | Integration | Verify request/response |
| Multiple services cooperating | Integration | Verify communication |
| User workflow (end-to-end) | System | Verify complete experience |
| Cross-cutting concerns (auth) | System | Verify permissions across layers |
| UI layout/styling | System (visual regression) | Screenshot comparison |

**Distribution guideline:**
- **Unit tests: Many** -- all business logic, edge cases, error conditions, algorithm variations
- **Integration tests: Some** -- component boundaries, data flow, contracts, database operations
- **System tests: Few** -- critical user journeys (checkout, signup), smoke tests only

**Don't test the same thing at every layer:**
- Edge case testing --> Unit (comprehensive)
- Edge case verification --> Integration (spot check)
- Edge case validation --> System (skip)

## Overview

Different types of tests operate at different levels of scope, trading detail for breadth. Understanding when to use each layer prevents both under-testing (gaps) and over-testing (waste).

**Core insight:** "Unit tests confirm the nuts and bolts. Integration tests confirm the bolt screws into the nut."

**The Mars Climate Orbiter Lesson:** NASA lost $327.6M when Lockheed's imperial units didn't match NASA's metric units. Unit tests for each component passed -- no integration test validated the contract between systems.

## The Three Layers

### Scope Diagram

```
+-------------------------------------------------+
| SYSTEM TESTS                                    |
| Scope: Entire application + external deps       |
| Focus: End-to-end user workflows                |
| Speed: Slow (minutes)                           |
+-------------------------------------------------+
          ^
          |
+-------------------------------------------------+
| INTEGRATION/ACCEPTANCE TESTS                    |
| Scope: Multiple components                      |
| Focus: Component cooperation                    |
| Speed: Medium (seconds-minutes)                 |
+-------------------------------------------------+
          ^
          |
+-------------------------------------------------+
| UNIT TESTS                                      |
| Scope: Single class/function                    |
| Focus: Logic correctness                        |
| Speed: Fast (milliseconds)                      |
+-------------------------------------------------+
```

### Detail vs Scope Trade-off

Think of zooming in/out on a map:
- **Street view (Unit):** See every detail, limited scope
- **Neighborhood (Integration):** See how streets connect, buildings as shapes
- **City view (System):** See entire layout, lose building details

You can't see everything at every level simultaneously.

### Layer 1: Unit Tests

Tests a single class or function in isolation. All dependencies replaced with test doubles.

| Aspect | Unit Tests |
|--------|------------|
| **Scope** | Single class/function |
| **Speed** | < 10ms per test, < 10s full suite |
| **Dependencies** | All mocked via test doubles |
| **Coverage** | Deep: all branches, edge cases |
| **When fails** | Easy to pinpoint (small scope) |
| **Fragility** | Can break during refactoring (if testing implementation) |
| **Creation effort** | Low (simple setup) |
| **Maintenance** | Medium (may need updates during refactoring) |

**When Unit Tests Aren't Enough:**
- Integration bugs (components don't communicate correctly)
- Configuration issues
- Deployment problems
- System-level behavior
- Type mismatches between components (unit test mocks hide them)

### Layer 2: Integration/Acceptance Tests

Tests multiple components working together. Internal dependencies are real, external dependencies mocked.

**Acceptance tests** are the subset that specify user-desired behavior (from User Story acceptance criteria).

| Aspect | Integration Tests |
|--------|-------------------|
| **Scope** | Multiple components (package/module) |
| **Speed** | Medium (100ms - 2s per test) |
| **Dependencies** | Internal real, external mocked |
| **Coverage** | Breadth: happy paths, key scenarios |
| **When fails** | Moderate effort to pinpoint |
| **Fragility** | Low (tests behavior, not structure) |
| **Creation effort** | Medium (more setup) |
| **Maintenance** | Low (behavior-focused) |

### Layer 3: System Tests

Tests entire system including UI, database, and potentially real external services. Simulates real user behavior.

| Aspect | System Tests |
|--------|--------------|
| **Scope** | Entire application |
| **Speed** | Slow (5-30s per test) |
| **Dependencies** | All real (or production-like) |
| **Coverage** | Shallow: critical paths only |
| **When fails** | Hard to pinpoint (large scope) |
| **Fragility** | High (UI changes break tests) |
| **Creation effort** | High (complex setup) |
| **Maintenance** | High (sensitive to changes) |

## Test Strategy Comparison

| Strategy | Unit | Integration | System | Best For |
|----------|------|-------------|--------|----------|
| **Ice Cream Cone** | Few | Few | Many (manual) | Legacy transition |
| **Pyramid** | Many | Some | Few | TDD, modular code |
| **Trophy** | Some | Many | Few | APIs, microservices |

### Pyramid (Recommended)

```
        +------------------+
        |  System Tests    |
        +------------------+
        |                  |
        | Integration Tests|
        |                  |
        +------------------+
        |                  |
        |                  |
        |   Unit Tests     |
        |                  |
        |                  |
        +------------------+
```

**Benefits:**
- Fast feedback (seconds)
- High coverage at low cost
- Easy to pinpoint failures
- Supports TDD workflow

**Best for:** New projects, modular architectures, teams practicing TDD.

**Ice Cream Cone** (anti-pattern): Heavy on manual system tests, few unit tests. Slow feedback, doesn't scale, QA bottleneck.

**Trophy** (alternative): Emphasizes integration tests over unit tests. Adds static analysis as a base layer. Best for microservices, API-first designs, frequent refactoring.

## Choosing the Right Layer

See [Quick Reference](#quick-reference) above.

## Common Mistakes

| Mistake | Why It's Wrong | Fix |
|---------|---------------|-----|
| **Only testing happy path** | Misses error conditions, edge cases, and failure modes that cause production bugs | Add failure cases, invalid inputs, boundary conditions at the unit level |
| **Testing same thing at multiple layers** | Wastes effort, slows suite, creates redundant maintenance burden | Test deeply at the lowest appropriate layer, spot-check one layer up, skip above that |
| **No integration tests** | Unit tests pass but components fail together (Mars Orbiter problem) -- mocks hide contract mismatches | Add integration tests for every component boundary and data handoff |
