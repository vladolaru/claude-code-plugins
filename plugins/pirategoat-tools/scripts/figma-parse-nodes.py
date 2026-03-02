#!/usr/bin/env python3
"""Parse Figma get_metadata XML response into structured node hierarchy.

Usage:
    python3 figma-parse-nodes.py <metadata-file> [options]

Options:
    --format tree|flat|json   Output format (default: tree)
    --filter <type>           Filter by node type (e.g., FRAME, COMPONENT, TEXT)
    --depth <n>               Max depth to traverse (default: unlimited)
    --names-only              Show only node names (skip IDs and types)

Input: Saved get_metadata response file (XML/text format from Figma MCP).
Output: Structured node hierarchy for understanding design structure.

This script handles the common case where get_metadata returns >50K chars
of XML that's too large to process in-context. It extracts the node
hierarchy so you can identify which nodes to fetch with get_design_context.
"""

import argparse
import json
import re
import sys
import xml.etree.ElementTree as ET
from pathlib import Path


def parse_figma_metadata_xml(content: str) -> list[dict]:
    """Parse Figma metadata XML into structured nodes.

    Figma get_metadata returns sparse XML like:
    <fig>
      <FRAME id="1087:14670" name="Canvas Name" x="0" y="0" w="1000" h="800">
        <FRAME id="1087:27150" name="Child Frame" x="10" y="10" w="500" h="400">
          <TEXT id="1087:27151" name="Label" characters="Hello" />
        </FRAME>
      </FRAME>
    </fig>
    """
    nodes = []

    # Try parsing as XML first
    try:
        # Wrap in root if needed
        if not content.strip().startswith("<"):
            # Maybe it's JSON - try to extract XML from JSON response
            content = extract_xml_from_json(content)

        root = ET.fromstring(content)
        nodes = parse_xml_element(root, depth=0)
    except ET.ParseError:
        # Fallback: regex-based parsing for malformed XML
        nodes = parse_with_regex(content)

    return nodes


def extract_xml_from_json(content: str) -> str:
    """Extract XML content from a JSON Figma MCP response."""
    try:
        data = json.loads(content)
        # The metadata is usually in a 'content' or 'text' field
        if isinstance(data, dict):
            for key in ["content", "text", "result", "metadata"]:
                if key in data:
                    val = data[key]
                    if isinstance(val, str) and "<" in val:
                        return val
                    if isinstance(val, list):
                        for item in val:
                            if isinstance(item, dict) and "text" in item:
                                return item["text"]
        # Try treating entire string content as XML
        if isinstance(data, str):
            return data
    except (json.JSONDecodeError, TypeError):
        pass
    return content


def parse_xml_element(element: ET.Element, depth: int) -> list[dict]:
    """Recursively parse an XML element into node dicts."""
    nodes = []

    node = {
        "type": element.tag,
        "id": element.get("id", ""),
        "name": element.get("name", ""),
        "depth": depth,
    }

    # Capture key attributes
    for attr in ["x", "y", "w", "h", "characters", "visible"]:
        if element.get(attr):
            node[attr] = element.get(attr)

    children = []
    for child in element:
        child_nodes = parse_xml_element(child, depth + 1)
        children.extend(child_nodes)

    node["children_count"] = len(list(element))
    nodes.append(node)
    nodes.extend(children)

    return nodes


def parse_with_regex(content: str) -> list[dict]:
    """Fallback regex parser for malformed XML."""
    nodes = []
    # Match opening tags with attributes
    pattern = r"<(\w+)\s+([^>]*?)(/?)>"
    for match in re.finditer(pattern, content):
        tag = match.group(1)
        attrs_str = match.group(2)

        node = {"type": tag, "depth": 0}

        # Extract key attributes
        for attr_match in re.finditer(r'(\w+)="([^"]*)"', attrs_str):
            key, val = attr_match.group(1), attr_match.group(2)
            if key in ("id", "name", "x", "y", "w", "h", "characters", "visible"):
                node[key] = val

        if "id" in node:
            nodes.append(node)

    # Estimate depth from indentation
    lines = content.split("\n")
    for line in lines:
        stripped = line.lstrip()
        if stripped.startswith("<") and not stripped.startswith("</"):
            indent = len(line) - len(stripped)
            tag_match = re.match(r"<(\w+)", stripped)
            if tag_match:
                tag = tag_match.group(1)
                for node in nodes:
                    if node["type"] == tag and node.get("depth", 0) == 0:
                        node["depth"] = indent // 2
                        break

    return nodes


