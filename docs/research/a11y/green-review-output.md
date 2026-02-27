# Accessibility Review: ConfirmDialog + NotificationSettings

**Component:** `ConfirmDialog` and `NotificationSettings`
**Review Date:** 2026-02-27
**Reviewer:** a11y-reviewer (green run)
**Standard:** WCAG 2.2 AA
**Agent:** `a11y-reviewer`

---

## Executive Summary

Both components — `ConfirmDialog` and `NotificationSettings` — have pervasive, layered accessibility failures that make them essentially unusable for keyboard-only and screen-reader users. The dialog is missing all structural ARIA semantics, focus management, keyboard handling, and uses non-interactive elements as interactive controls. The toggle switches have no role, no state, no name, and no keyboard access. The status announcement is invisible to assistive technology. These are not cosmetic issues; they represent complete functional exclusion of users who rely on assistive technology.

**Issue count by severity:**

| Severity | Count |
|----------|-------|
| P0 (Blocker — completely unusable for affected users) | 8 |
| P1 (Critical — major barrier, degraded/broken experience) | 9 |
| P2 (Moderate — noticeable friction, partial barrier) | 5 |

---

## Good Practices Observed

Before the findings, acknowledging what the code does right:

- The `ConfirmDialog` accepts a `confirmLabel` prop rather than hard-coding "OK", enabling caller-controlled labeling.
- The `isDangerous` prop changes the confirm button's visual appearance — the intent to communicate destructive action is present, even if the semantic expression is incomplete.
- The component correctly gates rendering with `if (!isOpen) return null`, which avoids hidden-but-focusable content in the DOM.
- `stopPropagation` is applied to the dialog container's `onClick`, showing awareness of event bubbling concerns.
- The `cancelLabel` prop with a sensible default shows awareness that button labels should be contextual.

---

## P0 Issues (Must Fix — WCAG A Violations)

### P0-1 — Dialog container has no ARIA role; screen readers cannot identify it as a dialog

**Anti-Pattern:** None in the AP table, but matches P0 checklist: "ARIA `role` matches actual interaction."
**WCAG:** 4.1.2 Name, Role, Value (Level A)
**Confidence:** 100

**Location:** `ConfirmDialog`, the inner `div.dialog-container`

```tsx
<div
  className="dialog-container"
  onClick={(e) => e.stopPropagation()}
  style={{ background: 'white', borderRadius: 8, padding: 24, ... }}
>
```

**Problem:** The dialog is rendered as a plain `<div>`. Assistive technology has no signal that this element is a modal dialog. Screen readers will not enter "dialog mode," will not trap the virtual cursor inside, and will not announce the dialog role or its label when it opens. A JAWS or NVDA user navigating with arrow keys will simply flow through this element as if it were static content.

**Fix:** Add `role="dialog"`, `aria-modal="true"`, and associate the dialog's title and description via `aria-labelledby` / `aria-describedby`. Add `tabIndex={-1}` to the container so it can receive programmatic focus on open.

```tsx
<div
  role="dialog"
  aria-modal="true"
  aria-labelledby="dialog-title"
  aria-describedby="dialog-description"
  tabIndex={-1}
  ref={dialogRef}
  className="dialog-container"
  ...
>
  <h2 id="dialog-title" style={{ margin: 0, fontSize: 18 }}>{title}</h2>
  <div id="dialog-description" className="dialog-body" ...>
    <p>{message}</p>
  </div>
```

**Effort:** 0.5 h

---

### P0-2 — Focus does not move into the dialog when it opens

**Anti-Pattern:** AP-02 (Missing focus return — same family: focus is not moved to the new surface)
**WCAG:** 2.4.3 Focus Order (Level A)
**Confidence:** 100

**Location:** `ConfirmDialog` — no `useEffect` moves focus on open. The `useRef` import exists but is never used, which is a strong signal this was an unfinished implementation.

```tsx
import React, { useState, useRef, useEffect } from 'react';
// useRef and useEffect are imported but never used in ConfirmDialog
```

**Problem:** When the dialog opens, keyboard focus stays on the element that triggered it (the toggle switch `<div>`, which itself is not focusable — so focus may already be on `<body>`). Keyboard users cannot Tab into the dialog because focus is not moved there. The dialog content is unreachable by keyboard.

**Fix:** Use the imported `useRef` and `useEffect` to move focus to the dialog container (or its first focusable child) when `isOpen` becomes true.

```tsx
const dialogRef = useRef<HTMLDivElement>(null);

useEffect(() => {
  if (isOpen && dialogRef.current) {
    dialogRef.current.focus();
  }
}, [isOpen]);

// In JSX, attach ref and tabIndex to the dialog container:
<div ref={dialogRef} role="dialog" aria-modal="true" tabIndex={-1} ...>
```

**Effort:** 0.5 h

---

### P0-3 — Focus is not returned to the trigger when the dialog closes

