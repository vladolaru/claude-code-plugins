#!/usr/bin/env python3
"""Extract design specifications from Figma get_design_context response.

Usage:
    python3 figma-extract-specs.py <design-context-file> [options]

Options:
    --tokens-file <path>      Path to cached token definitions (from get_variable_defs)
    --config-file <path>      Path to project .claude/figma-config.json
    --output-format md|json   Output format (default: md)
    --section <name>          Extract only a specific section (spacing|typography|colors|hierarchy)

Input: Saved get_design_context response file (React+Tailwind code from Figma MCP).
Output: Structured design specifications with spacing, typography, colors, and hierarchy.

The get_design_context response contains generated React+Tailwind code with embedded
CSS variables, component descriptions, and style definitions. This script extracts
the design-relevant values into a structured specification that can be used as a
source of truth for implementation.
"""

import argparse
import json
import re
import sys
from collections import defaultdict
from pathlib import Path


def extract_spacing_values(content: str) -> list[dict]:
    """Extract all spacing/dimension values from the response."""
    values = []
    seen = set()

    # Match Tailwind spacing classes
    tailwind_spacing = re.findall(
        r'(?:p|m|gap|space-[xy]|px|py|pt|pb|pl|pr|mx|my|mt|mb|ml|mr)-(\[?\d+(?:px|rem)?\]?|\d+)',
        content,
    )
    for val in tailwind_spacing:
        clean = val.strip("[]")
        if clean not in seen:
            seen.add(clean)
            values.append({"value": clean, "source": "tailwind-class"})

    # Match inline style dimensions
    inline_dims = re.findall(
        r'(?:padding|margin|gap|width|height|top|bottom|left|right)\s*[:=]\s*["\']?(\d+(?:px|rem|%))["\']?',
        content,
    )
    for val in inline_dims:
        if val not in seen:
            seen.add(val)
            values.append({"value": val, "source": "inline-style"})

    # Match CSS variable references for spacing
    css_vars = re.findall(r'var\((--[\w-]*(?:spacing|gap|padding|margin|dimension)[\w-]*)\)', content)
    for var in css_vars:
        if var not in seen:
            seen.add(var)
            values.append({"value": var, "source": "css-variable"})

    # Match numeric pixel values in style objects
    style_px = re.findall(r':\s*(\d+)(?:\s*,|\s*\})', content)
    for val in style_px:
        px_val = f"{val}px"
        if px_val not in seen and int(val) <= 200:  # reasonable UI values
            seen.add(px_val)
            values.append({"value": px_val, "source": "style-object"})

    return sorted(values, key=lambda x: _extract_numeric(x["value"]))


def extract_typography(content: str) -> list[dict]:
    """Extract typography specifications."""
    specs = []
    seen = set()

    # Match Tailwind text classes
    text_classes = re.findall(r'text-(\[?\d+(?:px|rem)?\]?|xs|sm|base|lg|xl|2xl|3xl)', content)
    for cls in text_classes:
        if cls not in seen:
            seen.add(cls)
            specs.append({"property": "font-size", "value": cls, "source": "tailwind"})

    # Match font-weight classes
    weight_classes = re.findall(r'font-(thin|extralight|light|normal|medium|semibold|bold|extrabold|black|\d+)', content)
    for w in weight_classes:
        if w not in seen:
            seen.add(w)
            specs.append({"property": "font-weight", "value": w, "source": "tailwind"})

    # Match leading (line-height) classes
    leading_classes = re.findall(r'leading-(\[?\d+(?:px|rem)?\]?|none|tight|snug|normal|relaxed|loose)', content)
    for lh in leading_classes:
        if lh not in seen:
            seen.add(lh)
            specs.append({"property": "line-height", "value": lh, "source": "tailwind"})

    # Match inline font properties
    font_sizes = re.findall(r'fontSize\s*[:=]\s*["\']?(\d+(?:px|rem)?)["\']?', content)
    for fs in font_sizes:
        key = f"fontSize:{fs}"
        if key not in seen:
            seen.add(key)
            specs.append({"property": "font-size", "value": fs, "source": "inline"})

    font_weights = re.findall(r'fontWeight\s*[:=]\s*["\']?(\d+|normal|bold|medium|semibold)["\']?', content)
    for fw in font_weights:
        key = f"fontWeight:{fw}"
        if key not in seen:
            seen.add(key)
            specs.append({"property": "font-weight", "value": fw, "source": "inline"})

    line_heights = re.findall(r'lineHeight\s*[:=]\s*["\']?(\d+(?:px|rem|%)?)["\']?', content)
    for lh in line_heights:
        key = f"lineHeight:{lh}"
        if key not in seen:
            seen.add(key)
            specs.append({"property": "line-height", "value": lh, "source": "inline"})

    # Match CSS variable references for typography
    typo_vars = re.findall(r'var\((--[\w-]*(?:font|text|typography)[\w-]*)\)', content)
    for var in typo_vars:
        if var not in seen:
            seen.add(var)
            specs.append({"property": "css-variable", "value": var, "source": "css-variable"})

    return specs