def filter_nodes(
    nodes: list[dict],
    node_type: str | None = None,
    max_depth: int | None = None,
) -> list[dict]:
    """Filter nodes by type and depth."""
    result = []
    for node in nodes:
        if node_type and node.get("type", "").upper() != node_type.upper():
            continue
        if max_depth is not None and node.get("depth", 0) > max_depth:
            continue
        result.append(node)
    return result


def format_tree(nodes: list[dict], names_only: bool = False) -> str:
    """Format nodes as an indented tree."""
    lines = []
    for node in nodes:
        depth = node.get("depth", 0)
        indent = "  " * depth
        prefix = "├── " if depth > 0 else ""

        if names_only:
            lines.append(f"{indent}{prefix}{node.get('name', '(unnamed)')}")
        else:
            name = node.get("name", "(unnamed)")
            node_id = node.get("id", "?")
            node_type = node.get("type", "?")
            size = ""
            if "w" in node and "h" in node:
                size = f" [{node['w']}x{node['h']}]"
            children = ""
            if node.get("children_count", 0) > 0:
                children = f" ({node['children_count']} children)"
            lines.append(
                f"{indent}{prefix}{node_type} `{node_id}` — {name}{size}{children}"
            )

    return "\n".join(lines)


def format_flat(nodes: list[dict]) -> str:
    """Format nodes as a flat table."""
    lines = ["| Node ID | Type | Name | Size | Children |", "|---------|------|------|------|----------|"]
    for node in nodes:
        node_id = node.get("id", "?")
        node_type = node.get("type", "?")
        name = node.get("name", "(unnamed)")
        size = f"{node.get('w', '?')}x{node.get('h', '?')}" if "w" in node else "-"
        children = str(node.get("children_count", 0))
        lines.append(f"| `{node_id}` | {node_type} | {name} | {size} | {children} |")
    return "\n".join(lines)


def format_json(nodes: list[dict]) -> str:
    """Format nodes as JSON."""
    return json.dumps(nodes, indent=2)


def classify_nodes(nodes: list[dict]) -> dict[str, list[dict]]:
    """Classify nodes into categories useful for Figma workflow.

    Returns dict with keys:
    - page_variants: Large frames likely representing full page states
    - components: Smaller frames likely representing individual components
    - text_nodes: Text elements
    - wrappers: Frames with generic names (Frame NNNN) — likely containers to skip
    """
    categories: dict[str, list[dict]] = {
        "page_variants": [],
        "components": [],
        "text_nodes": [],
        "wrappers": [],
    }

    for node in nodes:
        if node.get("depth", 0) == 0:
            continue  # Skip root

        node_type = node.get("type", "").upper()
        name = node.get("name", "")
        w = int(node.get("w", 0)) if node.get("w") else 0
        h = int(node.get("h", 0)) if node.get("h") else 0

        if node_type == "TEXT":
            categories["text_nodes"].append(node)
        elif re.match(r"^Frame \d+$", name):
            categories["wrappers"].append(node)
        elif w > 1000 and h > 500:
            categories["page_variants"].append(node)
        elif node.get("depth", 0) <= 2:
            categories["components"].append(node)

    return categories


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Parse Figma get_metadata response into structured hierarchy"
    )
    parser.add_argument("file", help="Path to saved metadata response file")
    parser.add_argument(
        "--format",
        choices=["tree", "flat", "json", "classify"],
        default="tree",
        help="Output format (default: tree)",
    )
    parser.add_argument("--filter", dest="node_type", help="Filter by node type")
    parser.add_argument("--depth", type=int, help="Max traversal depth")
    parser.add_argument(
        "--names-only", action="store_true", help="Show only node names"
    )

    args = parser.parse_args()

    path = Path(args.file)
    if not path.exists():
        print(f"Error: File not found: {path}", file=sys.stderr)
        sys.exit(1)

    content = path.read_text()
    print(f"Input size: {len(content):,} chars", file=sys.stderr)

    nodes = parse_figma_metadata_xml(content)
    print(f"Parsed {len(nodes)} nodes", file=sys.stderr)

    nodes = filter_nodes(nodes, node_type=args.node_type, max_depth=args.depth)
    print(f"After filtering: {len(nodes)} nodes", file=sys.stderr)

    if args.format == "tree":
        print(format_tree(nodes, names_only=args.names_only))
    elif args.format == "flat":
        print(format_flat(nodes))
    elif args.format == "json":
        print(format_json(nodes))
    elif args.format == "classify":
        categories = classify_nodes(nodes)
        for cat, cat_nodes in categories.items():
            print(f"\n## {cat.replace('_', ' ').title()} ({len(cat_nodes)})")
            if cat_nodes:
                print(format_flat(cat_nodes))
            else:
                print("(none)")


if __name__ == "__main__":
    main()
