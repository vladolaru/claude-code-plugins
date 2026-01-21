# AST Semantic Filtering Design

**Date:** January 21, 2026
**Status:** Design Complete
**Goal:** Enhance semantic filter from 40% to 70%+ noise reduction using AST parsing

---

## Problem Statement

The regex-based MVP (`scripts/semantic-filter-mvp.py`) achieves 40% noise reduction by filtering blank lines, docblocks, and comments. AST parsing can reach 70%+ by understanding code structure and detecting semantic equivalence.

**Current limitation:** Regex cannot distinguish:
- Import reordering (noise) vs new imports (signal)
- Type hint additions (noise) vs signature changes (signal)
- Variable renames (cosmetic) vs refactoring (signal)

**Solution:** Parse code to AST, compare normalized structures, apply semantic equivalence rules.

---

## Architecture

### Component Structure

```
lib/semantic_filter/
├── __init__.py           # API: filter_diff(diff_text)
├── parser.py             # Language detection, parser selection
├── ast_differ.py         # AST comparison
├── normalizer.py         # Strip comments, whitespace from AST
├── rules.py              # Semantic equivalence rules
└── diff_builder.py       # Reconstruct filtered diff

lib/semantic_filter/languages/
├── __init__.py
├── php.py                # PHP-specific rules
└── javascript.py         # JavaScript-specific rules

scripts/
└── semantic-filter.py    # CLI wrapper (drops in for MVP)
```

### Processing Pipeline

```
Input: git diff
    ↓
Parse diff headers → extract file paths
    ↓
Detect language per file (extension-based)
    ↓
┌─────────────┬──────────────┬────────────┐
│   PHP       │    JS/TS     │   Other    │
│  AST Mode   │   AST Mode   │ Regex Mode │
└─────────────┴──────────────┴────────────┘
    ↓              ↓              ↓
tree-sitter    tree-sitter    MVP regex
    ↓              ↓              ↓
Normalize AST  Normalize AST      ↓
    ↓              ↓              ↓
Compare        Compare            ↓
    ↓              ↓              ↓
Apply rules    Apply rules        ↓
    ↓              ↓              ↓
└──────────────┬────────────┬────┘
               ↓
    Reconstruct diff (keep signal only)
               ↓
         Output: filtered diff
```

---

## AST Normalization

Strip non-semantic elements from both old and new ASTs before comparison:

**Remove:**
- Comment nodes (inline, block, docblock)
- Whitespace nodes
- Formatting metadata (brace position, indentation)
- Optional syntax (trailing commas)

**Canonicalize:**
- Import statements (sort alphabetically)
- String literals (normalize quote style)
- Numeric literals (normalize format)

**Result:** Two ASTs that differ only in semantic changes.

---

## Semantic Equivalence Rules

### Conservative Filtering Principle

**Rule:** Filter only when certain the change is noise.

```python
def should_filter(old_node, new_node):
    """Return True only if CERTAIN the change is noise."""
    if not ast_structurally_identical(normalize(old_node), normalize(new_node)):
        return False  # Keep (structural change detected)

    if not matches_known_noise_pattern(old_node, new_node):
        return False  # Keep (unknown pattern)

    if has_potential_side_effects(old_node, new_node):
        return False  # Keep (uncertain)

    return True  # Safe to filter
```

**Philosophy:** False positives (stripping real changes) are worse than false negatives (keeping some noise).

### PHP Rules

**Phase 1 (safe):**
1. Docblock changes (`/** @var Type */`)
2. Import reordering (same imports, different order)
3. Type hint additions (`private $x` → `private Type $x`, signature unchanged)

**Phase 2 (validate first):**
4. Property visibility keywords (`var` → `public`)
5. Nullable type syntax (`?Type` vs `Type|null`)

**Skip (too risky):**
- Variable renames (may indicate refactor)
- Whitespace in strings (could be semantic)

### JavaScript Rules

**Phase 1 (safe):**
1. Semicolon insertion differences
2. Quote style (`'x'` vs `"x"` vs `` `x` ``)
3. Import reordering
4. Arrow function formatting (`() => x` vs `() => { return x }`)

