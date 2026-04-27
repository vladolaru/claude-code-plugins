#!/usr/bin/env python3
"""
Gather Review Context — unified Ring 1 context for all review entry points.

Supports --pr-number (PR review) and --branch (branch review, with optional
--incremental). Gap-filling: reads whatever review-context.json already exists,
fills in what's missing, writes the complete file.

Output: review-context.json in --output-dir with snake_case keys.
"""

import argparse
import json
import os
import re
import subprocess
import sys


# Host-context resolver import is best-effort — the scripts/ directory must
# be importable (and ahead of any shadowing test packages like tests/hosts/)
# for this to work. We attempt it once at module load so _fill_host_context()
# does not mutate sys.path on every call.
_HOSTS_CHAIN = None
_scripts_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _scripts_dir in sys.path:
    sys.path.remove(_scripts_dir)
sys.path.insert(0, _scripts_dir)
try:
    from hosts.chain import ResolverChain as _HOSTS_CHAIN  # noqa: E402
except ImportError:
    _HOSTS_CHAIN = None


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

KNOWN_AI_REVIEWERS = {
    "coderabbitai", "github-actions", "copilot", "codeclimate",
    "sonarcloud", "deepsource-autofix", "snyk-bot", "dependabot", "renovate",
}


# ---------------------------------------------------------------------------
# Deterministic helpers
# ---------------------------------------------------------------------------

def categorize_reviewer(login: str) -> str:
    """Categorize a reviewer login as human, bot, or ai."""
    if login.endswith("[bot]"):
        return "bot"
    base = login.removesuffix("[bot]")
    if base in KNOWN_AI_REVIEWERS:
        return "ai"
    return "human"


def extract_linked_issues(body: str) -> list:
    """Extract Linear IDs and GitHub issue refs from PR body."""
    if not body:
        return []
    ids = set()
    for m in re.finditer(r'\b([A-Z]+-\d+)\b', body):
        ids.add(m.group(1))
    for m in re.finditer(r'(?:closes?|fixes?|resolves?|refs?)\s+#(\d+)', body, re.IGNORECASE):
        ids.add(m.group(1))
    return sorted(ids)


def bucket_pr_size(lines: int) -> str:
    """Categorize PR size by total changed lines."""
    if lines <= 30:
        return "tiny"
    if lines <= 200:
        return "small"
    if lines <= 700:
        return "medium"
    if lines <= 2000:
        return "large"
    if lines <= 5000:
        return "huge"
    return "vlad-sized"


def safe_dirname(name: str) -> str:
    """Replace anything not alphanumeric, dot, underscore, or hyphen."""
    return re.sub(r'[^a-zA-Z0-9._-]', '-', name).strip('-')


# ---------------------------------------------------------------------------
# Shell helpers
# ---------------------------------------------------------------------------

def _run_cmd(cmd, cwd=None):
    """Run a shell command and return stdout, or None on failure."""
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, cwd=cwd, timeout=30)
        if r.returncode == 0:
            return r.stdout.strip()
    except (subprocess.TimeoutExpired, FileNotFoundError):
        pass
    return None


def _resolve_repo_root(path):
    """Return the git root for path when available, otherwise path itself."""
    absolute = os.path.abspath(path)
    cwd = absolute if os.path.isdir(absolute) else os.path.dirname(absolute)
    root = _run_cmd(["git", "rev-parse", "--show-toplevel"], cwd=cwd)
    return root or absolute


def resolve_gh_cmd():
    """Detect whether to use 'gh' or 'ghe' CLI."""
    origin = _run_cmd(["git", "remote", "get-url", "origin"]) or ""
    if "a8c.com" in origin or "automattic.com" in origin:
        return "ghe"
    return "gh"


# ---------------------------------------------------------------------------
# Fill helpers — each only runs external commands when fields are missing
# ---------------------------------------------------------------------------

