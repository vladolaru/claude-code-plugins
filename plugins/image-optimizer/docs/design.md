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
- **Calibrated in one session.** The floors were set on the four photos of #69539 and the thirteen PNGs of #69553 and #69556, all WooCommerce assets. The skill and README say so, and the benchmark corpus plan revisits the floors on more kinds of images.
- **No margin above the floor.** The smallest passing candidate wins, so a pick sits at the floor, where the reported-only metrics (butteraugli, banding outside palette PNGs) look worst: the `gallery-6` test photo's pick under `high` measured SSIM 0.9827 with banding 5.0. A size margin would move every pick, which is a calibration change, so it waits for the corpus plan as well. Until then the page shows each gate beside the reported value.

SSIM is ffmpeg's `ssim` on gray (luma only), computed on both images flattened onto white, and also onto dark grey when the image has alpha; the worse score counts. Comparing alpha images without flattening them produced 0.74 for a pair that measured 0.965 once flattened.

**Banding is gated only on palette PNG output.** That is the one case calibrated against a real failure: #69556, a 24-colour gradient cut to 16 colours, which passed both other metrics. Lossy photo encodes scored 4 to 14 without visible banding, and the WebP of the #69556 gradient header scored 6. A threshold for those formats needs its own calibration, so their score is reported, not gated, and the smooth-area crop is required viewing instead.

