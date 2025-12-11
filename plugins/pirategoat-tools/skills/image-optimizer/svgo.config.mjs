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
  plugins: [
    {
      name: 'preset-default',
      params: {
        overrides: {
          // Keep viewBox - essential for responsive SVGs
          removeViewBox: false,
        },
      },
    },
    // Plugins enabled by default in SVGOMG (already in preset-default):
    // - removeDoctype
    // - removeXMLProcInst
    // - removeComments
    // - removeMetadata
    // - removeEditorsNSData
    // - cleanupAttrs
    // - mergeStyles
    // - inlineStyles
    // - minifyStyles
    // - cleanupIds
    // - removeUselessDefs
    // - cleanupNumericValues
    // - convertColors
    // - removeUnknownsAndDefaults
    // - removeNonInheritableGroupAttrs
    // - removeUselessStrokeAndFill
    // - cleanupEnableBackground
    // - removeHiddenElems
    // - removeEmptyText
    // - convertShapeToPath
    // - moveElemsAttrsToGroup
    // - moveGroupAttrsToElems
    // - collapseGroups
    // - convertPathData
    // - convertEllipseToCircle
    // - convertTransform
    // - removeEmptyAttrs
    // - removeEmptyContainers
    // - mergePaths
    // - removeUnusedNS
    // - sortAttrs
    // - sortDefsChildren
    // - removeDesc
    // - removeDeprecatedAttrs

    // Additional plugins (disabled by default, enable as needed):

    // 'removeXMLNS',              // Remove xmlns (for inline SVGs only)
    // 'convertStyleToAttrs',      // Convert styles to attributes
    // 'removeRasterImages',       // Remove embedded raster images
    // 'cleanupListOfValues',      // Round/rewrite number lists
    // 'reusePaths',               // Replace duplicate elements with links
    // 'removeTitle',              // Remove <title> (hurts accessibility)
    // 'removeDimensions',         // Remove width/height, prefer viewBox
    // 'removeStyleElement',       // Remove <style> elements
    // 'removeScripts',            // Remove <script> elements
    // 'removeOffCanvasPaths',     // Remove paths outside viewBox
    // 'convertOneStopGradients',  // Convert single-stop gradients to solid
    // 'removeXlink',              // Replace xlink with native SVG attributes
  ],
};