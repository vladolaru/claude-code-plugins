# image-optimizer

Lossless image optimization for PNG, JPEG, GIF, and SVG files with review-before-apply workflow.

**Current Version:** 1.1.0

---

## What It Does

Optimizes images without quality loss using industry-standard tools:
- **ImageOptim** - PNG, JPEG, GIF compression
- **svgo** - SVG optimization

**Key feature:** Review-before-apply workflow - see size savings before committing changes.

---

## Installation

### Add Marketplace
```bash
/plugin marketplace add vladolaru/claude-code-plugins
```

### Install Plugin
```bash
/plugin install image-optimizer@vladolaru-claude-code-plugins
```

### Install Dependencies
```bash
# macOS - ImageOptim CLI
npm install -g imageoptim-cli
# Or: brew install imageoptim-cli

# SVG optimization
npm install -g svgo
```

---

## Usage

### Optimize Images in Directory

```bash
/optimize-images path/to/images
```

**Workflow:**
1. Scans directory for PNG, JPEG, GIF, SVG files
2. Runs optimization
3. Shows before/after size comparison
4. Asks for confirmation before applying changes
5. Commits optimized images

### Example Output

```
Found 15 images to optimize:
  - product-hero.png (1.2 MB)
  - logo.svg (45 KB)
  - screenshot-1.jpg (890 KB)
  ...

Optimizing...
✅ product-hero.png: 1.2 MB → 856 KB (28% reduction)
✅ logo.svg: 45 KB → 12 KB (73% reduction)
✅ screenshot-1.jpg: 890 KB → 678 KB (24% reduction)

Total savings: 582 KB (31% reduction)

Apply changes? [Y/n]
```

---

## Features

### Lossless Compression
- No quality degradation
- Removes metadata and unnecessary data
- Optimizes encoding

### Batch Processing
- Recursive directory scanning
- Multiple formats in one pass
- Parallel optimization

### Safe Workflow
- Preview size changes before applying
- Confirmation required
- Can be cancelled without changes

### Size Reporting
- Before/after comparison per file
- Percentage reduction
- Total savings summary

---

## Supported Formats

| Format | Tool | Typical Reduction |
|--------|------|-------------------|
| PNG | ImageOptim | 20-40% |
| JPEG | ImageOptim | 5-15% |
| GIF | ImageOptim | 10-30% |
| SVG | svgo | 40-70% |

---

## Requirements

**Required:**
- imageoptim-cli OR svgo (at least one)

**Optional:**
- Both tools for complete format support

**Platforms:**
- macOS (ImageOptim)
- Linux/Windows (svgo only)

---

## Use Cases

- Optimize images before committing to repo
- Reduce asset sizes for web performance
- Batch optimize screenshot directories
- SVG cleanup for icon libraries
- Prepare images for production deployment

---

## Version History

See [CHANGELOG.md](./CHANGELOG.md) for detailed version history.

**Latest:** v1.1.0 - Converted to command for easier invocation

---

## License

MIT License - See [LICENSE](../../LICENSE)

---

## Author

**Vlad Olaru** - [@vladolaru](https://github.com/vladolaru)

**Repository:** https://github.com/vladolaru/claude-code-plugins