**The Evidence SSIM is held to the floor too.** The Evidence SSIM is the number a PR reviewer gets from the ffmpeg one-liner `compare` prints. The gate decodes with Pillow, while that one-liner runs ffmpeg, whose JPEG decoder differs by up to about 1e-3 (9.8e-4 on the #69539 photos). `candidates` therefore runs the reviewer check on each lossy pick. A pick whose Evidence SSIM falls below the floor gives way to the next passing candidate.

**The Evidence SSIM is a luma SSIM.** It does not see colour: a copy of a 4 MP photo with its luma kept and its chroma blurred and shifted scored 0.995 against ssimulacra2 7.5. The ssimulacra2 floor rejects such a copy, so the evidence table puts ssimulacra2 beside the luma SSIM, and every output labels the number "Evidence SSIM (luma)".

**Reproduction depends on the ffmpeg build.** With the same ffmpeg build the reviewer one-liner prints the same number (see Acceptance evidence). Across builds a different IDCT or scaler can move the last digits; the skill tells reviewers to expect agreement to about 1e-4, an estimate that no second build has tested. `compare` prints the ffmpeg version beside the command so a mismatch can be traced to the build.

## What counts as lossless

A pick is lossless only when a lossless encoder rewrote the source file itself. Then its colour profile and orientation survive and it needs no approval. Anything re-encoded from prepared pixels is lossy, even when its encoder is lossless: pixels converted to sRGB, rotated by their orientation tag, resized or flattened. Such a pick needs crops and the human's approval. Before this rule, a Display P3 PNG under `high` was reported as "identical", and `apply` overwrote it without its profile and with its wide-gamut colours clipped.

Pillow's decoded pixels are not everything a browser or screen reader sees, so "identical" also covers:

- **PNG colour chunks.** Pillow ignores `gAMA` on decode, so oxipng's `--strip safe` dropped it from a gamma 1.0 PNG, the pick passed as identical, and Chrome rendered it up to 73/255 lighter. Lossless PNG rungs keep `cICP`, `iCCP`, `sRGB`, `gAMA`, `cHRM`, `sBIT`, `pHYs` and the APNG chunks, and the identity check compares the colour chunks too. A PNG whose `gAMA` or `cHRM` decides its rendering (no `sRGB` chunk, no ICC profile) gets lossless rungs only, because pixel encoders drop those chunks; it cannot be resized or converted.
- **APNG frames.** A lossy rung reads the first frame only, so an animated PNG once came out as a still marked `apply`. Animated PNGs get lossless rungs only, their frames are compared like a GIF's, and a resize or conversion is refused for that file.
- **SVG semantics.** svgo's `preset-default` removed `role="img"` and every id, including the ones `aria-labelledby` points at, and the rendering check could not see it. The bundled config keeps ids, `role`, `aria-*`, classes, `<desc>` text and empty groups (which may carry an id), and does not inline `<style>` blocks; without the last two, every Sketch export lost its "Created with Sketch." description and an empty layer group and got no pick at all; a pick that loses an id, class, role, `aria-*` attribute or title/desc text, or that renders differently, is discarded.
- **Metadata is listed, not kept.** Lossless JPEG and PNG rungs strip EXIF (unless it carries the orientation), XMP, IPTC, comments and PNG text, where copyright and credit fields live. Every pick lists what it removes, in `candidates`, on the page and in the record, and the skill tells the agent to say so before applying; nothing prevents the removal.

## Picking and verdicts

- The smallest passing candidate wins. Ties go to lossless, then to progressive.
- **In-place jobs** (same format, same size) leave the file untouched when the saving is below max(1 KB, 1%): a few bytes are not worth a binary diff in a repository.
- **Resizes and format changes** always apply their pick, because the change was asked for. A resized or converted file larger than its original is still applied, but it is flagged LARGER so the human hears about it first.
- **When nothing passes**, the verdict names the closest candidate. For a resize under `high`, it also suggests `medium`: shrinking green-glass-jars from #69539 to 240 px under `high` found nothing, with the best candidate at SSIM 0.974.

## Tooling policy

Missing required and quality tools stop the work instead of degrading it silently. Only a tool the human explicitly declines can be skipped, with `--allow-missing`, and every output repeats the waiver. Optional tools (jpegli's `cjpegli`, butteraugli) never block. The scripts never install anything.

- **jpegli is optional.** It has no package: building it takes git, cmake and a C++ compiler. Its gain (36.3% against 34.6% under `high`) was measured on one corpus of 31 WooCommerce photos re-encoding existing JPEGs, with the prepare and convert paths unmeasured. Blocking every lossy JPEG job on it cost more than that bought. `doctor` lists it as optional with the build line, and when it is absent `candidates` prints OPTIONAL TOOLS MISSING and the page shows a banner. The build `doctor` prints is self-contained: jpegli static, libpng bundled, and the OpenEXR, GIF and JPEG readers off, since imgopt only feeds it PNG. A plain `-DBUILD_SHARED_LIBS=OFF` build still loaded five Homebrew libraries, so a `brew upgrade` could break it. `doctor` rejects a `cjpegli` that cannot start. Shipping a prebuilt binary was considered and declined.
- **Tools are identified by their build.** A tool that prints no version (cjpegli, guetzli, ssimulacra2, butteraugli) is identified by a short sha256 of its binary, which keys the cache and appears in the `tools:` line. Keying on size and mtime made a copied binary invalidate every cached candidate and left the evidence unable to name the build. `apply` compares the recorded ffmpeg and ssimulacra2 identities first and refuses lossy picks when either changed, instead of failing the tight re-measurement tolerance as a MISMATCH.
- **Encoder refusals are cached.** An encoder that exits with an error status on unchanged inputs (guetzli on CMYK or non-YUV JPEGs) is not retried on the next run. A death by signal, such as an out-of-memory kill, is retried.
- **No browser.** `sheet --browser` and its Chrome dependency were removed. The screenshot was a fixed viewport of scaled panes, the kind of image that hid banding in the session, and Chrome could not start inside the Codex sandbox. The page carries the 1:1 crops instead and opens at 100%.

- Only mozjpeg `jpegtran` and `cjpeg` are used. A libjpeg-turbo build made files 1.5% to 4% larger.
- svgo 4 or newer is required. svgo 3's defaults drop `viewBox` and `<title>`, which the rendering check cannot see.
- Homebrew's tools (found on `PATH`) come before the copies bundled with ImageOptim.app, which date from 2023 and update only with ImageOptim; `doctor` suggests the Homebrew install when only a bundled copy is found. jpegoptim is the exception: the bundled build is linked to mozjpeg, Homebrew's to libjpeg-turbo.
- The scripts run under plain `python3`, not `python3 -I`, because Pillow may live in user site-packages.

## Running conditions

- **Working folder.** `imgopt workdir <task>` returns a folder under the cache root: `$IMGOPT_CACHE` when set, else `~/Library/Caches/imgopt/work/<task>` on macOS, else `$XDG_CACHE_HOME/imgopt` or `~/.cache/imgopt`. It survives sessions, so a re-run reuses candidates and an interrupted run resumes after its last finished candidate. A sandbox that cannot write there (Codex) gets `$TMPDIR/image-optimization/<task>`; imgopt never picks `/tmp` itself. The session scratchpad used before this lived under `/private/tmp` and changed with every session, so the cache never carried over.
- **Never in the repository.** `candidates` refuses an `--out` inside an input folder (a later folder run would optimize the working files) or a git work tree (one `git add -A` from a commit), and writes a `.gitignore` of `*` into the folder. Folder inputs skip imgopt working folders, git-ignored files and vendored trees.
- **Only marked folders are deleted.** `candidates` writes a `.imgopt-workdir` marker into `--out`, and refuses an existing non-empty folder without it. `imgopt clean <dir>` deletes only a marked folder; `--keep-picks` keeps each record, its reference, its source copy and its pick, which is all `sheet` and `apply` read. File names such as `run.json` are common elsewhere, so a guard on them would have let `clean` delete a project folder.
- **Footprint per input.** Measured on a 238 KB, 4.2 MP WooCommerce photo (`du -sh` of the working folder, before `sheet`): under `lossless` 19 MB before the audit fixes and 2.9 MB after; under `high` 25 MB before and 11 MB after, 2.7 MB after `clean --keep-picks`. The pixel dumps are written only when a rung reads them, and the flattened reference once per file. `candidates` prints the folder's size and the `clean` command at the end.
- **Time and memory.** `candidates` measures two files at a time by default (`--jobs`). Where the process pool cannot start, as in some sandboxes, it says so and measures one file at a time. In-place candidates that are not smaller than the original are not measured, and butteraugli runs on the pick only. On three public WooCommerce test files (two photos and a PNG) under `high`, the run went from 203 s to 158 s with identical picks; guetzli's two encodes of the 4.2 MP photo, about 150 s, set the floor. guetzli needs about 300 MB per megapixel, so it is skipped above 6 MP, and two jobs can double that peak; `--jobs 1` halves it.

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

## Acceptance evidence

Runs of 2026-10-08:

- **#69556 header:** `lossless` picked oxipng at 41,068 B, the same size ImageOptim produced.
- **#69539 JPEGs under `high`:** three of the four picks matched the session's. `gallery-5` landed one setting milder, because guetzli q90 measured just under SSIM 0.98.
- **Reviewer one-liner:** run on the copy GitHub serves, it printed `All:0.990246`, equal to `compare`'s Evidence SSIM to six decimals.
- **Codex sandbox (macOS, `codex sandbox` with `workspace-write`):** `doctor`, `inspect`, `candidates`, `sheet`, `apply` and `compare` all ran, with `--out` under `$TMPDIR`. Headless Chrome could not start there, which is why `sheet --browser` was removed: the page carries the 1:1 crops itself.

After the workflow audit fixes (2026-10-09), one `high` batch of the three public test files plus an animated PNG, a gamma 1.0 PNG, an accessible SVG and an animated GIF:

- **Edge inputs:** the APNG's pick kept all 3 frames, the gamma PNG kept `gAMA` and `cHRM` once written, and the SVG pick kept `role`, `aria-labelledby` and every id.
- **Approval:** ticking two of the three lossy picks on the page built an `--approve` list of exactly those two files. `apply` wrote them and the lossless gamma PNG pick, and reported the third lossy pick as `not approved, left as it was`.
- **Stacking:** a second `high` run on a file `apply` had written was refused for that file, naming `--ref-rev` and `--ref`.
- **Cleanup:** the batch took 160 s with two jobs and left 12 MB after `sheet`; `clean --keep-picks` freed 8.8 MB and `clean` the remaining 3.8 MB.

## Known gaps

- **Linux** is untested. The apt package names in `doctor` are best effort, and the scripts have only run on macOS.
- **16-bit images** are refused per file: Pillow clips 16-bit gray at 255 and reads 16-bit colour as 8-bit, so counts and comparisons would mislead.
- **Banding thresholds** for JPEG, WebP and AVIF output are not calibrated (see above); calibration is the benchmark corpus plan's job, together with the floors and a size margin.
- **Quality descents are measured to the bottom.** Stopping a descent at its first failure was rejected because the gates are not monotonic in quality: on the `gallery-6` test photo, cjpegli at q45 measured SSIM 0.9794 and at q50 0.9780, and q35 measured higher than q40. A descent cut at its first failure could miss a smaller passing setting below it.
- **Gamma-tagged PNG exports count as gamma-dependent.** A PNG with a `gAMA` chunk and no `sRGB` chunk or ICC profile gets lossless rungs only, including the common 0.45455 export, which costs pngquant on those files. Whether that value should count as sRGB is for the corpus plan.
- **`--ref-rev` is all or nothing.** It stops the run when any input is an SVG or did not exist at the revision, so a folder that mixes SVG and raster files cannot use it; pass the raster files by name.
- **jpegli is optional, and its gain was measured on one corpus.** 31 WooCommerce photos re-encoded as JPEG; the prepare and convert paths were not measured. The benchmark corpus plan decides how much weight it deserves.
- **Homebrew's jpegoptim** (linked to libjpeg-turbo) was not measured against the bundled one; without ImageOptim it is what runs.
- **The `medium` JPEG ladder stops at jpegoptim `-m40`.** Under `medium`, 15 of 31 photos landed on that last setting; going down to `-m20` would save about 0.8 points more.
