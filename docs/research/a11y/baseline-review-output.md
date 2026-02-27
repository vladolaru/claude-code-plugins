# Accessibility Review: ConfirmDialog + NotificationSettings

**Component:** `ConfirmDialog` and `NotificationSettings`
**Review Date:** 2026-02-27
**Reviewer:** Baseline (Claude Sonnet 4.6)
**Standard:** WCAG 2.2 AA

---

## Summary

The component pair has pervasive, layered accessibility failures across every WCAG principle (Perceivable, Operable, Understandable, Robust). The `ConfirmDialog` is essentially unusable for keyboard and screen-reader users. The toggle switches in `NotificationSettings` are custom-built without any accessible semantics. Many of the issues are P0 — a keyboard-only user cannot operate the dialog at all, and a screen-reader user receives no information about the dialog's role, state, or content structure.

**Issue count by severity:**

| Severity | Count |
|----------|-------|
| P0 (Blocker — completely unusable for affected users) | 8 |
| P1 (Critical — major barrier, degraded/broken experience) | 10 |
| P2 (Moderate — noticeable friction, partial barrier) | 6 |

---

## P0 Issues

### P0-1 — Dialog has no ARIA role; screen readers cannot identify it as a dialog

**WCAG:** 4.1.2 Name, Role, Value (Level A)

**Location:** `ConfirmDialog`, the inner `div.dialog-container`

```tsx
<div
  className="dialog-container"
  onClick={(e) => e.stopPropagation()}
  style={{ ... }}
>
```

**Problem:** The container is a plain `<div>`. Screen readers have no way to know this is a modal dialog. AT (assistive technology) will not switch into "dialog mode", will not trap virtual cursor inside the container, and will not announce the dialog when it opens.

**Fix:** Use `role="dialog"` with `aria-modal="true"`, and pair it with `aria-labelledby` pointing to the heading's `id`.

```tsx
<div
  role="dialog"
  aria-modal="true"
  aria-labelledby="dialog-title"
  aria-describedby="dialog-description"
  className="dialog-container"
  ...
>
  <h2 id="dialog-title" ...>{title}</h2>
  <div id="dialog-description" ...><p>{message}</p></div>
```

---

### P0-2 — Focus is not moved into the dialog when it opens

**WCAG:** 2.4.3 Focus Order (Level A); also critical for 3.2.2 On Input

**Location:** `ConfirmDialog` — no `useRef`/`useEffect` focus management

**Problem:** When the dialog opens, keyboard focus stays on whatever triggered it (or the body). Keyboard users cannot reach dialog content. The `useRef` import is present but never used, confirming this was an oversight.

**Fix:** Add a ref to the dialog container and focus it (or the first focusable element) when `isOpen` becomes true.

```tsx
const dialogRef = useRef<HTMLDivElement>(null);

useEffect(() => {
  if (isOpen && dialogRef.current) {
    dialogRef.current.focus();
  }
}, [isOpen]);

// In JSX:
<div ref={dialogRef} role="dialog" aria-modal="true" tabIndex={-1} ...>
```

---

### P0-3 — Focus is not returned to the trigger element when the dialog closes

**WCAG:** 2.4.3 Focus Order (Level A)

**Location:** `ConfirmDialog` — `handleConfirm` / `handleCancel`

**Problem:** When the dialog closes, focus is dropped to the browser's default (usually `<body>`). Keyboard users lose their place in the page entirely.

**Fix:** Capture the triggering element before opening the dialog and restore focus on close.

```tsx
// In NotificationSettings:
const triggerRef = useRef<HTMLElement | null>(null);

const handleToggle = (type: 'email' | 'push') => {
  triggerRef.current = document.activeElement as HTMLElement;
  // ... rest of logic
};

const handleCancelDisable = () => {
  setShowDisableConfirm(false);
  setPendingToggle(null);
  triggerRef.current?.focus();
};
```

---

### P0-4 — Focus is not trapped inside the dialog; Tab exits into background content

**WCAG:** 2.1.2 No Keyboard Trap (Level A) — specifically the inverse: focus must be confined while a modal is open

**Location:** `ConfirmDialog` — no focus trap implementation

**Problem:** Pressing Tab inside the dialog will move focus to elements behind the overlay. There is no `onKeyDown` handler and no focus-trap logic. Background content becomes operable while the modal is open, which violates the modal contract and can confuse both keyboard and screen-reader users.

