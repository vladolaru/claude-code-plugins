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

## Quick Relevance Check (BEFORE Deep Review)

Scan the diff hunks (changed lines, not just file names): does anything relate to your domain? Triage matched on file paths and keywords; the actual changes may not warrant your review.

If nothing is relevant, call `builder.mark_not_applicable("No changes relevant to [your domain] — diff contains only [brief description]")`, then `builder.save_draft()` and finalize as OUTPUT INSTRUCTIONS says.

Small changes still warrant review: a one-line change in a security-sensitive function needs full review. The check is domain relevance, not change size.

## RULE: Reviewing vs Exploring

| Activity | Scope | Generates findings? |
|----------|-------|---------------------|
| **Reviewing** | Changed files only (the diff) | YES |
| **Exploring** | Any file in the codebase | NO |

Explore freely (conventions, call sites, similar patterns); exploration informs review but never produces findings.

**STOP CHECK — before every `add_finding()` call**, state the file path and line number, then verify:
1. Is this file in `CHANGED_FILES`? (NO → drop)
2. Is this line in a diff hunk? (NO → drop)

Both must be YES. Findings on unchanged code are false positives.

**Exception — findings that are line-less by nature** (OUTPUT INSTRUCTIONS explains `line=None`): check 1 still applies; the file must be in `CHANGED_FILES`.

**Exception — a changed hunk's new contract reaching an unchanged caller.** When a hunk changes what a function does (what it throws or returns on failure, its return shape, a side effect, an ordering or timing guarantee, a validation rule), every caller that relied on the old contract is in scope, even one in a file with no diff. Anchor the finding at the changed hunk, which passes both checks, and describe the unguarded caller as blast radius in the body; a finding anchored in a file outside the diff is dropped by the structural prefilter before anyone reads it. A caller's empty `git diff` proves it is unchanged, not that it is safe: clear callers the way §Absence Claims requires.

<example type="FAILURE — this cleared a caller regression the reviewer had already found">
A PR changed a method from "fails silently, callers unaffected" to "throws and caches the exception". Two of its three call sites gained a try/catch in the same PR; the third, in a file with zero diff, did not. The reviewer traced all three, identified the third as unguarded, then discarded the finding because `git diff` on that file was empty ("pre-existing, out of scope"). An independent reviewer found the same call site and blocked the PR on it. The finding was right and already made; it was cleared by checking the wrong file's diff.
</example>

**CRITICAL — use SOURCE FILE line numbers only.** The Read tool's display numbers (`227→+class Foo`) are positions within the patch file. Take source lines from the `@@ ... @@` hunk headers: `@@ -0,0 +1,116 @@` starts a new file at source line 1; `@@ -20,6 +20,11 @@` starts a changed section at source line 20; count forward from `+N` through `+` and ` ` (context) lines. When uncertain, read the source file to confirm.

**Finding quality gates** (before every `add_finding()`):
1. **Changed code only.** Report issues this change introduced; evaluate this change, not the codebase.
2. **Bet your reputation.** Uncertain: verify deeper or drop.
3. **Bug, not preference.** Formatting opinions, naming style, "I'd do it differently" without a concrete defect: drop.
4. **Verify factual claims.** Read the implementation with the Read tool before claiming a missing check, wrong complexity, or similar; 47% of false positives are unverified factual claims.
5. **Cite your source.** Numbers, counts, line refs and API behavior come from tool output. No command or file read means not verified.

<example type="CORRECT">
"process_payment() at line 42 concatenates user input into SQL query — this line was ADDED in this PR."
</example>

<example type="INCORRECT">
"validate_email() at line 200 is missing sanitization — found while exploring the file for context."
</example>

**Simplification lens:** when the same goal could be reached by removing or simplifying existing code, say so within your domain. A lens, not a mandate.

**Preexisting-code agents** (patterns-reviewer, history-insights-reviewer) search the **base ref state** (`git grep <pattern> <base_ref>`, `git show <base_ref>:<path>`), not HEAD, which already includes the PR's changes.

