# Review Boundary Hardening Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use $subagent-driven-development (recommended) or $executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Resolve the five review findings while consolidating Git path decoding and preserving the repository's provenance and worktree-containment invariants.

**Architecture:** Declared repository paths and resolved filesystem targets remain separate identities: trust checks inspect both, while staging reads the resolved source and writes to the declared relative location. A shared stdlib Git C-quote decoder owns grammar only; each caller preserves its own fail-closed policy. Composer vendor, bin, and cache writes all land in the cache transaction, and a cross-language drift test locks the host-banner vocabulary.

**Tech Stack:** Python 3 stdlib, pytest, TypeScript declaration text, Git, deterministic Codex compatibility generator.

---

### Task 1: Gate symlinked reviewer refs on their targets

**Files:**
- Modify: `plugins/pirategoat-tools/tests/review/test_review_config.py`
- Modify: `plugins/pirategoat-tools/scripts/review/review_config.py`

- [ ] **Step 1: Write the failing reviewer-target provenance test**

```python
def test_symlinked_reviewer_gates_on_the_target(self, mod, tmp_path):
    _touch(tmp_path, "docs/target.md")
    (tmp_path / "reviewer-link.md").symlink_to(
        tmp_path / "docs" / "target.md"
    )
    _write_config(tmp_path, {"review": {
        "reviewers": [{"id": "x", "ref": "reviewer-link.md"}],
    }})

    result = mod.load_review_config(
        str(tmp_path), changed_files=["docs/target.md"]
    )

    assert result["reviewers"] == []
    assert result["untrusted"][0]["kind"] == "reviewer"
```

- [ ] **Step 2: Run it and confirm the current gate trusts the reviewer**

Run: `pytest plugins/pirategoat-tools/tests/review/test_review_config.py::TestProvenanceGate::test_symlinked_reviewer_gates_on_the_target -v`

Expected: FAIL because `result["reviewers"]` contains reviewer `x`.

- [ ] **Step 3: Derive the normalized resolved field from the declaration field**

```python
def _gate(entry, kind, file_field):
    rel_path = str(entry.get(file_field, "")).replace(os.sep, "/")
    identities = _provenance_rel_paths(
        rel_path, entry.get(f"resolved_{file_field}") or "", repo_real
    )
```

- [ ] **Step 4: Run the complete review-config suite**

Run: `pytest plugins/pirategoat-tools/tests/review/test_review_config.py -v`

Expected: PASS.

### Task 2: Preserve declared destinations for staged symlinks

**Files:**
- Modify: `plugins/pirategoat-tools/tests/hosts/install/test_staging.py`
- Modify: `plugins/pirategoat-tools/scripts/hosts/install/staging.py`

- [ ] **Step 1: Add failing manifest and patch symlink tests**

```python
def test_symlinked_manifest_keeps_its_declared_path(repo, cache):
    _write(repo / "config/package.json", "{}")
    (repo / "package.json").symlink_to(repo / "config/package.json")
    _write(repo / "package-lock.json", "{}")

    stage_inputs("npm", str(repo), str(cache))

    assert (cache / "package.json").is_file()
    assert not (cache / "config/package.json").exists()


def test_symlinked_patch_keeps_its_declared_path(repo, cache):
    _write(repo / "package.json", json.dumps({
        "pnpm": {"patchedDependencies": {"pkg@1": "patches/pkg.patch"}}
    }))
    _write(repo / "pnpm-lock.yaml", "")
    _write(repo / "patch-targets/pkg.patch", "patch")
    (repo / "patches").mkdir()
    (repo / "patches/pkg.patch").symlink_to(repo / "patch-targets/pkg.patch")

    stage_inputs("pnpm", str(repo), str(cache))

    assert (cache / "patches/pkg.patch").read_text() == "patch"
    assert not (cache / "patch-targets/pkg.patch").exists()
```

- [ ] **Step 2: Run both tests and confirm files appear under resolved target paths**

Run: `pytest plugins/pirategoat-tools/tests/hosts/install/test_staging.py -k 'symlinked' -v`

Expected: FAIL on the declared destination assertions.

- [ ] **Step 3: Resolve source and destination independently**

```python
src = _resolve_staged_source(repo_path, rel_path)
if src is None:
    return False
dest = resolve_inside(cache_dir, rel_path)
if dest is None:
    return False
os.makedirs(os.path.dirname(dest), exist_ok=True)
shutil.copy2(src, dest)
```

- [ ] **Step 4: Run staging and containment suites**

