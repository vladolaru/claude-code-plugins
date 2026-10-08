---
description: Optimize images with measured quality profiles - lossless by default, per-file lossy picks, resizing and format conversion, with a 1:1 review before anything is written
argument-hint: "<files or folders> [what the images are for]"
allowed-tools: Bash, Read, AskUserQuestion, Skill
---

# Optimize Images

**Target:** $ARGUMENTS

Load the `image-optimization` skill and follow its method for the target above. If the target names no files or folders, ask the human which images to work on and what they are for.

Use the `lossless` profile unless the human says the images may change visibly, and write no file before the skill's approval gates are met.
