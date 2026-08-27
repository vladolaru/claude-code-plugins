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

from helpers.review_fixtures import canonical_review_document
from review import manifest_sections
from review.manifest_sections import aggregate_file_review
from review.reviewer_lifecycle import ReviewPaths


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
    suffix = f"-{domain}" if domain else ""
    path = os.path.join(output_dir, f"{agent}-scope-summary{suffix}.json")
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
    payload = canonical_review_document(
        stem.removesuffix("-review"),
        review_claimable_files=list(claims if claimable is None else claimable),
        reviewed_file_claims=list(claims),
    )
    with open(os.path.join(output_dir, f"{stem}.json"), "w") as f:
        json.dump(payload, f)


def _write_assignment(output_dir, reviewer, claimable, *, inline_count=0):
    payload = {
        "schema": 4,
        "agent_name": f"{reviewer}-reviewer",
        "reviewer": reviewer,
        "review_claimable_files": claimable,
        "review_budget": 15,
        "inline_diff_file_count": inline_count,
        "in_scope_review_file_count": inline_count + len(claimable),
        "channels": ["blocking"],
    }
    with open(
        os.path.join(output_dir, f"{reviewer}-assignment.json"),
        "w",
    ) as f:
        json.dump(payload, f)
    return payload


class TestAggregateReviewedFiles:
    """aggregate_file_review() reads *-scope-summary*.json sidecars."""

    def test_direct_review_reads_follow_review_paths_authority(
        self, tmp_path, monkeypatch
    ):
        authority_dir = tmp_path / "authority"
        authority_dir.mkdir()
        paths = ReviewPaths(
            draft=str(authority_dir / "draft.json"),
            final=str(authority_dir / "final.json"),
            assignment=str(authority_dir / "authority.json"),
        )
        Path(paths.final).write_text(json.dumps(canonical_review_document(
            "security",
            review_claimable_files=["src/read.php", "src/unread.php"],
            reviewed_file_claims=["src/read.php"],
        )))
        monkeypatch.setattr(
            manifest_sections, "review_paths", lambda *_args: paths
        )

        claimed, unclaimed = manifest_sections._load_agent_reviewed_files(
            str(tmp_path), "security-reviewer"
        )

        assert claimed == ["src/read.php"]
        assert unclaimed == ["src/unread.php"]

    def test_returns_none_without_summaries(self, tmp_path):
        assert aggregate_file_review(str(tmp_path)) is None

    def test_returns_none_for_missing_dir(self, tmp_path):
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
            "src/a.php": ["security-reviewer"],
            "src/b.php": ["code-reviewer"],
        }
        assert cov["agents_with_unclaimed_review_by_file"] == {
            "src/b.php": ["security-reviewer"],
            "src/starved.php": [
            "code-reviewer", "security-reviewer",
            ],
        }

    def test_inline_receipt_keeps_other_agents_unclaimed_work_from_becoming_a_run_gap(
        self, tmp_path
    ):
        """The aggregate keeps both per-agent facts; its report consumer must
        not strengthen one reviewer's unfinished work into a run-wide gap."""
        _write_summary(
            str(tmp_path), "security-reviewer", [], ["src/shared.php"],
        )
        _write_summary(
            str(tmp_path), "code-reviewer", ["src/shared.php"], [],
        )

        file_review = aggregate_file_review(str(tmp_path))

        assert file_review["agents_receiving_inline_diff_by_file"] == {
            "src/shared.php": ["code-reviewer"]
        }
        assert file_review["agents_with_unclaimed_review_by_file"] == {
            "src/shared.php": ["security-reviewer"]
        }
        from review.briefings import _has_file_review_gap
        assert not _has_file_review_gap(file_review)

    def test_malformed_summary_skipped(self, tmp_path):
        (tmp_path / "broken-scope-summary.json").write_text("{not json")
        _write_summary(
            str(tmp_path), "security-reviewer",
            ["src/a.php"], [],
        )
        cov = aggregate_file_review(str(tmp_path))
        assert cov["scope_reporting_agent_count"] == 1

    def test_secondary_summaries_attribute_to_agent(self, tmp_path):
        _write_summary(
            str(tmp_path), "security-reviewer", [], ["ci.yml"],
            domain="config-ops",
        )
        cov = aggregate_file_review(str(tmp_path))
        assert cov["agents_with_unclaimed_review_by_file"]["ci.yml"] == ["security-reviewer"]

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
            "src/a.py": ["security-reviewer"]
        }
        assert cov["agents_with_unclaimed_review_by_file"] == {
            "src/b.py": ["security-reviewer"]
        }

    @pytest.mark.parametrize(
        "claims", ["src/read.php", ["src/read.php", None]],
        ids=["raw-string", "malformed-entry"],
    )
    def test_malformed_claims_credit_nothing(self, tmp_path, claims):
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
        review["reviewed_file_claims"] = claims
        (tmp_path / "security-review.json").write_text(json.dumps(review))

        cov = aggregate_file_review(str(tmp_path))

        assert cov["agents_claiming_review_by_file"] == {}
        assert cov["agents_with_unclaimed_review_by_file"] == {
            "src/read.php": ["security-reviewer"],
            "src/unread.php": ["security-reviewer"],
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
            "src/shared.php": ["security-reviewer"]
        }
        assert cov["agents_with_unclaimed_review_by_file"] == {
            "src/shared.php": ["code-reviewer"]
        }


