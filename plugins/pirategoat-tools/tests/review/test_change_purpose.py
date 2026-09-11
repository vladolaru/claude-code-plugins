"""Tests for the change purpose's parsed structure."""

import sys
from pathlib import Path

import pytest

TESTS_DIR = Path(__file__).resolve().parent.parent
SCRIPTS_DIR = TESTS_DIR.parent / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))
from review import change_purpose as cp  # noqa: E402


SAMPLE = """# Change purpose — PR #12089

**PR:** "Fix express checkout crash" by someone

## What the change does

Adds a guard.

## Verify
V1. `getOnClickOptions()` can return undefined while a refresh is in flight — where: `client/express-checkout/index.js:212` — settled by: reading the click handler and the refresh path — source: PR description
V2. Blocks checkout reads the WC data store, not this cache — where: `client/blocks/` — settled by: grep for the cache symbol under client/blocks — source: inferred from the diff
- V3. The overlay does not intercept keyboard activation — where: `button-ui.js:40` — settled by: the blockUI docs and a manual check — source: review thread (carried over)

## Context
C1. The bug reproduces only on the merchant's site — source: linked issue WOOPMNT-6265
C2. `unblockButton()` keeps its behaviour — source: commit 407d70566

## Author's description (extracted)
> The button is guarded on both fronts: click rejection plus a white overlay.
> Blocks cart and checkout are unaffected.
"""


class TestParse:
    def test_reads_ids_text_source_and_carried_over(self):
        parsed = cp.parse_change_purpose(SAMPLE)
        assert parsed["structured"] is True
        assert parsed["problems"] == []
        assert [i["id"] for i in parsed["verify"]] == ["V1", "V2", "V3"]
        assert parsed["verify"][0]["source"] == "PR description"
        assert parsed["verify"][0]["text"].startswith("`getOnClickOptions()` can return undefined")
        assert "— where:" in parsed["verify"][0]["text"]
        assert parsed["verify"][1]["source"] == cp.INFERRED_SOURCE
        assert parsed["verify"][2]["carried_over"] is True
        assert parsed["verify"][2]["source"] == "review thread"
        assert [i["id"] for i in parsed["context"]] == ["C1", "C2"]
        assert parsed["context"][0]["source"] == "linked issue WOOPMNT-6265"
        assert parsed["context"][0]["carried_over"] is False
        assert parsed["author_description"].startswith("> The button is guarded")
        assert "Blocks cart and checkout are unaffected." in parsed["author_description"]

    def test_continuation_lines_join_the_item_above(self):
        text = "## Verify\nV1. A long claim\n   that wraps — source: PR description\n## Context\nNone.\n## Author's description (extracted)\nx\n"
        parsed = cp.parse_change_purpose(text)
        assert parsed["verify"][0]["text"] == "A long claim that wraps"
        assert parsed["verify"][0]["source"] == "PR description"
        assert parsed["context"] == []
        assert parsed["problems"] == []

    def test_source_uses_the_last_source_marker(self):
        text = "## Verify\nV1. Preserve the literal — source: label in the claim — source: PR description\n## Context\nNone.\n## Author's description (extracted)\nx\n"
        parsed = cp.parse_change_purpose(text)
        assert parsed["verify"][0]["text"] == "Preserve the literal — source: label in the claim"
        assert parsed["verify"][0]["source"] == "PR description"

    def test_an_unstructured_purpose_is_a_fact_not_a_problem(self):
        parsed = cp.parse_change_purpose("# Change purpose\n\nFree prose with a numbered list.\n1. focus one\n")
        assert parsed == {
            "verify": [], "context": [], "author_description": "",
            "problems": [], "structured": False,
        }
        assert cp.parse_change_purpose("")["structured"] is False
        assert cp.parse_change_purpose(None)["structured"] is False


