# Accessibility Standards Foundations

> Research document for AI agent consumption. Part of the a11y research series.
> Generated: 2026-02-27 | Session 1, Task 1.1

## 1. WCAG 2.2 -- Actionable Rules for Frontend Development

Target conformance: **Level AA** (covers both Level A and Level AA criteria).

### Perceivable (Principle 1)

#### 1.1.1 Non-text Content (Level A)
- **Rule:** Every non-text element (images, icons, charts, decorative graphics) must have a text alternative that conveys the same information or function.
- **Frontend pattern:** Use `alt` on `<img>`. For decorative images use `alt=""` or `role="presentation"`. For icon buttons, use `aria-label` or visually hidden text. For `<svg>`, use `<title>` plus `role="img"` and `aria-labelledby`.
- **Common violation:** Missing `alt` attribute on `<img>`; icon-only `<button>` with no accessible name; SVGs with no text alternative; CSS background images that convey meaning without a text fallback.
- **Detection:** Check every `<img>` for `alt` attribute presence. Flag `<button>` or `<a>` elements containing only `<svg>` or `<i>` with no `aria-label`, `aria-labelledby`, or `.screen-reader-text` child. Flag `<svg>` without `<title>` or `aria-label`.

#### 1.2.1 Audio-only and Video-only (Prerecorded) (Level A)
- **Rule:** Prerecorded audio-only content needs a text transcript. Prerecorded video-only content needs either a text alternative or an audio description.
- **Frontend pattern:** Provide a `<details>` or linked transcript near the player. For video-only, provide descriptive text or an audio track.
- **Common violation:** Podcast embeds with no transcript link; background videos conveying information with no text alternative.
- **Detection:** Look for `<audio>` or `<video>` elements and check for adjacent transcript links or `<track>` elements.

#### 1.2.2 Captions (Prerecorded) (Level A)
- **Rule:** All prerecorded audio in synchronized media (video with audio) must have captions.
- **Frontend pattern:** Use `<track kind="captions">` on `<video>` elements. For third-party embeds, verify the source provides captions.
- **Common violation:** Videos embedded without caption tracks; auto-generated captions not reviewed for accuracy.
- **Detection:** Check `<video>` elements for `<track kind="captions">`. Flag iframe embeds of video services where caption availability cannot be verified.

#### 1.2.3 Audio Description or Media Alternative (Prerecorded) (Level A)
- **Rule:** Provide an audio description or a full text alternative for the visual information in prerecorded synchronized media.
- **Frontend pattern:** Offer a secondary audio track with descriptions, or provide a full text transcript that includes visual descriptions.
- **Common violation:** Tutorial videos where on-screen actions are never described verbally.
- **Detection:** Flag `<video>` elements that lack `<track kind="descriptions">` and have no adjacent descriptive text.

#### 1.3.1 Info and Relationships (Level A)
- **Rule:** Information, structure, and relationships conveyed visually must be programmatically determinable through semantic markup.
- **Frontend pattern:** Use `<h1>`-`<h6>` for headings (no skipping levels within a section). Use `<ul>`/`<ol>` for lists. Use `<table>` with `<th>` and `scope` for data tables. Use `<label>` associated with `<input>`. Use `<fieldset>`/`<legend>` for related form controls. Use landmark elements (`<header>`, `<nav>`, `<main>`, `<aside>`, `<footer>`).
- **Common violation:** Styled `<div>` or `<span>` used as headings without heading semantics; form inputs without associated `<label>` elements; data presented in grid layout without `<table>` semantics; visual grouping with no programmatic grouping.
- **Detection:** Flag `<div>`/`<span>` with heading-like CSS (large font-size, font-weight: bold) but no heading role. Check every `<input>`, `<select>`, `<textarea>` for an associated `<label>` (via `for`/`id` or wrapping). Flag `<table>` without `<th>`. Flag radio/checkbox groups without `<fieldset>`.

#### 1.3.2 Meaningful Sequence (Level A)
- **Rule:** When the visual order of content conveys meaning, the DOM order must match that reading sequence.
- **Frontend pattern:** Ensure the HTML source order matches the visual presentation order. Avoid using CSS `order`, `flex-direction: row-reverse`, `float`, or `position: absolute` to reorder content in ways that diverge from DOM order.
- **Common violation:** CSS Grid or Flexbox reordering that makes DOM order illogical; tabindex values that create a reading order different from visual order.
- **Detection:** Flag use of CSS `order` property, `flex-direction: row-reverse`, or `grid` with explicit row/column placement that might diverge from source order. Flag positive `tabindex` values (> 0).

#### 1.3.3 Sensory Characteristics (Level A)
- **Rule:** Instructions must not rely solely on shape, color, size, visual location, orientation, or sound to convey meaning.
- **Frontend pattern:** When referencing UI elements in instructions, include text labels in addition to visual descriptions. For example, say "Click the Save button" rather than "Click the green button on the right."
- **Common violation:** "Click the round icon" without identifying it by name; error messages that say "see the red fields" without also marking them with text or icons.
- **Detection:** Scan instructional text for shape/color/location-only references. This requires natural language analysis and is difficult to detect purely from code structure.

#### 1.3.4 Orientation (Level AA)
- **Rule:** Content must not be locked to a single display orientation (portrait or landscape) unless a specific orientation is essential.
- **Frontend pattern:** Avoid CSS or JavaScript that forces orientation via `orientation: portrait` in `@media` queries or the Screen Orientation API. Test layouts in both orientations.
- **Common violation:** CSS `@media (orientation: portrait)` that hides content in landscape; JavaScript that displays "please rotate your device" overlays.
- **Detection:** Search for `screen.orientation.lock()` calls. Search CSS for `@media` queries that set `display: none` based on orientation.

#### 1.3.5 Identify Input Purpose (Level AA)
- **Rule:** Input fields that collect user information (name, email, address, phone, etc.) must have their purpose programmatically identifiable.
- **Frontend pattern:** Use the `autocomplete` attribute with appropriate values from the HTML spec (e.g., `autocomplete="given-name"`, `autocomplete="email"`, `autocomplete="tel"`).
- **Common violation:** Login/registration forms with no `autocomplete` attributes; address forms missing `autocomplete` on street, city, postal code fields.
- **Detection:** Check `<input>` elements with `type="text"`, `type="email"`, `type="tel"` in forms that collect personal data. Flag missing `autocomplete` attribute. Validate that `autocomplete` values match the HTML spec list.

#### 1.4.1 Use of Color (Level A)
- **Rule:** Color must not be the only visual means of conveying information, indicating an action, prompting a response, or distinguishing a visual element.
- **Frontend pattern:** Supplement color with text labels, icons, patterns, or underlines. For links in body text, use underline in addition to color. For form errors, use an icon and text alongside the red highlight.
- **Common violation:** Form validation that only turns the border red with no error text; links distinguishable from surrounding text only by color; charts using only color to differentiate data series.
- **Detection:** Flag `<a>` elements inside text blocks that lack `text-decoration: underline`. Flag form error states that only change `border-color` or `color` without adding error text. Check for `.error` or `.invalid` classes that only set color properties.

#### 1.4.2 Audio Control (Level A)
- **Rule:** If audio plays automatically for more than 3 seconds, provide a mechanism to pause/stop it or control its volume independently of the system volume.
- **Frontend pattern:** Never autoplay audio. If autoplay is required, provide visible pause/mute controls and keep initial duration under 3 seconds.
- **Common violation:** Background music on page load with no controls; video hero banners with audio that autoplays.
- **Detection:** Search for `<audio autoplay>` or `<video autoplay>` without `muted`. Search for JavaScript calls to `.play()` on audio/video elements at page load.

