# Accessibility Anti-Patterns from Gutenberg Bug History

> Research document for AI agent consumption. Part of the a11y research series.
> Generated: 2026-02-27 | Session 2, Task 2.2

## Methodology

- **Total commits scanned:** ~450 across 9 targeted git queries
- **Commits selected for deep analysis:** 35
- **Git queries used:**
  - `git log --all --grep="a11y" --grep="fix" --all-match` (55 results)
  - `git log --all --grep="accessibility" --grep="fix" --all-match` (100 results)
  - `git log --all --grep="screen reader"` (100 results)
  - `git log --all --grep="focus" --grep="fix" --all-match` (100 results)
  - `git log --all --grep="keyboard" --grep="fix" --all-match` (80 results)
  - `git log --all --grep="aria" --grep="fix" --all-match` (80 results)
  - `git log --all --grep="tabindex" --grep="fix" --all-match` (21 results)
  - `git log --all --grep="role" --grep="a11y|accessibility|aria" --all-match` (50 results)
  - `git log --all --grep="announce" --grep="fix" --all-match` (18 results)
- **Selection criteria:** Clear before/after pattern in diff, representative of a recurring anti-pattern category, covers diverse a11y areas (focus, keyboard, ARIA, screen reader), includes both simple and complex fixes

## Anti-Pattern Catalog

### AP-01: Focus Lost on State Change / Re-render

- **Frequency:** 12 commits fix this pattern
- **Severity:** P0
- **Description:** When React state updates cause a component to unmount and remount (or conditionally render), the browser's focus is lost because the previously focused DOM element no longer exists. Focus falls to `<body>`, leaving keyboard and screen reader users stranded with no indication of where they are.
- **Root cause:** Conditional rendering (`{condition && <Component />}`) or state-driven key changes cause React to destroy and recreate DOM nodes. If the focused element is inside the destroyed subtree, focus is silently lost. Another variant: state updates trigger re-renders that cause `useEffect` hooks to re-run focus logic, stealing focus from its current location.
- **Impact on users:** Keyboard users lose their place and must Tab through the entire page to find where they were. Screen reader users hear nothing and are teleported to the top of the page without context.

#### Example 1: Navigation Link Focus Loss (state-driven remount)
- **Commit:** `710e544028730fd310078a90b6978988a2825f5f`
- **Component:** `packages/block-library/src/navigation-link/edit.js`
- **Before (buggy code):**
```tsx
const [ isEditingControl, setIsEditingControl ] = useState( false );

// In JSX: RichText was conditionally rendered based on isEditingControl
{ ! isInvalid && ! isDraft && ! isEditingControl && (
    <RichText
        ref={ ref }
        identifier="label"
        className="wp-block-navigation-item__label"
        value={ label }
        onChange={ ( labelValue ) => setAttributes( { label: labelValue } ) }
    />
) }

// In Controls component: focus/blur toggled isEditingControl
<TextControl
    onFocus={ () => setIsEditingControl( true ) }
    onBlur={ () => setIsEditingControl( false ) }
/>
```
- **After (fix):**
```tsx
// Removed isEditingControl state entirely
// RichText always renders regardless of sidebar focus state
{ ! isInvalid && ! isDraft && (
    <RichText
        ref={ ref }
        identifier="label"
        className="wp-block-navigation-item__label"
        value={ label }
        onChange={ ( labelValue ) => setAttributes( { label: labelValue } ) }
    />
) }
```
- **Analysis:** The `isEditingControl` state was toggled on focus/blur of sidebar inputs, which unmounted the RichText in the canvas. When `setIsEditingControl(false)` fired on blur, the RichText remounted and stole focus. The fix removed the conditional rendering entirely -- the RichText always renders, preventing the remount cycle.

#### Example 2: FormTokenField Focus Lost on Tab
- **Commit:** `f7ba498ec3a9a18f015f67a3cc9baeab16c854b1`
- **Component:** `packages/components/src/form-token-field/index.tsx`
- **Before (buggy code):**
```tsx
// Tab key was not handled -- the suggestions list remained expanded,
// and the focus was lost because the expanded state caused re-renders
// that interfered with natural tab navigation
function handleEscapeKey( event: KeyboardEvent ) {
    if ( event.target instanceof HTMLInputElement ) {
        setIncompleteTokenValue( event.target.value );
        setIsExpanded( false );
        setSelectedSuggestionIndex( -1 );
        setSelectedSuggestionScroll( false );
    }
    return true; // PreventDefault.
}
```
- **After (fix):**
```tsx
case 'Tab':
    preventDefault = handleTabKey( event );
    break;

function collapseSuggestionsList( event: KeyboardEvent ) {
    if ( event.target instanceof HTMLInputElement ) {
        setIncompleteTokenValue( event.target.value );
        setIsExpanded( false );
        setSelectedSuggestionIndex( -1 );
        setSelectedSuggestionScroll( false );
    }
}

function handleTabKey( event: KeyboardEvent ) {
    collapseSuggestionsList( event );
    return false; // Do not prevent the default behavior.
}
```
- **Analysis:** The Tab key was not handled, so the suggestions list stayed expanded during tab navigation. The expanded state triggered re-renders that disrupted focus. The fix collapses the suggestions list on Tab (like Escape) but critically does NOT call `preventDefault()`, allowing normal tab navigation to continue.

#### Example 3: ImageURLInputUI Focus Loss on Settings Change
- **Commit:** `7bf1ab3e5831fe7991413ad0ae46754a5090d65a`
- **Component:** `packages/block-editor/src/components/url-popover/image-url-input-ui.js`
- **Before (buggy code):**
```tsx
// No ref to the popover wrapper, no focus management on state changes
const ImageURLInputUI = ( { ... } ) => {
    const [ urlInput, setUrlInput ] = useState( null );
    const autocompleteRef = useRef( null );
    // When isEditingLink or lightboxEnabled changed, focus was lost
    // because the popover content re-rendered without focus restoration
```
- **After (fix):**
```tsx
const wrapperRef = useRef();

useEffect( () => {
    if ( ! wrapperRef.current ) {
        return;
    }
    const nextFocusTarget =
        focus.focusable.find( wrapperRef.current )[ 0 ] ||
        wrapperRef.current;
    nextFocusTarget.focus();
}, [ isEditingLink, url, lightboxEnabled ] );

// URLPopover converted to forwardRef to accept the ref
<URLPopover ref={ wrapperRef } ... />
```
- **Analysis:** When popover state changed (editing link, toggling lightbox), the popover content re-rendered and focus was lost. The fix adds a `useEffect` that re-focuses the first focusable element inside the popover whenever relevant state changes, and uses `forwardRef` on URLPopover to enable ref passing.

- **Prevention rule:** Never conditionally unmount the currently focused element based on state that can change during user interaction. If content must toggle, use CSS visibility/display or `hidden` attribute instead of conditional rendering. When state changes cause re-renders that affect focused elements, add explicit focus restoration via `useEffect`.
- **Detection heuristic:** Look for `{condition && <InteractiveElement />}` patterns where `condition` is derived from state that changes during focus/blur/click events. Also flag `useEffect` hooks with focus-related dependencies that lack focus restoration logic.

---

### AP-02: Missing Focus Return After Popover/Modal/Dialog Close

- **Frequency:** 8 commits fix this pattern
- **Severity:** P0
- **Description:** When a popover, modal, or dialog closes, focus is not returned to the element that triggered it. Focus falls to `<body>` or an unpredictable location.
- **Root cause:** The trigger element reference is not stored before opening the overlay, or the close handler does not explicitly call `.focus()` on the trigger. In React, the trigger element may itself have re-rendered, making stale refs useless.
- **Impact on users:** Keyboard users lose their place entirely. Screen reader users hear the page title or nothing, losing all context of where they were working.

#### Example 1: Site Editor Focus Loss After Save Panel Close
- **Commit:** `6c5b22779efa5dfd6f50041e55bb3b479cdc04ca`
- **Component:** `packages/edit-site/src/components/save-panel/index.js`
- **Before (buggy code):**
```tsx
// Save panel conditionally replaced the "Open save panel" button
{ isSaveViewOpen ? (
    <_EntitiesSavedStates onClose={ onClose } />
) : (
    <div className="edit-site-editor__toggle-save-panel">
        <Button onClick={ () => setIsSaveViewOpened( true ) }>
            { __( 'Open save panel' ) }
        </Button>
    </div>
) }
```
- **After (fix):**
```tsx
// Button always renders (hidden visually when panel is open)
<div className={ classnames( 'edit-site-editor__toggle-save-panel', {
    'screen-reader-text': isSaveViewOpen,
} ) }>
    <Button
        onClick={ () => setIsSaveViewOpened( true ) }
        aria-haspopup={ 'dialog' }
        disabled={ disabled }
        __experimentalIsFocusable
    >
        { __( 'Open save panel' ) }
    </Button>
</div>
{ isSaveViewOpen && (
    <_EntitiesSavedStates onClose={ onClose } renderDialog />
) }
```
- **Analysis:** The trigger button was unmounted when the save panel opened. When the panel closed, there was no button to return focus to. The fix keeps the button in the DOM (visually hidden with `screen-reader-text` class) so it can receive focus when the panel closes. The panel itself is upgraded to a proper `role="dialog"` with `aria-labelledby`/`aria-describedby`.

