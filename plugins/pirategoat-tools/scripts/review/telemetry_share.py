#!/usr/bin/env python3
"""Machine-local telemetry sharing consent and repository identity.

Findings, review documents, code excerpts, and diffs never leave the machine.
This module is the one canonical derivation of a repository identity, so
callers must provide a repository path rather than compute an identity.
"""

import argparse
import base64
import copy
import json
import re
import subprocess
import sys
from urllib.parse import urlparse

try:
    from .atomic_io import atomic_write_json, output_dir_lock
    from .run_paths import (
        SAFE_RUN_ID_SEGMENT_RE,
        telemetry_log_path,
        telemetry_manifest_path,
    )
    from .user_settings import (
        REPO_CHOICES,
        SHARING_CHOICES,
        load_user_settings,
        telemetry_settings,
        user_config_path,
    )
except ImportError:  # Direct ``python telemetry_share.py`` invocation.
    from atomic_io import atomic_write_json, output_dir_lock  # type: ignore[no-redef]
    from run_paths import (  # type: ignore[no-redef]
        SAFE_RUN_ID_SEGMENT_RE,
        telemetry_log_path,
        telemetry_manifest_path,
    )
    from user_settings import (  # type: ignore[no-redef]
        REPO_CHOICES,
        SHARING_CHOICES,
        load_user_settings,
        telemetry_settings,
        user_config_path,
    )


REMOTE_REPO = "vladolaru/pirategoat-tools-review-telemetry"
# The sink lives on GitHub.com. Every request pins this host explicitly
# because ``gh`` otherwise honors ``GH_HOST`` — routinely exported while
# working against a GitHub Enterprise instance — and would then resolve the
# login and the repository path on that host instead of the disclosed one.
GH_HOSTNAME = "github.com"
LAYOUT_PREFIX = "v1"
# The two outcomes that mean the requester never opted in globally. A caller
# that reports progress stays silent for these — a run that has not been
# offered sharing, or has declined it, should not narrate that on every
# review — so the vocabulary lives here rather than being re-derived from
# settings at the call site.
SHARING_DISABLED_OUTCOME = "skipped: sharing disabled"
CONSENT_UNSET_OUTCOME = "skipped: consent unset"
UNOPTED_OUTCOMES = frozenset({SHARING_DISABLED_OUTCOME, CONSENT_UNSET_OUTCOME})

GH_TIMEOUT_SECONDS = 30
_GIT_TIMEOUT_SECONDS = 10
_SSH_REMOTE = re.compile(r"^(?:[^@/:]+@)?([^/:]+):(.+)$")
_COLLABORATOR_ACCESS_HINT = "ask Vlad for collaborator access"


class _UploadFailure(Exception):
    """A short, safe reason an external `gh` request could not finish."""


# The step-12 consent prompt's disclosure. It lives beside the redaction
# rules it describes so the two are edited together; briefings.py renders it.
CONSENT_DISCLOSURE = (
    "Before asking, explain that shared run metadata includes repo names, "
    "the review target (the PR number or branch, which identifies the "
    "exact PR to collaborators), the reviewed commit range and SHAs, "
    "the run id, the plugin version, pipeline step timings, skips, and "
    "status flags, per-agent "
    "dispatch and outcome data (which agents ran or were skipped, whether "
    "reviewers, the reconciliator, or the decision critic, and each one's "
    "domain, model tier, registry-configured triage checks, tool budget, "
    "status, verdict, finding-severity counts, and content hashes of its "
    "review document), repo-relative changed-file "
    "paths and which agents each file was assigned to, worktree-hygiene "
    "and dependency-refresh status, and token usage by model; never file "
    "contents, diffs, finding text, local workspace paths or filenames "
    "outside the reviewed change, PR titles or authors, session ids, or "
    "triage reasoning."
)


