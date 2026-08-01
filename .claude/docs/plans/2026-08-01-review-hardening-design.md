Last updated: 2026-08-01 14:37

> **Prompt:** "Fix these cleanly, preferably by going to the root cause and ensure better architectural basis and more robust behavior. Take opportunities to simply rather than expand. Commit when done."

# Review boundary hardening design

## Goal

Close the five reviewed correctness gaps while making path identity and output vocabulary harder to drift across pipeline layers.

## Architecture

The implementation will distinguish declared paths from resolved filesystem identities. Provenance checks will gate both identities; staging will read the resolved source but preserve the declared destination. Both flows remain fail-closed at their existing normalization choke points.

Git C-quoting will have one grammar implementation under `scripts/`. Callers retain only their policy differences: provenance preserves malformed input so it cannot broaden trust, telemetry marks malformed authoritative data unavailable, scope parsing drops an unusable marker, and dependency-root discovery ignores an undecodable path.

Composer will continue running in place for relative `type: path` repositories, but every supported write root—vendor, bin, and cache—will point into the cache slot's atomic staging directory. The host containment invariant remains the architectural contract rather than a set of unrelated redirect tests.

The Python host-context banner vocabulary remains the runtime source. A schema drift test will extract the TypeScript `HostContextBanner.reason` literals and require exact agreement, catching future producer/consumer omissions.

## Components

- `review/review_config.py`: derive `resolved_<declaration field>` in the shared provenance gate.
- `hosts/install/staging.py`: resolve source and destination independently through the shared containment primitive.
- `hosts/ensure_installed.py`: redirect Composer cache alongside vendor and bin.
- `git_paths.py`: own Git `quote.c` escape and octal decoding once.
- Existing provenance, telemetry, and scope callers: delegate grammar to `git_paths.py` while preserving their current failure semantics.
- `hosts/install/lockfile.py`: decode a scope path before slash normalization and ancestor discovery.
- `schemas/review-output.ts`: include the capped-root degradation reason.

## Error behavior

- Malformed Git quoting never invents a path. Security-sensitive provenance retains the original spelling; authoritative telemetry fails closed; root selection skips the unusable entry.
- A staged source or destination that escapes its allowed root is skipped.
- Composer install failures keep the existing degraded-banner behavior; only the subprocess environment changes.

## Test strategy

Each defect gets a focused regression test observed failing before production edits:

- changed target behind a reviewer symlink is untrusted;
- relative Composer cache configuration cannot alter the worktree and `COMPOSER_CACHE_DIR` is absolute/outside it;
- symlinked manifests and patches are staged at their declared paths;
- Git-quoted non-ASCII/control-character scope paths select their nested dependency roots;
- Python and TypeScript host-context banner reasons are identical.

Existing decoder-policy tests and the end-to-end worktree snapshot test will cover refactor preservation. Focused suites run after each fix, followed by the full pirategoat-tools suite and deterministic Codex-generation check.
