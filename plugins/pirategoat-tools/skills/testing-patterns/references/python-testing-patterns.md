# Python Testing Patterns

Testing patterns for Python using pytest, unittest, and common libraries.

## Quick Reference: Assertions

| pytest (preferred) | unittest equivalent | Use When |
|---|---|---|
| `assert x == y` | `self.assertEqual(x, y)` | Value equality (pytest shows diff) |
| `assert x is y` | `self.assertIs(x, y)` | Identity check |
| `assert x in collection` | `self.assertIn(x, collection)` | Membership |
| `assert x == pytest.approx(y)` | `self.assertAlmostEqual(x, y)` | Float comparison |
| `with pytest.raises(ExcType)` | `self.assertRaises(ExcType)` | Exception check |
| `with pytest.raises(ExcType, match=r"regex")` | `self.assertRaisesRegex(ExcType, r"regex")` | Exception + message match |
| `assert result is None` | `self.assertIsNone(result)` | None check |
| `assert isinstance(x, Cls)` | `self.assertIsInstance(x, Cls)` | Type check |

**pytest assertion rewriting:** pytest rewrites `assert` statements to produce detailed introspection. This only works in test modules collected by pytest — assertions in helper modules need `pytest.register_assert_rewrite("module")` in `conftest.py`.

**Caution:** `assert expr, "message"` disables pytest's introspection — only the message string is shown on failure. Prefer bare `assert` unless the message adds information the diff wouldn't show.

---

## Test Organization

### pytest layout

```
project/
  src/mypackage/
    __init__.py
    core.py
  tests/
    conftest.py           # shared fixtures, auto-discovered
    unit/
      conftest.py         # unit-specific fixtures
      test_core.py
    integration/
      conftest.py
      test_api.py
```

- `conftest.py` fixtures are auto-imported — no explicit import needed.
- Nested `conftest.py` files can shadow parent fixtures (intentional override or accidental hiding).
- Prefer `tests/` at project root. `src/` layout prevents accidental imports of uninstalled code.

### unittest layout

```python
import unittest

class TestOrderCreate(unittest.TestCase):
    def setUp(self):
        super().setUp()  # CRITICAL when subclassing
        self.client = Client()

    def test_creates_order(self):
        order = self.client.create_order(items=["widget"])
        self.assertEqual(order.status, "pending")

    def tearDown(self):
        self.client.close()
        super().tearDown()
```

- `setUp`/`tearDown` must call `super()` when subclassing.
- `addCleanup(fn)` is safer than `tearDown` — runs regardless of `setUp` outcome, LIFO order.

---

## Fixtures

### Scope and Mutability

```python
# DANGEROUS — session scope + mutable return = shared state across all tests
@pytest.fixture(scope="session")
def db_records():
    return []  # same list object for every test

# SAFE — function scope (default), fresh per test
@pytest.fixture
def db_records():
    return []

# SAFE — session scope but immutable handle
@pytest.fixture(scope="session")
def db_connection():
    conn = create_connection()
    yield conn
    conn.close()
```

**Rule:** Broad scope (`session`, `module`) only for expensive, immutable resources. Never return mutable containers from broad-scoped fixtures.

### Factory Fixtures

```python
@pytest.fixture
def make_user():
    """Factory fixture — returns a callable for custom user creation."""
    created = []
    def _make_user(name="alice", role="viewer"):
        user = User(name=name, role=role)
        created.append(user)
        return user
    yield _make_user
    for u in created:
        u.delete()
```

Prefer named concrete fixtures for common cases. Use factory fixtures when variability is the point.

### conftest Shadowing

A nested `conftest.py` can silently override a root-level fixture with the same name. Tests behave differently depending on directory. This is powerful when intentional but a trap when accidental.

**Detection:** If a fixture exists in multiple conftest files, verify it's intentional.

---

## Parametrize

```python
@pytest.mark.parametrize("input_val,expected", [
    pytest.param("abc", 3, id="ascii-string"),
    pytest.param("", 0, id="empty-string"),
    pytest.param("日本語", 3, id="unicode-string"),
    pytest.param(None, 0, id="none-input", marks=pytest.mark.xfail(strict=True)),
], ids=str)
def test_length(input_val, expected):
    assert compute_length(input_val) == expected
```

- Always use `id=` or `ids=` with 3+ cases — `test_foo[0]`, `test_foo[1]` is useless in CI.
- Use `pytest.param(..., marks=...)` to mark individual cases (not the whole function).
- Beware shared mock objects across parametrized runs — module-scoped mock return values are reused.

---

## Mocking

### Patch Target Rule

```python
# module: logic.py
from data_source import fetch_user_data

def get_user(uid):
    return fetch_user_data(uid)

# WRONG — patches the original, but logic.py holds its own reference
@patch("data_source.fetch_user_data")
def test_get_user_wrong(mock_fetch): ...

# CORRECT — patch where it's looked up
@patch("logic.fetch_user_data")
def test_get_user_correct(mock_fetch): ...
```