# Fields the step-12 consent prompt never disclosed: PR metadata naming third
# parties (a title, author, or linked issue is theirs, not the uploader's to
# consent for) and triage reasoning whose template text echoes keyword
# matches against undisclosed PR title/body — repo-contributed reviewers can
# even inject custom vocabulary into it. Every key here is unambiguous across
# the whole payload; the bare ``reason`` key is not (enumerated exclusion and
# skip codes reuse it), so dispatch-snapshot reasons are stripped
# location-aware by ``_SCOPED_STRIPS``. Branch refs go too: the
# disclosed review target already names the branch in branch mode, and in
# PR mode the PR number identifies the change without its branch name,
# which often carries an issue id or a description. Undisclosed fields whose
# SLOT the shared reader's schema requires are not stripped but rewritten in
# place: see ``_REDACTED_VALUES``.
_UNDISCLOSED_KEYS = frozenset({
    "pr_title",
    "pr_author",
    "pr_url",
    "linked_issues",
    "base_ref",
    "head_ref",
    "initial_reason",
    "final_reason",
    "adjustment_reason",
    "planner_signals",
})


# Keys whose bare name is a DISCLOSED value somewhere else in the payload,
# so they can only be stripped where they are undisclosed. Each row locates
# nodes by path ("*" matches every value of a dict) and strips its keys
# from them; ``recursive`` says whether nested occurrences under that node
# go too. One table, one walker — a new scoped strip is a row here, never a
# fifth mechanism.
#
# Why each key needs a location rather than a global strip:
#   new_files/changed_files/probe_residue_removed, commands, dirty_files
#       Local workspace state the step-3 precheck and step-11 sweep record:
#       the orchestrator's command text and the names of dirty, untracked,
#       or probe-residue files. Those describe the requester's machine, not
#       the reviewed change — a private scratch file is not a "changed file"
#       of the review — so only each section's status and flags upload.
#       Meanwhile ``assignment.changed_files`` IS the disclosed reviewed
#       diff. Recursive: ``dependency_refresh.precheck`` repeats
#       ``dirty_files`` one level down.
#   reason
#       Undisclosed triage text on dispatch decisions and step decisions,
#       whose template echoes keyword matches against the undisclosed PR
#       title and body. But ``assignment.file_exclusions[].reason`` is a
#       disclosed enumerated code ("noise_filtered"), and skip codes reuse
#       the name, so a global strip would delete disclosed values.
#   files
#       ``snapshot.files`` is the run directory's own listing — whatever the
#       requester left in the run root, which no reader consumes. Exact, not
#       recursive: ``scope.files`` is a disclosed count, and a future
#       snapshot section carrying one must not be silently emptied.
_SCOPED_STRIPS = (
    (
        ("worktree_hygiene",),
        frozenset({"new_files", "changed_files", "probe_residue_removed"}),
        True,
    ),
    (("dependency_refresh",), frozenset({"commands", "dirty_files"}), True),
    (("decisions",), frozenset({"reason"}), False),
    (("snapshot",), frozenset({"files"}), False),
    (("snapshot", "dispatch", "agents", "*"), frozenset({"reason"}), False),
)


# Undisclosed fields the shared reader's schema requires a slot for, so they
# are rewritten in place rather than stripped. ``run.session_id`` is a
# required nullable key: dropping it makes the reader reject the manifest and
# fall back to its reduced legacy JSONL reading. A not-applicable reviewer's
# ``skip_reason`` is model-authored text; the reader requires a non-empty
# string there, so a constant keeps the roster and counts measurable. The
# run's ``output_dir`` is a local directory: its basename is the run id in
# the durable layout and a path-encoding name in older ones, so it carries
# nothing the disclosed run id does not, and is nulled.
_REDACTED_VALUES = {
    "session_id": None,
    "output_dir": None,
    "skip_reason": "redacted",
}


def _redact_tree(payload: object, strip: frozenset, rewrite: dict | None = None) -> None:
    """Walk ``payload`` once: delete fields in ``strip``, replace fields in ``rewrite``."""
    if rewrite is None:
        rewrite = {}
    if isinstance(payload, dict):
        for key in list(payload):
            if key in strip:
                del payload[key]
            elif key in rewrite:
                payload[key] = rewrite[key]
            else:
                _redact_tree(payload[key], strip, rewrite)
    elif isinstance(payload, list):
        for value in payload:
            _redact_tree(value, strip, rewrite)


