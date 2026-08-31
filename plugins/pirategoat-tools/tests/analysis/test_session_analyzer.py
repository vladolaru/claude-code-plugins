"""
Tests for quality metrics extraction from reviewer session JSONL logs.

Validates the --quality-metrics mode of analysis/session_analyzer.py:
- Parsing agent Write output (JSON) to extract finding counts
- Handling missing/partial data gracefully
- Overlap detection across agents
- Severity disagreements across agents
- Empty session data
"""

import importlib.util
import json
import sys
from pathlib import Path

import pytest

# Import the module under test (hyphenated filename requires importlib)
TESTS_DIR = Path(__file__).resolve().parent.parent  # analysis/ -> tests/
PLUGIN_ROOT = TESTS_DIR.parent
SCRIPT_PATH = PLUGIN_ROOT / "scripts" / "analysis" / "session_analyzer.py"
SCRIPTS_DIR = PLUGIN_ROOT / "scripts"

sys.path.insert(0, str(SCRIPTS_DIR))
sys.path.insert(0, str(TESTS_DIR))
from helpers.review_fixtures import canonical_review_document

_spec = importlib.util.spec_from_file_location("analyze_reviewer_sessions", str(SCRIPT_PATH))
_mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_mod)

_bootstrap_spec = importlib.util.spec_from_file_location(
    "review_bootstrap_for_session_test",
    str(PLUGIN_ROOT / "scripts" / "review" / "agent" / "bootstrap.py"),
)
_bootstrap_mod = importlib.util.module_from_spec(_bootstrap_spec)
_bootstrap_spec.loader.exec_module(_bootstrap_mod)


def _real_bootstrap_builder_command(tmp_path, *, plugin_version=""):
    """Extract the builder command from REAL build_output() prose.

    Every other test in this file hand-writes the envelope string, so an
    envelope-shape change in bootstrap leaves them all green while session
    analysis silently stops recognizing builder saves. This helper is the
    one that would fail.
    """
    prompt = _bootstrap_mod.build_output(
        agent_name="security-reviewer",
        plugin_root=str(PLUGIN_ROOT),
        status="OK",
        review_rules="",
        domain_rules=None,
        scope_output="=== REVIEW SCOPE ===\nSTATUS: OK",
        exploration_scope=None,
        output_dir=str(tmp_path),
        pr_number="42",
        reviewer_name="security",
        review_claimable_count=0,
        has_php=False,
        plugin_version=plugin_version,
    )
    start = prompt.index("PIRATEGOAT_PLUGIN_ROOT=")
    end = prompt.index("\nPY", start) + len("\nPY")
    return prompt[start:end]


def _review_artifact(output_dir, reviewer="security", suffix="json"):
    """Return the canonical per-reviewer artifact path for one run."""
    return Path(output_dir, "reviewers", reviewer, f"review.{suffix}")


def _write_review_artifact(output_dir, content, reviewer="security"):
    """Write a finalized review fixture at its canonical run-relative path."""
    artifact = _review_artifact(output_dir, reviewer)
    artifact.parent.mkdir(parents=True, exist_ok=True)
    artifact.write_text(content)
    return artifact


extract_agent_findings = _mod.extract_agent_findings
detect_overlaps = _mod.detect_overlaps


def test_reconciliator_classification_accepts_run_relative_reviewer_paths():
    data = {
        "files_read": [
            f"reviewers/reviewer-{index}/review.json" for index in range(5)
        ],
        "bash_commands": [],
        "final_texts": [],
    }

    assert _mod.classify_dispatch(data) == "reconciliator"


# ---------------------------------------------------------------------------
# Helpers: build mock review JSON output (what an agent writes via Write tool)
# ---------------------------------------------------------------------------

