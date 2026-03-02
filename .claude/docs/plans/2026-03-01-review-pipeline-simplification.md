# Review Pipeline Simplification — Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Simplify the pirategoat-tools review pipeline by centralizing orchestration, adding deterministic processing for mechanical tasks, extending scope coverage, and instrumenting quality metrics.

**Architecture:** Move duplicated orchestration logic from markdown commands into deterministic Python scripts backed by a canonical JSON agent registry. Split LLM-dependent work (creative reasoning) from mechanical work (scope checks, dedup, schema validation). Extend domain coverage to config/CI/infra files via existing agents.

**Tech Stack:** Python 3 scripts, JSON registry, pytest test suites, existing review-scope.py and bootstrap-reviewer.py infrastructure.

**Analysis document:** `plugins/pirategoat-tools/docs/analysis/2026-03-01-pr-and-code-review-pipeline-analysis.md`

---

## Dependencies

```
P0-1 (doc fixes)          → independent
P0-2 (config-ops scope)   → independent
P0-3 (quality metrics)    → independent
P1-2 (agent registry)     → independent (implement first in P1)
P1-1 (dispatch planner)   → depends on P1-2
P1-3 (reconcile engine)   → independent
P1-4 (ingest preprocess)  → independent (benefits from P1-3 output)
P2-2 (reliability agent)  → depends on P1-2
P2-3 (test adequacy)      → depends on P1-1 or P1-3 (integration point)
```

**Execution order:**
- P0: All three in parallel
- P1: Registry first → then planner, reconcile, ingest in parallel
- P2: After P1

---

## Task 1: Fix doc/policy consistency (P0-1)

**Files:**
- Modify: `plugins/pirategoat-tools/commands/pr-review.md` — fix hardcoded `/12` agent count
- Modify: `plugins/pirategoat-tools/README.md` — verify model tier counts (6+8+4=18) match actual agents
- Scan: all `commands/*.md` for other hardcoded agent counts

**Step 1: Audit all hardcoded agent counts**

Search all command files and docs for hardcoded numbers 12, 13, 18 that refer to agent counts. The dispatch table in `full-code-review.md:183-197` and `code-review.md:211-225` lists 13 agents. The README lists 18 total agents (13 dispatched + 5 non-default: tests-mutation, gemini, codex, reconciliator, technical-writer).

Known issues:
- `pr-review.md` report template: "Agents dispatched: <N> / 12" — should be 13
- README model tier counts (inherit: 6, sonnet: 8, haiku: 4) — verify these still match actual agent model assignments

**Step 2: Fix the agent count in pr-review.md**

Find the line with `/ 12` in the report template and change to `/ 13`.

**Step 3: Verify README tier counts**

Cross-reference the model tier counts in `README.md:38-40` against actual agent definitions. Count agents by model assignment in their frontmatter.

**Step 4: Run command tests**

Run: `pytest plugins/pirategoat-tools/tests/test_commands.py -v`
Expected: All pass

**Step 5: Commit**

```bash
git add plugins/pirategoat-tools/commands/pr-review.md plugins/pirategoat-tools/README.md
git commit -m "fix(pirategoat-tools): correct hardcoded agent count references"
```

---

## Task 2: Add config/CI/infra to scope catalog (P0-2)

**Files:**
- Modify: `plugins/pirategoat-tools/scripts/review-scope.py:37-98` — add `config-ops` domain
- Modify: `plugins/pirategoat-tools/scripts/bootstrap-reviewer.py:33-106` — map security-reviewer and architecture-reviewer to also cover config-ops
- Modify: `plugins/pirategoat-tools/agents/security-reviewer.md` — add config/CI security checklist
- Modify: `plugins/pirategoat-tools/agents/architecture-reviewer.md` — add infra/deployment architecture checklist
- Modify: `plugins/pirategoat-tools/commands/full-code-review.md:95-106` — extend security-reviewer triage criteria
- Modify: `plugins/pirategoat-tools/commands/code-review.md:123-134` — same triage update
- Create: `plugins/pirategoat-tools/tests/fixtures/ci-config-changes.diff` — test fixture
- Modify: `plugins/pirategoat-tools/tests/test_domain_routing.py` — add config-ops routing tests

**Step 1: Add config-ops domain to DOMAIN_CATALOG**

In `review-scope.py`, add after the `a11y` domain entry (after line 98):

```python
"config-ops": {
    "include": r"(\.github/workflows/|\.gitlab-ci|Dockerfile|docker-compose|\.tf$|\.tfvars$|\.toml$|Jenkinsfile|\.circleci/|Makefile$|\.helmfile|chart\.yaml$|values\.yaml$)",
    "exclude": None,
},
```

This captures: GitHub Actions, GitLab CI, Docker, Terraform, TOML config, Jenkins, CircleCI, Helm charts, Makefiles.

Note: `pnpm-lock.yaml` and similar are already in NOISE_PATTERNS (line 102) so they won't match.

**Step 2: Run existing domain routing tests to verify no regressions**

