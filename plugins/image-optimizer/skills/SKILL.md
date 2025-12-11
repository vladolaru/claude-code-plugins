---
name: image-optimizer
description: Use when optimizing images (PNG, JPEG, GIF, SVG) for web/production - losslessly reduces file sizes using imageoptim CLI and svgo with review and confirmation workflow
---

# Image Asset Optimizer

Lossless image optimization with review -> confirm -> apply workflow.

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
"${CLAUDE_PLUGIN_ROOT}/skills/image-optimizer/scripts/optimize-images.sh" <target> "" /tmp/img-optimize
```

Answer **N** when prompted. This preserves the temp directory.

### Step 2: Ask User for Confirmation

**REQUIRED:** After showing the report, ASK THE USER if they want to apply the optimizations. Do NOT proceed without explicit user confirmation.

### Step 3: Apply or Cancel (with cleanup)

Based on user's answer, run WITH `--cleanup`:

**If user confirms YES:**
```bash
echo "y" | "${CLAUDE_PLUGIN_ROOT}/skills/image-optimizer/scripts/optimize-images.sh" --cleanup <target> "" /tmp/img-optimize
```

**If user says NO:**
```bash
echo "n" | "${CLAUDE_PLUGIN_ROOT}/skills/image-optimizer/scripts/optimize-images.sh" --cleanup <target> "" /tmp/img-optimize
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

Bundled config at `${CLAUDE_PLUGIN_ROOT}/skills/image-optimizer/svgo.config.mjs`:
- Uses `preset-default` with standard optimizations
- Preserves `viewBox` for responsive SVGs
- `multipass: true` for better compression