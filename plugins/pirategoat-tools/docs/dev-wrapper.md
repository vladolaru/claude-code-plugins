# Running the unreleased plugin against a real repository

Read this before exercising plugin changes end to end, and before trusting a review that a dev session produced.

Start Claude Code through the wrapper at the repository root:

```bash
scripts/claude-pirategoat-tools-dev                          # interactive
scripts/claude-pirategoat-tools-dev -p "review this branch"  # headless; args pass through
```

Symlink it onto your `PATH` if you want it everywhere; it resolves the worktree from its own location, following symlinks.

## What it does

Two flags that always travel together:

```bash
claude --plugin-dir <worktree>/plugins/pirategoat-tools \
       --settings '{"enabledPlugins":{"pirategoat-tools@<marketplace>":false}}'
```

`--plugin-dir` loads the worktree in place, so edits apply to the next session with no sync step. The `--settings` override is not optional: `--plugin-dir` alone loads the worktree alongside the installed release, and both register the same commands and agents. The release's plugin id is derived from `.claude-plugin/marketplace.json`, so renaming the marketplace cannot leave the wrapper disabling a plugin that no longer exists.

Nothing is installed, cached, or written to disk. `claude plugin list` reports the worktree copy as `pirategoat-tools@inline` with `Status: loaded`; the release keeps its own entry, disabled only inside that process. A plain `claude` is always the released version, so forgetting the wrapper puts you on the safe version.

The wrapper passes `--dangerously-skip-permissions`, because these sessions exist to exercise the pipeline end to end. It is scoped to the wrapper rather than aliased onto `claude`, so ordinary sessions keep their prompts. The remaining backstop is the `yoloing-safe` PreToolUse hook; check it is enabled with `claude plugin list | grep -A3 yoloing-safe`.

## Which version and which build ran

`plugin_version` in the run manifest records the worktree version (`_detect_plugin_version()` falls back to the CHANGELOG's top version when the plugin root's directory name is not a semver), even though `claude plugin list` shows `Version: unknown` for an inline load:

```bash
python3 scripts/analysis/review_run_metrics.py --last 1 --format json | grep plugin_version
```

`plugin_version` only moves at a release, so every dev-mount commit between two releases stamps the same number. `_detect_plugin_commit()` records the checkout's short HEAD as `plugin_commit` in `run-config.json`, the one artifact that could not otherwise answer the question. It resolves for ordinary installs too (a marketplace install is a clone) and is `null` only where there is no repository to ask:

```bash
python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["plugin_commit"])' <run dir>/run-config.json
```

## Caveats

- The mount is the live working tree, uncommitted edits included. Check `git status` before starting a session you intend to trust.
- Edits made during a session do not affect the already-loaded plugin; restart the wrapper.
- `/tmp/.pirategoat-tools-root` is repopulated from `$CLAUDE_PLUGIN_ROOT` by the PreToolUse hook, which under the wrapper is the worktree, so the fallback cache self-corrects.
