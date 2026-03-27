# Testing Patterns Reference Library

**Source:** Comprehensive synthesis from jhumelsine.github.io testing series

This reference library provides deep dives into testing concepts, patterns, and practices. Each document is self-contained but interconnected.

## Quick Navigation

### 🧠 Foundational Understanding

Start here to understand the mental models and philosophy:

- **[test-philosophy.md](./test-philosophy.md)** - Philosophy and quality principles
  - Tests as specifications vs verification
  - Tests as experiments (hard in training, easy in battle)
  - Future-focused testing
  - Behavior vs implementation distinction
  - Common mental traps and how to avoid them
  - Quality pillars: Independence, Determinism, Speed, Readability, Single Concern

### 🔍 Diagnostic Guides

Use these when something's wrong:

- **[test-smells.md](./test-smells.md)** - Identifying and fixing test problems
  - Flaky tests (usually reveal implementation bugs!)
  - Brittle tests (testing implementation details)
  - Slow tests (I/O not mocked)
  - Complex tests (SRP violations)
  - False positives (missing assertions)
  - Over-mocking (tight coupling)
  - Investigation protocols and fix strategies

### 📚 Core Principles

Deep dives into what makes tests good:

- **[test-structure.md](./test-structure.md)** - AAA/Given-When-Then patterns
  - Test organization and naming conventions
  - Arrange-Act-Assert structure
  - Language-specific patterns

### 🔄 Workflows and Practices

Methodologies and processes:

- **[tdd-workflow.md](./tdd-workflow.md)** - Red-Green-Refactor cycle
  - The Three Laws of TDD (Bob Martin)
  - Complete iteration examples
  - "Fake it until you make it" strategy
  - Test && Commit || Revert (TCR)
  - Coding katas for practice
  - Anti-patterns and common mistakes

### 🏗️ Architecture and Strategy

Understanding the bigger picture:

- **[test-layers.md](./test-layers.md)** - Unit, Integration, and System testing
  - The Mars Climate Orbiter lesson
  - "Nuts and bolts" vs "fit" testing
  - Pyramid, Trophy, and Ice Cream Cone strategies
  - When to use which layer
  - The composable design challenge

### 🎯 Why We Test

Understanding the value:

- **[test-benefits.md](./test-benefits.md)** - 13 benefits of testing
  - Tests as codified specifications
  - Tests as experiments
  - Documenting assumptions and invariants
  - Finding and preventing future bugs
  - Reducing debugging time
  - Safety net for refactoring
  - Leading to better design
  - Faster development
  - Better APIs
  - Working documentation

### 🔧 Implementation Techniques

Specific tactics and tools:

- **[mocking-strategies.md](./mocking-strategies.md)** - When and how to use test doubles
  - Types: Dummy, Stub, Mock, Spy, Fake
  - Decision framework for mocking
  - Mock at boundaries, not internals
  - Dependency injection patterns

- **[test-data.md](./test-data.md)** - Fixtures, factories, and builders
  - When to use each approach
  - Data generation patterns
  - Avoiding test pollution

- **[coverage.md](./coverage.md)** - What to test and what to skip
  - Prioritization strategies
  - Coverage metrics interpretation
  - Testing boundaries and edge cases

### 💻 Language-Specific Patterns

Framework and language details:

- **[phpunit-patterns.md](./phpunit-patterns.md)** - PHP, PHPUnit, WordPress, WooCommerce
- **[jest-vitest-patterns.md](./jest-vitest-patterns.md)** - JavaScript testing with Jest/Vitest
- **[playwright-patterns.md](./playwright-patterns.md)** - E2E testing with Playwright

## Reading Paths

### Path 1: "I'm new to testing"
1. Start with **test-philosophy.md** (understand the mindset and quality pillars)
2. Study **tdd-workflow.md** (how to write tests)
4. Practice with coding katas
5. Consult **test-smells.md** when stuck

### Path 2: "My tests are problematic"
1. Read **test-smells.md** (diagnose the problem)
2. Review **test-philosophy.md** quality pillars (understand what's missing)
3. Check **mocking-strategies.md** (fix over-mocking)
4. Revisit **test-philosophy.md** (correct mental model)

### Path 3: "I want to improve our testing strategy"
1. Read **test-layers.md** (understand the options)
2. Study **test-benefits.md** (build the case for testing)
3. Review **tdd-workflow.md** (establish workflow)
4. Consult language-specific guides for implementation

### Path 4: "I'm debugging flaky/brittle tests"
1. Start with **test-smells.md** (diagnostic protocol)
2. Check **test-philosophy.md** quality pillars (determinism, independence)
3. Review **mocking-strategies.md** (proper isolation)
4. Study **test-philosophy.md** (behavior vs implementation)

## Key Insights Across Documents

### The Central Theme
**Tests are specifications, not verification.** They define what code should do, then confirm it continues to do that. This insight appears in every document because it's foundational to everything else.

### The Mars Orbiter Lesson
Unit tests alone aren't enough. Integration tests validate contracts between components. Appears in **test-layers.md** and referenced throughout.

### The Flaky Test Revelation
Flaky tests usually reveal implementation bugs (race conditions, concurrency issues), not test bugs. From **test-smells.md** and **test-philosophy.md**.

### The Design Feedback Loop
If tests are hard to write, the code is hard to use. Tests are the first client of your API. From **test-philosophy.md**, **test-benefits.md**, and **test-smells.md**.

### The Future Focus
Tests don't find bugs now—they prevent bugs later. From **test-benefits.md** and repeated throughout.

### The Hard Training Principle
Subject code to adversarial scenarios in tests so it's battle-hardened for production. From **test-philosophy.md** and **test-benefits.md**.

## Recurring Quotes

> _Tests don't break your code; they break your illusions about the quality of that code._ — Maaret Pyhäjärvi

> _As the tests get more specific, the code gets more generic._ — Bob Martin

> _If you're good at the debugger it means you spent a lot of time debugging. I don't want you to be good at the debugger._ — Bob Martin

> _Hard in training; easy in battle._ — Alexander Suvorov

> _When programmers do their jobs, testers find nothing._ — Bob Martin

> _Testing can show the presence of bugs, but not their absence._ — Edsger W. Dijkstra

## Contributing

These documents synthesize insights from Jim Humelsine's excellent blog series at jhumelsine.github.io. When updating:

1. Maintain focus on practical, actionable guidance
2. Include real-world examples (preferably from the blog)
3. Preserve the quotes and attributions
4. Add "Further Reading" sections with blog post links
5. Use consistent formatting and structure

## Source Attribution

All content synthesized from:
- **Primary source:** jhumelsine.github.io testing series
- Blog posts include: "Writing Tests Before the Implementation", "Testing Benefits", "Testing Concerns", "Attributes of Effective Unit Tests", "Test Doubles", "Test Layers: From Unit to System", and related posts

## License

MIT - See root LICENSE file
