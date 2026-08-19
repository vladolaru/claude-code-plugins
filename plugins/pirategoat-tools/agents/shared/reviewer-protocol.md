# Shared Reviewer Protocol

Standard protocol for all review agents. Read this FIRST before starting your review.

## Step 0: Locate Plugin Root

**Preferred: Use the bootstrap script** which handles all setup (plugin root, protocol, scope discovery) in a single command:

```bash
PLUGIN_ROOT=$(cat /tmp/.pirategoat-tools-root 2>/dev/null)
[ -z "$PLUGIN_ROOT" ] || [ ! -d "$PLUGIN_ROOT/scripts" ] && PLUGIN_ROOT=$(find ~/.claude -path "*/pirategoat-tools/*/scripts/review/agent/bootstrap.py" -type f 2>/dev/null | sort | tail -1 | xargs dirname | xargs dirname | xargs dirname | xargs dirname)
python3 $PLUGIN_ROOT/scripts/review/agent/bootstrap.py --agent <agent-name>
```

If the bootstrap script is not available, locate the plugin root manually:

```bash
PLUGIN_ROOT=$(cat /tmp/.pirategoat-tools-root 2>/dev/null)
# Fallback if hook hasn't run yet
[ -z "$PLUGIN_ROOT" ] || [ ! -d "$PLUGIN_ROOT/scripts" ] && PLUGIN_ROOT=$(find ~/.claude -path "*/pirategoat-tools/*/scripts/review/agent/scope.py" -type f 2>/dev/null | sort | tail -1 | xargs dirname | xargs dirname | xargs dirname | xargs dirname)
echo "PLUGIN_ROOT=$PLUGIN_ROOT"
```

If this fails, fall back to the manual scope discovery at the end of this section.

Store `PLUGIN_ROOT` — you'll use it for:
- `python3 $PLUGIN_ROOT/scripts/review/agent/scope.py` — scope discovery
- Reading reference files like `$PLUGIN_ROOT/agents/shared/*.md`, `$PLUGIN_ROOT/skills/*/references/*.md`

## Scope Discovery (Do This FIRST)

Use `scope.py` to efficiently determine your review scope. It handles range detection, noise filtering, domain filtering, context budgeting, and output directory detection in a single call.

```bash
# Your Scope section specifies which --domain to use
python3 $PLUGIN_ROOT/scripts/review/agent/scope.py --domain <your-domain>

# With explicit range (when provided by caller)
python3 $PLUGIN_ROOT/scripts/review/agent/scope.py --domain <your-domain> --range "main..feature-branch"

# For large PRs: get diffstat overview, then selectively read diffs
python3 $PLUGIN_ROOT/scripts/review/agent/scope.py --domain <your-domain> --summary

# For agents exploring preexisting code (patterns-reviewer, history-insights-reviewer)
python3 $PLUGIN_ROOT/scripts/review/agent/scope.py --domain <your-domain> --base-ref-only
```

### Reading the Output

The script outputs structured text. Parse these key fields from the header:

| Field | Use |
|-------|-----|
| `STATUS` | `OK`, `NO_DOMAIN_FILES`, or `ERROR` |
| `RANGE` | The git range used (for manual diff reads if needed) |
| `BASE_REF` | Base branch ref (for exploring preexisting code) |
| `OUTPUT_DIR` | Where to write review output files |
| `PR_NUMBER` | PR number (if detected) |
| `BUDGET_EXCEEDED` | Files listed but not diffed due to context budget |

**On `STATUS: ERROR`:** Report the error to the caller. Do NOT proceed with review.

**On `STATUS: NO_DOMAIN_FILES`:** Call `builder.mark_not_applicable("No [domain] files in diff")`, save output, and exit. Do NOT perform any further analysis.

**On `STATUS: OK`:** The `=== DIFFS ===` section contains filtered diffs for matched files within the context budget. Files are sorted by budget priority (production code before tests for mixed domains), largest-first within each tier. One oversized leading file may be admitted in full as a protected exception; the remaining files share the normal budget.