**Anti-Pattern:** AP-02 (Missing focus return — overlay closes without `.focus()` on trigger)
**WCAG:** 2.4.3 Focus Order (Level A)
**Confidence:** 100

**Location:** `ConfirmDialog` — `handleConfirm` / `handleCancel`; `NotificationSettings` — `handleConfirmDisable` / `handleCancelDisable`

**Problem:** When either dialog action fires, `isOpen` is set to `false` and the dialog unmounts. Focus is dropped to `<body>`. Keyboard users lose their position in the document entirely. The WCAG 2.4.3 expectation for modals is that focus returns to the element that triggered the overlay. Since the trigger is a non-focusable `<div>` (see P0-7), focus cannot even return to the right place — but this is a compounded failure that must be fixed in conjunction with P0-7.

**Fix:** Capture `document.activeElement` before opening the dialog (or keep a ref to the trigger element) and call `.focus()` on it in the close callbacks.

```tsx
// In NotificationSettings:
const triggerRef = useRef<HTMLElement | null>(null);

const handleToggle = (type: 'email' | 'push') => {
  // Capture focus before opening
  triggerRef.current = document.activeElement as HTMLElement;
  // ... existing logic
};

const handleCancelDisable = () => {
  setShowDisableConfirm(false);
  setPendingToggle(null);
  triggerRef.current?.focus();
};

const handleConfirmDisable = () => {
  // ... existing logic
  triggerRef.current?.focus();
};
```

**Effort:** 0.5 h

---

### P0-4 — No focus trap in dialog; Tab moves focus to background content

**Anti-Pattern:** None directly in AP table, but matches P0 checklist: "Modals have focus trapping and focus on mount."
**WCAG:** 2.1.2 No Keyboard Trap (Level A) — specifically, the inverse requirement: modal dialogs MUST confine focus
**Confidence:** 100

**Location:** `ConfirmDialog` — no `onKeyDown` handler, no focus-trap logic anywhere in the component

**Problem:** After focus enters the dialog (once P0-2 is fixed), pressing Tab will move focus past the dialog's last interactive element and into background page content. There is no mechanism to cycle focus within the dialog. Background content becomes operable while the modal is open, violating the modal contract. Combined with the missing background `inert` treatment (P1-1), the entire page is reachable by keyboard while the dialog is open.

**Fix:** Implement a focus trap that cycles focus among the dialog's focusable children on Tab/Shift+Tab.

```tsx
const FOCUSABLE_SELECTORS =
  'button, [href], input, select, textarea, [tabindex]:not([tabindex="-1"])';

const handleKeyDown = (e: React.KeyboardEvent<HTMLDivElement>) => {
  if (e.key === 'Escape') {
    handleCancel();
    return;
  }
  if (e.key === 'Tab') {
    const focusable = dialogRef.current?.querySelectorAll<HTMLElement>(FOCUSABLE_SELECTORS);
    if (!focusable || focusable.length === 0) return;
    const first = focusable[0];
    const last = focusable[focusable.length - 1];
    if (e.shiftKey && document.activeElement === first) {
      e.preventDefault();
      last.focus();
    } else if (!e.shiftKey && document.activeElement === last) {
      e.preventDefault();
      first.focus();
    }
  }
};

// In JSX:
<div ref={dialogRef} role="dialog" onKeyDown={handleKeyDown} ...>
```

Alternatively, use `focus-trap-react` or the native `<dialog>` element which provides this behavior natively.

**Effort:** 1 h (or 0.25 h with `focus-trap-react`)

---

### P0-5 — Escape key does not close the dialog

**Anti-Pattern:** None directly in AP table
**WCAG:** 2.1.1 Keyboard (Level A)
**Confidence:** 100

**Location:** `ConfirmDialog` — no `onKeyDown` handler anywhere in the component tree

**Problem:** The WAI-ARIA Authoring Practices for the dialog pattern require Escape to close the dialog. There is no keyboard event handler anywhere in `ConfirmDialog`. Keyboard users who open the dialog (once P0-2 is fixed) have no standard exit path — they would be trapped unless the focus trap cycles back to a Cancel button they can activate.

**Fix:** The `handleKeyDown` function in the P0-4 fix covers this with `if (e.key === 'Escape') handleCancel()`. Alternatively, use a `document.addEventListener` approach if the dialog container does not reliably capture keyboard events.

**Effort:** Included in P0-4 fix (0 additional hours)

---

### P0-6 — Action buttons and close control are `<div>`/`<span>` elements; keyboard users cannot reach or activate them

**Anti-Pattern:** AP-07 (`<div onClick={...}>` — Non-semantic interactive element)
**WCAG:** 2.1.1 Keyboard (Level A); 4.1.2 Name, Role, Value (Level A)
**Confidence:** 100

**Location:** `ConfirmDialog` — three interactive elements:

