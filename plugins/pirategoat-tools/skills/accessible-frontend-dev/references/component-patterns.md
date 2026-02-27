# Component Accessibility Patterns Reference

> Heavy reference for the accessible-frontend-dev skill. Consult when building specific widget types.

## Dialog / Modal

**ARIA pattern (APG Dialog Modal):**
- `role="dialog"` on the container (NOT the backdrop)
- `aria-modal="true"` on the dialog element
- `aria-labelledby` referencing visible title, OR `aria-label`
- Optional: `aria-describedby` for descriptive content

**Keyboard:**
- Tab/Shift+Tab: cycle within dialog (trapped)
- Escape: close the dialog

**Focus lifecycle:**
1. On open: focus moves into dialog (first tabbable, or `tabIndex={-1}` container)
2. While open: Tab is constrained within dialog
3. On close: focus returns to the element that opened it
4. Background: siblings get `aria-hidden="true"` (or use `inert`)

**Implementation skeleton:**
```tsx
function Modal({ isOpen, onClose, title, children }) {
  const dialogRef = useRef(null);
  const triggerRef = useRef(null);
  const titleId = useId();

  useEffect(() => {
    if (!isOpen) return;
    // Store trigger for focus return
    triggerRef.current = document.activeElement;
    // Focus first element
    const first = dialogRef.current?.querySelector('button, input, [tabindex]');
    (first || dialogRef.current)?.focus();
    // Hide background
    const siblings = Array.from(document.body.children)
      .filter(el => el !== dialogRef.current?.closest('[data-portal]'));
    siblings.forEach(el => el.setAttribute('aria-hidden', 'true'));
    return () => {
      siblings.forEach(el => el.removeAttribute('aria-hidden'));
      triggerRef.current?.focus(); // Return focus
    };
  }, [isOpen]);

  if (!isOpen) return null;
  return createPortal(
    <div data-portal>
      <div className="backdrop" onClick={onClose} />
      <div ref={dialogRef} role="dialog" aria-modal="true"
        aria-labelledby={titleId} tabIndex={-1}
        onKeyDown={e => {
          if (e.nativeEvent.isComposing) return;
          if (e.key === 'Escape') onClose();
          // Focus trap logic for Tab/Shift+Tab
        }}>
        <h2 id={titleId}>{title}</h2>
        {children}
      </div>
    </div>,
    document.body
  );
}
```

---

## Combobox / Autocomplete

**ARIA pattern (APG Combobox):**
- Input: `role="combobox"`, `aria-expanded`, `aria-autocomplete="list"`, `aria-controls={listboxId}`, `aria-activedescendant={activeOptionId}`
- Popup: `role="listbox"`, options: `role="option"` with `aria-selected`
- Multi-select: `aria-multiselectable="true"` on listbox

**Keyboard:**
- ArrowDown/Up: navigate options
- Enter: select highlighted option
- Escape: close listbox (stopPropagation if inside modal)
- Tab: close listbox, move to next field
- Typing: filters options

**Focus:**
- Physical focus STAYS on the input at all times
- Virtual focus via `aria-activedescendant` points to active option ID
- Only set `aria-activedescendant` when: input focused AND listbox expanded AND option highlighted

**Announcements:**
- Results count: `speak(\`${count} results available\`, 'polite')` — debounced 500ms
- Selection: `speak(\`${label} selected\`, 'assertive')`

---

## Tabs / Tab Panel

**ARIA pattern (APG Tabs):**
- Container: `role="tablist"` with `aria-label`
- Tabs: `role="tab"`, `aria-selected`, `aria-controls={panelId}`
- Panels: `role="tabpanel"`, `aria-labelledby={tabId}`, `tabIndex={0}`

**Keyboard:**
- ArrowLeft/Right (horizontal) or ArrowUp/Down (vertical): move between tabs
- Home/End: first/last tab
- Enter/Space: select tab (when `selectOnMove=false`)
- RTL: arrow direction reverses

**Focus:**
- Roving tabindex: active tab has `tabIndex={0}`, others `tabIndex={-1}`
- Tab stops: tablist is ONE Tab stop; Tab from tab goes to panel content

---

## Menu / Menu Button / Dropdown

**ARIA pattern (APG Menu Button):**
- Trigger: `<button>` with `aria-haspopup="true"`, `aria-expanded`, `aria-controls`
- Menu: `role="menu"`, items: `role="menuitem"` (or `menuitemcheckbox`, `menuitemradio`)

**Keyboard:**
- Trigger: Enter/Space/ArrowDown opens menu, focuses first item
- ArrowDown/Up: navigate items
- Escape: close menu, return focus to trigger
- Home/End: first/last item
- Type-ahead: jump to matching item

**Focus:**
- On open: focus first menuitem
- On close: return focus to trigger button
- No wrapping elements without roles inside `role="menu"`

---

## Select / Listbox

**ARIA pattern (APG Listbox):**
- Container: `role="listbox"`, `aria-label`
- Options: `role="option"`, `aria-selected`
- Multi-select: `aria-multiselectable="true"` on listbox

