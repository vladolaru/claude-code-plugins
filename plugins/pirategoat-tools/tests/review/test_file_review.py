"""The run-level file review — `manifest_sections.aggregate_file_review()`.

Moved here with the function: it reads the scope-summary sidecars and the
finalized review documents, never the reconciliation context, and the
reconciliator never read it. It lives beside the coverage manifest that
reads the same artifacts over a different population.
"""

import json
import os
import sys
from pathlib import Path

import pytest

TESTS_DIR = Path(__file__).resolve().parent.parent
PLUGIN_ROOT = TESTS_DIR.parent
SCRIPTS_DIR = PLUGIN_ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))
sys.path.insert(0, str(TESTS_DIR))

from helpers.review_fixtures import (
    canonical_assignment,
    canonical_review_document,
)
from review import manifest_sections
from review.manifest_sections import aggregate_file_review
from review.reviewer_lifecycle import review_paths, scope_summary_path
from review.reviewer_names import derive_reviewer_name


def _write_summary(
    output_dir, agent, files_with_diffs, budget_exceeded, *, domain=None,
    list_only=None, in_scope=None,
):
    """Write one agent's scope-summary sidecar under its real filename.

    Keyed on the AGENT name rather than a hand-spelled filename so every
    coverage test addresses the sidecar the way the aggregator does;
    `domain` appends the secondary-summary suffix that adapter and
    multi-domain agents emit.
    """
    reviewer = derive_reviewer_name(agent)
    path = scope_summary_path(output_dir, reviewer, domain)
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w") as f:
        json.dump({
            "schema": 3,
            "inline_diff_files": files_with_diffs,
            "review_claimable_files": budget_exceeded,
            "list_only_files": list(list_only or []),
            # Real sidecars publish this in every mode; the helper defaults
            # it to the union of what was passed so ordinary-mode fixtures
            # stay honest without every caller restating their scope.
            "routing_files": (
                list(in_scope) if in_scope is not None
                else sorted(
                    set(files_with_diffs)
                    | set(budget_exceeded)
                    | set(list_only or [])
                )
            ),
        }, f)


def _write_review(output_dir, stem, claims, claimable=None):
    """Write <stem>.json — the real filename an agent's review carries.

    Takes the review STEM, not the agent name: several tests exist to pin
    the stem-derivation rule itself, so deriving it here would hide the
    thing under test.

    The document is the canonical finalized one, so the reviewed-file
    partition consumers read is embedded in it: `claimable` defaults to the
    claims (nothing left unclaimed) and widens when a test needs unclaimed
    review files.
    """
    reviewer = stem.removesuffix("-review")
    payload = canonical_review_document(
        reviewer,
        review_claimable_files=list(claims if claimable is None else claimable),
        reviewed_file_claims=list(claims),
    )
    path = Path(review_paths(output_dir, reviewer).final)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w") as f:
        json.dump(payload, f)


def _write_assignment(output_dir, reviewer, claimable, *, inline_count=0):
    payload = canonical_assignment(
        reviewer,
        review_claimable_files=claimable,
        inline_diff_file_count=inline_count,
    )
    path = Path(review_paths(output_dir, reviewer).assignment)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w") as f:
        json.dump(payload, f)
    return payload


