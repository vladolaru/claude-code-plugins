# Diff-scoped review is blind to inherited invalidity in measurement code

**Date:** 2026-08-06
**Context:** `feat/detection-benchmark-eval` — the detection benchmark for the
review pipeline went through 10 spec-reviewed tasks, a whole-branch final
review, and four post-hoc review rounds, yet two foundational defects
survived until the last round:

1. `dispatch_agent()` loaded each agent's `.md` definition, accepted it as a
   parameter, and **never used it** — every "reviewer" the benchmark graded
   was generic Claude with only the shared bootstrap protocol, on the ambient
   default model instead of the agent's routed tier. The benchmark's primary
   claim ("measures the configured reviewers") was false from day one.
2. Ten hand-authored fixture diffs declared hunk headers 1–3 lines short, and
   `git apply` silently drops the excess — every affected file had *always*
   applied truncated, injecting unkeyed syntax errors into what reviewers saw.

## Why several careful review rounds missed both

- **Layer inheritance.** Both defects lived in code/data that predated the
  benchmark. The dispatch harness was written when the eval only graded
  output-format compliance — for that purpose, a generic session with the
  bootstrap's output contract was arguably adequate. The detection benchmark
  then reused the layer unchanged, and the new feature silently *redefined
  what "correct" meant* for unchanged lines. No diff ever contained the
  defect, so no diff-scoped review ever saw it.
- **Green signals validated the wrong proposition.** Live eval runs kept
  passing. That proved the graders worked; it was read as proving the
  benchmark measured what it claimed. "The eval passes" and "the eval
  measures X" are independent claims — nothing in the loop separated them.
- **A visible smell went unflagged.** `agent_def` loaded → passed → ignored
  is a dead parameter across a function boundary; any reviewer would flag it
  *in a diff*. It appeared in none.

## The rule

**When a change makes code into a measurement instrument (eval, benchmark,
metric, guard), one review pass must trace the full execution path from the
claim to the mechanism — including unchanged inherited layers.** Ask
literally: "when this runs, what exact process, prompt, identity, model, and
input content exist — and does each match what the measurement claims?"
Fixtures and harnesses are part of the instrument; validate what they
*produce at runtime* (apply the diff, read the assembled prompt), not what
their source looks like.

Corollary: pin instrument identity with deterministic tests so it cannot
silently regress — see `TestDispatchIdentity` and the fixture integrity
guards in `plugins/pirategoat-tools/tests/grading/test_answer_keys.py`.

## Fourth-order lessons (independent 4-reviewer audit round, 2026-08-06)

1. **A probe must prove the specific claim, not a neighboring one.** The
   `--agent` probe proved "an agent by that name runs with reviewer
   behavior" — not "the WORKTREE definitions run". The plugin dir carries
   no manifest, so the name silently resolved to the user-scope INSTALLED
   plugin. Only a sentinel planted in the worktree file plus a negative
   control (isolation on, plugin-dir off → agent must NOT resolve)
   discriminated the hypotheses. Design probes with discriminating power:
   what observation would differ if the claim were false?
2. **Evidence produced but not consumed is decoration.** `dispatch_agent`
   computed the model-mismatch rejection and returned rc=1 — which the
   caller wrote to a transcript and ignored, grading the rejected run's
   artifacts green. When adding an evidence chain, trace it to the decision
   it must gate and test the gate, not the evidence generator.
3. **Independent reviewers with distinct charters find disjoint defect
   sets.** Doctrine audit, harness logic, and end-to-end validity each
   surfaced P1s the others did not. For measurement code, one reviewer is
   not a review.

## Third-order lesson: fixing the instrument invalidates prior calibrations

The answer keys were authored and live-validated while the benchmark was
still dispatching generic Claude. They therefore encoded *what generic
Claude does*, not *what the configured reviewers mandate*: the performance
and WP-architecture keys rejected `block` verdicts that those agents'
definitions explicitly require (missing `LIMIT` = CRITICAL; unprefixed
globals = CRITICAL), and the SQL-injection spec accepted a bare `$_GET`
source token — satisfiable by an access-control finding with no injection
reported. When the dispatch-identity fix landed, these latent
miscalibrations became live defects. **Every calibration made against a
broken instrument must be re-derived when the instrument is fixed** — and
key expectations must always be derived from the dispatched agent's own
stated doctrine (read its .md, cite the section in the key), never from
generic reviewer intuition. Identity claims should also be evidenced, not
trusted: the JSON `modelUsage` check (`check_dispatched_models`) turns
"the routed model ran" from an assumption into per-run verified data.

## Second-order lesson: don't imitate a mechanism you can invoke

The first fix for the dispatch defect embedded the agent definition body in
a user prompt and hand-mapped the `model:` field to `--model` — a partial
re-encoding that silently dropped `effort:` and `tools:` and demoted the
instructions to user-prompt priority. Any hand-mapping of a config contract
drifts the moment the contract grows a field. The correct fix used the
native mechanism itself: `claude -p --plugin-dir <plugin>` lets the
subprocess dispatch the real subagent via the Agent tool, so the host
applies the *entire* frontmatter contract with zero re-encoding
(`eval_agent_compliance.py::dispatch_agent`). **When a harness must mirror
production behavior, first check whether the platform can run the
production mechanism directly — imitation is a last resort, and every
imitated field is a future drift.** Where two config sources exist
(registry `model_tier` vs frontmatter `model`), refuse to run on
divergence rather than picking one silently (`check_model_routing`).