def _fill_git_context(ctx, pr_number=None, branch=False, incremental=False, git_range=None):
    """Fill git context fields (merge_base, head_ref, changed_files, etc.)."""
    git = ctx.setdefault("git", {})

    if git_range:
        # Explicit range provided
        git.setdefault("git_range", git_range)
        parts = git_range.split("..")
        if len(parts) == 2:
            git.setdefault("merge_base", parts[0])
            git.setdefault("head_ref", parts[1])
    elif pr_number and "merge_base" not in git:
        gh_cmd = ctx.get("github_cli_command", "gh")
        # Get PR base info
        pr_info = _run_cmd([gh_cmd, "pr", "view", str(pr_number), "--json",
                           "baseRefName,headRefName", "-q",
                           ".baseRefName + \" \" + .headRefName"])
        if pr_info:
            parts = pr_info.split()
            if len(parts) == 2:
                git.setdefault("base_ref", parts[0])
                git.setdefault("head_ref", parts[1])

        # Compute merge base
        base = git.get("base_ref", "main")
        head = git.get("head_ref", "HEAD")
        merge_base = _run_cmd(["git", "merge-base", f"origin/{base}", head])
        if merge_base:
            git["merge_base"] = merge_base
            git.setdefault("git_range", f"{merge_base}..{head}")
    elif branch and "merge_base" not in git:
        head = _run_cmd(["git", "branch", "--show-current"]) or "HEAD"
        git.setdefault("head_ref", head)

        if incremental:
            # Baseline migration (rule 26): read new format first, fall back to legacy
            output_base = ctx.get("output", {}).get("directory", ".")
            baseline_file = os.path.join(output_base, ".branch-review-baseline.json")
            legacy_file = os.path.join(output_base, ".review-state.json")
            state_file = None
            if os.path.isfile(baseline_file):
                state_file = baseline_file
            elif os.path.isfile(legacy_file):
                state_file = legacy_file  # migration read
            if state_file:
                with open(state_file) as f:
                    state = json.load(f)
                last_sha = state.get("last_reviewed_sha")
                if last_sha:
                    # Validate the SHA is a valid ancestor of HEAD.
                    # After rebases or force-pushes, the persisted SHA may
                    # no longer exist in the current history.
                    is_ancestor = _run_cmd(
                        ["git", "merge-base", "--is-ancestor", last_sha, "HEAD"]
                    )
                    if is_ancestor is not None:  # exit code 0 = is ancestor
                        git.setdefault("merge_base", last_sha)
                        git.setdefault("git_range", f"{last_sha}..HEAD")
                    else:
                        # SHA is not an ancestor — history was rewritten.
                        # Fall through to full-branch range detection below.
                        print(
                            f"WARNING: last_reviewed_sha {last_sha[:12]} is not an "
                            f"ancestor of HEAD (history rewritten?). "
                            f"Falling back to full-branch review.",
                            file=sys.stderr,
                        )

        if "merge_base" not in git:
            # Detect default branch
            default_branch = _run_cmd(["git", "symbolic-ref", "refs/remotes/origin/HEAD"])
            if default_branch:
                default_branch = default_branch.replace("refs/remotes/origin/", "")
            else:
                default_branch = "main"
            git.setdefault("base_ref", default_branch)
            merge_base = _run_cmd(["git", "merge-base", f"origin/{default_branch}", "HEAD"])
            if merge_base:
                git["merge_base"] = merge_base
                git.setdefault("git_range", f"{merge_base}..HEAD")

    # Changed files
    if "changed_files" not in git and git.get("git_range"):
        files_output = _run_cmd(["git", "diff", "--name-only", git["git_range"]])
        if files_output:
            git["changed_files"] = [f for f in files_output.split("\n") if f]

    # Diff stats
    if "diff_stats" not in git and git.get("git_range"):
        stats = _run_cmd(["git", "diff", "--stat", git["git_range"]])
        if stats:
            git["diff_stats"] = stats

    # Commit count
    if "commit_count" not in git and git.get("git_range"):
        count = _run_cmd(["git", "rev-list", "--count", git["git_range"]])
        if count:
            try:
                git["commit_count"] = int(count)
            except ValueError:
                pass


