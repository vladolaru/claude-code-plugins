#!/usr/bin/env python3
"""
Semantic Filter MVP - Regex-based noise removal from git diffs

Removes obvious noise (whitespace, docblocks, blank lines) while preserving
semantic changes (logic, signatures, dependencies).

Usage:
    ./semantic-filter-mvp.py < input.diff > filtered.diff
    git diff main feature | ./semantic-filter-mvp.py

Goal: 50-70% noise reduction without AST parsing.
"""

import sys
import re

def is_blank_line_change(line):
    """Check if line is just adding/removing whitespace."""
    stripped = line.lstrip('+-').strip()
    return stripped == ''

def is_docblock_line(line):
    """Check if line is part of a docblock comment."""
    stripped = line.lstrip('+-').strip()
    return (
        stripped.startswith('/**') or
        stripped.startswith('*/') or
        stripped.startswith('*') or
        stripped.startswith('///')
    )

def is_inline_comment_only(line):
    """Check if line is only an inline comment change."""
    stripped = line.lstrip('+-').strip()
    return stripped.startswith('//')

def is_formatting_only(line):
    """Check if line is pure formatting (braces, spacing)."""
    stripped = line.lstrip('+-').strip()

    # Opening brace on its own line
    if stripped == '{':
        return True

    # Closing brace on its own line (might be semantic, so be conservative)
    if stripped == '}':
        return False  # Keep these (might indicate block boundaries)

    return False

def is_type_hint_only(line):
    """Check if line is only adding type hints (no logic change)."""
    # Conservative: Only filter obvious type hint additions
    # Pattern: "private Type $var" where before was "private $var"

    # Look for property type hint pattern
    if re.match(r'^\s*[\+\-]\s*(private|protected|public)\s+[A-Z]\w+\s+\$', line):
        # This might be a new property OR a type hint addition
        # Be conservative: don't filter (could be new property)
        return False

    return False

def is_import_change(line):
    """Check if line is import/use statement change."""
    stripped = line.lstrip('+-').strip()
    return (
        stripped.startswith('use ') or
        stripped.startswith('import ') or
        stripped.startswith('from ') or
        stripped.startswith('require ')
    )

def is_namespace_line(line):
    """Check if line is namespace declaration."""
    stripped = line.lstrip('+-').strip()
    return stripped.startswith('namespace ')

def should_filter(line, context=None):
    """
    Determine if line should be filtered out.

    Conservative approach: When in doubt, keep the line.
    Goal: Remove obvious noise without losing signal.
    """
    # Always keep diff headers
    if line.startswith('---') or line.startswith('+++') or line.startswith('@@'):
        return False

    # Keep unchanged lines (context)
    if not line.startswith('+') and not line.startswith('-'):
        return False

    # Filter blank lines
    if is_blank_line_change(line):
        return True

    # Filter docblock lines
    if is_docblock_line(line):
        return True

    # Filter inline comments
    if is_inline_comment_only(line):
        return True

    # Filter pure formatting
    if is_formatting_only(line):
        return True

    # Keep everything else (conservative)
    return False

def filter_diff(diff_text):
    """
    Filter noise from git diff while preserving semantic changes.

    Returns:
        filtered_diff: Diff with noise removed
        stats: Dictionary with noise reduction statistics
    """
    lines = diff_text.split('\n')
    filtered_lines = []
    stats = {
        'total_lines': len(lines),
        'blank_lines_filtered': 0,
        'docblock_lines_filtered': 0,
        'comment_lines_filtered': 0,
        'formatting_lines_filtered': 0,
        'lines_kept': 0,
        'noise_removed': 0
    }

    for line in lines:
        if should_filter(line):
            # Categorize what we filtered
            if is_blank_line_change(line):
                stats['blank_lines_filtered'] += 1
            elif is_docblock_line(line):
                stats['docblock_lines_filtered'] += 1
            elif is_inline_comment_only(line):
                stats['comment_lines_filtered'] += 1
            elif is_formatting_only(line):
                stats['formatting_lines_filtered'] += 1

            stats['noise_removed'] += 1
        else:
            filtered_lines.append(line)
            stats['lines_kept'] += 1

    stats['noise_percentage'] = (stats['noise_removed'] / stats['total_lines'] * 100) if stats['total_lines'] > 0 else 0
    stats['reduction_ratio'] = f"{stats['total_lines']}→{stats['lines_kept']} ({stats['noise_percentage']:.1f}% reduction)"

    return '\n'.join(filtered_lines), stats

def print_stats(stats):
    """Print filtering statistics to stderr."""
    print(f"""
Semantic Filter MVP - Results:
  Total lines: {stats['total_lines']}
  Noise filtered: {stats['noise_removed']} ({stats['noise_percentage']:.1f}%)
    - Blank lines: {stats['blank_lines_filtered']}
    - Docblocks: {stats['docblock_lines_filtered']}
    - Comments: {stats['comment_lines_filtered']}
    - Formatting: {stats['formatting_lines_filtered']}
  Signal kept: {stats['lines_kept']} ({100 - stats['noise_percentage']:.1f}%)
  Reduction: {stats['reduction_ratio']}
""", file=sys.stderr)

if __name__ == '__main__':
    # Read diff from stdin
    diff_text = sys.stdin.read()

    # Filter the diff
    filtered_diff, stats = filter_diff(diff_text)

    # Print filtered diff to stdout
    print(filtered_diff)

    # Print stats to stderr (so they don't pollute the diff)
    print_stats(stats)