def _make_review_json(
    reviewer="security",
    findings=None,
    verdict="comment",
):
    """Build a canonical finalized-review JSON dictionary."""
    if findings is None:
        findings = []

    review = canonical_review_document(
        reviewer, [finding["severity"] for finding in findings]
    )
    for canonical, supplied in zip(review["findings"], findings):
        canonical.update({
            field: supplied[field]
            for field in (
                "title", "file", "line", "description",
                "recommendation", "confidence",
            )
        })
    return review


def _make_finding(
    severity="high",
    title="Test Issue",
    file="src/Foo.php",
    line=10,
    finding_id="abc1",
    description="desc",
    recommendation="fix it",
):
    """Build a single finding dict."""
    return {
        "id": finding_id,
        "category": "general",
        "severity": severity,
        "title": title,
        "file": file,
        "line": line,
        "description": description,
        "recommendation": recommendation,
        "confidence": 0.9,
    }


# ---------------------------------------------------------------------------
# Tests: extract_agent_findings
# ---------------------------------------------------------------------------

class TestExtractAgentFindings:
    """extract_agent_findings returns canonical finding-domain values."""

    def test_basic_extraction(self):
        """Extracts finding counts from a well-formed review JSON."""
        review = _make_review_json(
            reviewer="security",
            findings=[
                _make_finding(severity="critical", title="SQL Injection", file="a.php", line=10, finding_id="s1"),
                _make_finding(severity="high", title="XSS", file="b.php", line=20, finding_id="s2"),
                _make_finding(severity="medium", title="Missing escape", file="c.php", line=30, finding_id="s3"),
            ],
        )
        result = extract_agent_findings(review)
        assert result["total_findings"] == 3
        assert result["findings_by_severity"]["critical"] == 1
        assert result["findings_by_severity"]["high"] == 1
        assert result["findings_by_severity"]["medium"] == 1
        assert result["findings_by_severity"].get("low", 0) == 0

    def test_findings_list_preserved(self):
        """Parsed findings are returned for downstream overlap detection."""
        findings = [
            _make_finding(severity="high", file="x.php", line=42, finding_id="h1"),
            _make_finding(severity="low", file="y.php", line=7, finding_id="h2"),
        ]
        review = _make_review_json(findings=findings)
        result = extract_agent_findings(review)
        assert len(result["findings"]) == 2
        assert result["findings"][0]["file"] == "x.php"
        assert result["findings"][0]["line"] == 42

    def test_missing_findings_key(self):
        """Gracefully handles review JSON with no findings key."""
        review = {"pr_id": "1", "reviewer": "security", "verdict": "approve"}
        result = extract_agent_findings(review)
        assert result["total_findings"] == 0

    def test_missing_summary_key(self):
        """Gracefully handles review JSON with no summary key."""
        review = {
            "pr_id": "1",
            "reviewer": "security",
            "verdict": "comment",
            "findings": [_make_finding(severity="high")],
        }
        result = extract_agent_findings(review)
        assert result["total_findings"] == 1
        assert result["findings_by_severity"]["high"] == 1

    def test_non_dict_input(self):
        """Non-dict input returns empty result."""
        result = extract_agent_findings("not a dict")
        assert result["total_findings"] == 0


# ---------------------------------------------------------------------------

