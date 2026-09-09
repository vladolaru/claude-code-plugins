# AGENTS.md

You maintain **vladolaru-claude-code-plugins**, Vlad Olaru's dual-host (Claude Code and Codex) plugin marketplace. This file holds the repository-wide rules. A plugin with its own `AGENTS.md` holds that plugin's rules; read it before changing anything under that plugin.

## Development Model

This project is AI-written and AI-maintained. The human (Vlad) sets direction, makes architectural decisions, and reviews work. Claude Code agents do the implementation, testing, analysis, and maintenance. "Single maintainer" means a single human decision-maker with AI execution capacity, not limited implementation bandwidth.

No agent carries context between sessions; every agent reads the code cold. So:

- **Prefer single canonical implementations** over duplicated patterns. An agent copies whichever pattern it meets first, and two conventions for one thing guarantee drift.
- **Constants, types, named helpers, and docstrings are the discovery mechanisms.** A fact about one module belongs in that module, where an agent reads it for free when the file is open.
- **Consolidating duplicated logic is drift prevention**, not polish.

## What Belongs in an AGENTS.md

These files are the always-on cost of every session, and the longer they get the less of them an agent follows. Codex reads at most 32 KiB across the root-to-cwd `AGENTS.md` chain and drops the rest; `plugins/pirategoat-tools/tests/test_instruction_budget.py` enforces ceilings under that line. Keep the test green by moving content, not by compressing wording:

| Content | Lives in |
|---|---|
| A rule an agent would otherwise violate, with one clause of why | `AGENTS.md` (root for repo-wide, plugin for plugin-wide) |
| A fact about one module: contract, invariants, importers, history | that module's docstring |
| A procedure, a design record, a CLI manual, a directory layout | `plugins/<plugin>/docs/`, pointed at from `AGENTS.md` with the trigger for reading it |
| Which tests a change should run | the plugin's `tests/TESTING.md` |
| Counts and inventories of commands, skills, agents | `.claude-plugin/marketplace.json` and the plugin README, never restated here |
| An incident or a lesson | `.claude/docs/learnings/` |

A pointer states when to follow it ("read `docs/review-pipeline.md` before changing a step's handoff"), and a pointer is the only summary: never inline the content it points at.

## Repository Layout

```text
.claude-plugin/marketplace.json   # canonical plugin registry (versions, commands, skills, agents)
.agents/plugins/marketplace.json  # GENERATED Codex marketplace
plugins/<name>/
├── .codex-plugin/plugin.json     # GENERATED Codex manifest
├── codex-skills/                 # GENERATED Codex command adapters
├── agents/ commands/ skills/ scripts/ hooks/ docs/ tests/   # as needed
├── AGENTS.md + CLAUDE.md shim    # plugin rules (pirategoat-tools, yoloing-safe)
└── CHANGELOG.md
scripts/generate_codex_compat.py  # deterministic Codex adapter generator
docs/                             # committed references and design patterns
.claude/docs/                     # AI session artifacts (gitignored except learnings/)
```

### Dual-Host Source Policy

Claude Code marketplace entries and command files are canonical; Codex packaging is generated from them. Never hand-edit a file marked `GENERATED FILE - DO NOT EDIT`. After changing a plugin entry or a command:

```bash
python3 scripts/generate_codex_compat.py
python3 scripts/generate_codex_compat.py --check
```

Shared skills, scripts, hooks, and agent definitions are used directly by both hosts. In shared skill prose use `$SKILL_DIR` (the directory containing the current `SKILL.md`); host adapters resolve the path. Do not copy Claude model names into Codex configuration; a subagent's `model:` frontmatter is Claude Code routing metadata (`inherit`, `sonnet`, `opus`, `haiku`), and the Codex adapters omit it.

## Plugins

| Plugin | Purpose | Before changing it, read |
|---|---|---|
| `pirategoat-tools` | Code review orchestration: reviewer agents, the 12-step review pipeline, shared skills, analysis tooling | `plugins/pirategoat-tools/AGENTS.md` |
| `yoloing-safe` | PreToolUse hook that blocks destructive commands in YOLO mode, dual-host | `plugins/yoloing-safe/AGENTS.md` |
| `dex` | Knowledge capture into agent-first docs; enforces a 550-line budget on host instruction files | `plugins/dex/README.md` |
| `prompt-engineer` | Evidence-grounded prompt optimization with human gates | `plugins/prompt-engineer/README.md` |
| `image-optimizer` | Lossless image optimization; needs `imageoptim-cli` or `svgo` | `plugins/image-optimizer/README.md` |
| `caffeinate-claude` | Keeps macOS awake during sessions via hooks | `plugins/caffeinate-claude/README.md` |

