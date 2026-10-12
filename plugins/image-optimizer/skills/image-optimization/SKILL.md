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

Every command is `python3 "$SKILL_DIR/../../scripts/imgopt.py" <command>`, written `IMGOPT <command>` below. Spell it out in each shell command, or define a function in that same command: `imgopt() { python3 "$SKILL_DIR/../../scripts/imgopt.py" "$@"; }`. Never keep it in a variable and run `$IMGOPT`: zsh, the macOS default shell, does not split an unquoted variable into words, so the command is not found. Use plain `python3`, never `python3 -I` (Pillow may live in user site-packages). `IMGOPT <command> --help` lists every flag.

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

**Selecting files.** `--files <list>` reads one path per line (`-` for stdin). Among measured files, `apply --only`, `apply --files` and `--file` take an absolute path, a path relative to the current folder, or a bare name that is unique; an ambiguous name is refused with the matching paths listed.

**Exit codes.** 0 success; 1 refused, verification failed, or at least one file reported a problem; 2 tooling missing or usage error (nothing was written, except that `--ref-rev` may already have extracted baselines into the working folder). A file that cannot be handled never aborts a batch: it gets its own line saying why, the other files finish, and the command exits 1 at the end. Act on each line by its kind (Rule 4):
- Fix it and re-run: a Git LFS pointer (`git lfs pull` first).
- Ask the human: content that does not match its extension, such as a JPEG named `.png` (renaming it changes its URL).
- Report it as it is: an unreadable file, a 16-bit image (not supported yet), a file the job cannot serve (a GIF, an animated PNG or a gamma-dependent PNG in a resize or a conversion), a `--resize` wider than the image (it is never upscaled) or equal to its width with no larger `--ref` (nothing to resize), a file that is a lossy variant imgopt produced or a baseline that is one (see Baselines), and a file whose every candidate errored (a tool failure, not a quality verdict).

Folder inputs skip git-ignored files, `node_modules`, `vendor`, `bower_components` and imgopt working folders, and say what they skipped.

**Working folder (`--out`).** Get it with `IMGOPT workdir <task-slug>`. It is the same folder in every session (in a sandbox it falls back to `$TMPDIR`), so a re-run reuses everything whose inputs, settings and tool versions are unchanged, and an interrupted run resumes from its last finished candidate. Never the repository: `candidates` refuses a folder inside an input folder or git work tree. Use that folder or a new empty one: `candidates` also refuses a non-empty folder it did not make, and `clean` deletes the whole folder it marked. Every later command acts only on the files the folder's last `candidates` run measured, and `sheet` and `apply` list older records, and those of files that run skipped, as `ignored:`. When the files are placed and the evidence is written, remove it with `IMGOPT clean <dir>`; with `--keep-picks` it keeps each file's record, reference, source copy, pick and choice, for when the human will place them later (the other variants leave the folder, and `choose` brings one back from the store while the store holds it).

