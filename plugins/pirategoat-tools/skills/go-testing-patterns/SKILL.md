---
name: go-testing-patterns
description: Use when writing or reviewing Go tests - standard testing package, table-driven tests, subtests, test helpers, httptest, benchmarks, fuzz testing, and interface-based mocking.
---

# Go Testing Patterns

Standard Go `testing` package patterns and common libraries. For shared test quality principles (philosophy, smells, mocking decisions), see `testing-patterns`.

## Reference Routing

| Need | Reference File | Sections to Read |
|------|---------------|-----------------|
| Go testing patterns | `${CLAUDE_SKILL_DIR}/../testing-patterns/references/go-testing-patterns.md` | Full file (~420L, manageable) |
| Behavior vs implementation | `${CLAUDE_SKILL_DIR}/../testing-patterns/references/test-philosophy.md` | `## The Fundamental Shift` + `## Four Core Principles` |
| Flaky/brittle tests | `${CLAUDE_SKILL_DIR}/../testing-patterns/references/test-smells.md` | `## The Six Major Test Smells` (relevant subsection) |
| Mock usage decisions | `${CLAUDE_SKILL_DIR}/../testing-patterns/references/mocking-strategies.md` | `## The Mocking Decision Framework` |
| AAA pattern/naming | `${CLAUDE_SKILL_DIR}/../testing-patterns/references/test-structure.md` | `## The AAA Pattern` + `## Test Naming Conventions` |
| Test data strategy | `${CLAUDE_SKILL_DIR}/../testing-patterns/references/test-data.md` | `## Factories` + `## Builders` |

**How to read sections:** Grep for heading to find line number, Read with offset+limit.

## Go Assertion Quick Reference

| Use | Instead Of | Why |
|-----|-----------|-----|
| `t.Fatalf("got %v, want %v", got, want)` | `t.Errorf` for preconditions | Stops test when continuing is meaningless |
| `require.NoError(t, err)` | `if err != nil { t.Fatal(err) }` | Concise with testify (optional) |
| `t.Run(tt.name, func(t *testing.T) {...})` | Flat loop body | Isolates cases, enables `-run` filtering |
| `t.Helper()` | Nothing | Failures point to caller, not helper |
| `t.Setenv("KEY", "val")` | `os.Setenv` + defer restore | Auto-restored, goroutine-safe |
| `t.TempDir()` | `os.MkdirTemp` + defer remove | Auto-cleaned after test |
| `t.Cleanup(fn)` | `defer fn()` | Runs even on `t.FailNow`/`t.Fatal` |
| `httptest.NewServer(handler)` | Real HTTP calls | Fast, deterministic, no network |

## Go Red Flags

| Pattern | Why Harmful | Fix |
|---------|------------|-----|
| Missing `t.Helper()` in helpers | Wrong failure location | Add as first line in helper functions |
| `os.Setenv` without `t.Setenv` | Env leaks between tests | Use `t.Setenv()` (Go 1.17+) |
| Table tests without `t.Run` | Can't isolate or run individual cases | Wrap body in `t.Run(tt.name, ...)` |
| Anonymous test cases | Hard to identify which failed | Add `name` field to test table |
| `t.Parallel()` + shared mutation | Data race | Isolate state per subtest |
| `t.Parallel()` + `t.Setenv` | Runtime panic | Use config structs instead |
| `fmt.Println` in tests | Lost in parallel output | Use `t.Log`/`t.Logf` |
| Manual temp dirs | Leak on failure | Use `t.TempDir()` |

## Table-Driven Test Template

```go
func TestParse(t *testing.T) {
    tests := []struct {
        name    string
        input   string
        want    Result
        wantErr bool
    }{
        {name: "valid", input: "abc", want: Result{Value: "abc"}},
        {name: "empty", input: "", wantErr: true},
    }

    for _, tt := range tests {
        t.Run(tt.name, func(t *testing.T) {
            got, err := Parse(tt.input)
            if (err != nil) != tt.wantErr {
                t.Fatalf("Parse(%q) error = %v, wantErr %v", tt.input, err, tt.wantErr)
            }
            if !tt.wantErr && got != tt.want {
                t.Errorf("Parse(%q) = %v, want %v", tt.input, got, tt.want)
            }
        })
    }
}
```

## Interface-Based Mocking

Go prefers small interfaces + hand-written test doubles over mocking frameworks:

```go
type Store interface {
    Get(ctx context.Context, id string) (*Item, error)
}

type fakeStore struct {
    items map[string]*Item
}

func (f *fakeStore) Get(_ context.Context, id string) (*Item, error) {
    item, ok := f.items[id]
    if !ok {
        return nil, ErrNotFound
    }
    return item, nil
}
```

Use `gomock`/`mockall` only when the interface has many methods.