```tsx
// Cancel "button" — a <div>
<div
  onClick={handleCancel}
  style={{ padding: '8px 16px', border: '1px solid #ccc', cursor: 'pointer', ... }}
>
  {cancelLabel}
</div>

// Confirm "button" — a <div>
<div
  onClick={handleConfirm}
  style={{ padding: '8px 16px', background: isDangerous ? '#dc3545' : '#007bff', cursor: 'pointer', ... }}
>
  {confirmLabel}
</div>

// Close "button" — a <span>
<span
  onClick={handleCancel}
  style={{ cursor: 'pointer', fontSize: 24, color: '#666' }}
>
  ×
</span>
```

**Problem:** `<div>` and `<span>` elements are not in the tab order by default, have no implicit interactive role, and do not respond to Enter or Space key presses. Keyboard users cannot Tab to any of these three elements and cannot activate them. Screen readers announce them as generic containers or text nodes, not as buttons. These are the only action controls in the dialog — making the dialog completely inoperable without a mouse.

**Fix:** Replace all three with `<button type="button">`. The close button additionally needs an `aria-label` since its only visible content is the ambiguous `×` character.

```tsx
<button
  type="button"
  onClick={handleCancel}
  style={{ padding: '8px 16px', border: '1px solid #ccc', borderRadius: 4, cursor: 'pointer', background: 'white' }}
>
  {cancelLabel}
</button>

<button
  type="button"
  onClick={handleConfirm}
  style={{ padding: '8px 16px', border: 'none', borderRadius: 4, cursor: 'pointer', background: isDangerous ? '#dc3545' : '#007bff', color: 'white' }}
>
  {confirmLabel}
</button>

<button
  type="button"
  onClick={handleCancel}
  aria-label="Close dialog"
  style={{ cursor: 'pointer', fontSize: 24, color: '#666', background: 'none', border: 'none' }}
>
  <span aria-hidden="true">×</span>
</button>
```

**Effort:** 0.5 h

---

### P0-7 — Toggle switches have no role, no accessible name, and no keyboard access

**Anti-Pattern:** AP-07 (`<div onClick={...}>` — Non-semantic interactive element)
**WCAG:** 4.1.2 Name, Role, Value (Level A); 2.1.1 Keyboard (Level A)
**Confidence:** 100

**Location:** `NotificationSettings` — both toggle switch `<div>` elements

```tsx
<div
  onClick={() => handleToggle('email')}
  style={{
    width: 48, height: 24, borderRadius: 12,
    background: emailEnabled ? '#007bff' : '#ccc',
    cursor: 'pointer', position: 'relative',
  }}
>
  <div style={{ width: 20, height: 20, borderRadius: '50%', background: 'white', ... }} />
</div>
```

**Problem:** These are custom toggle switches with zero accessibility. They have:
- No `role` — screen readers announce them as nothing meaningful
- No `aria-checked` — the on/off state is conveyed by color alone (also a P1-4 issue)
- No accessible name — the "Email Notifications" label `<div>` above has no programmatic association
- No `tabIndex` — they are not in the tab order; keyboard users cannot reach them at all
- No `onKeyDown` — even if focused, Space/Enter does nothing

A screen reader user hears nothing. A keyboard user cannot interact with them. Both toggles are completely broken for AT users.

**Fix:** Apply `role="switch"`, `aria-checked`, `aria-labelledby`, `tabIndex={0}`, and `onKeyDown`. Associate the label text via `aria-labelledby` referencing the nearby label element's `id` (also required for P1-7).

```tsx
<div
  role="switch"
  aria-checked={emailEnabled}
  aria-labelledby="email-notifications-label"
  aria-describedby="email-notifications-desc"
  tabIndex={0}
  onClick={() => handleToggle('email')}
  onKeyDown={(e) => {
    if (e.key === ' ' || e.key === 'Enter') {
      e.preventDefault();
      handleToggle('email');
    }
  }}
  style={{
    width: 48, height: 24, borderRadius: 12,
    background: emailEnabled ? '#007bff' : '#ccc',
    cursor: 'pointer', position: 'relative',
  }}
>
  <div style={{ ... }} />
</div>
```

And assign `id` values to the label elements (see P1-7).

**Effort:** 0.5 h per toggle (1 h total)

---

### P0-8 — Status message is not announced by screen readers

**Anti-Pattern:** AP-06 (Visual change without `speak()` or `aria-live`)
**WCAG:** 4.1.3 Status Messages (Level AA)
**Confidence:** 100

**Location:** `NotificationSettings` — the `{status && (...)}` block

```tsx
{status && (
  <div style={{
    padding: '8px 12px',
    background: '#d4edda',
    color: '#155724',
    borderRadius: 4,
    marginBottom: 16,
  }}>
    {status}
  </div>
)}
```

**Problem:** When a toggle is changed, a status message like "email notifications disabled" or "push notifications enabled" appears visually. Screen readers are not notified. The element does not have `aria-live`, `role="status"`, or `role="alert"`. Worse, the element is conditionally rendered — it is absent from the DOM when `status` is empty, which means screen readers cannot register it as a live region before content is injected. Even if `aria-live` were added, the container being newly inserted into the DOM means the live region announcement may not fire in all AT/browser combinations (a well-documented behavior).

