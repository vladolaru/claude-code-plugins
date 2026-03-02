# Accessibility Testing Patterns in Gutenberg

> Research document for AI agent consumption. Part of the a11y research series.
> Generated: 2026-02-27 | Session 3, Task 3.1

## 1. Testing Pyramid for Accessibility

Gutenberg tests accessibility across four automated levels plus manual testing. Each level catches different classes of bugs, and they complement rather than replace each other.

### Level 1: Static Analysis (ESLint jsx-a11y)

**Configuration:** `/packages/eslint-plugin/configs/jsx-a11y.js`

Gutenberg extends `plugin:jsx-a11y/recommended` with these overrides:

| Rule | Setting | Effect |
|------|---------|--------|
| `jsx-a11y/label-has-associated-control` | `error` (assert: `htmlFor`) | Requires form labels to use `htmlFor` attribute (not `nesting`) |
| `jsx-a11y/media-has-caption` | `off` | Disabled -- media captions not enforced |
| `jsx-a11y/no-noninteractive-tabindex` | `off` | Disabled -- Gutenberg intentionally uses tabindex on non-interactive elements (e.g., scrollable modal content, tabpanels) |
| `jsx-a11y/role-has-required-aria-props` | `off` | Disabled -- allows custom ARIA patterns |

**Additional Gutenberg-specific ESLint rules for a11y:**

1. **`@wordpress/components-no-unsafe-button-disabled`** -- Enforces that `Button` components include `accessibleWhenDisabled` when the `disabled` prop is set. This prevents disabled buttons from disappearing from the tab order.
   - Source: `/packages/eslint-plugin/docs/rules/components-no-unsafe-button-disabled.md`
   - Applied to `packages/*/src/**/*.[tj]s?(x)` and with `checkLocalImports: true` inside `packages/components/src/**`

2. **`no-restricted-syntax` for string literal IDs** -- Forbids `<div id="literal-string">` in JSX. Requires `useId()` hook instead, ensuring unique IDs for ARIA relationships (`aria-labelledby`, `aria-describedby`, `aria-controls`).

**What static analysis catches:**
- Missing form labels
- Missing alt text on images
- Invalid ARIA roles/attributes
- Click handlers on non-interactive elements without keyboard equivalents
- Disabled buttons without `accessibleWhenDisabled`
- Hardcoded IDs that could cause ARIA reference collisions

**What static analysis CANNOT catch:**
- Dynamic ARIA state management (expanded, selected, checked)
- Focus management on mount/unmount
- Keyboard navigation flow
- Live region announcement timing
- Correct focus return after closing overlays
- Whether `aria-label` text is actually meaningful
- Whether keyboard interactions match the expected WAI-ARIA pattern

### Level 2: Unit Tests (Jest + React Testing Library)

**Configuration:** `.eslintrc.js` lines 277-291 apply `plugin:jest-dom/recommended` and `plugin:testing-library/react` to all test files except E2E and performance tests.

Unit tests in Gutenberg verify that components render with correct ARIA attributes and roles. They test the static output of rendering.

**What unit tests catch well:**
- Correct ARIA roles on rendered elements
- Correct ARIA attribute values (aria-label, aria-describedby, aria-expanded)
- Label association (visible labels, visually hidden labels)
- Correct element semantics (button vs. link, dialog role)
- Disabled state accessibility (aria-disabled vs. disabled attribute)

**What unit tests catch poorly:**
- Complex multi-step keyboard interactions (timing-sensitive)
- Focus management in real browser layout (jsdom has no layout engine)
- Screen reader announcement ordering
- Real-world focus visibility

### Level 3: Integration Tests (Jest + RTL + user-event)

Integration tests are the workhorse of Gutenberg's a11y test suite. They use `userEvent` (or `@ariakit/test` utilities) to simulate realistic user interactions -- keyboard navigation, focus flow, typing, and clicking.

**What integration tests catch well:**
- Keyboard navigation sequences (Tab, Arrow keys, Escape, Enter)
- Focus management (trap, return, roving tabindex)
- Live region announcements (via mocked `speak()`)
- Multi-component interaction patterns (dropdown opening, menu navigation)
- Dynamic ARIA state changes (expanded toggling, selection changes)

**What integration tests catch poorly:**
- Real browser layout-dependent behavior (scrolling, viewport visibility)
- Cross-browser keyboard handling differences
- Actual screen reader behavior
- Zoom/magnification behavior

### Level 4: E2E Tests (Playwright)

**Configuration:** `.eslintrc.js` lines 300-343 apply `plugin:@wordpress/eslint-plugin/test-playwright` to E2E tests.

Gutenberg has dedicated E2E accessibility tests in:
- `/test/e2e/specs/editor/various/a11y.spec.js` -- Modal focus trap, region navigation
- `/test/e2e/specs/editor/various/a11y-region-navigation.spec.js` -- Editor region cycling
- `/test/e2e/specs/editor/various/dropdown-menu.spec.js` -- Menu keyboard navigation
- `/test/e2e/specs/editor/various/block-editor-keyboard-shortcuts.spec.js` -- Block movement
- `/test/e2e/specs/site-editor/dataviews-list-layout-keyboard.spec.js` -- Grid keyboard nav

E2E tests run in real browsers (Chromium, Firefox, WebKit) with actual layout engines.

**What E2E tests catch:**
- Real browser focus behavior
- Cross-browser keyboard handling (the `a11y.spec.js` test suite is annotated with `@firefox, @webkit`)
- Region navigation through actual editor
- Focus trap behavior in real modal overlays
- Complex interaction sequences that depend on real DOM layout

**Axe integration (Puppeteer-based, legacy):**
- Package: `/packages/jest-puppeteer-axe/` -- provides `toPassAxeTests()` matcher
- Source: `/packages/jest-puppeteer-axe/src/index.ts`
- This is a Puppeteer-based axe-core integration for the older E2E test setup
- Currently NOT actively used in the Playwright-based E2E tests (no `toPassAxeTests` calls found in `test/` directory)
- The package exists for external consumers and the older test infrastructure

### Level 5: Manual Testing

What automated tests cannot catch:
- Whether screen readers actually announce elements in the expected order
- Whether announcements make semantic sense in context
- Cognitive accessibility (is the UI confusing?)
- High contrast mode rendering
- Zoom/magnification usability (200%+ zoom)
- Voice control compatibility
- Switch access navigation patterns
- Whether focus indicators are visually apparent
- Touch target sizing on mobile

---

## 2. Test Pattern Catalog

### Pattern 1: Testing ARIA Roles

**Testing level:** Unit / Integration
**What it catches:** Elements rendered with correct semantic roles
**What it misses:** Whether the role is appropriate for the interaction pattern

**Real example -- Modal dialog role:**
```typescript
// File: /packages/components/src/modal/test/index.tsx, line 31
expect( screen.getByRole( 'dialog' ) ).toHaveAttribute(
    'aria-describedby',
    'description-id'
);
```

**Real example -- Tabs roles and semantics:**
```typescript
// File: /packages/components/src/tabs/test/index.tsx, lines 243-268
const tabList = screen.getByRole( 'tablist' );
const allTabs = screen.getAllByRole( 'tab' );
const allTabpanels = screen.getAllByRole( 'tabpanel' );

expect( tabList ).toBeVisible();
expect( tabList ).toHaveAttribute( 'aria-orientation', 'horizontal' );
expect( allTabs ).toHaveLength( TABS.length );
expect( allTabpanels ).toHaveLength( 1 ); // Only active tabpanel

// Verify aria-controls/aria-labelledby relationship
expect( allTabs[ 0 ] ).toHaveAttribute(
    'aria-controls',
    allTabpanels[ 0 ].getAttribute( 'id' )
);
expect( allTabpanels[ 0 ] ).toHaveAttribute(
    'aria-labelledby',
    allTabs[ 0 ].getAttribute( 'id' )
);
```