class TestUnscopedFiles:
    """`unscoped_files` — changed files no reviewer's scope contained.

    The population that used to vanish: every other bucket is keyed on a
    file some agent's sidecar mentions, so a lockfile, binary, or dotfile
    matching no domain landed in none of them. A field run's true
    never-covered population was ~46 while the report said 41.
    """

    def test_changed_files_matching_no_domain_are_reported(self, tmp_path):
        _write_summary(
            str(tmp_path), "security-reviewer", ["src/a.php"], [],
        )
        cov = aggregate_file_review(
            str(tmp_path),
            changed_files=[
                "src/a.php", "package-lock.json", ".editorconfig",
            ],
        )
        assert cov["unscoped_files"] == [".editorconfig", "package-lock.json"]

    def test_union_covers_every_sidecar_file_list(self, tmp_path):
        """Inline, claimable, AND name-only listing all count as scoped —
        a file the agent was told about is not "matched no domain"."""
        _write_summary(
            str(tmp_path), "security-reviewer",
            ["src/inline.php"], ["src/claimable.php"],
            list_only=["src/listed.php"],
        )
        cov = aggregate_file_review(
            str(tmp_path),
            changed_files=[
                "src/inline.php", "src/claimable.php", "src/listed.php",
                "yarn.lock",
            ],
        )
        assert cov["unscoped_files"] == ["yarn.lock"]

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

    def test_unnormalizable_changed_path_leaves_the_population_unmeasured(
        self, tmp_path
    ):
        """A shrunken population reads as a cleaner review than the run
        earned, so the strict side fails to unmeasured instead."""
        _write_summary(
            str(tmp_path), "security-reviewer", ["src/a.php"], [],
        )
        cov = aggregate_file_review(
            str(tmp_path),
            changed_files=["src/a.php", r'"src/broken\3"'],
        )
        assert cov["unscoped_files"] is None

    def test_equivalent_spellings_of_one_path_are_one_file(
        self, tmp_path
    ):
        _write_summary(
            str(tmp_path), "security-reviewer", ["./src//a.php"], [],
        )
        cov = aggregate_file_review(
            str(tmp_path), changed_files=["src/a.php"],
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
        path = tmp_path / "legacy-reviewer-scope-summary.json"
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

    def test_all_files_scoped_is_measured_empty(self, tmp_path):
        _write_summary(
            str(tmp_path), "security-reviewer", ["src/a.php"], [],
        )
        cov = aggregate_file_review(
            str(tmp_path), changed_files=["src/a.php"],
        )
        assert cov["unscoped_files"] == []

    def test_no_changed_file_list_is_unmeasured_not_empty(self, tmp_path):
        """None, not [] — a caller must not read "not measured" as "none"."""
        _write_summary(
            str(tmp_path), "security-reviewer", ["src/a.php"], [],
        )
        cov = aggregate_file_review(str(tmp_path))
        assert cov["unscoped_files"] is None

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

    def test_a_measured_run_that_finds_nothing_reports_an_empty_list(
        self, tmp_path
    ):
        """The other side of the same distinction: measured and clean."""
        _write_summary(
            str(tmp_path), "security-reviewer", ["src/a.php"], [],
        )
        cov = aggregate_file_review(
            str(tmp_path), changed_files=["src/a.php"],
        )
        assert cov["unscoped_files"] == []

    def test_secondary_domain_sidecar_files_count_as_scoped(
        self, tmp_path
    ):
        _write_summary(
            str(tmp_path), "security-reviewer", ["src/a.php"], [],
        )
        _write_summary(
            str(tmp_path), "security-reviewer", ["ci.yml"], [],
            domain="config-ops",
        )
        cov = aggregate_file_review(
            str(tmp_path), changed_files=["src/a.php", "ci.yml"],
        )
        assert cov["unscoped_files"] == []


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

        assert len(list(tmp_path.glob("*-scope-summary*.json"))) == 6
        assert cov["scope_reporting_agent_count"] == 3

    def test_only_unreadable_summaries_still_reads_as_no_data(
        self, tmp_path
    ):
        (tmp_path / "broken-scope-summary.json").write_text("{not json")
        assert aggregate_file_review(str(tmp_path)) is None
