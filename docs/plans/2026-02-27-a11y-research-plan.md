# Web Accessibility Research Plan — Gutenberg-Grounded, Agent-Targeted

> **For Claude:** This is a multi-session research plan. Each session produces intermediary documents that feed into subsequent sessions. Use `superpowers:executing-plans` adapted for research (not code — skip TDD steps, focus on document outputs).

**Goal:** Build a comprehensive accessibility knowledge base grounded in Gutenberg's real-world patterns, then distill it into two Claude Code skills: one for writing accessible frontend code, one for reviewing code for a11y issues.

**Scope:** General React/web accessibility principles, using the Gutenberg project as primary source of real-world examples, patterns, and anti-patterns.

**Audience:** AI agents (not humans) — output must be rule-based, actionable, structured for machine consumption.

**Output directory:** `docs/research/a11y/`

---

## Overview: Session Map

```
Session 1: Standards & Gutenberg Infrastructure
  ├── 01-standards-foundations.md      (WCAG, ARIA, WP standards)
  └── 02-gutenberg-a11y-infrastructure.md  (hooks, utils, packages)

Session 2: Pattern Mining (parallel research)
  ├── 03-component-a11y-patterns.md    (30+ component deep dives)
  └── 04-anti-patterns-from-bugs.md    (672+ commits analyzed)

Session 3: Testing & External Research
  ├── 05-a11y-testing-patterns.md      (unit, E2E, automated)
  └── 06-external-standards-research.md (WAI-ARIA APG, WP handbook, Ariakit)

Session 4: Synthesis
  └── 07-synthesized-rules.md          (merged, deduplicated, prioritized)

Session 5: Skill Creation
  ├── skill: accessible-frontend-dev   (code generation guidance)
  └── skill: a11y-code-reviewer        (review checklist + patterns)

Session 6: Validation & Refinement
  └── Test skills against real Gutenberg PRs and components
```

---

## Session 1: Standards & Gutenberg Infrastructure

**Goal:** Establish the foundational knowledge layer — what standards apply and what tools Gutenberg provides.

**Estimated effort:** 1 session with parallel subagents.

### Task 1.1: Standards Foundations (`01-standards-foundations.md`)