**Real example -- Menu WAI-ARIA compliance:**
```typescript
// File: /packages/components/src/menu/test/index.tsx, lines 46-93
const toggleButton = screen.getByRole( 'button', {
    name: 'Open dropdown',
} );

expect( toggleButton ).toHaveAttribute( 'aria-haspopup', 'menu' );
expect( toggleButton ).toHaveAttribute( 'aria-expanded', 'false' );

await click( toggleButton );

expect( toggleButton ).toHaveAttribute( 'aria-expanded', 'true' );

expect( screen.getByRole( 'separator' ) ).toHaveAttribute(
    'aria-orientation',
    'horizontal'
);
expect( screen.getAllByRole( 'menuitem' ) ).toHaveLength( 2 );

const submenuTrigger = screen.getByRole( 'menuitem', {
    name: 'Submenu trigger item',
} );
expect( submenuTrigger ).toHaveAttribute( 'aria-haspopup', 'menu' );
expect( submenuTrigger ).toHaveAttribute( 'aria-expanded', 'false' );
```

**When to use:** Every component that renders with a non-default role. Test that roles, relationships (`aria-controls`, `aria-labelledby`, `aria-owns`), and structural attributes (`aria-orientation`) are correct.

---

### Pattern 2: Testing ARIA States (expanded, selected, checked, pressed, disabled)

**Testing level:** Unit / Integration
**What it catches:** Dynamic state attributes that change during interaction
**What it misses:** Whether state changes are announced to screen readers in real time

**Real example -- aria-expanded on dropdown toggle:**
```typescript
// File: /packages/components/src/dropdown/test/index.tsx, lines 28-37
const button = screen.getByRole( 'button', { expanded: false } );
expect( button ).toBeVisible();

await user.click( button );

expect(
    screen.getByRole( 'button', { expanded: true } )
).toBeVisible();
```

**Real example -- aria-selected on tabs:**
```typescript
// File: /packages/components/src/tabs/test/index.tsx, lines 374-411
// Click on Beta, verify selection state
await click( screen.getByRole( 'tab', { name: 'Beta' } ) );

expect(
    screen.getByRole( 'tab', {
        selected: true,
        name: 'Beta',
    } )
).toBeVisible();
expect(
    screen.getByRole( 'tabpanel', {
        name: 'Beta',
    } )
).toBeVisible();
```

**Real example -- aria-selected on custom select options:**
```typescript
// File: /packages/components/src/custom-select-control-v2/test/index.tsx, lines 203-231
// Assert first item has aria-selected="true"
expect(
    screen.getByRole( 'option', {
        name: 'violets',
        selected: true,
    } )
).toBeVisible();

// Change selection
await click( screen.getByRole( 'option', { name: 'poppy' } ) );

// Re-open and verify selection changed
await click( currentSelectedItem );

expect(
    screen.getByRole( 'option', {
        name: 'violets',
        selected: false,
    } )
).toBeVisible();
expect(
    screen.getByRole( 'option', {
        name: 'poppy',
        selected: true,
    } )
).toBeVisible();
```

**Real example -- aria-pressed on toggle buttons:**
```typescript
// File: /packages/components/src/button/test/index.tsx, lines 453-499
// Boolean true
render( <Button aria-pressed /> );
expect( screen.getByRole( 'button' ) ).toHaveClass( 'is-pressed' );

// String "mixed"
render( <Button aria-pressed="mixed" /> );
expect( screen.getByRole( 'button' ) ).toHaveClass(
    'is-pressed is-pressed-mixed'
);

// Boolean false
render( <Button aria-pressed={ false } /> );
expect( screen.getByRole( 'button' ) ).not.toHaveClass( 'is-pressed' );
```

**Real example -- aria-multiselectable on listbox:**
```typescript
// File: /packages/components/src/custom-select-control-v2/test/index.tsx, lines 278-279
expect( screen.getByRole( 'listbox' ) ).toHaveAttribute(
    'aria-multiselectable'
);
```

**When to use:** Whenever a component has interactive states that change. Always verify BOTH sides of the toggle (true/false, selected/not-selected).

---

### Pattern 3: Testing ARIA Properties (label, describedby, controls)

**Testing level:** Unit
**What it catches:** Correct association between labels and their targets
**What it misses:** Whether the label text is semantically useful

**Real example -- Accessible name via aria-labelledby:**
```typescript
// File: /packages/components/src/modal/test/index.tsx, lines 37-47
render(
    <Modal aria={ { labelledby: 'title-id' } } onRequestClose={ noop }>
        <h1 id="title-id">Modal Title Text</h1>
    </Modal>
);
expect( screen.getByRole( 'dialog' ) ).toHaveAccessibleName(
    'Modal Title Text'
);
```

**Real example -- aria-label on icon-only buttons:**
```typescript
// File: /packages/components/src/button/test/index.tsx, lines 318-325
render( <Button aria-label="Custom" /> );
expect( screen.getByRole( 'button' ) ).toHaveAttribute(
    'aria-label',
    'Custom'
);
```

**Real example -- Accessible description:**
```typescript
// File: /packages/components/src/button/test/index.tsx, lines 327-334
render( <Button description="Description text" /> );
expect(
    screen.getByRole( 'button', {
        description: 'Description text',
    } )
).toBeVisible();
```

**Real example -- Tooltip aria-describedby management (not overriding existing):**
```typescript
// File: /packages/components/src/tooltip/test/index.tsx, lines 521-551
// Pre-existing aria-describedby should NOT be overridden by tooltip
render(
    <>
        <Tooltip { ...props }>
            <button aria-describedby="tooltip-test-description">
                Tooltip anchor
            </button>
        </Tooltip>
        <p id="tooltip-test-description">Tooltip description</p>
    </>
);

expect(
    screen.getByRole( 'button', { name: 'Tooltip anchor' } )
).toHaveAccessibleDescription( 'Tooltip description' );

// After tooltip shows, description should STILL be the original
await press.Tab();
await waitExpectTooltipToShow();

expect(
    screen.getByRole( 'button', { name: 'Tooltip anchor' } )
).toHaveAccessibleDescription( 'Tooltip description' );
```

**Real example -- Tooltip not duplicating aria-label as description:**
```typescript
// File: /packages/components/src/tooltip/test/index.tsx, lines 560-588
// When tooltip text matches the anchor's aria-label, don't add aria-describedby
render(
    <Tooltip text="tooltip text">
        <button aria-label="tooltip text">Tooltip anchor</button>
    </Tooltip>
);

expect(
    screen.getByRole( 'button', { name: 'tooltip text' } )
).not.toHaveAccessibleDescription();
```

**When to use:** For every component that has labels, descriptions, or controls relationships. Pay special attention to avoiding duplicate label/description (the tooltip pattern above is a known anti-pattern caught by testing).

---

### Pattern 4: Testing Keyboard Navigation (Tab, Arrow keys, Escape, Enter/Space)

**Testing level:** Integration / E2E
**What it catches:** Keyboard operability of interactive components
**What it misses:** Whether navigation matches user expectations without documentation