class TestDetectOverlaps:
    """detect_overlaps(all_findings) → dict with overlap_clusters, severity_disagreements."""

    def test_basic_overlap(self):
        """Two agents flag the same file+line → one overlap cluster."""
        findings = [
            {"agent": "security", "file": "src/Foo.php", "line": 42, "severity": "critical", "title": "SQL Injection"},
            {"agent": "code", "file": "src/Foo.php", "line": 42, "severity": "critical", "title": "Unescaped input"},
        ]
        result = detect_overlaps(findings)
        assert result["overlap_clusters"] == 1
        assert result["severity_disagreements"] == 0

    def test_no_overlaps(self):
        """Different files → no overlap."""
        findings = [
            {"agent": "security", "file": "src/A.php", "line": 10, "severity": "high", "title": "Issue A"},
            {"agent": "code", "file": "src/B.php", "line": 20, "severity": "high", "title": "Issue B"},
        ]
        result = detect_overlaps(findings)
        assert result["overlap_clusters"] == 0
        assert result["severity_disagreements"] == 0

    def test_severity_disagreement(self):
        """Same file+line but different severity → severity disagreement."""
        findings = [
            {"agent": "security", "file": "src/Foo.php", "line": 42, "severity": "critical", "title": "SQL Injection"},
            {"agent": "code", "file": "src/Foo.php", "line": 42, "severity": "medium", "title": "Input handling"},
        ]
        result = detect_overlaps(findings)
        assert result["overlap_clusters"] == 1
        assert result["severity_disagreements"] == 1

    def test_three_agents_same_location(self):
        """Three agents at the same file+line → still one cluster."""
        findings = [
            {"agent": "security", "file": "x.php", "line": 5, "severity": "high", "title": "X1"},
            {"agent": "code", "file": "x.php", "line": 5, "severity": "high", "title": "X2"},
            {"agent": "perf", "file": "x.php", "line": 5, "severity": "medium", "title": "X3"},
        ]
        result = detect_overlaps(findings)
        assert result["overlap_clusters"] == 1
        # perf has different severity than security/code
        assert result["severity_disagreements"] == 1

    def test_empty_findings(self):
        """Empty findings list → no overlaps."""
        result = detect_overlaps([])
        assert result["overlap_clusters"] == 0
        assert result["severity_disagreements"] == 0

    def test_none_line_ignored(self):
        """Findings with None line are excluded from overlap detection."""
        findings = [
            {"agent": "security", "file": "a.php", "line": None, "severity": "high", "title": "NoLine1"},
            {"agent": "code", "file": "a.php", "line": None, "severity": "high", "title": "NoLine2"},
        ]
        result = detect_overlaps(findings)
        assert result["overlap_clusters"] == 0

    def test_overlap_details_returned(self):
        """Overlap details include file, line, and involved agents."""
        findings = [
            {"agent": "security", "file": "src/Foo.php", "line": 42, "severity": "critical", "title": "A"},
            {"agent": "code", "file": "src/Foo.php", "line": 42, "severity": "high", "title": "B"},
        ]
        result = detect_overlaps(findings)
        assert len(result["clusters"]) == 1
        cluster = result["clusters"][0]
        assert cluster["file"] == "src/Foo.php"
        assert cluster["line"] == 42
        assert set(cluster["agents"]) == {"security", "code"}


# ---------------------------------------------------------------------------
# Tests: format_quality_text_report / format_quality_json_report
# ---------------------------------------------------------------------------

format_quality_text_report = _mod.format_quality_text_report
format_quality_json_report = _mod.format_quality_json_report


class TestUnrelatedWritesInQualityReport:
    """Quality reports ignore Write payloads that are not review results."""

    @pytest.mark.parametrize(
        "formatter,path,content",
        [
            pytest.param(format_quality_json_report, ".nvmrc", "22\n", id="json-scalar"),
            pytest.param(
                format_quality_text_report,
                "package.json",
                json.dumps({"name": "example"}),
                id="unrelated-json-object",
            ),
        ],
    )
    def test_ignores_non_review_write_payloads(self, formatter, path, content):
        dispatch = (
            {"agent_name": "general-purpose"},
            {
                "write_outputs": [{"content": content, "path": path}],
                "files_read": [],
                "bash_commands": [],
                "final_texts": [],
            },
        )

        report = formatter([dispatch], None)

        assert "unknown" not in report

    def test_ignores_retired_review_payload(self):
        dispatch = (
            {"agent_name": "security-reviewer"},
            {
                "write_outputs": [{
                    "content": json.dumps({
                        "schema": 1,
                        "reviewer": "security",
                        "findings": [],
                        "issues": [],
                        "verdict": "approve",
                    }),
                    "path": "reviewers/security/review.json",
                }],
                "files_read": [],
                "bash_commands": [],
                "final_texts": [],
            },
        )

        report = json.loads(format_quality_json_report([dispatch], None))

        assert report["per_agent"] == []