def _fill_pr_metadata(ctx):
    """Fill PR metadata fields from gh pr view."""
    pr = ctx.get("pr", {})
    gh_cmd = ctx.get("github_cli_command", "gh")
    pr_number = pr.get("number")
    if not pr_number:
        return

    fields = "title,author,state,isDraft,baseRefName,headRefName,body,labels,url"
    raw = _run_cmd([gh_cmd, "pr", "view", str(pr_number), "--json", fields])
    if not raw:
        return

    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return

    pr.setdefault("title", data.get("title", ""))
    author = data.get("author", {})
    pr.setdefault("author", author.get("login", "") if isinstance(author, dict) else str(author))
    pr.setdefault("state", data.get("state", ""))
    pr.setdefault("is_draft", data.get("isDraft", False))
    pr.setdefault("base_ref_name", data.get("baseRefName", ""))
    pr.setdefault("head_ref_name", data.get("headRefName", ""))
    pr.setdefault("body", data.get("body", ""))
    labels = data.get("labels", [])
    pr.setdefault("labels", [l.get("name", l) if isinstance(l, dict) else l for l in labels])
    pr.setdefault("url", data.get("url", ""))


def _fill_reviews(ctx):
    """Fill review summary from gh pr view."""
    pr = ctx.get("pr", {})
    gh_cmd = ctx.get("github_cli_command", "gh")
    pr_number = pr.get("number")
    if not pr_number:
        return

    raw = _run_cmd([gh_cmd, "pr", "view", str(pr_number), "--json",
                    "reviews,reviewRequests"])
    if not raw:
        return

    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return

    reviews_raw = data.get("reviews", [])
    review_requests = data.get("reviewRequests", [])

    # Deduplicate: keep latest review per author
    latest = {}
    for r in reviews_raw:
        author = r.get("author", {}).get("login", "unknown")
        latest[author] = r

    approved = 0
    changes_requested = 0
    commented = 0
    reviewers = []

    for login, r in latest.items():
        state = r.get("state", "").upper()
        if state == "APPROVED":
            approved += 1
        elif state == "CHANGES_REQUESTED":
            changes_requested += 1
        elif state == "COMMENTED":
            commented += 1
        reviewers.append({
            "login": login,
            "type": categorize_reviewer(login),
            "state": state,
        })

    pending = []
    for rr in review_requests:
        login = rr.get("login", rr.get("name", "unknown"))
        pending.append(login)

    ctx["reviews"] = {
        "summary": {
            "total": len(latest),
            "approved": approved,
            "changes_requested": changes_requested,
            "commented": commented,
        },
        "reviewers": reviewers,
        "pending": pending,
    }


# ---------------------------------------------------------------------------
# Core: load and fill
# ---------------------------------------------------------------------------