**Research sources:**
- WCAG 2.2 Quick Reference (https://www.w3.org/WAI/WCAG22/quickref/)
- WAI-ARIA 1.2 specification (key roles, states, properties)
- WordPress Accessibility Coding Standards (https://developer.wordpress.org/coding-standards/wordpress-coding-standards/accessibility/)
- WordPress Accessibility Handbook (https://make.wordpress.org/accessibility/handbook/)
- make.wordpress.org/accessibility recent posts for current direction

**Document structure:**
```
1. WCAG 2.2 Principles → Concrete Rules
   - For each WCAG criterion at AA level:
     - Rule ID (e.g., WCAG-1.1.1)
     - One-sentence rule
     - Frontend implementation pattern
     - Common violation
   - Prioritized by frequency of violation in web apps

2. ARIA Roles, States & Properties — Decision Matrix
   - When to use ARIA vs semantic HTML
   - The 5 rules of ARIA usage
   - Role categories with usage guidance
   - Common ARIA anti-patterns

3. WordPress-Specific Standards
   - WP accessibility coding standards (distilled)
   - WP-specific requirements beyond WCAG
   - Gutenberg contribution requirements
```

**Output:** `docs/research/a11y/01-standards-foundations.md`

### Task 1.2: Gutenberg A11y Infrastructure (`02-gutenberg-a11y-infrastructure.md`)

**Research sources (all in local repo at `/Users/vladolaru/Work/a8c/gutenberg`):**
- `packages/a11y/` — full source code, tests, README
- `packages/compose/src/hooks/` — all focus/a11y hooks
- `packages/dom/src/` — focusable.js, tabbable.js
- `packages/components/src/higher-order/` — a11y HOCs
- `packages/components/src/visually-hidden/` — VisuallyHidden component
- `packages/jest-puppeteer-axe/` — axe testing integration
- `packages/eslint-plugin/configs/jsx-a11y.js` — lint rules

**Document structure:**
```
1. Package Map
   - @wordpress/a11y: API surface, when to use speak() vs DOM
   - @wordpress/dom: focusable/tabbable utilities, API reference
   - @wordpress/compose: each a11y hook with signature, usage, gotchas
   - @wordpress/components HOCs: each HOC with when/why/how

2. Hook Reference Cards (for each hook)
   - Name, package, import path
   - Signature with types
   - When to use (decision criteria)
   - When NOT to use
   - Example from Gutenberg codebase (real, not invented)
   - Common mistakes

3. Utility Reference Cards (for each utility)
   - Same structure as hooks

4. Composition Patterns
   - How hooks/HOCs compose together in real components
   - Modal pattern (focus trap + focus return + constrained tabbing)
   - Dropdown pattern (focus outside + popover positioning)
   - Navigable container pattern (arrow keys + roving tabindex)
```

**Output:** `docs/research/a11y/02-gutenberg-a11y-infrastructure.md`

---

## Session 2: Pattern Mining

**Goal:** Extract concrete patterns from components and anti-patterns from bug history.

**Estimated effort:** 1 session with parallel subagents (components and bugs are independent research tracks).

### Task 2.1: Component A11y Patterns (`03-component-a11y-patterns.md`)

**Research approach:**
- Deep-dive into 30+ components in `packages/components/src/`
- For each, extract: ARIA usage, keyboard handling, focus management, screen reader support
- Group findings into reusable pattern categories

**Components to analyze (prioritized by interaction complexity):**

*Tier 1 — Complex widgets (full deep dive):*
1. Modal — dialog pattern, focus trap, background hiding
2. ComboboxControl — combobox pattern, live regions, filtering
3. TabPanel / Tabs — tab pattern, arrow navigation, panel association
4. NavigableContainer — roving tabindex, arrow key navigation
5. Dropdown / DropdownMenu — disclosure pattern, focus management
6. FormTokenField — token input, multi-select, announcements
7. CustomSelectControl — custom select, virtual focus
8. DateTimePicker — complex widget, multiple sub-components
9. ColorPicker / ColorPalette — specialized input, ARIA
10. TreeSelect — tree pattern, expandable

*Tier 2 — Medium complexity (focused review):*
11. Button — disabled states, icon-only, toggle
12. CheckboxControl / RadioControl / ToggleControl — form controls
13. TextControl / TextareaControl — input patterns
14. RangeControl — slider pattern
15. Notice / Snackbar — live region announcements
16. Tooltip — tooltip pattern, hover/focus triggers
17. Popover — positioning, focus containment
18. SearchControl — search input pattern
19. ToggleGroupControl — segmented control
20. Guide — multi-step wizard

*Tier 3 — Simple but instructive:*
21. VisuallyHidden — screen-reader-only content
22. Icon — decorative vs meaningful images
23. Spinner — loading state announcement
24. Disabled — disabled state patterns
25. FocalPointPicker — drag interaction a11y
26. Draggable — drag-and-drop a11y

**Document structure:**
```
For each component:
  - Component name and complexity tier
  - ARIA pattern implemented (with W3C APG reference if applicable)
  - Key a11y code excerpts (actual code, not pseudocode)
  - Keyboard interaction model
  - Screen reader behavior
  - Reusable pattern extracted

Summary sections:
  - Pattern catalog (grouped by concern)
  - Keyboard interaction models (arrow key, tab, escape, enter/space)
  - ARIA relationship patterns (labelledby, describedby, controls, owns)
  - Focus management strategies (trap, return, roving, virtual)
  - Live region usage patterns
```

**Output:** `docs/research/a11y/03-component-a11y-patterns.md`

### Task 2.2: Anti-Patterns from Bug History (`04-anti-patterns-from-bugs.md`)

**Research approach:**
- Analyze all 672+ a11y-related commits
- For bug fixes: extract what was wrong, why, and what the fix was
- Categorize into anti-pattern types
- Quantify frequency of each anti-pattern

**Git queries to run:**
```bash
# All a11y bug fixes with full messages
git log --all --grep="a11y" --grep="fix" --all-match --format="%H %s" | head -100
git log --all --grep="accessibility" --grep="fix" --all-match --format="%H %s"

# Screen reader fixes
git log --all --grep="screen reader" --format="%H %s"

# Focus-related fixes
git log --all --grep="focus" --grep="fix" --all-match --format="%H %s" | head -80

# Keyboard fixes
git log --all --grep="keyboard" --grep="fix" --all-match --format="%H %s"

# ARIA fixes
git log --all --grep="aria" --grep="fix" --all-match --format="%H %s"
```

**For the top 30-40 most instructive commits:** read the full diff to understand the before/after.

**Document structure:**
```
1. Anti-Pattern Catalog (each entry):
   - Anti-pattern name (e.g., "Focus Lost on State Update")
   - Frequency (how many commits fix this)
   - Description of what goes wrong
   - Real example: before code → after code (from actual commits)
   - Root cause analysis
   - Prevention rule for agents

2. Frequency Analysis
   - Ranked list of anti-patterns by occurrence
   - Heat map by component area
   - Correlation with component complexity

3. Prevention Rules (distilled)
   - Numbered, actionable rules
   - Each tied to specific anti-patterns
   - Priority-ordered by frequency
```

**Output:** `docs/research/a11y/04-anti-patterns-from-bugs.md`

---

## Session 3: Testing & External Research

**Goal:** Understand how to verify accessibility and fill gaps from external standards resources.

**Estimated effort:** 1 session with parallel subagents.

### Task 3.1: A11y Testing Patterns (`05-a11y-testing-patterns.md`)

**Research sources:**
- `test/e2e/specs/editor/various/a11y.spec.js` — E2E a11y tests
- `test/e2e/specs/editor/various/a11y-region-navigation.spec.js` — region nav tests
- `packages/jest-puppeteer-axe/` — axe integration
- Component test files (from Task 2.1 components) — unit-level a11y testing
- `packages/eslint-plugin/configs/jsx-a11y.js` — static analysis

**Document structure:**
```
1. Testing Pyramid for A11y
   - Static analysis (ESLint jsx-a11y rules)
   - Unit tests (role queries, ARIA assertions)
   - Integration tests (keyboard interaction, focus flow)
   - E2E tests (axe-core scans, full flow testing)
   - Manual testing guide (screen reader checklists)

2. Test Pattern Catalog
   For each pattern:
   - Pattern name
   - Testing level (unit/integration/E2E)
   - Real example from Gutenberg tests
   - What it catches
   - What it misses

3. Assertion Reference
   - Role-based queries (getByRole, queryByRole with name/description)
   - ARIA attribute assertions
   - Focus assertions
   - Keyboard simulation patterns
   - Live region verification
   - axe-core rule configuration

4. Test Writing Rules for Agents
   - What to test for every component
   - Minimum a11y test coverage checklist
   - How to write a11y tests that actually catch bugs
```

**Output:** `docs/research/a11y/05-a11y-testing-patterns.md`

### Task 3.2: External Standards Deep Dive (`06-external-standards-research.md`)

**Research sources (web):**
- WAI-ARIA Authoring Practices Guide (APG) — https://www.w3.org/WAI/ARIA/apg/
  - All pattern pages (dialog, tabs, combobox, menu, tree, etc.)
  - Keyboard interaction models for each pattern
- Ariakit documentation — https://ariakit.org/ (Gutenberg's component library dependency)
- React accessibility docs — https://react.dev/reference/react-dom/components#form-components
- Inclusive Components by Heydon Pickering (patterns reference)
- Adrian Roselli's blog (common a11y mistakes)
- Scott O'Hara's blog (ARIA patterns)
- WordPress Accessibility Handbook best practices

**Document structure:**
```
1. WAI-ARIA APG Pattern Reference
   For each pattern used by Gutenberg:
   - Pattern name and APG URL
   - Required roles, states, properties
   - Required keyboard interactions
   - Gutenberg component that implements it
   - Gaps between APG spec and Gutenberg implementation

2. Ariakit Integration Patterns
   - Which Ariakit components Gutenberg uses
   - How Ariakit handles a11y internally
   - When to use Ariakit vs custom implementation
   - Ariakit configuration for accessibility

3. React-Specific A11y Guidance
   - Fragment and key management for screen readers
   - Portal accessibility (focus, announcements)
   - Ref-based focus management patterns
   - State management and live region timing
   - Server component considerations

4. Gap Analysis
   - WCAG criteria NOT covered by Gutenberg patterns
   - Patterns in APG NOT implemented in Gutenberg
   - Areas where Gutenberg exceeds standards
```

**Output:** `docs/research/a11y/06-external-standards-research.md`

---

## Session 4: Synthesis

**Goal:** Merge all research documents into a single, deduplicated, prioritized rule set.

**Estimated effort:** 1 session, primarily synthesis and writing.

### Task 4.1: Synthesized Rules (`07-synthesized-rules.md`)

**Input documents:**
- `01-standards-foundations.md`
- `02-gutenberg-a11y-infrastructure.md`
- `03-component-a11y-patterns.md`
- `04-anti-patterns-from-bugs.md`
- `05-a11y-testing-patterns.md`
- `06-external-standards-research.md`

**Document structure:**
```
1. Universal Rules (apply to ALL frontend code)
   - Semantic HTML rules
   - Keyboard interaction rules
   - Focus management rules
   - Color and visual rules
   - Content and language rules

2. Component-Type Rules (keyed by widget type)
   For each widget type (dialog, tabs, combobox, menu, tree, etc.):
   - Required ARIA pattern
   - Required keyboard interactions
   - Required focus behavior
   - Common mistakes to avoid
   - Testing requirements

3. React-Specific Rules
   - Hook usage patterns
   - Ref management for focus
   - Portal considerations
   - State update and announcement timing
   - Component composition patterns

4. Review Checklist (prioritized)
   - P0: Must fix (WCAG A violations, keyboard traps, missing labels)
   - P1: Should fix (WCAG AA violations, focus management gaps)
   - P2: Nice to fix (enhanced screen reader UX, WCAG AAA)

5. Anti-Pattern Quick Reference
   - Numbered list, each with:
     - Name
     - Detection heuristic (how an agent spots it in code)
     - Fix pattern
     - Severity

6. Decision Trees
   - "Should I use ARIA or semantic HTML?" → flowchart
   - "What focus management strategy?" → flowchart
   - "How to announce dynamic content?" → flowchart
   - "What keyboard model for this widget?" → flowchart
```

**Output:** `docs/research/a11y/07-synthesized-rules.md`

---

## Session 5: Skill Creation

**Goal:** Create the two Claude Code skills from the synthesized rules.

**Estimated effort:** 1 session.

### Task 5.1: `accessible-frontend-dev` Skill

**Purpose:** Guide AI agents when writing frontend code to produce accessible output by default.

**Skill structure:**
```
- SKILL.md frontmatter (name, description, triggers)
- When to activate (any frontend component creation/modification)
- Semantic HTML defaults
- ARIA usage rules
- Keyboard interaction requirements by widget type
- Focus management patterns
- Live region usage
- Quick reference for common components
- Links to detailed research docs for edge cases
```

**Location:** `plugins/pirategoat-tools/skills/accessible-frontend-dev/SKILL.md`

### Task 5.2: `a11y-code-reviewer` Agent/Skill

**Purpose:** Review frontend code for accessibility issues, prioritized by severity.

**Skill/agent structure:**
```
- Review checklist (P0/P1/P2 prioritized)
- Detection heuristics for each anti-pattern
- ARIA validation rules
- Keyboard interaction verification
- Focus management verification
- Testing coverage check
- Output format (findings with severity, location, fix suggestion)
```

**Location:** Either:
- `plugins/pirategoat-tools/skills/a11y-code-reviewer/SKILL.md` (if skill)
- `plugins/pirategoat-tools/agents/a11y-reviewer.md` (if agent)

### Task 5.3: Integration Testing

- Run both skills against 5-10 real Gutenberg components
- Verify the generation skill produces accessible patterns
- Verify the reviewer skill catches known anti-patterns
- Adjust rules based on false positives/negatives

---

## Session 6: Validation & Refinement

**Goal:** Battle-test skills against real-world code.

**Estimated effort:** 1 session.

### Task 6.1: Test Against Gutenberg PRs

- Find 5 recent Gutenberg PRs with a11y impact
- Run the reviewer skill against them
- Compare findings to actual PR review comments
- Measure precision (are findings real?) and recall (do we miss real issues?)

### Task 6.2: Test Generation Quality

- Use the generation skill to build 3 accessible components from scratch
- Have the reviewer skill review them
- Manual spot-check against WAI-ARIA APG requirements

### Task 6.3: Refinement

- Adjust rules based on test results
- Update research docs if gaps found
- Final skill versions

---

## File Structure

```
docs/research/a11y/
├── 01-standards-foundations.md
├── 02-gutenberg-a11y-infrastructure.md
├── 03-component-a11y-patterns.md
├── 04-anti-patterns-from-bugs.md
├── 05-a11y-testing-patterns.md
├── 06-external-standards-research.md
└── 07-synthesized-rules.md

plugins/pirategoat-tools/
├── skills/
│   └── accessible-frontend-dev/
│       └── SKILL.md
└── agents/
    └── a11y-reviewer.md
```

---

## Session Dependencies

```
Session 1 ──→ Session 2 ──→ Session 3 ──→ Session 4 ──→ Session 5 ──→ Session 6
  01,02          03,04          05,06          07          Skills       Validation
  (parallel)     (parallel)     (parallel)     (serial)    (serial)     (serial)
```

Sessions 1-3 each have two parallel research tracks.
Sessions 4-6 are serial — each depends on the previous.

---

## Research Principles

1. **Ground in real code** — every rule must cite a real Gutenberg example (file path + line range or commit hash)
2. **Prioritize by bug frequency** — rules addressing the most common Gutenberg a11y bugs come first
3. **Agent-consumable format** — rules are numbered, unambiguous, with clear detection heuristics
4. **Layered specificity** — universal rules → React rules → widget-specific rules
5. **Anti-patterns over principles** — "don't do X" is more actionable for agents than "strive for Y"
6. **Include the why** — agents make better decisions when they understand the impact on users with disabilities
