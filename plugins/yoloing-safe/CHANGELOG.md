# Changelog

All notable changes to the yoloing-safe plugin will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

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
