# Cross-repo contract: pirategoat-bot

Read this before changing any surface the `pirategoat-bot` Slack bot (`~/Work/a8c/pirategoat-bot`) exchanges with this plugin, and read the bot's code first, since the bot's expectations are in its source, not in this plugin's: `src/orchestrator-review.js` (writes `review-context.json`, reads review output), `src/github.js` (maps verdicts to GitHub actions), `prompts/pr-review.md` (the outer-pipeline prompt), `src/orchestrator-linear.js` and `src/messages-linear.js` (Linear results and Slack messages).

The shared surfaces:

- **`review-context.json` and `issue-context.json`.** The bot writes them before spawning the `claude` CLI; this plugin reads and enriches them (`review/context.py`). Field names, nesting, required paths, and the bot-owned `version: 1` key must match.
- **Verdict layers.** Outer-pipeline verdicts (`APPROVE`/`COMMENT`/`REQUEST_CHANGES`) are the bot's layer; this plugin's `block`/`request_changes`/`comment`/`approve` are the ledger's. `verdict_rules.publish_verdict()` maps between them and `orchestration.py` applies the critic `ESCALATE` override at step 11.
- **Prompt template variables** (`{{MERGE_BASE}}`, `{{GIT_RANGE}}`, and others in the bot's `prompts/`) reach this plugin through the review context.
- **Terminal marker.** Resume discovery treats any `pipeline-result.json` as complete and delivery then reads `review-report.md`, so step 11 creates `pipeline-result.json` only after that exact report exists, with `report_path` naming it; `review-record.md` and `review-findings.md` are never terminal fallbacks.
- **Reviewer markers.** The bot's resume path scans reviewer `started` markers under `reviewers/<reviewer>/`; synthesis agents use a different name (`synthesis_lifecycle.MARKER_SUFFIX`) so they are never mistaken for reviewers.
- **Linear pipeline.** The bot reads `pipeline-result.json` (`status`, `verdict`, `clarity_gate`, `clarity_gate_overridden`) and `clarity-assessment.json` (`summary`, `questions_for_author`). Step 8's clarity gate blocks implementation (`status: "blocked"`, `verdict: "needs_clarification"`) when a hard gate fails; the bot can override with `skip_clarity_gate: true` in `run-config.json`. `needs_more_info` means the investigation was inconclusive; `needs_clarification` means it succeeded but the issue lacks implementation clarity.
- **`usage` block.** `pipeline-result.json` carries a compact token-usage block the bot consumes, projected through `manifest_sections.build_usage_manifest()`.