#### Example 2: URLPopover Missing Focus Return and Role
- **Commit:** `2f00bfdf5e25665ff80637d8345f123addb62101`
- **Component:** `packages/block-editor/src/components/url-popover/index.js`, `packages/block-editor/src/components/media-placeholder/index.js`
- **Before (buggy code):**
```tsx
// URLPopover had no role, no aria-modal, no aria-label
<Popover
    className="block-editor-url-popover"
    focusOnMount={ focusOnMount }
    ...
>

// Close handler did not return focus
const closeURLInput = () => {
    setIsURLInputVisible( false );
};
```
- **After (fix):**
```tsx
// URLPopover now has proper dialog semantics
<Popover
    ref={ ref }
    role="dialog"
    aria-modal="true"
    aria-label={ __( 'Edit URL' ) }
    className="block-editor-url-popover"
    ...
>

// Close handler returns focus to the trigger
const closeURLInput = () => {
    setIsURLInputVisible( false );
    popoverAnchor?.focus();
};
```
- **Analysis:** The URLPopover lacked dialog semantics and did not return focus on close. The fix adds `role="dialog"`, `aria-modal="true"`, and `aria-label`, plus explicitly focuses the anchor element when closing.

- **Prevention rule:** Every popover, modal, or dialog MUST: (1) store a reference to the trigger element before opening, (2) call `triggerElement.focus()` in the close handler, (3) have `role="dialog"` and `aria-label`/`aria-labelledby`. Never unmount the trigger element while the overlay is open.
- **Detection heuristic:** Look for any component that renders a `<Popover>`, `<Modal>`, or overlay without a corresponding `onClose` handler that calls `.focus()` on the trigger. Also check for `aria-haspopup` on the trigger without matching `role="dialog"` on the overlay.

---

### AP-03: Invalid ARIA Attribute Usage (Wrong Role/State Pairing)

- **Frequency:** 7 commits fix this pattern
- **Severity:** P1
- **Description:** ARIA attributes are used on elements with incompatible roles, or attributes are set that violate the WAI-ARIA specification. This causes screen readers to announce incorrect information or ignore the attributes entirely.
- **Root cause:** Developers apply ARIA attributes without checking the specification for which roles support which attributes. Common mistakes: `aria-checked` on `role="menuitem"` (only valid on `menuitemcheckbox`/`menuitemradio`), `aria-expanded` missing on toggle buttons, wrapper `<div>` elements inside `role="menu"` breaking the required parent-child relationship.
- **Impact on users:** Screen readers announce incorrect state (e.g., a regular menu item reported as "checked"), or worse, fail to announce state at all because the browser silently drops invalid attributes.

#### Example 1: aria-checked on Wrong ARIA Role
- **Commit:** `53209f4a029fe3fe2cb643a7956abb588e3491bb`
- **Component:** `packages/components/src/menu-item/index.js`
- **Before (buggy code):**
```tsx
// aria-checked was applied regardless of the role
{
    'aria-label': label,
    'aria-checked': isSelected,
    role,
    className,
    ...props,
}
```
- **After (fix):**
```tsx
// aria-checked only applied when role supports it per spec
{
    'aria-label': label,
    // Make sure aria-checked matches spec https://www.w3.org/TR/wai-aria-1.1/#aria-checked
    'aria-checked': ( role === 'menuitemcheckbox' || role === 'menuitemradio' ) ? isSelected : undefined,
    role,
    className,
    ...props,
}
```
- **Analysis:** `aria-checked` is only valid on `menuitemcheckbox`, `menuitemradio`, `checkbox`, `radio`, and `switch` roles per WAI-ARIA 1.1. Applying it to `role="menuitem"` is invalid and caused screen readers to announce misleading state.

#### Example 2: Wrapper div Breaking Menu ARIA Structure
- **Commit:** `e95970d888c309274e24324d593c77c536c9f1d8`
- **Component:** `packages/block-editor/src/components/block-variation-transforms/index.js`
- **Before (buggy code):**
```tsx
// Wrapping div violated the required parent-child relationship for menu roles
<DropdownMenu ...>
    { () => (
        <div className={ `${ className }__container` }>
            <MenuGroup>
                <MenuItemsChoice ... />
            </MenuGroup>
        </div>
    ) }
</DropdownMenu>
```
- **After (fix):**
```tsx
// Removed the wrapping div to maintain valid ARIA tree structure
<DropdownMenu ...>
    { () => (
        <MenuGroup>
            <MenuItemsChoice ... />
        </MenuGroup>
    ) }
</DropdownMenu>
```
- **Analysis:** WAI-ARIA requires that `role="menu"` contains only `role="menuitem"`, `menuitemcheckbox`, `menuitemradio`, `group`, or `menubar` as direct children. A wrapping `<div>` without a role breaks this required parent-child structure, causing screen readers to fail to announce the menu items correctly.

#### Example 3: document Role Causing Windows Screen Readers to Switch Modes
- **Commit:** `8d31694d662d59f1523868f5abc596e85a6f8cd4`
- **Component:** `packages/block-editor/src/components/block-list/use-block-props/index.js`
- **Before (buggy code):**
```tsx
// Every block had role="document" applied via useBlockProps
{
    ref: mergedRefs,
    id: `block-${ clientId }${ htmlSuffix }`,
    role: 'document',
    'aria-label': blockLabel,
    'data-block': clientId,
}
```
- **After (fix):**
```tsx
// role="document" removed -- it was causing Windows screen readers
// (JAWS, NVDA) to switch from focus/forms mode to browse mode on every block
{
    ref: mergedRefs,
    id: `block-${ clientId }${ htmlSuffix }`,
    'aria-label': blockLabel,
    'data-block': clientId,
}
```
- **Analysis:** `role="document"` on every block element caused Windows screen readers to switch from focus/forms mode to browse mode, breaking keyboard interaction in the editor. The role was semantically incorrect for interactive block wrappers.

- **Prevention rule:** Before applying any ARIA attribute, verify it is valid for the element's role per the WAI-ARIA spec. Never add `aria-checked` unless the role is `menuitemcheckbox`, `menuitemradio`, `checkbox`, `radio`, or `switch`. Never insert non-role-bearing wrapper elements inside ARIA container widgets (`menu`, `listbox`, `tree`, `tablist`). Avoid `role="document"` on interactive elements.
- **Detection heuristic:** Check every `aria-checked`, `aria-selected`, `aria-expanded`, and `aria-pressed` attribute against its element's `role`. Flag any `<div>` or `<span>` without an ARIA role that is a direct child of a `role="menu"`, `role="listbox"`, or `role="tree"` element.

---

### AP-04: Missing aria-haspopup / aria-expanded on Trigger Buttons

- **Frequency:** 6 commits fix this pattern
- **Severity:** P1
- **Description:** Buttons that open popovers, dialogs, or menus lack `aria-haspopup` and/or `aria-expanded` attributes. Screen reader users cannot discover that a button has a popup or know whether it is currently open.
- **Root cause:** Developers add the visual popover/modal behavior but forget the ARIA attributes that communicate the relationship between trigger and popup.
- **Impact on users:** Screen reader users do not know a button will open something, or cannot tell if a popup is currently open or closed.

#### Example 1: Block Locking Toolbar Missing aria-expanded and aria-haspopup
- **Commit:** `066af9023158a820fd14120fda0800565b8bc5cb`
- **Component:** `packages/block-editor/src/components/block-lock/toolbar.js`, `packages/block-editor/src/components/block-lock/menu-item.js`
- **Before (buggy code):**
```tsx
<ToolbarButton
    ref={ lockButtonRef }
    icon={ lock }
    label={ __( 'Unlock' ) }
    onClick={ toggleModal }
/>

<MenuItem
    icon={ isLocked ? unlock : lockOutline }
    onClick={ toggleModal }
>
    { label }
</MenuItem>
```
- **After (fix):**
```tsx
<ToolbarButton
    ref={ lockButtonRef }
    icon={ lock }
    label={ __( 'Unlock' ) }
    onClick={ toggleModal }
    aria-expanded={ isModalOpen }
    aria-haspopup="dialog"
/>

<MenuItem
    icon={ isLocked ? unlock : lockOutline }
    onClick={ toggleModal }
    aria-expanded={ isModalOpen }
    aria-haspopup="dialog"
>
    { label }
</MenuItem>
```
- **Analysis:** Both the toolbar button and menu item opened a modal dialog but lacked `aria-haspopup="dialog"` and `aria-expanded`. Screen reader users had no way to know the button would open a dialog or whether it was currently open.

