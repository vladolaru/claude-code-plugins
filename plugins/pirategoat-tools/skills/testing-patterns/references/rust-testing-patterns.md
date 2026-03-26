# Rust Testing Patterns

Testing patterns for Rust using the built-in test framework, common assertion patterns, and popular test crates.

## Quick Reference: Assertions

| Macro | Purpose | In Release? |
|---|---|---|
| `assert!(expr)` | Boolean check | Yes |
| `assert_eq!(left, right)` | Equality with diff (needs `PartialEq` + `Debug`) | Yes |
| `assert_ne!(left, right)` | Inequality | Yes |
| `debug_assert!(expr)` | Boolean check, debug only | No |
| `debug_assert_eq!(a, b)` | Equality, debug only | No |
| `assert!(result.is_err())` | Error presence (preferred over `#[should_panic]`) | Yes |
| `prop_assert!(expr)` | Proptest-aware boolean (integrates with shrinking) | Yes |

**`assert!` vs `debug_assert!`:** `assert!` runs in all builds — use for invariants that must hold in production. `debug_assert!` is stripped in release — use only for internal assumptions where tests provide sufficient coverage.

---

## Test Organization

### Unit Tests (Inline `mod tests`)

```rust
#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn parses_valid_input() {
        let result = parse("v1.2.3");
        assert_eq!(result.unwrap(), Version { major: 1, minor: 2, patch: 3 });
    }
}
```

- Live in the same file, compiled only during `cargo test`.
- Can access **private** functions via `use super::*` (by design).
- Run with `cargo test --lib`.

### Integration Tests (`tests/` directory)

```
tests/
  common/
    mod.rs          -- shared helpers (NOT tests/common.rs)
  integration_a.rs  -- uses `mod common;`
  integration_b.rs
```

- Each file compiles as a **separate crate** — only accesses the public API.
- No `#[cfg(test)]` needed.
- Run with `cargo test --tests`.

### Doc Tests

```rust
/// Parses a version string.
///
/// ```
/// let v = my_crate::parse("v1.2.3").unwrap();
/// assert_eq!(v.major, 1);
/// ```
pub fn parse(input: &str) -> Result<Version, Error> { ... }
```

Run with `cargo test --doc`. Lines starting with `#` are compiled but hidden from rendered docs.

### Organization Anti-Patterns

| Anti-Pattern | Why Harmful | Fix |
|---|---|---|
| `tests/common.rs` instead of `tests/common/mod.rs` | Creates phantom test crate with "running 0 tests" | Use subdirectory convention |
| Integration tests accessing private internals | Broken test boundary | Move to unit tests or redesign API |
| No integration tests at all | Public API contract untested | Add `tests/` directory |
| `#[ignore]` or `no_run` on doc tests without comment | May hide broken examples | Document why or fix |

---

## Result-Based Tests

```rust
#[test]
fn parse_config() -> Result<(), Box<dyn std::error::Error>> {
    let config = parse("valid input")?;
    assert_eq!(config.name, "expected");
    Ok(())
}
```

Use when tests involve multiple fallible operations. `anyhow::Result<()>` is popular.

### Result Test Anti-Patterns

| Anti-Pattern | Why Harmful | Fix |
|---|---|---|
| Result return but never uses `?` | Noise, misleading signature | Remove `Result` return type |
| `parse(input)?; Ok(())` without checking value | Only checks "no error", not correctness | Add `assert_eq!` on result |
| `#[should_panic]` + Result return | `Err` doesn't panic — test fails for wrong reason | These are mutually exclusive |

---

## `#[should_panic]` Patterns

```rust
#[test]
#[should_panic(expected = "index out of bounds")]
fn panics_on_invalid_index() {
    access_item(vec![], 5);
}
```

The `expected` parameter does a **substring match** against the panic message.

### Better Alternative for Error Testing

```rust
#[test]
fn rejects_invalid_input() {
    let err = parse("bad").unwrap_err();
    assert!(err.to_string().contains("invalid"));
}
```

### Should-Panic Anti-Patterns

| Anti-Pattern | Why Harmful | Fix |
|---|---|---|
| `#[should_panic]` without `expected` | ANY panic passes — setup `.unwrap()`, bounds check, anything | Add `expected = "specific message"` |
| `#[should_panic]` + `.unwrap()` in setup | Setup unwrap panic satisfies test | Use `Result::is_err()` instead |
| Overly broad `expected` substring | `expected = "error"` matches everything | Be specific to identify the exact panic |

---

## Async Test Patterns

### Tokio

```rust
#[tokio::test]
async fn fetches_data() {
    let result = fetch_data().await;
    assert!(result.is_ok());
}

// Force multi-threaded to catch race conditions:
#[tokio::test(flavor = "multi_thread", worker_threads = 2)]
async fn concurrent_test() { ... }

// Paused time for deterministic time-dependent tests:
#[tokio::test(start_paused = true)]
async fn timeout_test() {
    tokio::time::sleep(Duration::from_secs(3600)).await; // instant
}
```

