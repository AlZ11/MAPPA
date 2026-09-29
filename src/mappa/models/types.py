"""Column types and the one clock every timestamp comes from.

Principle 3 says every record is dated in UTC. SQLite has no date type, so timestamps
are stored as ISO-8601 text with an explicit ``+00:00`` offset. That text sorts in time
order, reads cleanly in any SQL tool, and parses in DuckDB and pandas. Naive datetimes
(no time zone) are refused on write: a local time that looks like UTC is exactly the
kind of silent error that makes a dated snapshot untrustworthy.
"""

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import String
from sqlalchemy.engine import Dialect
from sqlalchemy.types import TypeDecorator


def utc_now() -> datetime:
    """Current time, timezone-aware UTC. The single clock for every stored timestamp."""
    return datetime.now(UTC)


class UTCDateTime(TypeDecorator[datetime]):
    """Timezone-aware datetime stored as ``YYYY-MM-DDTHH:MM:SS.ffffff+00:00`` text."""

    impl = String(32)
    cache_ok = True

    def process_bind_param(self, value: datetime | None, dialect: Dialect) -> str | None:
        if value is None:
            return None
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError(f"naive datetime refused (timestamps must be UTC-aware): {value}")
        return value.astimezone(UTC).isoformat(timespec="microseconds")

    def process_result_value(self, value: Any | None, dialect: Dialect) -> datetime | None:
        if value is None:
            return None
        return datetime.fromisoformat(str(value))