#### 1.4.3 Contrast (Minimum) (Level AA)
- **Rule:** Text must have a contrast ratio of at least 4.5:1 against its background. Large text (18pt or 14pt bold) requires at least 3:1.
- **Frontend pattern:** Verify contrast ratios for all text/background combinations. Use CSS custom properties for colors to make systematic checking easier. Large text is defined as 24px regular or 18.66px bold.
- **Common violation:** Light gray text on white backgrounds; white text on light-colored hero images; placeholder text with insufficient contrast.
- **Detection:** Extract all `color` and `background-color` pairs from CSS. Calculate contrast ratios. Flag ratios below 4.5:1 for normal text and below 3:1 for large text. Pay special attention to `::placeholder` styles.

#### 1.4.4 Resize Text (Level AA)
- **Rule:** Text must be resizable up to 200% without loss of content or functionality, without requiring assistive technology.
- **Frontend pattern:** Use relative units (`rem`, `em`, `%`) for font sizes instead of `px`. Avoid fixed-height containers for text content. Test at 200% browser zoom.
- **Common violation:** Fixed-height containers that clip text when zoomed; content that overflows and becomes unreadable; horizontal scrollbars that prevent reading.
- **Detection:** Flag `font-size` declarations using `px`. Flag containers with `overflow: hidden` combined with fixed `height` in `px` that contain text. Flag `max-height` with `px` on text containers.

#### 1.4.5 Images of Text (Level AA)
- **Rule:** Use real text instead of images of text, except where a particular visual presentation is essential (e.g., logos).
- **Frontend pattern:** Use CSS for text styling (gradients, shadows, custom fonts) rather than rasterized images. Use web fonts and CSS `text-shadow`, `background-clip: text`, or SVG text for decorative typography.
- **Common violation:** Hero banners with baked-in text as part of the image; buttons rendered as images; navigation items as image maps.
- **Detection:** Flag `<img>` elements where the `alt` text is longer than a few words and appears to be a sentence or heading (suggests the image contains text). Flag image files named like "banner-text.png" or "heading.jpg".

#### 1.4.10 Reflow (Level AA)
- **Rule:** Content must reflow to a single column at 320 CSS pixels wide (equivalent to 400% zoom on a 1280px viewport) without horizontal scrolling or loss of information. Exception: content that requires 2D layout (data tables, maps, diagrams).
- **Frontend pattern:** Use responsive CSS (Flexbox, Grid) with `max-width` and relative units. Avoid fixed-width layouts. Test at 320px viewport width.
- **Common violation:** Horizontal scrolling at narrow widths; content cut off or overlapping; fixed-width containers wider than 320px.
- **Detection:** Search for fixed `width` values greater than 320px on content containers. Flag `overflow-x: scroll` or `overflow-x: auto` on main content areas. Flag `min-width` values greater than 320px.

#### 1.4.11 Non-text Contrast (Level AA)
- **Rule:** UI components (form controls, focus indicators) and meaningful graphical objects must have at least 3:1 contrast ratio against adjacent colors.
- **Frontend pattern:** Ensure form field borders, button outlines, custom checkboxes/radios, icons, and chart elements meet 3:1 contrast. Ensure focus indicators are visible with sufficient contrast.
- **Common violation:** Light gray form field borders on white backgrounds; custom toggles/switches with low contrast; icons that are too faint; focus outlines with insufficient contrast.
- **Detection:** Check `border-color` on `<input>`, `<select>`, `<textarea>` against their `background-color`. Check `outline-color` or `box-shadow` used for focus styles against the surrounding background. Flag SVG `fill`/`stroke` colors with low contrast.

#### 1.4.12 Text Spacing (Level AA)
- **Rule:** No loss of content or functionality when users override text spacing to: line-height 1.5x font size, paragraph spacing 2x font size, letter-spacing 0.12em, word-spacing 0.16em.
- **Frontend pattern:** Do not use fixed heights on text containers. Avoid `overflow: hidden` on elements that contain text. Use relative units for spacing. Test with a text-spacing bookmarklet.
- **Common violation:** Buttons with fixed height that clip text when letter-spacing is increased; cards with `overflow: hidden` that lose content when line-height grows; truncation that loses critical information.
- **Detection:** Flag elements with both `overflow: hidden` and fixed `height`/`max-height` that contain text. Flag elements with `white-space: nowrap` combined with `overflow: hidden` and `text-overflow: ellipsis` on essential content (truncation may hide information under modified spacing).

#### 1.4.13 Content on Hover or Focus (Level AA)
- **Rule:** Additional content that appears on hover or focus must be: (1) dismissible without moving the pointer (usually via Escape), (2) hoverable (the user can move the pointer to the new content without it disappearing), and (3) persistent (stays visible until the user dismisses it, moves focus/pointer, or the information is no longer valid).
- **Frontend pattern:** For tooltips and popovers, use `aria-describedby` or `role="tooltip"`. Ensure the tooltip remains visible when the user hovers over it. Add Escape key to dismiss. Avoid `title` attribute for critical information.
- **Common violation:** Tooltips that disappear when the user moves toward them; hover content that cannot be dismissed without moving pointer/focus; content that disappears on a timer.
- **Detection:** Search for CSS `:hover` rules that toggle `display` or `visibility`. Check if the revealed content element itself has hover/focus handlers to keep it visible. Search for JavaScript `mouseenter`/`mouseleave` handlers on tooltips and verify they include the revealed content area.

### Operable (Principle 2)

#### 2.1.1 Keyboard (Level A)
- **Rule:** All functionality must be operable through a keyboard interface, with no requirement for specific timing of keystrokes. Exception: functions that require analog, path-dependent input (e.g., freehand drawing).
- **Frontend pattern:** Use native interactive elements (`<button>`, `<a>`, `<input>`, `<select>`) which have built-in keyboard support. For custom widgets, implement keyboard handlers for Enter, Space, Arrow keys as appropriate per ARIA APG patterns. Never rely solely on `click`, `mousedown`, `mouseenter` events.
- **Common violation:** `<div onclick>` or `<span onclick>` without `role="button"`, `tabindex="0"`, and keyboard event handlers; drag-and-drop without keyboard alternative; custom dropdowns that only respond to mouse.
- **Detection:** Flag `<div>` and `<span>` elements with `onclick`/`@click` handlers but no `role`, `tabindex`, or `onkeydown`/`onkeyup` handlers. Flag custom interactive components built with non-interactive elements. Check for `mousedown`/`mouseup`/`mouseover` handlers without corresponding `keydown`/`keyup`/`focus` handlers.

#### 2.1.2 No Keyboard Trap (Level A)
- **Rule:** If keyboard focus can be moved to a component, focus must be movable away using the keyboard alone. If non-standard keys are needed to leave, the user must be informed.
- **Frontend pattern:** For modals, trap focus within the modal but allow Escape to close. For complex widgets, ensure Tab can exit the component. Test by tabbing through every interactive element.
- **Common violation:** Modals that do not release focus on close; custom widgets that consume all keyboard events; infinite tab loops in carousels.
- **Detection:** Search for focus-trap implementations and verify they include an exit mechanism (Escape handler, close button). Check modal components for `keydown` handlers that call `preventDefault()` on Tab without allowing Escape to exit.

