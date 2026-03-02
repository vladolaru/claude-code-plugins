# Dead-Code-Reviewer Efficiency Fixes Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Implement the top 4 efficiency fixes from the dead-code-reviewer analysis, targeting the ~6.0 wasted calls/session that affect all 14 reviewer agents pipeline-wide.

**Architecture:** Three of the four fixes are in the bootstrap pipeline infrastructure (`bootstrap-reviewer.py` and `reviewer-protocol.md`) and benefit all agents. One fix is dead-code-reviewer-specific (Step 0 conditional). Changes are additive — no existing behavior is removed, only augmented with better API documentation, size handling, and smarter defaults.

**Tech Stack:** Python (`bootstrap-reviewer.py`), Markdown (agent definitions, shared protocol), pytest (deterministic tests)

**Analysis doc:** `.claude/docs/analysis/2026-02-28-dead-code-reviewer-efficiency.md`

---

## Fix Scope and Prioritization

From the analysis, fixes ranked by impact:

| # | Fix | Impact | Sessions | Scope |
|---|-----|--------|----------|-------|
| 1 | ReviewOutputBuilder API usage example in bootstrap | 3.2 calls/session | 6/6 (100%) | **All 14 agents** |
| 2 | Bootstrap output size cap for large PRs | 2.7 calls/session | 3/6 (50%) | All agents (large PRs) |
| 3 | Remove post-write verification reads | 1.5 calls/session | 6/6 (100%) | All agents |
| 4 | Conditional Step 0 PHP check | 1.0 call/session | 3/6 (50%) | Dead-code-specific |

**Not in this plan (lower priority):**
- Fix 5 (search strategy tips) — agent-specific prompt tuning, lower ROI
- Fix 6 (agent name alias) — 2/6 sessions, trivial impact, existing validation is working

---

### Task 1: Add ReviewOutputBuilder Usage Example to Bootstrap Output

This is the highest-impact fix — saves ~3.2 calls/session across ALL 14 agents (~30 calls/pipeline-wide per full review). Every agent currently hallucmates wrong method names (`add_finding()`, `add_positive_observation()`, `set_summary()`, `write()`) because bootstrap Section 3 shows only the constructor, not the methods.

**Files:**
- Modify: `plugins/pirategoat-tools/scripts/bootstrap-reviewer.py:388-395`

**Step 1: Write the failing test**

In `plugins/pirategoat-tools/tests/test_bootstrap_reviewer.py`, find the `TestBuildOutput` class (or create it near the existing output tests). Add a test that verifies the bootstrap output contains the full API usage example:

