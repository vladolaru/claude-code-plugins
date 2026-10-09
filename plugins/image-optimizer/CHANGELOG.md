# Changelog

All notable changes to the image-optimizer plugin will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [2.0.0] - Unreleased

### Changed

- `/optimize-images` now follows the new `image-optimization` skill: it measures every candidate against a quality profile (`lossless` by default, `high` or `medium` on request) and picks per file instead of running one lossless pass.
- Encoders are called directly (mozjpeg, oxipng, pngquant, guetzli, gifsicle, svgo, and jpegli when installed) instead of through the x86-only `imageoptim` CLI, and work stops until missing required tools are installed or explicitly waived; optional tools never stop it.
- Homebrew's tools are preferred to the copies bundled with ImageOptim.app (except jpegoptim, whose bundled build is the one linked to mozjpeg), and `imgopt.py doctor` suggests the Homebrew install when only an older bundled copy is found.
- Runtime requirements: `python3` with Pillow for everything, plus ffmpeg and ssimulacra2 (from jpeg-xl) for the `high` and `medium` profiles; `imgopt.py doctor` lists the rest per job.

### Added

- `imgopt.py` with `doctor`, `inspect`, `candidates`, `sheet`, `apply` and `compare`: tool checks, an audit-only report, per-file picks, 1:1 review tiles for the agent and a comparison page for the human, re-measured writes, and a reviewer-runnable SSIM check.
- Resizing to a target width, PNG-to-JPEG, and WebP or AVIF output (labelled uncalibrated) under the `high` or `medium` gates; like any pick made from re-encoded pixels, these wait for approval on the comparison page, and a resized or converted file that comes out larger than its original is flagged LARGER.
- The comparison page shows each lossy pick's 1:1 crops beside its gates and builds the `apply --approve a,b` command from the picks the human ticks; `apply` writes only the lossy picks named there.
- `imgopt.py workdir <task>` gives a working folder that survives sessions (falling back to `$TMPDIR` in sandboxes), and `candidates` refuses one inside an input folder, a git work tree or a non-empty folder it did not make. `imgopt.py clean` removes a folder `candidates` made, or only its losing candidates with `--keep-picks`, and `candidates` prints the folder's size and that command.
- `sheet` and `apply` act only on the files the last `candidates` run in a working folder measured, so a file that run skipped is not offered again, and they name the records they ignore; an interrupted run resumes from its last finished candidate.
- The `candidates` summary and the page lead with whole-batch totals, signed, beside the files that will change, and printed commands quote their paths.
- Lossy JPEG jobs also try jpegli when it is installed; it is optional because no package manager ships it and its gain (about 2 points on 31 WooCommerce photos) is unmeasured elsewhere, and `imgopt.py doctor` prints a build that keeps working after Homebrew upgrades.
- PNGs up to 2 megapixels also get an oxipng zopfli candidate, about 2% smaller on small PNGs; larger images skip it, because it took minutes on screenshots for 0.4% or less.
- Under `high` and `medium`, a lossy pick's Evidence SSIM (a luma SSIM, so ssimulacra2 goes in the evidence table too; what the PR reviewer's ffmpeg check prints) must also clear the SSIM floor, so the number quoted in PR evidence never shows below it.
- Every pick lists the metadata it removes (EXIF, XMP, IPTC, comments, PNG text), whether it is a lossless pass or a re-encode, so copyright and credit fields are never dropped silently.
- `--ref-rev <rev>` measures a whole batch against each file's content at an earlier commit, with each baseline's size beside the file's, and a file an earlier lossy pick wrote is not measured again under a lossy profile without a baseline from before that pick, so lossy passes never stack.
- Folder inputs skip git-ignored files, vendored folders and imgopt working folders, and say what they skipped; a file the job cannot serve (a GIF in a resize) or a Git LFS pointer gets its own line instead of stopping the batch.
- Tools that print no version are identified by a hash of their binary, encoder refusals are not retried on every run, and `apply` stops with a clear reason when ffmpeg or ssimulacra2 changed since the picks were measured.
- `candidates` measures two files at a time (`--jobs`, one at a time where a sandbox denies worker processes), skips measuring in-place candidates that are not smaller than the original, runs butteraugli on the pick only, and writes pixel files only when a candidate reads them; guetzli is skipped above 6 megapixels.

### Fixed

- Lossless PNG picks keep the gAMA, cHRM and sBIT chunks browsers use to render them, and animated PNGs keep every frame; animated PNGs and PNGs whose gAMA or cHRM decides their rendering get lossless candidates only.
- SVG optimization uses svgo 4 or newer with a bundled config that keeps ids, classes, `role` and `aria-*` attributes, and `<desc>` text; a pick that loses one, or changes the rendering, is discarded, and an older svgo is refused.

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
