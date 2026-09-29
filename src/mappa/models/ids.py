"""Identifier rules.

Snapshot IDs appear in every primary key and in file names such as
``reports/coverage_<snapshot>.md``, so they are limited to a filesystem-safe alphabet
when they are created rather than escaped everywhere they are used.
"""

import re

SNAPSHOT_ID_MAX_LEN = 64
_SNAPSHOT_ID_RE = re.compile(rf"[A-Za-z0-9][A-Za-z0-9._-]{{0,{SNAPSHOT_ID_MAX_LEN - 1}}}")

# The same rule as a SQLite CHECK expression, so the database enforces it too.
SNAPSHOT_ID_SQL_CHECK = (
    f"length(snapshot_id) BETWEEN 1 AND {SNAPSHOT_ID_MAX_LEN} "
    "AND substr(snapshot_id, 1, 1) GLOB '[A-Za-z0-9]' "
    "AND snapshot_id NOT GLOB '*[^-A-Za-z0-9._]*'"
)


def is_valid_snapshot_id(value: str) -> bool:
    """True for IDs like ``2026-10-S1`` or ``dev-01``: 1-64 chars of letters, digits,
    ``.``, ``_`` or ``-``, starting with a letter or digit."""
    return _SNAPSHOT_ID_RE.fullmatch(value) is not None
