"""The reconciliator's builder: content plus reconciliation, no reviewer."""
import json
import re
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

PLUGIN_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PLUGIN_ROOT / "scripts"))
RECONCILIATOR_MD = PLUGIN_ROOT / "agents" / "review-reconciliator.md"

from review.findings_ledger import (  # noqa: E402
    DROP_REASONS_CHECK,
    DROP_REASONS_FINDING,
    LEDGER_SCHEMA,
    NOTE_OUTCOMES,
    FindingsLedgerBuilder,
    _no_lifecycle,
)
from review.review_document import (  # noqa: E402
    REVIEW_CONTENT_FIELDS,
    validate_review_content,
)
from review.review_markdown import render_markdown  # noqa: E402
from review import run_paths  # noqa: E402
from helpers import ts_schema  # noqa: E402
from helpers.review_fixtures import write_reconciliation_context  # noqa: E402


def _ledger(tmp_path):
    builder = FindingsLedgerBuilder(pr_id="42", output_dir=str(tmp_path))
    builder.add_finding(
        "high", "t", "src/a.py", "d", "r", line=3,
        sources=[{"reviewer": "security-review", "id": "f1"}],
    )
    builder.record_check(
        question="q", method="m", result="held",
        source_reviewers=["security-reviewer", "code-reviewer"],
        sources=[{"reviewer": "security-review", "id": "c1"}],
    )
    builder.set_assessment("fine")
    builder.set_reconciliation(
        grouped_concern_count=1, verified_concern_count=1,
        false_positive_concern_count=0, out_of_scope_concern_count=0,
    )
    return builder


def test_ledger_dict_is_content_plus_reconciliation(tmp_path):
    data = _ledger(tmp_path).to_dict()
    assert set(data) == REVIEW_CONTENT_FIELDS | {
        "dropped_findings", "dropped_checks", "orchestrator_notes",
    }
    assert data["schema"] == LEDGER_SCHEMA
    assert "reviewer" not in data
    assert data["checks"][0]["source_reviewers"] == [
        "security-reviewer", "code-reviewer",
    ]


def test_ledger_content_validates_as_content(tmp_path):
    data = _ledger(tmp_path).to_dict()
    content = {
        **{
            key: value for key, value in data.items()
            if key not in {
                "dropped_findings", "dropped_checks", "orchestrator_notes",
            }
        },
        "checks": [
            {key: value for key, value in check.items() if key != "sources"}
            for check in data["checks"]
        ],
        "meta": {
            k: v for k, v in data["meta"].items() if k != "reconciliation"
        },
    }
    validate_review_content(content, schema=LEDGER_SCHEMA)


def test_ledger_requires_reconciliation_before_serializing(tmp_path):
    builder = FindingsLedgerBuilder(pr_id="42", output_dir=str(tmp_path))
    with pytest.raises(ValueError, match="set_reconciliation"):
        builder.to_dict()


def test_ledger_has_no_reviewer_lifecycle(tmp_path):
    """Matched on the message: an inherited signature can raise TypeError
    of its own, which would pass this test without any override at all.
    One representative stands for the four lifecycle names bound to the
    same `_no_lifecycle` function object; `open` is the other binding
    below, a classmethod that calls `_no_lifecycle()` directly."""
    builder = FindingsLedgerBuilder(pr_id="42", output_dir=str(tmp_path))
    with pytest.raises(TypeError, match="no reviewer lifecycle"):
        builder.save_draft("x")


def test_ledger_has_no_open_classmethod(tmp_path):
    with pytest.raises(TypeError, match="no reviewer lifecycle"):
        FindingsLedgerBuilder.open(str(tmp_path), "42", "reconciliator")


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


def test_reconciliation_counts_must_be_non_negative_integers(tmp_path):
    builder = FindingsLedgerBuilder(pr_id="42", output_dir=str(tmp_path))
    with pytest.raises(ValueError, match="non-negative integer"):
        builder.set_reconciliation(
            grouped_concern_count=-1, verified_concern_count=0,
            false_positive_concern_count=0, out_of_scope_concern_count=0,
        )


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
    snippet = RECONCILIATOR_MD.read_text(encoding="utf-8")
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


