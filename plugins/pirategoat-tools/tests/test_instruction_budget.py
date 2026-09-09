"""Instruction-file byte budgets — keep every AGENTS.md small enough to be read whole.

The AGENTS.md files are the always-on instruction surface: Claude Code loads
the root file at launch and a plugin's file the first time it reads anything
under that plugin, and Codex concatenates the root-to-cwd chain at launch.
Two facts make size a correctness property, not a style preference:

- Codex reads at most ``project_doc_max_bytes`` (default 32 KiB) across the
  whole chain, truncates the file that crosses the line, and reads nothing
  after it. A root file over the budget loses its tail on that host, and a
  plugin file is never read at all.
- Anthropic's memory-file guidance targets under 200 lines per file: longer
  files consume context and reduce adherence, because the rules that matter
  drown in reference material.

This repository is maintained by agents that read it cold every session, and
an agent copies whatever pattern a file already shows. A prose rule saying
"keep it short" erodes the first time an essay row lands; a failing test
does not. That is why the ceilings live here rather than in the files.

When this test fails, the fix is to MOVE the content down a layer, never to
compress its wording:

- a fact about one module goes in that module's docstring;
- a procedure or a design record goes in ``plugins/<plugin>/docs/``;
- the file-to-test lookup goes in ``tests/TESTING.md``;
- an incident history goes in ``.claude/docs/learnings/``.

The plugin AGENTS.md keeps a one-line pointer with the trigger for reading
the moved text ("read X before changing Y").
"""

from pathlib import Path

import pytest

PLUGIN_ROOT = Path(__file__).resolve().parent.parent  # tests/ -> pirategoat-tools/
REPO_ROOT = PLUGIN_ROOT.parent.parent  # plugins/ -> repo root
PLUGINS_DIR = REPO_ROOT / "plugins"

ROOT_AGENTS_MD = REPO_ROOT / "AGENTS.md"
PLUGIN_AGENTS_MDS = sorted(PLUGINS_DIR.glob("*/AGENTS.md"))

# codex-rs `DEFAULT_PROJECT_DOC_MAX_BYTES`: the cumulative budget Codex reads
# across the root-to-cwd AGENTS.md chain before it stops reading.
CODEX_PROJECT_DOC_MAX_BYTES = 32 * 1024

# Ceilings. The chain ceiling leaves 4 KiB under Codex's budget so a routine
# edit cannot push the chain over the line between two test runs.
ROOT_BUDGET_BYTES = 10 * 1024
PLUGIN_BUDGET_BYTES = 18 * 1024
CHAIN_BUDGET_BYTES = CODEX_PROJECT_DOC_MAX_BYTES - 4 * 1024
MAX_LINE_CHARS = 600

RELOCATION_HINT = (
    "Move content down a layer instead of compressing it: module facts to "
    "the module docstring, procedures and design records to "
    "plugins/<plugin>/docs/, the file-to-test lookup to tests/TESTING.md, "
    "incident history to .claude/docs/learnings/. Keep a one-line pointer "
    "with its trigger in AGENTS.md."
)


def _size(path: Path) -> int:
    return path.stat().st_size


def _rel(path: Path) -> str:
    return str(path.relative_to(REPO_ROOT))


def test_root_agents_md_within_budget():
    size = _size(ROOT_AGENTS_MD)
    assert size <= ROOT_BUDGET_BYTES, (
        f"AGENTS.md is {size:,} bytes; the ceiling is {ROOT_BUDGET_BYTES:,}. "
        f"{RELOCATION_HINT}"
    )


@pytest.mark.parametrize("agents_md", PLUGIN_AGENTS_MDS, ids=_rel)
def test_plugin_agents_md_within_budget(agents_md: Path):
    size = _size(agents_md)
    assert size <= PLUGIN_BUDGET_BYTES, (
        f"{_rel(agents_md)} is {size:,} bytes; the ceiling is "
        f"{PLUGIN_BUDGET_BYTES:,}. {RELOCATION_HINT}"
    )


@pytest.mark.parametrize("agents_md", PLUGIN_AGENTS_MDS, ids=_rel)
def test_root_plus_plugin_chain_fits_codex_budget(agents_md: Path):
    chain = _size(ROOT_AGENTS_MD) + _size(agents_md)
    assert chain <= CHAIN_BUDGET_BYTES, (
        f"AGENTS.md + {_rel(agents_md)} is {chain:,} bytes; Codex reads at "
        f"most {CODEX_PROJECT_DOC_MAX_BYTES:,} across the chain and drops the "
        f"rest, so the ceiling is {CHAIN_BUDGET_BYTES:,}. {RELOCATION_HINT}"
    )


@pytest.mark.parametrize(
    "agents_md", [ROOT_AGENTS_MD, *PLUGIN_AGENTS_MDS], ids=_rel
)
def test_no_essay_lines(agents_md: Path):
    """A line over the limit is a paragraph that has outgrown its row."""
    long_lines = [
        (number, len(line))
        for number, line in enumerate(
            agents_md.read_text(encoding="utf-8").splitlines(), start=1
        )
        if len(line) > MAX_LINE_CHARS
    ]
    assert not long_lines, (
        f"{_rel(agents_md)} has lines over {MAX_LINE_CHARS} characters "
        f"(line: length): {long_lines}. {RELOCATION_HINT}"
    )


@pytest.mark.parametrize(
    "claude_md",
    [REPO_ROOT / "CLAUDE.md", *sorted(PLUGINS_DIR.glob("*/CLAUDE.md"))],
    ids=_rel,
)
def test_claude_md_is_a_shim(claude_md: Path):
    """CLAUDE.md only imports AGENTS.md, so both hosts read one canonical file."""
    assert claude_md.read_text(encoding="utf-8").strip() == "@AGENTS.md", (
        f"{_rel(claude_md)} must contain exactly '@AGENTS.md'; instructions "
        "belong in the sibling AGENTS.md so Codex reads them too."
    )
