# Changelog

All notable changes to the image-optimizer plugin will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [2.0.0] - Unreleased

### Added

- `imgopt.py doctor` checks every tool a job needs, names what each missing one adds, and prints one install command.
- `imgopt.py candidates` tries a ladder of encoder settings per file, measures each against a quality profile (`lossless` by default, `high`, `medium`), and picks the smallest that passes.
- `imgopt.py inspect` reports each file's format, dimensions, colours, profile, orientation, estimated JPEG quality and lossless headroom without changing anything.
- `imgopt.py sheet` cuts 1:1 difference tiles for the agent to view (never wider than about 1000 px) and builds a local comparison page with before, pick and an optional alternative.
- `imgopt.py apply` writes the picks, refuses lossy ones without `--approved`, refuses files that changed since `candidates`, and re-measures every pick before it replaces the original, which stays untouched on a mismatch.

## [1.2.0] - 2026-07-23

### Added

- Codex plugin packaging and a generated
  `$image-optimizer:optimize-images` command adapter derived from the
  canonical Claude Code command.

### Fixed

- The optimize workflow now passes the bundled `svgo.config.mjs` to the
  script instead of an empty config argument, so SVGs are optimized with the
  documented `multipass`/`viewBox`-preserving settings rather than svgo's
  defaults (which strip `viewBox`).

## [1.1.0] - 2025-12-11

### Changed

- Converted from skill to `/optimize-images` command for easier invocation
- Moved scripts to `scripts/` directory

## [1.0.0] - 2025-12-11

### Added

- Initial release as standalone plugin (extracted from pirategoat-tools)
- `image-optimizer` skill - Lossless image optimization using imageoptim-cli and svgo
- Review-before-apply workflow for safe optimization
- Support for PNG, JPEG, GIF (via ImageOptim) and SVG (via svgo)
- Batch processing with size comparison reports