#### Example 2: Navigation Block aria-expanded Not Tracking Hover State
- **Commit:** `ecd550ba6ce66ea37c203d3438a2eeb70f315d36`
- **Component:** `packages/block-library/src/navigation/interactivity.js`
- **Before (buggy code):**
```tsx
// isMenuOpen was a simple boolean that didn't distinguish between click and hover
context.core.navigation.isMenuOpen = true;

// aria-expanded was derived from this single boolean,
// but hover-opened menus didn't update it correctly
```
- **After (fix):**
```tsx
// isMenuOpen is now an object tracking both click and hover independently
context.core.navigation.isMenuOpen[ menuOpenedOn ] = true;  // 'click' or 'hover'

// isMenuOpen selector checks if ANY source has opened the menu
isMenuOpen: ( { context } ) =>
    Object.values( context.core.navigation.isMenuOpen ).filter( Boolean ).length > 0,
```
- **Analysis:** The navigation submenu's `aria-expanded` attribute was only updated on click, not on hover. When a submenu opened via hover, `aria-expanded` remained `false`, making the open submenu invisible to screen readers. The fix tracks both `click` and `hover` states independently and derives the overall open state from both.

- **Prevention rule:** Every button/element that opens a popover, menu, or dialog MUST have `aria-haspopup` set to the appropriate value (`"dialog"`, `"menu"`, `"listbox"`, `"tree"`, or `"grid"`). It MUST also have `aria-expanded` that dynamically reflects the open/closed state. For navigation menus, ensure `aria-expanded` updates for ALL interaction modes (click, hover, keyboard).
- **Detection heuristic:** Find any component that renders a `<Popover>`, `<Modal>`, dropdown, or similar overlay. Check that the trigger element has `aria-haspopup` and `aria-expanded`. Flag any `aria-expanded` that is hardcoded or only updated in some interaction paths.

---

### AP-05: Disabled Buttons Removed from Tab Order (Inaccessible Disabled State)

- **Frequency:** 4 commits fix this pattern
- **Severity:** P1
- **Description:** Using the HTML `disabled` attribute on buttons removes them from the tab order entirely. Screen reader users cannot discover disabled controls or understand why an action is unavailable. They simply cannot find the button at all.
- **Root cause:** Developers use `<button disabled>` or `<Button disabled>` which applies the native HTML `disabled` attribute, making the element completely non-interactive and unreachable via keyboard.
- **Impact on users:** Screen reader users cannot discover that a feature exists but is currently unavailable. They cannot understand the UI state. Keyboard users skip over the button entirely.

#### Example 1: Inaccessible Disabled Buttons Throughout Components
- **Commit:** `89bfae4134591b4e27b54519dd25dc09cc9b8ded`
- **Component:** Multiple: `packages/components/src/button/`, `packages/components/src/dropdown-menu/`, `packages/components/src/autocomplete/`
- **Before (buggy code):**
```tsx
// Button with native disabled attribute -- invisible to screen readers
<Button disabled={ ! canDoAction }>
    { __( 'Do Action' ) }
</Button>

// Test expected true disabled state
expect( screen.getByRole( 'button', { name: 'Block Name' } ) ).toBeDisabled();
```
- **After (fix):**
```tsx
// Button uses aria-disabled instead -- remains focusable and discoverable
<Button
    __experimentalIsFocusable
    disabled={ ! canDoAction }
>
    { __( 'Do Action' ) }
</Button>

// Test now expects accessible disabled state
const blockSwitcher = screen.getByRole( 'button', { name: 'Block Name' } );
expect( blockSwitcher ).toBeEnabled();
expect( blockSwitcher ).toHaveAttribute( 'aria-disabled', 'true' );
```
- **Analysis:** The Gutenberg `Button` component's `__experimentalIsFocusable` prop was added/applied to multiple instances across the codebase. It renders `aria-disabled="true"` instead of the HTML `disabled` attribute, keeping the button in the tab order while preventing activation. An ESLint rule (`no-restricted-syntax`) was also added to flag direct use of `disabled` on Button components.

- **Prevention rule:** Never use the HTML `disabled` attribute on interactive elements that users need to discover. Use `aria-disabled="true"` combined with preventing the click/keypress handler from executing. In Gutenberg's `Button` component, use `__experimentalIsFocusable` with `disabled` to get accessible disabled state.
- **Detection heuristic:** Flag any `<button disabled>`, `<Button disabled>` (without `__experimentalIsFocusable`), or `<input disabled>` on controls that users need to be aware of. Exception: controls that are purely decorative or inside already-hidden containers.

---

### AP-06: Screen Reader Live Region Not Announcing Dynamic Content

- **Frequency:** 5 commits fix this pattern
- **Severity:** P1
- **Description:** Dynamic content changes (autocomplete results appearing, formatting state changes, content updates) are not announced to screen readers because no ARIA live region or `wp.a11y.speak()` call is used.
- **Root cause:** Developers add visual updates (lists appearing, text changing, state toggling) without considering that screen reader users cannot see these changes. No programmatic announcement mechanism is implemented.
- **Impact on users:** Screen reader users are unaware of dynamic content changes. They don't know autocomplete results are available, that a formatting toggle was applied, or that an operation succeeded.

#### Example 1: Autocomplete Results Not Announced
- **Commit:** `8661ab4c7e3c9bf4104469fdde4deabd186b21ec`
- **Component:** `packages/components/src/autocomplete/autocompleter-ui.tsx`
- **Before (buggy code):**
```tsx
// Autocomplete results appeared visually but were never announced
// The announce function existed in the parent but was called too late
// (after the popover rendered, not when results changed)
useLayoutEffect( () => {
    onChangeOptions( items );
    // No announcement here
}, [] );
```
- **After (fix):**
```tsx
import { speak } from '@wordpress/a11y';

const debouncedSpeak = useDebounce( speak, 500 );

function announce( options: Array< KeyedOption > ) {
    if ( !! options.length ) {
        if ( filterValue ) {
            debouncedSpeak(
                sprintf(
                    _n(
                        '%d result found, use up and down arrow keys to navigate.',
                        '%d results found, use up and down arrow keys to navigate.',
                        options.length
                    ),
                    options.length
                ),
                'assertive'
            );
        } else {
            debouncedSpeak(
                sprintf(
                    _n(
                        'Initial %d result loaded. Type to filter all available results. Use up and down arrow keys to navigate.',
                        'Initial %d results loaded. Type to filter all available results. Use up and down arrow keys to navigate.',
                        options.length
                    ),
                    options.length
                ),
                'assertive'
            );
        }
    } else {
        debouncedSpeak( __( 'No results.' ), 'assertive' );
    }
}

useLayoutEffect( () => {
    onChangeOptions( items );
    announce( items );
}, [] );
```
- **Analysis:** The autocomplete results appeared visually but were never announced to screen readers. The fix uses `wp.a11y.speak()` (debounced to 500ms to avoid spam) to announce result counts with navigation instructions. Different messages are used for initial load vs. filtered results.

#### Example 2: Formatting Changes Not Announced
- **Commit:** `0638a8c52199381080abb809fd5ccd137db5bb45`
- **Component:** Multiple format library files (`bold/index.js`, `italic/index.js`, `code/index.js`, etc.)
- **Before (buggy code):**
```tsx
// Toggling bold via keyboard shortcut produced no announcement
function onToggle() {
    onChange( toggleFormat( value, { type: name } ) );
}
```
- **After (fix):**
```tsx
// Title is passed to toggleFormat, which triggers an announcement
function onToggle() {
    onChange( toggleFormat( value, { type: name, title } ) );
}

// Menu items also got proper role="menuitemcheckbox" for state communication
<RichTextToolbarButton
    title={ title }
    onClick={ onClick }
    isActive={ isActive }
    role="menuitemcheckbox"
/>
```
- **Analysis:** When a user toggled bold, italic, or other formatting via keyboard shortcut, there was no auditory feedback. The fix passes the format `title` to `toggleFormat()`, which triggers a screen reader announcement. Additionally, formatting menu items were given `role="menuitemcheckbox"` so screen readers announce their checked/unchecked state.

