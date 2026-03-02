# Component Accessibility Patterns

> Research document for AI agent consumption. Part of the a11y research series.
> Generated: 2026-02-27 | Session 2, Task 2.1

## Methodology

This document analyzes 26+ Gutenberg components in `packages/components/src/` from the Gutenberg repository. For each component, source files were read and the following were extracted:

1. **ARIA attributes** -- every `aria-*` attribute, `role`, and labelling strategy
2. **Keyboard interaction** -- event handlers for `onKeyDown`, arrow key logic, escape handling
3. **Focus management** -- focus trapping, focus return, roving tabindex, `tabIndex` assignments
4. **Screen reader support** -- `speak()` calls, live regions, `VisuallyHidden` usage
5. **Infrastructure** -- which `@wordpress/compose` hooks, `@wordpress/a11y` functions, and Ariakit primitives are used

Components were analyzed in three tiers by interaction complexity. Tier 1 received full deep dives (all source files read). Tier 2 focused on the main component file. Tier 3 received quick scans.

---

## Tier 1: Complex Widget Deep Dives

### 1. Modal

- **Complexity tier:** 1
- **ARIA pattern:** Dialog (W3C APG Dialog Modal)
- **Source files:**
  - `/packages/components/src/modal/index.tsx`
  - `/packages/components/src/modal/aria-helper.ts`
  - `/packages/components/src/modal/types.ts`
  - `/packages/components/src/modal/context.ts`
  - `/packages/components/src/modal/use-modal-exit-animation.ts`

- **Key a11y attributes:**
  - `role="dialog"` (default, configurable via `role` prop) on the frame element
  - `aria-label={contentLabel}` -- direct label when `contentLabel` is provided
  - `aria-labelledby={headingId}` -- points to the `<h1>` title element when `contentLabel` is absent
  - `aria-describedby={aria.describedby}` -- optional description link
  - `aria-hidden="true"` -- applied to all sibling elements via `ariaHelper.modalize()`
  - `role="document"` on the scrollable content area
  - `aria-label={__('Scrollable section')}` -- conditionally on scrollable content
  - `aria-hidden` on icon container (decorative icon)
  - Close button uses `label={closeButtonLabel || __('Close')}`

- **Keyboard interaction model:**
  - **Escape**: closes the modal (via `handleEscapeKeyDown`); checks `shouldCloseOnEsc` and `event.code === 'Escape'`; `event.preventDefault()` to stop propagation
  - IME composition events are ignored via `withIgnoreIMEEvents` wrapper

- **Focus management:**
  - **Focus on mount**: `useFocusOnMount()` from `@wordpress/compose` -- supports `'firstElement'`, `'firstInputElement'`, `true` (focus container), `false`, and `'firstContentElement'`
  - **Focus trapping**: `useConstrainedTabbing()` -- traps Tab/Shift+Tab within the modal frame
  - **Focus return**: `useFocusReturn()` -- restores focus to the previously focused element on unmount
  - `tabIndex={-1}` on the frame div to make it programmatically focusable
  - `tabIndex={0}` conditionally on scrollable content area to make it keyboard-scrollable

- **Screen reader support:**
  - Background isolation via `ariaHelper.modalize()` / `unmodalize()` -- sets `aria-hidden="true"` on all body children except the modal and live regions
  - Live regions (elements with `aria-live`, `role="alert"`, `role="status"`, etc.) are explicitly preserved (not hidden)
  - Nested modals supported via `ModalContext` -- prior modals are dismissed

- **Infrastructure used:**
  - `useInstanceId` from `@wordpress/compose`
  - `useFocusOnMount`, `useConstrainedTabbing`, `useFocusReturn`, `useMergeRefs` from `@wordpress/compose`
  - `useReducedMotion` from `@wordpress/compose` (in exit animation)
  - `createPortal` -- renders into `document.body`
  - `withIgnoreIMEEvents` utility

- **Key code excerpts:**

  ```tsx
  // /packages/components/src/modal/index.tsx:81-85 — Focus management hooks
  const focusOnMountRef = useFocusOnMount(
    focusOnMount === 'firstContentElement' ? 'firstElement' : focusOnMount
  );
  const constrainedTabbingRef = useConstrainedTabbing();
  const focusReturnRef = useFocusReturn();
  ```

  ```tsx
  // /packages/components/src/modal/index.tsx:114-118 — Background isolation
  useEffect( () => {
    ariaHelper.modalize( ref.current! );
    return () => ariaHelper.unmodalize();
  }, [] );
  ```

  ```tsx
  // /packages/components/src/modal/aria-helper.ts:23-37 — modalize implementation
  export function modalize( modalElement?: HTMLDivElement ) {
    const elements = Array.from( document.body.children );
    const hiddenElements: Element[] = [];
    hiddenElementsByDepth.push( hiddenElements );
    for ( const element of elements ) {
      if ( element === modalElement ) {
        continue;
      }
      if ( elementShouldBeHidden( element ) ) {
        element.setAttribute( 'aria-hidden', 'true' );
        hiddenElements.push( element );
      }
    }
  }
  ```

  ```tsx
  // /packages/components/src/modal/aria-helper.ts:46-55 — Preserves live regions
  export function elementShouldBeHidden( element: Element ) {
    const role = element.getAttribute( 'role' );
    return ! (
      element.tagName === 'SCRIPT' ||
      element.hasAttribute( 'hidden' ) ||
      element.hasAttribute( 'aria-hidden' ) ||
      element.hasAttribute( 'aria-live' ) ||
      ( role && LIVE_REGION_ARIA_ROLES.has( role ) )
    );
  }
  ```

  ```tsx
  // /packages/components/src/modal/index.tsx:274-278 — Dialog frame with ARIA
  role={ role }
  aria-label={ contentLabel }
  aria-labelledby={ contentLabel ? undefined : headingId }
  aria-describedby={ aria.describedby }
  tabIndex={ -1 }
  ```

- **Reusable pattern:** Dialog pattern with manual background isolation (aria-hidden siblings), constrained tabbing, focus-on-mount, and focus-return. The `modalize`/`unmodalize` approach is a workaround for poor `aria-modal` support in Safari.

---

### 2. ComboboxControl

- **Complexity tier:** 1
- **ARIA pattern:** Combobox with listbox popup (W3C APG Combobox)
- **Source files:**
  - `/packages/components/src/combobox-control/index.tsx`
  - `/packages/components/src/form-token-field/token-input.tsx` (shared input)
  - `/packages/components/src/form-token-field/suggestions-list.tsx` (shared listbox)

- **Key a11y attributes:**
  - Input: `role="combobox"`, `aria-expanded={isExpanded}`, `aria-autocomplete="list"`
  - Input: `aria-owns={listboxId}` -- points to the suggestions list when expanded
  - Input: `aria-activedescendant={selectedOptionId}` -- virtual focus on currently highlighted option (only when `hasFocus && selectedSuggestionIndex !== -1 && isExpanded`)
  - Input: `aria-describedby={howtoId}` -- points to usage hint text
  - Listbox: `role="listbox"` on `<ul>`
  - Options: `role="option"`, `aria-selected={isSelected}`, `aria-disabled={isDisabled}` on `<li>` items
  - Reset button: `label={__('Reset')}`

- **Keyboard interaction model:**
  - **ArrowDown**: moves highlight to next suggestion (wraps around)
  - **ArrowUp**: moves highlight to previous suggestion (wraps around)
  - **Enter**: selects the currently highlighted suggestion
  - **Escape**: collapses the suggestion list, clears selection
  - IME events are ignored via `withIgnoreIMEEvents`

- **Focus management:**
  - Physical focus stays on the `<input>` element at all times
  - Virtual focus is communicated via `aria-activedescendant`
  - `handleOnReset` refocuses the input after clearing value: `inputContainer.current?.focus()`