**Fix:** Implement a focus trap. At minimum, intercept `keydown` on the dialog and cycle focus among its focusable children. In production, use a proven library such as `focus-trap-react` or the `<dialog>` element (which provides native focus trapping in supporting browsers).

```tsx
const FOCUSABLE = 'button, [href], input, select, textarea, [tabindex]:not([tabindex="-1"])';

const trapFocus = (e: React.KeyboardEvent) => {
  const focusable = dialogRef.current?.querySelectorAll<HTMLElement>(FOCUSABLE);
  if (!focusable || focusable.length === 0) return;
  const first = focusable[0];
  const last = focusable[focusable.length - 1];
  if (e.key === 'Tab') {
    if (e.shiftKey && document.activeElement === first) {
      e.preventDefault();
      last.focus();
    } else if (!e.shiftKey && document.activeElement === last) {
      e.preventDefault();
      first.focus();
    }
  }
};
```

---

### P0-5 — Escape key does not close the dialog

**WCAG:** 2.1.1 Keyboard (Level A)

**Location:** `ConfirmDialog` — no `onKeyDown` handler anywhere on the dialog or overlay

**Problem:** Standard dialog/modal pattern requires Escape to dismiss. Without it, keyboard users have no standard exit path. This is one of the most commonly expected keyboard interactions for any modal.

**Fix:** Add a `keydown` listener on the dialog container (or `document`) and call `handleCancel` when `key === 'Escape'`.

```tsx
useEffect(() => {
  if (!isOpen) return;
  const handleKeyDown = (e: KeyboardEvent) => {
    if (e.key === 'Escape') handleCancel();
  };
  document.addEventListener('keydown', handleKeyDown);
  return () => document.removeEventListener('keydown', handleKeyDown);
}, [isOpen]);
```

---

### P0-6 — Buttons are `<div>` elements; completely inaccessible to keyboard users

**WCAG:** 2.1.1 Keyboard (Level A); 4.1.2 Name, Role, Value (Level A)

**Location:** `ConfirmDialog` — Cancel and Confirm "buttons" in `dialog-footer`; also the close "×" span

```tsx
<div onClick={handleCancel} style={{ cursor: 'pointer', ... }}>
  {cancelLabel}
</div>
<div onClick={handleConfirm} style={{ cursor: 'pointer', ... }}>
  {confirmLabel}
</div>
<span onClick={handleCancel} style={{ cursor: 'pointer', ... }}>
  ×
</span>
```

**Problem:** `<div>` and `<span>` elements are not in the tab order. They have no implicit role. They do not respond to Enter or Space. Keyboard users cannot activate them. Screen readers announce them as generic elements, not buttons. All three interactive elements are broken.

**Fix:** Replace all three with `<button>` elements.

```tsx
<button type="button" onClick={handleCancel}>{cancelLabel}</button>
<button type="button" onClick={handleConfirm}>{confirmLabel}</button>
<button type="button" onClick={handleCancel} aria-label="Close dialog">×</button>
```

---

### P0-7 — Toggle switches have no semantic role, no accessible name, and no state

**WCAG:** 4.1.2 Name, Role, Value (Level A)

**Location:** `NotificationSettings` — both toggle switch `<div>` elements

```tsx
<div
  onClick={() => handleToggle('email')}
  style={{ width: 48, height: 24, borderRadius: 12, background: emailEnabled ? '#007bff' : '#ccc', ... }}
>
  <div style={{ ... /* thumb */ }} />
</div>
```

**Problem:** These are custom toggle switches built entirely from `<div>` elements. They have:
- No `role` (not announced as a switch/checkbox/button)
- No `aria-checked` (state — on/off — is invisible to AT)
- No accessible name (screen readers have nothing to announce)
- No keyboard access (not in tab order, no key handlers)

A screen reader user hears nothing. A keyboard user cannot reach them. The on/off state is conveyed purely through background color, which also fails WCAG 1.3.3 (Sensory Characteristics) and 1.4.1 (Use of Color).

**Fix:** Use `role="switch"` with `aria-checked` and `aria-label`, make it focusable, and handle keyboard events.

```tsx
<div
  role="switch"
  aria-checked={emailEnabled}
  aria-label="Email notifications"
  tabIndex={0}
  onClick={() => handleToggle('email')}
  onKeyDown={(e) => {
    if (e.key === 'Enter' || e.key === ' ') {
      e.preventDefault();
      handleToggle('email');
    }
  }}
  style={{ ... }}
>
```

