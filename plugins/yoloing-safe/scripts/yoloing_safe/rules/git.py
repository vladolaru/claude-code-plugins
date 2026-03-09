"""Git-oriented safety rules."""

from __future__ import annotations

import re

from ..registry import ask_rule, block_rule


_RE_GIT_PUSH = re.compile(r"^git push\b")
_RE_GIT_FORCE_FLAG = re.compile(r"(--force\b|-f\b)")
_RE_GIT_PUSH_REFSPEC_ALT = re.compile(r"--(tags|all|mirror)\b")
_RE_GIT_CHECKOUT = re.compile(r"^git checkout\b")
_RE_DOUBLE_DASH_SEP = re.compile(r"\s--\s")
_RE_GIT_RESTORE = re.compile(r"^git restore\b")
_RE_STAGED_FLAG = re.compile(r"(--staged|-S)\b")
_RE_WORKTREE_FLAG = re.compile(r"(--worktree|-W)\b")
_RE_GIT_CLEAN = re.compile(r"^git clean\b")
_RE_CLEAN_FORCE_FLAG = re.compile(r"-[a-zA-Z]*f")
_RE_CLEAN_DRY_RUN = re.compile(r"(-[a-zA-Z]*n|--dry-run)")
_RE_GIT_BRANCH_DELETE = re.compile(r"^git branch\s+-D\b")
_RE_GIT_REMOTE_REMOVE = re.compile(r"^git remote remove\b")
_RE_GIT_REFLOG_EXPIRE = re.compile(r"^git reflog expire\b")
_RE_GIT_GC_PRUNE = re.compile(r"^git gc\b.*--prune=")
_RE_GIT_PUSH_DELETE = re.compile(r"^git push\b.*(?:--delete|-d)\b")
_RE_GIT_PUSH_COLON_REF = re.compile(r"^git push\b.*\s:[^-\s]")


def detect_git_bare_push(ctx):
    """Detect git push without explicit branch specification."""
    command = ctx.command
    if not _RE_GIT_PUSH.search(command):
        return False
    if _RE_GIT_FORCE_FLAG.search(command):
        return False
    if _RE_GIT_PUSH_REFSPEC_ALT.search(command):
        return False
    parts = command.split()
    non_flag_parts = [part for part in parts[2:] if not part.startswith("-")]
    if len(non_flag_parts) < 2:
        return True
    return False


def detect_git_discard_changes(ctx):
    """Detect git checkout -- and git restore that discards working tree changes."""
    command = ctx.command
    if _RE_GIT_CHECKOUT.search(command) and _RE_DOUBLE_DASH_SEP.search(command):
        return True
    if _RE_GIT_RESTORE.search(command):
        has_staged = bool(_RE_STAGED_FLAG.search(command))
        has_worktree = bool(_RE_WORKTREE_FLAG.search(command))
        if has_staged and not has_worktree:
            return False
        return True
    return False


def detect_git_other_dangerous(ctx):
    """Detect other dangerous git ops that can discard history or refs."""
    command = ctx.command
    if _RE_GIT_CLEAN.search(command):
        if _RE_CLEAN_FORCE_FLAG.search(command) and not _RE_CLEAN_DRY_RUN.search(command):
            return True
    if _RE_GIT_BRANCH_DELETE.search(command):
        return True
    if _RE_GIT_REMOTE_REMOVE.search(command):
        return True
    if _RE_GIT_REFLOG_EXPIRE.search(command):
        return True
    if _RE_GIT_GC_PRUNE.search(command):
        return True
    if _RE_GIT_PUSH_DELETE.search(command):
        return True
    if _RE_GIT_PUSH_COLON_REF.search(command):
        return True
    return False


RULE_SPECS = [
    ("git_bare_push", block_rule(
        tools={"Bash"},
        detect=detect_git_bare_push,
        message="Push with an explicit branch to avoid pushing to an unexpected target. Use `git push origin HEAD` to push the current branch, or `git push origin <branch-name>` for a specific branch.",
        examples=["git push"],
    )),
    ("git_force_push", ask_rule(
        tools={"Bash"},
        patterns=[r"^git push\b"],
        require=[r"(--force\b|-f\b)"],
        exclude=[r"--force-with-lease", r"--force-if-includes"],
        message="Force push rewrites remote history and can discard teammates' work. Use `--force-with-lease` for a safer alternative. Confirm this is intentional.",
        examples=["git push --force origin hotfix/fix-arena"],
    )),
    ("git_hard_reset", ask_rule(
        tools={"Bash"},
        patterns=[r"^git reset\b"],
        require=[r"(--hard|--merge)\b"],
        message="Hard reset discards uncommitted changes permanently. Use `git stash` first to preserve work. Confirm you want to proceed.",
        examples=["git reset --hard main"],
    )),
    ("git_discard_changes", ask_rule(
        tools={"Bash"},
        detect=detect_git_discard_changes,
        message="This discards uncommitted changes to working tree files. Use `git stash` first if you might need them. Confirm you want to proceed.",
        examples=["git checkout -- ."],
    )),
    ("git_destroy_stash", ask_rule(
        tools={"Bash"},
        patterns=[r"^git stash (drop|clear)\b"],
        message="Dropping or clearing stashes permanently destroys saved work. List stashes with `git stash list` first. Confirm this is intentional.",
        examples=["git stash drop"],
    )),
    ("git_history_rewrite", ask_rule(
        tools={"Bash"},
        patterns=[r"^git filter-(branch|repo)\b"],
        message="Rewriting git history (`filter-branch`, `filter-repo`) is irreversible on shared branches. Confirm this is intentional.",
        examples=["git filter-branch --force HEAD"],
    )),
    ("git_config_changes", ask_rule(
        tools={"Bash"},
        patterns=[r"^git config\b"],
        require=[r"--(global|system)\b"],
        message="Global or system git config changes affect all repositories on this machine. Confirm this is intentional.",
        examples=["git config --global user.email 'test@test.com'"],
    )),
    ("git_other_dangerous", ask_rule(
        tools={"Bash"},
        detect=detect_git_other_dangerous,
        message="This git operation can cause data loss or affect collaboration. Confirm you want to proceed.",
        examples=["git branch -D feature/goat-skins"],
    )),
]

ALLOWLIST_PATTERNS = [
    ("git_discard_changes", re.compile(r"^git checkout (-b|--orphan) ")),
    ("git_discard_changes", re.compile(r"^git restore\b.*(?:--staged|-S)(?!.*(?:--worktree|-W))")),
    ("git_other_dangerous", re.compile(r"^git clean\b.*(--dry-run|-[a-zA-Z]*n)")),
    ("git_force_push", re.compile(r"^git push\b.*--force-(with-lease|if-includes)")),
]