**On `BUDGET_EXCEEDED` / `=== NOT DIFFED ===`:** These files matched your domain but their diffs were NOT given to you. Your verdict does not cover them by default, and an APPROVE that silently ignores them is a protocol violation. Before writing output, handle every NOT DIFFED file in one of two ways:

1. **Review it:** `git diff <RANGE> -- <file>` (prioritize production code over tests, largest diffstat first), or
2. **Declare it:** list it under a `**Not reviewed (budget):**` line in your Markdown summary so the reconciliation step can account for the gap. Never count a declared-unreviewed file toward your verdict.

### When You Need More Context

Use the Read tool with offset+limit for surrounding context around a finding.

### Tool Selection for Search

Use the **Grep tool** for working-tree searches (supports glob filtering, context lines, multiple output modes). Use Bash `grep`/`rg` only when piping from another command or using `git grep` at a specific ref.

### If the Script Is Not Available

Fall back to manual commands:
```bash
_REF=$(git symbolic-ref refs/remotes/origin/HEAD 2>/dev/null)
DEFAULT_BRANCH="${_REF#refs/remotes/origin/}"
[ -z "$DEFAULT_BRANCH" ] && DEFAULT_BRANCH="main"
RANGE="${DEFAULT_BRANCH}..HEAD"
git diff --name-only $RANGE | grep -v -E '\.(lock|png|jpg|jpeg|gif|svg|ico|woff|woff2|ttf|eot|map)$' \
  | grep -v -E '(^|/)(vendor|node_modules)/' \
  | grep -v -E '\.min\.(js|css)$' \
  | grep -v -E '(^dist/|^build/|^\.idea/|^\.vscode/|\.DS_Store$)'
# Then apply your domain filter (see Scope section)
# Then: git diff $RANGE -- <file> for each matched file
```

## Quick Relevance Check (BEFORE Deep Review)

Scan the diff hunks (changed lines, not just file names): **does anything relate to your domain?**

**If nothing is relevant**, mark not-applicable and exit:

```python
builder.mark_not_applicable("No changes relevant to [your domain] — diff contains only [brief description]")
builder.add_positive("Diff scanned — no changes relevant to [your domain]")
builder.set_files_reviewed(N)  # count of files you scanned
result = builder.save(OUTPUT_DIR)
# Then return STATUS: FINISHED signal as normal
```

This backstops false-positive dispatch — triage matched on file paths/keywords, but the actual changes may not warrant your review.

**Small changes still warrant review.** A one-line change in a security-sensitive function needs full review. The check is domain relevance, not change size.

**Large PRs (100+ matched files):** Use `--summary` for a diffstat overview, then selectively read the most important diffs.

## RULE: Reviewing vs Exploring

| Activity | Scope | Generates findings? |
|----------|-------|---------------------|
| **Reviewing** | Changed files only (the diff) | YES |
| **Exploring** | Any file in the codebase | NO |

Explore freely (conventions, call sites, similar patterns) — exploration informs review but never produces findings.

**STOP CHECK — before every `add_issue()` call:**

State the file path and line number, then verify:
1. Is this file in `CHANGED_FILES`? (NO → drop)
2. Is this line in a diff hunk? (NO → drop)

Both must be YES. Findings on unchanged code are false positives.

**Exception — findings that are line-less BY NATURE.** Some legitimate findings have no line to anchor to: a whole changed file has no test coverage, a git-history precedent applies to the change, a cross-file architectural concern. For these, call `add_issue(..., line=None)` — the builder records a **file-scoped issue** (`line: null`, `scope: "file"`) that counts toward the verdict. Check 1 still applies: the file must be in `CHANGED_FILES`. Never use `line=None` for a point defect that has a line — that weakens verification downstream.