**Keyboard:**
- ArrowDown/Up: move focus between options
- Home/End: first/last
- Space: toggle selection (multi-select)
- Type-ahead: jump to matching option

---

## Tooltip

**ARIA pattern (APG Tooltip):**
- Tooltip: `role="tooltip"`, `id={tooltipId}`
- Trigger: `aria-describedby={tooltipId}` (tooltip adds description, NOT label)

**Behavior:**
- Shows on: focus AND hover (both required)
- Hides on: blur, mouse leave, Escape
- No interactive content inside tooltip
- Persistent while hovered/focused

**Critical distinction:** Tooltips provide DESCRIPTION (`aria-describedby`). They do NOT provide the accessible NAME. The trigger must already have a name via visible text or `aria-label`.

---

## Slider / Range

**ARIA pattern (APG Slider):**
- Element: `role="slider"` (or native `<input type="range">`)
- Required: `aria-valuemin`, `aria-valuemax`, `aria-valuenow`
- Optional: `aria-valuetext` for human-readable value (e.g., "Medium", "50%")
- Label: `aria-label` or `aria-labelledby`

**Keyboard:**
- ArrowRight/Up: increase by step
- ArrowLeft/Down: decrease by step
- PageUp/PageDown: increase/decrease by large step
- Home/End: min/max

---

## Toggle / Switch

**ARIA pattern (APG Switch):**
- Element: `role="switch"` with `aria-checked` (NOT `role="checkbox"` for on/off toggles)
- Label: `aria-label` or `aria-labelledby`

**Keyboard:**
- Space: toggle state
- Enter: may also toggle (convention varies)

**Critical:** Use `aria-disabled="true"` (not HTML `disabled`) if the toggle should remain discoverable. Add `aria-describedby` linking to descriptive text.

---

## Alert / Notice / Snackbar

**ARIA pattern (APG Alert):**
- Errors/critical: `role="alert"` (implicitly `aria-live="assertive"`)
- Status/info: `role="status"` (implicitly `aria-live="polite"`)

**Implementation rules:**
1. The live region container must be in the DOM BEFORE content is injected
2. Never conditionally render the container — render it empty, then fill it
3. Use `speak(message, 'polite')` in WordPress for reliable announcements
4. Auto-dismissing content (snackbar): allow configurable timeout, warn about WCAG 2.2.1
5. Map: `error` → assertive, `success`/`warning`/`info` → polite

---

## Form Controls

**Checkbox:** Native `<input type="checkbox">` with associated `<label>`. Use `aria-describedby` for help text.

**Radio Group:** `<fieldset>` + `<legend>` wrapping radio inputs. Each `<input type="radio">` with `<label>`.

**Text Input:** `<input>` with `<label htmlFor={id}>`. Help text via `aria-describedby`. Error via `aria-invalid="true"` + `aria-describedby` pointing to error message.

**Error states:** Set `aria-invalid="true"` on the input. Associate error text via `aria-describedby`. Announce with `speak(errorMessage, 'assertive')`.

---

## Popover / Disclosure

**Disclosure (toggle show/hide):**
- Trigger: `<button>` with `aria-expanded`, `aria-controls`
- Content: any element with `id` matching `aria-controls`

**Popover:**
- Same as disclosure, plus focus management
- Focus may move into popover (non-modal) or trap inside (modal-like)
- Escape closes and returns focus to trigger

---

## Toolbar

**ARIA pattern (APG Toolbar):**
- Container: `role="toolbar"` with `aria-label`
- Items: buttons, toggle buttons, etc.

**Keyboard:**
- ArrowLeft/Right: move between toolbar items
- Tab: move OUT of toolbar (toolbar is single Tab stop)
- Home/End: first/last item

**Focus:** Roving tabindex — one item has `tabIndex={0}`, rest have `tabIndex={-1}`.

---

## Treeview

**ARIA pattern (APG Tree View):**
- Container: `role="tree"` with `aria-label`
- Nodes: `role="treeitem"` on each item
- Children wrapper: `role="group"` wrapping child nodes of an expanded parent
- Parent nodes: `aria-expanded="true"` (open) or `aria-expanded="false"` (closed)
- Selection: `aria-selected` if tree supports selection

**Keyboard:**
- ArrowDown/Up: move focus to next/previous visible treeitem
- ArrowRight: expand closed parent → or move to first child if already expanded
- ArrowLeft: collapse expanded parent → or move to parent if on a child
- Home/End: first/last visible treeitem
- Enter: activate the focused treeitem
- `*` (asterisk): expand all siblings at the current level

**Focus:** Roving tabindex — one treeitem has `tabIndex={0}`, rest have `tabIndex={-1}`.

**Structure example:**
```tsx
<ul role="tree" aria-label="File browser">
  <li role="treeitem" aria-expanded="true" tabIndex={0}>
    src
    <ul role="group">
      <li role="treeitem" tabIndex={-1}>index.js</li>
      <li role="treeitem" tabIndex={-1}>App.js</li>
    </ul>
  </li>
  <li role="treeitem" tabIndex={-1}>README.md</li>
</ul>
```

