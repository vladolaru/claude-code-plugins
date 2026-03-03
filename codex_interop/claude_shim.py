"""Safe decision tree for project-root CLAUDE.md shim handling."""

from __future__ import annotations

from pathlib import Path

from codex_interop.model import ClaudeShimDecision, ProjectContext


SHIM_CONTENT = "@AGENTS.md\n"


def plan_claude_shim(project: ProjectContext) -> ClaudeShimDecision:
    """Plan safe CLAUDE.md behavior for the project root."""
    claude_md_path = project.root / "CLAUDE.md"

    if not claude_md_path.exists() and not claude_md_path.is_symlink():
        return ClaudeShimDecision(
            action="create",
            path=claude_md_path,
            content=SHIM_CONTENT,
            reason="CLAUDE.md missing",
        )

    if claude_md_path.is_symlink():
        target = claude_md_path.resolve()
        if target == project.agents_md_path.resolve():
            return ClaudeShimDecision(
                action="preserve",
                path=claude_md_path,
                reason="CLAUDE.md is a symlink to AGENTS.md",
            )
        return ClaudeShimDecision(
            action="fail",
            path=claude_md_path,
            reason="CLAUDE.md is a symlink but not to AGENTS.md",
        )

    content = claude_md_path.read_text()
    if content.strip() == "@AGENTS.md":
        return ClaudeShimDecision(
            action="preserve",
            path=claude_md_path,
            content=SHIM_CONTENT,
            reason="CLAUDE.md already matches shim",
        )

    return ClaudeShimDecision(
        action="fail",
        path=claude_md_path,
        reason="CLAUDE.md exists and is not a generator-owned shim",
    )