**Always patch where the name is used, not where it's defined.**

### spec and autospec

```python
# DANGEROUS — accepts ANY attribute/method, including typos
mock = Mock()
mock.prcess_payment(100)  # typo: no error, test passes

# SAFE — restricts to actual interface
mock = Mock(spec=PaymentService)
mock.prcess_payment(100)  # AttributeError: typo caught

# SAFEST — also validates call signatures
mock = create_autospec(PaymentService)
mock.process_payment(100, "usd", extra=True)  # TypeError: wrong signature
```

### Mock Method Typos (CRITICAL)

```python
# SILENT BUG — mock.called_once_with is NOT an assertion method
mock.called_once_with(arg)  # returns a new Mock (truthy), always "passes"

# CORRECT
mock.assert_called_once_with(arg)
```

`Mock` creates attributes on access. `mock.called_once_with(x)` silently returns a `Mock` object (truthy). Only methods starting with `assert_` actually assert.

### AsyncMock

```python
# WRONG — Mock() for async function returns a Mock, not a coroutine
mock_fetch = Mock(return_value={"id": 1})
result = await mock_fetch()  # TypeError or RuntimeWarning

# CORRECT
mock_fetch = AsyncMock(return_value={"id": 1})
result = await mock_fetch()
```

Use `AsyncMock` for any function that will be `await`ed. `Mock` creates a synchronous callable — `await`ing it either raises `TypeError` or produces a `RuntimeWarning: coroutine was never awaited`.

### Decorator Order

```python
# Decorators apply bottom-up, so arguments are reversed:
@patch("module.ClassA")   # → mock_b (second positional arg)
@patch("module.ClassB")   # → mock_a (first positional arg)
def test_something(mock_a, mock_b):
    pass
```

### Deep Mock Chains

```python
# FRAGILE — breaks on any internal refactor
mock_svc.get_client.return_value.fetch.return_value.parse.return_value = "ok"

# BETTER — fake at the boundary
class FakeClient:
    def fetch(self, url):
        return FakeResponse(data="ok")
```

---

## Async Testing (pytest-asyncio)

### Mode Configuration

```toml
# pyproject.toml
[tool.pytest.ini_options]
asyncio_mode = "auto"  # all async def test_* are async tests
```

| Mode | Behavior | Risk |
|---|---|---|
| `strict` (default) | Requires `@pytest.mark.asyncio` on every async test | Missing marker → test body never executes (silent PASS) |
| `auto` | All `async def test_*` auto-collected | Safer, recommended for single-async-lib projects |

### Silent Pass Bug (CRITICAL)

```python
# strict mode, missing marker:
async def test_create_user():
    user = await create_user("alice")
    assert user.id is not None  # NEVER RUNS — pytest gets a coroutine (truthy), marks PASSED
```

### Event Loop Scope

```python
# Session-scoped async fixture needs session-scoped event loop
@pytest_asyncio.fixture(scope="session", loop_scope="session")
async def db_pool():
    pool = await create_pool()
    yield pool
    await pool.close()
```

Default loop scope is `function`. Session-scoped async fixtures with function-scoped loops → `ScopeMismatch`.

### Async Fixtures

In strict mode, async fixtures MUST use `@pytest_asyncio.fixture`, not `@pytest.fixture`.

---

## Property-Based Testing (Hypothesis)

```python
from hypothesis import given, example, settings
import hypothesis.strategies as st

@given(st.text(min_size=1))
@example("")  # pin known edge case
@settings(max_examples=200)
def test_roundtrip_encode(s):
    assert decode(encode(s)) == s
```

### Key Patterns

- **Commit `.hypothesis/examples/`** to VCS — previously-found failures must be retested.
- **Use `@example()`** for known tricky inputs on top of generated ones.
- **Don't over-constrain:** `st.integers(min_value=1, max_value=3)` is just parametrize with overhead.
- **Use `assume()`** to filter invalid inputs, but check that it doesn't filter >90% (health check).
- **`strict=True` on `@settings`** — catches accidental over-filtering.

---

## Time Mocking (freezegun / time-machine)

### Fixture Interaction Bug

```python
@freeze_time("2024-01-01")
class TestExpiry:
    @pytest.fixture
    def token(self):
        return Token(created_at=datetime.now())  # NOT frozen — fixture runs outside freeze

    def test_is_expired(self, token):
        assert token.is_expired()  # fails unpredictably
```

`@freeze_time` on a class/function does NOT affect pytest fixture setup. Use `pytest-freezegun`'s `@pytest.mark.freeze_time` or pass time explicitly.

### Cleanup

