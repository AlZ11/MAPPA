"""M6: freeze a snapshot: verify its evidence, lock it, record it, back it up.

Freezing is the point of no return, so it checks before it locks:

1. every blob the snapshot points at is re-hashed. A damaged or missing file stops the
   freeze, so it surfaces now rather than during peer review;
2. the coverage report is written;
3. ``frozen_at`` is set. From then on, database triggers refuse every insert, update or
   delete of the snapshot's rows, whatever code tries;
4. a standalone copy of the database is written (SQLite ``VACUUM INTO``, a consistent
   copy even while other snapshots are being collected) and made read-only;
5. ``snapshot_manifest.json`` records the counts, code version, settings, software
   versions and the SHA-256 of that copy;
6. with ``--backup-to``, blobs, APKs and frozen copies go to a second location
   (OPEN DECISION 4: CSIRO storage) and this snapshot's blobs are re-hashed there.

Freezing an already-frozen snapshot changes nothing and returns the existing manifest.
"""

import hashlib
import json
import platform
import shutil
from dataclasses import dataclass
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
from typing import Any

from sqlalchemy import Engine, Table, select, update

from mappa.models.enums import StorePurpose
from mappa.models.tables import (
    app_metadata,
    discovery,
    discovery_queries,
    fetch_log,
    label_facts,
    label_practices,
    label_status,
    policy_docs,
    snapshots,
)
from mappa.models.types import utc_now
from mappa.provenance import git_commit
from mappa.reports import coverage as coverage_report
from mappa.storage.blobs import BlobError, BlobStore
from mappa.storage.db import SCHEMA_VERSION
from mappa.storage.layout import StoreLayout
from mappa.storage.queries import strings
from mappa.storage.snapshots import require_snapshot

MANIFEST = "snapshot_manifest.json"
_BLOB_COLUMNS: tuple[tuple[Table, tuple[str, ...]], ...] = (
    (discovery_queries, ("raw_blob",)),
    (discovery, ("raw_blob",)),
    (app_metadata, ("raw_blob", "parsed_blob")),
    (fetch_log, ("raw_blob",)),
    (policy_docs, ("raw_blob", "text_blob")),
    (label_status, ("raw_blob",)),
    (label_facts, ("raw_blob",)),
    (label_practices, ("raw_blob",)),
)
_SOFTWARE = (
    "mappa",
    "google-play-scraper",
    "httpx",
    "playwright",
    "pypdf",
    "sqlalchemy",
    "trafilatura",
)


class FreezeError(RuntimeError):
    """The snapshot can't be frozen safely. Nothing was locked."""


@dataclass(frozen=True)
class FreezeResult:
    manifest_path: Path
    already_frozen: bool
    backup_checked: int | None  # blobs re-hashed at the backup location