**Fix:** Keep the container always in the DOM and use `aria-live="polite"` with `role="status"`. Render it empty when there is no status, not absent.

```tsx
<div
  role="status"
  aria-live="polite"
  aria-atomic="true"
  style={{
    minHeight: 40,
    padding: status ? '8px 12px' : 0,
    background: status ? '#d4edda' : 'transparent',
    color: '#155724',
    borderRadius: 4,
    marginBottom: 16,
  }}
>
  {status}
</div>
```

**Effort:** 0.25 h

---

## P1 Issues (Should Fix — WCAG AA Violations)

### P1-1 — Background content not hidden from assistive technology when dialog is open

**WCAG:** 2.1.1 Keyboard (Level A) — inert content must be unreachable
**Confidence:** 85

**Location:** `ConfirmDialog` — `div.dialog-overlay`; the application root outside the dialog

**Problem:** There is no `aria-hidden="true"` or `inert` attribute applied to the rest of the page when the dialog is open. Screen reader users with virtual cursor can navigate freely into background content even while the dialog is the intended focus. Combined with the missing focus trap (P0-4), both keyboard and screen-reader users can escape the dialog context into the underlying page.

**Fix:** When the dialog opens, apply `inert` to the application's root container (excluding the dialog), or use a portal that places the dialog at the top level with `aria-hidden` on siblings.

```tsx
// In a useEffect within ConfirmDialog or NotificationSettings:
useEffect(() => {
  const root = document.getElementById('root');
  if (!root) return;
  if (isOpen) {
    root.setAttribute('inert', '');
  } else {
    root.removeAttribute('inert');
  }
  return () => root.removeAttribute('inert');
}, [isOpen]);
```

Note: The dialog itself must live outside the `inert` container — use a React portal if necessary.

**Effort:** 1 h

---

### P1-2 — Close button "×" has no accessible label

**WCAG:** 4.1.2 Name, Role, Value (Level A); 2.4.6 Headings and Labels (Level AA)
**Confidence:** 100

**Location:** `ConfirmDialog` — the `<span>` close control

```tsx
<span onClick={handleCancel} style={{ cursor: 'pointer', fontSize: 24, color: '#666' }}>
  ×
</span>
```

**Problem:** Even once converted to a `<button>` (P0-6), the visible content is only the Unicode multiplication sign `×` (U+00D7). Screen readers may announce this as "times," "multiply," "times sign," or simply the character — none of which convey the action of closing the dialog. There is no `aria-label`.

**Fix:** This is partially addressed in the P0-6 fix. Explicitly: add `aria-label="Close dialog"` and wrap the visible character in `<span aria-hidden="true">`.

```tsx
<button type="button" onClick={handleCancel} aria-label="Close dialog">
  <span aria-hidden="true">×</span>
</button>
```

**Effort:** Included in P0-6 fix (0 additional hours)

---

### P1-3 — Toggle button context missing: trigger lacks `aria-haspopup` and `aria-expanded`

**Anti-Pattern:** AP-04 (Button opening popup without `aria-haspopup`)
**WCAG:** 4.1.2 Name, Role, Value (Level A)
**Confidence:** 80

**Location:** `NotificationSettings` — the toggle switch `<div>` elements for email and push

**Problem:** Toggling a switch from enabled to disabled triggers a confirmation dialog. From an AT user's perspective, this toggle is not merely a switch — it conditionally opens a modal. The toggle element has neither `aria-haspopup="dialog"` (to signal that activating it may open a dialog) nor does any surrounding context inform the user that a confirmation step will appear. This is a WCAG 4.1.2 concern for predictable behavior. A screen reader user activating the "Email Notifications" switch expecting a simple toggle will instead be (once P0-2 is fixed) thrown into a dialog they were not warned about.

**Fix:** Because the behavior is conditional (toggle-on is immediate, toggle-off opens a dialog), `aria-haspopup` cannot be statically set without causing confusion for the enable path. The better solution is to describe the interaction in the toggle's `aria-describedby` content:

```tsx
<div id="email-notifications-desc" style={{ fontSize: 14, color: '#666' }}>
  Receive updates via email. Disabling will ask for confirmation.
</div>
```

Or use a separate "Disable" button that explicitly signals the destructive action, keeping the switch as a pure on/off indicator.

**Effort:** 0.5 h

---

### P1-4 — State of toggle switches conveyed by color alone

**Anti-Pattern:** None directly, but matches P1 checklist: "Color not sole state indicator."
**WCAG:** 1.4.1 Use of Color (Level A)
**Confidence:** 95

**Location:** `NotificationSettings` — both toggle switch divs

```tsx
background: emailEnabled ? '#007bff' : '#ccc',
```

