"""
Tests for quality metrics extraction from reviewer session JSONL logs.

Validates the --quality-metrics mode of analysis/session_analyzer.py:
- Parsing agent Write output (JSON) to extract finding counts
- Parsing ingest subagent log to extract categorization outcomes
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

_spec = importlib.util.spec_from_file_location("analyze_reviewer_sessions", str(SCRIPT_PATH))
_mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_mod)

extract_agent_findings = _mod.extract_agent_findings
extract_ingest_outcomes = _mod.extract_ingest_outcomes
compute_survival_rate = _mod.compute_survival_rate
detect_overlaps = _mod.detect_overlaps


# ---------------------------------------------------------------------------
# Helpers: build mock review JSON output (what an agent writes via Write tool)
# ---------------------------------------------------------------------------

def _make_review_json(
    reviewer="security",
    issues=None,
    verdict="comment",
):
    """Build a review JSON dict matching ReviewOutputBuilder.to_dict() schema."""
    if issues is None:
        issues = []

    severity_counts = {"critical": 0, "high": 0, "medium": 0, "low": 0, "info": 0}
    for issue in issues:
        sev = issue.get("severity", "info")
        if sev in severity_counts:
            severity_counts[sev] += 1

    return {
        "pr_id": "42",
        "reviewer": reviewer,
        "verdict": verdict,
        "summary": {
            "total_issues": len(issues),
            "by_severity": severity_counts,
        },
        "issues": issues,
        "meta": {
            "files_reviewed": 5,
            "review_duration_ms": 12000,
            "confidence_score": 0.9,
        },
    }


def _make_issue(
    severity="high",
    title="Test Issue",
    file="src/Foo.php",
    line=10,
    issue_id="abc1",
    description="desc",
    recommendation="fix it",
):
    """Build a single issue dict."""
    return {
        "id": issue_id,
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
    """extract_agent_findings(write_output) → dict with total, by_severity, issues list."""

    def test_basic_extraction(self):
        """Extracts finding counts from a well-formed review JSON."""
        review = _make_review_json(
            reviewer="security",
            issues=[
                _make_issue(severity="critical", title="SQL Injection", file="a.php", line=10, issue_id="s1"),
                _make_issue(severity="high", title="XSS", file="b.php", line=20, issue_id="s2"),
                _make_issue(severity="medium", title="Missing escape", file="c.php", line=30, issue_id="s3"),
            ],
        )
        result = extract_agent_findings(review)
        assert result["total_findings"] == 3
        assert result["findings_by_severity"]["critical"] == 1
        assert result["findings_by_severity"]["high"] == 1
        assert result["findings_by_severity"]["medium"] == 1
        assert result["findings_by_severity"].get("low", 0) == 0

    def test_empty_issues(self):
        """Agent produced zero findings — clean approve."""
        review = _make_review_json(reviewer="perf", issues=[], verdict="approve")
        result = extract_agent_findings(review)
        assert result["total_findings"] == 0
        assert all(v == 0 for v in result["findings_by_severity"].values())

    def test_issues_list_preserved(self):
        """The parsed issues list is returned for downstream overlap detection."""
        issues = [
            _make_issue(severity="high", file="x.php", line=42, issue_id="h1"),
            _make_issue(severity="low", file="y.php", line=7, issue_id="h2"),
        ]
        review = _make_review_json(issues=issues)
        result = extract_agent_findings(review)
        assert len(result["issues"]) == 2
        assert result["issues"][0]["file"] == "x.php"
        assert result["issues"][0]["line"] == 42

    def test_missing_issues_key(self):
        """Gracefully handles review JSON with no 'issues' key."""
        review = {"pr_id": "1", "reviewer": "security", "verdict": "approve"}
        result = extract_agent_findings(review)
        assert result["total_findings"] == 0

    def test_missing_summary_key(self):
        """Gracefully handles review JSON with no 'summary' key — counts from issues."""
        review = {
            "pr_id": "1",
            "reviewer": "security",
            "verdict": "comment",
            "issues": [_make_issue(severity="high")],
        }
        result = extract_agent_findings(review)
        assert result["total_findings"] == 1
        assert result["findings_by_severity"]["high"] == 1

    def test_non_dict_input(self):
        """Non-dict input returns empty result."""
        result = extract_agent_findings("not a dict")
        assert result["total_findings"] == 0

    def test_none_input(self):
        """None input returns empty result."""
        result = extract_agent_findings(None)
        assert result["total_findings"] == 0


# ---------------------------------------------------------------------------
# Tests: extract_ingest_outcomes
# ---------------------------------------------------------------------------

class TestExtractIngestOutcomes:
    """extract_ingest_outcomes(ingest_texts) → dict with category counts."""

    def test_all_categories(self):
        """Recognizes all five ingest categories in text output."""
        texts = [
            "F1 [IN_SCOPE] VERIFIED:\n  Status: VERIFIED\n  Rationale: confirmed issue",
            "F2 [OUT_OF_SCOPE: file not in diff]: Missing escape",
            "F3 [IN_SCOPE] FAILED:\n  Status: FAILED\n  Rationale: code already handles this",
            "F4 [IN_SCOPE] VERIFIED but STYLE/PREFERENCE:\n  Naming convention preference",
            "Final category mapping:\n"
            "| F1 | CONFIRMED |\n"
            "| F2 | OUT OF SCOPE |\n"
            "| F3 | FALSE POSITIVE |\n"
            "| F4 | STYLE |\n"
            "| F5 | LIKELY VALID |\n",
        ]
        result = extract_ingest_outcomes(texts)
        assert result["confirmed"] >= 1
        assert result["out_of_scope"] >= 1
        assert result["false_positive"] >= 1
        assert result["style"] >= 1
        assert result["likely_valid"] >= 1

    def test_empty_texts(self):
        """Empty text list returns zeroes."""
        result = extract_ingest_outcomes([])
        assert result["confirmed"] == 0
        assert result["out_of_scope"] == 0
        assert result["false_positive"] == 0
        assert result["style"] == 0
        assert result["likely_valid"] == 0

    def test_no_categories_found(self):
        """Text without any recognizable categories returns zeroes."""
        texts = ["Just some random text without any finding categories."]
        result = extract_ingest_outcomes(texts)
        assert all(v == 0 for v in result.values())

    def test_multiple_confirmed(self):
        """Multiple CONFIRMED findings are counted."""
        texts = [
            "| F1 | CONFIRMED |\n| F2 | CONFIRMED |\n| F3 | CONFIRMED |"
        ]
        result = extract_ingest_outcomes(texts)
        assert result["confirmed"] == 3

    def test_case_insensitive_categories(self):
        """Categories are matched case-insensitively."""
        texts = ["| F1 | confirmed |\n| F2 | Out of Scope |"]
        result = extract_ingest_outcomes(texts)
        assert result["confirmed"] >= 1
        assert result["out_of_scope"] >= 1


# ---------------------------------------------------------------------------
# Tests: compute_survival_rate
# ---------------------------------------------------------------------------

class TestComputeSurvivalRate:
    """compute_survival_rate(agent_findings, ingest_outcomes) → float 0.0-1.0."""

    def test_all_survived(self):
        """All findings confirmed → 1.0 survival rate."""
        agent_findings = {"total_findings": 3, "findings_by_severity": {}, "issues": []}
        ingest_outcomes = {
            "confirmed": 3, "likely_valid": 0,
            "false_positive": 0, "out_of_scope": 0, "style": 0,
        }
        rate = compute_survival_rate(agent_findings, ingest_outcomes)
        assert rate == pytest.approx(1.0)

    def test_none_survived(self):
        """All findings filtered out → 0.0 survival rate."""
        agent_findings = {"total_findings": 4, "findings_by_severity": {}, "issues": []}
        ingest_outcomes = {
            "confirmed": 0, "likely_valid": 0,
            "false_positive": 2, "out_of_scope": 1, "style": 1,
        }
        rate = compute_survival_rate(agent_findings, ingest_outcomes)
        assert rate == pytest.approx(0.0)

    def test_partial_survival(self):
        """Mix of confirmed/likely_valid and filtered → fractional rate."""
        agent_findings = {"total_findings": 5, "findings_by_severity": {}, "issues": []}
        ingest_outcomes = {
            "confirmed": 2, "likely_valid": 1,
            "false_positive": 1, "out_of_scope": 1, "style": 0,
        }
        # survived = confirmed + likely_valid = 3, total = 5
        rate = compute_survival_rate(agent_findings, ingest_outcomes)
        assert rate == pytest.approx(0.6)

    def test_zero_findings(self):
        """Zero total findings → 0.0 (avoid division by zero)."""
        agent_findings = {"total_findings": 0, "findings_by_severity": {}, "issues": []}
        ingest_outcomes = {
            "confirmed": 0, "likely_valid": 0,
            "false_positive": 0, "out_of_scope": 0, "style": 0,
        }
        rate = compute_survival_rate(agent_findings, ingest_outcomes)
        assert rate == pytest.approx(0.0)

    def test_missing_total_findings(self):
        """Missing total_findings key → 0.0."""
        agent_findings = {"findings_by_severity": {}, "issues": []}
        ingest_outcomes = {"confirmed": 1, "likely_valid": 0, "false_positive": 0, "out_of_scope": 0, "style": 0}
        rate = compute_survival_rate(agent_findings, ingest_outcomes)
        assert rate == pytest.approx(0.0)


# ---------------------------------------------------------------------------
# Tests: detect_overlaps
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

    def test_multiple_overlaps(self):
        """Three findings at two locations → two overlap clusters."""
        findings = [
            {"agent": "security", "file": "a.php", "line": 10, "severity": "high", "title": "A1"},
            {"agent": "code", "file": "a.php", "line": 10, "severity": "high", "title": "A2"},
            {"agent": "security", "file": "b.php", "line": 20, "severity": "medium", "title": "B1"},
            {"agent": "perf", "file": "b.php", "line": 20, "severity": "medium", "title": "B2"},
        ]
        result = detect_overlaps(findings)
        assert result["overlap_clusters"] == 2

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

    def test_single_finding(self):
        """Single finding → no possible overlap."""
        findings = [
            {"agent": "security", "file": "a.php", "line": 1, "severity": "high", "title": "Solo"},
        ]
        result = detect_overlaps(findings)
        assert result["overlap_clusters"] == 0
        assert result["severity_disagreements"] == 0

    def test_same_file_different_lines(self):
        """Same file but different lines → no overlap."""
        findings = [
            {"agent": "security", "file": "a.php", "line": 10, "severity": "high", "title": "Line10"},
            {"agent": "code", "file": "a.php", "line": 20, "severity": "high", "title": "Line20"},
        ]
        result = detect_overlaps(findings)
        assert result["overlap_clusters"] == 0

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
# Tests: format_quality_text_report — survival rate section
# ---------------------------------------------------------------------------

format_quality_text_report = _mod.format_quality_text_report
format_quality_json_report = _mod.format_quality_json_report


def _make_dispatch(reviewer="security", issues=None, ingest_texts=None):
    """Build a (meta, data) tuple mimicking a reviewer dispatch."""
    if issues is None:
        issues = [_make_issue(severity="high", title="XSS", file="f.php", line=1)]
    review = _make_review_json(reviewer=reviewer, issues=issues)
    review_json_str = json.dumps(review)
    data = {
        "write_outputs": [{"content": review_json_str, "path": f"{reviewer}-review.json"}],
        # Minimal fields for classify_dispatch
        "files_read": [],
        "bash_commands": [],
        "final_texts": [],
    }
    if ingest_texts is not None:
        data["ingest_texts"] = ingest_texts
    return ({"agent_name": f"{reviewer}-reviewer"}, data)


class TestSurvivalRateInReport:
    """Survival rate appears in quality report when ingest data is available."""

    def test_text_report_includes_survival_when_ingest_present(self):
        """When dispatch data includes ingest outputs, show survival rate."""
        ingest_text = "| F1 | CONFIRMED | SQL Injection |"
        dispatch = _make_dispatch(
            reviewer="security",
            ingest_texts=[ingest_text],
        )
        report = format_quality_text_report([dispatch], None)
        assert "Survival rate" in report or "survival" in report.lower()

    def test_text_report_shows_na_when_no_ingest(self):
        """When no ingest data, show N/A for survival rate."""
        dispatch = _make_dispatch(reviewer="security")
        report = format_quality_text_report([dispatch], None)
        assert "N/A" in report

    def test_json_report_includes_survival_when_ingest_present(self):
        """JSON report includes survival metrics when ingest data is present."""
        ingest_text = "| F1 | CONFIRMED |\n| F2 | FALSE POSITIVE |"
        dispatch = _make_dispatch(
            reviewer="security",
            ingest_texts=[ingest_text],
        )
        report_str = format_quality_json_report([dispatch], None)
        report = json.loads(report_str)
        assert "survival" in report
        assert report["survival"] is not None
        assert "rate" in report["survival"]
        assert "outcomes" in report["survival"]

    def test_json_report_survival_none_when_no_ingest(self):
        """JSON report has survival=None when no ingest data."""
        dispatch = _make_dispatch(reviewer="security")
        report_str = format_quality_json_report([dispatch], None)
        report = json.loads(report_str)
        assert report["survival"] is None


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


# ---------------------------------------------------------------------------
# Bash builder heredoc recognition (the mandated save mechanism)
# ---------------------------------------------------------------------------

def _builder_heredoc(reviewer="security", body=None):
    """Build the canonical one-shot builder command bootstrap prescribes."""
    if body is None:
        body = (
            "import sys, os\n"
            'plugin_root = os.environ["PIRATEGOAT_PLUGIN_ROOT"]\n'
            "sys.path.insert(0, os.path.join(plugin_root, \"scripts\"))\n"
            "from review.agent.output import ReviewOutputBuilder\n"
            f'builder = ReviewOutputBuilder(pr_id="42", reviewer="{reviewer}")\n'
            'builder.add_issue(severity="high", title="Reviewer\'s finding — '
            'unsafe echo", file="src/f.php",\n'
            '    description="What is wrong", recommendation="How to fix",\n'
            '    category="xss", line=42, confidence=0.9)\n'
            'builder.add_issue("medium", "Positional style", "src/g.php",\n'
            '    "desc", "rec", line=7)\n'
            "result = builder.save(os.environ[\"PIRATEGOAT_OUTPUT_DIR\"])\n"
        )
    return (
        "PIRATEGOAT_PLUGIN_ROOT='/plug' "
        "PIRATEGOAT_OUTPUT_DIR='/tmp/pr-review-42' "
        f"PIRATEGOAT_REVIEWER_NAME='{reviewer}' "
        "PIRATEGOAT_PR_ID='42' python3 <<'PY'\n"
        f"{body}"
        "PY"
    )


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


def _tool_result_entry(tool_id, is_error=False):
    return {
        "type": "user",
        "message": {
            "role": "user",
            "content": [
                {
                    "type": "tool_result",
                    "tool_use_id": tool_id,
                    "content": "RECORDED COUNTS: ..." if not is_error else "Traceback",
                    "is_error": is_error,
                }
            ],
        },
    }


class TestBashBuilderRecognition:
    """Compliant reviewers save via the mandated Bash heredoc, not Write —
    session analysis must recognize that mechanism or new sessions produce
    empty per-agent quality records."""

    def test_synthesizes_review_record_from_heredoc(self):
        record = _mod._builder_review_from_heredoc(_builder_heredoc())

        assert record is not None
        assert record["path"] == "/tmp/pr-review-42/security-review.json"
        assert record["source"] == "bash_builder_heredoc"
        review = json.loads(record["content"])
        assert review["reviewer"] == "security"
        kw_issue, positional_issue = review["issues"]
        assert kw_issue["severity"] == "high"
        assert kw_issue["file"] == "src/f.php"
        assert kw_issue["line"] == 42
        assert "Reviewer's finding" in kw_issue["title"]
        assert positional_issue["severity"] == "medium"
        assert positional_issue["file"] == "src/g.php"
        assert positional_issue["line"] == 7

    def test_positional_tuple_mirrors_the_full_add_issue_signature(self):
        """Drift guard: the tuple must cover EVERY positional parameter of
        the real add_issue — a name missing from it is silently dropped
        from fully positional calls (a dropped severity_floor records the
        pre-floor severity)."""
        import inspect

        spec = importlib.util.spec_from_file_location(
            "output_for_positional_contract",
            PLUGIN_ROOT / "scripts" / "review" / "agent" / "output.py",
        )
        output_mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(output_mod)

        parameters = list(
            inspect.signature(
                output_mod.ReviewOutputBuilder.add_issue
            ).parameters.values()
        )[1:]  # drop self
        positional = tuple(
            parameter.name
            for parameter in parameters
            if parameter.kind == parameter.POSITIONAL_OR_KEYWORD
        )
        assert positional == _mod._BUILDER_ISSUE_POSITIONAL

    def test_fully_positional_severity_floor_is_applied(self):
        body = (
            "from review.agent.output import ReviewOutputBuilder\n"
            'builder = ReviewOutputBuilder(pr_id="42", reviewer="security")\n'
            'builder.add_issue("low", "T", "src/f.php", "d", "r", "cat", 3,\n'
            '    0.9, None, None, "high")\n'
            "builder.save(\"/tmp/pr-review-42\")\n"
        )
        record = _mod._builder_review_from_heredoc(_builder_heredoc(body=body))

        [issue] = json.loads(record["content"])["issues"]
        assert issue["severity"] == "high"
        assert issue["confidence"] == 0.9

    def test_fully_positional_call_reconstructs_category_and_line(self):
        """add_issue accepts category and line positionally after
        recommendation — dropping them would restore the finding without
        its line and exclude it from overlap scoring."""
        body = (
            "from review.agent.output import ReviewOutputBuilder\n"
            'builder = ReviewOutputBuilder(pr_id="42", reviewer="security")\n'
            'builder.add_issue("high", "T", "src/f.php", "d", "r", "xss", 42)\n'
            "builder.save(\"/tmp/pr-review-42\")\n"
        )
        record = _mod._builder_review_from_heredoc(_builder_heredoc(body=body))

        [issue] = json.loads(record["content"])["issues"]
        assert issue["category"] == "xss"
        assert issue["line"] == 42

    def test_reconstruction_applies_severity_floor_promotion(self):
        """The builder lowercases severities and promotes to severity_floor;
        the reconstruction must match what was actually saved."""
        body = (
            "from review.agent.output import ReviewOutputBuilder\n"
            'builder = ReviewOutputBuilder(pr_id="42", reviewer="security")\n'
            'builder.add_issue(severity="LOW", title="Floored", file="f.php",\n'
            '    description="d", recommendation="r", line=3,\n'
            '    severity_floor="medium")\n'
            "builder.save(\"/tmp/pr-review-42\")\n"
        )
        record = _mod._builder_review_from_heredoc(_builder_heredoc(body=body))

        [issue] = json.loads(record["content"])["issues"]
        assert issue["severity"] == "medium"

    def test_issue_added_after_final_save_is_not_reconstructed(self):
        """The builder persists its state at save(): an add_issue() after
        the last save executed but entered no JSON — reconstructing it
        would fabricate findings into the quality report."""
        body = (
            "from review.agent.output import ReviewOutputBuilder\n"
            'builder = ReviewOutputBuilder(pr_id="42", reviewer="security")\n'
            'builder.add_issue(severity="high", title="Persisted", file="a.php",\n'
            '    description="d", recommendation="r", line=1)\n'
            "builder.save(\"/tmp/pr-review-42\")\n"
            'builder.add_issue(severity="critical", title="Never saved", file="b.php",\n'
            '    description="d", recommendation="r", line=2)\n'
        )
        record = _mod._builder_review_from_heredoc(_builder_heredoc(body=body))

        issues = json.loads(record["content"])["issues"]
        assert [issue["title"] for issue in issues] == ["Persisted"]

    def test_issues_before_intermediate_saves_all_reach_the_final_save(self):
        """Builder state accumulates across saves: everything added before
        the FINAL save is in the persisted JSON, including issues that
        also went out with an earlier save."""
        body = (
            "from review.agent.output import ReviewOutputBuilder\n"
            'builder = ReviewOutputBuilder(pr_id="42", reviewer="security")\n'
            'builder.add_issue(severity="high", title="First", file="a.php",\n'
            '    description="d", recommendation="r", line=1)\n'
            "builder.save(\"/tmp/pr-review-42\")\n"
            'builder.add_issue(severity="medium", title="Second", file="b.php",\n'
            '    description="d", recommendation="r", line=2)\n'
            "builder.save(\"/tmp/pr-review-42\")\n"
        )
        record = _mod._builder_review_from_heredoc(_builder_heredoc(body=body))

        issues = json.loads(record["content"])["issues"]
        assert [issue["title"] for issue in issues] == ["First", "Second"]

    def test_non_builder_bash_is_not_recognized(self):
        assert _mod._builder_review_from_heredoc("git diff main..HEAD") is None
        assert (
            _mod._builder_review_from_heredoc("python3 script.py <<PY\nx\nPY")
            is None
        )

    def test_unparseable_heredoc_body_degrades_to_none(self):
        command = _builder_heredoc(body="this is not python(\n")
        assert _mod._builder_review_from_heredoc(command) is None

    def test_categorizer_labels_builder_output(self):
        detail = _mod._categorize_tool_call(
            "Bash", {"command": _builder_heredoc()}
        )
        assert detail["category"] == "builder-output"

    def test_parse_subagent_log_populates_write_outputs(self, tmp_path):
        log = tmp_path / "agent.jsonl"
        entries = [
            _bash_entry("git diff main..HEAD -- src/f.php", tool_id="diff-1"),
            _tool_result_entry("diff-1"),
            _bash_entry(_builder_heredoc(), tool_id="builder-1"),
            _tool_result_entry("builder-1"),
        ]
        log.write_text("\n".join(json.dumps(e) for e in entries) + "\n")

        data = _mod.parse_subagent_log(str(log))

        assert len(data["write_outputs"]) == 1
        assert data["write_outputs"][0]["source"] == "bash_builder_heredoc"

    def test_failed_builder_heredoc_does_not_count_as_saved(self, tmp_path):
        """A heredoc that exited with an error saved nothing — its findings
        must not enter quality reports."""
        log = tmp_path / "agent.jsonl"
        entries = [
            _bash_entry(_builder_heredoc(), tool_id="builder-fail"),
            _tool_result_entry("builder-fail", is_error=True),
        ]
        log.write_text("\n".join(json.dumps(e) for e in entries) + "\n")

        data = _mod.parse_subagent_log(str(log))

        assert data["write_outputs"] == []

    @pytest.mark.parametrize(
        "structured",
        [
            {"exitCode": 1},
            {"interrupted": True},
            {"status": "error"},
            {"error": "ValueError: line must be a positive integer"},
        ],
        ids=["exit-code", "interrupted", "status", "error-field"],
    )
    def test_structured_failure_without_is_error_does_not_count(
        self, tmp_path, structured
    ):
        """A builder result can omit block-level is_error while reporting
        failure through structured toolUseResult fields — the save did not
        persist and must not reconstruct."""
        log = tmp_path / "agent.jsonl"
        result_entry = {
            "type": "user",
            "toolUseResult": structured,
            "message": {
                "role": "user",
                "content": [
                    {
                        "type": "tool_result",
                        "tool_use_id": "builder-structured",
                        "content": "Traceback",
                    }
                ],
            },
        }
        entries = [
            _bash_entry(_builder_heredoc(), tool_id="builder-structured"),
            result_entry,
        ]
        log.write_text("\n".join(json.dumps(e) for e in entries) + "\n")

        data = _mod.parse_subagent_log(str(log))

        assert data["write_outputs"] == []

    def test_failed_then_retried_builder_heredoc_counts_once(self, tmp_path):
        log = tmp_path / "agent.jsonl"
        entries = [
            _bash_entry(_builder_heredoc(), tool_id="builder-fail"),
            _tool_result_entry("builder-fail", is_error=True),
            _bash_entry(_builder_heredoc(), tool_id="builder-retry"),
            _tool_result_entry("builder-retry"),
        ]
        log.write_text("\n".join(json.dumps(e) for e in entries) + "\n")

        data = _mod.parse_subagent_log(str(log))

        assert len(data["write_outputs"]) == 1

    @pytest.mark.parametrize(
        "structured",
        [
            {"status": "running"},
            {"interrupted": False},
            {"weird": {"shape": 1}},
        ],
        ids=["nonterminal-status", "nonterminal-flag", "unclassifiable"],
    )
    def test_nonterminal_or_unclassifiable_result_does_not_count(
        self, tmp_path, structured
    ):
        """Only a terminal success confirms the save persisted — nonterminal
        and unrecognized structured payloads stay unresolved."""
        log = tmp_path / "agent.jsonl"
        result_entry = {
            "type": "user",
            "toolUseResult": structured,
            "message": {
                "role": "user",
                "content": [
                    {
                        "type": "tool_result",
                        "tool_use_id": "builder-open",
                        "content": "",
                    }
                ],
            },
        }
        entries = [
            _bash_entry(_builder_heredoc(), tool_id="builder-open"),
            result_entry,
        ]
        log.write_text("\n".join(json.dumps(e) for e in entries) + "\n")

        data = _mod.parse_subagent_log(str(log))

        assert data["write_outputs"] == []

    def test_bare_legacy_result_still_confirms_the_save(self, tmp_path):
        """A paired result with neither is_error nor structured data is the
        legacy success signal — the canonical classifier preserves it."""
        log = tmp_path / "agent.jsonl"
        result_entry = {
            "type": "user",
            "message": {
                "role": "user",
                "content": [
                    {
                        "type": "tool_result",
                        "tool_use_id": "builder-legacy",
                        "content": "RECORDED COUNTS: ...",
                    }
                ],
            },
        }
        entries = [
            _bash_entry(_builder_heredoc(), tool_id="builder-legacy"),
            result_entry,
        ]
        log.write_text("\n".join(json.dumps(e) for e in entries) + "\n")

        data = _mod.parse_subagent_log(str(log))

        assert len(data["write_outputs"]) == 1

    def test_heredoc_without_save_is_not_a_review_record(self):
        """add_issue() calls without builder.save() persisted nothing."""
        body = (
            "from review.agent.output import ReviewOutputBuilder\n"
            'builder = ReviewOutputBuilder(pr_id="42", reviewer="security")\n'
            'builder.add_issue(severity="high", title="Unsaved", file="f.php",\n'
            '    description="d", recommendation="r", line=3)\n'
        )
        assert _mod._builder_review_from_heredoc(_builder_heredoc(body=body)) is None

    def test_corrected_rerun_counts_once_with_final_content(self, tmp_path):
        """Successful saves overwrite the same artifact — quality reports
        must see the final save only, not one dispatch per rerun."""
        first_body = (
            "from review.agent.output import ReviewOutputBuilder\n"
            'builder = ReviewOutputBuilder(pr_id="42", reviewer="security")\n'
            'builder.add_issue(severity="high", title="First", file="f.php",\n'
            '    description="d", recommendation="r", line=3)\n'
            "builder.save(\"/tmp/pr-review-42\")\n"
        )
        corrected_body = (
            "from review.agent.output import ReviewOutputBuilder\n"
            'builder = ReviewOutputBuilder(pr_id="42", reviewer="security")\n'
            'builder.add_issue(severity="high", title="Corrected", file="f.php",\n'
            '    description="d", recommendation="r", line=3)\n'
            'builder.add_issue(severity="low", title="Added", file="g.php",\n'
            '    description="d", recommendation="r", line=9)\n'
            "builder.save(\"/tmp/pr-review-42\")\n"
        )
        log = tmp_path / "agent.jsonl"
        entries = [
            _bash_entry(_builder_heredoc(body=first_body), tool_id="save-1"),
            _tool_result_entry("save-1"),
            _bash_entry(_builder_heredoc(body=corrected_body), tool_id="save-2"),
            _tool_result_entry("save-2"),
        ]
        log.write_text("\n".join(json.dumps(e) for e in entries) + "\n")

        data = _mod.parse_subagent_log(str(log))

        [record] = data["write_outputs"]
        issues = json.loads(record["content"])["issues"]
        assert [issue["title"] for issue in issues] == ["Corrected", "Added"]

    def test_unresolved_builder_heredoc_does_not_count_as_saved(self, tmp_path):
        """No paired tool result means the save was never confirmed."""
        log = tmp_path / "agent.jsonl"
        entries = [_bash_entry(_builder_heredoc(), tool_id="builder-dangling")]
        log.write_text("\n".join(json.dumps(e) for e in entries) + "\n")

        data = _mod.parse_subagent_log(str(log))

        assert data["write_outputs"] == []

    def test_quality_report_counts_bash_saved_findings(self):
        dispatch = (
            {"agent_name": "security-reviewer"},
            {
                "write_outputs": [
                    _mod._builder_review_from_heredoc(_builder_heredoc())
                ],
                "files_read": [],
                "bash_commands": [],
                "final_texts": [],
            },
        )

        report = json.loads(format_quality_json_report([dispatch], None))

        [agent_record] = report["per_agent"]
        assert agent_record["agent_name"] == "security"
        assert agent_record["total_findings"] == 2
        assert agent_record["findings_by_severity"] == {"high": 1, "medium": 1}