def extract_colors(content: str) -> list[dict]:
    """Extract color specifications."""
    colors = []
    seen = set()

    # Match hex colors
    hex_colors = re.findall(r'#([0-9a-fA-F]{3,8})\b', content)
    for h in hex_colors:
        color = f"#{h}"
        if color not in seen:
            seen.add(color)
            colors.append({"value": color, "source": "hex"})

    # Match rgb/rgba
    rgb_colors = re.findall(r'rgba?\(\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)(?:\s*,\s*([\d.]+))?\s*\)', content)
    for r, g, b, a in rgb_colors:
        color = f"rgba({r},{g},{b},{a})" if a else f"rgb({r},{g},{b})"
        if color not in seen:
            seen.add(color)
            colors.append({"value": color, "source": "rgb"})

    # Match Tailwind color classes
    tw_colors = re.findall(
        r'(?:text|bg|border|fill|stroke)-(?![\[\d])([\w]+-\d{2,3}|white|black|transparent|current)',
        content,
    )
    for c in tw_colors:
        if c not in seen:
            seen.add(c)
            colors.append({"value": c, "source": "tailwind"})

    # Match CSS variable references for colors
    color_vars = re.findall(r'var\((--[\w-]*(?:color|bg|fg|stroke|fill)[\w-]*)\)', content)
    for var in color_vars:
        if var not in seen:
            seen.add(var)
            colors.append({"value": var, "source": "css-variable"})

    return colors


def extract_component_hierarchy(content: str) -> list[dict]:
    """Extract component hierarchy from React/JSX structure."""
    components = []

    # Match React component usage
    component_pattern = re.findall(
        r'<(\w+)(?:\s+([^>]*))?(?:/>|>)',
        content,
    )

    depth = 0
    for tag, attrs in component_pattern:
        # Skip HTML elements
        if tag[0].islower() and tag not in ("svg", "path", "circle", "rect"):
            continue

        component = {
            "name": tag,
            "depth": depth,
            "props": {},
        }

        # Extract key props
        if attrs:
            for prop_match in re.finditer(r'(\w+)=["{]([^"}\s]+)["}]', attrs):
                prop, val = prop_match.group(1), prop_match.group(2)
                if prop in (
                    "className",
                    "variant",
                    "size",
                    "tone",
                    "intent",
                    "direction",
                    "gap",
                    "align",
                    "justify",
                ):
                    component["props"][prop] = val

        components.append(component)

    return components


def extract_assets(content: str) -> list[dict]:
    """Extract asset references (images, SVGs, icons)."""
    assets = []
    seen = set()

    # Match localhost asset URLs
    localhost_urls = re.findall(r'(https?://localhost:\d+/[\w/.-]+)', content)
    for url in localhost_urls:
        if url not in seen:
            seen.add(url)
            assets.append({"url": url, "type": "localhost-asset"})

    # Match icon references
    icon_refs = re.findall(r'(?:icon|Icon)\s*[=:]\s*["{](\w+)["}]', content)
    for icon in icon_refs:
        if icon not in seen:
            seen.add(icon)
            assets.append({"name": icon, "type": "icon"})

    # Match SVG elements
    svg_count = len(re.findall(r'<svg\b', content, re.IGNORECASE))
    if svg_count > 0:
        assets.append({"count": svg_count, "type": "inline-svg"})

    return assets


