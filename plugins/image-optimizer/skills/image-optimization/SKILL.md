---
name: image-optimization
description: Use when optimizing, recompressing, resizing, converting or auditing images (PNG, JPEG, GIF, SVG, WebP, AVIF) in a repository or for a web page. Picks the smallest file per image that passes a measured quality profile, checks banding, and shows a 1:1 review before anything is written.
---

# Image Optimization

Scripts do the mechanics (encoding, measuring, gating, crops, writing); you do the judgment (where an image is used, how big it is shown, which profile); the human approves anything that changes pixels.

## The tool

Every command is `python3 "$SKILL_DIR/../../scripts/imgopt.py" <command>`; call it `IMGOPT` below. Use plain `python3`, never `python3 -I` (Pillow may live in user site-packages). `IMGOPT <command> --help` lists every flag.

| Command | Use it to |
|---|---|
| `doctor --job <audit\|recompress\|prepare\|convert\|compare> --profile <p> [--format f] [paths]` | Check tools before any work (`prepare` = resizing, `convert` = a new format) |
| `inspect <paths> [--json]` | Report facts and lossless headroom; changes nothing (the audit job) |
| `workdir <task>` | A stable working folder for `--out` |
| `candidates <paths> --out <dir> --profile <p> [--resize W] [--format f] [--ref file \| --ref-rev REV] [--jobs N]` | Try encoder settings per file, measure, gate, pick |
| `sheet <dir> [--alt <label>]` | 1:1 tiles for you, a comparison page for the human |
| `apply <dir> [--approve a,b] [--only a,b] [--dest dir]` | Write picks and re-measure them |
| `compare <ref> <new>` | Measure any pair (path, `git:<rev>:<path>`, URL) and get the reviewer check |
| `clean <dir> [--keep-picks]` | Delete the working folder, or with `--keep-picks` only its losing candidates |

**Exit codes.** 0 success; 1 refused, verification failed, or at least one file reported a problem; 2 tooling missing or usage error (nothing was written, except that `--ref-rev` may already have extracted baselines into the working folder). A file that cannot be handled never aborts a batch: it gets its own line, the other files finish, and the command exits 1 at the end. That covers an unreadable file, a Git LFS pointer (run `git lfs pull` first), a 16-bit image (not supported yet), a file whose content does not match its extension (a JPEG named `.png`: ask the human before renaming it, since its URL changes), a file the job cannot serve (a GIF, an animated PNG or a gamma-dependent PNG in a resize or a conversion), a `--resize` wider than the image (it is never upscaled) or equal to its width with no larger `--ref` (nothing to resize), a file an earlier lossy pick wrote or a baseline one wrote (see Baselines), and a file whose every candidate errored (a tool failure, not a quality verdict). Report those lines to the human; do not hide them. Folder inputs skip git-ignored files, `node_modules`, `vendor`, `bower_components` and imgopt working folders, and say what they skipped.

**Working folder (`--out`).** Get it with `IMGOPT workdir <task-slug>`. It is the same folder in every session (in a sandbox it falls back to `$TMPDIR`), so a re-run reuses everything whose inputs, settings and tool versions are unchanged, and an interrupted run resumes from its last finished candidate. Never the repository: `candidates` refuses a folder inside an input folder or git work tree. Use that folder or a new empty one: `candidates` also refuses a non-empty folder it did not make, and `clean` deletes the whole folder it marked. `sheet` and `apply` act only on the files the folder's last `candidates` run measured, and list older records, and those of files that run skipped, as `ignored:`. When the files are applied and the evidence is written, remove it with `IMGOPT clean <dir>`; with `--keep-picks` it keeps each file's record, reference, source copy and pick (the other candidates go, so `sheet --alt` can no longer show them), for when the human will apply later.

## Profiles

| Profile | Gate | Use for |
|---|---|---|
| `lossless` (default) | identical pixels, colour profile and orientation kept | anything without a stated reason to change visibly: logos, icons, UI |
| `high` | SSIM ≥ 0.98, ssimulacra2 ≥ 80, banding ≤ 3 | images the product or page shows |
| `medium` | SSIM ≥ 0.96, ssimulacra2 ≥ 60, banding ≤ 3 | files kept only so old URLs keep working, rarely seen |