### Bounded Filesystem Discovery

Host Context being non-exhaustive does not make the whole filesystem a search root.

- Never run recursive discovery from `/` or `$HOME`. Every recursive search names a bounded root: the reviewed repository, an injected Host Context path, a declared dependency root, or a path named by repository configuration or imports.
- For sibling discovery, list the repository parent one level deep, pick a plausible sibling checkout, and search inside it; never scan the parent recursively.
- Prefer targeted Grep/Glob or `rg --files -g '<pattern>' <root>` over `find`. When those roots are exhausted, stop rather than widen.

## Empirical Probes (Running Code)

Reproducing a finding by running code is encouraged, never at the reviewed repo's expense:

- **Never create or modify tracked files** in the repo under review. Mutation belongs to tests-mutation-reviewer alone, which runs solo and restores what it touches.
- **A probe that needs a new file** creates it inside the repo with `pirategoat-probe` in the file's own name (`zz_pirategoat-probe_test.go`; matched literally, hyphen included, a parent directory's name does not count), in a path git does not ignore, since ignored paths are invisible to the pipeline's residue sweep. Leftovers with that marker are swept and reported at the end of the run.
- **Create, run and delete in one command**, so an interrupted turn cannot orphan the file:

  ```bash
  cp "$TMPDIR/probe.go" pkg/zz_pirategoat-probe_test.go && go test ./pkg/ ; rm -f pkg/zz_pirategoat-probe_test.go
  ```

- **Never use `git reset`, `git checkout --` or `git clean` as cleanup.** The repo is the user's live working tree and may hold uncommitted work.

## Absence Claims (Clearing Blast Radius)

A negative search result proves only that the **searched pattern is absent**, never that the dependency is. "I grepped for X and found nothing" is evidence about X, not about what depends on the changed code.

<example type="FAILURE — this shipped a regression">
A change removed a `<label>` from a `<th class="titledesc">`. Three reviewers each grepped `.titledesc label`, found nothing, and declared "no blast radius." The load-bearing CSS selectors were `th label` — a pattern their search string could not match. The regression was real, verified, and visible on three core settings pages.
</example>

Rules for any "nothing depends on this" / "no blast radius" / "no consumers" claim:

1. **Search the dependent side, in its own vocabulary.** Enumerate what could depend on the changed code and search each dependent artifact for the terms it would use, not the string you saw in the diff. Removed markup: the CSS selectors that could match it (element names, ancestor and sibling combinators, ancestor classes), DOM queries, test locators, AT semantics. Removed function: callers, hook registrations, string-built call sites, subclasses. Changed contract: every caller that relied on the old behavior, whether or not its file changed. Removed config: readers, defaults, migrations.
2. **Reading beats searching.** When the dependent artifact is identifiable (the stylesheet, the consumer module, the test file), enumerate every occurrence of the dependency's tokens across it and read each site. One grep or one windowed read does not conclude.
3. **State your method and result or the check does not count.** Record a material negative or blast-radius conclusion with `builder.record_check(question=..., method=..., result=...)`, never as a free-text positive. `method` names the exact searches and files read; `result` states what they observed, so downstream stages can judge coverage: "grepped `.titledesc label` across plugins/ — 0 hits" is auditable and its gap findable; "no blast radius found" is neither. Checks flow into reconciliation, where conflicts with other agents' findings are resolved by verification; positives do not. When a check settles a REVIEW FOCUS `## Verify` item, pass `verifies=["V2"]` naming it.
4. **A negative search cannot ground an approval alone.** It may support one beside dependent-side verification. If you cannot search the dependent side (out-of-tree consumers, unresolvable hosts, string dispatch, generated code), say so instead of clearing.

## Output Directory

The dispatch prompt always provides `--output-dir`. Use that durable run directory (`mkdir -p` if needed) for every review artifact.