# ---------------------------------------------------------------------------
# Bash builder heredoc recognition (the mandated save mechanism)
# ---------------------------------------------------------------------------

def _builder_heredoc(reviewer="security", body=None, output_dir="/tmp/pr-review-42"):
    """Build the canonical one-shot builder command bootstrap prescribes."""
    if body is None:
        body = (
            "import sys, os\n"
            'plugin_root = os.environ["PIRATEGOAT_PLUGIN_ROOT"]\n'
            "sys.path.insert(0, os.path.join(plugin_root, \"scripts\"))\n"
            "from review.agent.output import ReviewOutputBuilder\n"
            'builder = ReviewOutputBuilder.open('
            'os.environ["PIRATEGOAT_OUTPUT_DIR"], '
            'os.environ["PIRATEGOAT_PR_ID"], '
            'os.environ["PIRATEGOAT_REVIEWER_NAME"])\n'
            'builder.add_finding(severity="high", title="Reviewer\'s finding — '
            'unsafe echo", file="src/f.php",\n'
            '    description="What is wrong", recommendation="How to fix",\n'
            '    category="xss", line=42, confidence=0.9)\n'
            "builder.save_draft()\n"
        )
    return (
        "PIRATEGOAT_PLUGIN_ROOT='/plug' "
        f"PIRATEGOAT_OUTPUT_DIR='{output_dir}' "
        f"PIRATEGOAT_REVIEWER_NAME='{reviewer}' "
        "PIRATEGOAT_PR_ID='42' PIRATEGOAT_PLUGIN_VERSION='1.114.0' "
        "python3 <<'PY'\n"
        f"{body}"
        "PY"
    )


