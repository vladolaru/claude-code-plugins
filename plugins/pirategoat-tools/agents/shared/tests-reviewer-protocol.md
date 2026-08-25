# Shared Tests Reviewer Protocol

Read this AFTER `reviewer-protocol.md`.

## RULE 0 (MOST IMPORTANT): Tests Verify Behavior, Not Implementation

A test has value only if it fails when code is broken and passes when code is correct. Fewer meaningful tests beat many overprescriptive tests.

## Verification Protocol (Each Test)

<verification_questions>
1. What specific behavior does this test verify? [Not "what code it calls"]
2. What condition would make this test fail? [Must be a real bug]
3. Would this test pass after a behavior-preserving refactor?
4. What is the single assertion's purpose?
5. Is the test name accurate?
6. Could a non-buggy change (rename, reword, add field) break this test? [Yes → overprescriptive]
7. Can assertions use structural/behavioral checks instead of exact strings?
</verification_questions>

Ask these as open questions, not yes/no confirmations.

When a review conclusion depends on a material negative such as “no assertion exercises this branch,” “no consumer relies on this helper,” or “no race remains,” record the dependent-side verification as structured evidence with `builder.record_check(question=..., method=..., result=...)`. A prose assurance or an empty finding list does not preserve what was checked; the shared reviewer protocol's absence-claim rules still apply.

## Overprescriptive Test Diagnosis

**Refactoring Resilience Test:** Imagine renaming an internal variable, rewording a string, adding a new field. Would any break this test?
- Yes → overprescriptive. What is the test actually protecting?
- Prefer: error codes over messages, `toMatchObject` over `toEqual`, semantic selectors over CSS classes

## Test Quality Severity

**CRITICAL (False Confidence):** Tests without assertions (always pass), asserting on mock return values (tautology), disabled/commented assertions.

**HIGH (Reduced Confidence):** Flaky tests (time/random), order-dependent (shared state), implementation-detail verification, excessive mocking, overprescriptive (break on harmless changes).

**MEDIUM (Best Practice):** Poor AAA structure, vague names, missing edge cases, magic values.

## Red Flags

**Instant CRITICAL:**

| Pattern | Look For |
|---------|----------|
| No assertion | `$this->assertTrue(true)`, missing expect |
| Testing mocks | `expect(mock.method()).toBe(mockedValue)` |
| Commented assertions | `// $this->assert...` |

**HIGH — overprescriptive:**

| Pattern | Look For |
|---------|----------|
| Exact error messages | `assertSame('The email...', $error)` when error codes exist |
| Large snapshots | `toMatchSnapshot()` on full components/pages |
| Full object equality | `toEqual({...20 fields...})` when testing 2-3 properties |
| Call order assertions | `toHaveBeenCalledBefore()` |
| Exact markup | `assertStringContainsString('<div class="exact classes">')` |

## Review Checklist

**CRITICAL:** Meaningful assertions? Behavior-based? Independent? Deterministic?

**HIGH:** Survives refactoring? Structural assertions? No snapshot abuse? Specific properties? Copy-safe?

**HIGH:** Clear AAA? Descriptive names? Mocking at boundaries only?

**MEDIUM:** Happy path? Error cases? Edge cases?

## Expected Situations

| Situation | Action |
|-----------|--------|
| No test files in diff | `builder.mark_not_applicable("No test files in diff")`; save and exit |
| Unfamiliar framework | WebSearch for patterns before generic review |
| Config only (no test logic) | Apply config standards, not quality standards |
