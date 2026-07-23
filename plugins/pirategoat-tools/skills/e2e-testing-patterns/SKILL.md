---
name: e2e-testing-patterns
description: Use when writing or reviewing Playwright E2E tests - locator strategies, Page Object Model, auto-waiting, network interception, and WordPress/WooCommerce test helpers.
---

## Skill Directory

Resolve `SKILL_DIR` to the absolute directory containing this `SKILL.md`, as
shown by the current host, before using any bundled path below.
Before a shell command uses `$SKILL_DIR`, assign it in that command or replace
it with the resolved path. It is not a host-exported environment variable.

# E2E Testing Patterns

Playwright end-to-end testing patterns. For shared test quality principles (philosophy, smells, mocking decisions), see `testing-patterns`.

## Reference Routing

| Need | Reference File | Sections to Read |
|------|---------------|-----------------|
| Playwright + E2E patterns | `$SKILL_DIR/../testing-patterns/references/playwright-patterns.md` | Full file (~461L, manageable) |
| Behavior vs implementation | `$SKILL_DIR/../testing-patterns/references/test-philosophy.md` | `## The Fundamental Shift` + `## Four Core Principles` |
| Flaky/brittle tests | `$SKILL_DIR/../testing-patterns/references/test-smells.md` | `## The Six Major Test Smells` (relevant subsection) |
| Mock usage decisions | `$SKILL_DIR/../testing-patterns/references/mocking-strategies.md` | `## The Mocking Decision Framework` |
| Test layer strategy | `$SKILL_DIR/../testing-patterns/references/test-layers.md` | `## The Three Layers` + `## Choosing the Right Layer` |

**How to read sections:** Grep for heading to find line number, Read with offset+limit.

## Locator Priority (Best to Worst)

1. `page.getByRole('button', { name: 'Submit' })` — accessible, resilient
2. `page.getByLabel('Email')` — form elements
3. `page.getByText('Welcome')` — visible content
4. `page.getByTestId('checkout-form')` — explicit test hooks
5. `page.locator('.class')` / `#id` — last resort, fragile

## E2E Quick Reference

| Use | Instead Of | Why |
|-----|-----------|-----|
| `await expect(locator).toBeVisible()` | `page.waitForTimeout(2000)` | Auto-waits, deterministic |
| `page.getByRole()` | `page.locator('.btn')` | Resilient to CSS changes |
| Page Object Model | Inline selectors | Reusable, single source of truth |
| `page.route()` for external APIs | Real HTTP calls | Fast, deterministic, no rate limits |
| Config-based URLs | `http://localhost:8080` | Works across environments |
| `toHaveScreenshot({ maxDiffPixelRatio: 0.01 })` | `toMatchSnapshot()` | Tolerates rendering differences |
| `test.describe()` grouping | Flat test structure | Logical organization |
| `await` before all assertions | Missing `await` | Playwright assertions are async |

## E2E Red Flags

| Pattern | Why Harmful | Fix |
|---------|------------|-----|
| CSS selectors as primary locators | Break on styling changes | Use role/label/text locators |
| `page.waitForTimeout()` | Arbitrary delay, flaky | Use `expect(locator).toBeVisible()` |
| Missing test isolation | Tests depend on prior state | `beforeEach` setup, clean state |
| Hardcoded URLs | Breaks across environments | Use config/env variables |
| No Page Object Model | Duplicated selectors | Extract to page object classes |
| Screenshot without tolerance | Pixel-perfect failures | Set `maxDiffPixelRatio` |
| Missing `await` before assertions | Silent assertion skip | Always await Playwright expects |
| `page.evaluate()` for assertions | Bypasses auto-waiting | Use Playwright locator assertions |
| No network interception | Flaky external calls | `page.route()` for third-party APIs |

## Page Object Model Pattern

- One class per page/component with locators as properties
- Methods return `this` or the next page object for chaining
- Locators defined once, referenced by all tests
- Actions (click, fill) encapsulated in methods
- Assertions stay in test files, not page objects