These floors were calibrated in one WooCommerce session (four photos, thirteen PNGs); treat them as a starting point, and say so if a reviewer asks. Banding is gated only on palette PNG output; for other lossy output it is reported and you must view the smooth-area tile. Override a single floor with `--ssim`, `--ss2` or `--band` only when the human asks.

## Method

1. **Tooling first.** Run `IMGOPT doctor` for the job, profile and paths. If it prints BLOCKED, stop and ask the human to install everything it lists: quote what each tool adds and the install line (they can paste it as `! brew install ...`). Do not present "continue without it" as an equal choice; missing tools cost quality. Only when the human explicitly declines a named tool, pass `--allow-missing <tool>` to `candidates` or `inspect` for exactly that tool, and repeat the waiver in your report. Never install anything yourself. Pillow, ffmpeg and ssimulacra2 (lossy profiles), the target encoder, `cjpegli` (lossy JPEG output) and rsvg-convert (SVG) cannot be waived. `cjpegli` has no package: quote the build line `doctor` prints and wait while the human builds it. Optional tools (butteraugli) never block and need no waiver.
2. **Usage sets the profile.** For each file find who loads it. In the repository, search code, CSS, templates and markup. You cannot see outside consumers (stored post content, other repositories, apps, feeds): ask the human whether the URL is public and used elsewhere, and present your search as covering the repository only. A CSS `url()` counts only if markup still carries that selector. A file nothing in the repository renders is a deletion candidate once the human confirms nothing outside uses it, so say so instead of compressing it. Group files by how visible they are, propose a profile per group, and get the human's confirmation.
3. **Display size sets dimensions.** Read the slot size from markup or CSS, or ask. Aim for about 2× the CSS size for high-density screens. Do not shrink a file that a larger slot elsewhere also uses, and do not crop cover images (the focal point moves). Any dimension change needs the human's yes; pass it as `--resize <width>` with `--profile high` or `medium` (the `lossless` profile cannot re-encode pixels, so it refuses a resize or a new format). A resize under `high` can find no pick; the verdict then names the closest candidate's scores, and you offer the human `medium` rather than lowering a floor yourself.
4. **Keep names and URLs** unless the human says they may change. Only then consider `--format webp|avif|jpeg|png` (with `high` or `medium`). Every tile of a converted file is required viewing, and WebP and AVIF are also uncalibrated against these gates. A resized or converted pick can come out larger than the original; `candidates` keeps it (the change was asked for) but marks it LARGER, and you tell the human before applying it.
5. **Run `candidates`, then `sheet`.** Run long batches in the background and wait for completion; never poll with `sleep`. Open every tile `sheet` marks REQUIRED at its natural size before calling a pick good. Never write "no banding" or "looks the same" for a file you did not view at 1:1. In your report, list the tiles you viewed; viewing is not a measurement, so never call a reported-only metric (JPEG, WebP, AVIF banding) passed. If `sheet` says a lossy pick could not be tiled, do not put it forward for approval. Open the comparison page for the human. The page shows each lossy pick with its 1:1 crops; the human ticks the picks they approve and the page shows the apply command. A pick whose path contains a comma cannot be ticked, and its card says to rename the file.
6. **Apply.** `IMGOPT apply <dir>` writes lossless picks; lossy picks are written only when named in `--approve`, exactly as the page's command lists them, and every other lossy pick is reported as `not approved, left as it was`. A pick counts as lossy whenever it was made from prepared pixels (resized, converted to a new format, or a device colour profile converted to sRGB), even when its encoder is lossless. When a pick removes metadata (`removes metadata: exif, xmp ...`), tell the human before applying: copyright and credit fields live there. Use `--dest` when prepare or convert outputs belong somewhere other than beside the source. `apply` never overwrites a file that is not the one being optimized: when a converted output's name is already taken, or two picks would write one path, it stops before writing anything; ask the human whether to remove the old file or use `--dest`. `apply` writes each pick beside its target, re-measures it there, and only then moves it into place. A MISMATCH line means the pick failed that check and the original was left as it was: stop and investigate. `apply` also stops when ffmpeg or ssimulacra2 changed since `candidates` ran: re-run `candidates`.
7. **Evidence** for a PR: use the format below, then the repository's own PR template.
8. **Clean up** with `IMGOPT clean <dir>` once nothing is left to apply.

