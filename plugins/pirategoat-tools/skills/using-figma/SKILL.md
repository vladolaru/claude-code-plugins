---
name: using-figma
description: Use when implementing UI from Figma designs, comparing implementations against Figma, performing design gap analysis, or planning Figma-based frontend work. Triggers on Figma URLs, node IDs, design-to-code tasks, requests to match a design, or Figma MCP setup and troubleshooting. Requires a working Figma MCP server.
---

## Skill Directory

Resolve `SKILL_DIR` to the absolute directory containing this `SKILL.md`, as
shown by the current host, before using any bundled path below.
Before a shell command uses `$SKILL_DIR`, assign it in that command or replace
it with the resolved path. It is not a host-exported environment variable.

# Using Figma for Implementation

You are a design-to-code specialist. Your job: translate Figma designs into production code with high fidelity by building a structured specification before writing any code.

**Why this workflow matters** — measured across 4 real sessions:
- Survey-first: 0 factual corrections, 6% call waste
- No survey (reactive): 17 user corrections, 18% call waste
- Screenshot-only implementation: 100% of guessed values were wrong
- Sessions without `get_variable_defs`: 4/4 had repeated token mismatches

## When to Use

- Implementing UI from Figma designs (new features or design polish)
- Comparing live implementation against Figma (gap analysis)
- Planning implementation of Figma-based designs
- Any task where a Figma URL or node ID is referenced

## When NOT to Use

- Pure codebase exploration with no Figma reference

## Figma URL Parsing

Extract `fileKey` and `nodeId` from Figma URLs:

**URL format:** `https://figma.com/design/:fileKey/:fileName?node-id=1-2`
- **fileKey:** segment after `/design/`
- **nodeId:** value of `node-id` query param (use as-is, with the hyphen)

Example: `https://figma.com/design/kL9xQn2VwM8pYrTb4ZcHjF/DesignSystem?node-id=42-15`
→ fileKey=`kL9xQn2VwM8pYrTb4ZcHjF`, nodeId=`42-15`

## Asset Handling

All assets come from Figma MCP's built-in endpoint:

- Use `localhost` URLs from Figma MCP responses directly as image/SVG sources
- Source all icons and images exclusively from the Figma payload
- Use the actual asset URL immediately — a localhost source is a ready asset, not a placeholder

## Iron Rules

### Foundation (violations derail the entire session)

**RULE 0:** Call `get_variable_defs` at session start and cache the result. Without it, tokens are discovered ad-hoc → hardcoded values → 2+ user corrections.

**RULE 1:** Build the Design Specification Document (Phase 1) before writing any code. Code from the spec, not from raw Figma responses.

**RULE 2:** Build the component hierarchy and enumerate ALL states (default, hover, disabled, error, empty, loading) before implementation. Missing states are discovered only when the user reports mismatches.

**RULE 3:** Use project design tokens for every spacing, color, and typography value. Check the project's token system first; if no token exists, document it and use the closest match.

### Discipline (violations cause waste or incorrect results)

**RULE 4:** Pair `get_screenshot` with `get_design_context`. Use screenshots for visual reference only — never as sole source for implementation values.

**RULE 5:** Batch Figma MCP calls separately from other tool providers. One Chrome DevTools error in a mixed batch kills all Figma calls (observed: 50% waste from cascade failure).

**RULE 6:** Parse large responses (>20K chars) with scripts instead of reading as text. 88KB-1.5MB blobs consume context; values are missed.

**RULE 7:** When a response is truncated, re-fetch sublayer nodes individually. Workarounds on incomplete data produce incomplete results.

**RULE 8:** Target content frames, not wrapper/title frames. Wrapper frames return trivial content (~700 chars of labels).

## Pre-Action Checkpoints — STOP

Before each action, verify the prerequisite. If the check fails, STOP and complete the prerequisite first.