#### 2.1.4 Character Key Shortcuts (Level A)
- **Rule:** If a single-character keyboard shortcut exists, it must be possible to turn it off, remap it to include a modifier key, or it must only be active when the relevant component has focus.
- **Frontend pattern:** Bind shortcuts to modifier+key combinations (Ctrl+K, not just K). If single-character shortcuts are used, provide a settings mechanism to disable or remap them.
- **Common violation:** Single-letter keyboard shortcuts that fire globally (not scoped to a focused component); shortcuts that conflict with screen reader or voice control commands.
- **Detection:** Search for global `keydown`/`keypress` event listeners that check for single character keys without modifier keys (`event.ctrlKey`, `event.altKey`, `event.metaKey`).

#### 2.2.1 Timing Adjustable (Level A)
- **Rule:** For time limits, users must be able to turn off, adjust, or extend the time (with at least 20 seconds warning, at least 10 times). Exceptions: real-time events, essential limits, limits over 20 hours.
- **Frontend pattern:** For session timeouts, warn the user before expiry with a dialog that allows extending. For timed quizzes, provide an option to disable the timer. For auto-advancing carousels, provide pause controls.
- **Common violation:** Session timeouts with no warning; auto-advancing carousels with no pause; countdown timers with no extension option.
- **Detection:** Search for `setTimeout`/`setInterval` that trigger navigation, content changes, or session expiry. Check for auto-advancing logic without associated pause controls.

#### 2.2.2 Pause, Stop, Hide (Level A)
- **Rule:** For moving, blinking, or scrolling content that starts automatically and lasts more than 5 seconds, users must be able to pause, stop, or hide it. For auto-updating content, users must be able to pause, stop, hide, or control the update frequency.
- **Frontend pattern:** Add pause/stop controls to carousels, marquees, animated banners, and auto-refreshing feeds. Respect `prefers-reduced-motion` media query.
- **Common violation:** Infinite CSS animations with no pause; auto-scrolling testimonials with no controls; live feeds that update without user control.
- **Detection:** Search for CSS `animation` with `infinite` iteration count. Search for `setInterval` that updates DOM content. Flag carousels/sliders without pause buttons. Check for `@media (prefers-reduced-motion: reduce)` usage.

#### 2.3.1 Three Flashes or Below Threshold (Level A)
- **Rule:** Content must not flash more than three times per second, or the flash must be below the general flash and red flash thresholds.
- **Frontend pattern:** Avoid flashing content entirely. If flashing is necessary, keep it below 3 flashes per second and ensure the flashing area is small (less than 25% of 10 degrees of the visual field).
- **Common violation:** Rapidly blinking CSS animations; video content with strobing effects; notification badges with fast blink animations.
- **Detection:** Search for CSS `animation` with very short durations (< 333ms cycle) and `alternate`/`steps` timing. Flag `@keyframes` that toggle between significantly different colors.

#### 2.4.1 Bypass Blocks (Level A)
- **Rule:** Provide a mechanism to skip blocks of content repeated across pages (e.g., navigation, headers).
- **Frontend pattern:** Add a skip link as the first focusable element: `<a href="#main-content" class="screen-reader-text">Skip to content</a>`. Use landmark regions (`<header>`, `<nav>`, `<main>`, `<footer>`) so screen reader users can navigate by landmarks.
- **Common violation:** No skip link; skip link that does not become visible on focus; skip link target (`#main-content`) that does not exist; no landmark regions.
- **Detection:** Check for an `<a>` element with `href` starting with `#` near the top of `<body>` that links to an anchor on `<main>` or a main content area. Verify the target ID exists. Check for `<main>` landmark element.

#### 2.4.2 Page Titled (Level A)
- **Rule:** Web pages must have titles that describe their topic or purpose.
- **Frontend pattern:** Set a descriptive `<title>` in `<head>`. For SPAs, update `document.title` on route changes. Include the page-specific title before the site name (e.g., "Settings - My App").
- **Common violation:** Generic titles like "Home" or just the site name on every page; SPA route changes that do not update the title.
- **Detection:** Check for `<title>` element in `<head>`. For SPAs, check that route change handlers update `document.title`. Flag identical titles across multiple routes.

#### 2.4.3 Focus Order (Level A)
- **Rule:** When navigation order affects meaning or operation, focusable components must receive focus in an order that preserves meaning and operability.
- **Frontend pattern:** Keep DOM order aligned with visual layout order. Do not use positive `tabindex` values (they override natural tab order). For dynamically inserted content (modals, notifications), manage focus programmatically to the new content.
- **Common violation:** Positive `tabindex` values creating unexpected tab order; modals that do not receive focus when opened; dynamically added content that is unreachable by keyboard.
- **Detection:** Flag any `tabindex` value greater than 0. Check that modal/dialog open handlers move focus to the dialog. Verify that dynamically injected interactive content is reachable.

#### 2.4.4 Link Purpose (In Context) (Level A)
- **Rule:** The purpose of each link must be determinable from the link text alone, or from the link text together with its programmatically associated context (enclosing sentence, list item, table cell, or heading).
- **Frontend pattern:** Use descriptive link text: "Read the accessibility report" instead of "click here". For repeated "Read more" links, add `aria-label` or visually hidden text with context: `<a href="...">Read more<span class="screen-reader-text"> about accessibility</span></a>`.
- **Common violation:** "Click here", "Read more", "Learn more" links without additional context; URLs as link text; icon-only links without accessible names.
- **Detection:** Flag `<a>` elements whose visible text content is only generic phrases: "click here", "read more", "learn more", "here", "link", "more". Flag `<a>` elements with no text content and no `aria-label`.

#### 2.4.5 Multiple Ways (Level AA)
- **Rule:** Provide more than one way to locate a page within a set of pages (e.g., navigation menu, search, site map, table of contents).
- **Frontend pattern:** Include at least two of: global navigation, search functionality, sitemap, table of contents, or breadcrumbs.
- **Common violation:** Single-page sites within a larger set that are only reachable through one navigation path; no search functionality on content-heavy sites.
- **Detection:** Check for the presence of `<nav>` elements plus at least one of: search form (`<form role="search">` or `<search>`), sitemap link, or breadcrumb navigation.

#### 2.4.6 Headings and Labels (Level AA)
- **Rule:** Headings and labels must describe the topic or purpose of the content they introduce.
- **Frontend pattern:** Write descriptive headings that summarize the section content. Write form labels that clearly describe the expected input. Avoid vague headings like "Section 1" or labels like "Field 1".
- **Common violation:** Generic headings that do not describe content; form labels that are ambiguous or missing context; icon-only labels.
- **Detection:** Flag headings (`<h1>`-`<h6>`) with very short or generic text (single words like "Info", "Details", "More"). Flag `<label>` elements with generic text. This requires semantic analysis.

#### 2.4.7 Focus Visible (Level AA)
- **Rule:** Any keyboard-operable user interface must have a visible keyboard focus indicator.
- **Frontend pattern:** Never use `outline: none` or `outline: 0` without providing a replacement focus style. Use `:focus-visible` to show focus styles only for keyboard users. Ensure focus indicators have at least 3:1 contrast against the background.
- **Common violation:** Global `*:focus { outline: none }` resets without replacements; focus styles that are only a color change with insufficient contrast; focus indicators hidden behind other elements.
- **Detection:** Search for `outline: none`, `outline: 0`, or `outline-style: none` in CSS, especially on `*`, `a`, `button`, `input`. Verify that a `:focus` or `:focus-visible` replacement style exists. Flag `:focus { outline: none }` without an adjacent rule providing a visible alternative.