def _descend(payload: object, path: tuple):
    """Yield every node ``path`` locates; ``"*"`` matches every value of a dict."""
    if not path:
        yield payload
        return
    if not isinstance(payload, dict):
        return
    head, rest = path[0], path[1:]
    values = payload.values() if head == "*" else (
        [payload[head]] if head in payload else []
    )
    for value in values:
        yield from _descend(value, rest)


def _strip_scoped(payload: object) -> None:
    """Apply every ``_SCOPED_STRIPS`` row to whichever nodes ``payload`` has."""
    for path, keys, recursive in _SCOPED_STRIPS:
        for node in _descend(payload, path):
            if not isinstance(node, dict):
                continue
            if recursive:
                _redact_tree(node, keys)
            else:
                for key in keys:
                    node.pop(key, None)


def redact_payloads(manifest: dict, jsonl_lines: list[str]) -> tuple[dict, list[str]]:
    """Create share-safe telemetry payloads without mutating their local inputs.

    ``repo_path`` is rewritten to the durable identity, schema-required
    undisclosed slots are rewritten in place (``_REDACTED_VALUES``: session
    id and output directory nulled, skip reasons replaced), undisclosed
    metadata is removed (``_UNDISCLOSED_KEYS`` everywhere it appears, plus
    the ``_SCOPED_STRIPS`` rows for keys that are disclosed under one path
    and undisclosed under another), and everything else uploads as
    recorded.
    ``run.repo`` is the durable non-path identity needed to redact
    correctly; without it, refusing to produce a payload is safer than
    guessing.

    What survives is, by audit of a full real run: pipeline enumerations and
    constants, identifiers the step-12 prompt discloses (repository, target
    branch or PR, commit range and SHAs, run id, agent names, model names),
    review-document content hashes, and repo-relative paths of the reviewed
    change. ``tests/review/test_telemetry_share.py`` pins the string-bearing
    key paths so a new producer field is a deliberate disclose-or-strip
    decision, never a silent upload.
    """
    if not isinstance(manifest, dict):
        raise ValueError("manifest is invalid")
    redacted_manifest = copy.deepcopy(manifest)
    run = redacted_manifest.get("run")
    if not isinstance(run, dict):
        raise ValueError("manifest run.repo is missing")
    repo = run.get("repo")
    if not isinstance(repo, str) or not repo:
        raise ValueError("manifest run.repo is missing")

    if "repo_path" in run:
        run["repo_path"] = repo
    _redact_tree(redacted_manifest, _UNDISCLOSED_KEYS, _REDACTED_VALUES)
    _strip_scoped(redacted_manifest)
    _assert_share_safe(redacted_manifest)

    redacted_lines = []
    for line in jsonl_lines:
        try:
            event = json.loads(line)
        except (TypeError, json.JSONDecodeError) as error:
            raise ValueError("telemetry JSONL is invalid") from error
        if not isinstance(event, dict):
            raise ValueError("telemetry JSONL is invalid")

        pipeline = event.get("pipeline")
        if event.get("event") == "pipeline_start" and isinstance(pipeline, dict):
            if "repo_path" in pipeline:
                pipeline["repo_path"] = repo
        _redact_tree(event, _UNDISCLOSED_KEYS, _REDACTED_VALUES)
        _strip_scoped(event)
        _assert_share_safe(event)
        # Text-mode reads translate every line ending to "\n"; only a final
        # line with no terminator varies.
        ending = "\n" if line.endswith("\n") else ""
        redacted_lines.append(json.dumps(event, ensure_ascii=False) + ending)

    return redacted_manifest, redacted_lines


