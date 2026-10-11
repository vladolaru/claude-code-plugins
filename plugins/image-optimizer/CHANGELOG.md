# Changelog

All notable changes to the image-optimizer plugin will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [2.0.0] - Unreleased

### Changed

- `/optimize-images` now follows the new `image-optimization` skill: it measures every candidate against a quality profile (`lossless` by default, `high` or `medium` on request) and picks per file instead of running one lossless pass.
- Encoders are called directly (mozjpeg, jpegli, jpegoptim, oxipng, pngquant, gifsicle and svgo) instead of through the x86-only `imageoptim` CLI, and work stops until missing required tools are installed or explicitly waived; optional tools never stop it.
- Homebrew's tools are preferred to the copies bundled with ImageOptim.app (except jpegoptim, whose bundled build is the one linked to mozjpeg), and `imgopt.py doctor` suggests the Homebrew install when only an older bundled copy is found.
- oxipng 10 or newer is required, so ImageOptim's bundled oxipng 9 is refused and PNG work waits for `brew install oxipng`; oxipng 9's zopfli mode took about ten times as long for a fifth of the saving.
- Runtime requirements: `python3` with Pillow for everything, plus ffmpeg and ssimulacra2 (from jpeg-xl) for the `high` and `medium` profiles; `imgopt.py doctor` lists the rest per job.

### Added

- `imgopt.py` with `doctor`, `inspect`, `candidates`, `sheet`, `record`, `apply`, `manifest` and `compare`: tool checks, an audit-only report, per-file picks, 1:1 review tiles for the agent and a review page for the human, the human's verdicts, re-measured writes, placement paths, and a reviewer-runnable SSIM check.
- Resizing to a target width, PNG-to-JPEG, and WebP or AVIF output (labelled uncalibrated) under the `high` or `medium` gates, written as untagged sRGB so the same input always gives the same bytes; like any choice made from re-encoded pixels, these wait for the human's keep on the review page, and a resized or converted file that comes out larger than its original is flagged LARGER.
- The review page shows each file's choice beside its original at 100%, with its status (sizes, format change, LARGER, why it is or is not written), 1:1 crops, its gates and a selector over every measured variant, and the human marks each file keep, reject, milder or stronger, with a note; it fits a phone and the dark color scheme, and a card that would write a variant without crops says so; a rebuilt page starts from the verdicts already recorded, and "Copy verdicts" gives one block for `record` that names the page's folders.
- `apply` writes a lossy choice only when the human kept it and a lossless one unless the human rejected it or asked for another variant, and refuses the run when no lossy choice has a verdict; `manifest --accepted` lists the same files. A kept or chosen variant smaller than its original is written even under the saving floor, also after a re-run, and `record` and `apply` name any keep that still writes nothing.
- `imgopt.py workdir <task>` gives a working folder that survives sessions, so a re-run reuses earlier candidates; sandboxes get one under `$TMPDIR`.
- `candidates` refuses a working folder inside an input folder or a git work tree, or one that already has files it did not make.
- `imgopt.py clean` deletes a folder `candidates` made, or with `--keep-picks` everything but each file's pick and choice, and `candidates` prints the folder's size and that command.
- `sheet` and `apply` act only on the files the last `candidates` run in a working folder measured, so a file that run skipped is not offered again, and they name the records they ignore.
- An interrupted `candidates` run resumes from its last finished candidate, and its partial results are never applied.
- The `candidates` summary and the page lead with whole-batch totals, signed, beside the files that will change, and printed commands quote their paths.
- Lossy JPEG jobs require jpegli, which cannot be waived because the `high` ladder saved 4.6 to 15 points less on photos without it; no package manager ships it, so `imgopt.py doctor` prints a build that keeps working after Homebrew upgrades.
- PNGs up to 2 megapixels (after any resize) also get an oxipng zopfli candidate, about 2% smaller on small PNGs; larger images skip it, because on 5 MP screenshots it took one to three minutes per file for 0.4 to 3%.
- Under `high` and `medium`, a lossy pick's Evidence SSIM (a luma SSIM, so ssimulacra2 goes in the evidence table too; what the reviewer's ffmpeg check prints) must also clear the SSIM floor, so the number quoted in evidence never shows below it.
- Every pick and choice lists the metadata it removes (EXIF, XMP, IPTC, comments, PNG text) in `candidates`, `variants`, `choose`, `apply`, `manifest` and the page, whether it is a lossless pass or a re-encode, so copyright and credit fields are never dropped silently.
- `--ref-rev <rev>` measures a whole batch against each file's content at an earlier commit, with each baseline's size beside the file's.
- Under `high` and `medium`, a file that is a lossy variant imgopt produced (one that changed the file, never one that kept the source's bytes or pixels), whether `apply` wrote it or it was copied by hand, is measured only against a baseline from before that change, so lossy passes never stack.
- Folder inputs skip git-ignored files, vendored folders and imgopt working folders, and say what they skipped.
- A file the job cannot serve (a GIF in a resize) or a Git LFS pointer gets its own line instead of stopping the batch.
- A cached candidate is made again when any tool that made or measured it changes, including tools that print no version.
- `apply` stops with a clear reason when ffmpeg or ssimulacra2 changed since the choices were measured.
- `candidates` measures two files at a time by default (`--jobs`), or one at a time where a sandbox denies worker processes.
- Every measured variant goes into a variant store shared by all working folders, so measuring a file set again in a new folder costs no encoding, and `candidates` says how many variants came from the store and how many it encoded; variants unused for 30 days are pruned, the store stays under 2 GB, and `clean --store` prunes it on demand.
- `imgopt.py variants` lists every measured variant of one file, and `imgopt.py choose` sets its variant by label, one step milder or stronger, or back to the default, restoring a variant's file from the store when needed; a choice and a verdict name the variant by its content, so a re-run keeps them only while that variant is still measured.
- `imgopt.py record` stores the pasted verdict block in each working folder the block names and refuses a block from a page built from other results; a keep, milder or stronger on a variant other than the current choice chooses that variant first, and a kept lossy file the page showed no 1:1 crops of is named with the `sheet` command that shows them.
- `imgopt.py manifest` lists each file's chosen variant path, its target and the metadata it removes (`--json` for scripts), so an agent can copy each variant to its target itself.
- `apply --only`, `apply --files` and `--file` select files by absolute path, relative path or unique name, and refuse an ambiguous name with the matching paths listed.
- `candidates --files <list>` and `apply --files <list>` take a file set as one path per line, or `-` for stdin.
- `candidates --min-saving <bytes>` leaves files that save less untouched, and the summary ranks picks by bytes saved with the cumulative share, the count of small savings and the folders that hold the most.
- `sheet`, `record`, `apply` and `manifest` take several working folders, so a set that needed two runs is reviewed on one page and placed in one call.
- `candidates --assume-srgb` tries lossy candidates for a gamma-tagged PNG the human compared in a browser, run as its own set; without it such PNGs stay lossless-only.

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
