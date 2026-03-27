# TDD Workflow: Red-Green-Refactor

**Source:** Synthesized from "Writing Tests Before the Implementation" and "Test-Driven Development" (jhumelsine.github.io)

## Quick Reference

| Phase | Purpose | Rule |
|---|---|---|
| RED | Write ONE failing test specifying ONE behavior | Test must actually fail before proceeding |
| GREEN | Write minimal code to make it pass | No extra features, no refactoring yet |
| REFACTOR | Clean up while tests stay green | Change structure only, not behavior |

**Cycle time:** 30-120 seconds per red-green-refactor iteration.

**The Three Laws (Bob Martin):**
1. No production code until you have a failing test
2. No more test than sufficient to fail (not compiling = failing)
3. No more production code than sufficient to pass the current test

## The Red-Green-Refactor Cycle

```
┌─────────────────────────────────────────┐
│ RED: Write failing test                  │
│ (Specify one behavior)                   │
└────────────┬────────────────────────────┘
             │
             ▼
┌─────────────────────────────────────────┐
│ GREEN: Make it pass                      │
│ (Minimal implementation)                 │
└────────────┬────────────────────────────┘
             │
             ▼
┌─────────────────────────────────────────┐
│ REFACTOR: Clean up                       │
│ (Improve structure, keep green)          │
└────────────┬────────────────────────────┘
             │
             ▼
         All tests pass?
             │
        ┌────┴────┐
        │ Yes     │ No → Debug, undo, retry
        ▼         │
    Can add       │
    failing test? │
        │         │
    ┌───┴───┐     │
    │ Yes   │ No → Done
    └───┬───┘     (Complete)
        │
        └──> Back to RED
```

## Phase 1: RED (Write Failing Test)

### Purpose
1. **Specify behavior** before implementing
2. **Prevent false positives** — test must fail before it can legitimately pass
3. **Define the interface** you wish you had

### Checklist

- [ ] Test describes ONE specific behavior
- [ ] Test name clearly states scenario and expectation
- [ ] Test is as simple as possible
- [ ] Test actually fails (run it!)
- [ ] Failure message is clear

### Common Mistake

```php
// WRONG: Writing full test suite upfront
public function test_reverses_empty_string() { }
public function test_reverses_single_char() { }
public function test_reverses_multiple_chars() { }
public function test_reverses_palindrome() { }
// ... then implementing everything
```

```php
// CORRECT: One test, make it pass, then next test
public function test_reverses_empty_string() {
    $this->assertSame( '', reverse_string( '' ) );
}
// Run test → implement → refactor → THEN write next test
```

## Phase 2: GREEN (Make It Pass)

### Purpose
1. **Make the test pass** with minimal code
2. **Fake it until you make it** (Kent Beck)
3. **As tests get more specific, code gets more generic** (Bob Martin)

### Fake It Until You Make It

Hardcode return values initially. It feels absurd, but it forces you to add more tests (hardcoded values fail eventually), prevents over-engineering, and lets the design emerge. The progression: specific hardcoded values → multiple if/else → pattern recognition → generic algorithm.

### Checklist

- [ ] Test passes
- [ ] All previous tests still pass
- [ ] Implementation is minimal (no extra features)
- [ ] Don't refactor yet (that's next phase)

### Common Mistake

```php
// WRONG: Implementing full feature for first test
function reverse_string( $str ) {
    if ( ! is_string( $str ) ) {
        throw new InvalidArgumentException( 'Must be string' );
    }
    if ( empty( $str ) ) {
        return '';
    }
    return mb_strrev( $str, 'UTF-8' ); // Handles unicode, RTL, etc.
}
```

```php
// CORRECT: Just enough for the current test
function reverse_string( $str ) {
    return ''; // Passes current test (empty string)
}
// Add complexity only when tests force you to
```

## Phase 3: REFACTOR (Clean It Up)

### Purpose
1. **Eliminate duplication**
2. **Improve structure** without changing behavior
3. **Make code clean** while tests provide safety net

### Safe Refactorings
- Extract method
- Rename variable/method/class
- Move method to better class
- Remove duplication
- Simplify conditionals
- Improve naming

**NOT refactoring:** Adding new behavior, changing behavior, adding features.

### Refactoring with Safety Net

```php
// GREEN: Tests pass with this implementation
function calculate_discount( $customer, $order ) {
    $discount = 0;
    if ( $customer->is_vip ) {
        $discount = $order->total * 0.1;
    }
    if ( $order->total > 100 ) {
        $discount += 5;
    }
    return $discount;
}

// REFACTOR: Extract methods for clarity
function calculate_discount( $customer, $order ) {
    return $this->calculate_vip_discount( $customer, $order )
         + $this->calculate_bulk_discount( $order );
}

private function calculate_vip_discount( $customer, $order ) {
    return $customer->is_vip ? $order->total * 0.1 : 0;
}

private function calculate_bulk_discount( $order ) {
    return $order->total > 100 ? 5 : 0;
}
// Run tests — still green? Refactoring succeeded!
// Something fails? Undo and try different approach.
```

