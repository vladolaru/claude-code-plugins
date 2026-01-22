# Proposal #2: Verbose Reasoning Mode for Review Agents

**Pattern:** Verbose Reasoning Transparency
**Priority:** Tier 1 - Implement Immediately
**Effort:** Low (1-2 hours per agent, 8-10 hours total)
**Impact:** High (improves trust, debugging, learning, verification)
**Source:** awesome-agentic-patterns

---

## The Problem (Why This Matters)

### Current State Analysis

**What developers experience today with agent reviews:**

```markdown
## Security Review

### CRITICAL Issues (1)

**Issue #1: SQL Injection Vulnerability**
- **Location:** `src/OrderProcessor.php:67`
- **Severity:** CRITICAL
- **Description:** Direct variable interpolation in SQL query without prepared statement
- **Code:**
  ```php
  $wpdb->query( "DELETE FROM {$wpdb->orders} WHERE id = {$order_id}" );
  ```
- **Fix:** Use `$wpdb->prepare()` with placeholders
```

**What's missing:**
- Why did the agent flag this?
- How confident is the agent?
- What checks did it perform?
- Did it consider any mitigating factors?
- Why is this CRITICAL vs HIGH?
- What makes this exploitable?

### The Core Problem: Black-Box Decision Making

**Developers ask:**
1. "Why is this flagged as CRITICAL?"
2. "Did the agent check for sanitization earlier in the flow?"
3. "How confident is this finding?"
4. "Is this a false positive?"
5. "What specific pattern triggered this?"

**Without reasoning transparency:**
- ❌ Developers must trust blindly or verify manually
- ❌ False positives waste time (no way to debug agent logic)
- ❌ True positives lack persuasive evidence (developers dismiss)
- ❌ No learning opportunity (why is this wrong?)
- ❌ Can't improve agent (no visibility into decision process)

### Real-World Impact

**Scenario 1: False Positive Confusion**

```markdown
Agent: "SQL Injection on line 42"

Developer: *checks line 42*
  $wpdb->prepare( "SELECT * FROM {$wpdb->posts} WHERE ID = %d", $id );

Developer: "This IS using prepare(). False positive?"
```

**Without reasoning:**
- Developer wastes 10 minutes investigating
- Developer loses trust in agent
- Developer dismisses finding without understanding
- Agent might have valid concern (e.g., dynamic table name)

**With reasoning:**
```markdown
Agent: "SQL Injection on line 42"

<reasoning>
1. Detected prepared statement: FOUND ✓
2. Checked placeholders for values: CORRECT ✓
3. Analyzed table name: DYNAMIC (from user input) ✗
4. Table name not validated against whitelist
5. Pattern: Dynamic table names bypass prepare() protection
6. Reference: WordPress VIP Code Review §3.4.2
7. Confidence: 85% (prepared values secure, table name vulnerable)
</reasoning>
```

**Result:** Developer immediately understands the nuanced issue.

---

**Scenario 2: Missed Context**

```markdown
Agent: "Missing nonce verification on line 145"

Developer: *checks line 145*
  // Line 145 is inside admin_init hook, nonce checked on line 138
  if ( ! wp_verify_nonce( $_POST['nonce'], 'my_action' ) ) return;

Developer: "False positive. Agent didn't see context."
```

**Without reasoning:** Developer dismisses, reports false positive, loses trust.

**With reasoning:**
```markdown
Agent: "Missing nonce verification on line 145"

<reasoning>
1. Detected $_POST usage on line 145: FOUND
2. Searched for wp_verify_nonce() in function scope: FOUND (line 138) ✓
3. Verified nonce check gates this code: YES ✓
4. Wait—nonce check uses early return pattern
5. Verified $_POST usage is protected: CONFIRMED ✓
6. False alarm—marking as resolved
7. Confidence: 99% (protected by gating check)
</reasoning>

Status: NO ISSUE (nonce properly verified on line 138)
```

**Result:** Transparent false negative avoidance, developer trusts thoroughness.

---

**Scenario 3: Confidence Calibration**

```php
// Code under review
function process_payment( $amount ) {
    $fee = $amount * 0.029; // Processing fee
    return $amount + $fee;
}
```

**Agent without reasoning:**
```markdown
Issue: Floating point precision error in payment calculation
Severity: HIGH
```

**Developer reaction:** "Really? HIGH severity for a few cents?"

**Agent with reasoning:**
```markdown
Issue: Floating point precision error in payment calculation
Severity: HIGH

<reasoning>
1. Detected floating point arithmetic: FOUND
2. Context: Financial calculation (payment processing)
3. Checked for decimal-safe operations: NOT FOUND
4. Potential error: 0.000001 per transaction
5. Scale impact: If 1M transactions → $1,000 cumulative error
6. Compliance: PCI DSS requires exact decimal precision
7. Reference: Payment Gateway Best Practices (2025)
8. Confidence: 75% (depends on transaction volume)
9. Severity rationale: Financial accuracy + compliance requirements
</reasoning>

Recommendation: Use bcmath or decimal library for financial calculations
```

**Result:** Developer understands business context, not just technical issue.

---

## The Solution (How It Works)

### Concept: Expose Internal Reasoning Process

Add optional `--verbose` mode (or reasoning blocks) that show agent's step-by-step analysis for each finding.

#### Output Modes

**Mode 1: Standard (default)**
- Concise findings
- No reasoning exposed
- Fast to read
- For trusted, mature agents

**Mode 2: Verbose (opt-in)**
- Full reasoning chain
- Confidence scores
- Decision factors
- For debugging, learning, verification

**Mode 3: Hybrid (recommended)**
- Standard findings by default
- Reasoning available on demand
- Expandable sections in markdown
- Best of both worlds

### What Gets Exposed

For each finding, show:

1. **Detection Process**
   - What pattern/rule triggered
   - What checks were performed
   - What context was considered

2. **Confidence Calibration**
   - Numerical confidence (0-100%)
   - Factors that increase confidence
   - Factors that decrease confidence
   - Uncertainty acknowledgment

3. **Severity Rationale**
   - Why this severity level?
   - What makes it CRITICAL vs HIGH?
   - Business impact considered
   - Security/performance/maintainability weight

4. **Cross-References**
   - Skills/patterns consulted
   - Documentation referenced
   - Best practices cited
   - Similar past issues

5. **Alternative Interpretations**
   - Were other explanations considered?
   - Why were they rejected?
   - Edge cases acknowledged

### Implementation Approaches

#### Approach A: Explicit Reasoning Blocks (Recommended)

**Prompt pattern:**
```markdown
For each issue you identify, provide reasoning using this format:

<reasoning>
1. Detection: [What triggered this finding?]
2. Context Analysis: [What surrounding context was examined?]
3. Checks Performed: [What validation steps occurred?]
4. Confidence: [0-100% with factors]
5. Severity Rationale: [Why this severity level?]
6. References: [Skills, docs, patterns consulted]
</reasoning>

In verbose mode, include reasoning blocks.
In standard mode, omit reasoning blocks.
```

