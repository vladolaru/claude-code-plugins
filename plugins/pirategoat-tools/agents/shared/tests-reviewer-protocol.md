# Shared Tests Reviewer Protocol

Standard protocol for all test review agents. Read this AFTER `reviewer-protocol.md`.

## RULE 0 (MOST IMPORTANT): Tests Must Verify Behavior, Not Implementation

A test has value only if it would fail when the code is broken and pass when the code is correct.

**Corollary: Fewer meaningful tests beat many overprescriptive tests.** A test suite's value comes from testing meaningful business logic, not from test count. Overprescriptive tests create maintenance burden and erode trust.

## Verification Protocol (Apply to Each Test)

<verification_questions>
1. What specific behavior does this test verify? [Not "what code does it call"]
2. Under what condition would this test fail? [Must be a real code bug]
3. Would this test pass if the implementation was refactored but behavior unchanged?
4. What is the single assertion's purpose?
5. Is the test name accurate about what's actually tested?
6. Could a non-buggy change (copy edit, rename, refactor) cause this test to fail? [If yes -> overprescriptive]
7. Is there a structural or behavioral way to assert instead of matching exact strings?
</verification_questions>

**Critical:** Ask these as open questions, not yes/no confirmations.

## Overprescriptive Test Diagnosis

Apply the **Refactoring Resilience Test:**
1. Imagine three harmless changes: renaming internal variable, rewording string, adding new field
2. Would any break this test? If yes -> overprescriptive
3. What is the test ACTUALLY protecting?
4. Can the assertion be structural? Error codes > messages, `toMatchObject` > `toEqual`, semantic selectors > CSS classes

## Test Quality Categories

### CRITICAL (False Confidence)
- Tests without assertions (always pass)
- Tests that assert on mock return values (tautology)
- Tests with disabled/commented assertions

### HIGH (Reduced Confidence)
- Flaky tests (time/random dependencies without mocking)
- Order-dependent tests (shared mutable state)
- Tests verifying implementation details instead of behavior
- Excessive mocking (testing mock wiring, not real code)
- Overprescriptive tests (break on harmless refactoring, copy edits, new fields)

### MEDIUM (Best Practice Violations)
- Poor AAA structure
- Vague test names
- Missing edge cases
- Magic values without context

## Test Quality Red Flags

**Instant CRITICAL:**

| Pattern | Why Critical | Look For |
|---------|-------------|----------|
| No assertion | Test always passes | `$this->assertTrue(true)`, missing expect |
| Testing mocks | Tests nothing | `expect(mock.method()).toBe(mockedValue)` |
| Commented assertions | Disabled verification | `// $this->assert...` |

**HIGH—overprescriptive:**

| Pattern | Why Harmful | Look For |
|---------|------------|----------|
| Exact error message assertions | Breaks on copy changes | `assertSame('The email...', $error)` when error codes exist |
| Large snapshot tests | Never meaningfully reviewed | `toMatchSnapshot()` on full components/pages |
| Full object equality for partial checks | Breaks when fields added | `toEqual({...20 fields...})` when testing 2-3 properties |
| Call order assertions | Tests implementation | `toHaveBeenCalledBefore()` |
| Exact HTML/markup assertions | Breaks on CSS refactoring | `assertStringContainsString('<div class="exact classes">')` |

## Review Checklist

**Test Quality (CRITICAL)**
```
[] Tests have meaningful assertions?
[] Tests verify behavior, not implementation details?
[] Tests are independent (no shared mutable state)?
[] Tests are deterministic (no time/random without mocking)?
```

**Test Resilience (HIGH)**
```
[] Tests survive refactoring?
[] Assertions use structural checks over exact copy?
[] No snapshot abuse?
[] Assertions target specific properties, not entire data shapes?
[] Copy changes won't break tests?
```

**Test Structure (HIGH)**
```
[] Clear AAA structure?
[] Descriptive test names?
[] Mocking at system boundaries only?
```

**Coverage (MEDIUM)**
```
[] Happy path covered?
[] Error cases tested?
[] Edge cases covered?
```

## Expected Situations

| Situation | Action |
|-----------|--------|
| No test files in diff | Report "No test files to review"; APPROVE |
| Unfamiliar framework | WebSearch for patterns before generic review |
| Config only (no test logic) | Apply config standards, not quality standards |