- **Screen reader support:**
  - **Selection announcement**: `speak(messages.selected, 'assertive')` when an option is selected
  - **Results count**: `speak(message, 'polite')` announcing number of results or "No results." when the list expands or results change
  - Customizable messages via `messages.selected` prop

- **Infrastructure used:**
  - `speak()` from `@wordpress/a11y`
  - `useInstanceId` from `@wordpress/compose`
  - `withFocusOutside` HOC from `@wordpress/components`
  - `withIgnoreIMEEvents` utility
  - `__()`, `_n()`, `sprintf()` from `@wordpress/i18n`

- **Key code excerpts:**

  ```tsx
  // /packages/components/src/form-token-field/token-input.tsx:72-90 — Combobox ARIA
  role="combobox"
  aria-expanded={ isExpanded }
  aria-autocomplete="list"
  aria-owns={
    isExpanded
      ? `components-form-token-suggestions-${ instanceId }`
      : undefined
  }
  aria-activedescendant={
    hasFocus && selectedSuggestionIndex !== -1 && isExpanded
      ? `components-form-token-suggestions-${ instanceId }-${ selectedSuggestionIndex }`
      : undefined
  }
  aria-describedby={ `components-form-token-suggestions-howto-${ instanceId }` }
  ```

  ```tsx
  // /packages/components/src/combobox-control/index.tsx:172-184 — Selection with announcement
  const onSuggestionSelected = ( newSelectedSuggestion ) => {
    if ( newSelectedSuggestion.disabled ) { return; }
    setValue( newSelectedSuggestion.value );
    speak( messages.selected, 'assertive' );
    setSelectedSuggestion( newSelectedSuggestion );
    setInputValue( '' );
    setIsExpanded( false );
  };
  ```

  ```tsx
  // /packages/components/src/combobox-control/index.tsx:298-316 — Results announcement
  useEffect( () => {
    if ( isExpanded ) {
      const message = hasMatchingSuggestions
        ? sprintf( _n(
            '%d result found, use up and down arrow keys to navigate.',
            '%d results found, use up and down arrow keys to navigate.',
            matchingSuggestions.length
          ), matchingSuggestions.length )
        : __( 'No results.' );
      speak( message, 'polite' );
    }
  }, [ matchingSuggestions, isExpanded ] );
  ```

- **Reusable pattern:** Combobox with virtual focus (`aria-activedescendant`), shared `TokenInput` and `SuggestionsList` components. Pattern: keep physical focus on the input, use `aria-activedescendant` to indicate the active option, announce results count and selection.

---

### 3. TabPanel / Tabs

- **Complexity tier:** 1
- **ARIA pattern:** Tabs (W3C APG Tabs Pattern)
- **Source files:**
  - `/packages/components/src/tab-panel/index.tsx` (legacy wrapper)
  - `/packages/components/src/tabs/index.tsx`
  - `/packages/components/src/tabs/tablist.tsx`
  - `/packages/components/src/tabs/tab.tsx`
  - `/packages/components/src/tabs/tabpanel.tsx`

- **Key a11y attributes:**
  - Delegates to **Ariakit** for all ARIA: `Ariakit.TabList`, `Ariakit.Tab`, `Ariakit.TabPanel`
  - Tab: `aria-controls={panelId}` linking tab to its panel
  - TabPanel: `id={panelId}` matching the `aria-controls`
  - TabList: `role="tablist"` (Ariakit automatic)
  - Tab: `role="tab"`, `aria-selected` (Ariakit automatic)
  - TabPanel: `role="tabpanel"` (Ariakit automatic)
  - RTL-aware: `rtl: isRTL()` passed to `useTabStore`

- **Keyboard interaction model:**
  - Arrow keys navigate between tabs (Ariakit handles this)
  - `selectOnMove` prop controls whether selection follows focus (automatic) or requires Enter/Space (manual)
  - `orientation` prop (`'horizontal'`/`'vertical'`) controls which arrow keys navigate
  - TabList: `tabIndex={props.tabIndex ?? -1}` fallback prevents scroll-container tabbability

- **Focus management:**
  - Ariakit manages roving tabindex internally
  - On blur with `selectOnMove`: syncs `activeId` back to `selectedId` so tabbing back in focuses the selected tab
  - Selected tab auto-scrolled into view via `useScrollRectIntoView`
  - Active tab tracking with correction in `useEffect` to sync `activeId` with focused element

- **Screen reader support:**
  - Ariakit handles `aria-selected` automatically
  - Tab-panel association via matching IDs

- **Infrastructure used:**
  - `Ariakit.useTabStore`, `Ariakit.TabList`, `Ariakit.Tab`, `Ariakit.TabPanel`
  - `useInstanceId`, `usePrevious`, `useMergeRefs` from `@wordpress/compose`
  - `isRTL` from `@wordpress/i18n`

- **Key code excerpts:**

  ```tsx
  // /packages/components/src/tabs/index.tsx:61-85 — Tab store with RTL and selectOnMove
  const store = Ariakit.useTabStore( {
    selectOnMove,
    orientation,
    defaultSelectedId: externalToInternalTabId( defaultTabId, instanceId ),
    setSelectedId: ( newSelectedId ) => {
      onSelect?.( internalToExternalTabId( newSelectedId, instanceId ) );
    },
    selectedId: externalToInternalTabId( selectedTabId, instanceId ),
    rtl: isRTL(),
  } );
  ```

  ```tsx
  // /packages/components/src/tabs/tablist.tsx:113-125 — Blur handler syncs active to selected
  const onBlur = () => {
    if ( ! selectOnMove ) { return; }
    if ( selectedId !== activeId ) {
      store?.setActiveId( selectedId );
    }
  };
  ```

  ```tsx
  // /packages/components/src/tabs/tabpanel.tsx:38-51 — TabPanel with proper ID association
  <StyledTabPanel
    ref={ ref }
    store={ store }
    id={ `${ instancedTabId }-view` }
    tabId={ instancedTabId }
    focusable={ focusable }
    { ...otherProps }
  >
    { selectedId === instancedTabId && children }
  </StyledTabPanel>
  ```

- **Reusable pattern:** Delegate to Ariakit for the W3C tabs pattern. Gutenberg adds: RTL support, instance ID namespacing, disabled tab handling, automatic scroll-into-view, and active/selected sync on blur.

---

### 4. NavigableContainer / NavigableMenu / TabbableContainer

- **Complexity tier:** 1
- **ARIA pattern:** Menu navigation (arrow keys) and tabbable container (Tab key)
- **Source files:**
  - `/packages/components/src/navigable-container/container.tsx`
  - `/packages/components/src/navigable-container/menu.tsx`
  - `/packages/components/src/navigable-container/tabbable.tsx`

- **Key a11y attributes:**
  - NavigableMenu: `role="menu"` (default, configurable)
  - NavigableMenu: `aria-orientation` set to `'horizontal'` or `'vertical'` (only when role is not `'presentation'`)
  - Menu item roles checked: `['menuitem', 'menuitemradio', 'menuitemcheckbox']` -- `preventDefault` applied only to elements with these roles (to avoid interfering with VoiceOver text highlighting)

- **Keyboard interaction model:**
  - **NavigableMenu**: ArrowUp/ArrowDown (vertical), ArrowLeft/ArrowRight (horizontal), all four arrows (both)
  - **TabbableContainer**: Tab/Shift+Tab
  - `eventToOffset` callback pattern: returns `1` (forward), `-1` (backward), `0` (stop but handle), or `undefined` (ignore)
  - Cycling: wraps from last to first and vice versa (configurable via `cycle` prop)
  - `stopNavigationEvents`: calls `event.stopImmediatePropagation()` for arrow keys, `event.preventDefault()` only for menu item roles
  - Tab key: `event.preventDefault()` to avoid browser double-focusing

