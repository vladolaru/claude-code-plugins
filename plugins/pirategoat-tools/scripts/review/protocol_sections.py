"""Sections of the shared protocol files, extracted for delivery.

Stdlib-only leaf. Bootstrap builds every reviewer's REVIEW RULES from
`agents/shared/reviewer-protocol.md` with extract_protocol_sections(); the
briefings deliver one section of that same file, `## Empirical Probes`, to
the two participants that run code outside a reviewer's prompt: the
orchestrator (step 9) and the decision critic (the step-10 dispatch prompt).
The probe rules therefore exist once. They used to be hand-copied into the
step-9 briefing and the critic's RULE 2, and the three wordings had already
drifted apart by the time a new rule was needed for all of them.
"""

import re
from functools import lru_cache
from pathlib import Path
from typing import List

PLUGIN_ROOT = Path(__file__).resolve().parents[2]
REVIEWER_PROTOCOL_PATH = PLUGIN_ROOT / "agents" / "shared" / "reviewer-protocol.md"
EMPIRICAL_PROBES_HEADING = "## Empirical Probes"


def extract_protocol_sections(content: str, skip_prefixes: List[str]) -> str:
    """Extract all sections from a markdown file EXCEPT those matching skip prefixes.

    Uses a skip-list so new sections added to the protocol are included automatically.
    Only setup/operational sections that the bootstrap replaces are skipped.
    Also strips the file's title heading (# level 1) since the bootstrap provides its own.
    """
    lines = content.splitlines()
    extracted = []
    skipping = False
    skip_level = 0
    in_code_fence = False

    for line in lines:
        # Track fenced code blocks — don't parse headings inside them
        if line.startswith("```"):
            in_code_fence = not in_code_fence
            if not skipping:
                extracted.append(line)
            continue

        if in_code_fence:
            if not skipping:
                extracted.append(line)
            continue

        # Check if this line is a markdown heading
        heading_match = re.match(r'^(#{1,6})\s', line)
        if heading_match:
            level = len(heading_match.group(1))
            stripped = line.strip()

            # Skip the file title (# level 1)
            if level == 1:
                continue

            # Check if this heading should be skipped
            should_skip = any(stripped.startswith(prefix) for prefix in skip_prefixes)

            if should_skip:
                skipping = True
                skip_level = level
                continue

            # If we were skipping and hit a heading of same/higher level, stop skipping
            if skipping and level <= skip_level:
                skipping = False

        if not skipping:
            extracted.append(line)

    return "\n".join(extracted).strip()



def extract_protocol_section(content: str, prefix: str) -> str:
    """Return the one `## ` section whose heading starts with ``prefix``.

    The heading line is included, `#` lines inside code fences are not
    parsed, and the section ends at the next `## ` or `# ` heading. Empty
    string when the protocol has no such section. This is the inverse of
    extract_protocol_sections() for the sections the skip list holds back
    so that build_output() can place them on a condition.
    """
    lines = content.splitlines()
    kept = []
    keeping = False
    in_code_fence = False
    for line in lines:
        if line.startswith("```"):
            in_code_fence = not in_code_fence
            if keeping:
                kept.append(line)
            continue
        if in_code_fence:
            if keeping:
                kept.append(line)
            continue
        heading_match = re.match(r'^(#{1,6})\s', line)
        if heading_match and len(heading_match.group(1)) <= 2:
            if keeping:
                break
            keeping = line.strip().startswith(prefix)
        if keeping:
            kept.append(line)
    return "\n".join(kept).strip()


@lru_cache(maxsize=None)
def empirical_probe_rules() -> str:
    """The body of the protocol's `## Empirical Probes` section, heading
    dropped, for embedding under a caller's own lead-in.

    Raises when the section is missing: the protocol ships with the plugin,
    and a briefing that silently lost the probe rules would send the
    orchestrator and the critic into the reviewed repo with none.
    """
    section = extract_protocol_section(
        REVIEWER_PROTOCOL_PATH.read_text(encoding="utf-8"),
        EMPIRICAL_PROBES_HEADING,
    )
    if not section:
        raise ValueError(
            f"{REVIEWER_PROTOCOL_PATH} has no {EMPIRICAL_PROBES_HEADING!r} section"
        )
    return section.split("\n", 1)[1].strip()
