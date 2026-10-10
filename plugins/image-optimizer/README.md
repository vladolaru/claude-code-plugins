# image-optimizer

Measured image optimization for PNG, JPEG, GIF and SVG, with WebP and AVIF output on request. Every candidate is measured against a quality profile, the smallest one that passes wins, and nothing that changes pixels is written before you approve it on a side-by-side page.

## How it works

```bash
/optimize-images path/to/images what they are used for
```

The command loads the `image-optimization` skill, which:

1. checks the tools the job needs and asks you to install any that are missing;
2. works out where each image is used and how large it is shown, and proposes a profile per group;
3. runs `imgopt.py candidates`: a ladder of encoder settings per file, each measured with SSIM, ssimulacra2 and a banding score;
4. builds 1:1 review crops and a comparison page where you approve each lossy pick, with `imgopt.py sheet`;
5. writes the picks with `imgopt.py apply` and re-measures every written file: lossless picks apply once you have confirmed the profile, lossy picks only when you approved them on the comparison page.

The candidates, crops and page live in a working folder outside the repository (`imgopt.py workdir <task>`: under `~/Library/Caches/imgopt/work/` on macOS, or `$IMGOPT_CACHE` when set), so a later session reuses them; `imgopt.py clean <folder>` removes it.

## Profiles

| Profile | Gate | For |
|---|---|---|
| `lossless` (default) | identical pixels; colour profile and orientation kept | logos, icons, anything that must not change |
| `high` | SSIM ≥ 0.98, ssimulacra2 ≥ 80, banding ≤ 3 | images a product or page shows |
| `medium` | SSIM ≥ 0.96, ssimulacra2 ≥ 60, banding ≤ 3 | files kept only so old URLs keep working |

The floors were set in one WooCommerce asset session in October 2026 (SSIM floors by a human reviewer, ssimulacra2 floors confirmed by eye), then tested on a 386-file benchmark corpus of photos, screenshots, illustrations, icons and odd inputs, where a reviewer accepted 94 of 97 picks sampled closest to the floors. Banding is gated on palette PNG output only: on the corpus no threshold separated rejected JPEG or WebP picks from accepted ones. WebP and AVIF output is labelled uncalibrated. [`docs/design.md`](docs/design.md) records where each floor and rule came from, the acceptance evidence and the known gaps; read it before changing a floor, the pick rules or the tooling policy.

## Installation

### Claude Code

```bash
/plugin marketplace add vladolaru/claude-code-plugins
/plugin install image-optimizer@vladolaru-claude-code-plugins
```

### Codex

```bash
codex plugin marketplace add vladolaru/claude-code-plugins
codex plugin add image-optimizer@vladolaru-claude-code-plugins
```

Use `/optimize-images ...` in Claude Code or `$image-optimizer:optimize-images ...` in Codex.

### Dependencies

`python3 plugins/image-optimizer/scripts/imgopt.py doctor --job recompress --profile high` lists what is missing and prints the install lines for it (a `brew install` line, plus pip or npm lines when Pillow or svgo is missing). On macOS, all of them:

```bash
python3 -m pip install --user pillow
brew install mozjpeg jpegoptim oxipng pngquant gifsicle librsvg ffmpeg jpeg-xl webp libavif
npm install -g svgo   # svgo 4 or newer
```

**jpegli, for any lossy JPEG output.** jpegli's `cjpegli` is required whenever a `high` or `medium` job writes a JPEG, and it cannot be waived: on the benchmark corpus it was the most-picked JPEG encoder, and without it the `high` ladder saved 4.6 to 15 points less on photos. No package manager ships it, so build it once (needs git, cmake and a C++ compiler; `brew install cmake` if missing) and put the binary on `PATH`:

```bash
git clone --recursive https://github.com/google/jpegli && cd jpegli
cmake -B build -DCMAKE_BUILD_TYPE=Release -DBUILD_TESTING=OFF -DBUILD_SHARED_LIBS=OFF \
  -DJPEGLI_ENABLE_OPENEXR=OFF -DJPEGLI_BUNDLE_LIBPNG=ON \
  -DCMAKE_DISABLE_FIND_PACKAGE_GIF=ON -DCMAKE_DISABLE_FIND_PACKAGE_JPEG=ON
cmake --build build --target cjpegli --parallel    # then copy build/tools/cjpegli somewhere on PATH
```

These flags make a binary that loads only system libraries, so it keeps working after you delete the build folder or upgrade Homebrew.

ImageOptim.app, if installed, is the first choice for jpegoptim, because its build is linked to mozjpeg and Homebrew's is not. For the mozjpeg jpegtran, pngquant and gifsicle it is only a fallback when they are not on `PATH`; its copies date from 2023, and `doctor` suggests Homebrew's pngquant and gifsicle when it finds only the bundled ones. A libjpeg-turbo `jpegtran` or `cjpeg` is never used, and neither is svgo older than 4 (its defaults drop `viewBox` and `<title>`) or oxipng older than 10, which includes the bundled oxipng 9 (its zopfli mode is about ten times slower).

Tested on macOS only. On Linux the install lines `doctor` prints are best effort, and no run has been verified. Inside the Codex sandbox the working folder falls back to `$TMPDIR`, and `--jobs` falls back to one file at a time when the sandbox denies a process pool. Both fallbacks are designed for that sandbox but have not been re-run there yet; the last Codex run predates them.

## License

MIT — see [LICENSE](../../LICENSE).
