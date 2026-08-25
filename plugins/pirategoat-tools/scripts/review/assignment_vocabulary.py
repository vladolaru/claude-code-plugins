"""Canonical schema-3 manifest assignment field names.

This module deliberately has no imports so the manifest producer and the
analysis package can share the contract without creating an import cycle.
Each consumer still owns its field-specific validation or projection logic.
"""

CHANGED_FILES = "changed_files"
REVIEWABLE_FILES = "reviewable_files"
ASSIGNED_FILES_BY_AGENT = "assigned_files_by_agent"
ASSIGNED_FILES = "assigned_files"
FILE_EXCLUSIONS = "file_exclusions"
UNASSIGNED_REVIEWABLE_FILES = "unassigned_reviewable_files"

ASSIGNMENT_FIELDS = (
    CHANGED_FILES,
    REVIEWABLE_FILES,
    ASSIGNED_FILES_BY_AGENT,
    ASSIGNED_FILES,
    FILE_EXCLUSIONS,
    UNASSIGNED_REVIEWABLE_FILES,
)

ASSIGNMENT_PATH_LIST_FIELDS = (
    CHANGED_FILES,
    REVIEWABLE_FILES,
    ASSIGNED_FILES,
    UNASSIGNED_REVIEWABLE_FILES,
)

ASSIGNMENT_COUNTABLE_LIST_FIELDS = (
    CHANGED_FILES,
    REVIEWABLE_FILES,
    ASSIGNED_FILES,
    FILE_EXCLUSIONS,
    UNASSIGNED_REVIEWABLE_FILES,
)

ASSIGNMENT_TABLE_FIELDS = (
    ASSIGNED_FILES,
    REVIEWABLE_FILES,
    UNASSIGNED_REVIEWABLE_FILES,
)
