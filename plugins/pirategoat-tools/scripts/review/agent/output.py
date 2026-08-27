"""
Simple Review Output Builder (No Dependencies)

Lightweight version without Pydantic for immediate use.
Provides structure and basic validation using plain Python.

Usage:
    from review.agent.output import ReviewOutputBuilder

    builder = ReviewOutputBuilder.open(output_dir, "123", "security")
    builder.add_finding(
        severity="critical",
        title="SQL Injection",
        file="src/User.php",
        line=42,
        description="...",
        recommendation="..."
    )
    saved = builder.save_draft()
    finalize_review(output_dir, "security", saved["review_digest"])

    Markdown is derived from the final JSON by review_markdown.py; the
    document contract every reader validates through lives in
    review_document.py.
"""

import hashlib
import json
import os
import sys
import uuid
from pathlib import Path
from datetime import datetime, timezone
from typing import List, Optional, Dict, Any

try:
    from .review_assignment import (
        ReviewAssignmentError,
        derive_reviewed_files,
        normalize_review_path,
    )
except ImportError:
    _SCRIPTS_DIR = os.path.dirname(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    )
    if _SCRIPTS_DIR not in sys.path:
        sys.path.insert(0, _SCRIPTS_DIR)
    from review.agent.review_assignment import (
        ReviewAssignmentError,
        derive_reviewed_files,
        normalize_review_path,
    )

try:
    from ..atomic_io import output_dir_lock
    from ..reviewer_lifecycle import (
        finalize_review_command,
        require_not_finalized,
        require_review_intake_open,
        review_paths,
    )
    from ..reviewer_names import derive_reviewer_name
    from ..review_document import (
        REVIEW_OUTPUT_SCHEMA,
        VALID_CHANNELS,
        validate_check_shape,
        validate_finding_shape,
        validate_review_document,
    )
except ImportError:
    from review.atomic_io import output_dir_lock
    from review.reviewer_lifecycle import (
        finalize_review_command,
        require_not_finalized,
        require_review_intake_open,
        review_paths,
    )
    from review.reviewer_names import derive_reviewer_name
    from review.review_document import (
        REVIEW_OUTPUT_SCHEMA,
        VALID_CHANNELS,
        validate_check_shape,
        validate_finding_shape,
        validate_review_document,
    )

try:
    from ..verdict_rules import (
        SEVERITY_RANK,
        VALID_SEVERITIES,
        derive_review_state,
        summary_for,
    )
except ImportError:
    # Stand-alone use — every reviewer publishes through
    # `python3 output.py finalize-review …`, which runs this file as
    # `__main__`, where a relative import has no package to resolve
    # against. The fallback puts `scripts/` on the path and imports the
    # same module rather than keeping a local copy of the ladder to drift
    # from: the verdict IS the artifact's headline, so a second copy here
    # would publish a different one.
    _SCRIPTS_DIR = os.path.dirname(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    )
    if _SCRIPTS_DIR not in sys.path:
        sys.path.insert(0, _SCRIPTS_DIR)
    from review.verdict_rules import (
        SEVERITY_RANK,
        VALID_SEVERITIES,
        derive_review_state,
        summary_for,
    )


def coerce_text(value: Any, single_line: bool = False) -> str:
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
        result = "\n".join(coerce_text(item) for item in value)
    else:
        result = str(value)
    if single_line:
        result = " ".join(result.split())
    return result


# The reviewer dispatch-marker suffix. Spelled here rather than imported
# from review/synthesis_lifecycle.py so the `finalize-review` CLI needs no
# import beyond the ones above to time an actor. Parity with the
# bootstrap-written `<agent>.started` contract is pinned by tests, so a
# rename fails loudly instead of silently unmeasuring a whole class of actor.
_REVIEWER_START_SUFFIX = ".started"


def _actor_start_time(
    output_dir: Optional[str], marker_name: Optional[str]
) -> Optional[datetime]:
    """When this actor was dispatched, per the marker the pipeline wrote.

    The only honest clock the builder has. A builder is constructed inside
    the final heredoc, seconds before serialization, so measuring from its
    own __init__ times the write and calls it the review — which is how
    every artifact of a 19-agent run came to carry a duration of ~0ms,
    including a reconciliator that ran for 211 seconds.

    ``marker_name`` is the exact filename its actor names for itself, never
    a guess: a reviewer names ``<agent_name>.started`` from the agent name
    its assignment carries, and a synthesis actor names its own
    ``.synthesis-started`` file. None everywhere the answer is not known —
    no directory, no name, no marker, or an unreadable stamp. Absence is
    reported as absence, never as zero.
    """
    if not output_dir or not marker_name:
        return None
    path = os.path.join(output_dir, marker_name)
    try:
        with open(path, "r", encoding="utf-8") as handle:
            return datetime.fromisoformat(handle.read().strip())
    except (OSError, UnicodeDecodeError, ValueError):
        return None


