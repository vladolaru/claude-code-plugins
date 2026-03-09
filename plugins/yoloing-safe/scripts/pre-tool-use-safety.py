#!/usr/bin/env python3
"""PreToolUse safety hook for YOLO mode.

Compatibility shim that preserves the legacy module surface while delegating
implementation to the internal `yoloing_safe` package.
"""

from __future__ import annotations

import os
import sys
import time

_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
if _SCRIPT_DIR not in sys.path:
    sys.path.insert(0, _SCRIPT_DIR)

from yoloing_safe.config import (
    DEFAULTS,
    NON_DISABLEABLE_RULES,
    SELF_PROTECTED_PATHS,
    SELF_PROTECTION_MESSAGE,
    USER_CONFIG_PATH,
    is_path_within_self_protected,
    is_self_protected_path,
    load_config,
)
from yoloing_safe.paths import (
    _bash_targets_protected_path,
    _candidate_sensitive_paths,
    _collect_bash_path_candidates,
    _collect_bash_targets,
    _collect_find_roots,
    _collect_interpreter_write_targets,
    _collect_protected_mutation_targets_for_segment,
    _collect_symlink_source_targets,
    _collect_write_targets_for_segment,
    _command_mentions_protected_path,
    _dedupe,
    _extract_path_candidates_from_arg,
    _is_non_file_command,
    _is_sensitive_write_target_path,
    _resolve_candidate_path,
    _token_matches_credential_pattern,
)
from yoloing_safe.registry import build_registry, is_allowlisted as _registry_is_allowlisted
from yoloing_safe.rules import ALLOWLIST_PATTERNS, RULES, RULES_BY_TOOL
from yoloing_safe.runtime import allow, ask, block, main
from yoloing_safe.shell import (
    RE_CHAIN_OPS as _RE_CHAIN_OPS,
    _collect_input_redirection_sources,
    _collect_positional_args,
    _collect_redirection_targets,
    _command_and_args_from_text,
    _merge_clobber_redirect_tokens,
    _segment_command_and_args,
    _split_bash_segments,
    _split_shell_segments,
    _strip_git_global_opts,
    _strip_npm_global_opts,
    _tokenize_shell,
    _tokenized_segments,
    _whole_bash_command,
    normalize_command,
    strip_writer_heredocs,
)


_SELF_PROTECTION_MESSAGE = SELF_PROTECTION_MESSAGE
_is_path_within_self_protected = is_path_within_self_protected
_is_self_protected_path = is_self_protected_path

_PROFILE = os.environ.get("YOLOING_SAFE_PROFILE") == "1"
_T0 = time.monotonic() if _PROFILE else 0


def _mark(label):
    if _PROFILE:
        elapsed_ms = (time.monotonic() - _T0) * 1000
        print(f"[yoloing-safe:profile] {label} {elapsed_ms:.3f}ms", file=sys.stderr)


_mark("module_loaded")
_mark("registry_built")


def is_allowlisted(command, disabled=None):
    """Compatibility wrapper for the legacy module-level helper."""
    return _registry_is_allowlisted(command, ALLOWLIST_PATTERNS, disabled)


if __name__ == "__main__":
    try:
        main(mark=_mark)
    except SystemExit:
        raise
    except Exception:
        sys.exit(0)
