# Changelog

## [1.0.0] - 2026-03-08

### Added
- Initial release
- `prevent-sleep.sh` hook on `UserPromptSubmit` — registers session and starts `caffeinate -i -t 3600`
- `allow-sleep.sh` hook on `Stop` — deregisters session and kills caffeinate when no sessions remain
- Per-session marker files using PPID for multi-tab support without shared state conflicts
- Stale session cleanup — both scripts prune markers for dead processes
- 1-hour caffeinate timeout as safety net if Claude Code crashes without triggering Stop