---

## Drag and Drop

**Accessibility principle:** Always provide a keyboard alternative. Drag-and-drop is a pointer-only interaction by default — keyboard and screen reader users are completely blocked without an alternative.

**Keyboard alternative patterns:**
- Action mode toggle: Enter/Space activates "grab" → arrow keys reorder → Enter/Space "drops"
- Move buttons: explicit "Move up"/"Move down" buttons alongside each item
- Context menu: right-click or Shift+F10 opens menu with move options

**Announcements:**
- On grab: `speak("Item grabbed, position 2 of 5", 'assertive')`
- On move: `speak("Item moved, now position 3 of 5", 'assertive')`
- On drop: `speak("Item dropped, final position 3 of 5", 'assertive')`
- On cancel: `speak("Reorder cancelled", 'polite')`

**ARIA guidance:**
- Use `aria-describedby` linking to instructions: "Press Space to grab, arrow keys to move, Space to drop"
- Use `aria-roledescription="sortable"` sparingly and only when standard ARIA roles are insufficient
- Set `aria-grabbed` is deprecated — use the action mode pattern instead

**Implementation skeleton:**
```tsx
function SortableItem({ label, position, total, onMove }) {
  const [grabbed, setGrabbed] = useState(false);
  const instructionsId = useId();

  return (
    <>
      <span id={instructionsId} hidden>
        Press Space to grab, arrow keys to reorder, Space to drop
      </span>
      <div
        role="option"
        aria-describedby={instructionsId}
        tabIndex={0}
        onKeyDown={(e) => {
          if (e.key === ' ') {
            e.preventDefault();
            setGrabbed(!grabbed);
            speak(grabbed
              ? `${label} dropped, final position ${position} of ${total}`
              : `${label} grabbed, position ${position} of ${total}`,
              'assertive');
          }
          if (grabbed && e.key === 'ArrowDown') onMove(1);
          if (grabbed && e.key === 'ArrowUp') onMove(-1);
          if (grabbed && e.key === 'Escape') {
            setGrabbed(false);
            speak('Reorder cancelled', 'polite');
          }
        }}
      >
        {label}
      </div>
    </>
  );
}
```

---

## Data Grid / Calendar Grid

**ARIA pattern (APG Grid):**
- Container: `role="grid"` or `role="application"` (for custom keyboard)
- Rows: `role="row"`
- Cells: `role="gridcell"`

**Keyboard:**
- Arrow keys: navigate cells in all directions
- PageUp/Down: previous/next month (calendar)
- Home/End: start/end of row or week
- Enter/Space: select cell

**Focus:** Roving tabindex — one cell has `tabIndex={0}`, rest have `tabIndex={-1}`.
Use `role="application"` sparingly (only when widget handles its own keyboard model).

---

## External Link / Opens in New Tab

**Security:** Always pair `target="_blank"` with `rel="noreferrer noopener"`. Merge with any caller-provided `rel` values — don't overwrite.

**Accessible name:** Screen readers must announce that the link opens in a new tab. Two patterns:
- Visually-hidden `<span>`: `<span className="sr-only">(opens in a new tab)</span>` inside the `<a>`
- `aria-label` on an icon span: `<span aria-label="(opens in a new tab)" />` — contributes to the link's computed accessible name

**Visual indicator (arrow icon):**
- **Best:** `::after` pseudo-element with `mask-image` SVG and `background: currentColor` — invisible to Twemoji, excluded from text selection, inherits text color, silent to screen readers (empty `content`)
- **Acceptable:** Inline SVG with `aria-hidden="true"` — explicit, testable, full color control
- **Avoid:** Unicode arrow (`↗`) as text node — Twemoji replaces it with `<img>`, it leaks into clipboard on text selection, and screen readers announce "North East Arrow"
- **Avoid:** Unicode in CSS `content: "\2197"` — screen readers announce the Unicode character name

**RTL:** Use CSS `:dir(rtl)::after` to flip the arrow direction (e.g., `↗` → `↖`), not JS `isRTL()`. The `:dir()` pseudo-class responds to inherited document directionality automatically.

```css
.external-link {
  text-decoration: none;
}
.external-link .link-text {
  text-decoration: underline;
}
.external-link::after {
  content: "";
  display: inline-block;
  width: 0.7em;
  height: 0.7em;
  margin-inline-start: 0.15em;
  background: currentColor;
  mask-image: url("data:image/svg+xml,..."); /* arrow SVG */
  mask-size: contain;
  mask-repeat: no-repeat;
}
.external-link:dir(rtl)::after {
  mask-image: url("data:image/svg+xml,..."); /* mirrored arrow SVG */
}
```

**Edge case:** `href="#..."` + `target="_blank"` opens a blank tab (hash fragment has no meaning in a new context). Prevent default navigation and warn or handle gracefully.