**Problem:** The enabled/disabled state of each toggle is communicated only by background color: blue (`#007bff`) for on, gray (`#ccc`) for off. Users with deuteranopia, protanopia, or low vision may not distinguish these states. There is no text indicator (ON/OFF), no icon, and no pattern differentiation. The `aria-checked` fix in P0-7 resolves this for AT users, but sighted users with color blindness remain affected.

**Fix:** In addition to `aria-checked`, add a visible non-color indicator. Options include "ON"/"OFF" text inside the track, a checkmark icon, or a border-only off state with a filled on state.

```tsx
// Minimal text approach inside the track:
<div role="switch" aria-checked={emailEnabled} ...>
  <span
    style={{
      position: 'absolute',
      left: emailEnabled ? 4 : 28,
      top: 4,
      fontSize: 10,
      color: emailEnabled ? 'white' : '#666',
      lineHeight: '16px',
    }}
  >
    {emailEnabled ? 'ON' : 'OFF'}
  </span>
  <div style={{ /* thumb */ }} />
</div>
```

**Effort:** 0.5 h

---

### P1-5 — Toggle switches have no visible focus indicator

**WCAG:** 2.4.7 Focus Visible (Level AA); 2.4.11 Focus Appearance (WCAG 2.2 Level AA)
**Confidence:** 90

**Location:** `NotificationSettings` — both toggle `<div>` elements

**Problem:** Once `tabIndex={0}` is added (P0-7 fix), these elements will receive keyboard focus. However, the inline `style` objects apply no `:focus` or `:focus-visible` ring. Many browser/CSS reset combinations suppress the default outline on `<div>` elements. A keyboard user tabbing to the toggle will see no visual focus indicator, violating WCAG 2.4.7 (Focus Visible) and 2.4.11 (Focus Appearance, WCAG 2.2 AA).

**Fix:** Add explicit focus styles. With inline styles this requires `onFocus`/`onBlur` state, but the recommended approach is a CSS class:

```css
.toggle-switch:focus-visible {
  outline: 2px solid #005fcc;
  outline-offset: 3px;
  border-radius: 12px;
}
```

Or inline via a `focusVisible` state variable:

```tsx
const [isFocused, setIsFocused] = useState(false);
// style={{ ..., outline: isFocused ? '2px solid #005fcc' : 'none', outlineOffset: 3 }}
// onFocus={() => setIsFocused(true)}, onBlur={() => setIsFocused(false)}
```

**Effort:** 0.25 h per toggle (0.5 h total)

---

### P1-6 — Notification labels are not programmatically associated with their toggle controls

**WCAG:** 1.3.1 Info and Relationships (Level A); 2.4.6 Headings and Labels (Level AA)
**Confidence:** 100

**Location:** `NotificationSettings` — label `<div>` and description `<div>` pairs alongside each toggle

```tsx
<div>
  <div style={{ fontWeight: 600 }}>Email Notifications</div>
  <div style={{ fontSize: 14, color: '#666' }}>Receive updates via email</div>
</div>
// ... gap in DOM ...
<div onClick={() => handleToggle('email')} style={{ ... }}>
```

**Problem:** The text "Email Notifications" and "Receive updates via email" are plain `<div>` elements with no `id`. They have no programmatic relationship to the adjacent toggle control. Screen readers will not associate this text as the label or description for the toggle. After the P0-7 fix adds `aria-labelledby` and `aria-describedby` to the toggle, there are still no corresponding `id` attributes on the label elements for those attributes to reference.

**Fix:** Add `id` attributes to the label and description elements:

```tsx
<div id="email-notifications-label" style={{ fontWeight: 600 }}>Email Notifications</div>
<div id="email-notifications-desc" style={{ fontSize: 14, color: '#666' }}>Receive updates via email</div>
```

And reference them in the toggle (as shown in the P0-7 fix):

```tsx
<div
  role="switch"
  aria-labelledby="email-notifications-label"
  aria-describedby="email-notifications-desc"
  ...
>
```

Repeat for the push notifications toggle with `id="push-notifications-label"` and `id="push-notifications-desc"`.

**Effort:** 0.25 h

---

### P1-7 — `isClosing` state is unused in rendering; buttons are not disabled during close animation

**WCAG:** 4.1.2 Name, Role, Value (Level A) — incomplete state disclosure; also 2.2.1 Timing Adjustable
**Confidence:** 80

**Location:** `ConfirmDialog` — `isClosing` state and `handleConfirm` / `handleCancel`

```tsx
const [isClosing, setIsClosing] = useState(false);

const handleConfirm = () => {
  setIsClosing(true);
  setTimeout(() => {
    onConfirm();
    setIsClosing(false);
  }, 200);
};
```

**Problem:** `isClosing` is set to `true` but is never referenced in the JSX — no `disabled` attribute, no `aria-disabled`, no `aria-busy`, no visual change. During the 200 ms timeout window, the dialog appears fully interactive. A keyboard or switch-access user who rapidly triggers actions a second time can cause a race condition where `onConfirm` fires twice or `isClosing` flips back to `false` while still inside the first timeout. AT users receive no indication that an action is in progress.

