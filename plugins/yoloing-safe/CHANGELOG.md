# Changelog

All notable changes to the yoloing-safe plugin will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

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