```python
class TestReviewOutputBuilderAPIExample:
    """Bootstrap Section 3 must include a complete ReviewOutputBuilder usage example."""

    def test_output_contains_add_issue_example(self):
        """The usage example must show add_issue() with named parameters."""
        output = build_output(
            agent_name="security-reviewer",
            plugin_root="/fake/root",
            status="OK",
            review_rules="rules",
            domain_rules=None,
            scope_output="scope",
            exploration_scope=None,
            output_dir="/tmp/pr-review-42",
            pr_number="42",
            reviewer_name="security",
        )
        assert "add_issue(" in output
        assert "severity=" in output
        assert "title=" in output
        assert "file=" in output
        assert "description=" in output
        assert "recommendation=" in output

    def test_output_contains_add_positive_example(self):
        """The usage example must show add_positive()."""
        output = build_output(
            agent_name="security-reviewer",
            plugin_root="/fake/root",
            status="OK",
            review_rules="rules",
            domain_rules=None,
            scope_output="scope",
            exploration_scope=None,
            output_dir="/tmp/pr-review-42",
            pr_number="42",
            reviewer_name="security",
        )
        assert "add_positive(" in output

    def test_output_contains_save_example(self):
        """The usage example must show save() with output_dir."""
        output = build_output(
            agent_name="security-reviewer",
            plugin_root="/fake/root",
            status="OK",
            review_rules="rules",
            domain_rules=None,
            scope_output="scope",
            exploration_scope=None,
            output_dir="/tmp/pr-review-42",
            pr_number="42",
            reviewer_name="security",
        )
        assert "save(" in output
        # Must reference the output_dir variable, not a hardcoded path
        assert "output_dir" in output.lower() or "/tmp/pr-review-42" in output

    def test_output_contains_set_files_reviewed(self):
        """The usage example must show set_files_reviewed()."""
        output = build_output(
            agent_name="security-reviewer",
            plugin_root="/fake/root",
            status="OK",
            review_rules="rules",
            domain_rules=None,
            scope_output="scope",
            exploration_scope=None,
            output_dir="/tmp/pr-review-42",
            pr_number="42",
            reviewer_name="security",
        )
        assert "set_files_reviewed(" in output

    def test_output_contains_set_confidence(self):
        """The usage example must show set_confidence()."""
        output = build_output(
            agent_name="security-reviewer",
            plugin_root="/fake/root",
            status="OK",
            review_rules="rules",
            domain_rules=None,
            scope_output="scope",
            exploration_scope=None,
            output_dir="/tmp/pr-review-42",
            pr_number="42",
            reviewer_name="security",
        )
        assert "set_confidence(" in output

    def test_output_contains_no_verify_instruction(self):
        """The usage example must tell agents not to verify save() output."""
        output = build_output(
            agent_name="security-reviewer",
            plugin_root="/fake/root",
            status="OK",
            review_rules="rules",
            domain_rules=None,
            scope_output="scope",
            exploration_scope=None,
            output_dir="/tmp/pr-review-42",
            pr_number="42",
            reviewer_name="security",
        )
        # Must include instruction to NOT read output files back
        lower = output.lower()
        assert "do not" in lower and ("read" in lower or "verify" in lower) and ("output file" in lower or "save()" in lower)
```

**Step 2: Run tests to verify they fail**

Run: `pytest plugins/pirategoat-tools/tests/test_bootstrap_reviewer.py::TestReviewOutputBuilderAPIExample -v`
Expected: All 6 tests FAIL (the current output only has constructor, no method examples)

**Step 3: Add the usage example to `build_output()` in `bootstrap-reviewer.py`**

In the `build_output()` function (around line 394, after the constructor line), add the full usage example. Replace the current Section 3 API block (lines 388-395) — keeping the existing constructor but adding methods after it:

After line 395 (`f'  builder = ReviewOutputBuilder(pr_id={pr_id_str}, reviewer="{reviewer_name}")'`), insert:

```python
    lines.append(f'  builder.add_issue(severity="high", title="Issue title", file="path/to/file.py",')
    lines.append(f'      description="What is wrong", recommendation="How to fix",')
    lines.append(f'      category="category-name", line=42, confidence=0.9)')
    lines.append(f'  builder.add_positive("Positive observation text")')
    lines.append(f'  builder.set_files_reviewed(N)')
    lines.append(f'  builder.set_confidence(0.85)')
    lines.append(f'  result = builder.save("{output_dir}")  # returns {{"json": path, "markdown": path}}')
    lines.append(f"")
    lines.append(f"  IMPORTANT: save() confirms success via its return value.")
    lines.append(f"  Do NOT read the output files back to verify — proceed directly to the STATUS signal.")
```

**Step 4: Run tests to verify they pass**

Run: `pytest plugins/pirategoat-tools/tests/test_bootstrap_reviewer.py::TestReviewOutputBuilderAPIExample -v`
Expected: All 6 tests PASS

Run: `pytest plugins/pirategoat-tools/tests/test_bootstrap_reviewer.py -v`
Expected: All existing tests still pass (no regression)

**Step 5: Commit**

