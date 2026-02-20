# Go Testing Patterns

Testing patterns for Go using the standard `testing` package and common libraries.

## Quick Reference: Assertions

Go's standard library uses manual checks with `t.Errorf`/`t.Fatalf`. Common assertion libraries add convenience:

| Standard Library | Testify Equivalent | Use When |
|------------------|-------------------|----------|
| `if got != want { t.Errorf(...) }` | `assert.Equal(t, want, got)` | Value equality |
| `if got == nil { t.Fatal(...) }` | `require.NotNil(t, got)` | Non-nil check (stop on fail) |
| `if !contains(slice, item) { ... }` | `assert.Contains(t, slice, item)` | Collection membership |
| `if err != nil { t.Fatalf(...) }` | `require.NoError(t, err)` | Error absence |
| `if err == nil { t.Fatal(...) }` | `assert.Error(t, err)` | Error presence |
| `if !errors.Is(err, target) { ... }` | `assert.ErrorIs(t, err, target)` | Specific error |

**`assert` vs `require`:** `assert` records failure and continues; `require` stops the test immediately. Use `require` for preconditions whose failure makes subsequent assertions meaningless.

---

## Basic Test Structure

```go
package order_test

import "testing"

func TestOrderCreate(t *testing.T) {
    // Arrange
    svc := NewOrderService()

    // Act
    order, err := svc.Create(OrderInput{Items: []Item{{Name: "Widget"}}})

    // Assert
    if err != nil {
        t.Fatalf("Create() error = %v, want nil", err)
    }
    if order.Status != "pending" {
        t.Errorf("Status = %q, want %q", order.Status, "pending")
    }
}
```

---

## Table-Driven Tests

The canonical Go test pattern. Use for any function with multiple input/output cases.

```go
func TestParseVersion(t *testing.T) {
    tests := []struct {
        name    string
        input   string
        want    Version
        wantErr bool
    }{
        {
            name:  "major minor patch",
            input: "v1.2.3",
            want:  Version{Major: 1, Minor: 2, Patch: 3},
        },
        {
            name:  "major only",
            input: "v1",
            want:  Version{Major: 1},
        },
        {
            name:    "empty string",
            input:   "",
            wantErr: true,
        },
    }

    for _, tt := range tests {
        t.Run(tt.name, func(t *testing.T) {
            got, err := ParseVersion(tt.input)
            if (err != nil) != tt.wantErr {
                t.Fatalf("ParseVersion(%q) error = %v, wantErr %v", tt.input, err, tt.wantErr)
            }
            if !tt.wantErr && got != tt.want {
                t.Errorf("ParseVersion(%q) = %v, want %v", tt.input, got, tt.want)
            }
        })
    }
}
```

### Table-Driven Anti-Patterns

| Anti-Pattern | Why Harmful | Fix |
|-------------|------------|-----|
| Missing `t.Run` | Can't run individual cases, unclear failure | Always wrap in `t.Run(tt.name, ...)` |
| Anonymous test cases | Hard to identify failures | Always provide `name` field |
| Huge struct with many fields | Hard to read, most fields zero-value | Use functional options or builder |
| Shared mutable state across cases | Cases interfere | Fresh state per `t.Run` |

---

## Subtests

```go
func TestOrderService(t *testing.T) {
    t.Run("Create", func(t *testing.T) {
        t.Run("with valid input", func(t *testing.T) {
            // test body
        })
        t.Run("with empty items", func(t *testing.T) {
            // test body
        })
    })

    t.Run("Cancel", func(t *testing.T) {
        // test body
    })
}
```

**Run specific subtest:** `go test -run TestOrderService/Create/with_valid_input`

---

## Test Helpers

```go
// Helper marks itself so failures point to the caller, not the helper
func newTestOrder(t *testing.T, opts ...func(*Order)) *Order {
    t.Helper()
    o := &Order{ID: "test-123", Status: "pending"}
    for _, opt := range opts {
        opt(o)
    }
    return o
}

func withStatus(s string) func(*Order) {
    return func(o *Order) { o.Status = s }
}

func TestCancelOrder(t *testing.T) {
    order := newTestOrder(t, withStatus("confirmed"))
    // ...
}
```

### `t.Helper()` Rules

- **Always** call `t.Helper()` as the first line in any function that calls `t.Fatalf`, `t.Errorf`, or other `t` methods
- Without it, failure messages point to the helper function instead of the actual test
- Applies to helper functions, not test functions

---

## TestMain

```go
func TestMain(m *testing.M) {
    // Setup (runs once before all tests in the package)
    setup()

    // Run all tests
    code := m.Run()

    // Teardown (runs once after all tests)
    teardown()

    os.Exit(code)
}
```

**Common uses:** Database setup, environment variable configuration, temp directory creation. Most tests don't need `TestMain` — prefer `t.Cleanup` for per-test teardown.

---

## Cleanup and Isolation

