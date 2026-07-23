---
name: rust-testing-patterns
description: Use when writing or reviewing Rust tests - built-in test framework, assert macros, Result-based tests, async test patterns, mockall, proptest, rstest, test-case, insta snapshots, criterion benchmarks, and serial_test isolation.
---

## Skill Directory

Resolve `SKILL_DIR` to the absolute directory containing this `SKILL.md`, as
shown by the current host, before using any bundled path below.
Before a shell command uses `$SKILL_DIR`, assign it in that command or replace
it with the resolved path. It is not a host-exported environment variable.

# Rust Testing Patterns

Apply these Rust testing patterns when writing or reviewing tests. Flag red flags during review. Use the assertion quick reference when writing assertions. For shared test quality principles (philosophy, smells, mocking decisions), see `testing-patterns`.

## Reference Routing

| Need | Reference File | Sections to Read |
|------|---------------|-----------------|
| Rust testing patterns | `$SKILL_DIR/../testing-patterns/references/rust-testing-patterns.md` | Full file (~350L, manageable) |
| Behavior vs implementation | `$SKILL_DIR/../testing-patterns/references/test-philosophy.md` | `## The Fundamental Shift` + `## Four Core Principles` |
| Flaky/brittle tests | `$SKILL_DIR/../testing-patterns/references/test-smells.md` | `## The Six Major Test Smells` (relevant subsection) |
| Mock usage decisions | `$SKILL_DIR/../testing-patterns/references/mocking-strategies.md` | `## The Mocking Decision Framework` |
| AAA pattern/naming | `$SKILL_DIR/../testing-patterns/references/test-structure.md` | `## The AAA Pattern` + `## Test Naming Conventions` |
| Test data strategy | `$SKILL_DIR/../testing-patterns/references/test-data.md` | `## Factories` + `## Builders` |

**How to read sections:** Grep for heading to find line number, Read with offset+limit.

## Rust Assertion Quick Reference

| Use | Instead Of | Why |
|-----|-----------|-----|
| `assert_eq!(got, want)` | `assert!(got == want)` | Diff output on failure |
| `assert!(result.is_err())` | `#[should_panic]` | Explicit, no false positives |
| `result.unwrap_err()` + assert | `#[should_panic(expected = "...")]` | Better failure messages |
| `prop_assert!(expr)` | `assert!(expr)` in proptest | Integrates with shrinking |
| `tempfile::TempDir` | `fs::create_dir("/tmp/...")` | RAII cleanup, no collisions |
| `#[serial]` | `-- --test-threads=1` | Granular, per-resource isolation |
| `#[tokio::test(flavor = "multi_thread")]` | `#[tokio::test]` for concurrency | Catches race conditions |

## Rust Red Flags

**Safety and correctness — assertions that lie or miss bugs:**

| Pattern | Why Harmful | Fix |
|---------|------------|-----|
| `#[should_panic]` without `expected` | Any panic passes — setup unwrap, bounds check, anything | Add `expected = "specific message"` |
| `debug_assert!` in unsafe code | Stripped in release → undefined behavior | Use `assert!` |
| Side effects in `debug_assert!` | Different behavior debug vs release | Extract side effect, then assert |
| `assert!(a == b)` | No diff on failure | Use `assert_eq!(a, b)` |
| `assert_eq!` on floats | Imprecision failures | `(a - b).abs() < epsilon` or `approx` |
| Result test without assertions | `parse(x)?; Ok(())` only checks "no error" | Assert on the returned value |

**Async and concurrency — race conditions and silent failures:**

| Pattern | Why Harmful | Fix |
|---------|------------|-----|
| Spawned tasks not awaited | Panics inside `tokio::spawn` silently lost | `.await` the `JoinHandle` |
| Single-threaded async test for multi-threaded prod | Misses race conditions | `flavor = "multi_thread"` |
| `block_on()` inside `#[tokio::test]` | Deadlocks single-threaded runtime | Use `.await` directly |
| Shared mutable state without `#[serial]` | Flaky under parallel execution | Use `serial_test` crate |

**Tooling and hygiene — maintenance debt:**

| Pattern | Why Harmful | Fix |
|---------|------------|-----|
| Manual temp dirs | No cleanup on panic, parallel collisions | Use `tempfile::TempDir` |
| `tests/common.rs` (not `tests/common/mod.rs`) | Phantom test crate in output | Use subdirectory convention |
| `.proptest-regressions/` not committed | Previously-found bugs not retested | Add to VCS |
| `.snap.new` committed or `INSTA_UPDATE=always` in CI | Unreviewed snapshot changes | Fail CI on `.snap.new` |
| `#[ignore]` without reason | Hidden tech debt | `#[ignore = "reason"]` |

## Inline Unit Test Template

```rust
#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn rejects_empty_input() {
        let err = parse("").unwrap_err();
        assert!(err.to_string().contains("empty"));
    }

    #[test]
    fn parses_valid_version() {
        let v = parse("v1.2.3").unwrap();
        assert_eq!(v, Version { major: 1, minor: 2, patch: 3 });
    }
}
```

## Trait-Based Mocking (`mockall`)

```rust
#[automock]
trait Store {
    fn get(&self, id: &str) -> Option<Item>;
}

#[test]
fn service_finds_item() {
    let mut mock = MockStore::new();
    mock.expect_get()
        .with(eq("item-1"))
        .returning(|_| Some(Item { name: "Widget".into() }));

    let svc = Service::new(Box::new(mock));
    assert_eq!(svc.find("item-1").unwrap().name, "Widget");
}
```

## Mocking Principle

Mock at boundaries (network, disk, time), not internal functions. Tests that over-mock verify mock wiring, not real behavior. When unsure, see `testing-patterns` → `mocking-strategies.md`.
