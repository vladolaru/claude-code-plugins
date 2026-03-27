# Testing Patterns Reference Library

**Source:** Comprehensive synthesis from jhumelsine.github.io testing series

This reference library provides deep dives into testing concepts, patterns, and practices. Each document is self-contained but interconnected.

## Quick Navigation

### Foundational Understanding

Start here to understand the mental models and philosophy:

- **[test-philosophy.md](./test-philosophy.md)** - Philosophy and quality principles
  - Tests as specifications vs verification
  - Tests as experiments (hard in training, easy in battle)
  - Future-focused testing
  - Behavior vs implementation distinction
  - Quality pillars: Independence, Determinism, Speed, Readability, Single Concern

### Diagnostic Guides

Use these when something's wrong:

- **[test-smells.md](./test-smells.md)** - Identifying and fixing test problems
  - Flaky tests (usually reveal implementation bugs!)
  - Brittle tests (testing implementation details)
  - Slow tests (I/O not mocked)
  - Complex tests (SRP violations)
  - False positives (missing assertions)
  - Over-mocking (tight coupling)

### Core Principles

Deep dives into what makes tests good:

- **[test-structure.md](./test-structure.md)** - AAA/Given-When-Then patterns
  - Test organization and naming conventions
  - Arrange-Act-Assert structure

### Workflows and Practices

- **[tdd-workflow.md](./tdd-workflow.md)** - Red-Green-Refactor cycle
  - The Three Laws of TDD (Bob Martin)
  - Coding katas for practice

### Architecture and Strategy

- **[test-layers.md](./test-layers.md)** - Unit, Integration, and System testing
  - The Mars Climate Orbiter lesson
  - Pyramid strategy and when to use which layer

### Why We Test

- **[test-benefits.md](./test-benefits.md)** - 13 benefits of testing
  - Tests as codified specifications
  - Safety net for refactoring
  - Faster development and better APIs

### Implementation Techniques

- **[mocking-strategies.md](./mocking-strategies.md)** - When and how to use test doubles
  - Types: Dummy, Stub, Mock, Spy, Fake
  - Decision framework for mocking

- **[test-data.md](./test-data.md)** - Fixtures, factories, and builders

- **[coverage.md](./coverage.md)** - What to test and what to skip

### Language-Specific Patterns

- **[phpunit-patterns.md](./phpunit-patterns.md)** - PHP, PHPUnit, WordPress, WooCommerce
- **[jest-vitest-patterns.md](./jest-vitest-patterns.md)** - JavaScript testing with Jest/Vitest
- **[playwright-patterns.md](./playwright-patterns.md)** - E2E testing with Playwright
- **[go-testing-patterns.md](./go-testing-patterns.md)** - Go testing package patterns
- **[rust-testing-patterns.md](./rust-testing-patterns.md)** - Rust built-in framework, mockall, proptest
- **[python-testing-patterns.md](./python-testing-patterns.md)** - pytest, hypothesis, factory_boy

## Reading Paths

### Path 1: "I'm new to testing"
1. Start with **test-philosophy.md** (understand the mindset and quality pillars)
2. Study **tdd-workflow.md** (how to write tests)
3. Practice with coding katas
4. Consult **test-smells.md** when stuck

### Path 2: "My tests are problematic"
1. Read **test-smells.md** (diagnose the problem)
2. Review **test-philosophy.md** quality pillars (understand what's missing)
3. Check **mocking-strategies.md** (fix over-mocking)

### Path 3: "I want to improve our testing strategy"
1. Read **test-layers.md** (understand the options)
2. Study **test-benefits.md** (build the case for testing)
3. Review **tdd-workflow.md** (establish workflow)
4. Consult language-specific guides for implementation

### Path 4: "I'm debugging flaky/brittle tests"
1. Start with **test-smells.md** (diagnostic protocol)
2. Check **test-philosophy.md** quality pillars (determinism, independence)
3. Review **mocking-strategies.md** (proper isolation)

## Source Attribution

All content synthesized from:
- **Primary source:** jhumelsine.github.io testing series
- Blog posts include: "Writing Tests Before the Implementation", "Testing Benefits", "Testing Concerns", "Attributes of Effective Unit Tests", "Test Doubles", "Test Layers: From Unit to System", and related posts

## License

MIT - See root LICENSE file
