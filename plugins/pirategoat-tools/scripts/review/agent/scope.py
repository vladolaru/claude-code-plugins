#!/usr/bin/env python3
"""
Review Scope - Efficient diff scoping for review agents.

Single source of truth for all filtering logic. Agents call this script
instead of running 5+ ad-hoc git/grep commands to determine their review scope.

Usage:
    python3 scope.py --domain code
    python3 scope.py --domain code --summary
    python3 scope.py --domain php-tests --range main..feature-branch
    python3 scope.py --domain security --max-lines 3000
    python3 scope.py --domain patterns --base-ref-only

Exit codes:
    0  Success — scope determined, output on stdout
    1  Error — something failed, details on stderr AND stdout (for agent visibility)
    2  No changes — clean working tree, nothing to review

Zero external dependencies (stdlib only).
"""

import argparse
import json
import os
import re
import subprocess
import sys
from typing import Dict, List, Optional, Tuple

# =============================================================================
# Semantic filter — content-level noise removal from diffs
# =============================================================================

def _load_semantic_filter():
    """Lazy-load filter_diff from diff_noise_filter.py (sibling script)."""
    import importlib.util as _ilu
    _sf_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "diff_noise_filter.py")
    _sf_spec = _ilu.spec_from_file_location("diff_noise_filter", _sf_path)
    _sf_mod = _ilu.module_from_spec(_sf_spec)
    _sf_spec.loader.exec_module(_sf_mod)
    return _sf_mod.filter_diff

_filter_diff_fn = None

def apply_semantic_filter(diff_text: str) -> str:
    """Apply semantic filtering to remove noise from a diff.

    Strips docblocks, blank lines, inline comments, and formatting-only
    changes while preserving diff headers and meaningful code changes.

    Returns filtered diff text. Returns empty string for empty input.
    """
    if not diff_text:
        return ""
    global _filter_diff_fn
    if _filter_diff_fn is None:
        _filter_diff_fn = _load_semantic_filter()
    filtered, _stats = _filter_diff_fn(diff_text)
    return filtered

# =============================================================================
# Markup-emission detection — SINGLE SOURCE, shared with plan_dispatch.py's
# has_markup_changes triage check and the a11y domain's budget priority.
# Interactive/semantic elements (opening AND closing tags — a moved </label>
# changes associations too), a11y attributes (whitespace-tolerant around =),
# focus management, speak() announcements, screen-reader classes.
# =============================================================================

MARKUP_CONTENT_TOKEN_PATTERNS = (
    # Semantic/interactive elements only — div/span/p stay OUT (presentational
    # containers would gate-lift every template tweak, and tag-shape matching
    # would misread TS generics like Promise<number> as tags).
    re.compile(
        r"</?(button|input|select|textarea|form|label|fieldset|legend|dialog|"
        r"summary|details|nav|main|img|a|h[1-6]|"
        # Table semantics — captions and scoped headers are how screen
        # readers navigate tabular data.
        r"table|caption|thead|tbody|tfoot|tr|th|td|colgroup|"
        # Figures, lists, description lists, landmarks, output/status
        # elements — screen-reader-visible document structure.
        r"figure|figcaption|ul|ol|li|dl|dt|dd|optgroup|datalist|output|"
        r"progress|meter|article|section|aside|header|footer|blockquote|menu|"
        # Media/embedded elements — captions, autoplay, iframe titles, and
        # canvas/svg alternatives are core accessibility concerns.
        r"video|audio|iframe|embed|object|canvas|svg|track|source|picture)\b"
    ),
    re.compile(r"\baria-[a-z]+"),
    # Attribute assignments need attribute CONTEXT: `(?<![\w$])` rejects PHP
    # variables ('$role = ...' emits no markup and would otherwise let a big
    # backend diff outrank genuine template evidence in budget priority),
    # and the value must open like an attribute value — quote, JSX brace,
    # or (tabindex) a number.
    re.compile(
        r"(?<![\w$])role\s*=\s*(?:[\"'{]|"
        # Unquoted attribute values are valid HTML; accept the known ARIA
        # role vocabulary so `<div role=button>` counts while a JS variable
        # assignment `role = getRole()` does not.
        r"(?:button|link|dialog|alertdialog|alert|menuitem|menubar|menu|"
        r"tabpanel|tablist|tab|tooltip|navigation|banner|main|region|search|"
        r"form|listitem|listbox|list|grid|gridcell|row|cell|checkbox|radio|"
        r"switch|slider|spinbutton|progressbar|status|img|presentation|none|"
        r"group|heading|separator|toolbar|treeitem|tree|combobox|option)\b)"
    ),
    re.compile(r"(?<![\w$])tabindex\b(?!\s*=\s*\$)"),
    re.compile(r"(?<![\w$])alt\s*=\s*[\"'{]"),
    re.compile(r"(?<![\w$])(html)?for\s*=\s*[\"'{]"),
    re.compile(r"\bautofocus\b"),
    re.compile(r"\bon(click|key(down|up|press)|focus|blur)\b"),
    re.compile(r"focusable|screen-reader|sr-only|visually-hidden"),
)

# Call-shaped evidence must be found in executable/template syntax, not inside
# quoted prose. _line_has_markup_token() masks quoted content before applying
# these patterns while leaving literal markup patterns above able to recognize
# HTML assembled in strings.
MARKUP_CODE_TOKEN_PATTERNS = (
    re.compile(r"\bspeak\s*\("),
    # WordPress/WooCommerce form-helper calls emit controls with no
    # literal tag in the diff — helper-generated markup is still markup
    # (a submit_button() change alters rendered UI as surely as <button>).
    re.compile(
        r"\b(get_)?submit_button\s*\(|\bwoocommerce_form_field\s*\(|"
        r"\bwc_help_tip\s*\(|\bwp_dropdown_\w+\s*\(|\bwp_nonce_field\s*\(|"
        r"\bwp_editor\s*\(|\bwp_list_categories\s*\(|\bpaginate_links\s*\(|"
        # WordPress core renderers that emit navigation, forms, comment
        # lists, archives, widgets, or other page structure by default:
        r"\b(wp_nav_menu|wp_login_form|get_search_form|comment_form|"
        r"wp_list_comments|wp_page_menu|wp_link_pages|wp_loginout|wp_register|"
        r"wp_meta|wp_get_archives|wp_tag_cloud|dynamic_sidebar|the_widget)\s*\(|"
        # The whole woocommerce_wp_* admin-field family (text_input,
        # select, checkbox, radio, textarea, hidden_input, note, ...):
        r"\bwoocommerce_wp_\w+\s*\(|"
        # Template rendering emits an entire markup file's worth of UI:
        r"\bwc_get_template(_part|_html)?\s*\(|\bget_template_part\s*\("
    ),
    # Template COMPOSITION — includes/partials/renders pull whole
    # interactive UIs into the page with no literal tag on the line:
    # Twig {% include/embed/extends %}, Handlebars/Mustache {{> partial}},
    # ERB <%= render %>, Blade @include/@component, Go {{template}}.
    # PHP `include 'file.php'` stays out (code inclusion, not markup).
    re.compile(
        r"\{%-?\s*(include|embed|extends|use|block)\b|"
        r"\{\{>\s*[\w./-]|"
        r"<%=?\s*render\b|"
        r"@(include|component|extends|each)\s*\(|"
        r"\{\{\s*template\b"
    ),
    # Explicit PHP output constructs can emit arbitrary custom-helper results;
    # silence here must not gate accessibility merely because the callee name
    # is project-specific. Require an expression-shaped operand so prose that
    # merely mentions "echo" does not count.
    re.compile(
        r"<\?=|"
        r"\b(echo|print)\s*(?:\(|\$|['\"]|[a-z_\\][a-z0-9_\\]*\s*\()|"
        r"\b(printf|vprintf)\s*\("
    ),
    # Conventional renderer methods emit UI without a literal tag or output
    # keyword at the call site. render/display are conventional enough to use
    # directly; ambiguous output/emit calls require a view-like receiver so
    # event emitters and byte streams do not masquerade as rendered UI.
    re.compile(r"(?:->|::)(render|display)(?:_?[a-z0-9]+)*\s*\("),
    re.compile(
        r"(?:\$[a-z0-9_]*(?:view|renderer|template|component)[a-z0-9_]*|"
        r"[a-z_\\][a-z0-9_\\]*(?:view|renderer|template|component)[a-z0-9_\\]*)"
        r"\s*(?:->|::)(output|emit)(?:_?[a-z0-9]+)*\s*\("
    ),
    re.compile(r"\.focus\("),
)