def test_the_definition_states_what_the_builder_derives_and_accepts():
    """In all six field runs the reconciliator read `agent/output.py`,
    `findings_ledger.py` and `verdict_rules.py` for 10 s to 4 min 20 s
    looking for a verdict setter, a category vocabulary and the severity
    and outcome vocabularies. The template states them instead.

    This is the one parity pin between the constants and the agent
    definition; the keyword-soup checks that used to sit beside it (the
    named builder methods it calls, `sources=[`, `severity_note=`, and one
    literal `resolve_note(...)` call) pinned wording, not parity, and are
    gone — `test_the_taught_snippet_calls_only_methods_the_builder_has`
    below is the structural guard for the methods it calls."""
    snippet = RECONCILIATOR_MD.read_text(encoding="utf-8")
    for value in DROP_REASONS_FINDING + DROP_REASONS_CHECK + NOTE_OUTCOMES:
        assert f"`{value}`" in snippet, value


class TestTypeScriptContractLockstep:
    """schemas/review-output.ts's drop-reason and note-outcome unions must
    match this module's own vocabularies — moved from
    `agent/test_output.py` (G7), trimmed to the parity asserts against
    `DROP_REASONS_FINDING`/`DROP_REASONS_CHECK`/`NOTE_OUTCOMES`. The literal
    TS field-shape text those tests also pinned was wording, not parity,
    and is dropped.
    """

    def test_drop_reason_vocabulary_matches_the_ts_union(self):
        assert set(re.findall(
            r"'([^']+)'", ts_schema.type_alias("DroppedFinding"),
        )) == set(DROP_REASONS_FINDING)
        assert DROP_REASONS_CHECK == ("void",)

    def test_note_outcome_vocabulary_matches_the_ts_union(self):
        assert set(re.findall(
            r"'([^']+)'", ts_schema.field_types("OrchestratorNote")["outcome"],
        )) == set(NOTE_OUTCOMES)


def _ledger_with_provenance(tmp_path):
    builder = FindingsLedgerBuilder(pr_id="42", output_dir=str(tmp_path))
    builder.add_finding(
        severity="high", title="Unescaped output", file="src/a.php", line=4,
        description="d", recommendation="r", category="security",
        sources=[{"reviewer": "security-review", "id": "f2"},
                 {"reviewer": "code-review", "id": "f1"}],
        severity_note="code-review said medium; the sink is reachable from GET.",
    )
    builder.record_check(
        question="Callers?", method="git grep foo | grep -n bar", result="0",
        source_reviewers=["security-reviewer"],
        sources=[{"reviewer": "security-review", "id": "c1"}],
    )
    builder.drop_finding("code-review", "f3", reason="false_positive",
                         evidence="src/a.php:9 escapes it")
    builder.drop_finding("code-review", "f4", reason="prefiltered")
    builder.drop_check("code-review", "c2", reason="void",
                       evidence="searched the class name, not the hook name")
    builder.resolve_note("n1", outcome="refuted",
                         evidence="f2 and code-review f1 are one concern")
    builder.set_assessment("Fine.")
    builder.set_reconciliation(
        grouped_concern_count=2, verified_concern_count=1,
        false_positive_concern_count=1, out_of_scope_concern_count=0,
    )
    return builder.to_dict()


def test_provenance_lands_on_the_serialized_ledger(tmp_path):
    data = _ledger_with_provenance(tmp_path)
    assert data["findings"][0]["sources"] == [
        {"reviewer": "security-review", "id": "f2"},
        {"reviewer": "code-review", "id": "f1"},
    ]
    assert data["findings"][0]["severity_note"].startswith("code-review said")
    assert data["checks"][0]["sources"] == [
        {"reviewer": "security-review", "id": "c1"}
    ]
    assert data["dropped_findings"] == [
        {"reviewer": "code-review", "id": "f3", "reason": "false_positive",
         "evidence": "src/a.php:9 escapes it"},
        {"reviewer": "code-review", "id": "f4", "reason": "prefiltered"},
    ]
    assert data["dropped_checks"] == [
        {"reviewer": "code-review", "id": "c2", "reason": "void",
         "evidence": "searched the class name, not the hook name"},
    ]
    assert data["orchestrator_notes"] == [
        {"id": "n1", "outcome": "refuted",
         "evidence": "f2 and code-review f1 are one concern"},
    ]


def test_provenance_ledger_passes_the_reader_boundary(tmp_path):
    from review.critic_adjustments import validate_findings_document
    data = _ledger_with_provenance(tmp_path)
    data["meta"]["reconciliation"].update({
        "input_finding_count": 2,
        "contributing_agent_count": 2,
        "reviewing_agents": ["security-reviewer", "code-reviewer"],
        "not_applicable_agents": [],
        "dispatched_agents": ["security-reviewer", "code-reviewer"],
        "missing_agents": [],
    })
    validate_findings_document(data)


