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

ImageOptim.app, if installed, supplies native builds of jpegoptim, the mozjpeg jpegtran, oxipng, pngquant, guetzli and gifsicle. A libjpeg-turbo `jpegtran` or `cjpeg` is never used, and neither is svgo older than 4 (its defaults drop `viewBox` and `<title>`).

Tested on macOS only. On Linux the install lines `doctor` prints are best effort, and no run has been verified. Inside the Codex sandbox every command works except `sheet --browser`, because headless Chrome cannot start there; open the comparison page instead.

## License

MIT — see [LICENSE](../../LICENSE).