# Detection is structural — where a path STARTS — never by substring: a
# repo-relative ``src/home/index.py`` or ``private/config.php`` is a
# disclosed reviewed path, and a marker such as ``/home/`` matched anywhere
# would refuse the whole upload for it.
_DRIVE_PATH = re.compile(r"^[A-Za-z]:[\\/]")
_EMBEDDED_DRIVE_PATH = re.compile(r"(?:^|[^0-9A-Za-z])[A-Za-z]:[\\/]")
_UNC_PATH = re.compile(r"\\\\[0-9A-Za-z]")
# An absolute POSIX path embedded mid-string after a delimiter (cwd=/tmp/run,
# "at /opt/tool"). The delimiter class excludes ":" — colon-delimited paths
# are _COLON_POSIX_PATH's job, with its URL-scheme exemption.
_EMBEDDED_POSIX_PATH = re.compile(r"""[\s"'=(\[,]/[^\s/]""")
# An absolute POSIX path formatted right after a colon (cwd:/tmp/run,
# path:/opt/tool). The "//" lookahead exempts URL schemes ("https://...").
_COLON_POSIX_PATH = re.compile(r":/(?!/)[^\s/]")
_FILE_URL = re.compile(
    r"(?<![0-9A-Za-z+.-])file:(?:/+|[A-Za-z]:[\\/])", re.IGNORECASE
)


def _looks_like_local_path(value: str) -> bool:
    return (
        _FILE_URL.search(value) is not None
        or value.startswith("/")
        or _EMBEDDED_DRIVE_PATH.search(value) is not None
        or _UNC_PATH.search(value) is not None
        or _EMBEDDED_POSIX_PATH.search(value) is not None
        or _COLON_POSIX_PATH.search(value) is not None
    )


def _assert_share_safe(payload: object) -> None:
    """Refuse any payload where an absolute local path survived redaction.

    The redaction contract is "no local paths leave the machine", not "these
    enumerated fields are rewritten" — this guard enforces the contract
    itself, so a future manifest field carrying a path (POSIX, Windows
    drive, or UNC; leading or embedded) fails the upload closed instead of
    shipping.
    """
    if isinstance(payload, str):
        if _looks_like_local_path(payload):
            raise ValueError("share-unsafe path survived redaction")
    elif isinstance(payload, dict):
        for key, value in payload.items():
            _assert_share_safe(key)
            _assert_share_safe(value)
    elif isinstance(payload, (list, tuple)):
        for value in payload:
            _assert_share_safe(value)


def _run_gh(arguments: list[str], body: str | None = None) -> subprocess.CompletedProcess:
    """Run one ``gh api`` request against GitHub.com, fixed timeout, no retry.

    ``body`` is the JSON request body, handed to gh on stdin. It never goes
    through argv: Linux caps a single argument at 128 KiB (MAX_ARG_STRLEN),
    which a base64 payload exceeds for any artifact above roughly 96 KiB,
    and real review manifests reach several hundred KiB.
    """
    try:
        return subprocess.run(
            ["gh", "api", "--hostname", GH_HOSTNAME, *arguments],
            input=body,
            capture_output=True,
            text=True,
            timeout=GH_TIMEOUT_SECONDS,
            check=False,
        )
    except subprocess.TimeoutExpired as error:
        raise _UploadFailure("gh timed out") from error
    except OSError as error:
        # Covers a missing gh (FileNotFoundError) and every other exec failure.
        raise _UploadFailure("gh unavailable") from error


def _gh_failure(result: subprocess.CompletedProcess) -> _UploadFailure:
    """The one safe failure for a non-zero gh exit: never gh's own output."""
    return _UploadFailure(f"gh exited {result.returncode}; {_COLLABORATOR_ACCESS_HINT}")


def _successful_gh(
    arguments: list[str], body: str | None = None
) -> subprocess.CompletedProcess:
    """Return a successful GitHub CLI response or a safe failure reason."""
    result = _run_gh(arguments, body)
    if result.returncode != 0:
        raise _gh_failure(result)
    return result


# A GitHub login: alphanumerics and single hyphens, not starting with one.
# Enforced because the login becomes a path segment of the upload.
_GITHUB_LOGIN = re.compile(r"[A-Za-z0-9](?:-?[A-Za-z0-9])*")


def _login() -> str:
    """Read the authenticated login once, as the raw text ``--jq .login`` prints.

    The output is never JSON-decoded: logins such as ``123``, ``true``, or
    ``null`` are valid GitHub usernames and would otherwise decode into
    non-strings and be refused.
    """
    response = _successful_gh(["user", "--jq", ".login"])
    value = response.stdout.strip()
    if not _GITHUB_LOGIN.fullmatch(value):
        raise _UploadFailure("invalid login response")
    return value