- **Prevention rule:** Every dynamic content change that a sighted user can see MUST have a corresponding screen reader announcement. Use `wp.a11y.speak()` for transient status messages. Use ARIA live regions (`aria-live="polite"` or `assertive`) for persistent dynamic content. For toggle controls, use `role="menuitemcheckbox"` or `aria-pressed`.
- **Detection heuristic:** Look for state changes that update visible UI (lists appearing, counts changing, status text updating) without a corresponding `speak()` call or live region. Flag `toggleFormat()` calls that don't pass a `title`. Flag toggle buttons/menu items without `aria-pressed` or `role="menuitemcheckbox"`.

---

### AP-07: Non-Semantic Element Used as Interactive Control

- **Frequency:** 4 commits fix this pattern
- **Severity:** P1
- **Description:** A `<div>`, `<span>`, or other non-interactive element is used as a button or link by adding `onClick`, `role="button"`, `tabIndex="0"`, and custom `onKeyDown` handlers instead of using the native `<button>` element.
- **Root cause:** Developers use divs or spans for styling convenience, then add interaction props. This is fragile, misses built-in keyboard behavior, and often has subtle bugs (missing Space key handling, missing focus styles).
- **Impact on users:** Keyboard users may find the control does not respond to Space or Enter correctly. Screen reader users may not be announced the correct role. The control may lack focus indication.

#### Example 1: Block Styles Using div Instead of button
- **Commit:** `65e412fac79c8f7c63dded91759300aad9361a0c`
- **Component:** `packages/block-editor/src/components/block-styles/index.js`
- **Before (buggy code):**
```tsx
// A div with role="button", manual onKeyDown, and tabIndex
<div
    onFocus={ () => styleItemHandler( style ) }
    onMouseLeave={ () => styleItemHandler( null ) }
    onBlur={ () => styleItemHandler( null ) }
    onKeyDown={ ( event ) => {
        if ( ENTER === event.keyCode || SPACE === event.keyCode ) {
            event.preventDefault();
            onSelectStylePreview( style );
        }
    } }
    onClick={ () => onSelectStylePreview( style ) }
    role="button"
    tabIndex="0"
>
```
- **After (fix):**
```tsx
// Replaced with native interaction patterns and proper semantics
<div
    onFocus={ () => styleItemHandler( style ) }
    onMouseLeave={ () => styleItemHandler( null ) }
    onBlur={ () => styleItemHandler( null ) }
    onClick={ () => onSelectStylePreview( style ) }
    aria-current={ activeStyle.name === style.name }
>
```
- **Analysis:** The `role="button"` div with manual keyCode handling was replaced. The unnecessary role and tabIndex were removed because the parent already had proper button semantics. The manual `onKeyDown` handler for Enter/Space was also removed. The `aria-current` attribute was added to indicate the active style.

#### Example 2: Cover Block Color Options Not Keyboard Accessible
- **Commit:** `d3a95861935d0a8ec92d9f0685343028769a447d`
- **Component:** `packages/block-library/src/cover/edit/index.js`
- **Before (buggy code):**
```tsx
// Color options rendered as listbox options (role="option")
// Not keyboard accessible -- options required listbox interaction pattern
<CircularOptionPicker
    value={ overlayColor.color }
    onChange={ onSetOverlayColor }
    clearable={ false }
/>
// Tests used role="option" to find elements
screen.getByRole( 'option', { name: 'Black' } )
```
- **After (fix):**
```tsx
// Color options rendered as buttons -- directly keyboard accessible
<CircularOptionPicker
    value={ overlayColor.color }
    onChange={ onSetOverlayColor }
    clearable={ false }
    asButtons
    aria-label={ __( 'Overlay color' ) }
/>
// Tests now use role="button"
screen.getByRole( 'button', { name: 'Black' } )
```
- **Analysis:** The color picker on the Cover block placeholder used `role="option"` inside a `role="listbox"`, which required arrow key navigation. For a simple color selection at the block placeholder level, individual buttons are more accessible because each is directly focusable and activatable. The `asButtons` prop switches the CircularOptionPicker to render buttons with a `role="group"` container.

- **Prevention rule:** Always use native `<button>` for click-triggered actions and `<a href>` for navigation. Never use `<div>` or `<span>` with `role="button"` unless there is a compelling technical reason. If you must, ensure Space, Enter, and focus handling are correct. For option selection, prefer `asButtons` mode when options are few and users benefit from direct Tab/Enter access.
- **Detection heuristic:** Flag any element with `role="button"` that is not a `<button>`. Flag any `<div>` or `<span>` with both `onClick` and `tabIndex`. Flag manual `onKeyDown` handlers that check for `ENTER`/`SPACE` keyCodes on non-button elements.

---

### AP-08: Keyboard Trap or Escape Key Not Working in Nested Menus

- **Frequency:** 4 commits fix this pattern
- **Severity:** P1
- **Description:** Pressing Escape in a nested submenu closes all menus instead of just the current level, or does not close the menu at all. Focus is not returned to the parent menu's trigger button.
- **Root cause:** Escape key event handlers use `stopPropagation()` incorrectly (or fail to use it), causing the event to either propagate to parent menus (closing them all) or be swallowed without closing the current menu.
- **Impact on users:** Keyboard users cannot navigate menu hierarchies predictably. They either get trapped or lose their place by having all menus close at once.

#### Example 1: Navigation Submenu Escape Closes All Levels
- **Commit:** `c1ae94db890d8c3fdbb5b8f93b0855aac1329404`
- **Component:** `packages/block-library/src/navigation/view.js`
- **Before (buggy code):**
```tsx
// Escape closed the menu but didn't stop propagation
if ( event?.key === 'Escape' ) {
    actions.closeMenu( 'click' );
    actions.closeMenu( 'focus' );
    return;
}
```
- **After (fix):**
```tsx
// event.stopPropagation() prevents ancestor menus from also closing
if ( event.key === 'Escape' ) {
    event.stopPropagation(); // Keeps ancestor menus open.
    actions.closeMenu( 'click' );
    actions.closeMenu( 'focus' );
    return;
}
```
- **Analysis:** Without `event.stopPropagation()`, the Escape keypress bubbled up to parent navigation blocks, closing all menu levels at once. The fix adds `stopPropagation()` so only the innermost submenu closes, returning focus to its parent trigger. This matches the WAI-ARIA Menu pattern where Escape closes one level at a time.

- **Prevention rule:** In nested menu/submenu architectures, Escape key handlers MUST call `event.stopPropagation()` to prevent parent menus from also closing. Each menu level should close independently and return focus to its trigger. Test with nested menus at least 2 levels deep.
- **Detection heuristic:** In navigation/menu components, look for Escape key handlers that call close/dismiss functions without `event.stopPropagation()`. Flag any menu escape handler that does not explicitly manage focus return to the parent trigger.

---

### AP-09: Focus Stealing on Component Mount/Re-render

- **Frequency:** 5 commits fix this pattern
- **Severity:** P1
- **Description:** A component, when it mounts or re-renders, programmatically moves focus to itself even though the user's focus was already somewhere meaningful. This "steals" focus from the user's current interaction.
- **Root cause:** `useEffect` hooks with focus logic run on mount or when dependencies change, moving focus without checking if focus is already in a valid location. `requestAnimationFrame` callbacks that set focus fire after the user has already moved focus elsewhere.
- **Impact on users:** Users are repeatedly interrupted as their focus jumps to unexpected locations. Typing in one field causes focus to jump to another. Moving blocks via toolbar buttons causes focus to jump away from the mover controls.

#### Example 1: Toolbar Focus Stealing After Block Move
- **Commit:** `a432f6654b2973781093f56fdeaf66bab7601017`
- **Component:** `packages/block-editor/src/components/navigable-toolbar/index.js`
- **Before (buggy code):**
```tsx
// After toolbar re-render, a requestAnimationFrame unconditionally moved focus
if ( ! initialFocusOnMount ) {
    raf = window.requestAnimationFrame( () => {
        const items = getAllFocusableToolbarItemsIn( navigableToolbarRef );
        // This moved focus to the previously active item, even if
        // focus was already inside the toolbar (e.g., on Move Up button)
    } );
}

// Also: aria-disabled buttons were excluded from focusable items
function getAllFocusableToolbarItemsIn( container ) {
    return Array.from(
        container.querySelectorAll(
            '[data-toolbar-item]:not([disabled]):not([aria-disabled="true"])'
        )
    );
}
```
- **After (fix):**
```tsx
// Check if toolbar already has focus before moving it
if (
    ! initialFocusOnMount &&
    ! hasFocusWithin( navigableToolbarRef )
) {
    raf = window.requestAnimationFrame( () => {
        const items = getAllFocusableToolbarItemsIn( navigableToolbarRef );
        // Only move focus if toolbar doesn't already have it
    } );
}

// aria-disabled buttons are now included as focusable
function getAllFocusableToolbarItemsIn( container ) {
    return Array.from(
        container.querySelectorAll( '[data-toolbar-item]:not([disabled])' )
    );
}
```
- **Analysis:** When a user pressed the "Move Up" button in the toolbar, the block moved, causing a re-render. The re-render triggered a `requestAnimationFrame` that moved focus to the toolbar's previously stored index, jumping focus away from the Move Up button. The fix adds a `hasFocusWithin()` check to skip focus movement when the toolbar already has focus. It also includes `aria-disabled` buttons as focusable items.