def _telemetry_for_output(output_dir):
    """Reach telemetry from inside the call, not from the module body.

    `telemetry.py` imports `critic_adjustments`, which imports
    `findings_ledger`, which imports this module for the builder — so a
    module-level import here closes a real cycle. It also keeps the
    import out of the reviewer's one-shot heredoc, which wants
    ReviewOutputBuilder and nothing behind it.

    Both spellings, like every import block above: the `finalize-review`
    CLI runs this file as `__main__`, where a relative import has no
    package to resolve against.
    """
    try:
        from ..telemetry import ReviewTelemetry
    except ImportError:
        from review.telemetry import ReviewTelemetry

    return ReviewTelemetry(output_dir)


def _log_agent_review_draft_saved_telemetry(
    output_dir, agent_name, review_digest
):
    telemetry = _telemetry_for_output(output_dir)
    telemetry.log_agent_review_draft_saved(
        agent_name=agent_name, review_digest=review_digest
    )


def _log_agent_complete_telemetry(
    output_dir, agent_name, verdict, finding_count, severities, review_digest
):
    telemetry = _telemetry_for_output(output_dir)
    telemetry.log_agent_complete(
        agent_name=agent_name,
        verdict=verdict,
        finding_count=finding_count,
        severities=severities,
        review_digest=review_digest,
    )


def _validate_review_bytes(
    data: bytes, *, reviewer: str, pr_id: str
) -> dict:
    """Validate one persisted draft before rehydrating builder state."""
    try:
        review = json.loads(data)
    except (UnicodeDecodeError, json.JSONDecodeError, TypeError) as exc:
        raise ValueError("malformed review draft JSON") from exc
    validate_review_document(review, reviewer)
    expected_pr_id = pr_id if isinstance(pr_id, str) else str(pr_id)
    if review["pr_id"] != expected_pr_id:
        raise ValueError("review draft PR does not match open request")
    return review


def _optional_file_digest(path: str) -> str | None:
    """Return a file's SHA-256 digest, or None only when it is absent."""
    try:
        data = Path(path).read_bytes()
    except FileNotFoundError:
        return None
    return hashlib.sha256(data).hexdigest()


def _atomic_replace_bytes(path: str, data: bytes) -> None:
    """Atomically replace one file with staged bytes and clean failures."""
    staged_path = f"{path}.{uuid.uuid4().hex}.tmp"
    try:
        Path(staged_path).write_bytes(data)
        os.replace(staged_path, path)
    finally:
        try:
            os.unlink(staged_path)
        except FileNotFoundError:
            pass