### Checklist

- [ ] All tests still pass
- [ ] Code is cleaner than before
- [ ] No duplication
- [ ] Clear naming
- [ ] No new features added

### When to Refactor

**Refactor when you see:** duplication, unclear naming, long methods (> 10-15 lines), complex conditionals, magic numbers.

**Don't refactor prematurely:** Wait until you have 2-3 tests, wait until a pattern emerges, don't refactor on speculation.

## Complete TDD Example: Roman Numerals

**Requirement:** Convert integers to Roman numerals.

### Iteration 1: Simplest case

```php
// RED: Test 1
public function test_converts_1_to_I() {
    $this->assertSame( 'I', to_roman( 1 ) );
}

// GREEN: Hardcode
function to_roman( $num ) {
    return 'I';
}
```

### Iteration 2: Force variation

```php
// RED: Test 2
public function test_converts_5_to_V() {
    $this->assertSame( 'V', to_roman( 5 ) );
}

// GREEN: If/else
function to_roman( $num ) {
    if ( $num === 1 ) return 'I';
    if ( $num === 5 ) return 'V';
}
```

### Iteration 3: Force pattern

```php
// RED: Test 3
public function test_converts_2_to_II() {
    $this->assertSame( 'II', to_roman( 2 ) );
}

// GREEN: Pattern emerges
function to_roman( $num ) {
    $result = '';
    while ( $num >= 5 ) {
        $result .= 'V';
        $num -= 5;
    }
    while ( $num >= 1 ) {
        $result .= 'I';
        $num -= 1;
    }
    return $result;
}

// REFACTOR: Extract mapping
function to_roman( $num ) {
    $mappings = [
        [ 5, 'V' ],
        [ 1, 'I' ],
    ];
    $result = '';
    foreach ( $mappings as list( $value, $numeral ) ) {
        while ( $num >= $value ) {
            $result .= $numeral;
            $num -= $value;
        }
    }
    return $result;
}
```

Further iterations (4, 9, 10, etc.) expand `$mappings` with `[10, 'X'], [9, 'IX'], [4, 'IV']` — the algorithm handles them without structural change.

## TDD Anti-Patterns

| Anti-Pattern | Why Wrong | Correct Approach |
|---|---|---|
| Implementation first | Can't verify tests catch bugs; design not shaped by usage | Write test first, watch it fail, then implement minimally |
| Big steps | Complex first test = complex first implementation; hard to debug failures | Start with degenerate case, build complexity gradually |
| Never seeing red | Tests written after code always pass — could be false positives | Always watch the test fail before making it pass |

### Implementation First

```php
// WRONG: 50 lines of implementation, then "how do I test this?"
function complex_algorithm( $data ) { /* full implementation */ }

// CORRECT: Specify behavior first
public function test_returns_empty_for_empty_data() {
    $this->assertSame( [], complex_algorithm( [] ) );
}
// Minimal implementation emerges through tests
```

### Big Steps

```php
// WRONG: First test is complex
public function test_parses_nested_json_with_arrays() {
    $json = '{"users":[{"id":1,"name":"John"},{"id":2,"name":"Jane"}]}';
    $result = parse_json( $json );
    $this->assertSame( 2, count( $result['users'] ) );
}

// CORRECT: Baby steps
public function test_parses_empty_object() {
    $this->assertSame( [], parse_json( '{}' ) );
}
// Build up complexity gradually
```

### Never Seeing Red

```php
// WRONG: Implementation exists, test written after — always passes
function add( $a, $b ) { return $a + $b; }
public function test_adds_numbers() {
    $this->assertSame( 4, add( 2, 2 ) ); // Is this real or false positive?
}

// CORRECT: Test first (fails — function doesn't exist), then implement
public function test_adds_numbers() {
    $this->assertSame( 4, add( 2, 2 ) );
}
function add( $a, $b ) { return $a + $b; } // Test now passes
```

## Test && Commit || Revert (TCR)

Extreme TDD discipline: tests pass = auto-commit; tests fail = auto-revert all changes. Forces tiny steps, eliminates sunk cost fallacy, keeps code always green. Modified version (Jeff Grigg): manual commit on red, auto-commit on green/refactor, auto-revert on failure.

## Coding Katas for Practice

- **Bowling Score:** Complex scoring rules
- **Roman Numerals:** Integer to Roman conversion
- **String Calculator:** Parse and calculate
- **FizzBuzz:** Classic interview problem
- **Prime Factors:** Decompose numbers
