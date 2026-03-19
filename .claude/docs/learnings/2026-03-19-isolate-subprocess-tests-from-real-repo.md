# Isolate subprocess tests from the real repo — git-mutating scripts stash uncommitted work

**Date:** 2026-03-19
**Tags:** testing, subprocess, git, isolation, pirategoat-tools

## Rule

Tests that invoke scripts via subprocess which run git operations (stash, checkout, reset) MUST pass `cwd=tmp_path` with an initialized temp git repo. Without isolation, scripts detect dirty working tree state in the real repo and silently mutate it — `git stash push -u` stashes uncommitted edits, `git checkout` switches branches, etc.

## Context

`TestStep2Orchestration` in `test_pipeline_orchestration.py` ran `review-pipeline.py --step 2` via `subprocess.run()` without a `cwd` parameter. Step 2 invoked `setup-workspace.py`, which:

1. Ran `git status --porcelain` — detected uncommitted edits in the real repo
2. Ran `git stash push -u -m "pr-review-auto-stash"` — silently stashed those edits
3. Tried `gh pr checkout 42` — failed (wrong repo), but the stash already happened

The result: any uncommitted file edits vanished during the test suite. 37 stale `pr-review-auto-stash` entries accumulated over time. Edits made between runs of the test suite were silently lost.

The symptom was subtle — files reverted to their last-committed state with no error, no test failure, and no visible output. The only clue was a Claude Code system reminder noting the file was "modified, either by the user or by a linter."

## Examples

**Incorrect — subprocess inherits real repo cwd:**
```python
def _run(self, *args):
    cmd = [sys.executable, str(SCRIPT_PATH)] + list(args)
    return subprocess.run(cmd, capture_output=True, text=True)  # no cwd!
```

**Correct — isolated temp git repo:**
```python
def _run(self, *args, cwd=None):
    cmd = [sys.executable, str(SCRIPT_PATH)] + list(args)
    return subprocess.run(cmd, capture_output=True, text=True, cwd=cwd)

@staticmethod
def _init_git_repo(path):
    subprocess.run(["git", "init"], cwd=str(path), capture_output=True, check=True)
    subprocess.run(["git", "config", "user.email", "test@test.com"], cwd=str(path), capture_output=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=str(path), capture_output=True)
    subprocess.run(["git", "config", "commit.gpgsign", "false"], cwd=str(path), capture_output=True)
    (path / "README.md").write_text("# test\n")
    subprocess.run(["git", "add", "."], cwd=str(path), capture_output=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=str(path), capture_output=True)

def test_step_2(self, tmp_path):
    self._init_git_repo(tmp_path)
    self._run("--step", "2", "--output-dir", str(tmp_path), cwd=str(tmp_path))
```

## Detection

If you suspect this is happening:
- Check `git stash list` for `pr-review-auto-stash` entries
- Run `stat -f "%m"` on the file before and after the test suite
- Add a canary marker (`echo "# CANARY" >> file`) and check if it survives