- **Focus management:**
  - Direct DOM focus: `focusables[nextIndex].focus()`
  - Uses `@wordpress/dom` `focus.tabbable.find()` or `focus.focusable.find()` to discover focusable children
  - `onlyBrowserTabstops` flag controls which finder is used
  - Native DOM event listeners (not React) for cross-portal support

- **Screen reader support:**
  - Role and aria-orientation communicate structure
  - VoiceOver compatibility: `preventDefault` only on elements with menu item roles

- **Infrastructure used:**
  - `focus` from `@wordpress/dom`
  - DOM event listeners (`addEventListener`/`removeEventListener`)
  - Class component with `forwardRef`

- **Key code excerpts:**

  ```tsx
  // /packages/components/src/navigable-container/container.tsx:95-159 — Core navigation logic
  onKeyDown( event: KeyboardEvent ) {
    const offset = eventToOffset( event );
    if ( offset !== undefined && stopNavigationEvents ) {
      event.stopImmediatePropagation();
      const targetRole = ( event.target as HTMLDivElement | null )?.getAttribute( 'role' );
      const targetHasMenuItemRole = !! targetRole && MENU_ITEM_ROLES.includes( targetRole );
      if ( targetHasMenuItemRole ) {
        event.preventDefault();
      }
    }
    // ... cycle through focusables
    const nextIndex = cycle
      ? cycleValue( index, focusables.length, offset )
      : index + offset;
    if ( nextIndex >= 0 && nextIndex < focusables.length ) {
      focusables[ nextIndex ].focus();
      onNavigate( nextIndex, focusables[ nextIndex ] );
    }
  }
  ```

  ```tsx
  // /packages/components/src/navigable-container/menu.tsx:55-69 — Menu with role and orientation
  <NavigableContainer
    ref={ ref }
    stopNavigationEvents
    onlyBrowserTabstops={ false }
    role={ role }
    aria-orientation={
      role !== 'presentation' &&
      ( orientation === 'vertical' || orientation === 'horizontal' )
        ? orientation
        : undefined
    }
    eventToOffset={ eventToOffset }
    { ...rest }
  />
  ```

- **Reusable pattern:** Arrow key navigation container using `focus.focusable.find()` from `@wordpress/dom`. The `eventToOffset` callback abstraction decouples key mapping from navigation logic. Uses DOM events (not React) for portal compatibility.

---

### 5. Dropdown / DropdownMenu

- **Complexity tier:** 1
- **ARIA pattern:** Disclosure (Dropdown) + Menu (DropdownMenu)
- **Source files:**
  - `/packages/components/src/dropdown/index.tsx`
  - `/packages/components/src/dropdown-menu/index.tsx`

- **Key a11y attributes:**
  - Toggle button: `aria-haspopup="true"`, `aria-expanded={isOpen}`
  - Menu: `role="menu"`, `aria-label={label}` on `NavigableMenu`
  - Menu items: `role="menuitem"` (default), `role="menuitemcheckbox"`, `role="menuitemradio"` based on `control.role`
  - `aria-checked={control.isActive}` on checkbox/radio menu items
  - `accessibleWhenDisabled` on disabled menu items (keeps them in tab order)
  - Container div: `tabIndex={-1}` to capture focus for `closeIfFocusOutside` detection

- **Keyboard interaction model:**
  - **ArrowDown on toggle**: opens the dropdown menu (unless `disableOpenOnArrowDown`)
  - Arrow navigation within the menu handled by `NavigableMenu`
  - Escape closes via Popover's built-in handler

- **Focus management:**
  - `focusOnMount` passed to Popover controls initial focus within the dropdown content
  - `closeIfFocusOutside()`: checks if focus has left both the container and any open dialogs
  - Container's `tabIndex={-1}` ensures it can receive focus for detection purposes

- **Screen reader support:**
  - `aria-haspopup` + `aria-expanded` announce the menu trigger state
  - `label` prop provides accessible name for icon-only toggle buttons

- **Infrastructure used:**
  - `Popover` component (which uses `useDialog` hook)
  - `NavigableMenu` for arrow key navigation within the menu
  - `contextConnect` / `useContextSystem` for prop forwarding

- **Key code excerpts:**

  ```tsx
  // /packages/components/src/dropdown-menu/index.tsx:97-148 — Toggle with ARIA
  <Toggle
    { ...mergedToggleProps }
    icon={ icon }
    onClick={ (event) => { onToggle(); /* ... */ } }
    onKeyDown={ (event) => { openOnArrowDown( event ); /* ... */ } }
    aria-haspopup="true"
    aria-expanded={ isOpen }
    label={ label }
    text={ text }
    showTooltip={ toggleProps?.showTooltip ?? true }
  >
  ```

  ```tsx
  // /packages/components/src/dropdown-menu/index.tsx:162-212 — Menu with roles
  <NavigableMenu { ...mergedMenuProps } role="menu">
    { /* ... */ }
    <Button
      role={ control.role === 'menuitemcheckbox' || control.role === 'menuitemradio'
        ? control.role : 'menuitem' }
      aria-checked={ control.role === 'menuitemcheckbox' || control.role === 'menuitemradio'
        ? control.isActive : undefined }
      accessibleWhenDisabled
      disabled={ control.isDisabled }
    >
  ```

- **Reusable pattern:** Disclosure pattern (Dropdown) with focus-outside detection; Menu pattern (DropdownMenu) composing Dropdown + NavigableMenu. The pattern of `aria-haspopup` + `aria-expanded` on the toggle and `role="menu"` on the content with menu item roles is standard.

---

### 6. FormTokenField

- **Complexity tier:** 1
- **ARIA pattern:** Combobox with multi-select tokens
- **Source files:**
  - `/packages/components/src/form-token-field/index.tsx`
  - `/packages/components/src/form-token-field/token-input.tsx`
  - `/packages/components/src/form-token-field/token.tsx`
  - `/packages/components/src/form-token-field/suggestions-list.tsx`

- **Key a11y attributes:**
  - Shares `TokenInput` with `ComboboxControl` (same `role="combobox"`, `aria-expanded`, `aria-autocomplete="list"`, `aria-owns`, `aria-activedescendant`, `aria-describedby`)
  - Suggestions list: `role="listbox"`, options: `role="option"`, `aria-selected`
  - Token: `VisuallyHidden` span with position info: `"%1$s (%2$d of %3$d)"`; visual text hidden with `aria-hidden="true"`
  - Token remove button: `aria-describedby` pointing to the token text span

- **Keyboard interaction model:**
  - **Enter**: adds current token or selected suggestion
  - **Backspace**: deletes token before input cursor (when input is empty)
  - **Delete**: deletes token after input cursor (when input is empty)
  - **ArrowLeft/ArrowRight**: moves the input cursor between tokens (when input is empty)
  - **ArrowUp/ArrowDown**: navigates suggestions (with wrapping)
  - **Escape**: collapses suggestions list
  - **Tab**: collapses suggestions list (does NOT prevent default)
  - **Comma**: tokenizes current input
  - **Space**: tokenizes if `tokenizeOnSpace` is enabled
  - IME events ignored via `withIgnoreIMEEvents`

- **Focus management:**
  - Input position is movable among tokens via `inputOffsetFromEnd` state
  - `isActive` state triggers `focus()` via `useEffect`

- **Screen reader support:**
  - `speak(messages.added, 'assertive')` when a token is added
  - `speak(messages.removed, 'assertive')` when a token is removed
  - `speak(messages.__experimentalInvalid, 'assertive')` for invalid input
  - `debouncedSpeak(message, 'assertive')` with 500ms debounce for suggestion count announcements
  - Howto text: `aria-describedby` linking to "Separate with commas or the Enter key."
  - Token position announcement: "TokenName (2 of 5)"

