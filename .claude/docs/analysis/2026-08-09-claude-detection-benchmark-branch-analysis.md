# Branch analysis — `feat/detection-benchmark-eval`

Last updated: 2026-08-09 09:45

> **Prompt:** "I want you to analyze the current branch changes and explain to me (at a high level) what are they trying to achieve and how do they do it"
> **Follow-up:** "Now think thoroughly how can this be simpler, where we are taking it too far for too little benefit, where are we inferring unreliable signals and conflate them, etc."

## Scope of the branch

- Branch: `feat/detection-benchmark-eval`
- Base: `feat/review-pipeline-measurement` @ `a248c69c` (NOT `main` — `main` is ~340 commits behind; diffing against `main` shows unrelated inherited work)
- Delta: **34 commits**, `a248c69c...HEAD` = 24 files, +2851 / −201
- Git range for review: `a248c69c...67c93ab2`

Files touched (all under `plugins/pirategoat-tools/tests/` except changelog/AGENTS/plan docs):

| Area | Files |
|---|---|
| Harness | `tests/grading/eval_agent_compliance.py` (+861) |
| Grading primitives | `tests/helpers/graders.py` (+318) |
| New guards | `tests/grading/test_answer_keys.py`, `test_eval_agent_compliance.py`, `test_graders.py` |
| Fixtures | 10 `.diff` fixtures rebuilt/scrubbed |
| Docs | `tests/TESTING.md` (+131), root `AGENTS.md`, `CHANGELOG.md`, 4 plan docs |

## What it is trying to achieve

Turn the existing **compliance eval** (does a reviewer emit well-formed JSON?) into a **detection benchmark** (does the configured reviewer actually find the planted defect, at the right severity, with the right verdict, without false positives?) — and make that number trustworthy enough to compare across plugin versions.

Two claims the branch has to make true:

1. **Instrument identity** — the thing being measured must be the branch's reviewer agent, on its configured model, with its real prompt contract. Not a generic Claude session, not the installed release of the plugin.
2. **Honest scoring** — the score must be falsifiable (a miss must be able to fail) and comparable (verbose reviewers must not out-score accurate ones).

## How it works

### 1. Answer keys per scenario

`SCENARIOS[...]["expected"][<agent>]` in `eval_agent_compliance.py` now carries a key per (scenario, agent) pair:

- `required_findings` — recall gate: file + optional line (± `line_tolerance`) + `match_any` regexes over title/description/category + `min_severity` floor
- `acceptable_findings` — legitimate secondary findings, never punished
- `max_severity` / `max_unexpected` — precision gates (the clean-code fixtures `php_clean_review` / `js_clean_review` are pure false-positive probes)
- `verdict_in` — the verdict the agent's own doctrine mandates
- `expect_not_applicable` — correct abstention (accepts `not_applicable` OR `approve` + zero findings, because the shared protocol and the tests-reviewer definitions currently mandate conflicting verdicts on `NO_DOMAIN_FILES`)

Grading is **deterministic** — regex + line window, no model judge (`graders.match_findings` / `grade_detection`). Specs claim issues mutually exclusively so one genuine finding can't satisfy two gates.

**Derivation rule (documented in TESTING.md):** keys are derived from the dispatched agent's `.md` doctrine, not from generic intuition, and cite the doctrine in a comment. E.g. security-reviewer classifies SQL injection as CRITICAL → the key floors `min_severity: critical` and requires `verdict_in: ["block"]`, so an under-classifying reviewer fails instead of scoring full credit. Two floors are deliberately calibrated below literal doctrine where the fixture cannot let the reviewer prove the stronger class (test-only fixture, WHERE-bounded query) — each documented inline.

### 2. Dispatch identity

Each dispatch now runs:

```
claude -p --dangerously-skip-permissions --setting-sources project \
  --plugin-dir <shim> --agent pirategoat-tools:<name> --output-format json
```

