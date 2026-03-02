# Synthesized Accessibility Rules

> Unified rule set for AI agent consumption. Merged from 6 research documents.
> Generated: 2026-02-27 | Session 4

## How to Use This Document

- **When generating code:** Follow Section 1 (Universal Rules) and Section 3 (React-Specific Rules). For specific widget types, consult Section 2 (Component-Type Rules).
- **When reviewing code:** Use Section 4 (Review Checklist) as your prioritized sweep list.
- **When debugging a11y issues:** Check Section 5 (Anti-Pattern Quick Reference) for known failure modes.
- **When making architectural decisions:** Use Section 6 (Decision Trees) to select the correct pattern before writing code.

---

## 1. Universal Rules (Apply to ALL Frontend Code)

### 1.1 Semantic HTML Rules

#### U-SEM-01: Use Native HTML Elements Before ARIA
- **Rule:** Use native HTML elements that provide the required semantics and behavior before adding ARIA roles. A `<button>` is always better than `<div role="button" tabindex="0">`.
- **Why:** Native elements provide keyboard behavior, focus management, and screen reader semantics for free. ARIA only changes semantics -- it does not add behavior.
- **Implementation:**
  ```tsx
  // CORRECT
  <button onClick={handleClick}>Save</button>

  // WRONG
  <div role="button" tabIndex={0} onClick={handleClick} onKeyDown={handleKeyDown}>Save</div>
  ```
