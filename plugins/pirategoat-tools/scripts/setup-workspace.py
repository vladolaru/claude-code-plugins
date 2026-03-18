#!/usr/bin/env python3
"""
Setup Workspace — deterministic workspace preparation for PR review.

Records the current branch, stashes dirty state if needed, and checks out
the PR branch. Outputs a JSON result to stdout.

Output: JSON to stdout with original_branch, stash_ref, was_dirty, checkout_ok.
"""

import argparse
import json
import subprocess
import sys


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


def resolve_gh_cmd():
    """Detect whether to use 'gh' or 'ghe' CLI."""
    origin = _run_cmd(["git", "remote", "get-url", "origin"]) or ""
    if "a8c.com" in origin or "automattic.com" in origin:
        return "ghe"
    return "gh"


# ---------------------------------------------------------------------------
# Core logic
# ---------------------------------------------------------------------------

def setup_workspace(pr_number, gh_cmd="gh"):
    """Set up workspace for PR review.

    1. Records the current branch
    2. Checks for dirty state
    3. Stashes if dirty (with -u for untracked files)
    4. Checks out the PR branch

    Returns a dict with original_branch, stash_ref, was_dirty, checkout_ok.
    On failure, includes an 'error' key.
    """
    result = {
        "original_branch": "unknown",
        "stash_ref": None,
        "was_dirty": False,
        "checkout_ok": False,
    }

    # 1. Record current branch
    branch = _run_cmd(["git", "branch", "--show-current"])
    if branch is not None:
        result["original_branch"] = branch

    # 2. Check for dirty state
    status = _run_cmd(["git", "status", "--porcelain"])
    if status is not None and status != "":
        result["was_dirty"] = True

        # 3. Stash dirty state (including untracked files)
        stash_ok = _run_cmd(
            ["git", "stash", "push", "-u", "-m", "pr-review-auto-stash"]
        )
        if stash_ok is not None:
            # Capture stash ref from stash list
            stash_list = _run_cmd(["git", "stash", "list"])
            if stash_list:
                # First line contains the most recent stash ref
                first_line = stash_list.split("\n")[0]
                # Extract stash@{N} from the line
                colon_idx = first_line.find(":")
                if colon_idx > 0:
                    result["stash_ref"] = first_line[:colon_idx]

    # 4. Check out the PR branch
    checkout = _run_cmd([gh_cmd, "pr", "checkout", str(pr_number)])
    if checkout is not None:
        result["checkout_ok"] = True
    else:
        result["error"] = f"Failed to checkout PR #{pr_number}"

    return result


# ---------------------------------------------------------------------------
# CLI entrypoint
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="Set up workspace for PR review"
    )
    parser.add_argument(
        "--pr-number", required=True,
        help="PR number to check out"
    )
    parser.add_argument(
        "--gh-cmd", default=None,
        help="GitHub CLI command (gh or ghe). Auto-detected if omitted."
    )
    args = parser.parse_args()

    gh_cmd = args.gh_cmd or resolve_gh_cmd()
    result = setup_workspace(args.pr_number, gh_cmd=gh_cmd)
    print(json.dumps(result))


if __name__ == "__main__":
    main()