**Exception — a changed hunk's new failure behavior reaching an unchanged caller.** "Unchanged code" means unchanged in isolation, not unaffected. If a hunk changes what a function does on failure — it now throws where it previously returned a default, swallowed the error, or was caught internally — every caller reachable from that changed behavior is in scope, even a caller in a file with zero diff. Anchor the finding at the **changed hunk that introduced the new failure behavior** (that file:line is in `CHANGED_FILES` and passes the STOP CHECK above), and describe the unguarded unchanged caller as blast radius in the finding body — never anchor the finding at the caller itself. Before clearing a suspected gap on "existing infrastructure unchanged by this PR," trace every reachable caller of the changed function using symbol-aware search, AST analysis, or repository index tooling when available — these catch aliases, re-exports/imports, overrides, callbacks, and generated or string-built call sites that plain text search misses. Use `grep -rn` across the whole repo as a supplemental pass, not the sole method. Review each candidate call site before clearing the finding — do not clear on tool output alone. A caller's empty `git diff` proves the caller is unchanged; it does not prove the caller is safe against the callee's new behavior.

<example type="FAILURE — this shipped a caller regression a reviewer had already found and then dismissed">
A PR changed a context method from "fails silently, callers unaffected" to "throws and caches the exception." Two of its three call sites gained a new try/catch in the same PR; the third — in a file with zero diff — did not. A reviewer traced all three call sites, correctly identified the third as unguarded against the new throw, then discarded the finding because `git diff` on that third file returned no output ("pre-existing, out of scope"). The finding was correct and had already been made; it was cleared by checking the wrong file's diff. A human reviewer found the same call site independently and blocked the PR on it.
</example>

**CRITICAL — use SOURCE FILE line numbers only:**

The Read tool's display numbers (e.g., `227→+class Foo`) are positions *within the patch file*. Use `@@ ... @@` hunk headers for source lines:
- `@@ -0,0 +1,116 @@` → new file starts at source line 1
- `@@ -20,6 +20,11 @@` → changed section starts at source line 20
- Count forward from `+N` through `+` and ` ` (context) lines

When uncertain, read the actual source file to confirm.

**Finding quality gates** (verify each before `add_issue()`):
1. **Changed code only.** Report issues INTRODUCED by this change.
2. **Bet your reputation.** Uncertain → verify deeper or drop.
3. **Review the change, not the codebase.** Evaluate THIS CHANGE only.
4. **Bug, not preference.** For LOW/MEDIUM: formatting opinions, naming style, "I'd do it differently" without a concrete defect → drop.
5. **Verify factual claims.** Read the actual implementation with the Read tool before claiming missing checks, wrong complexity, etc. (47% of false positives are unverified factual claims.)
6. **Cite your source.** Numbers, counts, line refs, API behaviors → cite the tool output. No command or file read = not verified.

<example type="CORRECT">
"process_payment() at line 42 concatenates user input into SQL query — this line was ADDED in this PR."
</example>

<example type="INCORRECT">
"validate_email() at line 200 is missing sanitization — found while exploring the file for context."
</example>

**Simplification bias:** When evaluating a change, consider whether the same goal could be achieved by removing or simplifying existing code rather than adding new code. The best fix is sometimes less code, not more. If you spot a simplification opportunity within your domain, include it. This is a lens, not a mandate — don't force it.

**Preexisting-code agents** (patterns-reviewer, history-insights-reviewer): search the **base ref state** (`git grep <pattern> <base_ref>`, `git show <base_ref>:<path>`), not HEAD. HEAD includes the PR's own changes.

## Absence Claims (Clearing Blast Radius)

A negative search result proves only that the **searched pattern is absent** — never that the dependency is. "I grepped for X and found nothing" is evidence about X, not about what depends on the changed code.

<example type="FAILURE — this shipped a regression">
A change removed a `<label>` from a `<th class="titledesc">`. Three reviewers each grepped `.titledesc label`, found nothing, and declared "no blast radius." The load-bearing CSS selectors were `th label` — a pattern their search string could not match. The regression was real, verified, and visible on three core settings pages.
</example>