**Example output:**
```markdown
### Issue #1: SQL Injection Vulnerability

**Location:** `src/OrderProcessor.php:67`
**Severity:** CRITICAL
**Confidence:** 95%

**Description:** Direct variable interpolation in SQL query without prepared statement

**Code:**
```php
$result = $wpdb->query( "DELETE FROM {$wpdb->orders} WHERE id = {$order_id}" );
```

<details>
<summary>🔍 Show reasoning process</summary>

**Detection:**
- Pattern match: Direct variable in SQL string (regex: `\$wpdb->query.*\{.*\}`)
- Location: Line 67 in processOrder() method
- Variable: `$order_id` (unparameterized)

**Context Analysis:**
1. Traced `$order_id` origin: Function parameter from REST API endpoint
2. Checked for sanitization at entry: NOT FOUND
3. Examined caller code: `$order_id` from `$_POST['order_id']` (user input)
4. Verified input validation: Only `!empty()` check (insufficient)

**Checks Performed:**
- ✗ Prepared statement usage: NOT FOUND
- ✗ Input sanitization: MISSING (no absint, intval, or validation)
- ✗ Capability check: MISSING (any user can trigger)
- ✗ Nonce verification: MISSING

**Confidence: 95%**

*Factors increasing confidence:*
- Direct user input path confirmed (no sanitization layer)
- DELETE operation (destructive, high impact)
- No prepared statement protection
- Pattern matches known SQL injection CVEs

*Factors decreasing confidence:*
- 5% possibility of sanitization in undiscovered caller
- Could be internal-only function (not exposed to users)

**Severity Rationale: CRITICAL**

*Why CRITICAL (not HIGH):*
1. **Exploitability:** Direct SQL injection, no barriers
2. **Impact:** DELETE operation → data loss
3. **Attack Surface:** REST API exposed to unauthenticated users
4. **WordPress Context:** Core security violation (WPCS critical)
5. **Compliance:** Fails OWASP A03:2021 (Injection)

*Business Impact:*
- Data loss: Customer orders deleted
- Availability: Database corruption possible
- Reputation: Security breach disclosure
- Compliance: PCI DSS violation if payment data exposed

**References:**
- WordPress PHPCS: `WordPress.DB.PreparedSQL.InterpolatedNotPrepared`
- OWASP Top 10 2021: A03:2021 – Injection
- WP Core Security Handbook: SQL Injection Prevention
- Skill consulted: `wordpress-backend-dev` (SQL security patterns)

**Cross-Check Results:**
- Checked project CLAUDE.md: No exception for this pattern
- Searched for similar issues in codebase: Found 3 similar patterns (flagged separately)
- Verified against test suite: No test coverage for this endpoint

**Alternative Interpretations Considered:**

*Could this be a false positive?*
1. ❌ "Maybe $order_id is sanitized elsewhere?"
   - Checked: No sanitization found in trace
2. ❌ "Maybe this is internal-only code?"
   - Checked: REST API route publicly registered
3. ❌ "Maybe prepare() is aliased?"
   - Checked: No custom wpdb wrappers in project

**Recommendation Confidence: 95%**

This is a true positive requiring immediate fix.

</details>

**Recommended Fix:**
```php
$result = $wpdb->query( $wpdb->prepare(
    "DELETE FROM {$wpdb->orders} WHERE id = %d",
    absint( $order_id )
) );
```
```

---

#### Approach B: Confidence Metadata (Lightweight)

For agents where full reasoning is too verbose, add confidence scores:

```markdown
### Issue #1: SQL Injection Vulnerability
**Severity:** CRITICAL
**Confidence:** 95% (High)
**Detection Method:** Pattern match + taint analysis
**Cross-Checked:** WordPress PHPCS, OWASP guidelines
```

---

#### Approach C: Reasoning Summary (Hybrid)

Provide brief summary, full reasoning available via agent prompt:

```markdown
### Issue #1: SQL Injection Vulnerability
**Severity:** CRITICAL
**Reasoning:** Direct user input in SQL query without prepare(), destructive DELETE operation, REST API exposure, no sanitization or capability checks found in trace.
**Confidence:** 95%
```

---

## Detailed Reasoning: Why Each Component Matters

### Reason 1: Trust Through Transparency

**Psychology:** Humans trust systems they understand.

**Black-box agents:**
- "Agent says this is critical. But why?"
- "Should I trust this or verify myself?"
- **Result:** Developer manually verifies every finding (agent provides no value)

**Transparent agents:**
- "Agent checked A, B, C and found X. Makes sense."
- "Agent considered Y but ruled it out. Thorough."
- **Result:** Developer trusts findings, acts quickly, learns from reasoning

**Trust metric:**
```
Trust = (Correct Findings + Reasoning Quality) / Total Findings

Without reasoning: Trust = Correct Findings / Total Findings (blind trust)
With reasoning: Trust = (Correct + Reasoning) / Total (verified trust)
```

**Real-world comparison:**

| Scenario | Black-Box | Transparent |
|----------|-----------|-------------|
| True Positive | Developer verifies manually (10 min) | Developer trusts immediately (30 sec) |
| False Positive | Developer wastes 10 min, loses trust | Developer sees agent logic, reports improvement |
| Low Confidence | Developer unsure, asks colleague (20 min) | Developer sees 60% confidence, investigates (5 min) |
| High Confidence | Developer assumes correctness, ships bug | Developer sees 95% + reasoning, confident to fix |

**Trust compounds:**
- First PR: Developer verifies everything (agent provides structure)
- Second PR: Developer trusts 80% of findings (speed improvement)
- Fifth PR: Developer trusts 95%, only verifies CRITICAL (10x productivity)

---

### Reason 2: Debugging & Improvement

**Problem:** Agent makes mistake. How do you fix it?

**Without reasoning:**
1. Developer: "This is wrong."
2. Plugin author: "What did the agent consider?"
3. Developer: "No idea. Just wrong."
4. Plugin author: "Can't fix what I can't see."

**Result:** Unfixable agents, stagnant quality.

**With reasoning:**
1. Developer: "This is wrong."
2. Developer: *reads reasoning* "Agent didn't check for sanitization at entry point"
3. Developer: Reports issue with specific gap
4. Plugin author: Updates prompt → "Always check entry point for sanitization"
5. Agent improves for everyone

**Feedback loop:**
```
Agent decision → Reasoning exposed → Developer feedback → Prompt improvement → Better agent
```

**Examples of fixable issues:**

| Finding | Reasoning Revealed | Fix |
|---------|-------------------|-----|
| False Positive | "Didn't check for nonce on line 138" | Add context window expansion |
| Severity Error | "Weighted style same as security" | Adjust severity weights |
| Missed Detection | "Only checked SQL, not NoSQL" | Expand pattern matching |
| Over-Confidence | "95% confidence, no mitigations checked" | Add mitigation discovery step |

**Improvement velocity:**
- Without reasoning: 1-2 improvements per quarter (guessing)
- With reasoning: 5-10 improvements per quarter (targeted fixes)

---

### Reason 3: Learning & Knowledge Transfer

**Educational value:**

**Scenario: Junior developer reviews PR with agent**

**Without reasoning:**
```markdown
Issue: Tight coupling detected
Fix: Use dependency injection
```

Junior dev: "What's tight coupling? Why is this bad?"
**Result:** Follows recommendation blindly, doesn't learn principle.

**With reasoning:**
```markdown
Issue: Tight coupling detected

<reasoning>
1. Detected direct instantiation: `new PaymentGateway()` in controller
2. Problem: Controller now depends on concrete class
3. Impact: Can't swap payment providers without modifying controller
4. Violates: Dependency Inversion Principle (SOLID)
5. Testing impact: Can't mock PaymentGateway in tests
6. Example: Adding Stripe alongside PayPal requires controller changes
7. Solution: Inject PaymentGatewayInterface via constructor
8. Benefit: Open/Closed Principle—extend without modification
9. Reference: Software Architecture skill § Dependency Inversion
</reasoning>
```

Junior dev: "Oh! Now I understand why DI matters. I see the concrete problem."
**Result:** Junior learns principle, applies to future code proactively.

**Knowledge transfer paths:**

1. **Principles → Practice**
   - Agent explains SOLID violations with examples
   - Developer internalizes principles through repetition
   - Developer writes better code without agent

2. **Patterns → Recognition**
   - Agent shows "This is Strategy pattern opportunity"
   - Developer learns to recognize pattern triggers
   - Developer suggests patterns in design reviews

3. **Security → Threat Modeling**
   - Agent explains exploit paths in reasoning
   - Developer learns attacker mindset
   - Developer writes defensive code by default

**Longitudinal learning:**

| Time | Without Reasoning | With Reasoning |
|------|-------------------|----------------|
| Week 1 | Follow recommendations | Understand principles |
| Month 1 | Rely on agent for every PR | Recognize 30% of issues before agent |
| Month 3 | Still dependent on agent | Recognize 60%, proactive fixes |
| Month 6 | No improvement | Write secure/maintainable code by default |

**Team scaling:**
- One agent trains entire team
- Senior knowledge propagates via agent reasoning
- Consistent quality across all experience levels

---

### Reason 4: Verification & Audit Trail

**Compliance & Quality Assurance:**

**Problem:** Manager asks: "Why did this bug slip through code review?"

**Without reasoning:**
```
PR #1234 reviewed by agent: APPROVED
```
Manager: "Did agent check for SQL injection?"
Team: "Probably?"

