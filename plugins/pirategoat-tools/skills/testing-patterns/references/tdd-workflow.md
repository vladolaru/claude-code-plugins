# TDD Workflow: Red-Green-Refactor

**Source:** Synthesized from "Writing Tests Before the Implementation" and "Test-Driven Development" (jhumelsine.github.io)

## Overview

Test-Driven Development (TDD) inverts the traditional code-then-test approach. You write tests FIRST, then write minimal code to make them pass, then refactor.

**Why it feels backward:** Because it IS backward from how most of us learned. But backward isn't wrong—it's often better.

## The Three Laws of TDD (Bob Martin)

1. **You may not write production code until you have written a failing unit test**
2. **You may not write more of a unit test than is sufficient to fail** (and not compiling is failing)
3. **You may not write more production code than is sufficient to pass the current failing test**

**Time per cycle:** 30-120 seconds

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
2. **Prevent false positives** (test must fail before it can legitimately pass)
3. **Define the interface** you wish you had

### How to Write the Failing Test

**Start with the degenerate case:**
```php
// Start simple: empty input
public function test_reverses_empty_string() {
    $this->assertSame( '', reverse_string( '' ) );
}
```

**Run it - watch it fail:**
```
Fatal error: Call to undefined function reverse_string()
```

**Why watch it fail?**
- Confirms test would catch a bug
- Prevents false positives (test that always passes)
- You know the test is actually testing something

### RED Phase Checklist

- [ ] Test describes ONE specific behavior
- [ ] Test name clearly states scenario and expectation
- [ ] Test is as simple as possible
- [ ] Test actually fails (run it!)
- [ ] Failure message is clear

### Common RED Phase Mistakes

**❌ Writing multiple tests before implementing:**
```php
// WRONG: Writing full test suite upfront
public function test_reverses_empty_string() { }
public function test_reverses_single_char() { }
public function test_reverses_multiple_chars() { }
public function test_reverses_palindrome() { }
// ... then implementing everything
```

**✅ One test at a time:**
```php
// CORRECT: One test, make it pass, then next test
public function test_reverses_empty_string() {
    $this->assertSame( '', reverse_string( '' ) );
}

// Run test → implement → refactor → THEN write next test
```

**❌ Writing complex test first:**
```php
// WRONG: Starting with complex case
public function test_reverses_sentence_with_punctuation() {
    $this->assertSame( '!dlrow ,olleH', reverse_string( 'Hello, world!' ) );
}
```

**✅ Start with simplest case:**
```php
// CORRECT: Start with degenerate/edge cases
public function test_reverses_empty_string() {
    $this->assertSame( '', reverse_string( '' ) );
}

// Then gradually add complexity
```

## Phase 2: GREEN (Make It Pass)

### Purpose
1. **Make the test pass** with minimal code
2. **Fake it until you make it** (Kent Beck)
3. **As tests get more specific, code gets more generic** (Bob Martin)

### Minimal Implementation Strategy

**Iteration 1: Empty string**
```php
// Test
public function test_reverses_empty_string() {
    $this->assertSame( '', reverse_string( '' ) );
}

// Minimal implementation (hardcode!)
function reverse_string( $str ) {
    return '';  // Simplest thing that makes test pass
}
```

**Iteration 2: Single character**
```php
// New test
public function test_reverses_single_character() {
    $this->assertSame( 'a', reverse_string( 'a' ) );
}

// Minimal implementation (still hardcoding!)
function reverse_string( $str ) {
    if ( $str === '' ) return '';
    return 'a';  // Makes both tests pass
}
```

**Iteration 3: Force generic solution**
```php
// New test forces generalization
public function test_reverses_two_characters() {
    $this->assertSame( 'ba', reverse_string( 'ab' ) );
}

// Now must implement real algorithm
function reverse_string( $str ) {
    if ( $str === '' ) return '';
    return strrev( $str );  // Generic solution emerges
}
```