**Critical:** `#[tokio::test]` defaults to single-threaded (`current_thread`), while `#[tokio::main]` defaults to multi-threaded. Race conditions only visible under multi-threading will pass in tests.

### Async Anti-Patterns

| Anti-Pattern | Why Harmful | Fix |
|---|---|---|
| `block_on()` inside `#[tokio::test]` | Deadlocks single-threaded runtime | Use `.await` directly |
| Single-threaded test for multi-threaded prod code | Misses race conditions | Use `flavor = "multi_thread"` |
| Spawned tasks not awaited | Panics inside `tokio::spawn` silently lost | `.await` the `JoinHandle` |
| Real `sleep()` in async tests | Slow and non-deterministic | Use `start_paused = true` |
| Error types not `Send + Sync` | Works single-thread, fails multi-thread | Make errors thread-safe |

---

## Test Fixtures and Cleanup

### Pattern 1: Helper Functions

```rust
fn setup() -> TestContext {
    TestContext { db: create_test_db(), user: create_test_user() }
}
```

### Pattern 2: RAII/Drop for Cleanup

```rust
struct TempDb { path: PathBuf }
impl Drop for TempDb {
    fn drop(&mut self) { std::fs::remove_dir_all(&self.path).ok(); }
}
```

`Drop` ensures cleanup even if the test panics. This is idiomatic Rust.

### Pattern 3: `rstest` Fixtures

```rust
#[fixture]
fn db() -> TestDb { TestDb::new() }

#[rstest]
fn test_query(db: TestDb) {
    db.execute("SELECT 1");
}
```

### Pattern 4: `LazyLock` for Expensive Shared Setup

```rust
static TEST_DB: LazyLock<TestDb> = LazyLock::new(|| TestDb::new());
```

### Cleanup Anti-Patterns

| Anti-Pattern | Why Harmful | Fix |
|---|---|---|
| Manual `fs::remove_dir_all()` at end | Won't run on panic | Use `Drop` or `tempfile::TempDir` |
| `std::fs::create_dir("/tmp/test_dir")` | Collides in parallel, no cleanup on panic | Use `tempfile::TempDir` |
| Shared mutable state without `#[serial]` | Flaky under parallel execution | Use `serial_test` or per-test state |
| `static mut` for shared state | Data race UB | Use `Mutex` + `#[serial]` |

---

## Parameterized Tests

### `rstest` with `#[case]`

```rust
#[rstest]
#[case("v1.2.3", Version { major: 1, minor: 2, patch: 3 })]
#[case("v0.0.0", Version { major: 0, minor: 0, patch: 0 })]
fn parses_version(#[case] input: &str, #[case] expected: Version) {
    assert_eq!(parse(input).unwrap(), expected);
}
```

### `test-case` Macro

```rust
#[test_case("v1.2.3", 1 ; "major version")]
#[test_case("v0.0.0", 0 ; "zero version")]
fn extracts_major(input: &str, expected: u32) {
    assert_eq!(parse(input).unwrap().major, expected);
}
```

---

## Mocking

### `mockall` — Trait-Based Mocking

```rust
#[automock]
trait Store {
    fn get(&self, id: &str) -> Option<Item>;
}

#[test]
fn uses_store() {
    let mut mock = MockStore::new();
    mock.expect_get()
        .with(eq("item-1"))
        .returning(|_| Some(Item { name: "Widget".into() }));

    let svc = Service::new(Box::new(mock));
    assert_eq!(svc.find("item-1").unwrap().name, "Widget");
}
```

Mock only at boundaries (network, disk, time). Over-mocking tests mock behavior, not real code.

### HTTP Mocking (`wiremock` / `httpmock`)

Use actual mock HTTP servers for testing code that makes HTTP requests. Different purpose than `mockall`.

---

## Property-Based Testing (`proptest`)

```rust
use proptest::prelude::*;

proptest! {
    #[test]
    fn roundtrip(input in any::<String>()) {
        let encoded = encode(&input);
        let decoded = decode(&encoded).unwrap();
        prop_assert_eq!(input, decoded);
    }
}
```

### Proptest Rules

| Rule | Why |
|---|---|
| Use `prop_assert!` not `assert!` | Integrates with shrinking, prints minimal failure only |
| Commit `.proptest-regressions/` | Previously-found bugs retested automatically |
| Encode actual invariants | "Doesn't crash" is weak — use roundtrip, commutativity, idempotency |
| Avoid over-constrained `prop_filter` | Rejecting 99% of inputs is slow — use `prop_flat_map` instead |

---

## Snapshot Testing (`insta`)