- **Common violation:** Using `<div>` or `<span>` with `role="button"`, `onClick`, `tabIndex="0"`, and manual `onKeyDown` handlers for Enter/Space.
- **Detection heuristic:** Flag any element with `role="button"` that is not a `<button>`. Flag any `<div>` or `<span>` with both `onClick` and `tabIndex`. Flag manual `onKeyDown` handlers checking for Enter/Space on non-button elements.
- **Sources:** 01 (ARIA Rule 1, When to Use table), 04 (AP-07, 4 bugs), 06 (Section 5.5 #10)
- **Priority:** P0

#### U-SEM-02: Every Interactive Element Must Have an Accessible Name
- **Rule:** Every interactive element (`<button>`, `<a>`, `<input>`, `<select>`, `<textarea>`, elements with interactive ARIA roles) must have an accessible name derived from visible text, a `<label>`, `aria-label`, or `aria-labelledby`.
- **Why:** An unnamed button or link is useless to assistive technology users. They hear "button" with no indication of purpose.
- **Implementation:**
  ```tsx
  // Icon-only button: use aria-label or label prop
  <Button icon={closeIcon} label="Close" />

  // Form input: use associated label
  <label htmlFor="email">Email</label>
  <input id="email" type="email" />

  // SVG: use title + role="img" + aria-labelledby
  <svg role="img" aria-labelledby="chart-title">
    <title id="chart-title">Sales chart for Q1</title>
  </svg>
  ```
- **Common violation:** Icon-only `<button>` with no `aria-label`; `<input>` with `placeholder` as the only label; `<svg>` without `<title>` or `aria-label`.
- **Detection heuristic:** Check every `<button>`, `<a>`, `<input>`, `<select>`, `<textarea>`, and elements with interactive roles for an accessible name. Flag `<img>` without `alt`, icon buttons without `aria-label` or visually hidden text.
- **Sources:** 01 (WCAG 1.1.1, 4.1.2, ARIA Rule 5), 03 (cross-cutting observation 7), 05 (Rule 1), 06 (Section 2.2)
- **Priority:** P0

#### U-SEM-03: Do Not Change Native Semantics Unnecessarily
- **Rule:** Do not add ARIA roles that conflict with an element's native semantics. Do not add `role="presentation"` or `aria-hidden="true"` to elements containing visible text or interactive children.
- **Why:** Overriding native semantics with incorrect ARIA actively communicates wrong information, which is worse than no ARIA at all.
- **Implementation:**
  ```tsx
  // WRONG: adding role="heading" to a button
  <button role="heading">Title</button>

  // WRONG: aria-hidden on visible text
  <span aria-hidden="true">Important label text</span>

  // WRONG: role="presentation" on text-containing element
  <span role="presentation">{variation.title}</span>
  ```
- **Common violation:** `role="presentation"` on elements with visible text (Gutenberg AP-13); `role="document"` on interactive elements causing screen reader mode switches; redundant roles on native elements (e.g., `role="button"` on `<button>`).
- **Detection heuristic:** Flag `role="presentation"` or `role="none"` on elements with text content or interactive children. Flag `role="button"` on `<button>` (redundant). Flag `aria-hidden="true"` on focusable elements.
- **Sources:** 01 (ARIA Rules 2, 4), 04 (AP-03, AP-13, 10 bugs combined), 06 (Section 5.5)
- **Priority:** P1

#### U-SEM-04: Validate ARIA Attributes Against Roles
- **Rule:** Before applying any ARIA state attribute, verify it is valid for the element's role per the WAI-ARIA specification. Never insert non-role-bearing wrapper elements inside ARIA container widgets.
- **Why:** Invalid ARIA causes screen readers to announce incorrect information or silently ignore attributes.
- **Implementation:**
  ```tsx
  // CORRECT: aria-checked only on supported roles
  <div role="menuitemcheckbox" aria-checked={isSelected}>Option</div>

  // WRONG: aria-checked on role="menuitem"
  <div role="menuitem" aria-checked={isSelected}>Option</div>

  // WRONG: wrapper div inside menu breaking parent-child structure
  <div role="menu">
    <div className="wrapper"> {/* This breaks ARIA structure */}
      <div role="menuitem">Item</div>
    </div>
  </div>
  ```
- **Common violation:** `aria-checked` on `role="menuitem"` (only valid on `menuitemcheckbox`/`menuitemradio`/`checkbox`/`radio`/`switch`); wrapper `<div>` inside `role="menu"` breaking required parent-child relationship; `aria-expanded` missing on toggle buttons.
- **Detection heuristic:** Check every `aria-checked`, `aria-selected`, `aria-expanded`, `aria-pressed` against its element's `role`. Flag any `<div>` or `<span>` without a role that is a direct child of `role="menu"`, `role="listbox"`, or `role="tree"`.
- **Sources:** 01 (ARIA anti-patterns table), 04 (AP-03, 7 bugs)
- **Priority:** P1

#### U-SEM-05: Use Landmarks to Structure Page Regions
- **Rule:** Include all perceivable content in landmark regions. Use native HTML elements (`<header>`, `<nav>`, `<main>`, `<footer>`, `<aside>`) over ARIA roles. Multiple instances of the same landmark must have unique `aria-label` values.
- **Why:** Screen reader users navigate by landmarks. Content outside landmarks is invisible to this navigation pattern.
- **Implementation:**
  ```tsx
  <header>...</header>
  <nav aria-label="Primary navigation">...</nav>
  <main>...</main>
  <aside aria-label="Settings sidebar">...</aside>
  <footer>...</footer>
  ```
- **Common violation:** Content outside any landmark region; multiple `<nav>` elements without distinguishing labels; `role="region"` without `aria-label`.
- **Detection heuristic:** Verify all visible content is inside a landmark. Flag `<nav>` or `role="navigation"` elements without unique `aria-label`. Flag `role="region"` or `<section>` without a label.
- **Sources:** 01 (WCAG 2.4.1), 02 (navigateRegions HOC), 06 (Section 2.1)
- **Priority:** P1

### 1.2 Keyboard Interaction Rules

#### U-KBD-01: Every Interactive Element Must Be Keyboard Operable
- **Rule:** Every interactive element must be operable via keyboard alone. Tab order must follow the visual layout. Never use `tabindex` values greater than 0.
- **Why:** Keyboard accessibility is the foundation for screen reader users, switch users, and motor-impaired users. Missing keyboard access locks out entire user populations.
- **Implementation:**
  ```tsx
  // CORRECT: native button is keyboard accessible
  <button onClick={handleAction}>Do Action</button>

  // If custom element is unavoidable, handle ALL keyboard interactions
  <div
    role="button"
    tabIndex={0}
    onClick={handleAction}
    onKeyDown={(e) => {
      if (e.key === 'Enter' || e.key === ' ') {
        e.preventDefault();
        handleAction();
      }
    }}
  >Do Action</div>
  ```
- **Common violation:** Click handlers without keyboard equivalents; drag-only interactions without keyboard alternatives; custom components missing Space key handling.
- **Detection heuristic:** Flag any `onClick` without corresponding keyboard handler on non-native-interactive elements. Flag any `mousedown`/`pointerdown` event that triggers actions without `keydown` equivalent. Flag `tabindex` values > 0.
- **Sources:** 01 (WCAG 2.1.1, 2.1.2, ARIA Rule 3), 03 (pattern catalog), 04 (AP-07)
- **Priority:** P0

#### U-KBD-02: No Keyboard Traps
- **Rule:** Users must be able to navigate to AND away from every interactive element using standard keyboard controls (Tab, Shift+Tab, Escape). The only exception is intentional focus trapping in modals/dialogs with an explicit close mechanism.
- **Why:** Keyboard traps strand users with no way to continue navigating, forcing them to reload the page.
- **Implementation:**
  ```tsx
  // Modal focus trap: intentional, with Escape to exit
  <Modal onRequestClose={close}>
    {/* Tab cycles within modal */}
    {/* Escape closes and returns focus */}
  </Modal>

  // WRONG: custom widget that traps without escape
  <div onKeyDown={(e) => e.preventDefault()}>
    {/* User can never leave */}
  </div>
  ```
- **Common violation:** Custom widgets that prevent Tab from leaving; nested menus where Escape closes all levels instead of one at a time.
- **Detection heuristic:** Flag `onKeyDown` handlers that call `preventDefault()` on Tab without providing an Escape handler. Flag focus-constrained areas without a visible close/escape mechanism.
- **Sources:** 01 (WCAG 2.1.2), 04 (AP-08, 4 bugs)
- **Priority:** P0

#### U-KBD-03: Escape Closes One Level at a Time in Nested Overlays
- **Rule:** In nested menu/submenu/dialog architectures, Escape handlers MUST call `event.stopPropagation()` so only the innermost overlay closes. Focus must return to the parent trigger.
- **Why:** Without `stopPropagation()`, Escape bubbles up and closes all levels simultaneously, stranding users at the top-level trigger instead of stepping back one level.
- **Implementation:**
  ```tsx
  const handleKeyDown = (event) => {
    if (event.key === 'Escape') {
      event.stopPropagation(); // Prevents parent menus from also closing
      closeCurrentMenu();
      parentTriggerRef.current?.focus();
    }
  };
  ```
- **Common violation:** Escape handler without `event.stopPropagation()` in nested menus; Escape closing all overlay levels at once.
- **Detection heuristic:** In navigation/menu components, look for Escape handlers that call close functions without `event.stopPropagation()`. Flag any nested overlay Escape handler that does not manage focus return to the parent trigger.
- **Sources:** 04 (AP-08, 4 bugs)
- **Priority:** P1

#### U-KBD-04: Composite Widgets Use Arrow Keys, Not Tab
- **Rule:** Within composite widgets (tabs, menus, toolbars, tree views, grids, listboxes), use arrow keys for internal navigation. The entire widget should be a single Tab stop. Tab moves focus out of the widget.
- **Why:** This matches the APG interaction model that screen reader users expect. Using Tab for every option within a composite widget creates excessive Tab stops.
- **Implementation:**
  ```
  Tab → enters widget (first or last-focused item)
  Arrow keys → navigate between items within widget
  Tab → exits widget to next focusable element
  ```
- **Common violation:** Making every menu item or tab individually tabbable; using Tab within a toolbar to move between buttons.
- **Detection heuristic:** Flag composite widgets (`role="tablist"`, `role="menu"`, `role="toolbar"`, `role="listbox"`, `role="tree"`) where children have `tabIndex={0}` instead of roving tabindex or `aria-activedescendant`.
- **Sources:** 01 (ARIA composite roles), 03 (NavigableContainer, Tabs), 06 (Section 2.3)
- **Priority:** P1

### 1.3 Focus Management Rules

> CRITICAL: Focus management is the #1 source of a11y bugs in Gutenberg (25+ fixes across AP-01, AP-02, AP-09). These rules get highest priority.

#### U-FOC-01: Guard Focus on State Changes and Re-renders
- **Rule:** Before any state update that conditionally renders or re-mounts a DOM subtree, check if the focused element is inside that subtree. If it is, either (a) do not unmount the element (use CSS hiding), or (b) store the focus position and restore it after re-render.
- **Why:** When React state updates cause a component to unmount and remount, the browser's focus falls to `<body>`, leaving keyboard and screen reader users stranded.
- **Implementation:**
  ```tsx
  // Pattern: Check and restore focus after conditional rendering
  const previousFocusRef = useRef<HTMLElement | null>(null);

  useEffect(() => {
    // After re-render, if focus was lost, restore it
    if (
      previousFocusRef.current &&
      !document.body.contains(previousFocusRef.current)
    ) {
      fallbackFocusTarget.current?.focus();
    }
  });

  // Pattern: Use CSS display instead of conditional rendering
  <div style={{ display: isVisible ? 'block' : 'none' }}>
    {/* Element stays in DOM, focus is preserved */}
  </div>
  ```
- **Common violation:** Conditional rendering (`{isVisible && <Component />}`) around elements that may have focus; React `key` prop changes on focused elements causing unmount/remount.
- **Detection heuristic:** Look for conditional rendering (`&&` or ternary) around interactive elements or containers. Flag state changes that toggle rendering of focused content without a focus restoration strategy.
- **Sources:** 04 (AP-01, 12 bugs -- highest frequency), 06 (Section 4.1)
- **Priority:** P0

#### U-FOC-02: Always Return Focus from Overlays
- **Rule:** Every popover, modal, dialog, and dropdown MUST store a reference to its trigger element before opening and call `triggerRef.focus()` in ALL close paths (close button, Escape, click outside, programmatic close).
- **Why:** When an overlay closes without restoring focus, keyboard users are stranded at `<body>` with no context of where they were.
- **Implementation:**
  ```tsx
  // Store trigger reference
  const triggerRef = useRef<HTMLElement | null>(null);

  const openOverlay = () => {
    triggerRef.current = document.activeElement as HTMLElement;
    setIsOpen(true);
  };

  // Restore in ALL close paths
  const closeOverlay = () => {
    setIsOpen(false);
    triggerRef.current?.focus();
  };

  // In Gutenberg: use useFocusReturn() hook
  const focusReturnRef = useFocusReturn();
  ```
- **Common violation:** Overlay close handler that sets state but does not restore focus; trigger element unmounted while overlay is open; only some close paths restore focus (e.g., Escape restores but click-outside does not).
- **Detection heuristic:** Find any component that renders `<Popover>`, `<Modal>`, or overlay. Check that the trigger element has a ref stored before opening and `.focus()` called in all close handlers. Flag overlays where the trigger can be unmounted while the overlay is open.
- **Sources:** 04 (AP-02, 8 bugs -- second highest), 02 (useFocusReturn), 03 (Modal/Dropdown patterns)
- **Priority:** P0

#### U-FOC-03: Check hasFocusWithin Before Programmatic Focus
- **Rule:** Before calling `.focus()` in `useEffect`, `requestAnimationFrame`, or any async callback, check `container.contains(document.activeElement)`. Skip the focus call if focus is already inside the target container.
- **Why:** Without this guard, programmatic focus in lifecycle methods "steals" focus from the user's current interaction, interrupting typing or menu navigation.
- **Implementation:**
  ```tsx
  useEffect(() => {
    if (
      containerRef.current &&
      !containerRef.current.contains(document.activeElement)
    ) {
      containerRef.current.focus();
    }
  }, [dependency]);
  ```
- **Common violation:** `useEffect` with `.focus()` that runs on mount or re-render without checking current focus; `requestAnimationFrame` callbacks that unconditionally move focus.
- **Detection heuristic:** Flag any `requestAnimationFrame` or `useEffect` callback that calls `.focus()` without first checking `document.activeElement` or `container.contains(document.activeElement)`.
- **Sources:** 04 (AP-09, 5 bugs)
- **Priority:** P0

#### U-FOC-04: Focus Trapping in Modals Must Use Proper Hook Composition
- **Rule:** Modal/dialog focus trapping requires three behaviors composed together: (1) constrained tabbing (Tab cycles within), (2) focus on mount (first element or container), (3) focus return on unmount (to trigger).
- **Why:** Missing any one of these creates a different accessibility failure: no trap = focus escapes; no focus on mount = user starts outside the dialog; no focus return = user is lost after close.
- **Implementation (Gutenberg):**
  ```tsx
  const constrainedTabbingRef = useConstrainedTabbing();
  const focusOnMountRef = useFocusOnMount('firstElement');
  const focusReturnRef = useFocusReturn();

  <div
    ref={useMergeRefs([
      constrainedTabbingRef,
      focusOnMountRef,
      focusReturnRef,
    ])}
    role="dialog"
    aria-modal="true"
    aria-labelledby={titleId}
    tabIndex={-1}
  >
  ```
- **Common violation:** Modal with focus trap but no focus return; dialog that focuses on mount but does not constrain Tab; overlay that returns focus but has no initial focus management.
- **Detection heuristic:** For any `role="dialog"` or modal component, verify all three behaviors are present: constrained tabbing, focus on mount, and focus return.
- **Sources:** 02 (hook reference cards), 03 (Modal composition pattern), 06 (APG Dialog)
- **Priority:** P0

#### U-FOC-05: Focus Visible Indicators Must Never Be Removed
- **Rule:** Never remove focus outlines without providing a visible replacement. Focus indicators must meet 3:1 contrast against adjacent colors per WCAG 2.4.7/2.4.11.
- **Why:** Removing focus outlines makes keyboard navigation impossible for sighted users. They cannot see where they are on the page.
- **Implementation:**
  ```css
  /* WRONG */
  *:focus { outline: none; }

  /* CORRECT: custom focus style */
  :focus-visible {
    outline: 2px solid #005fcc;
    outline-offset: 2px;
  }
  ```
- **Common violation:** Global `outline: none` without replacement; custom components without any focus styling; focus indicators obscured by sticky headers.
- **Detection heuristic:** Flag any CSS rule that removes `outline` on `:focus` without a visible replacement. Flag `outline: 0` or `outline: none` on interactive elements.
- **Sources:** 01 (WCAG 2.4.7, 2.4.11, WP Standards #3), 06 (Section 2.3)
- **Priority:** P0

### 1.4 Color and Visual Rules

#### U-VIS-01: Color Must Not Be the Sole Indicator
- **Rule:** Error states, required fields, success messages, active states, and links within text must use additional visual indicators beyond color alone (icons, text labels, underlines, patterns).
- **Why:** Users who are colorblind or have low vision cannot distinguish color-only indicators.
- **Implementation:**
  ```tsx
  // CORRECT: error uses icon + text + color
  <div className="error">
    <ErrorIcon /> Invalid email address
  </div>

  // WRONG: error uses only red border
  <input style={{ borderColor: 'red' }} />
  ```
- **Common violation:** Error states shown only as red borders; links indistinguishable from body text except by color; success/error icons that only differ by color.
- **Detection heuristic:** Flag form error handling that only changes border color. Flag links within text that rely solely on color (check for `text-decoration` removal).
- **Sources:** 01 (WCAG 1.4.1)
- **Priority:** P1

#### U-VIS-02: Meet Color Contrast Ratios
- **Rule:** Normal text requires 4.5:1 contrast ratio against its background. Large text (18pt or 14pt bold) requires 3:1. Non-text UI components and focus indicators require 3:1 contrast.
- **Why:** Low contrast makes text unreadable for users with low vision, affecting the largest disability population.
- **Implementation:** Use a contrast checker tool during development. Ensure all color pairings meet the ratios.
- **Common violation:** Light gray text on white backgrounds; placeholder text with insufficient contrast; custom theme colors that do not meet ratios.
- **Detection heuristic:** This requires computed style analysis. Flag known low-contrast patterns (e.g., `color: #999` on `background: #fff` = 2.85:1, fails).
- **Sources:** 01 (WCAG 1.4.3, 1.4.11)
- **Priority:** P1

### 1.5 Content and Language Rules

#### U-CNT-01: Page Language Must Be Declared
- **Rule:** Set `lang` attribute on `<html>` with a valid BCP 47 language tag. Wrap foreign-language text in elements with appropriate `lang` attributes.
- **Why:** Screen readers use the language attribute to switch pronunciation engines. Without it, content is mispronounced.
- **Implementation:**
  ```html
  <html lang="en">
  ...
  <p>The French word <span lang="fr">bonjour</span> means hello.</p>
  ```
- **Common violation:** Missing `lang` attribute on `<html>`; foreign-language blocks without `lang` attributes.
- **Detection heuristic:** Check `<html>` for `lang` attribute. Validate it is a recognized BCP 47 tag.
- **Sources:** 01 (WCAG 3.1.1, 3.1.2)
- **Priority:** P1

#### U-CNT-02: Heading Hierarchy Must Be Logical
- **Rule:** One `<h1>` per page. Headings must not skip levels (no `<h2>` to `<h4>` without `<h3>`). Use heading elements for structure, not for visual styling.
- **Why:** Screen reader users navigate by heading level. Skipped levels suggest missing content or broken page structure.
- **Implementation:** Maintain sequential heading levels. Use CSS for visual sizing instead of heading level changes.
- **Common violation:** Skipping from `<h2>` to `<h4>`; using `<h1>` multiple times; using heading tags for visual emphasis rather than structure.
- **Detection heuristic:** Parse heading elements and verify sequential progression. Flag heading level skips.
- **Sources:** 01 (WCAG 2.4.6, WP Standards #4)
- **Priority:** P1

### 1.6 Live Region and Announcement Rules

#### U-LIV-01: Announce All Dynamic Content Changes
- **Rule:** Every visual change that a sighted user can perceive (results appearing, counts changing, state toggling, formatting applied) MUST have a corresponding screen reader announcement via `wp.a11y.speak()` or ARIA live regions.
- **Why:** Screen reader users cannot see visual updates. Without programmatic announcements, dynamic changes are invisible to them.
- **Implementation (Gutenberg):**
  ```tsx
  import { speak } from '@wordpress/a11y';

  // After filter results update
  useEffect(() => {
    if (isExpanded) {
      const message = results.length > 0
        ? sprintf(_n('%d result found...', '%d results found...', results.length), results.length)
        : __('No results.');
      speak(message, 'polite');
    }
  }, [results, isExpanded]);
  ```
- **Implementation (general):**
  ```tsx
  <div aria-live="polite" aria-atomic="true">
    {statusMessage}
  </div>
  ```
- **Common violation:** Autocomplete results appearing without announcement; formatting toggles with no auditory feedback; search result counts updating silently.
- **Detection heuristic:** Look for state changes that update visible UI (lists appearing, counts changing, status text) without a corresponding `speak()` call or live region.
- **Sources:** 01 (WCAG 4.1.3), 04 (AP-06, 5 bugs), 03 (live region catalog)
- **Priority:** P1

#### U-LIV-02: Choose ONE Announcement Mechanism Per Message
- **Rule:** Never combine `role="alert"` with `aria-live="polite"` (they conflict -- `role="alert"` implies `aria-live="assertive"`). Never put live region attributes on elements also referenced by `aria-describedby`. Use `aria-describedby` for persistent context read on focus; use `speak()` or live regions for transient notifications.
- **Why:** Conflicting live region settings cause double announcements or unexpected announcement behavior.
- **Implementation:**
  ```tsx
  // WRONG: conflicting announcement mechanisms
  <span role="alert" aria-live="polite" id="error-msg">Error text</span>
  <input aria-describedby="error-msg" />

  // CORRECT: separate concerns
  <span id="error-desc">Error text</span>   {/* persistent context */}
  <input aria-describedby="error-desc" aria-invalid="true" />
  ```
- **Common violation:** `role="alert"` combined with `aria-live="polite"`; error text with both live region attributes and `aria-describedby` reference causing double announcement.
- **Detection heuristic:** Flag elements with both `role="alert"` and `aria-live`. Flag elements that have `aria-live` AND are referenced by another element's `aria-describedby`.
- **Sources:** 04 (AP-15, 2 bugs), 03 (live region catalog)
- **Priority:** P1

#### U-LIV-03: Use Correct Politeness Levels
- **Rule:** Use `polite` for non-urgent status updates (result counts, selection confirmations, loading completion). Use `assertive` only for time-sensitive errors and critical actions. Default to `polite`.
- **Why:** Overusing `assertive` interrupts the current screen reader announcement, desensitizing users and creating noise.
- **Implementation:**
  ```tsx
  // Polite: status update
  speak('3 results found', 'polite');

  // Assertive: error
  speak('Error: invalid email address', 'assertive');

  // Mapping pattern from Notice component
  const politeness = status === 'error' ? 'assertive' : 'polite';
  ```
- **Common violation:** Using `assertive` for routine updates; using `assertive` for result counts.
- **Detection heuristic:** Flag `speak(msg, 'assertive')` or `aria-live="assertive"` and verify the message warrants interruption (errors, critical warnings only).
- **Sources:** 01 (WCAG 4.1.3), 02 (speak() reference), 03 (politeness mapping), 04 (AP-06)
- **Priority:** P1

#### U-LIV-04: Debounce Rapid Announcements
- **Rule:** When content changes rapidly (typing in search fields, filtering lists), debounce `speak()` calls with a 500ms delay to avoid flooding the screen reader.
- **Why:** Rapid-fire announcements are unintelligible and distracting. The screen reader cannot finish one announcement before the next starts.
- **Implementation (Gutenberg):**
  ```tsx
  const debouncedSpeak = useDebounce(speak, 500);

  useEffect(() => {
    debouncedSpeak(
      sprintf(_n('%d result found...', '%d results found...', count), count),
      'assertive'
    );
  }, [results]);
  ```
- **Common violation:** `speak()` called on every keystroke in a search field; live region text updated on every character change.
- **Detection heuristic:** Flag `speak()` calls inside event handlers that fire on every keystroke or rapid state change without debouncing.
- **Sources:** 02 (withSpokenMessages, useDebounce), 03 (ComboboxControl pattern), 04 (AP-06)
- **Priority:** P2

### 1.7 Form and Input Rules

#### U-FRM-01: Every Form Control Must Have an Associated Label
- **Rule:** Every `<input>`, `<select>`, and `<textarea>` must have a visible `<label>` associated via `for`/`id` pairing. `placeholder` is not a substitute. Use `<fieldset>` + `<legend>` for grouped controls (radio groups, checkbox groups).
- **Why:** Without a label, screen reader users cannot identify the purpose of a form control. Voice control users cannot activate the control by speaking its label.
- **Implementation:**
  ```tsx
  // CORRECT: label with for/id
  <label htmlFor="search-input">Search</label>
  <input id="search-input" type="search" />

  // CORRECT: visually hidden label
  <VisuallyHidden as="label" htmlFor="search-input">Search</VisuallyHidden>
  <input id="search-input" type="search" />

  // CORRECT: radio group with fieldset/legend
  <fieldset>
    <legend>Notification preference</legend>
    <label><input type="radio" name="notif" value="email" /> Email</label>
    <label><input type="radio" name="notif" value="sms" /> SMS</label>
  </fieldset>
  ```
- **Common violation:** `<input>` with `placeholder` as only label; radio groups without `<fieldset>`/`<legend>`; required fields indicated only by asterisk without explanation.
- **Detection heuristic:** Check every `<input>`, `<select>`, `<textarea>` for an associated `<label>` or `aria-label`/`aria-labelledby`. Flag elements where `placeholder` is the only identifying text.
- **Sources:** 01 (WCAG 3.3.2, WP Standards #4), 03 (form control patterns)
- **Priority:** P0

#### U-FRM-02: Error States Must Be Identified and Described
- **Rule:** When an input error is detected: (1) set `aria-invalid="true"` on the field, (2) associate the error message via `aria-describedby` or `aria-errormessage`, (3) provide actionable error text (not just "Invalid input").
- **Why:** Screen reader users need to know which field has an error and how to fix it. Color-only error indication is invisible to them.
- **Implementation:**
  ```tsx
  <input
    id="email"
    aria-invalid={hasError}
    aria-describedby={hasError ? 'email-error' : undefined}
  />
  {hasError && (
    <span id="email-error" role="alert">
      Enter a valid email address (e.g., user@example.com)
    </span>
  )}
  ```
- **Common violation:** Error shown only as red border; error message not associated with the field; generic "An error occurred" messages.
- **Detection heuristic:** Check form validation logic for `aria-invalid="true"` on error fields. Check for `aria-describedby` pointing to error messages. Flag generic error strings.
- **Sources:** 01 (WCAG 3.3.1, 3.3.3), 06 (Section 5.5 #12)
- **Priority:** P0

#### U-FRM-03: Use aria-disabled Instead of HTML disabled for Discoverable Controls
- **Rule:** For interactive controls that users need to discover, use `aria-disabled="true"` with handler prevention instead of the HTML `disabled` attribute. This keeps the element focusable and perceivable.
- **Why:** HTML `disabled` removes elements from the tab order entirely. Screen reader users cannot discover that a feature exists but is unavailable.
- **Implementation:**
  ```tsx
  // CORRECT: accessible disabled state
  <Button
    disabled={!canSave}
    accessibleWhenDisabled
  >
    Save
  </Button>

  // Which renders as:
  <button
    aria-disabled="true"
    onClick={(e) => { e.preventDefault(); e.stopPropagation(); }}
  >
    Save
  </button>

  // WRONG: invisible to screen readers
  <button disabled>Save</button>
  ```
- **Common violation:** `<button disabled>` on controls users need to discover; disabled form controls with no explanation of why they are disabled.
- **Detection heuristic:** Flag `<button disabled>`, `<Button disabled>` without `accessibleWhenDisabled` (Gutenberg) or `aria-disabled`. Exception: controls inside already-hidden containers or purely decorative controls.
- **Sources:** 04 (AP-05, 4 bugs), 03 (Button pattern), 05 (Pattern 8)
- **Priority:** P1

#### U-FRM-04: Support Pointer Cancellation and Alternative Input
- **Rule:** Use `click` events (fires on up-event) for actions, not `mousedown`/`pointerdown`. For drag operations, provide click-based keyboard alternatives. Support paste in password fields.
- **Why:** Users with motor impairments need to cancel accidental pointer actions. Drag-only interactions exclude keyboard and switch users.
- **Implementation:**
  ```tsx
  // CORRECT: click event (fires on pointer up)
  <button onClick={handleAction}>Submit</button>

  // For drag interactions, provide alternatives
  <SortableList>
    <MoveUpButton /> {/* Keyboard alternative */}
    <MoveDownButton />
  </SortableList>
  ```
- **Common violation:** Actions on `mousedown`/`pointerdown` with no cancel mechanism; sortable lists operable only by drag; slider controls without keyboard support.
- **Detection heuristic:** Flag `mousedown`, `pointerdown`, `touchstart` handlers that trigger actions without corresponding cancel logic. Flag drag libraries without keyboard alternatives.
- **Sources:** 01 (WCAG 2.5.1, 2.5.2, 2.5.7)
- **Priority:** P2

### 1.8 Trigger-Popup Relationship Rules

#### U-TRG-01: Trigger Buttons Must Declare Popup Relationship
- **Rule:** Every button that opens a popup MUST have `aria-haspopup` set to the correct value (`"dialog"`, `"menu"`, `"listbox"`, `"tree"`, or `"grid"`) and `aria-expanded` that dynamically reflects the open/closed state across ALL interaction modes.
- **Why:** Screen reader users cannot discover that a button has a popup or determine whether it is currently open.
- **Implementation:**
  ```tsx
  <Button
    onClick={toggleMenu}
    aria-haspopup="menu"
    aria-expanded={isMenuOpen}
  >
    Options
  </Button>
  ```
- **Common violation:** Button opens a dialog but lacks `aria-haspopup="dialog"`; `aria-expanded` only updated on click, not on hover; `aria-expanded` hardcoded instead of dynamic.
- **Detection heuristic:** Find any component rendering `<Popover>`, `<Modal>`, dropdown, or overlay. Check that the trigger has `aria-haspopup` and `aria-expanded`. Flag `aria-expanded` that is hardcoded or only updated in some interaction paths.
- **Sources:** 04 (AP-04, 6 bugs), 03 (Dropdown/DropdownMenu patterns)
- **Priority:** P1

---

## 2. Component-Type Rules (Keyed by Widget Type)

### 2.1 Dialog / Modal

**Required ARIA pattern (APG Dialog Modal):**
- `role="dialog"` on the container
- `aria-modal="true"` on the dialog
- `aria-labelledby` referencing visible title, OR `aria-label`
- Optional: `aria-describedby` for descriptive content

**Required keyboard interactions:**
- Tab: Move to next tabbable element inside (wraps to first)
- Shift+Tab: Move to previous tabbable element (wraps to last)
- Escape: Close the dialog

**Required focus behavior:**
1. On open: focus moves into dialog (first tabbable element, or `tabIndex={-1}` container for complex content)
2. While open: Tab is constrained within dialog
3. On close: focus returns to the element that opened the dialog
4. Background: all sibling content hidden from screen readers via `aria-hidden="true"` on body children

**Common mistakes to avoid (from AP-01, AP-02):**
- Missing focus return on close (AP-02, 8 bugs)
- Not constraining Tab (focus escapes to background)
- Not hiding background from screen readers
- Trigger element being unmounted while dialog is open

**Testing requirements:**
1. Test focus on mount: `expect(screen.getByRole('dialog')).toHaveFocus()`
2. Test focus trap: Tab cycles within dialog (Pattern 5)
3. Test focus return: `expect(triggerButton).toHaveFocus()` after Escape
4. Test `aria-hidden` management on siblings (Pattern 10)
5. Test nested dialog stacking

**Gutenberg reference:** `Modal` in `packages/components/src/modal/`. Composes `useConstrainedTabbing`, `useFocusOnMount`, `useFocusReturn`, `useMergeRefs`. Uses `ariaHelper.modalize/unmodalize` for `aria-hidden` sibling management with depth stack for nested modals. Portaled to `document.body` via `createPortal`.

- **Sources:** 01, 02, 03, 04, 05, 06

---

### 2.2 Combobox / Autocomplete

**Required ARIA pattern (APG Combobox):**
- `role="combobox"` on the text input
- `aria-expanded`: `true` when listbox is visible, `false` when hidden
- `aria-autocomplete="list"` (or `"both"` or `"inline"`)
- `aria-controls` or `aria-owns` referencing the listbox
- `aria-activedescendant` pointing to the ID of the currently highlighted option
- Listbox: `role="listbox"`, options: `role="option"` with `aria-selected`

**Required keyboard interactions:**
- ArrowDown/ArrowUp: Navigate options (with wrapping)
- Enter: Select highlighted option
- Escape: Close listbox
- Typing: Filter/search within options

**Required focus behavior:**
- Physical focus stays on the input at all times
- Virtual focus (via `aria-activedescendant`) indicates the active option
- `aria-activedescendant` only set when input has focus AND listbox is expanded AND an option is highlighted

**Common mistakes to avoid:**
- Not announcing result count to screen readers (AP-06)
- `aria-activedescendant` set when listbox is collapsed
- No Escape handler to close suggestions
- Missing `aria-owns`/`aria-controls` linking input to listbox

**Testing requirements:**
1. Keyboard navigation: ArrowDown/Up through options, Enter to select (Pattern 4)
2. Live region: result count announced via `speak()` (Pattern 7)
3. Focus stays on input throughout
4. `aria-expanded` toggles correctly
5. Reset button returns focus to input (Pattern 14)

**Gutenberg reference:** `ComboboxControl` in `packages/components/src/combobox-control/`. Uses `speak()` with `polite` for result count, `assertive` for selection. `aria-activedescendant` via shared `TokenInput` component.

- **Sources:** 02, 03, 05, 06

---

### 2.3 Tabs / Tab Panel

**Required ARIA pattern (APG Tabs):**
- `role="tablist"` on the container
- `role="tab"` on each tab with `aria-selected`
- `role="tabpanel"` on each panel
- `aria-controls` on tab pointing to its panel
- `aria-labelledby` on panel pointing to its tab

**Required keyboard interactions:**
- ArrowLeft/ArrowRight (horizontal) or ArrowUp/ArrowDown (vertical): Navigate between tabs
- Automatic activation: selection follows focus (`selectOnMove=true`)
- Manual activation: Enter/Space to select (`selectOnMove=false`)
- Home/End: Jump to first/last tab (optional)
- Tab into tablist focuses selected tab; Tab out goes to tabpanel or next element

**Required focus behavior:**
- Roving tabindex: only the active tab has `tabIndex={0}`, others have `tabIndex={-1}`
- On blur with automatic activation: active tab syncs back to selected tab

**Common mistakes to avoid:**
- Making every tab individually tabbable (should be single Tab stop)
- Not supporting RTL (arrow keys should reverse direction)
- Panel content not associated with its tab

**Testing requirements:**
1. Arrow key navigation between tabs (Pattern 4)
2. `aria-selected` toggles (Pattern 2)
3. Tab panel visibility matches selected tab
4. Roving tabindex: Tab enters/leaves as single stop (Pattern 4)

**Gutenberg reference:** `Tabs` in `packages/components/src/tabs/`. Built on Ariakit `Tab`, `TabList`, `TabPanel`, `useTabStore`. RTL-aware via `isRTL()`. Instance IDs prevent collisions.

- **Sources:** 03, 05, 06

---

### 2.4 Menu / Menu Button / Dropdown

**Required ARIA pattern (APG Menu Button):**
- Trigger: `aria-haspopup="true"` (or `"menu"`), `aria-expanded`
- Menu: `role="menu"`, `aria-label` or `aria-labelledby`
- Items: `role="menuitem"`, `role="menuitemcheckbox"`, or `role="menuitemradio"`
- Checkbox/radio items: `aria-checked` tracking state

**Required keyboard interactions:**
- Enter/Space on trigger: Open menu
- ArrowDown on trigger: Open menu and focus first item
- ArrowDown/ArrowUp: Navigate items (with wrapping)
- Escape: Close menu, return focus to trigger
- Home/End: First/last item

**Required focus behavior:**
- On open: focus moves to first menu item
- On close: focus returns to trigger button
- Single Tab stop: Tab exits the menu entirely

**Common mistakes to avoid:**
- Using `role="menu"` for site navigation (only for application menus)
- Wrapper `<div>` breaking menu parent-child structure (AP-03)
- Missing `aria-haspopup` on trigger (AP-04)
- Escape closing all nested levels without `stopPropagation()` (AP-08)
- `aria-checked` on `role="menuitem"` instead of `menuitemcheckbox` (AP-03)

**Testing requirements:**
1. Multiple open methods: click, Enter, Space, ArrowDown (Pattern 11)
2. Multiple close methods: Escape, click outside, item selection
3. Focus return to trigger on close
4. `aria-expanded` toggling on trigger
5. Arrow key navigation with wrapping (Pattern 4)

**Gutenberg reference:** `DropdownMenu` in `packages/components/src/dropdown-menu/`. Uses `Dropdown` + `NavigableMenu`. Arrow key navigation via `NavigableContainer`.

- **Sources:** 03, 04, 05, 06

---

### 2.5 Select / Listbox

**Required ARIA pattern (APG Listbox):**
- `role="listbox"` on the container
- `role="option"` on each item with `aria-selected`
- `aria-label` or `aria-labelledby` on the listbox
- `aria-multiselectable="true"` for multi-select

**Required keyboard interactions:**
- ArrowDown/ArrowUp: Navigate options
- Home/End: First/last option
- Type-ahead: Focus moves to matching option
- Space (multi-select): Toggle selection
- Enter: Select/activate option

**Required focus behavior:**
- Roving tabindex or `aria-activedescendant` for option focus

**Gutenberg reference:** `CustomSelectControl v2` built on Ariakit `Select`, `SelectPopover`, `SelectItem`. Provides `VisuallyHidden` description "Currently selected: X".

- **Sources:** 03, 06

---

### 2.6 Tooltip

**Required ARIA pattern (APG Tooltip):**
- `role="tooltip"` on the tooltip container
- `aria-describedby` on trigger, referencing tooltip

**Required keyboard interactions:**
- Focus on trigger: Show tooltip
- Blur: Hide tooltip
- Escape: Dismiss tooltip (optional)

**Required focus behavior:**
- Tooltips do NOT receive keyboard focus
- For interactive tooltip content, use dialog pattern instead

**Common mistakes to avoid:**
- Adding `aria-describedby` when tooltip text matches `aria-label` (duplicates announcement)
- Overriding an existing `aria-describedby` on the trigger
- Using `title` attribute instead of proper tooltip

**Gutenberg reference:** `Tooltip` built on Ariakit. Smart `aria-describedby` management: skips when tooltip text equals anchor's `aria-label`, does not override existing `aria-describedby`.

- **Sources:** 03, 05, 06

---

### 2.7 Slider / Range

**Required ARIA pattern (APG Slider):**
- `role="slider"` on focusable element (or native `<input type="range">`)
- `aria-valuenow`, `aria-valuemin`, `aria-valuemax`
- `aria-label` or `aria-labelledby`
- `aria-valuetext` when numeric value is not user-friendly
- `aria-orientation` if vertical

**Required keyboard interactions:**
- Right/Up Arrow: Increment
- Left/Down Arrow: Decrement
- Home: Set to minimum
- End: Set to maximum
- Page Up/Down: Larger increments (optional)

**Gutenberg reference:** `RangeControl` uses native `<input type="range">` with companion `NumberControl` for precise input. Gap: no `aria-valuetext` support.

- **Sources:** 03, 06

---

### 2.8 Tree View

**Required ARIA pattern (APG Tree View):**
- `role="tree"` on container
- `role="treeitem"` on each node
- `role="group"` for child node containers
- `aria-expanded` on parent nodes (NOT leaf nodes)
- `aria-selected` for selection state
- `aria-level`, `aria-setsize`, `aria-posinset` for dynamic trees

**Required keyboard interactions:**
- Up/Down Arrow: Navigate nodes
- Right Arrow: Open closed node or move to first child
- Left Arrow: Close open node or move to parent
- Home/End: First/last node
- Enter: Activate node
- Type-ahead: Character navigation

**Common mistakes to avoid:**
- Using a flat `<select>` with visual indentation instead of tree roles (Gutenberg's `TreeSelect` has this gap)
- Missing `aria-expanded` on parent nodes

**Gutenberg note:** `TreeSelect` does NOT implement the tree pattern. It renders a flat `<select>` with non-breaking space indentation. The block editor's List View uses `TreeGrid` (`role="treegrid"` with `role="application"` wrapper for NVDA compatibility).

- **Sources:** 03, 06

---

### 2.9 Toggle / Switch

**Required ARIA pattern (APG Switch):**
- `role="switch"` on the element
- `aria-checked`: `true` when on, `false` when off
- Label must NOT change when state changes

**Required keyboard interactions:**
- Space: Toggle state (required)
- Enter: Toggle state (optional)

**Common mistakes to avoid:**
- Using `<input type="checkbox">` without `role="switch"` (Gutenberg's `FormToggle` gap)
- Label text that changes with the switch state

**Gutenberg note:** `FormToggle` uses `<input type="checkbox">` without `role="switch"`. This means screen readers announce it as a checkbox rather than a switch. Adding `role="switch"` would fix this with minimal code change.

- **Sources:** 03, 06

---

### 2.10 Notice / Alert / Snackbar

**Required ARIA pattern (APG Alert):**
- `role="alert"` on the alert container (implies `aria-live="assertive"`)
- OR `role="status"` for non-urgent messages (implies `aria-live="polite"`)
- Alerts should NOT auto-disappear (WCAG 2.2.1 timing risk)
- Alerts should NOT steal focus

**Implementation:**
```tsx
// Persistent notice with announcement
<div role="status">
  <VisuallyHidden>{getStatusLabel(status)}</VisuallyHidden>
  {message}
</div>

// Additionally announce via speak()
useEffect(() => {
  speak(message, status === 'error' ? 'assertive' : 'polite');
}, [message, status]);
```

**Common mistakes to avoid:**
- Auto-dismissing snackbars at fixed timeout with no user control (AP-12, WCAG 2.2.1)
- Using `speak()` without also having a visible status container
- Combining `role="alert"` with `aria-live="polite"` (conflicts)

**Gutenberg reference:** `Notice` maps politeness to severity (`error` = assertive, others = polite). `Snackbar` auto-dismisses at 6000ms (known WCAG 2.2.1 gap).

- **Sources:** 03, 04, 06

---

### 2.11 Form Controls (Checkbox, Radio, Text Input)

**Checkbox:**
- Use native `<input type="checkbox">` with `<label>`
- Support `indeterminate` state via JavaScript property
- `aria-describedby` for help text
- Safari: add `onClick` handler calling `event.currentTarget.focus()`

**Radio Group:**
- Use `<fieldset>` + `<legend>` + `<input type="radio">`
- Browser handles arrow key navigation natively
- `aria-describedby` on individual radios for option descriptions
- `VisuallyHidden` legend when label should not be visible

**Text Input:**
- `<label htmlFor={id}>` association via `BaseControl`
- `aria-describedby` for help text
- `hideLabelFromVision` uses `VisuallyHidden`

**Gutenberg reference:** All use native HTML elements with `BaseControl` wrapper providing label + help text association.

- **Sources:** 03

---

### 2.12 Popover / Disclosure

**Required ARIA pattern:**
- Trigger: `aria-expanded` (required), `aria-haspopup` (when popup has a specific role)
- Popover: `role="dialog"` if it contains interactive content, or no role for simple content
- Focus: moves into popover on open, returns to trigger on close

**Gutenberg reference:** `Popover` uses `useDialog` hook from `@wordpress/compose`, which composes `useConstrainedTabbing`, `useFocusOnMount`, `useFocusReturn`, `useFocusOutside`, and Escape-to-close.

- **Sources:** 02, 03

---

### 2.13 Navigation / Breadcrumb

**Required ARIA pattern:**
- Navigation: `<nav aria-label="Navigation label">` with `<ul>`/`<li>`/`<a>`
- Do NOT use `role="menu"` for site navigation (only for application menus)
- Breadcrumb: `<nav aria-label="Breadcrumb">` + `<ol>` + `aria-current="page"` on current page link

**Common mistakes to avoid:**
- Using `role="menu"` and `role="menuitem"` for navigation links
- Multiple `<nav>` elements without distinguishing `aria-label` values

- **Sources:** 01, 06

---

### 2.14 Toolbar

**Required ARIA pattern (APG Toolbar):**
- `role="toolbar"` on the container
- `aria-label` or `aria-labelledby`
- `aria-orientation` if not horizontal

**Required keyboard interactions:**
- Tab into toolbar: focus on first/last-focused item (single Tab stop)
- Left/Right Arrow: Navigate between controls
- Tab out: leaves toolbar entirely
- Roving tabindex for internal focus

**Gutenberg note:** The `@wordpress/components` package has `NavigableContainer` for arrow key navigation but no standalone `Toolbar` component with proper role. The block editor's toolbar in `@wordpress/block-editor` implements the full toolbar pattern.

- **Sources:** 03, 06

---

### 2.15 Data Grid / Calendar Grid

**Required ARIA pattern (APG Grid):**
- `role="grid"` on container (or use `role="application"` wrapper for screen reader compatibility)
- `role="row"` on each row, `role="gridcell"` on cells
- Arrow keys for cell navigation
- Roving tabindex (one cell has `tabIndex={0}`)

**Calendar-specific keyboard:**
- ArrowLeft/Right: Previous/next day (RTL-aware)
- ArrowUp/Down: Same day previous/next week
- PageUp/Down: Same day previous/next month
- Home/End: Start/end of week

**Gutenberg reference:** `DateTimePicker` implements calendar with roving tabindex and full arrow key navigation including PageUp/Down and Home/End. Uses `role="application"` wrapper and `isFocusAllowed` guard to prevent focus theft from sibling inputs. Gap: does not use `role="grid"`.

- **Sources:** 03, 06

---

## 3. React-Specific Rules

### 3.1 Hook Usage Patterns

#### R-HOK-01: Compose A11y Hooks with useMergeRefs
- **Rule:** When multiple a11y behaviors are needed on one element (focus trap + focus on mount + focus return), compose their refs using `useMergeRefs`. Never attach multiple ref callbacks manually.
- **Implementation:**
  ```tsx
  import { useMergeRefs } from '@wordpress/compose';

  const constrainedTabbingRef = useConstrainedTabbing();
  const focusOnMountRef = useFocusOnMount('firstElement');
  const focusReturnRef = useFocusReturn();

  <div ref={useMergeRefs([
    constrainedTabbingRef,
    focusOnMountRef,
    focusReturnRef,
  ])}>
  ```
- **Sources:** 02, 03

### 3.2 Ref Management for Focus

#### R-REF-01: Use useEffect for Focus Timing, useLayoutEffect for Critical Timing
- **Rule:** Use `useEffect` for standard focus management (runs after paint). Use `useLayoutEffect` when focus timing is critical (must happen before paint to avoid visual flash).
- **Implementation:**
  ```tsx
  // Standard: focus after render
  useEffect(() => {
    ref.current?.focus();
  }, [shouldFocus]);

  // Critical timing: focus before paint
  useLayoutEffect(() => {
    ref.current?.focus();
  }, [shouldFocus]);
  ```
- **Sources:** 06 (Section 4.3)

### 3.3 Portal and Modal Patterns

#### R-PRT-01: Portaled Content Needs Explicit A11y Management
- **Rule:** Content rendered via `createPortal` is outside the DOM hierarchy. Focus trapping, `aria-hidden` sibling management, and screen reader announcements must be explicitly handled.
- **Implementation:**
  ```tsx
  // Portal to document.body
  createPortal(
    <div role="dialog" aria-modal="true">
      {/* Focus trapped via useConstrainedTabbing */}
      {/* Siblings hidden via modalize() */}
    </div>,
    document.body
  );
  ```
- **Key detail:** Gutenberg's `modalize()` only hides direct children of `document.body`, so modals MUST be portaled to `document.body` for the `aria-hidden` management to work. Live regions (`aria-live`, `role="alert"`, `role="status"`) are intentionally NOT hidden so `speak()` continues to work while a modal is open.
- **Sources:** 02, 03, 06

### 3.4 State Update and Announcement Timing

#### R-ANN-01: Call speak() After State Updates
- **Rule:** Call `speak()` in a `useEffect` triggered by the relevant state dependency, not synchronously during the event handler. This ensures the announcement describes the current UI state.
- **Implementation:**
  ```tsx
  // CORRECT: speak after state settles
  useEffect(() => {
    if (isExpanded && matchingSuggestions.length > 0) {
      speak(sprintf(_n('%d result found...', '%d results found...', count), count), 'polite');
    }
  }, [matchingSuggestions, isExpanded]);

  // WRONG: speak during handler before state renders
  const handleFilter = (value) => {
    setFilter(value);
    speak('Results updated', 'polite'); // May describe stale UI
  };
  ```
- **Key detail:** `speak()` clears live regions before setting new content. Two rapid calls cause the first to be cleared before announcement. The second call wins.
- **Sources:** 02, 03, 06

### 3.5 Component Composition for A11y

#### R-CMP-01: Ariakit Components Require Prop Spreading and Ref Forwarding
- **Rule:** Custom components used with Ariakit's `render` prop must: (1) spread all incoming props, (2) forward and merge refs, (3) chain event handlers rather than replacing them.
- **Implementation:**
  ```tsx
  // Ariakit composition
  <Ariakit.Tab render={<CustomTab />}>Tab Label</Ariakit.Tab>

  // CustomTab must forward props and ref
  const CustomTab = forwardRef((props, ref) => (
    <button ref={ref} {...props} />
  ));
  ```
- **Sources:** 06 (Section 3.1)

#### R-CMP-02: Use Ariakit for Patterns It Covers
- **Rule:** For new components matching APG patterns covered by Ariakit (Dialog, Combobox, Menu, Select, Tabs, Tooltip, Composite, RadioGroup, Checkbox, Disclosure, Popover), prefer Ariakit over custom implementation. It handles roles, states, keyboard, and focus automatically.
- **Sources:** 06 (Section 3.4)

### 3.6 Conditional Rendering and Focus

#### R-CND-01: Prefer CSS Hiding Over Conditional Rendering for Focusable Content
- **Rule:** When an element may have focus and needs to be hidden temporarily, prefer `display: none` or `hidden` attribute over conditional rendering (`{condition && <Component />}`). If conditional rendering is necessary, implement explicit focus restoration.
- **Why:** Conditional rendering unmounts the DOM node, causing focus to fall to `<body>`. CSS hiding preserves the node in the DOM.
- **Implementation:**
  ```tsx
  // PREFERRED: CSS hiding preserves focus context
  <div style={{ display: isVisible ? 'block' : 'none' }}>
    <input ref={inputRef} />
  </div>

  // IF conditional rendering is necessary: restore focus
  useEffect(() => {
    if (!isVisible && previouslyFocusedRef.current) {
      fallbackRef.current?.focus();
    }
  }, [isVisible]);
  ```
- **Sources:** 04 (AP-01, 12 bugs), 06 (Section 4.1)

#### R-CND-02: Never Use Array Index as Key for Interactive Elements
- **Rule:** When rendering lists of interactive elements that may be reordered, always use a stable unique ID as the React `key`, never the array index.
- **Why:** When `key` changes on a focused element, React unmounts and remounts it, causing focus loss.
- **Sources:** 06 (Section 4.1)

---

## 4. Review Checklist (Prioritized)

### P0: Must Fix (Critical -- WCAG A violations, keyboard traps, missing labels)

- [ ] Every `<img>` has `alt` attribute (empty `alt=""` for decorative). Every icon-only button has an accessible name.
- [ ] Every form `<input>`, `<select>`, `<textarea>` has an associated `<label>` or `aria-label`/`aria-labelledby`.
- [ ] Every interactive element is operable via keyboard (Tab, Enter/Space, Escape, Arrow keys as applicable).
- [ ] No keyboard traps exist -- user can Tab/Escape away from every interactive element.
- [ ] Focus order follows visual layout -- no positive `tabindex` values.
- [ ] Focus is not lost on state changes or re-renders. When conditional rendering removes a focused element, focus is explicitly restored.
- [ ] Overlays (modals, popovers, dropdowns) return focus to trigger on ALL close paths.
- [ ] Modals have focus trapping (Tab cycles within) and focus on mount.
- [ ] Focus indicators are visible on every interactive element (no `outline: none` without replacement).
- [ ] Error states use `aria-invalid="true"` and error messages are associated via `aria-describedby`.
- [ ] ARIA `role` attributes match the actual interaction pattern. No `<div role="button">` without full keyboard handling.
- [ ] `aria-hidden="true"` is NOT on any focusable element.

### P1: Should Fix (Important -- WCAG AA violations, focus management gaps)

- [ ] Color contrast meets 4.5:1 for normal text, 3:1 for large text and UI components.
- [ ] Color is not the sole indicator for any state (error, required, active, links).
- [ ] Trigger buttons for popups have `aria-haspopup` and dynamic `aria-expanded`.
- [ ] ARIA state attributes (`aria-checked`, `aria-selected`, `aria-pressed`, `aria-expanded`) are valid for the element's role.
- [ ] No wrapper `<div>` or `<span>` without roles inside ARIA container widgets (`menu`, `listbox`, `tree`, `tablist`).
- [ ] Dynamic content changes are announced via `speak()` or `aria-live` regions.
- [ ] Announcements use correct politeness: `assertive` only for errors/critical actions.
- [ ] Disabled buttons use `aria-disabled="true"` (not HTML `disabled`) when they need to be discoverable.
- [ ] Composite widgets (tabs, menus, toolbars) use arrow keys internally and are single Tab stops.
- [ ] Programmatic focus calls check `contains(document.activeElement)` before moving focus.
- [ ] Escape in nested menus uses `event.stopPropagation()`.
- [ ] `role="application"` usage is rare and paired with `aria-label`.
- [ ] Page has exactly one `<h1>` and headings follow sequential levels.
- [ ] `<html>` has `lang` attribute with valid BCP 47 tag.
- [ ] Target size for interactive elements is at least 24x24 CSS pixels.

### P2: Nice to Fix (Enhancement -- improved screen reader UX, WCAG AAA)

- [ ] `aria-valuetext` provided for sliders displaying non-numeric labels.
- [ ] Toggle/switch components use `role="switch"` (not just `checkbox`).
- [ ] Notice containers have `role="alert"` or `role="status"` in addition to `speak()`.
- [ ] Rapid announcements are debounced (500ms) to avoid flooding screen readers.
- [ ] Announcement mechanisms are not duplicated (no `aria-live` on elements referenced by `aria-describedby`).
- [ ] Preview/read-only content uses `readOnly` and `aria-disabled` instead of `inert`.
- [ ] Iframes have descriptive `title` attributes.
- [ ] Auto-dismissing content (snackbars) allows user-configurable timeout.
- [ ] Home/End keys supported in sliders and spinbuttons.
- [ ] RTL support tested for arrow key navigation in composite widgets.
- [ ] Safari: form controls (checkbox, radio, toggle) have explicit `onClick` focus handler.
- [ ] Firefox: `aria-describedby` target text uses textContent reassignment workaround for dynamic updates.

---

## 5. Anti-Pattern Quick Reference

| # | Anti-Pattern | Severity | Frequency | Prevention Rule(s) | Detection |
|---|-------------|----------|-----------|-------------------|-----------|
| AP-01 | Focus Lost on State Change / Re-render | P0 | 12 bugs | U-FOC-01, R-CND-01, R-CND-02 | Conditional rendering (`&&`, ternary) around focused interactive elements without focus restoration |
| AP-02 | Missing Focus Return After Overlay Close | P0 | 8 bugs | U-FOC-02, U-FOC-04 | Overlay `onClose` handlers without `.focus()` on trigger; trigger unmounted while overlay is open |
| AP-03 | Invalid ARIA Attribute Usage (Wrong Role/State) | P1 | 7 bugs | U-SEM-04 | `aria-checked` on wrong roles; wrapper `<div>` inside ARIA containers; `role="document"` on interactive elements |
| AP-04 | Missing aria-haspopup / aria-expanded on Triggers | P1 | 6 bugs | U-TRG-01 | Buttons opening popups without `aria-haspopup`; hardcoded or partial `aria-expanded` updates |
| AP-05 | Disabled Buttons Removed from Tab Order | P1 | 4 bugs | U-FRM-03 | `<button disabled>` without `accessibleWhenDisabled`; `<Button disabled>` without `aria-disabled` pattern |
| AP-06 | Live Region Not Announcing Dynamic Content | P1 | 5 bugs | U-LIV-01, U-LIV-03 | Visual updates (results, counts, format toggles) without `speak()` or `aria-live` |
| AP-07 | Non-Semantic Element as Interactive Control | P1 | 4 bugs | U-SEM-01 | `<div>` or `<span>` with `role="button"` + `onClick` + `tabIndex`; manual Enter/Space key handlers |
| AP-08 | Keyboard Trap / Escape Not Working in Nested Menus | P1 | 4 bugs | U-KBD-03 | Escape handler without `event.stopPropagation()` in nested overlays |
| AP-09 | Focus Stealing on Component Mount/Re-render | P1 | 5 bugs | U-FOC-03 | `.focus()` in `useEffect`/`requestAnimationFrame` without `contains(document.activeElement)` check |
| AP-10 | Firefox/Safari Browser-Specific Focus Bugs | P1 | 4 bugs | (browser-specific) | Checkbox/radio/toggle without `onClick` focus handler; dynamic `aria-describedby` text without textContent workaround |
| AP-11 | Canvas Iframe Tab Order and Silent Tab Stops | P1 | 3 bugs | (context-specific) | Focus-capturing divs around iframes in view/preview mode; generic iframe labels |
| AP-12 | Inert Content Not Properly Communicated | P2 | 3 bugs | (see Section 2.10) | `inert` attribute on content users should perceive; previews without `VisuallyHidden` context |
| AP-13 | Unnecessary/Incorrect ARIA Role | P2 | 3 bugs | U-SEM-03 | `role="presentation"` on text elements; `role="application"` without `aria-label` |
| AP-14 | Table Semantic Structure Broken | P2 | 2 bugs | (context-specific) | `<RichText tagName="td">` or contentEditable directly on `<td>`/`<th>` |
| AP-15 | Redundant/Conflicting ARIA Live Announcements | P2 | 2 bugs | U-LIV-02 | `role="alert"` + `aria-live="polite"` on same element; live region + `aria-describedby` target overlap |

---

## 6. Decision Trees

### 6.1 "Should I Use ARIA or Semantic HTML?"

```
Is there a native HTML element that provides this semantic?
├── YES → Use the native element. Do NOT add ARIA roles.
│   ├── Button action → <button>
│   ├── Navigation link → <a href>
│   ├── Checkbox → <input type="checkbox">
│   ├── Radio group → <fieldset> + <legend> + <input type="radio">
│   ├── Text input → <input type="text"> + <label>
│   ├── Dropdown → <select> (when styling allows)
│   ├── Progress → <progress>
│   ├── Page regions → <header>, <nav>, <main>, <footer>, <aside>
│   └── Does it need additional state info? → Add aria-* STATE attributes only
│       (e.g., aria-expanded on a <button> that opens something)
└── NO → Use ARIA roles + required states/properties
    ├── Tabbed interface → role="tablist" + role="tab" + role="tabpanel"
    ├── Combobox → role="combobox" + role="listbox" + role="option"
    ├── Tree view → role="tree" + role="treeitem" + role="group"
    ├── Toolbar → role="toolbar"
    ├── Toggle switch → role="switch" + aria-checked
    ├── Dialog → <dialog> or role="dialog" + aria-modal
    ├── Live messages → role="status" (polite) or role="alert" (assertive)
    ├── Tooltip → role="tooltip" + aria-describedby
    └── Application menu → role="menu" + role="menuitem"
        (NOT for site navigation -- use <nav> instead)
```

### 6.2 "What Focus Management Strategy?"

```
Is this an overlay (modal, dialog, popover)?
├── YES → Focus trap + focus return + focus on mount
│   ├── Modal/Dialog
│   │   ├── useConstrainedTabbing (trap Tab within)
│   │   ├── useFocusReturn (restore focus on close)
│   │   ├── useFocusOnMount (focus first element or container)
│   │   ├── modalize() (hide siblings from screen readers)
│   │   └── Compose all via useMergeRefs
│   └── Popover/Dropdown
│       ├── useDialog (Ariakit) OR same hook composition
│       ├── useFocusOutside (close when focus leaves)
│       └── Focus return to trigger on Escape/close
│
└── NO → Is this a composite widget (tabs, menu, toolbar, tree)?
    ├── YES → Roving tabindex OR aria-activedescendant
    │   ├── Physical focus movement needed?
    │   │   ├── YES → Roving tabindex
    │   │   │   ├── Active item: tabIndex={0}
    │   │   │   ├── Other items: tabIndex={-1}
    │   │   │   ├── Arrow keys move focus and update tabIndex
    │   │   │   └── Tab enters/exits widget (single Tab stop)
    │   │   └── NO (focus stays on input, e.g., combobox)
    │   │       ├── aria-activedescendant on container
    │   │       ├── Points to ID of visually highlighted item
    │   │       └── Only set when container has focus AND options visible
    │   └── Guard against focus theft:
    │       ├── Check isFocusWithin before programmatic focus
    │       └── Use isFocusAllowed to prevent sibling components from stealing focus
    │
    └── NO → Standard tab order (no special management needed)
        └── Ensure logical tab order matches visual layout
```

### 6.3 "How to Announce Dynamic Content?"

```
Is the content change critical/urgent?
├── YES (error, destructive action, time-sensitive)
│   ├── WordPress/Gutenberg context?
│   │   ├── YES → speak(message, 'assertive')
│   │   └── NO → aria-live="assertive" OR role="alert"
│   └── Also set aria-invalid="true" on the relevant input if applicable
│
└── NO (status update, results count, selection confirmation)
    ├── WordPress/Gutenberg context?
    │   ├── YES → speak(message, 'polite')
    │   └── NO → aria-live="polite" OR role="status"
    │
    ├── Is content changing rapidly (typing, filtering)?
    │   └── YES → Debounce: useDebounce(speak, 500)
    │
    └── Choose ONE mechanism:
        ├── Transient notification → speak() or aria-live region
        ├── Persistent context → aria-describedby (read on focus)
        └── NEVER combine both on same element
```

### 6.4 "What Keyboard Model for This Widget?"

```
What type of widget is this?
│
├── Single interactive element (button, link, checkbox)
│   └── Tab to focus, Enter/Space to activate
│       └── Native HTML element handles this automatically
│
├── Composite with few options (tabs, toolbar, radio group, menu)
│   └── Arrow keys between options (roving tabindex)
│       ├── Horizontal layout (tabs, toolbar)
│       │   └── ArrowLeft/Right (RTL-aware!)
│       ├── Vertical layout (menu, listbox)
│       │   └── ArrowUp/Down
│       ├── Grid layout (calendar, data grid)
│       │   └── All four arrow keys
│       │       Plus: PageUp/Down for months, Home/End for row boundaries
│       └── All: Home/End to first/last item (recommended for 5+ items)
│
├── Composite with many options or text input (combobox)
│   └── Virtual focus (aria-activedescendant)
│       ├── Physical focus stays on text input
│       ├── ArrowDown/Up highlight options
│       ├── Enter selects highlighted option
│       └── Escape closes the option list
│
├── Overlay (modal, dialog, popover)
│   └── Escape to close, Tab trapped inside
│       └── Nested overlays: Escape closes ONE level (use stopPropagation)
│
├── Token/tag input (FormTokenField)
│   └── ArrowLeft/Right moves input cursor among tokens
│       ArrowUp/Down navigates suggestions
│       Enter/Comma adds token
│       Backspace/Delete removes adjacent token
│
└── Drag interaction (focal point, sortable list)
    └── Arrow keys as fine adjustment (1% or 1 position)
        Shift+Arrow for coarse adjustment (10% or 5 positions)
        MUST provide keyboard alternative alongside drag
```

### 6.5 "When to Use Ariakit vs Custom Implementation?"

```
Does Ariakit have a component for this pattern?
│
├── YES (Ariakit covers these APG patterns):
│   ├── Dialog / AlertDialog
│   ├── Combobox / ComboboxPopover
│   ├── Menu / MenuButton / MenuItem
│   ├── Select / SelectPopover / SelectItem
│   ├── Tabs / TabList / TabPanel
│   ├── Tooltip / TooltipAnchor
│   ├── Composite / CompositeItem / CompositeRow
│   ├── RadioGroup / Radio
│   ├── Checkbox
│   ├── Disclosure / DisclosureContent
│   └── Popover / PopoverDisclosure
│   │
│   └── USE ARIAKIT when:
│       ├── Building a new component matching these patterns
│       ├── Focus management complexity is high
│       ├── RTL support is needed
│       └── Popup positioning is required
│
├── NO (custom implementation required):
│   ├── Tree View → Full custom with role="tree", role="treeitem", role="group"
│   ├── Slider → Native <input type="range"> or custom role="slider"
│   ├── Spinbutton → Native <input type="number"> or custom role="spinbutton"
│   ├── Alert → Simple role="alert" container
│   ├── Breadcrumb → <nav aria-label> + <ol> + aria-current
│   ├── Switch → <input type="checkbox" role="switch">
│   ├── Calendar Grid → Use Ariakit Composite with rows, or custom
│   └── Custom editor interactions → Custom implementation
│
└── USE CUSTOM when:
    ├── Native HTML provides correct semantics (<button>, <input>, <select>)
    ├── The existing custom implementation is well-tested and stable
    ├── The pattern does not exist in APG
    └── Ariakit does not provide needed behavior
```

### 6.6 "How to Handle Disabled State?"

```
Should users be able to discover this control exists?
│
├── YES (most cases: buttons, menu items, form controls)
│   └── Use aria-disabled="true" (NOT HTML disabled)
│       ├── Element stays in tab order (focusable)
│       ├── Screen readers announce it as disabled
│       ├── Prevent activation in event handlers:
│       │   onClick: e.preventDefault(); e.stopPropagation();
│       ├── In Gutenberg: <Button disabled accessibleWhenDisabled />
│       └── Test: expect(el).toBeEnabled(); expect(el).toHaveAttribute('aria-disabled', 'true');
│
├── NO (truly hidden, e.g., inside an already-hidden container)
│   └── HTML disabled attribute is acceptable
│       └── Test: expect(el).toBeDisabled();
│
└── Entire section needs disabling?
    ├── Content should still be perceivable (previews, forms)
    │   └── Use readOnly + aria-disabled + onSubmit prevention
    │       Plus: VisuallyHidden explanation ("Form disabled in editor")
    │       Do NOT use inert
    └── Content should be completely hidden
        └── Use inert attribute OR display: none
```