#### 2.4.11 Focus Not Obscured (Minimum) (Level AA) -- New in WCAG 2.2
- **Rule:** When a UI component receives keyboard focus, it must not be entirely hidden by author-created content (sticky headers, footers, overlays, notifications).
- **Frontend pattern:** Ensure sticky/fixed-position elements do not cover the focused element. Use `scroll-padding-top` or `scroll-margin-top` to account for sticky headers. When opening overlays or banners, check if they obscure the current focus target.
- **Common violation:** Sticky headers or cookie banners that cover focused elements when tabbing through content below them; chat widgets overlapping footer links.
- **Detection:** Search for `position: fixed` or `position: sticky` elements. Check if they have `z-index` values that could overlay main content areas. Flag fixed-position elements at `top: 0` or `bottom: 0` without corresponding `scroll-padding` on the document.

#### 2.5.1 Pointer Gestures (Level A)
- **Rule:** Functionality that uses multipoint (pinch, multi-finger) or path-based (swiping, dragging) gestures must also be operable with a single-point activation without a path-based gesture (e.g., a single tap or click), unless the multipoint/path gesture is essential.
- **Frontend pattern:** For pinch-to-zoom, provide zoom buttons (+/-). For swipe carousels, provide next/previous buttons. For map pan, provide arrow buttons or keyboard controls.
- **Common violation:** Image carousels only navigable by swipe; maps only navigable by touch gestures; custom components requiring multi-finger gestures.
- **Detection:** Search for touch event handlers (`touchstart`, `touchmove`, `touchend`, pointer events with multi-touch) and verify that single-click alternatives exist (buttons, links) for the same functionality.

#### 2.5.2 Pointer Cancellation (Level A)
- **Rule:** For single-pointer activation, at least one of: the down-event is not used to execute the function; completion is on the up-event with an abort/undo mechanism; the up-event reverses the down-event; completing on the down-event is essential.
- **Frontend pattern:** Use `click` events (which fire on up-event) rather than `mousedown`/`pointerdown` for actions. If `mousedown` is used, allow cancellation by moving the pointer off the target before releasing.
- **Common violation:** Actions triggered on `mousedown`/`pointerdown`/`touchstart` with no ability to cancel; submit buttons that fire on pointer down.
- **Detection:** Search for `mousedown`, `pointerdown`, or `touchstart` event handlers that trigger actions (navigation, submission, state changes) without corresponding cancel-on-leave logic.

#### 2.5.3 Label in Name (Level A)
- **Rule:** For UI components with visible text labels, the accessible name must contain the visible text. The visible label text should appear at the start of the accessible name when possible.
- **Frontend pattern:** Ensure `aria-label` includes the visible text of the element. For example, if a button shows "Search", do not set `aria-label="Find products"` -- use `aria-label="Search products"` instead.
- **Common violation:** `aria-label` that does not contain the visible text of the element; `aria-labelledby` pointing to text that differs from the visible label; icon buttons with visible text "Close" but `aria-label="Dismiss dialog"`.
- **Detection:** For elements with both visible text content and `aria-label`, check if the `aria-label` value contains the visible text string. Flag mismatches.

#### 2.5.4 Motion Actuation (Level A)
- **Rule:** Functionality triggered by device motion (shaking, tilting) or user motion must also be operable via standard UI components, and motion actuation must be disableable (unless motion is essential, e.g., pedometer).
- **Frontend pattern:** For "shake to undo" features, provide an undo button. For tilt-to-scroll, provide scroll buttons. Include a setting to disable motion-based features.
- **Common violation:** Shake-to-undo with no button alternative; tilt-based game controls with no on-screen alternative.
- **Detection:** Search for `DeviceMotionEvent`, `DeviceOrientationEvent`, or accelerometer API usage. Verify that UI button alternatives exist for the same functionality.

#### 2.5.7 Dragging Movements (Level AA) -- New in WCAG 2.2
- **Rule:** Any functionality that uses dragging must also be achievable with a single pointer without dragging (e.g., click-based alternative), unless dragging is essential.
- **Frontend pattern:** For drag-to-reorder lists, provide up/down buttons or a "move to position" dialog. For sliders, allow click-on-track or numeric input. For drag-and-drop file uploads, provide a file picker button.
- **Common violation:** Sortable lists only operable by drag; kanban boards with no click-based move mechanism; slider controls with no keyboard or click alternative.
- **Detection:** Search for drag event handlers (`dragstart`, `drag`, `dragend`, `drop`) and drag-and-drop libraries (e.g., `react-dnd`, `sortablejs`, `@dnd-kit`). Verify that non-drag alternatives exist for each draggable interaction.

#### 2.5.8 Target Size (Minimum) (Level AA) -- New in WCAG 2.2
- **Rule:** Interactive targets must be at least 24x24 CSS pixels, unless: the target has sufficient spacing from other targets, an equivalent alternative meets the size requirement, the target is inline in text, the user agent controls the size, or the size is essential.
- **Frontend pattern:** Set `min-width: 24px; min-height: 24px` on interactive elements. For smaller targets (icons, inline links), ensure adequate spacing to meet the equivalent spacing exception. Use `padding` to enlarge touch targets beyond their visible content.
- **Common violation:** Small icon buttons (e.g., 16x16px close buttons); tightly packed action icons in toolbars; small checkboxes/radios without adequate padding.
- **Detection:** Check `width`, `height`, `min-width`, `min-height`, and `padding` on interactive elements (`<button>`, `<a>`, `<input>`, `[role="button"]`). Flag elements where the computed target size could be less than 24x24px.

### Understandable (Principle 3)

#### 3.1.1 Language of Page (Level A)
- **Rule:** The default human language of the page must be programmatically determinable.
- **Frontend pattern:** Set `lang` attribute on the `<html>` element: `<html lang="en">`. Use a valid BCP 47 language tag.
- **Common violation:** Missing `lang` attribute on `<html>`; incorrect language code (e.g., `lang="english"` instead of `lang="en"`).
- **Detection:** Check that the `<html>` element has a `lang` attribute. Validate that the value is a recognized BCP 47 language tag.

#### 3.1.2 Language of Parts (Level AA)
- **Rule:** The language of each passage or phrase that differs from the page's default language must be programmatically determinable.
- **Frontend pattern:** Wrap foreign-language text in an element with the appropriate `lang` attribute: `<span lang="fr">bonjour</span>`.
- **Common violation:** Foreign-language quotes or terms without `lang` attributes; multilingual pages without per-section language marking.
- **Detection:** This requires language detection capabilities. Flag known foreign-language patterns (e.g., common non-English words) without `lang` attributes. Check for `lang` attributes on `<blockquote>` or `<q>` elements containing foreign-language content.

#### 3.2.1 On Focus (Level A)
- **Rule:** Receiving focus must not trigger a change of context (page navigation, form submission, focus move, significant content change).
- **Frontend pattern:** Never auto-submit forms, navigate, or open new windows on focus. Focus events should only trigger visual changes (highlighting, revealing supplementary information within the same context).
- **Common violation:** Select menus that navigate on focus change; input fields that submit on focus; elements that open new windows when focused.
- **Detection:** Search for `focus` and `focusin` event handlers that trigger navigation (`window.location`, `router.push`), form submission, or `window.open`.

