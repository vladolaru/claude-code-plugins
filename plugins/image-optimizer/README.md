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
5. writes the picks with `imgopt.py apply` after your approval, and re-verifies every written file.

## Profiles

| Profile | Gate | For |
|---|---|---|
| `lossless` (default) | identical pixels; colour profile and orientation kept | logos, icons, anything that must not change |
| `high` | SSIM ≥ 0.98, ssimulacra2 ≥ 80, banding ≤ 3 | images a product or page shows |
| `medium` | SSIM ≥ 0.96, ssimulacra2 ≥ 60, banding ≤ 3 | files kept only so old URLs keep working |

The floors come from a WooCommerce asset session in October 2026 (SSIM floors set by a human reviewer, ssimulacra2 floors confirmed by eye). Banding is gated on palette PNG output; WebP and AVIF output is labelled uncalibrated.

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

`python3 plugins/image-optimizer/scripts/imgopt.py doctor --job recompress --profile high` lists what is missing and prints one install command. On macOS:

```bash
python3 -m pip install --user pillow
brew install mozjpeg jpegoptim oxipng pngquant guetzli gifsicle librsvg ffmpeg jpeg-xl webp libavif
npm install -g svgo
```

ImageOptim.app, if installed, supplies native builds of jpegoptim, the mozjpeg jpegtran, oxipng, pngquant, guetzli and gifsicle. A libjpeg-turbo `jpegtran` or `cjpeg` is never used. Linux package names are best effort and unverified.

## License

MIT — see [LICENSE](../../LICENSE).