```bash
git add plugins/pirategoat-tools/scripts/bootstrap-reviewer.py plugins/pirategoat-tools/tests/test_bootstrap_reviewer.py
git commit -m "$(cat <<'EOF'
fix(pirategoat-tools): add ReviewOutputBuilder API usage example to bootstrap

Every reviewer agent (14 total) hallucinated wrong method names on first
write attempt — add_finding(), add_positive_observation(), set_summary(),
write() — because bootstrap Section 3 only showed the constructor, not
the actual methods. Cross-agent validation confirmed the pattern across
all agent types (pr-reviewer 22 instances, security 20, etc.).

Adding a complete usage example with all core methods (add_issue,
add_positive, set_files_reviewed, set_confidence, save) and an explicit
instruction to trust save()'s return value eliminates ~3.2 wasted calls
per dispatch × ~10 agents = ~30 saved calls per full review session.

Refs dead-code-reviewer-efficiency-analysis
EOF
)"
```

---

### Task 2: Remove the Skipped `ReviewOutputBuilder API` Section from Protocol

The shared `reviewer-protocol.md` has a `## ReviewOutputBuilder API` section (lines 150-168) that is already listed in `REVIEWER_PROTOCOL_SKIP_SECTIONS` — meaning bootstrap strips it and replaces it with the concrete snippet from Task 1. However, the protocol still serves as fallback documentation when bootstrap isn't available. Verify this is consistent.

**Files:**
- Read-only verification: `plugins/pirategoat-tools/agents/shared/reviewer-protocol.md:150-168`
- Read-only verification: `plugins/pirategoat-tools/scripts/bootstrap-reviewer.py:112-118`

**Step 1: Verify the skip list includes the API section**

Read `bootstrap-reviewer.py` lines 112-118 and confirm `"## ReviewOutputBuilder API"` is in `REVIEWER_PROTOCOL_SKIP_SECTIONS`. (It already is — line 116.)

**Step 2: Verify consistency — no action needed**

The protocol's Section `## ReviewOutputBuilder API` (lines 150-168) is skipped by bootstrap and replaced with the concrete example from Task 1. The protocol's section remains as fallback documentation for manual use (no bootstrap). This is the correct architecture — no changes needed.

**Step 3: No commit** — this is a verification-only task.

---

### Task 3: Cap Bootstrap Output Size for Large PRs

When bootstrap output exceeds ~30KB (large PRs with 15+ files), Claude Code persists it to a file. The agent then reads the file, which is even larger (line numbers), triggering another persistence cascade — wasting 2-3 calls. This affects 50% of sessions with large PRs.

The fix: when the scope output (which contains inline diffs) exceeds a threshold, write the full scope to a file and provide only a summary inline with instructions to read the file with offset/limit.

**Files:**
- Modify: `plugins/pirategoat-tools/scripts/bootstrap-reviewer.py:328-412` (the `build_output` function)

**Step 1: Write the failing test**

Add to `plugins/pirategoat-tools/tests/test_bootstrap_reviewer.py`:

```python
class TestBootstrapOutputSizeCap:
    """Bootstrap caps inline scope when output would exceed size threshold."""

    def _build_large_output(self, scope_size_kb=50):
        """Helper: build output with a scope of the given KB size."""
        # Generate scope output large enough to trigger capping
        large_scope = "x" * (scope_size_kb * 1024)
        return build_output(
            agent_name="security-reviewer",
            plugin_root="/fake/root",
            status="OK",
            review_rules="rules here",
            domain_rules=None,
            scope_output=large_scope,
            exploration_scope=None,
            output_dir="/tmp/pr-review-42",
            pr_number="42",
            reviewer_name="security",
        )

    def test_small_scope_included_inline(self):
        """Scope under threshold is included inline (no change from current behavior)."""
        small_scope = "diff content here\n" * 100  # ~2KB
        output = build_output(
            agent_name="security-reviewer",
            plugin_root="/fake/root",
            status="OK",
            review_rules="rules here",
            domain_rules=None,
            scope_output=small_scope,
            exploration_scope=None,
            output_dir="/tmp/pr-review-42",
            pr_number="42",
            reviewer_name="security",
        )
        assert small_scope in output

    def test_large_scope_truncated(self):
        """Scope over threshold is truncated with a file reference."""
        output = self._build_large_output(scope_size_kb=50)
        # The full 50KB scope should NOT be in the output
        assert len(output) < 40 * 1024  # output should be well under 40KB total

    def test_large_scope_has_file_reference(self):
        """When scope is truncated, output tells agent where to read the full scope."""
        output = self._build_large_output(scope_size_kb=50)
        # Must mention a file path for the full scope
        assert "scoped-diff.patch" in output or "full scope" in output.lower() or "Read" in output

    def test_large_scope_has_read_instructions(self):
        """When scope is truncated, output tells agent to use offset/limit."""
        output = self._build_large_output(scope_size_kb=50)
        lower = output.lower()
        assert "offset" in lower or "limit" in lower or "head" in lower
```