Run: `pytest plugins/pirategoat-tools/tests/hosts/install/test_staging.py plugins/pirategoat-tools/tests/hosts/test_containment_contract.py -v`

Expected: PASS.

### Task 3: Redirect Composer cache writes

**Files:**
- Modify: `plugins/pirategoat-tools/tests/hosts/install/test_composer_in_place.py`
- Modify: `plugins/pirategoat-tools/tests/hosts/test_containment_contract.py`
- Modify: `plugins/pirategoat-tools/scripts/hosts/ensure_installed.py`

- [ ] **Step 1: Add a failing explicit cache redirect test**

```python
def test_cache_dir_is_redirected_outside_the_repo(nested_repo):
    captured = {}

    def fake_run(cmd, **kwargs):
        captured["env"] = kwargs["env"]
        Path(kwargs["env"]["COMPOSER_VENDOR_DIR"]).mkdir(
            parents=True, exist_ok=True
        )
        return subprocess.CompletedProcess(cmd, 0, "", "")

    with mock.patch(
        "hosts.ensure_installed.subprocess.run", side_effect=fake_run
    ):
        _handle_dep_root(
            DepRoot("composer", "plugins/woocommerce"),
            str(nested_repo),
            [],
        )

    cache_dir = captured["env"].get("COMPOSER_CACHE_DIR")
    assert cache_dir
    assert os.path.isabs(cache_dir)
    assert not cache_dir.startswith(str(nested_repo) + os.sep)
```

- [ ] **Step 2: Extend the end-to-end fake to honor relative `config.cache-dir`**

Replace the root Composer fixture with:

```python
(repo / "composer.json").write_text(json.dumps({
    "config": {"bin-dir": "bin", "cache-dir": ".composer-cache"},
}))
```

Then extend the Composer branch in `fake_run` after loading `config`:

```python
configured_cache = config.get("config", {}).get("cache-dir")
cache_dir = env.get("COMPOSER_CACHE_DIR") or (
    os.path.join(cwd, configured_cache) if configured_cache
    else os.path.expanduser("~/.cache/composer")
)
os.makedirs(cache_dir, exist_ok=True)
Path(cache_dir, "packages.json").write_text("{}")
```

The unchanged tree snapshot must fail before the production fix.

- [ ] **Step 3: Run the focused Composer and immutability tests and observe both failures**

Run: `pytest plugins/pirategoat-tools/tests/hosts/install/test_composer_in_place.py plugins/pirategoat-tools/tests/hosts/test_containment_contract.py::TestWorktreeImmutability -v`

Expected: FAIL because `COMPOSER_CACHE_DIR` is absent and `.composer-cache` changes the snapshot.

- [ ] **Step 4: Redirect Composer's cache into the staging transaction**

```python
staging_root = str(staging_path)
vendor_dir = os.path.join(staging_root, "vendor")
install_env = _build_subprocess_env({
    **(env or {}),
    "COMPOSER_VENDOR_DIR": vendor_dir,
    "COMPOSER_BIN_DIR": os.path.join(vendor_dir, "bin"),
    "COMPOSER_CACHE_DIR": os.path.join(staging_root, "composer-cache"),
})
```

- [ ] **Step 5: Re-run the focused suites**

Run: `pytest plugins/pirategoat-tools/tests/hosts/install/test_composer_in_place.py plugins/pirategoat-tools/tests/hosts/test_containment_contract.py -v`

Expected: PASS.

### Task 4: Centralize Git C-quote grammar and decode dependency scope

**Files:**
- Create: `plugins/pirategoat-tools/scripts/git_paths.py`
- Create: `plugins/pirategoat-tools/tests/test_git_paths.py`
- Modify: `plugins/pirategoat-tools/tests/hosts/install/test_dep_roots.py`
- Modify: `plugins/pirategoat-tools/scripts/hosts/install/lockfile.py`
- Modify: `plugins/pirategoat-tools/scripts/review/review_config.py`
- Modify: `plugins/pirategoat-tools/scripts/review/telemetry.py`
- Modify: `plugins/pirategoat-tools/scripts/review/agent/scope.py`

- [ ] **Step 1: Add shared grammar tests and quoted dependency-root tests**

