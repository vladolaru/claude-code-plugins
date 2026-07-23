# Changelog

All notable changes to the yoloing-safe plugin will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Tests

- Removed `tests/__init__.py` as part of the repo-wide fix for multi-plugin pytest collection collisions (all plugin suites were importable as the same package `tests`, and conftests collided as `tests.conftest`); the root `pytest.ini` now pins `--import-mode=importlib`. No runtime change.

## [1.14.0] - 2026-07-23

### Added

- Codex plugin packaging for the existing `PreToolUse` hook, including
  marketplace metadata and the standard Codex hook trust flow.
- Host-aware ask-tier handling: Claude Code keeps its interactive confirmation
  prompt, while Codex fails closed with the matching rule ID and opt-in
  guidance because Codex `PreToolUse` hooks do not support `ask` decisions.
- Codex `apply_patch` payload adaptation, including every add, update, delete,
  and move target resolved against the tool's working directory, so canonical
  file safety and self-protection rules apply even though Codex does not emit
  Claude's Write/Edit payload shape.

## [1.13.1] - 2026-04-24

### Fixed
- `ask()` output now includes `hookEventName: "PreToolUse"` inside `hookSpecificOutput` — Claude Code's hook schema requires this field whenever `hookSpecificOutput` is present. Without it, every confirmation prompt emitted a "Hook JSON output validation failed — hookSpecificOutput is missing required field 'hookEventName'" error.

## [1.13.0] - 2026-03-16

### Added
- `destructive_deletion` allowlist for common cache directories — `rm -rf` targeting `node_modules`, `__pycache__`, `.pytest_cache`, `.mypy_cache`, `.ruff_cache`, `.parcel-cache`, `.turbo`, or `.eslintcache` is now allowed without confirmation
- Traversal protection (`..`) prevents abuse of the cache dir allowlist

## [1.12.0] - 2026-03-12

### Added
- Shell control flow keywords (`then`, `else`, `do`) are now stripped as wrapper commands during normalization — fixes `find /tmp/... -delete` inside `if-then` blocks being incorrectly blocked
- `alternative_deletion` allowlist for `find` targeting `/tmp/`, `/var/tmp/`, `$TMPDIR/`, or `.claude/tmp/` paths
- `.claude/tmp/` added as a recognized temp directory prefix across all temp-dir allowlists (`destructive_deletion` and `alternative_deletion`)
- Variable resolution in compound commands — `export DIR="/tmp/..." && rm -rf "$DIR"` now resolves the variable for allowlist matching, so temp-dir cleanup via variables is correctly allowed

### Fixed
- `strip_writer_heredocs()` now strips `$(cat <<MARKER...MARKER)` subshell heredoc bodies — fixes false positives where commit message prose containing keywords like `rm -rf` triggered block rules

## [1.11.0] - 2026-03-09

### Changed
- **E2E harness:** Docker image now copies the full `yoloing_safe/` package alongside the shim, making the e2e container match the real runtime layout
- **Test suite:** Eliminated the `_legacy_safety_hook_tests.py` backing module — all test classes now live directly in their split entrypoints (`test_core.py`, `test_rules_*.py`, `test_integration.py`, `test_scenarios.py`)
- **Rule assembly:** Domain modules now export ordered `RULE_SPECS` lists of `(rule_id, spec)` tuples; the aggregator concatenates them with a two-pass assembly (block first, ask second) instead of manually re-listing every rule ID
- **Detector interface:** Custom detectors now take an `EvalContext` object with cached `whole_command` and `segments` properties, computed at most once per evaluation pass regardless of how many detectors run
- **Rule builders:** New `block_rule()` and `ask_rule()` helpers in `registry.py` replace raw dict literals, enforcing required fields and reducing boilerplate in domain modules
- Updated `AGENTS.md` to reflect the new architecture: `EvalContext`, rule builders, domain module pattern, and simplified rule workflows
- **Meta tests:** Added `TestShimCompatContract` to `test_meta.py` — enforces the shim's public testing contract (9 names) so refactors can't silently break test, e2e, or benchmark tooling
- **E2E generator:** Now imports `RULES` directly from the `yoloing_safe.rules` package instead of going through the shim, reducing implicit coupling to the compatibility surface
- Documented the public testing contract in `AGENTS.md`