**Step 2: Run tests to verify they fail**

Run: `pytest plugins/pirategoat-tools/tests/test_bootstrap_reviewer.py::TestBootstrapOutputSizeCap -v`
Expected: `test_large_scope_truncated`, `test_large_scope_has_file_reference`, and `test_large_scope_has_read_instructions` FAIL. `test_small_scope_included_inline` PASSES.

**Step 3: Implement the size cap in `build_output()`**

In `bootstrap-reviewer.py`, modify the `build_output()` function. After `scope_output` is assembled into the lines list, check its size. If the scope section exceeds 15KB, write it to a file and replace the inline content with a truncated version plus instructions.

Add a constant near the top of the file (after `REVIEWER_PROTOCOL_SKIP_SECTIONS`):

```python
# Maximum inline scope size before capping (in characters).
# Beyond this, the full scope is written to a file and only a summary is inlined.
# Prevents Claude Code's output persistence cascade for large PRs.
SCOPE_INLINE_CAP = 15 * 1024  # 15KB
```

Modify the Section 2 block in `build_output()`. After the line `lines.append(scope_output)` (line 366), wrap it in a size check:

```python
    # Section 2: Review Content (middle position — processing zone)
    lines.append("--- Section 2: REVIEW CONTENT (what to review) ---")
    lines.append("")
    lines.append("=== REVIEW SCOPE ===")

    if len(scope_output) > SCOPE_INLINE_CAP:
        # Write full scope to file to avoid output persistence cascade
        scope_file = os.path.join(output_dir, "scoped-diff.patch")
        with open(scope_file, 'w') as f:
            f.write(scope_output)
        # Show first ~200 lines inline
        scope_lines = scope_output.splitlines()
        truncated = "\n".join(scope_lines[:200])
        lines.append(truncated)
        lines.append("")
        lines.append(f"... SCOPE TRUNCATED ({len(scope_lines)} total lines) ...")
        lines.append(f"Full scope written to: {scope_file}")
        lines.append("Read it with offset/limit parameters (e.g., offset=200, limit=200) to avoid re-truncation.")
    else:
        lines.append(scope_output)

    lines.append("")
```

Note: The `output_dir` parameter is already available in `build_output()`. The `os` module is already imported. Add `os.makedirs(output_dir, exist_ok=True)` before the file write if output_dir might not exist yet — but the caller (`main()`) already creates it at line 564, so this is safe.

**Step 4: Run tests to verify they pass**

Run: `pytest plugins/pirategoat-tools/tests/test_bootstrap_reviewer.py::TestBootstrapOutputSizeCap -v`
Expected: All 4 tests PASS

Run: `pytest plugins/pirategoat-tools/tests/test_bootstrap_reviewer.py -v`
Expected: All tests pass

**Step 5: Commit**

```bash
git add plugins/pirategoat-tools/scripts/bootstrap-reviewer.py plugins/pirategoat-tools/tests/test_bootstrap_reviewer.py
git commit -m "$(cat <<'EOF'
fix(pirategoat-tools): cap bootstrap inline scope to prevent size cascade

When a PR's diff exceeds ~30KB, Claude Code persists the bootstrap output
to a file. The agent reads the file (even larger with line numbers),
which triggers another persistence, cascading 2-3 wasted tool calls
before the agent learns to use offset/limit.

Scope output exceeding 15KB is now written to scoped-diff.patch in the
output directory. The first 200 lines are shown inline with instructions
to read the rest using offset/limit. Small PRs are unaffected.

Saves ~2.7 wasted calls/session for the 50% of sessions with large PRs.

Refs dead-code-reviewer-efficiency-analysis
EOF
)"
```

