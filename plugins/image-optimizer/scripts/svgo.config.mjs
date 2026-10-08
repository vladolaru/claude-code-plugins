/**
 * SVGO Configuration
 *
 * Based on SVGOMG defaults (https://jakearchibald.github.io/svgomg/)
 * Safe, lossless optimization that preserves visual fidelity.
 *
 * Usage:
 *   svgo --config svgo.config.mjs input.svg -o output.svg
 *   svgo --config svgo.config.mjs -rf ./icons
 */
export default {
  multipass: true,
  // svgo 4's preset-default keeps the viewBox and no longer includes the
  // plugin that removed it, so the old override only produced a warning.
  plugins: ['preset-default'],
};