Run: `pytest plugins/pirategoat-tools/tests/test_domain_routing.py -v`
Expected: All existing tests pass (new domain doesn't affect existing routing)

**Step 3: Create test fixture for config/CI changes**

Create `tests/fixtures/ci-config-changes.diff` with a unified diff containing:
- A `.github/workflows/ci.yml` change
- A `Dockerfile` change
- A `terraform/main.tf` change

**Step 4: Add domain routing tests for config-ops**

Add parameterized tests in `test_domain_routing.py` that verify:
- `ci-config-changes.diff` + domain `config-ops` → STATUS=OK
- `ci-config-changes.diff` + domain `code` → STATUS=NO_DOMAIN_FILES
- `php-source.diff` + domain `config-ops` → STATUS=NO_DOMAIN_FILES

**Step 5: Run domain routing tests**

Run: `pytest plugins/pirategoat-tools/tests/test_domain_routing.py -v`
Expected: All pass including new tests

**Step 6: Update bootstrap-reviewer.py to map agents to config-ops**

The security-reviewer and architecture-reviewer currently have single domains (`"security"` and `"architecture"`). Change their `domain` field to support the config-ops domain.

Option A: Change `domain` to a list: `"domain": ["security", "config-ops"]`
Option B: Add a `secondary_domains` field: `"secondary_domains": ["config-ops"]`

Choose whichever pattern `bootstrap-reviewer.py`'s `build_output()` function can handle. If `domain` is currently expected as a string, option B is safer. Check `build_output()` to see how it calls `review-scope.py --domain`.

The key behavior: when changed files include config-ops files, security-reviewer and architecture-reviewer should NOT be skipped by preflight. Their scope output should include config-ops files alongside their primary domain files.

**Step 7: Update agent prompts**

Add a config/CI security checklist section to `security-reviewer.md` covering:
- Hardcoded secrets/credentials in CI configs
- Overly permissive CI permissions (e.g., `permissions: write-all`)
- Secret exposure via environment variables in public logs
- Insecure Docker base images
- Terraform security group misconfigurations

Add an infra/deployment architecture checklist section to `architecture-reviewer.md` covering:
- CI pipeline complexity and maintainability
- Docker layer efficiency and multi-stage builds
- Infrastructure-as-code organization
- Deployment topology and failure domains

**Step 8: Update triage criteria in command files**

In `full-code-review.md` (lines 95-106) and `code-review.md` (lines 123-134), extend the security-reviewer dispatch criteria to include:
- CI/CD configuration changes (`.github/workflows/`, `Dockerfile`, etc.)
- Infrastructure-as-code changes (`.tf`, Helm charts)

Similarly extend architecture-reviewer criteria (lines 115-122 / 143-150) to include:
- Deployment/infrastructure architecture changes

**Step 9: Run all tests**

Run: `pytest plugins/pirategoat-tools/tests/ -v`
Expected: All pass

**Step 10: Update CHANGELOG.md and marketplace.json version**

**Step 11: Commit**

```bash
git add plugins/pirategoat-tools/scripts/review-scope.py \
       plugins/pirategoat-tools/scripts/bootstrap-reviewer.py \
       plugins/pirategoat-tools/agents/security-reviewer.md \
       plugins/pirategoat-tools/agents/architecture-reviewer.md \
       plugins/pirategoat-tools/commands/full-code-review.md \
       plugins/pirategoat-tools/commands/code-review.md \
       plugins/pirategoat-tools/tests/fixtures/ci-config-changes.diff \
       plugins/pirategoat-tools/tests/test_domain_routing.py \
       plugins/pirategoat-tools/CHANGELOG.md \
       .claude-plugin/marketplace.json
git commit -m "feat(pirategoat-tools): add config-ops domain for CI/infra/config file review"
```

---

## Task 3: Quality metrics via session analysis (P0-3)

**Files:**
- Modify: `plugins/pirategoat-tools/scripts/analyze-reviewer-sessions.py` — add `--quality-metrics` mode
- Create: `plugins/pirategoat-tools/tests/test_quality_metrics.py` — tests for new extraction logic

**Step 1: Design the quality metrics data model**

Metrics to extract from session JSONL logs:

```python
@dataclass
class AgentQualityMetrics:
    agent_name: str
    total_findings: int            # from agent's Write output (JSON)
    findings_by_severity: dict     # {critical: N, high: N, ...}
    survived_ingest: int           # findings that passed ingest validation
    filtered_out_of_scope: int     # findings marked OUT_OF_SCOPE in ingest
    filtered_false_positive: int   # findings marked FALSE_POSITIVE in ingest
    filtered_style: int            # findings marked STYLE/PREFERENCE in ingest
    survival_rate: float           # survived / total (0.0-1.0)
```

```python
@dataclass
class SessionQualityReport:
    session_id: str
    agents_dispatched: int
    total_findings: int
    per_agent: List[AgentQualityMetrics]
    overlap_clusters: int          # findings flagged by 2+ agents
    severity_disagreements: int    # same issue, different severity from different agents
    ingest_summary: dict           # {confirmed: N, likely_valid: N, false_positive: N, out_of_scope: N, style: N}
```

**Step 2: Write tests for quality metrics extraction**

Create `tests/test_quality_metrics.py` with test cases:
- Parse a mock agent Write output (JSON) and extract finding counts
- Parse a mock ingest subagent log and extract categorization outcomes
- Handle missing/partial data gracefully (e.g., no ingest log, agent crashed)

**Step 3: Implement quality metrics extraction**

In `analyze-reviewer-sessions.py`, add:
- Function `extract_agent_findings(write_output: dict) -> dict` — parse agent JSON output, count findings by severity
- Function `extract_ingest_outcomes(ingest_log: list) -> dict` — parse ingest subagent's text output for finding categorization (CONFIRMED, FALSE POSITIVE, OUT_OF_SCOPE, etc.)
- Function `compute_survival_rate(agent_findings: dict, ingest_outcomes: dict) -> float` — correlate agent findings with ingest outcomes
- CLI flag `--quality-metrics` that triggers quality analysis mode

**Step 4: Add overlap detection**

Extract finding dedup data from reconciliator subagent log:
- Count findings that appear from 2+ agents (overlap clusters)
- Detect severity disagreements (same file+line range, different severity)

**Step 5: Run tests**

Run: `pytest plugins/pirategoat-tools/tests/test_quality_metrics.py -v`
Expected: All pass

**Step 6: Manual validation**

Run the quality metrics extraction against a real session directory to verify output makes sense:

```bash
python3 plugins/pirategoat-tools/scripts/analyze-reviewer-sessions.py \
    --sessions-dir ~/.claude/projects/-Users-vladolaru-Work-a8c-ciab-admin \
    --quality-metrics \
    --max-sessions 3
```

**Step 7: Update CHANGELOG.md and marketplace.json version**

**Step 8: Commit**

```bash
git add plugins/pirategoat-tools/scripts/analyze-reviewer-sessions.py \
       plugins/pirategoat-tools/tests/test_quality_metrics.py \
       plugins/pirategoat-tools/CHANGELOG.md \
       .claude-plugin/marketplace.json
git commit -m "feat(pirategoat-tools): add quality metrics extraction to session analysis"
```

---

## Task 4: Agent registry in JSON (P1-2)

**Files:**
- Create: `plugins/pirategoat-tools/scripts/agent-registry.json` — canonical registry
- Modify: `plugins/pirategoat-tools/scripts/bootstrap-reviewer.py:33-106` — import from registry
- Create: `plugins/pirategoat-tools/tests/test_agent_registry.py` — registry validation tests
- Modify: `plugins/pirategoat-tools/tests/test_bootstrap_reviewer.py` — verify bootstrap reads registry

**Step 1: Design the JSON schema**

```json
{
  "$schema": "agent-registry-v1",
  "agents": {
    "pr-reviewer": {
      "domain": "code",
      "protocols": ["reviewer"],
      "scope_flags": [],
      "dispatch_class": "always",
      "focus": "Goal alignment, bugs, code quality",
      "model_tier": "inherit"
    },
    "security-reviewer": {
      "domain": "security",
      "secondary_domains": ["config-ops"],
      "protocols": ["reviewer"],
      "scope_flags": [],
      "dispatch_class": "conditional",
      "triage_criteria": [
        "New or modified endpoints accepting external input",
        "Code processing user-supplied data",
        "Database operations",
        "Auth, authorization, or session management changes",
        "File system operations with user-influenced paths",
        "Third-party API or webhook integrations",
        "Cryptographic or secret/token handling",
        "CI/CD configuration or infrastructure changes"
      ],
      "focus": "Sanitization, escaping, nonces, capabilities, SQL injection, data exposure",
      "model_tier": "sonnet"
    }
  }
}
```

Fields per agent:
- `domain` (string): primary domain from review-scope.py DOMAIN_CATALOG
- `secondary_domains` (array, optional): additional domains to include in scope
- `protocols` (array): protocol files to load (["reviewer"] or ["reviewer", "tests-reviewer"])
- `scope_flags` (array): extra flags for review-scope.py
- `dispatch_class` (string): "always" | "conditional" | "manual"
- `triage_criteria` (array, optional): for conditional agents, the dispatch-when rules
- `focus` (string): one-line focus description for dispatch tables
- `model_tier` (string): "inherit" | "sonnet" | "haiku" | "opus"
- `extra_scope` (array, optional): e.g., ["--base-ref-only"] for patterns-reviewer
- `file_history` (boolean, optional): true for history-insights-reviewer

**Step 2: Write registry validation tests**

Create `tests/test_agent_registry.py`:
- All agents in registry have required fields (domain, protocols, dispatch_class, focus)
- All domains reference valid entries in DOMAIN_CATALOG
- All protocol names are valid ("reviewer", "tests-reviewer")
- dispatch_class is one of "always", "conditional", "manual"
- conditional agents have triage_criteria
- Agent count matches expected total (currently 14)
- Registry file is valid JSON

**Step 3: Run tests to verify they fail**

Run: `pytest plugins/pirategoat-tools/tests/test_agent_registry.py -v`
Expected: FAIL (registry file doesn't exist yet)

**Step 4: Create the registry JSON**

Create `scripts/agent-registry.json` with all 14 agents from current AGENT_CONFIG, plus the new fields (dispatch_class, triage_criteria, focus, model_tier). Source data from:
- `bootstrap-reviewer.py` AGENT_CONFIG (lines 33-106) for domain, protocols, scope_flags
- `full-code-review.md` dispatch table (lines 183-197) for focus descriptions
- `full-code-review.md` triage criteria (lines 95-146) for conditional agent rules
- Agent `.md` frontmatter for model_tier

**Step 5: Run registry tests**

Run: `pytest plugins/pirategoat-tools/tests/test_agent_registry.py -v`
Expected: All pass

**Step 6: Refactor bootstrap-reviewer.py to read from registry**

Replace the hardcoded `AGENT_CONFIG` dict (lines 33-106) with a function that loads from `agent-registry.json`:

```python
def load_agent_config() -> Dict[str, dict]:
    registry_path = Path(__file__).parent / "agent-registry.json"
    with open(registry_path) as f:
        registry = json.load(f)
    return registry["agents"]

AGENT_CONFIG = load_agent_config()
```

The rest of `bootstrap-reviewer.py` continues to use `AGENT_CONFIG` as before — the interface doesn't change, only the data source.

**Step 7: Run bootstrap tests**

Run: `pytest plugins/pirategoat-tools/tests/test_bootstrap_reviewer.py -v`
Expected: All pass (AGENT_CONFIG has same structure, just loaded from JSON)

**Step 8: Run all tests**

Run: `pytest plugins/pirategoat-tools/tests/ -v`
Expected: All pass

**Step 9: Update CHANGELOG.md and marketplace.json version**

**Step 10: Commit**

```bash
git add plugins/pirategoat-tools/scripts/agent-registry.json \
       plugins/pirategoat-tools/scripts/bootstrap-reviewer.py \
       plugins/pirategoat-tools/tests/test_agent_registry.py \
       plugins/pirategoat-tools/CHANGELOG.md \
       .claude-plugin/marketplace.json
git commit -m "feat(pirategoat-tools): add canonical agent-registry.json as single source of truth"
```

---

## Task 5: Unified dispatch planner (P1-1)

> Depends on: Task 4 (agent registry)

**Files:**
- Create: `plugins/pirategoat-tools/scripts/plan-review-dispatch.py` — dispatch planner
- Modify: `plugins/pirategoat-tools/commands/full-code-review.md` — thin wrapper calling planner
- Modify: `plugins/pirategoat-tools/commands/code-review.md` — thin wrapper calling planner
- Modify: `plugins/pirategoat-tools/commands/pr-review.md` — reference planner instead of step numbers
- Create: `plugins/pirategoat-tools/tests/test_dispatch_planner.py` — planner tests

**Step 1: Design the planner interface**

```bash
python3 scripts/plan-review-dispatch.py \
  --mode full|incremental|pr \
  --git-range "main..HEAD" \
  --output-dir "/tmp/branch-review-feature-x" \
  [--changed-files-list "file1.py,file2.ts"]  # optional override
```

Output (JSON to stdout):
```json
{
  "mode": "full",
  "git_range": "main..HEAD",
  "output_dir": "/tmp/branch-review-feature-x",
  "scope_summary": { "total_files": 12, "by_domain": {"code": 8, "security": 5, ...} },
  "dispatch": [
    {"agent": "pr-reviewer", "domain": "code", "status": "DISPATCH", "reason": "always"},
    {"agent": "security-reviewer", "domain": "security", "status": "DISPATCH", "reason": "triage: endpoints modified"},
    {"agent": "dead-code-reviewer", "domain": "dead-code", "status": "SKIPPED_TRIAGE", "reason": "no dependency graph changes"},
    {"agent": "a11y-reviewer", "domain": "a11y", "status": "SKIPPED", "reason": "no files in a11y domain"}
  ],
  "agent_signals": [
    "pr-reviewer: STATUS=DISPATCH",
    "security-reviewer: STATUS=DISPATCH (triage: endpoints modified)",
    "dead-code-reviewer: STATUS=SKIPPED_TRIAGE (no dependency graph changes)"
  ]
}
```

**Step 2: Write planner tests**

Create `tests/test_dispatch_planner.py`:
- Given a mock git range and file list, verify correct agent dispatch decisions
- Always-dispatch agents are always included
- Conditional agents are triaged based on file content/patterns
- Agents with no domain files are skipped with correct reason
- Output JSON validates against expected schema
- Agent signals format matches what reconciliator expects

**Step 3: Run tests to verify they fail**

Run: `pytest plugins/pirategoat-tools/tests/test_dispatch_planner.py -v`
Expected: FAIL

**Step 4: Implement the dispatch planner**

Create `scripts/plan-review-dispatch.py` that:
1. Loads agent registry from `agent-registry.json`
2. Runs `review-scope.py --preflight` to get per-domain file counts
3. For each agent:
   - If domain has no files → SKIPPED (no files in domain)
   - If dispatch_class is "always" → DISPATCH
   - If dispatch_class is "conditional" → run triage logic against changed files and commit messages
   - If dispatch_class is "manual" → SKIPPED (manual only)
4. Outputs structured JSON dispatch plan

The triage logic for conditional agents reads `triage_criteria` from the registry and matches against:
- Changed file paths and extensions
- Commit messages (via `git log --oneline <range>`)
- Diff content patterns (via `git diff --stat <range>`)

**Step 5: Run planner tests**

Run: `pytest plugins/pirategoat-tools/tests/test_dispatch_planner.py -v`
Expected: All pass

**Step 6: Refactor full-code-review.md**

Replace steps 3.5-4 (preflight, triage, dispatch table — currently ~120 lines) with:

```markdown
### Step 3: Generate dispatch plan

Run the dispatch planner:
\`\`\`bash
python3 scripts/plan-review-dispatch.py \
  --mode full \
  --git-range "<GIT_RANGE>" \
  --output-dir "<OUTPUT_DIR>"
\`\`\`

Read the JSON output. Display the dispatch summary to the user.

### Step 4: Execute dispatch plan

For each agent with status "DISPATCH" in the plan, dispatch using the Agent tool.
CRITICAL: Dispatch all eligible agents in a SINGLE message with MULTIPLE Agent tool calls.
```

The triage criteria, dispatch table, and skip-reason format are no longer in the command — they live in the registry and planner.

**Step 7: Refactor code-review.md**

Same as Step 6, but:
- Keep the incremental state management (steps 1-2: `.review-state.json`, rebase detection)
- Replace triage/dispatch with planner call using `--mode incremental`
- Keep state persistence step at the end

**Step 8: Refactor pr-review.md**

Replace step-number cross-references with direct planner invocation:
- Phase 1: planner with `--mode pr`
- No more "use `/full-code-review` dispatch (steps 3.5-5)"

**Step 9: Run command tests**

Run: `pytest plugins/pirategoat-tools/tests/test_commands.py -v`
Expected: All pass (may need test updates for new command structure)

**Step 10: Run all tests**

Run: `pytest plugins/pirategoat-tools/tests/ -v`
Expected: All pass

**Step 11: Update CHANGELOG.md and marketplace.json version**

**Step 12: Commit**

```bash
git add plugins/pirategoat-tools/scripts/plan-review-dispatch.py \
       plugins/pirategoat-tools/commands/full-code-review.md \
       plugins/pirategoat-tools/commands/code-review.md \
       plugins/pirategoat-tools/commands/pr-review.md \
       plugins/pirategoat-tools/tests/test_dispatch_planner.py \
       plugins/pirategoat-tools/tests/test_commands.py \
       plugins/pirategoat-tools/CHANGELOG.md \
       .claude-plugin/marketplace.json
git commit -m "feat(pirategoat-tools): unify dispatch orchestration into plan-review-dispatch.py"
```

---

## Task 6: Deterministic reconcile engine (P1-3)

**Files:**
- Create: `plugins/pirategoat-tools/scripts/reconcile-reviews.py` — deterministic dedup/merge
- Modify: `plugins/pirategoat-tools/agents/review-reconciliator.md` — simplify to narrative-only
- Create: `plugins/pirategoat-tools/tests/test_reconcile_reviews.py` — dedup tests
- Modify: `plugins/pirategoat-tools/commands/full-code-review.md` — call script before agent
- Modify: `plugins/pirategoat-tools/commands/code-review.md` — same

**Step 1: Design the reconcile script interface**

```bash
python3 scripts/reconcile-reviews.py \
  --output-dir "/tmp/branch-review-feature-x" \
  --agent-signals "pr-reviewer: STATUS=DISPATCH, security-reviewer: STATUS=DISPATCH, ..."
```

Output: writes `reconciled-structured.json` to output-dir:
```json
{
  "total_findings": 24,
  "deduplicated_findings": 18,
  "clusters": [
    {
      "cluster_id": "C1",
      "findings": ["pr-review:abc123", "security-review:def456"],
      "canonical": {
        "title": "SQL injection in user query",
        "file": "src/db.php",
        "line": 42,
        "severity": "critical",
        "confidence": 0.95,
        "source_agents": ["pr-reviewer", "security-reviewer"],
        "description": "..."
      }
    }
  ],
  "severity_disagreements": [],
  "skipped_agents": ["dead-code-reviewer"],
  "agent_stats": { "pr-reviewer": { "findings": 8, "unique": 5, "duplicated": 3 } }
}
```

**Step 2: Write reconcile tests**

Create `tests/test_reconcile_reviews.py`:

Test categories:
- **Exact dedup:** Same file + same line + same title from two agents → merged into one cluster
- **Near dedup:** Same file + overlapping line range (within 5 lines) + similar title (>70% token overlap) → merged
- **Distinct findings:** Same file but different issues → NOT merged
- **Severity resolution:** Two agents flag same issue at different severities → take highest
- **Source aggregation:** Cluster `source_agents` is union of all contributing agents
- **Schema validation:** Agent output missing required fields → graceful skip with warning
- **Empty input:** No agent output files → empty reconciliation (not crash)
- **Single agent:** Only one agent's output → pass through without clustering

**Step 3: Run tests to verify they fail**

Run: `pytest plugins/pirategoat-tools/tests/test_reconcile_reviews.py -v`
Expected: FAIL

**Step 4: Implement reconcile-reviews.py**

Core algorithm:
1. Read all `*-review.json` files from output-dir
2. Validate each against expected schema (from `review_output_simple.py`)
3. Collect all findings with source agent annotation
4. Cluster findings:
   - Group by file path
   - Within each file, compare pairs: if line ranges overlap (within 5 lines) AND title similarity > 0.7 (Jaccard on words), merge into cluster
   - For each cluster, pick canonical finding: highest severity, highest confidence, longest description
   - Merge `source_agents` lists
5. Resolve severity conflicts: if agents disagree, take highest severity
6. Write `reconciled-structured.json`

Title similarity function:
```python
def title_similarity(a: str, b: str) -> float:
    words_a = set(a.lower().split())
    words_b = set(b.lower().split())
    if not words_a or not words_b:
        return 0.0
    intersection = words_a & words_b
    union = words_a | words_b
    return len(intersection) / len(union)
```

**Step 5: Run reconcile tests**

Run: `pytest plugins/pirategoat-tools/tests/test_reconcile_reviews.py -v`
Expected: All pass

**Step 6: Shadow validation against past reviews**

Find existing review output directories and run the script against them:
```bash
# Find recent review outputs
ls -d /tmp/branch-review-* /tmp/pr-review-* 2>/dev/null

# Run reconcile script against a real output
python3 plugins/pirategoat-tools/scripts/reconcile-reviews.py \
  --output-dir /tmp/branch-review-<some-branch> \
  --agent-signals "all dispatched"

# Compare reconciled-structured.json against existing reconciled.json
# Manual inspection: are clusters sensible? Any false merges? Any missed duplicates?
```

**Step 7: Simplify review-reconciliator.md**

Replace the current prompt (which does both dedup and narrative) with a narrative-only prompt:
- Read `reconciled-structured.json` (pre-deduped, pre-clustered)
- Write executive summary: overall verdict, top issues, cross-validation insights
- Write `reconciled.json` (using ReviewOutputBuilder) and `reconciled.md`
- Do NOT re-sort, re-dedup, or re-classify findings — trust the script output

**Step 8: Update command files**

In `full-code-review.md` and `code-review.md`, update the reconciliation step to:
1. Run `reconcile-reviews.py` first (deterministic preprocessing)
2. Then dispatch reconciliator agent for narrative summary

**Step 9: Run all tests**

Run: `pytest plugins/pirategoat-tools/tests/ -v`
Expected: All pass

**Step 10: Update CHANGELOG.md and marketplace.json version**

**Step 11: Commit**

```bash
git add plugins/pirategoat-tools/scripts/reconcile-reviews.py \
       plugins/pirategoat-tools/agents/review-reconciliator.md \
       plugins/pirategoat-tools/commands/full-code-review.md \
       plugins/pirategoat-tools/commands/code-review.md \
       plugins/pirategoat-tools/tests/test_reconcile_reviews.py \
       plugins/pirategoat-tools/CHANGELOG.md \
       .claude-plugin/marketplace.json
git commit -m "feat(pirategoat-tools): add deterministic reconcile engine with dedup clustering"
```

---

## Task 7: Deterministic ingest preprocessor (P1-4)

**Files:**
- Create: `plugins/pirategoat-tools/scripts/ingest-preprocess.py` — deterministic scope/classification
- Modify: `plugins/pirategoat-tools/scripts/ingest-code-review.py` — reduce from 6 to 3 steps
- Modify: `plugins/pirategoat-tools/commands/ingest-code-review.md` — update step references
- Create: `plugins/pirategoat-tools/tests/test_ingest_preprocess.py` — preprocessor tests

**Step 1: Design the preprocessor interface**

```bash
python3 scripts/ingest-preprocess.py \
  --output-dir "/tmp/branch-review-feature-x" \
  --git-range "main..HEAD"
```

Reads: `reconciled-structured.json` (from Task 6) or `reconciled.json` (fallback)
Writes: `ingest-preprocessed.json` to output-dir:

```json
{
  "git_range": "main..HEAD",
  "changed_files": ["src/db.php", "src/api.ts", ...],
  "findings": [
    {
      "id": "F1",
      "title": "SQL injection in user query",
      "file": "src/db.php",
      "line": 42,
      "severity": "critical",
      "source_agents": ["pr-reviewer", "security-reviewer"],
      "confidence": 0.95,
      "scope_status": "IN_SCOPE",
      "scope_reason": "file in diff, line in hunk",
      "pre_classification": "needs_verification"
    },
    {
      "id": "F2",
      "title": "Unused import",
      "file": "src/old.py",
      "line": 1,
      "severity": "low",
      "source_agents": ["dead-code-reviewer"],
      "confidence": 0.8,
      "scope_status": "OUT_OF_SCOPE",
      "scope_reason": "file not in changed files",
      "pre_classification": "out_of_scope"
    }
  ],
  "summary": {
    "total": 24,
    "in_scope": 18,
    "out_of_scope": 4,
    "needs_verification": 14,
    "auto_classified": 10
  }
}
```

**Step 2: Write preprocessor tests**

Create `tests/test_ingest_preprocess.py`:

Test categories:
- **Scope check — file in diff:** Finding references a file in changed_files → IN_SCOPE
- **Scope check — file not in diff:** Finding references a file NOT in changed_files → OUT_OF_SCOPE
- **Scope check — line in hunk:** Finding line falls within a diff hunk → IN_SCOPE
- **Scope check — line outside hunk:** Finding line is outside all hunks (pre-existing code) → OUT_OF_SCOPE
- **Scope check — no line number:** Finding has no line → IN_SCOPE if file in diff (conservative)
- **Stable IDs:** Findings get sequential IDs (F1, F2, ...) in consistent order
- **Pre-classification:** IN_SCOPE findings → "needs_verification"; OUT_OF_SCOPE → "out_of_scope"
- **Edge case — empty findings:** No findings → valid empty output
- **Edge case — multi-hunk diff:** File with multiple hunks, finding in second hunk → IN_SCOPE

**Step 3: Run tests to verify they fail**

Run: `pytest plugins/pirategoat-tools/tests/test_ingest_preprocess.py -v`
Expected: FAIL

**Step 4: Implement ingest-preprocess.py**

Core logic:
1. Read reconciled findings from output-dir
2. Get changed files: `git diff --name-only <git-range>`
3. For each finding:
   a. Assign stable ID: F1, F2, ... (sorted by severity desc, then file, then line)
   b. Scope check 1: is `finding.file` in changed_files? No → OUT_OF_SCOPE
   c. Scope check 2: is `finding.line` in a diff hunk?
      - Run `git diff <git-range> -- <file>` and parse hunk headers (`@@ -a,b +c,d @@`)
      - If line falls within any hunk's range → IN_SCOPE
      - If line is outside all hunks → OUT_OF_SCOPE (pre-existing)
      - If finding has no line → IN_SCOPE (conservative, if file is in diff)
   d. Pre-classify: IN_SCOPE → "needs_verification"; OUT_OF_SCOPE → "out_of_scope"
4. Write `ingest-preprocessed.json`

Hunk parser:
```python
def parse_diff_hunks(diff_output: str) -> List[Tuple[int, int]]:
    """Parse @@ -a,b +c,d @@ headers into (start, end) line ranges for new file."""
    hunks = []
    for match in re.finditer(r'@@ -\d+(?:,\d+)? \+(\d+)(?:,(\d+))? @@', diff_output):
        start = int(match.group(1))
        count = int(match.group(2)) if match.group(2) else 1
        hunks.append((start, start + count - 1))
    return hunks
```

**Step 5: Run preprocessor tests**

Run: `pytest plugins/pirategoat-tools/tests/test_ingest_preprocess.py -v`
Expected: All pass

**Step 6: Refactor ingest-code-review.py**

Reduce from 6 steps to 3:

- **Old Step 1 (Locate & Initialize)** → handled by preprocessor
- **Old Step 2 (Parse & Assign IDs)** → handled by preprocessor
- **Old Step 3 (Classify Scope)** → handled by preprocessor
- **New Step 1 = Old Step 4 (Generate Verification Questions)** — LLM reads `ingest-preprocessed.json`, generates questions only for findings with `scope_status: "IN_SCOPE"` and `pre_classification: "needs_verification"`
- **New Step 2 = Old Step 5 (Factored Verification)** — LLM answers questions via code reading
- **New Step 3 = Old Step 6 (Categorize & Build Action Plan)** — LLM applies decision table, builds action plan

Update `get_step_guidance()` to emit guidance for 3 steps instead of 6. Each step's guidance now references `ingest-preprocessed.json` for pre-computed scope and IDs instead of instructing the LLM to compute them.

**Step 7: Update ingest-code-review.md**

Update the command to:
1. Run `ingest-preprocess.py` first
2. Then run `ingest-code-review.py` with `--total-steps 3`

**Step 8: Run all tests**

Run: `pytest plugins/pirategoat-tools/tests/ -v`
Expected: All pass

**Step 9: Update CHANGELOG.md and marketplace.json version**

**Step 10: Commit**

```bash
git add plugins/pirategoat-tools/scripts/ingest-preprocess.py \
       plugins/pirategoat-tools/scripts/ingest-code-review.py \
       plugins/pirategoat-tools/commands/ingest-code-review.md \
       plugins/pirategoat-tools/tests/test_ingest_preprocess.py \
       plugins/pirategoat-tools/CHANGELOG.md \
       .claude-plugin/marketplace.json
git commit -m "feat(pirategoat-tools): add deterministic ingest preprocessor, reduce LLM steps from 6 to 3"
```

---

## Task 8: Reliability-reviewer agent (P2-2)

> Depends on: Task 4 (agent registry)

**Files:**
- Create: `plugins/pirategoat-tools/agents/reliability-reviewer.md` — new agent
- Modify: `plugins/pirategoat-tools/scripts/agent-registry.json` — register agent
- Modify: `.claude-plugin/marketplace.json` — register agent path
- Modify: `plugins/pirategoat-tools/commands/full-code-review.md` — add to conditional triage
- Modify: `plugins/pirategoat-tools/commands/code-review.md` — same

**Step 1: Define the reliability domain**

Add `reliability` domain to `review-scope.py` DOMAIN_CATALOG. The reliability domain should match broadly — same as `code` domain plus config-ops, since reliability concerns span production code and infrastructure:

```python
"reliability": {
    "include": r"\.(php|js|ts|jsx|tsx|css|scss|py|java|rb|go|sql)$",
    "exclude": r"(tests?/|__tests__/|__mocks__/|spec/|\.test\.|\.spec\.|Test\.php$)",
},
```

Note: Intentionally same as production code domains minus test files. Reliability concerns apply to production code. Config-ops files are covered separately via secondary_domains.

**Step 2: Create the agent file**

Create `agents/reliability-reviewer.md` following the standard template (see `security-reviewer.md` for reference):

Frontmatter:
```yaml
---
name: reliability-reviewer
description: Operational resilience code review for logging, error handling, rollback safety, feature flags, and failure-mode handling
model: sonnet
color: orange
tools:
  - Read
  - Glob
  - Grep
  - Bash
  - Write
  - WebSearch
---
```

Content structure:
- Expert intro: operational resilience and reliability engineer
- RULE 0: "Every production code path must have an observable failure mode"
- Core mission: review for operational resilience gaps
- Category framework:
  - CRITICAL: Missing error handling on external service calls, database migrations without rollback, silent data corruption
  - HIGH: Missing logging on state transitions, no timeout on external calls, missing circuit breakers
  - MEDIUM: Missing feature flags on risky changes, no health check endpoints, missing metrics/alerts
  - LOW: Inconsistent error message formats, missing structured logging fields
- Checklists:
  - Database migrations: rollback script? backwards compatible? feature flagged?
  - External service calls: timeout? retry policy? circuit breaker? fallback?
  - Feature rollout: feature flag? kill switch? gradual rollout?
  - Error handling: logged? alertable? recoverable? user-facing message appropriate?
  - Observability: metrics emitted? dashboards exist? alerts configured?
- Confidence scoring: same pattern as other agents

**Step 3: Register in agent-registry.json**

Add to `scripts/agent-registry.json`:
```json
"reliability-reviewer": {
  "domain": "reliability",
  "secondary_domains": ["config-ops"],
  "protocols": ["reviewer"],
  "scope_flags": [],
  "dispatch_class": "conditional",
  "triage_criteria": [
    "Database migrations or schema changes",
    "External service integrations or API client changes",
    "Error handling or retry logic modifications",
    "Feature flag or kill-switch changes",
    "Deployment configuration or infrastructure changes",
    "Background job or queue processing changes",
    "Caching layer modifications"
  ],
  "focus": "Logging, error handling, rollback safety, feature flags, failure-mode resilience",
  "model_tier": "sonnet"
}
```

**Step 4: Register in marketplace.json**

Add `"./agents/reliability-reviewer.md"` to the pirategoat-tools agents array in `.claude-plugin/marketplace.json`.

**Step 5: Add triage criteria to command files**

Add reliability-reviewer to the conditional agent triage section in `full-code-review.md` and `code-review.md` (or if P1-1 is done, add to the registry's triage_criteria which the planner reads).

**Step 6: Run all tests**

Run: `pytest plugins/pirategoat-tools/tests/ -v`
Expected: All pass (new agent is automatically included in parameterized bootstrap tests)

**Step 7: Update CHANGELOG.md and marketplace.json version**

**Step 8: Commit**

```bash
git add plugins/pirategoat-tools/agents/reliability-reviewer.md \
       plugins/pirategoat-tools/scripts/agent-registry.json \
       plugins/pirategoat-tools/scripts/review-scope.py \
       .claude-plugin/marketplace.json \
       plugins/pirategoat-tools/CHANGELOG.md
git commit -m "feat(pirategoat-tools): add reliability-reviewer agent for operational resilience"
```

---

## Task 9: Test adequacy advisory (P2-3)

> Depends on: Task 5 (dispatch planner) or Task 6 (reconcile engine) for integration point

**Files:**
- Modify: `plugins/pirategoat-tools/scripts/reconcile-reviews.py` or `plan-review-dispatch.py` — add test gap detection
- Modify: `plugins/pirategoat-tools/tests/test_reconcile_reviews.py` or `test_dispatch_planner.py` — test gap tests

**Step 1: Implement test gap detection**

Add a function (in whichever script is the better integration point — reconcile if post-review, planner if pre-review):

```python
PRODUCTION_DOMAINS = ["code", "security", "performance", "architecture",
                      "wp-architecture", "dead-code", "patterns", "a11y",
                      "config-ops", "reliability"]
TEST_DOMAINS = ["php-tests", "js-tests", "e2e-tests", "go-tests"]

def detect_test_gap(changed_files: List[str], domain_catalog: dict) -> Optional[dict]:
    """Detect if production code changed without corresponding test changes."""
    production_changed = any(
        re.search(domain_catalog[d]["include"], f)
        for f in changed_files
        for d in PRODUCTION_DOMAINS
        if d in domain_catalog
    )
    tests_changed = any(
        re.search(domain_catalog[d]["include"], f)
        for f in changed_files
        for d in TEST_DOMAINS
        if d in domain_catalog
    )
    if production_changed and not tests_changed:
        prod_files = [f for f in changed_files if any(
            re.search(domain_catalog[d]["include"], f)
            for d in PRODUCTION_DOMAINS if d in domain_catalog
        )]
        return {
            "type": "advisory",
            "severity": "info",
            "title": "Production code changed without corresponding tests",
            "description": f"{len(prod_files)} production files changed but no test files were modified.",
            "production_files": prod_files,
        }
    return None
```

**Step 2: Write tests**

- Production files changed, no test files → advisory emitted
- Production files changed, test files also changed → no advisory
- Only test files changed → no advisory
- Only config/docs changed (no production domain match) → no advisory
- Mixed: some production, some test, some config → no advisory (tests present)

**Step 3: Run tests**

Run: `pytest plugins/pirategoat-tools/tests/test_reconcile_reviews.py -v` (or whichever test file)
Expected: All pass

**Step 4: Integrate into pipeline output**

The advisory appears as an informational note in reconciled output — not a finding, not a severity rating. Just a signal.

**Step 5: Update CHANGELOG.md and marketplace.json version**

**Step 6: Commit**

```bash
git add plugins/pirategoat-tools/scripts/reconcile-reviews.py \
       plugins/pirategoat-tools/tests/test_reconcile_reviews.py \
       plugins/pirategoat-tools/CHANGELOG.md \
       .claude-plugin/marketplace.json
git commit -m "feat(pirategoat-tools): add advisory test-gap detection for production changes without tests"
```

---

## Summary

| Task | Priority | Depends On | Key Deliverable |
|------|----------|------------|-----------------|
| 1. Fix doc/policy consistency | P0 | — | Correct agent counts across all files |
| 2. Config/CI/infra scope | P0 | — | `config-ops` domain + agent extensions |
| 3. Quality metrics | P0 | — | `--quality-metrics` mode in session analysis |
| 4. Agent registry JSON | P1 | — | `agent-registry.json` as single source of truth |
| 5. Dispatch planner | P1 | Task 4 | `plan-review-dispatch.py` replaces duplicated command logic |
| 6. Reconcile engine | P1 | — | `reconcile-reviews.py` for deterministic dedup |
| 7. Ingest preprocessor | P1 | — | `ingest-preprocess.py` + 6→3 step reduction |
| 8. Reliability-reviewer | P2 | Task 4 | New agent for operational resilience |
| 9. Test adequacy advisory | P2 | Task 5 or 6 | Informational test-gap detection |

**Parallel execution paths:**
- P0: Tasks 1, 2, 3 — all independent
- P1: Task 4 first → then Tasks 5, 6, 7 in parallel
- P2: Tasks 8, 9 after their dependencies
