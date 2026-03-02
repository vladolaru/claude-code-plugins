# External Accessibility Standards Research

> Research document for AI agent consumption. Part of the a11y research series.
> Generated: 2026-02-27 | Session 3, Task 3.2

## 1. WAI-ARIA APG Pattern Reference

### 1.1 Dialog (Modal)

**APG Reference:** https://www.w3.org/WAI/ARIA/apg/patterns/dialog-modal/

**Required roles:**
- `role="dialog"` on the container element

**Required states/properties:**
- `aria-modal="true"` on the dialog element
- `aria-labelledby` referencing a visible title, OR `aria-label`
- Optional: `aria-describedby` for descriptive content (but advisable to omit when dialog contains complex semantic structures like lists/tables)

**Required keyboard interactions:**
- Tab: Move focus to next tabbable element inside dialog (wraps to first)
- Shift+Tab: Move focus to previous tabbable element (wraps to last)
- Escape: Close the dialog

**Focus management requirements:**
- Focus moves into dialog on open (placement depends on content complexity)
- On close, focus returns to invoking element (unless it no longer exists)
- For complex content, add `tabindex="-1"` to a static element at start and focus it initially
- Include a visible close button in tab sequence

**Gutenberg component:** `Modal` (`packages/components/src/modal/`)

**Compliance assessment:**
- CORRECT: Uses `role="dialog"` (configurable via `role` prop, defaults to `"dialog"`)
- CORRECT: Supports `aria-labelledby` (auto-generated from title) and `aria-label` (via `contentLabel` prop)
- CORRECT: Supports `aria-describedby` (via `aria.describedby` prop)
- CORRECT: Escape key closes dialog (with `shouldCloseOnEsc` toggle)
- CORRECT: Focus trapping via `useConstrainedTabbing()` hook
- CORRECT: Focus return via `useFocusReturn()` hook
- CORRECT: Focus on mount via `useFocusOnMount()` hook with `firstContentElement` support
- CORRECT: Portal rendering via `createPortal(modal, document.body)`
- CORRECT: Visible close button with accessible label
- DEVIATION: Does NOT use `aria-modal="true"`. Instead uses manual `aria-hidden` on sibling elements via `aria-helper.ts` (`modalize`/`unmodalize`). This is a deliberate workaround for Safari bugs with `aria-modal`. The comment in source explicitly notes future removal in favor of `aria-modal="true"`.
- DEVIATION: Uses `role="document"` on content wrapper, which is non-standard for the APG dialog pattern but aids in assistive technology navigation of scrollable content.

**Notes:** The `aria-hidden` sibling approach is functionally equivalent to `aria-modal="true"` but more brittle. The implementation preserves live regions (elements with `aria-live` or live region roles like `alert`, `status`) from being hidden, which is correct behavior.

---

### 1.2 Combobox

**APG Reference:** https://www.w3.org/WAI/ARIA/apg/patterns/combobox/

**Required roles:**
- `role="combobox"` on the input element
- `role="listbox"` (or `grid`, `tree`, `dialog`) on the popup element
- `role="option"` on each selectable item in a listbox popup

**Required states/properties:**
- `aria-expanded`: `false` when popup hidden, `true` when visible
- `aria-controls`: references the popup element
- `aria-autocomplete`: `none`, `list`, or `both` (indicates filtering behavior)
- `aria-activedescendant`: on combobox, references currently focused popup element
- `aria-selected="true"` on selected options
- Label via `<label>`, `aria-labelledby`, or `aria-label`
- If popup is not listbox, `aria-haspopup` must be set to corresponding type

**Required keyboard interactions:**
- Tab: Included in page tab sequence
- Down Arrow: Open popup or move focus into it
- Escape: Close popup; optionally clear value
- Enter: Accept autocomplete suggestion
- Up/Down Arrow (in popup): Navigate options
- Printable characters: Type in editable combobox

**Optional keyboard interactions:**
- Alt+Down Arrow: Display popup without moving focus
- Alt+Up Arrow: Close popup
- Home/End: Move to first/last option or cursor position

**Gutenberg component:** `ComboboxControl` (`packages/components/src/combobox-control/`)

**Compliance assessment:**
- CORRECT: Uses `role="combobox"` on input (via TokenInput component)
- CORRECT: Uses `role="listbox"` on suggestions list
- CORRECT: `aria-expanded` toggles with popup state
- CORRECT: `aria-autocomplete="list"` set on input
- CORRECT: `aria-activedescendant` managed based on selected suggestion index
- CORRECT: Escape closes popup
- CORRECT: Enter selects suggestion
- CORRECT: Up/Down arrows navigate suggestions with wrapping
- CORRECT: Live region announcements for result count via `speak()` with `polite` politeness
- CORRECT: Selection announcement via `speak()` with `assertive` politeness
- GAP: Missing `aria-controls` attribute linking input to listbox (the TokenInput does not set `aria-controls`)
- DEVIATION: Uses class-based `withFocusOutside` HOC for focus management rather than modern patterns
- NOTE: IME event handling via `withIgnoreIMEEvents` prevents interference with CJK input methods

---

### 1.3 Tabs

**APG Reference:** https://www.w3.org/WAI/ARIA/apg/patterns/tabs/

**Required roles:**
- `tablist` on the container for tabs
- `tab` on each individual tab element
- `tabpanel` on each content panel

**Required states/properties:**
- `aria-selected`: `true` on active tab, `false` on others
- `aria-controls`: each tab references its associated tabpanel
- `aria-labelledby`: tabpanel references its associated tab
- `aria-label` or `aria-labelledby` on the tablist
- Optional: `aria-orientation` (set to `vertical` for vertical tabs; default is horizontal)

**Required keyboard interactions:**
- Tab: Move focus into/out of tab list (does NOT cycle between tabs)
- Left/Right Arrow (horizontal): Navigate between tabs
- Up/Down Arrow (vertical): Navigate between tabs
- Space/Enter: Activate tab (if not auto-activated)
- APG recommends: tabs activate automatically when they receive focus (unless content loads with latency)

**Optional keyboard interactions:**
- Home/End: Navigate to first/last tab
- Delete: Close tab (if closeable)

**Gutenberg component:** `Tabs` (`packages/components/src/tabs/`)

