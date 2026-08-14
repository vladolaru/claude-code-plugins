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
    builder.save(output_dir)  # persists the canonical JSON artifact

    Markdown is derived from the canonical JSON: render one dict with
    render_markdown(data), or from the shell via the CLI —
    `python3 output.py render <path>-review.json` prints one review's
    Markdown, `python3 output.py materialize <output_dir>` writes
    <reviewer>-review.md beside every *-review.json.
"""

import contextlib
import json
import os
import posixpath
import sys
import uuid

try:
    import fcntl
except ImportError:  # non-POSIX host — publish without the completion-publication lock
    fcntl = None
from datetime import datetime
from typing import List, Optional, Dict, Any


_VALID_SEVERITIES = ('critical', 'high', 'medium', 'low', 'info')
_VALID_CHANNELS = ('blocking', 'advisory')
_SEVERITY_RANK = {
    'info': 0,
    'low': 1,
    'medium': 2,
    'high': 3,
    'critical': 4,
}
_VERDICT_RANK = {
    'approve': 0,
    'comment': 1,
    'request_changes': 2,
    'block': 3,
}


def _verdict_for_issues(issues) -> str:
    """Calculate a gating verdict for the supplied findings."""
    counts = {'critical': 0, 'high': 0, 'medium': 0}

    for issue in issues:
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


def render_markdown(data: Dict) -> str:
    """Human-readable Markdown rendered from a review's canonical dict.

    A pure function of the JSON representation — the same dict
    to_dict()/to_json() produce and the *-review.json file holds — so a
    rendering can never disagree with the artifact it came from.

    Keys emitted since schema v1.0.0 are required (missing means KeyError —
    the caller's problem); later schema additions are read with .get() and
    render only when present.
    """
    md = []

    md.append(f"# {data['reviewer'].title()} Review - PR #{data['pr_id']}\n\n")
    md.append("## Executive Summary\n\n")
    md.append(f"**Verdict:** {data['verdict'].upper()}\n")
    md.append(f"**Total Issues:** {data['summary']['total_issues']}\n\n")

    advisory_suppressed = data['summary'].get('advisory_suppressed', 0)
    if advisory_suppressed:
        finding_word = "finding" if advisory_suppressed == 1 else "findings"
        md.append(
            f"**Advisory suppression:** {advisory_suppressed} {finding_word} "
            "excluded from the verdict"
        )
        verdict_without_advisory = data['summary'].get(
            'verdict_without_advisory'
        )
        if verdict_without_advisory:
            md.append(
                " (verdict without suppression: "
                f"{verdict_without_advisory.upper()})"
            )
        md.append("\n\n")

    if data['summary']['total_issues'] > 0:
        counts = data['summary']['by_severity']
        md.append(f"- Critical: {counts['critical']}\n")
        md.append(f"- High: {counts['high']}\n")
        md.append(f"- Medium: {counts['medium']}\n\n")

    # Coverage gap — two populations share the 'unreviewed' array but not a
    # reason, so they never share a label: what the reviewer declared at
    # budget exhaustion, and what save() auto-declared because it was
    # neither claimed nor declared. Filing the latter under "budget" would
    # attribute the system's backfill to the reviewer's judgment. Older
    # outputs carry no marker and render exactly as they used to.
    if data.get('unreviewed'):
        meta = data.get('meta')
        marker = meta.get('unreviewed_autofilled') if isinstance(meta, dict) else None
        # A non-list marker says nothing usable about membership (a string
        # would split into a set of characters), so it is ignored and every
        # path keeps the declared label.
        autofilled = set(marker) if isinstance(marker, list) else set()
        declared = [f for f in data['unreviewed'] if f not in autofilled]
        auto_declared = [f for f in data['unreviewed'] if f in autofilled]
        if declared:
            files = ", ".join(f"`{f}`" for f in declared)
            md.append(f"**Not reviewed (budget):** {files}\n\n")
        if auto_declared:
            files = ", ".join(f"`{f}`" for f in auto_declared)
            md.append(
                "**Not reviewed (unaccounted — auto-declared at save):** "
                f"{files}\n\n"
            )

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


def materialize_markdown(output_dir: str) -> List[str]:
    """Render <reviewer>-review.md beside every *-review.json in output_dir.

    Derived artifacts for humans browsing the output directory: idempotent,
    regenerated from the settled canonical JSON, read by no pipeline
    consumer (readiness, reconciliation, and the bot all key on the JSON).
    Malformed JSONs are skipped with a note on stderr — grading and
    reconciliation report those failures on their own channels.
    """
    written: List[str] = []
    for name in sorted(os.listdir(output_dir)):
        if not name.endswith("-review.json"):
            continue
        json_path = os.path.join(output_dir, name)
        try:
            with open(json_path, encoding="utf-8") as handle:
                data = json.load(handle)
            md_text = render_markdown(data)
        except (OSError, ValueError, KeyError, TypeError, AttributeError) as err:
            print(f"skipped {name}: {err}", file=sys.stderr)
            continue
        md_path = json_path[: -len(".json")] + ".md"
        with open(md_path, "w", encoding="utf-8") as handle:
            handle.write(md_text)
        written.append(md_path)
    return written


class ReviewOutputBuilder:
    """Simple builder for structured review outputs."""

    def __init__(self, pr_id: str, reviewer: str):
        # Agents that hand-roll a builder script pass whatever the bootstrap
        # wrapper would have injected as a string — a real run shipped an int
        # that serialized as a JSON number, so the artifact's shape stopped
        # being uniform across reviewers. Coerce once, at construction.
        self.pr_id = pr_id if isinstance(pr_id, str) else str(pr_id)
        self.reviewer = reviewer
        self.timestamp = datetime.now().isoformat()
        self.issues = []
        self.observations = []
        self.recommendations = {'immediate': [], 'important': [], 'suggestions': []}
        self.positive_observations = []
        self.clearances = []
        # Agent-authored: gaps the reviewer declared (plus, after save(),
        # the derived fill below merged in).
        self.unreviewed = []
        # Agent-authored: deferred files the reviewer claims it read.
        self.deferred_reviewed = []
        # Derived at save(): the subset of self.unreviewed the builder
        # auto-declared because the reviewer stated nothing about it.
        self.unreviewed_autofilled = []
        self.files_reviewed = 0
        self.review_start = datetime.now()
        self.tool_results_used = []
        self.overall_confidence = 0.95
        self._not_applicable = False
        self._skip_reason = None
        self._deferred_files_loaded = False
        self._deferred_files = None
        self._advisory_entitlement_loaded = False
        self._advisory_entitlement = None

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
        *,
        channel: Optional[str] = None,
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

        if channel is not None:
            if not isinstance(channel, str) or channel not in _VALID_CHANNELS:
                raise ValueError(
                    f"Invalid channel: {channel!r}. "
                    f"Must be one of {_VALID_CHANNELS}."
                )
            if (
                channel == "advisory"
                and self._known_advisory_entitlement() is False
            ):
                raise ValueError(
                    "Cannot record advisory finding: this reviewer is not "
                    "entitled to the advisory channel."
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
        if channel == 'advisory':
            issue['channel'] = channel

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

    @staticmethod
    def _load_deferred_files(
        output_dir: Optional[str], reviewer: Optional[str]
    ) -> Optional[frozenset]:
        """Load the bootstrap-written deferred set, or None when unavailable.

        None is deliberate fail-open: no sidecar means no authoritative set
        exists (manual builder use, older bootstrap, failed fail-open write)
        and validation stays form-only.
        """
        if not output_dir or not reviewer:
            return None
        sidecar = os.path.join(output_dir, f"{reviewer}-deferred-files.json")
        try:
            with open(sidecar, "r", encoding="utf-8") as f:
                data = json.load(f)
        except (OSError, UnicodeDecodeError, json.JSONDecodeError):
            return None
        files = data.get("deferred_files") if isinstance(data, dict) else None
        if not isinstance(files, list):
            return None
        return frozenset(p for p in files if isinstance(p, str))

    def _known_deferred_files(self) -> Optional[frozenset]:
        """The deferred set via the env envelope — add-time fast feedback.

        Authoritative enforcement happens at save() with the explicit
        output directory; this lookup only makes add_unreviewed() fail
        earlier on the recommended path.
        """
        if self._deferred_files_loaded:
            return self._deferred_files
        self._deferred_files_loaded = True
        self._deferred_files = self._load_deferred_files(
            os.environ.get("PIRATEGOAT_OUTPUT_DIR"),
            os.environ.get("PIRATEGOAT_REVIEWER_NAME"),
        )
        return self._deferred_files

    @staticmethod
    def _normalize_deferred_path(file: str, api_name: str) -> str:
        """The one path grammar both deferred-set APIs speak.

        Declarations and claims address the same namespace — the canonical
        repo-relative paths scope.py emits — so they must accept and reject
        exactly the same spellings. Keeping the grammar here rather than in
        each API is what stops the two from drifting: when they lived apart,
        claims accepted '/etc/passwd' and '../x' that declarations rejected.

        Normalizes "./src/x.php", "src\\x.php", and "src//x.php" to one
        form, and rejects forms no scope path can ever take (absolute,
        traversal, drive-prefixed, dot-only) — an unmatched path is not a
        near miss, it is a coverage statement about a file that does not
        exist in this review.
        """
        if not isinstance(file, str) or not file.strip():
            raise ValueError(f"{api_name} requires a non-empty file path.")
        path = posixpath.normpath(file.strip().replace("\\", "/"))
        if (
            path.startswith("/")
            or path == "."
            or path == ".."
            or path.startswith("../")
            or (len(path) >= 2 and path[1] == ":" and path[0].isalpha())
        ):
            raise ValueError(
                f"{api_name} requires a repository-relative path exactly "
                f"as shown in the NOT DIFFED listing, got {file!r}."
            )
        return path

    @staticmethod
    def _reject_unknown_deferred(
        paths: List[str], known: frozenset, api_name: str, noun: str
    ) -> None:
        """Raise the one canonical rejection for out-of-set deferred paths.

        Every enforcement point shares this phrasing. Add-time passes the
        single path just offered so feedback stays immediate; save-time
        passes every offender at once, so a review carrying 23 bad
        declarations costs one round trip instead of 23. ``api_name`` names
        the calling API, keeping rejections from the sibling deferred-set
        APIs distinguishable to agent and test alike, and ``noun`` says what
        the offending paths were offered as ("declaration", "claim").

        ``noun`` is deliberately required rather than defaulted: a default
        is how the empty-set branch came to tell a claimant that "nothing
        may be declared", and the next sibling API would inherit the same
        wrong word silently.
        """
        valid = (
            "Valid paths: " + ", ".join(sorted(known))
            if known
            else f"This review has no deferred files, so no {noun} may be "
                 "made."
        )
        offenders = ", ".join(repr(p) for p in paths)
        raise ValueError(
            f"{api_name} received {len(paths)} {noun}(s) matching no "
            f"NOT DIFFED file of this review: {offenders}. {valid}"
        )

    def _validate_deferred_serialization(
        self, output_dir: str
    ) -> Optional[frozenset]:
        """Authoritative deferred-set validation at publication time.

        Runs on EVERY save regardless of how the builder was invoked —
        save() already knows the output directory and reviewer, so the
        check cannot be bypassed by skipping the env envelope. Returns the
        known set (None preserves fail-open for genuinely sidecar-less use).
        Fail-open is membership-only: the contradiction guard below runs
        before it, because it needs no sidecar to be right.

        The seam differs from its advisory sibling on purpose: advisory
        entitlement revalidates at to_dict(output_dir=...) (serialization),
        this at save() (publication), so a caller serializing manually via
        to_dict/to_json knowingly opts out of deferred validation.
        """
        # Both agent-authored lists may be individually valid — or
        # unvalidatable — and still contradict each other. Serializing a path
        # into both arrays publishes two opposite statements about one file
        # and inflates the accounting (three statements about two files),
        # leaving every consumer to guess — conservatively "declared",
        # overriding the explicit claim. The reviewer is the only one who
        # knows which it meant.
        #
        # This runs ABOVE the fail-open return below because it compares the
        # reviewer's two lists against each other, not against the sidecar:
        # self-consistency needs no authority. Fail-open covers MEMBERSHIP
        # ("is this path a deferred file of this review?") — the one question
        # only the sidecar can answer — so a missing sidecar must not turn a
        # contradiction into a published artifact.
        #
        # Only the reviewer's own statements reach here: save() strips the
        # previous auto-fill before calling this, so the sanctioned
        # claim-after-warning re-save is not a contradiction.
        contradicted = sorted(
            set(self.unreviewed) & set(self.deferred_reviewed)
        )
        if contradicted:
            raise ValueError(
                f"{len(contradicted)} path(s) are both declared unreviewed "
                f"and claimed reviewed: "
                f"{', '.join(repr(p) for p in contradicted)}. "
                "A file is one or the other — make only one of the two calls "
                "for this path in your builder script and run it again."
            )
        known = self._load_deferred_files(output_dir, self.reviewer)
        if known is None:
            return None
        unknown = [path for path in self.unreviewed if path not in known]
        if unknown:
            self._reject_unknown_deferred(
                unknown, known, "add_unreviewed", "declaration"
            )
        # Claims are checked separately from declarations, under their own
        # api_name: both offenses mean "not a deferred file of this review",
        # but a wrongly declared gap and a wrongly claimed read need
        # different fixes, so the raises must stay attributable. The price
        # is that a review carrying both kinds of offense costs two round
        # trips instead of one — accepted deliberately, because a merged
        # message would have to drop the attribution that makes each
        # offender actionable.
        unknown_claims = [
            path for path in self.deferred_reviewed if path not in known
        ]
        if unknown_claims:
            self._reject_unknown_deferred(
                unknown_claims, known, "add_deferred_reviewed", "claim"
            )
        return known

    @staticmethod
    def _load_advisory_entitlement(
        output_dir: Optional[str], reviewer: Optional[str]
    ) -> Optional[bool]:
        """Load a bootstrap-declared advisory entitlement when authoritative.

        ``None`` is deliberate fail-open behavior: absent paths, absent files,
        write failures upstream, malformed JSON, wrong top-level shapes, and
        non-boolean declarations leave only the already-enforced channel
        vocabulary validation. Only an explicit boolean false denies advisory
        findings.
        """
        if not output_dir or not reviewer:
            return None
        sidecar = os.path.join(
            output_dir, f"{reviewer}-advisory-entitlement.json"
        )
        try:
            with open(sidecar, "r", encoding="utf-8") as f:
                data = json.load(f)
        except (OSError, UnicodeDecodeError, json.JSONDecodeError):
            return None
        entitled = data.get("advisory_entitled") if isinstance(data, dict) else None
        return entitled if isinstance(entitled, bool) else None

    def _known_advisory_entitlement(self) -> Optional[bool]:
        """Return the cached entitlement from the canonical env envelope.

        This add-time lookup intentionally fails open to vocabulary-only
        validation when the envelope or a valid boolean sidecar is unavailable.
        Canonical serialization can independently revalidate against an
        explicit output directory.
        """
        if self._advisory_entitlement_loaded:
            return self._advisory_entitlement
        self._advisory_entitlement_loaded = True
        self._advisory_entitlement = self._load_advisory_entitlement(
            os.environ.get("PIRATEGOAT_OUTPUT_DIR"),
            os.environ.get("PIRATEGOAT_REVIEWER_NAME"),
        )
        return self._advisory_entitlement

    def _validate_advisory_serialization(
        self, output_dir: Optional[str]
    ) -> None:
        """Reject explicitly unentitled advisory issues at finalization.

        Missing or malformed sidecars remain deliberately fail-open after the
        channel vocabulary has been validated. An explicit false is the only
        authoritative denial.
        """
        if not any(issue.get("channel") == "advisory" for issue in self.issues):
            return
        if self._load_advisory_entitlement(output_dir, self.reviewer) is False:
            raise ValueError(
                "Cannot serialize advisory finding: this reviewer is not "
                "entitled to the advisory channel."
            )

    def add_unreviewed(self, file: str):
        """Declare an in-scope file left unreviewed after budget exhaustion.

        Use ONLY for NOT DIFFED files genuinely out of reach when the tool
        budget ran out. Declared files render under the
        '**Not reviewed (budget):**' line in the Markdown summary and appear
        as 'unreviewed' in the JSON output, so downstream coverage accounting
        sees the gap. They never count toward the verdict.

        Explicit declaration is not the only way into that list: save()
        auto-declares any deferred file left neither declared here nor
        claimed via add_deferred_reviewed(), marking it in
        meta.unreviewed_autofilled and re-deriving both on every save.
        Declaring deliberately is still what distinguishes a known gap
        from an unnoticed one — and declaring a path the previous save
        auto-declared promotes it out of that marker, recording the gap as
        the reviewer's own statement.

        A path declared here must not also be claimed via
        add_deferred_reviewed(): save() rejects the contradiction rather
        than publishing both statements about one file.
        """
        # Shared grammar: an unmatchable declaration would invert into a
        # deferred-but-reviewed claim downstream, so malformed forms fail here.
        path = self._normalize_deferred_path(file, "add_unreviewed")
        # When bootstrap persisted the authoritative deferred set, a
        # declaration outside it (typo, wrong repo root) is rejected at
        # write time — form checks alone cannot catch a path that is merely
        # wrong rather than malformed, and downstream it would silently
        # count as a reviewed claim for every genuinely deferred file.
        known = self._known_deferred_files()
        if known is not None and path not in known:
            self._reject_unknown_deferred(
                [path], known, "add_unreviewed", "declaration"
            )
        if path in self.unreviewed_autofilled:
            # An explicit declaration outranks system backfill: promote the
            # path out of derived state so the next save records it as the
            # reviewer's own statement. Without this the call is a silent
            # no-op — the path is already in self.unreviewed — and the
            # marker would keep attributing to the system a gap the agent
            # has just taken ownership of.
            self.unreviewed_autofilled.remove(path)
        if path not in self.unreviewed:
            self.unreviewed.append(path)

    def add_deferred_reviewed(self, *files: str):
        """Claim NOT DIFFED (deferred) files as actually reviewed.

        A claim is a statement, not proof of read — downstream coverage
        accounting labels it as such. Call as you finish each deferred file
        (or once with several paths). Claiming is what makes a deferred
        file the reviewer read distinguishable from one it never opened:
        a deferred file neither claimed here nor declared via
        add_unreviewed() is auto-declared unreviewed at save() and listed
        in meta.unreviewed_autofilled. Silence records a gap; it never
        counts as review. Auto-fill is recomputed on every save, so
        claiming a file you did read and saving again clears both the
        auto-declaration and its warning.

        Claims share add_unreviewed()'s path grammar and are validated
        against the authoritative deferred set with the same membership
        rule — at add time when the env envelope is present, and always at
        save().
        """
        if not files:
            raise ValueError(
                "add_deferred_reviewed requires at least one file path — a "
                "call claiming nothing is a no-op, not a claim."
            )
        known = self._known_deferred_files()
        for file in files:
            path = self._normalize_deferred_path(
                file, "add_deferred_reviewed"
            )
            if known is not None and path not in known:
                self._reject_unknown_deferred(
                    [path], known, "add_deferred_reviewed", "claim"
                )
            if path not in self.deferred_reviewed:
                self.deferred_reviewed.append(path)

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

        # Advisory-channel findings are listed but do not gate the verdict.
        return _verdict_for_issues(
            issue for issue in self.issues
            if issue.get('channel') != 'advisory'
        )

    def _advisory_measurement(self, verdict: str) -> Dict[str, Any]:
        """Measure exact advisory-tag suppression without changing verdicts."""
        if self._not_applicable:
            # The not-applicable verdict short-circuits before channel tags are
            # consulted, so no finding was excluded from its calculation.
            return {'advisory_suppressed': 0}

        suppressed = sum(
            issue.get('channel') == 'advisory' for issue in self.issues
        )
        measurement: Dict[str, Any] = {'advisory_suppressed': suppressed}
        if suppressed == 0:
            return measurement

        verdict_without_advisory = _verdict_for_issues(self.issues)
        if _VERDICT_RANK[verdict_without_advisory] > _VERDICT_RANK[verdict]:
            measurement['verdict_without_advisory'] = verdict_without_advisory
        return measurement

    def to_dict(self, *, output_dir: Optional[str] = None) -> Dict:
        """Build as dictionary, revalidating advisory issues when directed.

        Without an explicit directory, manual and legacy callers retain the
        deliberate fail-open, vocabulary-only advisory behavior.

        Deferred-coverage fields reflect the LAST save()'s derivation:
        unreviewed carries any auto-declared paths and
        meta.unreviewed_autofilled names them. Called before any save, both
        contain only what the reviewer itself stated.
        """
        if output_dir is not None:
            self._validate_advisory_serialization(output_dir)
        review_duration = int((datetime.now() - self.review_start).total_seconds() * 1000)

        severity_counts = {'critical': 0, 'high': 0, 'medium': 0, 'low': 0, 'info': 0}
        for issue in self.issues:
            severity_counts[issue['severity']] += 1

        verdict = self._calculate_verdict()
        summary = {
            'total_issues': len(self.issues),
            'by_severity': severity_counts,
        }
        summary.update(self._advisory_measurement(verdict))

        result = {
            'pr_id': self.pr_id,
            'reviewer': self.reviewer,
            'timestamp': self.timestamp,
            'version': '1.0.0',
            'verdict': verdict,
            'summary': summary,
            'issues': self.issues,
            'unreviewed': self.unreviewed if self.unreviewed else None,
            # Never nulled when empty, unlike its siblings above: key
            # presence is the downstream consumer's signal that this output
            # carries explicit deferred-review claims, so an empty list must
            # stay readable as "claimed nothing" rather than "old producer".
            'deferred_reviewed': self.deferred_reviewed,
            'observations': self.observations if self.observations else None,
            'recommendations': self.recommendations if any(self.recommendations.values()) else None,
            'positive_observations': self.positive_observations if self.positive_observations else None,
            'clearances': self.clearances if self.clearances else None,
            'meta': {
                'files_reviewed': self.files_reviewed,
                'unreviewed_autofilled': (
                    self.unreviewed_autofilled
                    if self.unreviewed_autofilled else None
                ),
                'review_duration_ms': review_duration,
                'confidence_score': self.overall_confidence,
                'tool_results_used': self.tool_results_used if self.tool_results_used else None
            }
        }
        if self._skip_reason:
            result['skip_reason'] = self._skip_reason
        return result

    def to_json(
        self, indent: int = 2, *, output_dir: Optional[str] = None
    ) -> str:
        """Generate JSON, optionally revalidating advisory entitlement."""
        return json.dumps(
            self.to_dict(output_dir=output_dir),
            indent=indent,
            ensure_ascii=False,
        )

    def to_markdown(self) -> str:
        """Generate human-readable markdown."""
        return render_markdown(self.to_dict())

    def save(self, output_dir: str):
        """Publish the review JSON — the single canonical artifact.

        Markdown is derived from this JSON on demand (render_markdown /
        materialize_markdown; reconciliation materializes it for humans at
        end of run), so there is no artifact pair to keep consistent: an
        interrupted re-save simply leaves the previous complete JSON
        visible, the normal semantics of an atomic single-file write.
        """
        os.makedirs(output_dir, exist_ok=True)

        # Auto-fill is DERIVED state, recomputed from scratch on every save,
        # and the strip runs FIRST — before validation, before the
        # contradiction check, before the new derivation. That ordering is
        # load-bearing: the reviewer's answer to the warning is to claim a
        # file it did read, and the previous fill still lists that file as
        # unreviewed. Stripping first means validation only ever sees what
        # the reviewer itself stated, so the sanctioned remediation is not
        # mistaken for a declare-plus-claim contradiction, while a genuine
        # contradiction between two agent statements is still rejected.
        # Only paths this builder auto-filled are dropped, so agent-authored
        # declarations survive (add_unreviewed() promotes a path out of the
        # marker precisely so it survives here). The strip is unconditional
        # while the re-derivation below is not, so a save whose sidecar has
        # become unreadable publishes no derived gaps at all — derived state
        # states nothing once the authority that justified it is gone.
        if self.unreviewed_autofilled:
            previous_autofill = set(self.unreviewed_autofilled)
            self.unreviewed = [
                p for p in self.unreviewed if p not in previous_autofill
            ]
            self.unreviewed_autofilled = []

        known_deferred = self._validate_deferred_serialization(output_dir)
        # Close the silent third state: every deferred file must end up
        # claimed, declared, or auto-declared. Auto-fill is marked so
        # metrics can separate agent honesty from system honesty.
        if known_deferred is not None:
            unaccounted = sorted(
                known_deferred
                - set(self.deferred_reviewed)
                - set(self.unreviewed)
            )
            self.unreviewed_autofilled = unaccounted
            if unaccounted:
                self.unreviewed.extend(unaccounted)

        json_path = os.path.join(output_dir, f"{self.reviewer}-review.json")
        serialized = self.to_json(output_dir=output_dir)
        output = json.loads(serialized)

        # The review JSON is the readiness signal agents_status.py polls,
        # and the pipeline may finalize the telemetry manifest the moment
        # every agent looks finished. Completion must therefore be durable
        # BEFORE the JSON becomes visible — otherwise a finalize racing
        # this save records the agent permanently incomplete.
        # The staging name carries a nonce because the lifecycle supports
        # overlapping executions of the same reviewer (retry before the
        # prior invocation finishes): a shared staging file would let one
        # execution's os.replace() consume the other's staged artifact.
        nonce = uuid.uuid4().hex
        staged_json_path = f"{json_path}.{nonce}.tmp"
        try:
            with open(staged_json_path, 'w') as f:
                f.write(serialized)

            # Echo the RECORDED state so the calling agent reconciles its
            # self-reported COUNTS against what was actually saved, not its
            # intent — a mismatch here means a finding was dropped or
            # mangled before serialization.
            by_sev = output['summary']['by_severity']
            counts_str = ", ".join(f"{sev}: {by_sev[sev]}" for sev in _VALID_SEVERITIES)
            print(f"RECORDED COUNTS: {counts_str}")
            print(
                f"RECORDED ISSUES: {output['summary']['total_issues']} | "
                f"OBSERVATIONS: {len(self.observations)} | "
                f"VERDICT: {output['verdict']}"
            )
            # Deferred-coverage accounting, echoed for the same reason as
            # the counts above: the agent still has a turn left to correct
            # it. Auto-fill happened silently in the file; here it is
            # visible, so an agent that DID read the file can claim it and
            # save again rather than shipping a gap it never intended.
            declared = [
                p for p in self.unreviewed
                if p not in self.unreviewed_autofilled
            ]
            unreviewed_line = f"UNREVIEWED: {len(declared)} declared"
            if self.unreviewed_autofilled:
                unreviewed_line += (
                    f" (+{len(self.unreviewed_autofilled)} auto-filled)"
                )
            if known_deferred is not None:
                unreviewed_line += (
                    f" / {len(known_deferred)} deferred | "
                    f"CLAIMED REVIEWED: {len(self.deferred_reviewed)}"
                )
            print(unreviewed_line)
            if self.unreviewed_autofilled:
                print(
                    "WARNING: deferred files neither claimed nor declared "
                    "were auto-declared unreviewed. If you actually read "
                    "them, claim them with add_deferred_reviewed(...) and "
                    "save again."
                )
            # Completion telemetry and publication run under one exclusive
            # lock so {log, publish} is a single atomic unit per execution:
            # the manifest's latest agent_complete always describes the
            # JSON published last, never a slower overlapping save's. The
            # log still precedes the replace — completion must be durable
            # before the readiness signal a racing finalize would trust
            # becomes visible. The lock is the output directory's own fd
            # (no lock file to leave behind; flock auto-releases if the
            # process dies); where flock is unavailable (non-POSIX) the
            # two steps still run back-to-back.
            with contextlib.ExitStack() as stack:
                if fcntl is not None:
                    lock_fd = os.open(output_dir, os.O_RDONLY)
                    stack.callback(os.close, lock_fd)
                    fcntl.flock(lock_fd, fcntl.LOCK_EX)
                _log_agent_complete_telemetry(
                    output_dir,
                    f"{self.reviewer}-reviewer",
                    output['verdict'],
                    output['summary']['total_issues'],
                    output['summary']['by_severity'],
                )
                os.replace(staged_json_path, json_path)
        finally:
            # A unique staging name never self-overwrites, so a failed save
            # must remove its orphan (replace already consumed it on
            # success).
            try:
                os.unlink(staged_json_path)
            except FileNotFoundError:
                pass

        return {'json': json_path}

if __name__ == '__main__':
    import argparse

    parser = argparse.ArgumentParser(
        description="Render reviewer Markdown from canonical review JSON.",
    )
    sub = parser.add_subparsers(dest="command", required=True)
    render_cmd = sub.add_parser(
        "render", help="Print the Markdown for one *-review.json",
    )
    render_cmd.add_argument("json_path")
    mat_cmd = sub.add_parser(
        "materialize",
        help="Write <reviewer>-review.md beside every *-review.json in a directory",
    )
    mat_cmd.add_argument("output_dir")
    cli_args = parser.parse_args()
    if cli_args.command == "render":
        with open(cli_args.json_path, encoding="utf-8") as cli_handle:
            print(render_markdown(json.load(cli_handle)))
    else:
        for written_path in materialize_markdown(cli_args.output_dir):
            print(written_path)
