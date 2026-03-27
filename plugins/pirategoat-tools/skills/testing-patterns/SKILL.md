---
name: testing-patterns
description: Core test quality principles - philosophy, smells, structure, mocking, and coverage. For language-specific patterns see php-testing-patterns, js-testing-patterns, e2e-testing-patterns.
---

# Testing Patterns

Apply these language-agnostic test quality principles when writing or reviewing tests. Diagnose test smells. Enforce behavior-first testing. Route to language-specific skills for framework details.

## RULE 0: Test Behavior, Not Implementation

Every test must verify observable outcomes (return values, side effects, exceptions) — never internal method calls, execution order, or implementation structure. Tests that break during behavior-preserving refactors are testing the wrong thing.

When this rule conflicts with other guidance in this skill, this rule wins.

## When to Use This Skill

Use for language-agnostic test quality: philosophy, smells, structure, mocking strategies, coverage decisions, test data.

**Before applying patterns:** Read 2-3 existing tests in the project to identify established conventions (naming, structure, assertion style, fixture approach). Align your suggestions with the codebase's existing patterns unless they violate RULE 0.

**Delegate to language-specific skills for framework details:**
- `php-testing-patterns` — PHPUnit, WordPress, WooCommerce
- `js-testing-patterns` — Jest, Vitest, React Testing Library
- `e2e-testing-patterns` — Playwright, Page Object Model
- `go-testing-patterns` — Go testing, table-driven tests, httptest
- `rust-testing-patterns` — built-in framework, mockall, proptest
- `python-testing-patterns` — pytest, hypothesis, factory_boy

Do not use for TDD workflow (separate skill).

## Reference Routing

When you find a test concern, read ONLY the specified sections. Do NOT read full files.

| Test Concern | Reference File | Sections to Read |
|---|---|---|
| Tests pass but don't verify behavior | `references/test-philosophy.md` | `## Quick Reference` + `## The Behavior vs Implementation Distinction` |
| Flaky tests (random pass/fail) — usually an implementation bug, not a test bug | `references/test-smells.md` | `## Quick Reference` + `### 1. Flaky Tests` |
| Brittle tests (break on refactor) | `references/test-smells.md` | `## Quick Reference` + `### 2. Brittle Tests` |
| Over-mocking / mock confusion | `references/mocking-strategies.md` | `## Quick Reference` + `## The Mocking Decision Framework` |
| Poor structure (no AAA, bad names) | `references/test-structure.md` | `## Quick Reference` + `## The AAA Pattern` |
| Missing edge/error case coverage | `references/coverage.md` | `## Quick Reference` + `## What to Test` |
| Over-testing trivial code | `references/coverage.md` | `## Quick Reference` + `## What NOT to Test` |
| Test data strategy | `references/test-data.md` | `## Quick Reference` + `## Factories` |
| Choosing test layer (unit/integration/system) | `references/test-layers.md` | `## Quick Reference` + `## Choosing the Right Layer` |
| Independence/determinism/speed/readability issues | `references/test-philosophy.md` | `## Quick Reference` + relevant pillar section |
| "Why should we test this?" — stakeholder justification | `references/test-benefits.md` | `## Quick Reference` |
| TDD red-green-refactor questions | `references/tdd-workflow.md` | `## Quick Reference` + `## The Red-Green-Refactor Cycle` |

**How to read sections:** Grep for the start heading to find its line number, then Read with offset+limit to the next `## ` heading.

**Fallback:** For concerns not listed, read the reference file's `## Quick Reference` section only.

## Verify These Qualities in Every Test

| Principle | Violation Symptom |
|---|---|
| Behavior-Based | Tests break when refactoring without behavior changes |
| Independent | Tests pass/fail based on run order |
| Deterministic | Random failures, CI inconsistency |
| Fast | Slow test suite |
| Readable | "What does this test verify?" is hard to answer |
| Single Concern | Unclear which behavior failed |

## FORBIDDEN Patterns

| WRONG | CORRECT | Why |
|---|---|---|
| Tests depend on execution order | Each test sets up own state | Coupling between tests — one failure cascades |
| Multiple assertions testing different behaviors | One logical assertion per test | Unclear which behavior failed on test failure |
| Testing implementation details | Testing behavior/outcomes | Brittle — refactoring breaks tests unnecessarily |
| Hard-coded dates/times | Time mocking or relative dates | Flaky — tests expire or depend on timezone |
| Real HTTP calls in unit tests | Mock HTTP client | Slow, unreliable, non-deterministic |
| Shared mutable fixtures | Fresh fixtures per test | Test pollution — order-dependent failures |
| `sleep()` / `setTimeout()` for sync | Proper async waiting mechanisms | Flaky and slow — arbitrary delays mask real issues |

## Mocking Decision Quick Reference

| Scenario | Unit Test | Integration Test |
|---|---|---|
| HTTP calls | Always mock | Mock or use test server |
| Database | Mock | Real (with transactions) |
| File system | Mock | Temp directory |
| Time/dates | Always mock | Always mock |
| Third-party APIs | Always mock | Always mock |
| Internal classes | Rarely (design smell if needed) | No |

For deeper mocking guidance, see the routing table entry for `mocking-strategies.md`.

## Diagnose Test Smells

| Smell | Likely Root Cause | Fix |
|---|---|---|
| Slow tests | Coupled to external dependencies | Mock external boundaries |
| Flaky tests | Race conditions, timing, non-determinism | Fix implementation first, then test |
| Brittle tests | Testing implementation details | Rewrite to test public API/behavior |
| Complex tests | SUT has too many responsibilities | Simplify SUT, then tests simplify |
| No real assertions | "Doesn't crash" test | Add meaningful assertions on outcomes |
| Excessive mocking | Tight coupling in SUT | Refactor for loose coupling |