```python
import pytest

from git_paths import decode_git_c_quoted_path


@pytest.mark.parametrize("quoted,expected", [
    ('"caf\\303\\251.php"', "café.php"),
    ('"tab\\tname.php"', "tab\tname.php"),
    ('"quote\\"name.php"', 'quote"name.php'),
    ('"back\\\\slash.php"', "back\\slash.php"),
])
def test_decodes_git_c_quoting(quoted, expected):
    assert decode_git_c_quoted_path(quoted) == (expected, True)


def test_git_quoted_scope_selects_non_ascii_dependency_root(tmp_path):
    repo = tmp_path / "repo"
    _write(repo / "packages/café/composer.json")
    _write(repo / "packages/café/composer.lock")

    selected, _ = detect_dep_roots(
        str(repo), ['"packages/caf\\303\\251/src/File.php"']
    )

    assert DepRoot("composer", "packages/café") in selected
```

- [ ] **Step 2: Run the new tests and observe missing shared API / missed root**

Run: `pytest plugins/pirategoat-tools/tests/test_git_paths.py plugins/pirategoat-tools/tests/hosts/install/test_dep_roots.py -k 'quoted or decodes_git' -v`

Expected: collection failure until the shared module exists, then dependency-root assertion failure until its caller adopts it.

- [ ] **Step 3: Implement one decoder returning `(decoded_or_none, was_git_quoted)`**

```python
"""Shared Git path decoding primitives."""

from typing import Optional, Tuple


_GIT_QUOTE_ESCAPES = {
    "a": 0x07, "b": 0x08, "f": 0x0C, "n": 0x0A, "r": 0x0D,
    "t": 0x09, "v": 0x0B, '"': 0x22, "\\": 0x5C,
}


def decode_git_c_quoted_path(
    value: str, *, errors: str = "strict"
) -> Tuple[Optional[str], bool]:
    """Decode one whole Git C-quoted path.

    Ordinary input returns ``(value, False)``. Malformed escape-bearing
    wrappers return ``(None, True)`` so callers can apply their own
    fail-closed policy.
    """
    if errors not in {"strict", "surrogateescape"}:
        raise ValueError(f"unsupported UTF-8 error policy: {errors}")

    starts_quoted = value.startswith('"')
    ends_quoted = value.endswith('"')
    if not starts_quoted and not ends_quoted:
        return value, False
    if not starts_quoted or not ends_quoted or len(value) < 2:
        return (value, False) if "\\" not in value else (None, True)

    content = value[1:-1]
    if "\\" not in content:
        return value, False

    decoded = bytearray()
    index = 0
    while index < len(content):
        char = content[index]
        if char == '"':
            return None, True
        if char != "\\":
            decoded.extend(char.encode("utf-8", errors="surrogateescape"))
            index += 1
            continue
        if index + 1 >= len(content):
            return None, True
        escape = content[index + 1]
        if escape in _GIT_QUOTE_ESCAPES:
            decoded.append(_GIT_QUOTE_ESCAPES[escape])
            index += 2
            continue
        octal = content[index + 1:index + 4]
        if (
            len(octal) != 3
            or any(digit not in "01234567" for digit in octal)
            or int(octal, 8) > 0xFF
        ):
            return None, True
        decoded.append(int(octal, 8))
        index += 4

    try:
        return decoded.decode("utf-8", errors=errors), True
    except UnicodeDecodeError:
        return None, True
```

- [ ] **Step 4: Replace duplicated grammar with policy wrappers**

```python
# review_config.py
decoded, _ = decode_git_c_quoted_path(path, errors="surrogateescape")
return path if decoded is None else decoded

# telemetry.py
return decode_git_c_quoted_path(value)

# scope.py marker parsing
decoded, _ = decode_git_c_quoted_path(body)
if decoded is None:
    return None
body = decoded

# lockfile.py
decoded, _ = decode_git_c_quoted_path(raw)
if decoded is None:
    continue
rel = decoded.replace("\\", "/").lstrip("/")
```

Add the `scripts/` parent to `scope.py`'s `sys.path` before importing the
shared module, because `scope.py` is also executed directly by absolute path.

- [ ] **Step 5: Run all shared-decoder consumers**

Run: `pytest plugins/pirategoat-tools/tests/test_git_paths.py plugins/pirategoat-tools/tests/review/test_review_config.py plugins/pirategoat-tools/tests/review/test_telemetry.py plugins/pirategoat-tools/tests/review/agent/test_scope.py plugins/pirategoat-tools/tests/hosts/install/test_dep_roots.py -v`

Expected: PASS, including existing caller-specific malformed-input behavior.

### Task 5: Lock host banner reason contracts together

