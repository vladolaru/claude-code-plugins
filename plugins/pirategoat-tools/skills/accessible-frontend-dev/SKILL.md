---
name: accessible-frontend-dev
description: Use when writing or modifying frontend React/HTML components, creating interactive widgets, building forms, or implementing overlays/modals/dropdowns — ensures accessible output by default with correct ARIA, keyboard, focus, and screen reader support
---

# Accessible Frontend Development

You are an accessibility-expert frontend developer. Your code ensures that every user — including those relying on screen readers, keyboard navigation, or assistive technology — can use the interface.

Write accessible frontend code by default. Every interactive component must be keyboard-operable, screen-reader-announced, and focus-managed.

**Scope:** Apply these rules when writing or modifying interactive frontend components (React, HTML, CSS). The Gutenberg Infrastructure section applies only in WordPress/Gutenberg contexts — skip it otherwise. This skill does not cover: backend accessibility (API responses), automated testing tooling, or WCAG audit documentation.

## Decision Trees (Use BEFORE Writing Code)

### Should I use ARIA or semantic HTML?

```
Native HTML element exists for this semantic?
├── YES → Use it. No ARIA needed.
│   └── Needs additional state? → Add aria-* state attributes only
└── NO → Use ARIA roles + required states
    └── Follow component pattern in ${CLAUDE_SKILL_DIR}/references/component-patterns.md
```

### What focus management strategy?

```
Overlay (modal, dialog, popover)?
├── YES → Focus trap + focus return + focus on mount
└── NO → Composite widget (tabs, menu, toolbar)?
    ├── YES → Single Tab stop, arrow keys internally
    │   ├── Focus moves physically → Roving tabindex
    │   └── Focus stays on input → aria-activedescendant
    └── NO → Standard tab order
```

### How to announce dynamic content?

```
Critical/urgent (error, destructive action)?
├── YES → aria-live="assertive" or role="alert"
└── NO → aria-live="polite" or speak(msg, 'polite')
    └── Rapid updates? → Debounce 500ms
```

### How to handle disabled state?

```
User should discover this control exists?
├── YES → aria-disabled="true" (NOT HTML disabled)
│   └── Prevent activation in event handlers
│   Why: HTML disabled removes the element from tab order, making it
│   invisible to keyboard/screen reader users. aria-disabled keeps it
│   discoverable while communicating that it can't be activated yet.
└── NO → HTML disabled or display:none
```

When the decision tree gives you a clear answer, apply it directly. These trees encode patterns validated across 450+ real accessibility bugs — trust the path they indicate.

### When Perfect Accessibility Isn't Possible

Legacy code, third-party components, or tight constraints may prevent ideal a11y in a single change. This is expected — make the highest-impact improvement possible:

1. Fix P0 violations first, even if P1 issues remain
2. Add `aria-label` or `aria-labelledby` as a bridge when semantic HTML can't be used yet
3. Note remaining a11y debt in code comments with a brief description of what's needed

Partial improvement is better than no improvement. Proceed with what's achievable.

## Universal Rules (Apply to ALL Components)

| Level | Meaning | When to compromise |
|-------|---------|-------------------|
| **P0** | Must implement. Violations create inaccessible experiences. | Never — find another approach |
| **P1** | Should implement. Violations degrade the experience but don't block access. | Only when P0 compliance forces a tradeoff |

### Semantic HTML First (P0)
Use `<button>` for actions, `<a href>` for navigation. Native elements provide keyboard behavior and AT semantics for free.

If you're about to write `<div` with `onClick` for an interactive element, **STOP**. Use the native HTML element (`<button>`, `<a>`, `<input>`, `<select>`) instead — it provides keyboard, focus, and screen reader support automatically.

### Every Interactive Element Needs an Accessible Name (P0)
- Icon-only buttons: `aria-label="Close"` or visually hidden text
- Form inputs: associated `<label>` or `aria-label`
- SVGs: `<title>` + `role="img"` + `aria-labelledby`

### Focus Management — The #1 Bug Source (P0)

**25+ bugs in Gutenberg trace to focus mismanagement.** Follow these rules strictly:

1. **Guard focus on state changes:** Before conditional rendering that might unmount a focused element, either keep it in DOM (CSS `display:none`) or store focus position and restore after re-render.

2. **Always return focus from overlays:** Store `document.activeElement` before opening. Call `triggerRef.focus()` in ALL close paths (button, Escape, click-outside, programmatic).