def extract_dimensions(content: str) -> list[dict]:
    """Extract specific dimension values (width, height, border-radius)."""
    dimensions = []
    seen = set()

    # Match width/height
    for prop in ("width", "height", "minWidth", "maxWidth", "minHeight", "maxHeight"):
        values = re.findall(rf'{prop}\s*[:=]\s*["\']?(\d+(?:px|rem|%|vh|vw)?)["\']?', content)
        for val in values:
            key = f"{prop}:{val}"
            if key not in seen:
                seen.add(key)
                dimensions.append({"property": prop, "value": val})

    # Match border-radius
    radii = re.findall(r'(?:borderRadius|border-radius)\s*[:=]\s*["\']?(\d+(?:px|rem)?)["\']?', content)
    for r in radii:
        key = f"radius:{r}"
        if key not in seen:
            seen.add(key)
            dimensions.append({"property": "border-radius", "value": r})

    # Match Tailwind rounded classes
    rounded = re.findall(r'rounded(?:-(none|sm|md|lg|xl|2xl|3xl|full|\[\d+px\]))?', content)
    for r in rounded:
        val = r or "default"
        key = f"rounded:{val}"
        if key not in seen:
            seen.add(key)
            dimensions.append({"property": "border-radius", "value": val, "source": "tailwind"})

    return dimensions


def _extract_numeric(value: str) -> float:
    """Extract numeric value for sorting."""
    match = re.search(r'(\d+)', value)
    return float(match.group(1)) if match else 0


def map_to_project_tokens(specs: dict, config: dict | None) -> dict:
    """Map extracted Figma values to project design tokens."""
    if not config or "token_mapping" not in config:
        return specs

    mapping = config["token_mapping"]
    mapped = dict(specs)

    # Map spacing
    if "spacing" in mapping and "spacing" in mapped:
        for item in mapped["spacing"]:
            val = re.sub(r'px$', '', item["value"])
            if val in mapping["spacing"]:
                item["project_token"] = mapping["spacing"][val]

    # Map colors
    if "colors" in mapping and "colors" in mapped:
        for item in mapped["colors"]:
            for figma_name, project_token in mapping["colors"].items():
                if figma_name in item["value"]:
                    item["project_token"] = project_token

    # Map typography
    if "typography" in mapping and "typography" in mapped:
        for item in mapped["typography"]:
            if item["value"] in mapping["typography"]:
                item["project_token"] = mapping["typography"][item["value"]]

    return mapped