**The variant store.** Every measured variant also goes into a store shared by all working folders, at `<cache root>/store`. A run in any folder takes what the store holds instead of encoding again, so measuring a set again in a new folder costs no encoding; the folder gets each file as a hardlink, or a copy where a link fails. At the end of each run `candidates` says how many variants came from the store and how many it encoded, prunes the store (variants unused for 30 days go, then the least recently used until it is under 2 GB) and prints its size and location. `IMGOPT clean --store` prunes it on demand. The store also remembers every lossy variant it produced that changed the file (not one that kept the source's bytes or pixels), and never forgets one: that is the stacking guard in Baselines. Working-folder files can be hard links into the store, so place a variant by copying it, never by moving or hard-linking it.

**Sets.** A set is whatever the human wants reviewed or placed on its own. Give each set its own working folder (`IMGOPT workdir <set>`); after one run over everything, a run per set with the same settings in its own folder is free because of the store. Changing the profile, the gates or `--min-saving` reuses every stored variant, because the gates are evaluated again on reuse; a looser profile may also encode the lower levels a stricter run stopped before (step 8). `--resize`, `--format`, `--assume-srgb`, `--ref` or `--ref-rev`, or a new tool version encodes again. A re-run in the same folder keeps each file's choice while its variant is still measured. One page can cover several folders (`IMGOPT sheet a b`), for a set that needed two runs, such as a profile change or a `--resize` for some of its files. A page refuses a file with results in two of its folders: re-run the main set without that file (free, because of the store), or review the one-file folder on a page of its own.

## Profiles

| Profile | Gate | Use for |
|---|---|---|
| `lossless` (default) | identical pixels, colour profile and orientation kept | anything without a stated reason to change visibly: logos, icons, UI |
| `high` | SSIM ≥ 0.98, ssimulacra2 ≥ 80, banding ≤ 3 | images the product or page shows |
| `medium` | SSIM ≥ 0.96, ssimulacra2 ≥ 60, banding ≤ 3 | files kept only so old URLs keep working, rarely seen |

Banding is gated only on palette PNG output; for other lossy output it is reported and you must view the smooth-area tile. Override a single floor with `--ssim`, `--ss2` or `--band` only when the human asks.

## Method

1. **Tooling first.** Run `IMGOPT doctor` for the job, profile and paths. If it prints BLOCKED, stop and ask the human to install everything it lists: quote what each tool adds and the install line (they can paste it as `! brew install ...`). Recommend installing, because missing tools cost quality; going without one is the human's explicit choice. Only when the human explicitly declines a named tool, pass `--allow-missing <tool>` to `candidates` or `inspect` for exactly that tool, and repeat the waiver in your report (Rule 3). Pillow, ffmpeg and ssimulacra2 (lossy profiles), the target encoder, `cjpegli` (lossy JPEG output) and rsvg-convert (SVG) cannot be waived. `cjpegli` has no package: quote the build line `doctor` prints and wait while the human builds it. Optional tools (butteraugli) never block and need no waiver.
2. **Decide which files matter, then let usage set the profile.** First establish what ships (read the packaging config, for example a `.distignore` or the build's asset list, or ask) and who links each file. A file the repository does not ship gains nothing from being rewritten, and git keeps its old bytes anyway. Measure the whole candidate set once under `high` (`candidates --profile high`, then read the savings lines): it shows what a lossy pass could save, and the per-group runs that follow reuse its variants. Then research usage only for files whose saving is worth it. For each of those, find who loads it. In the repository, search code, CSS, templates and markup. You cannot see outside consumers (stored post content, other repositories, apps, feeds): ask the human whether the URL is public and used elsewhere, and present your search as covering the repository only. A CSS `url()` counts only if markup still carries that selector. A file nothing in the repository renders is a deletion candidate once the human confirms nothing outside uses it, so say so instead of compressing it. Group files by how visible they are, propose a profile per group, and get the human's confirmation.
3. **Display size sets dimensions.** Read the slot size from markup or CSS, or ask. Aim for about 2× the CSS size for high-density screens. Size each file for the largest slot that shows it, and keep cover images uncropped (cropping moves the focal point). Any dimension change needs the human's yes; pass it as `--resize <width>` with `--profile high` or `medium` (the `lossless` profile cannot re-encode pixels, so it refuses a resize or a new format). A resize under `high` can find no pick; the verdict then names the closest candidate's scores, and you offer the human `medium` rather than lowering a floor yourself.
4. **Keep names and URLs** unless the human says they may change. Only then consider `--format webp|avif|jpeg|png` (with `high` or `medium`). Every tile of a converted file is required viewing, and WebP and AVIF are also uncalibrated against these gates. A resized or converted pick can come out larger than the original; `candidates` keeps it (the change was asked for) but marks it LARGER, and you tell the human before placing it.
5. **Measure.** Run `candidates` on the set. Run long batches in the background and wait for the command to finish, without a `sleep` loop. On a machine short of memory, pass `--jobs 1` (the default measures two files at a time). Read the savings lines at the end: choices ranked by bytes saved with the cumulative share, how many save under 1, 3 and 10 KB, and the folders that hold the most. Agree with the human what is worth a changed file; `--min-saving <bytes>` leaves files that save less untouched for a run.
6. **Review.** Run `sheet` and open every tile it marks REQUIRED at its natural size (Rule 2): no "no banding" or "looks the same" for a file you did not view at 1:1. If `sheet` says a lossy choice could not be tiled, keep it out of the review until its crops exist (Rule 2). Then open the page for the human. Each card shows the file's current choice against the original at 100%, a selector over every measured variant (one that fails a gate says so), and the 1:1 crops of the choice the page was built with. A file with no choice shows its first variant. For crops of another variant, `choose` it and run `sheet` again.
7. **Record the verdicts.** The human marks each file keep, reject (leave the original), milder or stronger, with an optional note. A rebuilt page starts from the verdicts and notes already recorded; otherwise lossless choices start as keep, and lossy ones start with no verdict. Switching a file's variant clears that file's verdict and keeps its note, so a verdict always names the variant on screen. "Copy verdicts" gives one block that names its page's folders: pass it to `IMGOPT record --verdicts -` on stdin (folders you name must be the page's). A keep, milder or stronger on a variant other than the current choice makes that variant the choice, and a kept variant smaller than its original is written even when it saves less than the floor.
   - When `record` refuses the block (a `choose` or `candidates` ran since the page was built): rebuild the page and ask for the review again.
   - When it lists a file as `kept without its 1:1 crops`: run the `sheet` line it prints and view that file's REQUIRED crops before placing it (Rule 2).
   - When it lists a file as `kept, but nothing to write`: tell the human why (its reason follows).
8. **Milder or stronger.** For each file marked milder or stronger, run the `choose --milder` or `--stronger` line `record` prints, then `sheet` again and have the human review the new choices. `--milder` takes the next larger variant that passes the gates; `--stronger` takes the next smaller variant, which may fail a gate (Rule 1). Each encoder stops descending after two levels in a row fail the gates, so the variants below a file's pick end two failing levels under each encoder's last pass. `IMGOPT variants <dirs> --file <f>` lists every measured variant, and `choose --label` sets one by name; a chosen variant is written whenever it is smaller than the original, even under the saving floor.
   - When no milder variant passes: the file keeps its original, so ask the human to reject it.
   - When the human wants stronger than any listed variant: measure the file again under `medium`, as a one-file set in its own folder (see Sets).
   - When a variant's file is gone: `choose` restores it from the store, or names the `candidates` run that makes it again.
   - When the ladder never made the variant: run again with a different setting, as a one-file set in its own folder: a `--resize`, or `--assume-srgb` for a PNG whose colour chunks kept it lossless, once the human compared it in a browser and found it renders the same. Keep `--assume-srgb` to that one-file run.
9. **Place the kept files.** "Accepted" means: a lossy choice needs a keep; a lossless choice needs no verdict or a keep; reject, milder and stronger hold either back. `IMGOPT apply <dirs>` writes each accepted choice beside its target, re-measures it there, then moves it into place, and reports every other file as left as it was. A choice counts as lossy whenever it was made from prepared pixels (resized, converted to a new format, or a device colour profile converted to sRGB), even when its encoder is lossless.
   - When a choice removes metadata (`removes ...` in `candidates`, `variants`, `choose`, `apply` and `manifest`, and `metadata_removed` in its JSON): tell the human before placing it, since copyright and credit fields live there.
   - When prepare or convert outputs belong somewhere other than beside the source: pass `--dest`.
   - When you place files another way: copy each variant `IMGOPT manifest <dirs> --accepted` lists to the target after its arrow (`--json` for a script); unlike `apply`, a copy is not re-measured.
   - When `apply` refuses the run, it wrote nothing: lossy choices exist and none has a verdict (get the review); a converted output's name is taken by another file, or two choices would write one path (ask the human whether to remove the old file or use `--dest`); or ffmpeg or ssimulacra2 changed since `candidates` ran (re-run `candidates`).
   - When a line says MISMATCH: the choice failed the re-measure and the original was left as it was; stop and investigate.
10. **Evidence** for whoever reviews the change: use the format below.
11. **Clean up** each working folder with `IMGOPT clean <dir>` once nothing is left to place.

## Baselines

Measure against the original, never an already-optimized intermediate. For files an earlier change already compressed, use `--ref-rev <rev-before-it>` (a whole batch) or `--ref <file>` (one file). `--ref-rev` extracts each file's content at that commit, and the output shows each baseline's size beside the file's. For `--ref`, extract the original with `git show <rev-before-it>:<path> > <workdir>/ref.<ext>`. Neither works on SVG, and `--ref-rev` stops the run when any input is an SVG or did not exist at that commit, so in a folder that mixes them pass the raster files by name. Under a lossy profile `candidates` refuses a file whose bytes are a lossy variant imgopt produced, however it got into place (`apply` or a copy), unless you give a baseline from before that change, and refuses a baseline that is itself such a variant (a `--ref-rev` after the change was committed). To check what a reviewer will see, run `IMGOPT compare` on the raw GitHub URLs pinned to the base and head commits.

## What the scripts cannot enforce

- `jpegoptim -m` is not the IJG quality scale: a file another tool calls "q60" may measure about q86 in `inspect`.
- JPEG banding is reported, not gated: look hardest at dark gradients, skies and soft shadows.
- Animated PNGs, and PNGs whose cICP (other than sRGB) or gAMA/cHRM chunks decide how browsers render them, get lossless candidates only, so they cannot be resized or converted (step 8 says when `--assume-srgb` lifts that).
- `inspect` reports lossless headroom only: a JPEG with none can still save half its size lossily.
- The tool cannot know what ships or who links a file from outside the repository.
- Font-definition SVGs cannot be render-checked, so they stay untouched.
- The banding gate is conservative on flat logos (a score of 4 on a flat wordmark) and applies to `medium` as strictly as to `high`; the human can still keep a gate-failing variant on the page.
- Keep each set small enough that every required tile gets viewed.
- Leave files marked "untouched" as they are: they save too little to be worth a binary diff.

## Evidence for a reviewer

Lead with the outcome and totals (the summary line's whole-batch figure comes first; quote that, not only the files that change), then a table pinned to two revisions, the base and the head, so "before" stays the old file once the change lands:

```markdown
| File | Before | After | Method | Luma SSIM | ssimulacra2 |
|---|---|---|---|---|---|
| `path/to/image.png`<br><img src="https://raw.githubusercontent.com/<owner>/<repo>/<base-sha>/path/to/image.png" width="240"> | 46.4 KB | 41.1 KB<br><img src="https://raw.githubusercontent.com/<owner>/<repo>/<head-sha>/path/to/image.png" width="240"> | oxipng | pixel-identical | pixel-identical |
| `path/to/photo.jpg`<br><img src="https://raw.githubusercontent.com/<owner>/<repo>/<base-sha>/path/to/photo.jpg" width="240"> | 232.3 KB | 151.0 KB<br><img src="https://raw.githubusercontent.com/<owner>/<repo>/<head-sha>/path/to/photo.jpg" width="240"> | jpegoptim -m80 | 0.9853 | 84.2 |
```

The Evidence SSIM is a luma SSIM: it does not see colour, which is why ssimulacra2 sits beside it. Quote the "Evidence SSIM (luma)" line `compare` prints. The reviewer check reproduces it with the same ffmpeg build; across builds expect agreement to about 1e-4. `candidates` holds every lossy pick's Evidence SSIM (its "Evidence SSIM (luma)" line, the same number) to the profile floor too, because the JPEG decoders differ by up to about 1e-3; if an Evidence SSIM still falls below the floor (a `--ref` baseline other than the base revision's file, for example), say so plainly instead of rounding. Put butteraugli (when installed) and banding in one methods line. Add the `compare` reviewer command in a `<details>` block. The table's images use raw.githubusercontent.com, which serves public GitHub repositories only; for GitHub Enterprise or private repositories, give whoever reviews the change the `sheet` crops instead, and for Git LFS files use media.githubusercontent.com. State any waived tools. Where `compare` says ffmpeg alone cannot reproduce the number, it prints no Evidence SSIM: quote the gate SSIM instead and write "verified locally with imgopt compare" in place of the reviewer command.