class TestArtifactBackedReviews:
    """The mandated heredoc saves through ReviewOutputBuilder, so the review
    JSON is on disk and never in the transcript. Analysis reads the artifact
    the envelope names; an artifact it cannot read is unmeasured, which is a
    missing record rather than a measured zero."""

    def _quality_report(self, data):
        return json.loads(
            _mod.format_quality_json_report(
                [({"agent_name": "security-reviewer"}, data)], None
            )
        )

    def test_real_bootstrap_envelope_reports_the_saved_artifact(self, tmp_path):
        artifact = _write_review_artifact(
            tmp_path,
            json.dumps(
                canonical_review_document("security", ["high", "medium"])
            ),
        )
        log = tmp_path / "agent.jsonl"
        log.write_text(
            json.dumps(_bash_entry(_real_bootstrap_builder_command(tmp_path)))
            + "\n"
        )

        data = _mod.parse_subagent_log(str(log))
        report = self._quality_report(data)

        assert [record["path"] for record in data["write_outputs"]] == [
            str(artifact)
        ]
        [agent_record] = report["per_agent"]
        assert agent_record["agent_name"] == "security"
        assert agent_record["total_findings"] == 2
        assert agent_record["findings_by_severity"] == {"high": 1, "medium": 1}

    def test_a_failed_builder_call_does_not_attribute_a_retry_artifact(
        self, tmp_path
    ):
        """A builder call whose tool result is an error persisted nothing.
        The artifact may exist because a later dispatch retried into the
        same output directory; reading it for the failed call would count
        that reviewer's findings once per dispatch."""
        _write_review_artifact(
            tmp_path,
            json.dumps(canonical_review_document("security", ["high"])),
        )
        log = tmp_path / "agent.jsonl"
        entries = [
            _bash_entry(_real_bootstrap_builder_command(tmp_path), tool_id="b1"),
            {
                "type": "user",
                "message": {
                    "role": "user",
                    "content": [{
                        "type": "tool_result",
                        "tool_use_id": "b1",
                        "is_error": True,
                        "content": "REJECTED: review draft is absent",
                    }],
                },
            },
        ]
        log.write_text("".join(json.dumps(e) + "\n" for e in entries))

        data = _mod.parse_subagent_log(str(log))

        assert data["write_outputs"] == []
        assert self._quality_report(data)["per_agent"] == []

    def _session_log(self, tmp_path, session_id, entries):
        log = tmp_path / "sessions" / session_id / "subagents" / "agent-1.jsonl"
        log.parent.mkdir(parents=True)
        log.write_text("".join(json.dumps(e) + "\n" for e in entries))
        return log

    @pytest.mark.parametrize(
        "manifest_session,expected_records",
        [
            pytest.param("session-A", 1, id="same-run"),
            pytest.param("session-B", 0, id="later-run-replaced-the-artifact"),
            pytest.param(None, 0, id="no-provenance-is-unmeasured"),
        ],
    )
    def test_the_artifact_must_belong_to_the_transcripts_run(
        self, tmp_path, manifest_session, expected_records
    ):
        """Output directories are reused per PR/branch and swept at step 1,
        so the artifact on disk is the latest run's. The manifest names
        that run's session; a transcript from another session — or one
        whose run left no manifest to check — is credited with nothing
        rather than with a possibly foreign run's findings."""
        out = tmp_path / "out"
        out.mkdir()
        _write_review_artifact(
            out,
            json.dumps(canonical_review_document("security", ["high"])),
        )
        if manifest_session is not None:
            # Telemetry keeps the log and manifest outside the output dir and
            # leaves only a marker naming the log; the analyzer resolves the
            # manifest through that marker, never by scanning the directory.
            logs = tmp_path / "logs"
            logs.mkdir()
            Path(logs, "pr-run1--x.manifest.json").write_text(
                json.dumps({"run": {"session_id": manifest_session}})
            )
            Path(out, ".telemetry-log-path").write_text(
                str(logs / "pr-run1--x.jsonl")
            )
        log = self._session_log(
            tmp_path, "session-A",
            [_bash_entry(_real_bootstrap_builder_command(out))],
        )

        data = _mod.parse_subagent_log(str(log))

        assert len(data["write_outputs"]) == expected_records

    def test_a_failed_write_does_not_shadow_an_earlier_successful_save(
        self, tmp_path
    ):
        good = json.dumps(canonical_review_document("security", ["high"]))
        bad = json.dumps(canonical_review_document("security", ["low", "low"]))
        entries = [
            _write_entry("/out/reviewers/security/review.json", good, tool_id="w1"),
            _write_entry("/out/reviewers/security/review.json", bad, tool_id="w2"),
            {
                "type": "user",
                "message": {"role": "user", "content": [{
                    "type": "tool_result", "tool_use_id": "w2",
                    "is_error": True, "content": "EACCES",
                }]},
            },
        ]
        log = tmp_path / "agent.jsonl"
        log.write_text("".join(json.dumps(e) + "\n" for e in entries))

        data = _mod.parse_subagent_log(str(log))

        assert [r["content"] for r in data["write_outputs"]] == [good]

    def test_write_and_builder_spellings_of_one_artifact_count_once(
        self, tmp_path
    ):
        artifact = _write_review_artifact(
            tmp_path,
            json.dumps(canonical_review_document("security", ["high"])),
        )
        entries = [
            _write_entry(
                f"{tmp_path}/reviewers/security/./review.json", "{}", tool_id="w1"
            ),
            _bash_entry(_real_bootstrap_builder_command(tmp_path), tool_id="b1"),
        ]
        log = tmp_path / "agent.jsonl"
        log.write_text("".join(json.dumps(e) + "\n" for e in entries))

        data = _mod.parse_subagent_log(str(log))

        assert [r["path"] for r in data["write_outputs"]] == [
            str(artifact)
        ]

    @pytest.mark.parametrize(
        "artifact_content,reviewer",
        [
            pytest.param(None, "security", id="absent"),
            pytest.param("{ not json", "security", id="malformed"),
            pytest.param(
                json.dumps({
                    "schema": 1,
                    "reviewer": "security",
                    "findings": [],
                    "issues": [],
                    "verdict": "approve",
                }),
                "security",
                id="retired-schema",
            ),
            # No artifact is ever looked up for these — review_paths()
            # raises on the identity itself before any file is opened, the
            # same ValueError branch _review_from_artifact catches.
            pytest.param(None, "", id="empty-reviewer-identity"),
            pytest.param(
                None, "../escape", id="path-traversal-reviewer-identity"
            ),
        ],
    )
    def test_unreadable_artifact_is_unmeasured_not_zero(
        self, tmp_path, artifact_content, reviewer
    ):
        """An artifact that does not validate is a missing record, never an
        empty findings list — a reviewer whose output was never observed and
        a reviewer who genuinely found nothing are different facts. An
        unsafe reviewer identity (review_paths' own ValueError) is
        unmeasured the same way as a missing or malformed artifact."""
        if artifact_content is not None:
            _write_review_artifact(tmp_path, artifact_content, reviewer)
        log = tmp_path / "agent.jsonl"
        log.write_text(
            json.dumps(
                _bash_entry(
                    _builder_heredoc(
                        reviewer=reviewer, output_dir=str(tmp_path)
                    )
                )
            )
            + "\n"
        )

        data = _mod.parse_subagent_log(str(log))
        report = self._quality_report(data)

        assert data["write_outputs"] == []
        assert report["per_agent"] == []

    def test_non_straight_line_body_is_still_a_measured_builder_save(
        self, tmp_path
    ):
        artifact = _write_review_artifact(
            tmp_path,
            json.dumps(canonical_review_document("security", ["low"])),
        )
        body = (
            "from review.agent.output import ReviewOutputBuilder\n"
            'builder = ReviewOutputBuilder.open("/o", "42", "security")\n'
            "for path in ['src/a.php', 'src/b.php']:\n"
            "    builder.claim_files_reviewed(path)\n"
            "builder.save_draft()\n"
        )
        command = _builder_heredoc(output_dir=str(tmp_path), body=body)
        log = tmp_path / "agent.jsonl"
        log.write_text(json.dumps(_bash_entry(command)) + "\n")

        data = _mod.parse_subagent_log(str(log))
        detail = _mod._categorize_tool_call("Bash", {"command": command})

        assert detail["category"] == "builder-output"
        assert [record["path"] for record in data["write_outputs"]] == [
            str(artifact)
        ]
        document = json.loads(data["write_outputs"][0]["content"])
        assert [finding["severity"] for finding in document["findings"]] == [
            "low"
        ]

    def test_write_tool_save_of_the_same_artifact_counts_once(self, tmp_path):
        artifact = _write_review_artifact(
            tmp_path,
            json.dumps(canonical_review_document("security", ["high"])),
        )
        log = tmp_path / "agent.jsonl"
        entries = [
            _write_tool_entry(
                str(artifact),
                json.dumps(
                    canonical_review_document("security", ["low", "low"])
                ),
            ),
            _bash_entry(_builder_heredoc(output_dir=str(tmp_path))),
        ]
        log.write_text("\n".join(json.dumps(e) for e in entries) + "\n")

        data = _mod.parse_subagent_log(str(log))
        report = self._quality_report(data)

        assert [record["path"] for record in data["write_outputs"]] == [
            str(artifact)
        ]
        [agent_record] = report["per_agent"]
        assert agent_record["dispatches"] == 1
        assert agent_record["total_findings"] == 1
        assert agent_record["findings_by_severity"] == {"high": 1}


