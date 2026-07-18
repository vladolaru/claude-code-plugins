# Conservative Negative Evidence for Reviewer Routing

Last updated: 2026-07-18 22:18

> **Superseded implementation detail:** The explicit exhaustiveness declaration below was removed during final review because a configuration assertion cannot prove semantic completeness. The shipped design permits no small-diff negative inference; detector silence always dispatches unless a separate explicit evidence gate applies. The original approved reasoning remains below as decision history.

## Problem

The dispatch planner currently promotes representative detector coverage into proof of absence. A detector that recognizes one HTTP-client or loop form in a language makes every silent scan in that language eligible for a small-diff skip. Accessibility routing repeats the mistake by treating a finite PHP-render-helper vocabulary as proof that a PHP diff emits no markup. Template scope has a related closed-world assumption: common template formats outside `_MARKUP_LANGS` disappear before markup detection and do not trigger the programming-language safety net.

## Design

### Negative inference is an explicit capability

Triage checks remain positive-evidence detectors. Their existing language coverage describes where a positive match is meaningful; it does not authorize a negative inference.

The small-diff gate will require an explicit agent-level exhaustive-triage declaration before it may convert detector silence into `SKIPPED_TRIAGE`. The default is conservative dispatch. The declaration covers the agent's complete configured `triage_criteria`, including keyword-only criteria, rather than individual extensions or sample regex forms. Existing agents will not claim completeness because their semantic criteria are intentionally broader than their deterministic vocabularies.

The planner will retain language/check competence guards as additional constraints for any future exhaustive agent. A meta-test will bind the opt-in field to conditional-agent configuration and prove that supported-language membership alone cannot authorize a skip.

### Markup-emission surfaces are centralized and broadened

`scope.py` remains the single source for markup evidence. Its detector will distinguish:

- literal semantic and interactive markup;
- template composition;
- explicit PHP output constructs;
- WordPress/WooCommerce rendering helpers and helper families;
- conventional render/display/output method calls.

The WordPress surface will cover core navigation, login, search, comments, lists, widgets, pagination, editor, dropdown, and form helpers. Tests will be table-driven so future helper additions extend one behavioral contract rather than scattered regex assertions.

Detector matches are still positive evidence, not a claim that every possible custom PHP renderer can be recognized.

### Template identity has one reusable classifier

The markup-language source of truth will separate mixed executable markup (`php`, `phtml`) from inherently-UI template formats. Common server-template extensions will include EJS, Liquid, Nunjucks/Jinja, JSP, Razor, generic template formats, FreeMarker, Velocity, Haml/Slim, and existing HTML/Twig/Mustache/Handlebars/ERB formats.

A shared template-file classifier will handle both simple extensions and compound suffixes such as `*.blade.php`. The accessibility domain, `has_template_files` triage check, and accessibility budget prioritization will consume the same classifier, preventing scope, dispatch, and reviewer-context ordering from drifting.

Pure template files are positive accessibility evidence by identity. Mixed PHP/phtml remains evidence-gated, but the broadened markup detector recognizes core renderer surfaces.

## Routing flow

1. Scope classifies changed files from centralized language groups and template suffix rules.
2. Positive keyword and triage-check matches dispatch immediately.
3. PHP/phtml-only accessibility changes may skip only after the shared markup scan finds no known render surface.
4. The generic small-diff gate may skip only for an explicitly exhaustive agent whose detector competence also covers every scoped source file.
5. All other detector silence falls through to conservative dispatch.

## Testing

Tests will be written and observed failing before implementation:

- Go `client.Do(req)` dispatches performance and reliability despite detector silence.
- Indexed JavaScript loops dispatch performance despite detector silence.
- A synthetic explicitly exhaustive agent can still skip an irrelevant small change.
- Representative WordPress core renderer families dispatch PHP accessibility review.
- Plain backend PHP remains eligible for the scoped accessibility evidence skip.
- Common simple template extensions and `*.blade.php` match accessibility scope, fire template evidence, and receive budget priority.
- Registry/spec meta-tests reject accidental schema drift.

After targeted red-green cycles, run the required `test_plan_dispatch.py`, `test_criteria_coverage.py`, `test_scope.py`, and `test_scope_routing.py` suites, followed by the complete pirategoat-tools suite.

## Release and compatibility

This is a conservative routing correction. It changes no external schema. Because version `1.107.0` is present only on the unpushed feature branch and this fix corrects the same release's evidence-routing behavior, the changelog entry will be amended without another version bump.