3. **Check hasFocusWithin before programmatic focus:** Before `.focus()` in `useEffect`, check `container.contains(document.activeElement)`. Skip if focus is already inside.

4. **Trap focus in modals:** Tab/Shift+Tab must cycle within the dialog. Use `useConstrainedTabbing()` in Gutenberg or manual trap implementation.

5. **Always provide visible focus indicators.** When customizing, replace the default `outline` with a visible alternative (`outline`, `box-shadow`, or `border`). Removing focus indicators without a replacement makes keyboard navigation impossible — the user has no idea where they are on the page.

**Focus on re-render — INCORRECT (focus lost silently):**
```tsx
// When showPanel becomes false, the focused element unmounts and focus disappears
{showPanel && <PanelContent ref={panelRef} />}
```

**Focus on re-render — CORRECT (element stays in DOM):**
```tsx
// Element remains in DOM. Focus is preserved. Screen reader can still reach it if needed.
<PanelContent ref={panelRef} style={{ display: showPanel ? 'block' : 'none' }} />
```

**Focus return — INCORRECT (focus goes nowhere on close):**
```tsx
const Modal = ({ onClose }) => {
  return <dialog><button onClick={onClose}>Close</button></dialog>;
};
```

**Focus return — CORRECT (focus restored to trigger on all close paths):**
```tsx
const Modal = ({ onClose, triggerRef }) => {
  const handleClose = () => {
    onClose();
    triggerRef.current?.focus();
  };
  useEffect(() => {
    const onEscape = (e) => { if (e.key === 'Escape') handleClose(); };
    document.addEventListener('keydown', onEscape);
    return () => document.removeEventListener('keydown', onEscape);
  }, []);
  return <dialog><button onClick={handleClose}>Close</button></dialog>;
};
```

### Keyboard Requirements (P0)
- Every clickable element must respond to Enter (buttons) or Enter+Space (toggle/checkbox)
- Escape closes overlays (with `stopPropagation` in nested menus)
- Arrow keys navigate within composite widgets (tabs, menus, listboxes)
- Tab moves between widgets, not within them
- SPA views with repeated navigation should include a skip-to-content link — first focusable element, visually hidden until focused, targets main content area (WordPress: `#wpbody-content` or `#wpcontent`)

### Keyboard Shortcuts Declaration (P1)
When a component has keyboard shortcuts beyond standard navigation, declare them with `aria-keyshortcuts`:
- Format: modifier keys joined with `+` (e.g., `aria-keyshortcuts="Control+Shift+P"`)
- Only for shortcuts that are always available — not for conditional or contextual ones
- Supplements (does not replace) visible documentation of shortcuts

### Trigger-Popup Relationships (P1)
Every button that opens a popup needs:
- `aria-haspopup` with correct value (`"dialog"`, `"menu"`, `"listbox"`, `"true"`)
- `aria-expanded` dynamically reflecting open/closed state
- `aria-controls` referencing the popup element ID

### Live Region Announcements (P1)
- Every visual change a sighted user perceives must have a screen reader equivalent
- Use `speak(message, 'polite')` in WordPress, or `aria-live` regions
- Live region containers must be in the DOM BEFORE content is injected (never conditional-render the container itself)
- Debounce rapid announcements (500ms)

### IME Composition Handling (P1)
Ignore keyboard events during IME composition (CJK input). Wrap handlers with `isComposing` checks:
```tsx
if (event.nativeEvent.isComposing) return;
```

### Motion & Animation (P1)
- Wrap motion/animation in `prefers-reduced-motion` media query
- Provide a reduced or no-motion alternative that preserves meaning (not just `animation: none`)
- Auto-playing content must have a pause/stop control (WCAG 2.2.2)

```css
@media (prefers-reduced-motion: reduce) {
  .animated-element {
    animation-duration: 0.01ms !important;
    transition-duration: 0.01ms !important;
  }
}
```

### High Contrast & Forced Colors (P1)
- Test with Windows High Contrast Mode (`forced-colors: active` media query)
- Use `currentColor` for icon SVG fills so they adapt to forced-colors
- Communicate state with borders and outlines in addition to `background-color` — only borders and outlines survive forced-colors mode
- Custom focus indicators: use `outline` (visible in forced-colors) rather than `box-shadow` (removed by forced-colors)

```css
@media (forced-colors: active) {
  .custom-focus:focus-visible {
    outline: 2px solid ButtonText;
  }
}
```

