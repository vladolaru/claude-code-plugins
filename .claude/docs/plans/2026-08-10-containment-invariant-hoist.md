# Pipeline-Wide Containment Invariant Hoist Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use $subagent-driven-development (recommended) or $executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Move containment ownership to the shared `scripts/` root and make one allowlist-free drift guard cover every containment decision in the plugin.

**Architecture:** `scripts/containment.py` will own filesystem-resolved containment plus a distinct POSIX-lexical primitive for telemetry's recorded-path grammar. Advisory host resolvers, repo-contributed review configuration, and telemetry keep their caller-specific failure policies while importing the shared decision. The root-level contract test scans every Python file under `scripts/` and exempts only the exact shared module path.

**Tech Stack:** Python 3 standard library (`os.path`, `posixpath`), pytest, Git.

---

### Task 1: Relocate the existing containment contract

**Files:**
- Create: `plugins/pirategoat-tools/scripts/containment.py`
- Delete: `plugins/pirategoat-tools/scripts/hosts/containment.py`
- Create: `plugins/pirategoat-tools/tests/test_containment_contract.py`
- Delete: `plugins/pirategoat-tools/tests/hosts/test_containment_contract.py`
- Modify: `plugins/pirategoat-tools/scripts/hosts/resolvers/docker_compose.py`
- Modify: `plugins/pirategoat-tools/scripts/hosts/resolvers/explicit.py`
- Modify: `plugins/pirategoat-tools/scripts/hosts/resolvers/wp_env.py`

- [x] **Step 1: Move the behavioral contract test and point it at the desired root module**

Preserve every existing behavioral test body, change the module-level wording from a hosts-only invariant to a pipeline-wide invariant, adjust the still-host-scoped guard root for the test's shallower location (`Path(__file__).parents[1] / "scripts" / "hosts"`), and import the unchanged API from the root:

```python
from containment import contains, contains_lexically, resolve_inside
```

- [x] **Step 2: Run the relocated contract to verify the desired import fails**

Run:

```bash
pytest plugins/pirategoat-tools/tests/test_containment_contract.py -v
```

Expected: collection fails because `scripts/containment.py` does not exist yet.

- [x] **Step 3: Relocate the module without changing any existing primitive**

Keep these bodies byte-for-byte equivalent:

```python
def contains(repo_path: str, candidate: str) -> bool:
    return _is_prefix(os.path.realpath(repo_path), os.path.realpath(candidate))


def contains_lexically(repo_path: str, candidate: str) -> bool:
    return _is_prefix(os.path.normpath(repo_path), os.path.normpath(candidate))


def resolve_inside(repo_path: str, rel_path: str) -> Optional[str]:
    real_root = os.path.realpath(repo_path)
    resolved = os.path.realpath(os.path.join(real_root, rel_path))
    return resolved if _is_prefix(real_root, resolved) else None


def _is_prefix(root: str, candidate: str) -> bool:
    try:
        return os.path.commonpath([root, candidate]) == root
    except ValueError:
        return False
```

Rewrite only the module docstring: state the pipeline-wide invariant, name advisory host resolution and repo-declared execution-gating paths, and warn that lexical checks must never authorize a read or execution.

- [x] **Step 4: Update the three resolver imports**

Use the same root-module style as `git_paths.py`:

```python
from containment import contains
```

- [x] **Step 5: Run the relocated contract and hosts suite**

Run:

```bash
pytest plugins/pirategoat-tools/tests/test_containment_contract.py -v
pytest plugins/pirategoat-tools/tests/hosts/ -v
```

Expected: both commands pass; the relocated test retains the symlink escape, in-repo symlink, repo-via-symlink, prefix-sibling, and mixed lexical forms.

- [x] **Step 6: Exercise the standalone host entrypoint**

Run:

```bash
python3 plugins/pirategoat-tools/scripts/hosts/host_context.py --help
```

Expected: exit 0 with argparse help, proving the bare root import resolves when run as a script.

- [x] **Step 7: Commit the relocation**

Stage only the module move, test move, and resolver imports, then commit:

```bash
git commit -m "refactor(containment): hoist the invariant to the scripts root"
```

The body must explain that host resolution is now only one consumer class and that every existing primitive is relocated unchanged.

### Task 2: Route the repo-declared execution gate through the shared primitive

**Files:**
- Modify: `plugins/pirategoat-tools/scripts/review/review_config.py`
- Modify: `plugins/pirategoat-tools/tests/review/test_review_config.py`
- Modify: `plugins/pirategoat-tools/tests/test_containment_contract.py` (leave unstaged until Task 3)

- [x] **Step 1: Add reviewer-ref boundary characterization tests**

Add separate tests under `TestSecurityHardening` for traversal and symlink escape:

```python
def test_reviewer_ref_traversal_escape_is_dropped(self, mod, tmp_path):
    outside = tmp_path.parent / "outside-reviewer.md"
    outside.write_text("review instructions")
    _write_config(tmp_path, {"review": {"reviewers": [
        {"id": "escape", "ref": "../outside-reviewer.md"}
    ]}})

    result = mod.load_review_config(str(tmp_path), changed_files=[])

    assert result["reviewers"] == []
    assert any("escape" in item and "escapes" in item for item in result["diagnostics"])


def test_reviewer_ref_symlink_escape_is_dropped(self, mod, tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    outside = tmp_path / "outside-reviewer.md"
    outside.write_text("review instructions")
    (repo / "reviewer.md").symlink_to(outside)
    _write_config(repo, {"review": {"reviewers": [
        {"id": "escape", "ref": "reviewer.md"}
    ]}})

    result = mod.load_review_config(str(repo), changed_files=[])

    assert result["reviewers"] == []
    assert any("escape" in item and "escapes" in item for item in result["diagnostics"])
```

- [x] **Step 2: Run the characterization tests before refactoring**

Run:

```bash
pytest plugins/pirategoat-tools/tests/review/test_review_config.py::TestSecurityHardening -v
```

Expected: the new tests pass against the existing duplicate, establishing the no-behavior-change baseline.

- [x] **Step 3: Widen the drift guard first and verify RED**

Change the guard root to `Path(__file__).parents[1] / "scripts"`, scan `rglob("*.py")`, and exempt only `scripts_dir / "containment.py"` by exact path. Keep the same banned spellings.

Run:

```bash
pytest plugins/pirategoat-tools/tests/test_containment_contract.py::TestDriftGuard -v
```

Expected: FAIL listing `review/review_config.py: commonpath` and `review/telemetry.py: commonpath`.

- [x] **Step 4: Replace the review-config duplicate**

Add beside the existing shared root import:

```python
from containment import contains
from git_paths import decode_git_c_quoted_path
```

Replace both `_path_inside_repo(path, repo_path)` calls with `contains(repo_path, path)`, update the nearby comment, and delete `_path_inside_repo` completely. Do not add an import fallback: a missing import must fail module loading rather than widen trust.

- [x] **Step 5: Verify the review-config gate**

Run:

```bash
pytest plugins/pirategoat-tools/tests/review/test_review_config.py -v
python3 plugins/pirategoat-tools/scripts/review/context.py --help
PYTHONPATH=plugins/pirategoat-tools/scripts python3 -c 'from review import context; assert context._HOSTS_CHAIN is not None; assert context._REVIEW_CONFIG_LOADER is not None'
```

Expected: pytest passes and both direct import exercises exit 0 without tripping either soft fallback.

- [x] **Step 6: Commit the review gate migration**

Stage only `review_config.py` and `test_review_config.py`, then commit:

```bash
git commit -m "refactor(review): share repo path containment"
```

The body must state that traversal, symlink resolution, and `ValueError -> False` behavior remain unchanged while execution-gating declarations now use the shared primitive.

### Task 3: Centralize telemetry's POSIX lexical decision and finish the drift guard

**Files:**
- Modify: `plugins/pirategoat-tools/scripts/containment.py`
- Modify: `plugins/pirategoat-tools/scripts/review/telemetry.py`
- Modify: `plugins/pirategoat-tools/tests/test_containment_contract.py`
- Test: `plugins/pirategoat-tools/tests/review/test_telemetry.py`

- [x] **Step 1: Add a failing POSIX lexical primitive contract**

Import the module rather than the missing symbol at collection time and add:

```python
import containment


class TestContainsPosixLexically:
    def test_normalizes_posix_recorded_paths_without_filesystem_access(self):
        assert containment.contains_posix_lexically(
            "/recorded/repo", "/recorded/repo/missing/../src/file.py"
        )
        assert not containment.contains_posix_lexically(
            "/recorded/repo", "/recorded/repo-sibling/file.py"
        )

    def test_mixed_forms_fail_closed(self):
        assert not containment.contains_posix_lexically(
            "recorded/repo", "/recorded/repo/file.py"
        )
```

Run:

```bash
pytest plugins/pirategoat-tools/tests/test_containment_contract.py::TestContainsPosixLexically -v
```

Expected: FAIL with `AttributeError` because the primitive does not exist.

- [x] **Step 2: Add the POSIX lexical primitive**

Use `posixpath`, not `os.path`, and preserve telemetry's current fail-closed policy:

```python
def contains_posix_lexically(root: str, candidate: str) -> bool:
    """Pure POSIX-lexical containment for recorded path spellings."""
    normalized_root = posixpath.normpath(root)
    normalized_candidate = posixpath.normpath(candidate)
    try:
        return posixpath.commonpath(
            [normalized_root, normalized_candidate]
        ) == normalized_root
    except ValueError:
        return False
```

