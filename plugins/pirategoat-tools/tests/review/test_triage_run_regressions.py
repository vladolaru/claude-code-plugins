"""The three audited runs, replayed through the planner.

Each fixture is a real review run's planner input, captured once from a
clone that holds the range (`tests/helpers/triage_run_fixture.py`) and
never edited by hand — re-capture it with the helper's CLI. The integrity
test guards the fixtures; `TestAuditedRunContract` pins every agent's
status and reason on each run (so none of the keyword matches the three
audits named as noise can return), the quick-mode cohort, and the
dispatched signal counts. Keyword-match
reasons are compared with their sources and keywords sorted, so a
registry reorder cannot fail the contract by itself. The planner reports
at most five matches (three per source), so an agent with more matches
than that still shows the first ones in registry order; on the audited
runs only run 2's two WooCommerce agents cross that cap.
"""

import json
import re
import sys
from collections import Counter
from pathlib import Path

import pytest

TESTS_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(TESTS_DIR))

from helpers.triage_run_fixture import (
    FIXTURE_NAMES,
    FIXTURE_SCHEMA,
    load,
    replay,
    slice_patch,
    statuses,
)

RUN_1, RUN_2, RUN_3 = FIXTURE_NAMES

_KEYWORDS_RE = re.compile(r"^keywords matched \((.*)\)$")


def _match_pairs(reason):
    """The `(source, keyword)` pairs a keyword-match reason names; empty
    for every other reason. The reason format is
    `keywords matched (source: kw, kw; source: kw)`."""
    match = _KEYWORDS_RE.match(reason)
    if not match:
        return frozenset()
    pairs = set()
    for group in match.group(1).split("; "):
        source, _, keywords = group.partition(": ")
        for keyword in keywords.split(", "):
            pairs.add((source, keyword.strip()))
    return frozenset(pairs)


def _canonical(reason):
    """A keyword-match reason with sources and keywords sorted, so the pin
    names WHAT fired and not the registry's list order; any other reason
    verbatim."""
    pairs = _match_pairs(reason)
    if not pairs:
        return reason
    by_source = {}
    for source, keyword in pairs:
        by_source.setdefault(source, []).append(keyword)
    return "keywords matched (" + "; ".join(
        f"{source}: {', '.join(sorted(keywords))}"
        for source, keywords in sorted(by_source.items())
    ) + ")"


@pytest.fixture(scope="module")
def fixtures():
    return {name: load(name) for name in FIXTURE_NAMES}


@pytest.fixture(scope="module")
def plans(fixtures):
    return {
        name: {agent: (status, _canonical(reason))
               for agent, (status, reason) in statuses(replay(fixture)).items()}
        for name, fixture in fixtures.items()
    }


class TestFixtureIntegrity:
    @pytest.mark.parametrize("name", FIXTURE_NAMES)
    def test_fixture_integrity(self, fixtures, name):
        """The schema and keys the replay reads, a patch block and per-file
        stats for every changed file, and no session URL (the fixtures are
        committed, so a captured session link would be published)."""
        fixture = fixtures[name]
        assert fixture["schema"] == FIXTURE_SCHEMA
        assert fixture["name"] == name
        for key in (
            "git_range",
            "head_sha",
            "pr",
            "head_ref",
            "linked_issue_titles",
            "commit_log",
            "changed_files",
            "diffstat",
            "patch",
            "repository_text",
            "pr_template",
        ):
            assert key in fixture, key
        assert set(fixture["pr"]) == {"title", "body", "labels"}
        for path in fixture["changed_files"]:
            assert slice_patch(fixture["patch"], [path]).startswith("diff --git "), path
        assert set(fixture["diffstat"]["file_stats"]) >= set(fixture["changed_files"])
        assert "claude.ai/code/session" not in json.dumps(fixture)


DEFAULT = ("DISPATCH", "conditional (domain has files, no triage signal to skip)")
ALWAYS = ("DISPATCH", "always dispatch (domain has files)")
NO_PHP = ("SKIPPED_TRIAGE", "requires PHP source file")


def _skipped(domain):
    return ("SKIPPED", f"no files in {domain} domain")


