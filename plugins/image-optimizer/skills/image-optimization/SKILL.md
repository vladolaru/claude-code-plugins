---
name: image-optimization
description: Use when optimizing, recompressing, resizing, converting or auditing images (PNG, JPEG, GIF, SVG, WebP, AVIF) in a repository or for a web page. Picks the smallest file per image that passes a measured quality profile, checks banding, and shows a 1:1 review before anything is written.
---

# Image Optimization

Scripts do the mechanics (encoding, measuring, gating, crops, writing); you do the judgment (where an image is used, how big it is shown, which profile); the human gives the verdict on anything that changes pixels.

## Rules

These hold over every step below.

1. **The human decides every pixel change.** A variant that changes pixels is written only after the human kept that exact variant on the page (`apply` enforces this; a copy you make does not), and a gate-failing variant becomes a choice only on their word.
2. **View before you vouch.** Open every tile `sheet` marks REQUIRED at its natural size before calling a choice good, and list the tiles you viewed. Viewing is not measuring: a reported-only metric (JPEG, WebP, AVIF banding) is never "passed".
3. **The human installs tools.** Quote what `doctor` lists, with what each tool adds and its install line, and wait.
4. **Every problem line reaches the human.** Pass on each per-file line a command prints.

## The tool

Every command is `python3 "$SKILL_DIR/../../scripts/imgopt.py" <command>`, written `IMGOPT <command>` below. Spell it out in each shell command, or define a function in that same command: `imgopt() { python3 "$SKILL_DIR/../../scripts/imgopt.py" "$@"; }`. Never run it from a shell variable (`$IMGOPT`): zsh does not word-split an unquoted variable, so the command is not found. Use plain `python3`, never `python3 -I` (Pillow may live in user site-packages). `IMGOPT <command> --help` lists every flag.

| Command | Use it to |
|---|---|
| `doctor --job <audit\|recompress\|prepare\|convert\|compare> --profile <p> [--format f] [paths]` | Check tools before any work (`prepare` = resizing, `convert` = a new format) |
| `inspect <paths> [--json]` | Report facts and lossless headroom; changes nothing (the audit job) |
| `workdir <task>` | A stable working folder for `--out` |
| `candidates <paths> [--files <list>] --out <dir> --profile <p> [--resize W] [--format f] [--ref file \| --ref-rev REV] [--min-saving <bytes>] [--assume-srgb] [--jobs N]` | Try encoder settings per file, measure, gate, and set each file's default choice |
| `sheet <dir>...` | 1:1 tiles for you, one review page for the human over every folder named |
| `variants <dir>... --file <f>` | Every measured variant of one file |
| `choose <dir>... --file <f> (--label L \| --milder \| --stronger \| --default)` | Set one file's variant |
| `record [<dir>...] --verdicts -` | Store the verdict block the human pasted (in the folders the block names) |
| `apply <dir>... [--only a,b \| --files list] [--dest dir]` | Write the accepted choices and re-measure them |
| `manifest <dir>... [--accepted] [--json]` | Each file's chosen variant path, its target and the metadata it removes |
| `compare <ref> <new>` | Measure any pair (path, `git:<rev>:<path>`, URL) and get the reviewer check |
| `clean <dir> [--keep-picks]` | Delete the working folder, or with `--keep-picks` only its unchosen variants |
| `clean --store [--older-than DAYS]` | Prune the shared variant store |

**Selecting files.** `--files <list>` takes one path per line (`-` for stdin). `apply --only`, `apply --files` and `--file` take an absolute path, a path relative to the current folder, or a bare name that is unique among the measured files.

**Exit codes.** 0 success; 1 refused, verification failed, or at least one file reported a problem; 2 tooling missing or usage error (nothing was written). A file that cannot be handled never aborts a batch: it gets its own line saying why, the other files finish, and the command exits 1. Act on each line by its kind (Rule 4):
- Fix it and re-run: a Git LFS pointer.
- Ask the human: content that does not match its extension, such as a JPEG named `.png` (renaming it changes its URL).
- Report every other line as it is, with the reason it gives: an unreadable or 16-bit file, a job the file's format cannot take, a resize it does not need, a lossy pass that would stack on an earlier one, or every candidate erroring (a tool failure, not a quality verdict).

Folder inputs skip git-ignored files, `node_modules`, `vendor`, `bower_components` and imgopt working folders, and say what they skipped.

**Working folder (`--out`).** Get it with `IMGOPT workdir <task-slug>`. It is the same folder in every session, so a re-run reuses every candidate whose inputs, settings and tool versions are unchanged, and an interrupted run resumes from its last finished candidate. `candidates` refuses any other folder that is inside the repository or holds files it did not make. Every later command acts only on the files the folder's last `candidates` run measured; older records are listed as `ignored:`. `clean --keep-picks` keeps what `sheet` and `apply` read, for when the human will place the files later.