def _content_sha(path: str) -> str | None:
    """Return an existing Contents-API blob SHA, or None for a normal 404."""
    response = _run_gh([f"repos/{REMOTE_REPO}/contents/{path}"])
    if response.returncode != 0:
        response_text = f"{response.stdout}\n{response.stderr}"
        if "404" in response_text:
            return None
        raise _gh_failure(response)
    try:
        payload = json.loads(response.stdout)
    except json.JSONDecodeError as error:
        raise _UploadFailure("invalid contents response") from error
    if not isinstance(payload, dict):
        raise _UploadFailure("invalid contents response")
    sha = payload.get("sha")
    if not isinstance(sha, str) or not sha:
        raise _UploadFailure("invalid contents response")
    return sha


def _put_content(path: str, payload: bytes, sha: str | None) -> None:
    """Create or update one explicit telemetry payload in the shared repository."""
    body = {
        "message": f"Share telemetry run {path.rsplit('/', 1)[-1]}",
        "content": base64.b64encode(payload).decode("ascii"),
    }
    if sha:
        body["sha"] = sha
    _successful_gh(
        ["-X", "PUT", f"repos/{REMOTE_REPO}/contents/{path}", "--input", "-"],
        json.dumps(body),
    )


def _log_path(output_dir: str) -> str:
    """The run's telemetry log path, empty when missing OR unreadable.

    The sharing module's failure policy over ``run_paths``' shared read: an
    unshareable run and a damaged marker are the same non-answer here, since
    neither can produce an upload.
    """
    try:
        return telemetry_log_path(output_dir)
    except (OSError, UnicodeError):
        return ""


def _read_payloads(output_dir: str, repo: str) -> tuple[dict, list[str]] | str:
    """Read precisely the marker, sibling manifest, and sibling JSONL file.

    ``repo`` is the identity consent was checked against. The manifest must
    describe that same repository, or nothing is read for upload: consent
    for one repository never releases a payload naming another.
    """
    log_path = _log_path(output_dir)
    if not log_path:
        return "skipped: telemetry log unavailable"
    try:
        with open(telemetry_manifest_path(log_path), encoding="utf-8") as source:
            manifest = json.load(source)
    except (OSError, UnicodeError, json.JSONDecodeError):
        return "skipped: manifest unavailable"
    if not isinstance(manifest, dict):
        return "skipped: manifest invalid"
    if manifest.get("status") != "complete":
        return "skipped: run incomplete"
    run = manifest.get("run")
    if not isinstance(run, dict):
        return "skipped: manifest invalid"
    run_id = run.get("id")
    if not isinstance(run_id, str) or not run_id:
        return "skipped: run id unavailable"
    if SAFE_RUN_ID_SEGMENT_RE.fullmatch(run_id) is None:
        return "skipped: run id invalid"
    if not isinstance(run.get("repo"), str) or not run["repo"]:
        return "skipped: manifest repository unavailable"
    if run["repo"] != repo:
        return "skipped: manifest repository mismatch"
    try:
        with open(log_path, encoding="utf-8") as source:
            return manifest, source.readlines()
    except (OSError, UnicodeError):
        return "skipped: telemetry log unavailable"


def _upload_run(output_dir: str, repo: str) -> str:
    """Upload only a complete run's redacted telemetry, never raising to callers."""
    try:
        payloads = _read_payloads(output_dir, repo)
        if isinstance(payloads, str):
            return payloads
        manifest, jsonl_lines = payloads
        redacted_manifest, redacted_jsonl = redact_payloads(manifest, jsonl_lines)
        run = redacted_manifest["run"]
        run_id = run["id"]
        login = _login()
        manifest_path = f"{LAYOUT_PREFIX}/{login}/{run_id}.manifest.json"
        jsonl_path = f"{LAYOUT_PREFIX}/{login}/{run_id}.jsonl"
        # The manifest is the unit of publication: the shared reader measures
        # a complete manifest fully on its own, so it goes first. The JSONL
        # is supplementary, and once the manifest is remote a failed JSONL
        # upload is a partial share the outcome must say so — not a skip.
        _put_content(
            manifest_path,
            json.dumps(redacted_manifest, ensure_ascii=False).encode("utf-8"),
            _content_sha(manifest_path),
        )
        try:
            _put_content(
                jsonl_path,
                "".join(redacted_jsonl).encode("utf-8"),
                _content_sha(jsonl_path),
            )
        except _UploadFailure as error:
            return f"shared {run_id} (manifest only; jsonl upload failed: {error})"
        return f"shared {run_id}"
    except _UploadFailure as error:
        return f"skipped: upload failed ({error})"
    except (KeyError, TypeError, ValueError):
        return "skipped: telemetry payload invalid"
    except Exception:
        return "skipped: upload failed (unexpected error)"


