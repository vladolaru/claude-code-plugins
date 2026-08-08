# Agent Compliance Input Contracts Design

## Goal

Make the compliance benchmark interpret Claude CLI model usage and explicit CLI options by their documented meaning, so metadata, provider aliases, and default-valued flags cannot silently change evaluation behavior.

## Design

Keep both corrections inside `tests/grading/eval_agent_compliance.py`.

For model usage, define the four token counters that represent work: `inputTokens`, `outputTokens`, `cacheReadInputTokens`, and `cacheCreationInputTokens`. `_primary_model()` will sum only those fields. A tiny helper will resolve a record's routing identity from a non-empty string `canonicalModel`, falling back to the outer map key. The primary-model path and the no-weight membership fallback will consume that same resolved identity. This is an explicit projection of the payload, not a new model layer.

For CLI validation, let argparse represent omitted `--trials` as `None`. Compatibility checks will test presence with `is not None`; after those checks, the value will be normalized to `1` so the dispatch code stays unchanged. No subcommands, custom argparse actions, or raw `sys.argv` parsing are needed.

## Error handling

Malformed or absent usage records retain the current conservative behavior: records without positive recognized token usage do not become the primary model, and routed tiers fall back to membership or fail closed when no matching identity exists. Invalid trial counts still produce argparse exit code 2. Explicit `--trials 1` without dispatch, including beside `--grade-only`, will now follow the same configuration-error path as every other explicit trials value.

## Testing

Add focused regressions to `tests/grading/test_eval_agent_compliance.py`:

- capacity fields cannot outweigh real token usage;
- an alias with the expected `canonicalModel` passes routing validation;
- bare explicit `--trials 1` exits 2;
- `--grade-only ... --trials 1` exits 2.

Each regression must be observed failing before its production change and passing afterward. Run the focused test module after each task and the full prescribed grading suite before completion.

## Documentation and release metadata

Update `tests/TESTING.md` to describe token weighting and canonical identity. Extend the existing unpushed `1.114.0` changelog entry; do not add another version bump. No generated Codex output changes because the marketplace version is already `1.114.0` and no canonical marketplace source changes.

## Non-goals

- no typed model-usage class;
- no generic CLI mode framework;
- no speculative provider validation;
- no unrelated benchmark hardening.
