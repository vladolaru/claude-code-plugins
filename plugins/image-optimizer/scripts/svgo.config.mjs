/**
 * SVGO Configuration
 *
 * Based on SVGOMG defaults (https://jakearchibald.github.io/svgomg/)
 * Keeps rendering, ids, roles and aria attributes; candidates checks both.
 *
 * Usage:
 *   svgo --config svgo.config.mjs input.svg -o output.svg
 *   svgo --config svgo.config.mjs -rf ./icons
 */
export default {
  multipass: true,
  // svgo 4's preset-default keeps the viewBox and no longer includes the
  // plugin that removed it, so the old override only produced a warning.
  plugins: [
    {
      name: 'preset-default',
      params: {
        overrides: {
          // Ids are targets of aria-labelledby, CSS and scripts, none of which svgo can see.
          cleanupIds: false,
          // Classes on an inline SVG are hooks for page CSS and scripts svgo cannot see.
          inlineStyles: false,
          // role="img" plus aria-* give an inline SVG its accessible name.
          removeUnknownsAndDefaults: { keepRoleAttr: true, keepAriaAttrs: true },
          // A <desc> is part of an SVG's accessible description, even an editor's "Created with Sketch.".
          removeDesc: false,
          // An empty <g id> can be a script's or a stylesheet's target, like any other id.
          removeEmptyContainers: false,
        },
      },
    },
  ],
};
