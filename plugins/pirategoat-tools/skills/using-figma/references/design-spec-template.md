# Design Specification: [Feature Name]

**Figma file:** `<fileKey>`
**Root node:** `<nodeId>` — [Description]
**Generated:** YYYY-MM-DD HH:MM
**Last updated:** YYYY-MM-DD HH:MM

## Token Map

### Spacing

| Figma Value | Project Token | Resolved |
|-------------|--------------|----------|
| `4px` | `calc(1 * var(--token-base))` | 4px |
| `8px` | `calc(2 * var(--token-base))` | 8px |

### Typography

| Element | Font Size | Weight | Line Height | Project Token |
|---------|-----------|--------|-------------|---------------|
| Heading | 16px | 600 | 24px | `var(--font-size-lg)` |
| Body | 13px | 400 | 20px | `var(--font-size-md)` |
| Label | 11px | 500 | 16px | `var(--font-size-xs)` |

### Colors

| Usage | Figma Value | Project Token |
|-------|-------------|---------------|
| Primary text | `#1e1e1e` | `var(--color-fg-content-neutral)` |
| Secondary text | `#757575` | `var(--color-fg-content-neutral-weak)` |
| Border | `#e0e0e0` | `var(--color-stroke-surface-neutral)` |

## Component Tree

```
PageRoot
├── Header (padding: token-6)
│   ├── Title (font: heading)
│   └── Actions (gap: token-2)
├── Body (padding: token-5, gap: token-4)
│   ├── Section A
│   │   ├── SectionHeader (font: label, color: secondary)
│   │   └── SectionContent (gap: token-3)
│   └── Section B
│       └── ...
└── Footer (padding: token-4)
```

**Spacing ownership:** (Which element owns which spacing)

| Spacing | Owner Element | Value | Note |
|---------|--------------|-------|------|
| Page top padding | PageRoot | 24px | Card-level padding |
| Header-to-body gap | Body | 20px | NOT header bottom padding |
| Section gap | Body | 16px | Between sibling sections |
| Section internal gap | SectionContent | 12px | Between items in section |

## Component State Inventory

| Component | Default | Hover | Active | Focus | Disabled | Loading | Error |
|-----------|---------|-------|--------|-------|----------|---------|-------|
| Button Primary | solid/brand | darken bg | press effect | ring | muted, accessible | spinner | - |
| Toggle | off/neutral | - | - | ring | keep color, muted | - | - |
| Input | border/neutral | - | - | border/brand | muted bg | - | border/error |

## Sections

### [Section Name]

**Figma node:** `<nodeId>`
**Maps to:** `<ProjectComponent>` (existing / new)

| Property | Figma Value | Project Token |
|----------|-------------|---------------|
| Padding top | 20px | `calc(5 * var(--base))` |
| Padding bottom | 24px | `calc(6 * var(--base))` |
| Background | white | `var(--bg-surface-neutral)` |
| Border | 1px solid #e0e0e0 | `1px solid var(--stroke-neutral)` |
| Border radius | 4px | `var(--border-radius-sm)` |

**Children:**
- [Child A] — `<ChildComponent>`, font: body, color: primary
- [Child B] — `<ChildComponent>`, font: label, color: secondary

**States:**
- Default: [as specified above]
- Hover: [specific changes]
- Disabled: [specific changes — e.g., "toggle keeps blue color"]

## Validation Checklist

- [ ] Layout matches (spacing, alignment, sizing)
- [ ] Typography matches (font, size, weight, line height)
- [ ] Colors match — all using project tokens
- [ ] Interactive states verified (hover, disabled, focus)
- [ ] Component hierarchy matches Figma tree
- [ ] No hardcoded pixel values in CSS
- [ ] Responsive behavior follows Figma constraints
- [ ] Assets render correctly
- [ ] Accessibility standards met