| Before... | STOP and verify | Do this instead |
|-----------|----------------|-----------------|
| Writing any code | Design spec document exists | Complete Phase 1 (Specification) |
| Using a spacing/color/font value | Value comes from a design token, not a screenshot or raw pixel | Look up token in spec or `get_variable_defs` cache |
| Starting implementation | ALL component states are documented (default, hover, disabled, error, empty) | Complete Phase 2 state inventory |
| Fetching a Figma node | Check if you already have the data cached or parsed | Parse existing response with script |
| Batching tool calls | All calls use the same MCP provider | Separate Figma MCP calls from Chrome DevTools calls |

## Workflow

| Phase | Goal | Output |
|-------|------|--------|
| 0. Survey | Inventory design: tokens, nodes, visual | Node inventory + token cache |
| 1. Specification | Parse design context → spec doc | Design Specification Document |
| 2. Component Tree | Map hierarchy + enumerate states | Component tree + state inventory |
| 3. Implementation | Code from spec, not raw Figma | Working components |
| 4. Validation | Screenshot comparison + checklist | Verified spec |

Backtrack: Phase 4 → Phase 3 (fidelity gap found). Planning sessions: Phase 3 = "write the plan" (see below).

### Plugin Scripts

Some phases use helper scripts bundled with this plugin. Derive the plugin root
from `SKILL_DIR` by going two levels up:

```bash
PLUGIN_ROOT="$SKILL_DIR/../.."
```

### Phase 0: Survey

**Goal:** Build a complete inventory of the design before touching any code.

1. **Fetch token definitions** — Call `get_variable_defs` for the Figma file. Save to `.claude/tmp/figma-cache/tokens-<fileKey>.json`. This gives you the complete design token map (colors, spacing, typography).

2. **Fetch metadata** — Call `get_metadata` on the root node. If the response is >50K chars, save to file and parse with:
   ```bash
   python3 $SKILL_DIR/../../scripts/figma/parse_nodes.py <saved-file> [--format tree|flat|json]
   ```

3. **Classify nodes** — From the parsed hierarchy, identify:
   - Page-level variant frames (full screens/states)
   - Component-level frames (individual UI elements)
   - Wrapper/title frames to SKIP (these return trivial content)

4. **Visual reference** — Take `get_screenshot` of the root or key variant frames for visual orientation.

5. **Check for project config** — Look for `.claude/figma-config.json` in the project root. If present, it contains the Figma-to-project token mapping. If absent, you'll build the mapping in Phase 1.

**Output:** Node inventory, token definitions, visual reference.

### Phase 1: Specification

**Goal:** Transform raw Figma data into a structured Design Specification Document.

1. **Fetch design context** — Call `get_design_context` for each target node. Use parallel calls for independent nodes. **Never use `excludeScreenshot=true` for implementation work** — you need both visual and structural data.

2. **Parse each response** — If a response is >20K chars, save to file and parse with:
   ```bash
   python3 $SKILL_DIR/../../scripts/figma/extract_specs.py <saved-file> [--tokens-file <tokens-cache>]
   ```

3. **Build token mapping** — If no `.claude/figma-config.json` exists, cross-reference Figma token names with the project's CSS variables/design tokens. Write the mapping to `.claude/tmp/figma-cache/token-mapping-<fileKey>.json`.

4. **Write Design Specification Document** - Create `.claude/tmp/figma-specs/<feature-name>.md` using the template in `$SKILL_DIR/references/design-spec-template.md`. This document is the source of truth for implementation - NOT the raw Figma responses.

**Output:** Design Specification Document with normalized spacing, typography, colors, and component structure using project tokens.

### Phase 2: Component Tree

**Goal:** Map the Figma component hierarchy to project components and enumerate all states.

1. **Map hierarchy** — For each Figma component, identify:
   - Which project component it maps to (existing or new)
   - Parent-child spacing relationships (which element owns which padding)
   - Whether the project has a matching design system component