**Compliance assessment:**
- CORRECT: Built on Ariakit's `useTabStore`, `Tab`, `TabList`, `TabPanel` -- Ariakit handles `tablist`, `tab`, `tabpanel` roles automatically
- CORRECT: `aria-selected` managed by Ariakit
- CORRECT: Tab-panel linking via Ariakit's `tabId` prop
- CORRECT: Supports `selectOnMove` prop (auto-activation, defaults to `true` per APG recommendation)
- CORRECT: Supports `orientation` prop (horizontal/vertical)
- CORRECT: RTL support via `isRTL()` passed to Ariakit store
- CORRECT: Arrow key navigation handled by Ariakit
- NOTE: This is one of Gutenberg's best-implemented patterns due to full Ariakit delegation

---

### 1.4 Menu / Menu Button

**APG Reference:** https://www.w3.org/WAI/ARIA/apg/patterns/menu-button/

**Required roles:**
- `role="button"` on the trigger
- `role="menu"` on the menu container
- `role="menuitem"`, `role="menuitemradio"`, or `role="menuitemcheckbox"` on each item

**Required states/properties:**
- `aria-haspopup="menu"` or `"true"` on the button
- `aria-expanded`: `true` when menu visible, `false` when hidden
- Optional: `aria-controls` on button referencing menu element

**Required keyboard interactions:**
- Enter/Space on button: Open menu, focus first item
- Down Arrow on button (optional): Open menu, focus first item
- Up Arrow on button (optional): Open menu, focus last item
- Once menu open: arrow key navigation per Menu pattern

**Gutenberg component:** `DropdownMenu` (`packages/components/src/dropdown-menu/`)

**Compliance assessment:**
- CORRECT: Uses `aria-haspopup="true"` on toggle button
- CORRECT: `aria-expanded` reflects open state
- CORRECT: `role="menu"` on NavigableMenu
- CORRECT: Supports `menuitem`, `menuitemradio`, `menuitemcheckbox` roles on items
- CORRECT: `aria-checked` set for menuitemcheckbox/menuitemradio roles
- CORRECT: Down arrow opens menu (unless `disableOpenOnArrowDown` is true)
- PARTIAL: Arrow navigation within menu via `NavigableMenu` (class-based component using DOM focus management), not via Ariakit. This is functional but uses legacy patterns.
- GAP: Enter/Space on button only toggles via click handler, does not explicitly focus first menu item on open. Focus management depends on the Popover's `focusOnMount` behavior.
- GAP: Missing `aria-controls` linking button to menu

---

### 1.5 Disclosure

**APG Reference:** https://www.w3.org/WAI/ARIA/apg/patterns/disclosure/

**Required roles:**
- `role="button"` on the control element

**Required states/properties:**
- `aria-expanded`: `true` when content visible, `false` when hidden
- Optional: `aria-controls` referencing the content element

**Required keyboard interactions:**
- Enter: Toggle content visibility
- Space: Toggle content visibility

**Gutenberg component:** `Dropdown` (`packages/components/src/dropdown/`)

**Compliance assessment:**
- PARTIAL: `Dropdown` delegates toggle rendering to consumers via `renderToggle`. The component documentation example shows using a `Button` with `aria-expanded` set manually by the consumer. The component itself does NOT enforce disclosure semantics.
- GAP: `aria-expanded` is NOT automatically managed -- consumers must set it in `renderToggle`
- GAP: No `aria-controls` linking
- NOTE: The `Dropdown` component is more of a generic popover trigger than a true disclosure pattern. True disclosure semantics depend entirely on the consumer's implementation.

---

### 1.6 Listbox

**APG Reference:** https://www.w3.org/WAI/ARIA/apg/patterns/listbox/

**Required roles:**
- `role="listbox"` on the container
- `role="option"` on each item
- `role="group"` for grouped options (optional)

**Required states/properties:**
- `aria-label` or `aria-labelledby` on the listbox
- `aria-multiselectable="true"` for multi-select
- `aria-selected` or `aria-checked` on all options
- `aria-setsize`/`aria-posinset` for dynamic lists
- `aria-orientation` if horizontal (default vertical)

**Required keyboard interactions:**
- Arrow keys: Navigate between options
- Home/End: Jump to first/last (recommended for 5+ options)
- Type-ahead: Focus moves to matching option
- Space (multi-select): Toggle selection
- Shift+Arrow (multi-select): Toggle selection while navigating

**Gutenberg component:** `CustomSelectControl v2` (`packages/components/src/custom-select-control-v2/`)

**Compliance assessment:**
- CORRECT: Built on Ariakit's `Select`, `SelectPopover`, `SelectItem` -- Ariakit handles listbox/option roles
- CORRECT: `SelectLabel` provides accessible label via Ariakit
- CORRECT: Keyboard navigation handled by Ariakit
- CORRECT: Supports multi-value display (shows count: "N items selected")
- NOTE: Uses Ariakit's Select component (based on Combobox pattern per Ariakit docs), which provides comprehensive keyboard support automatically

---

### 1.7 Slider

**APG Reference:** https://www.w3.org/WAI/ARIA/apg/patterns/slider/

**Required roles:**
- `role="slider"` on the focusable control element

**Required states/properties:**
- `aria-valuenow`: current value
- `aria-valuemin`: minimum value
- `aria-valuemax`: maximum value
- `aria-labelledby` or `aria-label`
- `aria-orientation="vertical"` if vertical (default horizontal)
- `aria-valuetext` when numeric value is not user-friendly

**Required keyboard interactions:**
- Right/Up Arrow: Increment by one step
- Left/Down Arrow: Decrement by one step
- Home: Set to minimum
- End: Set to maximum
- Optional: Page Up/Down for larger increments

**Gutenberg component:** `RangeControl` (`packages/components/src/range-control/`)

**Compliance assessment:**
- CORRECT: Uses native `<input type="range">` which provides implicit `slider` role
- CORRECT: `aria-label` set on the range input (from `label` prop)
- CORRECT: `aria-describedby` linked to help text
- CORRECT: `min`, `max`, `step` attributes set on native input (browsers handle `aria-valuemin`, `aria-valuemax`, `aria-valuenow` automatically for native range inputs)
- CORRECT: Keyboard increment/decrement handled natively by the browser
- GAP: No `aria-valuetext` support. When displaying non-numeric values (like in RangeControl with custom tooltip content), screen readers only hear the raw number.
- GAP: No Home/End keyboard support (native `<input type="range">` does support this in most browsers, but it depends on browser implementation)
- BONUS: Companion `NumberControl` input field provides an alternative input method with `aria-label`

---

### 1.8 Tooltip

**APG Reference:** https://www.w3.org/WAI/ARIA/apg/patterns/tooltip/

