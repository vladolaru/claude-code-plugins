#!/usr/bin/env python3
"""Capture sanitized run directories for replay through finalize projections.

Capture from an existing local run; never hand-edit generated fixtures::

    python3 plugins/pirategoat-tools/tests/helpers/review_run_fixture.py \
        --run-dir <run-dir> --name e582-woocommerce-53136

Fixture format 1 retains historical missingness and canonical document
envelopes, with prose redacted. The three old proposals can contain severity
changes named ``correct``. Only final-ledger proof permits translating those
proposal actions to today's promote/demote vocabulary. The final ledger keeps
its historical action; the canonical critic writer binds the redacted proposal.
"""

import argparse
import copy
import json
import re
import sys
from pathlib import Path

TESTS_DIR = Path(__file__).resolve().parent.parent
FIXTURES_DIR = TESTS_DIR / "fixtures" / "review-runs"
SCRIPTS_DIR = TESTS_DIR.parent / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from review.critic_adjustments import (  # noqa: E402
    _validate_verdict_marker, proposal_digest,
    validate_adjustments_document, validate_findings_document,
    verdict_admits_proposal, write_critic_verdict,
)
from review.dispatch_status import DISPATCHED_STATUSES, validate_dispatch_plan_agents  # noqa: E402
from review.review_document import (  # noqa: E402
    REQUIRED_FINDING_FIELDS, REVIEW_CONTENT_FIELDS, REVIEWER_FIELDS,
    validate_review_document,
)
from review.reviewer_lifecycle import review_paths  # noqa: E402
from review.reviewer_names import derive_reviewer_name  # noqa: E402
from review.run_paths import artifact_path  # noqa: E402
from review.verdict_rules import SEVERITY_RANK  # noqa: E402

FIXTURE_SCHEMA = 1
FIXTURE_NAMES = (
    "e582-woocommerce-53136",
    "6e6a-woocommerce-35520",
    "3725-woocommerce-payments-12089",
)
_CONTEXT_FIELDS = frozenset({
    "git", "pr_size", "mode", "source", "changed_files_count", "commit_count",
    "host_context",
})
_GIT_FIELDS = frozenset({
    "git_range", "merge_base", "head_sha", "base_ref", "head_ref", "base_fetch",
    "scope_check", "changed_files", "commit_count",
})
_PROSE_KEYS = frozenset({
    "title", "description", "recommendation", "question", "method", "evidence",
    "rationale", "note", "text", "assessment", "summary", "positive_observations",
    "observations", "code_snippet", "references", "skip_reason", "result",
    # Plans contain free-form orchestrator explanations and matched prose too.
    "focus", "reason", "override_reason", "agent_signals", "warnings",
})
_FINDING_FIELDS = REQUIRED_FINDING_FIELDS | frozenset({
    "source_cited", "verifies", "source_reviewers", "result",
})
_LOCAL_PATH_RE = re.compile(r"(/Users/|/home/|/private/|[A-Za-z]:\\)[^\s\"']*")
_SESSION_URL_RE = re.compile(r"(Claude-Session:\s+)\S+")
_PURPOSE_ITEM_RE = re.compile(r"^(\s*(?:[-*]\s+)?[VC]\d+\.)\s*(.*)$")


def _recorded_output_dirs(value):
    """Collect identities before filtering any source document's fields."""
    if isinstance(value, dict):
        for name, item in value.items():
            if name == "output_dir" and isinstance(item, str) and item:
                yield item
            yield from _recorded_output_dirs(item)
    elif isinstance(value, list):
        for item in value:
            yield from _recorded_output_dirs(item)


def _redact(value, key=None, *, output_dirs=()):
    """Recursively remove private prose and local identity without adding data."""
    if key == "summary" and isinstance(value, dict):
        pass  # A ledger/reviewer summary is counts, not prose.
    elif key == "recommendations" and isinstance(value, dict):
        return {name: [] for name in value}
    elif key in _PROSE_KEYS:
        return [] if isinstance(value, list) else "<redacted>"
    elif key == "fields" and isinstance(value, dict):
        return {
            name: "<redacted>" if isinstance(item, str) and name not in {"severity", "file"}
            else _redact(item, name, output_dirs=output_dirs)
            for name, item in value.items()
        }
    if isinstance(value, dict):
        return {name: _redact(item, name, output_dirs=output_dirs) for name, item in value.items()}
    if isinstance(value, list):
        return [_redact(item, key, output_dirs=output_dirs) for item in value]
    if isinstance(value, str):
        if key == "output_dir":
            return "<redacted>"
        if key == "source_cited":
            host, separator, _rest = value.partition(":")
            value = f"{host}:<redacted>" if separator else "<redacted>"
        for output_dir in output_dirs:
            value = value.replace(output_dir, "<redacted>")
        value = _SESSION_URL_RE.sub(r"\1<redacted>", value)
        return _LOCAL_PATH_RE.sub("<redacted>", value)
    return value


