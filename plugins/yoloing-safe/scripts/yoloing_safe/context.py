"""Evaluation context for the yoloing-safe hook.

Caches expensive shell/path analysis so it is computed at most once
per evaluation pass, regardless of how many detectors run.
"""

from __future__ import annotations

from .shell import _tokenized_segments, _whole_bash_command

_SENTINEL = object()


class EvalContext:
    """Cached evaluation context for a single hook invocation."""

    def __init__(self, tool_name, tool_input, config, command=""):
        self.tool_name = tool_name
        self.tool_input = tool_input
        self.config = config
        self.command = command
        self._whole_command = _SENTINEL
        self._segments = _SENTINEL

    @property
    def whole_command(self):
        """Full normalized command from tool_input (stable across segments)."""
        if self._whole_command is _SENTINEL:
            self._whole_command = _whole_bash_command(self.command, self.tool_input)
        return self._whole_command

    @property
    def segments(self):
        """Tokenized segments of self.command."""
        if self._segments is _SENTINEL:
            self._segments = _tokenized_segments(self.command)
        return self._segments

    def for_segment(self, segment_command):
        """Create a segment-scoped context sharing full-command caches."""
        sub = EvalContext(self.tool_name, self.tool_input, self.config, segment_command)
        if self._whole_command is not _SENTINEL:
            sub._whole_command = self._whole_command
        return sub