### Why "Fake It Until You Make It"?

**It feels absurd to hardcode return values, but:**
1. Forces you to add more tests (hardcoded values fail eventually)
2. Prevents over-engineering
3. Lets the design emerge from tests
4. Keeps you in tight red-green-refactor cycles

**The progression:**
```
Specific hardcoded values
    ↓
Multiple hardcoded values (if/else)
    ↓
Pattern recognition
    ↓
Generic algorithm
```

### GREEN Phase Checklist

- [ ] Test passes
- [ ] All previous tests still pass
- [ ] Implementation is minimal (no extra features)
- [ ] Don't refactor yet (that's next phase)

### Common GREEN Phase Mistakes

**❌ Implementing full feature immediately:**
```php
// WRONG: Implementing everything at once
function reverse_string( $str ) {
    // Over-engineered for first test!
    if ( ! is_string( $str ) ) {
        throw new InvalidArgumentException( 'Must be string' );
    }

    if ( empty( $str ) ) {
        return '';
    }

    // Handles unicode, RTL languages, etc.
    return mb_strrev( $str, 'UTF-8' );
}
```

**✅ Minimal code for current test:**
```php
// CORRECT: Just enough
function reverse_string( $str ) {
    return ''; // Passes current test (empty string)
}

// Add complexity only when tests force you to
```

**❌ Refactoring during GREEN:**
```php
// WRONG: Refactoring while making test pass
function reverse_string( $str ) {
    return strrev( $str );  // Made test pass

    // Don't refactor here! Separate phase
}

// Immediately extracting methods, renaming, etc.
```

**✅ Make it pass, THEN refactor:**
```php
// CORRECT: Keep phases separate
function reverse_string( $str ) {
    return strrev( $str );  // Test passes - stop here
}

// Now move to REFACTOR phase
```

## Phase 3: REFACTOR (Clean It Up)

### Purpose
1. **Eliminate duplication**
2. **Improve structure** without changing behavior
3. **Make code clean** while tests provide safety net

### What Refactoring Means

**Refactoring = Changing structure WITHOUT changing behavior**

**Safe refactorings:**
- Extract method
- Rename variable/method/class
- Move method to better class
- Remove duplication
- Simplify conditionals
- Improve naming

**NOT refactoring:**
- Adding new behavior
- Changing behavior
- Adding features

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
    $discount = $this->calculate_vip_discount( $customer, $order );
    $discount += $this->calculate_bulk_discount( $order );
    return $discount;
}

private function calculate_vip_discount( $customer, $order ) {
    return $customer->is_vip ? $order->total * 0.1 : 0;
}

private function calculate_bulk_discount( $order ) {
    return $order->total > 100 ? 5 : 0;
}

// Run tests - still green? Refactoring succeeded!
```

### The Safety Net

**After each refactoring step:**
1. Run all tests
2. ✅ All pass? Continue refactoring
3. ❌ Something fails? Undo and try different approach

**This is why TDD enables confident refactoring:**
- Tests define behavior
- Refactoring preserves behavior
- Tests confirm behavior unchanged

### REFACTOR Phase Checklist

- [ ] All tests still pass
- [ ] Code is cleaner than before
- [ ] No duplication
- [ ] Clear naming
- [ ] No new features added

### When to Refactor

**Refactor when you see:**
- Duplication
- Unclear naming
- Long methods (> 10-15 lines)
- Complex conditionals
- Magic numbers

**Don't refactor prematurely:**
- Wait until you have 2-3 tests
- Wait until pattern emerges
- Don't refactor speculation ("we might need...")

## Complete TDD Example: Roman Numerals

**Requirement:** Convert integers to Roman numerals (1-10 initially)

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

### Iteration 4-10: Continue pattern

Add tests for 3, 4, 6, 7, 8, 9, 10. Algorithm handles them by expanding mappings:

```php
$mappings = [
    [ 10, 'X' ],
    [ 9, 'IX' ],
    [ 5, 'V' ],
    [ 4, 'IV' ],
    [ 1, 'I' ],
];
```

## TDD Anti-Patterns

### Anti-Pattern 1: Writing Implementation First

**❌ WRONG: Implementation-first**
```php
// Write full implementation
function complex_algorithm( $data ) {
    // 50 lines of code
}