class TestProblems:
    def _with(self, verify="V1. claim — source: PR description", context="C1. fact — source: commit abc"):
        return f"## Verify\n{verify}\n## Context\n{context}\n## Author's description (extracted)\nquoted\n"

    def test_a_missing_heading_is_named(self):
        parsed = cp.parse_change_purpose("## Verify\nV1. claim — source: PR description\n")
        assert "missing heading `## Context`" in parsed["problems"]
        assert "missing heading `## Author's description (extracted)`" in parsed["problems"]
        assert parsed["structured"] is True

    # Each row: a verify/context body override (None keeps `_with`'s
    # default) and the problems it produces, matched on the item id (or
    # leading fragment) plus one keyword rather than the full sentence —
    # the sentence is prose read by the reconciliator agent and the record.
    PROBLEMS_CASES = (
        pytest.param(
            "V1. claim with no provenance", None,
            [("V1", "no source")],
            id="verify_item_without_a_source",
        ),
        pytest.param(
            None, "C1. fact — source: inferred from the diff",
            [("C1", "may not be Context")],
            id="inferred_context_item_is_a_problem",
        ),
        pytest.param(
            "V1. claim — source: inferred from the diff", None,
            [],
            id="inferred_verify_item_is_fine",
        ),
        pytest.param(
            "V1. a — source: PR description\nV1. b — source: PR description",
            None,
            [("V1", "listed twice")],
            id="duplicate_id",
        ),
        pytest.param(
            None, "- a bare bullet",
            [("Context section", "no `C<n>.` item")],
            id="context_body_parses_to_no_item",
        ),
        pytest.param(
            "\n".join(
                f"V{n}. claim {n} — source: PR description"
                for n in range(1, 10)
            ),
            None,
            [("9 Verify items", "more than 8")],
            id="more_than_eight_verify_items_warns",
        ),
    )

    @pytest.mark.parametrize(
        ("verify", "context", "expected_fragments"), PROBLEMS_CASES,
    )
    def test_problems(self, verify, context, expected_fragments):
        kwargs = {}
        if verify is not None:
            kwargs["verify"] = verify
        if context is not None:
            kwargs["context"] = context
        parsed = cp.parse_change_purpose(self._with(**kwargs))

        assert len(parsed["problems"]) == len(expected_fragments)
        for problem, (id_fragment, keyword) in zip(
            parsed["problems"], expected_fragments
        ):
            assert id_fragment in problem
            assert keyword in problem

    def test_an_item_under_the_wrong_heading_is_named_and_not_tiered(self):
        parsed = cp.parse_change_purpose(self._with(context="V2. claim — source: PR description"))
        assert parsed["problems"] == ["V2 is listed under the Context heading"]
        assert parsed["context"] == []
        assert [item["id"] for item in parsed["verify"]] == ["V1"]

    def test_an_empty_verify_section_is_empty_without_a_problem(self):
        parsed = cp.parse_change_purpose(self._with(verify=""))
        assert parsed["verify"] == []
        assert parsed["problems"] == []
        assert cp.parse_change_purpose(self._with(verify="None."))["problems"] == []

    def test_a_verify_body_that_parses_to_no_item_is_named(self):
        """Legacy numbering is the likeliest orchestrator mistake; it must
        not read the same as a declared empty tier."""
        parsed = cp.parse_change_purpose(self._with(verify="1. legacy claim — source: PR description\n2. another"))
        assert parsed["verify"] == []
        assert parsed["problems"] == [
            "the Verify section has 2 line(s) but no `V<n>.` item — write `None.` when the tier is empty"
        ]

    def test_a_none_author_section_is_an_empty_description(self):
        text = "## Verify\nNone.\n## Context\nNone.\n## Author's description (extracted)\nNone.\n"
        assert cp.parse_change_purpose(text)["author_description"] == ""


class TestChecksSettling:
    def test_groups_checks_by_the_item_they_cite(self):
        items = [{"id": "V1"}, {"id": "V2"}]
        checks = [
            ("security-review", {"id": "c1", "result": "0 hits", "verifies": ["V1"]}),
            ("code-review", {"id": "c2", "result": "read both paths", "verifies": ["V1", "V9"]}),
            ("code-review", {"id": "c3", "result": "no citation"}),
        ]
        assert cp.checks_settling(items, checks) == {
            "V1": [
                {"reviewer": "security-review", "id": "c1", "result": "0 hits"},
                {"reviewer": "code-review", "id": "c2", "result": "read both paths"},
            ],
            "V2": [],
        }

    def test_ledger_citations_carry_checks_and_confirmed_notes(self):
        """One reader of "who cites what" for every post-merge consumer:
        the record table, the evidence manifest and the cohort metric."""
        ledger = {
            "checks": [
                {"id": "c1", "result": "0 hits", "source_reviewers": ["security-reviewer", "code-reviewer"], "verifies": ["V1"]},
                {"id": "c2", "result": "r"},
            ],
            "orchestrator_notes": [
                {"id": "n1", "outcome": "confirmed", "evidence": "regenerated cleanly", "verifies": ["V2"]},
                {"id": "n2", "outcome": "refuted", "evidence": "e", "verifies": ["V3"]},
                {"id": "n3", "outcome": "confirmed", "evidence": "e"},
            ],
        }
        cited = cp.ledger_citations(ledger)
        assert cited == [
            ("security-reviewer, code-reviewer", ledger["checks"][0]),
            ("", ledger["checks"][1]),
            (cp.RECONCILIATOR_LABEL, {"id": "n1", "result": "regenerated cleanly", "verifies": ["V2"]}),
        ]
        assert cp.checks_settling([{"id": "V1"}, {"id": "V2"}, {"id": "V3"}], cited) == {
            "V1": [{"reviewer": "security-reviewer, code-reviewer", "id": "c1", "result": "0 hits"}],
            "V2": [{"reviewer": "review-reconciliator", "id": "n1", "result": "regenerated cleanly"}],
            "V3": [],
        }
        assert cp.ledger_citations({"checks": []}) == []
        assert cp.ledger_citations({}) == []

    def test_an_undeclared_citation_is_named_not_dropped(self):
        items = [{"id": "V1"}]
        checks = [
            ("code-review", {"id": "c2", "verifies": ["V1", "V9"]}),
            ("security-review", {"id": "c1", "verifies": ["V3"]}),
            ("code-review", {"id": "c3"}),
        ]
        assert cp.undeclared_citations(items, checks) == [
            {"reviewer": "code-review", "id": "c2", "cites": "V9"},
            {"reviewer": "security-review", "id": "c1", "cites": "V3"},
        ]
        assert cp.undeclared_citations([], []) == []
