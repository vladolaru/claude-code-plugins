# yoloing-safe

YOLO mode safety net — because shipping fast shouldn't mean losing your home directory.

## The Problem

YOLO mode is great. Claude executes commands without asking, you move fast, life is good. Until it's not.

Real incidents have wiped home directories, deleted production databases, and published private packages to the world. All it takes is one `rm -rf /` or an accidental `npm publish` and "fast" turns into "catastrophic." I could do with less of those moments.

## How It Works

A `PreToolUse` hook evaluates every Bash, Read, Write, and Edit tool call against safety rules. Four tiers, checked in order — first match wins:

1. **Allowlist** — safe variants that look dangerous but aren't (`git checkout -b`, `rm -rf /tmp/`, `--force-with-lease`)
2. **Block** — irreversible operations an agent should never do (exit code 2, guidance on stderr)
3. **Ask** — risky but sometimes intentional (user gets a confirmation prompt)
4. **Allow** — everything else flies through silently

The hook auto-wires on install via `hooks.json`. Zero configuration needed — it just works.

## What Gets Caught

### Blocked (hard stop)

| Category | Examples |
|----------|----------|
| Destructive deletion | `rm -rf /`, `rm -fr /home`, chained `rm` via `&&`/`;` |
| Alternative deletion | `find / -delete`, `xargs rm`, `eval "rm"` |
| Disk formatting | `mkfs.ext4 /dev/sda1`, `dd of=/dev/sda` |
| Network exfiltration | `curl -d @/etc/passwd`, piping to `nc`, `wget \| bash` |
| Credential access | `.env`, `.pem`, `id_rsa` via any tool (configurable) |
| Package publishing | `npm publish`, `twine upload`, `gem push` |
| SSH remote destruction | `ssh host "rm -rf /"`, `ssh host "DROP DATABASE"` |
| GitHub repo deletion | `gh repo delete` |
| Protected paths | `~/.ssh/`, `~/.gnupg/` (configurable) |

### Asked (user confirms)

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

### Allowed (safe variants pass through)

`git checkout -b`, `git restore --staged`, `git clean --dry-run`, `git push --force-with-lease`, `rm -rf /tmp/...`, `chmod +x`, `npm publish --dry-run`.

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

Only keys you include override defaults. Omitted keys keep their built-in values. See `config/defaults.json` for the full default configuration including credential patterns.

## Installation

```bash
/plugin marketplace add vladolaru/claude-code-plugins
/plugin install yoloing-safe@vladolaru-claude-code-plugins
```

No dependencies — Python 3 stdlib only.

## Design Decisions

A few deliberate choices worth calling out:

- **Fail-open** — if the hook script errors, the tool call proceeds. Safety hooks should not break the agent on their own bugs.
- **Positive framing** — block/ask messages tell Claude what to do instead, not just what's wrong. This shapes the next attempt, not just blocks the current one.
- **Exit 2 for blocks** — Claude treats this as a system error it can't negotiate with. Stronger than a policy denial for behavior shaping.
- **5-second timeout** — if the script hangs, the tool call proceeds. No blocking the agent forever.
- **Allowlist checked first** — without it, `git checkout -b feature` would false-positive against `git checkout --`, and `rm -rf /tmp/build` would match the destructive deletion pattern. Order matters.

## License

MIT — see [LICENSE](../../LICENSE).