**Result:** No accountability, no improvement.

**With reasoning:**
```
PR #1234 reviewed by security-reviewer agent

<reasoning>
1. SQL Injection Check: PERFORMED
   - Scanned for $wpdb->query: FOUND (3 instances)
   - Verified prepare() usage: ALL PROTECTED ✓
   - Checked dynamic table names: NONE FOUND ✓
2. XSS Check: PERFORMED
   - Scanned for echo/print: FOUND (12 instances)
   - Verified escaping: 10/12 PROTECTED (2 flagged) ✓
3. CSRF Check: PERFORMED
   - Scanned for $_POST: FOUND (5 instances)
   - Verified nonces: ALL PROTECTED ✓
...
Overall: 2 HIGH issues flagged, PR blocked
```

Manager: "Agent performed comprehensive checks. The escaped XSS was in generated code not part of this PR."

**Result:** Audit trail, process validation, improvement identified.

**Audit scenarios:**

1. **Post-Incident Analysis**
   - Security breach occurred
   - Review agent reasoning for PR that introduced vulnerability
   - Identify gap in agent checks
   - Update agent to prevent recurrence

2. **Quality Metrics**
   - Track agent confidence scores over time
   - Low confidence findings → Agent uncertainty areas
   - High false positive rate → Pattern tuning needed
   - Measure: "Agent checked 47 potential issues, flagged 3"

3. **Compliance Documentation**
   - PCI DSS requires code review documentation
   - Agent reasoning = auditable review process
   - Demonstrates due diligence
   - Maps checks to compliance requirements

**Accountability matrix:**

| Question | Without Reasoning | With Reasoning |
|----------|-------------------|----------------|
| Was SQL injection checked? | Unknown | Yes, 3 instances verified |
| Why was this approved? | No explanation | All checks passed (see reasoning) |
| Did agent miss this? | Probably | Agent checked X but didn't cover Y (gap identified) |
| How confident was agent? | Unknown | 75% confidence (acknowledged uncertainty) |

---

### Reason 5: False Positive Investigation

**Problem:** Developer suspects false positive but must verify.

**Without reasoning:**
- Manual verification: 10 minutes
- Mental load: High (reconstruct agent logic)
- Trust impact: Negative (if FP confirmed)

**With reasoning:**
- Scan reasoning: 30 seconds
- Identify gap: "Agent missed context on line X"
- Report specific issue: 1 minute
- Trust impact: Neutral (understand limitation)

**False positive categories:**

| FP Type | Without Reasoning | With Reasoning |
|---------|-------------------|----------------|
| **Context Missed** | "Agent wrong" | "Agent checked lines 50-60, missed line 45 context" |
| **Pattern Over-Match** | "Bad pattern" | "Agent matched regex, didn't check semantic meaning" |
| **Framework Convention** | "Agent doesn't know WP" | "Agent flagged WordPress convention as violation" |
| **Custom Implementation** | "Agent missed our pattern" | "Agent checked standard patterns, not custom sanitizer" |

**Improvement feedback:**

```markdown
False Positive Report:

Issue: Agent flagged nonce violation (FP)

Reasoning showed:
- Agent checked for wp_verify_nonce() directly
- Didn't recognize custom wrapper: verify_request_security()

Fix: Update agent pattern to include custom wrappers
```

**Result:** Specific, actionable improvement.

---

## Implementation Strategy

### Phase 1: Prompt Pattern (1-2 hours per agent)

**Goal:** Add reasoning capability to agent prompts.

**Implementation:**

```markdown
# Addition to agent prompt (e.g., security-reviewer.md)

## Output Format

Your review must include findings with the following structure:

### Standard Finding Format (Always Include)

```markdown
### Issue #N: [Title]
**Location:** [file:line]
**Severity:** [CRITICAL|HIGH|MEDIUM|LOW]
**Confidence:** [0-100%]
**Description:** [Brief description]
**Recommended Fix:** [How to fix]
```

### Reasoning Format (Include if VERBOSE=true)

For each finding, wrap reasoning in expandable details:

```markdown
<details>
<summary>🔍 Show reasoning process</summary>

**Detection:**
- What pattern/check triggered this finding?
- How was this detected (regex, taint analysis, AST check)?

**Context Analysis:**
1. What surrounding code was examined?
2. What data flow was traced?
3. What mitigations were checked?

**Checks Performed:**
- List each validation step
- ✓ for passed checks
- ✗ for failed checks

**Confidence: [0-100%]**
- Factors increasing confidence: [list]
- Factors decreasing confidence: [list]
- Acknowledged uncertainties: [list]

**Severity Rationale:**
- Why this severity level?
- What makes this [SEVERITY] vs [OTHER_LEVEL]?
- Business impact: [description]

**References:**
- Skills consulted: [list]
- Documentation: [links]
- Standards: [OWASP, WPCS, etc.]

**Alternative Interpretations:**
- What other explanations were considered?
- Why were they rejected?
- Edge cases: [list]

</details>
```

## Environment Variables

Check for `VERBOSE` environment variable:
- If `VERBOSE=true`: Include reasoning blocks for all findings
- If `VERBOSE=false` or unset: Standard output only

You may use this pattern in reasoning:
```

**Integration with agents:**

```diff
# plugins/pirategoat-tools/agents/security-reviewer.md

+ ## Verbose Mode
+
+ This agent supports verbose reasoning mode. Set VERBOSE=true to see detailed
+ reasoning for each finding.
+
+ ```bash
+ VERBOSE=true claude agent security-reviewer
+ ```

  ## Your Review Process

  ### Step 5: Report Findings

- Write findings to output file in markdown format.
+ Write findings to output file. Include reasoning blocks if VERBOSE=true.
+
+ ```bash
+ # Check environment
+ VERBOSE=${VERBOSE:-false}
+
+ # Write findings
+ if [ "$VERBOSE" = "true" ]; then
+   echo "Verbose mode: Including reasoning blocks"
+   # Include <details> reasoning for each issue
+ else
+   echo "Standard mode: Concise findings"
+   # Omit reasoning blocks
+ fi
+ ```
```

**Effort per agent:** 1-2 hours

**Agents to update:**
1. `architecture-reviewer.md` (1.5 hours)
2. `security-reviewer.md` (1.5 hours)
3. `performance-reviewer.md` (1.5 hours)
4. `tests-reviewer.md` (1 hour)
5. `patterns-reviewer.md` (1.5 hours)

**Total:** ~8 hours for 5 reviewers

---

### Phase 2: Skill Integration (2-3 hours)

**Goal:** Update `pr-reviewing` skill to support verbose mode.

```diff
# plugins/pirategoat-tools/skills/pr-reviewing/SKILL.md

  ## Step 4: Spawn Review Agents

  ```bash
  OUTPUT_DIR="/tmp/pr-review-${PR_ID}"
+ VERBOSE=${VERBOSE:-false}  # Default to false

  # Spawn agents in parallel
  spawn_agent architecture-reviewer \
    --context "$OUTPUT_DIR/context.json" \
+   --env VERBOSE="$VERBOSE" \
    --output "$OUTPUT_DIR/architecture-review.md" &

  spawn_agent security-reviewer \
    --context "$OUTPUT_DIR/context.json" \
+   --env VERBOSE="$VERBOSE" \
    --output "$OUTPUT_DIR/security-review.md" &
  ```

+ ## Verbose Mode Usage
+
+ Enable verbose reasoning for all reviewers:
+
+ ```bash
+ VERBOSE=true /pr-review 123
+ ```
+
+ This will:
+ - Include reasoning blocks in all review outputs
+ - Show confidence scores and rationale
+ - Expose decision-making process
+ - Add cross-references and checks performed
+
+ **When to use verbose mode:**
+ - Investigating false positives
+ - Learning from agent decisions
+ - Debugging agent behavior
+ - Training new team members
+ - Auditing review process
```

---

### Phase 3: Documentation & Examples (2 hours)

**Goal:** Create comprehensive documentation and examples.

#### 3.1 User Guide

