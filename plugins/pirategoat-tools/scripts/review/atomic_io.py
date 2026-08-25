#!/usr/bin/env python3
"""Single implementation of the pipeline's atomic-JSON-write convention.

Every artifact this function touches replaces the old file in one step or
leaves it untouched: write to a temp file in the SAME directory as the
target, then ``os.replace`` it over the target. ``os.replace`` never
copies — it is a same-filesystem rename, and raises ``OSError`` (EXDEV)
if the temp file and target are on different filesystems, so pinning the
temp file to the target directory is what keeps that rename possible in
the first place. A half-written JSON file must never be observable on
disk.

Before consolidation this was five separate spellings of the same nine
lines: critic_adjustments.py's decision-critic ledger, orchestration.py's
dispatch-plan baseline, pipeline.py's review-context reset, telemetry.py's
run manifest, and analysis/usage_snapshot.py's token snapshot. One drifts,
they all drift eventually — so there is now exactly one.

One artifact may NOT use this function directly: review-findings.json is
never written with a bare ``atomic_write_json``. It goes through
``critic_adjustments.write_findings(output_dir, findings)``, which owns
the ledger's filename and calls this underneath. That artifact has exactly
ONE write path and exactly two writers going through it — the
reconciliator's first write via ``findings_save.py``, and the critic
adjustments applier — so a bare write here would be a SECOND write path
(see the one-write-path rule in the plugin's AGENTS.md).

Reviewer draft replacement and finalization use staged nonce files and
the shared ``output_dir_lock()`` below. Their state transitions coordinate
between processes, while this module deliberately knows nothing about
reviewer filenames or lifecycle states.
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
    whoever reads the artifact next. ``review-findings.json`` alone has
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


def atomic_write_text(path, text):
    """Replace a plain-text artifact in one step, or leave the old one
    intact — the same crash-safety contract as ``atomic_write_json``
    (same-directory temp file, then ``os.replace``), for artifacts that
    are prose rather than JSON. The decision critic's save channel uses
    this for ``decision-critic-findings.md``: a Markdown findings document
    has no JSON shape to serialize, but still must never be observable
    half-written on disk.
    """
    _atomic_write(path, lambda f: f.write(text))
