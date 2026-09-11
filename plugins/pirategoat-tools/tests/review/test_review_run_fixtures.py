"""The three audited runs, replayed through the finalize projections.

Expected measurements are cross-checked against the three end-to-end audits
under .claude/docs/analysis/2026-09-04-claude-pr-review-run-*-e2e-audit.md.
Historical missingness remains evidence, never synthesized instrumentation.
"""

import copy
import json
import re
import sys
from collections import Counter
from pathlib import Path

import pytest

TESTS_DIR = Path(__file__).resolve().parent.parent
if str(TESTS_DIR) not in sys.path:
    sys.path.insert(0, str(TESTS_DIR))

from helpers import review_run_fixture
from helpers.review_run_fixture import FIXTURE_NAMES, fixture_dir
from analysis.review_metrics.measure import _usage_shares
from review import manifest_sections
from review.critic_adjustments import read_committed_proposal
from review.evidence_manifest import build_evidence_manifest
from review.telemetry import _project_base_fetch, _project_scope_check


@pytest.mark.parametrize("name", FIXTURE_NAMES)
def test_historical_missingness_reads_as_unmeasured(name):
    """Every audited run predates live instrumentation for these six
    projections, so each reads as unmeasured rather than as a synthesized
    zero. All six assert groups share the fixture: pre-fetch range truth,
    dispatch signals, lineage-ledger evidence families, usage tool calls,
    the critic proposal's digest binding, and verify-item settlement."""
    output_dir = str(fixture_dir(name))

    git = json.loads((fixture_dir(name) / "review-context.json").read_text())["git"]
    assert "base_fetch" not in git
    assert "scope_check" not in git
    assert _project_base_fetch(git.get("base_fetch")) is None
    assert _project_scope_check(git.get("scope_check")) is None

    info = manifest_sections.inspect_dispatch_plan(output_dir, "dispatch_plan")
    assert info["available"]
    signals = Counter(a.get("signal") for a in info["entries"] if a["status"] == "DISPATCH")
    assert set(signals) == {None}
    dispatch = manifest_sections.build_dispatch_manifest(output_dir, info)
    assert dispatch["comparison_available"]
    assert dispatch["agents"]
    assert all(row["initial_signal"] is None for row in dispatch["agents"].values())
    assert all(row["final_signal"] is None for row in dispatch["agents"].values() if row["final_status"] == "DISPATCH")
    overrides = [row for row in dispatch["agents"].values() if row["final_status"] == "SKIPPED_OVERRIDE"]
    assert overrides
    assert all(row["final_signal"] == "override" for row in overrides)

    ledger = json.loads((fixture_dir(name) / "review-findings.json").read_text())
    assert all("sources" not in finding for finding in ledger["findings"])
    assert "dropped_findings" not in ledger
    assert "dropped_checks" not in ledger
    assert "orchestrator_notes" not in ledger

    evidence = build_evidence_manifest(output_dir)
    assert evidence is not None
    assert evidence["findings"]
    assert all(finding["sources"] is None for finding in evidence["findings"])
    assert evidence["dropped_findings"] is None
    assert evidence["checks"]["dropped"] is None
    assert evidence["orchestrator_notes"] is None

    usage = manifest_sections.build_usage_manifest(output_dir)
    assert usage["by_agent"]
    assert all(row["tool_calls"] is None for row in usage["by_agent"])

    verdict, proposal = read_committed_proposal(output_dir)
    assert verdict == "REVISE"
    assert proposal["adjustments"]
    assert all(entry["rationale"] == "<redacted>" for entry in proposal["adjustments"])

    assert evidence["verify_items"] is None
    assert evidence["undeclared_citations"] is None


def _by_action(evidence):
    return {action: (row["proposed"], row["verified"]) for action, row in evidence["critic"]["adjustments"].items()}