---

### Task 4: Make Dead-Code Step 0 Conditional on PHP Files in Scope

The dead-code-reviewer's Step 0 runs `git grep -c 'add_action|add_filter' -- '*.php'` even when the diff contains zero PHP files — wasting ~1 call in 50% of sessions. The fix: have bootstrap inject a `DYNAMIC_DISPATCH_RISK` signal computed from the file list, so the agent can skip Step 0 when no PHP files are in scope.

**Files:**
- Modify: `plugins/pirategoat-tools/scripts/bootstrap-reviewer.py:328-412` (add DYNAMIC_DISPATCH_RISK to output)
- Modify: `plugins/pirategoat-tools/agents/dead-code-reviewer.md:89-101` (make Step 0 conditional)

**Step 1: Write the failing test for bootstrap**

Add to `plugins/pirategoat-tools/tests/test_bootstrap_reviewer.py`:

```python
class TestDynamicDispatchRisk:
    """Bootstrap injects DYNAMIC_DISPATCH_RISK for dead-code-reviewer."""

    def test_dead_code_reviewer_gets_dispatch_risk(self):
        """dead-code-reviewer output includes DYNAMIC_DISPATCH_RISK."""
        scope_with_php = "=== FILES ===\nsrc/payment.php  (+10 -5)\nsrc/utils.ts  (+3 -1)\n=== DIFFS ==="
        output = build_output(
            agent_name="dead-code-reviewer",
            plugin_root="/fake/root",
            status="OK",
            review_rules="rules",
            domain_rules=None,
            scope_output=scope_with_php,
            exploration_scope=None,
            output_dir="/tmp/pr-review-42",
            pr_number="42",
            reviewer_name="dead-code",
        )
        assert "DYNAMIC_DISPATCH_RISK:" in output

    def test_dispatch_risk_high_with_php_files(self):
        """DYNAMIC_DISPATCH_RISK is 'high' when PHP files are in scope."""
        scope_with_php = "=== FILES ===\nsrc/payment.php  (+10 -5)\nsrc/utils.ts  (+3 -1)\n=== DIFFS ==="
        output = build_output(
            agent_name="dead-code-reviewer",
            plugin_root="/fake/root",
            status="OK",
            review_rules="rules",
            domain_rules=None,
            scope_output=scope_with_php,
            exploration_scope=None,
            output_dir="/tmp/pr-review-42",
            pr_number="42",
            reviewer_name="dead-code",
        )
        # Find the DYNAMIC_DISPATCH_RISK line
        for line in output.splitlines():
            if "DYNAMIC_DISPATCH_RISK:" in line:
                assert "high" in line.lower()
                break

    def test_dispatch_risk_low_without_php_files(self):
        """DYNAMIC_DISPATCH_RISK is 'low' when no PHP files are in scope."""
        scope_no_php = "=== FILES ===\nsrc/utils.ts  (+3 -1)\nsrc/component.tsx  (+20 -5)\n=== DIFFS ==="
        output = build_output(
            agent_name="dead-code-reviewer",
            plugin_root="/fake/root",
            status="OK",
            review_rules="rules",
            domain_rules=None,
            scope_output=scope_no_php,
            exploration_scope=None,
            output_dir="/tmp/pr-review-42",
            pr_number="42",
            reviewer_name="dead-code",
        )
        for line in output.splitlines():
            if "DYNAMIC_DISPATCH_RISK:" in line:
                assert "low" in line.lower()
                break

    def test_other_agents_no_dispatch_risk(self):
        """Non-dead-code agents do NOT get DYNAMIC_DISPATCH_RISK."""
        output = build_output(
            agent_name="security-reviewer",
            plugin_root="/fake/root",
            status="OK",
            review_rules="rules",
            domain_rules=None,
            scope_output="scope",
            exploration_scope=None,
            output_dir="/tmp/pr-review-42",
            pr_number="42",
            reviewer_name="security",
        )
        assert "DYNAMIC_DISPATCH_RISK:" not in output
```

**Step 2: Run tests to verify they fail**