# Real file markers: '+++ b/…', '--- a/…', C-quoted variants, '/dev/null'.
# Shape-tested rather than prefix-blacklisted: a changed line whose CONTENT
# starts with '--' (an SQL comment) or '++' (a C increment) renders as
# '---…'/'+++…' in the patch — those are content, not metadata, and a bare
# startswith(('+++', '---')) blacklist silently drops them.
_FILE_MARKER_RE = re.compile(r'^(\+\+\+|---) ("?[ab]/|/dev/null)')


def _is_file_marker(line: str) -> bool:
    return bool(_FILE_MARKER_RE.match(line))


def _strip_inline_comment(text: str) -> str:
    """Strip common line/block-comment starts while respecting quotes."""
    quote = None
    escaped = False
    index = 0
    while index < len(text):
        char = text[index]
        if quote is not None:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == quote:
                quote = None
            index += 1
            continue
        if char in {"'", '"', "`"}:
            quote = char
            index += 1
            continue
        if text.startswith(("//", "/*", "<!--"), index):
            return text[:index]
        if char == "#" and (index == 0 or text[index - 1].isspace()):
            return text[:index]
        index += 1
    return text


def _mask_quoted_content(text: str) -> str:
    """Mask quoted contents while preserving delimiters and code shape."""
    masked = list(text)
    quote = None
    escaped = False
    for index, char in enumerate(text):
        if quote is None:
            if char in {"'", '"', "`"}:
                quote = char
            continue
        if escaped:
            masked[index] = " "
            escaped = False
        elif char == "\\":
            masked[index] = " "
            escaped = True
        elif char == quote:
            quote = None
        else:
            masked[index] = " "
    return "".join(masked)


def _line_has_markup_token(patch_line: str) -> bool:
    """True when a patch line's content matches any markup token pattern.

    Strips the +/- diff marker, normalizes, and tests the shared content and
    code token vocabularies. Both the has_markup_changes triage check
    (patch_has_markup_tokens) and the a11y budget-priority evidence scan
    (classify_markup_evidence) call this function, so they cannot drift.
    """
    lowered = patch_line[1:].strip().lower()
    if lowered.startswith(("//", "#", "/*", "*", "<!--")):
        return False
    uncommented = _strip_inline_comment(lowered).strip()
    if not uncommented:
        return False
    if any(pattern.search(uncommented) for pattern in MARKUP_CONTENT_TOKEN_PATTERNS):
        return True
    code_only = _mask_quoted_content(uncommented)
    return any(pattern.search(code_only) for pattern in MARKUP_CODE_TOKEN_PATTERNS)


def patch_has_markup_tokens(diff_text: str) -> bool:
    """True when added or removed patch lines emit or touch UI markup."""
    for line in (diff_text or "").splitlines():
        if not line.startswith(("+", "-")):
            continue
        if _is_file_marker(line):
            continue
        if _line_has_markup_token(line):
            return True
    return False


def _unquote_git_path(path: str) -> str:
    """Decode git's C-style path quoting (backslash octal escapes, UTF-8)."""
    try:
        return (
            path.encode("latin-1")
            .decode("unicode_escape")
            .encode("latin-1")
            .decode("utf-8")
        )
    except (UnicodeDecodeError, UnicodeEncodeError):
        return path


def _parse_marker_path(line: str) -> Optional[str]:
    """Extract the path from a `+++ b/...` / `--- a/...` file marker.

    Marker lines carry ONE path, so a path containing ordinary spaces —
    which git does NOT quote — is unambiguous here, unlike the two-path
    `diff --git a/X b/X` line where any split point is a guess for a path
    containing " b/". Handles C-quoted markers (`+++ "b/\303\274.php"` —
    emitted for non-ASCII/control-character paths despite
    core.quotepath=false, which only covers the common cases). Returns
    None for `/dev/null` (the absent side of an add/delete) and for
    anything unparseable — the file then lands in the non-evidence tier
    (degraded ordering, never dropped).
    """
    body = line[4:]
    if body == "/dev/null":
        return None
    if body.startswith('"') and body.endswith('"') and len(body) > 1:
        body = _unquote_git_path(body[1:-1])
    if body.startswith(("a/", "b/")):
        return body[2:]
    return None


def classify_markup_evidence(range_spec: str, filepaths: List[str]) -> set:
    """Return the subset of `filepaths` whose changed lines carry markup tokens.

    ONE combined `git diff` for all files, scanned line-by-line while
    tracking the current file header — no per-file subprocess fan-out and no
    retained patch bodies (a large PR would otherwise mean hundreds of git
    calls and every full diff held in memory before budgeting). The scan
    runs on the raw (unfiltered) diff; that's a superset of the semantically
    filtered text, which is fine for ORDERING evidence.
    """
    if not filepaths:
        return set()
    git = ["git", "-c", "core.quotepath=false"]
    if range_spec == "--cached":
        cmd = [*git, "diff", "--cached", "--", *filepaths]
    elif range_spec == "":
        cmd = [*git, "diff", "--", *filepaths]
    else:
        cmd = [*git, "diff", range_spec, "--", *filepaths]
    try:
        output = run_cmd(cmd, check=True)
    except RuntimeError:
        return set()

    # Hunk-aware scan: paths come from the single-path '+++'/'---' markers
    # (the two-path 'diff --git' line is ambiguous for paths with spaces),
    # and markers are only read BETWEEN files — inside a hunk a removed
    # '-- comment' line renders as '---…' and must count as content.
    evidence = set()
    current = None
    old_path = None
    in_hunk = False
    for line in output.splitlines():
        if line.startswith("diff --git "):
            current = None
            old_path = None
            in_hunk = False
            continue
        if not in_hunk:
            if line.startswith("@@"):
                in_hunk = True
            elif line.startswith("--- "):
                old_path = _parse_marker_path(line)
            elif line.startswith("+++ "):
                current = _parse_marker_path(line) or old_path
            continue
        if current is None or current in evidence:
            continue
        if not line.startswith(("+", "-")):
            continue
        if _line_has_markup_token(line):
            evidence.add(current)
    return evidence


# =============================================================================
# Domain Catalog — single source of truth for file filtering
# =============================================================================

# Shared test-file exclusion pattern for production-code domains.
_TEST_EXCLUDE = r"(tests?/|__tests__/|__mocks__/|spec/|\.test\.|\.spec\.|Test\.php$|_test\.php$|_test\.go$)"
_E2E_TEST_INCLUDE = (
    r"(^e2e/|/e2e/|playwright\.config|"
    r"(^|/)(playwright|page-objects?)/.*(Page|PageObject)\.(js|ts)$)"
)