def recorded_repo(output_dir: str) -> str:
    """Return the run's recorded repository identity, empty when unavailable.

    Reads the pipeline-start event the manifest's ``run.repo`` is projected
    from, so the consent gate, the consent prompt, and the uploaded payload
    can never disagree about which repository a run belongs to.
    """
    log_path = _log_path(output_dir)
    if not log_path:
        return ""
    try:
        with open(log_path, encoding="utf-8") as source:
            first = json.loads(source.readline())
    except (OSError, ValueError):
        return ""
    if not isinstance(first, dict) or first.get("event") != "pipeline_start":
        return ""
    pipeline = first.get("pipeline")
    repo = pipeline.get("repo") if isinstance(pipeline, dict) else None
    return repo if isinstance(repo, str) else ""


def maybe_upload(output_dir: str) -> str:
    """Apply the global and repository consent gates to the run's own identity."""
    telemetry = telemetry_settings(load_user_settings())
    sharing = telemetry["sharing"]
    if sharing == "disabled":
        return SHARING_DISABLED_OUTCOME
    if sharing != "enabled":
        return CONSENT_UNSET_OUTCOME
    repo = recorded_repo(output_dir)
    if not repo:
        return "skipped: repository identity unavailable"
    consent = telemetry["repos"].get(repo, "unset")
    if consent == "exclude":
        return "skipped: repo excluded"
    if consent != "include":
        return "skipped: repo consent unset"
    return _upload_run(output_dir, repo)


def _remote_identity(remote_url: str) -> str | None:
    """Return a ``host/owner/name`` identity only from recognized origin URLs.

    The host stays in the identity because ``owner/name`` alone collides
    across forges, silently sharing one repository under another's consent.
    An explicit port stays for the same reason: two forges can share one
    hostname on different ports, and stripping the port would let consent
    for one endpoint authorize uploads from the other. The port is kept
    verbatim, so the same repository reached with and without a default
    port yields two consent keys — asking twice is the safe direction.
    """
    if _DRIVE_PATH.match(remote_url) or remote_url.startswith("\\\\"):
        # A local Windows path is not a remote; SCP parsing would otherwise
        # read the drive letter as a one-letter host.
        return None
    parsed = urlparse(remote_url)
    host = ""
    path = ""
    if parsed.scheme in {"http", "https", "ssh"} and parsed.netloc:
        host = parsed.netloc.rpartition("@")[2]
        path = parsed.path
    else:
        ssh_match = _SSH_REMOTE.fullmatch(remote_url)
        if ssh_match:
            host = ssh_match.group(1)
            path = ssh_match.group(2)
    parts = path.strip("/").split("/")
    if not host or len(parts) != 2 or not all(parts):
        return None
    owner, name = parts
    if name.endswith(".git"):
        name = name[:-4]
    return f"{host.lower()}/{owner}/{name}" if name else None


def repo_identity(repo_path: str) -> str:
    """Derive ``host/owner/name`` from origin; empty without a shareable identity.

    A repository without a recognized origin remote has no stable
    cross-machine identity, so consent and uploads fail closed instead of
    keying on a colliding directory basename.

    Total by construction: any failure to derive an identity — git missing or
    erroring, a remote URL whose syntax no parser accepts, an unreadable
    path — is the same answer, "no shareable identity". Callers therefore
    need no guard of their own, and a new origin-URL surprise fails closed
    without a new except clause here.
    """
    try:
        result = subprocess.run(
            ["git", "-C", repo_path, "remote", "get-url", "origin"],
            capture_output=True,
            text=True,
            timeout=_GIT_TIMEOUT_SECONDS,
            check=False,
        )
        if result.returncode != 0:
            return ""
        return _remote_identity(result.stdout.strip()) or ""
    except Exception:
        return ""