```markdown
# docs/verbose-reasoning-mode.md

# Verbose Reasoning Mode

## What is it?

Verbose reasoning mode exposes the internal decision-making process of review agents.
Each finding includes detailed reasoning showing:
- How it was detected
- What context was analyzed
- What checks were performed
- Why this severity level
- Confidence scores and uncertainties

## When to use it

**Use verbose mode when:**
- ✅ Investigating suspected false positives
- ✅ Learning architectural/security principles
- ✅ Debugging agent behavior
- ✅ Training new developers
- ✅ Auditing review process
- ✅ Providing feedback to improve agents

**Skip verbose mode when:**
- ⏭️ Routine PR reviews (trusted agent)
- ⏭️ Fast iteration (standard output sufficient)
- ⏭️ High volume reviews (conciseness preferred)

## How to enable

### Option 1: Environment Variable (Recommended)

```bash
# Enable for all reviewers
VERBOSE=true /pr-review 123

# Enable for specific reviewer
VERBOSE=true claude agent security-reviewer
```

### Option 2: Per-Agent Flag

```bash
# In pr-reviewing workflow
spawn_agent security-reviewer --verbose
```

## Output Comparison

### Standard Mode (Default)

```markdown
### Issue #1: SQL Injection Vulnerability
**Location:** `src/OrderProcessor.php:67`
**Severity:** CRITICAL
**Confidence:** 95%

Direct variable interpolation in SQL query without prepared statement.

**Fix:** Use `$wpdb->prepare()` with placeholders.
```

### Verbose Mode

```markdown
### Issue #1: SQL Injection Vulnerability
**Location:** `src/OrderProcessor.php:67`
**Severity:** CRITICAL
**Confidence:** 95%

Direct variable interpolation in SQL query without prepared statement.

<details>
<summary>🔍 Show reasoning process</summary>

**Detection:**
- Pattern match: Direct variable in SQL string
- Regex: `\$wpdb->query.*\{.*\}`
- Variable: `$order_id` (unparameterized)

**Context Analysis:**
1. Traced `$order_id` origin: REST API parameter
2. Checked sanitization: NOT FOUND
3. Verified input validation: Insufficient (only !empty)

**Checks Performed:**
- ✗ Prepared statement: NOT FOUND
- ✗ Input sanitization: MISSING
- ✗ Capability check: MISSING

**Confidence: 95%**
- Direct user input confirmed
- No mitigation found
- DELETE operation (destructive)

**Severity Rationale: CRITICAL**
1. Direct exploitation path
2. Data loss risk (DELETE)
3. Public API exposure
4. No authentication barrier

**References:**
- WPCS: `WordPress.DB.PreparedSQL`
- OWASP A03:2021 (Injection)
- Skill: `wordpress-backend-dev`

</details>

**Fix:** Use `$wpdb->prepare()` with placeholders.
```

## Reading Reasoning Blocks

### Detection Section
Shows what triggered the finding:
- Pattern matched (regex, AST, taint analysis)
- Code location and context
- Specific vulnerable construct

### Context Analysis Section
Shows what surrounding code was examined:
- Data flow tracing
- Sanitization checks
- Mitigation discovery
- Related code patterns

### Checks Performed Section
Shows validation steps:
- ✓ = Check passed (mitigating factor)
- ✗ = Check failed (confirming factor)
- Lists all validation performed

### Confidence Score
Numerical confidence (0-100%):
- 90-100%: High confidence (act immediately)
- 70-89%: Moderate confidence (verify if critical)
- 50-69%: Low confidence (investigate further)
- <50%: Very low confidence (likely FP)

**Factors listed:**
- What increases confidence
- What decreases confidence
- Acknowledged uncertainties

### Severity Rationale
Explains severity level choice:
- Why CRITICAL vs HIGH?
- Business impact analysis
- Compliance implications
- Attack surface assessment

### References Section
Shows what knowledge was consulted:
- Skills loaded
- Documentation referenced
- Standards cited (OWASP, WPCS)
- Past similar issues

### Alternative Interpretations
Shows critical thinking:
- Other explanations considered
- Why alternatives were rejected
- Edge cases acknowledged

## Providing Feedback

If reasoning reveals an issue:

1. **False Positive:** Report with specific reasoning gap
   ```
   Issue: Agent missed context on line X
   Reasoning showed: Agent only checked lines Y-Z
   Improvement: Expand context window
   ```

2. **Incorrect Severity:** Report with rationale mismatch
   ```
   Issue: Agent rated LOW, should be HIGH
   Reasoning showed: Didn't consider business impact
   Improvement: Add impact assessment step
   ```

3. **Low Confidence:** Report uncertainty sources
   ```
   Issue: Agent 60% confident but flagged anyway
   Reasoning showed: Uncertain about custom pattern
   Improvement: Add pattern to known safe list
   ```

## Performance Considerations

Verbose mode impact:
- **Token usage:** +20-40% (reasoning text added)
- **Processing time:** +0-5% (reasoning already computed)
- **Output size:** +100-200% (expanded markdown)

**Recommendation:** Use selectively, not by default for all reviews.
```

---

#### 3.2 Examples Document

```markdown
# docs/verbose-reasoning-examples.md

# Verbose Reasoning Examples

Real-world examples showing value of verbose reasoning mode.

## Example 1: Caught Mitigation

**Code:**
```php
function delete_user_data( $user_id ) {
    global $wpdb;
    $wpdb->query( "DELETE FROM {$wpdb->usermeta} WHERE user_id = $user_id" );
}
```

**Standard Output:**
```markdown
Issue: SQL Injection on line 3
Severity: CRITICAL
```

**Developer reaction:** "But $user_id is an integer from WordPress core. False positive?"

**Verbose Output:**
```markdown
Issue: SQL Injection on line 3
Severity: CRITICAL

<reasoning>
Detection: Direct variable in SQL query: $user_id

Context Analysis:
1. Traced $user_id origin: Function parameter
2. Checked callers:
   - Called from wp_delete_user() hook (WP core)
   - WP core guarantees $user_id is integer
3. Verified WordPress sanitization: PRESENT ✓

Wait—WordPress core sanitizes, but:
4. Function is publicly accessible (not private/protected)
5. Other plugins could call directly with unsanitized input
6. Best practice: Don't rely on caller sanitization

Checks Performed:
- ✗ Local sanitization: MISSING (relies on caller)
- ✗ Type enforcement: MISSING (no type hint)
- ✗ Prepared statement: NOT USED

Confidence: 75% (lower due to WP core usage)

Severity Rationale: HIGH (not CRITICAL)
- Mitigated by WP core in normal flow
- Vulnerable if called directly by other code
- Best practice violation (defense in depth)

Recommendation: Add local sanitization as defense-in-depth
</reasoning>
```

**Developer reaction:** "Ah! Not a false positive, but nuanced. I see the defense-in-depth principle. Will add absint() locally."

**Value:** Developer understands both the mitigation AND the principle.

---

## Example 2: Legitimate Complexity

**Code:**
```php
function calculate_tax( $subtotal, $tax_rate, $location ) {
    $base_tax = $subtotal * $tax_rate;

    if ( $location === 'EU' ) {
        $vat = $subtotal * 0.20;
        return $base_tax + $vat;
    } elseif ( $location === 'US' ) {
        $state_tax = $this->get_state_tax( $location );
        return $base_tax + $state_tax;
    } elseif ( $location === 'CA' ) {
        $provincial_tax = $this->get_provincial_tax( $location );
        $federal_tax = $subtotal * 0.05;
        return $base_tax + $provincial_tax + $federal_tax;
    }

    return $base_tax;
}
```

**Standard Output:**
```markdown
Issue: High cyclomatic complexity (4 branches)
Severity: MEDIUM
Recommendation: Refactor using Strategy pattern
```

**Developer reaction:** "This is tax logic. It's inherently complex. Over-engineering?"

**Verbose Output:**
```markdown
Issue: High cyclomatic complexity (4 branches)
Severity: LOW (adjusted from MEDIUM)

<reasoning>
Detection: 4 conditional branches in single method

Context Analysis:
1. Function purpose: Tax calculation (domain complexity)
2. Branches represent: Geographic tax rules (business logic)
3. Complexity is: Essential (inherent in domain)
4. Compared to: Similar tax functions in codebase

Essential vs Accidental Complexity:
- Essential: Tax rules genuinely differ by location
- Accidental: None—code is straightforward
- Each branch: Single responsibility (one location)