**Phase 2 (validate first):**
5. Declaration keywords (`var` → `let`/`const`) - scoping changes ARE semantic
6. Optional chaining formatting

**Skip (too risky):**
- Template literals (may contain expressions)
- Computed property names

### Universal Rules (All Languages)

```python
universal_filters = {
    'blank_lines': True,        # Always noise
    'comments': True,           # Always noise (except LICENSE headers)
    'whitespace': True,         # Always noise
    'import_order': False,      # Check semantic equivalence
    'renamed_identifiers': False # Check if cosmetic only
}
```

---

## Implementation Phases

### Phase 1: Core Infrastructure (2-3 hours)

**Build:**
- Language detection from diff headers
- tree-sitter parser integration (PHP, JS)
- Regex fallback for unsupported languages
- AST normalization (strip comments, whitespace)
- Diff reconstruction

**Test:**
- Multi-file diffs
- Mixed languages in one diff
- Graceful fallback for unknown languages
- Validate against MVP test case (should match 40%)

**Dependencies:**
```bash
brew install tree-sitter
pip3 install tree-sitter tree-sitter-php tree-sitter-javascript
```

### Phase 2: PHP AST Filtering (2-3 hours)

**Implement PHP rules:**
1. Docblock filtering
2. Import reordering detection
3. Type hint addition detection

**Test:**
- Run on `test-samples/semantic-filter-test/` (existing MVP test)
- Target: >70% reduction (vs 40% MVP)
- Manual review: verify no real changes stripped

**Success criteria:**
- Exceeds MVP reduction ratio
- Zero false positives in test suite

### Phase 3: JavaScript AST Filtering (2 hours)

**Implement JS rules:**
1. Semicolon differences
2. Quote style normalization
3. Import reordering
4. Arrow function formatting

**Test:**
- Create JS test samples (before/after pairs)
- Validate each rule independently
- Measure reduction ratio

### Phase 4: Testing & Refinement (1-2 hours)

**Comprehensive validation:**
- 10+ test cases per language
- Run on real git history (your repos)
- Measure false positive rate (target: <1%)
- Performance benchmark (target: <2s per diff)

**Documentation:**
- Document all rules and rationale
- Update README with examples
- Create troubleshooting guide

**Total estimated time:** 7-10 hours

---

## CLI Interface

### Usage (Drop-in Replacement for MVP)

```bash
# Basic usage (same as MVP)
git diff | ./scripts/semantic-filter.py

# Language override
./scripts/semantic-filter.py --language=php < input.diff

# Verbose mode (show filtered rules)
./scripts/semantic-filter.py --verbose < input.diff

# Stats only
./scripts/semantic-filter.py --stats-only < input.diff

# Force regex fallback (for comparison)
./scripts/semantic-filter.py --no-ast < input.diff
```

### Output Format

**Stdout:** Filtered diff
```diff
diff --git a/Payment.php b/Payment.php
--- a/Payment.php
+++ b/Payment.php
@@ -10,5 +10,7 @@
-use Mailer;
+use EmailService;
+use PaymentGateway;
```

**Stderr:** Statistics
```
Semantic Filter - Results:
  Mode: AST (PHP via tree-sitter)
  Total lines: 78
  Noise filtered: 55 (70.5%)
    - Comments/docblocks: 15
    - Blank lines: 12
    - Import reordering: 8
    - Type hints: 10
    - Formatting: 10
  Signal kept: 23 (29.5%)
  Fallback: 0 files
```

---

## Integration Roadmap

### Phase 1: Manual Usage (Immediate)

Developers use the filter manually:
```bash
git diff main feature | semantic-filter.py > clean.diff
```

### Phase 2: Agent Integration (After Validation)

Update `pr-reviewing` skill:
```python
# Before spawning agents
raw_diff = get_pr_diff()
filtered_diff = semantic_filter.filter_diff(raw_diff)
# Pass filtered_diff to all review agents
```

**Benefit:** All 5 review agents automatically use AST filtering.

### Phase 3: Git Alias (Optional)