class TestAggregateReviewedFiles:
    """aggregate_file_review() reads *-scope-summary*.json sidecars."""

    def test_returns_none_without_summaries(self, tmp_path):
        assert aggregate_file_review(str(tmp_path)) is None
        # A missing directory is the same absence.
        assert aggregate_file_review(str(tmp_path / "nope")) is None

    def test_reports_inline_receipt_and_each_agents_unclaimed_work(
        self, tmp_path
    ):
        _write_summary(
            str(tmp_path), "security-reviewer",
            ["src/a.php"], ["src/starved.php", "src/b.php"],
        )
        _write_summary(
            str(tmp_path), "code-reviewer",
            ["src/b.php"], ["src/starved.php"],
        )
        cov = aggregate_file_review(str(tmp_path))
        assert cov["scope_reporting_agent_count"] == 2
        assert cov["agents_receiving_inline_diff_by_file"] == {
            "src/a.php": ["security"],
            "src/b.php": ["code"],
        }
        assert cov["agents_with_unclaimed_review_by_file"] == {
            "src/b.php": ["security"],
            "src/starved.php": [
            "code", "security",
            ],
        }

    def test_fallback_unclaimed_honors_the_inline_wins_partition(
        self, tmp_path
    ):
        """A multi-domain reviewer that never finalized: a path inline in
        one domain's sidecar and claimable in another's is inline, not
        unclaimed — the same partition `bootstrap.partition_scope_paths`
        applies to the assignment. Recording it in both maps publishes one
        reviewer as having both received and never reviewed the file."""
        _write_summary(
            str(tmp_path), "security-reviewer",
            ["src/shared.php"], [],
        )
        _write_summary(
            str(tmp_path), "security-reviewer",
            [], ["src/shared.php", "src/starved.php"],
            domain="config-ops",
        )
        cov = aggregate_file_review(str(tmp_path))
        assert cov["agents_receiving_inline_diff_by_file"] == {
            "src/shared.php": ["security"],
        }
        assert cov["agents_with_unclaimed_review_by_file"] == {
            "src/starved.php": ["security"],
        }

    def test_malformed_summary_skipped(self, tmp_path):
        broken = Path(scope_summary_path(tmp_path, "broken"))
        broken.parent.mkdir(parents=True, exist_ok=True)
        broken.write_text("{not json")
        _write_summary(
            str(tmp_path), "security-reviewer",
            ["src/a.php"], [],
        )
        cov = aggregate_file_review(str(tmp_path))
        assert cov["scope_reporting_agent_count"] == 1

    def test_claims_come_from_the_final_document_not_the_sidecar(
        self, tmp_path
    ):
        """Finalization already proved the document's partition coherent;
        re-deriving it from the sidecar can only disagree with it."""
        claimable = ["src/a.py", "src/b.py"]
        _write_summary(str(tmp_path), "security-reviewer", [], claimable)
        _write_review(
            str(tmp_path), "security-review",
            claims=["src/a.py"], claimable=claimable,
        )
        # A sidecar that disagrees must not be consulted after finalization.
        _write_assignment(str(tmp_path), "security", ["src/zzz.py"])

        cov = aggregate_file_review(str(tmp_path))

        assert cov["agents_claiming_review_by_file"] == {
            "src/a.py": ["security"]
        }
        assert cov["agents_with_unclaimed_review_by_file"] == {
            "src/b.py": ["security"]
        }

    def test_malformed_claims_credit_nothing(self, tmp_path):
        """A document whose claim list is not a list of paths is not a
        finalized review: it credits nothing, and every review-claimable
        file its scope summary reported stays visible as unclaimed work."""
        claimable = ["src/read.php", "src/unread.php"]
        _write_summary(str(tmp_path), "security-reviewer", [], claimable)
        review = canonical_review_document(
            "security",
            review_claimable_files=claimable,
            reviewed_file_claims=["src/read.php"],
        )
        review["reviewed_file_claims"] = ["src/read.php", None]
        path = Path(review_paths(tmp_path, "security").final)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(review))

        cov = aggregate_file_review(str(tmp_path))

        assert cov["agents_claiming_review_by_file"] == {}
        assert cov["agents_with_unclaimed_review_by_file"] == {
            "src/read.php": ["security"],
            "src/unread.php": ["security"],
        }

    def test_one_claim_covers_globally_while_other_reviewer_gap_stays_visible(
        self, tmp_path
    ):
        for agent in ("security-reviewer", "code-reviewer"):
            _write_summary(str(tmp_path), agent, [], ["src/shared.php"])
        _write_review(
            str(tmp_path), "security-review", claims=["src/shared.php"]
        )

        cov = aggregate_file_review(str(tmp_path))

        assert cov["agents_claiming_review_by_file"] == {
            "src/shared.php": ["security"]
        }
        assert cov["agents_with_unclaimed_review_by_file"] == {
            "src/shared.php": ["code"]
        }