Alternatively, use a visually hidden `<input type="checkbox">` with `role="switch"` and style only the visual thumb.

---

### P0-8 — Status message is not announced to screen readers

**WCAG:** 4.1.3 Status Messages (Level AA)

**Location:** `NotificationSettings` — the `status` div

```tsx
{status && (
  <div style={{ padding: '8px 12px', background: '#d4edda', color: '#155724', ... }}>
    {status}
  </div>
)}
```

**Problem:** When the status message appears (e.g., "email notifications disabled"), it appears visually but is not announced by screen readers. The element is rendered into the DOM without a live region, so AT does not know to announce it.

**Fix:** Add `role="status"` and `aria-live="polite"`. Keep the container in the DOM at all times (render it empty rather than conditionally) so screen readers register the live region before content is injected.

```tsx
<div
  role="status"
  aria-live="polite"
  aria-atomic="true"
  style={{ minHeight: 40, ... }}
>
  {status}
</div>
```

---

## P1 Issues

### P1-1 — Dialog overlay has no accessible backdrop; background content is not inert

**WCAG:** 2.1.1 Keyboard (Level A)

**Location:** `ConfirmDialog` — `div.dialog-overlay`

**Problem:** There is no `aria-hidden="true"` on background content, no `inert` attribute on the rest of the app, and no mechanism to prevent screen-reader virtual cursor from wandering into background content when the dialog is open. Combined with the missing focus trap (P0-4), AT users can fully interact with the page behind the dialog.

**Fix:** When the dialog is open, apply `aria-hidden="true"` to all sibling DOM nodes outside the dialog, or use the HTML `inert` attribute on background containers.

```tsx
// When dialog opens, on the root app element:
document.getElementById('root')?.setAttribute('inert', '');
// When dialog closes:
document.getElementById('root')?.removeAttribute('inert');
```

---

### P1-2 — Close button "×" has no accessible label

**WCAG:** 2.4.6 Headings and Labels (Level AA); 4.1.2 Name, Role, Value (Level A)

**Location:** `ConfirmDialog` — the `<span>` close button

```tsx
<span onClick={handleCancel} style={{ cursor: 'pointer', fontSize: 24, color: '#666' }}>
  ×
</span>
```

**Problem:** Even if this were a `<button>`, the visible content is only the Unicode multiply sign `×`. Screen readers may announce "times", "multiply", or simply skip it. There is no `aria-label` to convey the action ("Close dialog").

**Fix:**

```tsx
<button type="button" onClick={handleCancel} aria-label="Close dialog">
  <span aria-hidden="true">×</span>
</button>
```

---

### P1-3 — The cancel action's label ("Cancel") is not descriptive enough in dialog context

**WCAG:** 2.4.6 Headings and Labels (Level AA)

**Location:** `ConfirmDialog` — cancel button, when used from `NotificationSettings`

**Problem:** The default `cancelLabel` is "Cancel". In the `NotificationSettings` usage, the confirm button is "Disable". A screen reader reads buttons out of context in Forms/Applications mode. "Cancel" and "Disable" without the dialog heading association can be ambiguous, but more importantly, the component provides a `cancelLabel` prop — the `NotificationSettings` usage never passes one, leaving the default "Cancel" with no paired description of what action is being cancelled.

**Fix:** The `NotificationSettings` call site should pass `cancelLabel="Keep enabled"` (or similar) to make the destructive choice explicit. At the component level, ensure the dialog heading provides sufficient context via `aria-labelledby`.

---

### P1-4 — Clicking the overlay closes the dialog without a keyboard equivalent

**WCAG:** 2.1.1 Keyboard (Level A)

**Location:** `ConfirmDialog` — `div.dialog-overlay` with `onClick={handleCancel}`

**Problem:** Mouse users can click the backdrop to dismiss the dialog. This interaction has no keyboard equivalent beyond the missing Escape key handler (P0-5). These are separate issues: the overlay click is a distinct pattern that must also be handled for pointer/touch accessibility — specifically, it may conflict with users who use switch access and trigger accidental dismissals.

**Fix:** The Escape key handler (P0-5 fix) covers the keyboard equivalent. Additionally, confirm the overlay dismiss is intentional UX — for destructive-action dialogs, accidental dismissal via backdrop click may itself be an anti-pattern worth disabling.

