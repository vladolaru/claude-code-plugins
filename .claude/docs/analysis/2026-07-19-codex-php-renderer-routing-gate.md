Last updated: 2026-07-19 00:21

> **Prompt:** "Fix them by going at the root cause and fix entire classes of issues. Commit when done"
>
> **Follow-up:** "Make sure no leftovers remain in the codebase."

# PHP renderer routing-gate investigation

## Scope

Investigate the review finding that accessibility routing treats a silent PHP/PHTML markup scan as negative evidence, then correct the underlying evidence policy rather than enumerating only the reported WordPress renderer calls. Verify the behavior through the deterministic dispatch suites.

## Investigation log

- The working tree started clean on `feat/review-dispatch-verification-hardening`, eight commits ahead of `main`.
- The reported decision point is in `plugins/pirategoat-tools/scripts/review/plan_dispatch.py`; implementation and tests have not yet been changed.
- The active `a11y-reviewer` registry configuration includes every PHP/PHTML file in its domain, then applies `evidence_gated_extensions` to skip PHP/PHTML-only diffs after keywords, markup tokens, style identity, and template identity remain silent.
- Reproduced the review exactly through `triage_conditional_agent()`: `get_header()`, `get_footer()`, `comments_template()`, and a project-specific `my_plugin_render_shell()` each return `SKIPPED_TRIAGE` with `all domain files are evidence-gated types (php); no keyword or check matched`.
- The detector already contains a growing finite list of literal elements, attributes, WordPress/WooCommerce helpers, PHP output constructs, and method-name conventions. Commit `fe29ea3` expanded that list specifically to address prior misses while preserving the same negative gate.
- Existing tests encode both sides of the policy: known renderer spellings dispatch, while token-silent backend PHP and PHP plus an unrelated test file skip. No test covers an unknown composition surface because the policy has no closed set from which such completeness could be proven.

## Root cause

`has_markup_changes` is a positive recognizer over an open-ended language: PHP can invoke arbitrary functions, hooks, shortcodes, template loaders, callbacks, and framework APIs that emit UI. `evidence_gated_extensions` promotes silence from that finite recognizer into proof that the entire PHP/PHTML change is accessibility-irrelevant. Adding `get_header()` or the three reported calls would improve recall for those spellings but preserve the invalid polarity for every unlisted renderer.

The generic scoped-gate abstraction is therefore unsound for mixed executable/markup languages unless it consumes an exhaustive negative proof. No such proof exists in the current planner. This is the same detector-silence class already removed from the small-diff gate in `9ab110d`, but retained under a different configuration field.

## Candidate designs

1. Expand the PHP renderer regex table. Low churn, but fixes only enumerated spellings and guarantees repeat findings.
2. Remove `evidence_gated_extensions` as a negative-routing mechanism. Keep PHP/PHTML in the accessibility domain, keep markup checks as positive dispatch reasons and budget-priority hints, and let silent mixed-markup diffs reach the conservative conditional default. This fixes arbitrary current and future composition surfaces at the cost of dispatching accessibility review for backend-only PHP/PHTML diffs.
3. Infer UI identity from paths or parse PHP call graphs. Path conventions are incomplete across plugins/themes/frameworks; exhaustive interprocedural rendering proof is outside the deterministic planner's scope and would still need a conservative unknown result.

Recommendation: option 2. It is the only current design whose negative decisions match the available evidence. The extra PHP/PHTML dispatches are an explicit recall-first trade-off, while test-only filtering and the domain boundary still prevent unrelated languages and test-only diffs from dispatching accessibility review.

## Decision

The user approved option 2. The validated design is recorded in `.claude/docs/plans/2026-07-19-php-renderer-routing-polarity-design.md`.

The implementation boundary includes removal of the generic `evidence_gated_extensions` configuration surface, runtime branch, gate-specific synthetic tests, and stale documentation. Leaving the field dormant would preserve an attractive path back to the same invalid negative-inference class.

## Execution and verification

- RED: the focused mixed-markup routing class produced seven expected failures. `get_header()`, `get_footer()`, `comments_template()`, a project-specific PHTML renderer, backend PHP, a PHP loop, and PHP plus an unrelated test all returned `SKIPPED_TRIAGE` through the scoped gate.
- GREEN: after deleting the generic gate and its registry field, the focused class passed all 27 tests.
- Targeted planner and executable-criteria suites: 687 passed, 24 skipped.
- Full pirategoat-tools suite: 2,660 passed, 24 skipped in 39.20 seconds.
- Residue audit found and removed additional gate assumptions in dispatch integration tests, scope tests, comments, architecture documentation, registry focus, and release notes. Remaining evidence-gate references describe separate active policies only.