class TestUnscopedFiles:
    """`unscoped_files` — changed files no reviewer's scope contained.

    The population that used to vanish: every other bucket is keyed on a
    file some agent's sidecar mentions, so a lockfile, binary, or dotfile
    matching no domain landed in none of them. A field run's true
    never-covered population was ~46 while the report said 41.
    """

    # Each row: a `_write_summary` call for one "security-reviewer" sidecar
    # (fixed across every row — only the changed/reviewable inputs vary),
    # and the `aggregate_file_review(...)` calls to make against it, each
    # as (kwargs, expected `noise_filtered_files`). Most rows are one call;
    # `no-planner-list-is-unmeasured` keeps its original two, since both
    # halves — no reviewable list, and no changed-file list — are the same
    # "unmeasured without both facts" branch that one row can pin together.
    NOISE_FILTERED_CASES = (
        pytest.param(
            [(
                {
                    "changed_files": [
                        "src/a.php", "package-lock.json",
                        "assets/logo.png", "Gemfile",
                    ],
                    "reviewable_files": ["src/a.php", "Gemfile"],
                },
                ["assets/logo.png", "package-lock.json"],
            )],
            id="noise_filtered_files_are_the_planners_exclusions",
        ),
        pytest.param(
            [
                (
                    {"changed_files": ["src/a.php", "package-lock.json"]},
                    None,
                ),
                (
                    {"changed_files": None, "reviewable_files": ["src/a.php"]},
                    None,
                ),
            ],
            id="no-planner-list-is-unmeasured",
        ),
        pytest.param(
            [(
                {
                    "changed_files": ["src/a.php"],
                    "reviewable_files": ["src/a.php", "src/b.php"],
                },
                None,
            )],
            id="plan_list_outside_the_changed_files_is_unmeasured",
        ),
        pytest.param(
            [(
                {"changed_files": ["a.png"], "reviewable_files": []},
                ["a.png"],
            )],
            id="empty_reviewable_list_is_measured",
        ),
    )

    @pytest.mark.parametrize("calls", NOISE_FILTERED_CASES)
    def test_noise_filtered_table(self, tmp_path, calls):
        _write_summary(str(tmp_path), "security-reviewer", ["src/a.php"], [])
        for kwargs, expected in calls:
            cov = aggregate_file_review(str(tmp_path), **kwargs)
            assert cov["noise_filtered_files"] == expected

    def test_noise_filtered_files_leave_unscoped_files_intact(self, tmp_path):
        """`unscoped_files` counts every unscoped file; `noise_filtered_files`
        is the subset the planner excluded — the two must not collapse into
        one measurement."""
        _write_summary(str(tmp_path), "security-reviewer", ["src/a.php"], [])
        cov = aggregate_file_review(
            str(tmp_path),
            changed_files=[
                "src/a.php", "package-lock.json", "assets/logo.png", "Gemfile",
            ],
            reviewable_files=["src/a.php", "Gemfile"],
        )
        assert cov["unscoped_files"] == [
            "Gemfile", "assets/logo.png", "package-lock.json",
        ]

    def test_override_orphans_are_the_unscoped_files_a_skip_left(self, tmp_path):
        """The plan says which files a skipped agent's domain alone
        matched; only the ones no scope contained are orphans here, so a
        file another reviewer did receive is never reported as one."""
        _write_summary(str(tmp_path), "security-reviewer", ["src/a.php"], [])
        cov = aggregate_file_review(
            str(tmp_path), changed_files=["src/a.php", "changelog/x", "Gemfile"],
            override_orphans={"changelog/x": ["docs-drift-reviewer"], "src/a.php": ["a11y-reviewer"]},
        )
        assert cov["override_orphaned_files"] == {"changelog/x": ["docs-drift-reviewer"]}
        assert cov["unscoped_files"] == ["Gemfile", "changelog/x"]

    def test_override_orphans_are_unmeasured_without_the_plan(self, tmp_path):
        _write_summary(str(tmp_path), "security-reviewer", ["src/a.php"], [])
        cov = aggregate_file_review(str(tmp_path), changed_files=["src/a.php", "changelog/x"])
        assert cov["override_orphaned_files"] is None
        cov = aggregate_file_review(str(tmp_path), changed_files=None, override_orphans={"changelog/x": ["docs-drift-reviewer"]})
        assert cov["override_orphaned_files"] is None

    # Each row: the sidecar(s) to write (as `_write_summary` kwargs, always
    # for "security-reviewer" unless a row's spec overrides `agent`), the
    # `changed_files` to measure against, and the expected `unscoped_files`.
    UNSCOPED_FILES_CASES = (
        pytest.param(
            [{"files_with_diffs": ["src/a.php"], "budget_exceeded": []}],
            ["src/a.php", "package-lock.json", ".editorconfig"],
            [".editorconfig", "package-lock.json"],
            id="changed_files_matching_no_domain_are_reported",
        ),
        pytest.param(
            [{
                "files_with_diffs": ["src/inline.php"],
                "budget_exceeded": ["src/claimable.php"],
                "list_only": ["src/listed.php"],
            }],
            [
                "src/inline.php", "src/claimable.php", "src/listed.php",
                "yarn.lock",
            ],
            ["yarn.lock"],
            id="union_covers_every_sidecar_file_list",
        ),
        pytest.param(
            [{"files_with_diffs": ["src/a.php"], "budget_exceeded": []}],
            ["src/a.php", r'"src/broken\3"'],
            None,
            id="unnormalizable_changed_path_leaves_the_population_unmeasured",
        ),
        pytest.param(
            [{"files_with_diffs": ["./src//a.php"], "budget_exceeded": []}],
            ["src/a.php"],
            [],
            id="equivalent_spellings_of_one_path_are_one_file",
        ),
        pytest.param(
            [{"files_with_diffs": ["src/a.php"], "budget_exceeded": []}],
            ["src/a.php"],
            [],
            id="all_files_scoped_is_measured_empty",
        ),
        pytest.param(
            [
                {"files_with_diffs": ["src/a.php"], "budget_exceeded": []},
                {
                    "files_with_diffs": ["ci.yml"], "budget_exceeded": [],
                    "domain": "config-ops",
                },
            ],
            ["src/a.php", "ci.yml"],
            [],
            id="secondary_domain_sidecar_files_count_as_scoped",
        ),
    )

    @pytest.mark.parametrize(
        ("sidecars", "changed_files", "expected"), UNSCOPED_FILES_CASES,
    )
    def test_unscoped_files_table(
        self, tmp_path, sidecars, changed_files, expected
    ):
        for spec in sidecars:
            _write_summary(
                str(tmp_path), spec.get("agent", "security-reviewer"),
                spec["files_with_diffs"], spec["budget_exceeded"],
                domain=spec.get("domain"), list_only=spec.get("list_only"),
            )
        cov = aggregate_file_review(str(tmp_path), changed_files=changed_files)
        assert cov["unscoped_files"] == expected

    def test_git_quoted_changed_path_matches_the_unquoted_sidecar(
        self, tmp_path
    ):
        """The two producers quote differently and the set difference is
        arithmetic on their paths.

        `context.py` runs a plain `git diff --name-only`, so a non-ASCII
        path arrives C-quoted and octal-escaped; scope sidecars run
        `-c core.quotepath=false` and emit real UTF-8. Subtracting one
        alphabet from the other published a fully reviewed file as
        "reviewed by no one" — inside the block step 9 now forbids the
        orchestrator to correct.
        """
        _write_summary(
            str(tmp_path), "security-reviewer", ["src/café.php"], [],
        )
        cov = aggregate_file_review(
            str(tmp_path), changed_files=[r'"src/caf\303\251.php"'],
        )
        assert cov["unscoped_files"] == []

    def test_base_ref_only_agent_contributes_its_whole_scope(
        self, tmp_path
    ):
        """A `--base-ref-only`/`--summary` agent never fetches a diff, so
        its three diff-derived lists are legitimately empty.

        patterns-reviewer is configured that way in the registry, and the
        reviewer protocol sends every reviewer there on 100+-file PRs — the
        exact runs this measurement exists for. Before `in_scope_files`,
        every file such an agent owned published as matched by no one.
        """
        _write_summary(
            str(tmp_path), "patterns-reviewer", [], [],
            in_scope=["src/a.php", "src/b.php"],
        )
        cov = aggregate_file_review(
            str(tmp_path),
            changed_files=["src/a.php", "src/b.php", "yarn.lock"],
        )
        assert cov["unscoped_files"] == ["yarn.lock"]

    def test_schema_one_summary_is_rejected_without_compatibility_reading(
        self, tmp_path
    ):
        path = Path(scope_summary_path(tmp_path, "legacy"))
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({
            "schema": 1,
            "domain": "x",
            "status": "OK",
            "files_with_diffs": ["src/a.php"],
            "budget_exceeded_files": [],
            "list_only_files": [],
        }))
        assert aggregate_file_review(
            str(tmp_path), changed_files=["src/a.php", "src/b.php"],
        ) is None

    @pytest.mark.parametrize(
        "changed_files", [None, []], ids=["absent", "empty"],
    )
    def test_absent_and_empty_changed_lists_are_both_unmeasured(
        self, tmp_path, changed_files
    ):
        """An empty list is an absent list, not "zero changed files".

        A review of zero changed files does not exist; a run whose file
        list never reached the builder does, and orchestration.py reaches
        it by passing `--changed-files ""`. Reading that as measured-and-
        zero publishes a clean coverage bill nothing looked at.
        """
        _write_summary(
            str(tmp_path), "security-reviewer", ["src/a.php"], [],
        )
        cov = aggregate_file_review(
            str(tmp_path), changed_files=changed_files,
        )
        assert cov["unscoped_files"] is None