Checks Performed:
- ✓ Each branch is cohesive
- ✓ No duplicate logic
- ✓ Clear business meaning
- ✗ Could extract strategies (over-engineering?)

Confidence: 60% (legitimate domain complexity)

Severity Rationale: LOW (not MEDIUM)
- Complexity is essential (business rules)
- Code is readable and maintainable
- Refactoring would add ceremony without value
- May revisit if: 10+ locations or duplicated logic

Recommendation: ACCEPTABLE AS-IS
Alternative: If locations grow to 10+, consider Strategy pattern

References:
- Software Architecture skill: Essential vs Accidental Complexity
- Domain-Driven Design: Accept domain complexity
</reasoning>
```

**Developer reaction:** "Perfect! Agent understands context and business domain. I trust this LOW rating."

**Value:** Agent demonstrates nuance, not rigid rule enforcement.

---

## Example 3: Confidence Calibration

**Code:**
```php
function format_currency( $amount ) {
    return '$' . number_format( $amount, 2 );
}
```

**Standard Output:**
```markdown
Issue: Hardcoded currency symbol
Severity: MEDIUM
```

**Developer reaction:** "Our app is US-only. Not an issue. False positive?"

**Verbose Output:**
```markdown
Issue: Hardcoded currency symbol
Severity: MEDIUM
Confidence: 50% (uncertain)

<reasoning>
Detection: Hardcoded '$' symbol in currency formatting

Context Analysis:
1. Checked for i18n: NOT FOUND
2. Checked for multi-currency support: NOT FOUND
3. Searched project docs: No mention of internationalization plans
4. Examined similar functions: All use '$' (pattern in codebase)

Uncertainty:
- Unknown: Is this a US-only application?
- Unknown: Are there future internationalization plans?
- Unknown: Is hardcoding acceptable per project standards?

Checks Performed:
- ✗ Uses wp_i18n functions: NO
- ✗ Currency parameter: NOT PRESENT
- ✓ Consistent with codebase: YES

Confidence: 50% (need more context)

Severity Rationale: MEDIUM (if i18n needed)
- Could be NON-ISSUE if US-only application
- Could be HIGH if internationalization planned
- Flagging as MEDIUM (default for i18n issues)

Recommendation: CLARIFICATION NEEDED
- If US-only app: Mark as acceptable, document decision
- If i18n planned: Refactor to use currency parameter
- Check project CLAUDE.md for internationalization policy

References:
- I18n Best Practices: Always prepare for localization
- YAGNI Principle: Don't add complexity until needed

</reasoning>
```

**Developer reaction:** "Agent is right to flag, but acknowledges uncertainty. I'll document 'US-only' decision in CLAUDE.md so future reviews know."

**Value:** Acknowledges uncertainty, prompts documentation of architectural decisions.

---

## Example 4: Cross-Reference Discovery

**Code:**
```php
class OrderService {
    private $db;
    private $logger;
    private $emailer;
    private $cache;

    public function createOrder( $data ) {
        $this->db->save( $data );
        $this->logger->log( 'Order created' );
        $this->emailer->send( $data['email'] );
        $this->cache->clear();
    }
}
```

**Standard Output:**
```markdown
Issue: Tight coupling to concrete implementations
Severity: HIGH
```

**Developer reaction:** "What implementations? I'm confused."

**Verbose Output:**
```markdown
Issue: Tight coupling to concrete implementations
Severity: HIGH
Confidence: 85%

<reasoning>
Detection: Class with 4 dependencies, no interface type hints

Context Analysis:
1. Examined dependencies: $db, $logger, $emailer, $cache
2. Checked for interfaces: NONE FOUND
3. Checked constructor:
   ```php
   public function __construct() {
       $this->db = new Database();
       $this->logger = new Logger();
       $this->emailer = new Emailer();
       $this->cache = new Cache();
   }
   ```
4. Problem: Direct instantiation of concrete classes

SOLID Violation: Dependency Inversion Principle
- High-level OrderService depends on low-level concrete classes
- Should depend on abstractions (interfaces)

Impact Analysis:
- Can't mock dependencies in tests (tightly coupled)
- Can't swap implementations (e.g., different emailer)
- Violates Open/Closed (must modify to extend)

Checks Performed:
- ✗ Constructor injection: NOT USED
- ✗ Interface type hints: NONE
- ✗ Factory pattern: NOT PRESENT
- ✓ Single Responsibility: YES (only orders)

Confidence: 85%

Severity Rationale: HIGH
- Testability: Very difficult to unit test
- Extensibility: Locked to single implementation
- Violates: 2 SOLID principles (DIP + OCP)
- Maintenance: Changes to dependencies break this class

Business Impact:
- Testing cost: 3x higher (integration tests required)
- Feature velocity: Slowed by tight coupling
- Technical debt: Will require refactoring for extensions

Recommended Fix:
1. Define interfaces: DatabaseInterface, LoggerInterface, etc.
2. Use constructor injection
3. Type-hint against interfaces
4. Use DI container or factory

Example:
```php
class OrderService {
    public function __construct(
        DatabaseInterface $db,
        LoggerInterface $logger,
        EmailerInterface $emailer,
        CacheInterface $cache
    ) {
        // Dependencies injected, testable, swappable
    }
}
```

References:
- Software Architecture skill: Dependency Inversion Principle
- SOLID Principles: Interface Segregation + Dependency Inversion
- Testing Patterns: Constructor Injection for Testability

Cross-Referenced:
- Similar issues: Found 12 other classes with same pattern
- Project architecture: No DI container detected
- Recommendation: Consider adding DI container (PHP-DI, Laravel Container)

</reasoning>
```

**Developer reaction:** "Wow! Comprehensive explanation. I understand DIP now. I'll refactor all 12 classes and add a DI container."

**Value:** Educational, actionable, contextual, cross-referenced. Developer learns principle + specific fix + project-wide solution.

---

## Example 5: False Positive Transparency

**Code:**
```php
function process_payment( $order_id ) {
    if ( ! wp_verify_nonce( $_POST['nonce'], 'payment_action' ) ) {
        wp_die( 'Invalid nonce' );
    }

    $amount = sanitize_text_field( $_POST['amount'] );

    // Process payment...
    $this->gateway->charge( $order_id, $amount );
}
```

**Standard Output:**
```markdown
Issue: Missing nonce verification for payment processing
Severity: CRITICAL
```

**Developer reaction:** "What?! Line 2 HAS nonce verification. False positive!"

**Without reasoning:** Developer files bug report, loses trust in agent.

**Verbose Output:**
```markdown
Issue: Missing nonce verification for payment processing
Severity: CRITICAL

<reasoning>
Detection: Detected $_POST usage on line 6 (payment processing)

Context Analysis:
1. Searched for wp_verify_nonce(): FOUND (line 2) ✓
2. Verified nonce check gates payment code: CHECKING...
3. Analyzed control flow:
   - Line 2: Nonce check with early exit
   - Line 3: wp_die() terminates on failure ✓
   - Line 6: $_POST usage is AFTER nonce check ✓
4. Confirmed: Payment code is protected by nonce

Wait—FALSE ALARM:
- Nonce verification IS present (line 2)
- Early exit pattern prevents execution without nonce
- Payment processing is properly gated

Checks Performed:
- ✓ Nonce verification: FOUND (line 2)
- ✓ Early exit on failure: CONFIRMED (wp_die)
- ✓ $_POST usage is protected: YES

Confidence: 99% (false positive)

Severity Rationale: NO ISSUE
- Initially flagged: $_POST without nonce
- Re-analysis: Nonce IS present with proper gating
- False positive: Pattern match didn't recognize early exit

Self-Correction:
This is a FALSE POSITIVE. Nonce verification is correctly implemented.

References:
- WordPress Security Handbook: Early exit pattern (recommended)
- WPCS: Nonce verification via wp_verify_nonce()

Apology:
Initial detection was overly aggressive. The code is secure.

</reasoning>

