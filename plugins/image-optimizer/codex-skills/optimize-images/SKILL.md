---
name: optimize-images
description: "Losslessly optimize images (PNG, JPEG, GIF, SVG) with review and confirmation workflow"
---

<!-- GENERATED FILE - DO NOT EDIT -->
<!-- Source: ./commands/optimize-images.md -->

## Codex Host Adapter

This skill is generated from the canonical Claude Code command named above. To execute it in Codex:

1. Treat the text supplied after the skill mention as the invocation arguments. Substitute that exact text for `${CODEX_SKILL_ARGUMENTS}` before executing shell commands.
2. Resolve `CODEX_PLUGIN_ROOT` to the absolute plugin root. The loaded skill directory is `<plugin-root>/codex-skills/<skill-name>`, so the plugin root is two directories above the directory containing this `SKILL.md`.
3. Assign both variables explicitly in any shell call that uses them. Codex does not export these instruction variables automatically.
4. Use Codex's available user-input and subagent tools when the workflow requests them.
5. Follow the canonical workflow below without skipping its gates or artifact checks.

## Canonical Workflow


# Image Asset Optimizer

Lossless image optimization with review -> confirm -> apply workflow.

**Target:** ${CODEX_SKILL_ARGUMENTS} (directory or image file to optimize)

## How It Works

**Raster images (PNG, JPEG, GIF):** Optimizations are fully lossless - file sizes are reduced without any loss in image quality. Uses ImageOptim which applies multiple optimization techniques while preserving every pixel.

**SVG files:** Uses [svgo](https://github.com/svg/svgo), the same optimizer powering [SVGOMG](https://svgomg.net/). The bundled configuration uses web-safe default techniques that safely reduce file size without breaking SVG rendering.

## Prerequisites

```bash
# Raster images (PNG, JPEG, GIF)
npm install -g imageoptim-cli
# ImageOptim.app required: https://imageoptim.com

# SVG optimization
npm install -g svgo
```

## Workflow (MUST FOLLOW)

### Step 1: Optimize and Show Report

Run optimization WITHOUT `--cleanup` to generate and review results:

```bash
CODEX_PLUGIN_ROOT="<absolute plugin root: two directories above the directory containing this SKILL.md>"
"${CODEX_PLUGIN_ROOT}/scripts/optimize-images.sh" "${CODEX_SKILL_ARGUMENTS}" "${CODEX_PLUGIN_ROOT}/scripts/svgo.config.mjs" /tmp/img-optimize
```

Answer **N** when prompted. This preserves the temp directory.

### Step 2: Ask User for Confirmation

**REQUIRED:** After showing the report, ASK THE USER if they want to apply the optimizations. Do NOT proceed without explicit user confirmation.

### Step 3: Apply or Cancel (with cleanup)

Based on user's answer, run WITH `--cleanup`:

**If user confirms YES:**
```bash
CODEX_PLUGIN_ROOT="<absolute plugin root: two directories above the directory containing this SKILL.md>"
echo "y" | "${CODEX_PLUGIN_ROOT}/scripts/optimize-images.sh" --cleanup "${CODEX_SKILL_ARGUMENTS}" "${CODEX_PLUGIN_ROOT}/scripts/svgo.config.mjs" /tmp/img-optimize
```

**If user says NO:**
```bash
CODEX_PLUGIN_ROOT="<absolute plugin root: two directories above the directory containing this SKILL.md>"
echo "n" | "${CODEX_PLUGIN_ROOT}/scripts/optimize-images.sh" --cleanup "${CODEX_SKILL_ARGUMENTS}" "${CODEX_PLUGIN_ROOT}/scripts/svgo.config.mjs" /tmp/img-optimize
```

Both commands clean up the temp directory. The `--cleanup` flag ensures cleanup happens regardless of yes/no.

## Quick Reference

| Option | Description |
|--------|-------------|
| `--cleanup` | Clean up temp directory when done (always cleans up on exit) |
| `--help` | Show usage information |

| Argument | Required | Description |
|----------|----------|-------------|
| `target` | Yes | Directory or image file to optimize |
| `svgo_config` | No | SVGO config file (use `""` to skip) |
| `temp_dir` | No | Temp directory path |

## Report Icons

- ✅ File optimized (size reduced)
- ⬜ Unchanged (already optimal)
- ⚠️ Larger after optimization (will be skipped)

## SVGO Configuration

Bundled config at `${CODEX_PLUGIN_ROOT}/scripts/svgo.config.mjs` uses web-safe defaults aligned with [SVGOMG](https://svgomg.net/):
- Uses `preset-default` with standard optimizations
- Preserves `viewBox` for responsive SVGs
- `multipass: true` for better compression
