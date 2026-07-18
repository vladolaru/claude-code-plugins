# Conservative Routing for Mixed Markup Languages

Last updated: 2026-07-19 00:12

## Problem

Accessibility scope correctly includes PHP and PHTML because either language may emit rendered UI. The planner then applies `evidence_gated_extensions`: if every in-scope production file uses one of those extensions and no configured keyword or structural check matches, it returns `SKIPPED_TRIAGE`.

That skip is not supported by the available evidence. The shared markup detector is a finite positive recognizer over literal markup, selected WordPress and WooCommerce helpers, explicit output syntax, template syntax, and renderer-method conventions. PHP can compose UI through arbitrary functions, callbacks, hooks, shortcodes, framework APIs, and project-specific abstractions. A silent scan proves only that no known positive pattern matched; it cannot prove that the change emits no UI.

## Decision

Remove `evidence_gated_extensions` as a routing mechanism. Mixed executable/markup languages will use the same conservative conditional default as other domains: positive keyword and structural evidence dispatches immediately with a specific reason, while detector silence falls through to `DISPATCH` because the domain still contains relevant files.

The planner will no longer expose a declarative scoped gate capable of promoting detector silence into negative evidence. A future negative optimization would require an executable, exhaustive proof of irrelevance and a conservative result for unknown cases; configuration alone is insufficient.

## Boundaries

- Keep PHP and PHTML in the accessibility domain.
- Keep `has_markup_changes` and its shared classifier. They remain useful positive evidence and accessibility budget-priority signals.
- Keep pure template and stylesheet identity as immediate positive evidence.
- Keep the existing test-only filter, so a PHP/PHTML diff containing only tests remains skipped.
- Keep non-markup backend languages outside the accessibility domain.
- Do not add the reported WordPress renderer names merely to make the regression pass; arbitrary token-silent PHP/PHTML changes must exercise the conservative fallback.
- Do not alter `require_triage_keyword_match`, whose repository/applicability contract is separate from the mixed-markup gate reported here.

## Runtime and configuration changes

`plugins/pirategoat-tools/scripts/review/plan_dispatch.py` will delete the scoped evidence-gate layer, its documentation, and its extension-set bookkeeping. `plugins/pirategoat-tools/scripts/review/agent_registry.json` will remove the `a11y-reviewer.evidence_gated_extensions` field and revise the focus text so it no longer promises a positive-evidence requirement for server-rendered files.

Comments in `scope.py` and plugin architecture documentation will describe PHP/PHTML as conservatively routed mixed-markup languages, not evidence-gated languages. No external output schema changes.

## Testing

Behavior-level regressions will be written and observed failing before the runtime change:

- PHP/PHTML changes containing arbitrary, unrecognized composition calls dispatch accessibility review through the conservative default.
- Ordinary backend PHP/PHTML changes also dispatch, pinning the deliberate recall-first trade-off rather than leaving it accidental.
- A PHP production change accompanied by an unrelated frontend test still dispatches; test files cannot manufacture or suppress evidence.
- PHP/PHTML test-only changes remain skipped by the existing test-only filter.
- Known markup, template, and style evidence continues to dispatch with its specific positive reason.

Run the required `test_plan_dispatch.py` and `test_criteria_coverage.py` suites, then the complete pirategoat-tools suite. Verify release metadata, documentation consistency, repository hygiene, and the final diff before committing the runtime fix.

## Release handling

This corrects the unpushed `1.107.0` routing work on the current feature branch. Amend the existing changelog entry rather than incrementing the version again; `.claude-plugin/marketplace.json` remains at `1.107.0` unless branch/remote inspection shows that release has already been published.