def load_and_fill(ctx_path, pr_number=None, gh_cmd=None, branch=False,
                  incremental=False, git_range=None, repo_path=None):
    """Load existing context, fill missing fields, return complete context."""
    ctx = {}
    if os.path.isfile(ctx_path):
        with open(ctx_path) as f:
            ctx = json.load(f)

    ctx.setdefault("version", 1)

    # Mode
    if pr_number:
        ctx.setdefault("mode", "pr")
    elif branch:
        ctx.setdefault("mode", "branch")

    # GitHub CLI command
    if "github_cli_command" not in ctx:
        ctx["github_cli_command"] = gh_cmd or resolve_gh_cmd()

    # Git context — recompute when explicit inputs are provided.
    # Skip recomputation when pre-computed context exists: merge_base is
    # present and no explicit git_range override. Caller-agnostic (rule 28):
    # any caller that writes review-context.json with a valid merge_base
    # gets this optimization — no identity detection.
    git = ctx.setdefault("git", {})
    precomputed = (
        "merge_base" in git
        and not git_range  # explicit range overrides even pre-computed context
    )
    if not precomputed:
        # Clear stale git context so _fill_git_context recomputes
        if "merge_base" in git:
            git.clear()
        _fill_git_context(ctx, pr_number=pr_number, branch=branch,
                         incremental=incremental, git_range=git_range)

    # Derived git fields
    if "changed_files_csv" not in git and "changed_files" in git:
        git["changed_files_csv"] = ",".join(git["changed_files"])

    # PR metadata — fetch what's missing
    pr = ctx.setdefault("pr", {})
    if pr_number:
        pr.setdefault("number", int(pr_number))
    if pr.get("number") and "body" not in pr:
        _fill_pr_metadata(ctx)

    # PR size
    pr_size = ctx.setdefault("pr_size", {})
    if "category" not in pr_size and "lines" in pr_size:
        pr_size["category"] = bucket_pr_size(pr_size.get("lines", 0))
    elif "category" not in pr_size and git.get("changed_files"):
        # Estimate lines from diff stats if available
        diff_stats = git.get("diff_stats", "")
        lines = 0
        # Try to parse total from last line of diff stats
        for line in diff_stats.split("\n"):
            for m in re.finditer(r'(\d+)\s+insertion', line):
                lines += int(m.group(1))
            for m in re.finditer(r'(\d+)\s+deletion', line):
                lines += int(m.group(1))
        if lines > 0:
            pr_size["lines"] = lines
            pr_size["category"] = bucket_pr_size(lines)
        pr_size.setdefault("files", len(git.get("changed_files", [])))

    # Reviews — categorize if raw data present but categorization missing
    if pr.get("number") and "reviews" not in ctx:
        _fill_reviews(ctx)

    # Linked issues — extract from body if missing
    if "linked_issues" not in ctx and pr.get("body"):
        ctx["linked_issues"] = extract_linked_issues(pr["body"])

    # Also extract from branch name (e.g. fix/WOOPLUG-5988-desc → WOOPLUG-5988)
    head_ref = git.get("head_ref", "")
    if head_ref:
        existing = set(ctx.get("linked_issues", []))
        for m in re.finditer(r'\b([A-Z]+-\d+)\b', head_ref):
            existing.add(m.group(1))
        ctx["linked_issues"] = sorted(existing)

    # Staleness detection — compare merge_base age against base branch
    _detect_staleness(ctx)

    # Linear ID flagging — detect TEAM-NNN patterns for MCP fetch
    _detect_linear_issues(ctx)

    # GitHub issue fetching — fetch #NNN details via gh
    _fetch_github_issues(ctx)

    # Author name resolution — fetch display name for PR author
    _resolve_author_name(ctx)

    # Review defaults
    ctx.setdefault("review", {}).setdefault("agent_timeout_seconds", 1200)

    # Populate the per-clone install cache so InstallCacheResolver can
    # surface library-dep entries below. Best-effort — install failures
    # degrade host_context but do not block the review.
    repo_root = _resolve_repo_root(repo_path or os.getcwd())
    try:
        _populate_install_cache(repo_root)
    except Exception:  # noqa: BLE001 — review must continue
        pass

    # Host context — discover from the repo root when git can identify it.
    _fill_host_context(ctx, repo_root)

    return ctx


def _detect_staleness(ctx):
    """Detect if the branch is stale (behind the base branch)."""
    git = ctx.get("git", {})
    merge_base = git.get("merge_base")
    base_ref = git.get("base_ref", "main")
    if not merge_base:
        return

    # Count commits the base branch has that the merge_base doesn't
    count_str = _run_cmd(["git", "rev-list", "--count",
                          f"{merge_base}..origin/{base_ref}"])
    if count_str:
        try:
            behind = int(count_str)
            ctx["staleness"] = {
                "is_stale": behind >= 10,  # matches STALE_BRANCH_THRESHOLD
                "commits_behind": behind,
            }
        except ValueError:
            pass


def _detect_linear_issues(ctx):
    """Flag has_unfetched_issues when Linear IDs (TEAM-NNN) are found."""
    issues = ctx.get("linked_issues", [])
    # Linear IDs match [A-Z]+-\d+ but NOT pure GitHub refs (which are just numbers)
    linear_ids = [i for i in issues if re.match(r'^[A-Z]+-\d+$', str(i))]
    ctx["has_unfetched_issues"] = len(linear_ids) > 0


def _fetch_github_issues(ctx):
    """Fetch details for GitHub issue refs (#NNN)."""
    issues = ctx.get("linked_issues", [])
    gh_cmd = ctx.get("github_cli_command", "gh")
    details = []

    for issue_ref in issues:
        # Only fetch pure numeric refs (GitHub issues)
        if isinstance(issue_ref, str) and issue_ref.isdigit():
            result = _run_cmd([gh_cmd, "issue", "view", issue_ref,
                              "--json", "title,body,labels"])
            if result:
                try:
                    data = json.loads(result)
                    details.append({
                        "id": f"#{issue_ref}",
                        "title": data.get("title", ""),
                        "body": data.get("body", ""),
                        "labels": [l.get("name", "") for l in data.get("labels", [])],
                    })
                except (json.JSONDecodeError, KeyError):
                    pass

    if details:
        ctx["linked_issues_details"] = details