```python
@pytest.fixture
def frozen():
    freezer = freeze_time("2024-01-01")
    freezer.start()
    yield freezer
    freezer.stop()  # MUST stop — otherwise all subsequent tests run in frozen time
```

---

## HTTP Mocking

### Use Transport-Level Libraries

```python
# FRAGILE — breaks on session/retry changes
with patch("requests.get") as mock_get:
    mock_get.return_value.json.return_value = {...}

# CORRECT (responses library for requests-based code)
@responses.activate
def test_api_call():
    responses.add(responses.GET, "https://api.example.com/users", json=[...])
    result = fetch_users()
    assert len(result) == 3

# CORRECT (pytest-httpx for httpx-based code)
async def test_api_call(httpx_mock):
    httpx_mock.add_response(url="https://api.example.com/users", json=[...])
    result = await fetch_users()
    assert len(result) == 3
```

### Verify All Mocks Were Called

`pytest-httpx` fails by default if registered responses aren't called. Disabling `assert_all_responses_were_requested` hides dead setup that diverges from actual behavior.

---

## factory_boy

### Mutable Default Trap

```python
# WRONG — shared across ALL instances
class UserFactory(factory.Factory):
    class Meta:
        model = User
    tags = []          # same list object reused
    metadata = {}      # same dict object reused

# CORRECT
class UserFactory(factory.Factory):
    class Meta:
        model = User
    tags = factory.LazyAttribute(lambda o: [])
    metadata = factory.LazyAttribute(lambda o: {})
```

### Import-Time DB Queries

```python
# WRONG — runs DB query at class definition time
class PostFactory(factory.Factory):
    author = User.objects.create(id=1)

# CORRECT
class PostFactory(factory.Factory):
    author = factory.SubFactory(UserFactory)
```

### build() vs create()

`create()` hits the database. `build()` creates in-memory. Use `build()` for unit tests that don't need persistence — 5-10x faster.

---

## Test Isolation

### Monkey-Patching Without Cleanup

```python
# CATASTROPHIC — corrupts all subsequent tests
import datetime
datetime.datetime = FakeDatetime  # never restored

# CORRECT — auto-restores
def test_with_fake_time(monkeypatch):
    monkeypatch.setattr(datetime, "datetime", FakeDatetime)
```

Always use `monkeypatch` fixture, `mock.patch` context manager, or `try/finally`.

### Database State

Tests that write to a shared DB without cleanup → order-dependent failures. Use `pytest-randomly` to detect. Use `@pytest.mark.django_db` (auto-transaction rollback) or function-scoped fixtures with truncation.

### Global/Module State

```python
# DANGER — module-level mutable state shared across tests
_registry = {}

def test_register():
    _registry["key"] = "value"  # leaks to all subsequent tests
```

---

## Coverage

### Branch Coverage

```python
def is_eligible(age, verified):
    if age >= 18 and verified:
        return True
    return False
```

One test with `(20, True)` gives 100% line coverage but misses three of four branches. Enable `branch = True` in coverage config.

### pragma: no cover

Correct for `if TYPE_CHECKING:` blocks. Using it on business logic to suppress gaps is coverage fraud.

---

## xfail and skip

```python
# BAD — XPASS doesn't fail the suite
@pytest.mark.xfail
def test_known_bug(): ...

# GOOD — XPASS fails the suite, forcing marker removal when fixed
@pytest.mark.xfail(strict=True, reason="GH-123: race condition in cache")
def test_known_bug(): ...

# BAD — skip hides failures without explanation
@pytest.mark.skip
def test_broken(): ...

# GOOD — conditional skip with reason
@pytest.mark.skipif(sys.platform == "win32", reason="Unix-only feature")
def test_unix_only(): ...
```

Set `xfail_strict = true` globally in `pyproject.toml` to make `strict=True` the default.

---

## Float Assertions

```python
# WRONG — fails due to floating-point imprecision
assert 0.1 + 0.2 == 0.3

# CORRECT
assert 0.1 + 0.2 == pytest.approx(0.3)
assert result == pytest.approx(expected, rel=1e-6)

# Also correct (stdlib)
import math
assert math.isclose(result, expected, rel_tol=1e-9)
```

---

## unittest.TestCase in pytest Projects

### Missing super() Calls

```python
class MyTest(BaseTest):
    def setUp(self):
        self.client = Client()
        # MISSING: super().setUp() — BaseTest setup never runs

    def tearDown(self):
        self.client.close()
        # MISSING: super().tearDown() — BaseTest cleanup never runs
```

### setUpClass Shared State

```python
class TestBatch(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.items = []  # DANGER — shared, mutated by test methods

    def test_add(self):
        self.items.append("a")  # leaks to all subsequent tests in class
```

### addCleanup vs tearDown

`tearDown` only runs if `setUp` succeeded. `addCleanup(fn)` runs after the test regardless, in LIFO order. Prefer `addCleanup` for multi-resource setup.