**Real example -- Tab as single tab stop (Composite/roving tabindex):**
```typescript
// File: /packages/components/src/composite/test/index.tsx, lines 54-81
await press.Tab();
expect(
    screen.getByRole( 'button', { name: 'Before' } )
).toHaveFocus();
await press.Tab();
expect(
    screen.getByRole( 'button', { name: 'Item 1' } )
).toHaveFocus();
await press.Tab();
expect(
    screen.getByRole( 'button', { name: 'After' } )
).toHaveFocus();
await press.ShiftTab();
expect(
    screen.getByRole( 'button', { name: 'Item 1' } )
).toHaveFocus();
```

**Real example -- Arrow key navigation within tabs:**
```typescript
// File: /packages/components/src/tabs/test/index.tsx, lines 959-1036
// Focus the tablist, alpha is selected
await press.Tab();
expect(
    await screen.findByRole( 'tab', {
        selected: true,
        name: 'Alpha',
    } )
).toHaveFocus();

// Press right arrow to select beta
await press.ArrowRight();
expect(
    screen.getByRole( 'tab', {
        selected: true,
        name: 'Beta',
    } )
).toHaveFocus();
expect(
    screen.getByRole( 'tabpanel', {
        name: 'Beta',
    } )
).toBeVisible();

// Press left arrow to go back to beta
await press.ArrowLeft();
expect(
    screen.getByRole( 'tab', {
        selected: true,
        name: 'Beta',
    } )
).toHaveFocus();
```

**Real example -- Manual tab activation (selectOnMove=false):**
```typescript
// File: /packages/components/src/tabs/test/index.tsx, lines 1038-1086
// Arrow moves focus WITHOUT selecting
await press.ArrowRight();

expect(
    screen.getByRole( 'tab', {
        selected: false,   // NOT selected
        name: 'Beta',
    } )
).toHaveFocus();             // But HAS focus
expect(
    await screen.findByRole( 'tab', {
        selected: true,
        name: 'Alpha',      // Alpha is still selected
    } )
).toBeVisible();
```

**Real example -- Menu keyboard navigation (E2E):**
```typescript
// File: /test/e2e/specs/editor/various/dropdown-menu.spec.js, lines 26-50
// Arrow down through all items
await pageUtils.pressKeys( 'ArrowDown', { times: totalItems - 1 } );
await expect( menuItems.last() ).toBeFocused();

// Arrow back up
await pageUtils.pressKeys( 'ArrowUp', { times: totalItems - 1 } );
await expect( menuItems.first() ).toBeFocused();

// Loop: arrow up from first wraps to last
await page.keyboard.press( 'ArrowUp' );
await expect( menuItems.last() ).toBeFocused();

// Loop: arrow down from last wraps to first
await page.keyboard.press( 'ArrowDown' );
await expect( menuItems.first() ).toBeFocused();
```

**Real example -- Escape to close menu:**
```typescript
// File: /packages/components/src/menu/test/index.tsx, lines 186-209
await click( trigger );
expect( screen.getByRole( 'menu' ) ).toHaveFocus();

await press.Escape();
expect( screen.queryByRole( 'menu' ) ).not.toBeInTheDocument();
expect( trigger ).toHaveFocus(); // Focus returns to trigger
```

**Real example -- Combobox keyboard selection:**
```typescript
// File: /packages/components/src/combobox-control/test/index.tsx, lines 161-189
// Tab to focus input
await user.tab();

// Navigate options with arrow keys
for ( let i = 0; i < targetIndex; i++ ) {
    await user.keyboard( '{ArrowDown}' );
}

// Enter to select
await user.keyboard( '{Enter}' );

expect( onChangeSpy ).toHaveBeenCalledWith( targetOption.value );
expect( input ).toHaveValue( targetOption.label );
```

**When to use:** For every interactive component. Test the full keyboard interaction pattern defined by WAI-ARIA APG for that widget role.

---

### Pattern 5: Testing Focus Management (trap, return, roving tabindex)

**Testing level:** Integration / E2E
**What it catches:** Focus correctly moves to expected elements on open/close/mount/unmount
**What it misses:** Whether focus indicators are visually apparent

**Real example -- Modal focus trap:**
```typescript
// File: /packages/components/src/modal/test/index.tsx (via E2E: a11y.spec.js, lines 47-75)
// E2E version -- real browser focus trap
await pageUtils.pressKeys( 'access+h' ); // Open keyboard shortcuts modal

const modalContent = page.locator(
    'role=dialog[name="Keyboard shortcuts"i] >> role=document'
);
const closeButton = page.locator(
    'role=dialog[name="Keyboard shortcuts"i] >> role=button[name="Close"i]'
);

// Close button should NOT be focused by default (UX issue #9410)
await expect( closeButton ).not.toBeFocused();

// Tab cycles within modal
await pageUtils.pressKeys( 'Tab' );
await expect( modalContent ).toBeFocused();

await pageUtils.pressKeys( 'Tab' );
await expect( closeButton ).toBeFocused();

await pageUtils.pressKeys( 'Tab' );
await expect( modalContent ).toBeFocused(); // Wraps back
```

**Real example -- Focus on mount (unit test):**
```typescript
// File: /packages/components/src/modal/test/index.tsx, lines 324-336
// Default: dialog element gets focus
await user.click( opener );
expect( screen.getByRole( 'dialog' ) ).toHaveFocus();

// focusOnMount="firstContentElement": first focusable content gets focus
await user.click( opener );
expect(
    screen.getByText( 'First Focusable Content Element' )
).toHaveFocus();

// focusOnMount="firstElement": close button gets focus (first in DOM)
await user.click( opener );
expect(
    screen.getByRole( 'button', { name: 'Close' } )
).toHaveFocus();

// focusOnMount={false}: focus stays on opener
await user.click( opener );
expect( opener ).toHaveFocus();
```

**Real example -- Focus return after dismiss:**
```typescript
// File: /packages/components/src/modal/test/index.tsx, lines 92-118
// Click opener to show modal
const opener = screen.getByRole( 'button' );
await user.click( opener );
const modalFrame = screen.getByRole( 'dialog' );
expect( modalFrame ).toHaveFocus();

// Click overlay to dismiss
await user.click( modalFrame.parentElement! );
expect( opener ).toHaveFocus(); // Focus returns to opener
```

**Real example -- withFocusReturn HOC:**
```typescript
// File: /packages/components/src/higher-order/with-focus-return/test/index.tsx, lines 86-105
// Focus textarea inside the HOC
await user.click(
    screen.getByRole( 'textbox', { name: 'Textarea' } )
);

// Unmount the component
unmount();

// Focus should return to the element that had focus before mount
expect( activeElement ).toHaveFocus();
```

**Real example -- Roving tabindex in Composite:**
```typescript
// File: /packages/components/src/composite/test/index.tsx, lines 83-103
// Disabled items are skipped
const item1 = screen.getByRole( 'button', { name: 'Item 1' } );
const item2 = screen.getByRole( 'button', { name: 'Item 2' } ); // disabled
const item3 = screen.getByRole( 'button', { name: 'Item 3' } );

expect( item2 ).toBeDisabled();

await press.Tab();
expect( item1 ).toHaveFocus();
await press.ArrowDown();
expect( item2 ).not.toHaveFocus(); // Skipped!
expect( item3 ).toHaveFocus();
```