class TestAgentsReportingCountsAgents:
    """`scope_reporting_agent_count` counts distinct agents, not summary files.

    Three reviewers ship a second `-config-ops` sidecar, so the file count
    reported 22 agents for a 19-agent field run.
    """

    def test_config_ops_sidecar_does_not_double_count_its_agent(
        self, tmp_path
    ):
        for agent in ("security-reviewer", "code-reviewer", "wp-reviewer"):
            _write_summary(str(tmp_path), agent, ["src/a.php"], [])
        for agent in ("security-reviewer", "code-reviewer", "wp-reviewer"):
            _write_summary(
                str(tmp_path), agent, ["ci.yml"], [], domain="config-ops",
            )

        cov = aggregate_file_review(str(tmp_path))

        assert len(list((tmp_path / "reviewers").glob("*/scope-summary*.json"))) == 6
        assert cov["scope_reporting_agent_count"] == 3

    def test_only_unreadable_summaries_still_reads_as_no_data(
        self, tmp_path
    ):
        broken = Path(scope_summary_path(tmp_path, "broken"))
        broken.parent.mkdir(parents=True, exist_ok=True)
        broken.write_text("{not json")
        assert aggregate_file_review(str(tmp_path)) is None


class TestHostContextSummary:
    def test_malformed_lists_are_ignored(self):
        summary = manifest_sections.summarize_host_context({
            "resolved": "bad", "unresolved": "bad",
            "diagnostics": {"self_provided": "bad", "scan_roots": True},
        })
        assert summary == {
            "resolved": [], "unresolved": [], "banner_reason": None,
            "self_provided": [], "scan_roots": None,
        }

    def test_non_string_identity_fields_are_unknown(self):
        value = {"path": "/Users/private"}
        summary = manifest_sections.summarize_host_context({
            "resolved": [{
                "name": value, "kind": value, "source": value, "version": value,
                "version_freshness": value,
                "notes": {"commit": value, "branch": value, "declared_minimum": value},
            }],
            "unresolved": [{"name": value, "reason": value, "version": value}],
            "banner": {"reason": value},
            "diagnostics": {"self_provided": [value]},
        })
        assert all(v is None for v in summary["resolved"][0].values())
        assert all(v is None for v in summary["unresolved"][0].values())
        assert summary["banner_reason"] is None
        assert summary["self_provided"] == []

    MANIFEST = {
        "version": 1,
        "resolved": [
            {"name": "wordpress", "kind": "runtime-host", "path": "/Users/x/.cache/pirategoat/ecosystem/wordpress/latest",
             "source": "ecosystem-cache", "version": "7.2-alpha-63166-src", "version_freshness": "2026-09-04T00:04:08Z",
             "confidence": "high", "notes": {"commit": "474555a85c052de90ddd22d4abdf163e678b88ac", "branch": "trunk",
                                             "commit_date": "2026-09-04T18:35:44Z", "declared_minimum": "7.0",
                                             "declared_by": [{"source": "plugin-headers", "root": "plugins/woocommerce"}]}},
            {"name": "node_modules", "kind": "library-dep", "path": "/Users/x/repo/node_modules",
             "source": "vendor-inspection", "version": None, "version_freshness": None, "confidence": "high", "notes": {}},
        ],
        "unresolved": [{"name": "jetpack", "reason": "declared_in_plugin_headers", "source": "plugin-headers", "version": None}],
        "banner": {"degraded": True, "reason": "partial_unresolved", "message": "…", "unresolved": []},
        "diagnostics": {"self_provided": ["woocommerce"], "scan_roots": 4, "resolvers_consulted": []},
    }

    def test_projects_identity_and_never_a_path(self):
        summary = manifest_sections.summarize_host_context(self.MANIFEST)
        assert summary == {
            "resolved": [
                {"name": "wordpress", "kind": "runtime-host", "source": "ecosystem-cache", "version": "7.2-alpha-63166-src",
                 "commit": "474555a85c052de90ddd22d4abdf163e678b88ac", "refreshed": "2026-09-04T00:04:08Z",
                 "declared_minimum": "7.0"},
                {"name": "node_modules", "kind": "library-dep", "source": "vendor-inspection", "version": None,
                 "commit": None, "refreshed": None, "declared_minimum": None},
            ],
            "unresolved": [{"name": "jetpack", "reason": "declared_in_plugin_headers", "version": None}],
            "banner_reason": "partial_unresolved",
            "self_provided": ["woocommerce"],
            "scan_roots": 4,
        }
        assert "/Users/" not in json.dumps(summary)

    def test_absent_or_malformed_manifests_are_unmeasured(self):
        assert manifest_sections.summarize_host_context(None) is None
        assert manifest_sections.summarize_host_context("nope") is None
        assert manifest_sections.summarize_host_context({}) == {"resolved": [], "unresolved": [], "banner_reason": None, "self_provided": [], "scan_roots": None}