**Files:**
- Modify: `plugins/pirategoat-tools/tests/hosts/test_types.py`
- Modify: `plugins/pirategoat-tools/schemas/review-output.ts`

- [ ] **Step 1: Add a failing producer/consumer vocabulary equality test**

```python
import re
from pathlib import Path
from typing import get_args

from hosts.types import BannerReason


def test_typescript_banner_reasons_match_runtime_contract():
    schema = (
        Path(__file__).resolve().parents[2] / "schemas" / "review-output.ts"
    ).read_text()
    interface = re.search(
        r"export interface HostContextBanner\s*\{.*?\breason:\s*([^;]+);",
        schema,
        re.DOTALL,
    )
    assert interface is not None
    ts_reasons = set(re.findall(r'"([a-z_]+)"', interface.group(1)))

    assert ts_reasons == set(get_args(BannerReason))
```

- [ ] **Step 2: Run the test and observe `dep_roots_capped` missing from TypeScript**

Run: `pytest plugins/pirategoat-tools/tests/hosts/test_types.py -v`

Expected: FAIL with the missing runtime literal.

- [ ] **Step 3: Add the valid runtime reason to the public union**

```typescript
reason: "partial_unresolved" | "fully_unavailable" | "install_failed" | "dep_roots_capped";
```

- [ ] **Step 4: Re-run the contract test**

Run: `pytest plugins/pirategoat-tools/tests/hosts/test_types.py -v`

Expected: PASS.

### Task 6: Release metadata, full verification, and commit

**Files:**
- Modify: `plugins/pirategoat-tools/CHANGELOG.md`
- Verify/regenerate: `.agents/plugins/marketplace.json`
- Verify/regenerate: `plugins/pirategoat-tools/.codex-plugin/plugin.json`

- [ ] **Step 1: Document the fixes under the existing unpushed `1.112.0` release**

Add these entries under the existing `1.112.0` headings (creating `### Fixed`
if the section does not already have one):

```markdown
### Security

- **Symlinked repo-reviewer refs are gated on their resolved targets.** The shared provenance gate derived each identity from `resolved_path`, but reviewer entries publish `resolved_ref`; changing only a reviewer's in-repo symlink target therefore left the executable prompt trusted. The gate now derives the resolved-field name from the declaration field, covering rules and reviewers through the same path.

### Fixed

- **Dependency installation preserves the reviewed worktree and declared input paths.** In-place Composer installs now redirect cache writes alongside vendor and bin output, including repositories with a relative `config.cache-dir`. Staged JS inputs are copied to their declared relative paths even when the source is an in-repo symlink, so manifests and patch references remain valid.
- **Git-quoted changed paths select nested dependency roots.** One shared Git C-quote decoder now backs provenance, telemetry, scope markers, and dependency-root discovery with caller-specific fail-closed policies, removing four copies of the escape grammar and allowing non-ASCII/control-character paths to find their lockfiles.
- **The public host-context banner type includes capped dependency roots.** The TypeScript reason union now represents `dep_roots_capped`, and a cross-language contract test prevents runtime/schema vocabulary drift.
```

Do not bump again because `1.112.0` is already the branch's unpushed release
and these are patch-level follow-ups.

- [ ] **Step 2: Regenerate and check deterministic Codex compatibility output**

Run: `python3 scripts/generate_codex_compat.py && python3 scripts/generate_codex_compat.py --check`

Expected: both commands exit 0.

- [ ] **Step 3: Run focused host/review suites**

Run: `pytest plugins/pirategoat-tools/tests/hosts/ plugins/pirategoat-tools/tests/review/test_review_config.py plugins/pirategoat-tools/tests/review/test_telemetry.py plugins/pirategoat-tools/tests/review/agent/test_scope.py -v`

Expected: PASS.

- [ ] **Step 4: Run the full plugin suite**

Run: `pytest plugins/pirategoat-tools/tests/ -v`

Expected: PASS with zero failures.

- [ ] **Step 5: Review the exact final diff and whitespace**

Run: `git diff --check && git status --short && git diff --stat && git diff`

Expected: only the planned implementation, tests, changelog, and generated metadata differ.

- [ ] **Step 6: Commit the logical hardening change**

```bash
git add plugins/pirategoat-tools scripts .claude-plugin/marketplace.json .agents/plugins/marketplace.json
git commit -m "fix(review): harden repository path boundaries"
```

The commit body must explain the prior bypasses, the declared/resolved identity split, shared Git decoder, Composer write redirects, and cross-language vocabulary guard.
