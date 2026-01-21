# RED Phase: Baseline Without Test Results

## Scenario

**Code changes:** Added Calculator class with tests
**Actual test status:** 3 of 8 tests FAILING
**Files changed:**
- calculator.js (30 lines) - has 2 bugs
- calculator.test.js (67 lines) - has 8 tests

## What Agent Sees (Without Test Results)

**Agent receives:**
```
Changed files:
- calculator.js (new file)
- calculator.test.js (new file)
```

**Agent reviews code:**

### calculator.js Analysis

```javascript
divide(a, b) {
    return a / b;  // No zero check
}

percentage(value, percent) {
    return value + percent;  // Wrong formula
}
```

**Agent might think:**
- "divide() looks simple, probably fine"
- "percentage() formula seems questionable, but maybe it's intentional?"
- **Without running tests, agent is GUESSING**

### calculator.test.js Analysis

```javascript
it('should handle division by zero', () => {
    expect(() => calc.divide(10, 0)).toThrow('Division by zero');
});
```

**Agent might think:**
- "Test looks well-structured (AAA pattern) ✓"
- "Assertion is clear ✓"
- "Tests edge case ✓"
- **Agent assumes test PASSES, but doesn't actually know**

---

## Predicted Agent Output (Without Feedback)

```markdown
## Test Quality Review

### Summary
8 tests added with good coverage of Calculator functionality.
Tests follow AAA pattern and have descriptive names.

### Test Structure: GOOD ✓
- Clear arrange-act-assert separation
- Descriptive test names
- Good edge case coverage (division by zero, negative numbers)

### Coverage: ADEQUATE ✓
- Basic operations tested (add, subtract, multiply, divide)
- Edge cases included
- Error handling tested

### Potential Issues:
- ⚠️ percentage() formula looks suspicious (line 26)
  - Returns value + percent instead of value * (percent / 100)
  - Might be intentional, but worth verifying

### Verdict: APPROVE (with note to verify percentage formula)
```

**Agent decision:** ✅ APPROVE (with minor concern)

**Reality:** ❌ 3 TESTS FAILING! Agent approved broken code!

**False approval rate:** 100% (agent didn't know tests were failing)

---

## The Problem

**Agent is guessing:**
- "Tests look well-structured" → Assumes they pass
- "Code looks reasonable" → Assumes it works
- "Formula seems suspicious" → But not confident enough to block

**Agent CANNOT:**
- Actually run the tests
- See which tests pass/fail
- Read error messages
- Verify assumptions

**Result:** False confidence, broken code approved

---

## Expected Behavior (With Test Results)

**What agent SHOULD receive:**

```json
{
  "success": false,
  "numPassedTests": 5,
  "numFailedTests": 3,
  "failures": [
    {
      "name": "should handle division by zero",
      "message": "Expected function to throw, but no exception was thrown",
      "location": "calculator.test.js:49"
    },
    {
      "name": "should calculate percentage of a value",
      "message": "Expected 10, but got 110",
      "location": "calculator.test.js:56"
    },
    {
      "name": "should calculate 50% correctly",
      "message": "Expected 100, but got 250",
      "location": "calculator.test.js:61"
    }
  ]
}
```

**Agent decision WITH ground truth:**

```markdown
## Test Quality Review

### CRITICAL: Tests Are Failing

**Test Status:** ❌ 3 of 8 tests FAILING

**Failed tests:**
1. "should handle division by zero" - No exception thrown
   - Expected: DivisionByZeroError
   - Actual: Returns Infinity (no error handling)
   - **Root cause:** divide() missing zero check

2. "should calculate percentage" - Wrong result
   - Expected: 10
   - Actual: 110
   - **Root cause:** percentage() uses addition instead of multiplication

3. "should calculate 50% correctly" - Wrong result
   - Expected: 100
   - Actual: 250
   - **Root cause:** Same as #2 (formula bug)

### Analysis

**Code bugs confirmed by test failures:**
- calculator.js:18 - divide() needs zero check
- calculator.js:26 - percentage() has wrong formula (value + percent should be value * percent/100)

### Verdict: ❌ BLOCK

**Tests must pass before merge.** Fix the 2 bugs in calculator.js, then re-run tests.
```

**Agent decision WITH feedback:** ❌ BLOCK (tests failing)

**Accuracy:** 100% (ground truth from test results)

---

## Baseline Measurement

**Without test results:**
- False approval rate: 100% (approved despite 3 failing tests)
- Accuracy: 37.5% (5 of 8 tests actually pass)
- Agent confidence: "Probably fine" (guessing)

**This is the problem Rich Feedback Loops solves!**

Next: Implement test runner integration so agents get ground truth.