---

### P1-5 — Toggle switch state conveyed by color alone

**WCAG:** 1.4.1 Use of Color (Level A)

**Location:** `NotificationSettings` — both toggle switch divs

```tsx
background: emailEnabled ? '#007bff' : '#ccc',
```

**Problem:** The enabled/disabled state of each toggle switch is communicated only through a change in background color (blue vs. gray). Users with color blindness (deuteranopia, protanopia) or low vision may not distinguish the two states. There is no text label, icon, or pattern change.

**Fix:** In addition to the `aria-checked` fix in P0-7, add a visible text indicator or use iconography (e.g., "ON"/"OFF" text inside the track, or a checkmark icon). The `aria-checked` attribute handles AT; a visible non-color cue handles sighted users with color vision deficiency.

---

### P1-6 — Toggle switch has no visible focus indicator

**WCAG:** 2.4.7 Focus Visible (Level AA); 2.4.11 Focus Appearance (Level AA, WCAG 2.2)

**Location:** `NotificationSettings` — toggle switch `<div>` elements

**Problem:** Even if `tabIndex={0}` were added, there is no `:focus` or `:focus-visible` CSS on these elements. The browser's default outline is suppressed by the inline styles (they do not set `outline: none`, but custom `<div>` elements often have no default outline in many browsers/resets). Keyboard users cannot see where focus is.

**Fix:** Apply an explicit focus ring in CSS:

```css
.toggle-switch:focus-visible {
  outline: 2px solid #005fcc;
  outline-offset: 2px;
}
```

Or inline via `onFocus`/`onBlur` state — though CSS `:focus-visible` is strongly preferred.

---

### P1-7 — No visible focus indicator on dialog "buttons" (divs)

**WCAG:** 2.4.7 Focus Visible (Level AA)

**Location:** `ConfirmDialog` — all three interactive elements (cancel div, confirm div, close span)

**Problem:** As `<div>` and `<span>` elements with no `tabIndex`, these elements cannot receive focus at all (P0-6). Even when converted to `<button>` elements, the inline styles do not define a focus ring. If the browser's default outline is reset globally (common in CSS resets), these buttons will have invisible focus.

**Fix:** After converting to `<button>`, ensure focus styles are not suppressed:

```css
button:focus-visible {
  outline: 2px solid #005fcc;
  outline-offset: 2px;
}
```

---

### P1-8 — Heading hierarchy is broken

**WCAG:** 1.3.1 Info and Relationships (Level A)

**Location:** `NotificationSettings` uses `<h1>`, `ConfirmDialog` uses `<h2>` — but the dialog is rendered inside `NotificationSettings`

**Problem:** `<h1>` is for the page's primary heading. `<h2>` in the dialog is correct relative to a page `<h1>`, but the component is exported as a standalone page-level component. In a real application this component is likely embedded inside a page that already has an `<h1>`, making the "Notification Settings" `<h1>` incorrect. Additionally, the visual label "Email Notifications" and "Push Notifications" are styled `<div>` elements (not headings), which means the structure of the settings items is not conveyed to AT.

**Fix:** Use the appropriate heading level for the context. The `NotificationSettings` component should accept a `headingLevel` prop, or use `<h2>` if it is a section of a larger page. The notification type labels ("Email Notifications") could be `<label>` elements associated with their switches, rather than unsemantic `<div>` elements.

---

### P1-9 — Notification type labels are not associated with their toggle controls

**WCAG:** 1.3.1 Info and Relationships (Level A); 2.4.6 Headings and Labels (Level AA)

**Location:** `NotificationSettings` — label `<div>` and toggle `<div>` pairs

```tsx
<div style={{ fontWeight: 600 }}>Email Notifications</div>
<div style={{ fontSize: 14, color: '#666' }}>Receive updates via email</div>
// ... then separately:
<div onClick={() => handleToggle('email')} ...>
```

**Problem:** The text "Email Notifications" and its description are plain `<div>` elements with no semantic association to the toggle control. Even after fixing the toggle with `role="switch"`, screen readers will not automatically associate these labels unless they are linked via `aria-labelledby`/`aria-describedby` or HTML `<label>`.

**Fix:**

```tsx
<div
  role="switch"
  aria-checked={emailEnabled}
  aria-labelledby="email-label"
  aria-describedby="email-desc"
  ...
>
```