**Real example -- Focusable disabled items (accessibleWhenDisabled):**
```typescript
// File: /packages/components/src/composite/test/index.tsx, lines 105-128
const item2 = screen.getByRole( 'button', { name: 'Item 2' } );

// Item is enabled in DOM but has aria-disabled
expect( item2 ).toBeEnabled();
expect( item2 ).toHaveAttribute( 'aria-disabled', 'true' );

await press.Tab();
expect( item1 ).toHaveFocus();
await press.ArrowDown();
expect( item2 ).toHaveFocus(); // NOT skipped -- accessible when disabled
```

**Important setup for focus testing in jsdom:**
```typescript
// File: /packages/components/src/modal/test/index.tsx, lines 303-315
// jsdom has no layout engine -- must mock getClientRects
// for focus detection to work
window.HTMLElement.prototype.getClientRects = function () {
    return [ 'trick-jsdom-into-having-size-for-element-rect' ];
};
```

**When to use:** For any component that:
1. Creates an overlay (modal, popover, dropdown) -- test focus trap and return
2. Uses roving tabindex (composite, tablist, toolbar) -- test arrow key navigation
3. Manages focus on mount/unmount -- test both mounting and unmounting behavior

---

### Pattern 6: Testing Focus on Mount/Unmount

**Testing level:** Integration
**What it catches:** Components that steal focus or lose focus tracking
**What it misses:** Animation/transition timing effects on focus

**Real example -- Tab panel focus on mount:**
```typescript
// File: /packages/components/src/tabs/test/index.tsx, lines 892-913
// Tab to the selected tab
await press.Tab();
expect(
    await screen.findByRole( 'tab', {
        selected: true,
        name: 'Alpha',
    } )
).toHaveFocus();

// Tab to tabpanel (receives focus by default)
await press.Tab();
expect(
    await screen.findByRole( 'tabpanel', {
        name: 'Alpha',
    } )
).toHaveFocus();
```

**Real example -- TabPanel with focusable=false (focus skips container):**
```typescript
// File: /packages/components/src/tabs/test/index.tsx, lines 916-957
// When tabpanel has focusable=false, tab skips the panel container
// and goes directly to the first focusable child
await press.Tab(); // Focus on tab
await press.Tab(); // Focus goes to button INSIDE tabpanel (not the panel itself)
expect(
    await screen.findByRole( 'button', {
        name: 'Alpha Button',
    } )
).toHaveFocus();
```

**Real example -- Composite remains focusable after active item removal:**
```typescript
// File: /packages/components/src/composite/test/index.tsx, lines 147-200
// Navigate to Item 3
await press.ArrowRight();
await press.ArrowRight();
expect(
    screen.getByRole( 'button', { name: 'Item 3' } )
).toHaveFocus();

// Remove Item 3 from DOM
await click( toggleButton );
expect(
    screen.queryByRole( 'button', { name: 'Item 3' } )
).not.toBeInTheDocument();

// Composite should still be reachable via Shift+Tab
await press.ShiftTab();
// Focus moves to a remaining item in the composite
```

**When to use:** Test focus behavior whenever elements are added to or removed from the DOM dynamically, especially in controlled component scenarios.

---

### Pattern 7: Testing Live Region Announcements (speak())

**Testing level:** Integration
**What it catches:** That `speak()` is called with correct message and politeness
**What it misses:** Whether the announcement is actually heard by the user

**Setup pattern -- Mocking @wordpress/a11y:**
```typescript
// File: /packages/components/src/notice/test/index.tsx, lines 17-18
jest.mock( '@wordpress/a11y', () => ( { speak: jest.fn() } ) );
const mockedSpeak = jest.mocked( speak );

// Reset between tests
beforeEach( () => {
    mockedSpeak.mockReset();
} );
```

**Real example -- Politeness levels:**
```typescript
// File: /packages/components/src/notice/test/index.tsx, lines 63-92
// Default politeness (polite)
render( <Notice>FYI</Notice> );
expect( speak ).toHaveBeenCalledWith( 'FYI', 'polite' );

// Explicit assertive
render( <Notice politeness="assertive">Uh oh!</Notice> );
expect( speak ).toHaveBeenCalledWith( 'Uh oh!', 'assertive' );

// Implicit assertive via status="error"
render( <Notice status="error">Uh oh!</Notice> );
expect( speak ).toHaveBeenCalledWith( 'Uh oh!', 'assertive' );

// Explicit overrides implicit
render(
    <Notice politeness="polite" status="error">
        No need to panic
    </Notice>
);
expect( speak ).toHaveBeenCalledWith( 'No need to panic', 'polite' );
```

**Real example -- No re-announcement on equivalent re-render:**
```typescript
// File: /packages/components/src/notice/test/index.tsx, lines 109-122
const { rerender } = render(
    <Notice>
        With <em>emphasis</em> this time.
    </Notice>
);
rerender(
    <Notice>
        With <em>emphasis</em> this time.
    </Notice>
);

// speak should only be called once, not on re-render with same content
expect( speak ).toHaveBeenCalledTimes( 1 );
```

**Real example -- aria-live region in DOM (ComboboxControl):**
```typescript
// File: /packages/components/src/combobox-control/test/index.tsx, lines 258-284
// After selecting an option, verify the aria-live announcement element exists
await user.keyboard( '{Enter}' );

expect(
    screen.getByText( 'Item selected.', {
        selector: '[aria-live]',
    } )
).toBeInTheDocument();
```

**Real example -- Testing the @wordpress/a11y speak() function itself:**
```typescript
// File: /packages/a11y/src/test/index.test.js, lines 43-58
// Default mode sets text in polite region
speak( 'default message' );
expect( containerPolite ).toHaveTextContent( 'default message' );
expect( containerAssertive ).toBeEmptyDOMElement();

// Assertive mode sets text in assertive region
speak( 'assertive message', 'assertive' );
expect( containerPolite ).toBeEmptyDOMElement();
expect( containerAssertive ).toHaveTextContent( 'assertive message' );
```

**When to use:** For any component that produces screen reader announcements. Test:
1. The announcement message content
2. The correct politeness level (polite vs. assertive)
3. That announcements don't repeat unnecessarily on re-render
4. Fallback behavior when announcement containers are missing

---

### Pattern 8: Testing Disabled State Accessibility

**Testing level:** Unit / Integration
**What it catches:** Disabled elements remain discoverable and have correct semantics
**What it misses:** Whether disabled state styling provides sufficient visual contrast

**Real example -- Standard disabled button:**
```typescript
// File: /packages/components/src/button/test/index.tsx, lines 236-248
// Standard disabled: button is actually disabled in DOM
render( <Button disabled /> );
expect( screen.getByRole( 'button' ) ).toBeDisabled();

// Accessible when disabled: button is enabled but has aria-disabled
render( <Button disabled accessibleWhenDisabled /> );
const button = screen.getByRole( 'button' );
expect( button ).toBeEnabled();
expect( button ).toHaveAttribute( 'aria-disabled' );
```

**Real example -- Disabled tab behavior:**
```typescript
// File: /packages/components/src/tabs/test/index.tsx, lines 415-448
// Clicking a disabled tab does NOT change selection
await click( screen.getByRole( 'tab', { name: 'Beta' } ) );

// Alpha remains selected
expect(
    screen.getByRole( 'tab', {
        selected: true,
        name: 'Alpha',
    } )
).toBeVisible();
```

**Real example -- Disabled dialog buttons with aria-disabled:**
```typescript
// File: /packages/components/src/confirm-dialog/test/index.tsx, lines 353-377
// When isBusy, buttons use aria-disabled (not disabled attribute)
expect( cancelButton ).toHaveAttribute( 'aria-disabled', 'true' );
expect( confirmButton ).toHaveAttribute( 'aria-disabled', 'true' );
```