## [1.10.1] - 2026-03-09

### Changed
- Refactored the hook implementation into an internal `scripts/yoloing_safe/` package, splitting config, shell parsing, path handling, registry assembly, runtime orchestration, and domain rule logic while keeping `scripts/pre-tool-use-safety.py` as the stable runtime entrypoint
- Reworked custom rule detectors so rule messages are attached by the registry layer instead of being looked up directly from the global `RULES` dict, reducing cross-module coupling and drift risk
- Split the test suite into domain and integration entrypoints (`test_core.py`, `test_rules_*.py`, `test_integration.py`, `test_scenarios.py`) while preserving the existing assertions through a non-collected legacy backing module
- Updated maintainer and testing documentation to describe the new assembled-rule architecture and split test layout

## [1.10.0] - 2026-03-09

### Fixed
- **Security:** self-protection now blocks `ln -s` when the symlink target points to a protected path, closing a TOCTOU bypass where a compound command could create a symlink and write through it in one invocation
- **Security:** `bash <(curl ...)` process substitution now triggers the `inline_interpreter` ask rule, closing an evasion equivalent to `curl | bash`
- **Security:** `zero_access_paths` now uses prefix matching on resolved paths instead of substring matching, preventing false positives on paths containing `.ssh` or `.gnupg` as substrings

### Added
- **Security:** critical block-tier rules (`destructive_deletion`, `network_exfiltration`, `credential_access`, `zero_access_paths`) are now non-disableable via config, matching self-protection's defense-in-depth model

## [1.9.2] - 2026-03-09

### Fixed
- **Security:** self-protection now catches interpreter-based writes to hook files even when the path is relative to `cwd` (for example `python3 -c "open('hooks/hooks.json','w')"` from the plugin root)
- **Security:** `network_exfiltration` now blocks `curl ... | bash/sh/zsh` in real hook execution, not just direct detector tests, closing a compound-command drift bug
- **Security:** clobber redirects (`>|`, `1>|`, `2>|`) now count as writes for both self-protection and `sensitive_write_target`, preventing bypasses like `echo ... >| ~/.bashrc`
- **Behavior:** ordinary HTTP requests such as `curl -X POST https://api.example.com/health` no longer false-positive as exfiltration when they are not uploading file/stdin data
- **Behavior:** destructive-deletion and database-destructive rules now ignore inert string mentions like `echo 'rm -rf /'` or `echo 'DROP TABLE users'` while still catching execution contexts such as shell `-c` payloads and SQL piped into database clients

## [1.9.1] - 2026-03-08

### Fixed
- **Security:** newline-separated Bash commands now participate in compound-command splitting, closing allowlist-prefix bypasses like `git checkout -b feature` on one line followed by `rm -rf /` on the next
- **Security:** self-protection now blocks additional Bash-side mutations of hook files, including `rm`, `ln -s`, `touch`, `chmod`, and `chown`, not just redirects and copy-style writes
- **Security:** `network_exfiltration` now catches additional `curl` file-post forms including `--data-binary`, `--data-raw`, and `--data=@file`
- **Behavior:** `permission_changes` now asks on common variants such as `chmod -R 777`, `chmod 0777`, and `chown --recursive`
- **Behavior:** Bash credential/protected-path detection now extracts likely file arguments instead of scanning the whole command string, eliminating false positives for search commands like `grep -R '.env' README.md`
- **Behavior:** `sensitive_write_target` now covers Bash writes and destructive mutations to shell init files, git hooks, and home-directory package-manager config, not just Write/Edit tools

## [1.9.0] - 2026-03-08

