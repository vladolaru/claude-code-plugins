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

**The SSIM a reviewer sees is held to the floor too.** The gate decodes with Pillow, while the reviewer one-liner `compare` prints runs ffmpeg, whose JPEG decoder differs by up to about 1e-3 (9.8e-4 on the #69539 photos). `candidates` therefore runs the reviewer check on each lossy pick. A pick that would show a reviewer a number below the floor gives way to the next passing candidate.

## What counts as lossless

A pick is lossless only when a lossless encoder rewrote the source file itself. Then its colour profile and orientation survive and it needs no approval. Anything re-encoded from prepared pixels is lossy, even when its encoder is lossless: pixels converted to sRGB, rotated by their orientation tag, resized or flattened. Such a pick needs crops and the human's approval. Before this rule, a Display P3 PNG under `high` was reported as "identical", and `apply` overwrote it without its profile and with its wide-gamut colours clipped.

## Picking and verdicts

- The smallest passing candidate wins. Ties go to lossless, then to progressive.
- **In-place jobs** (same format, same size) leave the file untouched when the saving is below max(1 KB, 1%): a few bytes are not worth a binary diff in a repository.
- **Resizes and format changes** always apply their pick, because the change was asked for. A converted file larger than its original is still applied, but it is flagged LARGER so the human hears about it first.
- **When nothing passes**, the verdict names the closest candidate. For a resize under `high`, it also suggests `medium`: shrinking green-glass-jars from #69539 to 240 px under `high` found nothing, with the best candidate at SSIM 0.974.

## Tooling policy

Missing tools stop the work instead of degrading it silently. Only a tool the human explicitly declines can be skipped, with `--allow-missing`, and every output repeats the waiver. The scripts never install anything.

- Only mozjpeg `jpegtran` and `cjpeg` are used. A libjpeg-turbo build made files 1.5% to 4% larger.
- svgo 4 or newer is required. svgo 3's defaults drop `viewBox` and `<title>`, which the rendering check cannot see.
- The scripts run under plain `python3`, not `python3 -I`, because Pillow may live in user site-packages.

## Acceptance evidence (2026-10-08)

- **#69556 header:** `lossless` picked oxipng at 41,068 B, the same size ImageOptim produced.
- **#69539 JPEGs under `high`:** three of the four picks matched the session's. `gallery-5` landed one setting milder, because guetzli q90 measured just under SSIM 0.98.
- **Reviewer one-liner:** run on the copy GitHub serves, it printed `All:0.990246`, equal to `compare`'s Evidence SSIM to six decimals.
- **Codex sandbox (macOS, `codex sandbox` with `workspace-write`):** `doctor`, `inspect`, `candidates`, `sheet`, `apply` and `compare` all ran, with `--out` under `$TMPDIR`. Headless Chrome cannot start there, so `sheet --browser` fails inside Codex. The comparison page itself is unaffected.

## Known gaps

- **Linux** is untested. The apt package names in `doctor` are best effort, and the scripts have only run on macOS.
- **16-bit images** are refused per file: Pillow clips 16-bit gray at 255 and reads 16-bit colour as 8-bit, so counts and comparisons would mislead.
- **Banding thresholds** for JPEG, WebP and AVIF output are not calibrated (see above).