**Real example -- Menu: disabled items remain focusable and accessible:**
```typescript
// File: /packages/components/src/menu/test/index.tsx, lines 120-151
// Arrow down opens menu, first item is disabled but receives focus
await press.ArrowDown();

// Disabled items are STILL focusable and accessible
expect(
    screen.getByRole( 'menuitem', { name: 'First item' } )
).toHaveFocus();
```

**When to use:** Whenever testing disabled interactive elements. Always verify:
1. The element uses `aria-disabled` (not `disabled`) when it should remain focusable
2. The ESLint rule `@wordpress/components-no-unsafe-button-disabled` is active
3. Disabled-but-focusable elements can still receive and manage focus

---

### Pattern 9: Testing Icon-Only Button Labels

**Testing level:** Unit / Integration
**What it catches:** Buttons without visible text have accessible names
**What it misses:** Whether the label is descriptive enough in context

**Real example -- Icon button with tooltip label:**
```typescript
// File: /packages/components/src/button/test/index.tsx, lines 307-316
render( <Button icon={ plusCircle } label="WordPress" /> );

// Label is not visible by default
expect( screen.queryByText( 'WordPress' ) ).not.toBeInTheDocument();

// Move focus to button -- tooltip shows the label
await press.Tab();
expect( screen.getByText( 'WordPress' ) ).toBeVisible();
```

**Real example -- Icon button without visible text -- no aria-label by default:**
```typescript
// File: /packages/components/src/button/test/index.tsx, lines 274-279
render( <Button icon={ plusCircle } /> );
const button = screen.getByRole( 'button' );
expect( button ).toHaveClass( 'has-icon' );
expect( button ).not.toHaveAttribute( 'aria-label' ); // WARNING: no accessible name!
```

**Real example -- Tooltip NOT added when button has both icon and children:**
```typescript
// File: /packages/components/src/button/test/index.tsx, lines 420-433
render(
    <Button icon={ plusCircle } label="WordPress">
        Children
    </Button>
);
// With visible text children, tooltip is NOT shown (redundant)
await press.Tab();
expect( screen.queryByText( 'WordPress' ) ).not.toBeInTheDocument();
```

**When to use:** Test every button that uses an icon without visible text. The minimum test: `screen.getByRole('button', { name: 'Expected label' })`.

---

### Pattern 10: Testing Modal/Dialog Lifecycle

**Testing level:** Integration / E2E
**What it catches:** Correct aria-hidden management, focus trap, and cleanup
**What it misses:** Animation effects on focus timing

**Real example -- aria-hidden management with nested modals:**
```typescript
// File: /packages/components/src/modal/test/index.tsx, lines 201-255
// Open outer modal -> container gets aria-hidden
await user.click( screen.getByRole( 'button', { name: 'Start' } ) );
expect( container ).toHaveAttribute( 'aria-hidden', 'true' );

const outer = screen.getByRole( 'dialog' ).parentElement!;

// Open inner modal -> outer modal gets aria-hidden
await user.click( screen.getByRole( 'button', { name: 'Nest' } ) );
expect( outer ).toHaveAttribute( 'aria-hidden', 'true' );

// Close inner -> outer is unhidden, container stays hidden
await user.keyboard( '[Escape]' );
expect( outer ).not.toHaveAttribute( 'aria-hidden' );
expect( container ).toHaveAttribute( 'aria-hidden', 'true' );

// Close outer -> container is unhidden
await user.keyboard( '[Escape]' );
expect( container ).not.toHaveAttribute( 'aria-hidden' );
```

**Real example -- Scrollable modal content gets tabindex:**
```typescript
// File: /test/e2e/specs/editor/various/a11y.spec.js, lines 116-225
// The Blocks tab panel content is long and scrollable.
// Check it's focusable.
await clickAndFocusTab( blocksTab );
await expect( preferencesModalContent ).toHaveAttribute(
    'tabindex',
    '0'
);

// Short content (not scrollable) should NOT be focusable
await clickAndFocusTab( generalTab );
await pageUtils.pressKeys( 'ArrowDown', { times: 2 } );
// Navigate to Accessibility tab (short content)
await pageUtils.pressKeys( 'Shift+Tab' );
await expect( closeButton ).toBeFocused();
await pageUtils.pressKeys( 'Shift+Tab' );
await expect( preferencesModalContent ).not.toBeFocused();
```

**Real example -- Modal Escape key handling:**
```typescript
// File: /packages/components/src/modal/test/index.tsx, lines 80-90
const user = userEvent.setup();
const onRequestClose = jest.fn();
render(
    <Modal onRequestClose={ onRequestClose }>
        <p>Modal content</p>
    </Modal>
);
await user.keyboard( '[Escape]' );
expect( onRequestClose ).toHaveBeenCalled();
```

**When to use:** For every overlay component (modal, popover, dialog). Test the complete lifecycle:
1. Opening: focus moves into the overlay
2. While open: aria-hidden on sibling content, focus trap works
3. Closing: focus returns to trigger, aria-hidden removed from siblings

---

### Pattern 11: Testing Dropdown/Menu Interactions

**Testing level:** Integration / E2E
**What it catches:** Menu keyboard pattern compliance
**What it misses:** Screen reader virtual cursor navigation

**Real example -- Opening menu with ArrowDown:**
```typescript
// File: /packages/components/src/dropdown-menu/test/index.tsx, lines 33-73
// Move focus to toggle button
await user.tab();

// ArrowDown opens menu
await user.keyboard( '[ArrowDown]' );

const menu = screen.getByRole( 'menu' );
await waitFor( () => expect( menu ).toBeVisible() );

expect( within( menu ).getAllByRole( 'menuitem' ) ).toHaveLength(
    controls.length
);
```

**Real example -- Submenu on hover with aria-expanded:**
```typescript
// File: /packages/components/src/menu/test/index.tsx, lines 71-93
const submenuTrigger = screen.getByRole( 'menuitem', {
    name: 'Submenu trigger item',
} );
expect( submenuTrigger ).toHaveAttribute( 'aria-haspopup', 'menu' );
expect( submenuTrigger ).toHaveAttribute( 'aria-expanded', 'false' );

await hover( submenuTrigger );

// Wait for open animation
await waitFor( () =>
    expect(
        screen.getByRole( 'menu', {
            name: submenuTrigger.textContent ?? '',
        } )
    ).toBeVisible()
);

expect( submenuTrigger ).toHaveAttribute( 'aria-expanded', 'true' );
expect( submenuTrigger ).toHaveAttribute(
    'aria-controls',
    screen.getAllByRole( 'menu' )[ 1 ].id
);
```

**When to use:** For any dropdown, menu, or popover component. Test:
1. Multiple open methods (click, ArrowDown, Space, Enter)
2. Multiple close methods (Escape, click outside, selecting an item)
3. Focus return to trigger on close
4. Correct aria-expanded toggling

---

### Pattern 12: Testing Visually Hidden Content for Assistive Technology

**Testing level:** Unit / Integration
**What it catches:** Content exists for screen readers but is hidden visually
**What it misses:** Whether the hidden text makes sense in screen reader context

**Real example -- Visually hidden label:**
```typescript
// File: /packages/components/src/combobox-control/test/index.tsx, lines 99-114
render(
    <Component
        options={ timezones }
        label={ defaultLabelText }
        hideLabelFromVision
    />
);
const label = getLabel( defaultLabelText );

expect( label ).toBeInTheDocument();
expect( label ).toHaveAttribute(
    'data-wp-component',
    'VisuallyHidden'
);
```