@pytest.mark.parametrize("call", [
    pytest.param(
        lambda b: b.add_finding(severity="low", title="t", file="f", line=1,
                                description="d", recommendation="r", sources=[]),
        id="empty_sources-normalized_sources",
    ),
    pytest.param(
        lambda b: b.add_finding(severity="low", title="t", file="f", line=1,
                                description="d", recommendation="r",
                                sources=[{"reviewer": "x-review", "id": "f1"},
                                         {"reviewer": "x-review", "id": "f1"}]),
        id="duplicate_source-normalized_sources",
    ),
    pytest.param(
        lambda b: b.drop_finding("x-review", "f1", reason="bogus", evidence="e"),
        id="bad_drop_reason-drop_finding",
    ),
    pytest.param(
        lambda b: b.drop_check("x-review", "c1", reason="void", evidence="  "),
        id="blank_drop_evidence-drop_check",
    ),
    pytest.param(
        lambda b: b.resolve_note("n1", outcome="refuted", evidence="e", verifies=["V2"]),
        id="verifies_on_a_refuted_note-resolve_note",
    ),
])
def test_malformed_provenance_is_refused_at_the_builder(tmp_path, call):
    """One row per builder function that validates provenance:
    `normalized_sources` (shared by add_finding/record_check, two rows for
    its two failure branches), `drop_finding`, `drop_check`, `resolve_note`.
    """
    builder = FindingsLedgerBuilder(pr_id="42", output_dir=str(tmp_path))
    with pytest.raises(ValueError):
        call(builder)


class TestNoteSettlesVerifyItems:
    """WooCommerce PR #68063: the orchestrator's note on the lockfile
    regeneration was confirmed by the reconciliator's own reproduction and
    settled V2 and V3, yet the record read both as unverified because only
    checks carried `verifies` and a check needs reviewer sources. A confirmed
    note may now cite the items its evidence settles."""

    def test_a_confirmed_note_carries_the_items_it_settles(self, tmp_path):
        builder = FindingsLedgerBuilder(pr_id="42", output_dir=str(tmp_path))
        builder.resolve_note("n1", outcome="confirmed", evidence="regenerated the lockfile",
                             verifies=["V2", "V3", "V2"])
        assert builder.orchestrator_notes == [{
            "id": "n1", "outcome": "confirmed",
            "evidence": "regenerated the lockfile", "verifies": ["V2", "V3"],
        }]

    def test_no_citation_leaves_the_note_as_before(self, tmp_path):
        builder = FindingsLedgerBuilder(pr_id="42", output_dir=str(tmp_path))
        builder.resolve_note("n1", outcome="confirmed", evidence="e", verifies=[])
        builder.resolve_note("n2", outcome="refuted", evidence="e")
        assert all("verifies" not in n for n in builder.orchestrator_notes)


def _reader_ready(
    builder, *, grouped=0, verified=0, false_positive=0, out_of_scope=0,
):
    builder.set_reconciliation(
        grouped_concern_count=grouped,
        verified_concern_count=verified,
        false_positive_concern_count=false_positive,
        out_of_scope_concern_count=out_of_scope,
    )
    data = builder.to_dict()
    data["meta"]["reconciliation"].update({
        "input_finding_count": grouped,
        "contributing_agent_count": 1 if grouped else 0,
        "reviewing_agents": ["security-reviewer"] if grouped else [],
        "not_applicable_agents": [],
        "dispatched_agents": ["security-reviewer"] if grouped else [],
        "missing_agents": [],
    })
    return data


@pytest.mark.parametrize(("call", "collection"), [
    pytest.param(
        lambda b: b.add_finding(
            severity="low", title="t", file="f", line=1,
            description="d", recommendation="r",
            sources=[{"reviewer": "security-review", "id": "f1"}],
            severity_note="x" * 4097,
        ),
        "findings",
        id="over_length-severity_note",
    ),
    pytest.param(
        lambda b: b.resolve_note(
            "n1", outcome="not_checked", evidence="contains\x07control",
        ),
        "orchestrator_notes",
        id="control_char-evidence",
    ),
])
def test_bounded_provenance_is_rejected_before_mutation(
    tmp_path, call, collection,
):
    """One row per failure mode of the shared `normalize_bounded_text`
    (over-length, control character); every call site (add_finding,
    drop_finding, drop_check, resolve_note) routes through it identically."""
    builder = FindingsLedgerBuilder(pr_id="42", output_dir=str(tmp_path))

    with pytest.raises(ValueError, match="at most 4096"):
        call(builder)

    assert getattr(builder, collection) == []
    from review.critic_adjustments import validate_findings_document
    validate_findings_document(_reader_ready(builder))