def _write_entry(file_path, content, tool_id="write-1"):
    return {
        "type": "assistant",
        "message": {
            "role": "assistant",
            "content": [{
                "type": "tool_use", "id": tool_id, "name": "Write",
                "input": {"file_path": file_path, "content": content},
            }],
        },
    }


def _bash_entry(command, tool_id="bash-1"):
    return {
        "type": "assistant",
        "message": {
            "role": "assistant",
            "content": [
                {
                    "type": "tool_use",
                    "id": tool_id,
                    "name": "Bash",
                    "input": {"command": command},
                }
            ],
        },
    }


class TestTextReportFindingCounts:
    """A save that parses as a review payload carries its exact finding list.
    The keyword heuristic estimates JSON findings by counting '"id"', which
    is only right by accident — applied to a real review it miscounts, so a
    payload that validates is counted directly."""

    def test_artifact_backed_save_counts_findings_exactly(self, tmp_path):
        _write_review_artifact(
            tmp_path,
            json.dumps(
                canonical_review_document("security", ["high", "medium"])
            ),
        )
        log = tmp_path / "agent.jsonl"
        log.write_text(
            json.dumps(
                _bash_entry(_builder_heredoc(output_dir=str(tmp_path)))
            )
            + "\n"
        )
        data = _mod.parse_subagent_log(str(log))
        # A prose save has no exact structure — it keeps the heuristic,
        # displayed as approximate.
        data["write_outputs"].append({
            "path": str(_review_artifact(tmp_path, suffix="md")),
            "content": "## Finding A\n",
        })
        meta = {"session_id": "session-1234", "date": "2026-07-29"}

        report = _mod.format_text_report([(meta, data)], "security-reviewer")

        assert ", 2 findings)" in report
        assert ", ~1 findings)" in report


