# Conservative Reviewer Routing Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use $subagent-driven-development (recommended) or $executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Prevent silent reviewer-routing false negatives by making negative inference explicitly exhaustive, broadening server-rendered accessibility evidence, and centralizing template identity.

**Architecture:** Positive regex and structural matches continue to dispatch reviewers immediately. Detector silence can skip a small diff only when the agent explicitly claims exhaustive coverage of its complete criteria; no existing agent makes that claim, so obsolete per-language negative-competence machinery is removed. Accessibility scope, template triage, and budget priority share template and markup classifiers from `scope.py`.

**Tech Stack:** Python 3 standard library, pytest, JSON agent registry, Markdown project documentation.

---

## File map

- `plugins/pirategoat-tools/scripts/review/plan_dispatch.py`: owns conditional dispatch polarity and consumes the shared template classifier.
- `plugins/pirategoat-tools/scripts/review/agent/scope.py`: owns markup evidence, template formats, compound template suffixes, domain matching, and accessibility budget priority.
- `plugins/pirategoat-tools/scripts/review/agent_registry.json`: removes obsolete small-diff exemption flags now that skipping is explicit opt-in.
- `plugins/pirategoat-tools/tests/review/test_plan_dispatch.py`: behavior-level routing regressions and registry/check meta-contracts.
- `plugins/pirategoat-tools/tests/review/test_criteria_coverage.py`: keeps positive detector matrices while removing the invalid negative-coverage binding.
- `plugins/pirategoat-tools/tests/review/agent/test_scope.py`: shared markup/template classifier and budget-ordering regressions.
- `plugins/pirategoat-tools/tests/review/agent/test_scope_routing.py`: domain-routing coverage for server templates.
- `plugins/pirategoat-tools/AGENTS.md`: documents the new negative-inference contract.
- `plugins/pirategoat-tools/CHANGELOG.md`: amends the unpushed `1.107.0` release entry.

### Task 1: Make small-diff negative inference explicitly exhaustive

**Files:**
- Modify: `plugins/pirategoat-tools/tests/review/test_plan_dispatch.py`
- Modify: `plugins/pirategoat-tools/tests/review/test_criteria_coverage.py`
- Modify: `plugins/pirategoat-tools/scripts/review/plan_dispatch.py`
- Modify: `plugins/pirategoat-tools/scripts/review/agent_registry.json`
- Modify: `plugins/pirategoat-tools/AGENTS.md`
- Modify: `plugins/pirategoat-tools/CHANGELOG.md`

- [ ] **Step 1: Write failing routing tests for partial detector silence**

Add behavior tests that use the real performance and reliability configurations:

```python
@pytest.mark.parametrize("agent", ["performance-reviewer", "reliability-reviewer"])
def test_go_client_do_dispatches_when_partial_detector_is_silent(self, agents, agent):
    filepath = "internal/client.go"
    status, _ = triage_conditional_agent(
        agent,
        agents[agent],
        [filepath],
        "tidy client code",
        self._small_diffstat(filepath),
        diff_text="+ response, err := client.Do(req)",
    )
    assert status == "DISPATCH"

def test_indexed_javascript_loop_dispatches_when_partial_detector_is_silent(self, agents):
    filepath = "src/items.js"
    status, _ = triage_conditional_agent(
        "performance-reviewer",
        agents["performance-reviewer"],
        [filepath],
        "tidy item processing",
        self._small_diffstat(filepath),
        diff_text="+ for (let i = 0; i < items.length; i++) { consume(items[i]); }",
    )
    assert status == "DISPATCH"
```

Keep the small-diff optimization behavior covered with a synthetic configuration that explicitly includes `"small_diff_triage_exhaustive": True`. Update existing synthetic skip tests to opt in, and change registry-backed expectations from skip to conservative dispatch.

- [ ] **Step 2: Run the new tests and verify RED**

Run:

```bash
PYTHONDONTWRITEBYTECODE=1 pytest -p no:cacheprovider \
  plugins/pirategoat-tools/tests/review/test_plan_dispatch.py::TestSmallDiffPolarity -v
```

Expected: the Go `client.Do(req)`, indexed JavaScript loop, and unflagged small-diff expectations fail because the current gate treats supported extensions as complete negative evidence.

- [ ] **Step 3: Remove obsolete competence machinery and add the exhaustive opt-in**

Delete `_DETECTOR_COVERED_LANGS`, `_has_uncovered_source_files()`, `_UNIVERSAL`, `_STRUCTURAL`, `_HTTP_CLIENT_CLAIMED_LANGS`, `_ITERATION_CLAIMED_LANGS`, `_CHECK_COMPETENCE`, and `_agent_checks_competent()`. Reduce each `_CHECK_SPECS` record to the active `reads_diff` field:

```python
_CHECK_SPECS = {
    "has_new_functions": {"reads_diff": True},
    # ... retain every existing check and reads_diff value ...
}
```

Gate small-diff skips only behind the explicit complete-criteria contract:

```python
if config.get("small_diff_triage_exhaustive"):
    changed_lines = _count_in_scope_non_test_changed_lines(domain_files, diffstat)
    if changed_lines is not None and changed_lines < SMALL_DIFF_THRESHOLD:
        return "SKIPPED_TRIAGE", (
            f"small change ({changed_lines} lines in scope), "
            "exhaustive triage found no positive signal"
        )
```

Update `_validate_triage_checks()` to reject a non-boolean `small_diff_triage_exhaustive` value. Remove `small_diff_exempt` from the security and history registry entries because it no longer controls any path.

- [ ] **Step 4: Remove tests and documentation that certify partial vocabularies as complete**

Keep positive form tests for HTTP clients, iteration, and the criteria language matrix, but simplify proof tables to store detector inputs rather than extension claims. Delete claimed-set equality tests and the `_DETECTOR_COVERED_LANGS` matrix binding. Update `_CHECK_SPECS` meta-tests to expect only `{"reads_diff"}`.

Replace the AGENTS.md competence-model text with this contract:

```markdown
**Negative-inference contract:** triage checks and keywords are positive-evidence detectors. Their silence never proves a criterion absent unless a conditional agent explicitly sets `small_diff_triage_exhaustive: true`, certifying that its complete `triage_criteria` are exhaustively detected across every file type in its domain. The default is conservative dispatch. Representative form tables prove positive recognition only and must never authorize negative inference.
```

Amend the `1.107.0` changelog so it no longer claims all small diffs require positive evidence; state that only explicitly exhaustive agents may skip on detector silence.

- [ ] **Step 5: Run the dispatch suites and verify GREEN**

Run:

```bash
PYTHONDONTWRITEBYTECODE=1 pytest -p no:cacheprovider \
  plugins/pirategoat-tools/tests/review/test_plan_dispatch.py \
  plugins/pirategoat-tools/tests/review/test_criteria_coverage.py -v
```

Expected: all tests pass with no warnings or collection artifacts.

- [ ] **Step 6: Commit the routing-polarity fix**

Stage only Task 1 files and commit with a Conventional Commit message describing the old false-negative behavior, the criterion-completeness problem, and the conservative default.

### Task 2: Centralize server-template and PHP render evidence

**Files:**
- Modify: `plugins/pirategoat-tools/tests/review/agent/test_scope.py`
- Modify: `plugins/pirategoat-tools/tests/review/agent/test_scope_routing.py`
- Modify: `plugins/pirategoat-tools/tests/review/test_plan_dispatch.py`
- Modify: `plugins/pirategoat-tools/scripts/review/agent/scope.py`
- Modify: `plugins/pirategoat-tools/scripts/review/plan_dispatch.py`
- Modify: `plugins/pirategoat-tools/AGENTS.md`
- Modify: `plugins/pirategoat-tools/CHANGELOG.md`

- [ ] **Step 1: Write failing template-classifier tests**

Add parameterized coverage for the common server-template family:

```python
@pytest.mark.parametrize("filepath", [
    "views/cart.ejs", "templates/page.liquid", "views/page.njk",
    "templates/page.jinja", "templates/page.jinja2", "views/index.jsp",
    "views/index.jspx", "Views/Cart.cshtml", "Views/Cart.vbhtml",
    "templates/email.tmpl", "templates/email.tpl", "views/page.ftl",
    "views/page.vm", "views/page.haml", "views/page.slim",
    "resources/views/cart.blade.php",
])
def test_a11y_domain_matches_common_server_templates(filepath):
    assert count_files_in_domain([filepath], "a11y") == 1
    assert review_scope.is_template_file(filepath)
```

Add a dispatch assertion proving `*.blade.php` fires `has_template_files` before the PHP evidence gate, and add a budget regression where a small token-free Blade template is reviewed before a 2,100-line backend PHP file.

- [ ] **Step 2: Write failing PHP renderer-family tests**

Add table-driven positive evidence for core WordPress renderers and general output surfaces:

```python
@pytest.mark.parametrize("line", [
    "+ wp_nav_menu( $args );",
    "+ wp_login_form( $args );",
    "+ get_search_form();",
    "+ comment_form( $args );",
    "+ wp_list_comments( $args );",
    "+ wp_page_menu( $args );",
    "+ dynamic_sidebar( 'primary' );",
    "+ the_widget( WC_Widget_Cart::class );",
    "+ echo $renderer->render( $context );",
    "+ $view->display( $context );",
])
def test_php_render_surfaces_are_markup(line):
    assert review_scope.patch_has_markup_tokens(line)
```

Add an end-to-end a11y dispatch test for a large PHP-only `wp_nav_menu()` change. Keep negative lookalikes such as `$data = render_totals($order)` covered so renderer conventions require method/output context rather than arbitrary function names.

- [ ] **Step 3: Run the new scope and dispatch tests and verify RED**

Run:

```bash
PYTHONDONTWRITEBYTECODE=1 pytest -p no:cacheprovider \
  plugins/pirategoat-tools/tests/review/agent/test_scope.py::TestMarkupTokenEdgeCases \
  plugins/pirategoat-tools/tests/review/agent/test_scope.py::TestMarkupEvidenceBudgetPriority \
  plugins/pirategoat-tools/tests/review/test_plan_dispatch.py::TestA11yMarkupGatedDispatch \
  plugins/pirategoat-tools/tests/review/test_plan_dispatch.py::TestCountFilesInDomain -v
```