def _normalize_legacy_proposal(proposal, ledger):
    """Translate a severity-changing `correct` into today's `promote`/`demote`,
    in the proposal and in the ledger's provenance for the same finding.

    Before 1.119.0 a `correct` could carry `severity`; today's validator
    refuses that, and the meaning is exactly a promote or demote. The
    translation is proven from the final ledger (its prior and final
    severity) and applied to both documents so a fixture never counts an
    adjustment as `demote` while its finding still says `correct`. The audits
    counted under the old vocabulary; the replay tests state the translation.
    """
    normalized = copy.deepcopy(proposal)
    for entry in normalized.get("adjustments", []):
        if entry.get("action") != "correct" or "severity" not in entry.get("fields", {}):
            continue
        target = entry.get("target", {})
        matches = [finding for finding in ledger.get("findings", []) if finding.get("id") == target.get("id")]
        finding = matches[0] if len(matches) == 1 else {}
        adjustment = finding.get("critic_adjustment") or {}
        prior_fields = adjustment.get("prior")
        prior = prior_fields.get("severity") if isinstance(prior_fields, dict) else None
        final = finding.get("severity")
        if (
            target.get("kind") != "finding"
            or not target.get("id")
            or len(matches) != 1
            or adjustment.get("action") != "correct"
            or not isinstance(prior, str) or prior not in SEVERITY_RANK
            or not isinstance(final, str) or final not in SEVERITY_RANK
            or final != entry["fields"]["severity"]
            or prior == final
        ):
            raise ValueError("cannot prove legacy severity adjustment from final ledger")
        entry["action"] = "promote" if SEVERITY_RANK[final] > SEVERITY_RANK[prior] else "demote"
        adjustment["action"] = entry["action"]
    return normalized


def _redact_change_purpose(text):
    """Keep the section headings (the template's structure; a purpose without
    `## Verify` must still parse as unstructured), declared ids, source
    delimiters and carried-over markers. The H1 carries the PR title and is
    redacted like any prose."""
    lines = []
    for line in text.splitlines():
        heading = re.match(r"^(#{1,6})\s+(.*)$", line)
        item = _PURPOSE_ITEM_RE.match(line)
        if heading:
            title = heading[2] if len(heading[1]) > 1 else "<redacted>"
            lines.append(f"{heading[1]} {title}")
        elif item:
            carried = " (carried over)" if "(carried over)" in item[2] else ""
            source = " — source: <redacted>" if "— source:" in item[2] else ""
            lines.append(f"{item[1]} <redacted>{carried}{source}")
        elif line.strip() == "None.":
            lines.append("None.")
        else:
            lines.append("<redacted>" if line.strip() else "")
    return "\n".join(lines) + "\n"


def fixture_dir(name: str) -> Path:
    if name not in FIXTURE_NAMES:
        raise ValueError("unknown fixture name")
    return FIXTURES_DIR / name


def _read_text(path):
    path = Path(path)
    try:
        return path.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as exc:
        raise ValueError(f"cannot read required artifact {path.name}") from exc


def _read_object(path):
    path = Path(path)
    try:
        value = json.loads(_read_text(path))
    except json.JSONDecodeError as exc:
        raise ValueError(f"invalid JSON in required artifact {path.name}") from exc
    if not isinstance(value, dict):
        raise ValueError(f"required artifact {path.name} must be an object")
    return value