#### Example 2: Accordion Block Auto-Focus Stealing
- **Commit:** `fdec4a0809e4dbc985ed8364042468204cc65c4a`
- **Component:** `packages/block-library/src/accordion-heading/edit.js`
- **Before (buggy code):**
```tsx
// The toggle button inside the accordion heading had default tabIndex (0),
// which caused it to receive focus when the block was inserted,
// fighting with the RichText that should receive initial focus
<button className="wp-block-accordion-heading__toggle">
    { showIcon && iconPosition === 'left' && ( <span ... /> ) }
```
- **After (fix):**
```tsx
// tabIndex="-1" prevents the toggle from stealing focus on block insertion
<button
    className="wp-block-accordion-heading__toggle"
    tabIndex="-1"
>
    { showIcon && iconPosition === 'left' && ( <span ... /> ) }
```
- **Analysis:** The `<button>` element in the accordion heading was focusable by default. When the accordion block was inserted, focus management attempted to focus the first focusable element, which was this toggle button instead of the editable summary text. Setting `tabIndex="-1"` removes it from the tab order so focus goes to the RichText editor instead.

- **Prevention rule:** Before programmatically moving focus, ALWAYS check if the target container already has focus (use `container.contains(document.activeElement)`). Never move focus in `useEffect`/`requestAnimationFrame` without this guard. Use `tabIndex="-1"` on decorative or non-primary-interaction buttons that should not receive initial focus.
- **Detection heuristic:** Look for `requestAnimationFrame` or `useEffect` callbacks that call `.focus()` without first checking `document.activeElement` or `container.contains(document.activeElement)`. Flag any focusable element inside a block that could steal initial focus from the primary editing element.

---

### AP-10: Firefox/Safari Browser-Specific Focus Bugs

- **Frequency:** 4 commits fix this pattern
- **Severity:** P1
- **Description:** Browser-specific focus handling differences cause accessibility bugs. Safari does not focus checkboxes/radios on click. Firefox has bugs with `aria-describedby` text content updates and iframe focus management.
- **Root cause:** Browsers implement focus differently. Safari does not focus form controls on mouse click (only on Tab). Firefox does not recompute `aria-describedby` text when only the text node changes. Firefox's `body.focus()` inside an iframe causes the parent document to focus the iframe element.
- **Impact on users:** In Safari, clicking a checkbox/toggle does not give it focus, breaking subsequent keyboard navigation. In Firefox, dynamically updated descriptions are not re-announced. In Firefox, typing in sidebar inputs while an iframe editor is active causes focus to jump to the iframe.

#### Example 1: Safari Checkbox/Toggle Not Focused on Click
- **Commit:** `9c725d04c5e08bd47269c28d1f5e3bc81269a37b`
- **Component:** `packages/components/src/checkbox-control/index.tsx`, `packages/components/src/form-toggle/index.tsx`, `packages/components/src/radio-control/index.tsx`
- **Before (buggy code):**
```tsx
// Standard checkbox -- Safari does not focus it on click
<input
    type="checkbox"
    onChange={ onChangeValue }
    checked={ checked }
/>
```
- **After (fix):**
```tsx
// Explicit focus on click for Safari compatibility
<input
    type="checkbox"
    onChange={ onChangeValue }
    checked={ checked }
    onClick={ ( event ) => {
        // Compat code for Safari to ensure that the checkbox is focused when clicked.
        event.currentTarget.focus();
        onClick?.( event );
    } }
/>
```
- **Analysis:** Safari does not automatically focus checkboxes, radio buttons, or toggle switches when clicked. This means clicking a toggle, then pressing Tab, would not continue from the toggle's position. The fix explicitly calls `event.currentTarget.focus()` on click for all three control types.

#### Example 2: Firefox aria-describedby Text Not Recomputed
- **Commit:** `a9e19c0a139fd60f357497628d9c8047cff15bd5`
- **Component:** `packages/block-editor/src/components/list-view/aria-referenced-text.js` (new file)
- **Before (buggy code):**
```tsx
// Standard div with aria-describedby reference
<div
    className="list-view-appender__description"
    id={ descriptionId }
>
    { description }
</div>
```
- **After (fix):**
```tsx
// New component that forces Firefox to recompute text content
export default function AriaReferencedText( { children, ...props } ) {
    const ref = useRef();
    useEffect( () => {
        if ( ref.current ) {
            // This seems like a no-op, but it fixes a bug in Firefox where
            // it fails to recompute the text when only the text node changes.
            ref.current.textContent = ref.current.textContent;
        }
    }, [ children ] );

    return (
        <div hidden { ...props } ref={ ref }>
            { children }
        </div>
    );
}
```
- **Analysis:** Firefox has a bug where changing the text content of an element referenced by `aria-describedby` does not trigger re-announcement. The workaround forces a textContent reassignment (a no-op in terms of content, but triggers Firefox to recompute the accessible description). The element also uses `hidden` instead of a visually-hidden class.

#### Example 3: Firefox Focus Jumping to Iframe During Sidebar Input
- **Commit:** `1f0fa4b0c7a6ffe0b2a2450fd26c9ccf4a502c95`
- **Component:** `packages/rich-text/src/to-dom.js`
- **Before (buggy code):**
```tsx
// After selection.addRange(), focus was restored to body, which in Firefox
// causes the parent document to focus the iframe
if ( activeElement instanceof defaultView.HTMLElement ) {
    activeElement.focus();
}
```
- **After (fix):**
```tsx
if ( activeElement instanceof defaultView.HTMLElement ) {
    // Don't restore focus to BODY or HTML elements, as this can cause
    // unwanted focus changes. In Firefox, focusing BODY inside an iframe
    // can cause the parent document to focus the iframe itself.
    const tagName = activeElement.tagName;
    const isContentEditable = activeElement.isContentEditable;
    const isMeaningfulElement =
        tagName !== 'BODY' && tagName !== 'HTML' && tagName !== 'DIV';

    if ( isContentEditable || isMeaningfulElement ) {
        activeElement.focus();
    }
}
```
- **Analysis:** In Firefox, `selection.addRange()` inside an iframe changes focus from BODY to a contentEditable. The code attempted to restore focus by calling `body.focus()`, but in Firefox, focusing BODY inside an iframe causes the parent document to focus the iframe element itself, pulling focus out of the sidebar input.

- **Prevention rule:** Always test in Firefox and Safari, not just Chrome. For form controls (checkbox, radio, toggle), explicitly call `event.currentTarget.focus()` on click for Safari compatibility. For `aria-describedby`/`aria-labelledby` referenced text, use the `AriaReferencedText` pattern to force Firefox recomputation. Never call `.focus()` on `<body>`, `<html>`, or generic `<div>` elements inside iframes.
- **Detection heuristic:** Flag checkbox/radio/toggle controls without explicit `onClick` focus handling. Flag `aria-describedby` target elements that have dynamic text content without the textContent reassignment workaround. Flag any `.focus()` call on `document.body` inside iframe contexts.

---

### AP-11: Canvas Iframe Tab Order and Silent Tab Stops

- **Frequency:** 3 commits fix this pattern
- **Severity:** P1
- **Description:** The editor canvas iframe creates unexpected tab stops or "silent" tab stops where focus lands on invisible or non-interactive elements. Users Tab to invisible focus traps before/after the iframe, or Tab to an iframe with an unhelpful label.
- **Root cause:** Focus-capturing `<div>` elements rendered before/after the iframe for edit mode are not removed in view/preview mode. The iframe's `title` attribute and `role` are not updated based on the current mode.
- **Impact on users:** Keyboard users encounter extra Tab stops that seem to do nothing (silent tab stops). The iframe button lacks a descriptive label, making it unclear what activating it will do.