Expected: new helper, template-format, Blade triage, and Blade budget assertions fail against the current finite vocabulary and last-extension classifier.

- [ ] **Step 4: Implement the shared template classifier**

In `scope.py`, separate mixed executable markup from pure template extensions:

```python
_MIXED_MARKUP_LANGS = ["php", "phtml"]
_TEMPLATE_LANGS = [
    "html", "htm", "xhtml", "twig", "mustache", "hbs", "erb",
    "ejs", "liquid", "njk", "jinja", "jinja2", "jsp", "jspx",
    "cshtml", "vbhtml", "tmpl", "tpl", "gsp", "ftl", "vm",
    "haml", "slim",
]
_TEMPLATE_SUFFIXES = ("blade.php",)
_MARKUP_LANGS = [*_MIXED_MARKUP_LANGS, *_TEMPLATE_LANGS]

def is_template_file(path: str) -> bool:
    lowered = path.lower()
    extension = lowered.rpartition(".")[2]
    return extension in _TEMPLATE_LANGS or any(
        lowered.endswith(f".{suffix}") for suffix in _TEMPLATE_SUFFIXES
    )
```

Allow domain specifications to provide an `include_file` predicate in `filter_domain()` and set the a11y domain's predicate to `is_template_file`, while retaining its extension regex for normal fast matching.

Replace `plan_dispatch.py`'s `_TEMPLATE_EXTENSIONS` and last-extension filter with `_scope_mod.is_template_file(f)`.

- [ ] **Step 5: Implement centralized PHP render surfaces**

Extend the single `MARKUP_TOKEN_PATTERNS` tuple with exact WordPress renderer families, explicit PHP output constructs, and method-shaped renderer conventions:

```python
re.compile(
    r"\b(wp_nav_menu|wp_login_form|get_search_form|comment_form|"
    r"wp_list_comments|wp_page_menu|wp_link_pages|wp_loginout|wp_register|"
    r"wp_meta|wp_get_archives|wp_tag_cloud|dynamic_sidebar|the_widget)\s*\("
),
re.compile(r"\b(echo|print|printf|vprintf)\b"),
re.compile(r"(?:->|::)(render|display|output|emit)(_[a-z0-9_]+)?\s*\("),
```

Use `is_template_file()` as inherent UI evidence in accessibility budget priority alongside style files and markup-token matches.

- [ ] **Step 6: Run required scope and dispatch suites and verify GREEN**

Run:

```bash
PYTHONDONTWRITEBYTECODE=1 pytest -p no:cacheprovider \
  plugins/pirategoat-tools/tests/review/agent/test_scope.py \
  plugins/pirategoat-tools/tests/review/agent/test_scope_routing.py \
  plugins/pirategoat-tools/tests/review/test_plan_dispatch.py \
  plugins/pirategoat-tools/tests/review/test_criteria_coverage.py -v
```

Expected: all tests pass, including the full template-extension and render-helper tables.

- [ ] **Step 7: Update documentation and release notes**

Update the scope language-group documentation in `plugins/pirategoat-tools/AGENTS.md` to name `_TEMPLATE_LANGS`, `_TEMPLATE_SUFFIXES`, and `is_template_file()` as the compound-template source of truth. Amend `1.107.0` with the broadened renderer and template coverage. Do not bump `.claude-plugin/marketplace.json`; `1.107.0` remains unpushed and this is a same-impact correction to that release.

- [ ] **Step 8: Commit the accessibility coverage fix**

Stage only Task 2 files and commit with a Conventional Commit message focused on server-rendered accessibility routing.

### Task 3: Verify the repository and remove execution residue

**Files:**
- Modify only if verification exposes a defect in the planned files.

- [ ] **Step 1: Run the complete pirategoat-tools suite**

Run without Python bytecode or pytest cache output:

```bash
PYTHONDONTWRITEBYTECODE=1 pytest -p no:cacheprovider plugins/pirategoat-tools/tests/ -v
```

Expected: all pirategoat-tools tests pass.

- [ ] **Step 2: Run repository hygiene checks**

Run:

```bash
git diff --check
git status --short --untracked-files=all
git log -3 --oneline
```

Expected: no whitespace errors; no unstaged, staged, or untracked residue; only intentional commits at the branch tip.

- [ ] **Step 3: Audit for obsolete symbols and contradictory docs**

Run:

```bash
rg -n "_DETECTOR_COVERED_LANGS|_CHECK_COMPETENCE|_HTTP_CLIENT_CLAIMED_LANGS|_ITERATION_CLAIMED_LANGS|small_diff_exempt" \
  plugins/pirategoat-tools AGENTS.md
```

Expected: no matches. Also verify every template classifier consumer routes through `is_template_file()` and every runtime behavior change is present under changelog version `1.107.0`.

- [ ] **Step 4: Report the exact git range**

Use `02e6650...HEAD` as the full feature-branch range, and separately identify the first commit from this fix session (`8918728`) through the final commit so the user can inspect only the review-feedback resolution.
