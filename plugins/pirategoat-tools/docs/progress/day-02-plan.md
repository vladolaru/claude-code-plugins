# Day 2 Plan: Proposal #5 - Rich Feedback Loops

**Date:** Wednesday, January 22, 2026
**Goal:** Start Rich Feedback Loops implementation
**Target:** Complete Phase 1 (Test Runner Integration)
**Time Available:** 4-5 hours

---

## 🎯 Today's Objectives

### Primary Goal: Test Runner Integration (Phase 1)

**Deliverable:** Agents receive actual test results instead of guessing

**Success criteria:**
- ✅ Test runners produce JSON output (Jest, PHPUnit, Playwright)
- ✅ Test results parser extracts key info
- ✅ tests-reviewer agent uses test results in review
- ✅ Agent blocks PRs with failing tests (no more "looks good" approvals)

**Time estimate:** 4 hours

---

### Stretch Goal: Start Linter Integration (Phase 2)

**If time permits after Phase 1:**
- Implement ESLint JSON output
- Implement PHPCS JSON output
- Create linter result parser

**Time estimate:** +2 hours (total 6 hours)

---

## 📋 Detailed Task Breakdown

### Morning Block (9am-11am) - 2 hours

**Task 1: RED Phase - Baseline Testing (30 min)**

- [ ] Create test PR with intentionally failing tests
- [ ] Run current tests-reviewer (without test results)
- [ ] Document: Does agent approve despite test failures?
- [ ] Expected: Agent says "tests look well-structured" (guessing)
- [ ] Measure: False approval rate

**Task 2: GREEN Phase - Test Runner Scripts (1.5 hours)**

- [ ] Create `plugins/pirategoat-tools/scripts/run-tests-for-review.sh`
  ```bash
  #!/bin/bash
  # Run all test suites with JSON output

  OUTPUT_DIR=${1:-/tmp/test-results}
  mkdir -p "$OUTPUT_DIR"

  # Jest (JavaScript)
  if [ -f "package.json" ] && grep -q "jest" package.json; then
      npm test -- --json --outputFile="$OUTPUT_DIR/jest-results.json" 2>&1 || true
  fi

  # PHPUnit (PHP)
  if [ -f "phpunit.xml" ] || [ -f "phpunit.xml.dist" ]; then
      phpunit --log-json "$OUTPUT_DIR/phpunit-results.json" 2>&1 || true
  fi

  # Playwright (E2E)
  if [ -f "playwright.config.ts" ] || [ -f "playwright.config.js" ]; then
      npx playwright test --reporter=json --output "$OUTPUT_DIR/playwright-results.json" 2>&1 || true
  fi

  echo "✅ Test results written to $OUTPUT_DIR/"
  ```

- [ ] Make executable: `chmod +x plugins/pirategoat-tools/scripts/run-tests-for-review.sh`
- [ ] Test on our repository (run Jest tests)
- [ ] Verify JSON output is valid

---

### Afternoon Block (2pm-4pm) - 2 hours

**Task 3: GREEN Phase - Test Result Parser (1 hour)**

- [ ] Create `plugins/pirategoat-tools/scripts/parse-test-results.py`
  ```python
  #!/usr/bin/env python3
  """
  Parse test results from various frameworks into unified format.

  Supports: Jest, PHPUnit, Playwright
  Output: Standardized JSON with pass/fail counts and details
  """

  import json
  import sys

  def parse_jest_results(jest_json):
      # Extract: numPassedTests, numFailedTests, testResults
      pass

  def parse_phpunit_results(phpunit_json):
      # Extract: tests, failures, errors
      pass

  def parse_playwright_results(playwright_json):
      # Extract: suites, tests, failures
      pass

  def unify_results(results_dict):
      # Combine into standard format
      return {
          'summary': {
              'total': X,
              'passed': Y,
              'failed': Z
          },
          'failures': [...]
      }
  ```

- [ ] Implement parsers for all 3 frameworks
- [ ] Test with sample JSON files
- [ ] Verify output format

