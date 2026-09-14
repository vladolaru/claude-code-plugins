#!/usr/bin/env python3
"""The pipeline's JSON file conventions, each implemented once: the atomic
write, the object read, and the output-directory lock.

Write to a temp file in the SAME directory as the target, then
``os.replace`` it over the target: a half-written JSON file must never be
observable on disk. The shared directory keeps that replace a
same-filesystem rename, the only kind ``os.replace`` can do.

The findings ledger may NOT use this function directly. It goes through
``critic_adjustments.write_findings()``, which owns the ledger's filename
and calls this underneath, so that artifact keeps exactly ONE write path
(see the one-write-path rule in the plugin's AGENTS.md).

``output_dir_lock()`` is the pipeline's one directory-lock convention,
shared by publication, adjudication and reviewer finalization. This module
deliberately knows nothing about reviewer filenames or lifecycle states.
"""

import contextlib
import json
import os
import tempfile

try:
    import fcntl
except ImportError:  # pragma: no cover - exercised only on non-POSIX hosts
    fcntl = None


@contextlib.contextmanager
def output_dir_lock(output_dir):
    """Exclusively lock an output directory without creating an artifact.

    The directory's own descriptor is the lock target, so there is no lock
    file to leak into artifact discovery or cleanup. ``flock`` releases when
    the descriptor closes, including after process death. Non-POSIX hosts
    retain the context-manager boundary but cannot provide cross-process
    exclusion.
    """
    if fcntl is None:
        yield
        return
    lock_fd = os.open(output_dir, os.O_RDONLY)
    try:
        fcntl.flock(lock_fd, fcntl.LOCK_EX)
        yield
    finally:
        os.close(lock_fd)


def _atomic_write(path, write_payload):
    """Shared staging for both writers below: write via
    ``write_payload(file_obj)`` to a temp file in the SAME directory as
    ``path``, then replace ``path`` with it in one step. The crash-safety
    contract — same-directory temp file (so ``os.replace`` stays a
    same-filesystem rename), best-effort cleanup of the temp file on
    failure, never touching ``path`` itself until the replace — is
    written once here; ``atomic_write_json`` and ``atomic_write_text``
    differ only in how they serialize ``payload`` onto the open file.
    """
    directory = os.path.dirname(path) or "."
    temp_path = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            delete=False,
            dir=directory,
            encoding="utf-8",
        ) as temp_file:
            temp_path = temp_file.name
            write_payload(temp_file)
            temp_file.flush()
        os.replace(temp_path, path)
        temp_path = None
    finally:
        if temp_path and os.path.exists(temp_path):
            try:
                os.unlink(temp_path)
            except OSError:
                pass


def atomic_write_json(path, payload):
    """Replace a JSON artifact in one step, or leave the old one intact.

    Failures propagate to the caller — an artifact this function could not
    write in full must not read as a success to anything outside it.

    ``ensure_ascii=False`` is deliberate and shared by every caller: review
    findings and telemetry text carry ordinary prose (em dashes, curly
    quotes), and escaping it would turn a routine rewrite into a wall of
    ``\\uXXXX`` runs — lossless, but indistinguishable from corruption to
    whoever reads the artifact next. The canonical findings ledger alone has
    two writers across a run (the review-reconciliator agent's first
    write, and critic_adjustments.py applying decision-critic adjustments)
    and both reach this function through
    ``critic_adjustments.write_findings()``, so they
    share this encoding and no writer's turn can make the file's prose
    unreadable to the others.

    This guarantees the artifact is never TORN — never half-old,
    half-new content — not that it survives a power loss: there is no
    ``fsync`` here, matching every writer this replaces. A crash after
    ``os.replace`` returns but before the OS has flushed the rename to
    disk can still lose the write; that risk existed in all five prior
    spellings and is unchanged by this consolidation.
    """
    _atomic_write(
        path, lambda f: json.dump(payload, f, indent=2, ensure_ascii=False)
    )


def read_json_object(path, label):
    """Read a JSON file that must hold an object.

    A missing file raises FileNotFoundError untouched, because callers
    answer absence differently: a critic snapshot not yet written is a state,
    a save input that does not exist is a refusal. Anything else wrong with
    the file (an unreadable path, bytes that are not JSON, JSON that is not
    an object) raises ValueError naming `label` and what was found, so every
    caller reports the same fault in the same words and never as a
    traceback.
    """
    try:
        with open(path, "r", encoding="utf-8") as handle:
            value = json.load(handle)
    except FileNotFoundError:
        raise
    # RecursionError is what the parser raises for pathologically nested
    # arrays or objects; it is a malformed file like any other.
    except (OSError, ValueError, RecursionError) as error:
        raise ValueError(f"{label} is not readable JSON: {error}") from error
    if not isinstance(value, dict):
        found = "null" if value is None else type(value).__name__
        raise ValueError(f"{label} must be a JSON object, got {found}")
    return value


def collect_json_object(path, label, problems):
    """`read_json_object` for a save channel that reports every problem
    before deciding anything: the object, or None with the problem
    appended.

    `label` is the command-line flag the file came in on; the problem names
    it and the path, and a missing file reads as not found rather than as
    unreadable JSON. The critic's and the reconciliator's save channels
    both read their JSON input through here.
    """
    try:
        return read_json_object(path, f"{label} ({path})")
    except FileNotFoundError:
        problems.append(f"{label} file not found: {path}")
    except ValueError as error:
        problems.append(str(error))
    return None


def atomic_write_text(path, text):
    """Replace a plain-text artifact in one step, or leave the old one
    intact — the same crash-safety contract as ``atomic_write_json``
    (same-directory temp file, then ``os.replace``), for artifacts that
    are prose rather than JSON. The decision critic's save channel uses
    this for the decision critic's Markdown findings artifact: that document
    has no JSON shape to serialize, but still must never be observable
    half-written on disk.
    """
    _atomic_write(path, lambda f: f.write(text))