**Fix:** Use `isClosing` to disable the action buttons and signal the busy state:

```tsx
<button
  type="button"
  onClick={handleConfirm}
  disabled={isClosing}
  aria-disabled={isClosing}
>
  {isClosing ? 'Processing...' : confirmLabel}
</button>
```

Or eliminate the timeout entirely and use CSS transitions on the dialog element itself.

**Effort:** 0.25 h

---

### P1-8 — Heading hierarchy is ambiguous; notification item labels are unsemantic

**WCAG:** 1.3.1 Info and Relationships (Level A)
**Confidence:** 75

**Location:** `NotificationSettings` uses `<h1>`; `ConfirmDialog` uses `<h2>`.

```tsx
// NotificationSettings
<h1 style={{ fontSize: 24 }}>Notification Settings</h1>

// ConfirmDialog (rendered inside NotificationSettings)
<h2 style={{ margin: 0, fontSize: 18 }}>{title}</h2>
```

**Problem:** `<h1>` is appropriate only for the top-level page heading. This component is exported as `NotificationSettings` — in a real application it will almost certainly be embedded within a page that already has an `<h1>`, creating duplicate `<h1>` elements. The `<h2>` inside the dialog is structurally correct relative to the `<h1>`, but if the parent page uses `<h2>` for sections, the dialog's `<h2>` may create a confusing heading tree. Additionally, "Email Notifications" and "Push Notifications" are represented as styled `<div>` elements rather than semantic text — losing their relationship to the controls as labels.

**Fix:** The component should either accept a `headingLevel` prop, or default to `<h2>` assuming it will be embedded. The notification type names ("Email Notifications", "Push Notifications") should function as labels for their respective switches (addressed in P1-6), not as standalone headings, which is the correct semantic treatment.

**Effort:** 0.5 h

---

### P1-9 — Overlay backdrop is a focusable-adjacent click target with no ARIA role

**WCAG:** 4.1.2 Name, Role, Value (Level A)
**Confidence:** 75

**Location:** `ConfirmDialog` — `div.dialog-overlay`

```tsx
<div className="dialog-overlay" onClick={handleCancel}>
```

**Problem:** The overlay `<div>` has an `onClick` handler that dismisses the dialog. This element has no `role`, no `aria-label`, and is not in the tab order — so keyboard users cannot reach it. This is intentional for the dismiss pattern, but the element is still present in the AT's accessibility tree as a generic unnamed interactive-ish element (some AT may announce it as a clickable region). It should be explicitly hidden from AT since it carries no meaningful information — the Escape key and Cancel button are the accessible dismiss mechanisms.

**Fix:** Add `aria-hidden="true"` to the overlay div to remove it from the accessibility tree:

```tsx
<div className="dialog-overlay" aria-hidden="true" onClick={handleCancel}>
```

Note: Ensure the dialog container (inside the overlay) is NOT a descendant of the `aria-hidden` element — use a React portal to render the dialog as a sibling, or restructure so the `aria-hidden` applies only to the overlay backdrop layer.

**Effort:** 0.5 h

---

## P2 Issues (Nice to Fix — Enhancements)

### P2-1 — Color contrast: disabled toggle thumb on off-state track may fail 3:1 UI component ratio

**WCAG:** 1.4.3 Contrast (Minimum) (Level AA); 1.4.11 Non-text Contrast (Level AA)
**Confidence:** 70

**Location:** `NotificationSettings` — disabled toggle `background: '#ccc'`; thumb `background: 'white'`

**Problem:** When a toggle is off, the track is `#ccc` (gray) and the thumb is `white`. The contrast ratio of white on `#ccc` is approximately 1.6:1. WCAG 1.4.11 requires a 3:1 contrast ratio for UI component boundaries against adjacent colors. The white thumb on a gray track does not meet this threshold. Additionally, the dialog body text uses `color: '#333'` — this passes (approximately 12.6:1 on white), but the secondary description text uses `color: '#666'` at 14px normal weight — approximately 5.74:1, which passes AA but is worth monitoring.

**Fix:** Use a darker off-state track color. `#767676` provides exactly 4.54:1 against white, meeting the 3:1 UI component threshold with headroom.

```tsx
background: emailEnabled ? '#007bff' : '#767676',
```

Or add a visible border to the thumb in the off state to create the required boundary contrast without relying on the track color alone.

**Effort:** 0.25 h

---

### P2-2 — Toggle control target size is below WCAG 2.2 minimum (height: 24px)

**WCAG:** 2.5.8 Target Size (Minimum) (Level AA, WCAG 2.2)
**Confidence:** 80

**Location:** `NotificationSettings` — both toggle `<div>` elements; `style={{ width: 48, height: 24 }}`