# What each audit recorded for its run, as the fixture must still read.
# Synthesis shares are over effective input tokens (the audits' separate
# cost-weighted percentages use a different denominator); critic actions
# are counted under today's vocabulary — a pre-1.119.0 `correct` that
# carried a severity is a `demote` (see `review_run_fixture._normalize_legacy_proposal`).
AUDITED_RUNS = [
    pytest.param(
        "3725-woocommerce-payments-12089", 31.5, {"history-insights-reviewer": 12.9},
        8, "REVISE", "block",
        {"correct": (2, 2), "demote": (2, 2), "remove": (1, 1), "rescope": (1, 1)},
        {"f1": "demote", "f3": "correct", "f4": "demote", "f7": "rescope"},
        id="3725",
    ),
    pytest.param(
        "e582-woocommerce-53136", 47.7, {}, 4, "REVISE", "comment",
        {"correct": (1, 1), "demote": (2, 2)}, None, id="e582",
    ),
    pytest.param(
        "6e6a-woocommerce-35520", 19.0, {"history-insights-reviewer": 4.5},
        5, "REVISE", None, {"correct": (3, 3)}, None, id="6e6a",
    ),
]


@pytest.mark.parametrize(
    "name, synthesis_pct, by_agent_pct, finding_count, verdict, verdict_before, by_action, actions_by_finding",
    AUDITED_RUNS,
)
def test_each_run_matches_its_audit(
    name, synthesis_pct, by_agent_pct, finding_count, verdict, verdict_before, by_action, actions_by_finding,
):
    output_dir = str(fixture_dir(name))
    shares = _usage_shares({"usage": manifest_sections.build_usage_manifest(output_dir)})
    assert shares["synthesis_pct"] == synthesis_pct
    for agent, pct in by_agent_pct.items():
        assert shares["by_agent_pct"][agent] == pct
    evidence = build_evidence_manifest(output_dir)
    assert len(evidence["findings"]) == finding_count
    assert evidence["critic"]["verdict"] == verdict
    assert evidence["critic"]["verdict_before_adjustments"] == verdict_before
    assert _by_action(evidence) == by_action
    if actions_by_finding is not None:
        assert {f["id"]: f["critic_action"] for f in evidence["findings"] if f["critic_action"]} == actions_by_finding


def test_recursive_redaction_preserves_counts_and_critic_coordinates():
    source = {
        "summary": {"total": 1, "by_severity": {"high": 1}},
        "findings": [{"title": "private finding", "source_cited": "wordpress@7.2-alpha:wp-includes/core.php:31"}],
        "checks": [{"question": "private question", "method": "private command", "result": "confirmed"}],
        "adjustments": [{"rationale": "private reason", "fields": {"severity": "high", "file": "src/index.php", "line": 31, "description": "private correction"}}],
        "nested": [{"text": "private nested prose", "references": ["private reference"]}],
        "paths": ["/Users/test/work/file", "/home/test/work/file", "/private/work/file", r"C:\Users\test\file"],
        "trailer": "Claude-Session: https://example.test/private-session",
        "recommendations": {"immediate": ["private recommendation"], "important": [], "suggestions": ["private suggestion"]},
    }
    redacted = review_run_fixture._redact(source)
    assert redacted["summary"] == {"total": 1, "by_severity": {"high": 1}}
    assert redacted["findings"] == [{"title": "<redacted>", "source_cited": "wordpress@7.2-alpha:<redacted>"}]
    assert redacted["checks"] == [{"question": "<redacted>", "method": "<redacted>", "result": "<redacted>"}]
    assert redacted["adjustments"] == [{"rationale": "<redacted>", "fields": {"severity": "high", "file": "src/index.php", "line": 31, "description": "<redacted>"}}]
    assert redacted["nested"] == [{"text": "<redacted>", "references": []}]
    assert redacted["paths"] == ["<redacted>"] * 4
    assert redacted["trailer"] == "Claude-Session: <redacted>"
    assert redacted["recommendations"] == {"immediate": [], "important": [], "suggestions": []}
    assert source["findings"][0]["title"] == "private finding"


def test_redaction_replaces_arbitrary_output_directory_and_planner_prose():
    source = {
        "output_dir": "/opt/local-review",
        "paths": ["prefix:/opt/local-review/review.json", "/opt/local-review"],
        "agents": [{"reason": "private reason", "override_reason": "private override", "focus": "private focus"}],
        "agent_signals": ["private matched text"],
    }
    assert review_run_fixture._redact(source, output_dirs=("/opt/local-review",)) == {
        "output_dir": "<redacted>", "paths": ["prefix:<redacted>/review.json", "<redacted>"],
        "agents": [{"reason": "<redacted>", "override_reason": "<redacted>", "focus": "<redacted>"}],
        "agent_signals": [],
    }


