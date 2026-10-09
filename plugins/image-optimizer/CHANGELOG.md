# Changelog

All notable changes to the image-optimizer plugin will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [2.0.0] - Unreleased

### Changed

- `/optimize-images` now follows the new `image-optimization` skill: it measures every candidate against a quality profile (`lossless` by default, `high` or `medium` on request) and picks per file instead of running one lossless pass.
- Encoders are called directly (mozjpeg, jpegli, oxipng, pngquant, guetzli, gifsicle, svgo) instead of through the x86-only `imageoptim` CLI, and work stops until missing tools are installed or explicitly waived.
- Homebrew's tools are preferred to the copies bundled with ImageOptim.app (except jpegoptim, whose bundled build is the one linked to mozjpeg), and `imgopt.py doctor` suggests the Homebrew install when only an older bundled copy is found.
- Runtime requirements: `python3` with Pillow for everything, plus ffmpeg and ssimulacra2 (from jpeg-xl) for the `high` and `medium` profiles; `imgopt.py doctor` lists the rest per job.

### Added

- Resizing to a target width, PNG-to-JPEG, and WebP or AVIF output (labelled uncalibrated) under the `high` or `medium` gates; like any pick made from re-encoded pixels, these wait for approval on the comparison page, and a resized or converted file that comes out larger than its original is flagged LARGER.
- `imgopt.py` with `doctor`, `inspect`, `candidates`, `sheet`, `apply` and `compare`: tool checks, an audit-only report, per-file picks, 1:1 review tiles and a comparison page, re-measured writes, and a reviewer-runnable SSIM check.
- Lossy JPEG jobs also try jpegli, which is needed for the best JPEG results but which neither Homebrew nor apt packages: `imgopt.py doctor` prints a one-time build that keeps working after Homebrew upgrades, and the job waits until it is built or explicitly waived.
- PNGs up to 2 megapixels also get an oxipng zopfli candidate, about 2% smaller on small PNGs; larger images skip it, because it took minutes on screenshots for 0.4% or less.
- Under `high` and `medium`, a lossy pick's Evidence SSIM (what the PR reviewer's ffmpeg check prints) must also clear the SSIM floor, so the number quoted in PR evidence never shows below it.
- Every pick lists the metadata it removes (EXIF, XMP, IPTC, comments, PNG text), whether it is a lossless pass or a re-encode, so copyright and credit fields are never dropped silently.
- Folder inputs skip git-ignored files, vendored folders and imgopt working folders, and say what they skipped; a file the job cannot serve (a GIF in a resize) is skipped with its own line instead of stopping the batch.

### Fixed

- Animated PNGs keep every frame (they get lossless candidates only), and lossless PNG picks keep the gAMA, cHRM and sBIT chunks browsers use to render them.
- SVG optimization uses svgo 4 or newer with a bundled config that keeps ids, `role` and `aria-*` attributes; a pick that loses one, or changes the rendering, is discarded, and an older svgo is refused.
- The working folder is refused inside an input folder or a git work tree and gets a `.gitignore`, and a Git LFS pointer is named as one.

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
