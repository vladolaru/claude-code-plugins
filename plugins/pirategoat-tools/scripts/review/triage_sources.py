#!/usr/bin/env python3
"""The prose sources keyword triage reads, reduced to the author's words.

The PR body and the commit log carry text nobody meant as a review signal
— template lines, commit trailers, labels. This module strips what the
author did not write; plan_dispatch.py matches what remains.

The body loses its HTML comments and every line the repository's own
template also carries, compared as whole normalized lines so author prose
under a template heading survives. Commit trailers are dropped from each
commit body's final paragraph. Labels never enter the text at all. Stdlib
only; a leaf of the review package's import graph.
"""

import os
import re

COMMIT_SEPARATOR = "\x00"

# GitHub's lookup for a repository's pull request template, in the order
# it documents them; every file found is used, since a body may have been
# started from any of them.
PR_TEMPLATE_PATHS = (
    ".github/PULL_REQUEST_TEMPLATE.md",
    ".github/pull_request_template.md",
    "PULL_REQUEST_TEMPLATE.md",
    "pull_request_template.md",
    "docs/PULL_REQUEST_TEMPLATE.md",
    "docs/pull_request_template.md",
)
PR_TEMPLATE_DIRS = (
    ".github/PULL_REQUEST_TEMPLATE",
    "PULL_REQUEST_TEMPLATE",
    "docs/PULL_REQUEST_TEMPLATE",
)

_HTML_COMMENT_RE = re.compile(r"<!--.*?-->", re.DOTALL)
_LINE_MARKER_RE = re.compile(r"^(?:[#*-]+\s*)+")
_CHECKBOX_RE = re.compile(r"\[[xX ]\]")
# Git's trailer line: `Token: value`, any whitespace after the colon.
_TRAILER_LINE_RE = re.compile(r"^[A-Za-z][A-Za-z0-9-]*:[ \t]+\S")
# This module's extension beyond git's rule: a bare issue-reference line
# (`Refs #12`, `Fixes WOOPLUG-5988, WOOPLUG-6001.`) in the same final
# paragraph. Git does not classify such a paragraph as trailers; the
# planner does, because the line is a formal footer in Conventional
# Commits and never a review signal. A reference followed by prose
# ("Fixes #123 by enforcing …") is the author's sentence and stays.
_REFERENCE_TRAILER_RE = re.compile(
    r"^(?:[Rr]efs?|[Ff]ixes|[Cc]loses) (?:#\d+|[A-Z][A-Z0-9]+-\d+)"
    r"(?:,\s*(?:#\d+|[A-Z][A-Z0-9]+-\d+))*\.?$"
)
_PARAGRAPH_SPLIT_RE = re.compile(r"\n\s*\n")


def strip_html_comments(text):
    """Remove every terminated `<!-- … -->` comment.

    HTML's own semantics: an opener runs to the NEXT `-->` wherever that
    is, so an unterminated `<!--` followed later by another comment
    swallows the text between them; an opener with no close at all is
    left as written. Both degrade to less triage text, never more.
    """
    return _HTML_COMMENT_RE.sub("", text or "")


def normalize_template_line(line):
    """The comparison key for one line: markers, checkbox state, case,
    trailing punctuation and whitespace runs do not distinguish a
    template line from the same line as the author left it."""
    line = strip_html_comments(line).strip()
    line = _LINE_MARKER_RE.sub("", line)
    line = _CHECKBOX_RE.sub("[ ]", line)
    line = re.sub(r"\s+", " ", line).strip().lower()
    return line.rstrip(":.").strip()


def subtract_template(body, template):
    """The body without its HTML comments and without any line the
    template also carries. Author prose survives even under a template
    heading, because only whole normalized lines are compared."""
    template_lines = {
        normalize_template_line(line)
        for line in strip_html_comments(template or "").splitlines()
    }
    template_lines.discard("")
    kept = []
    for line in strip_html_comments(body or "").splitlines():
        if normalize_template_line(line) in template_lines:
            continue
        kept.append(line)
    return "\n".join(kept).strip()


def find_pr_template(repo_root):
    """Every pull request template GitHub would offer in ``repo_root``,
    concatenated; empty when there is none or it cannot be read."""
    parts = []
    candidates = [os.path.join(str(repo_root), rel) for rel in PR_TEMPLATE_PATHS]
    for template_dir in PR_TEMPLATE_DIRS:
        directory = os.path.join(str(repo_root), template_dir)
        try:
            if os.path.isdir(directory):
                candidates.extend(
                    os.path.join(directory, name)
                    for name in sorted(os.listdir(directory))
                    if name.lower().endswith(".md")
                )
        except OSError:
            continue
    seen = set()
    for path in candidates:
        key = os.path.normcase(path)
        if key in seen:
            continue
        seen.add(key)
        try:
            with open(path, "r", encoding="utf-8", errors="replace") as handle:
                parts.append(handle.read())
        except OSError:
            continue
    return "\n".join(parts)


def _without_trailers(commit):
    subject, separator, body = commit.strip().partition("\n")
    if not separator:
        return subject

    paragraphs = _PARAGRAPH_SPLIT_RE.split(body)
    last = [line for line in paragraphs[-1].splitlines() if line.strip()]
    if last and all(
        _TRAILER_LINE_RE.match(line.strip())
        or _REFERENCE_TRAILER_RE.fullmatch(line.strip())
        for line in last
    ):
        paragraphs = paragraphs[:-1]
    body = "\n\n".join(p.strip() for p in paragraphs if p.strip())
    return f"{subject}\n{body}" if body else subject


def strip_commit_trailers(log):
    """Commit text without trailer paragraphs.

    ``log`` is `git log --format=%s%n%b%x00` output: subject, one newline,
    body, and a NUL after each commit. The subject is split off before the
    body's final paragraph is classified. That paragraph is removed when
    every line is a git trailer (`Token: value`) or a bare issue
    reference (`Refs #12`, this module's extension of git's rule); a
    one-line final paragraph such as `Note: this is intentional` is a
    trailer to git and is removed here too.
    """
    commits = [c for c in (log or "").split(COMMIT_SEPARATOR) if c.strip()]
    return "\n".join(_without_trailers(c) for c in commits).strip()