**The variant store.** Every measured variant also goes into a store shared by all working folders, so a run in any folder reuses what an earlier run measured instead of encoding again. A working folder's variant files may be hard links into the store, so place a variant by copying it, never by moving or hard-linking it.

**Sets.** A set is whatever the human wants reviewed or placed on its own. Give each set its own working folder (`IMGOPT workdir <set>`); after one run over everything, a run per set with the same settings is free because of the store. A profile, gate or `--min-saving` change also reuses the stored variants (a looser profile may encode the lower levels a stricter run stopped before, step 8); `--resize`, `--format`, `--assume-srgb`, a baseline or a new tool version encodes again. A re-run in the same folder keeps each file's choice while its variant is still measured. One page can cover several folders (`IMGOPT sheet a b`), for a set that needed two runs. A page refuses a file with results in two of its folders: re-run the main set without that file, or review the one-file folder on a page of its own.

## Profiles

| Profile | Gate | Use for |
|---|---|---|
| `lossless` (default) | identical pixels, colour profile and orientation kept | anything without a stated reason to change visibly: logos, icons, UI |
| `high` | SSIM ≥ 0.98, ssimulacra2 ≥ 80, banding ≤ 3 | images the product or page shows |
| `medium` | SSIM ≥ 0.96, ssimulacra2 ≥ 60, banding ≤ 3 | files kept only so old URLs keep working, rarely seen |

Banding is gated only on palette PNG output; elsewhere it is reported and the smooth-area tile is REQUIRED viewing: look hardest at dark gradients, skies and soft shadows. Override a single floor with `--ssim`, `--ss2` or `--band` only when the human asks.

## Method