#### 3.2.2 On Input (Level A)
- **Rule:** Changing a form control's value must not automatically trigger a change of context unless the user has been warned beforehand.
- **Frontend pattern:** Do not auto-submit forms on input change. Do not navigate on select change. If auto-submission is needed, warn the user before the control: "Changing this option will reload the page."
- **Common violation:** `<select onchange>` that navigates to a new page; radio buttons that submit the form on selection; search inputs that navigate on every keystroke.
- **Detection:** Search for `change` event handlers on `<select>`, `<input>`, and `<textarea>` that trigger navigation or form submission. Flag `<select>` elements with `onchange` handlers that include navigation logic.

#### 3.2.3 Consistent Navigation (Level AA)
- **Rule:** Navigation mechanisms repeated across pages must appear in the same relative order each time, unless the user initiates a change.
- **Frontend pattern:** Keep the primary navigation, breadcrumbs, and footer links in a consistent order across all pages/views. Shared layout components should render navigation identically.
- **Common violation:** Navigation items that reorder between pages; sidebar menus that change position; inconsistent header layouts across routes.
- **Detection:** This is best detected by comparing navigation structures across multiple pages. In a component-based architecture, verify that shared navigation components are used consistently without per-page overrides that change item order.

#### 3.2.4 Consistent Identification (Level AA)
- **Rule:** Components with the same functionality must be identified consistently across pages (same labels, icons, and text).
- **Frontend pattern:** Use the same label text for the same action everywhere. If "Search" is used on one page, do not use "Find" on another. Maintain a design system with consistent component naming.
- **Common violation:** A search icon labeled "Search" on one page and "Find" on another; a close button labeled "Close" in one modal and "Dismiss" in another; inconsistent icon usage for the same action.
- **Detection:** Search for components with similar functionality (search forms, close buttons, submit buttons) and compare their accessible names across instances. Flag inconsistencies.

#### 3.2.6 Consistent Help (Level A) -- New in WCAG 2.2
- **Rule:** If help mechanisms (contact information, human contact, self-help options, automated contact) are provided on multiple pages, they must appear in the same relative order on each page.
- **Frontend pattern:** Place help links, chat widgets, and contact information in consistent locations (e.g., always in the footer, always in the same position within a sidebar). Use shared layout components.
- **Common violation:** Help chat widget that appears in different positions on different pages; contact links sometimes in the header, sometimes in the footer.
- **Detection:** Search for help-related components (chat widgets, "Contact us" links, FAQ links) and verify they appear in the same structural position across page templates.

#### 3.3.1 Error Identification (Level A)
- **Rule:** When an input error is automatically detected, the item in error must be identified and the error described to the user in text.
- **Frontend pattern:** Show error messages near the invalid field. Associate errors with the field using `aria-describedby` or `aria-errormessage`. Use `aria-invalid="true"` on the field. Announce errors to screen readers with `aria-live="assertive"` or `role="alert"`.
- **Common violation:** Error indication only through color change (red border); error messages not associated with the field; error messages not announced to screen readers; vague "An error occurred" messages.
- **Detection:** Search for form validation logic. Check that error states set `aria-invalid="true"` on the field and that error message elements are associated via `aria-describedby`. Check that error messages contain descriptive text (not just icons or color changes).

#### 3.3.2 Labels or Instructions (Level A)
- **Rule:** Labels or instructions must be provided when content requires user input.
- **Frontend pattern:** Every form control must have a visible `<label>` associated via `for`/`id`. Provide placeholder text only as supplementary hint, not as the sole label. Group related fields with `<fieldset>` and `<legend>`. Indicate required fields with text (not just asterisk).
- **Common violation:** `<input>` with `placeholder` as the only label; required fields indicated only by asterisk with no legend explaining the convention; radio/checkbox groups without `<fieldset>`/`<legend>`.
- **Detection:** Check every `<input>`, `<select>`, `<textarea>` for an associated `<label>` or `aria-label`/`aria-labelledby`. Flag elements where `placeholder` is the only identifying text. Check that required field conventions are explained.

#### 3.3.3 Error Suggestion (Level AA)
- **Rule:** When an input error is detected and suggestions for correction are known, the suggestions must be provided to the user (unless it would jeopardize security or purpose).
- **Frontend pattern:** Provide specific, actionable error messages: "Enter a valid email address (e.g., user@example.com)" instead of "Invalid input". For constrained fields, specify the format: "Date must be in MM/DD/YYYY format."
- **Common violation:** Generic error messages like "Invalid value" or "Error"; password fields that say "Invalid password" without explaining the requirements; date fields that say "Invalid date" without showing the expected format.
- **Detection:** Search for error message strings in the codebase. Flag generic messages that do not include format hints or suggestions. Check that validation error messages reference the expected format or constraints.

#### 3.3.4 Error Prevention (Legal, Financial, Data) (Level AA)
- **Rule:** For pages that cause legal, financial, or data commitments: submissions must be reversible, data must be checked and the user given a chance to correct, or a confirmation step must be provided.
- **Frontend pattern:** Provide a review/confirmation step before final submission of orders, contracts, or account changes. Allow users to review entered data and go back to correct it. Provide undo for irreversible deletions.
- **Common violation:** One-click purchase with no confirmation; account deletion with no confirmation dialog; form submission with no review step for financial transactions.
- **Detection:** Search for form submission handlers related to purchases, payments, deletions, or account changes. Verify that a confirmation step or review page is present in the flow.

#### 3.3.7 Redundant Entry (Level A) -- New in WCAG 2.2
- **Rule:** Information previously entered by the user in the same session must not need to be re-entered, unless: re-entering is essential for security, the previous information is no longer valid, or the user chose to re-enter.
- **Frontend pattern:** Auto-populate fields with previously entered data in multi-step forms. Offer "same as billing address" checkboxes. Pre-fill confirmed information.
- **Common violation:** Multi-step checkout asking for the same address twice; registration flows re-asking for email; wizards that lose data on back-navigation.
- **Detection:** In multi-step form flows, check if the same field names or types appear in multiple steps without being pre-populated. Flag multi-step forms that do not carry forward previously entered data.

#### 3.3.8 Accessible Authentication (Minimum) (Level AA) -- New in WCAG 2.2
- **Rule:** Login processes must not require a cognitive function test (memorizing a password, recognizing images, solving puzzles) unless: an alternative authentication method is available, a mechanism helps the user complete the test (e.g., paste support), or the test recognizes common objects/images.
- **Frontend pattern:** Support paste in password fields (do not block `paste` event). Support password managers by using correct `autocomplete` attributes (`autocomplete="current-password"`, `autocomplete="username"`). Support passkeys/WebAuthn as an alternative. Do not use CAPTCHAs as the sole authentication method.
- **Common violation:** Password fields with `oncopy` or `onpaste` handlers that call `preventDefault()`; login forms without `autocomplete` attributes; CAPTCHA-only authentication with no alternative.
- **Detection:** Check password `<input>` elements for `onpaste` handlers that prevent pasting. Check for `autocomplete="current-password"` and `autocomplete="username"` on login forms. Search for CAPTCHA integrations and verify alternatives exist.

### Robust (Principle 4)

