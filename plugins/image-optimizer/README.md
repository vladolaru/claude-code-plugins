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
4. builds 1:1 difference tiles and a comparison page with `imgopt.py sheet`;
5. writes the picks with `imgopt.py apply` and re-verifies every written file: lossless picks apply once you have confirmed the profile, lossy picks only after you approve them on the comparison page.

## Profiles

| Profile | Gate | For |
|---|---|---|
| `lossless` (default) | identical pixels; colour profile and orientation kept | logos, icons, anything that must not change |
| `high` | SSIM ≥ 0.98, ssimulacra2 ≥ 80, banding ≤ 3 | images a product or page shows |
| `medium` | SSIM ≥ 0.96, ssimulacra2 ≥ 60, banding ≤ 3 | files kept only so old URLs keep working |

The floors come from a WooCommerce asset session in October 2026 (SSIM floors set by a human reviewer, ssimulacra2 floors confirmed by eye). Banding is gated on palette PNG output; WebP and AVIF output is labelled uncalibrated. [`docs/design.md`](docs/design.md) records where each floor and rule came from, the acceptance evidence and the known gaps; read it before changing a floor, the pick rules or the tooling policy.

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
brew install mozjpeg jpegoptim oxipng pngquant guetzli gifsicle librsvg ffmpeg jpeg-xl webp libavif
npm install -g svgo   # svgo 4 or newer
```

**For the best JPEG results, build jpegli.** The lossy JPEG ladder uses jpegli's `cjpegli`, which no package manager ships yet. On 31 test photos it gave the smallest passing JPEG for 13, and the `high` ladder saved 36.3% with it against 34.6% without. Lossy JPEG jobs stop until it is built or you choose to go without it (`--allow-missing cjpegli`, which every output then repeats). Build it once (needs git, cmake and a C++ compiler; `brew install cmake` if missing) and put the binary on `PATH`:

```bash
git clone --recursive https://github.com/google/jpegli && cd jpegli
cmake -B build -DCMAKE_BUILD_TYPE=Release -DBUILD_TESTING=OFF -DBUILD_SHARED_LIBS=OFF \
  -DJPEGLI_ENABLE_OPENEXR=OFF -DJPEGLI_BUNDLE_LIBPNG=ON \
  -DCMAKE_DISABLE_FIND_PACKAGE_GIF=ON -DCMAKE_DISABLE_FIND_PACKAGE_JPEG=ON
cmake --build build --target cjpegli --parallel    # then copy build/tools/cjpegli somewhere on PATH
```

These flags make a binary that loads only system libraries, so it keeps working after you delete the build folder or upgrade Homebrew.

ImageOptim.app, if installed, is the first choice for jpegoptim, because its build is linked to mozjpeg and Homebrew's is not. For the mozjpeg jpegtran, oxipng, pngquant, guetzli and gifsicle it is only a fallback when they are not on `PATH`; its copies date from 2023, and `doctor` suggests Homebrew's oxipng, pngquant and gifsicle when it finds only the bundled ones. A libjpeg-turbo `jpegtran` or `cjpeg` is never used, and neither is svgo older than 4 (its defaults drop `viewBox` and `<title>`).

Tested on macOS only. On Linux the install lines `doctor` prints are best effort, and no run has been verified. Inside the Codex sandbox every command works.

## License

MIT — see [LICENSE](../../LICENSE).
