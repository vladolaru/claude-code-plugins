# Shared Reviewer Protocol

Standard protocol for all review agents. Read this FIRST before starting your review.

## Step 0: Locate Plugin Root

**Preferred: Use the bootstrap script** which handles all setup (plugin root, protocol, scope discovery) in a single command:

```bash
PLUGIN_ROOT=$(cat "${PIRATEGOAT_TOOLS_HOME:-$HOME/.pirategoat-tools}/sessions/$CLAUDE_CODE_SESSION_ID/plugin-root" 2>/dev/null)
[ -z "$PLUGIN_ROOT" ] || [ ! -d "$PLUGIN_ROOT/scripts" ] && PLUGIN_ROOT=$(find ~/.claude -path "*/pirategoat-tools/*/scripts/review/agent/bootstrap.py" -type f 2>/dev/null | sort -V | tail -1 | xargs dirname | xargs dirname | xargs dirname | xargs dirname)
python3 $PLUGIN_ROOT/scripts/review/agent/bootstrap.py --agent <agent-name>
```

If the bootstrap script is not available, locate the plugin root manually:

```bash
PLUGIN_ROOT=$(cat "${PIRATEGOAT_TOOLS_HOME:-$HOME/.pirategoat-tools}/sessions/$CLAUDE_CODE_SESSION_ID/plugin-root" 2>/dev/null)
# Fallback if hook hasn't run yet
[ -z "$PLUGIN_ROOT" ] || [ ! -d "$PLUGIN_ROOT/scripts" ] && PLUGIN_ROOT=$(find ~/.claude -path "*/pirategoat-tools/*/scripts/review/agent/scope.py" -type f 2>/dev/null | sort -V | tail -1 | xargs dirname | xargs dirname | xargs dirname | xargs dirname)
echo "PLUGIN_ROOT=$PLUGIN_ROOT"
```

If this fails, fall back to the manual scope discovery at the end of this section.

Store `PLUGIN_ROOT` — you'll use it for:
- `python3 $PLUGIN_ROOT/scripts/review/agent/scope.py` — scope discovery
- Reading reference files like `$PLUGIN_ROOT/agents/shared/*.md`, `$PLUGIN_ROOT/skills/*/references/*.md`

## Scope Discovery (Do This FIRST)

Use `scope.py` to efficiently determine your review scope. It handles range detection, noise filtering, domain filtering, the diff line cap, and output directory detection in a single call.

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
| `REVIEW_CLAIMABLE` | Files listed but not diffed because the diff line cap was reached |

**On `STATUS: ERROR`:** bootstrap prints the diagnosis and its ACTION and delivers no briefing. Each agent definition branches on the status before its read-the-briefing instruction, because this section is stripped before you receive the protocol.

**On `STATUS: NO_DOMAIN_FILES`:** bootstrap records and finalizes the `not_applicable` review itself. Its stdout stub delivers the return signal, and each agent definition branches on the status before its read-the-briefing instruction, because this section is stripped before you receive the protocol.

**On `STATUS: OK`:** The `=== DIFFS ===` section contains filtered diffs for matched files within the diff line cap. Files are sorted by inline priority (production code before tests for mixed domains), largest-first within each tier. One oversized leading file may be admitted in full as a protected exception; the remaining files share the normal cap.

**On `REVIEW_CLAIMABLE` / `=== REVIEW-CLAIMABLE ===`:** These files matched your domain but their diffs were NOT given to you. Claim every file you actually read through the positive-claim API; the builder validates those claims and derives every remaining path as an unclaimed review file. Bootstrap's `=== REVIEW BUDGET ===` section delivers the executable contract because this section is stripped before you receive the protocol.

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
builder.save_draft()
# Inspect the receipt, then run its exact FINALIZE REVIEW command in a separate tool turn.
# Return STATUS: FINISHED only after that command prints REVIEW FINALIZED.
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

**STOP CHECK — before every `add_finding()` call:**

State the file path and line number, then verify:
1. Is this file in `CHANGED_FILES`? (NO → drop)
2. Is this line in a diff hunk? (NO → drop)

Both must be YES. Findings on unchanged code are false positives.

**Exception — findings that are line-less BY NATURE.** Some legitimate findings have no line to anchor to: a whole changed file has no test coverage, a git-history precedent applies to the change, a cross-file architectural concern. For these, call `add_finding(..., line=None)` — the builder records a **file-scoped finding** (`line: null`, `scope: "file"`) that counts toward the verdict. Check 1 still applies: the file must be in `CHANGED_FILES`. Never use `line=None` for a point defect that has a line — that weakens verification downstream.