#### 4.1.2 Name, Role, Value (Level A)
- **Rule:** For all user interface components, the name, role, and state must be programmatically determinable. States, properties, and values that can be set by the user must be settable programmatically. Notification of changes must be available to user agents, including assistive technologies.
- **Frontend pattern:** Use native HTML elements with implicit roles (`<button>`, `<input type="checkbox">`, `<select>`). When custom components are necessary, provide explicit ARIA `role`, `aria-label`/`aria-labelledby`, and state attributes (`aria-expanded`, `aria-checked`, `aria-selected`, etc.). Update ARIA state attributes dynamically when the component state changes.
- **Common violation:** Custom toggles without `role="switch"` or `aria-checked`; accordion headers without `aria-expanded`; custom select components without `role="listbox"` and `role="option"`; tab interfaces without `role="tablist"`, `role="tab"`, `role="tabpanel"`.
- **Detection:** Search for custom interactive components (divs/spans with click handlers). Verify they have appropriate `role` attributes and ARIA state attributes. Check that state attributes are updated dynamically (search for code that toggles `aria-expanded`, `aria-checked`, etc.).

#### 4.1.3 Status Messages (Level AA)
- **Rule:** Status messages that provide information without changing context must be programmatically determinable through roles or properties, so they can be announced by assistive technologies without receiving focus.
- **Frontend pattern:** Use `role="status"` for general status updates (e.g., "3 results found"). Use `role="alert"` for urgent messages (e.g., "Session expired"). Use `aria-live="polite"` for non-urgent updates and `aria-live="assertive"` for urgent ones. In WordPress, use `wp.a11y.speak()`.
- **Common violation:** Toast notifications without `role="alert"` or `aria-live`; search result count updates with no live region; "Added to cart" messages not announced; loading state changes not communicated.
- **Detection:** Search for dynamically injected status messages, toast/snackbar components, and inline feedback text. Check that they have `role="status"`, `role="alert"`, or are placed within an `aria-live` region. In WordPress, check that `wp.a11y.speak()` is used for dynamic messages.

## 2. ARIA Usage -- Decision Matrix

### The 5 Rules of ARIA

1. **Use native HTML first.** If an HTML element or attribute provides the semantics and behavior you need, use it instead of adding ARIA. A `<button>` is always better than `<div role="button" tabindex="0">`. ARIA supplements HTML; it does not replace it.

2. **Do not change native semantics unnecessarily.** Do not add ARIA roles that conflict with an element's native semantics. For example, do not add `role="heading"` to a `<button>`. Use the correct HTML element instead of overriding semantics.

3. **All interactive ARIA controls must be keyboard accessible.** If you add `role="button"` to a `<div>`, you must also add `tabindex="0"` and handle `keydown` events for Enter and Space. ARIA changes semantics only; it does not add behavior.

4. **Do not use `role="presentation"` or `aria-hidden="true"` on focusable elements.** Removing an element from the accessibility tree while it remains focusable creates a confusing experience. A screen reader user can Tab to an element that is invisible to their assistive technology. If you need to hide something, also make it non-focusable (`tabindex="-1"` or `display: none`).

5. **All interactive elements must have an accessible name.** Every interactive control must have an accessible name derived from visible text, a `<label>`, `aria-label`, or `aria-labelledby`. An unnamed button or link is useless to assistive technology users.

### When to Use ARIA vs Semantic HTML

| Scenario | Use | Reasoning |
|----------|-----|-----------|
| Standard button | `<button>` | Native keyboard support, focus, and role for free |
| Standard link | `<a href>` | Native navigation, role, and keyboard support |
| Checkbox | `<input type="checkbox">` | Native toggle, form participation, and state |
| Text input | `<input type="text">` with `<label>` | Native label association and form behavior |
| Dropdown select | `<select>` | Native keyboard, mobile optimization, and form participation |
| Radio group | `<fieldset>` + `<legend>` + `<input type="radio">` | Native group labeling and mutual exclusivity |
| Page regions | `<header>`, `<nav>`, `<main>`, `<footer>`, `<aside>` | Native landmark roles without ARIA |
| Tabbed interface | `role="tablist"`, `role="tab"`, `role="tabpanel"` | No native HTML equivalent; ARIA required |
| Accordion | `<details>`/`<summary>` or `<button>` + `aria-expanded` | Prefer `<details>` when adequate; use ARIA when custom behavior is needed |
| Combobox (autocomplete) | `role="combobox"` + `role="listbox"` + `role="option"` | No native HTML equivalent for the full pattern |
| Dialog / Modal | `<dialog>` or `role="dialog"` + `aria-modal="true"` | Prefer `<dialog>` where browser support is sufficient |
| Toggle switch | `<button>` + `role="switch"` + `aria-checked` | No native HTML switch element |
| Tree view | `role="tree"` + `role="treeitem"` | No native HTML equivalent |
| Toolbar | `role="toolbar"` | No native HTML toolbar element |
| Live status messages | `role="status"` or `aria-live="polite"` | No native HTML equivalent for live announcements |
| Alert messages | `role="alert"` or `aria-live="assertive"` | No native HTML equivalent for urgent announcements |
| Progress indicator | `<progress>` or `role="progressbar"` | Prefer native `<progress>` when styling is sufficient |
| Tooltip | `role="tooltip"` + `aria-describedby` | No native HTML tooltip element (do not use `title`) |
| Menu (application) | `role="menu"` + `role="menuitem"` | Only for application-style menus (not navigation). Navigation menus should use `<nav>` with `<ul>`/`<li>`/`<a>` |
| Breadcrumb | `<nav aria-label="Breadcrumb">` + `<ol>` | Native `<nav>` with ARIA label for disambiguation |
| Decorative image | `<img alt="">` or `role="presentation"` | Empty alt is preferred; `role="presentation"` removes semantics |

### Role Categories Quick Reference

| Category | Roles | When to use |
|----------|-------|-------------|
| **Landmark** | `banner`, `complementary`, `contentinfo`, `form`, `main`, `navigation`, `region`, `search` | Define page structure for screen reader navigation. Prefer HTML5 equivalents (`<header>`, `<aside>`, `<footer>`, `<form>`, `<main>`, `<nav>`, `<section>`, `<search>`). Use ARIA landmark roles only when HTML5 elements are insufficient or need disambiguation. |
| **Widget** | `button`, `checkbox`, `combobox`, `gridcell`, `link`, `menuitem`, `menuitemcheckbox`, `menuitemradio`, `option`, `progressbar`, `radio`, `scrollbar`, `searchbox`, `slider`, `spinbutton`, `switch`, `tab`, `textbox`, `treeitem` | For custom interactive controls that cannot be built with native HTML elements. Always pair with keyboard handling and state management. |
| **Composite** (widget containers) | `grid`, `listbox`, `menu`, `menubar`, `radiogroup`, `tablist`, `tree`, `treegrid` | Containers that manage focus among child widgets. Implement arrow key navigation within the container. |
| **Document Structure** | `article`, `columnheader`, `definition`, `directory`, `document`, `feed`, `figure`, `group`, `heading`, `img`, `list`, `listitem`, `math`, `none`/`presentation`, `row`, `rowgroup`, `rowheader`, `table`, `term`, `toolbar` | Annotate content structure when HTML semantics are insufficient. `none`/`presentation` removes semantics from decorative elements. |
| **Live Region** | `alert`, `log`, `marquee`, `status`, `timer` | For dynamically updated content that should be announced by screen readers. `alert` for urgent; `status` for non-urgent; `log` for chat/feed; `timer` for countdowns. |
| **Window** | `alertdialog`, `dialog` | For modal and non-modal dialogs. `alertdialog` for dialogs that require immediate user response. Prefer `<dialog>` HTML element when possible. |

### ARIA States & Properties -- Most Used

