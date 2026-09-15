"""The one unsafe-session-id list shared by the hook and run_paths tests.

`hooks/init-plugin-root.sh`'s `case` and `run_paths._is_safe_session_id()`
must refuse exactly the same ids — a session id either half accepts and
the other refuses lets the hook write where run_paths (or a reader that
follows it) never looks, or vice versa. Both suites parametrize their
refusal tests from this one tuple instead of keeping their own lists,
which drift.
"""

UNSAFE_SESSION_IDS = (
    "",
    ".",
    "..",
    "../evil",
    "a/b",
    "a b",
    " s1",
    "s1\n",
    "é",
)
