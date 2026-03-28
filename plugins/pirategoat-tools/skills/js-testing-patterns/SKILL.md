---
name: js-testing-patterns
description: Use when writing or reviewing JavaScript/TypeScript tests - Jest/Vitest assertions, React Testing Library queries, module mocking, async patterns, and snapshot discipline.
---

# JavaScript Testing Patterns

Jest, Vitest, and React Testing Library testing patterns. For shared test quality principles (philosophy, smells, mocking decisions), see `testing-patterns`.

## Reference Routing

| Need | Reference File | Sections to Read |
|------|---------------|-----------------|
| Jest/Vitest + RTL patterns | `${CLAUDE_SKILL_DIR}/../testing-patterns/references/jest-vitest-patterns.md` | Full file (~422L, manageable) |
| Behavior vs implementation | `${CLAUDE_SKILL_DIR}/../testing-patterns/references/test-philosophy.md` | `## The Fundamental Shift` + `## Four Core Principles` |
| Flaky/brittle tests | `${CLAUDE_SKILL_DIR}/../testing-patterns/references/test-smells.md` | `## The Six Major Test Smells` (relevant subsection) |
| Mock usage decisions | `${CLAUDE_SKILL_DIR}/../testing-patterns/references/mocking-strategies.md` | `## The Mocking Decision Framework` |
| AAA pattern/naming | `${CLAUDE_SKILL_DIR}/../testing-patterns/references/test-structure.md` | `## The AAA Pattern` + `## Test Naming Conventions` |
| Test data strategy | `${CLAUDE_SKILL_DIR}/../testing-patterns/references/test-data.md` | `## Factories` + `## Builders` |

**How to read sections:** Grep for heading to find line number, Read with offset+limit.

## RTL Query Priority (Best to Worst)

1. `getByRole('button', { name: 'Submit' })` — accessible, resilient
2. `getByLabelText('Email')` — form elements
3. `getByPlaceholderText()` — when no label
4. `getByText('Click me')` — visible content
5. `getByDisplayValue()` — filled inputs
6. `getByTestId('submit-btn')` — last resort

## JS Assertion Quick Reference

| Use | Instead Of | Why |
|-----|-----------|-----|
| `toMatchObject({key: val})` | `toEqual({...all fields})` | Partial match, resilient to new fields |
| `toHaveBeenCalledWith(x)` | `toHaveBeenCalled()` | Verifies correct arguments |
| `await expect(fn()).rejects.toThrow()` | Missing `await` | Without await, assertion silently passes |
| `userEvent.click()` | `fireEvent.click()` | Simulates real user interaction |
| `jest.useFakeTimers()` | Real timers | Deterministic, no flakiness |
| `toContainEqual(item)` | `toEqual([...all items])` | Checks membership, not exact array |
| Inline snapshots | `toMatchSnapshot()` on large output | Reviewed in PR diff, not hidden files |

## JS Red Flags

| Pattern | Why Harmful | Fix |
|---------|------------|-----|
| `toMatchSnapshot()` on large components | Never meaningfully reviewed | Use inline snapshots or specific assertions |
| `jest.mock()` on owned modules | Tests mock wiring, not behavior | Test through public API |
| `toEqual` for partial checks | Breaks when fields added | Use `toMatchObject` |
| Missing `await` on async assertions | Silent pass | Always `await expect(...).rejects` |
| Blanket `act()` wrapping | Hides real act() warnings | Let RTL handle, wrap only when needed |
| Timer-dependent without fake timers | Flaky in CI | Use `jest.useFakeTimers()` |
| `getByTestId` as primary query | Tests implementation | Use role/label/text queries |
| `fireEvent` over `userEvent` | Not real interaction | Use `@testing-library/user-event` |

## Async Testing Patterns

- **Promises:** Always `await` or return the promise
- **Timers:** `jest.useFakeTimers()` + `jest.advanceTimersByTime(ms)`
- **Queries:** Use `findBy*` (waits) not `getBy*` (immediate) for async content
- **Act warnings:** Let RTL `render()` handle; wrap only state updates outside RTL