| Attribute | Purpose | Valid on | Example |
|-----------|---------|----------|---------|
| `aria-label` | Provides an accessible name when no visible text label exists | Any element | `<button aria-label="Close">X</button>` |
| `aria-labelledby` | Points to one or more element IDs that provide the accessible name (highest precedence in name calculation) | Any element | `<div role="dialog" aria-labelledby="dialog-title">` |
| `aria-describedby` | Points to element IDs that provide supplementary description | Any element | `<input aria-describedby="password-hint">` |
| `aria-hidden="true"` | Removes element and descendants from accessibility tree entirely | Any non-focusable element | `<span aria-hidden="true">decorative icon</span>` |
| `aria-expanded` | Indicates whether a collapsible section is open or closed | `button`, elements controlling expandable content | `<button aria-expanded="false">Show details</button>` |
| `aria-pressed` | Indicates the pressed state of a toggle button | `button` | `<button aria-pressed="true">Bold</button>` |
| `aria-checked` | Indicates checked state for checkboxes, radios, switches | `checkbox`, `radio`, `switch`, `menuitemcheckbox`, `menuitemradio` | `<div role="switch" aria-checked="false">` |
| `aria-selected` | Indicates the selected state in tabs, options, grid cells | `tab`, `option`, `gridcell`, `row`, `treeitem` | `<div role="tab" aria-selected="true">Tab 1</div>` |
| `aria-disabled="true"` | Marks an element as perceivable but not operable | Any interactive element | `<button aria-disabled="true">Submit</button>` |
| `aria-required="true"` | Indicates a form field is mandatory | Form controls (`input`, `select`, `textarea`, `combobox`, etc.) | `<input aria-required="true">` |
| `aria-invalid` | Indicates a validation error on a form field | Form controls | `<input aria-invalid="true" aria-errormessage="err1">` |
| `aria-errormessage` | Points to the element containing the error message for the field | Form controls with `aria-invalid="true"` | `<input aria-invalid="true" aria-errormessage="email-error">` |
| `aria-live` | Declares a region whose content updates should be announced | Container elements (`div`, `section`, etc.) | `<div aria-live="polite">3 results found</div>` |
| `aria-atomic` | Whether the entire live region should be announced or just the change | Elements with `aria-live` | `<div aria-live="polite" aria-atomic="true">` |
| `aria-busy` | Indicates the element is being updated (defer announcements) | Elements with `aria-live` | `<div aria-live="polite" aria-busy="true">Loading...</div>` |
| `aria-controls` | Points to the element(s) controlled by this element | Interactive elements | `<button aria-controls="panel1" aria-expanded="true">` |
| `aria-owns` | Redefines DOM parent-child when visual layout diverges from DOM tree | Container elements | `<div role="tree" aria-owns="branch1 branch2">` |
| `aria-haspopup` | Indicates the element triggers a popup (menu, listbox, tree, grid, or dialog) | `button`, `link`, `menuitem` | `<button aria-haspopup="menu">Options</button>` |
| `aria-current` | Indicates the current item in a set (page, step, location, date, time) | Any element | `<a aria-current="page" href="/about">About</a>` |
| `aria-modal="true"` | Indicates that a dialog is modal (blocks interaction with content outside) | `dialog`, `alertdialog` | `<div role="dialog" aria-modal="true">` |
| `aria-activedescendant` | Manages virtual focus within a composite widget (the container keeps DOM focus; this attribute points to the visually focused descendant) | Composite widgets (`combobox`, `listbox`, `grid`, `tree`, `tablist`) | `<div role="listbox" aria-activedescendant="option-3">` |
| `aria-roledescription` | Overrides the role announcement with a custom string (use sparingly) | Any element with a role | `<div role="region" aria-roledescription="slide">` |
| `aria-keyshortcuts` | Documents keyboard shortcuts for the element | Interactive elements | `<button aria-keyshortcuts="Ctrl+S">Save</button>` |

### Common ARIA Anti-Patterns

| Anti-pattern | Why it's wrong | Correct approach |
|-------------|----------------|-----------------|
| `<div onclick="..." role="button">` without `tabindex="0"` and keyboard handlers | ARIA adds semantics but not behavior. The element is announced as a button but cannot be activated via keyboard. | Use `<button>`. If `<div>` is required: add `tabindex="0"`, handle Enter and Space keydown events. |
| `aria-hidden="true"` on a focusable element | The element is hidden from screen readers but keyboard users can still Tab to it, creating a "ghost" focus trap. | Also add `tabindex="-1"` to remove from tab order, or use `display: none`/`visibility: hidden` instead. |
| `role="presentation"` on a `<table>` used for data | Removes table semantics, making data tables unnavigable for screen reader users. | Only use `role="presentation"` on layout tables. Data tables need their native semantics. |
| Adding `role="button"` to `<button>` | Redundant. Native elements already have implicit roles. Redundant roles can cause screen reader bugs. | Use the native element without explicit ARIA roles. |
| `aria-label` on a `<div>` or `<span>` without a role | `aria-label` is ignored on generic elements by most screen readers. The name is applied but never announced. | Add an appropriate role, or use a semantic element. Only apply `aria-label` to elements with interactive or landmark roles. |
| `aria-label` that differs from visible text on a button/link | Voice control users say the visible text to activate the element, but the accessible name does not match. Fails 2.5.3 Label in Name. | Ensure `aria-label` includes the visible text. Or use `aria-labelledby` pointing to the visible text plus additional context. |
| Using `role="menu"` for site navigation | `role="menu"` is for application-style menus (file menus, context menus) and requires specific keyboard patterns (arrow keys, typeahead). Site navigation is not a menu. | Use `<nav>` with `<ul>`/`<li>`/`<a>` for site navigation. |
| Using `aria-live="assertive"` for routine updates | Assertive interrupts the current screen reader announcement. Overuse desensitizes users and creates a noisy experience. | Use `aria-live="polite"` or `role="status"` for non-urgent updates. Reserve `assertive`/`role="alert"` for errors and time-sensitive warnings. |
| Nesting interactive elements (e.g., `<button>` inside `<a>`) | Creates ambiguous and unpredictable behavior for assistive technology. The accessibility tree cannot properly represent nested interactive controls. | Restructure: one interactive element per clickable area. Use separate elements with clear, distinct purposes. |
| Using `title` attribute as the primary accessible name | `title` is not consistently exposed by screen readers, not accessible to touch devices, and only appears on mouse hover. | Use `aria-label`, `aria-labelledby`, or visible `<label>` text instead. `title` should be supplementary at most. |
| `aria-owns` or `aria-controls` pointing to non-existent IDs | The reference breaks silently. No error is thrown, but the relationship is meaningless. | Verify all IDREF attributes point to existing element IDs in the DOM. |
| Using `role="none"` and `aria-hidden="true"` together | `role="none"` removes semantics; `aria-hidden="true"` removes the entire element from the tree. They serve different purposes and combining them shows confusion about their meaning. | Choose one based on intent: `role="none"` to remove semantics while keeping content visible to assistive tech; `aria-hidden="true"` to fully hide from assistive tech. |

## 3. WordPress-Specific Accessibility Standards

### WP Coding Standards (Distilled)

1. **WCAG 2.2 Level AA is the target.** All code contributed to WordPress core, themes, and plugins must meet WCAG 2.2 at Level AA. This is the baseline, not an aspiration.

2. **Keyboard operability is mandatory.** Every interactive element must be operable via keyboard alone. Tab order must be logical and follow the visual layout.

3. **Visible focus indicators are required.** Never remove focus outlines without providing a visible replacement. WordPress core provides default focus styles; do not override them without a replacement that meets contrast requirements.