def _write_tool_entry(path, content, tool_id="write-1"):
    return {
        "type": "assistant",
        "message": {
            "role": "assistant",
            "content": [
                {
                    "type": "tool_use",
                    "id": tool_id,
                    "name": "Write",
                    "input": {"file_path": path, "content": content},
                }
            ],
        },
    }


class TestWriteRecordDeduplication:
    """Write records reduce like the artifact reduction they sit beside:
    same normalized path collapses to the last write, and a non-string path
    carries no dedup identity — it must never raise, just stay unreduced."""

    @pytest.mark.parametrize(
        "malformed_path",
        [
            pytest.param(7, id="hashable-non-string"),
            # An unhashable path (a list, from a malformed transcript) must
            # never reach a dict membership check unguarded — that raises
            # TypeError and crashes the whole run.
            pytest.param(["nested", "path"], id="unhashable-non-string"),
        ],
    )
    def test_non_string_path_is_kept_without_dedup_identity(
        self, tmp_path, malformed_path
    ):
        log = tmp_path / "agent.jsonl"
        entries = [
            _write_tool_entry(
                malformed_path, "first malformed path", tool_id="write-1"
            ),
            _write_tool_entry(
                malformed_path, "second malformed path", tool_id="write-2"
            ),
        ]
        log.write_text("\n".join(json.dumps(e) for e in entries) + "\n")

        data = _mod.parse_subagent_log(str(log))

        assert data["write_outputs"] == [
            {"path": malformed_path, "content": "first malformed path"},
            {"path": malformed_path, "content": "second malformed path"},
        ]

    def test_canonicalizes_path_before_last_save_wins(self, tmp_path):
        log = tmp_path / "agent.jsonl"
        entries = [
            _write_tool_entry(
                "/out/reviewers/security/review.json",
                json.dumps({"reviewer": "security", "findings": []}),
                tool_id="write-1",
            ),
            _write_tool_entry(
                "/out/reviewers/security/./review.json",
                json.dumps({"reviewer": "security", "findings": [], "v": 2}),
                tool_id="write-2",
            ),
        ]
        log.write_text("\n".join(json.dumps(e) for e in entries) + "\n")

        data = _mod.parse_subagent_log(str(log))

        [record] = data["write_outputs"]
        assert record["path"] == "/out/reviewers/security/./review.json"
        assert json.loads(record["content"])["v"] == 2
