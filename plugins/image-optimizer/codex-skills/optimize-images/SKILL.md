---
name: optimize-images
description: "Optimize images with measured quality profiles - lossless by default, per-file lossy picks, resizing and format conversion, with a 1:1 review before anything is written"
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


# Optimize Images

**Target:** ${CODEX_SKILL_ARGUMENTS}

Load the `image-optimization` skill and follow its method for the target above. If the target names no files or folders, ask the human which images to work on and what they are for.

Use the `lossless` profile unless the human says the images may change visibly, and write no file before the skill's approval gates are met.