**Exception — a changed hunk's new contract reaching an unchanged caller.** "Unchanged code" means unchanged in isolation, not unaffected. When a hunk changes what a function does — what it throws or returns on failure, its return shape, a side effect, an ordering or timing guarantee, a validation rule — every caller that relied on the old contract is in scope, even one in a file with no diff. Anchor the finding at the changed hunk (that file:line passes both checks above) and describe the unguarded caller as blast radius in the body — never at the caller: a finding anchored in a file outside the diff is dropped by the pipeline's structural prefilter before anyone reads it. A caller's empty `git diff` proves it is unchanged, not that it is safe. Trace and clear callers the way §Absence Claims requires: enumerate the dependent side in its own vocabulary, read each site, and record the clearance as a `record_check` whose `result` names any call site you could not resolve statically (string dispatch, generated code, out-of-tree consumers) instead of treating tool silence as safety.

<example type="FAILURE — this cleared a caller regression the reviewer had already found">
A PR changed a method from "fails silently, callers unaffected" to "throws and caches the exception". Two of its three call sites gained a try/catch in the same PR; the third, in a file with zero diff, did not. The reviewer traced all three, identified the third as unguarded, then discarded the finding because `git diff` on that file was empty ("pre-existing, out of scope"). An independent reviewer found the same call site and blocked the PR on it. The finding was right and already made; it was cleared by checking the wrong file's diff.
</example>

**CRITICAL — use SOURCE FILE line numbers only:**

The Read tool's display numbers (e.g., `227→+class Foo`) are positions *within the patch file*. Use `@@ ... @@` hunk headers for source lines:
- `@@ -0,0 +1,116 @@` → new file starts at source line 1
- `@@ -20,6 +20,11 @@` → changed section starts at source line 20
- Count forward from `+N` through `+` and ` ` (context) lines

When uncertain, read the actual source file to confirm.

**Finding quality gates** (verify each before `add_finding()`):
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

### Bounded Filesystem Discovery

Host Context being non-exhaustive does not make the whole filesystem a valid search root.

- Never run recursive discovery from `/` or `$HOME`.
- Every recursive search must name a bounded root: the reviewed repository, an injected Host Context path, a declared dependency root, or a specific path named by repository configuration/imports.
- For sibling discovery, list the repository parent one level deep, select a plausible sibling checkout, and search inside that specific sibling. Do not recursively scan the parent directory.
- Prefer targeted Grep/Glob or `rg --files -g '<pattern>' <root>` over `find`.
- When those roots are exhausted, stop discovery rather than widening the search root.

## Empirical Probes (Running Code)

Reproducing a finding by running code is encouraged — never at the
reviewed repo's expense:

- **Never create or modify tracked files** in the repo under review.
  Mutation belongs exclusively to tests-mutation-reviewer, which runs
  solo and restores what it touches.
- **A probe that needs a new file** creates it inside the repo with
  `pirategoat-probe` in the FILENAME (e.g. `zz_pirategoat-probe_test.go`
  — the marker is matched literally, hyphen included, and it must be in
  the file's own name, not just a parent directory's), in a path
  git does not ignore (ignored paths are invisible to the pipeline's
  residue sweep). The pipeline treats that marker as its own residue:
  leftovers are swept and reported at the end of the run.
- **Create, run, and delete in a single command**, so an interrupted
  turn cannot orphan the file:

  ```bash
  cp "$TMPDIR/probe.go" pkg/zz_pirategoat-probe_test.go && go test ./pkg/ ; rm -f pkg/zz_pirategoat-probe_test.go
  ```

- **Never use `git reset`, `git checkout --`, or `git clean` as cleanup**
  — the repo is the user's live working tree and may hold uncommitted
  work.

## Absence Claims (Clearing Blast Radius)

A negative search result proves only that the **searched pattern is absent** — never that the dependency is. "I grepped for X and found nothing" is evidence about X, not about what depends on the changed code.

<example type="FAILURE — this shipped a regression">
A change removed a `<label>` from a `<th class="titledesc">`. Three reviewers each grepped `.titledesc label`, found nothing, and declared "no blast radius." The load-bearing CSS selectors were `th label` — a pattern their search string could not match. The regression was real, verified, and visible on three core settings pages.
</example>

Rules for any "nothing depends on this" / "no blast radius" / "no consumers" claim:

1. **Search the dependent side, in its own vocabulary.** Enumerate what COULD depend on the changed code, and search each dependent artifact for the terms *it* would use — not the literal string you saw in the diff. For removed markup: CSS selectors that could match it (element names, ancestor/sibling combinators, ancestor classes — not just its own class), JS/DOM queries, test locators, AT semantics. For removed functions: callers, hook registrations, string-built call sites, subclasses. For a changed contract (failure behavior, return shape, side effects, ordering, validation): the same dependent side — every caller that relied on the old behavior, whether or not its own file changed. For removed config: readers, defaults, migrations.
2. **Reading beats searching.** When the dependent artifact is identifiable (the stylesheet, the consumer module, the test file), enumerate ALL occurrences of the dependency's tokens across the whole artifact and read each site — do not conclude from a single grep or a single windowed read.
3. **State your method and result or the check doesn't count.** When the task requires a material negative or blast-radius conclusion, record it via `builder.record_check(question=..., method=..., result=...)` — never as a free-text positive. The `method` field must state the exact search commands/terms used and files read, and `result` must state what the probe observed, so downstream stages can judge coverage: "grepped `.titledesc label` across plugins/ — 0 hits" is auditable (and its gap is findable); "no blast radius found" is not. Checks flow into reconciliation where conflicts with other agents' findings are resolved by verification; positives do not. When the check settles one of the REVIEW FOCUS `## Verify` items, pass `verifies=["V2"]` naming that item, so the pipeline can table who verified what without reading prose.
4. **A negative search cannot ground an approval alone.** It may support one alongside dependent-side verification. If you cannot search the dependent side (out-of-tree consumers, unresolvable hosts), say so explicitly instead of clearing.

## Output Directory

The dispatch prompt always provides `--output-dir`. Use that durable run directory (`mkdir -p` if needed) for every review artifact.

Per-reviewer artifacts live under `OUTPUT_DIR/reviewers/<reviewer>/` with fixed filenames: `assignment.json`, `scope-summary.json` (plus domain-specific `scope-summary-<domain>.json` files), `scoped-diff.patch`, `briefing.md`, `started` and a `bootstrap-error` failure record when a bootstrap stopped, `review.draft.json`, `review.json`, and derived `review.md`; use the short reviewer identity bootstrap provides, never a full agent-name filename prefix.

Use `OUTPUT_DIR/tmp/` as the sanctioned scratch location for probes, `.patch` experiments, and similar temporary work — never the reviewed repo worktree and never the run-directory root.

## Canonical Draft Lifecycle

Follow this order for every raw domain review. Bootstrap supplies the one executable command; this section explains the state transitions and ownership without offering a second command to copy.

1. Start with `ReviewOutputBuilder.open(...)`. It creates a new working draft on first use or validates and rehydrates the existing complete draft on continuation. Never use the direct constructor as a raw reviewer.
2. Record the review state you own with `builder.add_finding(...)`, `builder.add_observation(...)`, and `builder.add_positive_observation(...)`. Use the stable `fN` IDs shown in a continuation's `DRAFT INDEX` with `update_finding()` or `remove_finding()` when correcting prior work.
3. Record every material verification result with `builder.record_check(...)`. Use the stable `cN` IDs from the continuation index with `update_check()` or `remove_check()` when correcting prior work.
4. After actually reading a claimable non-inline file, call `builder.claim_files_reviewed(...)`; use `retract_reviewed_file_claims()` if a prior claim no longer reflects what you read.
5. Raw reviewers must not call `set_assessment()`. The initial synthesis assessment is pipeline-owned and is authored only by the reconciliator after judging all reviewer evidence.
6. Call `builder.save_draft()` when the builder holds the review you intend to finalize, then do what its receipt says: it reports the full persisted draft's totals, what this save changed against the draft you opened, any review files still unclaimed, and the exact `FINALIZE REVIEW` command to run verbatim in a separate tool turn — never constructed, edited, or interpreted. If more work is needed, continue in a new invocation with `ReviewOutputBuilder.open(...)`, which rehydrates everything including the stable ID counters; otherwise return `STATUS: FINISHED` only after that command prints `REVIEW FINALIZED`.
7. Never write review JSON or Markdown directly. The builder plus its printed `FINALIZE REVIEW` command are the only publication path a raw reviewer has.
8. A builder script that raises has recorded nothing: the builder lives in that process and only `save_draft()` persists it. Fix the failing call and re-run the whole script with every finding, check and observation it carried. A retry that only saves publishes an empty review, and an empty approve reads downstream as a clean approve.

## ReviewOutputBuilder API

This is a non-executable API reference. Bootstrap's **OUTPUT INSTRUCTIONS** block is the sole canonical executable builder command; if bootstrap fails, stop and report the failure instead of reconstructing a command from this reference.

