# image-optimizer

Lossless image optimization for PNG, JPEG, GIF, and SVG files. Review the savings before applying — no surprises.

## How It Works

```bash
/optimize-images path/to/images
```

1. Scans the directory for PNG, JPEG, GIF, SVG files
2. Runs optimization (ImageOptim for raster, svgo for SVG)
3. Shows before/after comparison per file
4. Asks for confirmation before applying
5. Applies changes only with your approval

```
Found 15 images to optimize:

Optimizing...
  product-hero.png: 1.2 MB -> 856 KB (28% reduction)
  logo.svg: 45 KB -> 12 KB (73% reduction)
  screenshot-1.jpg: 890 KB -> 678 KB (24% reduction)

Total savings: 582 KB (31% reduction)

Apply changes? [Y/n]
```

No quality loss. Just smaller files.

## Typical Savings

| Format | Tool | Reduction |
|--------|------|-----------|
| PNG | ImageOptim | 20-40% |
| JPEG | ImageOptim | 5-15% |
| GIF | ImageOptim | 10-30% |
| SVG | svgo | 40-70% |

## Installation

```bash
/plugin marketplace add vladolaru/claude-code-plugins
/plugin install image-optimizer@vladolaru-claude-code-plugins
```

### Dependencies

```bash
# ImageOptim CLI (macOS)
npm install -g imageoptim-cli

# SVG optimization (all platforms)
npm install -g svgo
```

At least one tool is required. Both for complete format support.

## License

MIT — see [LICENSE](../../LICENSE).