#### Example 1: Silent Tab Stops in View Mode
- **Commit:** `98552b2486b52e4dd3c90775c2a029b95e64253c`
- **Component:** `packages/block-editor/src/components/iframe/index.js`, `packages/edit-site/src/components/block-editor/editor-canvas.js`
- **Before (buggy code):**
```tsx
// Focus capture divs always rendered regardless of mode
return (
    <>
        { tabIndex >= 0 && before }
        <iframe title={ __( 'Editor canvas' ) } ... />
        { tabIndex >= 0 && after }
    </>
);

// View mode props had generic label
const viewModeProps = {
    'aria-label': __( 'Editor Canvas' ),
    role: 'button',
    tabIndex: 0,
};
```
- **After (fix):**
```tsx
// Focus capture divs only rendered when needed (edit mode, not preview)
const shouldRenderFocusCaptureElements = tabIndex >= 0 && ! isPreviewMode;

return (
    <>
        { shouldRenderFocusCaptureElements && before }
        <iframe title={ title } ... />
        { shouldRenderFocusCaptureElements && after }
    </>
);

// View mode props have action-oriented label, no title attribute
const viewModeIframeProps = {
    'aria-label': __( 'Edit' ),
    title: null,
    role: 'button',
    tabIndex: 0,
};
```
- **Analysis:** The focus-capturing `<div>` elements before and after the iframe were rendered in view/preview mode, creating invisible tab stops. The fix conditionally renders them only when in edit mode. The iframe's `aria-label` in view mode was changed from the generic "Editor Canvas" to the action-oriented "Edit", and `title` was set to `null` to avoid redundant announcements.

- **Prevention rule:** Focus-capturing helper elements MUST be conditionally rendered based on the interaction mode. In view/preview modes, remove all non-essential tab stops. Iframe buttons should have action-oriented labels ("Edit") rather than descriptive labels ("Editor Canvas"). Set `title={null}` when `aria-label` provides the accessible name to avoid double announcements.
- **Detection heuristic:** Look for focus-capturing `<div>` elements around iframes that do not check for preview/view mode. Flag iframes with `role="button"` that have generic labels like "Editor Canvas" or "Preview".

---

### AP-12: Inert Content Not Properly Communicated

- **Frequency:** 3 commits fix this pattern
- **Severity:** P2
- **Description:** Preview content, disabled forms, or non-interactive editor previews are either made inert (removing them from the accessibility tree entirely) or left fully interactive when they should be read-only.
- **Root cause:** Using the HTML `inert` attribute makes content completely invisible to screen readers. Not using it leaves interactive controls that shouldn't be interacted with.
- **Impact on users:** Screen reader users either cannot perceive preview content at all, or they encounter interactive controls that don't work and provide no explanation of why.

#### Example 1: Comments Form Preview Using inert
- **Commit:** `557d84b9506113261473b1039284dd4869a8a077`
- **Component:** `packages/block-library/src/post-comments-form/form.js`, `packages/block-library/src/post-comments-form/edit.js`
- **Before (buggy code):**
```tsx
// inert made the entire form invisible to screen readers
<form noValidate className="comment-form" inert="true">
    <textarea name="comment" cols="45" rows="8" />
    <input type="submit" value={ __( 'Post Comment' ) } />
</form>
```
- **After (fix):**
```tsx
// Form is accessible but non-functional, with descriptive message
<form
    noValidate
    className="comment-form"
    onSubmit={ ( event ) => event.preventDefault() }
>
    <textarea name="comment" cols="45" rows="8" readOnly />
    <input
        type="submit"
        value={ __( 'Post Comment' ) }
        aria-disabled="true"
    />
</form>

// Block-level description explains the preview context
<div { ...blockProps }>
    <CommentsForm postId={ postId } postType={ postType } />
    <VisuallyHidden id={ instanceIdDesc }>
        { __( 'Comments form disabled in editor.' ) }
    </VisuallyHidden>
</div>
```
- **Analysis:** The `inert` attribute made the comments form completely invisible to screen readers -- users could not perceive that a comments form exists in the layout. The fix replaces `inert` with individual control disabling: `readOnly` for the textarea, `aria-disabled="true"` for the submit button, `onSubmit` prevention for the form. A `VisuallyHidden` description explains the preview context.

#### Example 2: HTML Block Preview Lacking Screen Reader Guidance
- **Commit:** `ceadbe4b3ece5ecc06a4e161d195c85d83c01514`
- **Component:** `packages/block-library/src/html/edit.js`, `packages/block-library/src/html/preview.js`
- **Before (buggy code):**
```tsx
// HTML preview rendered an iframe with no accessibility guidance
<SandBox html={ content } styles={ styles } />
```
- **After (fix):**
```tsx
// Iframe gets a descriptive title and tabIndex for focus management
<SandBox
    html={ content }
    styles={ styles }
    title={ __( 'Custom HTML Preview' ) }
    tabIndex={ -1 }
/>

// Block wrapper describes the preview limitations
<div { ...blockProps }>
    { isPreview && (
        <>
            <Preview content={ attributes.content } isSelected={ isSelected } />
            <VisuallyHidden id={ instanceId }>
                { __( 'HTML preview is not yet fully accessible. Please switch screen reader to virtualized mode to navigate the below iFrame.' ) }
            </VisuallyHidden>
        </>
    ) }
</div>
```
- **Analysis:** The HTML block's preview iframe had no `title` attribute and no guidance for screen reader users. The fix adds a descriptive `title`, sets `tabIndex={-1}` to prevent it from being an unexpected tab stop, and provides screen reader guidance about how to navigate the iframe content.

- **Prevention rule:** Never use `inert` on content that screen reader users should be able to perceive (e.g., previews, form layouts). Instead, use `readOnly`, `aria-disabled="true"`, and `onSubmit` prevention to disable interactivity while maintaining perceptibility. Always provide context for preview/read-only content via `aria-describedby` and `VisuallyHidden` messages. Iframes in preview mode should have descriptive `title` attributes and guidance.
- **Detection heuristic:** Flag any use of `inert` attribute. Flag iframes without `title` attributes. Flag preview components that lack `aria-describedby` or VisuallyHidden context messages.

---

### AP-13: Unnecessary/Incorrect ARIA role on Elements

- **Frequency:** 3 commits fix this pattern
- **Severity:** P2
- **Description:** ARIA roles are applied to elements where they are unnecessary (the element already has the correct semantics), incorrect (the role does not match the interaction pattern), or harmful (the role causes screen reader behavior changes).
- **Root cause:** Developers add ARIA roles "to be safe" without understanding that native HTML elements already have implicit roles. Or roles are applied for styling/testing purposes rather than semantic ones.
- **Impact on users:** Screen readers may announce elements incorrectly, or change interaction modes unexpectedly. Unnecessary roles add noise to the accessible tree.

#### Example 1: BlockVariationPicker Using role="presentation" Incorrectly
- **Commit:** `8795c6e336d7cdf69caa02762bb3a0f4681c6fa7`
- **Component:** `packages/block-editor/src/components/block-variation-picker/index.js`
- **Before (buggy code):**
```tsx
<span
    className="block-editor-block-variation-picker__variation-label"
    role="presentation"
>
    { variation.title }
</span>
```
- **After (fix):**
```tsx
<span className="block-editor-block-variation-picker__variation-label">
    { variation.title }
</span>
```
- **Analysis:** `role="presentation"` removes an element's semantics from the accessibility tree. On a `<span>` containing visible text, this was harmful -- it could cause the text to not be associated with its button. The role was unnecessary since `<span>` has no implicit role that needs to be removed.

#### Example 2: Role=application for List View NVDA Browse Mode
- **Commit:** `d16040f4db853e50007c59661199b07a3b4926c9`
- **Component:** `packages/components/src/tree-grid/index.js`
- **Before (buggy code):**
```tsx
// NVDA browse mode intercepted arrow keys, breaking tree navigation
<table role="treegrid" onKeyDown={ onKeyDown } ref={ ref }>
    <tbody>{ children }</tbody>
</table>
```
- **After (fix):**
```tsx
// role="application" wrapper prevents NVDA browse mode from triggering
<div role="application" aria-label={ applicationAriaLabel }>
    <table role="treegrid" onKeyDown={ onKeyDown } ref={ ref }>
        <tbody>{ children }</tbody>
    </table>
</div>
```
- **Analysis:** NVDA's browse mode was intercepting keyboard events (arrow keys, letters) before they reached the treegrid's JavaScript handlers. Wrapping the treegrid in `role="application"` tells NVDA to pass all keyboard events through to the application, enabling the custom keyboard interaction pattern. Note: `role="application"` should be used sparingly and only when a custom keyboard interaction model is implemented. It must have an `aria-label`.