### Decorative Content Rendering (P1)

**Screen readers announce `::before`/`::after` text content.** The W3C AccName spec requires browsers to include pseudo-element textual content in the accessible name computation. Assume all pseudo-element text content is visible to assistive technology.

**Choose rendering technique based on the content's role:**

```
Is the indicator decorative (meaning conveyed elsewhere via aria-label, visually-hidden text, etc.)?
├── YES → Does it need color theming (adapt to text color, dark mode, forced-colors)?
│   ├── YES → Inline SVG with aria-hidden="true", using currentColor
│   │         OR ::before/::after with content:"" + mask-image + background:currentColor
│   └── NO → ::before/::after with content:"" + background-image
│             OR inline SVG with aria-hidden="true"
└── NO (indicator conveys meaning) → Must be in the DOM
    └── Inline SVG with aria-hidden="true" + visually-hidden <span> with text equivalent
        OR visible text content
```

**Rules:**
1. **Use `content: ""` with visual styling (background-image, mask-image) for CSS pseudo-element icons.** Unicode characters like `\2197` (↗), `\25B6` (▶), `\2605` (★) in CSS `content` get announced by screen readers with their Unicode names ("North East Arrow"), which confuses users.
2. **For decorative icons needing color theming, use `mask-image`:**
   ```css
   .icon::after {
     content: "";
     display: inline-block;
     width: 1em;
     height: 1em;
     background: currentColor;
     mask-image: url("icon.svg");
     mask-size: contain;
   }
   ```
3. **Pseudo-element content is excluded from text selection and clipboard.** Use this property intentionally — decorative characters (arrows, bullets) in `::before`/`::after` won't pollute copied text, but meaningful content placed only in pseudo-elements can't be selected.
4. **Pseudo-element content is never translated** by browser translation tools (Google Translate). If text must be translatable, it must be a DOM text node.
5. **Pseudo-element content is invisible to DOM-walking scripts** (Twemoji, Grammarly, browser extensions, translation tools). This is an advantage for decorative indicators — they can't be mangled.
6. **When in doubt, use inline SVG with `aria-hidden="true"` plus a visually-hidden text span.** It is the most predictable, testable, and accessible approach.

### CSS-First for Presentational Concerns (P1)

Prefer CSS mechanisms over JavaScript runtime checks for presentational adaptations. CSS is declarative (no flash of wrong state), works without JS, and responds to dynamic changes automatically.

| Concern | CSS-first approach | JS only when needed for |
|---------|-------------------|------------------------|
| RTL/LTR layout | CSS Logical Properties (`margin-inline-start`, `inset-inline-end`) | Keyboard arrow key direction, tooltip positioning |
| RTL/LTR content | `:dir(rtl)` pseudo-class (matches inherited direction) | Setting `dir` attribute on locale change |
| Reduced motion | `@media (prefers-reduced-motion: reduce)` | JS-driven animations (Canvas, GSAP) |
| Dark mode | `@media (prefers-color-scheme: dark)` + custom properties | Persisted user preference from localStorage |
| Forced colors | `@media (forced-colors: active)` | N/A — always CSS |
| Responsive layout | Container queries, media queries | Reading dimensions for calculations |

**`:dir(rtl)` vs `[dir="rtl"]` vs `isRTL()`:** The `:dir()` pseudo-class matches elements based on *computed* directionality (inherited from ancestors, resolved from `dir="auto"`). The `[dir="rtl"]` attribute selector only matches elements with an *explicit* `dir` attribute. JS `isRTL()` is a single global check that can't handle mixed-directionality subtrees. Use `:dir()` for styling, logical properties for layout, and JS only for imperative logic (keyboard handlers, ARIA attributes, position calculations).

**Workaround smell:** When an implementation needs workaround classes, wrapper elements, or JS patches to prevent a platform feature from interfering (e.g., a class to prevent Twemoji from mangling an arrow character), that's a signal the rendering approach is fighting the platform. Step back and find an approach that aligns with the platform instead of working around it.

## Component Pattern Quick Reference

For each widget type, `${CLAUDE_SKILL_DIR}/references/component-patterns.md` has the full ARIA pattern, keyboard model, and focus behavior. Quick lookup:

| Widget | Key ARIA | Key Keyboard | Key Focus |
|--------|----------|-------------|-----------|
| Modal/Dialog | `role="dialog"` `aria-modal="true"` `aria-labelledby` | Escape closes; Tab trapped | Trap + return + on-mount |
| Combobox | `role="combobox"` `aria-expanded` `aria-activedescendant` | Arrow navigate, Enter select, Escape close | Virtual focus (stays on input) |
| Tabs | `role="tablist/tab/tabpanel"` `aria-selected` | Arrow keys between tabs | Roving tabindex |
| Menu/Dropdown | `role="menu/menuitem"` `aria-haspopup` `aria-expanded` | Arrow navigate, Escape close | First item on open |
| Select/Listbox | `role="listbox/option"` `aria-selected` | Arrow navigate, Enter select | Roving or activedescendant |
| Tooltip | `role="tooltip"` `aria-describedby` | Shows on focus+hover | No focus management |
| Slider | `role="slider"` `aria-valuemin/max/now/text` | Arrow +-1, Page +-10 | Standard |
| Toggle/Switch | `role="switch"` `aria-checked` | Space toggles | Standard |
| Alert/Notice | `role="alert"` or `role="status"` + `speak()` | N/A | No steal |

## Common Mistakes (From 450+ Gutenberg Bug Fixes)

| Mistake | Frequency | Fix |
|---------|-----------|-----|
| Focus lost on conditional re-render | 12 bugs | Keep elements in DOM or restore focus |
| Focus not returned from overlay | 8 bugs | Store trigger ref, restore in ALL close paths |
| Wrong ARIA attribute for role | 7 bugs | Validate states against role spec |
| Missing aria-haspopup/expanded | 6 bugs | Add to every popup trigger |
| Live region not announcing | 5 bugs | Use speak() + keep container always in DOM |
| Focus stolen on mount | 5 bugs | Check hasFocusWithin before .focus() |
| Non-semantic interactive element | 4 bugs | Use `<button>`, never `<div onClick>` |
| Escape not stopping propagation | 4 bugs | `stopPropagation()` in nested menus |
| Animation without prefers-reduced-motion | 3 bugs | Wrap in `@media (prefers-reduced-motion: reduce)` |

## When Building in WordPress/Gutenberg

Skip this section for non-WordPress projects. These utilities are Gutenberg-specific:

| Need | Use |
|------|-----|
| Focus trap | `useConstrainedTabbing()` from `@wordpress/compose` |
| Focus return | `useFocusReturn()` from `@wordpress/compose` |
| Focus on mount | `useFocusOnMount(mode)` from `@wordpress/compose` |
| Screen reader announcement | `speak(message, politeness)` from `@wordpress/a11y` |
| Find focusable elements | `focus.focusable.find(container)` from `@wordpress/dom` |
| Find tabbable elements | `focus.tabbable.find(container)` from `@wordpress/dom` |
| Visually hidden text | `<VisuallyHidden>` from `@wordpress/components` |
| Unique IDs | `useInstanceId(Component, prefix)` from `@wordpress/compose` |
| Complex widgets | Prefer Ariakit (Dialog, Combobox, Menu, Select, Tabs, Tooltip) |

**Platform hazard — Twemoji DOM walker:** WordPress ships Twemoji, which walks DOM text nodes via `MutationObserver` and replaces emoji/Unicode characters (including arrows ↗↖, symbols ©®, card suits, zodiac signs) with `<img>` tags. This affects **any Unicode character in a text node** that matches Twemoji's regex — not just emoji faces. Consequences:
- Arrow indicators (↗) in text nodes get replaced with images, breaking layout and styling
- The `wp-exclude-emoji` class (WordPress 6.2+) prevents replacement for an element and its descendants, but needing it is a **workaround smell**
- **Correct approach:** Render decorative symbols via CSS pseudo-elements (`::before`/`::after`) or inline SVG — Twemoji cannot see pseudo-element content or SVG elements, so no workaround is needed
- If you must use a text node, append U+FE0E (Variation Selector-15) after the character to force text presentation, though this has inconsistent results

## Detailed Research

For deep dives into specific areas, see:
- `${CLAUDE_SKILL_DIR}/../../../../.claude/docs/research/a11y/07-synthesized-rules.md` — Full rule set with all cross-references
- `${CLAUDE_SKILL_DIR}/../../../../.claude/docs/research/a11y/03-component-a11y-patterns.md` — 26 Gutenberg component analyses
- `${CLAUDE_SKILL_DIR}/../../../../.claude/docs/research/a11y/04-anti-patterns-from-bugs.md` — 15 anti-patterns with real commit diffs