```rust
assert_snapshot!(render_output(&input));
assert_json_snapshot!(api_response, {
    ".id" => "[uuid]",
    ".created_at" => "[timestamp]",
});
```

**Workflow:** `cargo test` → `.snap.new` created → `cargo insta review` → approve/reject.

### Insta Anti-Patterns

| Anti-Pattern | Why Harmful | Fix |
|---|---|---|
| `.snap.new` files committed to VCS | Unreviewed snapshot changes | CI should fail on their presence |
| `INSTA_UPDATE=always` in CI | Auto-accepts without review | Use `INSTA_UPDATE=no` in CI |
| No redactions on timestamps/UUIDs | Flaky on every run | Use `"[timestamp]"` redactions |
| Inline snapshots for large output | Clutters source | Use `.snap` files |

---

## Benchmarks (`criterion`)

```rust
use criterion::{criterion_group, criterion_main, Criterion};

fn bench_parse(c: &mut Criterion) {
    c.bench_function("parse version", |b| {
        b.iter(|| parse("v1.2.3"))
    });
}

criterion_group!(benches, bench_parse);
criterion_main!(benches);
```

Requires `harness = false` in `[[bench]]` section of `Cargo.toml`.

---

## Serialization and Execution

### `#[serial]` for Shared State

```rust
use serial_test::serial;

#[test]
#[serial]
fn modifies_global_config() { ... }

#[test]
#[serial(database)]
fn writes_to_db() { ... }
```

- `#[serial]` without key serializes against all other keyless `#[serial]` tests.
- `#[serial(key)]` serializes only against tests with the same key.
- `#[file_serial]` for cross-process (integration tests, doc tests).
- **Caution:** `#[serial]` (in-memory) and `#[file_serial]` don't coordinate with each other.

---

## `cargo test` Quick Reference

| Flag | Effect |
|---|---|
| `--lib` | Unit tests only |
| `--tests` | Integration tests only |
| `--doc` | Doc tests only |
| `--release` | Release mode (strips `debug_assert!`) |
| `-- --test-threads=1` | Force sequential execution |
| `-- --nocapture` | Show stdout/stderr |
| `-- --ignored` | Run `#[ignore]` tests only |
| `-- filter` | Run tests matching substring |

---

## Assertion Anti-Patterns

| Anti-Pattern | Why Harmful | Fix |
|---|---|---|
| `assert!(a == b)` instead of `assert_eq!` | No diff on failure | `assert_eq!(a, b)` |
| `debug_assert!` in unsafe code | Stripped in release → UB | Use `assert!` |
| Side effects in `debug_assert!` | Different behavior in release | Extract side effect, then assert |
| `assert_eq!` on floats | Floating-point imprecision | Use `(a - b).abs() < epsilon` or `approx` crate |
| Missing custom messages | Cryptic failure output | `assert!(cond, "context: {}", var)` |
| `assert!(collection.len() > 0)` | Passes with wrong items | Check specific items with `assert_eq!` |

---

## General Anti-Patterns

| Anti-Pattern | Why Harmful | Fix |
|---|---|---|
| `#[should_panic]` without `expected` | Any panic passes | Add `expected = "message"` |
| Tests sharing files/env without `#[serial]` | Parallel races | Use `#[serial]`, `tempfile`, or isolation |
| Real network calls | Flaky, slow | Use `wiremock`/`httpmock` |
| `thread::sleep()` for sync | Timing-dependent, slow | Use channels, barriers, condvars |
| `#[ignore]` without reason | Hidden tech debt | Use `#[ignore = "reason"]` |
| Test names like `test_it_works` | Useless on failure | Name by behavior: `rejects_empty_input` |
| `#[bench]` (unstable) | Nightly-only, limited | Use `criterion` |
| `harness = false` without need | Loses filtering, capture, parallelism | Only for custom harnesses/criterion |

---

## Best Practices Summary

| Do | Don't |
|----|-------|
| Use `assert_eq!`/`assert_ne!` for comparisons | Use `assert!(a == b)` |
| Use `Result::is_err()` for error testing | Use `#[should_panic]` without `expected` |
| Use `tempfile::TempDir` for temp dirs | Manually create/remove temp dirs |
| Use `Drop` for test cleanup | Rely on code at end of test running |
| Use `#[serial]` for shared state | Hope parallel execution doesn't collide |
| Use `prop_assert!` in proptest | Use `assert!` in proptest |
| Commit `.proptest-regressions/` | Ignore regression files |
| Use `criterion` for benchmarks | Use unstable `#[bench]` |
| Name tests by behavior | Use `test1`, `it_works` |
| Use `flavor = "multi_thread"` for concurrency tests | Rely on default single-threaded runtime |
| Await spawned task `JoinHandle`s | Let spawned tasks panic silently |
| Redact non-deterministic snapshot fields | Assert on timestamps/UUIDs directly |