**Real example -- Dual rendering for FormTokenField tokens:**
```typescript
// File: /packages/components/src/form-token-field/test/index.tsx, lines 55-84
// Each token has TWO representations:
// 1. Assistive technology: "tokenName (X of Y)" -- visibly hidden
// 2. Visual: "tokenName" -- hidden from assistive tech via aria-hidden

const assistiveTechnologyToken = screen.getByText(
    `${ tokenText } (${ tokenIndex + 1 } of ${ tokensArray.length })`,
    { normalizer: getDefaultNormalizer( { collapseWhitespace: false, trim: false } ) }
);
const visibleToken = screen.getByText( tokenText, {
    exact: true,
    normalizer: getDefaultNormalizer( { collapseWhitespace: false, trim: false } ),
} );

expect( assistiveTechnologyToken ).toBeInTheDocument();
expect( visibleToken ).toBeVisible();
expect( visibleToken ).toHaveAttribute( 'aria-hidden', 'true' );
```

**Real example -- Snackbar dismiss label:**
```typescript
// File: /packages/components/src/snackbar/test/index.tsx, lines 68-79
// Implicit dismiss snackbar
const snackbar = screen.getByTestId( testId );
expect( snackbar ).toHaveAttribute( 'role', 'button' );
expect( snackbar ).toHaveAttribute( 'aria-label', 'Dismiss this notice' );

// Explicit dismiss snackbar (different pattern)
expect( snackbar ).not.toHaveAttribute( 'role', 'button' );
expect( snackbar ).not.toHaveAttribute( 'aria-label', 'Dismiss this notice' );
// Instead has a visible close button:
const closeButton = within( snackbar ).getByRole( 'button', {
    name: 'Dismiss this notice',
} );
```

**When to use:** When content is displayed differently for sighted users vs. screen reader users. Test BOTH representations.

---

### Pattern 13: Testing Region Navigation (E2E Only)

**Testing level:** E2E
**What it catches:** Editor region landmarks are correctly labeled and navigable
**What it misses:** Whether the region labels are meaningful to screen reader users

**Real example -- Cycling through editor regions:**
```typescript
// File: /test/e2e/specs/editor/various/a11y-region-navigation.spec.js, lines 15-62
// Navigate forward through regions: Ctrl+`
await page.keyboard.press( 'Control+`' );
await page.keyboard.press( 'Control+`' );
await page.keyboard.press( 'Control+`' );
await page.keyboard.press( 'Control+`' );

const editorTopBar = page.locator(
    'role=region[name="Editor top bar"i]'
);
await expect( editorTopBar ).toBeFocused();

// Navigate to next region
await page.keyboard.press( 'Control+`' );
const editorContent = page.locator(
    'role=region[name="Editor content"i]'
);
await expect( editorContent ).toBeFocused();

// Navigate backward: Ctrl+Shift+` (or Ctrl+Shift+~ in non-Chromium)
if ( testInfo.project.name === 'chromium' ) {
    await page.keyboard.press( 'Control+Shift+`' );
} else {
    await page.keyboard.press( 'Control+Shift+~' );
}
await expect( editorTopBar ).toBeFocused();
```

**When to use:** For testing landmark-based navigation in complex page layouts. This pattern is specific to E2E tests because it requires real browser keyboard handling.

---

### Pattern 14: Testing Form Control Associations and Reset

**Testing level:** Integration
**What it catches:** Form inputs are associated with their labels; reset returns focus correctly
**What it misses:** Whether the form is usable with voice control ("click label name")

**Real example -- Combobox with labeled input:**
```typescript
// File: /packages/components/src/combobox-control/test/index.tsx, lines 54-55
const getInput = ( name: string ) => screen.getByRole( 'combobox', { name } );

// Later usage:
const input = getInput( defaultLabelText ); // queries by accessible name
```

**Real example -- Reset button returns focus to input:**
```typescript
// File: /packages/components/src/combobox-control/test/index.tsx, lines 381-413
// Select a value
await user.tab();
await user.keyboard( getOptionSearchString( targetOption ) );
await user.keyboard( '{Enter}' );

expect( input ).toHaveValue( targetOption.label );

// Click reset
const resetButton = screen.getByRole( 'button', { name: 'Reset' } );
expect( resetButton ).toBeEnabled();
await user.click( resetButton );

// Reset button disappears, input cleared, focus returns to input
expect( resetButton ).not.toBeInTheDocument();
expect( input ).toHaveValue( '' );
expect( input ).toHaveFocus();
```

**Real example -- Range control with slider and spinbutton roles:**
```typescript
// File: /packages/components/src/range-control/test/index.tsx, lines 11-13
const getRangeInput = (): HTMLInputElement => screen.getByRole( 'slider' );
const getNumberInput = (): HTMLInputElement => screen.getByRole( 'spinbutton' );
const getResetButton = (): HTMLButtonElement => screen.getByRole( 'button' );
```

**When to use:** For all form controls. Test that the control can be found by its accessible name, and that any reset/clear operation properly manages focus.

---

## 3. Assertion Reference

### Query Methods (prioritized)

Gutenberg's tests use React Testing Library queries in this priority order:

| Priority | Method | Use Case | Example |
|----------|--------|----------|---------|
| 1 | `getByRole` | Primary query for all elements | `screen.getByRole('button', { name: 'Save' })` |
| 2 | `getByRole` with `selected`/`expanded` | State-based queries | `screen.getByRole('tab', { selected: true, name: 'Alpha' })` |
| 3 | `getByRole` with `description` | Description-based queries | `screen.getByRole('button', { description: 'Desc text' })` |
| 4 | `getByLabelText` | Form inputs by label | Less common in Gutenberg -- prefer `getByRole('combobox', { name })` |
| 5 | `getByText` | Visible text content | `screen.getByText('Item selected.', { selector: '[aria-live]' })` |
| 6 | `findByRole` | Async waiting for element | `await screen.findByRole('tab', { selected: true })` |
| 7 | `queryByRole` | Assert element does NOT exist | `screen.queryByRole('menu').not.toBeInTheDocument()` |

**When NOT to use:**
- `getByTestId` -- Used in Gutenberg only as a last resort for elements without semantic roles (e.g., Snackbar wrapper). Never use for elements that have ARIA roles.
- `container.querySelector` -- Used only when no semantic query can reach the element (e.g., modal overlay). Always add eslint-disable comment explaining why.

### ARIA Attribute Assertions

```typescript
// Role
expect( element ).toHaveAttribute( 'role', 'dialog' );

// Expanded state
expect( element ).toHaveAttribute( 'aria-expanded', 'true' );
expect( element ).toHaveAttribute( 'aria-expanded', 'false' );
// Preferred: query by state
screen.getByRole( 'button', { expanded: true } );

// Selected state (via query)
screen.getByRole( 'tab', { selected: true, name: 'Alpha' } );
screen.getByRole( 'option', { selected: false, name: 'violets' } );

// Label
expect( element ).toHaveAttribute( 'aria-label', 'Close' );
expect( element ).toHaveAccessibleName( 'Modal Title Text' );  // Preferred

// Description
expect( element ).toHaveAccessibleDescription( 'Tooltip description' );
expect( element ).not.toHaveAccessibleDescription();

