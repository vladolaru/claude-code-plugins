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
| `candidates <paths> --out <dir> --profile <p> [--resize W] [--format f] [--ref file]` | Try encoder settings per file, measure, gate, pick |
| `sheet <dir> [--alt <label>] [--browser]` | 1:1 tiles for you, a comparison page for the human |
| `apply <dir> [--approved] [--only a,b] [--dest dir]` | Write picks and re-verify them |
| `compare <ref> <new>` | Measure any pair (path, `git:<rev>:<path>`, URL) and get the reviewer check |

**Exit codes.** 0 success; 1 refused, verification failed, or at least one file reported a problem; 2 tooling missing or usage error (nothing was written). An unreadable file never aborts a batch: it gets its own line, the other files finish, and the command exits 1 at the end. The same holds for a 16-bit image (not supported yet), a `--resize` wider than the image (it is never upscaled), and a file whose every candidate errored (a tool failure, not a quality verdict). Report those lines to the human; do not hide them.

**Working folder (`--out`).** Claude Code: a folder in the session scratchpad. Codex: `$TMPDIR/image-optimization/<task-slug>/`. Never `/tmp`, never inside the repository. Re-running `candidates` on the same folder reuses everything whose inputs, settings and tool versions are unchanged.

## Profiles

| Profile | Gate | Use for |
|---|---|---|
| `lossless` (default) | identical pixels, colour profile and orientation kept | anything without a stated reason to change visibly: logos, icons, UI |
| `high` | SSIM ≥ 0.98, ssimulacra2 ≥ 80, banding ≤ 3 | images the product or page shows |
| `medium` | SSIM ≥ 0.96, ssimulacra2 ≥ 60, banding ≤ 3 | files kept only so old URLs keep working, rarely seen |

Banding is gated only on palette PNG output; for other lossy output it is reported and you must view the smooth-area tile. Override a single floor with `--ssim`, `--ss2` or `--band` only when the human asks.

## Method

1. **Tooling first.** Run `IMGOPT doctor` for the job, profile and paths. If it prints BLOCKED, stop and ask the human to install everything it lists: quote what each tool adds and the install line (they can paste it as `! brew install ...`). Do not present "continue without it" as an equal choice; missing tools cost quality. Only when the human explicitly declines a named tool, pass `--allow-missing <tool>` to `candidates` or `inspect` for exactly that tool, and repeat the waiver in your report. Never install anything yourself. Pillow, ffmpeg and ssimulacra2 (lossy profiles), the target encoder, and rsvg-convert (SVG) cannot be waived.
2. **Usage sets the profile.** For each file find who loads it: code, CSS, templates, markup, and outside consumers when the URL is public (stored post content, other repositories, apps, feeds). A CSS `url()` counts only if markup still carries that selector; a file nothing renders is a deletion candidate, so say so instead of compressing it. Group files by how visible they are, propose a profile per group, and get the human's confirmation.
3. **Display size sets dimensions.** Read the slot size from markup or CSS, or ask. Aim for about 2× the CSS size for high-density screens. Do not shrink a file that a larger slot elsewhere also uses, and do not crop cover images (the focal point moves). Any dimension change needs the human's yes; pass it as `--resize <width>` with `--profile high` or `medium` (the `lossless` profile cannot re-encode pixels, so it refuses a resize or a new format).
4. **Keep names and URLs** unless the human says they may change. Only then consider `--format webp|avif|jpeg|png` (with `high` or `medium`). Every tile of a converted file is required viewing, and WebP and AVIF are also uncalibrated against these gates.
5. **Run `candidates`, then `sheet`.** Run long batches in the background and wait for completion; never poll with `sleep`. Open every tile `sheet` marks REQUIRED at its natural size before calling a pick good, and say which files you viewed and which you did not. Never write "no banding" or "looks the same" for a file you did not view at 1:1. If `sheet` says a lossy pick could not be tiled, do not put it forward for approval. Open the comparison page for the human. Lossy picks wait for their explicit approval there.
6. **Apply.** `IMGOPT apply <dir>` for lossless picks; add `--approved` only after the human approved the lossy picks on the page. A pick counts as lossy whenever it was made from prepared pixels (resized, converted to a new format, or a device colour profile converted to sRGB), even when its encoder is lossless. Use `--dest` when prepare or convert outputs belong somewhere other than beside the source. `apply` never overwrites a file that is not the one being optimized: when a converted output's name is already taken, or two picks would write one path, it stops before writing anything; ask the human whether to remove the old file or use `--dest`. `apply` writes each pick beside its target, re-verifies it there, and only then moves it into place. A MISMATCH line means the pick failed that check and the original was left as it was: stop and investigate.
7. **Evidence** for a PR: use the format below, then the repository's own PR template.

## Baselines

Measure against the original, never an already-optimized intermediate. For a follow-up on files a merged PR already compressed, extract the pre-merge version (`git show <pre-merge-sha>:<path> > <workdir>/ref.<ext>`) and pass it with `--ref` (one input file only, not for SVG). To check what a reviewer will see, run `IMGOPT compare` on the raw GitHub URLs pinned to the base and head commits.

## What the scripts cannot enforce

- guetzli is strong on larger photos and softens small thumbnails; it takes seconds to minutes per file and gigabytes of memory on multi-megapixel images.
- Never stack two lossy passes; re-encode from the original.
- `jpegoptim -m` is not the IJG quality scale: a file another tool calls "q60" may measure about q86 in `inspect`.
- On sources that are already around q86, guetzli's advantage mostly disappears.
- JPEG banding is reported, not gated: look hardest at dark gradients, skies and soft shadows.
- Files marked "untouched" save too little to be worth a binary diff; do not override that for a few bytes.
- Split PRs by the team that owns the files, and keep recompression separate from deletion.

## Evidence format

Lead with the outcome and totals, then a table pinned to commits so "before" stays the old file after merge:

```markdown
| File | Before | After | Method | Quality |
|---|---|---|---|---|
| `path/to/image.png`<br><img src="https://raw.githubusercontent.com/<owner>/<repo>/<base-sha>/path/to/image.png" width="240"> | 46.4 KB | 41.1 KB<br><img src="https://raw.githubusercontent.com/<owner>/<repo>/<head-sha>/path/to/image.png" width="240"> | oxipng | pixel-identical |
```

Show only SSIM (the "Evidence SSIM" `compare` prints, which the reviewer check reproduces digit for digit) or "pixel-identical" in the table; if the Evidence SSIM falls below the profile floor while the gate SSIM passed (the JPEG decoders differ by up to about 1e-3), say so plainly instead of rounding; put ssimulacra2, butteraugli (when installed) and banding in one methods line. Add the `compare` reviewer command in a `<details>` block. State any waived tools. Where `compare` says ffmpeg alone cannot reproduce the number, it prints no Evidence SSIM: quote the gate SSIM instead and write "verified locally with imgopt compare" in place of the reviewer command.