**Core methods:**
- `builder.add_finding(severity, title, file, description, recommendation, category="general", line=<required for point defects>, confidence=0.9)` - Add diff-anchored finding. Pass `line=None` ONLY for findings that are line-less by nature (missing test coverage, precedent, cross-file architecture) — recorded as a verdict-counting file-scoped finding
- `builder.update_finding(finding_id, **fields)` / `builder.remove_finding(finding_id)` - Correct or remove a persisted finding without recycling its stable ID
- `builder.add_observation(file, note, category="general")` - Add informational file-level note (doesn't affect verdict — do NOT use for real findings)
- `builder.record_check(question, method, result, verifies=["V2"])` - Record a material verification check with the exact question, searches/reads, and observed result. Required for material negative or blast-radius conclusions — see "Absence Claims" section — `verifies` is optional and names the REVIEW FOCUS Verify items the check settles
- `builder.update_check(check_id, **fields)` / `builder.remove_check(check_id)` - Correct or remove a persisted check without recycling its stable ID
- `builder.claim_files_reviewed(*files)` - Claim REVIEW-CLAIMABLE files you actually read from the review-claimable queue (a statement, not proof — surfaced as a claim downstream). The builder validates this entire batch against the authoritative review assignment, derives every unclaimed review file, and derives the reviewed-file count from inline files plus validated claims. Claiming an inline (diffed) file is harmless and records nothing — it is already counted.
- `builder.retract_reviewed_file_claims(*files)` - Retract reviewed-file claims that no longer reflect what you actually read before saving again.
- `builder.set_confidence(0.0-1.0)` - Set overall confidence
- `builder.add_positive_observation("observation")` - Note good patterns
- `builder.set_assessment(text)` - Pipeline-synthesis-only API for the reconciliator's initial overall assessment; raw domain reviewers do not call it

**Valid severities:** `critical`, `high`, `medium`, `low`, `info`

## File-Based Output

Bootstrap's **OUTPUT INSTRUCTIONS** provide the concrete, collision-safe command, resolved reviewer identity and paths, count-reconciliation rules, and return-signal format. They are the sole executable source for file-based output; do not reconstruct a fallback command from this protocol.

## Project-Specific Knowledge

Before reviewing, search for project-specific documentation:

```bash
find . -type f \( -name "CLAUDE.md" -o -name "*.md" \) -path "*/.claude/*" 2>/dev/null | head -20
```

Read: `CLAUDE.md`, `.claude/skills/`, `.claude/docs/`, ADRs, architecture docs. **Project standards override generic patterns.** Apply project conventions before domain expertise. This is exploration — it informs review but is not itself reviewable.

## Host Context Usage

The bootstrap may inject a **Host Context** section into your prompt with local paths that repo signals made worth checking: upstream runtime hosts (e.g., wp-env'd WordPress at `/x/wp`) and library dependency roots (composer's `vendor/`, npm's `node_modules/`). Treat these as starting points; explore normally when they don't match the code path under review. A host listed from a wp-env or docker-compose mount proves only that the local site mounts it beside the repository, not which way the dependency runs: WordPress mounted into a plugin's site is upstream, but a plugin mounted into WooCommerce core's site is a downstream consumer of the code under review. Decide the direction from the diff — read a listed host as upstream only when the reviewed code calls into it, and as a consumer to grep for callers when it calls into the reviewed code.

**Rules:**
- Use Host Context paths as shortcuts when your finding depends on upstream behavior — read or grep the listed paths instead of speculating about hook signatures, class methods, or library function shapes.
- Prefer targeted `Grep` over wholesale directory reads — `vendor/` and `node_modules/` roots can be huge.
- If a host is marked **unresolved** or the **Banner** indicates degradation, you cannot verify upstream behavior. Two options: (1) downgrade severity and add a `verify locally` note in the recommendation, or (2) skip the finding if it depends entirely on the unverified host. Do not state absence ("function X doesn't exist") for unresolved hosts.
- Don't recommend edits to Host Context library dependency roots — those are review aids, not editable code. Recommendations should target the reviewed repo.
- When a resolved host covers the code you need, read it there and cite it as `<host>@<version, commit, or unknown>:<upstream-relative path>:<line>` (`wordpress@7.2-alpha-63166-src:src/wp-includes/post.php:1234`, or `wordpress@unknown:…` when the run recorded neither), so the reconciliator can verify against the same copy; cite any other source as `file:line`. Do not search for another copy: the Host Context line names the version and commit the run verified against, and a second copy at another version makes your finding unverifiable. If you read a different copy anyway, say so in the check's `method` with that copy's identity and version, never a local path.