# =============================================================================
# Language extension groups — SINGLE SOURCE OF TRUTH for file-type recognition.
#
# Every general-purpose code domain composes its `include` pattern from these
# groups via `_ext_re(...)`. To teach the reviewers a new language, add its
# extension(s) to the right group ONCE here and every domain that should see it
# picks it up — no per-domain regex edits, no partial-addition drift.
#
# (History: `.rs` was wired into the rust-test domains but omitted from all 14
# production-code domains, so `security`/`code`/etc. returned NO_DOMAIN_FILES on
# pure-Rust repos. `.cs` had the same partial gap. These groups exist so that
# class of bug can't recur.)
# =============================================================================

# General-purpose programming languages (production source, any ecosystem).
_PROG_LANGS = [
    # Web / dynamic / scripting. phtml is executable PHP in a template
    # costume — it belongs to BOTH the code domains (logic changes need
    # code/security review) and _MARKUP_LANGS (a11y's mixed-markup class);
    # listing it only in the markup group left pure-logic .phtml diffs with
    # no code reviewer at all.
    "php", "phtml", "js", "mjs", "cjs", "jsx", "ts", "tsx", "py", "rb",
    # Systems / compiled / managed
    "go", "rs", "java", "kt", "kts", "scala", "cs",
    "c", "h", "cc", "cpp", "cxx", "hpp", "hh",
    # Apple platforms
    "swift", "m", "mm",
    # Functional / JVM / other mainstream
    "ex", "exs", "erl", "clj", "cljs", "hs", "ml", "mli", "fs", "fsx",
    "lua", "pl", "pm", "dart", "groovy", "zig",
    # Component-framework single-file modules (markup + logic)
    "vue", "svelte",
    # Shell (injection / destructive-command surface — worth security review)
    "sh", "bash",
]

# Frontend-only languages — for domains that review UI/markup, not backends (a11y).
_FRONTEND_LANGS = ["js", "mjs", "cjs", "jsx", "ts", "tsx", "vue", "svelte"]

# Mixed executable-markup languages contain both backend logic and rendered
# UI. Accessibility routing includes them conservatively: finite positive
# detectors cannot prove that arbitrary composition code emits no UI.
_MIXED_MARKUP_LANGS = ["php", "phtml"]

# Pure template formats are inherent UI surfaces: a changed template is
# accessibility-relevant even when the hunk contains only data binding or
# composition syntax. Keep simple extensions here and compound suffixes in
# _TEMPLATE_SUFFIXES; is_template_file() is the shared classifier.
_TEMPLATE_LANGS = [
    "html", "htm", "xhtml", "twig", "mustache", "hbs", "erb",
    "ejs", "liquid", "njk", "nunjucks", "jinja", "jinja2", "j2",
    "jsp", "jspx", "cshtml", "vbhtml", "razor", "tmpl", "tpl",
    "gsp", "ftl", "vm", "haml", "slim",
]
_TEMPLATE_SUFFIXES = ("blade.php",)
_TEMPLATE_EXTENSIONS = frozenset(_TEMPLATE_LANGS)

# Server-rendered markup languages — mixed renderers, template engines, and
# raw HTML. The a11y domain includes these: accessibility semantics (<label>,
# ARIA, fieldset/legend) live in emitted markup regardless of host language.
# (History: a11y was _FRONTEND_LANGS-only, so a PHP-only diff that removed a
# dangling <label for> was skipped with NO_DOMAIN_FILES before its triage
# keywords were ever consulted — ~30 admin-UI PHP runs went unreviewed over
# 3 months. PHP/PHTML therefore stay in scope and dispatch conservatively
# when their finite positive detectors are silent.)
_MARKUP_LANGS = [*_MIXED_MARKUP_LANGS, *_TEMPLATE_LANGS]

# Stylesheet languages.
_STYLE_LANGS = ["css", "scss", "sass", "less"]

# Query / data-definition languages.
_QUERY_LANGS = ["sql"]

# Prose / documentation formats.
_DOC_LANGS = ["md", "txt", "rst"]

# Structured-data / config formats reviewed for drift or reference integrity.
_DATA_LANGS = ["json", "yaml", "yml"]


def _ext_re(*groups) -> str:
    """Build an anchored file-extension regex from one or more language groups.

    `_ext_re(_PROG_LANGS, _QUERY_LANGS)` -> r"\\.(php|js|...|go|rs|...|sql)$".
    Duplicate extensions across groups are harmless (regex alternation).
    """
    exts = [ext for group in groups for ext in group]
    return r"\.(" + "|".join(exts) + r")$"


def is_template_file(path: str) -> bool:
    """Return whether path is an inherently UI-emitting template file."""
    lowered = path.lower()
    extension = lowered.rpartition(".")[2]
    return extension in _TEMPLATE_EXTENSIONS or any(
        lowered.endswith(f".{suffix}") for suffix in _TEMPLATE_SUFFIXES
    )


