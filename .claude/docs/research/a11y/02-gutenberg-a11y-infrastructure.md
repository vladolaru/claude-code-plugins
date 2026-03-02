# Gutenberg Accessibility Infrastructure

> Research document for AI agent consumption. Part of the a11y research series.
> Generated: 2026-02-27 | Session 1, Task 1.2

## 1. Package Map

### @wordpress/a11y

- **Purpose:** Accessibility utilities for WordPress. Provides ARIA live region support to announce dynamic interface updates to screen readers.
- **Exports:**
  - `speak(message: string, ariaLive?: 'polite' | 'assertive'): void` -- Announces a message to screen readers via ARIA live regions.
  - `setup(): void` -- Creates the live region DOM elements. Script entry point calls this on `domReady`; module entry point exports a no-op since filters should inject the HTML on page load instead.
- **How it works internally:**
  1. `setup()` (script entry, `packages/a11y/src/index.js` line 17) checks for three DOM elements by ID: `a11y-speak-intro-text`, `a11y-speak-assertive`, `a11y-speak-polite`. Creates any that are missing.
  2. `addContainer(ariaLive)` (`packages/a11y/src/script/add-container.js` line 8) creates a `<div>` with `aria-live`, `aria-relevant="additions text"`, `aria-atomic="true"`, and visually-hidden inline styles. Appends to `document.body`.
  3. `addIntroText()` (`packages/a11y/src/script/add-intro-text.ts` line 14) creates a `<p>` with text "Notifications" (translatable), visually hidden, with `hidden` HTML attribute. The `hidden` attribute is removed when a message is spoken, making the intro text available to assistive technologies.
  4. `speak(message, ariaLive)` (`packages/a11y/src/shared/index.js` line 25):
     - Calls `clear()` which empties all `.a11y-speak-region` elements and re-hides the intro text.
     - Calls `filterMessage(message)` which strips HTML tags via regex (`/<[^<>]+>/g`) and appends a no-break space (`\u00A0`) if the message is identical to the previous one (Safari+VoiceOver workaround -- they don't announce repeated identical strings).
     - Sets `textContent` on the assertive container (if `ariaLive === 'assertive'`) or polite container (default).
     - Removes the `hidden` attribute from the intro text.
  5. The **module entry** (`packages/a11y/src/module/index.ts`) exports `speak` from shared but exports `setup` as a no-op. Comment: "Filters should inject the relevant HTML on page load instead of requiring setup."
- **When to use:** Announce dynamic content changes to screen readers -- search result counts, selection confirmations, status updates, error messages. Use `'polite'` (default) for non-urgent updates. Use `'assertive'` for immediate interruptions (e.g., item selection confirmations).
- **When NOT to use:** Do not use for content that is already visible and in the natural reading flow. Do not use for content rendered in ARIA live regions. Do not pass HTML -- the filter strips it, but passing plain strings is the intended pattern.

### @wordpress/dom (Focus Utilities)

- **focusable module** (`packages/dom/src/focusable.js`):
  - `find(context: Element, options?: { sequential?: boolean }): HTMLElement[]` -- Returns all focusable elements within a context element. When `sequential` is `true`, excludes elements with negative `tabindex` (matching the HTML spec's sequential focus navigation). Uses a CSS selector that covers: `[tabindex]`, `a[href]`, `button:not([disabled])`, `input:not([type="hidden"]):not([disabled])`, `select:not([disabled])`, `textarea:not([disabled])`, `iframe:not([tabindex^="-"])`, `object`, `embed`, `summary`, `area[href]`, `[contenteditable]:not([contenteditable=false])`. Filters out invisible elements (checks `offsetWidth`, `offsetHeight`, `getClientRects`), elements inside `[inert]` subtrees, and `AREA` elements not associated with a visible image map.
- **tabbable module** (`packages/dom/src/tabbable.js`):
  - `isTabbableIndex(element: Element): boolean` -- Returns `true` if the element's tabindex is not -1.
  - `find(context: Element): HTMLElement[]` -- Returns all tabbable elements within a context. Calls `focusable.find(context)`, then filters to tabbable indexes, sorts by tabindex (stable sort preserving document order for equal tabindex values), and collapses radio groups (keeps only the checked radio or the first radio per `name` group).
  - `findPrevious(element: Element): HTMLElement | undefined` -- Given an element, finds the preceding tabbable element in the entire document body by document position comparison.
  - `findNext(element: Element): HTMLElement | undefined` -- Given an element, finds the next tabbable element in the entire document body by document position comparison.
- **Key implementation details:**
  - Radio group collapsing (`createStatefulCollapseRadioGroup`, line 44): Uses a stateful reducer that tracks chosen radios by `name`. A checked radio replaces a previously chosen unchecked radio for the same name. This correctly models browser tab behavior where only one radio per group is tabbable.
  - `getTabIndex` (line 18) reads from the `tabindex` attribute string (not the DOM property) to normalize cross-browser inconsistencies (see Mozilla bug 1190261). Returns 0 as default when no attribute is set.
  - `findPrevious`/`findNext` search the entire `document.body` for tabbable elements, not just within a container. They use `compareDocumentPosition` with `DOCUMENT_POSITION_PRECEDING`/`DOCUMENT_POSITION_FOLLOWING` bitwise flags.
- **Package export structure** (`packages/dom/src/index.js`): `export const focus = { focusable, tabbable };` -- Both modules are grouped under a single `focus` namespace object.

### @wordpress/compose (A11y Hooks)

Six accessibility-related hooks are exported from `@wordpress/compose`:

| Hook | Purpose |
|------|---------|
| `useFocusOnMount` | Focus the first tabbable, first input, or container element when a component mounts |
| `useFocusReturn` | Return focus to the previously focused element when a component unmounts |
| `useConstrainedTabbing` | Trap Tab key navigation within a container element |
| `useFocusOutside` | Detect when focus moves outside a container element |
| `useInstanceId` | Generate unique IDs for ARIA attribute associations (labelledby, describedby, etc.) |
| `useMergeRefs` | Combine multiple ref callbacks into one -- essential for composing a11y hooks |

Supporting hooks used by the above:
- `useRefEffect` -- Effect-like ref callback pattern. Returns a cleanup-capable ref callback, used internally by `useFocusOnMount` and `useConstrainedTabbing`.

Composite hook:
- `useDialog` (exported as `__experimentalUseDialog`) -- Composes `useConstrainedTabbing`, `useFocusOnMount`, `useFocusReturn`, `useFocusOutside`, and escape key handling into a single ref+props tuple. Used by `Popover`.

### @wordpress/components (HOCs)

Five accessibility HOCs wrap the compose hooks for class-component and legacy usage:

| HOC | Wraps Hook | Package |
|-----|-----------|---------|
| `withFocusReturn` | `useFocusReturn` | `@wordpress/components` |
| `withConstrainedTabbing` | `useConstrainedTabbing` | `@wordpress/components` |
| `withFocusOutside` | `useFocusOutside` (experimental) | `@wordpress/components` |
| `withSpokenMessages` | `speak` + `useDebounce(speak, 500)` | `@wordpress/components` |
| `navigateRegions` | `useNavigateRegions` | `@wordpress/components` |

### @wordpress/eslint-plugin (jsx-a11y config)

- **File:** `packages/eslint-plugin/configs/jsx-a11y.js`
- **Base config:** Extends `plugin:jsx-a11y/recommended` from `eslint-plugin-jsx-a11y`.
- **Custom rules:**

| Rule | Setting | Rationale |
|------|---------|-----------|
| `jsx-a11y/label-has-associated-control` | `['error', { assert: 'htmlFor' }]` | Requires `htmlFor` attribute specifically (stricter than the default which also accepts nesting) |
| `jsx-a11y/media-has-caption` | `'off'` | Disabled -- Gutenberg handles media differently |
| `jsx-a11y/no-noninteractive-tabindex` | `'off'` | Disabled -- Gutenberg intentionally uses `tabIndex` on non-interactive elements for region navigation, scroll containers, and focus management |
| `jsx-a11y/role-has-required-aria-props` | `'off'` | Disabled -- likely due to custom ARIA patterns in the editor |
| `jsx-quotes` | `'error'` | Enforces consistent JSX quote style (not strictly a11y, but bundled here) |

---

## 2. Hook Reference Cards

### useFocusOnMount

- **Package:** `@wordpress/compose`
- **Import:** `import { useFocusOnMount } from '@wordpress/compose';`
- **File:** `packages/compose/src/hooks/use-focus-on-mount/index.ts`
- **Signature:**
  ```ts
  function useFocusOnMount(
    focusOnMount: useFocusOnMount.Mode = 'firstElement'
  ): RefCallback<HTMLElement>
  ```
- **Type:**
  ```ts
  namespace useFocusOnMount {
    type Mode = boolean | 'firstElement' | 'firstInputElement';
  }
  ```
- **Parameters:**
  - `focusOnMount` (default: `'firstElement'`):
    - `'firstElement'` -- Focuses the first tabbable element within the container (via `focus.tabbable.find(node)[0]`).
    - `'firstInputElement'` -- Tries to find the first non-hidden, non-disabled `input`, `select`, or `textarea` first. Falls back to the first tabbable element.
    - `true` -- Focuses the container element itself.
    - `false` -- Does nothing.
- **Returns:** A `RefCallback<HTMLElement>` (via `useRefEffect`) to attach to the container element.
- **Behavior (step-by-step from code):**
  1. Stores `focusOnMount` in a ref that updates on each render (line 48-50).
  2. Returns a `useRefEffect` callback (line 52) that runs when the ref attaches to a node.
  3. If `focusOnMountRef.current === false`, returns immediately (line 53-55).
  4. If the node already contains the active element, returns immediately (line 57-59) -- prevents re-stealing focus.
  5. If mode is not `'firstElement'` or `'firstInputElement'` (i.e., it's `true`), calls `setFocus(node)` directly on the container (line 61-67).
  6. Otherwise, sets a `setTimeout(..., 0)` (line 69) to defer the focus operation:
     - For `'firstInputElement'`: queries for `input:not([type="hidden"]):not([disabled]), select:not([disabled]), textarea:not([disabled])` within the node. Focuses if found.
     - Falls back to `focus.tabbable.find(node)[0]`.
  7. `setFocus` calls `element.focus({ preventScroll: true })` -- the comment (line 42-44) explains this prevents layout shifts when focusing newly mounted dialogs where popover position may not be finalized on first render.
  8. Cleanup: clears the timeout if the ref detaches before the timeout fires.
- **When to use:** Modals, popovers, dropdown menus, any overlay that should capture focus on open.
- **When NOT to use:** Inline content that appears without user interaction. Components where focus should remain on the trigger element.
- **Real example:** Modal component (`packages/components/src/modal/index.tsx`, lines 81-83):
  ```tsx
  const focusOnMountRef = useFocusOnMount(
    focusOnMount === 'firstContentElement' ? 'firstElement' : focusOnMount
  );
  ```
  Applied to the frame element (line 266-273) or the children container (lines 345-350) depending on whether `focusOnMount === 'firstContentElement'`.
- **Gotchas:**
  - The `setTimeout(0)` deferral means focus doesn't happen synchronously. This is intentional to allow the DOM to settle, but it means focus assertions in tests may need to wait.
  - `preventScroll: true` is passed to `focus()` to avoid layout shifts, but this may not work in all browsers.
  - The hook does NOT re-run when `focusOnMount` changes (the `useRefEffect` dependency array is empty `[]`). The ref stores the latest value, but the effect only fires when the DOM node attaches/detaches.

### useFocusReturn

- **Package:** `@wordpress/compose`
- **Import:** `import { useFocusReturn } from '@wordpress/compose';`
- **File:** `packages/compose/src/hooks/use-focus-return/index.js`
- **Signature:**
  ```ts
  function useFocusReturn(
    onFocusReturn?: () => void
  ): React.RefCallback<HTMLElement>
  ```
- **Parameters:**
  - `onFocusReturn` (optional) -- Custom callback that overrides the default focus return behavior. If provided, it is called instead of `element.focus()` on the previously focused element.
- **Returns:** A `React.RefCallback<HTMLElement>` (via `useCallback`).
- **Behavior (step-by-step from code):**
  1. Maintains `focusedBeforeMount` ref to track which element had focus when the component mounted (line 35).
  2. Maintains a module-level `origin` variable (line 7) that acts as a fallback when the originally focused element is no longer connected to the DOM.
  3. **On mount (ref called with node):** Records `document.activeElement` as `focusedBeforeMount`. Handles iframe edge case: if the active element is an `HTMLIFrameElement`, it reads from the iframe's `contentDocument` instead (lines 51-57).
  4. **On unmount (ref called with null):**
     - If the component's node is still connected AND focus is NOT inside it (line 63), sets `origin` to the recorded element and returns without restoring focus. This handles cases where focus has already moved elsewhere intentionally.
     - If `onFocusReturn` callback is provided, calls it (line 72-73).
     - Otherwise, focuses `focusedBeforeMount.current` if it's still connected, or falls back to `origin` (lines 75-79).
     - Resets `origin` to `null` (line 81).
- **When to use:** Modals, sidebars, dropdowns, any disposable UI overlay where focus should return to the trigger on close.
- **When NOT to use:** Persistent UI that doesn't close. Components where focus should move to a different element after closing (use `onFocusReturn` override instead).
- **Real example:** Modal component (`packages/components/src/modal/index.tsx`, line 85):
  ```tsx
  const focusReturnRef = useFocusReturn();
  ```
  Applied to the modal frame via `useMergeRefs` (line 269).
- **Gotchas:**
  - The `origin` variable is **module-level**, not per-instance. It acts as a global fallback for cascading unmounts (e.g., a dropdown inside a modal -- when both unmount, the origin tracks the deep-original element).
  - If the previously focused element has been removed from the DOM, focus goes to `origin`. If `origin` is also gone, nothing happens.
  - The iframe detection on mount (line 51-55) means it works with iframe-embedded content.

### useConstrainedTabbing

- **Package:** `@wordpress/compose`
- **Import:** `import { useConstrainedTabbing } from '@wordpress/compose';`
- **File:** `packages/compose/src/hooks/use-constrained-tabbing/index.js`
- **Signature:**
  ```ts
  function useConstrainedTabbing(): React.RefCallback<Element>
  ```
- **Parameters:** None.
- **Returns:** A `React.RefCallback<Element>` (via `useRefEffect`).
- **Behavior (step-by-step from code):**
  1. Attaches a `keydown` event listener to the container node (line 85).
  2. On Tab press:
     - Determines direction: `shiftKey` = `'findPrevious'`, otherwise `'findNext'` (line 41).
     - Calls `focus.tabbable[action](target)` to find the next/previous tabbable element globally (line 42-45).
     - **Case 1 -- target contains nextElement** (line 53-58): When the target is itself a tabbable container that contains the next focusable element, browsers disagree on where to move focus. The hook takes over: `event.preventDefault()` and explicitly focuses the next element. (See GitHub issue #46041.)
     - **Case 2 -- nextElement is inside the container** (line 64): Relies on native browser behavior. Does nothing.
     - **Case 3 -- nextElement is outside the container** (lines 72-82): Creates a temporary `<div tabIndex="-1">` trap element, prepends or appends it to the container depending on direction. Focuses the trap. The trap self-removes on blur. The browser then naturally tabs to the next element within the container (wrapping around).
  3. Cleanup removes the `keydown` listener.
- **When to use:** Modals and dialogs where Tab should not escape the content area. Must always be paired with an escape mechanism (Escape key, close button).
- **When NOT to use:** Inline content. Components where focus should be able to leave via Tab. Dropdowns that should close on focus-out rather than trap focus.
- **Real example:** Modal component (`packages/components/src/modal/index.tsx`, line 84):
  ```tsx
  const constrainedTabbingRef = useConstrainedTabbing();
  ```
  Applied to the modal frame (line 268).
- **Gotchas:**
  - The trap `<div>` technique is clever but creates a temporary DOM node on every wrap-around Tab. The node self-destructs on blur (line 80).
  - `findNext`/`findPrevious` search the **entire document body**, not just the container. This is correct because they need the global tab order to determine when focus would escape.
  - The hook does NOT call `event.preventDefault()` in cases 2 and 3 -- it relies on native browser tabbing. Only case 1 (container-contains-next) uses `preventDefault`.

### useInstanceId

- **Package:** `@wordpress/compose`
- **Import:** `import { useInstanceId } from '@wordpress/compose';`
- **File:** `packages/compose/src/hooks/use-instance-id/index.ts`
- **Signature (three overloads):**
  ```ts
  function useInstanceId(object: object): number;
  function useInstanceId(object: object, prefix: string): string;
  function useInstanceId<T extends string | number>(
    object: object, prefix: string, preferredId?: T
  ): T;
  ```
- **Parameters:**
  - `object` -- A reference object used as a key (typically the component function itself, e.g., `Modal`). Uses a `WeakMap` to track instance counts per object.
  - `prefix` (optional) -- String prefix. When provided, returns `"${prefix}-${id}"` instead of a raw number.
  - `preferredId` (optional) -- If provided, returned directly (bypass generation). Useful for controlled ID patterns.
- **Returns:** A unique ID (number or string depending on overload). Memoized via `useMemo` with `[object, preferredId, prefix]` dependencies.
- **Behavior:**
  - Maintains a module-level `WeakMap<object, number>` (line 6) that maps reference objects to their instance count.
  - Each call increments the count for the given object. First instance gets `0`, second gets `1`, etc.
  - The `WeakMap` ensures no memory leaks -- when the object (component function) is garbage collected, the entry is removed.
- **When to use:** Generating unique IDs for `aria-labelledby`, `aria-describedby`, `htmlFor`, or any attribute that links elements by ID. Especially important when multiple instances of the same component exist on a page.
- **When NOT to use:** When you need a stable ID across renders that doesn't depend on mount order. When you need a globally unique ID (this is only unique per-object-reference).
- **Real example:** Modal (`packages/components/src/modal/index.tsx`, lines 68-71):
  ```tsx
  const instanceId = useInstanceId( Modal );
  const headingId = title
    ? `components-modal-header-${ instanceId }`
    : aria.labelledby;
  ```
  ComboboxControl (`packages/components/src/combobox-control/index.tsx`, line 147):
  ```tsx
  const instanceId = useInstanceId( ComboboxControl, 'combobox-control' );
  ```
- **Gotchas:**
  - IDs are **mount-order dependent**. The same component will get different IDs depending on render order. Do not rely on specific ID values in tests.
  - The `object` parameter should be a stable reference (like a component function), not an object created on each render.

### useFocusOutside

- **Package:** `@wordpress/compose`
- **Import:** `import { __experimentalUseFocusOutside as useFocusOutside } from '@wordpress/compose';`
- **File:** `packages/compose/src/hooks/use-focus-outside/index.ts`
- **Signature:**
  ```ts
  function useFocusOutside(
    onFocusOutside: ((event: React.FocusEvent) => void) | undefined
  ): UseFocusOutsideReturn

  type UseFocusOutsideReturn = {
    onFocus: React.FocusEventHandler;
    onMouseDown: React.MouseEventHandler;
    onMouseUp: React.MouseEventHandler;
    onTouchStart: React.TouchEventHandler;
    onTouchEnd: React.TouchEventHandler;
    onBlur: React.FocusEventHandler;
  };
  ```
- **Parameters:**
  - `onFocusOutside` -- Callback fired when focus leaves the container. Can be `undefined` to disable.
- **Returns:** An object of six event handlers to spread onto the container element.
- **Behavior (step-by-step from code):**
  1. **onBlur** (`queueBlurCheck`, line 127): Persists the React event, then schedules a `setTimeout(0)` check. Skips if `preventBlurCheckRef` is true (button focus normalization). Checks `data-unstable-ignore-focus-outside-for-relatedtarget` attribute for explicitly ignored related targets (used for non-React modals like the Media Library). In the timeout, checks `document.hasFocus()` -- if the document lost focus entirely (e.g., window switch), cancels the blur to keep focus in place. Otherwise calls `onFocusOutside`.
  2. **onFocus** (`cancelBlurCheck`, line 86): Cancels any pending blur check timeout. This means: if focus moves between children of the container, the blur fires but the subsequent focus cancels it before the timeout runs.
  3. **onMouseDown/onMouseUp/onTouchStart/onTouchEnd** (`normalizeButtonFocus`, line 107): Handles a Firefox/Safari quirk where clicking a `<button>`, `<a>`, or button-type `<input>` does NOT fire a focus event. Sets `preventBlurCheckRef` to `true` on mousedown/touchstart for these elements, preventing the blur check from firing. Resets on mouseup/touchend.
- **When to use:** Dropdown menus, popovers, and other floating UI that should close when focus moves outside.
- **When NOT to use:** Modals that should trap focus (use `useConstrainedTabbing` instead). Content that should remain open regardless of focus.
- **Real example:** Used internally by `useDialog` (`packages/compose/src/hooks/use-dialog/index.ts`, line 80):
  ```ts
  const focusOutsideProps = useFocusOutside( ( event ) => {
    if ( currentOptions.current?.__unstableOnClose ) {
      currentOptions.current.__unstableOnClose( 'focus-outside', event );
    } else if ( currentOptions.current?.onClose ) {
      currentOptions.current.onClose();
    }
  } );
  ```
- **Gotchas:**
  - The `data-unstable-ignore-focus-outside-for-relatedtarget` attribute (line 144) is an escape hatch for non-React modals. The attribute value is a CSS selector; if the blur's `relatedTarget` matches that selector, the blur is ignored. This is unstable API and should be avoided.
  - The `document.hasFocus()` check (line 159) means that switching browser tabs or windows will NOT trigger the focus-outside callback. Focus remains in place.
  - Still marked as `__experimental` in the public API.

### useMergeRefs

- **Package:** `@wordpress/compose`
- **Import:** `import { useMergeRefs } from '@wordpress/compose';`
- **File:** `packages/compose/src/hooks/use-merge-refs/index.ts`
- **Signature:**
  ```ts
  function useMergeRefs<T>(refs: Ref<T>[]): RefCallback<T>
  ```
- **Parameters:**
  - `refs` -- Array of refs (ref callbacks, ref objects, or falsy values). Falsy values are skipped, enabling conditional ref composition: `[enabled && someRef, otherRef]`.
- **Returns:** A single `RefCallback<T>` that forwards to all provided refs.
- **Behavior (step-by-step from code):**
  1. Returns a stable `useCallback([], ...)` (line 97) ref callback that fires when the DOM element changes.
  2. When the element changes (mount/unmount), calls `assignRef` for each ref with the new value. On unmount, uses `previousRefsRef` to call old refs with `null`.
  3. Between element changes, a `useLayoutEffect` (line 72) detects when individual refs in the array change (e.g., due to dependency updates in a `useCallback` ref). It calls the old ref with `null` and the new ref with the current element.
  4. `assignRef` (line 7) handles both function refs and object refs (`{ current }` pattern).
  5. A `didElementChangeRef` flag (line 61) prevents double-calling: if the element and a ref change in the same render cycle, the ref callback handles it and the effect skips.
- **Role in a11y specifically:** This is the glue that enables composing multiple a11y hooks on a single element. Without it, you cannot use `useFocusOnMount`, `useConstrainedTabbing`, and `useFocusReturn` on the same DOM node.
- **When to use:** Any time you need multiple ref-based behaviors on one element. Critical for a11y patterns.
- **When NOT to use:** Single-ref scenarios where a simple `useRef` suffices.
- **Real example:** Modal component (`packages/components/src/modal/index.tsx`, lines 266-273):
  ```tsx
  ref={ useMergeRefs( [
    frameRef,
    constrainedTabbingRef,
    focusReturnRef,
    focusOnMount !== 'firstContentElement'
      ? focusOnMountRef
      : null,
  ] ) }
  ```
- **Gotchas:**
  - The refs array is used as the dependency list for `useLayoutEffect` (line 87). If you create a new array on every render, the effect runs every render. Use a stable array reference or ensure the individual refs are stable.
  - Passing `null` or `false` in the array is valid and skips that slot.

---

## 3. Utility Reference Cards

### speak() (@wordpress/a11y)

- **Import:** `import { speak } from '@wordpress/a11y';`
- **File:** `packages/a11y/src/shared/index.js`
- **Signature:**
  ```ts
  function speak(message: string, ariaLive?: 'polite' | 'assertive'): void
  ```
- **Behavior:**
  1. Calls `clear()` -- empties all `.a11y-speak-region` elements and hides the intro text by setting `hidden` attribute.
  2. Calls `filterMessage(message)`:
     - Strips HTML tags via `message.replace(/<[^<>]+>/g, ' ')`.
     - If message equals the previous message, appends `\u00A0` (no-break space) to force Safari+VoiceOver to re-announce.
  3. Finds `#a11y-speak-assertive` and `#a11y-speak-polite` containers.
  4. Sets `textContent` on the appropriate container.
  5. Removes the `hidden` attribute from `#a11y-speak-intro-text`.
- **When to use:** Dynamic status updates ("3 results found"), action confirmations ("Item selected"), error announcements. Default to `'polite'`; use `'assertive'` only for time-sensitive interruptions.
- **Real example:** ComboboxControl (`packages/components/src/combobox-control/index.tsx`, lines 180 and 299-315):
  ```tsx
  // On selection (assertive)
  speak( messages.selected, 'assertive' );

  // On filter change (polite)
  const message = hasMatchingSuggestions
    ? sprintf(
        _n(
          '%d result found, use up and down arrow keys to navigate.',
          '%d results found, use up and down arrow keys to navigate.',
          matchingSuggestions.length
        ),
        matchingSuggestions.length
      )
    : __( 'No results.' );
  speak( message, 'polite' );
  ```
- **Gotchas:**
  - `clear()` is called BEFORE setting the new message. This means two rapid `speak()` calls will clear the first before the screen reader announces it. The second call wins.
  - The Safari workaround (appending `\u00A0`) means the `previousMessage` tracking is module-level state. In single-page apps this persists across component lifecycles.
  - The DOM containers must exist before `speak()` is called. The script entry auto-creates them on `domReady`, but the module entry relies on server-side rendering or filters.

### setup() (@wordpress/a11y)

- **Import:** `import { setup } from '@wordpress/a11y';`
- **File:** `packages/a11y/src/index.js` (script) / `packages/a11y/src/module/index.ts` (module)
- **Signature:**
  ```ts
  function setup(): void
  ```
- **Behavior:**
  - **Script entry** (`packages/a11y/src/index.js`, line 17): Checks for `#a11y-speak-intro-text`, `#a11y-speak-assertive`, `#a11y-speak-polite` by ID. Creates any that are missing via `addIntroText()` and `addContainer('assertive'|'polite')`. Also auto-called on `domReady` (line 40).
  - **Module entry** (`packages/a11y/src/module/index.ts`, line 11): No-op. Comment: "Filters should inject the relevant HTML on page load instead of requiring setup."
- **When to use:** Only if you need to manually ensure live regions exist before `speak()` is called, and you're using the script entry point. Normally this is automatic.
- **Gotchas:** The module entry is a no-op. If you import from the module build, you must ensure the DOM containers exist through other means (e.g., server-rendered HTML).

### focusable.find() (@wordpress/dom)

- **Import:** `import { focus } from '@wordpress/dom';` then `focus.focusable.find(context)`
- **File:** `packages/dom/src/focusable.js`, line 101
- **Signature:**
  ```ts
  function find(
    context: Element,
    options?: { sequential?: boolean }
  ): HTMLElement[]
  ```
- **Behavior:**
  1. Builds a CSS selector via `buildSelector(sequential)`. When `sequential` is true, uses `[tabindex]:not([tabindex^="-"])` (excludes negative tabindex); otherwise includes all `[tabindex]`.
  2. Queries `context.querySelectorAll(selector)`.
  3. Filters results:
     - Removes invisible elements via `isVisible()` (checks `offsetWidth > 0 || offsetHeight > 0 || getClientRects().length > 0`).
     - Removes elements inside `[inert]` subtrees (line 111).
     - For `AREA` elements, validates the area is in a `<map>` that is referenced by a visible `<img usemap>` (line 73-85).
- **When to use:** Finding all elements that can receive focus programmatically. Useful for focus management outside the tab order.
- **Gotchas:** The `sequential` option is critical. Without it, elements with `tabindex="-1"` are included (they're focusable via JavaScript but not via Tab key).

### tabbable.find() (@wordpress/dom)

- **Import:** `import { focus } from '@wordpress/dom';` then `focus.tabbable.find(context)`
- **File:** `packages/dom/src/tabbable.js`, line 149
- **Signature:**
  ```ts
  function find(context: Element): HTMLElement[]
  ```
- **Behavior:**
  1. Calls `focusable.find(context)` (no `sequential` flag, so includes all focusable).
  2. Filters via `filterTabbable()`:
     - Removes elements with `tabIndex === -1`.
     - Wraps elements in `{ element, index }` objects for stable sorting.
     - Sorts by `tabIndex` (ascending), preserving document order for equal values.
     - Collapses radio groups: keeps one radio per `name` (the checked one, or first encountered).
- **When to use:** Determining the Tab key navigation order within a container. Used by `useConstrainedTabbing` and `useFocusOnMount`.
- **Gotchas:** This does NOT use the `sequential` flag on `focusable.find`, so it first gets all focusable elements and then filters by tabIndex. The result is equivalent but the path differs from calling `focusable.find(context, { sequential: true })`.

### tabbable.findNext() / findPrevious() (@wordpress/dom)

- **Import:** `import { focus } from '@wordpress/dom';` then `focus.tabbable.findNext(element)` / `focus.tabbable.findPrevious(element)`
- **File:** `packages/dom/src/tabbable.js`, lines 161-187
- **Signatures:**
  ```ts
  function findPrevious(element: Element): HTMLElement | undefined;
  function findNext(element: Element): HTMLElement | undefined;
  ```
- **Behavior:**
  - Both search `element.ownerDocument.body` for ALL tabbable elements (not scoped to a container).
  - `findNext`: Finds the first tabbable element that comes AFTER `element` in document order (using `compareDocumentPosition` with `DOCUMENT_POSITION_FOLLOWING`).
  - `findPrevious`: Reverses the tabbable array and finds the first element that comes BEFORE `element` in document order (using `DOCUMENT_POSITION_PRECEDING`).
- **When to use:** Determining what the browser would focus on Tab/Shift+Tab from a given element. Used by `useConstrainedTabbing` to detect when focus would leave the container.
- **Gotchas:** These search the entire document body, which can be expensive on large pages. They also rebuild the full tabbable list on every call.

### modalize() / unmodalize() (modal/aria-helper)

- **Import:** `import * as ariaHelper from './aria-helper';` (internal to `@wordpress/components`)
- **File:** `packages/components/src/modal/aria-helper.ts`
- **Signatures:**
  ```ts
  function modalize(modalElement?: HTMLDivElement): void;
  function unmodalize(): void;
  function elementShouldBeHidden(element: Element): boolean;
  ```
- **Behavior:**
  - `modalize(modalElement)`:
    1. Iterates all direct children of `document.body` (line 24).
    2. Skips the `modalElement` itself (line 28-30).
    3. For each other child, calls `elementShouldBeHidden()`. If true, sets `aria-hidden="true"` and pushes to a hidden-elements array.
    4. Pushes the hidden-elements array onto `hiddenElementsByDepth` stack (line 26) -- supports nested modals.
  - `elementShouldBeHidden(element)` (line 46): Returns `true` UNLESS the element is:
    - A `<script>` tag
    - Already has `hidden` attribute
    - Already has `aria-hidden` attribute
    - Has `aria-live` attribute
    - Has a role in `['alert', 'status', 'log', 'marquee', 'timer']` (ARIA live region roles)
  - `unmodalize()`:
    1. Pops the latest hidden-elements array from the stack (line 61).
    2. Removes `aria-hidden` from each element in the array.
- **When to use:** When opening a modal to hide background content from screen readers. This is a workaround for `aria-modal="true"` being buggy in Safari (noted in code comment, lines 17-19).
- **Real example:** Modal (`packages/components/src/modal/index.tsx`, lines 114-118):
  ```tsx
  useEffect( () => {
    ariaHelper.modalize( ref.current! );
    return () => ariaHelper.unmodalize();
  }, [] );
  ```
- **Gotchas:**
  - Only hides direct children of `document.body`. Modals MUST be portaled to `document.body` for this to work correctly.
  - The depth stack enables nested modals: opening modal B while modal A is open pushes a second layer. Closing B restores its layer without affecting A's.
  - Preserves existing `aria-hidden` -- elements that already have `aria-hidden` are NOT added to the hidden list and thus NOT modified on unmodalize.
  - Live regions (`aria-live`, live region roles) are intentionally NOT hidden, so `speak()` announcements continue to work while a modal is open.

---

## 4. HOC Reference Cards

### withFocusReturn

- **Package:** `@wordpress/components`
- **Import:** `import { withFocusReturn } from '@wordpress/components';`
- **File:** `packages/components/src/higher-order/with-focus-return/index.tsx`
- **Signature:**
  ```ts
  // Simple usage
  withFocusReturn(WrappedComponent: React.ComponentType): React.ComponentType
  // With options
  withFocusReturn({ onFocusReturn?: () => void })(WrappedComponent): React.ComponentType
  ```
- **Behavior:** Wraps the component in a `<div ref={useFocusReturn(onFocusReturn)}>`. Detects whether the argument is a component or an options object and handles both patterns.
- **When to use:** Class components or legacy code that cannot use hooks directly.
- **When NOT to use:** Functional components -- use `useFocusReturn` directly.
- **Also exports:** `Provider` -- deprecated since WordPress 5.7. It's a no-op passthrough that logs a deprecation warning.

### withConstrainedTabbing

- **Package:** `@wordpress/components`
- **Import:** `import { withConstrainedTabbing } from '@wordpress/components';`
- **File:** `packages/components/src/higher-order/with-constrained-tabbing/index.tsx`
- **Signature:**
  ```ts
  withConstrainedTabbing(WrappedComponent: React.ComponentType): React.ComponentType
  ```
- **Behavior:** Wraps in `<div ref={useConstrainedTabbing()} tabIndex={-1}>`. The `tabIndex={-1}` on the wrapper ensures the container itself is focusable, which is needed for the constrained tabbing trap to work as a focus boundary.
- **When to use:** Class components that need focus trapping.
- **When NOT to use:** Functional components -- use `useConstrainedTabbing` directly.

### withSpokenMessages

- **Package:** `@wordpress/components`
- **Import:** `import { withSpokenMessages } from '@wordpress/components';`
- **File:** `packages/components/src/higher-order/with-spoken-messages/index.tsx`
- **Signature:**
  ```ts
  withSpokenMessages(Component: React.ComponentType): React.ComponentType
  ```
- **Behavior:** Injects two props into the wrapped component:
  - `speak` -- The `speak` function from `@wordpress/a11y`.
  - `debouncedSpeak` -- `useDebounce(speak, 500)` -- a debounced version with a 500ms delay.
- **When to use:** Class components that need to announce dynamic changes. The debounced variant is useful for rapidly changing content (e.g., typing search queries) to avoid flooding the screen reader.
- **When NOT to use:** Functional components -- import `speak` directly and use `useDebounce` if needed.

### navigateRegions

- **Package:** `@wordpress/components`
- **Import:** `import { navigateRegions } from '@wordpress/components';`
- **File:** `packages/components/src/higher-order/navigate-regions/index.tsx`
- **Signature:**
  ```ts
  navigateRegions(Component: React.ComponentType): React.ComponentType
  ```
- **Also exports:** `useNavigateRegions(shortcuts?: Shortcuts)` -- the hook version.
- **Behavior:**
  1. Wraps in `<div {...useNavigateRegions(shortcuts)}>`.
  2. `useNavigateRegions` (line 45):
     - Finds all `[role="region"][tabindex="-1"]` elements within the wrapper.
     - On keyboard shortcut (default: `Ctrl+`` or `Access+N` for next, `Ctrl+Shift+`` or `Access+P` for previous), cycles focus between regions.
     - Tracks `isFocusingRegions` state. When true, adds `is-focusing-regions` CSS class to the wrapper.
     - Clicking anywhere resets `isFocusingRegions` to false.
  3. Returns `{ ref, className, onKeyDown }` to spread on the wrapper div.
- **Default shortcuts:**
  ```ts
  previous: [
    { modifier: 'ctrlShift', character: '`' },
    { modifier: 'ctrlShift', character: '~' },
    { modifier: 'access', character: 'p' },
  ],
  next: [
    { modifier: 'ctrl', character: '`' },
    { modifier: 'access', character: 'n' },
  ],
  ```
- **When to use:** Top-level editor layouts with multiple landmark regions (header, content, sidebar).
- **When NOT to use:** Components within a single region. Small UIs without distinct landmark areas.
- **Gotchas:**
  - Regions MUST have both `role="region"` and `tabindex="-1"` to be discovered (line 51-53).
  - The `is-focusing-regions` CSS class is intended for styling focus indicators on regions. It persists until the user clicks.

---

## 5. Composition Patterns

### Modal Pattern

- **File:** `packages/components/src/modal/index.tsx`
- **Hooks used:** `useInstanceId`, `useFocusOnMount`, `useConstrainedTabbing`, `useFocusReturn`, `useMergeRefs`, plus `ariaHelper.modalize/unmodalize`.
- **How they compose:**
  1. `useInstanceId(Modal)` generates a unique ID for the modal heading's `id` attribute, linked via `aria-labelledby` (lines 68-71).
  2. `useFocusOnMount(mode)` returns a ref that will focus the first tabbable or the container on mount (line 81-83).
  3. `useConstrainedTabbing()` returns a ref that traps Tab within the modal frame (line 84).
  4. `useFocusReturn()` returns a ref that tracks the pre-modal active element and restores it on unmount (line 85).
  5. `useMergeRefs` combines all refs onto the modal frame div (lines 266-273).
  6. `ariaHelper.modalize(ref.current)` hides all sibling elements from screen readers (lines 115-118).
  7. The modal is portaled to `document.body` via `createPortal` (line 361).
- **Focus lifecycle:**
  ```
  open
    -> createPortal to document.body
    -> modalize() hides siblings with aria-hidden
    -> useFocusReturn records document.activeElement
    -> useFocusOnMount focuses first tabbable (or container, or first input)
    -> useConstrainedTabbing traps Tab within frame
    -> user interacts...
  close
    -> unmodalize() removes aria-hidden from siblings
    -> useFocusReturn restores focus to the pre-modal element
    -> body class 'modal-open' removed (with ref-counting for nested modals)
  ```
- **Code excerpt** (ref composition on the modal frame, lines 254-279):
  ```tsx
  <div
    className={ clsx(
      'components-modal__frame',
      sizeClass,
      className
    ) }
    style={ { ...frameStyle, ...style } }
    ref={ useMergeRefs( [
      frameRef,
      constrainedTabbingRef,
      focusReturnRef,
      focusOnMount !== 'firstContentElement'
        ? focusOnMountRef
        : null,
    ] ) }
    role={ role }
    aria-label={ contentLabel }
    aria-labelledby={ contentLabel ? undefined : headingId }
    aria-describedby={ aria.describedby }
    tabIndex={ -1 }
    onKeyDown={ onKeyDown }
  >
  ```
- **Notable detail:** When `focusOnMount === 'firstContentElement'`, the `focusOnMountRef` is applied to the children container div instead of the frame (lines 344-351), so focus lands inside the content area rather than on header/close buttons.

### Dropdown Pattern

- **File:** `packages/components/src/dropdown/index.tsx`
- **Hooks used:** `useMergeRefs`, delegates to `Popover` which uses `useDialog` (composing `useConstrainedTabbing`, `useFocusOnMount`, `useFocusReturn`, `useFocusOutside`).
- **How they compose:**
  1. `Dropdown` renders a wrapper `<div>` with `tabIndex={-1}` (line 123) -- comment (line 120-122): "Some UAs focus the closest focusable parent when the toggle is clicked. Making this div focusable ensures such UAs will focus it and `closeIfFocusOutside` can tell if the toggle was clicked."
  2. `useMergeRefs([containerRef, forwardedRef, setFallbackPopoverAnchor])` merges the container ref with the forwarded ref and the popover anchor state setter (lines 115-119).
  3. When open, renders `<Popover>` which internally calls `useDialog` (imported as `__experimentalUseDialog`).
  4. `useDialog` (`packages/compose/src/hooks/use-dialog/index.ts`) composes all dialog behaviors:
     ```ts
     return [
       useMergeRefs( [
         constrainTabbing ? constrainedTabbingRef : null,
         options.focusOnMount !== false ? focusReturnRef : null,
         options.focusOnMount !== false ? focusOnMountRef : null,
         closeOnEscapeRef,
       ] ),
       { ...focusOutsideProps, tabIndex: -1 },
     ];
     ```
  5. `Dropdown` also implements `closeIfFocusOutside` (line 78) which checks if focus moved to a dialog (preserves focus for nested dialogs).
- **Focus lifecycle:**
  ```
  click toggle
    -> setIsOpen(true)
    -> Popover mounts with useDialog ref
    -> useFocusReturn records toggle as activeElement
    -> useFocusOnMount focuses first tabbable in popover
    -> useConstrainedTabbing traps Tab (if constrainTabbing)
  close (Escape / focus outside / toggle click)
    -> Popover unmounts
    -> useFocusReturn restores focus to toggle
  ```
- **Code excerpt** (Dropdown wrapper, lines 112-156):
  ```tsx
  <div
    className={ className }
    ref={ useMergeRefs( [
      containerRef,
      forwardedRef,
      setFallbackPopoverAnchor,
    ] ) }
    tabIndex={ -1 }
    style={ style }
  >
    { renderToggle( args ) }
    { isOpen && (
      <Popover
        onClose={ close }
        onFocusOutside={ closeIfFocusOutside }
        focusOnMount={ focusOnMount }
        { ...popoverProps }
      >
        { renderContent( args ) }
      </Popover>
    ) }
  </div>
  ```

### Navigable Container Pattern

- **File:** `packages/components/src/navigable-container/container.tsx`
- **Mechanism:** Class component that manages arrow-key (or Tab-key) focus navigation among its focusable children.
- **Focus strategy:** Uses a **roving focus** approach. On arrow key press:
  1. `eventToOffset(event)` converts the key event to a direction offset (+1, -1, 0, or undefined).
  2. `getFocusableContext(activeElement)` calls either `focus.tabbable.find()` or `focus.focusable.find()` (depending on `onlyBrowserTabstops` prop) to get the ordered list of focusable children.
  3. `cycleValue(index, total, offset)` computes the next index, wrapping around if `cycle` is true (default).
  4. Directly calls `focusables[nextIndex].focus()`.
  5. Calls `onNavigate(nextIndex, focusables[nextIndex])` callback.
- **Two specializations:**
  - `NavigableMenu` (`menu.tsx`): Arrow keys navigate. Sets `role="menu"`, `aria-orientation`, `stopNavigationEvents=true`, `onlyBrowserTabstops=false` (includes all focusable elements).
  - `TabbableContainer` (`tabbable.tsx`): Tab key navigates. Sets `stopNavigationEvents=true`, `onlyBrowserTabstops=true` (only browser tabbable elements).
- **Code excerpt** (core navigation logic, `container.tsx` lines 95-159):
  ```tsx
  onKeyDown( event: KeyboardEvent ) {
    if ( this.props.onKeyDown ) {
      this.props.onKeyDown( event );
    }
    const { cycle = true, eventToOffset, onNavigate = noop,
            stopNavigationEvents } = this.props;
    const offset = eventToOffset( event );

    if ( offset !== undefined && stopNavigationEvents ) {
      event.stopImmediatePropagation();
      const targetRole = (event.target as HTMLDivElement | null)
        ?.getAttribute( 'role' );
      const targetHasMenuItemRole = !! targetRole &&
        MENU_ITEM_ROLES.includes( targetRole );
      if ( targetHasMenuItemRole ) {
        event.preventDefault();
      }
    }

    if ( ! offset ) { return; }
    const context = getFocusableContext( activeElement );
    if ( ! context ) { return; }

    const { index, focusables } = context;
    const nextIndex = cycle
      ? cycleValue( index, focusables.length, offset )
      : index + offset;

    if ( nextIndex >= 0 && nextIndex < focusables.length ) {
      focusables[ nextIndex ].focus();
      onNavigate( nextIndex, focusables[ nextIndex ] );
      if ( event.code === 'Tab' ) {
        event.preventDefault();
      }
    }
  }
  ```
- **Gotchas:**
  - Uses DOM event listeners (not React synthetic events) because "the React Tree can be different from the DOM tree when using portals. Block Toolbars for instance are rendered in a separate React Trees" (comment, lines 48-52).
  - `MENU_ITEM_ROLES = ['menuitem', 'menuitemradio', 'menuitemcheckbox']` -- `preventDefault` is only called for these roles to avoid interfering with VoiceOver's text highlighting on arrow keys (line 117-128).
  - `stopImmediatePropagation` (line 113) prevents document-level arrow key handlers from interfering.

### ComboboxControl Pattern (Live Regions)

- **File:** `packages/components/src/combobox-control/index.tsx`
- **speak() usage:** Two distinct announcement points:
  1. **On selection** (line 180): `speak(messages.selected, 'assertive')` -- Immediately announces "Item selected." (customizable via `messages.selected` prop). Uses `'assertive'` because this is a direct user action result.
  2. **On filter change** (lines 299-315, in a `useEffect` triggered by `[matchingSuggestions, isExpanded]`): Announces result count with navigation instructions, or "No results." Uses `'polite'` because this is a passive update as the user types.
- **Announcement strategy:**
  - **Assertive** for discrete user-initiated actions (selecting an item).
  - **Polite** for ongoing state changes (filtering updates the count).
  - The result count message includes keyboard navigation instructions: "%d result found, use up and down arrow keys to navigate." Uses `_n()` for proper singular/plural i18n.
  - Only announces when `isExpanded` is true (line 301), preventing announcements when the dropdown is closed.
- **Code excerpt** (announcement effect, lines 298-316):
  ```tsx
  // Announcements.
  useEffect( () => {
    const hasMatchingSuggestions = matchingSuggestions.length > 0;
    if ( isExpanded ) {
      const message = hasMatchingSuggestions
        ? sprintf(
            _n(
              '%d result found, use up and down arrow keys to navigate.',
              '%d results found, use up and down arrow keys to navigate.',
              matchingSuggestions.length
            ),
            matchingSuggestions.length
          )
        : __( 'No results.' );

      speak( message, 'polite' );
    }
  }, [ matchingSuggestions, isExpanded ] );
  ```
- **Additional a11y features:**
  - `useInstanceId(ComboboxControl, 'combobox-control')` (line 147) generates prefixed unique IDs to avoid collisions with `FormTokenField` instances on the same page (see GitHub issue #42112).
  - `withFocusOutside` wraps the entire component via `DetectOutside` (lines 47-57) to close the dropdown when focus leaves.
  - Arrow key navigation cycles through suggestions (lines 186-199) with wrap-around.