**Task 4: GREEN Phase - Agent Integration (1 hour)**

- [ ] Update tests-reviewer.md to use test results
  ```markdown
  ## Step 1: Load Test Results (if available)

  ```bash
  if [ -f "$TEST_RESULTS" ]; then
      cat "$TEST_RESULTS"
      # Use actual pass/fail data in review
  else
      # Reviewing without execution data (mention this limitation)
  fi
  ```

  ## Step 2: Review Based on Ground Truth

  If test results available:
  - Use actual pass/fail status (don't guess)
  - Reference specific test failures
  - Analyze error messages from results
  - Block if tests failing
  ```

- [ ] Test with sample test results JSON
- [ ] Verify agent uses data correctly

---

### Evening (Optional) - +1-2 hours

**Task 5: REFACTOR Phase - End-to-End Test**

- [ ] Create full test scenario:
  1. Make code change with failing test
  2. Run `run-tests-for-review.sh`
  3. Run tests-reviewer with results
  4. Verify: Agent blocks PR due to failing tests
  5. Fix test
  6. Re-run
  7. Verify: Agent now approves

- [ ] Document: Actual behavior vs expected
- [ ] Measure: Accuracy improvement

---

## 🎯 Success Criteria for Day 2

**Must achieve:**
- [ ] Test runners produce valid JSON output
- [ ] tests-reviewer loads and uses test results
- [ ] Agent correctly identifies failing tests
- [ ] Agent blocks PRs with test failures

**Nice to have:**
- [ ] Multiple framework support (Jest + PHPUnit + Playwright)
- [ ] Unified test result format
- [ ] Graceful handling of missing test results

**Stretch:**
- [ ] Linter integration started
- [ ] Coverage reports integration

---

## 📊 Progress Tracking

**After Day 2 (if Phase 1 complete):**

Tier 1 Completion: 3.2 of 5 (64%)
- ✅ #4 Parallel Spawning (complete)
- ✅ #2 Verbose Reasoning (complete)
- ✅ #1 Semantic Filtering MVP (complete)
- 🔄 #5 Rich Feedback Phase 1 (complete)
- ⏳ #5 Rich Feedback Phase 2-3 (remaining)
- ⏳ #3 Structured Output (remaining)

**Estimated completion:** Day 4-5 (vs original 15 days)

---

## 🚨 Risks for Day 2

**Risk 1: Test framework complexity**
- Jest, PHPUnit, Playwright have different JSON formats
- **Mitigation:** Start with Jest only, add others incrementally

**Risk 2: No test suite in repository**
- We might not have comprehensive tests to validate with
- **Mitigation:** Create minimal test suite for validation

**Risk 3: Scope creep**
- Rich Feedback is large (20-24 hours total)
- **Mitigation:** Focus ONLY on Phase 1 (test runners), defer linters/scanners

**Risk 4: Integration complexity**
- Agents need to parse and reason from JSON
- **Mitigation:** Keep JSON simple, add examples to agent prompts

---

## 📝 Pre-Day 2 Checklist

**Before starting tomorrow:**
- [ ] Review proposal-05-rich-feedback-loops.md
- [ ] Identify test suites in our repository
- [ ] Verify test runners are installed (npm test, phpunit)
- [ ] Clear 4-5 hours on calendar
- [ ] Mental preparation: Complex task, stay focused

**During Day 2:**
- [ ] Follow TDD discipline (RED-GREEN-REFACTOR)
- [ ] Create todos for each phase
- [ ] Checkpoint after Phase 1 (go/no-go for Phase 2)
- [ ] Document learnings

**End of Day 2:**
- [ ] Commit all changes
- [ ] Update progress document
- [ ] Decide: Continue to Phase 2-3 or move to Structured Output?

---

## 🚀 Ready for Day 2!

**Start time:** Wednesday, January 22, 9am
**First task:** RED Phase - Create test PR with failing tests
**Goal:** Test runner integration complete by 1pm
**Stretch:** Linter integration started by 5pm

**Let's finish Tier 1! 💪**
