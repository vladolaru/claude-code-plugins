# image-optimizer design record

Why `imgopt.py` decides the way it does. Read this before changing a profile floor, the pick or verdict rules, what counts as lossless, or the tooling policy. The code's module docstrings hold the mechanics; this file holds the reasons and the evidence behind them.

## Where 2.0 came from

1.2.0 ran one lossless pass per folder through the `imageoptim` CLI, which is x86_64-only. A WooCommerce asset session in October 2026 (PRs #69539, #69553 and #69556) needed what that pass could not give: per-file lossy picks under quality floors, resizing to the display size, real metrics, a banding check, a 1:1 visual review and evidence a reviewer can reproduce. Every mechanical mistake in that session (comparing alpha images without flattening them, a parser that only read JPEG output, Pillow's PNG optimizer used as if it were oxipng, downscaled contact sheets that hid banding) became a rule the scripts own. Judgment stays with the agent (where an image is used, how large it is shown, which profile) and approval with the human (any change to pixels).

## Profiles and their floors

| Profile | SSIM | ssimulacra2 | Banding |
|---|---|---|---|
| `lossless` | decoded pixels identical, colour profile and orientation kept | | |
| `high` | ≥ 0.98 | ≥ 80 | ≤ 3 |
| `medium` | ≥ 0.96 | ≥ 60 | ≤ 3 |

These are not validated constants, and nobody should treat them as such:

- **SSIM 0.98** is the floor the maintainer set after rejecting a PR whose files measured 0.922 to 0.988. **0.96** is the floor set for files kept only so old URLs keep working.
- **ssimulacra2 80 and 60** were chosen during the session and confirmed by eye. They follow ssimulacra2's own scale, where 70–90 is high quality and 50–70 is medium.
- **Both metrics stay** because each caught a candidate the other passed.
- **What they were tested on:** 32 small PNGs (median 7 KB), 3 survey JPEGs, 16 photos in use and 17 orphaned photos. No WebP, AVIF or GIF output was ever gated, which is why WebP and AVIF output is labelled uncalibrated and every crop of it must be viewed.

SSIM is ffmpeg's `ssim` on gray, computed on both images flattened onto white, and also onto dark grey when the image has alpha; the worse score counts. Comparing alpha images without flattening them produced 0.74 for a pair that measured 0.965 once flattened.

**Banding is gated only on palette PNG output.** That is the one case calibrated against a real failure: #69556, a 24-colour gradient cut to 16 colours, which passed both other metrics. Lossy photo encodes scored 4 to 14 without visible banding, and the WebP of the #69556 gradient header scored 6. A threshold for those formats needs its own calibration, so their score is reported, not gated, and the smooth-area crop is required viewing instead.

**The Evidence SSIM is held to the floor too.** The Evidence SSIM is the number a PR reviewer gets from the ffmpeg one-liner `compare` prints. The gate decodes with Pillow, while that one-liner runs ffmpeg, whose JPEG decoder differs by up to about 1e-3 (9.8e-4 on the #69539 photos). `candidates` therefore runs the reviewer check on each lossy pick. A pick whose Evidence SSIM falls below the floor gives way to the next passing candidate.

## What counts as lossless

A pick is lossless only when a lossless encoder rewrote the source file itself. Then its colour profile and orientation survive and it needs no approval. Anything re-encoded from prepared pixels is lossy, even when its encoder is lossless: pixels converted to sRGB, rotated by their orientation tag, resized or flattened. Such a pick needs crops and the human's approval. Before this rule, a Display P3 PNG under `high` was reported as "identical", and `apply` overwrote it without its profile and with its wide-gamut colours clipped.

## Picking and verdicts

- The smallest passing candidate wins. Ties go to lossless, then to progressive.
- **In-place jobs** (same format, same size) leave the file untouched when the saving is below max(1 KB, 1%): a few bytes are not worth a binary diff in a repository.
- **Resizes and format changes** always apply their pick, because the change was asked for. A resized or converted file larger than its original is still applied, but it is flagged LARGER so the human hears about it first.
- **When nothing passes**, the verdict names the closest candidate. For a resize under `high`, it also suggests `medium`: shrinking green-glass-jars from #69539 to 240 px under `high` found nothing, with the best candidate at SSIM 0.974.

## Tooling policy

Missing tools stop the work instead of degrading it silently. Only a tool the human explicitly declines can be skipped, with `--allow-missing`, and every output repeats the waiver. The scripts never install anything.

- Only mozjpeg `jpegtran` and `cjpeg` are used. A libjpeg-turbo build made files 1.5% to 4% larger.
- svgo 4 or newer is required. svgo 3's defaults drop `viewBox` and `<title>`, which the rendering check cannot see.
- Homebrew's tools (found on `PATH`) come before the copies bundled with ImageOptim.app, which date from 2023 and update only with ImageOptim; `doctor` suggests the Homebrew install when only a bundled copy is found. jpegoptim is the exception: the bundled build is linked to mozjpeg, Homebrew's to libjpeg-turbo.
- The scripts run under plain `python3`, not `python3 -I`, because Pillow may live in user site-packages.

## Encoder choice (benchmark, 2026-10-08)

Measured with imgopt's own metrics and floors on WooCommerce files at `689f232d2`: the 31 photos in `pattern-placeholders/` (1.66 MB, the #69539 set and its siblings) and the 13 PNGs of #69553 and #69556. For each file the smallest passing candidate counts, over the settings the ladder ships (jpegoptim and `cjpeg` 95 to 40, jpegli 90 to 25, guetzli 84 and 90).

| JPEG ladder | `high` | `medium` |
|---|---|---|
| jpegoptim + guetzli (before) | -34.6% | -60.4% |
| jpegoptim + jpegli | -34.1% | -61.0% |
| jpegoptim + jpegli + guetzli (now) | -36.3% | -61.1% |
| mozjpeg `cjpeg` + jpegli + guetzli (no jpegoptim) | -36.0% | -60.9% |

- **jpegli joins guetzli rather than replacing it.** Under `high` it was the smallest passing encoder on 13 of 31 photos, in about 30 ms per encode against guetzli's 19 s, but each wins photos the other loses. guetzli also refused 2 of the photos outright at both settings (non-YUV input), and its upstream is archived.
- **jpegoptim stays.** Without it the full ladder is 0.3 points larger under `high` and 0.1 under `medium`; in a ladder without jpegli, replacing it with mozjpeg `cjpeg` costs 0.8 points under `high` (-34.6% against -33.8%), and under `medium` it is the smallest encoder on 25 of 31 photos. That keeps the one reason to look in the ImageOptim bundle first.
- **PNG:** oxipng 10.2.1 and 9.0.0 give the same sizes (131,285 against 131,306 B). Zopfli mode saves 1.8% more on these small PNGs, but on 5 to 7 megapixel screenshots it took 110 to 196 s for 0.4% or less, so it runs only up to 2 megapixels, and `inspect`, which has no cache, skips it. oxipng 9, the bundled copy, ran zopfli 5× slower than oxipng 10 on the small PNGs, which is one more reason `doctor` suggests Homebrew's. As a post-pass on palette candidates it saved 0.7% at 15× the time, so the post-pass stays plain.

## Acceptance evidence (2026-10-08)

- **#69556 header:** `lossless` picked oxipng at 41,068 B, the same size ImageOptim produced.
- **#69539 JPEGs under `high`:** three of the four picks matched the session's. `gallery-5` landed one setting milder, because guetzli q90 measured just under SSIM 0.98.
- **Reviewer one-liner:** run on the copy GitHub serves, it printed `All:0.990246`, equal to `compare`'s Evidence SSIM to six decimals.
- **Codex sandbox (macOS, `codex sandbox` with `workspace-write`):** `doctor`, `inspect`, `candidates`, `sheet`, `apply` and `compare` all ran, with `--out` under `$TMPDIR`. Headless Chrome cannot start there, so `sheet --browser` fails inside Codex. The comparison page itself is unaffected.

## Known gaps

- **Linux** is untested. The apt package names in `doctor` are best effort, and the scripts have only run on macOS.
- **16-bit images** are refused per file: Pillow clips 16-bit gray at 255 and reads 16-bit colour as 8-bit, so counts and comparisons would mislead.
- **Banding thresholds** for JPEG, WebP and AVIF output are not calibrated (see above).
- **jpegli has no package.** It is needed for the best JPEG results, so lossy JPEG jobs stop until `cjpegli` is built from source or waived. `doctor` prints the steps. They build a self-contained binary that loads only system libraries: jpegli static, libpng bundled, and the OpenEXR, GIF and JPEG readers off, since imgopt only feeds it PNG. A plain `-DBUILD_SHARED_LIBS=OFF` build still loaded five Homebrew libraries, so a `brew upgrade` could break it. `doctor` rejects a `cjpegli` that cannot start. Shipping a prebuilt binary was considered and declined.
- **Homebrew's jpegoptim** (linked to libjpeg-turbo) was not measured against the bundled one; without ImageOptim it is what runs.
- **The `medium` JPEG ladder stops at jpegoptim `-m40`.** Under `medium`, 15 of 31 photos landed on that last setting; going down to `-m20` would save about 0.8 points more.
