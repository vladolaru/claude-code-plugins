"""
Simple Review Output Builder (No Dependencies)

Lightweight version without Pydantic for immediate use.
Provides structure and basic validation using plain Python.

Usage:
    from review.agent.output import ReviewOutputBuilder

    builder = ReviewOutputBuilder(pr_id="123", reviewer="security")
    builder.add_issue(
        severity="critical",
        title="SQL Injection",
        file="src/User.php",
        line=42,
        description="...",
        recommendation="..."
    )
    json_output = builder.to_json()
    markdown_output = builder.to_markdown()
"""

import json
import os
import posixpath
import sys
import uuid
from datetime import datetime
from typing import List, Optional, Dict, Any


_VALID_SEVERITIES = ('critical', 'high', 'medium', 'low', 'info')
_SEVERITY_RANK = {
    'info': 0,
    'low': 1,
    'medium': 2,
    'high': 3,
    'critical': 4,
}


def _coerce_text(value: Any, single_line: bool = False) -> str:
    """Coerce a free-form finding field to a string at write time.

    These fields are model-authored, so a value the schema expects to be a
    string (``title``, ``description``, ``recommendation``) can arrive as a
    list, number, or ``None``. Persisting a non-string here lets the malformed
    value flow downstream into the reconciliation Markdown renderer, which
    crashes the whole review at pipeline step 8. Coerce at the producer so bad
    values never reach disk: lists/tuples join on newlines, ``None`` becomes
    empty, everything else stringifies. (The reconciliation renderer keeps its
    own equivalent guard as defense in depth.)

    ``single_line=True`` additionally collapses all whitespace to single
    spaces. Titles render inline downstream (``**N. …**``, ``### F1: …``)
    without block-syntax escaping, so a newline could forge a heading or
    thematic break — keeping titles single-line prevents that.
    """
    if isinstance(value, str):
        result = value
    elif value is None:
        result = ""
    elif isinstance(value, (list, tuple)):
        result = "\n".join(_coerce_text(item) for item in value)
    else:
        result = str(value)
    if single_line:
        result = " ".join(result.split())
    return result


def _log_agent_complete_telemetry(output_dir, reviewer, verdict, issue_count, severities):
    """Best-effort telemetry logging on agent completion. Never raises."""
    try:
        import importlib.util
        spec = importlib.util.spec_from_file_location(
            "review_telemetry",
            os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "telemetry.py"),
        )
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        t = mod.ReviewTelemetry(output_dir)
        t.log_agent_complete(
            agent_name=reviewer,
            verdict=verdict,
            issue_count=issue_count,
            severities=severities,
        )
    except Exception:
        pass