```go
func TestWithTempFile(t *testing.T) {
    dir := t.TempDir() // Auto-cleaned after test

    path := filepath.Join(dir, "test.txt")
    os.WriteFile(path, []byte("data"), 0644)
    // dir is removed when test completes
}

func TestWithEnv(t *testing.T) {
    t.Setenv("API_KEY", "test-key") // Auto-restored after test
    // Original value restored when test completes
}

func TestWithResource(t *testing.T) {
    db := openTestDB(t)
    t.Cleanup(func() {
        db.Close()
    })
    // db.Close() called when test completes
}
```

### Cleanup Anti-Patterns

| Anti-Pattern | Why Harmful | Fix |
|-------------|------------|-----|
| `os.Setenv` without restore | Leaks to other tests | Use `t.Setenv()` (Go 1.17+) |
| Manual temp dir without cleanup | Disk leak, test pollution | Use `t.TempDir()` |
| `defer` for test cleanup | Doesn't run if `t.FailNow`/`t.Fatal` | Use `t.Cleanup()` |
| Global state mutation | Tests depend on run order | Isolate state per test |

---

## Parallel Tests

```go
func TestParallel(t *testing.T) {
    tests := []struct {
        name  string
        input int
        want  int
    }{
        {"zero", 0, 0},
        {"positive", 5, 25},
        {"negative", -3, 9},
    }

    for _, tt := range tests {
        t.Run(tt.name, func(t *testing.T) {
            t.Parallel()
            got := Square(tt.input)
            if got != tt.want {
                t.Errorf("Square(%d) = %d, want %d", tt.input, got, tt.want)
            }
        })
    }
}
```

### Parallel Test Pitfalls

| Pitfall | Why Harmful | Fix |
|---------|------------|-----|
| Loop variable capture (pre-Go 1.22) | All subtests share last value | Use `tt := tt` or upgrade to Go 1.22+ |
| `t.Parallel()` with shared mutable state | Data race | Use `sync.Mutex` or isolate state |
| `t.Parallel()` without `t.Run` | Confusing behavior | Always pair with subtests |
| `t.Setenv` with `t.Parallel` | Panics (Go enforces this) | Use local config structs instead |

---

## HTTP Testing

```go
import (
    "net/http"
    "net/http/httptest"
    "testing"
)

func TestHealthHandler(t *testing.T) {
    req := httptest.NewRequest(http.MethodGet, "/health", nil)
    rec := httptest.NewRecorder()

    HealthHandler(rec, req)

    if rec.Code != http.StatusOK {
        t.Errorf("status = %d, want %d", rec.Code, http.StatusOK)
    }
    if body := rec.Body.String(); body != "ok" {
        t.Errorf("body = %q, want %q", body, "ok")
    }
}

func TestWithExternalService(t *testing.T) {
    // Mock external service
    server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
        w.WriteHeader(http.StatusOK)
        w.Write([]byte(`{"status":"ok"}`))
    }))
    t.Cleanup(func() { server.Close() })

    client := NewClient(server.URL) // Inject test server URL
    result, err := client.GetStatus()
    if err != nil {
        t.Fatalf("GetStatus() error = %v", err)
    }
    if result != "ok" {
        t.Errorf("status = %q, want %q", result, "ok")
    }
}
```

---

## Interface-Based Mocking

Go's preferred mocking pattern uses interfaces and test doubles:

```go
// Production code defines a small interface
type OrderStore interface {
    Save(ctx context.Context, order *Order) error
    FindByID(ctx context.Context, id string) (*Order, error)
}

// Test double implements the interface
type mockOrderStore struct {
    saveFunc    func(ctx context.Context, order *Order) error
    findFunc    func(ctx context.Context, id string) (*Order, error)
}

func (m *mockOrderStore) Save(ctx context.Context, order *Order) error {
    return m.saveFunc(ctx, order)
}

func (m *mockOrderStore) FindByID(ctx context.Context, id string) (*Order, error) {
    return m.findFunc(ctx, id)
}

func TestOrderService_Cancel(t *testing.T) {
    store := &mockOrderStore{
        findFunc: func(_ context.Context, id string) (*Order, error) {
            return &Order{ID: id, Status: "confirmed"}, nil
        },
        saveFunc: func(_ context.Context, order *Order) error {
            if order.Status != "cancelled" {
                t.Errorf("saved status = %q, want %q", order.Status, "cancelled")
            }
            return nil
        },
    }

    svc := NewOrderService(store)
    err := svc.Cancel(context.Background(), "order-123")
    if err != nil {
        t.Fatalf("Cancel() error = %v", err)
    }
}
```

### When to Use `mockall`/`gomock` vs Hand-Written Mocks

| Hand-Written | Generated (`mockall`, `gomock`) |
|-------------|-------------------------------|
| Interface has 1-3 methods | Interface has many methods |
| Need specific test behavior | Need call verification (times, order) |
| Simpler, more readable | More powerful, more boilerplate |

---

## Benchmarks

