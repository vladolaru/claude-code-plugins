# Changelog

All notable changes to the image-optimizer plugin will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [2.0.0] - Unreleased

### Changed

- `/optimize-images` now follows the new `image-optimization` skill: it measures every candidate against a quality profile (`lossless` by default, `high` or `medium` on request) and picks per file instead of running one lossless pass.
- Encoders are called directly (mozjpeg, oxipng, pngquant, guetzli, gifsicle, svgo) instead of through the x86-only `imageoptim` CLI, and work stops until missing tools are installed or explicitly waived.
- Runtime requirements: `python3` with Pillow for everything, plus ffmpeg and ssimulacra2 (from jpeg-xl) for the `high` and `medium` profiles; `imgopt.py doctor` lists the rest per job.

### Added

- Resizing to a target width, PNG-to-JPEG, and WebP or AVIF output (labelled uncalibrated) under the `high` or `medium` gates; like any pick made from re-encoded pixels, these wait for approval on the comparison page.
- `imgopt.py` with `doctor`, `inspect`, `candidates`, `sheet`, `apply` and `compare`: tool checks, an audit-only report, per-file picks, 1:1 review tiles and a comparison page, verified writes, and a reviewer-runnable SSIM check.
- Under `high` and `medium`, a lossy pick must also clear the SSIM floor as the reviewer's ffmpeg check measures it, so the SSIM quoted in PR evidence never shows below the floor.

### Fixed

- SVG optimization uses svgo 4 or newer with an updated bundled config; an older svgo is refused, because its defaults drop `viewBox` and `<title>`.

### Removed

- `scripts/optimize-images.sh` and the `imageoptim-cli` dependency.

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