def format_markdown(specs: dict) -> str:
    """Format extracted specs as markdown."""
    lines = ["# Extracted Design Specifications", ""]

    # Spacing
    lines.append("## Spacing Values")
    lines.append("")
    if specs.get("spacing"):
        has_tokens = any("project_token" in s for s in specs["spacing"])
        if has_tokens:
            lines.append("| Value | Source | Project Token |")
            lines.append("|-------|--------|---------------|")
        else:
            lines.append("| Value | Source |")
            lines.append("|-------|--------|")
        for s in specs["spacing"]:
            if has_tokens:
                token = s.get("project_token", "—")
                lines.append(f"| `{s['value']}` | {s['source']} | `{token}` |")
            else:
                lines.append(f"| `{s['value']}` | {s['source']} |")
    else:
        lines.append("(none found)")
    lines.append("")

    # Typography
    lines.append("## Typography")
    lines.append("")
    if specs.get("typography"):
        lines.append("| Property | Value | Source |")
        lines.append("|----------|-------|--------|")
        for t in specs["typography"]:
            lines.append(f"| {t['property']} | `{t['value']}` | {t['source']} |")
    else:
        lines.append("(none found)")
    lines.append("")

    # Colors
    lines.append("## Colors")
    lines.append("")
    if specs.get("colors"):
        lines.append("| Value | Source |")
        lines.append("|-------|--------|")
        for c in specs["colors"]:
            token = c.get("project_token", "")
            token_col = f" → `{token}`" if token else ""
            lines.append(f"| `{c['value']}`{token_col} | {c['source']} |")
    else:
        lines.append("(none found)")
    lines.append("")

    # Dimensions
    lines.append("## Dimensions")
    lines.append("")
    if specs.get("dimensions"):
        lines.append("| Property | Value |")
        lines.append("|----------|-------|")
        for d in specs["dimensions"]:
            lines.append(f"| {d['property']} | `{d['value']}` |")
    else:
        lines.append("(none found)")
    lines.append("")

    # Component hierarchy
    lines.append("## Component Hierarchy")
    lines.append("")
    if specs.get("components"):
        for c in specs["components"]:
            indent = "  " * c.get("depth", 0)
            props = ", ".join(f"{k}={v}" for k, v in c.get("props", {}).items())
            props_str = f" ({props})" if props else ""
            lines.append(f"{indent}- `<{c['name']}>`{props_str}")
    else:
        lines.append("(none found)")
    lines.append("")

    # Assets
    lines.append("## Assets")
    lines.append("")
    if specs.get("assets"):
        for a in specs["assets"]:
            if a["type"] == "localhost-asset":
                lines.append(f"- Asset URL: `{a['url']}`")
            elif a["type"] == "icon":
                lines.append(f"- Icon: `{a['name']}`")
            elif a["type"] == "inline-svg":
                lines.append(f"- {a['count']} inline SVG(s)")
    else:
        lines.append("(none found)")

    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Extract design specs from Figma get_design_context response"
    )
    parser.add_argument("file", help="Path to saved design context response file")
    parser.add_argument("--tokens-file", help="Path to cached token definitions")
    parser.add_argument("--config-file", help="Path to project figma-config.json")
    parser.add_argument(
        "--output-format",
        choices=["md", "json"],
        default="md",
        help="Output format (default: md)",
    )
    parser.add_argument(
        "--section",
        choices=["spacing", "typography", "colors", "dimensions", "hierarchy", "assets"],
        help="Extract only a specific section",
    )

    args = parser.parse_args()

    path = Path(args.file)
    if not path.exists():
        print(f"Error: File not found: {path}", file=sys.stderr)
        sys.exit(1)

    content = path.read_text()
    print(f"Input size: {len(content):,} chars", file=sys.stderr)

    # Load config if provided
    config = None
    if args.config_file:
        config_path = Path(args.config_file)
        if config_path.exists():
            config = json.loads(config_path.read_text())
            print(f"Loaded config from {config_path}", file=sys.stderr)

    # Extract all specs
    specs: dict = {}

    sections_to_extract = (
        [args.section] if args.section else
        ["spacing", "typography", "colors", "dimensions", "hierarchy", "assets"]
    )

    if "spacing" in sections_to_extract:
        specs["spacing"] = extract_spacing_values(content)
        print(f"Spacing values: {len(specs['spacing'])}", file=sys.stderr)

    if "typography" in sections_to_extract:
        specs["typography"] = extract_typography(content)
        print(f"Typography specs: {len(specs['typography'])}", file=sys.stderr)

    if "colors" in sections_to_extract:
        specs["colors"] = extract_colors(content)
        print(f"Color values: {len(specs['colors'])}", file=sys.stderr)

    if "dimensions" in sections_to_extract:
        specs["dimensions"] = extract_dimensions(content)
        print(f"Dimension values: {len(specs['dimensions'])}", file=sys.stderr)

    if "hierarchy" in sections_to_extract:
        specs["components"] = extract_component_hierarchy(content)
        print(f"Components: {len(specs['components'])}", file=sys.stderr)

    if "assets" in sections_to_extract:
        specs["assets"] = extract_assets(content)
        print(f"Assets: {len(specs['assets'])}", file=sys.stderr)

    # Map to project tokens if config available
    if config:
        specs = map_to_project_tokens(specs, config)

    # Output
    if args.output_format == "json":
        print(json.dumps(specs, indent=2))
    else:
        print(format_markdown(specs))


if __name__ == "__main__":
    main()
