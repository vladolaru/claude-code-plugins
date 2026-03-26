---
name: python-testing-patterns
description: Use when writing or reviewing Python tests - pytest fixtures, parametrize, conftest, mock/patch patterns, pytest-asyncio, hypothesis property-based testing, freezegun/time-machine, factory_boy, and coverage configuration.
---

# Python Testing Patterns

pytest and unittest patterns and common libraries. For shared test quality principles (philosophy, smells, mocking decisions), see `testing-patterns`.

## Reference Routing

| Need | Reference File | Sections to Read |
|------|---------------|-----------------|
| Python testing patterns | `../testing-patterns/references/python-testing-patterns.md` | Full file (~430L, manageable) |
| Behavior vs implementation | `../testing-patterns/references/test-philosophy.md` | `## The Fundamental Shift` + `## Four Core Principles` |
| Flaky/brittle tests | `../testing-patterns/references/test-smells.md` | `## The Six Major Test Smells` (relevant subsection) |
| Mock usage decisions | `../testing-patterns/references/mocking-strategies.md` | `## The Mocking Decision Framework` |
| AAA pattern/naming | `../testing-patterns/references/test-structure.md` | `## The AAA Pattern` + `## Test Naming Conventions` |
| Test data strategy | `../testing-patterns/references/test-data.md` | `## Factories` + `## Builders` |

**How to read sections:** Grep for heading to find line number, Read with offset+limit.

## Python Assertion Quick Reference

| Use | Instead Of | Why |
|-----|-----------|-----|
| `assert x == y` | `self.assertEqual(x, y)` | pytest introspection shows diff |
| `assert x == pytest.approx(y)` | `assert x == 0.3` | Float-safe comparison |
| `with pytest.raises(Exc, match=r"...")` | bare `try/except` | Validates exception type + message |
| `mock.assert_called_once_with(...)` | `mock.called_once_with(...)` | The typo silently passes (returns Mock) |
| `AsyncMock(return_value=...)` | `Mock(return_value=...)` for async | Mock can't be awaited correctly |
| `@patch("using_module.fn")` | `@patch("defining_module.fn")` | Patch where the name is looked up |
| `Mock(spec=Cls)` / `create_autospec(Cls)` | bare `Mock()` | Catches typos and wrong signatures |
| `monkeypatch.setattr(...)` | direct attribute assignment | Auto-restores after test |
| `factory.LazyAttribute(lambda o: [])` | `tags = []` at class level | Avoids shared mutable default |

## Python Red Flags

| Pattern | Why Harmful | Fix |
|---------|------------|-----|
| `mock.called_once_with(...)` | Truthy Mock, not an assertion | `mock.assert_called_once_with(...)` |
| `async def test_*` without marker (strict) | Body never runs, silent PASS | Add `@pytest.mark.asyncio` or use `auto` mode |
| `Mock()` without `spec=` | Accepts any attribute/typo | Use `spec=` or `create_autospec()` |
| `@patch` at definition site | Mock has no effect | Patch where the name is imported |
| Session-scoped fixture + mutable return | State leaks across tests | Scope `function` or return immutable |
| `@freeze_time` but fixture uses `now()` | Fixture outside freeze context | Use `@pytest.mark.freeze_time` |
| `setUp` without `super().setUp()` | Base class setup skipped | Call `super().setUp()` first |
| `time.sleep(N)` for sync | Flaky under CI load | Event/queue-based sync |
| `assert x == float_expr` | Imprecision failures | `pytest.approx()` |
| `tags = []` in factory class | Shared mutable default | `factory.LazyAttribute(lambda o: [])` |
| `.hypothesis/` in `.gitignore` | Prior failures not retested | Commit `.hypothesis/examples/` |

## Parametrize Template

```python
@pytest.mark.parametrize("input_val,expected", [
    pytest.param("abc", 3, id="ascii"),
    pytest.param("", 0, id="empty"),
    pytest.param(None, 0, id="none", marks=pytest.mark.xfail(strict=True)),
])
def test_length(input_val, expected):
    assert compute_length(input_val) == expected
```

## Factory Fixture Template

```python
@pytest.fixture
def make_user():
    created = []
    def _make(name="alice", role="viewer"):
        user = User(name=name, role=role)
        created.append(user)
        return user
    yield _make
    for u in created:
        u.delete()
```

## Async Test Patterns

```python
# Auto mode (recommended): all async tests auto-collected
# pyproject.toml: asyncio_mode = "auto"

async def test_create_user():
    user = await create_user("alice")
    assert user.id is not None

# Session-scoped async fixture
@pytest_asyncio.fixture(scope="session", loop_scope="session")
async def db_pool():
    pool = await create_pool()
    yield pool
    await pool.close()
```

Mock at boundaries, not internals. Over-mocking tests mock behavior, not real code.
