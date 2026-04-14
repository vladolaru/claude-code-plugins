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
# Domain Catalog — single source of truth for file filtering
# =============================================================================

# Shared test-file exclusion pattern for production-code domains.
_TEST_EXCLUDE = r"(tests?/|__tests__/|__mocks__/|spec/|\.test\.|\.spec\.|Test\.php$|_test\.php$|_test\.go$)"

DOMAIN_CATALOG = {
    "code": {
        "description": "All code files (code-reviewer)",
        "include": r"\.(php|js|ts|jsx|tsx|css|scss|py|java|rb|go|sql)$",
        "exclude": None,
    },
    "security": {
        "description": "Security-relevant code files",
        "include": r"\.(php|js|ts|jsx|tsx|py|rb|go)$",
        "exclude": None,
    },
    "performance": {
        "description": "Performance-relevant code files (incl. SQL)",
        "include": r"\.(php|js|ts|jsx|tsx|py|java|rb|go|sql)$",
        "exclude": None,
    },
    "dead-code": {
        "description": "Production code only, excluding tests (dead-code-reviewer)",
        "include": r"\.(php|js|ts|jsx|tsx|css|scss|py|java|rb|go|sql)$",
        "exclude": _TEST_EXCLUDE,
    },
    "architecture": {
        "description": "Implementation files, excluding tests",
        "include": r"\.(php|js|ts|jsx|tsx|py|java|cs|go|rb)$",
        "exclude": _TEST_EXCLUDE,
    },
    "wp-architecture": {
        "description": "WordPress PHP/JS/TS files",
        "include": r"\.(php|js|ts|jsx|tsx)$",
        "exclude": None,
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
        "include": r"(^e2e/|/e2e/|playwright\.config|Page\.(js|ts)$|PageObject\.(js|ts)$)",
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
        "include": r"\.(php|js|ts|jsx|tsx|css|scss|py|java|rb|go)$",
        "exclude": None,
    },
    "a11y": {
        "description": "Frontend files for accessibility review (JS/TS/JSX/TSX/CSS)",
        "include": r"\.(js|ts|jsx|tsx|css|scss)$",
        "exclude": None,
    },
    "reliability": {
        "description": "Production code for operational resilience review",
        "include": r"\.(php|js|ts|jsx|tsx|py|java|rb|go|sql)$",
        "exclude": _TEST_EXCLUDE,
    },
    "api-contract": {
        "description": "API surface files — endpoints, schemas, migrations, hook signatures",
        "include": r"\.(php|js|ts|jsx|tsx|py|go|sql)$",
        "exclude": _TEST_EXCLUDE,
    },
    "data-flow": {
        "description": "Data handling files — logging, serialization, storage, privacy",
        "include": r"\.(php|js|ts|jsx|tsx|py|rb|go|java|sql)$",
        "exclude": _TEST_EXCLUDE,
    },
    "concurrency": {
        "description": "Concurrency-relevant files — async, transactions, queues, cron",
        "include": r"\.(php|js|ts|jsx|tsx|py|go|java|sql)$",
        "exclude": _TEST_EXCLUDE,
    },
    "clarity": {
        "description": "Code files for naming/documentation clarity review, excluding tests",
        "include": r"\.(php|js|ts|jsx|tsx|py|java|cs|go|rb)$",
        "exclude": _TEST_EXCLUDE,
    },
    "simplification": {
        "description": "All production code for complexity analysis, excluding tests",
        "include": r"\.(php|js|ts|jsx|tsx|css|scss|py|java|cs|go|rb|sql)$",
        "exclude": _TEST_EXCLUDE,
    },
    "docs-drift": {
        "description": "Code and documentation files for drift detection",
        "include": r"\.(php|js|ts|jsx|tsx|py|java|cs|go|rb|md|txt|rst|yaml|yml|json)$",
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
        "include": r"\.(php|js|ts|jsx|tsx|py|java|rb|go|json|yaml|yml)$",
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
    budget_exceeded_files = []
    list_only_files = []

    # Determine if semantic filtering is enabled
    use_semantic_filter = not getattr(args, "no_semantic_filter", False)

    if not args.base_ref_only and not args.summary:
        for filepath in domain_matched_sorted:
            # List-only files: appear in file list + diffstat, but no diff content.
            # These are files rescued from noise (e.g., lock files for toolchain domain)
            # that are too large/noisy for inline diffs but signal relevant changes.
            if list_only_re and list_only_re.search(filepath):
                list_only_files.append(filepath)
                continue

            if total_lines >= max_lines:
                budget_exceeded_files.append(filepath)
                continue

            diff_text = get_diff_for_file(range_spec, filepath)

            # Apply semantic filtering to reduce noise (docblocks, comments, formatting)
            if use_semantic_filter:
                diff_text = apply_semantic_filter(diff_text)

            diff_lines = count_diff_lines(diff_text)

            if total_lines + diff_lines > max_lines and diffs:
                # Would exceed budget and we already have some diffs
                budget_exceeded_files.append(filepath)
                continue

            diffs[filepath] = diff_text
            total_lines += diff_lines

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
            lines.append("Use 'git diff <range> -- <file>' to read any of these selectively.")
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