- **Prevention rule:** Do not add `role="presentation"` to elements that contain visible text content. Use `role="application"` only when implementing a custom keyboard interaction model that conflicts with screen reader browse mode, and always pair it with `aria-label`. Before adding any ARIA role, check if the native HTML element already provides the correct semantics.
- **Detection heuristic:** Flag `role="presentation"` or `role="none"` on elements that contain visible text or interactive children. Flag `role="application"` usage and verify it has `aria-label` and genuinely needs to override browse mode.

---

### AP-14: Table Semantic Structure Broken in Editor

- **Frequency:** 2 commits fix this pattern
- **Severity:** P2
- **Description:** The Table block's editor renders `<td>` and `<th>` elements with `contentEditable` applied directly, breaking screen reader table navigation because the semantic table cell becomes a content-editable region.
- **Root cause:** `RichText` was rendered with `tagName="td"`, making the `<td>` itself content-editable. Screen readers interpret content-editable table cells differently from normal table cells, losing cell position announcements.
- **Impact on users:** Screen reader users navigating the table with Ctrl+Alt+Arrow keys do not hear cell position (e.g., "row 2, column 3"). The table appears as a flat content-editable region rather than a structured data table.

#### Example 1: Table Block Semantic Structure Fix
- **Commit:** `bd52276857a70d4ea33c7947ac71247e5ab933ac`
- **Component:** `packages/block-library/src/table/edit.js`
- **Before (buggy code):**
```tsx
// RichText rendered directly as <td>, making the cell content-editable
<RichText
    tagName={ CellTag }
    key={ columnIndex }
    scope={ CellTag === 'th' ? scope : undefined }
    colSpan={ colspan }
    rowSpan={ rowspan }
    value={ content }
    onChange={ onChange }
    aria-label={ cellAriaLabel[ name ] }
/>
```
- **After (fix):**
```tsx
// Semantic <td>/<th> wraps the RichText, preserving table structure
<CellTag
    key={ columnIndex }
    scope={ CellTag === 'th' ? scope : undefined }
    colSpan={ colspan }
    rowSpan={ rowspan }
    className={ classnames( { ... }, 'wp-block-table__cell-content' ) }
>
    <RichText
        value={ content }
        onChange={ onChange }
        aria-label={ cellAriaLabel[ name ] }
    />
</CellTag>
```
- **Analysis:** By separating the semantic `<td>`/`<th>` element from the content-editable `RichText` inside it, screen readers correctly identify the table cell semantics and announce row/column positions during table navigation. The focus selector also needed updating from `td[contentEditable="true"]` to `td div[contentEditable="true"]`.

- **Prevention rule:** Never make semantic table elements (`<td>`, `<th>`, `<tr>`) directly content-editable. Place content-editable regions inside table cells, not as the cells themselves. Maintain proper `<table>` > `<thead>`/`<tbody>` > `<tr>` > `<th>`/`<td>` hierarchy.
- **Detection heuristic:** Flag any `<RichText tagName="td">` or `<RichText tagName="th">` pattern. Flag any table cell that has `contentEditable` directly on it.

---

### AP-15: Redundant/Conflicting ARIA Live Region Announcements

- **Frequency:** 2 commits fix this pattern
- **Severity:** P2
- **Description:** An element has both `role="alert"` and `aria-live="polite"` (conflicting politeness levels), or the same text is announced via both an ARIA live region and `aria-describedby`, causing double announcements.
- **Root cause:** Developers add multiple announcement mechanisms without understanding they overlap. `role="alert"` implicitly has `aria-live="assertive"`, so adding `aria-live="polite"` creates a conflict. Text referenced by `aria-describedby` is read when the associated input is focused, so a live region on the same text creates a duplicate announcement.
- **Impact on users:** Screen reader users hear the same message twice, which is confusing and time-wasting.

#### Example 1: Navigation Link Error Announced Twice
- **Commit:** `bcc4ea390abd5571b88f6719c93e8448cebc5db5`
- **Component:** `packages/block-library/src/navigation-link/shared/controls.js`
- **Before (buggy code):**
```tsx
// Error text had both role="alert" AND aria-live="polite" AND was referenced by aria-describedby
<span
    id={ id }
    className="navigation-link-control__error-text"
    role="alert"
    aria-live="polite"
>
    { sprintf(
        __( 'Synced %s is missing. Please update or remove this link.' ),
        entityType
    ) }
</span>
```
- **After (fix):**
```tsx
// Error text is only referenced via aria-describedby -- no live region needed
<span id={ id } className="navigation-link-control__error-text">
    <MissingEntityHelpText type={ type } kind={ kind } />
</span>

// Additionally, a VisuallyHidden description is added to the block itself
<VisuallyHidden id={ missingEntityDescriptionId }>
    <MissingEntityHelpText type={ type } kind={ kind } />
</VisuallyHidden>
```
- **Analysis:** The error text had three competing announcement mechanisms: `role="alert"` (assertive live region), `aria-live="polite"`, and `aria-describedby` from the associated input. This caused the error to be announced once when it appeared (via the live region) and again when the user focused the input (via `aria-describedby`). The fix removes the live region attributes, relying solely on `aria-describedby` for contextual announcement. Additionally, `aria-invalid` is added to the block wrapper element to indicate the error state.

- **Prevention rule:** Choose ONE announcement mechanism per message. Use `aria-describedby` for persistent contextual information that should be read on focus. Use `wp.a11y.speak()` or live regions for transient notifications. Never combine `role="alert"` with `aria-live="polite"` (they conflict). Never put live region attributes on elements that are also referenced by `aria-describedby`.
- **Detection heuristic:** Flag elements with both `role="alert"` and `aria-live`. Flag elements that have `aria-live` AND are referenced by another element's `aria-describedby` or `aria-labelledby`.

---

## Frequency Analysis

### Anti-Patterns Ranked by Occurrence
| Rank | Anti-Pattern | Count | Severity | Components Affected |
|------|-------------|-------|----------|-------------------|
| 1 | AP-01: Focus Lost on State Change / Re-render | 12 | P0 | Navigation Link, FormTokenField, ImageURLInputUI, Editor Canvas, Sidebar Inputs |
| 2 | AP-02: Missing Focus Return After Overlay Close | 8 | P0 | Save Panel, URLPopover, Social Link, Modal, Link UI |
| 3 | AP-03: Invalid ARIA Attribute Usage | 7 | P1 | MenuItem, DropdownMenu, BlockList (useBlockProps), Navigation menus |
| 4 | AP-04: Missing aria-haspopup/aria-expanded | 6 | P1 | Block Lock, Navigation, Template Part menus |
| 5 | AP-06: Live Region Not Announcing Dynamic Content | 5 | P1 | Autocomplete, Format Library (bold/italic/code), Inserter |
| 6 | AP-09: Focus Stealing on Mount/Re-render | 5 | P1 | Navigable Toolbar, Accordion, Details Block, Block List |
| 7 | AP-05: Disabled Buttons Removed from Tab Order | 4 | P1 | Button, DropdownMenu, Autocomplete, FormTokenField, RangeControl |
| 8 | AP-07: Non-Semantic Element as Interactive Control | 4 | P1 | Block Styles, Cover Placeholder, Document Outline |
| 9 | AP-08: Keyboard Trap / Escape Not Working | 4 | P1 | Navigation Submenus, Combobox, Modal |
| 10 | AP-10: Browser-Specific Focus Bugs | 4 | P1 | CheckboxControl, RadioControl, ToggleControl, RichText, List View |
| 11 | AP-11: Canvas Iframe Tab Order Issues | 3 | P1 | Editor Canvas (Iframe), Site Editor |
| 12 | AP-13: Unnecessary/Incorrect ARIA Role | 3 | P2 | BlockVariationPicker, TreeGrid, Block Styles |
| 13 | AP-12: Inert Content Not Communicated | 3 | P2 | Comments Form, HTML Block Preview |
| 14 | AP-14: Table Semantic Structure Broken | 2 | P2 | Table Block |
| 15 | AP-15: Redundant ARIA Live Announcements | 2 | P2 | Navigation Link, Help Text |