**Status:** NO ISSUE (nonce properly verified on line 2)
```

**Developer reaction:** "Excellent! Agent caught its own false positive and self-corrected. Reasoning showed the thought process. I trust this agent more now."

**Value:** Transparency builds trust even when agent makes mistakes. Self-correction demonstrates robust analysis.

---

## Key Takeaways

### What Reasoning Provides

1. **Trust:** Understand why agent flagged something
2. **Learning:** Internalize principles through explanations
3. **Context:** See nuance, not just rigid rules
4. **Debugging:** Identify gaps in agent logic
5. **Verification:** Audit decision-making process
6. **Confidence:** Calibrated certainty, not binary judgments

### When Verbose Mode Shines

- ✅ False positives (understand why)
- ✅ Low confidence findings (see uncertainty)
- ✅ Educational reviews (learn principles)
- ✅ Audit trails (compliance documentation)
- ✅ Agent improvement (targeted feedback)

### When to Skip Verbose Mode

- ⏭️ Routine reviews (trusted agent)
- ⏭️ High-volume PRs (conciseness needed)
- ⏭️ Fast iteration (standard output sufficient)
```

---

## Expected Outcomes

### Quantitative Improvements

| Metric | Before Verbose Mode | After Verbose Mode | Improvement |
|--------|---------------------|--------------------|----|
| **False positive investigation time** | 10 min/issue | 1 min/issue | 10x faster |
| **Developer trust score** | 60% (blind trust) | 90% (verified trust) | +50% |
| **Agent improvement velocity** | 2 fixes/quarter | 8 fixes/quarter | 4x faster |
| **Junior developer ramp-up time** | 3 months (trial/error) | 1 month (learn from agent) | 3x faster |
| **Review verification time** | 5 min/finding (manual check) | 30 sec/finding (skim reasoning) | 10x faster |
| **Agent feedback specificity** | 20% actionable | 80% actionable | 4x better |

### Qualitative Improvements

**Developer Experience:**
- ✅ Understand agent decisions (no black box)
- ✅ Learn principles through explanations
- ✅ Trust findings (reasoning validates correctness)
- ✅ Debug false positives quickly (see agent logic)
- ✅ Provide targeted feedback (specific gaps identified)

**Agent Quality:**
- ✅ Faster improvement cycle (targeted fixes)
- ✅ Better calibration (confidence scores tuned)
- ✅ Fewer false positives (gaps discovered and fixed)
- ✅ More educational value (knowledge transfer)
- ✅ Audit-ready (compliance documentation)

**Team Scaling:**
- ✅ Junior developers learn from agent reasoning
- ✅ Consistent quality (agent teaches standards)
- ✅ Knowledge preservation (agent captures senior expertise)
- ✅ Onboarding acceleration (interactive learning)

---

## Risks & Mitigations

### Risk 1: Verbose Overhead (Token Cost)

**Scenario:** Reasoning adds 20-40% tokens per review.

**Analysis:**
- Standard review: 5,000 tokens
- Verbose review: 6,500 tokens (+30%)
- Cost: $0.0195 (standard) vs $0.0254 (verbose) = +$0.0059

**At scale:**
- 100 PRs/week: +$30/year
- **Not material** compared to value gained

**Mitigation:**
```yaml
# Use verbose mode selectively
verbose_mode:
  default: false
  enable_for:
    - first_review_by_developer  # Learning mode
    - critical_severity_findings # Verify reasoning
    - low_confidence_findings    # Investigate uncertainty
    - suspected_false_positives  # Debug agent logic
```

**Result:** Use when valuable, skip when unnecessary. Cost increase < 10%.

---

### Risk 2: Reasoning Quality (Hallucinated Reasoning)

**Scenario:** Agent invents reasoning that sounds plausible but is incorrect.

**Example:**
```markdown
<reasoning>
1. Checked for SQL injection: FOUND
2. Verified sanitization: MISSING
</reasoning>
```

**Reality:** Agent didn't actually check sanitization, just claims it did.

**Mitigation 1: Prompt Engineering**
```markdown
CRITICAL: Your reasoning must be FACTUAL and VERIFIABLE.

DO NOT claim you performed checks you didn't actually perform.
DO NOT invent context that doesn't exist.
DO NOT hallucinate code analysis.

Your reasoning should reference:
- Actual code snippets (quote lines)
- Actual grep/search commands you ran
- Actual skills you loaded
- Actual checks you performed

If you didn't check something, say: "Did not verify [X]"
If you're uncertain, say: "Unable to determine [Y]"
```

**Mitigation 2: Factual Anchoring**
```markdown
## Reasoning Requirements

Each reasoning block must include:
1. **Evidence:** Quote actual code lines
2. **Commands:** Show actual grep/search commands run
3. **Skills:** List skills actually loaded
4. **Limitations:** Acknowledge what you DIDN'T check

Example:
```bash
# Command run:
grep -n "wp_verify_nonce" file.php

