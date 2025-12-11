# Changelog

All notable changes to the pirategoat-tools plugin will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [1.1.0] - 2025-12-11

### Changed

- Extracted `image-optimizer` skill into standalone plugin

## [1.0.0] - 2025-12-09

### Added

- Initial release of pirategoat-tools plugin
- **Skills:**
  - `image-optimizer` - Lossless image optimization using imageoptim-cli and svgo
  - `prompt-optimizer` - Two-phase prompt optimization with pattern attribution
  - `wordpress-backend-dev` - WordPress backend development guidance (WPCS, security, i18n, hooks)
- **Commands:**
  - `/fix-github-issue` - Analyze and fix GitHub issues end-to-end
  - `/execute-plan` - Project manager mode for executing implementation plans
  - `/optimize-prompt` - Quick access to prompt optimization
- **Agents:**
  - `architect` - Lead architect for code analysis and solution design
  - `developer` - Implementation specialist with test focus
  - `debugger` - Systematic bug analysis through evidence gathering
  - `quality-reviewer` - Code review for real issues (security, performance)
  - `technical-writer` - Documentation creation after feature completion
  - `adr-writer` - Architecture Decision Record creation
