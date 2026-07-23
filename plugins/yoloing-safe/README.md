# yoloing-safe

YOLO mode safety net — because shipping fast shouldn't mean losing your home directory.

## The Problem

YOLO mode is great. Claude executes commands without asking, you move fast, life is good. Until it's not.

Real incidents have wiped home directories, deleted production databases, and published private packages to the world. All it takes is one `rm -rf /` or an accidental `npm publish` and "fast" turns into "catastrophic." I could do with less of those moments.

## How It Works

A `PreToolUse` hook evaluates Bash calls, Claude Code Read/Write/Edit calls,
and Codex `apply_patch` targets against safety rules. Four tiers, checked in
order - first match wins:

1. **Allowlist** - safe variants that look dangerous but aren't (`git checkout -b`, `rm -rf /tmp/`, `--force-with-lease`)
2. **Block** - irreversible operations an agent should never do (exit code 2, guidance on stderr)
3. **Ask** - risky but sometimes intentional (Claude Code gets a confirmation prompt; Codex fails closed with actionable guidance)
4. **Allow** - everything else flies through silently

The hook auto-wires on install via `hooks.json`. Codex asks you to review and
trust plugin hooks before running them. Codex `PreToolUse` hooks cannot surface
an interactive `ask` decision, so ask-tier matches become explicit blocks in
Codex. The denial names the matching rule and explains how to opt in through
configuration when the action is intentional.

## Architecture

The runtime entrypoint stays at `scripts/pre-tool-use-safety.py`, but the implementation now lives in `scripts/yoloing_safe/`:

- `config.py` — defaults, config loading, self-protection constants
- `shell.py` — command normalization, tokenization, heredoc stripping
- `paths.py` — path extraction and sensitive target handling
- `registry.py` — declarative detector compilation and custom detector wrapping
- `runtime.py` — allow/block/ask/allow flow
- `rules/` — domain rule modules plus the assembled ordered `RULES` registry

This keeps the hook path stable for both hosts while making rule maintenance less error-prone.

## What Gets Caught

### Blocked (hard stop)

| Category | Examples |
|----------|----------|
| Destructive deletion | `rm -rf /`, `rm -fr /home` (compound commands like `&&`, `;`, pipes/background ops, and newline-separated commands are split and each segment is evaluated) |
| Alternative deletion | `find / -delete`, `xargs rm`, `eval "rm"` |
| Disk formatting | `mkfs.ext4 /dev/sda1`, `dd of=/dev/sda` |
| Network exfiltration | `curl -d @/etc/passwd`, `curl -F`, `curl -T`, `scp` upload, `rsync` upload, piping to `nc`, `curl \| bash`, `wget \| bash` |
| Credential access | `.env`, `.pem`, `id_rsa`, `id_ecdsa`, `.p12`, `.pfx`, `.jks`, `.keystore` via any tool (configurable) |
| Package publishing | `npm publish`, `twine upload`, `gem push` |
| SSH remote destruction | `ssh host "rm -rf /"`, `ssh host rm -rf /` (quoted or unquoted), `ssh host "DROP DATABASE"` |
| GitHub repo deletion | `gh repo delete` |
| Protected paths | `~/.ssh/`, `~/.gnupg/`, `~/.aws/`, `~/.config/gcloud/` (configurable) |
| Bare git push | `git push`, `git push origin` (no explicit branch — use `git push origin HEAD`) |
| Self-protection | Write/Edit/Bash writes or destructive mutations to the hook's own config or plugin files (non-configurable) |

### Asked in Claude Code (blocked in Codex)