2. **Enumerate states** — For EVERY interactive component, document these states:
   - Default, Hover, Active/Pressed, Focus, Disabled, Loading, Error, Empty

3. **Check Figma for state variants** — Use `get_design_context` or `get_screenshot` on state variants. If states aren't in Figma, document them as "not designed — use project defaults."

4. **Add to specification** — Update the Design Specification Document with the component tree and state inventory.

**Output:** Component tree with parent-child spacing annotations and complete state inventory.

### Figma Output ≠ Final Code

Figma MCP output (React + Tailwind) is a **representation of the design**, not production code. When translating to implementation:
- Replace Tailwind utility classes with the project's design tokens
- Reuse existing project components instead of duplicating
- Respect existing routing, state management, and data-fetch patterns

### Phase 3: Implementation

**Goal:** Code from the specification, not from raw Figma responses.

1. **Work from the spec** — Open and reference the Design Specification Document. Do NOT re-read raw Figma responses.

2. **Token-first** — Every spacing, color, and typography value MUST use a project token. If no token exists, document it and use the closest match.

3. **One component at a time** — Implement, then immediately validate against the spec before moving to the next component.

4. **Check hierarchy** — Before setting any spacing value, verify which element in the parent-child tree owns that spacing. Refer to the component tree from Phase 2.

### Phase 4: Validation

**Goal:** Verify implementation matches the design specification.

1. **Take browser screenshot** — Compare against the Figma screenshot from Phase 0.

2. **Spot-check values** — Use browser DevTools (`evaluate_script`) to verify:
   - Spacing matches spec (check parent AND child elements)
   - Typography matches spec (font-size, weight, line-height)
   - Colors match spec (no hardcoded hex values)

3. **Check all states** — Verify every state from the Phase 2 inventory renders correctly.

4. **Update spec** — Mark each item in the Design Specification Document as verified or note deviations.

## Planning Sessions

For planning/gap-analysis sessions (no code written):

- Phase 0 (Survey) is still required
- Phase 1 (Specification) can use screenshots more heavily since you don't need pixel values
- Phase 2 (Component Tree) should identify existing project components to reuse
- Phase 3 becomes "write the plan" — but INCLUDE design spec references so implementors don't re-fetch
- Phase 4 becomes "decision-critic validation" of the plan

## Project Configuration

The skill looks for `.claude/figma-config.json` in the project root:

```json
{
  "design_system": {
    "token_prefix": "--wpds-",
    "spacing_unit": "calc(N * var(--wpds-dimension-base))",
    "spacing_base_px": 4,
    "components_package": "@automattic/design-system",
    "components_doc": ".claude/docs/wds-components.md"
  },
  "token_mapping": {
    "spacing": {
      "4": "calc(1 * var(--wpds-dimension-base))",
      "8": "calc(2 * var(--wpds-dimension-base))",
      "12": "calc(3 * var(--wpds-dimension-base))",
      "16": "calc(4 * var(--wpds-dimension-base))",
      "20": "calc(5 * var(--wpds-dimension-base))",
      "24": "calc(6 * var(--wpds-dimension-base))"
    },
    "colors": {
      "text-primary": "var(--wpds-color-fg-content-neutral)",
      "text-secondary": "var(--wpds-color-fg-content-neutral-weak)",
      "bg-surface": "var(--wpds-color-bg-surface-neutral)",
      "border": "var(--wpds-color-stroke-surface-neutral)"
    },
    "typography": {
      "xs": "var(--wpds-font-size-xs)",
      "sm": "var(--wpds-font-size-sm)",
      "md": "var(--wpds-font-size-md)",
      "lg": "var(--wpds-font-size-lg)"
    }
  },
  "component_mapping": {
    "Button": { "package": "@automattic/design-system", "variants": ["solid", "outline", "minimal"] },
    "Badge": { "package": "@automattic/design-system", "intents": ["high", "medium", "low", "stable", "informational", "draft", "none"] },
    "Card": { "package": "@wordpress/components", "note": "No DS equivalent yet" }
  },
  "cache_dir": ".claude/tmp/figma-cache"
}
```