DOMAIN_CATALOG = {
    "code": {
        "description": "All code files (code-reviewer)",
        "include": _ext_re(_PROG_LANGS, _STYLE_LANGS, _QUERY_LANGS),
        "exclude": None,
        # Matches production AND test files. Budget production first — on a
        # test-heavy branch, pure largest-first hands the reviewer test
        # files and starves the code under review (2026-07-21 incident).
        "budget_priority": "production_first",
    },
    "security": {
        "description": "Security-relevant code files",
        "include": _ext_re(_PROG_LANGS),
        "exclude": None,
        "budget_priority": "production_first",
    },
    "performance": {
        "description": "Performance-relevant code files (incl. SQL)",
        "include": _ext_re(_PROG_LANGS, _QUERY_LANGS),
        "exclude": None,
        "budget_priority": "production_first",
    },
    "dead-code": {
        "description": "Production code only, excluding tests (dead-code-reviewer)",
        "include": _ext_re(_PROG_LANGS, _STYLE_LANGS, _QUERY_LANGS),
        "exclude": _TEST_EXCLUDE,
    },
    "architecture": {
        "description": "Implementation files, excluding tests",
        "include": _ext_re(_PROG_LANGS, _QUERY_LANGS),
        "exclude": _TEST_EXCLUDE,
    },
    "wp-architecture": {
        "description": "WordPress PHP/JS/TS files",
        "include": r"\.(php|js|ts|jsx|tsx)$",
        "exclude": None,
        "budget_priority": "production_first",
    },
    "php-tests": {
        "description": "PHP test files only",
        "include": r"(Test\.php|_test\.php|tests/.*\.php|phpunit\.xml|bootstrap\.php)$",
        "exclude": None,
    },
    "js-tests": {
        "description": "JS/TS test files, excluding E2E",
        "include": r"(\.(test|spec)\.(js|ts|tsx|jsx)$|__tests__/)",
        "exclude": r"(^e2e/|/e2e/)",
    },
    "e2e-tests": {
        "description": "Playwright E2E test files",
        "include": _E2E_TEST_INCLUDE,
        "exclude": None,
    },
    "go-tests": {
        "description": "Go test files only",
        "include": r"_test\.go$",
        "exclude": None,
    },
    "rust-tests": {
        "description": "Rust files for rust-tests-reviewer: all .rs files (integration tests, benchmarks, and source files with inline #[cfg(test)] unit tests)",
        "include": r"(tests/.*\.rs$|benches/.*\.rs$|\.rs$)",
        "exclude": None,
    },
    "rust-test-dirs": {
        "description": "Rust dedicated test locations for triage only: tests/ and benches/ dirs are always test code; source .rs files are not (they mix production and inline unit tests)",
        "include": r"(tests/.*\.rs$|benches/.*\.rs$)",
        "exclude": None,
    },
    "python-tests": {
        "description": "Python test files only",
        "include": r"(test_.*\.py$|.*_test\.py$|tests/.*\.py$|conftest\.py$|pytest\.ini$|pyproject\.toml$)",
        "exclude": None,
    },
    "patterns": {
        "description": "All code files for pattern analysis",
        "include": _ext_re(_PROG_LANGS, _STYLE_LANGS),
        "exclude": None,
        "budget_priority": "production_first",
    },
    "a11y": {
        "description": "UI-emitting files for accessibility review (JS/TS/JSX/TSX/CSS + server-rendered markup: PHP/HTML/templates)",
        "include": _ext_re(_FRONTEND_LANGS, _STYLE_LANGS, _MARKUP_LANGS),
        "exclude": None,
        # Budget markup-evidence-bearing files FIRST: the broad markup-language
        # match can pull a huge non-UI PHP diff into scope alongside the tiny
        # template change that actually triggered dispatch — largest-first
        # budgeting would starve the evidence file out of the diff budget.
        "budget_priority": "markup_evidence",
    },
    "reliability": {
        "description": "Production code for operational resilience review",
        "include": _ext_re(_PROG_LANGS, _QUERY_LANGS),
        "exclude": _TEST_EXCLUDE,
    },
    "api-contract": {
        "description": "API surface files — endpoints, schemas, migrations, hook signatures",
        "include": _ext_re(_PROG_LANGS, _QUERY_LANGS),
        "exclude": _TEST_EXCLUDE,
    },
    "data-flow": {
        "description": "Data handling files — logging, serialization, storage, privacy",
        "include": _ext_re(_PROG_LANGS, _QUERY_LANGS),
        "exclude": _TEST_EXCLUDE,
    },
    "concurrency": {
        "description": "Concurrency-relevant files — async, transactions, queues, cron",
        "include": _ext_re(_PROG_LANGS, _QUERY_LANGS),
        "exclude": _TEST_EXCLUDE,
    },
    "clarity": {
        "description": "Code files for naming/documentation clarity review, excluding tests",
        "include": _ext_re(_PROG_LANGS),
        "exclude": _TEST_EXCLUDE,
    },
    "simplification": {
        "description": "All production code for complexity analysis, excluding tests",
        "include": _ext_re(_PROG_LANGS, _STYLE_LANGS, _QUERY_LANGS),
        "exclude": _TEST_EXCLUDE,
    },
    "docs-drift": {
        "description": "Code and documentation files for drift detection",
        "include": _ext_re(_PROG_LANGS, _DOC_LANGS, _DATA_LANGS),
        "exclude": _TEST_EXCLUDE,
    },
    "toolchain": {
        "description": "Developer toolchain configs — package managers, build tools, linters, version constraints, CI pipelines",
        "include": r"("
                   r"pnpm-workspace\.yaml|\.npmrc|\.pnpmrc|\.yarnrc|\.pnpmfile\.cjs|"
                   r"(^|/)package\.json$|"
                   r"\.lock$|pnpm-lock\.yaml$|package-lock\.json$|npm-shrinkwrap\.json$|go\.sum$|"
                   r"tsconfig.*\.json$|jsconfig.*\.json$|"
                   r"webpack\.config\.|vite\.config\.|rollup\.config\.|esbuild\.config\.|turbo\.json$|nx\.json$|"
                   r"babel\.config\.|\.babelrc|"
                   r"eslint\.config\.|\.eslintrc|\.prettierrc|\.stylelintrc|"
                   r"composer\.json$|phpstan.*\.neon|phpcs\.xml|phpunit\.xml|"
                   r"Dockerfile|docker-compose|\.wp-env\.json|\.wp-env\.override\.json|"
                   r"\.github/workflows/|\.gitlab-ci|Jenkinsfile|\.circleci/|"
                   r"\.nvmrc$|\.node-version$|\.tool-versions$|\.editorconfig$|"
                   r"renovate\.json|\.github/dependabot\.yml|"
                   r"Makefile$"
                   r")",
        "exclude": r"node_modules/",
        "list_only": r"(\.lock$|pnpm-lock\.yaml|package-lock\.json|npm-shrinkwrap\.json|go\.sum)",
    },
    "config-ops": {
        "description": "CI/CD configs, Docker, Terraform, and infrastructure files",
        "include": r"(\.github/workflows/|\.gitlab-ci|Dockerfile|docker-compose|\.tf$|\.tfvars$|\.toml$|Jenkinsfile|\.circleci/|Makefile$|\.helmfile|chart\.yaml$|values\.yaml$)",
        "exclude": None,
    },
    "reference-integrity": {
        "description": "Code and config files for reference integrity verification",
        "include": _ext_re(_PROG_LANGS, _DATA_LANGS),
        "exclude": _TEST_EXCLUDE,
    },
}

# Noise patterns — files no reviewer should waste context on
NOISE_PATTERNS = [
    # Lock files (all flavors) and images, fonts, media, binary assets
    r"\.(lock|png|jpg|jpeg|gif|svg|ico|webp|avif|bmp|woff|woff2|ttf|eot|otf|map)$",
    r"(package-lock\.json|pnpm-lock\.yaml|npm-shrinkwrap\.json|go\.sum)$",
    # Archives and compiled binaries
    r"\.(zip|tar|gz|tgz|jar|war|wasm|pyc|pyo|so|dylib|dll|exe)$",
    # Documents, translations, and non-code artifacts
    r"\.(pdf|mo|po|pot)$",
    # Jest snapshots (large, noisy)
    r"\.snap$",
    # Dependency and cache directories
    r"(^|/)(vendor|node_modules|\.yarn|__pycache__)/",
    # Coverage and tool cache directories
    r"(^|/)(\.cache|\.nyc_output|coverage|htmlcov)/",
    # Minified assets and source maps
    r"\.min\.(js|css)$",
    # Build artifacts, caches, and IDE/OS config
    r"(^dist/|^build/|^\.idea/|^\.vscode/|\.DS_Store$)",
    # Build and linter caches
    r"(tsconfig\.tsbuildinfo|\.eslintcache|\.stylelintcache)$",
]

# Stale branch threshold — branches this many commits behind the base
# trigger a warning message. Merge-base rebasing happens unconditionally
# (the threshold only controls the advisory warning, not the rebase decision).
STALE_BRANCH_THRESHOLD = 10


def run_cmd(cmd: List[str], check: bool = True, capture_stderr: bool = True) -> str:
    """Run a command and return stdout. Raises on failure if check=True."""
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=30,
        )
        if check and result.returncode != 0:
            stderr_msg = result.stderr.strip()
            # Truncate verbose git error output
            stderr_lines = stderr_msg.splitlines()
            if len(stderr_lines) > 5:
                stderr_msg = "\n".join(stderr_lines[:5]) + f"\n... ({len(stderr_lines) - 5} more lines truncated)"
            raise RuntimeError(
                f"Command failed (exit {result.returncode}): {' '.join(cmd)}\n"
                f"stderr: {stderr_msg}"
            )
        return result.stdout.strip()
    except subprocess.TimeoutExpired:
        raise RuntimeError(f"Command timed out after 30s: {' '.join(cmd)}")
    except FileNotFoundError:
        raise RuntimeError(f"Command not found: {cmd[0]}")


def detect_default_branch() -> str:
    """Detect the default branch (main/master/trunk/develop)."""
    # Try symbolic ref first (most reliable)
    try:
        ref = run_cmd(
            ["git", "symbolic-ref", "refs/remotes/origin/HEAD"],
            check=False,
        )
        if ref:
            return ref.replace("refs/remotes/origin/", "")
    except RuntimeError:
        pass

    # Fallback: check common branch names
    for branch in ["main", "master", "trunk", "develop"]:
        try:
            run_cmd(["git", "rev-parse", f"refs/remotes/origin/{branch}"], check=True)
            return branch
        except RuntimeError:
            continue

    return "main"  # last resort