And add `id` attributes to the label elements:

```tsx
<div id="email-label" style={{ fontWeight: 600 }}>Email Notifications</div>
<div id="email-desc" style={{ fontSize: 14, color: '#666' }}>Receive updates via email</div>
```

---

### P1-10 — `setTimeout` delays in confirm/cancel handlers cause focus and state timing issues

**WCAG:** 2.2.1 Timing Adjustable (Level A); also a practical AT interaction bug

**Location:** `ConfirmDialog` — `handleConfirm` and `handleCancel`

```tsx
const handleConfirm = () => {
  setIsClosing(true);
  setTimeout(() => {
    onConfirm();
    setIsClosing(false);
  }, 200);
};
```

**Problem:** The 200 ms delay defers the actual `onConfirm`/`onCancel` callbacks. During this window, `isClosing` is `true` but `isOpen` is still `true` (since `onCancel` hasn't been called yet to set `showDisableConfirm` to `false`). There is no visual or AT feedback during this 200 ms gap. More critically, the `isClosing` state is never visually communicated (no disabled state, no spinner), meaning screen readers and keyboard users get no indication that an action is in progress. Additionally, if the user triggers the action again within 200 ms, the timeout can race. The `isClosing` state is also never used to apply any visible or accessible "loading" cue in the rendered JSX.

**Fix:** Use CSS transitions on the dialog itself rather than JavaScript delays for animations. If a delay is necessary, disable the buttons during `isClosing` and add `aria-busy="true"` to the dialog.

```tsx
<button disabled={isClosing} aria-disabled={isClosing} onClick={handleConfirm}>
  {isClosing ? 'Processing...' : confirmLabel}
</button>
```

---

## P2 Issues

### P2-1 — Color contrast may fail for body text and border elements

**WCAG:** 1.4.3 Contrast (Minimum) (Level AA)

**Location:** Multiple elements

- Cancel button label text: black text on `white` background with `#ccc` border — text itself passes, but the border color `#ccc` on white gives a contrast ratio of ~1.6:1 for the border (borders are not text, so not a direct WCAG fail, but it reduces perceivability)
- `color: '#666'` on `white` background (description text under notification labels, close button) — `#666` on white is approximately 5.74:1 for normal text, which passes AA for normal text but barely. For the 14px description text (`fontSize: 14`), this is small text. At 14px/normal weight, the threshold is 4.5:1, so it technically passes but is close to the edge.
- Dialog body text `color: '#333'` on white — passes (approximately 12.6:1).
- Disabled toggle background `#ccc` on white page background — the thumb (`white` on `#ccc`) gives approximately 1.6:1, which fails for any UI component boundary.

**Fix:** Audit all color pairs with a contrast checker. For the toggle thumb-on-track, use a darker track color in the off state (e.g., `#767676` gives 4.54:1 against white) or add a border to the thumb.

---

### P2-2 — Interactive target sizes are below WCAG 2.2 minimum

**WCAG:** 2.5.8 Target Size (Minimum) (Level AA, WCAG 2.2)

**Location:**
- Close button `<span>`: visually `24px` font-size but no explicit width/height/padding — actual hit target likely under 24×24 px
- Toggle switch thumb: `20×20 px` — below the 24×24 px minimum
- Toggle switch overall: `48×24 px` — width passes but height (24 px) is at the minimum boundary

**Fix:** Ensure all interactive targets are at least 24×24 px (WCAG 2.2 AA) or 44×44 px (WCAG 2.1 AAA / Apple HIG). Add `padding` to the close button and increase the toggle height.

---

### P2-3 — No `lang` attribute on the document (inferred issue)

**WCAG:** 3.1.1 Language of Page (Level A)

**Location:** Not in the component itself, but the component is a full-page export (`export default NotificationSettings`) with no wrapping `<html lang="en">` in the snippet context.

**Problem:** The component is exported as a top-level page. If consumed as a standalone page without a `lang` attribute on `<html>`, screen readers cannot correctly pronounce content.

**Fix:** Ensure the consuming application sets `<html lang="en">` (or appropriate language code). If the component is always embedded, this is the application's responsibility, but it should be documented.

---

### P2-4 — `message` prop interpolation renders `null` as a string when `pendingToggle` is null

**WCAG:** 3.3.1 Error Identification (Level A) — indirectly; 3.1 Readable

**Location:** `NotificationSettings` — dialog `message` prop

```tsx
message={`Are you sure you want to disable ${pendingToggle} notifications? You won't receive any ${pendingToggle} alerts until you re-enable them.`}
```

**Problem:** `pendingToggle` can be `null` (the TypeScript type is `'email' | 'push' | null`). If the dialog renders before `pendingToggle` is set (race condition), the message reads "Are you sure you want to disable null notifications?" — which is meaningless and confusing, particularly for screen reader users who hear the full announcement.

**Fix:** Guard the message: `message={pendingToggle ? \`...\${pendingToggle}...\` : 'Are you sure you want to disable this notification type?'}`. Or assert `pendingToggle` is non-null before rendering the dialog.

---

### P2-5 — No error/confirmation feedback when disabling is complete; status message clears unexpectedly

**WCAG:** 3.3.4 Error Prevention (Reversible, Checked, Confirmed) (Level AA)

**Location:** `NotificationSettings` — `status` state

**Problem:** The status message (e.g., "email notifications disabled") is set but never cleared, meaning once set it persists indefinitely on screen. This is not itself a WCAG violation but creates confusion. More importantly, there is no timeout or explicit clear, and the message is not announced on update (P0-8 covers the announcement gap). The message is also unconditionally rendered only when `status` is truthy — if the user dismisses and re-enables, the message updates but if they are fast, AT may miss the change.

**Fix:** Address P0-8 first (live region). Then set a timeout to clear the status after a reasonable duration (e.g., 5 seconds) so it does not persist and confuse users who return to the page.

---

### P2-6 — Overlay `onClick` propagation stop is fragile; no `aria` role on overlay

**WCAG:** 4.1.2 Name, Role, Value (Level A)

**Location:** `ConfirmDialog` — `div.dialog-overlay` and `div.dialog-container`

```tsx
<div className="dialog-overlay" onClick={handleCancel}>
  <div className="dialog-container" onClick={(e) => e.stopPropagation()} ...>
```

**Problem:** The overlay is a plain `<div>` with an `onClick`. It has no `role` or `aria-label`, so AT can see it as a clickable element with no description. The `stopPropagation` pattern is fragile — if any child element fails to stop propagation (e.g., due to a bug), the dialog closes unexpectedly. The overlay also has no `tabIndex` management to prevent it from receiving focus.

**Fix:** If the overlay is purely decorative (the dismiss happens via the Escape key and cancel button), give it `aria-hidden="true"` and remove the `onClick` from it, relying on the Escape handler (P0-5) for keyboard and the cancel button for pointer users. If backdrop dismissal is intentional UX, keep it but ensure it is not focusable: `tabIndex={-1}`.

---

## Consolidated Fix Priority

| Priority | Issues | Effort |
|----------|--------|--------|
| Address first (P0) | P0-1 through P0-8 | High — requires structural changes to replace `<div>`/`<span>` buttons, add ARIA roles, implement focus management and keyboard handling |
| Address next (P1) | P1-1, P1-3 through P1-10 | Medium — ARIA attribute additions, CSS focus styles, label associations, heading review |
| Address last (P2) | P2-1 through P2-6 | Low-Medium — color audit, target size adjustments, edge case guards |

## Recommended Structural Refactor

Rather than patching the existing implementation, the `ConfirmDialog` should be rebuilt using the HTML `<dialog>` element, which provides:
- Native focus trapping in supporting browsers
- Native Escape key handling
- Built-in `dialog` ARIA role
- Native `::backdrop` pseudo-element for the overlay
- `showModal()` / `close()` API that manages focus return automatically

```tsx
const dialogRef = useRef<HTMLDialogElement>(null);

useEffect(() => {
  if (isOpen) {
    dialogRef.current?.showModal();
  } else {
    dialogRef.current?.close();
  }
}, [isOpen]);

return (
  <dialog ref={dialogRef} aria-labelledby="dialog-title" aria-describedby="dialog-description">
    <h2 id="dialog-title">{title}</h2>
    <div id="dialog-description"><p>{message}</p></div>
    <button type="button" onClick={handleCancel}>{cancelLabel}</button>
    <button type="button" onClick={handleConfirm}>{confirmLabel}</button>
  </dialog>
);
```

Browser support for `<dialog>` is now excellent (Chrome 37+, Firefox 98+, Safari 15.4+). A polyfill (`dialog-polyfill`) is available for older environments.