- **Infrastructure used:**
  - `speak` from `@wordpress/a11y`
  - `useDebounce`, `useInstanceId`, `usePrevious` from `@wordpress/compose`
  - `withIgnoreIMEEvents` utility
  - `VisuallyHidden` component

- **Key code excerpts:**

  ```tsx
  // /packages/components/src/form-token-field/token.tsx:48-71 — Token with position announcement
  const termPositionAndCount = sprintf(
    __( '%1$s (%2$d of %3$d)' ),
    transformedValue, termPosition, termsCount
  );
  return (
    <span className={ tokenClasses }>
      <span className="components-form-token-field__token-text" id={ tokenTextId }>
        <VisuallyHidden as="span">{ termPositionAndCount }</VisuallyHidden>
        <span aria-hidden="true">{ transformedValue }</span>
      </span>
      <Button
        className="components-form-token-field__remove-token"
        icon={ closeSmall }
        label={ messages.remove }
        aria-describedby={ tokenTextId }
      />
    </span>
  );
  ```

  ```tsx
  // /packages/components/src/form-token-field/index.tsx:460-476 — Token add with announcement
  function addNewToken( token: string ) {
    if ( ! __experimentalValidateInput( token ) ) {
      speak( messages.__experimentalInvalid, 'assertive' );
      return;
    }
    addNewTokens( [ token ] );
    speak( messages.added, 'assertive' );
    // ...
  }
  ```

- **Reusable pattern:** Multi-token input with combobox, movable input cursor among tokens, position-aware screen reader announcements, and debounced suggestion count announcements.

---

### 7. CustomSelectControl / CustomSelectControlV2

- **Complexity tier:** 1
- **ARIA pattern:** Select (W3C APG Select-Only Combobox / Listbox)
- **Source files:**
  - `/packages/components/src/custom-select-control/index.tsx`
  - `/packages/components/src/custom-select-control-v2/custom-select.tsx`
  - `/packages/components/src/custom-select-control-v2/index.tsx`
  - `/packages/components/src/custom-select-control-v2/item.tsx`

- **Key a11y attributes:**
  - `Ariakit.Select` button -- Ariakit provides `role="combobox"` or `role="listbox"` trigger
  - `Ariakit.SelectLabel` -- renders as `VisuallyHidden` or visual label
  - `Ariakit.SelectPopover` -- the dropdown with `role="listbox"` (Ariakit automatic)
  - `Ariakit.SelectItem` -- `role="option"` with `aria-selected` (Ariakit automatic)
  - `aria-describedby={descriptionId}` on the select button
  - VisuallyHidden description: `sprintf(__('Currently selected: %s'), currentName)`
  - Check icon in selected items for visual indication

- **Keyboard interaction model:**
  - Ariakit handles: arrow keys to navigate, Enter/Space to select, Escape to close
  - `showOnKeyDown={!isLegacy}` -- legacy mode moves selection on arrow keys without opening popover
  - `flip={!isLegacy}` -- legacy mode prevents popover flipping
  - `onKeyDown` on popover: `e.stopPropagation()` in legacy mode to prevent bubbling

- **Focus management:**
  - Ariakit manages focus internally
  - Select button receives focus; popover items use virtual focus or direct focus depending on Ariakit version

- **Screen reader support:**
  - Ariakit announces selected value changes automatically
  - VisuallyHidden description provides "Currently selected: X" context

- **Infrastructure used:**
  - `Ariakit.useSelectStore`, `Ariakit.Select`, `Ariakit.SelectLabel`, `Ariakit.SelectPopover`, `Ariakit.SelectItem`
  - `useInstanceId` from `@wordpress/compose`
  - `VisuallyHidden` component

- **Key code excerpts:**

  ```tsx
  // /packages/components/src/custom-select-control/index.tsx:190-213 — Select with description
  <_CustomSelect
    aria-describedby={ descriptionId }
    renderSelectedValue={ renderSelectedValue }
    size={ translatedSize }
    store={ store }
    isLegacy
    { ...restProps }
  >
    { children }
  </_CustomSelect>
  <VisuallyHidden>
    <span id={ descriptionId }>
      { getDescribedBy( selectedOption?.name, describedBy ) }
    </span>
  </VisuallyHidden>
  ```

  ```tsx
  // /packages/components/src/custom-select-control-v2/custom-select.tsx:120-133 — Label rendering
  <Ariakit.SelectLabel
    store={ store }
    render={
      hideLabelFromVision ? (
        <VisuallyHidden />
      ) : (
        <BaseControl.VisualLabel as="div" />
      )
    }
  >
    { label }
  </Ariakit.SelectLabel>
  ```

- **Reusable pattern:** Ariakit-based select with VisuallyHidden description for current selection, configurable label visibility, and legacy/modern mode distinction.

---

### 8. DateTimePicker / DatePicker

- **Complexity tier:** 1
- **ARIA pattern:** Calendar grid with roving tabindex (W3C APG Date Picker Dialog)
- **Source files:**
  - `/packages/components/src/date-time/date/index.tsx`
  - `/packages/components/src/date-time/time/index.tsx`
  - `/packages/components/src/date-time/date-time/index.tsx`

- **Key a11y attributes:**
  - Wrapper: `role="application"`, `aria-label={__('Calendar')}`
  - Navigation buttons: `aria-label={__('View previous month')}`, `aria-label={__('View next month')}`
  - Day buttons: `aria-label` with rich descriptive text including date, "Selected", "Today", and event count
  - Day buttons: `tabIndex={isFocusable ? 0 : -1}` for roving tabindex
  - Day buttons: `disabled={isInvalid}` for invalid dates
  - TimePicker fieldsets: `<legend>` for "Time" and "Date" groups
  - `VisuallyHidden as="legend"` when `hideLabelFromVision` is true

- **Keyboard interaction model:**
  - **ArrowLeft/ArrowRight**: move focus to previous/next day (RTL-aware)
  - **ArrowUp/ArrowDown**: move focus to same day in previous/next week
  - **PageUp/PageDown**: move to same day in previous/next month
  - **Home**: move to start of the week
  - **End**: move to end of the week
  - All arrow/page keys call `event.preventDefault()` and update `focusable` state
  - Month navigation: auto-updates viewing month when focus moves across month boundaries

- **Focus management:**
  - **Roving tabindex**: only one day has `tabIndex={0}`, all others have `tabIndex={-1}`
  - `useEffect` on the Day component: `ref.current.focus()` when `isFocusable && isFocusAllowed`
  - `isFocusAllowed` prevents stealing focus from TimePicker inputs
  - `isFocusWithinCalendar` tracked via `onFocus`/`onBlur` on the Calendar container

- **Screen reader support:**
  - Day label construction: `"February 27, 2026. Selected. Today. There are 2 events"` -- parts joined with `. `
  - `role="application"` communicates that the widget handles its own keyboard interaction

- **Infrastructure used:**
  - `useState`, `useRef`, `useEffect` from `@wordpress/element`
  - `isRTL` from `@wordpress/i18n`
  - `dateI18n` from `@wordpress/date`
  - Standard HTML `<fieldset>`/`<legend>` for time picker grouping
  - `VisuallyHidden` for hidden legends