**Problem:** WCAG 2.2 SC 2.5.8 requires interactive target sizes of at least 24×24 CSS pixels (with spacing considerations for smaller targets). The toggle switch is 48px wide but only 24px tall — exactly at the minimum boundary with no margin for spacing exceptions. The close button (`<span>` with `fontSize: 24`) has no explicit width/height and its actual hit target may be smaller than 24×24px depending on browser rendering.

**Fix:** Increase the toggle height to at least 28px (or use padding to increase the effective touch target without changing the visual design):

```tsx
style={{
  width: 48,
  height: 28,  // was 24
  borderRadius: 14,
  padding: 4,  // adds breathing room for the thumb
  ...
}}
```

For the close button, add explicit padding after converting to `<button>`:

```tsx
<button type="button" style={{ padding: '8px', minWidth: 44, minHeight: 44 }} ...>
```

**Effort:** 0.25 h

---

### P2-3 — `pendingToggle` can be `null` when dialog renders, producing "null notifications" message

**WCAG:** 3.1 Readable — the content becomes unreadable; also a robustness concern
**Confidence:** 85

**Location:** `NotificationSettings` — `message` prop passed to `ConfirmDialog`

```tsx
message={`Are you sure you want to disable ${pendingToggle} notifications? You won't receive any ${pendingToggle} alerts until you re-enable them.`}
```

**Problem:** `pendingToggle` has TypeScript type `'email' | 'push' | null`. If the dialog ever renders when `pendingToggle` is `null` (e.g., a state update timing edge case, or if the dialog is opened without setting `pendingToggle` first), the message reads: "Are you sure you want to disable null notifications? You won't receive any null alerts until you re-enable them." Screen readers will literally announce the word "null." This is a readability and comprehension failure.

**Fix:** Guard the interpolation or assert non-null before rendering:

```tsx
message={
  pendingToggle
    ? `Are you sure you want to disable ${pendingToggle} notifications? You won't receive any ${pendingToggle} alerts until you re-enable them.`
    : 'Are you sure you want to disable these notifications?'
}
```

**Effort:** 0.25 h

---

### P2-4 — Status message persists indefinitely; no auto-clear or dismiss mechanism

**WCAG:** 3.3.4 Error Prevention (Level AA) — not a direct violation, but a usability concern with AT implications
**Confidence:** 65

**Location:** `NotificationSettings` — `status` state, never reset

**Problem:** The `status` state is set on toggle changes but never cleared. After a user disables email notifications, the status message "email notifications disabled" remains visible indefinitely. If the user then enables them, a new status message replaces it. There is no visual indication that the message is temporary, no dismiss button, and no auto-clear timeout. Screen reader users relying on the live region (once P0-8 is fixed) will not receive a second announcement if the same status is set again (React does not re-announce identical text in live regions).

**Fix:** Clear the status after a configurable duration (5 seconds is a common baseline) and ensure the live region content changes when the same action is repeated (e.g., append a counter or use a key to force a DOM update):

```tsx
const setStatusWithTimeout = (msg: string) => {
  setStatus(msg);
  setTimeout(() => setStatus(''), 5000);
};
```

**Effort:** 0.25 h

---

### P2-5 — Toggle switches should use `role="switch"` semantically matched to the interaction

**WCAG:** (enhancement, not a violation if fixed per P0-7 with `role="switch"`)
**Confidence:** 65

**Location:** `NotificationSettings` — toggle switches

**Problem:** The P0-7 fix recommends `role="switch"`, which is correct per WAI-ARIA 1.2 and the P2 checklist: "Toggle components use `role='switch'` (not just checkbox)." This P2 item documents that `role="checkbox"` would be an incorrect alternative — the interaction model is a switch (binary state that takes immediate effect), not a checkbox (state that may be submitted in a form). This distinction matters for screen readers: some announce "switch on"/"switch off" for `role="switch"` versus "checked"/"not checked" for `role="checkbox"`.

**Fix:** Ensure the P0-7 fix uses `role="switch"` specifically (not `role="checkbox"` or `role="button"`), and that `aria-checked` is used (not `aria-pressed`, which applies to `role="button"`).

**Effort:** Included in P0-7 fix (documentation only)

---

## Anti-Pattern Detection Summary

| Anti-Pattern | ID | Found | Location |
|---|---|---|---|
| `{condition && <Component />}` near interactive elements | AP-01 | Yes | `{status && <div>}` — status live region conditionally rendered |
| Modal/Popover `onClose` without `.focus()` on trigger | AP-02 | Yes | `handleCancelDisable`, `handleConfirmDisable` — no focus return |
| `aria-checked` on wrong role | AP-03 | N/A | No existing ARIA states to check |
| `<button>` opening popup without `aria-haspopup` | AP-04 | Partial | Toggles open dialog with no prior warning |
| `<Button disabled>` without accessible equivalent | AP-05 | N/A | No disabled buttons present |
| Visual change without `speak()` or `aria-live` | AP-06 | Yes | Status message div; toggle state changes |
| `<div onClick={...}>` or `<span onClick={...}>` | AP-07 | Yes | All 5 interactive elements in the component |
| `Escape` handler without `stopPropagation()` in nested overlay | AP-08 | N/A | No Escape handler exists at all |
| `.focus()` in `useEffect` without `contains()` check | AP-09 | N/A | No `.focus()` calls exist |
| `outline: none` without replacement | — | No | Not present |
| Live region container inside conditional render | — | Yes | `{status && <div>}` — the container is conditionally rendered |

---

## Consolidated Fix Priority

| Priority | Issue IDs | Core Problem | Effort Estimate |
|----------|-----------|--------------|-----------------|
| P0 — Fix first | P0-1, P0-2, P0-3, P0-4, P0-5 | Dialog has no role, no focus management, no keyboard handling | 3 h |
| P0 — Fix first | P0-6 | All interactive elements are `<div>`/`<span>` | 0.5 h |
| P0 — Fix first | P0-7 | Toggle switches are completely inaccessible | 1 h |
| P0 — Fix first | P0-8 | Status messages invisible to screen readers | 0.25 h |
| P1 — Fix next | P1-1 | Background content reachable by AT during modal | 1 h |
| P1 — Fix next | P1-2 | Close button has no accessible name | Included in P0-6 |
| P1 — Fix next | P1-3 | Toggle doesn't warn about dialog trigger | 0.5 h |
| P1 — Fix next | P1-4 | Toggle state conveyed by color alone | 0.5 h |
| P1 — Fix next | P1-5 | No focus indicator on toggles | 0.5 h |
| P1 — Fix next | P1-6 | Labels not associated with toggle controls | 0.25 h |
| P1 — Fix next | P1-7 | `isClosing` not reflected in button state | 0.25 h |
| P1 — Fix next | P1-8 | Heading hierarchy ambiguous | 0.5 h |
| P1 — Fix next | P1-9 | Overlay backdrop in AT accessibility tree | 0.5 h |
| P2 — Fix last | P2-1 through P2-5 | Contrast, target size, edge cases | 1 h |

**Total estimated effort:** ~10 h for all issues. The P0 fixes alone (4.75 h) would bring the components from completely non-functional to minimally accessible.

---

## Recommended Structural Refactor

Rather than patching the existing implementation incrementally, the `ConfirmDialog` should be rebuilt using the native HTML `<dialog>` element. Browser support is now excellent (Chrome 37+, Firefox 98+, Safari 15.4+).

The `<dialog>` element provides natively:
- The `dialog` ARIA role without manual annotation
- Focus trapping within the dialog
- Escape key dismissal
- `showModal()` / `close()` API that handles focus return automatically
- `::backdrop` pseudo-element for the overlay without a wrapper div

```tsx
const dialogRef = useRef<HTMLDialogElement>(null);
const triggerRef = useRef<HTMLElement | null>(null);