def freshen_base_ref(branch: str) -> str:
    """
    Ensure the base ref is as fresh as possible by using the remote tracking ref.

    Fetches the latest state from origin (best-effort, silent on failure) and
    returns ``origin/<branch>`` when available. Falls back to the local ref
    if the remote ref doesn't exist or the fetch fails (e.g. offline).

    This prevents stale local branch refs from inflating the review scope with
    commits that are already on the remote default branch.
    """
    # Already a remote ref or a commit SHA — nothing to freshen.
    if branch.startswith("origin/") or re.match(r"^[0-9a-f]{7,40}$", branch):
        return branch

    remote_ref = f"origin/{branch}"

    # Best-effort fetch — single branch, no tags, quick timeout.
    try:
        subprocess.run(
            ["git", "fetch", "origin", branch, "--no-tags"],
            capture_output=True,
            text=True,
            timeout=15,
        )
    except (subprocess.TimeoutExpired, OSError):
        pass  # Offline or slow network — use whatever we have.

    # Prefer the remote ref if it exists.
    try:
        run_cmd(["git", "rev-parse", "--verify", remote_ref], check=True)
        return remote_ref
    except RuntimeError:
        return branch


def check_branch_freshness(base_ref: str) -> dict:
    """Check how far HEAD is behind the base ref.

    Returns:
        ahead: commits on branch not in base
        behind: commits on base not in branch
        is_stale: behind > STALE_BRANCH_THRESHOLD (advisory — used for
            warning messages only, NOT for gating merge-base rebasing)
        merge_base: the merge-base commit SHA (common ancestor)
    """
    ahead = 0
    behind = 0
    merge_base_sha = ""

    try:
        behind_str = run_cmd(
            ["git", "rev-list", "--count", f"HEAD..{base_ref}"], check=True,
        )
        behind = int(behind_str)
    except (RuntimeError, ValueError):
        pass

    try:
        ahead_str = run_cmd(
            ["git", "rev-list", "--count", f"{base_ref}..HEAD"], check=True,
        )
        ahead = int(ahead_str)
    except (RuntimeError, ValueError):
        pass

    try:
        merge_base_sha = run_cmd(
            ["git", "merge-base", base_ref, "HEAD"], check=True,
        )
    except RuntimeError:
        pass

    return {
        "ahead": ahead,
        "behind": behind,
        "is_stale": behind > STALE_BRANCH_THRESHOLD,
        "merge_base": merge_base_sha,
    }


def rebase_range_to_merge_base(range_spec: str, merge_base: str) -> str:
    """Replace the base ref in a range spec with the merge-base SHA.

    "origin/trunk..HEAD" + merge_base "abc1234" → "abc1234..HEAD".
    Returns original range_spec if no '..' or empty merge_base.
    """
    if not merge_base or ".." not in range_spec:
        return range_spec
    _, range_end = range_spec.split("..", 1)
    return f"{merge_base}..{range_end}"


def detect_range() -> Tuple[str, str]:
    """
    Detect the appropriate diff range.

    Returns:
        (range_spec, base_ref) — e.g. ("origin/main..HEAD", "origin/main")
        or ("--cached", "HEAD")

    Raises RuntimeError if no changes found.
    """
    default_branch = detect_default_branch()
    base_ref = freshen_base_ref(default_branch)

    # Check if current branch has diverged from default
    try:
        commit_count = run_cmd(
            ["git", "rev-list", "--count", f"{base_ref}..HEAD"],
            check=True,
        )
        if int(commit_count) > 0:
            return f"{base_ref}..HEAD", base_ref
    except (RuntimeError, ValueError):
        pass

    # Check for staged changes
    staged = run_cmd(["git", "diff", "--cached", "--name-only"], check=True)
    if staged:
        return "--cached", "HEAD"

    # Check for unstaged changes
    unstaged = run_cmd(["git", "diff", "--name-only"], check=True)
    if unstaged:
        return "", "HEAD"  # empty range = unstaged working tree diff

    raise RuntimeError("NO_CHANGES: No changes to review — clean working tree.")


def get_changed_files(range_spec: str) -> List[str]:
    """Get list of changed files for the given range."""
    if range_spec == "--cached":
        cmd = ["git", "diff", "--cached", "--name-only"]
    elif range_spec == "":
        cmd = ["git", "diff", "--name-only"]
    else:
        cmd = ["git", "diff", "--name-only", range_spec]

    output = run_cmd(cmd, check=True)
    if not output:
        return []
    return output.splitlines()


def filter_noise(files: List[str]) -> Tuple[List[str], List[str]]:
    """
    Remove files no reviewer should waste context on.

    Returns:
        (kept_files, skipped_files)
    """
    kept = []
    skipped = []

    for f in files:
        is_noise = False
        for pattern in NOISE_PATTERNS:
            if re.search(pattern, f):
                is_noise = True
                break
        if is_noise:
            skipped.append(f)
        else:
            kept.append(f)

    return kept, skipped


def filter_domain(files: List[str], domain: str) -> Tuple[List[str], List[str]]:
    """
    Apply domain-specific include/exclude filters.

    Returns:
        (matched_files, excluded_files)
    """
    if domain not in DOMAIN_CATALOG:
        raise RuntimeError(
            f"Unknown domain '{domain}'. "
            f"Available: {', '.join(sorted(DOMAIN_CATALOG.keys()))}"
        )

    spec = DOMAIN_CATALOG[domain]
    include_re = re.compile(spec["include"])
    exclude_re = re.compile(spec["exclude"]) if spec["exclude"] else None

    matched = []
    excluded = []

    for f in files:
        if not include_re.search(f):
            excluded.append(f)
            continue
        if exclude_re and exclude_re.search(f):
            excluded.append(f)
            continue
        matched.append(f)

    return matched, excluded


def get_diff_for_file(range_spec: str, filepath: str) -> str:
    """Get the diff for a single file."""
    if range_spec == "--cached":
        cmd = ["git", "diff", "--cached", "--", filepath]
    elif range_spec == "":
        cmd = ["git", "diff", "--", filepath]
    else:
        cmd = ["git", "diff", range_spec, "--", filepath]

    return run_cmd(cmd, check=True)


def count_diff_lines(diff_text: str) -> int:
    """Count meaningful lines in a diff (added + removed, not headers/context)."""
    count = 0
    for line in diff_text.splitlines():
        if line.startswith("+") and not line.startswith("+++"):
            count += 1
        elif line.startswith("-") and not line.startswith("---"):
            count += 1
    return count


def get_diffstat(range_spec: str, files: List[str]) -> Dict[str, Tuple[int, int]]:
    """
    Get per-file diffstat (additions, deletions) using git diff --numstat.

    Returns:
        {filepath: (additions, deletions)} for each file in the list.
        Binary files get (0, 0). Files not in the numstat output get (0, 0).
    """
    if range_spec == "--cached":
        cmd = ["git", "diff", "--cached", "--numstat"]
    elif range_spec == "":
        cmd = ["git", "diff", "--numstat"]
    else:
        cmd = ["git", "diff", "--numstat", range_spec]

    output = run_cmd(cmd, check=True)
    if not output:
        return {f: (0, 0) for f in files}

    # Parse numstat: "added\tremoved\tfilepath" (binary files show "-\t-\t")
    file_set = set(files)
    stats = {}
    for line in output.splitlines():
        parts = line.split("\t", 2)
        if len(parts) != 3:
            continue
        added_str, removed_str, filepath = parts
        if filepath not in file_set:
            continue
        try:
            added = int(added_str) if added_str != "-" else 0
            removed = int(removed_str) if removed_str != "-" else 0
        except ValueError:
            added, removed = 0, 0
        stats[filepath] = (added, removed)

    # Fill in any files not found in numstat
    for f in files:
        if f not in stats:
            stats[f] = (0, 0)

    return stats