4. **Form controls must have associated labels.** Every `<input>`, `<select>`, and `<textarea>` must have an associated `<label>` element. Use `for`/`id` pairing or wrapping. Placeholder text is not a substitute for labels.

5. **Use `screen-reader-text` class for visually hidden text.** WordPress provides a standardized `.screen-reader-text` CSS class for content that should be hidden visually but available to assistive technology (skip links, icon button labels, additional context).

6. **Color must not be the sole indicator.** Error states, required fields, success messages, and links within text must use additional visual indicators beyond color alone.

7. **Comply with ATAG 2.0 for authoring interfaces.** WordPress admin and block editor interfaces must support users in creating accessible content (e.g., prompting for alt text, supporting heading hierarchy in the editor).

8. **Semantic HTML is preferred over ARIA.** Use native HTML elements and attributes before reaching for ARIA. When ARIA is necessary, follow the WAI-ARIA specification.

### WP Requirements Beyond WCAG

1. **`wp.a11y.speak()` for live announcements.** WordPress provides the `wp.a11y.speak( message, ariaLive )` JavaScript utility to announce dynamic content changes. Use it instead of manually creating `aria-live` regions. It creates a status region in the DOM and dispatches announcements.
   - `wp.a11y.speak( 'Settings saved.', 'polite' )` -- for non-urgent updates
   - `wp.a11y.speak( 'Error: invalid email.', 'assertive' )` -- for errors

2. **`.screen-reader-text` CSS pattern.** WordPress defines a specific CSS class with standardized properties:
   ```css
   .screen-reader-text {
     border: 0;
     clip-path: inset(50%);
     height: 1px;
     margin: -1px;
     overflow: hidden;
     padding: 0;
     position: absolute;
     width: 1px;
     word-wrap: normal !important;
   }
   .screen-reader-text:focus {
     background-color: #ddd;
     clip-path: none;
     color: #444;
     display: block;
     font-size: 1em;
     height: auto;
     left: 5px;
     line-height: normal;
     padding: 15px 23px 14px;
     text-decoration: none;
     top: 5px;
     width: auto;
     z-index: 100000;
   }
   ```
   Key details: width/height of 1px (not 0) because some screen readers skip 0-sized elements. `word-wrap: normal` prevents letter-by-letter reading in the 1px space. The `:focus` rule makes skip links visible when tabbed to.

3. **Skip links required in themes.** Every WordPress theme must include a skip link as the first focusable element, targeting `#content` or the main content area.

4. **Heading hierarchy must be logical.** The page should have exactly one `<h1>`. Headings must not skip levels (e.g., `<h2>` to `<h4>` without `<h3>`). In the block editor, the document outline panel helps authors maintain proper heading order.

5. **Admin notices must be accessible.** WordPress admin notices (`<div class="notice notice-success">`) must have appropriate ARIA roles and be announced to screen readers.

6. **Color contrast in admin UI.** WordPress admin color schemes must meet 4.5:1 contrast for normal text and 3:1 for large text. Custom admin themes must meet the same requirements.

7. **Media uploads must prompt for alt text.** The media upload interface must provide and encourage the alt text field. Themes and plugins that display images must respect the alt text stored in the media library.

### Gutenberg / Block Editor Contribution Requirements

1. **Landmark regions are mandatory.** All content in the editor must be contained within landmarks so screen reader users can navigate by region. Use `role="region"` with `aria-label` for custom sections.

2. **Keyboard navigation between regions.** The block editor supports region-based keyboard navigation. Custom blocks must participate in this pattern and not trap keyboard focus.

3. **Block toolbar keyboard access.** Block toolbars must be keyboard accessible. Users must be able to reach toolbar controls from the block content using standard keyboard patterns.

4. **Focus management on block insertion.** When a new block is inserted, focus must move to the new block. When a block is deleted, focus must move to the nearest remaining block.

5. **Custom blocks must be labeled.** Every block must have a descriptive `aria-label` that identifies the block type and its position or content when applicable.

6. **Rich text accessibility.** Rich text controls within blocks must support keyboard-based formatting (Ctrl+B for bold, etc.) and expose formatting state via ARIA attributes.

7. **Sidebar and inspector controls must be labeled.** All block settings in the inspector sidebar must have associated labels and be keyboard navigable.

8. **Test with screen readers.** Accessibility testing for block editor contributions requires testing with at least one screen reader (NVDA on Windows, VoiceOver on macOS).

## 4. Priority Matrix

| Priority | Criteria | Impact |
|----------|----------|--------|
| **P0 (Must fix)** | 1.1.1 Non-text Content, 1.3.1 Info and Relationships, 2.1.1 Keyboard, 2.1.2 No Keyboard Trap, 2.4.3 Focus Order, 2.4.4 Link Purpose, 2.4.7 Focus Visible, 3.3.1 Error Identification, 3.3.2 Labels or Instructions, 4.1.2 Name Role Value | These are the most frequently violated criteria and cause the most severe barriers. Missing keyboard access locks out screen reader users, switch users, and motor-impaired users entirely. Missing names/roles make components invisible or confusing to assistive technology. Missing labels make forms unusable. |
| **P1 (Should fix)** | 1.4.3 Contrast (Minimum), 1.4.11 Non-text Contrast, 1.4.1 Use of Color, 1.4.10 Reflow, 1.4.13 Content on Hover or Focus, 2.4.1 Bypass Blocks, 2.4.2 Page Titled, 2.4.6 Headings and Labels, 2.4.11 Focus Not Obscured, 2.5.3 Label in Name, 2.5.8 Target Size (Minimum), 3.1.1 Language of Page, 3.2.1 On Focus, 3.2.2 On Input, 3.3.3 Error Suggestion, 4.1.3 Status Messages | Contrast failures affect low-vision users (a large population). Reflow failures affect mobile and zoom users. Status messages affect all screen reader users of dynamic interfaces. Target size affects motor-impaired and touch users. These are high-frequency issues that significantly degrade usability. |
| **P2 (Nice to fix)** | 1.3.2 Meaningful Sequence, 1.3.3 Sensory Characteristics, 1.3.4 Orientation, 1.3.5 Identify Input Purpose, 1.4.4 Resize Text, 1.4.5 Images of Text, 1.4.12 Text Spacing, 2.1.4 Character Key Shortcuts, 2.2.1 Timing Adjustable, 2.2.2 Pause Stop Hide, 2.3.1 Three Flashes, 2.4.5 Multiple Ways, 2.5.1 Pointer Gestures, 2.5.2 Pointer Cancellation, 2.5.4 Motion Actuation, 2.5.7 Dragging Movements, 3.1.2 Language of Parts, 3.2.3 Consistent Navigation, 3.2.4 Consistent Identification, 3.2.6 Consistent Help, 3.3.4 Error Prevention, 3.3.7 Redundant Entry, 3.3.8 Accessible Authentication | These criteria address real accessibility needs but are less commonly violated in typical frontend component development, or they apply to specific interaction patterns that may not be present in every component. They are still required for Level AA conformance, but their frequency in component-level code reviews is lower. Prioritize them when the specific pattern applies (e.g., 2.5.7 when implementing drag-and-drop). |
| **Multimedia-specific** | 1.2.1 Audio/Video-only, 1.2.2 Captions, 1.2.3 Audio Description, 1.4.2 Audio Control | Only relevant when the component includes audio or video content. Apply when multimedia is present; skip during component reviews that do not involve media. |
