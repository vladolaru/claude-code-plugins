Last updated: 2026-07-18 22:22

> **Prompt:** "Fix them by going at the root cause and fix entire classes of issues. Commit then done"
>
> **Follow-up:** "proceed. Make sure we don't leave any garbage behind"
>
> **Follow-up:** "Garbage also means code that is not longer needed"

# Review routing false negatives

## Scope

Validate and resolve the three reported reviewer-routing gaps as classes of failure:

1. Small diffs must not be skipped on the strength of incomplete negative detectors.
2. Accessibility routing must recognize server-side render helpers without relying on an open-ended helper whitelist.
3. Accessibility scope must cover common server-template formats without silently dropping unfamiliar markup-bearing files.

## Investigation log

- Worktree started clean on `feat/review-dispatch-verification-hardening` at `271399f`.
- Commit `1c0a36a` introduced the small-diff negative-evidence gate, PHP/phtml accessibility evidence gate, shared markup detector, and centralized markup-language group.
- Reproduced all reported outcomes through `triage_conditional_agent()` and the real scope matchers:
  - `client.Do(req)` in a four-line Go diff skips both performance and reliability.
  - An indexed JavaScript `for` loop skips performance.
  - A 101-line PHP `wp_nav_menu()` change skips accessibility.
  - `.ejs`, `.liquid`, `.njk`, `.jinja2`, `.jsp`, `.cshtml`, and `.tmpl` files match neither the accessibility domain nor the unrecognized-source warning.

## Root cause

### 1. Positive recognition was promoted into negative completeness

`_CHECK_COMPETENCE` and `_agent_checks_competent()` prove only that a detector has representative positive forms for an extension. They do not prove that the detector exhaustively recognizes every form covered by an agent's natural-language criteria. `_HTTP_CLIENT_CLAIMED_LANGS` therefore claims Go because `http.Get()` is recognized even though ordinary `client.Do()` is not; `_ITERATION_CLAIMED_LANGS` claims JavaScript because `for...of` and `.forEach()` are recognized even though indexed loops are not. The small-diff gate turns that partial vocabulary into a skip.

This is a criterion-completeness problem, not an extension-membership problem. Adding the two missed regexes would leave every other unenumerated client and loop form vulnerable.

### 2. PHP markup emission is modeled as an open-ended inline whitelist

The shared markup detector recognizes literal semantic markup, template composition, and a curated set of WordPress/WooCommerce helpers. The gate nevertheless interprets silence as proof that PHP/phtml emits no UI. Common WordPress renderers (`wp_nav_menu()`, `wp_login_form()`, `get_search_form()`, `comment_form()`) sit outside the vocabulary, as do broad families of output constructs and renderer conventions.

### 3. Template identity is too narrow and last-extension-only

`_MARKUP_LANGS` is the correct single source, but its inventory stops at eight formats. The general unrecognized-source safety net intentionally considers programming extensions only, so missing template formats fail silently. Compound formats such as Blade (`*.blade.php`) are additionally flattened to `php` by `_ext_of()`, which makes an inherently UI template pass through the mixed-PHP evidence gate.

## Candidate designs

1. **Patch the examples.** Add regexes for `client.Do`, indexed loops, four WordPress helpers, and seven extensions. Lowest churn, but preserves all three unsound assumptions and guarantees more false negatives.
2. **Make negative inference opt-in and centralize template/render surfaces.** Keep detectors as positive-evidence accelerators; permit a small-diff skip only when an agent explicitly declares its full criterion set exhaustively detectable. Default to conservative dispatch otherwise. Split mixed markup languages from pure template formats, add common server-template extensions and compound suffix recognition, and broaden PHP emission evidence to output constructs plus WordPress/core renderer families. This preserves efficient positive dispatch while making detector silence honest.
3. **Remove every evidence gate.** Always dispatch conditional agents for small diffs and every PHP change. Maximizes recall but discards useful, safe file-class distinctions and makes future complete detectors impossible to exploit.

Recommendation: option 2. It fixes the polarity error at the abstraction boundary, keeps the optimization available only under an explicit completeness contract, and strengthens accessibility recognition without pretending regex vocabularies are proofs of absence.

## Decision

The user approved option 2. The validated design is recorded in `.claude/docs/plans/2026-07-18-review-routing-negative-evidence-design.md`.

The implementation boundary was tightened after approval: obsolete language/check competence tables will be removed rather than left dormant. They exist only to authorize the unsound small-diff negative inference. Positive detector proof tables remain where they verify dispatch evidence, but claimed-language unions and negative-competence bindings will be deleted.

## Implementation outcome

- Small-diff detector silence is always conservative. An initially implemented boolean exhaustiveness assertion was removed after independent review showed that a declaration cannot prove criterion completeness; there is no negative-inference escape hatch.
- The obsolete competence model and its language unions were deleted, along with the now-unused per-agent `small_diff_exempt` escape hatch.
- Accessibility routing now separates mixed executable-markup languages from inherently UI-emitting templates. Centralized language and compound-suffix definitions feed domain scope, `is_template_file()` dispatch, and budget priority without a second planner vocabulary.
- PHP accessibility evidence now recognizes expression-shaped output constructs, conventional renderer method/static calls, and core WordPress navigation, form, comment, archive, widget, and related renderers. Plain prose and ordinary helper names remain negative controls.
- Regression coverage exercises unrecognized small Go and JavaScript forms, every supported template family (including `*.blade.php`), WordPress render surfaces, generic output, and false-positive controls.

## Verification

- Affected routing/scope suites after independent-review fixes: `1036 passed, 24 skipped`.
- Complete pirategoat-tools suite: `2658 passed, 24 skipped in 34.15s`.
- The full suite ran with bytecode generation disabled and pytest's cache provider disabled.
- Obsolete competence, claimed-language, opt-out, and abandoned generic-include symbols are absent from the plugin.

## Independent review follow-up

The completion review found four remaining abstraction gaps and all were addressed before handoff:

- Removed the unprovable exhaustiveness assertion and its locked-in skip tests.
- Added PHP short-echo evidence (`<?= ... ?>`).
- Split literal markup from call-shaped evidence, stripping comments and masking quoted prose before call matching; ambiguous `emit`/`output` methods now require view-like receivers.
- Added native Razor plus common Nunjucks and Jinja aliases to the centralized template inventory.