def detect_output_dir() -> Tuple[str, Optional[str]]:
    """
    Detect output directory. Try gh/ghe to find PR number.

    Returns:
        (output_dir, pr_number_or_none)
    """
    # Detect if this is a github.a8c.com (GHE) or github.com repo
    try:
        remote_url = run_cmd(["git", "remote", "get-url", "origin"], check=False)
    except RuntimeError:
        remote_url = ""

    is_ghe = "github.a8c.com" in remote_url

    # Try the appropriate CLI first, then fallback
    cli_order = ["ghe", "gh"] if is_ghe else ["gh", "ghe"]

    for cli in cli_order:
        try:
            pr_num = run_cmd(
                [cli, "pr", "view", "--json", "number", "-q", ".number"],
                check=True,
            )
            if pr_num and pr_num.isdigit():
                output_dir = f"/tmp/pr-review-{pr_num}"
                os.makedirs(output_dir, exist_ok=True)
                return output_dir, pr_num
        except RuntimeError:
            continue

    return "/tmp", None


def detect_base_ref(range_spec: str) -> str:
    """Extract the base ref from a range spec."""
    if ".." in range_spec:
        return range_spec.split("..")[0]
    return "HEAD"


def build_scope(args: argparse.Namespace) -> dict:
    """
    Build the complete review scope.

    Returns a structured dict with all scope information.
    Raises RuntimeError on any failure (defensive — no silent errors).
    """
    # Step 0: Verify we're in a git repository
    try:
        run_cmd(["git", "rev-parse", "--git-dir"], check=True)
    except RuntimeError:
        raise RuntimeError("NOT_GIT_REPO: Not inside a git repository. Run from a git repo root.")

    # Step 1: Determine range
    if args.range:
        raw_base = detect_base_ref(args.range)
        # Freshen the base ref to avoid stale local branch refs.
        base_ref = freshen_base_ref(raw_base)
        # Rebuild range with the (possibly upgraded) base ref.
        if ".." in args.range:
            _, range_end = args.range.split("..", 1)
            range_spec = f"{base_ref}..{range_end}"
        else:
            range_spec = args.range
        # Validate the resolved base ref is valid
        try:
            run_cmd(["git", "rev-parse", base_ref], check=True)
        except RuntimeError:
            raise RuntimeError(
                f"Invalid range '{range_spec}': base ref '{base_ref}' does not exist."
            )
    else:
        range_spec, base_ref = detect_range()

    # Step 1.5: Check branch freshness and rebase to merge-base
    freshness = check_branch_freshness(base_ref)
    range_rebased = False
    if (freshness["merge_base"]
            and ".." in range_spec
            and not getattr(args, "no_merge_base", False)):
        range_spec = rebase_range_to_merge_base(range_spec, freshness["merge_base"])
        range_rebased = True

    # Step 2: Get changed files
    all_files = get_changed_files(range_spec)
    if not all_files:
        raise RuntimeError("NO_CHANGES: Range resolved but no files changed.")

    # Step 3: Filter noise
    after_noise, noise_skipped = filter_noise(all_files)

    # Step 3.5: Rescue list-only files from noise (domain-specific override).
    # Some domains (e.g., toolchain) need to know that lock files changed
    # even though they're normally noise. Rescued files appear in the file
    # list and diffstat but their full diff is not fetched.
    domain_spec = DOMAIN_CATALOG[args.domain]
    list_only_re = re.compile(domain_spec["list_only"]) if domain_spec.get("list_only") else None
    if list_only_re and noise_skipped:
        rescued = [f for f in noise_skipped if list_only_re.search(f)]
        if rescued:
            rescued_set = set(rescued)
            after_noise.extend(rescued)
            noise_skipped = [f for f in noise_skipped if f not in rescued_set]

    if not after_noise:
        raise RuntimeError(
            f"NO_RELEVANT_FILES: All {len(all_files)} changed files were "
            f"noise (lock files, vendor, build artifacts). Nothing to review."
        )

    # Step 4: Apply domain filter
    domain_matched, domain_excluded = filter_domain(after_noise, args.domain)
    if not domain_matched:
        return {
            "status": "NO_DOMAIN_FILES",
            "range": range_spec,
            "base_ref": base_ref,
            "total_changed": len(all_files),
            "noise_skipped": len(noise_skipped),
            "domain_excluded": len(domain_excluded),
            "domain": args.domain,
            "files": [],
            "list_only_files": [],
            "diffs": {},
            "skipped_files": {
                "noise": noise_skipped,
                "domain": domain_excluded,
            },
            "branch_freshness": {
                "ahead": freshness["ahead"],
                "behind": freshness["behind"],
                "is_stale": freshness["is_stale"],
                "merge_base": freshness["merge_base"],
                "range_rebased": range_rebased,
            },
        }

    # Step 5: Get diffstat for all matched files (cheap — single git command)
    diffstat = get_diffstat(range_spec, domain_matched)

    # Largest files first — ensures big changes get budget priority
    domain_matched_sorted = sorted(
        domain_matched,
        key=lambda f: sum(diffstat.get(f, (0, 0))),
        reverse=True,
    )

    # Step 6: Get diffs with budget control (skip if --base-ref-only or --summary)
    max_lines = args.max_lines
    diffs = {}
    total_lines = 0
    ordinary_budget_lines = 0
    budget_exceeded_files = []
    list_only_files = []

    # Determine if semantic filtering is enabled
    use_semantic_filter = not getattr(args, "no_semantic_filter", False)

    if not args.base_ref_only and not args.summary:
        # Markup-evidence budget priority (a11y): the broad markup-language
        # match can put a huge non-UI file ahead of the tiny template or
        # stylesheet change that actually carries the review evidence —
        # largest-first budgeting would then hand the reviewer everything
        # EXCEPT the file that triggered dispatch. Evidence = markup tokens
        # in the file's changed lines (classified in ONE combined git-diff
        # scan — see classify_markup_evidence), a stylesheet, OR an
        # inherently UI template. This mirrors the has_style_files and
        # has_template_files dispatch signals. Evidence-bearing files budget
        # first, largest-first within each tier.
        if domain_spec.get("budget_priority") == "markup_evidence":
            style_ext_re = re.compile(_ext_re(_STYLE_LANGS))
            scan_candidates = [
                f for f in domain_matched_sorted
                if not (list_only_re and list_only_re.search(f))
                and not style_ext_re.search(f)  # style files: evidence by extension
                and not is_template_file(f)  # pure templates: inherent UI
            ]
            token_files = classify_markup_evidence(range_spec, scan_candidates)

            def _is_evidence(f):
                return (
                    f in token_files
                    or bool(style_ext_re.search(f))
                    or is_template_file(f)
                )

            domain_matched_sorted = sorted(
                domain_matched_sorted,
                key=lambda f: (
                    not _is_evidence(f),            # evidence first
                    -sum(diffstat.get(f, (0, 0))),  # then largest first
                ),
            )

        # Production-first budget priority: domains that match both
        # production and test files would otherwise let a test-heavy branch
        # spend the whole budget on test files, starving the code under
        # review. Non-test files (per the shared _TEST_EXCLUDE classifier)
        # budget first, largest-first within each tier. Test-only domains
        # keep pure largest-first — test files ARE their evidence.
        elif domain_spec.get("budget_priority") == "production_first":
            production_first_test_re = re.compile(_TEST_EXCLUDE)
            domain_matched_sorted = sorted(
                domain_matched_sorted,
                key=lambda f: (
                    bool(production_first_test_re.search(f)),  # production first
                    -sum(diffstat.get(f, (0, 0))),             # then largest first
                ),
            )

        for filepath in domain_matched_sorted:
            # List-only files: appear in file list + diffstat, but no diff content.
            # These are files rescued from noise (e.g., lock files for toolchain domain)
            # that are too large/noisy for inline diffs but signal relevant changes.
            if list_only_re and list_only_re.search(filepath):
                list_only_files.append(filepath)
                continue

            if diffs and ordinary_budget_lines >= max_lines:
                budget_exceeded_files.append(filepath)
                continue

            # Pre-skip without fetching when the RAW diffstat estimate alone
            # exceeds the remaining ordinary budget — but ONLY when semantic
            # filtering is off. With filtering enabled, raw size proves nothing: a
            # 2,050-line patch that is 2,040 docblock lines filters to 10
            # reviewable lines and fits comfortably; the budget contract is
            # on FILTERED lines, so the file must be measured, not guessed.
            if not use_semantic_filter:
                est_lines = sum(diffstat.get(filepath, (0, 0)))
                remaining_ordinary_lines = max_lines - ordinary_budget_lines
                if diffs and est_lines > remaining_ordinary_lines:
                    budget_exceeded_files.append(filepath)
                    continue

            diff_text = get_diff_for_file(range_spec, filepath)

            # Apply semantic filtering to reduce noise (docblocks, comments, formatting)
            if use_semantic_filter:
                diff_text = apply_semantic_filter(diff_text)

            diff_lines = count_diff_lines(diff_text)
            is_protected_oversized_diff = not diffs and diff_lines > max_lines

            if (
                not is_protected_oversized_diff
                and ordinary_budget_lines + diff_lines > max_lines
            ):
                # Would exceed the ordinary pool. Keep scanning so a later,
                # smaller diff may still fit.
                budget_exceeded_files.append(filepath)
                continue

            diffs[filepath] = diff_text
            total_lines += diff_lines
            if not is_protected_oversized_diff:
                ordinary_budget_lines += diff_lines

    # Step 7: Detect output directory (skip network calls when --output-dir provided)
    if args.output_dir:
        output_dir = args.output_dir
        pr_number = None
        os.makedirs(output_dir, exist_ok=True)
    else:
        output_dir, pr_number = detect_output_dir()

    return {
        "status": "OK",
        "range": range_spec,
        "base_ref": base_ref,
        "pr_number": pr_number,
        "output_dir": output_dir,
        "domain": args.domain,
        "total_changed": len(all_files),
        "noise_skipped": len(noise_skipped),
        "domain_excluded": len(domain_excluded),
        "domain_matched": len(domain_matched),
        "files_with_diffs": len(diffs),
        "list_only_files": list_only_files,
        "total_diff_lines": total_lines,
        "budget_max": max_lines,
        "budget_exceeded_files": budget_exceeded_files,
        "files": domain_matched_sorted if (args.base_ref_only or args.summary) else list(diffs.keys()),
        "diffs": diffs,
        "diffstat": diffstat,
        "skipped_files": {
            "noise": noise_skipped,
            "domain": domain_excluded,
            "budget": budget_exceeded_files,
            "list_only": list_only_files,
        },
        "branch_freshness": {
            "ahead": freshness["ahead"],
            "behind": freshness["behind"],
            "is_stale": freshness["is_stale"],
            "merge_base": freshness["merge_base"],
            "range_rebased": range_rebased,
        },
    }