1. **Tooling first.** Run `IMGOPT doctor` for the job, profile and paths. If it prints BLOCKED, stop and follow Rule 3 (the human can paste an install line as `! brew install ...`); recommend installing, because missing tools cost quality. `doctor` lists each tool as required (no waiver), quality (waivable) or optional (never blocks). Only when the human explicitly declines a named quality tool, pass `--allow-missing <tool>` to `candidates` or `inspect` for exactly that tool.
2. **Decide which files matter, then let usage set the profile.** First establish what ships (read the packaging config, for example a `.distignore` or the build's asset list, or ask): a file the repository does not ship gains nothing from being rewritten. Measure the whole candidate set once under `high`: its savings lines show what a lossy pass could save, and the per-group runs that follow reuse its variants. Then research usage only for files whose saving is worth it: for each, find who loads it by searching code, CSS, templates and markup in the repository. You cannot see outside consumers (stored post content, other repositories, apps, feeds): ask the human whether the URL is public and used elsewhere, and present your search as covering the repository only. A CSS `url()` counts only if markup still carries that selector. A file nothing in the repository renders is a deletion candidate once the human confirms nothing outside uses it, so say so instead of compressing it. Group files by how visible they are, propose a profile per group, and get the human's confirmation.
3. **Display size sets dimensions.** Read the slot size from markup or CSS, or ask. Aim for about 2× the CSS size for high-density screens. Size each file for the largest slot that shows it, and keep cover images uncropped (cropping moves the focal point). Any dimension change needs the human's yes; pass it as `--resize <width>` under `high` or `medium`. When a resize finds no pick under `high`, offer the human `medium` (the verdict names the closest candidate and suggests it) rather than lowering a floor yourself.
4. **Keep names and URLs** unless the human says they may change. Only then consider `--format webp|avif|jpeg|png` (with `high` or `medium`; WebP and AVIF are uncalibrated against these gates, so `sheet` marks every tile of a converted file REQUIRED).
5. **Measure.** Run `candidates` on the set. Run long batches in the background and wait for the command to finish, without a `sleep` loop; on a machine short of memory, pass `--jobs 1`. Read the savings lines at the end, agree with the human what saving is worth a changed file, and pass it as `--min-saving <bytes>` on the runs that follow.
6. **Review.** Run `sheet` and open every tile it marks REQUIRED at its natural size (Rule 2), then open the page for the human: each card shows the choice against the original at 100%, a selector over every measured variant, and the 1:1 crops of the choice the page was built with. For crops of another variant, `choose` it and run `sheet` again.
7. **Record the verdicts.** The human marks each file keep, reject (leave the original), milder or stronger, with an optional note; a rebuilt page starts from the verdicts already recorded, and lossless choices start as keep. A verdict names the variant on screen, and a keep, milder or stronger on a variant other than the current choice makes that variant the choice. Pass the block "Copy verdicts" gives to `IMGOPT record --verdicts -` on stdin; `record` prints the `choose`, `sheet` and `apply` lines that follow.
   - When `record` refuses the block or lists a file as kept without its 1:1 crops or with nothing to write: do what the line says (Rule 2 for the crops, Rule 4 for the rest).
8. **Milder or stronger.** For each file marked milder or stronger, run the `choose` line `record` prints, then `sheet` again and have the human review the new choices. `--milder` takes the next larger variant that passes the gates; `--stronger` takes the next smaller variant, which may fail a gate (Rule 1). Each encoder stops descending after two levels in a row fail the gates, so a file's variants end two failing levels under each encoder's last pass. `IMGOPT variants <dirs> --file <f>` lists every measured variant, and `choose --label` sets one by name; a chosen variant is written whenever it is smaller than the original, even under the saving floor.
   - When the human wants stronger than any listed variant: measure the file again under `medium`, as a one-file set in its own folder (see Sets).
   - When the ladder never made the variant: run again as a one-file set with the setting that makes it, a `--resize` or, for a PNG whose colour chunks kept it lossless, `--assume-srgb`, the latter only once the human compared the file in a browser and found it renders the same.
9. **Place the kept files.** "Accepted" means: a lossy choice needs a keep; a lossless choice needs no verdict or a keep; reject, milder and stronger hold either back. `IMGOPT apply <dirs>` writes each accepted choice, re-measures it in place, and reports every other file as left as it was. A choice made from prepared pixels (resized, converted, or a device colour profile converted to sRGB) counts as lossy even when its encoder is lossless.
   - When a line says a choice `removes` metadata: tell the human before placing it, since copyright and credit fields live there.
   - When you place files another way: copy each variant `IMGOPT manifest <dirs> --accepted` lists to the target after its arrow (`--json` for a script); unlike `apply`, a copy is not re-measured.
   - When `apply` refuses the run, it wrote nothing: do what its message says, and ask the human before removing a file that is in the way of a converted output.
   - When a line says MISMATCH: the original was left as it was; stop and investigate.
10. **Evidence** for whoever reviews the change: use the format below.
11. **Clean up** each working folder with `IMGOPT clean <dir>` once nothing is left to place.

## Baselines

Measure against the original, never an already-optimized intermediate. For files an earlier change already compressed, use `--ref-rev <rev-before-it>` (a whole batch; it extracts each file's content at that commit) or `--ref <file>` (one file, extracted with `git show <rev-before-it>:<path> > <workdir>/ref.<ext>`). Neither works on SVG, and `--ref-rev` stops the run when any input is an SVG or did not exist at that commit, so in a folder that mixes them pass the raster files by name. Under a lossy profile `candidates` refuses a file that is itself a lossy variant imgopt produced, however it got into place, unless it has such a baseline, and refuses a baseline that is one. To check what a reviewer will see, run `IMGOPT compare` on the raw GitHub URLs pinned to the base and head commits.

## What the scripts cannot enforce

- `inspect` reports lossless headroom only: a JPEG with none can still save half its size lossily.
- Font-definition SVGs cannot be render-checked, so they stay untouched.
- The banding gate is conservative on flat logos and as strict under `medium` as under `high`; the human can still keep a gate-failing variant on the page.
- Keep each set small enough that every required tile gets viewed.
- Leave files marked "untouched" as they are: they save too little to be worth a binary diff.

## Evidence for a reviewer

Lead with the outcome and totals (quote the summary line's whole-batch figure, not only the files that change), then a table pinned to the base and head revisions, so "before" stays the old file once the change lands:

```markdown
| File | Before | After | Method | Luma SSIM | ssimulacra2 |
|---|---|---|---|---|---|
| `path/to/image.png`<br><img src="https://raw.githubusercontent.com/<owner>/<repo>/<base-sha>/path/to/image.png" width="240"> | 46.4 KB | 41.1 KB<br><img src="https://raw.githubusercontent.com/<owner>/<repo>/<head-sha>/path/to/image.png" width="240"> | oxipng | pixel-identical | pixel-identical |
| `path/to/photo.jpg`<br><img src="https://raw.githubusercontent.com/<owner>/<repo>/<base-sha>/path/to/photo.jpg" width="240"> | 232.3 KB | 151.0 KB<br><img src="https://raw.githubusercontent.com/<owner>/<repo>/<head-sha>/path/to/photo.jpg" width="240"> | jpegoptim -m80 | 0.9853 | 84.2 |
```

Quote the "Evidence SSIM (luma)" line `compare` prints, and tell the reviewer that other ffmpeg builds can differ by about 1e-4. If an Evidence SSIM falls below the profile floor (a `--ref` baseline other than the base revision's file, for example), say so plainly instead of rounding. Put butteraugli (when installed) and banding in one methods line. Add the `compare` reviewer command in a `<details>` block. The table's images use raw.githubusercontent.com, which serves public GitHub repositories only; for GitHub Enterprise or private repositories give the reviewer the `sheet` crops instead, and for Git LFS files use media.githubusercontent.com. State any waived tools.