# The keyword matches the audits named as noise, per run, as the (source,
# keyword stem) they fired on. None appears below, so a returning one fails
# test_every_agent_decision. Run 1: the PR template's checklist (security,
# password, escap*, user_data, performance, request, plugin, package,
# require, config) and a Co-Authored-By trailer (commits: auth). Run 2: the
# `plugin: woocommerce` label, a template line (require), and `address`
# inside "not addressed". Run 3: a trailer (auth), `token` inside the
# tokenized-* filenames, and `http` from a session URL. `address` is a
# legitimate whole-word commit match on run 3.
EXPECTED = {
    RUN_1: {
        "a11y-reviewer": ("DISPATCH", "keywords matched (commits: focusable, keyboard*; pr: aria)"),
        "api-contract-reviewer": ("DISPATCH", "keywords matched (pr: breaking)"),
        "architecture-reviewer": DEFAULT,
        "code-clarity-reviewer": ("DISPATCH", "docblock or API comment changes"),
        "code-reviewer": ALWAYS,
        "concurrency-reviewer": DEFAULT,
        "data-flow-privacy-reviewer": DEFAULT,
        "dead-code-reviewer": DEFAULT,
        "devils-advocate-reviewer": ("SKIPPED_TRIAGE", "below minimum addition threshold (4 < 50 lines)"),
        "docs-drift-reviewer": ("DISPATCH", "documentation file changes"),
        "e2e-tests-reviewer": _skipped("e2e-tests"),
        "ecosystem-integration-reviewer": NO_PHP,
        "go-tests-reviewer": _skipped("go-tests"),
        "history-insights-reviewer": DEFAULT,
        "js-tests-reviewer": ALWAYS,
        "patterns-reviewer": ALWAYS,
        "performance-reviewer": DEFAULT,
        "php-tests-reviewer": _skipped("php-tests"),
        "python-tests-reviewer": _skipped("python-tests"),
        "reference-integrity-reviewer": DEFAULT,
        "reliability-reviewer": DEFAULT,
        "rust-tests-reviewer": _skipped("rust-tests"),
        "security-reviewer": DEFAULT,
        "simplification-reviewer": ALWAYS,
        "toolchain-reviewer": _skipped("toolchain"),
        "woo-regression-reviewer": NO_PHP,
        "wp-architecture-reviewer": NO_PHP,
    },
    RUN_2: {
        "a11y-reviewer": DEFAULT,
        "api-contract-reviewer": ("DISPATCH", "keywords matched (commits: migration*; pr: backward*)"),
        "architecture-reviewer": ("DISPATCH", "new class, interface, trait, or enum definition"),
        "code-clarity-reviewer": ("DISPATCH", "keywords matched (diff: /**, @param, @return)"),
        "code-reviewer": ALWAYS,
        "concurrency-reviewer": ("DISPATCH", "keywords matched (commits: cach*, duplicate*, wpdb)"),
        "data-flow-privacy-reviewer": DEFAULT,
        "dead-code-reviewer": ("DISPATCH", "new source file(s) introduced (1)"),
        "devils-advocate-reviewer": ("DISPATCH", "keywords matched (commits: cach*, layer*, migration*; diff: database*; pr: compat*)"),
        "docs-drift-reviewer": ("DISPATCH", "keywords matched (commits: hooks, migrat*; pr: hook)"),
        "e2e-tests-reviewer": _skipped("e2e-tests"),
        "ecosystem-integration-reviewer": ("DISPATCH", "keywords matched (diff: add_action, add_filter)"),
        "go-tests-reviewer": _skipped("go-tests"),
        "history-insights-reviewer": DEFAULT,
        "js-tests-reviewer": _skipped("js-tests"),
        "patterns-reviewer": ALWAYS,
        "performance-reviewer": ("DISPATCH", "keywords matched (commits: bulk, cach*, wpdb; diff: query()"),
        "php-tests-reviewer": ALWAYS,
        "python-tests-reviewer": _skipped("python-tests"),
        "reference-integrity-reviewer": ("DISPATCH", "keywords matched (commits: extension*; diff: ::class; pr: compat*)"),
        "reliability-reviewer": ("DISPATCH", "keywords matched (commits: cach*, migration*)"),
        "rust-tests-reviewer": _skipped("rust-tests"),
        "security-reviewer": ("DISPATCH", "keywords matched (commits: sanitiz*; diff: escap*)"),
        "simplification-reviewer": ALWAYS,
        "toolchain-reviewer": _skipped("toolchain"),
        "woo-regression-reviewer": ("DISPATCH", "keywords matched (commits: wc-, wc_, woocommerce)"),
        "wp-architecture-reviewer": ("DISPATCH", "keywords matched (commits: hook*, wc, woocommerce; pr: backward*, filter*)"),
    },
    RUN_3: {
        "a11y-reviewer": ("DISPATCH", "keywords matched (commits: keyboard*; pr: screen reader*)"),
        "api-contract-reviewer": ("DISPATCH", "keywords matched (pr: backward*)"),
        "architecture-reviewer": ("DISPATCH", "keywords matched (commits: module*, refactor*)"),
        "code-clarity-reviewer": ("DISPATCH", "new function, method, or type definition"),
        "code-reviewer": ALWAYS,
        "concurrency-reviewer": ("DISPATCH", "keywords matched (commits: async, cach*, promise*; diff: await)"),
        "data-flow-privacy-reviewer": ("DISPATCH", "keywords matched (commits: address, charge*)"),
        "dead-code-reviewer": ("DISPATCH", "keywords matched (commits: refactor*)"),
        "devils-advocate-reviewer": ("DISPATCH", "keywords matched (commits: cach*, table*; diff: background; pr: compat*, retry)"),
        "docs-drift-reviewer": ("DISPATCH", "keywords matched (commits: hooks)"),
        "e2e-tests-reviewer": _skipped("e2e-tests"),
        "ecosystem-integration-reviewer": NO_PHP,
        "go-tests-reviewer": _skipped("go-tests"),
        "history-insights-reviewer": DEFAULT,
        "js-tests-reviewer": ALWAYS,
        "patterns-reviewer": ALWAYS,
        "performance-reviewer": ("DISPATCH", "keywords matched (commits: cach*, fetch*, slow)"),
        "php-tests-reviewer": _skipped("php-tests"),
        "python-tests-reviewer": _skipped("python-tests"),
        "reference-integrity-reviewer": ("DISPATCH", "keywords matched (commits: payment; pr: compat*, country)"),
        "reliability-reviewer": ("DISPATCH", "keywords matched (commits: cach*; diff: background, catch; pr: retry)"),
        "rust-tests-reviewer": _skipped("rust-tests"),
        "security-reviewer": DEFAULT,
        "simplification-reviewer": ALWAYS,
        "toolchain-reviewer": _skipped("toolchain"),
        "woo-regression-reviewer": NO_PHP,
        "wp-architecture-reviewer": NO_PHP,
    },
}