Document that the primitive does not resolve symlinks or establish filesystem trust.

- [x] **Step 3: Route telemetry through the new primitive**

Add:

```python
from containment import contains_posix_lexically
```

Keep `normalized_root`, `normalized_absolute`, and `posixpath.relpath`, replacing only the inline prefix decision:

```python
if not contains_posix_lexically(normalized_root, normalized_absolute):
    return None
```

- [x] **Step 4: Verify telemetry behavior and the global guard**

Run:

```bash
pytest plugins/pirategoat-tools/tests/test_containment_contract.py -v
pytest plugins/pirategoat-tools/tests/review/test_telemetry.py -v
```

Expected: both pass; the guard reports no banned spelling outside the exact shared module and the existing sanitizer test still accepts a nonexistent in-repo absolute spelling while rejecting sibling/traversal/drive paths.

- [x] **Step 5: Commit telemetry centralization and the widened guard**

```bash
git commit -m "refactor(containment): centralize POSIX lexical decisions"
```

The body must explicitly justify option (a): telemetry normalizes recorded POSIX evidence without touching the filesystem; the OS-native lexical primitive is not portable for that contract; the dedicated primitive gives the telemetry spelling a real caller and keeps the global guard allowlist-free.

### Task 4: Document and release the pipeline-wide invariant

**Files:**
- Modify: `plugins/pirategoat-tools/AGENTS.md`
- Modify: `plugins/pirategoat-tools/CHANGELOG.md`
- Modify: `AGENTS.md`
- Modify: `.claude/docs/analysis/2026-08-10-codex-containment-invariant.md`
- Create: `.claude/docs/plans/2026-08-10-containment-invariant-hoist.md`

- [x] **Step 1: Move the invariant documentation out of the reviewer-only subsection**

Add a pipeline-wide invariant near the shared architecture/key-file documentation. Name `scripts/containment.py`, advisory host resolution, repo-declared paths gating execution, telemetry's POSIX-only lexical caller, and the all-`scripts/**/*.py` drift guard. Remove the obsolete "Hosts containment invariant" bullet under repo-contributed reviewers.

- [x] **Step 2: Fold the refactor into 1.114.0**

Add one `### Changed` bullet following Context → Problem → Solution. State that this is a relocation with no existing behavior change, while telemetry's existing POSIX lexical decision is now a named shared primitive. Do not edit the marketplace version.

- [x] **Step 3: Add the shared module to the root testing table**

Add a row mapping `scripts/containment.py` to the root contract, host resolver suite, review-config suite, and telemetry suite. The relocated test file itself needs no changed-file row; the table maps production files to required verification.

- [x] **Step 4: Verify documentation references**

Run:

```bash
rg -n 'scripts/hosts/containment.py|tests/hosts/test_containment_contract.py|Hosts containment invariant' plugins/pirategoat-tools/AGENTS.md plugins/pirategoat-tools/scripts plugins/pirategoat-tools/tests
```

Expected: no live-code/current-invariant references. Historical changelog references remain untouched.

- [x] **Step 5: Commit documentation**

```bash
git commit -m "docs(containment): record the pipeline-wide invariant"
```

The commit includes the required working analysis and implementation plan artifacts.

### Task 5: Final verification and handoff

**Files:**
- Verify: all files changed by Tasks 1–4

- [x] **Step 1: Run the requested targeted suites**

```bash
pytest plugins/pirategoat-tools/tests/hosts/ -v
pytest plugins/pirategoat-tools/tests/review/test_review_config.py -v
pytest plugins/pirategoat-tools/tests/review/test_telemetry.py -v
```

- [x] **Step 2: Run the full pirategoat-tools suite exactly as requested**

```bash
pytest plugins/pirategoat-tools/tests/ 2>&1 | tail -30
```

- [x] **Step 3: Check generated Codex compatibility**

```bash
python3 scripts/generate_codex_compat.py --check
```

- [x] **Step 4: Directly exercise both standalone entrypoints and soft-fallback sentinels**

```bash
python3 plugins/pirategoat-tools/scripts/hosts/host_context.py --help
python3 plugins/pirategoat-tools/scripts/review/context.py --help
PYTHONPATH=plugins/pirategoat-tools/scripts python3 -c 'from review import context; assert context._HOSTS_CHAIN is not None; assert context._REVIEW_CONFIG_LOADER is not None; print("host and review-config imports active")'
```

- [x] **Step 5: Audit the final diff, commits, and range**

```bash
git status --short
git diff e86d7ba9...HEAD --check
git log --oneline e86d7ba9...HEAD
```

Report `e86d7ba9...<last-commit>` as the git range, the exact test summaries and exit codes, the option (a) rationale, unchanged filesystem semantics, and the two inventory differences recorded in the analysis.