## Baselines

Measure against the original, never an already-optimized intermediate. For files a merged PR already compressed, use `--ref-rev <pre-merge-sha>` (a whole batch) or `--ref <file>` (one file). `--ref-rev` extracts each file's content at that commit, and the output shows each baseline's size beside the file's. For `--ref`, extract the original with `git show <pre-merge-sha>:<path> > <workdir>/ref.<ext>`. Neither works on SVG, and `--ref-rev` stops the run when any input is an SVG or did not exist at that commit, so in a folder that mixes them pass the raster files by name. `candidates` refuses to re-measure a file an earlier imgopt lossy pick wrote unless you give a baseline from before that pick, and refuses a baseline that is itself such a pick (a `--ref-rev` after the pick was committed). To check what a reviewer will see, run `IMGOPT compare` on the raw GitHub URLs pinned to the base and head commits.

## What the scripts cannot enforce

- `candidates` measures two files at a time by default; on a machine short of memory pass `--jobs 1`.
- `jpegoptim -m` is not the IJG quality scale: a file another tool calls "q60" may measure about q86 in `inspect`.
- JPEG banding is reported, not gated: look hardest at dark gradients, skies and soft shadows.
- Animated PNGs and PNGs with gAMA/cHRM chunks get lossless candidates only, so they cannot be resized or converted.
- Files marked "untouched" save too little to be worth a binary diff; do not override that for a few bytes.
- Split PRs by the team that owns the files, and keep recompression separate from deletion.

## Evidence format

Lead with the outcome and totals (the summary line's whole-batch figure comes first; quote that, not only the files that change), then a table pinned to commits so "before" stays the old file after merge:

```markdown
| File | Before | After | Method | Luma SSIM | ssimulacra2 |
|---|---|---|---|---|---|
| `path/to/image.png`<br><img src="https://raw.githubusercontent.com/<owner>/<repo>/<base-sha>/path/to/image.png" width="240"> | 46.4 KB | 41.1 KB<br><img src="https://raw.githubusercontent.com/<owner>/<repo>/<head-sha>/path/to/image.png" width="240"> | oxipng | pixel-identical | pixel-identical |
| `path/to/photo.jpg`<br><img src="https://raw.githubusercontent.com/<owner>/<repo>/<base-sha>/path/to/photo.jpg" width="240"> | 232.3 KB | 151.0 KB<br><img src="https://raw.githubusercontent.com/<owner>/<repo>/<head-sha>/path/to/photo.jpg" width="240"> | jpegoptim -m80 | 0.9853 | 84.2 |
```

The Evidence SSIM is a luma SSIM: it does not see colour, which is why ssimulacra2 sits beside it. Quote the "Evidence SSIM (luma)" line `compare` prints. The reviewer check reproduces it with the same ffmpeg build; across builds expect agreement to about 1e-4. `candidates` holds every lossy pick's Evidence SSIM (its "Evidence SSIM (luma)" line, the same number) to the profile floor too, because the JPEG decoders differ by up to about 1e-3; if an Evidence SSIM still falls below the floor (a `--ref` baseline other than the PR's base file, for example), say so plainly instead of rounding. Put butteraugli (when installed) and banding in one methods line. Add the `compare` reviewer command in a `<details>` block. The table's images use raw.githubusercontent.com, which serves public GitHub repositories only; for GitHub Enterprise or private repositories, attach the `sheet` crops to the PR instead, and for Git LFS files use media.githubusercontent.com. State any waived tools. Where `compare` says ffmpeg alone cannot reproduce the number, it prints no Evidence SSIM: quote the gate SSIM instead and write "verified locally with imgopt compare" in place of the reviewer command.