### Heat Map by Component Area
| Component Area | Total Fixes | Top Anti-Pattern |
|---------------|-------------|-----------------|
| Navigation Block (+ Link) | 12 | Focus lost on state change, Escape key handling |
| Block Toolbar / Navigable Toolbar | 7 | Focus stealing on re-render, Focus return |
| Modal / Popover / Dialog | 7 | Focus return after close, Missing dialog role |
| Form Controls (Token, Combobox, Select) | 5 | Focus lost on tab, Virtual focus screen reader issues |
| Autocomplete / Inserter | 4 | Live region not announcing, Keyboard a11y |
| Site Editor (Canvas/Iframe) | 4 | Silent tab stops, Focus loss after save |
| Table / Details / Accordion Blocks | 4 | Semantic structure, Keyboard accessibility |
| Format Library (Bold/Italic/etc.) | 2 | No announcement on formatting change |
| Cover Block | 2 | Non-semantic elements, Color options not keyboard accessible |
| List View (TreeGrid) | 3 | NVDA browse mode, aria-describedby Firefox bug |

### Correlation with Complexity
- **Navigation block** is by far the most a11y-bug-prone component, with 12+ fixes across focus management, ARIA states, keyboard handling, and submenu behavior. Its complexity comes from: nested interactive elements, multiple interaction modes (click/hover/keyboard), and the interplay between editor and front-end rendering.
- **Focus management** is the single largest source of a11y bugs (AP-01 + AP-02 + AP-09 = 25 fixes combined). This correlates with React's rendering model -- state changes trigger re-renders that destroy/recreate DOM nodes, making focus inherently fragile.
- **Components that use popovers/overlays** are second-highest in bug frequency because they require a strict lifecycle: store trigger ref > open overlay > manage focus inside > return focus on close. Any step missed creates a P0 bug.
- **Browser-specific bugs** cluster around Safari's focus-on-click behavior and Firefox's ARIA computation bugs, suggesting these browsers need explicit testing in CI.

## Prevention Rules (Distilled)

Numbered, actionable rules for AI agents, priority-ordered by frequency:

1. **Guard focus on state changes:** Before any state update that conditionally renders or re-mounts a DOM subtree, check if the focused element is inside that subtree. If it is, either (a) do not unmount the element (use CSS hiding instead), or (b) store the focus position and restore it after the re-render. Prevents: AP-01, AP-09. Frequency: 17 bugs.

2. **Always return focus from overlays:** Every popover, modal, dialog, and dropdown MUST store a ref to its trigger element and call `triggerRef.focus()` in ALL close paths (close button, Escape, click outside, programmatic close). Never unmount the trigger while the overlay is open. Prevents: AP-02. Frequency: 8 bugs.

3. **Validate ARIA attributes against roles:** Before applying `aria-checked`, `aria-selected`, `aria-expanded`, or `aria-pressed`, verify the element's role supports that attribute per WAI-ARIA spec. Never insert non-role-bearing wrapper elements (plain `<div>`, `<span>`) inside ARIA container widgets. Prevents: AP-03. Frequency: 7 bugs.

4. **Add aria-haspopup and aria-expanded to all trigger buttons:** Every button that opens a popup must have `aria-haspopup` with the correct value and `aria-expanded` that dynamically reflects the open state across ALL interaction modes (click, hover, keyboard, programmatic). Prevents: AP-04. Frequency: 6 bugs.

5. **Announce all dynamic content changes:** Every visual change that a sighted user can perceive (results appearing, counts changing, state toggling, formatting applied) must have a corresponding screen reader announcement via `wp.a11y.speak()` or ARIA live regions. Use debouncing (500ms) to avoid announcement spam. Prevents: AP-06. Frequency: 5 bugs.

6. **Check hasFocusWithin before programmatic focus:** Before calling `.focus()` in `useEffect`, `requestAnimationFrame`, or any asynchronous callback, check `container.contains(document.activeElement)`. Skip the focus call if focus is already inside the target container. Prevents: AP-09. Frequency: 5 bugs.

7. **Use aria-disabled instead of HTML disabled:** For interactive controls that users need to discover, use `aria-disabled="true"` with handler prevention instead of the HTML `disabled` attribute. In Gutenberg's Button, use `__experimentalIsFocusable` with `disabled`. Prevents: AP-05. Frequency: 4 bugs.

8. **Use native semantic elements:** Use `<button>` for actions, `<a href>` for navigation. Never use `<div role="button">` with manual keyDown handlers unless technically unavoidable. For option pickers, prefer `asButtons` mode for direct keyboard access. Prevents: AP-07. Frequency: 4 bugs.

9. **stopPropagation on Escape in nested menus:** In nested menu/submenu architectures, Escape handlers MUST call `event.stopPropagation()` so only the innermost menu closes. Return focus to the parent trigger. Prevents: AP-08. Frequency: 4 bugs.

10. **Test in Firefox and Safari:** Always test checkbox, radio, and toggle focus on click (Safari bug). Test dynamic `aria-describedby` text updates (Firefox bug). Test iframe focus management (Firefox bug). Add explicit `event.currentTarget.focus()` on click handlers for form controls. Prevents: AP-10. Frequency: 4 bugs.

11. **Remove non-interactive tab stops in view/preview mode:** Focus-capturing helper elements around iframes must be conditionally rendered. Remove them in view/preview modes. Use action-oriented labels ("Edit") on iframe buttons instead of descriptive labels ("Editor Canvas"). Prevents: AP-11. Frequency: 3 bugs.

12. **Choose ONE announcement mechanism:** Never combine `role="alert"` with `aria-live`. Never put live region attributes on elements referenced by `aria-describedby`. Use `aria-describedby` for persistent context, `wp.a11y.speak()` for transient status. Prevents: AP-15. Frequency: 2 bugs.

13. **Preserve table cell semantics:** Never make `<td>` or `<th>` directly content-editable. Place editable regions inside semantic table cells, not as the cells themselves. Prevents: AP-14. Frequency: 2 bugs.

14. **Avoid role="presentation" on text-containing elements:** Do not use `role="presentation"` or `role="none"` on elements that contain visible text or interactive children. Use `role="application"` only when custom keyboard interaction conflicts with browse mode, and always pair with `aria-label`. Prevents: AP-13. Frequency: 3 bugs.

15. **Do not use inert for perceivable content:** Never apply the `inert` attribute to content users should be able to read (previews, form layouts). Use `readOnly`, `aria-disabled="true"`, and `onSubmit` prevention instead. Always add VisuallyHidden descriptions for preview/disabled contexts. Prevents: AP-12. Frequency: 3 bugs.

## Key Observations

### Systemic Pattern: React's Rendering Model vs. Focus Management
The single largest source of accessibility bugs in Gutenberg (25+ fixes) stems from the fundamental tension between React's declarative rendering model and the imperative nature of browser focus. React destroys and recreates DOM nodes on state changes, but focus is an imperative property of a specific DOM node. When that node is destroyed, focus is silently lost. This is not a bug in React -- it is an architectural mismatch that requires explicit management. Every component that conditionally renders interactive content near the user's focus point needs a focus management strategy.

### Trend: Focus Bugs Cluster in Complex Navigation Patterns
The Navigation block alone accounts for 12+ accessibility fixes because it combines multiple anti-patterns: nested menus, hover vs. click interaction, Escape key propagation, focus return from Link UI, and state-driven conditional rendering. Components with similar complexity (nested popovers inside toolbars inside iframes) follow the same pattern.

### Observation: ARIA Overuse is as Harmful as ARIA Underuse
Multiple fixes involve REMOVING ARIA attributes (`role="document"`, `role="presentation"`, `aria-checked` on wrong roles, `role="alert"` + `aria-live="polite"` conflict). The "first rule of ARIA" -- do not use ARIA if native HTML provides the correct semantics -- is frequently violated. Adding incorrect ARIA is worse than adding no ARIA, because it actively communicates wrong information.

### Observation: Browser-Specific Bugs Require Defensive Coding
Safari's refusal to focus checkboxes on click and Firefox's failure to recompute `aria-describedby` text are well-known browser bugs that Gutenberg has worked around multiple times. These workarounds are not in any specification -- they must be discovered through testing and shared as institutional knowledge. AI agents reviewing code should flag known browser-specific patterns.

### Observation: "Preview" Content is a Recurring Accessibility Challenge
Multiple blocks (Comments Form, HTML Block, Editor Canvas) struggle with the concept of "preview" content that should be perceivable but not interactive. The `inert` attribute is the wrong tool (makes content invisible). The pattern that works: maintain all content in the accessibility tree, disable interactivity with `readOnly`/`aria-disabled`, and provide contextual descriptions via `VisuallyHidden`.

### Observation: The Fix Often Includes an E2E Test
Many of the analyzed commits include Playwright E2E tests that verify the fix. This is notable because accessibility bugs have a high regression rate -- a focus management fix in one area can be broken by a seemingly unrelated change in another. E2E tests that verify focus position, `aria-expanded` state, and screen reader announcements are the most effective regression prevention.