Run: `pytest plugins/pirategoat-tools/tests/test_bootstrap_reviewer.py::TestDynamicDispatchRisk -v`
Expected: All 4 tests FAIL

**Step 3: Implement DYNAMIC_DISPATCH_RISK in `build_output()`**

In `bootstrap-reviewer.py`, modify `build_output()` to compute and inject the risk signal. Add this logic after the scope section and before Section 3, only for dead-code-reviewer:

```python
    # Inject DYNAMIC_DISPATCH_RISK for dead-code-reviewer
    if agent_name == "dead-code-reviewer":
        # Check if any PHP files are in the scope
        has_php = any(
            line.strip().split("  ")[0].strip().endswith(".php")
            for line in scope_output.splitlines()
            if line.strip() and not line.startswith("===")
        )
        risk = "high (PHP files in scope — check for hooks, filters, callbacks)" if has_php else "low (0 PHP files in scope — skip Step 0)"
        lines.append(f"DYNAMIC_DISPATCH_RISK: {risk}")
        lines.append("")
```

**Step 4: Update dead-code-reviewer.md Step 0 to be conditional**

In `plugins/pirategoat-tools/agents/dead-code-reviewer.md`, replace lines 89-101 (the Step 0 section):

Current:
```markdown
### Step 0: Assess Dynamic Dispatch Risk

Before cataloging symbols, determine the codebase's dynamic dispatch profile:

```bash
# Count framework hook registrations to gauge false positive risk
git grep -c 'add_action\|add_filter\|register_rest_route\|add_shortcode' -- '*.php' 2>/dev/null | tail -5
```

**High dynamic dispatch** (WordPress/WooCommerce plugins): Many functions are called by the framework, not by grep-able code. Apply the False Positive Checklist aggressively. Start confidence at 60 and require boosters to report.

**Low dynamic dispatch** (standalone JS/TS libraries): Most calls are explicit. Start confidence at 75. Standard verification sufficient.
```

Replace with:
```markdown
### Step 0: Assess Dynamic Dispatch Risk

Check the `DYNAMIC_DISPATCH_RISK` value from bootstrap output.

- **`DYNAMIC_DISPATCH_RISK: low`** — No PHP files in scope. Skip the grep below and start confidence at 75. Standard verification sufficient.
- **`DYNAMIC_DISPATCH_RISK: high`** — PHP files are in scope. Run the command below to gauge false positive risk:

```bash
# Count framework hook registrations to gauge false positive risk
git grep -c 'add_action\|add_filter\|register_rest_route\|add_shortcode' -- '*.php' 2>/dev/null | tail -5
```

**High dynamic dispatch** (WordPress/WooCommerce plugins): Many functions are called by the framework, not by grep-able code. Apply the False Positive Checklist aggressively. Start confidence at 60 and require boosters to report.

**Low dynamic dispatch** (standalone JS/TS libraries): Most calls are explicit. Start confidence at 75. Standard verification sufficient.
```

**Step 5: Run tests to verify they pass**

Run: `pytest plugins/pirategoat-tools/tests/test_bootstrap_reviewer.py::TestDynamicDispatchRisk -v`
Expected: All 4 tests PASS

Run: `pytest plugins/pirategoat-tools/tests/ -v`
Expected: All tests pass

**Step 6: Commit**

```bash
git add plugins/pirategoat-tools/scripts/bootstrap-reviewer.py plugins/pirategoat-tools/tests/test_bootstrap_reviewer.py plugins/pirategoat-tools/agents/dead-code-reviewer.md
git commit -m "$(cat <<'EOF'
fix(pirategoat-tools): make dead-code Step 0 conditional on PHP files

The dead-code-reviewer's Step 0 runs git grep for PHP hooks even when
the diff contains zero PHP files, wasting ~1 call in 50% of sessions.

Bootstrap now injects DYNAMIC_DISPATCH_RISK computed from the file list:
'high' when PHP files are in scope (run the grep), 'low' when none are
(skip it). This moves the decision to deterministic code rather than
relying on the agent to check file extensions.

Refs dead-code-reviewer-efficiency-analysis
EOF
)"
```

