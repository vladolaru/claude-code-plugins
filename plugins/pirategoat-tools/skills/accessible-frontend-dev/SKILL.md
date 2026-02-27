---
name: accessible-frontend-dev
description: Use when writing or modifying frontend React/HTML components, creating interactive widgets, building forms, or implementing overlays/modals/dropdowns — ensures accessible output by default with correct ARIA, keyboard, focus, and screen reader support
---

# Accessible Frontend Development

Write accessible frontend code by default. Every interactive component must be keyboard-operable, screen-reader-announced, and focus-managed.

## Decision Trees (Use BEFORE Writing Code)

### Should I use ARIA or semantic HTML?

```
Native HTML element exists for this semantic?
├── YES → Use it. No ARIA needed.
│   └── Needs additional state? → Add aria-* state attributes only
└── NO → Use ARIA roles + required states
    └── Follow component pattern in references/component-patterns.md
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
└── NO → HTML disabled or display:none
```

## Universal Rules (Apply to ALL Components)

### Semantic HTML First (P0)
Use `<button>` for actions, `<a href>` for navigation. **Never** `<div role="button" tabIndex={0} onClick={...}>`. Native elements provide keyboard behavior and AT semantics for free.

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

5. **Never suppress focus indicators:** No `outline: none` without a visible replacement (`outline`, `box-shadow`, or `border`).

### Keyboard Requirements (P0)
- Every clickable element must respond to Enter (buttons) or Enter+Space (toggle/checkbox)
- Escape closes overlays (with `stopPropagation` in nested menus)
- Arrow keys navigate within composite widgets (tabs, menus, listboxes)
- Tab moves between widgets, not within them

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

## Component Pattern Quick Reference

For each widget type, `references/component-patterns.md` has the full ARIA pattern, keyboard model, and focus behavior. Quick lookup:

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

## Gutenberg Infrastructure Reference

When building in WordPress/Gutenberg context:

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

## Detailed Research

For deep dives into specific areas, see:
- `docs/research/a11y/07-synthesized-rules.md` — Full rule set with all cross-references
- `docs/research/a11y/03-component-a11y-patterns.md` — 26 Gutenberg component analyses
- `docs/research/a11y/04-anti-patterns-from-bugs.md` — 15 anti-patterns with real commit diffs