def test_change_purpose_redacts_prose_but_preserves_parse_facts():
    source = """# Private title
## What the change does
Private prose
## Verify
V1. Private requirement (carried over) — source: private discussion
## Context
C1. Private context — source: private issue
## Author's description (extracted)
Private author prose
"""
    assert review_run_fixture._redact_change_purpose(source) == """# <redacted>
## What the change does
<redacted>
## Verify
V1. <redacted> (carried over) — source: <redacted>
## Context
C1. <redacted> — source: <redacted>
## Author's description (extracted)
<redacted>
"""


@pytest.mark.parametrize("prior,final,action", [("high", "low", "demote"), ("low", "high", "promote")])
def test_legacy_severity_action_requires_final_ledger_proof(prior, final, action):
    proposal = {"schema": 2, "adjustments": [{
        "action": "correct", "target": {"kind": "finding", "id": "f1"},
        "fields": {"severity": final, "title": "private title", "confidence": 0.8},
        "rationale": "private rationale", "adjustment_id": "a" * 32,
    }]}
    ledger = {"findings": [{"id": "f1", "severity": final, "critic_adjustment": {
        "action": "correct", "prior": {"severity": prior},
    }}]}
    expected = copy.deepcopy(proposal)
    expected["adjustments"][0]["action"] = action
    assert review_run_fixture._normalize_legacy_proposal(proposal, ledger) == expected
    assert proposal["adjustments"][0]["action"] == "correct"
    # The finding's provenance follows the proposal, so the fixture never
    # counts a `demote` whose finding still says `correct`.
    assert ledger["findings"][0]["critic_adjustment"]["action"] == action


@pytest.mark.parametrize("problem", ["missing_target", "wrong_action", "unchanged_severity"])
def test_legacy_severity_action_rejects_unprovable_changes(problem):
    proposal = {"schema": 2, "adjustments": [{
        "action": "correct", "target": {"kind": "finding", "id": "f1"},
        "fields": {"severity": "low"}, "adjustment_id": "a" * 32,
    }]}
    finding = {"id": "f1", "severity": "low", "critic_adjustment": {
        "action": "correct", "prior": {"severity": "high"},
    }}
    ledger = {"findings": [finding]}
    if problem == "missing_target":
        ledger["findings"] = []
    elif problem == "wrong_action":
        finding["critic_adjustment"]["action"] = "demote"
    else:
        finding["critic_adjustment"]["prior"]["severity"] = "low"
    with pytest.raises(ValueError, match="cannot prove legacy severity adjustment"):
        review_run_fixture._normalize_legacy_proposal(proposal, ledger)


def _assert_prose_redacted(value):
    if isinstance(value, dict):
        for key, item in value.items():
            if key == "recommendations":
                assert isinstance(item, dict)
                assert all(bucket == [] for bucket in item.values())
            elif key == "summary" and isinstance(item, dict):
                _assert_prose_redacted(item)
            elif key in {"title", "description", "recommendation", "question", "method", "result", "evidence", "rationale", "note", "text", "assessment", "summary", "positive_observations", "observations", "code_snippet", "references", "skip_reason"}:
                assert item == "<redacted>" or item == []
            elif key == "source_cited":
                assert item == "<redacted>" or re.fullmatch(r"[^:]+:<redacted>", item)
            else:
                _assert_prose_redacted(item)
    elif isinstance(value, list):
        for item in value:
            _assert_prose_redacted(item)