| Category | Examples |
|----------|----------|
| Git force push | `git push --force` (not `--force-with-lease` — that's the safe alternative) |
| Git hard reset | `git reset --hard`, `git reset --merge` |
| Git discard changes | `git checkout -- .`, `git restore` (without `--staged` only) |
| Git stash/history | `git stash drop`, `git filter-branch` |
| Git config | `git config --global`, `git config --system` |
| Other git | `git clean -fd`, `git branch -D`, `git remote remove` |
| Permissions | `chmod 777`, setuid, recursive `chown` |
| Brew | `brew install`, `brew uninstall`, `brew upgrade` |
| Docker | `docker system prune`, `docker-compose down -v` |
| Database | `DROP TABLE`, `TRUNCATE`, `DELETE` without `WHERE` |
| Infrastructure | `terraform destroy`, `terraform apply -auto-approve` |
| GitHub CI/CD | `gh secret delete`, `gh workflow disable` |
| Sensitive writes | Shell init files (`~/.bashrc`, `~/.zshrc`), git hooks (`.git/hooks/*`), home-directory `~/.gitconfig`, `~/.npmrc` via Write/Edit or Bash writes like `echo ... > ~/.bashrc` or `echo ... >| ~/.bashrc` (project-level dotfiles are allowed) |
| Shell subshell execution | `bash -c`, `sh -c`, `zsh -c` (interpreter one-liners like `python3 -c` are allowed — see Known Limitations) |
| Interpreter heredocs | `bash << 'EOF'`, `python3 << 'EOF'`, `mysql << 'EOF'` (writer heredocs like `cat >` are not flagged) |

### Allowed (safe variants pass through)

`git checkout -b`, `git restore --staged`, `git clean --dry-run`, `git push --force-with-lease`, `rm -rf /tmp/...`, `chmod +x`, `npm publish --dry-run`, `curl -X POST https://api.example.com/health`.

## Configuration

Works out of the box. For customization, create `~/.claude/yoloing-safe.json`:

```json
{
  "zero_access_paths": [
    "~/.ssh/",
    "~/.gnupg/",
    "~/.aws/"
  ],
  "disable_rules": [
    "brew_commands",
    "docker_destructive"
  ]
}
```

Only keys you include override defaults. Omitted keys keep their built-in values (credential patterns, zero-access paths documented in the tables above).

### Disableable Rules

Most rules can be disabled via `disable_rules` in your config file. The following **cannot** be disabled:

- **Self-protection** — prevents the agent from modifying the hook's own config or plugin files (hardcoded, not a named rule)
- `destructive_deletion`, `network_exfiltration`, `credential_access`, `zero_access_paths` — critical safety rules that are non-disableable to prevent defense-in-depth bypass

**Disableable block tier:** `alternative_deletion`, `disk_formatting`, `package_publishing`, `ssh_remote_destruction`, `github_repo_deletion`, `git_bare_push`

**Disableable ask tier:** `git_force_push`, `git_hard_reset`, `git_discard_changes`, `git_destroy_stash`, `git_history_rewrite`, `git_config_changes`, `git_other_dangerous`, `permission_changes`, `brew_commands`, `docker_destructive`, `database_destructive`, `terraform_destructive`, `github_cicd_ops`, `sensitive_write_target`, `inline_interpreter`, `inline_heredoc`

## Installation

### Claude Code

```bash
/plugin marketplace add vladolaru/claude-code-plugins
/plugin install yoloing-safe@vladolaru-claude-code-plugins
```

### Codex

```bash
codex plugin marketplace add vladolaru/claude-code-plugins
codex plugin add yoloing-safe@vladolaru-claude-code-plugins
```

Review the plugin's hook definition when Codex presents its trust prompt.
Ask-tier actions fail closed in Codex because its `PreToolUse` hook protocol
does not support confirmation prompts. Disable the named rule in
`~/.claude/yoloing-safe.json` only when you intentionally accept that risk.
No dependencies - Python 3 stdlib only.

## Alternatives

If you want a different style of safety layer, there are other projects in this space. One worth evaluating is [destructive_command_guard](https://github.com/Dicklesworthstone/destructive_command_guard), another AI-agent command safety hook with a broader cross-agent focus. `yoloing-safe` is intentionally narrower: a Claude Code and Codex plugin that installs as a `PreToolUse` hook and guards Bash plus supported file operations with allowlist, block, and ask-tier policies.

## Design Decisions

A few deliberate choices worth calling out:

- **Fail-open** - if the hook script errors, the tool call proceeds. Safety hooks should not break the agent on their own bugs.
- **Fail-closed Codex asks** - Codex reports `permissionDecision: "ask"` as an unsupported hook result and continues the tool call. The runtime identifies Codex's documented `turn_id` field and converts ask-tier results into explicit blocks instead.
- **Positive framing** — block/ask messages tell Claude what to do instead, not just what's wrong. This shapes the next attempt, not just blocks the current one.
- **Exit 2 for blocks** — Claude treats this as a system error it can't negotiate with. Stronger than a policy denial for behavior shaping.
- **5-second timeout** — if the script hangs, the tool call proceeds. No blocking the agent forever.
- **Allowlist checked first** — without it, `git checkout -b feature` would false-positive against `git checkout --`, and `rm -rf /tmp/build` would match the destructive deletion pattern. Order matters.
- **Compound command evaluation** — commands with shell separators (`&&`, `;`, `||`, `|`, `&`, newline) are split into segments, each evaluated independently against the allowlist and rules. This prevents safe-prefix attacks (`git checkout -b safe && rm -rf /` or `git checkout -b safe` on one line followed by `rm -rf /` on the next) while correctly allowing compound commands where every segment is safe.
- **Self-protection** — the hook blocks Write/Edit/Bash writes and destructive mutations (`>`, `>|`, `cp`, `mv`, `tee`, `sed -i`, `rm`, `ln -s`, `touch`, `chmod`, `chown`, interpreter writes, etc.) to its own config file and plugin directory, preventing an agent from disabling all rules then running destructive commands. Path checks resolve symlinks (`realpath`) and relative targets (including `cd ... &&` command chains). This check is hardcoded and cannot be disabled via `disable_rules`.
- **Tool-scoped rules** - each rule declares which tools it applies to. Read evaluates 2 rules (credential access, protected paths), Write/Edit evaluate 3, and Bash evaluates the rest plus Bash-side sensitive-write detection for redirects/copies/moves/removals into shell init files, git hooks, and home-directory package config. Codex `apply_patch` source and destination paths are resolved against the tool working directory and adapted to the canonical Write rules. A new rule must include example commands - the e2e generator fails if examples are missing.
- **Command normalization** — strips leading binary paths (`/usr/bin/rm` or `/opt/homebrew/bin/git` → `rm`/`git`) and command wrappers (`sudo`, `env`, `nice`, `nohup`, etc.) so detection works regardless of how the command is invoked.
- **Path expansion** — zero-access paths are checked in both `~/` form and expanded absolute form (`/Users/you/`), so protection works regardless of which form the tool provides.
- **Case-insensitive matching** — credential patterns and zero-access paths match case-insensitively (`.ENV`, `~/.AWS/`), so protection works on case-insensitive filesystems like macOS HFS+/APFS.

## Known Limitations

This hook is a safety net, not a sandbox. It catches common and accidental destructive patterns, but a sufficiently motivated adversary can find ways around it. Understanding these limits helps set the right expectations.

### What it can't catch

**Indirection attacks.** The hook inspects the command string, not what the command does. An agent can:
- Write a destructive script to a file (`cleanup.sh`), then run `bash cleanup.sh` — the Bash command has no dangerous keywords
- Use alternative language interpreters: `python3 -c "import shutil; shutil.rmtree('/')"` — interpreter one-liners are deliberately allowed because agents use them constantly for legitimate purposes (JSON formatting, calculations, version checks). Shell subshells (`bash -c`) are flagged since they're the evasion vector.
- Write malicious content via the Write tool — only the file path is checked, not the content (mitigated for known sensitive paths by the `sensitive_write_target` rule)

**Shell metacharacter evasion.** The hook sees literal strings; the shell interprets them:
- Variable expansion: `CMD=rm; $CMD -rf /`
- Subshell execution: `$(echo rm) -rf /`
- Hex encoding: `$'\x72\x6d' -rf /`
- Brace expansion: `{rm,-rf,/}`

These are fundamentally hard to catch without a shell parser, which is beyond the scope of a fast, stateless hook.

**Unmonitored tools.** The hook monitors `Bash`, Claude Code `Read`/`Write`/`Edit`,
and Codex `apply_patch`. Other tools are not checked:
- MCP server tools (filesystem, browser automation, etc.) can read/write/delete files
- `NotebookEdit` can write executable code cells

MCP tool safety requires its own solution at the MCP permission level.

**Multi-step attacks.** The hook is stateless — each tool call is evaluated independently:
- Copy a credential file to an innocuous location, then read the copy
- Set up a git hook that exfiltrates data on every commit
- Push code to an attacker-controlled remote via `git remote add` + `git push`

**Network exfiltration via interpreters.** While `curl`, `wget`, `nc`, `scp`, and `rsync` are monitored, data can be sent via `python3 -c "requests.post(...)"`, DNS exfiltration, `socat`, `telnet`, or any other network tool not in the detection list.

### What it does catch

The 95% case — the accidental `rm -rf /`, the unintended `npm publish`, the `git push --force` that should have been `--force-with-lease`, the credential file read that should use `.env.example` instead. These are the real incidents that happen in daily YOLO mode usage, and they're all caught reliably.

## License

MIT — see [LICENSE](../../LICENSE).
