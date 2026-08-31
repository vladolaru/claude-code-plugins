"""The reconciliator's builder: content plus reconciliation, no reviewer."""
import re
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

SCRIPTS_DIR = Path(__file__).resolve().parents[2] / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))

from review.findings_ledger import (  # noqa: E402
    LEDGER_SCHEMA,
    RECONCILIATION_JUDGMENT_FIELDS,
    FindingsLedgerBuilder,
    _no_lifecycle,
)
from review.review_document import (  # noqa: E402
    REVIEW_CONTENT_FIELDS,
    validate_review_content,
)
from review.review_markdown import render_markdown  # noqa: E402
from review import run_paths  # noqa: E402


def _ledger(tmp_path):
    builder = FindingsLedgerBuilder(pr_id="42", output_dir=str(tmp_path))
    builder.add_finding("high", "t", "src/a.py", "d", "r", line=3)
    builder.record_check(
        question="q", method="m", result="held",
        source_reviewers=["security-reviewer", "code-reviewer"],
    )
    builder.set_assessment("fine")
    builder.set_reconciliation(
        grouped_concern_count=1, verified_concern_count=1,
        false_positive_concern_count=0, out_of_scope_concern_count=0,
    )
    return builder


def test_ledger_dict_is_content_plus_reconciliation(tmp_path):
    data = _ledger(tmp_path).to_dict()
    assert set(data) == REVIEW_CONTENT_FIELDS
    assert data["schema"] == LEDGER_SCHEMA
    assert "reviewer" not in data
    recon = data["meta"]["reconciliation"]
    assert tuple(recon) == RECONCILIATION_JUDGMENT_FIELDS
    assert data["checks"][0]["source_reviewers"] == [
        "security-reviewer", "code-reviewer",
    ]


def test_ledger_content_validates_as_content(tmp_path):
    data = _ledger(tmp_path).to_dict()
    content = {
        **data,
        "meta": {
            k: v for k, v in data["meta"].items() if k != "reconciliation"
        },
    }
    validate_review_content(content, schema=LEDGER_SCHEMA)


def test_ledger_requires_reconciliation_before_serializing(tmp_path):
    builder = FindingsLedgerBuilder(pr_id="42", output_dir=str(tmp_path))
    with pytest.raises(ValueError, match="set_reconciliation"):
        builder.to_dict()


@pytest.mark.parametrize(
    "method",
    [
        "save_draft",
        "claim_files_reviewed",
        "retract_reviewed_file_claims",
        "mark_not_applicable",
    ],
)
def test_ledger_has_no_reviewer_lifecycle(tmp_path, method):
    """Matched on the message: an inherited signature can raise TypeError
    of its own, which would pass this test without any override at all."""
    builder = FindingsLedgerBuilder(pr_id="42", output_dir=str(tmp_path))
    with pytest.raises(TypeError, match="no reviewer lifecycle"):
        getattr(builder, method)("x")


def test_ledger_has_no_open_classmethod(tmp_path):
    with pytest.raises(TypeError, match="no reviewer lifecycle"):
        FindingsLedgerBuilder.open(str(tmp_path), "42", "reconciliator")


def test_ledger_pr_id_is_coerced_to_a_string(tmp_path):
    builder = FindingsLedgerBuilder(pr_id=42, output_dir=str(tmp_path))
    builder.set_reconciliation(
        grouped_concern_count=0, verified_concern_count=0,
        false_positive_concern_count=0, out_of_scope_concern_count=0,
    )
    assert builder.to_dict()["pr_id"] == "42"


def test_ledger_reads_plugin_version_from_the_bound_run(tmp_path, monkeypatch):
    monkeypatch.delenv("PIRATEGOAT_PLUGIN_VERSION", raising=False)
    (tmp_path / "run-config.json").write_text('{"plugin_version": "1.114.0"}')
    builder = FindingsLedgerBuilder(pr_id="42", output_dir=str(tmp_path))
    builder.set_reconciliation(
        grouped_concern_count=0, verified_concern_count=0,
        false_positive_concern_count=0, out_of_scope_concern_count=0,
    )
    assert builder.to_dict()["plugin_version"] == "1.114.0"


def test_ledger_duration_spans_the_reconciliator_dispatch(tmp_path):
    """The marker is keyed on the dispatched agent name, not the actor."""
    started = datetime.now(timezone.utc) - timedelta(seconds=5)
    marker = run_paths.synthesis_started_marker(
        tmp_path, "review-reconciliator"
    )
    marker.parent.mkdir(parents=True)
    marker.write_text(started.isoformat())
    builder = FindingsLedgerBuilder(pr_id="42", output_dir=str(tmp_path))
    builder.set_reconciliation(
        grouped_concern_count=0, verified_concern_count=0,
        false_positive_concern_count=0, out_of_scope_concern_count=0,
    )
    assert builder.to_dict()["meta"]["review_duration_ms"] >= 5000


@pytest.mark.parametrize(
    "counts",
    [
        {"grouped_concern_count": -1, "verified_concern_count": 0,
         "false_positive_concern_count": 0, "out_of_scope_concern_count": 0},
        {"grouped_concern_count": True, "verified_concern_count": 1,
         "false_positive_concern_count": 0, "out_of_scope_concern_count": 0},
        {"grouped_concern_count": "1", "verified_concern_count": 1,
         "false_positive_concern_count": 0, "out_of_scope_concern_count": 0},
    ],
)
def test_reconciliation_counts_must_be_non_negative_integers(tmp_path, counts):
    builder = FindingsLedgerBuilder(pr_id="42", output_dir=str(tmp_path))
    with pytest.raises(ValueError, match="non-negative integer"):
        builder.set_reconciliation(**counts)


def test_reconciliation_judgments_must_partition_the_grouped_concerns(
    tmp_path,
):
    builder = FindingsLedgerBuilder(pr_id="42", output_dir=str(tmp_path))
    with pytest.raises(ValueError, match="grouped_concern_count"):
        builder.set_reconciliation(
            grouped_concern_count=3, verified_concern_count=1,
            false_positive_concern_count=0, out_of_scope_concern_count=0,
        )


def test_ledger_renders_without_a_reviewer_title(tmp_path):
    rendered = render_markdown(_ledger(tmp_path).to_dict())
    assert rendered.startswith("# Review Findings - PR #42\n\n")
    assert "## Verified Checks" in rendered


def test_the_taught_snippet_calls_only_methods_the_builder_has():
    """The reconciliator builds the ledger by following a Markdown snippet,
    so a renamed or deleted builder method breaks a live run and nothing
    else — no Python caller changes. This is the lockstep."""
    snippet = (
        Path(__file__).resolve().parents[2]
        / "agents" / "review-reconciliator.md"
    ).read_text(encoding="utf-8")
    called = set(re.findall(r"\bbuilder\.([A-Za-z_][A-Za-z0-9_]*)\(", snippet))

    assert called, "the snippet no longer calls the builder at all"
    # `open()` and the draft-lifecycle names still resolve as attributes —
    # they are bound to the refusal that gives the ledger no reviewer
    # lifecycle. Teaching one would fail at runtime, so mere existence is
    # not the bar: the attribute has to be a method that does something.
    assert "open" not in called
    for method in sorted(called):
        attribute = getattr(FindingsLedgerBuilder, method, None)
        assert callable(attribute), (
            f"builder.{method}() is taught but FindingsLedgerBuilder has no "
            "such method"
        )
        assert attribute is not _no_lifecycle, (
            f"builder.{method}() is taught but raises: the findings ledger "
            "has no reviewer lifecycle"
        )