def render_draft_index(review: dict) -> str:
    """Render concise mutable review state for continuation bootstrap."""
    findings = review.get("findings") or []
    checks = review.get("checks") or []
    reviewed_file_claims = review.get("reviewed_file_claims") or []
    lines = [
        "DRAFT INDEX:",
        f"  findings {len(findings)} | checks {len(checks)} | "
        f"reviewed-file claims {len(reviewed_file_claims)}",
    ]
    for finding in findings:
        location = (
            f"{finding['file']}:{finding['line']}"
            if finding["line"] is not None
            else f"{finding['file']} (file scope)"
        )
        lines.append(
            f"  finding {finding['id']}: {finding['severity']} "
            f"{json.dumps(finding['title'], ensure_ascii=False)} @ {location}"
        )
    for check in checks:
        lines.append(
            f"  check {check['id']}: "
            f"{json.dumps(check['question'], ensure_ascii=False)}"
        )
    for path in reviewed_file_claims:
        lines.append(f"  reviewed-file claim: {path}")
    return "\n".join(lines)


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
        self.findings = []
        self.observations = []
        self.recommendations = {'immediate': [], 'important': [], 'suggestions': []}
        self.positive_observations = []
        self.checks = []
        # Agent-authored: the producer's own reading of the change as a
        # whole. The reconciliator's overall-state prose lives here.
        self.assessment = None
        self.next_finding_number = 1
        self.next_check_number = 1
        # Agent-authored: review-claimable files the reviewer claims it read.
        self.reviewed_file_claims = []
        self.overall_confidence = 0.95
        self._not_applicable = False
        self._skip_reason = None
        self._output_dir = None
        self._paths = None
        self._base_digest = None
        self._last_saved_review = None
        self._invocation_delta = []

    @classmethod
    def open(
        cls, output_dir: str, pr_id: str, reviewer: str
    ) -> "ReviewOutputBuilder":
        """Create or rehydrate one mutable draft under the lifecycle lock."""
        output_dir = str(output_dir)
        os.makedirs(output_dir, exist_ok=True)
        paths = review_paths(output_dir, reviewer)
        with output_dir_lock(output_dir):
            require_review_intake_open(output_dir)
            require_not_finalized(paths)
            if not os.path.exists(paths.draft):
                return cls(pr_id, reviewer)._bind(
                    output_dir, base_digest=None
                )
            draft_bytes = Path(paths.draft).read_bytes()
            review = _validate_review_bytes(
                draft_bytes, reviewer=reviewer, pr_id=pr_id
            )
            digest = hashlib.sha256(draft_bytes).hexdigest()
        return cls._from_review(review)._bind(
            output_dir, base_digest=digest
        )

    @classmethod
    def _from_review(cls, review: dict) -> "ReviewOutputBuilder":
        """Rehydrate every builder-owned field from a validated review."""
        builder = cls(review["pr_id"], review["reviewer"])
        builder.timestamp = review["timestamp"]
        builder.findings = list(review["findings"])
        builder.observations = list(review.get("observations") or [])
        recommendations = review.get("recommendations") or {}
        builder.recommendations = {
            priority: list(recommendations.get(priority) or [])
            for priority in ("immediate", "important", "suggestions")
        }
        builder.positive_observations = list(
            review.get("positive_observations") or []
        )
        builder.checks = list(review["checks"])
        builder.assessment = review.get("assessment")
        builder.reviewed_file_claims = list(review["reviewed_file_claims"])
        meta = review["meta"]
        builder.next_finding_number = meta["next_finding_number"]
        builder.next_check_number = meta["next_check_number"]
        builder.overall_confidence = meta["confidence_score"]
        builder._not_applicable = review["verdict"] == "not_applicable"
        builder._skip_reason = review.get("skip_reason")
        return builder

    def _bind(
        self, output_dir: str, *, base_digest: str | None
    ) -> "ReviewOutputBuilder":
        """Bind this builder to exactly one run and observed draft state."""
        self._output_dir = str(output_dir)
        self._paths = review_paths(self._output_dir, self.reviewer)
        self._base_digest = base_digest
        return self

    def _allocate_finding_id(self) -> str:
        finding_id = f"f{self.next_finding_number}"
        self.next_finding_number += 1
        return finding_id

    def _allocate_check_id(self) -> str:
        check_id = f"c{self.next_check_number}"
        self.next_check_number += 1
        return check_id

    @staticmethod
    def _entry_index(entries: list, entry_id: str, kind: str) -> int:
        for index, entry in enumerate(entries):
            if isinstance(entry, dict) and entry.get("id") == entry_id:
                return index
        raise ValueError(f"unknown {kind} id: {entry_id}")

    def _normalize_finding(self, fields: Dict, *, partial: bool) -> Dict:
        """Normalize one complete finding candidate — the one implementation.

        ``fields`` is the whole finding: freshly authored by ``add_finding``,
        or an existing one merged with a patch by ``update_finding``. Every
        check reads the merged result, so a patch that lowers ``severity``
        under a stored ``severity_floor`` is promoted exactly as a fresh add
        would be, and a patched title is collapsed exactly as an added one is.

        ``partial=False`` additionally drops the keys a new finding carries no
        information in — an absent floor, evidence or citation, and the
        default channel — so one writer cannot publish a shape the other
        would not.
        """
        candidate = dict(fields)
        severity = candidate.get("severity")
        if isinstance(severity, str):
            severity = candidate["severity"] = severity.lower()
        if severity not in VALID_SEVERITIES:
            raise ValueError(
                f"Invalid severity: {fields.get('severity')}. "
                f"Must be one of {list(VALID_SEVERITIES)}"
            )
        floor = candidate.get("severity_floor")
        if floor is not None:
            if not isinstance(floor, str) or floor.lower() not in VALID_SEVERITIES:
                raise ValueError(
                    f"Invalid severity_floor: {floor}. "
                    f"Must be one of {list(VALID_SEVERITIES)}"
                )
            floor = candidate["severity_floor"] = floor.lower()
            if SEVERITY_RANK[severity] < SEVERITY_RANK[floor]:
                candidate["severity"] = floor
        for field in ("title", "description", "recommendation"):
            candidate[field] = coerce_text(
                candidate.get(field), single_line=field == "title"
            )
        if candidate.get("line") is None:
            candidate["scope"] = "file"
        else:
            candidate.pop("scope", None)
        channel = candidate.get("channel")
        if channel is None:
            channel = "blocking"
        if channel not in VALID_CHANNELS:
            raise ValueError(
                f"Invalid channel: {candidate.get('channel')!r}. "
                f"Must be one of {VALID_CHANNELS}."
            )
        reviewed_files = self._bound_reviewed_files()
        if reviewed_files is not None and channel not in reviewed_files.channels:
            raise ValueError(
                f"channel {channel!r} is not among this reviewer's "
                f"channels {list(reviewed_files.channels)}"
            )
        if not partial:
            for field in ("severity_floor", "behavior_evidence", "source_cited"):
                if candidate.get(field) is None:
                    candidate.pop(field, None)
            if candidate.get("channel") != "advisory":
                candidate.pop("channel", None)
        return candidate

    def update_finding(self, finding_id: str, **fields) -> None:
        """Strictly patch one finding without changing its identity."""
        allowed = {
            "category",
            "severity",
            "title",
            "description",
            "file",
            "line",
            "recommendation",
            "confidence",
            "behavior_evidence",
            "source_cited",
            "severity_floor",
            "channel",
            "code_snippet",
            "references",
        }
        rejected = sorted(set(fields) - allowed)
        if rejected:
            raise ValueError(
                "update_finding cannot update field(s): "
                + ", ".join(rejected)
            )
        if not fields:
            raise ValueError("update_finding requires at least one field")
        index = self._entry_index(self.findings, finding_id, "finding")
        candidate = self._normalize_finding(
            {**self.findings[index], **fields}, partial=True
        )
        validate_finding_shape(candidate, index)
        self.findings[index] = candidate
        self._invocation_delta.append(f"updated finding {finding_id}")

    def remove_finding(self, finding_id: str) -> None:
        """Remove one finding without recycling its stable ID."""
        index = self._entry_index(self.findings, finding_id, "finding")
        self.findings.pop(index)
        self._invocation_delta.append(f"removed finding {finding_id}")

    def record_check(
        self,
        question: str,
        method: str,
        result: str,
        *,
        source_reviewers: Optional[List[str]] = None,
    ) -> str:
        """Record one check; ``source_reviewers`` defaults to this reviewer.

        One entry point for both producers: a reviewer recording its own
        verification work, and the reconciliator recording a check merged
        from several reviewers' — which names them all.
        """
        if source_reviewers is None:
            source_reviewers = [self.reviewer]
        values = [
            coerce_text(value).strip()
            for value in (question, method, result)
        ]
        if not all(values):
            raise ValueError(
                "record_check requires non-empty question, method, and result"
            )
        if (
            not isinstance(source_reviewers, list)
            or not source_reviewers
            or any(
                not isinstance(source, str) or not source.strip()
                for source in source_reviewers
            )
        ):
            raise ValueError(
                "record_check source_reviewers must be non-empty strings"
            )
        normalized_sources = list(
            dict.fromkeys(source.strip() for source in source_reviewers)
        )
        check_id = self._allocate_check_id()
        self.checks.append({
            "id": check_id,
            "question": values[0],
            "method": values[1],
            "result": values[2],
            "source_reviewers": normalized_sources,
        })
        self._invocation_delta.append(
            f"added check {check_id} "
            f"{json.dumps(values[0], ensure_ascii=False)}"
        )
        return check_id

    def update_check(self, check_id: str, **fields) -> None:
        """Strictly patch check content without changing identity or sources."""
        allowed = {"question", "method", "result"}
        rejected = sorted(set(fields) - allowed)
        if rejected:
            raise ValueError(
                "update_check cannot update field(s): "
                + ", ".join(rejected)
            )
        if not fields:
            raise ValueError("update_check requires at least one field")
        index = self._entry_index(self.checks, check_id, "check")
        candidate = dict(self.checks[index])
        candidate.update(
            (field, coerce_text(value).strip())
            for field, value in fields.items()
        )
        validate_check_shape(candidate, index)
        self.checks[index] = candidate
        self._invocation_delta.append(f"updated check {check_id}")

    def remove_check(self, check_id: str) -> None:
        """Remove one check without recycling its stable ID."""
        index = self._entry_index(self.checks, check_id, "check")
        self.checks.pop(index)
        self._invocation_delta.append(f"removed check {check_id}")

    def add_finding(
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
        """Add a finding and return its builder-generated stable ID.

        Line is required for point defects — the reviewer protocol mandates
        diff-anchored findings for anything that has a line. Findings that are
        line-less BY NATURE (missing test coverage, missing assertions,
        git-history precedent, cross-file architecture) may pass line=None:
        they are recorded as first-class FILE-SCOPED findings (line: null,
        scope: "file") that count toward the verdict, with a stderr NOTE so
        accidental line omission stays visible.
        Use add_observation() only for genuinely informational notes that
        should NOT count toward the verdict.
        When severity_floor is provided, lower severities are promoted to it.
        """
        if not 0.0 <= confidence <= 1.0:
            raise ValueError(f"Confidence must be 0.0-1.0, got {confidence}")
        if behavior_evidence is not None and behavior_evidence not in (
            "cited", "inferred",
        ):
            raise ValueError(
                f"Invalid behavior_evidence: {behavior_evidence!r}. "
                "Must be one of ('cited', 'inferred')."
            )

        finding = self._normalize_finding({
            'category': category,
            'severity': severity,
            'title': title,
            'description': description,
            'file': file,
            'line': line,
            'recommendation': recommendation,
            'confidence': confidence,
            'behavior_evidence': behavior_evidence,
            'source_cited': source_cited,
            'severity_floor': severity_floor,
            'channel': channel,
            **extra_fields
        }, partial=False)

        # Line is required for point defects — hard enforcement for invalid
        # values, and a stderr NOTE for the legitimately line-less finding so
        # an accidental omission stays visible. A file-scoped finding is a
        # real, verdict-counting finding, not a downgrade.
        if line is None:
            print(
                f"NOTE: recording '{finding['title']}' ({finding['severity']}) "
                f"as a FILE-SCOPED finding for '{file}' because line=None. "
                f"It counts toward the verdict. If this finding points at a "
                f"specific line, re-add it with line=<source line>.",
                file=sys.stderr,
            )
        elif not isinstance(line, int) or line <= 0:
            raise ValueError(
                f"line must be a positive integer, got {line}. "
                "Lines are 1-indexed."
            )
        elif line > 5000:
            # Agents reading a patch file sometimes cite the patch's own
            # display line numbers instead of the source file's.
            print(
                f"WARNING: line={line} for '{file}' is unusually large. "
                f"Verify this is a source file line number, not a patch file "
                f"display line number from the Read tool.",
                file=sys.stderr,
            )

        finding_id = self._allocate_finding_id()
        self.findings.append({'id': finding_id, **finding})
        self._invocation_delta.append(
            f"added finding {finding_id} "
            f"{json.dumps(finding['title'], ensure_ascii=False)}"
        )
        return finding_id

    def add_observation(self, file: str, note: str, category: str = "general"):
        """Add a file-level observation (not a finding).

        Observations are informational notes about files that don't have a
        specific line reference. They don't affect the verdict and are
        displayed separately from findings.
        """
        self.observations.append({
            "file": file,
            "note": note,
            "category": category,
        })

    def set_assessment(self, text):
        """Record the overall-state prose this artifact's verdict summarizes.

        Two or three sentences answering "what is the overall state of this
        code?" — the one judgment a list of findings cannot express, and
        the reason the reconciliation Markdown was hand-written before the
        pipeline took ownership of rendering it. Blank prose records
        absence rather than an empty string, so a consumer never has to
        distinguish "said nothing" from "said ''".
        """
        coerced = coerce_text(text).strip()
        self.assessment = coerced or None
        self._invocation_delta.append("updated assessment")

    def add_recommendation(self, priority: str, text: str):
        """Add recommendation (priority: immediate, important, suggestions)."""
        if priority in self.recommendations:
            self.recommendations[priority].append(coerce_text(text))

    def add_positive_observation(self, observation: str):
        """Add positive observation."""
        value = coerce_text(observation)
        self.positive_observations.append(value)
        self._invocation_delta.append(
            "added positive observation "
            + json.dumps(value, ensure_ascii=False)
        )

    @staticmethod
    def _resolve_plugin_version(output_dir: Optional[str]) -> Optional[str]:
        """Name the plugin that produced this artifact, or admit ignorance.

        Two paths to ONE fact, never a second detection of it — the version
        is detected once, at pipeline step 1, and travels from there:

        1. ``PIRATEGOAT_PLUGIN_VERSION`` in the builder envelope, which
           bootstrap fills from the run's ``run-config.json`` stamp. Always
           present in the envelope, sometimes empty (unresolvable run).
        2. That same stamp read from the bound output directory's
           ``run-config.json``, for a builder that has one and no envelope.

        Fails open to None everywhere — an unbound builder outside the
        envelope has no honest answer. An unstamped artifact is honest about
        not knowing; it is never an error and never a guess.
        """
        env_value = os.environ.get("PIRATEGOAT_PLUGIN_VERSION")
        if isinstance(env_value, str) and env_value.strip():
            return env_value.strip()
        if not output_dir:
            return None
        try:
            with open(
                os.path.join(output_dir, "run-config.json"), "r", encoding="utf-8"
            ) as f:
                config = json.load(f)
        except (OSError, UnicodeDecodeError, json.JSONDecodeError):
            return None
        stamped = config.get("plugin_version") if isinstance(config, dict) else None
        if isinstance(stamped, str) and stamped.strip():
            return stamped.strip()
        return None

    def _bound_reviewed_files(self):
        """Reviewed files derived from the bound assignment, or None.

        Add-time feedback only: save_draft() derives again, with the real
        claims, and fails closed. The facts this serves — ``channels`` and
        ``review_claimable_files`` — are claim-independent, so it derives
        with no claims: a claim outside the claimable set is save_draft()'s
        to reject, not a reason to go silent at add time.
        """
        if self._paths is None:
            return None
        try:
            with open(self._paths.assignment, "r", encoding="utf-8") as handle:
                data = json.load(handle)
            return derive_reviewed_files(data, [], reviewer=self.reviewer)
        except (OSError, UnicodeDecodeError, json.JSONDecodeError, ReviewAssignmentError):
            return None

    def _marker_name(self) -> Optional[str]:
        """The dispatch marker filename this actor's duration is measured from.

        The assignment is the one artifact that knows this builder's dispatch
        identity: `reviewer` is derive_reviewer_name(agent_name), and that
        derivation is not invertible — which is what the four-spelling probe
        this replaced was working around.
        """
        reviewed_files = self._bound_reviewed_files()
        if reviewed_files is None:
            return None
        return f"{reviewed_files.agent_name}{_REVIEWER_START_SUFFIX}"

    @staticmethod
    def _normalize_reviewed_file_claim(file: str) -> str:
        """Normalize one reviewed-file claim.

        Normalizes "./src/x.php", "src\\x.php", and "src//x.php" to one
        form, and rejects forms no scope path can ever take (absolute,
        traversal, drive-prefixed, dot-only) — an unmatched path is not a
        near miss, it is a claim about a file that does not exist in this
        review.
        """
        if not isinstance(file, str) or not file.strip():
            raise ValueError(
                "claim_files_reviewed requires a non-empty file path."
            )
        try:
            return normalize_review_path(file, "claim_files_reviewed")
        except ReviewAssignmentError as exc:
            raise ValueError(str(exc)) from exc

    @staticmethod
    def _reject_unknown_reviewed_file_claims(
        paths: List[str], known: frozenset
    ) -> None:
        """Reject claims outside the authoritative claimable set.

        Collect every offender so a review carrying several bad claims costs
        one correction round trip instead of one retry per path.
        """
        valid = (
            "Valid paths: " + ", ".join(sorted(known))
            if known
            else "This review has no review-claimable files, so no claim may be made."
        )
        offenders = ", ".join(repr(p) for p in paths)
        raise ValueError(
            f"claim_files_reviewed received {len(paths)} claim(s) matching no "
            f"review-claimable file of this review: {offenders}. {valid}"
        )

    def _validate_reviewed_file_claims(self, files) -> List[str]:
        """Normalize and membership-check one positive-claim batch.

        Both error classes collect across the whole batch — grammar
        failures as their own messages and membership offenders together —
        so one raise names every problem instead of surfacing them one retry
        at a time. Nothing is recorded until the whole batch passes.
        """
        if not files:
            raise ValueError(
                "claim_files_reviewed requires at least one file path — "
                "a call naming nothing is a no-op, not a claim."
            )
        reviewed_files = self._bound_reviewed_files()
        known = (
            frozenset(reviewed_files.review_claimable_files)
            if reviewed_files is not None else None
        )
        normalized: List[str] = []
        unknown: List[str] = []
        grammar_errors: List[str] = []
        for file in files:
            try:
                path = self._normalize_reviewed_file_claim(file)
            except ValueError as exc:
                grammar_errors.append(str(exc))
                continue
            normalized.append(path)
            if known is not None and path not in known:
                unknown.append(path)
        if grammar_errors or unknown:
            parts = list(grammar_errors)
            if unknown:
                try:
                    self._reject_unknown_reviewed_file_claims(unknown, known)
                except ValueError as exc:
                    parts.append(str(exc))
            raise ValueError("; ".join(parts))
        return normalized

    def _derive_reviewed_files(self, output_dir: str):
        """Return the reviewed files this publication must carry.

        Every draft save uses this path; the bound output directory makes the
        check independent of the optional environment envelope. A caller
        serializing manually via to_dict() knowingly opts out of
        that validation — publication is the enforcing seam.
        """
        assignment_path = review_paths(output_dir, self.reviewer).assignment
        try:
            with open(assignment_path, "r", encoding="utf-8") as handle:
                assignment = json.load(handle)
        except FileNotFoundError as exc:
            raise ValueError(
                "missing authoritative review assignment: "
                f"{assignment_path}"
            ) from exc
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ValueError(
                "malformed authoritative review assignment: "
                f"{assignment_path}"
            ) from exc
        try:
            return derive_reviewed_files(
                assignment, self.reviewed_file_claims, reviewer=self.reviewer
            )
        except ReviewAssignmentError as exc:
            raise ValueError(
                "malformed authoritative review assignment: "
                f"{exc}"
            ) from exc

    def claim_files_reviewed(self, *files: str):
        """Claim review-claimable files as reviewed, atomically."""
        normalized = self._validate_reviewed_file_claims(files)
        for path in normalized:
            if path not in self.reviewed_file_claims:
                self.reviewed_file_claims.append(path)
                self._invocation_delta.append(f"claimed file {path}")

    def retract_reviewed_file_claims(self, *files: str):
        """Retract existing reviewed-file claims, atomically."""
        if not files:
            raise ValueError(
                "retract_reviewed_file_claims requires at least one file path"
            )
        normalized: List[str] = []
        errors: List[str] = []
        for file in files:
            try:
                normalized.append(self._normalize_reviewed_file_claim(file))
            except ValueError as exc:
                errors.append(str(exc))
        if errors:
            raise ValueError("; ".join(errors))
        missing = [
            path for path in normalized if path not in self.reviewed_file_claims
        ]
        if missing:
            raise ValueError(
                "retract_reviewed_file_claims received paths that are not "
                "currently claimed: " + ", ".join(missing)
            )
        retracted = set(normalized)
        self.reviewed_file_claims = [
            path for path in self.reviewed_file_claims if path not in retracted
        ]
        self._invocation_delta.extend(
            f"retracted file {path}" for path in normalized
        )

    def set_confidence(self, score: float):
        """Set overall confidence score."""
        if not 0.0 <= score <= 1.0:
            raise ValueError(f"Confidence must be 0.0-1.0, got {score}")
        self.overall_confidence = score

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
        if self.findings:
            raise ValueError(
                "Cannot mark review as not_applicable — "
                f"{len(self.findings)} finding(s) already recorded. "
                "An agent that found findings reviewed the code; "
                "it should not also claim the changes are irrelevant."
            )
        self._not_applicable = True
        self._skip_reason = reason.strip()

    def _calculate_verdict(self) -> str:
        """Auto-calculate verdict from findings."""
        if self._not_applicable:
            return 'not_applicable'
        return derive_review_state(self.findings)['verdict']

    def to_dict(self) -> Dict:
        """Build the review content as a dictionary from this builder's own state.

        Carries content plus ``reviewer``. It has no authority over the six
        reviewed-file fields — ``save_draft`` stitches those on separately via
        ``reviewed_files_fields()``, from the one authoritative derivation.
        """
        review_duration = self._review_duration_ms()

        derived = summary_for(self.findings)
        verdict = (
            'not_applicable' if self._not_applicable else derived['verdict']
        )
        summary = derived['summary']
        if self._not_applicable:
            # `mark_not_applicable` only refuses to abstain once a finding
            # is ALREADY recorded; nothing stops a subsequent add_finding
            # call, so the summary cannot assume an empty finding list here.
            # An abstaining review makes no advisory-suppression claim
            # regardless of what was added afterward — the verdict does not
            # depend on it either way.
            summary = dict(summary)
            summary['suppressed_advisory_finding_count'] = 0
            summary.pop('verdict_without_advisory', None)

        result = {
            'pr_id': self.pr_id,
            'reviewer': self.reviewer,
            'timestamp': self.timestamp,
            'plugin_version': self._resolve_plugin_version(self._output_dir),
            'schema': REVIEW_OUTPUT_SCHEMA,
            'verdict': verdict,
            'summary': summary,
            'findings': self.findings,
            'observations': self.observations if self.observations else None,
            'recommendations': self.recommendations if any(self.recommendations.values()) else None,
            'positive_observations': self.positive_observations if self.positive_observations else None,
            'checks': list(self.checks),
            'assessment': self.assessment,
            'meta': {
                'review_duration_ms': review_duration,
                'confidence_score': self.overall_confidence,
                'next_finding_number': self.next_finding_number,
                'next_check_number': self.next_check_number,
            }
        }
        if self._skip_reason:
            result['skip_reason'] = self._skip_reason
        return result

    def _review_duration_ms(self) -> Optional[int]:
        """Milliseconds from this actor's dispatch to now, or None.

        Derived from the dispatch marker the pipeline wrote — the one clock
        that spans the actual review. A negative interval (marker stamped
        after this serialization, which no ordering produces) is discarded
        rather than published: a wrong number is worse than a missing one.
        """
        started = _actor_start_time(self._output_dir, self._marker_name())
        if started is None:
            return None
        if started.tzinfo is None:
            started = started.replace(tzinfo=timezone.utc)
        elapsed = (datetime.now(timezone.utc) - started).total_seconds()
        if elapsed < 0:
            return None
        return int(elapsed * 1000)

    def save_draft(self) -> dict[str, str]:
        """Validate and replace this builder's complete bound draft."""
        if self._output_dir is None or self._paths is None:
            raise ValueError(
                "save_draft requires ReviewOutputBuilder.open(...)"
            )
        reviewed_files = self._derive_reviewed_files(self._output_dir)
        off_channel = sorted({
            finding.get("channel") or "blocking" for finding in self.findings
        } - set(reviewed_files.channels))
        if off_channel:
            raise ValueError(
                f"findings use channel(s) {off_channel} not among this reviewer's "
                f"channels {list(reviewed_files.channels)}"
            )
        document = {**self.to_dict(), **reviewed_files_fields(reviewed_files)}
        draft_bytes = json.dumps(
            document, indent=2, ensure_ascii=False
        ).encode("utf-8")
        review = validate_review_document(document, self.reviewer)
        agent_name = reviewed_files.agent_name
        review_digest = hashlib.sha256(draft_bytes).hexdigest()

        with output_dir_lock(self._output_dir):
            require_review_intake_open(self._output_dir)
            require_not_finalized(self._paths)
            current_digest = _optional_file_digest(self._paths.draft)
            if current_digest != self._base_digest:
                raise ValueError("draft changed; reopen before saving")
            _atomic_replace_bytes(self._paths.draft, draft_bytes)
            try:
                _log_agent_review_draft_saved_telemetry(
                    self._output_dir, agent_name, review_digest
                )
            except Exception as exc:
                print(
                    "WARNING: draft saved, but agent_review_draft_saved "
                    f"telemetry failed: {exc}",
                    file=sys.stderr,
                )

        self._base_digest = review_digest
        self._last_saved_review = review
        return self._draft_receipt(review_digest, reviewed_files)

    def _draft_receipt(
        self, review_digest: str, reviewed_files
    ) -> dict[str, str]:
        """Print and return the compact next-action surface for one save."""
        review = self._last_saved_review
        summary = review["summary"]
        severity_parts = [
            f"{severity} {summary['by_severity'][severity]}"
            for severity in VALID_SEVERITIES
            if summary["by_severity"][severity]
        ]
        findings = f"findings {summary['total_findings']}"
        if severity_parts:
            findings += f" ({', '.join(severity_parts)})"
        totals = [findings]
        if review["checks"]:
            totals.append(f"checks {len(review['checks'])}")
        if review.get("observations"):
            totals.append(f"observations {len(review['observations'])}")

        command = finalize_review_command(
            os.path.abspath(__file__),
            self._output_dir,
            self.reviewer,
            review_digest,
        )
        print(f"DRAFT SAVED: verdict {review['verdict']}")
        print(f"DRAFT TOTALS: {' | '.join(totals)}")
        unclaimed = list(review["unclaimed_review_files"])
        if unclaimed:
            shown = ", ".join(unclaimed[:3])
            if len(unclaimed) > 3:
                shown += f" (+{len(unclaimed) - 3} more)"
            # A target of ~0 calls is not a target worth repeating.
            budget = reviewed_files.review_budget
            if budget:
                shown += f" | target ~{budget} tool calls"
            print(
                "FILES NOT YET CLAIMED AS REVIEWED "
                f"({len(unclaimed)}): {shown}"
            )
        if self._invocation_delta:
            print(f"CHANGED: {' | '.join(self._invocation_delta)}")
        print(f"FINALIZE REVIEW: {command}")
        self._invocation_delta = []
        return {
            "draft": self._paths.draft,
            "review_digest": review_digest,
            "finalize_review_command": command,
        }


def _read_json_object(path, label):
    try:
        with open(path, "r", encoding="utf-8") as handle:
            value = json.load(handle)
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"malformed {label}: {path}") from exc
    if not isinstance(value, dict):
        raise ValueError(f"malformed {label}: expected an object")
    return value