@pytest.mark.parametrize(("record", "collection", "counts"), [
    pytest.param(
        lambda b: b.drop_finding(
            "security-review", "f1", reason="false_positive", evidence="e",
        ),
        "dropped_findings",
        {"grouped": 1, "false_positive": 1},
        id="shared_require_new_source_identity-drop_finding",
    ),
    pytest.param(
        lambda b: b.resolve_note("n1", outcome="confirmed", evidence="e"),
        "orchestrator_notes",
        {},
        id="inline_identity_check-resolve_note",
    ),
])
def test_repeated_provenance_identity_is_rejected_before_mutation(
    tmp_path, record, collection, counts,
):
    builder = FindingsLedgerBuilder(pr_id="42", output_dir=str(tmp_path))
    record(builder)

    with pytest.raises(ValueError, match="already recorded"):
        record(builder)

    assert len(getattr(builder, collection)) == 1
    from review.critic_adjustments import validate_findings_document
    validate_findings_document(_reader_ready(builder, **counts))


class TestRecordCheckCarriesMergedSources:
    """The save gate demands each merged source's method verbatim and the
    union of their Verify items; the builder reads them from the
    reconciliation context so the reconciliator never assembles them by
    hand (run 4's first save was rejected four times over for this)."""

    def _context(self, tmp_path):
        write_reconciliation_context(tmp_path, {
            "security-review": {"checks": [
                {"id": "c1", "question": "q", "method": "grep -rn nonce src/", "result": "held", "verifies": ["V1"]},
            ]},
            "code-review": {"checks": [
                {"id": "c3", "question": "q", "method": "read src/a.php:10-40", "result": "held", "verifies": ["V2", "V1"]},
            ]},
        })

    def test_appends_source_methods_and_unions_verifies(self, tmp_path):
        self._context(tmp_path)
        builder = FindingsLedgerBuilder(pr_id="42", output_dir=str(tmp_path))
        builder.record_check(
            question="q", method="I re-ran the grep and read the file myself", result="held",
            source_reviewers=["security-reviewer", "code-reviewer"],
            sources=[{"reviewer": "security-review", "id": "c1"}, {"reviewer": "code-review", "id": "c3"}],
        )
        check = builder.checks[0]
        assert check["method"] == (
            "I re-ran the grep and read the file myself\n"
            "[security-review:c1] grep -rn nonce src/\n"
            "[code-review:c3] read src/a.php:10-40"
        )
        assert check["verifies"] == ["V1", "V2"]

    def test_a_method_already_carried_verbatim_is_not_repeated(self, tmp_path):
        self._context(tmp_path)
        builder = FindingsLedgerBuilder(pr_id="42", output_dir=str(tmp_path))
        builder.record_check(
            question="q", method="mine; grep -rn nonce src/", result="held",
            sources=[{"reviewer": "security-review", "id": "c1"}], verifies=["V1"],
        )
        check = builder.checks[0]
        assert check["method"] == "mine; grep -rn nonce src/"
        assert check["verifies"] == ["V1"]

    def test_without_a_context_the_method_is_taken_as_given(self, tmp_path):
        builder = FindingsLedgerBuilder(pr_id="42", output_dir=str(tmp_path))
        builder.record_check(
            question="q", method="m", result="r",
            sources=[{"reviewer": "security-review", "id": "c1"}],
        )
        assert builder.checks[0]["method"] == "m"

    def test_the_sources_alone_can_be_the_method(self, tmp_path):
        self._context(tmp_path)
        builder = FindingsLedgerBuilder(pr_id="42", output_dir=str(tmp_path))
        builder.record_check(
            question="q", method=None, result="held",
            sources=[{"reviewer": "code-review", "id": "c3"}],
        )
        assert builder.checks[0]["method"] == "[code-review:c3] read src/a.php:10-40"


def test_update_check_keeps_the_sources_it_recorded(tmp_path):
    """The base validator knows the reviewer check grammar; a ledger check
    always carries `sources`, and correcting its text must not trip on them."""
    builder = FindingsLedgerBuilder(pr_id="1", output_dir=str(tmp_path))
    builder.record_check(
        question="Q?", method="m", result="r", source_reviewers=["code-review"],
        sources=[{"reviewer": "code-review", "id": "c1"}],
    )
    builder.update_check("c1", result="corrected")
    assert builder.checks[0]["result"] == "corrected"
    assert builder.checks[0]["sources"] == [{"reviewer": "code-review", "id": "c1"}]
