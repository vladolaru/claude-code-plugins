Last updated: 2026-08-01 14:52

> **Prompt:** "Fix these cleanly, preferably by going to the root cause and ensure better architectural basis and more robust behavior. Take opportunities to simply rather than expand. Commit when done."

# Review hardening investigation

## Scope

Validate and resolve the five supplied review findings around repo-contributed reviewer provenance, in-place Composer containment, symlinked dependency-input staging, Git-quoted changed paths, and the public host-context reason type.

## Initial state

- Branch: `feat/review-pipeline-measurement`.
- Worktree was clean at investigation start.
- The plugin contract explicitly requires canonical-path provenance checks and forbids host-install writes to the reviewed worktree.
- Runtime already emits `dep_roots_capped`; the TypeScript output contract must be checked for drift.

## Investigation log

All five findings are valid:

1. `load_review_config._gate()` always reads `resolved_path`, but reviewer normalization publishes `resolved_ref`. A reviewer symlink whose target is changed remains trusted; the equivalent rule case is already covered.
2. In-place Composer installs override vendor and bin output only. A relative `config.cache-dir` therefore remains rooted in the reviewed dependency directory; `COMPOSER_CACHE_DIR` is absent from the subprocess environment.
3. Staging resolves an input for the source read and then derives the destination from that resolved identity. For `package.json -> config/package.json`, staging creates `cache/config/package.json` and omits the declared `cache/package.json` manifest.
4. Dependency-root scoping treats backslashes as path separators before interpreting Git C-quoting. A changed path such as `"packages/caf\303\251/src/x.php"` selects no nested Composer root. Three existing callers already implement the same Git `quote.c` grammar with different error policies; adding a fourth local parser would deepen documented drift.
5. Python runtime output includes `dep_roots_capped`, while `HostContextBanner.reason` in `schemas/review-output.ts` omits it.

Minimal reproductions confirmed the first four behavior failures directly. The fifth is a literal producer/consumer contract mismatch.

## Design options

### A. Patch each call site independently

Add the missing reviewer field lookup, Composer environment variable, declared staging destination, a fourth Git path decoder, and the TypeScript literal. This is the smallest diff, but it preserves the decoder-drift root cause already called out in code comments.

### B. Fix identity boundaries and centralize Git path grammar (recommended)

- Make provenance gating derive the resolved-field name from the declaration field (`path` -> `resolved_path`, `ref` -> `resolved_ref`) so the two normalized entry shapes share one gate without another branch.
- Treat staging source and destination as separate identities: read from the containment-checked resolved source, write to a containment-checked normalized destination based on the declared relative path.
- Redirect every known Composer write root (vendor, bin, cache) into the atomic cache staging directory and extend the end-to-end immutability fake to model relative cache configuration.
- Extract the existing Git C-quote grammar into one small stdlib module. Keep caller-specific failure policies in thin wrappers, and use the shared decoder before dependency-root path normalization.
- Add `dep_roots_capped` to the public TypeScript union plus a drift test comparing the Python and TypeScript banner-reason vocabularies.

This touches more existing decoder call sites than option A, but removes duplicated grammar and follows the repository's own documented threshold: a fourth decoder is the evidence to consolidate.

### C. Change only Git collection to NUL-delimited output

Use `git diff --name-only -z` in `review/context.py`. This fixes locally collected paths at the source, including newlines, but not precomputed bot context or direct `ensure_installed --scope-path/--scope-json` inputs. It is useful independently but incomplete for this review finding.

## Implementation outcome

Implemented option B and covered every reported boundary:

- Reviewer provenance now derives the resolved identity field from the declared field, so both rule paths and reviewer refs gate their canonical targets.
- Dependency staging reads from the resolved, containment-checked source but writes to the independently checked declared path.
- In-place Composer installs force vendor, bin, and cache output into the atomic staging transaction.
- Git C-quoted path grammar now has one canonical implementation. Existing consumers retain their caller-specific malformed-input policies, while dependency-root selection decodes before path normalization.
- Python and TypeScript host-context reason vocabularies now include `dep_roots_capped`, with an exact cross-language drift test.

## Verification evidence

- Focused regression aggregate: `717 passed`.
- Pirategoat Tools suite: `4084 passed, 24 skipped`.
- All plugin suites: `4948 passed, 24 skipped`.
- Generated Codex compatibility check: all 48 generated files current.
- Direct Python compile and CLI entry-point smoke checks passed.
- Independent code review found no critical, important, or minor issues and judged the change ready to merge.