---

### Task 5: Output Filename Mismatch Fix

The analysis found a filename mismatch: bootstrap says output should be `dead-code-review.json` but `ReviewOutputBuilder("dead-code")` writes `dead-code.json` via `save()`. One session had to `cp` files to work around this. The bootstrap output says `{reviewer_name}-review.json` but `save()` writes `{reviewer_name}.json`.

**Files:**
- Modify: `plugins/pirategoat-tools/scripts/review_output_simple.py:197-211` (fix `save()` to match expected names)

**Step 1: Write the failing test**

In `plugins/pirategoat-tools/tests/test_bootstrap_reviewer.py` (or a new test file if more appropriate), add:

```python
class TestOutputFilenameConsistency:
    """Output filenames from ReviewOutputBuilder.save() match bootstrap expectations."""

    def test_save_uses_review_suffix(self, tmp_path):
        """save() should write {reviewer}-review.json and {reviewer}-review.md."""
        sys.path.insert(0, str(SCRIPTS_DIR))
        from review_output_simple import ReviewOutputBuilder

        builder = ReviewOutputBuilder(pr_id="42", reviewer="dead-code")
        result = builder.save(str(tmp_path))

        assert result["json"].endswith("dead-code-review.json"), f"Got: {result['json']}"
        assert result["markdown"].endswith("dead-code-review.md"), f"Got: {result['markdown']}"
        assert os.path.isfile(result["json"])
        assert os.path.isfile(result["markdown"])

    def test_bootstrap_output_matches_save_filenames(self):
        """Bootstrap OUTPUT_FILES paths match what save() actually creates."""
        output = build_output(
            agent_name="dead-code-reviewer",
            plugin_root="/fake/root",
            status="OK",
            review_rules="rules",
            domain_rules=None,
            scope_output="scope",
            exploration_scope=None,
            output_dir="/tmp/pr-review-42",
            pr_number="42",
            reviewer_name="dead-code",
        )
        assert "/tmp/pr-review-42/dead-code-review.json" in output
        assert "/tmp/pr-review-42/dead-code-review.md" in output
```

**Step 2: Run tests to verify they fail**

Run: `pytest plugins/pirategoat-tools/tests/test_bootstrap_reviewer.py::TestOutputFilenameConsistency -v`
Expected: `test_save_uses_review_suffix` FAILS (save() writes `dead-code.json`, not `dead-code-review.json`). `test_bootstrap_output_matches_save_filenames` PASSES (bootstrap already uses `-review` suffix).

**Step 3: Fix `save()` in `review_output_simple.py`**

In `review_output_simple.py`, change lines 202-203:

From:
```python
        json_path = os.path.join(output_dir, f"{self.reviewer}.json")
        md_path = os.path.join(output_dir, f"{self.reviewer}.md")
```

To:
```python
        json_path = os.path.join(output_dir, f"{self.reviewer}-review.json")
        md_path = os.path.join(output_dir, f"{self.reviewer}-review.md")
```

**Step 4: Run tests to verify they pass**

Run: `pytest plugins/pirategoat-tools/tests/test_bootstrap_reviewer.py::TestOutputFilenameConsistency -v`
Expected: Both tests PASS

Run: `pytest plugins/pirategoat-tools/tests/ -v`
Expected: All tests pass (check for any existing tests that depend on the old filename format and update them)

**Step 5: Commit**

```bash
git add plugins/pirategoat-tools/scripts/review_output_simple.py plugins/pirategoat-tools/tests/test_bootstrap_reviewer.py
git commit -m "$(cat <<'EOF'
fix(pirategoat-tools): align save() filenames with bootstrap expectations

ReviewOutputBuilder.save() wrote {reviewer}.json but bootstrap told
agents to expect {reviewer}-review.json. This mismatch caused agents
to cp files or fail to find their own output. Aligning save() to use
the -review suffix matches the convention documented in bootstrap
Section 3 and the shared reviewer protocol.

Refs dead-code-reviewer-efficiency-analysis
EOF
)"
```

---