def _resolve_author_name(ctx):
    """Fetch PR author's display name via GitHub API."""
    pr = ctx.get("pr", {})
    author = pr.get("author")
    if not author or pr.get("author_name"):
        return

    gh_cmd = ctx.get("github_cli_command", "gh")
    name = _run_cmd([gh_cmd, "api", f"users/{author}", "--jq", ".name"])
    if name:
        pr["author_name"] = name


def _populate_install_cache(repo_path):
    """Run ensure_installed.py for the repo. Returns parsed payload or empty dict.

    Best-effort: subprocess failure / timeout / unparseable JSON / missing
    script all degrade silently to {}. The caller wraps this in a try/except
    too, so a raised exception also doesn't block the review.

    Currently invoked for side effects (cache population) only — load_and_fill
    discards the return. Returning the payload anyway leaves the door open for
    a debug command or a future caller to inspect populate status.
    """
    # Reuse _scripts_dir resolved at module load — single source of truth for
    # the scripts/ root path, shared with the hosts.chain import above.
    script = os.path.join(_scripts_dir, "hosts", "ensure_installed.py")
    if not os.path.isfile(script):
        return {}
    try:
        result = subprocess.run(
            [sys.executable, script, "--repo", repo_path],
            capture_output=True, text=True,
            # Matches the inner per-manager timeout in
            # ensure_installed.py:_run_install_command. A pathological install
            # that consumes the full inner timeout will trip both timeouts at
            # once — the caller still degrades silently, the inner banner is lost.
            timeout=20 * 60,
        )
        if result.returncode != 0:
            return {}
        try:
            return json.loads(result.stdout)
        except json.JSONDecodeError:
            return {}
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        return {}


def _fill_host_context(ctx, repo_path):
    """Populate host_context using hosts.chain.ResolverChain.

    Failure is soft: if the hosts package cannot be imported at module
    load, _HOSTS_CHAIN is None and we record host_context=None. Existing
    values are overwritten because review-context.json may be reused across
    runs and stale host paths are worse than no host context.
    """
    if _HOSTS_CHAIN is None:
        ctx["host_context"] = None
        return
    ctx["host_context"] = _HOSTS_CHAIN().run(repo_path).to_dict()


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="Gather review context — unified Ring 1 context."
    )
    parser.add_argument("--pr-number", type=str,
                        help="PR number (PR review mode)")
    parser.add_argument("--branch", action="store_true",
                        help="Branch review mode")
    parser.add_argument("--incremental", action="store_true",
                        help="Incremental branch review (resume from last state)")
    parser.add_argument("--git-range", type=str,
                        help="Explicit git range (e.g. abc123..HEAD)")
    parser.add_argument("--output-dir", type=str, required=True,
                        help="Output directory for review-context.json")
    parser.add_argument("--repo-path", type=str, default=None,
                        help="Path to the repo under review (for host-context "
                             "discovery). Defaults to the git root of the "
                             "current working directory when available.")

    args = parser.parse_args()

    if not args.pr_number and not args.branch:
        print("ERROR: Must provide --pr-number or --branch", file=sys.stderr)
        sys.exit(1)

    os.makedirs(args.output_dir, exist_ok=True)
    ctx_path = os.path.join(args.output_dir, "review-context.json")

    ctx = load_and_fill(
        ctx_path,
        pr_number=args.pr_number,
        branch=args.branch,
        incremental=args.incremental,
        git_range=args.git_range,
        repo_path=args.repo_path,
    )

    # Set output directory
    ctx.setdefault("output", {})["directory"] = args.output_dir

    # Write back
    with open(ctx_path, "w") as f:
        json.dump(ctx, f, indent=2)

    print(json.dumps(ctx, indent=2))


if __name__ == "__main__":
    main()