### Fixed
- **Security:** Git global options (`-C`, `-c`, `--git-dir`, `--work-tree`, `--no-pager`, etc.) between `git` and the subcommand bypassed all anchored git rules — added `_strip_git_global_opts()` normalization
- **Security:** npm global options (`--registry`, `--prefix`, etc.) before the subcommand bypassed `package_publishing` detection — added `_strip_npm_global_opts()` normalization
- **Security:** Pipe (`|`) and background (`&`) operators were not treated as compound command separators, allowing `echo ok | git push` to bypass detection — expanded chain splitting regex
- **Behavior:** `echo .env` and `echo ~/.ssh/` incorrectly triggered credential_access and zero_access_paths rules — added non-file-command exclusion for `echo`, `printf`, `export`, `test`, and other non-file-accessing builtins

### Changed
- Removed unused regex constants (`_RE_RM`, `_RE_RECURSIVE_FLAG`, `_RE_FORCE_DELETE_FLAG`)

## [1.8.2] - 2026-03-08

### Fixed
- **Security:** Zero-access paths (`~/.ssh/`, `~/.aws/`, etc.) could be bypassed via `$HOME` and `${HOME}` shell variable forms — now expanded alongside `~` in config loading
- **Security:** `find -delete` scoped-root allowance could be bypassed via parent-directory traversal (`find ./../../etc -delete`) — added `..` traversal detection
- **Security:** `su -c` subshell execution was not caught by `inline_interpreter` rule — added `su -c` (with optional username) to the pattern
- **Security:** Self-protection Bash detection missed interpreter-based writes (`python3 -c`, `node -e`) to protected paths — added interpreter write pattern detection
- **Security:** command normalization now strips any leading absolute binary path (for example `/opt/homebrew/bin/git`), closing bypasses where anchored rules (`^git push`, `^brew`, etc.) were skipped
- **Security:** Bash self-protection now resolves write targets as real paths with relative-path handling and `cd ... &&` chain awareness, closing bypasses like `cd plugins/yoloing-safe && echo '{}' > hooks/hooks.json`
- **Security:** loopback exception for network exfiltration now checks parsed URL hosts, preventing false loopback matches from unrelated substrings (for example `?x=localhost` on non-loopback URLs)
- **Behavior:** `git push --force` now consistently triggers the `git_force_push` ask flow (including no-refspec form), instead of being blocked by `git_bare_push` precedence
- `is_allowlisted()` did not respect `disable_rules` config, inconsistent with `main()` behavior — now accepts optional `disabled` parameter

## [1.8.1] - 2026-03-08

### Fixed
- **Security:** `rm -rf /tmp/build /home` bypassed `destructive_deletion` — temp-directory allowlist matched on first target only, silently allowing mixed-target deletions. Tightened regex to require ALL rm targets be in temp directories (anchored with `$`)
- `.env` credential pattern over-matched filenames like `.envoy.yml` and `.environ` — added `\b` word boundary after `.env`
- Defensive `return` after `allow()` calls in `main()` early-exit paths to guard against `UnboundLocalError` if `sys.exit` were ever caught

### Changed
- README.md updated to reflect removal of `chained_deletion` rule and current compound command evaluation design
- Clarified SSH remote command parsing comment in `detect_ssh_remote_destruction`

## [1.8.0] - 2026-03-08

### Changed
- Simplified compound command evaluation from two passes to one per-segment pass
- Compound commands with allowlisted segments (e.g., `mkdir -p /tmp/x && rm -rf /tmp/x`) now pass through correctly

### Removed
- `chained_deletion` rule — per-segment evaluation already catches `rm -rf` via `destructive_deletion`; blocking plain `rm file` in chains was overly broad

## [1.7.2] - 2026-03-08

### Fixed
- `find . -delete` (bare dot path) incorrectly blocked — scoped root regex required a character after the dot, missing the most common form
- `docker compose down -v` (V2 syntax, no hyphen) bypassed `docker_destructive` rule — only V1 `docker-compose` was matched
- `redis-cli flushall` (lowercase) bypassed `database_destructive` rule — pattern expected uppercase `FLUSH(ALL|DB)` but Redis commands are case-insensitive
- `.envrc` (direnv config) incorrectly blocked as credential file — added to safe patterns