# Result:
42: if ( wp_verify_nonce( $_POST['nonce'] ) ) {

# Evidence: Line 42 contains nonce check
```

This grounds reasoning in verifiable facts.
```

**Mitigation 3: Audit Sampling**
```python
# Periodically verify reasoning accuracy
def audit_reasoning(review_output):
    """
    Randomly sample 10% of reasoning blocks.
    Manually verify claims against actual code.
    Track accuracy over time.
    """
    sample = random.sample(review_output.findings, k=0.1 * len(findings))

    for finding in sample:
        verify_claims_in_reasoning(finding.reasoning)
        # If inaccurate, update prompt to improve
```

**Result:** Reasoning accuracy > 95%, with continuous improvement.

---

### Risk 3: Developer Laziness (Over-Reliance on Reasoning)

**Scenario:** Developer reads reasoning, doesn't verify code themselves, ships bug.

**Example:**
```markdown
Agent: "No SQL injection found"
<reasoning>Checked all queries: ALL SAFE</reasoning>

Developer: "Agent says safe, shipping!"
Reality: Agent missed a query, SQL injection present.
```

**Mitigation 1: Confidence-Based Verification**
```markdown
## Confidence Thresholds for Verification

- **95-100%:** Trust, act immediately
- **80-94%:** Trust, optional spot-check
- **60-79%:** Verify critical paths manually
- **<60%:** Always verify manually

If agent confidence is low, YOU MUST VERIFY.
```

**Mitigation 2: Critical Findings Require Human Verification**
```yaml
# Policy: CRITICAL findings must be verified by human
verification_required:
  severity: [CRITICAL]
  confidence_below: 90%

# Agent output:
Issue: SQL Injection (CRITICAL, 85% confidence)
⚠️ HUMAN VERIFICATION REQUIRED (confidence < 90%)
```

**Mitigation 3: Reasoning as Educational, Not Gospel**
```markdown
## Using Reasoning Correctly

Reasoning is for:
- ✅ Understanding agent logic
- ✅ Learning principles
- ✅ Debugging false positives
- ✅ Providing feedback

Reasoning is NOT:
- ❌ Replacement for code review
- ❌ Absolute truth
- ❌ Excuse to skip verification

ALWAYS verify CRITICAL findings manually, regardless of reasoning.
```

**Result:** Reasoning enhances judgment, doesn't replace it.

---

### Risk 4: Output Verbosity (Readability)

**Scenario:** Too much reasoning overwhelms developers.

**Example:**
```markdown
# 50-page review output with reasoning for every minor issue

Developer: "TL;DR. Skipping entire review."
```

**Mitigation 1: Collapsible Reasoning (HTML Details)**
```markdown
### Issue #1: SQL Injection
**Severity:** CRITICAL

Brief description here.

<details>
<summary>🔍 Show reasoning (click to expand)</summary>
[Full reasoning here]
</details>
```

**Result:** Reasoning available but not intrusive. Developer expands when needed.

**Mitigation 2: Reasoning Summary**
```markdown
### Issue #1: SQL Injection
**Severity:** CRITICAL
**Reasoning Summary:** Direct user input + no prepare() + public API = exploitable

<details>
<summary>🔍 Full reasoning</summary>
[Detailed reasoning here]
</details>
```

**Result:** Glanceable summary, expandable details.

**Mitigation 3: Selective Verbose Mode**
```bash
# Only show reasoning for CRITICAL/HIGH
VERBOSE=critical /pr-review 123

# Only show reasoning for low-confidence findings
VERBOSE=uncertain /pr-review 123
```

**Result:** Verbose only where valuable.

---

## Testing Strategy

### Unit Tests: Reasoning Presence

```python
# tests/test_verbose_reasoning.py

def test_verbose_mode_includes_reasoning():
    """
    Verify VERBOSE=true adds reasoning blocks.
    """
    # Run review in verbose mode
    output = run_agent('security-reviewer', verbose=True, code=TEST_CODE)

    # Verify reasoning present
    assert '<details>' in output
    assert 'Show reasoning process' in output
    assert '<reasoning>' in output or 'Detection:' in output

def test_standard_mode_omits_reasoning():
    """
    Verify VERBOSE=false omits reasoning.
    """
    # Run review in standard mode
    output = run_agent('security-reviewer', verbose=False, code=TEST_CODE)

    # Verify reasoning absent
    assert '<details>' not in output
    assert '<reasoning>' not in output

def test_reasoning_contains_required_sections():
    """
    Verify reasoning includes all required sections.
    """
    output = run_agent('security-reviewer', verbose=True, code=TEST_CODE)

    reasoning = extract_reasoning_blocks(output)

    for r in reasoning:
        assert 'Detection:' in r
        assert 'Context Analysis:' in r
        assert 'Checks Performed:' in r
        assert 'Confidence:' in r
        assert 'Severity Rationale:' in r
        # References optional
```

---

### Integration Tests: Reasoning Accuracy

```python
def test_reasoning_matches_detection():
    """
    Verify reasoning claims match actual analysis.
    """
    code_with_sql_injection = """
    $wpdb->query( "DELETE FROM posts WHERE id = {$_GET['id']}" );
    """

    output = run_agent('security-reviewer', verbose=True, code=code_with_sql_injection)

    # Verify detection
    assert 'SQL Injection' in output

    # Verify reasoning accuracy
    reasoning = extract_reasoning(output)

    # Agent should mention:
    assert 'Direct variable in SQL' in reasoning  # True
    assert '$_GET' in reasoning  # True (user input)
    assert 'prepare()' in reasoning  # Should check for this

    # Agent should NOT claim:
    assert 'sanitization found' not in reasoning.lower()  # False claim

def test_confidence_calibration():
    """
    Verify confidence scores are reasonable.
    """
    # High-confidence case: obvious SQL injection
    obvious_sqli = """$wpdb->query("DELETE FROM posts WHERE id = {$_GET['id']}");"""
    output1 = run_agent('security-reviewer', verbose=True, code=obvious_sqli)
    confidence1 = extract_confidence(output1)
    assert confidence1 >= 90  # Should be high confidence

    # Low-confidence case: ambiguous pattern
    ambiguous = """$wpdb->query( $this->build_query( $params ) );"""
    output2 = run_agent('security-reviewer', verbose=True, code=ambiguous)
    confidence2 = extract_confidence(output2)
    assert confidence2 < 70  # Should be lower confidence (can't trace build_query)

def test_severity_rationale_present():
    """
    Verify severity rationale explains level choice.
    """
    code = """$wpdb->query("DELETE FROM posts WHERE id = {$_GET['id']}");"""
    output = run_agent('security-reviewer', verbose=True, code=code)

    reasoning = extract_reasoning(output)

    # Should explain why CRITICAL
    assert 'CRITICAL' in reasoning or 'exploitability' in reasoning.lower()
    assert 'DELETE' in reasoning or 'destructive' in reasoning.lower()
    # Should reference impact
```

---

### User Acceptance Testing

```markdown
# UAT: Verbose Reasoning Mode

## Test Cases

### TC1: False Positive Investigation
**Goal:** Verify reasoning helps identify false positives

1. Create PR with false positive trigger (nonce present but not detected)
2. Run review with VERBOSE=true
3. Read reasoning block
4. **Expected:** Reasoning shows agent missed context, explains gap
5. **Verify:** Developer identifies issue in < 2 minutes

### TC2: Learning Experience
**Goal:** Verify reasoning teaches principles

1. Junior developer reviews PR with architectural issue
2. Run review with VERBOSE=true
3. Junior reads reasoning for "Tight Coupling" issue
4. **Expected:** Reasoning explains SOLID principle, shows impact, provides examples
5. **Verify:** Junior can explain principle afterwards (quiz)

### TC3: Confidence Calibration
**Goal:** Verify confidence scores guide verification

1. Run review with various code patterns (high/low confidence)
2. Review confidence scores in verbose output
3. **Expected:** High confidence (>90%) = trust, Low confidence (<70%) = verify
4. **Verify:** Confidence correlates with actual accuracy (spot check)

### TC4: Audit Trail
**Goal:** Verify reasoning provides compliance documentation

1. Run security review with VERBOSE=true
2. Extract reasoning blocks
3. **Expected:** Clear record of what was checked, how, and why
4. **Verify:** Reasoning maps to compliance requirements (PCI DSS, OWASP)

### TC5: Agent Improvement
**Goal:** Verify reasoning enables targeted improvements

1. Identify false positive in review
2. Read reasoning to understand gap
3. Report specific improvement needed
4. **Expected:** Feedback is actionable (specific check missing)
5. **Verify:** Improvement can be implemented in agent prompt
```

---

## Rollout Plan

### Week 1: Implementation (8-10 hours)

**Monday (3 hours):**
- Update architecture-reviewer.md with reasoning pattern
- Update security-reviewer.md with reasoning pattern
- Test locally with sample PRs

**Tuesday (3 hours):**
- Update performance-reviewer.md with reasoning pattern
- Update tests-reviewer.md with reasoning pattern
- Update patterns-reviewer.md with reasoning pattern

**Wednesday (2 hours):**
- Update pr-reviewing skill to pass VERBOSE flag
- Create user documentation (verbose-reasoning-mode.md)
- Create examples document (verbose-reasoning-examples.md)

**Thursday (1 hour):**
- Test end-to-end with real PRs
- Verify reasoning quality
- Adjust prompts if needed

**Friday (1 hour):**
- Create CHANGELOG entry
- Update README
- Deploy to production

---

### Week 2: Validation & Iteration

**Monday-Wednesday:**
- Monitor verbose mode usage
- Collect developer feedback
- Identify reasoning quality issues
- Spot-check reasoning accuracy (sampling)

**Thursday:**
- Analyze feedback
- Identify prompt improvements
- Update reasoning patterns based on gaps

**Friday:**
- Deploy improvements
- Document findings

---

### Week 3: Optimization

**Monday-Tuesday:**
- Implement selective verbose mode (VERBOSE=critical, VERBOSE=uncertain)
- Add reasoning summaries for glanceability
- Optimize token usage

**Wednesday-Thursday:**
- Create training materials for team
- Run workshop: "How to Read Agent Reasoning"
- Gather more feedback

**Friday:**
- Finalize documentation
- Update best practices guide
- Celebrate launch

---

## Success Metrics

### Must Achieve (Go/No-Go):

| Metric | Target | Measurement |
|--------|--------|-------------|
| **Reasoning accuracy** | ≥ 90% | Spot-check claims against code (sample 20 findings) |
| **Developer trust increase** | ≥ +20% | Survey: "I trust agent findings" (before/after) |
| **False positive debug time** | ≤ 2 min | Time from suspicion to gap identification |
| **Reasoning completeness** | 100% | All findings have required reasoning sections |

**If any metric fails:** Iterate on prompt engineering.

### Nice to Have (Optimization Targets):

| Metric | Target | Measurement |
|--------|--------|-------------|
| **Agent improvement velocity** | +3x | Number of targeted improvements per quarter |
| **Junior dev ramp-up time** | -50% | Time to independently review PRs |
| **Verification time saved** | -80% | Time to verify finding (reasoning vs manual) |
| **Verbose mode adoption** | 30% of reviews | % of PRs reviewed with VERBOSE=true |

---

## ROI Analysis

### Investment

**Development time:** 8-10 hours
- Phase 1 (Prompts): 8 hours (5 agents × 1.5h + 0.5h testing)
- Phase 2 (Skill): 1 hour
- Phase 3 (Docs): 2 hours
- **Total: 11 hours**

**Assuming $100/hour developer rate:** $1,100 investment

### Return

**Time savings per finding:**
- False positive investigation: 10 min → 1 min = **9 minutes saved**
- Verification time: 5 min → 0.5 min = **4.5 minutes saved**
- Learning (junior dev): 20 min research → 5 min reading = **15 minutes saved**

**Scenario: 100 PRs/week, 5 findings/PR**
- Total findings: 500/week
- False positives (10%): 50/week × 9 min = **450 min/week saved (7.5 hours)**
- Verification (50%): 250/week × 4.5 min = **1,125 min/week saved (18.75 hours)**
- Learning (20% are learning opportunities): 100/week × 15 min = **1,500 min/week saved (25 hours)**

**Total weekly savings: 51.25 hours**
**Annual savings: 2,665 hours = $266,500/year @ $100/hour**

**ROI:** 24,227% first year (absurdly high because reasoning is cheap to add)

**More conservative estimate (50% of theoretical savings):**
- Annual savings: 1,332 hours = $133,250/year
- **ROI:** 12,013% first year

**Payback period:** < 1 day

### Intangible Benefits

1. **Trust:** Developer confidence in agent findings (+50%)
2. **Learning:** Knowledge transfer from agent to developers (immeasurable)
3. **Quality:** Better agent improvement velocity (+4x)
4. **Compliance:** Audit trail for code review process (regulatory value)
5. **Debugging:** Faster agent improvement cycle (cumulative improvement)

---

## Alternative Approaches Considered

### Alternative 1: No Reasoning (Status Quo)

**Pros:**
- Zero implementation effort
- Minimal token usage
- Concise output

**Cons:**
- Black-box decisions
- Low developer trust
- Slow agent improvement
- No learning opportunity
- Poor debugging experience

**Verdict:** ❌ Rejected - Benefits of transparency far outweigh costs

---

### Alternative 2: Always-On Verbose Mode

**Approach:** Include reasoning in every review by default.

**Pros:**
- Maximum transparency
- Consistent experience
- No toggle needed

**Cons:**
- Verbose output always (even when not needed)
- +30% token cost on every review
- Overwhelming for routine reviews
- TL;DR effect (developers skip long output)

**Verdict:** ❌ Rejected - Too verbose for routine use

---

### Alternative 3: Reasoning on Request (API)

**Approach:** Agent stores reasoning internally, exposes via API.

```bash
# Initial review (standard output)
/pr-review 123

# Request reasoning for specific finding
/pr-review 123 --explain-finding 3
```

**Pros:**
- Minimal default output
- Reasoning available when needed
- Low token cost (only on request)

**Cons:**
- Requires additional API infrastructure
- Two-step process (review, then request)
- Reasoning not visible in markdown output
- Complex implementation

**Verdict:** ⚠️ Possible future enhancement, but overkill for MVP

---

### Alternative 4: Hybrid with Collapsible Details (SELECTED ✅)

**Approach:** Standard output by default, reasoning in expandable `<details>` blocks.

**Pros:**
- ✅ Glanceable by default (collapsed)
- ✅ Reasoning available when needed (expand)
- ✅ Works in markdown (GitHub, GitLab)
- ✅ No infrastructure changes
- ✅ Opt-in verbosity (VERBOSE env var)

**Cons:**
- ⚠️ Slightly more complex prompt
- ⚠️ +30% tokens when verbose enabled (acceptable)

**Verdict:** ✅ **SELECTED** - Best balance of usability and implementation simplicity

---

## Detailed Implementation Checklist

### Prerequisites
- [ ] Review awesome-agentic-patterns § Verbose Reasoning
- [ ] Analyze existing agent outputs (baseline)
- [ ] Define reasoning section structure (template)

### Phase 1: Agent Prompts (8 hours)
- [ ] Update architecture-reviewer.md (1.5h)
  - [ ] Add reasoning prompt pattern
  - [ ] Add VERBOSE environment check
  - [ ] Add example reasoning block
  - [ ] Test with sample PR
- [ ] Update security-reviewer.md (1.5h)
  - [ ] Add reasoning prompt pattern
  - [ ] Add VERBOSE environment check
  - [ ] Add example reasoning block
  - [ ] Test with SQL injection sample
- [ ] Update performance-reviewer.md (1.5h)
  - [ ] Add reasoning prompt pattern
  - [ ] Add VERBOSE environment check
  - [ ] Add example reasoning block
  - [ ] Test with performance issue sample
- [ ] Update tests-reviewer.md (1h)
  - [ ] Add reasoning prompt pattern
  - [ ] Add VERBOSE environment check
  - [ ] Add example reasoning block
  - [ ] Test with test coverage sample
- [ ] Update patterns-reviewer.md (1.5h)
  - [ ] Add reasoning prompt pattern
  - [ ] Add VERBOSE environment check
  - [ ] Add example reasoning block
  - [ ] Test with code smell sample
- [ ] Integration testing (1h)
  - [ ] Run all agents with VERBOSE=true
  - [ ] Verify reasoning quality
  - [ ] Spot-check accuracy

### Phase 2: Skill Integration (1 hour)
- [ ] Update pr-reviewing skill
  - [ ] Add VERBOSE environment variable
  - [ ] Pass VERBOSE to all spawned agents
  - [ ] Document verbose mode usage
  - [ ] Test end-to-end workflow

### Phase 3: Documentation (2 hours)
- [ ] Create docs/verbose-reasoning-mode.md
  - [ ] What is verbose reasoning
  - [ ] When to use it
  - [ ] How to enable
  - [ ] Output comparison
  - [ ] Reading reasoning blocks
  - [ ] Providing feedback
- [ ] Create docs/verbose-reasoning-examples.md
  - [ ] Example 1: Caught Mitigation
  - [ ] Example 2: Legitimate Complexity
  - [ ] Example 3: Confidence Calibration
  - [ ] Example 4: Cross-Reference Discovery
  - [ ] Example 5: False Positive Transparency
- [ ] Update README.md
  - [ ] Add verbose mode section
  - [ ] Link to documentation
- [ ] Update CHANGELOG.md
  - [ ] Add feature description
  - [ ] Note all updated agents

### Phase 4: Deployment & Monitoring
- [ ] Deploy to production
- [ ] Monitor usage (VERBOSE=true vs false)
- [ ] Collect developer feedback
- [ ] Spot-check reasoning accuracy (sample 20 findings)
- [ ] Identify improvement opportunities
- [ ] Iterate on prompts as needed

---

## Recommendation

**IMPLEMENT IMMEDIATELY**

**Reasoning:**
1. **Highest impact/effort ratio** (266% ROI with 11 hours investment)
2. **Universal benefit** (improves trust, learning, debugging across all agents)
3. **Low risk** (reasoning doesn't change detection, only transparency)
4. **Fast implementation** (8-10 hours for 5 agents)
5. **Compound benefits** (trust + learning + improvement velocity)
6. **Competitive advantage** (transparent AI is differentiator)

**Implementation approach:**
1. Start with **Phase 1** (agent prompts) - 8 hours
2. Validate with real PRs and developer feedback
3. Iterate on reasoning quality (prompt tuning)
4. Proceed to **Phase 2 & 3** (skill integration + docs)
5. Monitor usage and continuously improve

---

## Questions for Approval

1. **Go/No-Go:** Approve implementation of verbose reasoning mode for all review agents?

2. **Reasoning Format:** Use hybrid approach (collapsed `<details>` blocks) or always-on verbose?
   - **Recommendation:** Hybrid (collapsed by default, opt-in verbose)

3. **Required Sections:** Which reasoning sections are mandatory?
   - **Recommendation:** Detection, Context Analysis, Checks Performed, Confidence, Severity Rationale (References optional)

4. **Default Mode:** VERBOSE=false (opt-in) or VERBOSE=true (opt-out)?
   - **Recommendation:** Default false (opt-in for debugging/learning)

5. **Selective Verbose:** Support VERBOSE=critical, VERBOSE=uncertain modes?
   - **Recommendation:** Phase 2 enhancement (after MVP validation)

6. **Token Budget:** Acceptable to add +30% tokens for verbose reviews?
   - **Recommendation:** Yes, when explicitly enabled. Minimal cost for massive value.

7. **Accuracy Validation:** How to verify reasoning claims are factual?
   - **Recommendation:** Spot-check sampling (20 findings/week) + prompt engineering for factual grounding

Please approve or request modifications to this proposal before I proceed with implementation.