## Creating or Extending a Plugin

New plugin: create `plugins/<name>/` with a `CHANGELOG.md` (Keep a Changelog format), register it in `.claude-plugin/marketplace.json` following an existing entry, add a row to the table above and to `README.md`, then run the generator.

New command, skill, or agent in an existing plugin: add it to the plugin's array in `marketplace.json`, update the plugin's `README.md` table, run the generator, and commit the generated output. A skill is a `skills/<name>/SKILL.md` with `name` and `description` frontmatter. pirategoat-tools has its own checklist for reviewer agents.

## Testing

Run from the repository root. `pytest.ini` pins `--import-mode=importlib`, and no `tests/` tree may contain an `__init__.py` (`plugins/pirategoat-tools/tests/test_pytest_layout.py` guards this), which is what lets every plugin's suite run in one session.

```bash
pytest plugins/<name>/tests/ -q        # the plugin you changed; the whole tree takes about two minutes
pytest plugins/ -q 2>&1 | tail -5      # everything
```

For the suites a specific file change should run, and the couplings behind them, read `plugins/pirategoat-tools/tests/TESTING.md` § Which tests to run, or `plugins/yoloing-safe/AGENTS.md` § Which Tests to Run. `TESTING.md` also holds the test-design principles (code-based graders, outcomes not paths, parameterize on the axis of variation) and the offline compliance grader: `python3 plugins/pirategoat-tools/tests/grading/eval_agent_compliance.py --grade-only <run-dir>`.

## Versioning and Releases

**RULE 0: every commit that changes plugin behavior (feature, fix, refactor, performance) updates the plugin's `CHANGELOG.md` and bumps its `version` in `.claude-plugin/marketplace.json`** (`feat` = minor, `fix`/`refactor`/`perf` = patch, `BREAKING CHANGE` = major). If the latest bump is not pushed yet, fold a change of similar impact into that entry instead of bumping again; a higher-impact change upgrades the version. `docs`, `test`, `ci`, `style`, and `chore` commits that leave runtime behavior alone need no bump, and a changelog bullet only when a user would notice.

Changelog entries are for a plugin user deciding whether a change affects them; the why, the evidence, and the mechanism tour belong in the commit body, which git archives.

- One bullet per user-visible behavior, not per commit; a follow-up fix to an unreleased behavior edits its bullet rather than appending a correction.
- One sentence per bullet, two at most; no test counts, no file tours. A bullet that needs more is several behaviors (split it) or commit-body detail (cut it).
- Purely internal changes get no bullet.

Tags are plugin-prefixed: `<plugin-name>/v<semver>` (for example `pirategoat-tools/v1.119.2`). Release: update the changelog, bump the version, run the generator, commit, tag, optionally `gh release create`.

## AI Artifacts

Session artifacts (plans, analysis, research, decisions, learnings) go under `.claude/docs/` in `analysis/`, `decisions/`, `learnings/`, `patterns/`, `plans/`, or `research/`. Never put them under `docs/` at the repo root or under `plugins/*/docs/`; those hold committed documentation that ships with the plugin. After a significant debugging session or a non-obvious discovery, suggest `/dex:grok` to capture it.

## Read When

| Situation | Read |
|---|---|
| Designing a multi-phase analytical workflow where later steps must not see earlier conclusions | `docs/patterns/step-by-step-prompt-injection.md` |
| Building a script-curated, multi-mode LLM pipeline with file-based state | `docs/patterns/curated-context-pipeline.md` |
| Integrating with the OpenAI Codex CLI (prompting, structured output, sandbox, headless review) | `docs/codex-cli-reference.md` |
| Spawning Claude Code as a subprocess (nesting guard, isolation flags, `--json-schema`) | `docs/claude-code-cli-reference.md` |
| Using `CLAUDE_PLUGIN_ROOT`, `CLAUDE_SKILL_DIR`, or `CLAUDE_PLUGIN_DATA` in a plugin | `docs/plugin-env-vars-reference.md` |
| Any Claude Code behavior (subagents, hooks, skills, memory, plugins) | `https://code.claude.com/docs/en/<page>.md`; append `.md` to a docs URL for raw markdown, index at `https://code.claude.com/docs/llms.txt` |

License: MIT.