- **Key code excerpts:**

  ```tsx
  // /packages/components/src/date-time/date/index.tsx:217-282 — Full keyboard navigation
  onKeyDown={ ( event ) => {
    let nextFocusable;
    if ( event.key === 'ArrowLeft' ) { nextFocusable = addDays( day, isRTL() ? 1 : -1 ); }
    if ( event.key === 'ArrowRight' ) { nextFocusable = addDays( day, isRTL() ? -1 : 1 ); }
    if ( event.key === 'ArrowUp' ) { nextFocusable = subWeeks( day, 1 ); }
    if ( event.key === 'ArrowDown' ) { nextFocusable = addWeeks( day, 1 ); }
    if ( event.key === 'PageUp' ) { nextFocusable = subMonths( day, 1 ); }
    if ( event.key === 'PageDown' ) { nextFocusable = addMonths( day, 1 ); }
    if ( event.key === 'Home' ) { /* move to week start */ }
    if ( event.key === 'End' ) { /* move to week end */ }
    if ( nextFocusable ) {
      event.preventDefault();
      setFocusable( nextFocusable );
      if ( ! isSameMonth( nextFocusable, viewing ) ) {
        setViewing( nextFocusable );
      }
    }
  } }
  ```

  ```tsx
  // /packages/components/src/date-time/date/index.tsx:350-383 — Day label construction
  function getDayLabel( date, isSelected, isToday, numEvents ) {
    const parts = [ localizedDate ];
    if ( isSelected ) { parts.push( __( 'Selected' ) ); }
    if ( isToday ) { parts.push( __( 'Today' ) ); }
    if ( numEvents > 0 ) {
      parts.push( sprintf( _n( 'There is %d event', 'There are %d events', numEvents ), numEvents ) );
    }
    return parts.join( '. ' );
  }
  ```

  ```tsx
  // /packages/components/src/date-time/date/index.tsx:336 — Roving tabindex on day button
  tabIndex={ isFocusable ? 0 : -1 }
  aria-label={ getDayLabel( day, isSelected, isToday, numEvents ) }
  ```

- **Reusable pattern:** Calendar grid with roving tabindex. The `isFocusAllowed` guard prevents focus theft from sibling inputs. Day labels constructed as joined string parts. Full APG date picker keyboard model including PageUp/PageDown, Home/End.

---

### 9. ColorPicker / ColorPalette

- **Complexity tier:** 1
- **ARIA pattern:** Specialized color input + listbox-based color swatch picker
- **Source files:**
  - `/packages/components/src/color-picker/component.tsx`
  - `/packages/components/src/color-palette/index.tsx`
  - `/packages/components/src/circular-option-picker/circular-option-picker.tsx`
  - `/packages/components/src/circular-option-picker/circular-option-picker-option.tsx`

- **Key a11y attributes:**
  - Color format select: `label={__('Color format')}`, `hideLabelFromVision`
  - ColorPalette custom color button: `aria-expanded={isOpen}`, `aria-haspopup="true"`, `aria-label` with full description
  - CircularOptionPicker (listbox mode): `Composite` with `role="listbox"`
  - Options (listbox mode): `role="option"`, `aria-selected={isSelected}`
  - Options (button mode): `aria-pressed={isSelected}` on buttons
  - `tooltipText` provides accessible label for color swatches
  - `aria-labelledby` linking palette groups to their headings

- **Screen reader support:**
  - Custom color button label: `"Custom color picker. The currently selected color is called 'vivid red' and has a value of '#f00'."`
  - Color swatch tooltip: color name or `sprintf(__('Color code: %s'), color)` as fallback

- **Key code excerpts:**

  ```tsx
  // /packages/components/src/color-palette/index.tsx:229-238 — Custom color accessible label
  const customColorAccessibleLabel = !! displayValue
    ? sprintf(
        __( 'Custom color picker. The currently selected color is called "%1$s" and has a value of "%2$s".' ),
        buttonLabelName,
        displayValue
      )
    : __( 'Custom color picker' );
  ```

  ```tsx
  // /packages/components/src/circular-option-picker/circular-option-picker-option.tsx:67-81 — Listbox option
  <Composite.Item
    render={
      <Button __next40pxDefaultSize
        { ...additionalProps }
        role="option"
        aria-selected={ !! isSelected }
        ref={ forwardedRef }
        label={ label }
      />
    }
    id={ id }
  />
  ```

- **Reusable pattern:** Dual-mode component (listbox vs. buttons) with `Composite` for arrow-key navigation in listbox mode. Color swatches use `tooltipText` as the accessible label.

---

### 10. TreeSelect

- **Complexity tier:** 1
- **ARIA pattern:** Native `<select>` with visual indentation
- **Source files:**
  - `/packages/components/src/tree-select/index.tsx`

- **Key a11y attributes:**
  - Wraps `SelectControl` which renders a native `<select>` element
  - Hierarchy conveyed visually via `\u00A0` (non-breaking space) indentation in option labels
  - Inherits all ARIA from `SelectControl` (native semantics)

- **Screen reader support:**
  - Non-breaking spaces in labels provide some level nesting context in screen readers
  - Native `<select>` semantics handle selection announcements

- **Reusable pattern:** For tree structures where full ARIA tree pattern is overkill, visual indentation in a native select is a simple approach. Note: this does NOT convey hierarchical structure to screen readers.

---

## Tier 2: Medium Complexity Reviews

### 11. Button

- **Complexity tier:** 2
- **ARIA pattern:** Button / Toggle button / Link
- **Source file:** `/packages/components/src/button/index.tsx`

- **Key a11y attributes:**
  - `aria-label={label}` for icon-only buttons
  - `aria-pressed` for toggle buttons (`true`, `'true'`, `'mixed'`)
  - `aria-checked`, `aria-selected` forwarded when present
  - `aria-describedby={descriptionId}` linking to a VisuallyHidden description span
  - `aria-disabled="true"` (NOT `disabled`) when `accessibleWhenDisabled` is true -- keeps button in tab order
  - `type="button"` explicitly set (prevents accidental form submission)

- **Disabled state pattern:**
  - `disabled && !accessibleWhenDisabled`: native `disabled` attribute (removed from tab order)
  - `disabled && accessibleWhenDisabled`: `aria-disabled="true"` + click/mousedown handlers that call `stopPropagation()` and `preventDefault()`

- **Key code excerpts:**

  ```tsx
  // /packages/components/src/button/index.tsx:200-213 — accessibleWhenDisabled pattern
  if ( disabled && accessibleWhenDisabled ) {
    buttonProps[ 'aria-disabled' ] = true;
    anchorProps[ 'aria-disabled' ] = true;
    for ( const disabledEvent of disabledEventsOnDisabledButton ) {
      disableEventProps[ disabledEvent ] = ( event ) => {
        if ( event ) {
          event.stopPropagation();
          event.preventDefault();
        }
      };
    }
  }
  ```

  ```tsx
  // /packages/components/src/button/index.tsx:294-302 — Description via VisuallyHidden
  <Tooltip { ...tooltipProps }>{ element }</Tooltip>
  { description && (
    <VisuallyHidden>
      <span id={ descriptionId }>{ description }</span>
    </VisuallyHidden>
  ) }
  ```

- **Reusable pattern:** `accessibleWhenDisabled` -- use `aria-disabled="true"` instead of native `disabled` to keep the element perceivable by screen readers and in tab order while preventing interaction.

---

### 12. CheckboxControl / RadioControl / ToggleControl

- **Complexity tier:** 2
- **Source files:**
  - `/packages/components/src/checkbox-control/index.tsx`
  - `/packages/components/src/radio-control/index.tsx`
  - `/packages/components/src/toggle-control/index.tsx`
  - `/packages/components/src/form-toggle/index.tsx`

- **Key a11y attributes:**
  - All use native `<input>` elements (`type="checkbox"` or `type="radio"`)
  - `aria-describedby={id + '__help'}` linking to help text
  - CheckboxControl: `indeterminate` property set via DOM API (not HTML attribute)
  - CheckboxControl icons: `role="presentation"` on visual check/indeterminate icons
  - RadioControl: `<fieldset>` + `<legend>` for grouping; option descriptions via `aria-describedby`
  - RadioControl: `hideLabelFromVision` renders legend in `VisuallyHidden`
  - ToggleControl: wraps `FormToggle` (which is a styled checkbox `type="checkbox"`)
  - Safari compat: `onClick` handler calls `event.currentTarget.focus()` to ensure focus

