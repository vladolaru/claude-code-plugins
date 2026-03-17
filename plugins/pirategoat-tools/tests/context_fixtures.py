"""Shared test fixtures for review context protocol tests."""

# A complete context — all fields present
# (matches what gather-review-context.py produces after gap-filling
#  on top of what the bot writes)
COMPLETE_CONTEXT = {
    "version": 1,
    "mode": "pr",
    "github_cli_command": "ghe",
    "git": {
        "merge_base": "abc123",
        "git_range": "abc123..fix/thing",
        "head_ref": "fix/thing",
        "base_ref": "main",
        "changed_files": ["src/a.js", "src/b.js"],
        "changed_files_csv": "src/a.js,src/b.js",
        "diff_stats": " src/a.js | 10 ++++\n 2 files changed",
        "commit_count": 3,
    },
    "output": {"directory": "/tmp/pr-review-org-repo-42"},
    "pr": {
        "number": 42,
        "title": "Fix the thing",
        "author": "octocat",
        "state": "OPEN",
        "is_draft": False,
        "base_ref_name": "main",
        "head_ref_name": "fix/thing",
        "body": "Fixes WOOPLUG-1234",
        "labels": ["bug"],
        "url": "https://github.com/org/repo/pull/42",
    },
    "pr_size": {"files": 2, "lines": 38, "category": "small"},
    "review": {"agent_timeout_seconds": 1200},
    "reviews": {
        "summary": {"total": 1, "approved": 1, "changes_requested": 0, "commented": 0},
        "reviewers": [{"login": "maria", "type": "human", "state": "APPROVED"}],
        "pending": [],
    },
    "linked_issues": ["WOOPLUG-1234"],
    "staleness": {"is_stale": False, "commits_behind": 0},
    "has_unfetched_issues": True,  # WOOPLUG-1234 is a Linear ID
    "source": "pirategoat-bot",
}

# A partial context — what the bot writes (git + basic PR, no rich fields)
PARTIAL_CONTEXT = {
    "version": 1,
    "mode": "pr",
    "github_cli_command": "ghe",
    "git": {
        "merge_base": "abc123",
        "git_range": "abc123..fix/thing",
        "head_ref": "fix/thing",
        "base_ref": "main",
        "changed_files": ["src/a.js", "src/b.js"],
        "changed_files_csv": "src/a.js,src/b.js",
        "diff_stats": " src/a.js | 10 ++++\n 2 files changed",
        "commit_count": 3,
    },
    "output": {"directory": "/tmp/pr-review-org-repo-42"},
    "pr": {"number": 42},
    "pr_size": {"files": 2, "lines": 38, "category": "small"},
    "review": {"agent_timeout_seconds": 1200},
    "source": "pirategoat-bot",
}

# A branch-mode context — no PR metadata
BRANCH_CONTEXT = {
    "version": 1,
    "mode": "branch",
    "github_cli_command": "gh",
    "git": {
        "merge_base": "abc123",
        "git_range": "abc123..HEAD",
        "head_ref": "feat/new-feature",
        "base_ref": "main",
        "changed_files": ["src/feature.py", "tests/test_feature.py"],
        "changed_files_csv": "src/feature.py,tests/test_feature.py",
        "diff_stats": " src/feature.py | 50 ++++\n tests/test_feature.py | 30 +++\n 2 files changed",
        "commit_count": 5,
    },
    "output": {"directory": "/tmp/branch-review-org-repo-feat-new-feature"},
    "pr_size": {"files": 2, "lines": 80, "category": "small"},
    "review": {"agent_timeout_seconds": 1200},
    "has_unfetched_issues": False,
}

# Run config fixtures for the unified pipeline
RUN_CONFIG_PR = {
    "mode": "pr",
    "pr_number": "42",
    "interactive": True,
}

RUN_CONFIG_BOT = {
    "mode": "pr",
    "pr_number": "42",
    "interactive": False,
    "output_instructions": "Keep it brief.",
}

PIPELINE_RESULT_SUCCESS = {
    "status": "success",
    "verdict": "REQUEST_CHANGES",
    "report_path": "/tmp/pr-review-org-repo-42/review-report.md",
    "findings_path": "/tmp/pr-review-org-repo-42/review-findings.json",
    "critic_verdict": "REVISE",
    "degradation_notes": [],
    "review_baseline_saved": True,
}