class ReviewOutputBuilder:
    """Simple builder for structured review outputs."""

    def __init__(self, pr_id: str, reviewer: str):
        self.pr_id = pr_id
        self.reviewer = reviewer
        self.timestamp = datetime.now().isoformat()
        self.issues = []
        self.observations = []
        self.recommendations = {'immediate': [], 'important': [], 'suggestions': []}
        self.positive_observations = []
        self.clearances = []
        self.unreviewed = []
        self.files_reviewed = 0
        self.review_start = datetime.now()
        self.tool_results_used = []
        self.overall_confidence = 0.95
        self._not_applicable = False
        self._skip_reason = None

    def add_issue(
        self,
        severity: str,
        title: str,
        file: str,
        description: str,
        recommendation: str,
        category: str = "general",
        line: int = None,
        confidence: float = 0.95,
        behavior_evidence: Optional[str] = None,
        source_cited: Optional[str] = None,
        severity_floor: Optional[str] = None,
        **extra_fields
    ) -> Optional[str]:
        """Add an issue. Returns issue ID.

        Line is required for point defects — the reviewer protocol mandates
        diff-anchored findings for anything that has a line. Findings that are
        line-less BY NATURE (missing test coverage, missing assertions,
        git-history precedent, cross-file architecture) may pass line=None:
        they are recorded as first-class FILE-SCOPED issues (line: null,
        scope: "file") that count toward the verdict, with a stderr NOTE so
        accidental line omission stays visible.
        Use add_observation() only for genuinely informational notes that
        should NOT count toward the verdict.
        When severity_floor is provided, lower severities are promoted to it.
        """
        # Validate severity and enforce an optional minimum.
        severity_value = severity.lower()
        if severity_value not in _VALID_SEVERITIES:
            raise ValueError(
                f"Invalid severity: {severity}. "
                f"Must be one of {list(_VALID_SEVERITIES)}"
            )

        floor_value = None
        if severity_floor is not None:
            if not isinstance(severity_floor, str):
                raise ValueError("severity_floor must be a severity name")
            floor_value = severity_floor.lower()
            if floor_value not in _VALID_SEVERITIES:
                raise ValueError(
                    f"Invalid severity_floor: {severity_floor}. "
                    f"Must be one of {list(_VALID_SEVERITIES)}"
                )
            if _SEVERITY_RANK[severity_value] < _SEVERITY_RANK[floor_value]:
                severity_value = floor_value

        # Validate confidence
        if not 0.0 <= confidence <= 1.0:
            raise ValueError(f"Confidence must be 0.0-1.0, got {confidence}")

        # Validate behavior_evidence enum
        if behavior_evidence is not None:
            valid_evidence = ("cited", "inferred")
            if behavior_evidence not in valid_evidence:
                raise ValueError(
                    f"Invalid behavior_evidence: {behavior_evidence!r}. "
                    f"Must be one of {valid_evidence}."
                )

        # Validate line — None records a first-class file-scoped issue (loud),
        # hard enforcement for invalid values (0, negative, non-int).
        file_scoped = line is None
        if file_scoped:
            # A legitimately line-less finding (missing coverage, precedent,
            # cross-file architecture) is a real, verdict-counting issue.
            # Point defects still need line= — hence the stderr NOTE.
            print(
                f"NOTE: recording '{title}' ({severity_value}) as a "
                f"FILE-SCOPED issue for '{file}' because line=None. "
                f"It counts toward the verdict. If this finding points at a "
                f"specific line, re-add it with line=<source line>.",
                file=sys.stderr,
            )
        elif not isinstance(line, int) or line <= 0:
            raise ValueError(
                f"line must be a positive integer, got {line}. "
                "Lines are 1-indexed."
            )

        # Warn on implausibly large line numbers — likely patch-file line confusion.
        # When agents read a diff/patch file, the Read tool displays line numbers
        # within the patch (e.g., "227→+class Foo"). Agents sometimes use these
        # patch-file positions instead of the actual source file line numbers.
        if not file_scoped and line > 5000:
            print(
                f"WARNING: line={line} for '{file}' is unusually large. "
                f"Verify this is a source file line number, not a patch file "
                f"display line number from the Read tool.",
                file=sys.stderr,
            )

        issue_id = str(uuid.uuid4())[:8]

        issue = {
            'id': issue_id,
            'category': category,
            'severity': severity_value,
            'title': _coerce_text(title, single_line=True),
            'description': _coerce_text(description),
            'file': file,
            'line': line,
            'recommendation': _coerce_text(recommendation),
            'confidence': confidence,
            **extra_fields
        }
        if file_scoped:
            issue['scope'] = 'file'
        if behavior_evidence is not None:
            issue['behavior_evidence'] = behavior_evidence
        if source_cited is not None:
            issue['source_cited'] = source_cited
        if floor_value is not None:
            issue['severity_floor'] = floor_value

        self.issues.append(issue)
        return issue_id

    def add_observation(self, file: str, note: str, category: str = "general"):
        """Add a file-level observation (not a finding).

        Observations are informational notes about files that don't have a
        specific line reference. They don't affect the verdict and are
        displayed separately from issues.
        """
        self.observations.append({
            "file": file,
            "note": note,
            "category": category,
        })

    def add_recommendation(self, priority: str, text: str):
        """Add recommendation (priority: immediate, important, suggestions)."""
        if priority in self.recommendations:
            self.recommendations[priority].append(_coerce_text(text))

    def add_positive(self, observation: str):
        """Add positive observation."""
        self.positive_observations.append(observation)

    def add_clearance(self, claim: str, method: str, evidence: Optional[str] = None):
        """Record an auditable absence claim ("nothing depends on this").

        Use for blast-radius clears: "no CSS selects the removed element",
        "no caller uses the deleted parameter", "no test targets this row".
        Unlike positive observations (which reconciliation excludes),
        clearances flow into the reconciliation context WITH their method,
        so conflicts with other agents' findings are visible and search
        coverage can be judged downstream.

        Args:
            claim: The absence being asserted.
            method: The exact searches run / files read that ground the claim
                (e.g. "grep -rn 'th label' client/legacy/css/; read each hit").
                Required — an absence claim without its method is unauditable.
            evidence: Optional supporting detail (hit counts, file:line list).
        """
        if not claim or not claim.strip():
            raise ValueError("add_clearance requires a non-empty claim.")
        if not method or not method.strip():
            raise ValueError(
                "add_clearance requires a non-empty method — state the exact "
                "searches/reads that ground the claim so downstream stages "
                "can judge their coverage."
            )
        self.clearances.append({
            "claim": claim.strip(),
            "method": method.strip(),
            "evidence": evidence.strip() if evidence and evidence.strip() else None,
        })

    def add_unreviewed(self, file: str):
        """Declare an in-scope file left unreviewed after budget exhaustion.

        Use ONLY for NOT DIFFED files genuinely out of reach when the tool
        budget ran out. Declared files render under the
        '**Not reviewed (budget):**' line in the Markdown summary and appear
        as 'unreviewed' in the JSON output, so downstream coverage accounting
        sees the gap. They never count toward the verdict.
        """
        if not isinstance(file, str) or not file.strip():
            raise ValueError("add_unreviewed requires a non-empty file path.")
        # Normalize to the canonical repo-relative form scope.py emits, so
        # "./src/x.php", "src\\x.php", and "src/x.php" declare the same
        # coverage gap. Forms that can never match a scope path are rejected
        # loudly — an unmatched declaration would invert into a
        # deferred-but-reviewed claim downstream.
        path = posixpath.normpath(file.strip().replace("\\", "/"))
        if (
            path.startswith("/")
            or path == "."
            or path == ".."
            or path.startswith("../")
            or (len(path) >= 2 and path[1] == ":" and path[0].isalpha())
        ):
            raise ValueError(
                "add_unreviewed requires a repository-relative path exactly "
                f"as shown in the NOT DIFFED listing, got {file!r}."
            )
        if path not in self.unreviewed:
            self.unreviewed.append(path)

    def set_files_reviewed(self, count: int):
        """Set number of files reviewed."""
        self.files_reviewed = count

    def set_confidence(self, score: float):
        """Set overall confidence score."""
        if not 0.0 <= score <= 1.0:
            raise ValueError(f"Confidence must be 0.0-1.0, got {score}")
        self.overall_confidence = score

    def add_tool_result(self, tool_name: str):
        """Record tool result used."""
        if tool_name not in self.tool_results_used:
            self.tool_results_used.append(tool_name)

    def mark_not_applicable(self, reason: str):
        """Mark this review as not applicable — changes not relevant to this domain.

        Use this when the Quick Relevance Check determines the diff has no
        changes relevant to this agent's specialty, or when NO_DOMAIN_FILES
        is returned by scope discovery. Produces a 'not_applicable' verdict
        so the reconciliator knows the agent abstained rather than endorsed.
        """
        if not reason or not reason.strip():
            raise ValueError(
                "mark_not_applicable requires a non-empty reason explaining "
                "why the changes are not relevant to this domain."
            )
        if self.issues:
            raise ValueError(
                f"Cannot mark review as not_applicable — {len(self.issues)} issue(s) "
                "already recorded. An agent that found issues reviewed the code; "
                "it should not also claim the changes are irrelevant."
            )
        self._not_applicable = True
        self._skip_reason = reason.strip()

    def _calculate_verdict(self) -> str:
        """Auto-calculate verdict from issues."""
        if self._not_applicable:
            return 'not_applicable'

        counts = {'critical': 0, 'high': 0, 'medium': 0}

        for issue in self.issues:
            # Advisory-channel findings (repo-contributed reviewers on the
            # advisory channel) never gate the verdict — they are listed but not
            # enforced. Native agents never set 'channel', so this is a no-op for
            # them (backward-compatible).
            if issue.get('channel') == 'advisory':
                continue
            sev = issue['severity']
            if sev in counts:
                counts[sev] += 1

        if counts['critical'] > 0:
            return 'block'
        if counts['high'] >= 3:
            return 'block'
        if counts['high'] > 0 or counts['medium'] >= 5:
            return 'request_changes'
        if counts['medium'] > 0:
            return 'comment'

        return 'approve'

    def to_dict(self) -> Dict:
        """Build as dictionary."""
        review_duration = int((datetime.now() - self.review_start).total_seconds() * 1000)

        severity_counts = {'critical': 0, 'high': 0, 'medium': 0, 'low': 0, 'info': 0}
        for issue in self.issues:
            severity_counts[issue['severity']] += 1

        result = {
            'pr_id': self.pr_id,
            'reviewer': self.reviewer,
            'timestamp': self.timestamp,
            'version': '1.0.0',
            'verdict': self._calculate_verdict(),
            'summary': {
                'total_issues': len(self.issues),
                'by_severity': severity_counts
            },
            'issues': self.issues,
            'unreviewed': self.unreviewed if self.unreviewed else None,
            'observations': self.observations if self.observations else None,
            'recommendations': self.recommendations if any(self.recommendations.values()) else None,
            'positive_observations': self.positive_observations if self.positive_observations else None,
            'clearances': self.clearances if self.clearances else None,
            'meta': {
                'files_reviewed': self.files_reviewed,
                'review_duration_ms': review_duration,
                'confidence_score': self.overall_confidence,
                'tool_results_used': self.tool_results_used if self.tool_results_used else None
            }
        }
        if self._skip_reason:
            result['skip_reason'] = self._skip_reason
        return result

    def to_json(self, indent: int = 2) -> str:
        """Generate JSON string."""
        return json.dumps(self.to_dict(), indent=indent, ensure_ascii=False)

    def to_markdown(self) -> str:
        """Generate human-readable markdown."""
        data = self.to_dict()
        md = []

        md.append(f"# {self.reviewer.title()} Review - PR #{self.pr_id}\n\n")
        md.append("## Executive Summary\n\n")
        md.append(f"**Verdict:** {data['verdict'].upper()}\n")
        md.append(f"**Total Issues:** {data['summary']['total_issues']}\n\n")

        if data['summary']['total_issues'] > 0:
            counts = data['summary']['by_severity']
            md.append(f"- Critical: {counts['critical']}\n")
            md.append(f"- High: {counts['high']}\n")
            md.append(f"- Medium: {counts['medium']}\n\n")

        # Declared coverage gap — in-scope files unreached at budget exhaustion
        if data.get('unreviewed'):
            files = ", ".join(f"`{f}`" for f in data['unreviewed'])
            md.append(f"**Not reviewed (budget):** {files}\n\n")

        # Issues — every severity that counts toward total_issues must render,
        # or the Markdown claims findings it doesn't show.
        for sev in ['critical', 'high', 'medium', 'low', 'info']:
            sev_issues = [i for i in data['issues'] if i['severity'] == sev]

            if sev_issues:
                md.append(f"## {sev.title()} Issues\n\n")

                for issue in sev_issues:
                    md.append(f"### {issue['title']}\n\n")
                    if issue['line']:
                        location = f"**File:** `{issue['file']}` line {issue['line']}"
                    elif issue.get('scope') == 'file':
                        location = f"**File:** `{issue['file']}` (file-scoped)"
                    else:
                        location = f"**File:** `{issue['file']}`"
                    md.append(location + "\n\n")
                    md.append(f"{issue['description']}\n\n")
                    if issue.get('severity_floor'):
                        md.append(f"**Severity floor:** {issue['severity_floor']}\n\n")
                    md.append(f"**Fix:** {issue['recommendation']}\n\n")

        # Clearances — absence claims with their verification method
        if data.get('clearances'):
            md.append("## Clearances (verified absences)\n\n")
            for c in data['clearances']:
                md.append(f"- **{c['claim']}**\n")
                md.append(f"  - Method: {c['method']}\n")
                if c.get('evidence'):
                    md.append(f"  - Evidence: {c['evidence']}\n")
            md.append("\n")

        # Positive
        if data['positive_observations']:
            md.append("## Positive Observations\n\n")
            for obs in data['positive_observations']:
                md.append(f"- {obs}\n")

        # Observations
        if data.get('observations'):
            md.append("\n## Observations\n\n")
            for obs in data['observations']:
                md.append(f"- **`{obs['file']}`** — {obs['note']}\n")

        return ''.join(md)

    def save(self, output_dir: str):
        """Save both JSON and markdown."""
        os.makedirs(output_dir, exist_ok=True)

        json_path = os.path.join(output_dir, f"{self.reviewer}-review.json")
        md_path = os.path.join(output_dir, f"{self.reviewer}-review.md")

        with open(json_path, 'w') as f:
            f.write(self.to_json())

        with open(md_path, 'w') as f:
            f.write(self.to_markdown())

        # Telemetry: log agent completion (best-effort)
        # Use full agent name (reviewer + "-reviewer") to match the
        # agent_start event and .started file written by bootstrap.py.
        output = self.to_dict()

        # Echo the RECORDED state so the calling agent reconciles its
        # self-reported COUNTS against what was actually saved, not its
        # intent — a mismatch here means a finding was dropped or mangled
        # before serialization.
        by_sev = output['summary']['by_severity']
        counts_str = ", ".join(f"{sev}: {by_sev[sev]}" for sev in _VALID_SEVERITIES)
        print(f"RECORDED COUNTS: {counts_str}")
        print(
            f"RECORDED ISSUES: {output['summary']['total_issues']} | "
            f"OBSERVATIONS: {len(self.observations)} | "
            f"VERDICT: {output['verdict']}"
        )
        _log_agent_complete_telemetry(
            output_dir,
            f"{self.reviewer}-reviewer",
            output['verdict'],
            output['summary']['total_issues'],
            output['summary']['by_severity'],
        )

        return {'json': json_path, 'markdown': md_path}

# Test
if __name__ == '__main__':
    builder = ReviewOutputBuilder(pr_id="123", reviewer="security")

    builder.add_issue(
        severity="critical",
        title="SQL Injection",
        file="src/User.php",
        line=42,
        description="Direct $_GET input in query",
        recommendation="Use $wpdb->prepare()",
        category="security",
        vulnerability_type="sql_injection"
    )

    builder.set_files_reviewed(1)

    print("=== JSON ===")
    print(builder.to_json())

    print("\n=== MARKDOWN ===")
    print(builder.to_markdown())