def sharing_state() -> str:
    """Return the global sharing choice, or ``unset`` without explicit consent."""
    return telemetry_settings(load_user_settings())["sharing"]


def repo_consent(repo: str) -> str:
    """Return a repository inclusion choice, or ``unset`` without one."""
    return telemetry_settings(load_user_settings())["repos"].get(repo, "unset")


def _record_setting(mutate) -> None:
    """Serialize one read-modify-write of the settings file, published atomically.

    The config directory's descriptor is the lock (the same primitive the
    review pipeline uses), so two runs finishing together cannot erase each
    other's choices, and an interrupted write can never leave malformed JSON
    that reads as an empty settings file.
    """
    path = user_config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    with output_dir_lock(str(path.parent)):
        settings = load_user_settings()
        telemetry = settings.get("telemetry")
        telemetry = dict(telemetry) if isinstance(telemetry, dict) else {}
        mutate(telemetry)
        settings["telemetry"] = telemetry
        atomic_write_json(str(path), settings)


def record_sharing(value: str) -> None:
    """Record the explicit global sharing choice without changing other settings."""
    if not isinstance(value, str) or value not in SHARING_CHOICES:
        raise ValueError("sharing must be 'enabled' or 'disabled'")

    def mutate(telemetry: dict) -> None:
        telemetry["sharing"] = value

    _record_setting(mutate)


def record_repo(repo: str, value: str) -> None:
    """Record the explicit inclusion choice for one canonical repository identity."""
    if not isinstance(repo, str) or not repo:
        raise ValueError("repository has no shareable identity (origin remote required)")
    if not isinstance(value, str) or value not in REPO_CHOICES:
        raise ValueError("repository consent must be 'include' or 'exclude'")

    def mutate(telemetry: dict) -> None:
        repos = telemetry.get("repos")
        repos = dict(repos) if isinstance(repos, dict) else {}
        repos[repo] = value
        telemetry["repos"] = repos

    _record_setting(mutate)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    status = commands.add_parser("status")
    status_source = status.add_mutually_exclusive_group()
    status_source.add_argument("--repo-path")
    status_source.add_argument("--output-dir")
    set_sharing = commands.add_parser("set-sharing")
    set_sharing.add_argument("value", choices=SHARING_CHOICES)
    set_repo = commands.add_parser("set-repo")
    set_repo_source = set_repo.add_mutually_exclusive_group(required=True)
    set_repo_source.add_argument("--repo-path")
    set_repo_source.add_argument(
        "--output-dir",
        help="Bind the choice to the run's recorded repository identity.",
    )
    set_repo.add_argument("value", choices=REPO_CHOICES)
    upload_run_parser = commands.add_parser("upload-run")
    upload_run_parser.add_argument("--output-dir", required=True)
    return parser


def _cli_repo(arguments) -> str:
    """Resolve the identity a command acts on — recorded run first, path second."""
    if arguments.output_dir:
        return recorded_repo(arguments.output_dir)
    if arguments.repo_path:
        return repo_identity(arguments.repo_path)
    return ""


def main(argv=None) -> int:
    """Run the consent-store CLI."""
    arguments = _parser().parse_args(argv)
    if arguments.command == "status":
        print(f"sharing={sharing_state()}")
        if arguments.repo_path or arguments.output_dir:
            repo = _cli_repo(arguments)
            if repo:
                print(f"repo={repo} consent={repo_consent(repo)}")
            else:
                print("repo=unavailable consent=unavailable")
    elif arguments.command == "set-sharing":
        record_sharing(arguments.value)
    elif arguments.command == "set-repo":
        try:
            record_repo(_cli_repo(arguments), arguments.value)
        except ValueError as error:
            print(f"ERROR: {error}", file=sys.stderr)
            return 2
    elif arguments.command == "upload-run":
        outcome = maybe_upload(arguments.output_dir)
        stream = sys.stderr if outcome.startswith("skipped: upload failed") else sys.stdout
        print(outcome, file=stream)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