- **Key code excerpts:**

  ```tsx
  // /packages/components/src/radio-control/index.tsx:88-99 — Fieldset with legend
  <fieldset id={ id } className={ clsx( className, 'components-radio-control' ) }
    aria-describedby={ !! help ? generateHelpId( id ) : undefined }
  >
    { hideLabelFromVision ? (
      <VisuallyHidden as="legend">{ label }</VisuallyHidden>
    ) : (
      <BaseControl.VisualLabel as="legend">{ label }</BaseControl.VisualLabel>
    ) }
  ```

  ```tsx
  // /packages/components/src/checkbox-control/index.tsx:74-87 — Indeterminate via DOM
  const ref = useRefEffect< HTMLInputElement >( ( node ) => {
    if ( ! node ) { return; }
    node.indeterminate = !! indeterminate;
    setShowCheckedIcon( node.matches( ':checked' ) );
    setShowIndeterminateIcon( node.matches( ':indeterminate' ) );
  }, [ checked, indeterminate ] );
  ```

- **Reusable pattern:** Native form controls with `<label>` association, `<fieldset>`/`<legend>` grouping for radio groups, `aria-describedby` for help text, and `VisuallyHidden` for screen-reader-only labels.

---

### 13. TextControl / TextareaControl

- **Complexity tier:** 2
- **Source file:** `/packages/components/src/text-control/index.tsx`

- **Key a11y attributes:**
  - `<label>` associated via `htmlFor={id}` (through BaseControl)
  - `aria-describedby={id + '__help'}` when help text is provided
  - `hideLabelFromVision` renders the label as VisuallyHidden via BaseControl
  - `type` defaults to `"text"` but is configurable

- **Reusable pattern:** Standard `BaseControl` wrapper providing label + help text association.

---

### 14. RangeControl

- **Complexity tier:** 2
- **Source files:**
  - `/packages/components/src/range-control/index.tsx`
  - `/packages/components/src/range-control/input-range.tsx`

- **Key a11y attributes:**
  - Native `<input type="range">` element
  - `aria-label={label}` on the range input
  - `aria-describedby={describedBy}` pointing to help text
  - `aria-hidden={false}` explicitly on the range input
  - `tabIndex={0}` explicitly on the range input
  - Companion `InputNumber`: `aria-label={label}` for the numeric input field
  - Visual-only elements (Track, Thumb, RangeRail): `aria-hidden` on decorative elements
  - Reset button: `accessibleWhenDisabled={!disabled}` -- not in tab sequence when RangeControl is disabled

- **Key code excerpts:**

  ```tsx
  // /packages/components/src/range-control/input-range.tsx:20-31 — Range input ARIA
  <BaseInputRange
    { ...otherProps }
    aria-describedby={ describedBy }
    aria-label={ label }
    aria-hidden={ false }
    ref={ ref }
    tabIndex={ 0 }
    type="range"
    value={ value }
  />
  ```

- **Reusable pattern:** Native range input with explicit `aria-label`, paired with a numeric input for precise value entry. Decorative visual elements marked `aria-hidden`.

---

### 15. Notice / Snackbar

- **Complexity tier:** 2
- **Source files:**
  - `/packages/components/src/notice/index.tsx`
  - `/packages/components/src/snackbar/index.tsx`

- **Key a11y attributes:**
  - Notice: `VisuallyHidden` status label ("Warning notice", "Error notice", "Information notice", "Notice")
  - Snackbar (non-explicit dismiss): `role="button"`, `tabIndex={0}`, `aria-label={__('Dismiss this notice')}`
  - Snackbar (explicit dismiss): dismiss button with `role="button"`, `aria-label={__('Dismiss this notice')}`, `tabIndex={0}`

- **Screen reader support:**
  - Both use `speak()` from `@wordpress/a11y` to announce messages
  - Notice politeness mapping: `error` -> `'assertive'`; `success`/`warning`/`info` -> `'polite'`
  - Snackbar default politeness: `'polite'`
  - `spokenMessage` prop allows customizing what is spoken (defaults to `children`)
  - Snackbar auto-dismisses after 6 seconds (`NOTICE_TIMEOUT = 6000`)

- **Key code excerpts:**

  ```tsx
  // /packages/components/src/notice/index.tsx:28-40 — useSpokenMessage hook
  function useSpokenMessage( message, politeness ) {
    const spokenMessage = typeof message === 'string' ? message : renderToString( message );
    useEffect( () => {
      if ( spokenMessage ) { speak( spokenMessage, politeness ); }
    }, [ spokenMessage, politeness ] );
  }
  ```

  ```tsx
  // /packages/components/src/notice/index.tsx:42-52 — Politeness mapping
  function getDefaultPoliteness( status ) {
    switch ( status ) {
      case 'success': case 'warning': case 'info': return 'polite';
      default: return 'assertive';  // 'error'
    }
  }
  ```

  ```tsx
  // /packages/components/src/notice/index.tsx:111 — VisuallyHidden status
  <VisuallyHidden>{ getStatusLabel( status ) }</VisuallyHidden>
  ```

- **Reusable pattern:** `speak()` for live region announcements with politeness mapped to severity. VisuallyHidden status labels for persistent notices. Auto-dismiss with timeout for snackbars.

---

### 16. Tooltip

- **Complexity tier:** 2
- **Source file:** `/packages/components/src/tooltip/index.tsx`

- **Key a11y attributes:**
  - Uses `Ariakit.Tooltip`, `Ariakit.TooltipAnchor`
  - `aria-describedby={describedById}` manually added to the anchor element (Ariakit 0.4+ no longer does this automatically)
  - Only adds `aria-describedby` when: (a) tooltip is mounted, (b) anchor doesn't already have `aria-describedby`, and (c) tooltip text differs from anchor's `aria-label`
  - Tooltip content has `role="tooltip"` (Ariakit automatic)
  - Nested tooltip prevention via `TooltipInternalContext`

- **Key code excerpts:**

  ```tsx
  // /packages/components/src/tooltip/index.tsx:96-103 — Manual aria-describedby
  function addDescribedById( element ) {
    return describedById &&
      mounted &&
      element.props[ 'aria-describedby' ] === undefined &&
      element.props[ 'aria-label' ] !== text
      ? cloneElement( element, { 'aria-describedby': describedById } )
      : element;
  }
  ```

- **Reusable pattern:** Tooltip uses `aria-describedby` to associate tooltip text with the anchor, with smart deduplication (skips if `aria-label` equals tooltip text). Nested tooltips are suppressed via context.

---

### 17. Popover

- **Complexity tier:** 2
- **Source file:** `/packages/components/src/popover/index.tsx`

- **Key a11y attributes:**
  - Uses `useDialog` hook from `@wordpress/compose` which provides:
    - Constrained tabbing (optional, defaults to `focusOnMount !== false`)
    - Focus on mount
    - Focus return on unmount
    - Focus outside detection (close on blur)
    - Escape key to close
  - `tabIndex={-1}` on the popover container
  - Arrow SVG: `role="presentation"`

- **Reusable pattern:** `useDialog` hook is the foundational dialog behavior hook -- provides constrained tabbing, focus management, escape-to-close, and focus-outside in one composable hook.

---

### 18. SearchControl

- **Complexity tier:** 2
- **Source file:** `/packages/components/src/search-control/index.tsx`

- **Key a11y attributes:**
  - `type="search"` on the input
  - `label` defaults to `__('Search')`
  - `hideLabelFromVision` defaults to `true` (label is visually hidden but available to screen readers)
  - `autoComplete="off"` to prevent browser autocomplete interference
  - Reset button: `label={__('Reset search')}` or `label={__('Close search')}`

- **Reusable pattern:** Search input with always-present accessible label (visually hidden by default), search icon prefix, and contextual reset/close button.