@pytest.mark.parametrize("name", FIXTURE_NAMES)
def test_fixture_is_redacted(name):
    """Both privacy scans over every committed fixture file: no local paths
    or live session URLs survive in the raw text, and every JSON document's
    result/recommendation prose fields are redacted."""
    paths = [path for path in fixture_dir(name).rglob("*") if path.is_file()]
    assert paths
    assert sum(path.stat().st_size for path in paths) < 1_000_000
    json_paths = []
    for path in paths:
        text = path.read_text(encoding="utf-8")
        assert not re.search(r"/Users/|/home/|/private/|[A-Za-z]:\\", text)
        assert not re.search(r"Claude-Session:\s*(?!<redacted>)\S+", text)
        if path.suffix == ".json":
            json_paths.append(path)
    assert json_paths
    for path in json_paths:
        _assert_prose_redacted(json.loads(path.read_text(encoding="utf-8")))


@pytest.mark.parametrize("name", FIXTURE_NAMES)
def test_capture_is_deterministic_and_keeps_only_the_interface(name, tmp_path, monkeypatch):
    source = fixture_dir(name)
    original = {path.relative_to(source): path.read_bytes() for path in source.rglob("*") if path.is_file()}
    assert original
    monkeypatch.setattr(review_run_fixture, "FIXTURES_DIR", tmp_path)
    target = review_run_fixture.capture(source, name)
    captured = {path.relative_to(target): path.read_bytes() for path in target.rglob("*") if path.is_file()}
    assert captured == original
    review_run_fixture.capture(source, name)
    assert {path.relative_to(target): path.read_bytes() for path in target.rglob("*") if path.is_file()} == captured
    assert {path.relative_to(source): path.read_bytes() for path in source.rglob("*") if path.is_file()} == original
    expected_files = {
        Path("review-context.json"), Path("pipeline/dispatch-plan.initial.json"),
        Path("pipeline/dispatch-plan.json"), Path("pipeline/usage-snapshot.json"),
        Path("pipeline/change-purpose.md"), Path("review-findings.json"),
        Path("synthesis/decision-critic-adjustments.json"), Path("synthesis/decision-critic-verdict.json"),
    }
    plan = json.loads(captured[Path("pipeline/dispatch-plan.json")])
    for agent in plan["agents"]:
        if agent["status"] == "DISPATCH":
            expected_files.add(Path("reviewers") / agent["name"].removesuffix("-reviewer") / "review.json")
    assert set(captured) == expected_files
    context = json.loads(captured[Path("review-context.json")])
    assert set(context) <= {"git", "pr_size", "mode", "source", "changed_files_count", "commit_count", "host_context"}
    assert set(context["git"]) <= {"git_range", "merge_base", "head_sha", "base_ref", "head_ref", "base_fetch", "scope_check", "changed_files", "commit_count"}


def test_missing_source_artifact_refuses_capture_before_writing(tmp_path, monkeypatch):
    monkeypatch.setattr(review_run_fixture, "FIXTURES_DIR", tmp_path / "fixtures")
    with pytest.raises(ValueError, match="cannot read required artifact"):
        review_run_fixture.capture(tmp_path / "absent", FIXTURE_NAMES[0])
    assert not (tmp_path / "fixtures").exists()


def test_relocated_capture_scrubs_recorded_output_directories_everywhere(tmp_path, monkeypatch):
    source = fixture_dir(FIXTURE_NAMES[0])
    read_object = review_run_fixture._read_object

    def read_relocated_document(path):
        value = read_object(path)
        if Path(path).name == "review-context.json":
            # Collect before filtering: neither top-level key survives capture.
            value["output_dir"] = "/opt/reviews/original"
            value["metadata"] = [{"nested": {"output_dir": "/srv/reviews/earlier"}}]
            value["git"]["head_ref"] = "ref:/var/reviews/reviewer/branch"
        elif Path(path).name == "dispatch-plan.json":
            value["output_dir"] = "/opt/reviews/original"
            value["agents"][0]["artifact_ref"] = "ref:/opt/reviews/original/review.json"
        elif Path(path).name == "review.json":
            # Collect all documents before redacting earlier ones.
            value["output_dir"] = "/var/reviews/reviewer"
            for finding in value["findings"]:
                finding["file"] = "/srv/reviews/earlier/src/file.php"
        return value

    monkeypatch.setattr(review_run_fixture, "_read_object", read_relocated_document)
    monkeypatch.setattr(review_run_fixture, "FIXTURES_DIR", tmp_path / "fixtures")
    target = review_run_fixture.capture(source, FIXTURE_NAMES[0])
    context = json.loads((target / "review-context.json").read_text())
    plan = json.loads((target / "pipeline/dispatch-plan.json").read_text())
    reviewer = json.loads((target / "reviewers/a11y/review.json").read_text())
    assert plan["output_dir"] == "<redacted>"
    assert plan["agents"][0]["artifact_ref"] == "ref:<redacted>/review.json"
    assert context["git"]["head_ref"] == "ref:<redacted>/branch"
    assert reviewer["findings"][0]["file"] == "<redacted>/src/file.php"
    for path in target.rglob("*"):
        if path.is_file():
            text = path.read_text()
            assert all(directory not in text for directory in (
                "/opt/reviews/original", "/srv/reviews/earlier", "/var/reviews/reviewer",
            ))


