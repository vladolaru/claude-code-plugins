---
description: Losslessly optimize images (PNG, JPEG, GIF, SVG) with review and confirmation workflow
allowed-tools: Bash, Read, AskUserQuestion
---

# Image Asset Optimizer

Lossless image optimization with review -> confirm -> apply workflow.

**Target:** $ARGUMENTS (directory or image file to optimize)

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
"${CLAUDE_PLUGIN_ROOT}/scripts/optimize-images.sh" "$ARGUMENTS" "" /tmp/img-optimize
```

Answer **N** when prompted. This preserves the temp directory.

### Step 2: Ask User for Confirmation

**REQUIRED:** After showing the report, ASK THE USER if they want to apply the optimizations. Do NOT proceed without explicit user confirmation.

### Step 3: Apply or Cancel (with cleanup)

Based on user's answer, run WITH `--cleanup`:

**If user confirms YES:**
```bash
echo "y" | "${CLAUDE_PLUGIN_ROOT}/scripts/optimize-images.sh" --cleanup "$ARGUMENTS" "" /tmp/img-optimize
```

**If user says NO:**
```bash
echo "n" | "${CLAUDE_PLUGIN_ROOT}/scripts/optimize-images.sh" --cleanup "$ARGUMENTS" "" /tmp/img-optimize
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

Bundled config at `${CLAUDE_PLUGIN_ROOT}/scripts/svgo.config.mjs` uses web-safe defaults aligned with [SVGOMG](https://svgomg.net/):
- Uses `preset-default` with standard optimizations
- Preserves `viewBox` for responsive SVGs
- `multipass: true` for better compression