// Controls/labelledby relationships
expect( tab ).toHaveAttribute( 'aria-controls', tabpanel.getAttribute( 'id' ) );
expect( tabpanel ).toHaveAttribute( 'aria-labelledby', tab.getAttribute( 'id' ) );

// Hidden
expect( element ).toHaveAttribute( 'aria-hidden', 'true' );
expect( element ).not.toHaveAttribute( 'aria-hidden' );

// Disabled
expect( element ).toBeDisabled();                       // HTML disabled attribute
expect( element ).toBeEnabled();                        // NOT disabled
expect( element ).toHaveAttribute( 'aria-disabled', 'true' );  // aria-disabled

// Haspopup
expect( element ).toHaveAttribute( 'aria-haspopup', 'menu' );

// Orientation
expect( tabList ).toHaveAttribute( 'aria-orientation', 'horizontal' );
expect( separator ).toHaveAttribute( 'aria-orientation', 'horizontal' );

// Tabindex
expect( element ).toHaveAttribute( 'tabindex', '0' );
expect( element ).toHaveAttribute( 'tabindex', '-1' );

// Multiselectable
expect( listbox ).toHaveAttribute( 'aria-multiselectable' );

// Visibility
expect( element ).toBeVisible();
expect( element ).not.toBeVisible();
expect( element ).toBeInTheDocument();
expect( element ).not.toBeInTheDocument();
```

### Keyboard Simulation

**In unit/integration tests (Jest + user-event):**
```typescript
const user = userEvent.setup();

// Tab navigation
await user.tab();
await user.keyboard( '[Tab]' );
await user.keyboard( '[Shift>][Tab]' ); // Shift+Tab

// Keys
await user.keyboard( '[Escape]' );
await user.keyboard( '[Enter]' );
await user.keyboard( '[Space]' );
await user.keyboard( '{ArrowDown}' );
await user.keyboard( '{ArrowUp}' );
await user.keyboard( '{ArrowLeft}' );
await user.keyboard( '{ArrowRight}' );

// Typing
await user.type( input, 'search text' );
await user.type( input, 'apple[Enter]' ); // Type then press Enter
await user.clear( input );

// Click
await user.click( element );
```

**In unit/integration tests (@ariakit/test -- used by newer components):**
```typescript
import { press, click, hover, type, sleep } from '@ariakit/test';

await press.Tab();
await press.ShiftTab();
await press.Enter();
await press.Space();
await press.Escape();
await press.ArrowDown();
await press.ArrowUp();
await press.ArrowLeft();
await press.ArrowRight();

await click( element );
await hover( element );
await type( 'search text' );
await sleep( 300 ); // For animation delays
```

**In E2E tests (Playwright):**
```typescript
// Direct keyboard
await page.keyboard.press( 'Tab' );
await page.keyboard.press( 'Shift+Tab' );
await page.keyboard.press( 'ArrowDown' );
await page.keyboard.press( 'Escape' );
await page.keyboard.press( 'Enter' );
await page.keyboard.press( 'Control+`' ); // Region navigation
await page.keyboard.type( 'search text' );

// Via pageUtils (handles cross-platform modifier keys)
await pageUtils.pressKeys( 'Tab' );
await pageUtils.pressKeys( 'shift+Tab' );
await pageUtils.pressKeys( 'ctrl+`' );
await pageUtils.pressKeys( 'ArrowDown', { times: 5 } );
await pageUtils.pressKeys( 'access+h' ); // Access key shortcut
await pageUtils.pressKeys( 'primary+c' ); // Copy (Cmd on Mac, Ctrl otherwise)
await pageUtils.pressKeys( 'secondary+t' ); // Block move shortcut
```

### Focus Assertions

**In unit/integration tests:**
```typescript
expect( element ).toHaveFocus();
expect( element ).not.toHaveFocus();

// Via role query
expect(
    screen.getByRole( 'button', { name: 'Save' } )
).toHaveFocus();
```

**In E2E tests (Playwright):**
```typescript
await expect( page.locator( 'role=button[name="Save"]' ) ).toBeFocused();
await expect( menuItems.first() ).toBeFocused();
await expect( editorTopBar ).toBeFocused();

// Locator-based
await expect(
    editor.canvas.locator( 'role=textbox[name=/Add title/i]' )
).toBeFocused();

// Region-based
await expect(
    page.locator( 'role=region[name="Editor top bar"i]' )
).toBeFocused();
```

### Live Region Assertions

**Mocking speak():**
```typescript
import { speak } from '@wordpress/a11y';

jest.mock( '@wordpress/a11y', () => ( { speak: jest.fn() } ) );
const mockedSpeak = jest.mocked( speak );

beforeEach( () => mockedSpeak.mockReset() );