useEffect(() => {
  if (isOpen) {
    triggerRef.current = document.activeElement as HTMLElement;
    dialogRef.current?.showModal();
  } else {
    dialogRef.current?.close();
    triggerRef.current?.focus();
  }
}, [isOpen]);

return (
  <dialog
    ref={dialogRef}
    aria-labelledby="dialog-title"
    aria-describedby="dialog-description"
    style={{ borderRadius: 8, padding: 24, maxWidth: 480, border: 'none' }}
  >
    <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 16 }}>
      <h2 id="dialog-title" style={{ margin: 0, fontSize: 18 }}>{title}</h2>
      <button type="button" onClick={handleCancel} aria-label="Close dialog">
        <span aria-hidden="true">×</span>
      </button>
    </div>
    <div id="dialog-description" style={{ marginBottom: 24, color: '#333', lineHeight: 1.6 }}>
      <p>{message}</p>
    </div>
    <div style={{ display: 'flex', justifyContent: 'flex-end', gap: 12 }}>
      <button type="button" onClick={handleCancel}>{cancelLabel}</button>
      <button type="button" onClick={handleConfirm}
        style={{ background: isDangerous ? '#dc3545' : '#007bff', color: 'white' }}>
        {confirmLabel}
      </button>
    </div>
  </dialog>
);
```

For the toggle switches, use a visually-hidden `<input type="checkbox">` with `role="switch"` — this delegates keyboard handling and state to the browser and eliminates the need for manual `onKeyDown`, `tabIndex`, and `aria-checked` management:

```tsx
<label htmlFor="email-toggle" style={{ fontWeight: 600 }}>Email Notifications</label>
<input
  id="email-toggle"
  type="checkbox"
  role="switch"
  checked={emailEnabled}
  onChange={() => handleToggle('email')}
  style={{ /* visually-hidden but not display:none */ }}
/>
<div aria-hidden="true" style={{ /* visual toggle track */ }}>
  <div style={{ /* visual thumb */ }} />
</div>
```