def capture(run_dir: Path, name: str) -> Path:
    """Validate and sanitize all inputs before writing exactly the replay files."""
    target = fixture_dir(name)
    # Check the fixture root, target, and every existing descendant, including
    # dangling links and output-directory components, before any write occurs.
    if FIXTURES_DIR.is_symlink() or target.is_symlink() or any(path.is_symlink() for path in target.rglob("*")):
        raise ValueError("fixture destination contains a symlink; refusing to overwrite")
    run_dir = Path(run_dir).expanduser().resolve()
    if target.resolve() == run_dir or target.resolve().is_relative_to(run_dir):
        raise ValueError("fixture destination must be outside the read-only source run")
    keys = ("review_context", "dispatch_plan_initial", "dispatch_plan", "usage_snapshot", "review_findings_json")
    documents = {key: _read_object(artifact_path(run_dir, key)) for key in keys}
    context = documents["review_context"]
    if not isinstance(context.get("git"), dict):
        raise ValueError("review context must contain a git object")
    agents = validate_dispatch_plan_agents(documents["dispatch_plan"].get("agents"))
    validate_dispatch_plan_agents(documents["dispatch_plan_initial"].get("agents"))
    reviewers = {
        derive_reviewer_name(agent["name"]): _read_object(review_paths(run_dir, derive_reviewer_name(agent["name"])).final)
        for agent in agents if agent["status"] in DISPATCHED_STATUSES
    }
    proposal = _read_object(artifact_path(run_dir, "critic_adjustments"))
    marker = _read_object(artifact_path(run_dir, "critic_verdict"))
    # The rule write_critic_verdict commits by, so a recorded STAND that
    # carries wording corrections is a fixture source like any other run.
    if _validate_verdict_marker(marker) or verdict_admits_proposal(
        marker.get("verdict"), proposal, strict=False
    ):
        raise ValueError("source critic marker failed validation")
    if marker.get("proposal_digest") != proposal_digest(proposal):
        raise ValueError("source critic verdict does not bind its proposal")
    output_dirs = {str(run_dir)}
    for document in (*documents.values(), *reviewers.values(), proposal, marker):
        output_dirs.update(_recorded_output_dirs(document))
    # Longest first keeps a recorded child directory from being only partly
    # removed when another document records one of its parents.
    output_dirs = tuple(sorted(output_dirs, key=lambda value: (-len(value), value)))
    documents["review_context"] = {key: value for key, value in context.items() if key in _CONTEXT_FIELDS}
    documents["review_context"]["git"] = {key: value for key, value in context["git"].items() if key in _GIT_FIELDS}
    purpose = _redact_change_purpose(_read_text(artifact_path(run_dir, "change_purpose")))
    proposal = _normalize_legacy_proposal(proposal, documents["review_findings_json"])
    proposal = _redact(proposal, output_dirs=output_dirs)
    problems = validate_adjustments_document(proposal)
    if problems:
        raise ValueError("redacted critic proposal failed validation: " + "; ".join(problems))
    documents = {key: _redact(value, output_dirs=output_dirs) for key, value in documents.items()}
    validate_findings_document(documents["review_findings_json"])
    outputs = {artifact_path(target, key): value for key, value in documents.items()}
    for reviewer, document in reviewers.items():
        document = {key: value for key, value in document.items() if key in REVIEW_CONTENT_FIELDS | REVIEWER_FIELDS | {"skip_reason"}}
        document["findings"] = [{key: value for key, value in finding.items() if key in _FINDING_FIELDS} for finding in document["findings"]]
        document = _redact(document, output_dirs=output_dirs)
        validate_review_document(document, reviewer)
        outputs[Path(review_paths(target, reviewer).final)] = document
    expected = set(outputs) | {artifact_path(target, key) for key in ("change_purpose", "critic_adjustments", "critic_verdict")}
    if any(path.is_file() and path not in expected for path in target.rglob("*")):
        raise ValueError("fixture destination contains unexpected files; refusing to overwrite")
    for path, document in outputs.items():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(document, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    artifact_path(target, "change_purpose").write_text(purpose, encoding="utf-8")
    write_critic_verdict(target, marker.get("verdict"), proposal)
    return target


def main():
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--run-dir", required=True, type=Path)
    parser.add_argument("--name", required=True, choices=FIXTURE_NAMES)
    args = parser.parse_args()
    try:
        target = capture(args.run_dir, args.name)
    except (OSError, ValueError) as exc:
        parser.exit(1, f"capture failed: {exc}\n")
    files = [path for path in target.rglob("*") if path.is_file()]
    print(f"wrote {args.name} (fixture schema {FIXTURE_SCHEMA}, {len(files)} files, {sum(path.stat().st_size for path in files)} bytes)")


if __name__ == "__main__":
    main()