def format_text_output(scope: dict) -> str:
    """Format scope as structured text for agent consumption."""
    lines = []

    # Header — always present, agents parse this
    lines.append("=== REVIEW SCOPE ===")
    lines.append(f"STATUS: {scope['status']}")
    lines.append(f"RANGE: {scope.get('range', 'N/A')}")
    lines.append(f"BASE_REF: {scope.get('base_ref', 'N/A')}")
    lines.append(f"DOMAIN: {scope.get('domain', 'N/A')}")

    if scope.get("pr_number"):
        lines.append(f"PR_NUMBER: {scope['pr_number']}")
    lines.append(f"OUTPUT_DIR: {scope.get('output_dir', '/tmp')}")

    freshness = scope.get("branch_freshness")
    if freshness and freshness.get("range_rebased"):
        lines.append("")
        lines.append(f"RANGE_REBASED: true (using merge-base {freshness.get('merge_base', '')[:12]} as anchor)")
    if freshness and freshness.get("is_stale"):
        if not freshness.get("range_rebased"):
            lines.append("")
        lines.append(f"BRANCH_FRESHNESS: STALE ({freshness['behind']} commits behind base)")

    lines.append("")
    lines.append(f"FILES_CHANGED: {scope.get('total_changed', 0)}")
    lines.append(f"NOISE_SKIPPED: {scope.get('noise_skipped', 0)}")
    lines.append(f"DOMAIN_EXCLUDED: {scope.get('domain_excluded', 0)}")
    lines.append(f"DOMAIN_MATCHED: {scope.get('domain_matched', 0)}")
    lines.append(f"FILES_WITH_DIFFS: {scope.get('files_with_diffs', 0)}")
    list_only = scope.get("list_only_files", [])
    if list_only:
        lines.append(f"LIST_ONLY_FILES: {len(list_only)}")
    lines.append(f"TOTAL_DIFF_LINES: {scope.get('total_diff_lines', 0)}")

    if scope.get("budget_exceeded_files"):
        lines.append(
            f"BUDGET_EXCEEDED: {len(scope['budget_exceeded_files'])} files skipped "
            f"(max {scope.get('budget_max', 'N/A')} lines)"
        )

    if scope["status"] == "NO_DOMAIN_FILES":
        lines.append("")
        lines.append(
            f"No files matched domain '{scope['domain']}'. "
            f"Changed files were all noise ({scope.get('noise_skipped', 0)}) "
            f"or outside domain ({scope.get('domain_excluded', 0)})."
        )
        return "\n".join(lines)

    diffstat = scope.get("diffstat", {})
    is_summary = bool(diffstat) and not scope.get("diffs")

    if is_summary:
        # Summary mode: diffstat for ALL matched files, sorted by size descending
        lines.append("")
        lines.append("=== DIFFSTAT (all matched files, largest first) ===")
        lines.append(f"{'File':<80s} {'Added':>6s} {'Removed':>7s} {'Total':>6s}")
        lines.append("-" * 103)

        # Sort by total changes descending for summary view
        sorted_files = sorted(
            scope.get("files", []),
            key=lambda f: sum(diffstat.get(f, (0, 0))),
            reverse=True,
        )
        total_added = 0
        total_removed = 0
        for filepath in sorted_files:
            added, removed = diffstat.get(filepath, (0, 0))
            total = added + removed
            total_added += added
            total_removed += removed
            # Truncate long paths from the left
            display_path = filepath if len(filepath) <= 78 else "..." + filepath[-(78-3):]
            lines.append(f"{display_path:<80s} {'+' + str(added):>6s} {'-' + str(removed):>7s} {total:>6d}")

        lines.append("-" * 103)
        lines.append(
            f"{'TOTAL':<80s} {'+' + str(total_added):>6s} {'-' + str(total_removed):>7s} "
            f"{total_added + total_removed:>6d}"
        )
        lines.append("")
        lines.append(
            f"Use 'git diff {scope.get('range', '')} -- <file>' to read specific diffs."
        )
    else:
        # Regular mode: file list + diffs
        lines.append("")
        lines.append("=== FILES ===")
        for filepath in scope.get("files", []):
            added, removed = diffstat.get(filepath, (0, 0))
            lines.append(f"{filepath}  (+{added} -{removed})")

        # List-only files: changed but diff intentionally skipped (e.g., lock files)
        list_only_files = scope.get("list_only_files", [])
        if list_only_files:
            lines.append("")
            lines.append(f"=== CHANGED (no diff — {len(list_only_files)} lock/generated files) ===")
            lines.append("These files changed but diffs are skipped (too large/noisy for inline review).")
            lines.append(f"Use 'git diff {scope.get('range', '')} -- <file>' to inspect if relevant.")
            for filepath in list_only_files:
                added, removed = diffstat.get(filepath, (0, 0))
                lines.append(f"  {filepath}  (+{added} -{removed})")

        # Budget-exceeded files with their diffstat so agent knows what it's missing
        budget_files = scope.get("skipped_files", {}).get("budget", [])
        if budget_files:
            lines.append("")
            lines.append(f"=== NOT DIFFED (budget exceeded, {len(budget_files)} files) ===")
            lines.append("These files ARE IN YOUR SCOPE — their diffs were withheld only to fit")
            lines.append("the context budget. This list is your remaining work queue, largest")
            lines.append(
                f"first: review with 'git diff {scope.get('range', '')} -- <file>' "
                "while tool budget"
            )
            lines.append("remains, and declare only the files you genuinely cannot reach.")
            # Sort budget-exceeded by size descending so agent sees biggest changes first
            budget_sorted = sorted(
                budget_files,
                key=lambda f: sum(diffstat.get(f, (0, 0))),
                reverse=True,
            )
            for filepath in budget_sorted:
                added, removed = diffstat.get(filepath, (0, 0))
                lines.append(f"  {filepath}  (+{added} -{removed})")

        # Skipped files summary (noise + domain)
        skipped = scope.get("skipped_files", {})
        has_noise_or_domain = skipped.get("noise") or skipped.get("domain")
        if has_noise_or_domain:
            lines.append("")
            lines.append("=== SKIPPED ===")
            if skipped.get("noise"):
                lines.append(f"Noise ({len(skipped['noise'])}): {', '.join(skipped['noise'][:10])}")
                if len(skipped["noise"]) > 10:
                    lines.append(f"  ... and {len(skipped['noise']) - 10} more")
            if skipped.get("domain"):
                lines.append(
                    f"Outside domain ({len(skipped['domain'])}): "
                    f"{', '.join(skipped['domain'][:10])}"
                )
                if len(skipped["domain"]) > 10:
                    lines.append(f"  ... and {len(skipped['domain']) - 10} more")

        # Diffs
        if scope.get("diffs"):
            lines.append("")
            lines.append("=== DIFFS ===")
            for filepath, diff_text in scope["diffs"].items():
                lines.append(f"--- {filepath} ---")
                lines.append(diff_text)
                lines.append("")

    return "\n".join(lines)