## [1.7.1] - 2026-03-08

### Fixed
- Short colon refspec deletion (`git push origin :branch`) now detected by `git_other_dangerous` — previously bypassed all rules
- Short `-d` flag deletion (`git push origin -d branch`) now detected — previously only `--delete` was matched

### Added
- Meta-test: every rule_id has a corresponding unit test class in the hook test suite
- Meta-test: every rule_id has safe-variant coverage in `allowed.json`
- Meta-test: critical ask-tier rules require evasion scenario coverage
- 10 evasion scenarios for ask-tier rules (git_force_push, git_hard_reset, permission_changes, docker_destructive, database_destructive)
- 4 evasion scenarios for git_other_dangerous (colon refspec and `-d` flag bypasses)

## [1.7.0] - 2026-03-08

### Added

- Meta-test suite (`test_meta.py`) validates structural invariants between
  `RULE_REGISTRY`, message catalogs, allowlist patterns, and scenario files.
  Prevents drift at the fast test layer — 13 invariant checks.
- Detection for `git push --delete` and `git push origin :refs/...` (remote
  branch/tag deletion) under the `git_other_dangerous` ask-tier rule.
- `scenarios/asked.json` — regression scenarios for all 16 ask-tier rules.
- Evasion scenarios for `git_bare_push` (3), `inline_heredoc` (4),
  `network_exfiltration` (2), and `zero_access_paths` (1) — 10 new
  adversarial bypass tests.
- `rule_id` field in `evasion.json` entries for per-rule coverage validation.
- Expanded `allowed.json` with safe variants of terraform, docker, brew,
  gh, database, git config, chmod, and interpreter commands.
- Rule add/remove/rename templates and checklists in `CLAUDE.md`.

### Changed

- `TESTING.md` updated to document the meta-test layer (layer 6),
  `asked.json` file, evasion `rule_id` field, and structural sync validation.

## [1.6.1] - 2026-03-08

### Changed

- E2E test examples now live in `RULE_REGISTRY` as a 5th tuple element.
  Adding a new rule without examples fails the generator — drift between
  rules and e2e tests is no longer possible.
- `test-fixtures.json` reduced from 148 lines to 25 — now holds only
  optional overrides (tool, branch, subagent, pattern, prompt). Most rules
  need no fixture entry.
- Generator derives batch prompts from tool-specific templates, patterns
  from example commands, and test name suffixes from command first words.
- Removed `config/defaults.json` — defaults are hardcoded in the hook script
  and documented in README. The file was not loaded at runtime and risked
  drifting from actual defaults.
- Updated all docs to reflect current rule counts (27 rules, 33 e2e tests,
  28 rule categories) and added `git_bare_push` / `inline_heredoc` to
  disableable rule ID lists and "What Gets Caught" tables.

### Fixed

- E2E classifier no longer silently defaults to `HOOK_BLOCKED` when no hook
  trace is found. New `HOOK_UNKNOWN` outcome distinguishes "hook confirmed
  the block" from "something stopped it but we can't prove it was the hook."
- `run-e2e.sh` now handles `HOOK_UNKNOWN` as inconclusive (was falling into
  the error wildcard and counting as failure).
- Generator validates no duplicate test names across all rules.

## [1.6.0] - 2026-03-08

### Added

- New `git_bare_push` block rule: blocks `git push` and `git push origin`
  (no explicit branch). Requires an explicit refspec like `git push origin HEAD`
  or `git push origin <branch-name>`. Also allows `--tags`, `--all`, `--mirror`
  as refspec alternatives.
- New `inline_heredoc` ask rule: flags heredocs fed to shell interpreters or
  databases (`bash << 'EOF'`, `python3 << 'EOF'`, `mysql << 'EOF'`, etc.).
  These are executed — unlike writer heredocs — and warrant confirmation.