- `--agent` → **the session IS the reviewer**: the canonical `agents/<name>.md` becomes its system prompt with its full frontmatter contract (model/effort/tools) applied natively. Earlier iterations (a) never used the definition at all, (b) embedded it in a user prompt — both measured the wrong instrument.
- `ensure_plugin_shim()` synthesizes a tempdir with a minimal plugin manifest + symlink to the **worktree** `agents/`, because the plugin dir itself has no `.claude-plugin` manifest — without it the user-scope **installed** plugin silently answered and the benchmark graded a stale release (sentinel-verified).
- `--setting-sources project` cuts ambient hooks, user memory, and the installed copy out of the measurement.
- Model routing is pinned to `agent_registry.json` at three layers: pre-dispatch `check_model_routing` (frontmatter vs registry), a `TestDispatchIdentity` CI guard, and post-hoc verification of the run's `modelUsage` (`_primary_model` sums only the four token counters and resolves `canonicalModel`).
- Any nonzero dispatch exit fails the entry **before** grading — a rejected run may still have left a plausible artifact.

### 3. Nondeterminism control

`--trials N` re-dispatches each keyed agent N times and majority-votes every check (threshold `N//2 + 1`, so `--trials 2` demands unanimity). Because per-check majorities could be assembled from *different* trials, the aggregate **additionally** requires a majority of trials to pass outright. Unreadable/raising trials count as a miss but no longer abort the run or discard completed paid trials.

### 4. Structured reporting