// Assert speak was called
expect( speak ).toHaveBeenCalledWith( 'FYI', 'polite' );
expect( speak ).toHaveBeenCalledWith( 'Error!', 'assertive' );
expect( speak ).toHaveBeenCalledTimes( 1 );
```

**Asserting aria-live regions in DOM:**
```typescript
// Find text within an aria-live region
expect(
    screen.getByText( 'Item selected.', {
        selector: '[aria-live]',
    } )
).toBeInTheDocument();
```

---

## 4. Test Writing Rules for Agents

### Rule 1: Minimum a11y Test Coverage for Every Component

Every component MUST have these tests:

1. **Role verification** -- Query the primary element by role and accessible name:
   ```typescript
   screen.getByRole( 'button', { name: 'Submit' } )
   ```
   If this fails, the component has an accessibility problem.

2. **Disabled state** -- If the component supports `disabled`, test both patterns:
   ```typescript
   // Standard disabled
   expect( screen.getByRole( 'button' ) ).toBeDisabled();
   // Accessible when disabled
   expect( screen.getByRole( 'button' ) ).toHaveAttribute( 'aria-disabled', 'true' );
   expect( screen.getByRole( 'button' ) ).toBeEnabled(); // Still in DOM!
   ```

3. **Label/name** -- If the component has a label prop, test visible and hidden label modes:
   ```typescript
   expect( screen.getByText( labelText ) ).toBeVisible(); // or
   expect( screen.getByText( labelText ) ).toHaveAttribute( 'data-wp-component', 'VisuallyHidden' );
   ```

### Rule 2: When to Add Keyboard Interaction Tests

Add keyboard tests when the component:
- Has a `role` of `tab`, `tablist`, `menu`, `menuitem`, `combobox`, `listbox`, `option`, `dialog`, `tree`, `treeitem`, or `grid`
- Renders custom keyboard shortcuts
- Uses roving tabindex (Composite pattern)
- Has expandable/collapsible sections

Test the COMPLETE keyboard pattern from the WAI-ARIA APG for that role:
- Tab/Shift+Tab for entering/leaving the widget
- Arrow keys for navigating within
- Enter/Space for activation
- Escape for closing/canceling
- Home/End for jumping to first/last item

### Rule 3: When to Add Focus Management Tests

Add focus management tests when the component:
- Creates overlays (modals, popovers, dropdowns)
- Removes elements from the DOM (unmount/conditional rendering)
- Has a `focusOnMount` or equivalent prop
- Manages focus return (closing dialogs, removing items)

Test:
1. Where focus goes on mount
2. Where focus goes on unmount/close
3. That focus trap works (Tab cycles within overlay)
4. That focus returns to the trigger element on close

### Rule 4: When to Add Live Region Tests

Add live region tests when the component:
- Renders notices, snackbars, or alerts
- Has status changes that should be announced (selection, loading, errors)
- Uses `@wordpress/a11y` `speak()` function
- Renders `aria-live` regions

Test:
1. That `speak()` is called with the correct message
2. That `speak()` uses the correct politeness level
3. That repeated identical messages don't cause re-announcements
4. That error states use `assertive` politeness

### Rule 5: How to Write a11y Tests That Actually Catch Bugs

1. **Always use role-based queries** -- If you can't find an element by role and accessible name, that IS the bug.

2. **Test the negative case** -- Don't just test that `aria-expanded` is `true` when open; test that it's `false` when closed.

3. **Test state transitions** -- The most common a11y bugs are in transitions: opening/closing, enabling/disabling, selecting/deselecting.

4. **Test after user actions, not just on render** -- Many a11y bugs manifest only after interaction:
   ```typescript
   // BAD: Only tests initial render
   expect( screen.getByRole( 'button' ) ).toHaveAttribute( 'aria-expanded', 'false' );

   // GOOD: Tests the transition
   await user.click( screen.getByRole( 'button' ) );
   expect( screen.getByRole( 'button', { expanded: true } ) ).toBeVisible();
   await user.keyboard( '[Escape]' );
   expect( screen.getByRole( 'button', { expanded: false } ) ).toBeVisible();
   ```

5. **Test focus AFTER the action completes** -- Focus bugs are the #1 source of a11y regressions:
   ```typescript
   await user.click( openButton );
   expect( screen.getByRole( 'dialog' ) ).toHaveFocus();

   await user.keyboard( '[Escape]' );
   expect( openButton ).toHaveFocus(); // Focus MUST return
   ```

### Rule 6: Common Test Anti-Patterns to Avoid

1. **Using `container.querySelector` for elements with roles** -- Always prefer `screen.getByRole`. If you need `querySelector`, add an eslint-disable comment explaining why.

2. **Using `getByTestId` as primary query** -- Only use as last resort. If an element needs a testid, consider whether it should have a role instead.

3. **Testing CSS classes instead of ARIA attributes** -- Don't test `toHaveClass('is-expanded')` when you should test `toHaveAttribute('aria-expanded', 'true')`. CSS classes are implementation details; ARIA attributes are the contract with assistive technology.

4. **Forgetting the jsdom layout mock** -- jsdom has no layout engine. Tests that check focusability WILL fail without mocking `getClientRects`:
   ```typescript
   window.HTMLElement.prototype.getClientRects = function () {
       return [ 'trick-jsdom-into-having-size-for-element-rect' ];
   };
   ```

5. **Not testing Shift+Tab** -- Many tests only test forward Tab navigation. Always test backward navigation too, especially for focus traps and composite widgets.

6. **Testing only one keyboard path** -- If a menu can be opened with click, Enter, Space, and ArrowDown, test ALL of them. Different users use different input methods.

7. **Forgetting `waitFor` with animated components** -- Dropdowns and menus use animations. Always wrap visibility assertions in `waitFor`:
   ```typescript
   await waitFor( () => expect( menu ).toBeVisible() );
   ```

8. **Not cleaning up tooltips** -- Tooltips and popovers may persist between tests. Use cleanup utilities:
   ```typescript
   import cleanupTooltip from '../../tooltip/test/utils';
   afterEach( async () => await cleanupTooltip() );
   ```

### Rule 7: Coverage Gap Identification

Common areas where test suites miss a11y coverage:

1. **Error state announcements** -- Many components show visual error states without testing that errors are announced via `speak()` or `aria-live`.

2. **Loading state accessibility** -- Loading indicators often lack `aria-busy`, `aria-live` regions for completion, or proper labeling.

3. **Dynamic content updates** -- When content changes without page navigation (e.g., filtering a list), the update often is not announced.

4. **Focus after async operations** -- After a save, delete, or API call completes, focus often goes nowhere. Test where focus should land.

5. **Zoom/reflow behavior** -- No automated tests verify the UI works at 200% zoom or when text-only zoom is applied.

6. **Axe-core integration in E2E** -- Gutenberg has the `jest-puppeteer-axe` package but does NOT run automated axe scans in the current Playwright E2E suite. This is a significant gap -- adding axe-playwright integration would catch many basic violations automatically.

7. **Multi-modal keyboard shortcuts** -- Complex shortcuts like `Ctrl+Shift+Backtick` behave differently across browsers (note the cross-browser handling in `a11y-region-navigation.spec.js` line 55-59). Test shortcuts in all target browsers.

8. **RTL keyboard navigation** -- The `Tabs` test mocks `isRTL()` to test arrow key direction reversal, but many other components don't test RTL mode at all.

---

## 5. Key Testing Infrastructure Files Reference

| File | Purpose |
|------|---------|
| `/packages/eslint-plugin/configs/jsx-a11y.js` | ESLint jsx-a11y rule configuration |
| `/packages/eslint-plugin/docs/rules/components-no-unsafe-button-disabled.md` | Custom rule: require accessibleWhenDisabled |
| `/.eslintrc.js` (lines 277-291) | testing-library/react and jest-dom ESLint plugins for test files |
| `/packages/jest-puppeteer-axe/src/index.ts` | axe-core Puppeteer integration (legacy, not actively used in E2E) |
| `/packages/e2e-test-utils-playwright/src/page-utils/press-keys.ts` | Cross-platform keyboard simulation for E2E |
| `/packages/a11y/src/test/index.test.js` | Tests for the `speak()` function itself |
| `/test/e2e/specs/editor/various/a11y.spec.js` | E2E: Modal focus trap, region navigation, scrollable content |
| `/test/e2e/specs/editor/various/a11y-region-navigation.spec.js` | E2E: Editor region cycling (Ctrl+Backtick) |
| `/packages/components/src/modal/test/index.tsx` | Unit: aria-hidden management, focus on mount/return, nested modals |
| `/packages/components/src/tabs/test/index.tsx` | Unit: Tab roles/semantics, keyboard navigation, roving tabindex |
| `/packages/components/src/menu/test/index.tsx` | Unit: WAI-ARIA menu pattern, keyboard interactions, submenus |
| `/packages/components/src/composite/test/index.tsx` | Unit: Roving tabindex, disabled items, accessibleWhenDisabled |
| `/packages/components/src/button/test/index.tsx` | Unit: aria-pressed, aria-disabled, aria-label, tooltip behavior |
| `/packages/components/src/tooltip/test/index.tsx` | Unit: aria-describedby management, keyboard/hover show/hide |
| `/packages/components/src/notice/test/index.tsx` | Unit: speak() mock, politeness levels, re-announcement prevention |
| `/packages/components/src/snackbar/test/index.tsx` | Unit: speak() mock, dismiss semantics (role=button vs. explicit close) |
| `/packages/components/src/combobox-control/test/index.tsx` | Unit: Combobox keyboard, aria-live selection announcement, reset focus |
| `/packages/components/src/form-token-field/test/index.tsx` | Unit: Dual rendering (AT vs. visual), token position announcements |
| `/packages/components/src/confirm-dialog/test/index.tsx` | Unit: Dialog keyboard (Enter=confirm, Escape=cancel, Tab order) |
| `/packages/components/src/custom-select-control-v2/test/index.tsx` | Unit: aria-selected, keyboard selection, typeahead, multiselect |
| `/packages/components/src/higher-order/with-focus-return/test/index.tsx` | Unit: Focus return on unmount |
| `/packages/components/src/higher-order/with-focus-outside/test/index.tsx` | Unit: Focus outside detection, document.hasFocus() edge case |