If this file doesn't exist, build the mapping from `get_variable_defs` + codebase analysis and save it for future sessions.

## Caching

Design systems don't change often. Cache aggressively:

| What | Where | Invalidation |
|------|-------|-------------|
| Token definitions (`get_variable_defs`) | `.claude/tmp/figma-cache/tokens-<fileKey>.json` | Manual or weekly |
| Token mapping (Figma → project) | `.claude/tmp/figma-cache/token-mapping-<fileKey>.json` | When design system updates |
| Node hierarchy (`get_metadata`) | `.claude/tmp/figma-cache/nodes-<rootNodeId>.json` | When Figma design changes |
| Design spec documents | `.claude/tmp/figma-specs/<feature>.md` | Per implementation session |

**At session start:** Check if cached token data exists. If yes, skip `get_variable_defs` call. If the Figma file version has changed, refresh.

## Script Reference

Scripts are in the plugin's `scripts/` directory:

| Script | Input | Output | When |
|--------|-------|--------|------|
| `figma/parse_nodes.py` | Saved `get_metadata` response file | Structured node hierarchy (tree, flat list, or JSON) | Phase 0, when metadata > 50K chars |
| `figma/extract_specs.py` | Saved `get_design_context` response file | Extracted design specifications (spacing, typography, colors, hierarchy) | Phase 1, when response > 20K chars |

## Figma MCP Tool Catalog

| Tool | Purpose | When to use |
|------|---------|-------------|
| `get_design_context` | Structured design data + React/Tailwind code | Primary tool — always use for implementation |
| `get_variable_defs` | Design token definitions (colors, spacing, typography) | Phase 0 — always at session start |
| `get_metadata` | Sparse XML outline of layer hierarchy | Phase 0 — for large designs before targeted fetches |
| `get_screenshot` | Visual screenshot of a node | Phase 0 + Phase 4 — visual reference and validation |
| `get_code_connect_map` | Figma node → code component mapping | Phase 2 — discover existing component reuse |
| `add_code_connect_map` | Register a Figma node → code component mapping | After implementation — improve future reuse |
| `create_design_system_rules` | Generate design-to-code guidance for your stack | One-time setup — save output for reuse |
| `get_figjam` | FigJam board content (diagrams, flows) | When referencing FigJam boards, not designs |

## Handling Figma MCP Failures

Truncated responses, empty results, and connection errors are expected — Figma MCP operates over a network with large payloads. Apply the recovery action immediately; do not apologize or explain the failure to the user.

| Failure | Recovery |
|---------|----------|
| Truncated response | Save what you received, re-fetch sublayer nodes individually |
| Empty/null response | Retry once. If still empty, use `get_metadata` to verify the node ID is valid, then retry with correct ID |
| MCP connection error | Proceed with cached data if available. If no cache, inform user and pause — do not invent values |
| Unexpected format | Save raw response to file and parse with script — the structure may differ from what you expect |

## Common Anti-Patterns

| Anti-Pattern | What Happens | Fix |
|-------------|-------------|-----|
| **Fetch and Forget** | 88KB response read as text, values missed | Parse with script into spec doc |
| **Token Amnesia** | `get_variable_defs` never called, tokens discovered ad-hoc | Call at session start, cache result |
| **Component Island** | Each element treated independently, spacing confusion | Build hierarchy first (Phase 2) |
| **Screenshot Guessing** | Pixel values guessed from image, wrong | Always pair screenshot with design context |
| **Cascade Batching** | Figma + Chrome calls batched, cascade failure | Separate tool providers into own batches |
| **Reactive Figma** | No survey, issues discovered one-by-one by user | Survey phase mandatory (Phase 0) |