`--report-out` writes JSON: `mode`, requested `trials`, and `results[]` with `scenario`, `agent`, per-entry `trials`, `keyed`, `dispatched` (derived from actual model-usage evidence, so harness failures aren't counted as reviewer failures), `passed`, check counts, `failures`, and a polymorphic `detail` (single-trial vs aggregate vs abstention vs `dispatch_rejected`), including `output_dir` for traceability.

**Headline metric is per-entry `passed`**, not summed checks — compliance adds checks per schema-valid issue, so a check-ratio headline rewarded verbosity. Check counts are now labeled diagnostic-only.

Exit codes are contractual: `2` for any config error before artifacts exist (unknown scenario, empty selection, `--trials 0`, dispatch-only flags without `--dispatch`, unwritable report path — pre-flighted with append-mode open, not `touch()`), `1` when the eval ran and an entry failed, `0` on full pass.

### 5. Fixture and key integrity guards

`tests/grading/test_answer_keys.py` (pure pytest, no model calls) validates keys before anyone pays for dispatches: files exist in the diff, lines in range, regexes compile, fixtures apply, every key has at least one gate, `line_tolerance` non-negative and non-inert, `max_unexpected` a non-negative int, anchored patterns rejected, `expect_not_applicable` forbids `verdict_in`. Plus fixture-wide integrity: every hunk header's declared new-line count must equal its carried `+` lines, and new source files must close every delimiter they open — this caught 10 fixtures that had silently been applying truncated.

Fixtures were also **de-biased**: five legacy fixtures named their own planted defects in comments ("SQL injection: unsanitized user input"), which under regex detection scoring would let a reviewer pass by restating the label. Labels scrubbed, diffs regenerated, keys re-anchored, live-reconfirmed.

## Shape of the work

Roughly: 6 commits build the feature, ~25 are hardening rounds driven by review passes (including a Codex cross-model pass) and **live calibration** — several commits cite live run scores (`php_source_review 76/76`, `e2e_tests_review 31/31`) as validation, and in two cases the *fixture* was changed rather than the key weakened (the JS XSS sink now reflects a URL query param; the WP fixture echoes `$_GET['status']` directly) so the CRITICAL classification is unambiguous.

One root-cause worth noting, captured in `.claude/docs/learnings/2026-08-06-diff-scoped-review-misses-inherited-invalidity.md`: the "never actually dispatched the configured agent" defect survived four review rounds because it lived in an *unchanged* inherited layer whose meaning the new feature silently redefined — diff-scoped review never looked at it.

---

## Critique: where this can be simpler, where it goes too far, where signals are inferred and conflated

### What is genuinely load-bearing (keep as is)

- **Plugin shim + `--setting-sources project` + `--agent` dispatch.** Fixed a real measure-the-wrong-instrument bug, sentinel-verified. This is the branch's core value.
- **Answer keys + deterministic grading.** The feature itself.
- **Fixture-apply / hunk-exactness guard.** Found 10 genuinely broken fixtures. Earned its place.
- **Label scrubbing.** Real bias under text-matching detection.
- **Per-entry `passed` headline metric.** Correct and simple.

### 1. Per-check majority voting is mathematically dead machinery

`aggregate_detection_trials` votes every check (compliance, verdict, each spec, each gate) AND requires a majority of trials to pass outright. But the outright gate *implies* every per-check majority: if ≥need trials passed outright, each of those trials passed every check, so every per-check count is ≥need. The per-check votes can never be the sole cause of failure — they add only failure-message granularity, at the cost of ~50 lines, an extra vote pair for abstention keys, and a standing doc caveat that aggregate check counts aren't comparable with single-trial counts (a metric that must not be compared with itself across modes is a smell).

**Simpler:** count passing trials, report `k/N`. Arguably drop the binary majority vote entirely — for cross-version comparison, the pass *rate* (2/3 vs 3/3) is strictly more informative than a thresholded boolean, and `per_trial` details are already retained. The vote layer is presentation, not measurement.

### 2. Model verification: three layers, one of them an unreliable inferred signal

- Layer (a) runtime `check_model_routing` (frontmatter vs registry) and layer (b) CI `TestDispatchIdentity` are **the same equality checked twice**. CI covers committed drift; the runtime check only adds coverage for uncommitted local edits.
- Layer (c) — post-hoc `_primary_model` over `modelUsage` — verifies that *Claude Code's* `--agent` model routing works. That's the host's contract, not the plugin's. And the attribution heuristic is fragile in three stacked ways:
  1. **Token weight conflates consumption with identity.** The sum is dominated by `cacheReadInputTokens`; an auxiliary model making a few cache-heavy calls can in principle outweigh the main loop, and vice versa.
  2. **Substring matching** (`tier in primary`) ties correctness to model naming conventions.
  3. **It fires after the money is spent** — a paid, possibly-correct run is converted into a failure by a heuristic (the commit history itself shows this class of check being reworked twice).

**Simpler:** keep (b); record `models`/`primary_model` in evidence for audit; demote (c) from a per-entry gate to a report field (or a single suite-level smoke assertion). If gateway model substitution ever becomes a real threat model, that's a host-level concern to verify once, not per dispatch.

### 3. `dispatched` is a boolean inferred from evidence shape, and it conflates cases

Derived from "recorded model usage present." Timeouts and unparseable output report `false` "conservatively" — but a 900s timeout almost certainly made model calls (money spent), and a reviewer that hangs is arguably *reviewer* behavior being classified as harness failure. Meanwhile a null-detail keyed failure vs a compliance-only entry must be discriminated by cross-referencing `keyed` — a doc paragraph teaches consumers the decode procedure.

**Simpler and more truthful:** replace the inferred boolean with an explicit `status` enum set by the code path that knows what happened: `config_error | cli_missing | timed_out | dispatch_failed | model_rejected | bootstrap_short_circuit | graded`. No inference, no conflation, and the discrimination paragraph in TESTING.md gets deleted.

### 4. Polymorphic `detail` outsources complexity to every consumer

Five-ish shapes (null / `{output_dir}` / single-trial / single-trial-abstention (no `gates`/`match`) / aggregate / `dispatch_rejected`), discriminated by a ~15-line prose procedure. The original justification ("downstream tooling doesn't need a normalized shape to start consuming") is backwards — polymorphism without a discriminator field is exactly what makes consumers hard. A `kind` (or the `status` enum above) costs one line per shape.

### 5. Live-run calibration risks fitting the key to the instrument

Two distinct moves got made under the same "live calibration" banner:

- **Fixture strengthening** (URL-param XSS sink; direct `$_GET['status']` echo) — good: makes ground truth unambiguous.
- **Floor lowering to match observed model behavior** (assertNotNull HIGH-not-CRITICAL, unbounded-query medium) — the benchmark's ground truth now encodes today's model's instance judgments. The floors are minimums so a stricter future model still passes, but the underlying fact — *the agent doctrine says CRITICAL and the benchmark accepts less* — is a doctrine-text miscalibration being settled in a key comment instead of fixed in the doctrine. The repo's own stated principle is "new precision belongs in producers."

**Rule of thumb worth adopting:** when live calibration disagrees with a key, the fix is either the fixture or the *agent definition* — never a negotiated key. A key that requires per-change "re-walk the derivation" across 18 pairs is a standing maintenance tax.

### 6. Abstention double-accept encodes a known doc conflict instead of fixing it

The shared protocol vs tests-reviewer definitions conflict on `NO_DOMAIN_FILES` is a small edit in files this repo owns. Instead the grader permanently widens the gate and the conflict is documented in three places (grader comment, TESTING.md, commit message). Grader-side accommodation of a producer bug — inverted priorities for ~2 lines of upstream fix.

### 7. Fixture guards exist because fixtures are hand-authored diff text

The delimiter-balance checker (with comment-tail stripping, new-file-only scoping) is a mini-parser guarding hand-edited patch files. Root-cause simplification: keep before/after fixture *source trees* in the repo and generate diffs with git (at commit time or test time). Headers exact by construction, syntax verifiable by linting real files, and the guard class collapses to "does it apply." The branch already did this once (rebuilt 10 fixtures "from their full merge-base content with git-generated exact headers") — it built the generative pipeline as a one-off instead of keeping it.

### 8. The long tail: CLI-hygiene hardening with rising marginal cost

The `--trials` presence dance (None vs explicit 1), append-mode-vs-`touch()` pre-flight (defeats an owner-metadata-touch edge case on a read-only file), dispatch-only-flags-without-`--dispatch` exit codes. Each individually defensible; collectively roughly a third of the branch polishes flag ergonomics of an internal test harness, and several rounds fix prior rounds' fixes (`dispatched` derivation reworked twice, severity floors recalibrated across three commits). The audit loop's marginal defect severity was clearly declining by round four — a stopping rule ("hardening rounds end when a round finds no measurement-invalidating defect") would have capped this at roughly half the commits.

### Summary table

| Item | Verdict | Action |
|---|---|---|
| Shim + `--agent` + setting isolation | Load-bearing | Keep |
| Answer keys, deterministic matcher | Load-bearing | Keep |
| Hunk-exactness guard, label scrub | Load-bearing | Keep |
| Per-check majority votes | Dead machinery (implied by outright-majority gate) | Delete; report k/N pass rate |
| Post-hoc model attribution gate | Unreliable inferred signal; tests the host | Demote to report field |
| Runtime frontmatter-vs-registry check | Duplicate of CI guard | Optional; cheap, may keep |
| `dispatched` boolean | Inferred, conflates timeout/harness/reviewer | Replace with explicit `status` enum |
| Polymorphic `detail` | Consumer-side complexity | Add discriminator, delete doc prose |
| Doctrine-floor settlements | Instrument-fitting risk | Fix doctrine text or fixture, not keys |
| Abstention double-accept | Grader accommodating producer bug | Reconcile the definitions |
| Delimiter-balance mini-parser | Guarding hand-authored text | Generate fixtures from source trees |
| CLI-hygiene tail | Diminishing returns | Stopping rule for future hardening loops |