---

### 19. ToggleGroupControl

- **Complexity tier:** 2
- **Source files:**
  - `/packages/components/src/toggle-group-control/toggle-group-control/component.tsx`
  - `/packages/components/src/toggle-group-control/toggle-group-control/as-radio-group.tsx`
  - `/packages/components/src/toggle-group-control/toggle-group-control/as-button-group.tsx`

- **Key a11y attributes:**
  - Non-deselectable mode: `Ariakit.RadioGroup` with `aria-label={label}`
  - Deselectable mode: `role="group"` with `aria-label={label}`
  - Options: Ariakit `Radio` (non-deselectable) or `Button` with `aria-pressed` (deselectable)
  - RTL-aware via `rtl: isRTL()`

- **Reusable pattern:** Dual-mode segmented control: radio group when single selection required, button group when deselectable. Ariakit handles arrow key navigation in radio mode.

---

### 20. Guide

- **Complexity tier:** 2
- **Source file:** `/packages/components/src/guide/index.tsx`

- **Key a11y attributes:**
  - Wraps `Modal` component (inherits all dialog a11y)
  - `contentLabel` prop passed to Modal
  - Page navigation focus management: `useEffect` focuses the `.components-guide` frame on page change

- **Keyboard interaction model:**
  - **ArrowLeft**: go to previous page (with `event.preventDefault()` to prevent scroll)
  - **ArrowRight**: go to next page (with `event.preventDefault()` to prevent scroll)

- **Reusable pattern:** Multi-step wizard built on Modal with arrow key page navigation.

---

## Tier 3: Simple Components

### 21. VisuallyHidden

- **Complexity tier:** 3
- **Source file:** `/packages/components/src/visually-hidden/component.tsx`
- **Styles file:** `/packages/components/src/visually-hidden/styles.ts`

- **Purpose:** Renders content that is visually hidden but accessible to screen readers.

- **CSS technique:**
  ```ts
  // /packages/components/src/visually-hidden/styles.ts:6-18
  export const visuallyHidden: CSSProperties = {
    border: 0,
    clip: 'rect(1px, 1px, 1px, 1px)',
    WebkitClipPath: 'inset( 50% )',
    clipPath: 'inset( 50% )',
    height: '1px',
    margin: '-1px',
    overflow: 'hidden',
    padding: 0,
    position: 'absolute',
    width: '1px',
    wordWrap: 'normal',
  };
  ```

- **Polymorphic:** Supports `as` prop via `View` component -- can render as any element type.
- **Usage across codebase:** Used extensively for labels, descriptions, status text, token position announcements.

---

### 22. Icon

- **Complexity tier:** 3
- **Source file:** `/packages/components/src/icon/index.tsx`