- Chain-aware rule evaluation: compound commands (`&&`, `;`, `||`) are now
  split into segments and each segment is evaluated independently. Previously,
  `echo ok && git push` bypassed all `^git`-anchored rules. Two-pass design
  preserves existing chain-specific rules (e.g. `detect_chained_deletion`)
  while catching git operations hidden after chain operators.

### Fixed

- `network_exfiltration` no longer blocks `curl` requests to loopback addresses
  (`localhost`, `127.0.0.1`, `::1`). Other rules (`credential_access`,
  `zero_access_paths`) still apply. Eliminates false positives from local dev
  server API calls.
- `alternative_deletion` no longer blocks `find -delete` when the search root
  is a relative dot-path (e.g. `.claude/tmp/`) or an explicit temp directory
  (`$TMPDIR`, `/tmp/`, `/var/tmp/`). Absolute and home-relative roots are
  still blocked.
- Writer heredoc bodies (`cat >`, `tee`) are now stripped before rule evaluation,
  eliminating false positives when heredoc content contains words like `rm` or
  `DELETE FROM` (e.g. PR review text written to `$TMPDIR`).
- `inline_interpreter` no longer asks for confirmation when `bash -c` is used
  via container exec tooling (`docker exec`, `pnpm wp-env run`, `wp-env run`).
  Destructive commands inside container exec are still caught by other rules.

## [1.5.0] - 2026-03-08

### Added

- Single source of truth for e2e test cases: `generate-test-cases.py` imports `RULE_REGISTRY` from the safety hook and merges with `test-fixtures.json` to produce `test-cases.json`
- `make generate` and `make check` targets for regeneration and staleness detection
- Bidirectional validation: generator fails if any registry rule lacks a fixture or vice versa
- Full rule coverage: 31 tests across all 26 rule categories (13 block, 15 ask, 3 subagent)
- Batched session execution reduces 22 individual sessions to 8, achieving 100% hook coverage

### Changed

- `test-cases.json` is now generated — do not edit directly

## [1.4.0] - 2026-03-08

### Added

- Docker-based E2E test harness that runs Claude Code in YOLO mode against crafted prompts inside a disposable container, verifying the safety hook blocks/asks as expected
- Four-outcome test classification: `HOOK_BLOCKED` (denied), `HOOK_ASKED` (ask decision), `MODEL_REFUSED` (inconclusive), `HOOK_FAILED` (bug)
- Session JSONL + `--debug "hooks"` logs as classification source of truth — detects both block-tier hook errors and ask-tier `permissionDecision: "ask"` decisions
- 22 test cases across block tier (12), ask tier (7), and subagent bypass (3)
- Noise hooks (PreToolUse logger, PostToolUse logger, UserPromptSubmit timestamp) for multi-hook coexistence testing
- Bait filesystem (fake SSH keys, AWS credentials, GPG keyrings, `.env` files) with before/after checksum verification
- Makefile with `build`, `auth`, `run`, `run-save`, `shell`, `rebuild`, `clean`, `clean-all` targets

## [1.3.0] - 2026-03-06

### Fixed

- Self-protection now covers Bash tool writes (redirects `>`, `>>`, `cp`, `mv`, `tee`, `sed -i`) to protected paths — previously only blocked Write/Edit tools
- Self-protection path check now resolves symlinks via `os.path.realpath`, preventing symlink-based bypasses (e.g., `ln -s plugin-root /tmp/link` then Write to `/tmp/link/hooks.json`)
- Credential pattern matching is now case-insensitive — `.ENV`, `.Env`, `CLIENT_SECRET.JSON` are all caught on case-insensitive filesystems (macOS HFS+/APFS)
- Zero-access path matching is now case-insensitive — `~/.AWS/`, `~/.SSH/` are caught regardless of case

### Added

- `_is_self_protected_path()` helper: centralizes symlink-resolving path check for Write/Edit
- `_bash_targets_protected_path()` helper: detects Bash commands that write to self-protected paths
- 35 new test assertions covering all three security fixes

