---
name: php-testing-patterns
description: Use when writing or reviewing PHP tests - PHPUnit assertions, WordPress test utilities (WP_UnitTestCase, factories), WooCommerce test patterns, and Brain Monkey isolation.
---

## Skill Directory

Resolve `SKILL_DIR` to the absolute directory containing this `SKILL.md`, as
shown by the current host, before using any bundled path below.
Before a shell command uses `$SKILL_DIR`, assign it in that command or replace
it with the resolved path. It is not a host-exported environment variable.

# PHP Testing Patterns

PHPUnit, WordPress, and WooCommerce testing patterns. For shared test quality principles (philosophy, smells, mocking decisions), see `testing-patterns`.

## Reference Routing

| Need | Reference File | Sections to Read |
|------|---------------|-----------------|
| PHPUnit + WordPress + WooCommerce patterns | `$SKILL_DIR/../testing-patterns/references/phpunit-patterns.md` | Full file (~422L, manageable) |
| Behavior vs implementation | `$SKILL_DIR/../testing-patterns/references/test-philosophy.md` | `## The Fundamental Shift` + `## Four Core Principles` |
| Flaky/brittle tests | `$SKILL_DIR/../testing-patterns/references/test-smells.md` | `## The Six Major Test Smells` (relevant subsection) |
| Mock usage decisions | `$SKILL_DIR/../testing-patterns/references/mocking-strategies.md` | `## The Mocking Decision Framework` |
| AAA pattern/naming | `$SKILL_DIR/../testing-patterns/references/test-structure.md` | `## The AAA Pattern` + `## Test Naming Conventions` |
| Test data strategy | `$SKILL_DIR/../testing-patterns/references/test-data.md` | `## Factories` + `## Builders` |

**How to read sections:** Grep for heading to find line number, Read with offset+limit.

## PHP Assertion Quick Reference

| Use | Instead Of | Why |
|-----|-----------|-----|
| `assertSame($expected, $actual)` | `assertEquals()` | Strict type comparison — `assertEquals('1', 1)` passes silently |
| `assertCount(3, $items)` | `assertSame(3, count($items))` | Better failure messages |
| `assertInstanceOf(Foo::class, $obj)` | `assertTrue($obj instanceof Foo)` | Better failure messages |
| `assertStringContainsString()` | `assertTrue(strpos(...) !== false)` | Readable, descriptive |
| `@dataProvider` | Duplicated test methods | DRY, parameterized, each case is a named data set |
| `$this->factory->post->create()` | `wp_insert_post()` | WordPress factory handles cleanup |
| `expectException(E::class)` | `try/catch` + `fail()` | PHPUnit native exception testing |

## PHP Red Flags

| Pattern | Why Harmful | Fix |
|---------|------------|-----|
| `assertTrue(true)` | No assertion at all | Add meaningful assertion |
| `assertEquals` over `assertSame` | Type coercion hides bugs | Use `assertSame` |
| Missing `@dataProvider` | Duplicated test logic | Extract data provider |
| Direct DB queries in unit tests | Slow, fragile | Use WordPress factories |
| `setUp()` without `parent::setUp()` | WP state not initialized | Add `parent::setUp()` |
| Mocking owned classes | Design smell | Refactor for loose coupling |
| Missing `tearDown()` cleanup | State leaks | Restore globals, remove filters |
| `wp_insert_post()` in unit tests | No automatic cleanup | Use `$this->factory->post->create()` |

## WordPress Test Utilities

- **WP_UnitTestCase** — base class with DB transactions, factory access, assertion helpers
- **Factories** — `$this->factory->post`, `->user`, `->term`, `->comment` with `create()` and `create_and_get()`
- **Brain Monkey** — isolate WordPress functions in true unit tests (`Functions\when('get_option')->justReturn('value')`)
- **WC_Unit_Test_Case** — WooCommerce base class with product/order helpers