def freeze(
    engine: Engine,
    store: BlobStore,
    layout: StoreLayout,
    snapshot_id: str,
    *,
    purpose: StorePurpose,
    backup_to: Path | None = None,
) -> FreezeResult:
    target = layout.frozen_dir / snapshot_id
    manifest_path = target / MANIFEST
    with engine.connect() as conn:
        info = require_snapshot(conn, snapshot_id, writable=False)
        referenced = referenced_blobs(conn, snapshot_id)
    if info.frozen_at is not None:
        if not manifest_path.exists():
            raise FreezeError(f"{snapshot_id} is frozen but {manifest_path} is missing")
        return FreezeResult(manifest_path, True, _backup(engine, layout, referenced, backup_to))

    damaged = _verify(store, referenced)
    if damaged:
        raise FreezeError(
            f"{len(damaged)} blobs failed verification, e.g. {damaged[:3]}; nothing was frozen"
        )
    target.mkdir(parents=True, exist_ok=True)
    db_copy = target / "mappa.sqlite"
    if db_copy.exists():
        raise FreezeError(
            f"{db_copy} already exists from an interrupted freeze; move it aside and re-run"
        )

    with engine.begin() as conn:
        now = utc_now()
        conn.execute(
            update(snapshots)
            .where(snapshots.c.snapshot_id == snapshot_id, snapshots.c.frozen_at.is_(None))
            .values(finished_at=info.finished_at or now, frozen_at=now)
        )
    coverage = coverage_report.build(
        engine, snapshot_id, purpose=purpose.value, data_dir=layout.root
    )
    report_md, _ = coverage_report.write(coverage, layout)
    _vacuum_into(engine, db_copy)

    with engine.connect() as conn:
        row = conn.execute(select(snapshots).where(snapshots.c.snapshot_id == snapshot_id)).one()
    manifest = {
        "snapshot_id": snapshot_id,
        "purpose": purpose.value,
        "sample": str(row.sample),
        "started_at": row.started_at.isoformat(),
        "finished_at": row.finished_at.isoformat(),
        "frozen_at": row.frozen_at.isoformat(),
        "git_commit_at_start": row.git_commit,
        "git_commit_at_freeze": git_commit(),
        "schema_version": SCHEMA_VERSION,
        "config": row.config_json,
        "counts": coverage.stage_counts(),
        "acceptance": [{"criterion": c, "value": v, "met": m} for c, v, m in coverage.acceptance()],
        "coverage_report": str(report_md),
        "database_copy": {
            "path": str(db_copy.relative_to(layout.root)),
            "sha256": _file_sha256(db_copy),
            "bytes": db_copy.stat().st_size,
        },
        "blobs": {
            "referenced": len(referenced),
            "bytes": sum(store.path_for(b).stat().st_size for b in referenced),
            "verified": True,
        },
        "software": _software(),
    }
    manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True, default=str) + "\n")
    for path in (db_copy, manifest_path):
        path.chmod(0o444)
    return FreezeResult(manifest_path, False, _backup(engine, layout, referenced, backup_to))


def referenced_blobs(conn: Any, snapshot_id: str) -> set[str]:
    found: set[str] = set()
    for table, columns in _BLOB_COLUMNS:
        for name in columns:
            column = table.c[name]
            query = (
                select(column)
                .where(table.c.snapshot_id == snapshot_id, column.is_not(None))
                .distinct()
            )
            found.update(strings(conn, query))
    return found


def _verify(store: BlobStore, blobs: set[str]) -> list[str]:
    damaged = []
    for sha256 in sorted(blobs):
        try:
            store.get(sha256)
        except BlobError as exc:
            damaged.append(f"{sha256}: {type(exc).__name__}")
    return damaged


def _vacuum_into(engine: Engine, target: Path) -> None:
    """Write a consistent standalone copy. VACUUM can't run inside a transaction."""
    with engine.connect().execution_options(isolation_level="AUTOCOMMIT") as conn:
        conn.exec_driver_sql("VACUUM INTO ?", (str(target),))


def _backup(
    engine: Engine, layout: StoreLayout, referenced: set[str], target: Path | None
) -> int | None:
    """Copy blobs, APKs and frozen copies to ``target``, plus a fresh copy of the live
    database; then re-hash this snapshot's blobs at the destination."""
    if target is None:
        return None
    destination = target / layout.root.name
    for folder in (layout.blobs_dir, layout.apks_dir, layout.frozen_dir):
        if folder.exists():
            _copy_tree(folder, destination / folder.name)
    live_copy = destination / f"mappa-{utc_now().strftime('%Y%m%dT%H%M%SZ')}.sqlite"
    live_copy.parent.mkdir(parents=True, exist_ok=True)
    _vacuum_into(engine, live_copy)
    # Read-only use: get() re-hashes the copied files and never writes through the engine.
    copied = BlobStore(destination / "blobs", engine)
    damaged = _verify(copied, referenced)
    if damaged:
        raise FreezeError(
            f"backup at {destination} has {len(damaged)} damaged blobs, e.g. {damaged[:3]}"
        )
    return len(referenced)


def _copy_tree(source: Path, destination: Path) -> None:
    """Copy files that aren't there yet. Existing files must match in size: content-
    addressed files never change, so a size difference means a damaged copy."""
    for path in source.rglob("*"):
        if not path.is_file():
            continue
        target = destination / path.relative_to(source)
        if target.exists():
            if target.stat().st_size != path.stat().st_size:
                raise FreezeError(f"{target} exists with a different size from {path}")
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, target)


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _software() -> dict[str, str]:
    versions = {"python": platform.python_version()}
    for name in _SOFTWARE:
        try:
            versions[name] = version(name)
        except PackageNotFoundError:
            versions[name] = "not installed"
    return versions