Rules for any "nothing depends on this" / "no blast radius" / "no consumers" claim:

1. **Search the dependent side, in its own vocabulary.** Enumerate what COULD depend on the changed code, and search each dependent artifact for the terms *it* would use — not the literal string you saw in the diff. For removed markup: CSS selectors that could match it (element names, ancestor/sibling combinators, ancestor classes — not just its own class), JS/DOM queries, test locators, AT semantics. For removed functions: callers, hook registrations, string-built call sites, subclasses. For removed config: readers, defaults, migrations.
2. **Reading beats searching.** When the dependent artifact is identifiable (the stylesheet, the consumer module, the test file), enumerate ALL occurrences of the dependency's tokens across the whole artifact and read each site — do not conclude from a single grep or a single windowed read.
3. **State your method or the claim doesn't count.** Record every clearance via `builder.add_clearance(claim=..., method=..., evidence=...)` — never as a free-text positive. The `method` field must state the exact search commands/terms used and files read, so downstream stages can judge coverage: "grepped `.titledesc label` across plugins/ — no hits" is auditable (and its gap is findable); "no blast radius found" is not. Clearances flow into reconciliation where conflicts with other agents' findings are resolved by verification; positives do not.
4. **A negative search cannot ground an approval alone.** It may support one alongside dependent-side verification. If you cannot search the dependent side (out-of-tree consumers, unresolvable hosts), say so explicitly instead of clearing.

## Output Directory

**If Output Directory was provided:** use it (`mkdir -p` if needed).

**If not provided:** use the `OUTPUT_DIR` from `scope.py` output. The script auto-detects PR number via `gh` (github.com) or `ghe` (github.a8c.com) and creates `/tmp/pr-review-{N}`. Falls back to `/tmp/` when no PR is found.

**If the script was not available:**
```bash
PR_NUM=$(gh pr view --json number -q .number 2>/dev/null || ghe pr view --json number -q .number 2>/dev/null || echo "")
if [ -n "$PR_NUM" ]; then
  OUTPUT_DIR="/tmp/pr-review-${PR_NUM}"
else
  OUTPUT_DIR="/tmp"
fi
mkdir -p "$OUTPUT_DIR"
```

**Note on GHE:** For repos hosted on `github.a8c.com`, the `ghe` CLI is used (requires SOCKS5 proxy). The `scope.py` script handles this automatically by detecting the remote URL.

## ReviewOutputBuilder API

```python
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '../scripts'))
from review.agent.output import ReviewOutputBuilder

builder = ReviewOutputBuilder(pr_id=PR_ID, reviewer="REVIEWER_NAME")
```