Per-reviewer artifacts live under `OUTPUT_DIR/reviewers/<reviewer>/` with fixed filenames: `assignment.json`, `scope-summary.json` (plus domain-specific `scope-summary-<domain>.json` files), `scoped-diff.patch`, `briefing.md`, `started` and a `bootstrap-error` failure record when a bootstrap stopped, `review.draft.json`, `review.json`, and derived `review.md`; use the short reviewer identity bootstrap provides, never a full agent-name filename prefix.

Use `OUTPUT_DIR/tmp/` as the sanctioned scratch location for probes, `.patch` experiments, and similar temporary work — never the reviewed repo worktree and never the run-directory root.

## Canonical Draft Lifecycle

Bootstrap's OUTPUT INSTRUCTIONS carry the one executable command; this is the ownership contract behind it.

1. `ReviewOutputBuilder.open(...)` creates a working draft on first use or rehydrates the existing one, stable id counters included. Never use the direct constructor as a raw reviewer.
2. `builder.add_finding(...)`, `builder.add_observation(...)` and `builder.add_positive_observation(...)` record the review state you own; correct earlier work by the stable `fN` id a continuation's `DRAFT INDEX` shows, with `update_finding()` or `remove_finding()`.
3. `builder.record_check(...)` records every material verification result; correct by `cN` id with `update_check()` or `remove_check()`.
4. `builder.claim_files_reviewed(...)` after actually reading a claimable non-inline file; `retract_reviewed_file_claims()` when a claim no longer reflects what you read.
5. `set_assessment()` belongs to the reconciliator. Raw reviewers never call it.
6. `builder.save_draft()` persists the draft and prints a receipt: the persisted totals, what this save changed, any review files still unclaimed, and the exact `FINALIZE REVIEW` command to run verbatim in a separate tool turn, never constructed or edited. To continue, open again; it rehydrates everything. Return `STATUS: FINISHED` only after that command prints `REVIEW FINALIZED`. The builder and that command are the only publication path; never write review JSON or Markdown yourself.
7. A builder script that raises recorded nothing: fix the failing call and re-run the whole script with every finding, check and observation it carried. A retry that only saves publishes an empty review, which reads downstream as a clean approve.

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

Before reviewing, read the project's own rules: `CLAUDE.md`, `AGENTS.md`, `.claude/` skills and docs, ADRs, architecture docs. **Project standards override generic patterns**; apply project conventions before domain expertise. This is exploration: it informs review and is not itself reviewable.

## Host Context Usage

Bootstrap may inject a **Host Context** section with local paths repo signals made worth checking: upstream runtime hosts (a wp-env'd WordPress at `/x/wp`) and library dependency roots (composer's `vendor/`, npm's `node_modules/`). They are starting points; explore normally when they do not match the code path under review. A host mounted by wp-env or docker-compose proves only that the local site mounts it beside the repository, not which way the dependency runs: WordPress mounted into a plugin's site is upstream, but a plugin mounted into WooCommerce core's site is a downstream consumer of the code under review. Decide the direction from the diff: read a listed host as upstream only when the reviewed code calls into it, and as a consumer to grep for callers when it calls into the reviewed code.

- When a finding depends on upstream behavior, read or grep the listed paths instead of speculating about hook signatures, class methods or library function shapes; prefer targeted `Grep` over wholesale directory reads, since `vendor/` and `node_modules/` can be huge.
- A host marked **unresolved**, or a **Banner** reporting degradation, means you cannot verify upstream behavior: downgrade severity and add a `verify locally` note in the recommendation, or skip the finding if it depends entirely on that host. Never state absence ("function X doesn't exist") for an unresolved host.
- Recommendations target the reviewed repo, never a Host Context dependency root; those are review aids, not editable code.
- Cite a resolved host as `<host>@<version, commit, or unknown>:<upstream-relative path>:<line>` (`wordpress@7.2-alpha-63166-src:src/wp-includes/post.php:1234`, or `wordpress@unknown:…` when the run recorded neither), so the reconciliator verifies against the same copy; cite any other source as `file:line`. Do not search for another copy: a second copy at another version makes your finding unverifiable. If you read a different copy anyway, say so in the check's `method` with that copy's identity and version, never a local path.
