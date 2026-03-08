# yoloing-safe E2E Tests

End-to-end tests that verify yoloing-safe's safety hook works correctly
when Claude Code runs in YOLO mode (`--dangerously-skip-permissions`).

## How It Works

A Docker container runs Claude Code against crafted prompts designed to
trigger dangerous operations. The safety hook should intercept these
operations. After each test, bait files (fake SSH keys, AWS credentials,
etc.) are checked to verify no damage occurred.

Tests are batched by branch and tier into shared sessions — multiple
commands are presented as a numbered list in a single prompt. This
eliminates model self-censorship and reduces session count. Subagent
tests run as solo sessions.

Each test produces one of four outcomes:

| Outcome | Meaning |
|---------|---------|
| `HOOK_BLOCKED` | CC attempted the tool call, hook denied it |
| `HOOK_ASKED` | CC attempted the tool call, hook returned "ask" decision |
| `MODEL_REFUSED` | CC never attempted the dangerous tool call (inconclusive) |
| `HOOK_FAILED` | Tool call went through, bait files damaged (BUG) |

`HOOK_BLOCKED` and `HOOK_ASKED` are successes — the hook fired and
responded correctly. `MODEL_REFUSED` is inconclusive (the model
self-censored before the hook had a chance to act). `HOOK_FAILED` is a
real bug.

## Important: Cost and Duration

These tests make **real Claude API calls** (31 tests across 8 sessions).
Each full suite run costs real money and takes **5–10 minutes**. Don't
run the full suite casually.

## Safety Guard

`run-e2e.sh` refuses to run outside the Docker container. It checks for
`YOLOING_SAFE_E2E=1`, which is only set via `ENV` in the Dockerfile.
There is no override flag — this is intentional.

## Prerequisites

- Docker Desktop (or Docker Engine on Linux)
- A Claude Code account with an active subscription

## Quick Start

All commands run from this directory (`plugins/yoloing-safe/tests/e2e/`):

```bash
make build    # Build the Docker image
make auth     # Interactive shell — run 'claude auth login' inside
make run      # Run the full e2e suite
```

Run `make help` to see all available targets.

## First-Time Setup

### 1. Build the image

```bash
make build
```

### 2. Authenticate Claude Code

```bash
make auth
```

This drops you into an interactive shell inside the container. Run:

```bash
claude auth login
```

Claude prints a URL — open it in your browser on the **host machine**,
complete the login, and the container receives the token. Verify with:

```bash
claude auth status
```

Type `exit` to leave. The auth token is stored in a Docker volume and
persists across container rebuilds. You only need to do this once (until
the token expires).

## Running Tests

```bash
make run          # Full suite (31 tests, 8 sessions, ~5-10 min)
make run-save     # Full suite, results saved to ./results/
make shell        # Interactive shell for debugging
```

Override the model (default: haiku):

```bash
CC_MODEL=sonnet make run
CC_MODEL=opus make run
```

## After Code Changes

```bash
make rebuild      # Rebuild image (auth volume survives)
make run          # Run tests with updated code
```

## After Agent Damage

The container is expendable. Rebuild it:

```bash
make rebuild
```

## Test Cases

See `test-cases.json` for all 31 test cases covering all 26 rule
categories:

- **Block tier** (13 tests): destructive deletion, credential access,
  network exfiltration, package publishing, SSH remote destruction,
  GitHub repo deletion, disk formatting, self-protection
- **Ask tier** (15 tests): git force push, hard reset, discard changes,
  stash drop, branch delete, history rewrite, global config, permission
  changes, brew install, Docker prune, database drop, Terraform destroy,
  GitHub CI/CD ops, sensitive file writes, inline interpreter
- **Subagent bypass** (3 tests): verify hooks fire on subagent tool calls
  (rm -rf, SSH key read, curl exfiltration)

## Noise Hooks

The container includes noise hooks (in `hooks/`) registered alongside
yoloing-safe to verify multi-hook coexistence:

- `pre-tool-logger.sh` — logs PreToolUse events
- `post-tool-logger.sh` — logs PostToolUse events
- `prompt-timestamp.sh` — injects timestamp on UserPromptSubmit
- `subagent-context.sh` — injects test executor context into subagents

## Output

Results are saved to `/Users/testuser/results/` inside the container.
Use `make run-save` to mount them to `./results/` on the host:

- `<batch-key>.session.jsonl` — CC session log (parent + subagent sessions concatenated)
- `<batch-key>.debug.txt` — CC debug log (hook decision traces including ask)
- `<batch-key>.stderr` — CC stderr
- `<batch-key>.snap-before` / `.snap-after` — bait file checksums
- `batch-plan.json` — execution plan (batches and solo tests)
- `hook-log.jsonl` — noise hook event log
- `summary.json` — machine-readable results