def reviewed_files_fields(reviewed_files) -> Dict:
    """The six reviewer-envelope reviewed-file fields, from one derivation.

    ``save_draft`` stitches these onto ``to_dict()``'s content to build the
    complete draft document; nothing else may assemble them piecemeal.
    """
    return {
        "review_claimable_files": list(reviewed_files.review_claimable_files),
        "reviewed_file_claims": list(reviewed_files.reviewed_file_claims),
        "unclaimed_review_files": list(reviewed_files.unclaimed_review_files),
        "inline_diff_file_count": reviewed_files.inline_diff_file_count,
        "reviewed_file_count": reviewed_files.reviewed_file_count,
        "in_scope_review_file_count": reviewed_files.in_scope_review_file_count,
    }


def _validate_review(output_dir, reviewer, paths, review_bytes):
    """Validate one exact review snapshot and return telemetry facts."""
    try:
        review = json.loads(review_bytes)
    except (UnicodeDecodeError, json.JSONDecodeError, TypeError) as exc:
        raise ValueError("malformed review JSON") from exc
    validate_review_document(review, reviewer)

    assignment = _read_json_object(
        paths.assignment, "review assignment"
    )
    try:
        reviewed_files = derive_reviewed_files(
            assignment, review["reviewed_file_claims"], reviewer=reviewer
        )
    except ReviewAssignmentError as exc:
        raise ValueError(
            f"reviewed-file derivation is malformed: {exc}"
        ) from exc
    derived = reviewed_files_fields(reviewed_files)
    if {key: review[key] for key in derived} != derived:
        raise ValueError(
            "review derived reviewed-file fields do not match the assignment"
        )
    return review, reviewed_files.agent_name


