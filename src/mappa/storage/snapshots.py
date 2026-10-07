"""Snapshot lifecycle: create on first use, refuse writes once frozen, never mix kinds.

A snapshot is the unit of reproducibility (principle 3). It records when it started,
which code and settings produced it, and whether it holds the full sample or the dev
sample. Mixing those inside one snapshot would make its counts meaningless, so a
mismatch is an error rather than a warning.
"""

from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy import Connection, insert, select, update

from mappa.models.enums import SampleMode, StorePurpose
from mappa.models.ids import SYNTHETIC_SNAPSHOT_PREFIX, is_synthetic_snapshot_id
from mappa.models.tables import snapshots
from mappa.models.types import utc_now


class SnapshotError(RuntimeError):
    """The snapshot can't be used this way. The message says why."""


@dataclass(frozen=True)
class SnapshotInfo:
    snapshot_id: str
    sample: SampleMode
    started_at: datetime
    finished_at: datetime | None
    frozen_at: datetime | None
    git_commit: str | None


def check_id_matches_store(snapshot_id: str, purpose: StorePurpose) -> None:
    """Synthetic snapshot IDs live only in synthetic stores, and only they do."""
    synthetic_id = is_synthetic_snapshot_id(snapshot_id)
    if purpose is StorePurpose.SYNTHETIC and not synthetic_id:
        raise SnapshotError(
            f"synthetic runs need a snapshot ID starting with {SYNTHETIC_SNAPSHOT_PREFIX!r}"
        )
    if purpose is StorePurpose.REAL and synthetic_id:
        raise SnapshotError(
            f"snapshot IDs starting with {SYNTHETIC_SNAPSHOT_PREFIX!r} are for synthetic "
            "data; use --synthetic, or a different ID for real data"
        )


def get_snapshot(conn: Connection, snapshot_id: str) -> SnapshotInfo | None:
    row = conn.execute(select(snapshots).where(snapshots.c.snapshot_id == snapshot_id)).first()
    if row is None:
        return None
    return SnapshotInfo(
        snapshot_id=row.snapshot_id,
        sample=SampleMode(row.sample),
        started_at=row.started_at,
        finished_at=row.finished_at,
        frozen_at=row.frozen_at,
        git_commit=row.git_commit,
    )


def ensure_snapshot(
    conn: Connection,
    snapshot_id: str,
    *,
    sample: SampleMode,
    config_json: dict[str, Any],
    git_commit: str | None,
) -> SnapshotInfo:
    """Create the snapshot on first use; afterwards insist on the same sample mode."""
    info = get_snapshot(conn, snapshot_id)
    if info is None:
        conn.execute(
            insert(snapshots).values(
                snapshot_id=snapshot_id,
                sample=sample,
                started_at=utc_now(),
                git_commit=git_commit,
                config_json=config_json,
            )
        )
        created = get_snapshot(conn, snapshot_id)
        assert created is not None
        return created
    _check_writable(info)
    if info.sample is not sample:
        raise SnapshotError(
            f"snapshot {snapshot_id} holds the {info.sample.value} sample; it can't also "
            f"take the {sample.value} sample. Use a new snapshot ID."
        )
    return info


def require_snapshot(conn: Connection, snapshot_id: str, *, writable: bool = True) -> SnapshotInfo:
    """For steps after discovery: the snapshot must exist (and be unfrozen to write)."""
    info = get_snapshot(conn, snapshot_id)
    if info is None:
        raise SnapshotError(f"unknown snapshot {snapshot_id}: run `mappa discover` first")
    if writable:
        _check_writable(info)
    return info


def mark_finished(conn: Connection, snapshot_id: str) -> None:
    conn.execute(
        update(snapshots)
        .where(snapshots.c.snapshot_id == snapshot_id)
        .values(finished_at=utc_now())
    )


def _check_writable(info: SnapshotInfo) -> None:
    if info.frozen_at is not None:
        raise SnapshotError(
            f"snapshot {info.snapshot_id} was frozen at {info.frozen_at.isoformat()}; "
            "its data can't change. Start a new snapshot instead."
        )