// Then try to test it
public function test_complex_algorithm() {
    // How do I even test this?
    // What scenarios matter?
}
```

**✅ CORRECT: Test-first**
```php
// Specify behavior
public function test_returns_empty_for_empty_data() {
    $this->assertSame( [], complex_algorithm( [] ) );
}

// Minimal implementation emerges through tests
```

### Anti-Pattern 2: Big Steps

**❌ WRONG: Jumping to complex cases**
```php
// First test is complex
public function test_parses_nested_json_with_arrays() {
    $json = '{"users":[{"id":1,"name":"John"},{"id":2,"name":"Jane"}]}';
    $result = parse_json( $json );
    $this->assertSame( 2, count( $result['users'] ) );
}
```

**✅ CORRECT: Baby steps**
```php
// Start simple
public function test_parses_empty_object() {
    $this->assertSame( [], parse_json( '{}' ) );
}

public function test_parses_single_property() {
    $this->assertSame( [ 'name' => 'John' ], parse_json( '{"name":"John"}' ) );
}

// Build up complexity gradually
```

### Anti-Pattern 3: Testing After Implementation

**❌ WRONG: Never sees test fail**
```php
// Implementation exists
function add( $a, $b ) {
    return $a + $b;
}

// Test written after
public function test_adds_numbers() {
    $this->assertSame( 4, add( 2, 2 ) );
    // Always passes - is it a real test or false positive?
}
```

**✅ CORRECT: Watch it fail first**
```php
// Test first (fails - function doesn't exist)
public function test_adds_numbers() {
    $this->assertSame( 4, add( 2, 2 ) );
}

// Implement (test now passes)
function add( $a, $b ) {
    return $a + $b;
}
```

## TDD Benefits

1. **Better design:** Tests as first client shape better APIs
2. **Living documentation:** Tests show how to use code
3. **Confident refactoring:** Safety net catches regression
4. **Less debugging:** Tight cycles catch bugs immediately
5. **Complete coverage:** Code only exists if test requires it

## Test && Commit || Revert (TCR)

**Extreme TDD discipline:**
- ✅ Tests pass → Auto-commit
- ❌ Tests fail → Auto-revert all changes

**Benefits:**
- Eliminates sunk cost fallacy
- Forces tiny steps
- Code always in green state
- Discourages long sessions without testing

**Modified version (Jeff Grigg):**
- Red: Write failing test → Manual commit
- Green: Make test pass → Auto-commit if passes
- Refactor: Clean up → Auto-commit if passes, revert if fails

## Coding Katas for Practice

**Don't practice TDD on production code first!** Use katas:

- **Bowling Score:** Complex scoring rules
- **Roman Numerals:** Integer to Roman conversion
- **String Calculator:** Parse and calculate
- **FizzBuzz:** Classic interview problem
- **Prime Factors:** Decompose numbers

**Practice schedule:**
- 30 minutes per day
- Same kata repeatedly
- Try different approaches
- Focus on rhythm, not solution

## Quotes

> _As the tests get more specific, the code gets more generic._ — Bob Martin

> _Fake it until you make it._ — Kent Beck

> _If you think TDD is about testing, you haven't done enough TDD._ — Kent Beck

> _The word "refactoring" should never appear in a schedule. Refactoring is not a story or a backlog item. Refactoring is continuous._ — Bob Martin

## Further Reading

- "Test-Driven Development: By Example" - Kent Beck
- "The Three Laws of TDD" - Bob Martin blog
- "Test && Commit || Revert" - Kent Beck
- Clean Coders TDD videos - Bob Martin (behind paywall)