### Task 6: Version Bump and Changelog

**Files:**
- Modify: `plugins/pirategoat-tools/CHANGELOG.md:8`
- Modify: `.claude-plugin/marketplace.json:16`

**Step 1: Check if the current version (1.39.0) has been pushed**

```bash
git log --oneline origin/main..HEAD | head -5
```

If 1.39.0 is unpushed, fold these changes into a patch bump (1.39.1) since they're bug fixes. If 1.39.0 is already pushed, bump to 1.39.1.

**Step 2: Add changelog entry**

Insert a new version section at the top (after line 7, before `## [1.39.0]`):

```markdown
## [1.39.1] - 2026-02-28

### Fixed

- **ReviewOutputBuilder API hallucination** — Bootstrap Section 3 now includes a complete usage example with all core methods (add_issue, add_positive, set_files_reviewed, set_confidence, save). Previously only showed the constructor, causing all 14 agents to hallucinate wrong method names on first write attempt (~3.2 wasted calls/agent × ~10 agents = ~30 saved calls per full review session).
- **Bootstrap output size cascade** — Scope output exceeding 15KB is now written to scoped-diff.patch and truncated inline with read instructions. Prevents the persistence cascade that wasted 2-3 calls per large PR session.
- **Post-write verification reads** — Bootstrap now instructs agents to trust save()'s return value, eliminating 1.5 unnecessary Read calls per agent per session.
- **Dead-code Step 0 unconditional PHP check** — Bootstrap injects DYNAMIC_DISPATCH_RISK computed from file extensions; dead-code-reviewer skips the PHP hook grep when no PHP files are in scope (~1 wasted call in 50% of sessions).
- **Output filename mismatch** — ReviewOutputBuilder.save() now writes `{reviewer}-review.json/.md` matching the convention documented in bootstrap and shared protocol. Previously wrote `{reviewer}.json/.md`, causing filename mismatches.
```

**Step 3: Bump version in marketplace.json**

Change:
```json
      "version": "1.39.0",
```
To:
```json
      "version": "1.39.1",
```

**Step 4: Commit**

```bash
git add plugins/pirategoat-tools/CHANGELOG.md .claude-plugin/marketplace.json
git commit -m "$(cat <<'EOF'
fix(pirategoat-tools): bump to v1.39.1 for pipeline efficiency fixes

Five bug fixes targeting ~6.0 wasted tool calls per agent dispatch:
API usage example in bootstrap, output size cap, post-write verification
removal, conditional Step 0, and output filename alignment.

Pipeline-wide impact: ~30 saved calls per full review session from the
API fix alone (all 14 agents benefit).

Refs dead-code-reviewer-efficiency-analysis
EOF
)"
```

---

## Summary

| Task | What | Files | Type | Impact |
|---|---|---|---|---|
| 1 | ReviewOutputBuilder API usage example + no-verify instruction | `bootstrap-reviewer.py`, tests | fix | ~3.2 calls/session × 14 agents |
| 2 | Verify protocol skip list consistency | `reviewer-protocol.md` (read-only) | verify | — |
| 3 | Bootstrap output size cap for large PRs | `bootstrap-reviewer.py`, tests | fix | ~2.7 calls/session (50% of sessions) |
| 4 | Conditional Step 0 PHP check | `bootstrap-reviewer.py`, `dead-code-reviewer.md`, tests | fix | ~1.0 call/session (50% of sessions) |
| 5 | Output filename mismatch | `review_output_simple.py`, tests | fix | Eliminates cp workaround |
| 6 | Version bump + changelog | `CHANGELOG.md`, `marketplace.json` | chore | — |

**Expected efficiency improvement:** From 62% → ~80% per dead-code-reviewer session. Pipeline-wide: ~30 saved calls per full review (from API fix across all 14 agents).

**Not implemented (future work):**
- Search strategy tips for dead-code-reviewer (fix #5 from analysis) — lower ROI, agent-specific
- Agent name alias (fix #6 from analysis) — 2/6 sessions, trivial impact
- History-insights Tier 2/3 optimizations — separate plan, different scope