def finalize_review(output_dir: str, reviewer: str, review_digest: str):
    """Validate and atomically promote exactly one observed draft.

    Publishing is the only event: a retry with the same digest re-validates
    the final it already published, clears any stray draft, and returns the
    same result. Completion telemetry is logged by the promotion itself, so
    a retry cannot double-log it, and status and the manifest read the final
    file rather than the event.
    """
    if (
        not isinstance(review_digest, str)
        or len(review_digest) != 64
        or any(ch not in "0123456789abcdef" for ch in review_digest)
    ):
        raise ValueError("review digest must be a lowercase SHA-256")
    paths = review_paths(output_dir, reviewer)
    with output_dir_lock(output_dir):
        require_review_intake_open(output_dir)
        if os.path.exists(paths.final):
            final_bytes = Path(paths.final).read_bytes()
            if hashlib.sha256(final_bytes).hexdigest() != review_digest:
                raise ValueError(
                    "review digest conflicts with the finalized review"
                )
            _validate_review(output_dir, reviewer, paths, final_bytes)
            try:
                os.unlink(paths.draft)
            except FileNotFoundError:
                pass
        else:
            try:
                draft_bytes = Path(paths.draft).read_bytes()
            except OSError as exc:
                raise ValueError("review draft is absent") from exc
            if hashlib.sha256(draft_bytes).hexdigest() != review_digest:
                raise ValueError(
                    "review digest no longer matches the saved draft"
                )
            review, agent_name = _validate_review(
                output_dir, reviewer, paths, draft_bytes
            )
            os.replace(paths.draft, paths.final)
            _log_agent_complete_telemetry(
                output_dir,
                agent_name,
                review["verdict"],
                review["summary"]["total_findings"],
                review["summary"]["by_severity"],
                review_digest,
            )
    return {"final": paths.final, "review_digest": review_digest}


if __name__ == '__main__':
    import argparse

    parser = argparse.ArgumentParser(
        description="Publish one canonical review from its saved draft.",
    )
    sub = parser.add_subparsers(dest="command", required=True)
    finalize_cmd = sub.add_parser(
        "finalize-review", help="Validate and publish one review draft"
    )
    finalize_cmd.add_argument("--output-dir", required=True)
    finalize_cmd.add_argument("--reviewer", required=True)
    finalize_cmd.add_argument("--review-digest", required=True)
    cli_args = parser.parse_args()
    try:
        finalized = finalize_review(
            cli_args.output_dir,
            cli_args.reviewer,
            cli_args.review_digest,
        )
    except (OSError, ValueError) as exc:
        print(f"REJECTED: {exc}", file=sys.stderr)
        raise SystemExit(1)
    print(
        "REVIEW FINALIZED: "
        f"{os.path.basename(finalized['final'])}"
    )