def format_json_output(scope: dict) -> str:
    """Format scope as JSON (for programmatic consumption)."""
    # Convert diffstat tuples to dicts for JSON serialization
    output = dict(scope)
    if "diffstat" in output:
        output["diffstat"] = {
            f: {"added": a, "removed": r}
            for f, (a, r) in output["diffstat"].items()
        }
    return json.dumps(output, indent=2)



def write_scope_summary(scope: dict, path: str) -> None:
    """Persist a compact machine-readable scope summary for run-level
    coverage aggregation (reconciliation_context.aggregate_inline_coverage).

    Fail-open: a summary-write failure must never break scope output.
    """
    summary = {
        "schema": 1,
        "domain": scope.get("domain"),
        "range": scope.get("range"),
        "status": scope.get("status"),
        "files_with_diffs": sorted(scope.get("diffs", {}) or {}),
        "budget_exceeded_files": list(scope.get("budget_exceeded_files", []) or []),
        "list_only_files": list(scope.get("list_only_files", []) or []),
        "total_diff_lines": scope.get("total_diff_lines", 0),
        "budget_max": scope.get("budget_max"),
    }
    try:
        parent = os.path.dirname(path)
        if parent:
            os.makedirs(parent, exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(summary, f, indent=2)
    except OSError as e:
        print(f"WARNING: could not write scope summary to {path}: {e}", file=sys.stderr)


def main():
    parser = argparse.ArgumentParser(
        description="Review Scope — efficient diff scoping for review agents.",
        epilog="Available domains: " + ", ".join(sorted(DOMAIN_CATALOG.keys())),
    )
    parser.add_argument(
        "--domain",
        choices=sorted(DOMAIN_CATALOG.keys()),
        help="Domain filter to apply (determines which file types to include). Required unless --list-domains.",
    )
    parser.add_argument(
        "--range",
        default=None,
        help="Git range to diff (e.g., 'main..HEAD'). Auto-detected if omitted.",
    )
    parser.add_argument(
        "--max-lines",
        type=int,
        default=2000,
        help="Max diff lines to include (default: 2000). Files beyond budget are listed but not diffed.",
    )
    parser.add_argument(
        "--format",
        choices=["text", "json"],
        default="text",
        help="Output format (default: text). Use 'json' for programmatic consumption.",
    )
    parser.add_argument(
        "--summary",
        action="store_true",
        help="Output diffstat overview for all matched files (no diffs). Agent picks which files to deep-dive.",
    )
    parser.add_argument(
        "--base-ref-only",
        action="store_true",
        help="Only output the base ref and file list (no diffs). For agents that explore preexisting code.",
    )
    parser.add_argument(
        "--output-dir",
        default=None,
        help="Override output directory. Skips gh/ghe PR detection when provided.",
    )
    parser.add_argument(
        "--list-domains",
        action="store_true",
        help="List all available domains with descriptions and exit.",
    )
    parser.add_argument(
        "--no-merge-base",
        action="store_true",
        help="Disable automatic merge-base range adjustment (use raw two-dot range as-is).",
    )
    parser.add_argument(
        "--no-semantic-filter",
        action="store_true",
        help="Disable semantic noise filtering on diffs (keep docblocks, comments, formatting).",
    )
    parser.add_argument(
        "--summary-json-out",
        default=None,
        help="Write a machine-readable scope summary JSON (admitted/skipped files) to this path. Fail-open.",
    )

    args = parser.parse_args()

    # Handle --list-domains
    if args.list_domains:
        for name, spec in sorted(DOMAIN_CATALOG.items()):
            print(f"  {name:20s} {spec['description']}")
            print(f"  {'':20s} include: {spec['include']}")
            if spec["exclude"]:
                print(f"  {'':20s} exclude: {spec['exclude']}")
        sys.exit(0)

    if not args.domain:
        parser.error("--domain is required (unless using --list-domains)")
        sys.exit(1)

    try:
        scope = build_scope(args)

        if args.summary_json_out:
            write_scope_summary(scope, args.summary_json_out)

        if args.format == "json":
            print(format_json_output(scope))
        else:
            print(format_text_output(scope))

        # Exit code based on status
        if scope["status"] == "NO_DOMAIN_FILES":
            sys.exit(0)  # Not an error — agent should APPROVE and exit
        sys.exit(0)

    except RuntimeError as e:
        error_msg = str(e)

        # Structured error output so agents can parse it
        error_output = (
            f"=== REVIEW SCOPE ===\n"
            f"STATUS: ERROR\n"
            f"ERROR: {error_msg}\n"
        )

        # Special exit code for "no changes" (not a failure)
        if error_msg.startswith("NO_CHANGES:"):
            error_output += "ACTION: APPROVE and exit — nothing to review.\n"
            print(error_output)
            print(error_output, file=sys.stderr)
            sys.exit(2)

        # All other errors — agent should report back to caller
        error_output += "ACTION: Report this error to the caller. Do NOT proceed with review.\n"
        print(error_output)
        print(error_output, file=sys.stderr)
        sys.exit(1)

    except Exception as e:
        # Catch-all for unexpected errors — NEVER silently eat them
        error_output = (
            f"=== REVIEW SCOPE ===\n"
            f"STATUS: ERROR\n"
            f"ERROR: Unexpected error: {type(e).__name__}: {e}\n"
            f"ACTION: Report this error to the caller. Do NOT proceed with review.\n"
        )
        print(error_output)
        print(error_output, file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
