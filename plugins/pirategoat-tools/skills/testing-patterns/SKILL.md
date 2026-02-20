---
name: testing-patterns
description: Core test quality principles - philosophy, smells, structure, mocking, and coverage. For language-specific patterns see php-testing-patterns, js-testing-patterns, e2e-testing-patterns.
---

# Testing Patterns

Core guide for writing and reviewing high-quality tests. Focuses on shared test quality principles, not language-specific patterns.

## When to Use This Skill

Use for language-agnostic test quality: philosophy, smells, structure, mocking strategies, coverage decisions, test data. NOT for TDD workflow (separate skill).

**For language-specific patterns use:**
- `php-testing-patterns` — PHPUnit, WordPress, WooCommerce
- `js-testing-patterns` — Jest, Vitest, React Testing Library
- `e2e-testing-patterns` — Playwright, Page Object Model
- `go-testing-patterns` — Go testing package, table-driven tests, httptest

## Test Smell -> Reference Routing (Section-Targeted References)

When you find a test smell, read ONLY the specified sections. Do NOT read full files.

| Test Smell / Finding | Reference File | Sections to Read |
|---------------------|---------------|-----------------|
| Tests pass but don't verify behavior | `references/test-philosophy.md` | `## The Fundamental Shift` + `## Four Core Principles` |
| Flaky tests (random pass/fail) | `references/test-smells.md` | `## The Six Major Test Smells` (Flaky subsection) |
| Brittle tests (break on refactor) | `references/test-smells.md` | `## The Six Major Test Smells` (Brittle subsection) |
| Over-mocking / mock confusion | `references/mocking-strategies.md` | `## The Mocking Decision Framework` + `## Types of Test Doubles` |
| Poor structure (no AAA, bad names) | `references/test-structure.md` | `## The AAA Pattern` + `## Test Naming Conventions` |
| Missing edge/error case coverage | `references/coverage.md` | `## What to Test` |
| Over-testing trivial code | `references/coverage.md` | `## What NOT to Test` |
| Test data strategy | `references/test-data.md` | `## Factories` + `## Builders` |
| Choosing test layer | `references/test-layers.md` | `## The Three Layers` + `## Choosing the Right Layer` |

**Fallback:** For patterns not listed, read the reference file's `## Quick Reference` section only. If that heading doesn't exist, read the first 50 lines.

**How to read sections:** Grep for the start heading to find its line number, then Read with offset+limit to the next `## ` heading.

## What Makes a Good Test (Quick Reference)

| Principle | Violation Symptom |
|-----------|-------------------|
| Behavior-Based | Tests break when refactoring without behavior changes |
| Independent | Tests pass/fail based on run order |
| Deterministic | Random failures, CI inconsistency |
| Fast | Slow test suite |
| Readable | "What does this test verify?" |
| Single Concern | Unclear which behavior failed |

## FORBIDDEN Patterns

| WRONG | CORRECT | Issue |
|-------|---------|-------|
| Tests depend on execution order | Each test sets up own state | Coupling |
| Multiple assertions testing different things | One logical assertion | Unclear failures |
| Testing implementation details | Testing behavior/outcomes | Brittle tests |
| Hard-coded dates/times | Time mocking or relative dates | Flaky tests |
| Real HTTP calls in unit tests | Mock HTTP client | Slow/unreliable |
| Shared mutable fixtures | Fresh fixtures per test | Test pollution |
| `sleep()` / `setTimeout()` for sync | Proper async waiting | Flaky, slow |

## When to Mock (Decision Table)

| Principle | Explanation |
|-----------|-------------|
| **Mock at boundaries** | Mock HTTP, database, filesystem, time, external APIs - not your own domain logic |
| **Don't mock what you own** | Mocking internal classes is usually a design smell |
| **Prefer fakes over mocks** | For complex behavior, use lightweight fake implementations |
| **Verify behavior, not implementation** | Mock to isolate, not to verify internal method calls |

| Scenario | Unit Test | Integration Test |
|----------|-----------|------------------|
| HTTP calls | Always mock | Mock or use test server |
| Database | Mock | Real (with transactions) |
| File system | Mock | Temp directory |
| Time/dates | Always mock | Always mock |
| Third-party APIs | Always mock | Always mock |
| Internal classes | Rarely (design smell) | No |

## Test Smells (Quick Diagnosis)

| Smell | Likely Root Cause | Fix |
|-------|-------------------|-----|
| Slow tests | Coupled to external dependencies | Mock external boundaries |
| Flaky tests | Race conditions, timing, non-determinism | Fix implementation, not just test |
| Brittle tests | Testing implementation details | Rewrite to test public API/behavior |
| Complex tests | SUT has too many responsibilities | Simplify SUT, then tests simplify |
| No real assertions | "Doesn't crash" test | Add meaningful assertions |
| Extensive mocking | Tight coupling in SUT | Refactor for loose coupling |

## Reference Library

```
references/test-philosophy.md      # Mental models, behavior vs implementation
references/test-smells.md          # Diagnostic guide with root cause analysis
references/test-structure.md       # AAA pattern, naming, organization
references/mocking-strategies.md   # When/how to mock, decision framework
references/test-data.md            # Fixtures, factories, builders
references/coverage.md             # What to test, prioritization
references/test-layers.md          # Unit/Integration/System strategy
references/phpunit-patterns.md     # PHPUnit + WordPress + WooCommerce
references/jest-vitest-patterns.md # Jest, Vitest, React Testing Library
references/playwright-patterns.md  # E2E patterns, Page Object Model
references/go-testing-patterns.md  # Go testing package patterns
```

Use the routing table above to read only relevant sections.
