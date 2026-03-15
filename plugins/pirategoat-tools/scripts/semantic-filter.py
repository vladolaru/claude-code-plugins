#!/usr/bin/env python3
"""
Semantic Filter - Regex-based noise removal from git diffs

Filters noise from diffs while preserving semantic changes:
- Multi-line docblocks (/** ... */)
- Inline docblock tags (@param, @return, @var, etc.)
- Blank lines and whitespace
- Formatting changes (braces, semicolons)

Inline comments (// and #) are preserved — they carry developer intent
(translators directives, API contracts, ordering constraints) that is
review-relevant, especially in WordPress/WooCommerce codebases.

Usage:
    ./semantic-filter.py < input.diff > filtered.diff
    git diff main feature | ./semantic-filter.py

Typical reduction: 10-20% (docblocks, annotations, formatting, blanks)
"""

import sys
import re

# Suppression/intent-bearing comment patterns — these are NOT noise.
# They carry developer intent that is review-relevant:
#   - Linter/type suppressions: eslint-disable, phpcs:ignore, @ts-ignore, noqa, nosec
#   - Deprecation markers: @deprecated
#   - Action markers: TODO, FIXME, HACK, XXX
_STRUCTURED_COMMENT_PATTERNS = re.compile(
    r'(?i)'  # case-insensitive
    r'(?:'
    r'eslint-disable|'
    r'phpcs:(ignore|disable|enable)|'
    r'@ts-(ignore|expect-error|nocheck)|'
    r'noinspection\s|'
    r'noqa|'
    r'nosec|'
    r'nolint|'
    r'type:\s*ignore|'
    r'pylint:\s*(disable|enable)|'
    r'@deprecated|'
    r'deprecated:|'
    r'\b(TODO|FIXME|HACK|XXX)\b'
    r')'
)


def is_blank_line_change(line):
    """Check if line is just adding/removing whitespace."""
    stripped = line.lstrip('+-').strip()
    return stripped == ''


def is_docblock_line(line):
    """
    Check if line is part of a docblock comment.

    Matches:
    - /** (docblock start)
    - */ (docblock end)
    - * (docblock line)
    - * @param, * @return, etc. (docblock tags)
    - /// (triple-slash comments)
    """
    stripped = line.lstrip('+-').strip()

    # Docblock markers
    if stripped.startswith('/**') or stripped.startswith('*/'):
        return True

    # Docblock content lines (starts with *)
    if stripped.startswith('*'):
        return True

    # Triple-slash comments (TypeScript/PHP)
    if stripped.startswith('///'):
        return True

    return False


def is_inline_comment_only(line):
    """Check if line is only an inline comment change.

    Returns False for structured directives (eslint-disable, phpcs:ignore,
    @ts-ignore, noqa, nosec, TODO, FIXME, @deprecated) — these carry
    intent and are review-relevant.
    """
    stripped = line.lstrip('+-').strip()

    # Single-line comments
    if stripped.startswith('//') or stripped.startswith('#'):
        # Exempt structured directives — they carry intent
        if _STRUCTURED_COMMENT_PATTERNS.search(stripped):
            return False
        return True

    return False


def is_phpdoc_annotation_line(line):
    """
    Check if line contains only PHPDoc/JSDoc annotations.

    Matches:
    - @param Type $var Description
    - @return Type Description
    - @var Type Description
    - @throws ExceptionType Description
    - @type {Type} Description (JSDoc)
    """
    stripped = line.lstrip('+-').strip()

    # PHPDoc/JSDoc annotation pattern
    # Starts with * (docblock) and contains @ tag
    if stripped.startswith('*') and '@' in stripped:
        # Common annotation tags
        annotation_pattern = r'^\*\s*@(param|return|var|throws|type|typedef|property|method|see|link|since|deprecated|author|package)'
        if re.match(annotation_pattern, stripped):
            return True

    return False


def is_formatting_only(line):
    """Check if line is pure formatting (braces, spacing, alignment)."""
    stripped = line.lstrip('+-').strip()

    # Opening brace on its own line
    if stripped == '{':
        return True

    # Closing brace on its own line
    if stripped == '}':
        return True

    # Closing parenthesis with optional brace
    if re.match(r'^\)\s*\{?$', stripped):
        return True

    # Lines with only semicolons (JS statement terminators)
    if stripped == ';':
        return True

    # Class/function signature without body (brace moved)
    # Example: "class Payment" (where before had "class Payment {")
    # But this might be semantic - be conservative
    # if re.match(r'^(class|interface|trait|function)\s+\w+\s*$', stripped):
    #     return True

    return False


def is_visibility_keyword_only(line):
    """
    Check if line only changes visibility keywords (var → public).

    PHP var → public/private/protected changes are formatting-like
    but technically semantic (affects access). Be conservative: keep them.
    """
    # For now, be conservative and don't filter
    return False


def is_import_reordering(prev_lines, line, next_lines):
    """
    Check if import/use statements are just reordered (not added/removed).

    This requires context from surrounding lines. For MVP: be conservative.
    """
    # TODO: Implement import reordering detection with context
    # For now, keep all import changes (conservative)
    return False


def is_type_hint_only(line):
    """
    Check if line only adds type hints without changing logic.

    Examples:
    - private $var → private Type $var
    - function foo($x) → function foo(Type $x)
    - : void added to function signature

    Be conservative: only filter obvious cases.
    """
    stripped = line.lstrip('+-').strip()

    # PHP property type hint pattern
    # Example: private Repository $repository
    # This is tricky because it could be a NEW property or type hint addition
    # Be conservative: don't filter

    return False


def should_filter(line, context=None):
    """
    Determine if line should be filtered out.

    Conservative approach: When in doubt, keep the line.
    Goal: Remove obvious noise without losing signal.

    Args:
        line: Diff line to check
        context: Optional dict with prev_lines, next_lines for context-aware filtering
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

    # Filter docblock lines (most aggressive filtering)
    if is_docblock_line(line):
        return True

    # Filter PHPDoc/JSDoc annotation lines
    if is_phpdoc_annotation_line(line):
        return True

    # Inline comments are preserved — they carry developer intent
    # (translators directives, API contracts, ordering constraints).

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
        'annotation_lines_filtered': 0,
        'comment_lines_filtered': 0,
        'formatting_lines_filtered': 0,
        'lines_kept': 0,
        'noise_removed': 0
    }

    for i, line in enumerate(lines):
        # Build context for context-aware filtering
        context = {
            'prev_lines': lines[max(0, i-3):i],
            'next_lines': lines[i+1:min(len(lines), i+4)]
        }

        if should_filter(line, context):
            # Categorize what we filtered
            if is_blank_line_change(line):
                stats['blank_lines_filtered'] += 1
            elif is_phpdoc_annotation_line(line):
                stats['annotation_lines_filtered'] += 1
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
Semantic Filter - Results:
  Total lines: {stats['total_lines']}
  Noise filtered: {stats['noise_removed']} ({stats['noise_percentage']:.1f}%)
    - Blank lines: {stats['blank_lines_filtered']}
    - Docblocks: {stats['docblock_lines_filtered']}
    - Annotations (@param, @return, etc.): {stats['annotation_lines_filtered']}
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