**Core methods:**
- `builder.add_issue(severity, title, file, description, recommendation, category="general", line=<required for point defects>, confidence=0.9)` - Add diff-anchored finding. Pass `line=None` ONLY for findings that are line-less by nature (missing test coverage, precedent, cross-file architecture) — recorded as a verdict-counting file-scoped issue
- `builder.add_observation(file, note, category="general")` - Add informational file-level note (doesn't affect verdict — do NOT use for real findings)
- `builder.add_clearance(claim, method, evidence=None)` - Record an absence claim ("nothing depends on the removed X") with the exact searches/reads that ground it. Required for any blast-radius clear — see "Absence Claims" section
- `builder.set_files_reviewed(N)` - Track files reviewed
- `builder.add_tool_result("ToolName")` - Track tools used
- `builder.set_confidence(0.0-1.0)` - Set overall confidence
- `builder.add_positive("observation")` - Note good patterns
- `builder.save(output_dir)` - Write both output files, print the RECORDED COUNTS echo, return the paths (use this — not manual `to_json()`/`to_markdown()` writes)

**Valid severities:** `critical`, `high`, `medium`, `low`, `info`

## File-Based Output

Write both outputs via `save()`, then return signals only:

```python
result = builder.save(OUTPUT_DIR)
# Writes {output_dir}/{reviewer}-review.json and .md, prints the RECORDED
# COUNTS / RECORDED ISSUES / VERDICT echo, and returns {"json": path, "markdown": path}
```

Do NOT write `to_json()`/`to_markdown()` output by hand — a manual write skips the RECORDED COUNTS echo, leaving you nothing to reconcile your COUNTS against.

**Invocation rule:** run the builder from a script FILE (written with the Write tool) or a heredoc (`python3 <<'PY' ... PY`). NEVER inline `python3 -c "..."` — finding prose contains apostrophes, quotes, and em-dashes that break shell quoting and crash the call.

**When using `/tmp/` directly** (no PR number detected), save into a timestamped subdirectory to avoid collisions: `builder.save(f"/tmp/{reviewer}-review-{YYYYMMDD-HHMMSS}")`.

**Count reconciliation:** `builder.save()` prints the RECORDED COUNTS / RECORDED ISSUES / VERDICT of what was actually saved. Copy the `COUNTS:` in your return signal from that echo — not from memory of what you intended to file. If the echo differs from your intent (an issue you added is missing, a severity changed), investigate and fix BEFORE declaring FINISHED.

**Return signal format:**
```
STATUS: FINISHED
OUTPUT_FILES:
  - {output_dir}/{reviewer}-review.json
  - {output_dir}/{reviewer}-review.md
COUNTS:
  critical: N
  high: N
  medium: N
VERDICT: <verdict>
SUMMARY: <one sentence>
```

Do NOT return full review text. The reconciliator reads your files.

## Project-Specific Knowledge

Before reviewing, search for project-specific documentation:

```bash
find . -type f \( -name "CLAUDE.md" -o -name "*.md" \) -path "*/.claude/*" 2>/dev/null | head -20
```

Read: `CLAUDE.md`, `.claude/skills/`, `.claude/docs/`, ADRs, architecture docs. **Project standards override generic patterns.** Apply project conventions before domain expertise. This is exploration — it informs review but is not itself reviewable.

## Host Context Usage

The bootstrap may inject a **Host Context** section into your prompt with local paths that repo signals made worth checking: upstream runtime hosts (e.g., wp-env'd WordPress at `/x/wp`) and library dependency roots (composer's `vendor/`, npm's `node_modules/` — possibly served from `~/.cache/pirategoat/library-deps/<clone_id>/<manager>/` rather than the repo). Treat these as starting points; explore normally when they don't match the code path under review.

**Rules:**
- Use Host Context paths as shortcuts when your finding depends on upstream behavior — read or grep the listed paths instead of speculating about hook signatures, class methods, or library function shapes.
- Prefer targeted `Grep` over wholesale directory reads — `vendor/` and `node_modules/` roots can be huge.
- If a host is marked **unresolved** or the **Banner** indicates degradation, you cannot verify upstream behavior. Two options: (1) downgrade severity and add a `verify locally` note in the recommendation, or (2) skip the finding if it depends entirely on the unverified host. Do not state absence ("function X doesn't exist") for unresolved hosts.
- Don't recommend edits to paths under `~/.cache/pirategoat/library-deps/` or any other library-dep root — those are review aids, not editable code. Recommendations should target the reviewed repo.
- Cite upstream sources using `file:line` so the reconciliator can verify: e.g., `woocommerce/plugins/woocommerce/includes/class-wc-order.php:123`.

### Bounded Filesystem Discovery

Host Context being non-exhaustive does not make the whole filesystem a valid search root.

- Never run recursive discovery from `/` or `$HOME`.
- Every recursive search must name a bounded root: the reviewed repository, an injected Host Context path, a declared dependency root, or a specific path named by repository configuration/imports.
- For sibling discovery, list the repository parent one level deep, select a plausible sibling checkout, and search inside that specific sibling. Do not recursively scan the parent directory.
- Prefer targeted Grep/Glob or `rg --files -g '<pattern>' <root>` over `find`.
- When those roots are exhausted, stop discovery rather than widening the search root.