@pytest.mark.parametrize("symlink_location", ["root", "target", "file"])
def test_destination_symlink_refuses_capture_before_any_write(symlink_location, tmp_path, monkeypatch):
    # One `or` expression (`FIXTURES_DIR.is_symlink() or target.is_symlink()
    # or any(path.is_symlink() for path in target.rglob("*"))`); each row
    # covers a distinct clause — "target" is not subsumed by "file" because
    # `target.rglob("*")` walks a symlinked target's resolved contents,
    # which are not themselves symlinks.
    original = fixture_dir(FIXTURE_NAMES[0])
    # The helper creates an expendable source, so even the RED run cannot
    # write through a symlink into the committed or private source runs.
    monkeypatch.setattr(review_run_fixture, "FIXTURES_DIR", tmp_path / "source")
    source = review_run_fixture.capture(original, FIXTURE_NAMES[0])
    source_bytes = {path.relative_to(source): path.read_bytes() for path in source.rglob("*") if path.is_file()}
    root = tmp_path / "fixtures"
    target = root / FIXTURE_NAMES[0]
    external = tmp_path / "external"
    external.mkdir()
    if symlink_location == "root":
        root.symlink_to(external, target_is_directory=True)
    elif symlink_location == "target":
        root.mkdir()
        target.symlink_to(external, target_is_directory=True)
    else:
        target.mkdir(parents=True)
        (target / "review-context.json").symlink_to(source / "review-context.json")
    before = set(tmp_path.rglob("*"))
    monkeypatch.setattr(review_run_fixture, "FIXTURES_DIR", root)
    with pytest.raises(ValueError, match="fixture destination contains a symlink"):
        review_run_fixture.capture(source, FIXTURE_NAMES[0])
    assert set(tmp_path.rglob("*")) == before
    assert list(external.iterdir()) == []
    assert {path.relative_to(source): path.read_bytes() for path in source.rglob("*") if path.is_file()} == source_bytes


@pytest.mark.parametrize("verdict", ["unknown", "STAND"])
def test_invalid_source_marker_refuses_capture_before_writing(verdict, tmp_path, monkeypatch):
    source = fixture_dir(FIXTURE_NAMES[0])
    read_object = review_run_fixture._read_object

    def read_with_invalid_verdict(path):
        value = read_object(path)
        if Path(path).name == "decision-critic-verdict.json":
            value["verdict"] = verdict
        return value

    # Corrupt only the read boundary's in-memory value; source bytes stay intact.
    monkeypatch.setattr(review_run_fixture, "_read_object", read_with_invalid_verdict)
    monkeypatch.setattr(review_run_fixture, "FIXTURES_DIR", tmp_path / "fixtures")
    with pytest.raises(ValueError, match="source critic marker failed validation"):
        review_run_fixture.capture(source, FIXTURE_NAMES[0])
    assert not (tmp_path / "fixtures").exists()


def test_capture_refuses_unknown_fixture_names(tmp_path, monkeypatch):
    monkeypatch.setattr(review_run_fixture, "FIXTURES_DIR", tmp_path / "fixtures")
    with pytest.raises(ValueError, match="unknown fixture name"):
        review_run_fixture.capture(tmp_path, "../escape")
    assert not (tmp_path / "fixtures").exists()