```go
func BenchmarkParseVersion(b *testing.B) {
    for b.Loop() {
        ParseVersion("v1.2.3")
    }
}

// With setup cost excluded
func BenchmarkProcess(b *testing.B) {
    data := loadTestData()
    b.ResetTimer()

    for b.Loop() {
        Process(data)
    }
}

// Sub-benchmarks
func BenchmarkSort(b *testing.B) {
    sizes := []int{10, 100, 1000, 10000}
    for _, n := range sizes {
        b.Run(fmt.Sprintf("n=%d", n), func(b *testing.B) {
            data := generateData(n)
            b.ResetTimer()
            for b.Loop() {
                sort.Ints(data)
            }
        })
    }
}
```

**Run:** `go test -bench=. -benchmem`

---

## Fuzz Testing

```go
func FuzzParseVersion(f *testing.F) {
    // Seed corpus
    f.Add("v1.2.3")
    f.Add("v0.0.0")
    f.Add("")

    f.Fuzz(func(t *testing.T, input string) {
        v, err := ParseVersion(input)
        if err != nil {
            return // Invalid input is fine
        }
        // Round-trip property: parse -> string -> parse should be stable
        reparsed, err := ParseVersion(v.String())
        if err != nil {
            t.Fatalf("round-trip failed: ParseVersion(%q) then String() = %q, re-parse error: %v",
                input, v.String(), err)
        }
        if reparsed != v {
            t.Errorf("round-trip: got %v, want %v", reparsed, v)
        }
    })
}
```

**Run:** `go test -fuzz=FuzzParseVersion -fuzztime=30s`

---

## Testdata Directory

Go convention for test fixtures:

```
package/
  handler.go
  handler_test.go
  testdata/
    golden_output.json
    input.txt
    fixtures.sql
```

```go
func TestRender(t *testing.T) {
    input, err := os.ReadFile("testdata/input.txt")
    if err != nil {
        t.Fatal(err)
    }

    got := Render(string(input))

    // Golden file comparison
    golden, err := os.ReadFile("testdata/golden_output.json")
    if err != nil {
        t.Fatal(err)
    }
    if got != string(golden) {
        t.Errorf("Render output differs from golden file")
    }
}
```

**Note:** `testdata/` is ignored by `go build` but included by `go test`.

---

## Bubbletea TUI Testing

For testing bubbletea-based TUI applications:

```go
import tea "github.com/charmbracelet/bubbletea"

func TestModel_KeyNavigation(t *testing.T) {
    m := NewModel()

    // Simulate window resize
    m, _ = m.Update(tea.WindowSizeMsg{Width: 80, Height: 24})

    // Simulate key press
    m, cmd := m.Update(tea.KeyMsg{Type: tea.KeyDown})

    // Assert state changed
    if m.cursor != 1 {
        t.Errorf("cursor = %d, want 1", m.cursor)
    }

    // Assert command output if needed
    if cmd != nil {
        msg := cmd()
        if _, ok := msg.(SelectionMsg); !ok {
            t.Errorf("cmd() = %T, want SelectionMsg", msg)
        }
    }
}
```

### Bubbletea Testing Patterns

| Pattern | Use When |
|---------|----------|
| `model.Update(msg)` + assert model state | Testing state transitions |
| `cmd()` + type assert result | Testing side effects (commands) |
| `ansi.Strip(model.View())` + string checks | Testing rendered output |
| `model.Init()` + process batch commands | Testing initialization |

### Bubbletea Anti-Patterns

| Anti-Pattern | Why Harmful | Fix |
|-------------|------------|-----|
| Asserting raw `View()` with ANSI codes | Brittle, hard to read | Use `ansi.Strip()` before asserting |
| Skipping `WindowSizeMsg` initialization | Model may have zero dimensions | Always send initial `WindowSizeMsg` |
| Not processing commands | Missing state transitions | Execute `cmd()` and feed result back |
| Testing view output character-by-character | Breaks on styling changes | Use `strings.Contains()` for content |

---

## Race Detection

Always run tests with the race detector in CI:

```bash
go test -race ./...
```

**Race detection in tests only catches races exercised during the test run.** Design tests that exercise concurrent paths.

---

## Test Build Tags

```go
//go:build integration

package store_test

func TestDatabaseIntegration(t *testing.T) {
    // Only runs with: go test -tags=integration
}
```

Use for separating slow integration tests from fast unit tests.

---

## Best Practices Summary

| Do | Don't |
|----|-------|
| Use table-driven tests | Write one test per case |
| Call `t.Helper()` in helpers | Let failures point to helpers |
| Use `t.Parallel()` when safe | Parallelize tests with shared state |
| Use `t.TempDir()` | Create temp dirs manually |
| Use `t.Setenv()` | Use `os.Setenv()` directly |
| Use `t.Cleanup()` | Rely only on `defer` |
| Use `httptest` for HTTP tests | Make real HTTP calls |
| Define small interfaces | Mock concrete types |
| Use `t.Run()` for subtests | Use flat test functions for variants |
| Test behavior not implementation | Assert internal state directly |
| Use `-race` flag in CI | Skip race detection |
| Use `testdata/` for fixtures | Embed large test data in code |