**Required roles:**
- `role="tooltip"` on the tooltip container

**Required states/properties:**
- `aria-describedby` on trigger element, referencing the tooltip

**Required keyboard interactions:**
- Escape: Dismiss tooltip
- Focus on trigger: Show tooltip
- Blur from trigger: Hide tooltip

**Implementation notes:**
- Tooltips do NOT receive keyboard focus
- For tooltips with interactive content, use a dialog pattern instead
- APG status: work-in-progress, not fully consensus-approved

**Gutenberg component:** `Tooltip` (`packages/components/src/tooltip/`)

**Compliance assessment:**
- CORRECT: Built on Ariakit's `TooltipAnchor` and `Tooltip` -- Ariakit provides `role="tooltip"` automatically
- CORRECT: `aria-describedby` manually managed via `addDescribedById` function (workaround for Ariakit 0.4.0 change)
- CORRECT: Show on focus, hide on blur handled by Ariakit
- CORRECT: Show delay (700ms default) prevents accidental triggers
- CORRECT: `hideOnClick` prop (default true) dismisses on click
- CORRECT: Nested tooltip prevention via `TooltipInternalContext`
- CORRECT: Smart `aria-describedby` handling -- skips when tooltip text equals `aria-label` (avoiding redundant announcements)
- GAP: No explicit Escape key handling to dismiss tooltip (depends on Ariakit's implementation)
- NOTE: Tooltip supports both text and keyboard shortcut display

---

### 1.9 Tree View

**APG Reference:** https://www.w3.org/WAI/ARIA/apg/patterns/treeview/

**Required roles:**
- `role="tree"` on the container
- `role="treeitem"` on each node
- `role="group"` for child node containers

**Required states/properties:**
- `aria-expanded`: `true`/`false` on parent nodes only (NOT on leaf nodes)
- `aria-selected` or `aria-checked` for selection state
- `aria-multiselectable` for multi-select trees
- `aria-labelledby` or `aria-label` on tree container
- `aria-level`, `aria-setsize`, `aria-posinset` for dynamic trees
- `aria-owns` for non-DOM-order root nodes

**Required keyboard interactions:**
- Up/Down Arrow: Navigate nodes
- Right Arrow: Open closed node, or move to first child
- Left Arrow: Close open node, or move to parent
- Home/End: First/last focusable node
- Enter: Activate node (default action)
- Type-ahead: Character-based navigation
- Space (multi-select): Toggle selection

**Gutenberg component:** `TreeSelect` (`packages/components/src/tree-select/`)

**Compliance assessment:**
- MAJOR GAP: `TreeSelect` does NOT implement the tree view pattern at all. It renders a flat `<select>` element (via `SelectControl`) with indentation via non-breaking spaces in option labels. This means:
  - No `role="tree"` or `role="treeitem"`
  - No `aria-expanded` for parent nodes
  - No arrow key tree navigation
  - No type-ahead navigation
  - No collapse/expand behavior
  - Hierarchy is only conveyed visually via indentation, not semantically
- NOTE: This is a significant accessibility gap. The component name implies tree semantics but provides only a flat select with visual hierarchy. Screen reader users cannot perceive the parent-child relationships.

---

### 1.10 Grid

**APG Reference:** https://www.w3.org/WAI/ARIA/apg/patterns/grid/

**Required roles:**
- `role="grid"` on the container
- `role="row"` on each row
- `role="gridcell"` on standard cells
- `role="columnheader"` / `role="rowheader"` for headers
- `role="rowgroup"` for row grouping (optional)

**Required states/properties:**
- `aria-labelledby` or `aria-label` on the grid
- `aria-selected` for cell/row selection
- `aria-sort` on sorted column headers
- `aria-readonly` for non-editable grids
- `aria-colcount`/`aria-rowcount` for dynamic grids

**Required keyboard interactions (data grid):**
- Arrow keys: Move focus between cells
- Home/End: First/last cell in row
- Ctrl+Home/End: First/last cell in grid
- Page Up/Down: Scroll through rows
- Tab: Move to next focusable element outside grid (only one tab stop per grid)

**Required keyboard interactions (layout grid):**
- Arrow keys with optional wrapping
- Home/End for row boundaries

**Gutenberg component:** `DateTimePicker` calendar (`packages/components/src/date-time/`)

**Compliance assessment:**
- MAJOR GAP: The DateTimePicker calendar does NOT use `role="grid"`. There is no grid pattern implementation for the calendar.
- NOTE: This is a common pattern that many date pickers implement. The APG grid pattern is the recommended approach for calendar widgets.

---

### 1.11 Alert

**APG Reference:** https://www.w3.org/WAI/ARIA/apg/patterns/alert/

**Required roles:**
- `role="alert"` on the alert container

**Required states/properties:**
- None beyond the alert role itself

**Keyboard interactions:**
- Not applicable (alerts should not require keyboard interaction)

**Implementation notes:**
- Dynamically rendered alerts are automatically announced by screen readers
- Alerts should NOT auto-disappear (WCAG 2.2.3 violation risk)
- Alerts should NOT steal focus (should not interfere with user workflow)
- Excessive alerts impact cognitive accessibility (WCAG 2.2.4)
- Alerts present before page load are NOT announced
- Use Alert Dialog when interruption is necessary

**Gutenberg component:** `Notice` (`packages/components/src/notice/`), `Snackbar` (`packages/components/src/snackbar/`)

**Compliance assessment:**

**Notice:**
- CORRECT: Uses `speak()` from `@wordpress/a11y` to announce message via live regions
- CORRECT: Politeness level mapped to status: `assertive` for errors, `polite` for success/warning/info
- CORRECT: Includes visually hidden status label ("Warning notice", "Error notice", etc.)
- GAP: Does NOT use `role="alert"` on the container. Relies entirely on `speak()` for screen reader announcements. This means the visible DOM element has no alert semantics -- if `speak()` fails or a screen reader does not support it, the notice may be missed.
- CORRECT: Does not auto-dismiss (persistent until user action)

**Snackbar:**
- CORRECT: Uses `speak()` for announcements
- CORRECT: Default politeness is `polite`
- VIOLATION: Auto-dismisses after 6000ms (NOTICE_TIMEOUT). APG explicitly warns that alerts should not disappear automatically as it risks violating WCAG 2.2.3 (No Timing). Users who need more time to read cannot access the message.
- GAP: No `role="alert"` or `role="status"` on the container
- ISSUE: Uses `onKeyPress` (deprecated) instead of `onKeyDown` for dismiss interaction
- ISSUE: Explicit dismiss uses a `<span role="button">` instead of an actual `<button>` element

---

### 1.12 Breadcrumb

**APG Reference:** https://www.w3.org/WAI/ARIA/apg/patterns/breadcrumb/

**Required roles:**
- Navigation landmark (`<nav>` or `role="navigation"`)

**Required states/properties:**
- `aria-label` or `aria-labelledby` on the navigation element
- `aria-current="page"` on the link representing the current page

**Keyboard interactions:**
- Not applicable (standard link navigation)

**Gutenberg component:** No dedicated breadcrumb component exists.

**Compliance assessment:**
- NOT APPLICABLE: Gutenberg does not have a dedicated breadcrumb component. Breadcrumb-like patterns in the editor (block hierarchy display) use custom implementations.

---

### 1.13 Checkbox

**APG Reference:** https://www.w3.org/WAI/ARIA/apg/patterns/checkbox/

**Required roles:**
- `role="checkbox"` on the interactive element (or native `<input type="checkbox">`)

**Required states/properties:**
- `aria-checked`: `true`, `false`, or `mixed` (tri-state)
- Accessible label via text content, `aria-labelledby`, or `aria-label`
- `aria-describedby` for additional descriptions
- `role="group"` with `aria-labelledby` for grouping related checkboxes

**Required keyboard interactions:**
- Space: Toggle checkbox state

**Gutenberg component:** `CheckboxControl` (`packages/components/src/checkbox-control/`)

**Compliance assessment:**
- CORRECT: Uses native `<input type="checkbox">` (implicit checkbox role)
- CORRECT: Supports `indeterminate` state (tri-state) via JavaScript property
- CORRECT: Label associated via `<label htmlFor>`
- CORRECT: `aria-describedby` linked to help text
- CORRECT: Space to toggle handled by native browser behavior
- CORRECT: Visual indicators for checked/indeterminate states with `role="presentation"` on decorative icons
- NOTE: Includes Safari compat code to ensure focus on click (`event.currentTarget.focus()`)

---

### 1.14 Radio Group

**APG Reference:** https://www.w3.org/WAI/ARIA/apg/patterns/radio/

**Required roles:**
- `role="radiogroup"` on the container (or `<fieldset>`)
- `role="radio"` on each radio button (or native `<input type="radio">`)

**Required states/properties:**
- `aria-checked`: `true` on selected, `false` on others
- `aria-labelledby` or `aria-label` on the group
- `aria-describedby` for additional info
- Label on each radio via content, `aria-labelledby`, or `aria-label`

**Required keyboard interactions:**
- Tab/Shift+Tab: Move focus into/out of group (lands on checked button, or first if none selected)
- Space: Check focused button
- Right/Down Arrow: Move to next, check it (wraps)
- Left/Up Arrow: Move to previous, check it (wraps)

**Gutenberg component:** `RadioControl` (`packages/components/src/radio-control/`)

**Compliance assessment:**
- CORRECT: Uses `<fieldset>` as group container (implicit radiogroup semantics)
- CORRECT: Uses native `<input type="radio">` elements
- CORRECT: `<legend>` element for group label (with `VisuallyHidden` option)
- CORRECT: Label via `<label htmlFor>` for each radio
- CORRECT: `aria-describedby` linked to help text and option descriptions
- CORRECT: Native radio group keyboard behavior (arrow key navigation, Tab in/out) handled by browser
- NOTE: Uses `name` attribute to group radios, ensuring native browser behavior works correctly
- NOTE: Includes Safari compat code for focus on click

---

### 1.15 Switch

**APG Reference:** https://www.w3.org/WAI/ARIA/apg/patterns/switch/

**Required roles:**
- `role="switch"` on the element

**Required states/properties:**
- `aria-checked`: `true` when on, `false` when off
- Accessible label via text content, `aria-labelledby`, or `aria-label`
- CRITICAL: Label must NOT change when state changes
- `aria-describedby` for additional descriptions
- `role="group"` or `<fieldset>` for grouping multiple switches

**Required keyboard interactions:**
- Space: Toggle switch state (required)
- Enter: Toggle switch state (optional)

**Gutenberg components:** `ToggleControl` (`packages/components/src/toggle-control/`), `FormToggle` (`packages/components/src/form-toggle/`)

**Compliance assessment:**
- MAJOR GAP: `FormToggle` uses `<input type="checkbox">` without `role="switch"`. Screen readers announce it as a checkbox, not a switch. The visual presentation is a toggle switch, but semantics are checkbox.
- GAP: No `aria-checked` attribute (relies on native `checked` property which does not communicate switch semantics)
- CORRECT: Label is external and does not change with state (via `ToggleControl` wrapper)
- CORRECT: `aria-describedby` linked to help text
- CORRECT: Space to toggle handled by native checkbox behavior
- NOTE: The APG explicitly defines `switch` as a distinct role from `checkbox`. Using `role="switch"` on the input would fix this gap with minimal code change. This is a common oversight in many component libraries.

---

### 1.16 Toolbar

**APG Reference:** https://www.w3.org/WAI/ARIA/apg/patterns/toolbar/

**Required roles:**
- `role="toolbar"` on the container

**Required states/properties:**
- `aria-labelledby` or `aria-label` on the toolbar
- `aria-orientation="vertical"` if vertical (default horizontal)

**Required keyboard interactions:**
- Tab/Shift+Tab: Move focus into/out of toolbar (one tab stop)
- Left/Right Arrow (horizontal): Navigate between controls
- Up/Down Arrow (vertical): Navigate between controls
- Optional: Home/End to first/last control
- Roving tabindex for focus management

**Implementation notes:**
- Only one toolbar stop in the tab sequence
- Use roving tabindex to manage internal focus
- Avoid widgets requiring arrow keys that conflict with toolbar navigation
- Apply toolbar grouping only for 3+ controls
- Consider making disabled controls focusable for discoverability

**Gutenberg component:** `ToggleGroupControl` (`packages/components/src/toggle-group-control/`)

**Compliance assessment:**
- NOTE: `ToggleGroupControl` when used as a radio group delegates to Ariakit's `RadioGroup`. It does NOT use `role="toolbar"`.
- CORRECT: Uses `aria-label` on the RadioGroup
- CORRECT: Arrow key navigation via Ariakit's RadioGroup
- CORRECT: RTL support via `isRTL()`
- GAP: No `role="toolbar"` implementation. The actual Gutenberg block toolbar is implemented in `@wordpress/block-editor`, not in the `@wordpress/components` package. The components package has `NavigableContainer` which provides arrow key navigation but without explicit toolbar role.
- NOTE: The Gutenberg editor's block toolbar in `@wordpress/block-editor` does implement toolbar patterns, but the reusable components package lacks a standalone `Toolbar` component with proper role.

---

### 1.17 Spinbutton

**APG Reference:** https://www.w3.org/WAI/ARIA/apg/patterns/spinbutton/

**Required roles:**
- `role="spinbutton"` on the focusable element

**Required states/properties:**
- `aria-valuenow`: current value
- `aria-valuemin`: minimum value (if applicable)
- `aria-valuemax`: maximum value (if applicable)
- `aria-valuetext`: human-readable string when numeric value is unclear
- `aria-labelledby` or `aria-label`
- `aria-invalid="true"` when value is out of range

**Required keyboard interactions:**
- Up Arrow: Increase value
- Down Arrow: Decrease value
- Home: Set to minimum (if defined)
- End: Set to maximum (if defined)
- Standard text editing keys
- Optional: Page Up/Down for larger increments

**Implementation notes:**
- Focus stays on text field during all operations
- Do not interfere with browser text editing functions
- Increment/decrement buttons should NOT receive focus

**Gutenberg component:** `NumberControl` (`packages/components/src/number-control/`)

**Compliance assessment:**
- CORRECT: Uses `<input>` with `type="number"` and `inputMode="numeric"`, which provides implicit spinbutton semantics in some browsers
- CORRECT: Up/Down Arrow increment/decrement via state reducer (`PRESS_UP`/`PRESS_DOWN` actions)
- CORRECT: Shift+Up/Down for larger step increments (`shiftStep` prop)
- CORRECT: Value clamping to min/max on commit (Enter/blur)
- CORRECT: Custom spin buttons have `label` ("Increment"/"Decrement") and use `<Button>` elements
- CORRECT: `min`, `max`, `step` attributes set on input
- GAP: No explicit `role="spinbutton"` (relies on browser's implicit role for `type="number"`)
- GAP: No `aria-valuenow`, `aria-valuemin`, `aria-valuemax` attributes (relies on native `value`, `min`, `max` attributes)
- GAP: No `aria-valuetext` support
- GAP: No `aria-invalid` state management
- GAP: No Home/End key handling for jump-to-min/max
- BONUS: Supports drag-to-change value (unique UX enhancement)


## 2. APG Practices Reference

### 2.1 Landmark Regions

**APG Reference:** https://www.w3.org/WAI/ARIA/apg/practices/landmark-regions/

**Key landmark types:**

| Landmark | HTML Element | ARIA Role | Usage |
|----------|-------------|-----------|-------|
| Banner | `<header>` (top-level) | `banner` | Site identity, logo, search. One per page. |
| Main | `<main>` | `main` | Primary content. One per page. |
| Navigation | `<nav>` | `navigation` | Navigation links. Multiple allowed with unique labels. |
| Complementary | `<aside>` | `complementary` | Supporting content meaningful when separated. |
| Content Info | `<footer>` (top-level) | `contentinfo` | Footer info. One per page. |
| Search | N/A | `search` | Search functionality. Use instead of `form` for search. |
| Form | `<form>` (with label) | `form` | Form item collection. Requires visible label. |
| Region | `<section>` (with label) | `region` | Generic perceivable section. Must have label. |

**Best practices:**
1. Include ALL perceivable content in a landmark region
2. Multiple instances of same landmark need unique labels
3. Do not include role name in label (avoid "Site Navigation Navigation")
4. Do not wrap modal content in landmarks (only perceivable when open)
5. Use native HTML elements over ARIA roles when possible

**Gutenberg editor landmark usage:**
- The block editor uses landmarks for major UI regions (sidebar, toolbar, content area)
- `@wordpress/interface` package manages editor layout with landmark regions
- Individual blocks/components within the editor typically do NOT need landmarks
- The Popover/Modal components correctly avoid landmark wrapping

**Gaps in Gutenberg landmark usage:**
- No systematic enforcement of landmark coverage in the editor UI
- Some supplementary UI regions may lack landmark designation
- Plugin/extension content injected into the editor may fall outside landmarks

---

### 2.2 Names and Descriptions

**APG Reference:** https://www.w3.org/WAI/ARIA/apg/practices/names-and-descriptions/

**Accessible name computation order (priority):**
1. `aria-labelledby` (highest priority -- concatenates referenced elements' text)
2. `aria-label`
3. Host-language attributes (`<label>`, `<caption>`, `<figcaption>`, `alt`, `<legend>`)
4. Child content (for elements that support name from content)
5. Fallback attributes (`title`, `placeholder` -- least discoverable)

**When to use each technique:**

| Situation | Approach |
|-----------|----------|
| Visible text label available | Use native HTML (`<label>`, `<caption>`, `<legend>`) |
| Need to reference existing text | Use `aria-labelledby` |
| Invisible label required | Use `aria-label` |
| Multiple elements compose one name | Use `aria-labelledby` (concatenates) |
| Hidden reference acceptable | Use `aria-labelledby` (includes hidden content) |

**Critical warning:** Using `aria-label` or `aria-labelledby` will HIDE descendant content from assistive tech for elements like buttons, links, and checkboxes. Avoid overriding visible content except in rare cases.

**Description patterns:**
- Primary: `aria-describedby` (references another element)
- Fallback: `title` attribute (least discoverable)
- Tables/figures: `<caption>`, `<figcaption>`

**Five cardinal rules:**
1. Test thoroughly for cross-browser consistency
2. Prefer visible text for maintenance and accessibility
3. Use native HTML over ARIA when possible
4. Avoid browser fallbacks like `title` and `placeholder`
5. Keep names brief (1-3 words) yet distinctive and functional

**Gutenberg alignment:**
- Gutenberg components consistently use `BaseControl` with `label` prop for accessible naming
- `hideLabelFromVision` option available on most form controls (uses `VisuallyHidden`)
- `aria-describedby` linked to help text via instance IDs
- Button component uses `label` prop for `aria-label` when icon-only
- Some components use `aria-label` where `aria-labelledby` would be more robust (e.g., `RangeControl` uses `aria-label` on the range input even when a visible label exists)

---

### 2.3 Keyboard Interface

**APG Reference:** https://www.w3.org/WAI/ARIA/apg/practices/keyboard-interface/

**Core principles:**
- ALL interactive elements must be operable via keyboard
- Tab and Shift+Tab move between UI components
- Arrow keys move within composite widgets
- Only one focusable element per composite widget in the tab sequence

**Two focus management strategies:**

1. **Roving tabindex:**
   - `tabindex="0"` on currently focused element
   - `tabindex="-1"` on all other focusable elements
   - Update dynamically as user navigates with arrow keys
   - Gutenberg usage: `NavigableContainer` uses DOM focus management

2. **aria-activedescendant:**
   - Container keeps DOM focus
   - `aria-activedescendant` property indicates logically active child
   - Gutenberg usage: `ComboboxControl` uses this for suggestion list navigation

**Focus visibility requirements:**
1. Visibility: Focus indicator must be easily distinguishable
2. Persistence: Always a visible active element
3. Predictability: Focus movement matches reading order

**Keyboard conflicts to avoid:**
- Modifier keys + Tab/Enter/Space/Escape
- Meta key + single keys
- Alt + function keys
- Caps Lock / Insert / Scroll Lock as modifiers

**Gutenberg alignment:**
- `NavigableContainer` (class-based) provides arrow key navigation for Menu and Tabbable container variants
- `useConstrainedTabbing` traps Tab within modals
- `useFocusOnMount` manages initial focus placement
- `useFocusReturn` returns focus on unmount
- Ariakit-based components (Tabs, CustomSelectControl, ToggleGroupControl) use roving tabindex automatically
- Legacy components (DropdownMenu, NavigableContainer) use direct DOM focus management


## 3. Ariakit Integration Patterns

### 3.1 Ariakit Architecture

**Component composition model:**
Ariakit uses a `render` prop pattern as its primary composition mechanism. The `render` prop enables replacing the default HTML element or enhancing it with custom components.

Two forms:
- **Element-based:** `render={<textarea rows={5} />}` -- pass JSX elements directly
- **Function-based:** `render={(props) => <CustomComponent {...props} />}` -- custom prop merging

**Automatic prop merging:**
When using HTML elements with the render prop, Ariakit automatically merges:
- `style` and `className` attributes
- `ref` props
- Event handlers (chained, not replaced)
- Other props: rendered element's props take precedence over component defaults

**Component openness requirement:**
For custom components to work with Ariakit's composition:
1. Spread all incoming props to underlying elements
2. Forward and merge the `ref` prop
3. Merge `style` and `className`
4. Chain event handlers rather than replacing them

**State management:**
- Store-based pattern: `useTabStore()`, `useMenuStore()`, etc.
- `useStoreState(store, 'propertyName')` for reactive access to store properties
- Provider components for context distribution

**What Ariakit handles automatically:**
- ARIA roles and states on elements
- Keyboard navigation within composite widgets
- Focus management (roving tabindex)
- RTL support (when `rtl` prop is passed to store)
- Popup positioning (via Floating UI)
- Portal rendering
- Animation lifecycle (waits for transitions before hiding)

**What developers must add:**
- Accessible labels (`aria-label`, label components)
- Custom styling for focus/active/selected states (via `data-active-item`, `data-focus-visible`)
- Business logic and callbacks
- Content structure

### 3.2 Ariakit Components Used by Gutenberg

#### Tabs (`@ariakit/react` Tab, TabList, TabPanel, useTabStore)
- **Gutenberg usage:** `Tabs` component (`packages/components/src/tabs/`)
- **Ariakit provides:** `tablist`/`tab`/`tabpanel` roles, `aria-selected`, arrow key navigation, `selectOnMove`, orientation support, RTL support
- **Gutenberg customizes:** Instance-based tab IDs (prefix with instanceId), `selectOnMove` default, focus sync via `requestAnimationFrame`
- **Known issues:** Focus sync workaround needed when Ariakit recomputes activeId

#### RadioGroup (`@ariakit/react` RadioGroup, useRadioStore)
- **Gutenberg usage:** `ToggleGroupControl` (as-radio-group variant)
- **Ariakit provides:** `radiogroup` role, `aria-checked`, arrow key navigation, roving tabindex
- **Gutenberg customizes:** Custom value types (string/number), RTL support, active ID reset on value clear
- **Configuration:** `rtl: isRTL()` passed to store

#### Select (`@ariakit/react` Select, SelectPopover, SelectItem, SelectLabel, useStoreState)
- **Gutenberg usage:** `CustomSelectControl v2`
- **Ariakit provides:** Combobox-based select semantics, listbox popup, keyboard navigation, selection management
- **Gutenberg customizes:** `showOnKeyDown` behavior differs between legacy and new modes, custom button rendering with `InputBase` wrapper, `sameWidth` popover, key event propagation control

#### Tooltip (`@ariakit/react` Tooltip, TooltipAnchor, useTooltipStore, useStoreState)
- **Gutenberg usage:** `Tooltip` component
- **Ariakit provides:** `role="tooltip"`, show/hide timing, focus-based display, positioning
- **Gutenberg customizes:** Manual `aria-describedby` management (workaround for Ariakit 0.4.0 change), nested tooltip prevention, `hideOnClick` behavior, shortcut display
- **Known issues:** `aria-describedby` must be added manually since Ariakit 0.4.0 no longer passes it to anchors

#### Role (`@ariakit/react` Role)
- **Gutenberg usage:** Tooltip nested component fallback
- **Ariakit provides:** Generic render delegation without additional semantics

### 3.3 Ariakit Components NOT Used by Gutenberg (but available)

These Ariakit components have Gutenberg equivalents that use custom implementations:

| Ariakit Component | Gutenberg Equivalent | Status |
|---|---|---|
| `Dialog` | `Modal` | Custom implementation (uses `useFocusOnMount`, `useConstrainedTabbing`, `useFocusReturn` hooks) |
| `Disclosure` | `Dropdown` | Custom implementation (render prop pattern) |
| `Menu`, `MenuButton` | `DropdownMenu`, `NavigableMenu` | Custom implementation (class-based NavigableContainer) |
| `Combobox` | `ComboboxControl` | Custom implementation (uses TokenInput + SuggestionsList) |
| `Composite` | `NavigableContainer` | Custom implementation (class-based, DOM event listeners) |
| `Popover` | `Popover` | Gutenberg's Popover uses Floating UI directly, not Ariakit |

### 3.4 When to Use Ariakit vs Custom Implementation

**Decision criteria:**

Use Ariakit when:
1. The component maps to a well-defined APG pattern (Tabs, Select, RadioGroup, Tooltip)
2. Focus management complexity is high (roving tabindex, activedescendant)
3. The pattern requires composite widget keyboard navigation
4. RTL support is needed
5. Popup positioning is required

Use custom implementation when:
1. The component is a simple wrapper around native HTML (CheckboxControl, RadioControl)
2. Native browser behavior already provides correct semantics (`<input type="checkbox">`, `<input type="radio">`, `<input type="range">`)
3. The pattern does not exist in APG (custom editor interactions)
4. Existing custom implementation is well-tested and stable (Modal)
5. The component needs behavior Ariakit does not provide

**Migration considerations:**
- Gutenberg is progressively migrating to Ariakit (Tabs was migrated, DropdownMenu v2 was planned)
- Custom implementations should be preserved when they are correct and well-tested
- Migration should prioritize components with known accessibility gaps
- Ariakit version compatibility must be tracked (e.g., aria-describedby change in 0.4.0)

**Ariakit's APG coverage:**

| APG Pattern | Ariakit Coverage |
|---|---|
| Dialog | Yes (Dialog, AlertDialog) |
| Combobox | Yes (Combobox, ComboboxPopover) |
| Tabs | Yes (Tab, TabList, TabPanel) |
| Menu/Menu Button | Yes (Menu, MenuButton, MenuItem) |
| Disclosure | Yes (Disclosure, DisclosureContent) |
| Listbox/Select | Yes (Select, SelectPopover, SelectItem) |
| Tooltip | Yes (Tooltip, TooltipAnchor) |
| Toolbar | Yes (Toolbar via Composite) |
| Composite | Yes (Composite, CompositeItem, CompositeRow) |
| Radio Group | Yes (RadioGroup, Radio) |
| Checkbox | Yes (Checkbox) |
| Popover | Yes (Popover, PopoverDisclosure) |
| Tree View | No -- not covered |
| Grid | No -- not directly (Composite with rows) |
| Slider | No -- not covered |
| Spinbutton | No -- not covered |
| Alert | No -- not covered |
| Breadcrumb | No -- not covered |
| Switch | No -- not covered (use Checkbox with role) |


## 4. React-Specific A11y Guidance

### 4.1 Fragment and Key Management

**How React fragments affect screen reader parsing:**
- React fragments (`<>...</>` or `<React.Fragment>`) produce no DOM nodes. Screen readers parse the DOM, not React's virtual DOM, so fragments themselves have no accessibility impact.
- However, fragments can cause unexpected DOM structures when a wrapper element with ARIA attributes is expected. For example, wrapping form controls in a fragment loses the `role="group"` container.

**Key prop changes and focus implications:**
- When React's `key` prop changes on a focused element, React unmounts and remounts it, causing focus loss. This is a common source of focus bugs in dynamic lists.
- Gutenberg pattern: Components that conditionally render (e.g., Snackbar list items) must manage focus restoration explicitly.
- Rule: Never use array index as `key` for interactive elements that may be reordered.

**Conditional rendering and a11y tree stability:**
- Mounting/unmounting elements changes the accessibility tree. Screen readers may lose track of position.
- Pattern: Use `display: none` or `hidden` attribute instead of conditional rendering when the element should be temporarily invisible but remain in the a11y tree.
- Gutenberg pattern: `TabPanel` only renders children when selected (`selectedId === instancedTabId && children`), which means screen readers cannot prefetch panel content. This is acceptable per APG tabs pattern.

### 4.2 Portal Accessibility

**Focus management in React portals:**
- `createPortal` renders DOM nodes outside the parent hierarchy, but React events still bubble through the React tree.
- Focus trapping must be explicitly managed since the portal's DOM position differs from its React position.
- Gutenberg pattern: `Modal` uses `createPortal(modal, document.body)` combined with `useConstrainedTabbing()` to trap Tab/Shift+Tab within the portal.

**Screen reader announcement timing with portals:**
- When a portal mounts, screen readers may not announce its content unless focus is moved to it or a live region is used.
- Gutenberg pattern: `Modal` uses `useFocusOnMount()` to ensure focus moves into the portal immediately, triggering screen reader announcement.

**Gutenberg's portal usage:**
- `Modal`: Portals to `document.body`, manages focus trapping and `aria-hidden` on siblings
- `Popover`: Uses `@floating-ui/react-dom` for positioning, may use portal rendering
- `Tooltip`: Ariakit handles portal rendering internally
- `StyleProvider`: Used within Modal to ensure styles apply in portal context

### 4.3 Ref-Based Focus Management

**useRef + useEffect for focus:**
```jsx
// Pattern used in Gutenberg:
const ref = useRef();
useEffect(() => {
  ref.current?.focus();
}, [dependency]);
```

**Timing issues:**
- `useEffect` runs after paint. In some cases, focus must happen before paint to avoid visual flash.
- `useLayoutEffect` runs synchronously after DOM mutations, before paint. Use when focus timing is critical.
- Gutenberg pattern: `Snackbar` uses `useLayoutEffect` for callback refs to avoid stale closures.

**Cleanup on unmount:**
- Focus return must be handled before the component unmounts, not after.
- Gutenberg pattern: `useFocusReturn()` hook saves the previously focused element and restores focus in cleanup.

**Patterns from Gutenberg:**
- `useFocusOnMount(firstElement)`: Focuses first focusable element or the element itself
- `useFocusReturn()`: Returns focus to previously active element on unmount
- `useConstrainedTabbing()`: Prevents Tab from leaving a container
- `useMergeRefs()`: Combines multiple ref callbacks safely

### 4.4 State Management and Live Region Timing

**setState + speak() ordering:**
- `speak()` from `@wordpress/a11y` creates/updates an `aria-live` region in the DOM.
- If `speak()` is called before state updates render, the announcement may describe stale UI.
- Pattern: Call `speak()` after state update, typically in a `useEffect`.

**Gutenberg patterns:**

```jsx
// ComboboxControl: Announces result count after matchingSuggestions changes
useEffect(() => {
  if (isExpanded) {
    const message = hasMatchingSuggestions
      ? sprintf(_n('%d result found...', '%d results found...', count), count)
      : __('No results.');
    speak(message, 'polite');
  }
}, [matchingSuggestions, isExpanded]);

// Notice: Announces on mount
useEffect(() => {
  if (spokenMessage) {
    speak(spokenMessage, politeness);
  }
}, [spokenMessage, politeness]);
```

**Debouncing announcements:**
- Rapid state changes (e.g., typing in a search field) can flood live regions.
- Gutenberg pattern: ComboboxControl triggers announcements based on `matchingSuggestions` array identity, which debounces naturally as React batches updates. However, there is no explicit debounce/throttle on `speak()`.

**Race conditions:**
- Multiple components calling `speak()` simultaneously can cause overlapping announcements.
- The `@wordpress/a11y` `speak()` function uses two live regions (polite and assertive) and clears content before setting new content. Rapid sequential calls can cause the first message to be cleared before the screen reader processes it.
- Rule: Use `assertive` sparingly (only for errors/critical feedback). Use `polite` for informational updates.


## 5. Gap Analysis

### 5.1 WCAG Criteria NOT Fully Covered by Gutenberg Patterns

Based on cross-referencing the WCAG 2.2 criteria from `01-standards-foundations.md` with actual Gutenberg implementations:

| WCAG SC | Title | Level | Gap |
|---------|-------|-------|-----|
| 1.3.1 | Info and Relationships | A | `TreeSelect` does not convey hierarchical relationships semantically |
| 1.3.2 | Meaningful Sequence | A | Some dynamic content reordering (block moving) may not convey sequence changes to screen readers |
| 2.1.1 | Keyboard | A | `Snackbar` dismiss uses `onKeyPress` (deprecated) instead of `onKeyDown` |
| 2.2.1 | Timing Adjustable | A | `Snackbar` auto-dismisses at fixed 6000ms with no user control over timing |
| 2.4.3 | Focus Order | A | `Dropdown` does not manage focus on open/close (depends on consumer) |
| 2.4.7 | Focus Visible | AA | No systematic enforcement of focus visibility in custom components |
| 3.2.2 | On Input | A | Some components change context on selection without explicit activation (by design per APG tabs recommendation, but can be unexpected) |
| 4.1.2 | Name, Role, Value | A | `FormToggle` lacks `role="switch"` (announced as checkbox); `Notice` lacks `role="alert"` |

### 5.2 APG Patterns NOT Implemented in Gutenberg

| APG Pattern | Status in Gutenberg |
|---|---|
| Tree View | `TreeSelect` uses flat `<select>`, not tree pattern |
| Grid (calendar) | `DateTimePicker` calendar lacks grid pattern |
| Alert | `Notice` uses `speak()` but no `role="alert"` |
| Breadcrumb | No dedicated component |
| Feed | No implementation |
| Accordion | No dedicated component (disclosure pattern used informally) |
| Carousel | No implementation |
| Link | N/A (uses native `<a>`) |
| Meter | No implementation |
| Window Splitter | No implementation |

### 5.3 Areas Where Gutenberg Exceeds Standards

1. **Live region announcements:** Gutenberg's `speak()` system proactively announces state changes even when not strictly required by APG (e.g., ComboboxControl announces result count and selection).
2. **IME compatibility:** The `withIgnoreIMEEvents` utility prevents keyboard shortcuts from interfering with CJK input method composition -- a consideration not addressed by APG.
3. **Focus return patterns:** The `useFocusReturn()` hook is more robust than many library implementations, handling edge cases like the invoking element being removed.
4. **Safari compatibility:** Multiple components include explicit `event.currentTarget.focus()` calls to work around Safari's non-standard focus behavior on click.
5. **Modal nesting:** Gutenberg's Modal manages dismissal of sibling and nested modals via a Set-based tracking system, ensuring only one modal is active.
6. **RTL-aware navigation:** Both custom implementations and Ariakit-based components respect RTL text direction for arrow key navigation.
7. **Scrollable content accessibility:** Modal adds `aria-label="Scrollable section"` and `tabIndex={0}` when content is scrollable, making the scrollable region focusable and labeled.

### 5.4 Ariakit Coverage Gaps

APG patterns requiring custom implementation (Ariakit does NOT cover):

| Pattern | Custom Implementation Needed |
|---|---|
| Tree View | Full custom implementation with roles, keyboard, focus management |
| Slider | Use native `<input type="range">` or custom with `role="slider"` |
| Spinbutton | Use native `<input type="number">` or custom with `role="spinbutton"` |
| Alert | Use `role="alert"` container (simple, no Ariakit needed) |
| Breadcrumb | Use `<nav>` with `aria-label` and `aria-current` (simple) |
| Switch | Use `<input type="checkbox" role="switch">` (simple role addition) |
| Grid (data/calendar) | Use Ariakit's `Composite` with `CompositeRow` for basic grid, or custom for full data grid |

### 5.5 Recommendations for Agent Skills

Based on this gap analysis, the Session 5 accessibility skills should emphasize:

**High Priority (known gaps):**
1. **Switch role enforcement:** When generating toggle/switch components, ALWAYS use `role="switch"` on the underlying input. This is Gutenberg's most straightforward fix.
2. **Alert role enforcement:** When generating notice/alert components, include `role="alert"` or `role="status"` on the visible container, not just `speak()`.
3. **aria-modal usage:** For new modal implementations, prefer `aria-modal="true"` over manual `aria-hidden` sibling management. Safari support has improved since the original workaround.
4. **Timer-based dismissal warnings:** When reviewing code with auto-dismissing content, flag WCAG 2.2.1 timing concerns. Require user-configurable or infinite timeout options.
5. **Tree view semantics:** When generating hierarchical select components, use proper `tree`/`treeitem`/`group` roles instead of flat selects with visual indentation.
6. **Missing aria-controls:** When generating combobox or menu button patterns, always include `aria-controls` linking trigger to popup.

**Medium Priority (best practices):**
7. **Ariakit delegation:** For new components matching APG patterns that Ariakit covers, prefer Ariakit over custom implementations. Specifically: Dialog, Combobox, Menu, Disclosure, Select.
8. **aria-valuetext for sliders:** When range controls display non-numeric labels, include `aria-valuetext`.
9. **Keyboard event modernization:** Replace `onKeyPress` (deprecated) with `onKeyDown` in all new code.
10. **Semantic HTML first:** Use native `<button>`, `<input>`, `<select>`, `<fieldset>` elements before reaching for ARIA roles.

**Low Priority (enhancements):**
11. **Grid pattern for calendars:** When building date picker components, implement the APG grid pattern.
12. **aria-invalid for validation:** Include `aria-invalid="true"` on inputs with validation errors.
13. **Home/End key support:** Add to components that currently lack it (sliders, spinbuttons).
14. **Consistent focus management strategy:** Document when to use roving tabindex vs. `aria-activedescendant` and apply consistently.