# Quick mode keys on the default dispatch signal, so run 1 — where the
# template text used to "confirm" four agents — now sheds them.
EXPECTED_QUICK_COUNTS = {RUN_1: 12, RUN_2: 18, RUN_3: 16}

EXPECTED_DISPATCH_SIGNAL_COUNTS = {
    RUN_1: {"always": 4, "check": 2, "default": 9, "keyword": 2},
    RUN_2: {"always": 4, "check": 2, "default": 3, "keyword": 12},
    RUN_3: {"always": 4, "check": 1, "default": 2, "keyword": 11},
}


class TestAuditedRunContract:
    """The planner's decisions on the three audited runs, in full."""

    @pytest.mark.parametrize("name", FIXTURE_NAMES)
    def test_every_agent_decision(self, plans, name):
        assert plans[name] == EXPECTED[name]

    @pytest.mark.parametrize("name", FIXTURE_NAMES)
    def test_quick_mode_cohort(self, fixtures, name):
        plan = statuses(replay(fixtures[name], quick=True))
        assert sum(1 for status, _ in plan.values() if status == "DISPATCH") == EXPECTED_QUICK_COUNTS[name]

    @pytest.mark.parametrize("name", FIXTURE_NAMES)
    def test_dispatched_signal_counts(self, fixtures, name):
        plan = replay(fixtures[name])
        actual = Counter(
            agent["signal"]
            for agent in plan["agents"]
            if agent["status"] == "DISPATCH"
        )
        assert dict(sorted(actual.items())) == EXPECTED_DISPATCH_SIGNAL_COUNTS[name]