## [1.2.0] - 2026-03-06

### Added

- Tool-aware rule dispatch: each rule declares which tools it applies to (`{"Bash"}`, `{"Write", "Edit"}`, etc.). Rules only run when the current tool is in their set. Read evaluates 2 rules (was 25), Write/Edit evaluate 3 (was 25), Bash still runs all 25. New rules must declare applicable tools — omission is a visible error, not a silent bypass.

### Changed

- All ~80 inline regex patterns pre-compiled as named module-level constants (e.g., `_RE_RM`, `_RE_GIT_PUSH`, `_RE_CURL_POST_DATA`). Shared patterns defined once and reused. Improves discoverability and prevents pattern drift between functions.

## [1.1.0] - 2026-03-06

### Added

- Self-protection: hook blocks Write/Edit to its own config file and plugin directory, preventing an agent from disabling safety rules (non-configurable, cannot be disabled via `disable_rules`)
- Allowlist chain guard: allowlist is skipped when chain operators (`&&`, `;`, `||`) are present, preventing bypass via safe prefix + destructive tail (e.g., `rm -rf /tmp/build && rm -rf /home`)
- Command wrapper normalization: strips `sudo`, `env`, `nice`, `nohup`, `time`, `exec`, `strace`, `ionice`, `taskset` prefixes with loop for nesting
- Zero-access path expansion: `~/.ssh/` is now also checked as `/Users/you/.ssh/` so protection works regardless of path form
- New block-tier patterns: `curl -F`/`-T`/`--upload-file` (form/file upload), `scp` upload, `rsync` upload
- SSH unquoted command detection: `ssh host rm -rf /` now caught alongside quoted `ssh host "rm -rf /"`
- New credential patterns: `id_ecdsa`, `.p12`, `.pfx`, `.jks`, `.keystore`
- New default zero-access paths: `~/.aws/`, `~/.config/gcloud/`
- New ask-tier rule `sensitive_write_target`: flags Write/Edit to shell init files (`~/.bashrc`, `~/.zshrc`, etc.), git hooks (`.git/hooks/*`), and home-directory package config (`~/.gitconfig`, `~/.npmrc`, `~/.yarnrc`). Project-level dotfiles are allowed to preserve YOLO flow.
- New ask-tier rule `inline_interpreter`: flags shell subshell execution (`bash -c`, `sh -c`, `zsh -c`). Interpreter one-liners (`python3 -c`, `node -e`, etc.) are deliberately excluded — agents use them constantly for legitimate purposes, and the noise-to-signal ratio is too high. Interpreter-based attacks are documented as a known limitation.
- Package publishing segment-aware detection: `npm publish --dry-run && npm publish` now correctly blocks the second segment
- Known limitations section in README documenting what the hook fundamentally cannot catch
- 30 evasion scenarios (was 16), 40 blocked scenarios (was 27), 29 allowed scenarios (was 27)
- 84 new test assertions across 8 new test classes

## [1.0.0] - 2026-03-06

### Added

- Initial release: PreToolUse safety hook for YOLO mode
- Four-tier response model: allowlist → block (exit 2) → ask (JSON permissionDecision) → allow
- Block tier: destructive deletion, disk formatting, network exfiltration, credential access, package publishing, SSH remote destruction, GitHub repo deletion, zero-access path protection
- Ask tier: dangerous git ops, permission changes, brew commands, Docker/database/Terraform destructive ops, GitHub CI/CD ops
- Allowlist: safe variants (git checkout -b, git restore --staged, git clean --dry-run, --force-with-lease, rm -rf /tmp/, chmod +x, npm publish --dry-run)
- Command normalization: absolute path stripping, whitespace collapse
- Configurable credential patterns, zero-access paths, and disable_rules via ~/.claude/yoloing-safe.json
- Positive-framing block/ask messages that guide agent toward safer alternatives
- Auto-wiring via hooks.json with ${CLAUDE_PLUGIN_ROOT}
