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
``critic_adjustments.write_findings(output_dir, findings)``, which stamps
the content digest finalize verifies and then calls this — a bare write
here publishes a ledger the run reports as modified out of channel (see
the one-write-path rule in the plugin's AGENTS.md).

The one deliberate exception is agent/output.py's staged-nonce two-file
write for ``<reviewer>-review.json``. That protocol coordinates BETWEEN
processes — an agent's own write racing the step 8 readiness gate's read —
not a single writer's crash safety against itself, so it is a different
problem and intentionally stays on its own implementation.
"""

import json
import os
import tempfile


def atomic_write_json(path, payload):
    """Replace a JSON artifact in one step, or leave the old one intact.

    Failures propagate to the caller — an artifact this function could not
    write in full must not read as a success to anything outside it.

    ``ensure_ascii=False`` is deliberate and shared by every caller: review
    findings and telemetry text carry ordinary prose (em dashes, curly
    quotes), and escaping it would turn a routine rewrite into a wall of
    ``\\uXXXX`` runs — lossless, but indistinguishable from corruption to
    whoever reads the artifact next. ``review-findings.json`` alone has
    three writers across a run (the review-reconciliator agent's first
    write, critic_adjustments.py applying decision-critic adjustments,
    and orchestration.py's Rule 23 verdict sync) and all three reach this
    function through ``critic_adjustments.write_findings()``, so they
    share this encoding and no writer's turn can make the file's prose
    unreadable to the others.

    This guarantees the artifact is never TORN — never half-old,
    half-new content — not that it survives a power loss: there is no
    ``fsync`` here, matching every writer this replaces. A crash after
    ``os.replace`` returns but before the OS has flushed the rename to
    disk can still lose the write; that risk existed in all five prior
    spellings and is unchanged by this consolidation.
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
            json.dump(payload, temp_file, indent=2, ensure_ascii=False)
            temp_file.flush()
        os.replace(temp_path, path)
        temp_path = None
    finally:
        if temp_path and os.path.exists(temp_path):
            try:
                os.unlink(temp_path)
            except OSError:
                pass
