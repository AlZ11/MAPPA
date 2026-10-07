"""Typed helpers for the common "one column" and "two columns" query shapes.

Our columns are declared with SQLAlchemy Core, which types values as ``Any``. Converting
to ``str`` at this boundary keeps the rest of the code typed, and turns enum columns
into their plain values.
"""

from typing import Any

from sqlalchemy import Connection, Select


def strings(conn: Connection, query: Select[Any]) -> list[str]:
    """First column of every row, as strings."""
    return [str(row[0]) for row in conn.execute(query)]


def string_map(conn: Connection, query: Select[Any]) -> dict[str, str | None]:
    """{first column: second column} for every row, as strings (None stays None)."""
    return {str(row[0]): (None if row[1] is None else str(row[1])) for row in conn.execute(query)}