- **A11y notes:** Icon component itself does NOT add any ARIA attributes. It renders the icon as-is. Accessibility is the caller's responsibility:
  - Decorative icons: parent should add `aria-hidden="true"` (e.g., Modal's icon container)
  - Meaningful icons: parent should provide `aria-label` on the containing element
  - Presentation icons: callers use `role="presentation"` (e.g., CheckboxControl, RangeRail)

---

### 23. Spinner

- **Complexity tier:** 3
- **Source file:** `/packages/components/src/spinner/index.tsx`

- **Key a11y attributes:**
  - `role="presentation"` -- explicitly marks as decorative
  - `focusable="false"` -- prevents SVG from receiving focus in IE/Edge

- **Reusable pattern:** Loading indicators should be `role="presentation"` when they are purely visual feedback. The actual loading state announcement should be done via `speak()` or a live region elsewhere.

---

### 24. Disabled

- **Complexity tier:** 3
- **Source file:** `/packages/components/src/disabled/index.tsx`

- **Key a11y attributes:**
  - Uses the `inert` HTML attribute: `inert={isDisabled ? 'true' : undefined}`
  - `inert` removes all descendant elements from the accessibility tree and prevents focus/interaction
  - Context provider exposes disabled state to children

- **Reusable pattern:** `inert` attribute is the modern, standards-based approach to disabling an entire subtree. It replaces manual tabIndex manipulation and aria-disabled on each element.

---

### 25. FocalPointPicker

- **Complexity tier:** 3
- **Source file:** `/packages/components/src/focal-point-picker/index.tsx`

- **Key a11y attributes:**
  - Drag area is focusable via `dragAreaRef.current?.focus()` on drag start
  - Standard label and help text via BaseControl pattern

- **Keyboard interaction model:**
  - **Arrow keys**: move focal point by 1% (0.01) per press
  - **Shift + Arrow keys**: move focal point by 10% (0.1) per press
  - ArrowUp/ArrowLeft: decrease; ArrowDown/ArrowRight: increase
  - `event.preventDefault()` on arrow keys

- **Key code excerpts:**

  ```tsx
  // /packages/components/src/focal-point-picker/index.tsx:215-229 — Arrow key stepping
  const arrowKeyStep = ( event ) => {
    const { code, shiftKey } = event;
    if ( ! [ 'ArrowUp', 'ArrowDown', 'ArrowLeft', 'ArrowRight' ].includes( code ) ) { return; }
    event.preventDefault();
    const step = shiftKey ? 0.1 : 0.01;
    const delta = code === 'ArrowUp' || code === 'ArrowLeft' ? -1 * step : step;
    // ... apply delta to x or y
  };
  ```

- **Reusable pattern:** Drag interactions need keyboard equivalents. Arrow keys as small steps, Shift+Arrow as large steps.

---

### 26. Draggable

- **Complexity tier:** 3
- **Source file:** `/packages/components/src/draggable/index.tsx`

- **A11y notes:** The Draggable component is a render-prop that provides drag handlers (`onDraggableStart`, `onDraggableEnd`). It does NOT provide any accessibility features itself. The caller is responsible for:
  - Setting `draggable="true"` on the handle element
  - Providing keyboard alternatives for the drag operation
  - Communicating drag state to screen readers

---

## Pattern Catalog

### By Concern

#### Keyboard Interaction Models

1. **Arrow key navigation (focus movement):**
   - NavigableMenu: ArrowUp/Down (vertical), ArrowLeft/Right (horizontal)
   - DatePicker: Arrow keys for day navigation, PageUp/Down for months, Home/End for week boundaries
   - FocalPointPicker: Arrow keys move focal point position
   - Guide: ArrowLeft/Right for page navigation
   - Tabs: Arrow keys navigate between tabs (via Ariakit)
   - ToggleGroupControl (radio mode): Arrow keys via Ariakit RadioGroup
   - CircularOptionPicker (listbox mode): Arrow keys via Composite

2. **Arrow key navigation (virtual focus / active descendant):**
   - ComboboxControl: ArrowUp/Down highlight suggestions, `aria-activedescendant` tracks highlight
   - FormTokenField: ArrowUp/Down for suggestions; ArrowLeft/Right to move input cursor among tokens

3. **Tab-based navigation:**
   - TabbableContainer: Tab/Shift+Tab between tabbable children
   - Modal: constrained tabbing via `useConstrainedTabbing()`
   - Popover: constrained tabbing via `useDialog()`

4. **Escape to close/dismiss:**
   - Modal: `handleEscapeKeyDown` with IME ignore
   - Popover: via `useDialog` -> `closeOnEscapeRef`
   - ComboboxControl: collapses suggestion list
   - FormTokenField: collapses suggestion list
   - CustomSelectControl: Ariakit handles Escape

5. **Enter/Space to activate:**
   - ComboboxControl: Enter selects highlighted suggestion
   - FormTokenField: Enter adds current token
   - Tabs: Enter/Space selects tab (when `selectOnMove=false`)
   - CustomSelectControl: Ariakit handles Enter/Space

6. **Shift modifier:**
   - FocalPointPicker: Shift+Arrow for 10% jumps (vs 1%)
   - FormTokenField: Shift+Step via `isShiftStepEnabled` on RangeControl companion
   - TabbableContainer: Shift+Tab reverses direction

#### ARIA Relationship Patterns

1. **`aria-labelledby`:**
   - Modal: `aria-labelledby={headingId}` pointing to `<h1>` title (when no `contentLabel`)
   - ColorPalette/MultiplePalettes: `aria-labelledby={id}` on palette groups pointing to heading
   - Mutual exclusion: `aria-label` and `aria-labelledby` never both present on Modal

2. **`aria-describedby`:**
   - Modal: `aria-describedby={aria.describedby}` (configurable)
   - TextControl/CheckboxControl/ToggleControl/RangeControl: `aria-describedby={id + '__help'}` for help text
   - RadioControl: `aria-describedby` on fieldset for group help; on individual radios for option descriptions
   - Button: `aria-describedby={descriptionId}` pointing to VisuallyHidden description
   - Tooltip: `aria-describedby={describedById}` added to anchor element
   - TokenInput: `aria-describedby` pointing to "how to" usage hint
   - CustomSelectControl: `aria-describedby` pointing to "Currently selected: X" text
   - Token remove button: `aria-describedby` pointing to token text

3. **`aria-owns`:**
   - TokenInput: `aria-owns={suggestionsListId}` when expanded (connects combobox to listbox)

4. **`aria-activedescendant`:**
   - TokenInput: `aria-activedescendant={selectedOptionId}` when focused, expanded, and an option is highlighted

5. **`aria-controls`:**
   - TabPanel (legacy): `aria-controls={panelId}` on each tab

#### Focus Management Strategies

1. **Focus trapping (constrained tabbing):**
   - **Components:** Modal, Popover (optional)
   - **Implementation:** `useConstrainedTabbing()` hook -- creates temporary `tabIndex=-1` trap divs at container boundaries
   - **Mechanism:** On Tab at the end of container, prepends a trap div; on Shift+Tab at start, appends a trap div. Trap removes itself on blur.

2. **Focus return/restoration:**
   - **Components:** Modal, Popover (when `focusOnMount !== false`)
   - **Implementation:** `useFocusReturn()` hook -- records `document.activeElement` on mount, restores focus on unmount

3. **Focus on mount:**
   - **Components:** Modal, Popover
   - **Implementation:** `useFocusOnMount(mode)` -- modes: `'firstElement'`, `'firstInputElement'`, `true` (focus container), `false` (no focus)
   - **Special:** Modal adds `'firstContentElement'` mode that applies focus to the content area instead of the frame

4. **Roving tabindex:**
   - **Components:** DatePicker, CircularOptionPicker (listbox mode), Tabs (via Ariakit)
   - **Implementation (DatePicker):** Only the focused day has `tabIndex={0}`, all others have `tabIndex={-1}`. `useEffect` calls `.focus()` when `isFocusable` changes.
   - **Guard pattern:** `isFocusAllowed` prevents focus theft when focus is in a sibling component

5. **Virtual/active descendant focus:**
   - **Components:** ComboboxControl, FormTokenField
   - **Implementation:** Physical focus stays on `<input>`, `aria-activedescendant` points to the ID of the visually highlighted `<li>` option
   - **Condition:** Only set when input has focus AND suggestions are expanded AND an option is selected

6. **Focus on interaction:**
   - **Components:** FocalPointPicker (focus drag area on drag start), FormTokenField (focus input when active), Guide (focus modal frame on page change)
   - **Safari compat:** CheckboxControl, RadioControl, FormToggle all call `event.currentTarget.focus()` in `onClick` handler

#### Live Region Usage

1. **Assertive announcements (immediate):**
   - FormTokenField: token added, token removed, invalid input
   - ComboboxControl: item selected
   - FormTokenField: suggestion count (debounced 500ms, but uses `'assertive'`)

2. **Polite announcements (queued):**
   - ComboboxControl: results count when suggestions expand
   - Notice: success/warning/info status messages
   - Snackbar: all messages (default `'polite'`)

3. **Politeness mapping pattern:**
   - `error` -> `'assertive'`
   - `success`/`warning`/`info` -> `'polite'`
   - Selection confirmation -> `'assertive'`
   - Results count -> `'polite'` (ComboboxControl) or `'assertive'` debounced (FormTokenField)

4. **`speak()` function usage:**
   - Always called with two args: `speak(message, politeness)`
   - Messages are i18n-ized: `sprintf(_n('%d result found...', '%d results found...', count), count)`
   - `useSpokenMessage` custom hook encapsulates the speak-on-mount pattern (Notice, Snackbar)
   - `useDebounce(speak, 500)` prevents rapid-fire announcements in FormTokenField

#### Background Isolation Patterns

1. **`aria-hidden` sibling isolation:**
   - **Component:** Modal
   - **Implementation:** `ariaHelper.modalize()` sets `aria-hidden="true"` on all `document.body` children except the modal
   - **Nesting support:** `hiddenElementsByDepth` stack allows multiple nested modals
   - **Preservation:** Scripts, already-hidden elements, and live regions are NOT hidden
   - **Reason:** Workaround for poor `aria-modal="true"` support in Safari

2. **`inert` attribute:**
   - **Component:** Disabled
   - **Implementation:** `inert="true"` on the wrapper div
   - **Effect:** Removes entire subtree from accessibility tree and prevents interaction

### Cross-Cutting Observations

1. **Ariakit as the foundation:** Gutenberg is progressively migrating to Ariakit for complex ARIA patterns (Tabs, Select, Tooltip, RadioGroup, Composite). Ariakit handles most ARIA attributes, keyboard interaction, and focus management automatically. Custom solutions (NavigableContainer, combobox) predate this migration.

2. **Infrastructure hooks are composable:** The Modal pattern composes four hooks via `useMergeRefs`: `useConstrainedTabbing`, `useFocusReturn`, `useFocusOnMount`, plus the frame ref. This composability is the key architectural pattern.

3. **VisuallyHidden is ubiquitous:** Used across 15+ components for labels, descriptions, status announcements, and position information. The CSS technique (clip + 1px size + absolute position) is the Gutenberg standard.

4. **`withIgnoreIMEEvents` is critical:** Applied to all keyboard handlers that process Enter/Escape/Arrow keys in ComboboxControl, FormTokenField, and Modal. Prevents processing keyboard events during IME composition (e.g., CJK input).

5. **Safari compatibility layer:** Multiple components include `onClick -> event.currentTarget.focus()` as a Safari workaround. Safari does not always focus interactive elements on click, which breaks state tracking.

6. **`accessibleWhenDisabled` pattern:** The Button component establishes a critical pattern: use `aria-disabled="true"` instead of `disabled` to keep elements perceivable by screen readers. This is used throughout the component library (DropdownMenu items, CircularOptionPicker actions, RangeControl reset button).

7. **ID generation consistency:** All components use `useInstanceId(Component, prefix)` from `@wordpress/compose` to generate unique IDs. The prefix convention is `'components-{component-name}'` or `'inspector-{component-name}-control'`.

8. **Label triple-fallback pattern:** Modal demonstrates the priority: (1) `contentLabel` as `aria-label`, (2) `title` generates a heading with `aria-labelledby`, (3) `aria.labelledby` allows custom external label. Only one is active at a time.

9. **Native elements preferred:** Form controls (CheckboxControl, RadioControl, TextControl, RangeControl) use native HTML elements whenever possible, relying on native semantics rather than ARIA roles. Custom components (ComboboxControl, CustomSelectControl) use ARIA only when native elements cannot achieve the desired UX.

10. **`role="application"` used sparingly:** Only DatePicker uses `role="application"` to indicate that the widget handles its own keyboard interaction model. This tells screen readers to pass through keyboard events rather than intercepting them for their own navigation.