```bash
git config alias.sdiff '!git diff "$@" | semantic-filter.py'
git sdiff main feature
```

---

## Success Metrics

### Target: 70%+ Noise Reduction

**Baseline:** MVP achieves 40% (78 lines → 47 lines in test case)

**Target:** AST achieves 70%+ (78 lines → 23 lines projected)

**Measurement method:**
1. Run on 10 real PRs from git history
2. Compare: Original vs MVP vs AST
3. Manual review for false positives
4. Calculate average reduction ratio

### Acceptance Criteria

- ✅ Noise reduction ≥ 70% on average
- ✅ False positive rate < 1%
- ✅ Fallback works for unsupported languages
- ✅ Performance < 2 seconds for typical diffs
- ✅ No dependencies beyond tree-sitter

---

## Risk Mitigation

### Risk: False Positives (Stripping Real Changes)

**Mitigation:**
- Conservative rule set (err on keeping changes)
- Comprehensive test suite (before/after pairs)
- Manual validation on real PRs
- Incremental rollout (add rules only after validation)

### Risk: Performance Degradation

**Mitigation:**
- Cache parsed ASTs (if parsing same file multiple times)
- Parallel processing for multi-file diffs
- Fallback to regex if AST parsing takes >1s

### Risk: Language Support Gaps

**Mitigation:**
- Regex fallback for unsupported languages (maintains MVP baseline)
- Document supported languages clearly
- Graceful degradation (never fail, always produce output)

### Risk: Dependency Installation

**Mitigation:**
- Use Homebrew for system library (standard macOS workflow)
- Clear installation instructions
- Fallback to regex if tree-sitter not installed

---

## Future Enhancements

### Additional Languages

tree-sitter supports 40+ languages:
- Python (common in scripting)
- Go (backend services)
- Ruby (Rails apps)
- Rust (systems programming)
- TypeScript (frontend)

**Effort:** 1-2 hours per language (rules + tests)

### Advanced Rules

**Semantic equivalence detection:**
- Refactored code (extract method, inline variable)
- Equivalent expressions (`x * 2` vs `x << 1`)
- Control flow equivalence (early return vs if/else)

**Effort:** 4-6 hours (research + implementation)

### IDE Integration

**Goal:** Real-time semantic diff in editors

**Approach:**
- Language Server Protocol (LSP) extension
- Show semantic changes inline
- Dim non-semantic changes

**Effort:** 8-12 hours

---

## Appendix: Tree-sitter Overview

### Why Tree-sitter?

**Advantages:**
- Universal parser (40+ languages)
- Incremental parsing (fast on large files)
- Error-tolerant (handles incomplete code)
- Battle-tested (GitHub, Neovim, Atom)
- Python bindings available

**Alternatives considered:**
- nikic/php-parser: PHP-only, requires PHP runtime
- @babel/parser: JavaScript-only, requires Node.js
- Custom regex: Limited accuracy (MVP uses this)

### Installation

```bash
# System library
brew install tree-sitter

# Python bindings
pip3 install tree-sitter tree-sitter-php tree-sitter-javascript
```

### Basic Usage

```python
import tree_sitter_php as tsphp
from tree_sitter import Language, Parser

# Create parser
php_parser = Parser(Language(tsphp.language()))

# Parse code
tree = php_parser.parse(bytes(code, "utf8"))

# Walk AST
cursor = tree.walk()
# ... traverse nodes
```

---

## Summary

**Goal:** Enhance semantic filtering from 40% to 70%+ noise reduction.

**Approach:** AST parsing with tree-sitter, conservative semantic rules, regex fallback.

**Languages:** PHP, JavaScript (with path to add more).

**Timeline:** 7-10 hours implementation + validation.

**Deliverables:**
- `lib/semantic_filter/` (reusable library)
- `scripts/semantic-filter.py` (CLI tool)
- Test suite with validation
- Documentation

**Integration:** Drop-in replacement for MVP, enables future agent integration.

**Risk:** Low (conservative rules, comprehensive testing, graceful fallback).

---

**Status:** Ready for implementation.
