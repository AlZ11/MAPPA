"""The schema creates cleanly and enforces the project's principles by itself."""

from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import Engine, insert, inspect, select, update
from sqlalchemy.exc import IntegrityError, StatementError

from mappa.models.enums import StorePurpose
from mappa.models.ids import is_valid_snapshot_id
from mappa.models.tables import app_metadata, fetch_log, label_status, metadata, snapshots
from mappa.storage.blobs import BlobStore
from mappa.storage.db import (
    SCHEMA_VERSION,
    SchemaVersionError,
    StorePurposeError,
    check_store,
    init_schema,
    make_engine,
    user_version,
)
from tests.conftest import T0, T0_TEXT, add_snapshot

EXPECTED_TABLES = {
    "store_meta",
    "snapshots",
    "runs",
    "blobs",
    "discovery_queries",
    "discovery",
    "app_metadata",
    "fetch_log",
    "policy_docs",
    "label_status",
    "label_facts",
    "label_practices",
    "apks",
}


def _metadata_row(**overrides: Any) -> dict[str, Any]:
    row: dict[str, Any] = {
        "snapshot_id": "dev-01",
        "app_id": "com.example.app",
        "fetched_at": T0,
        "status": "failed",
    }
    row.update(overrides)
    return row


def _log_row(snapshot_id: str = "dev-01", app_id: str = "com.example.app") -> dict[str, Any]:
    return {
        "snapshot_id": snapshot_id,
        "app_id": app_id,
        "kind": "metadata",
        "url": "https://example.org/",
        "status": "failed",
        "attempt": 1,
        "started_at": T0,
        "finished_at": T0,
    }


def test_schema_creates_every_table_cleanly(engine: Engine) -> None:
    assert set(inspect(engine).get_table_names()) == EXPECTED_TABLES
    with engine.connect() as conn:
        assert user_version(conn) == SCHEMA_VERSION
        assert conn.exec_driver_sql("PRAGMA integrity_check").scalar_one() == "ok"
        assert conn.exec_driver_sql("PRAGMA foreign_key_check").all() == []


def test_init_schema_is_idempotent(engine: Engine) -> None:
    init_schema(engine, StorePurpose.REAL)
    init_schema(engine, StorePurpose.REAL)
    assert set(inspect(engine).get_table_names()) == EXPECTED_TABLES


def test_every_evidence_table_is_keyed_by_snapshot() -> None:
    for table in metadata.sorted_tables:
        if table.name in ("blobs", "store_meta", "runs"):
            continue  # content shared across snapshots / store settings / the run log
        assert not table.c.snapshot_id.nullable, table.name
        if table.name != "snapshots":
            assert table.c.snapshot_id.foreign_keys, table.name


def test_connections_enforce_foreign_keys_and_use_wal(engine: Engine) -> None:
    with engine.connect() as conn:
        assert conn.exec_driver_sql("PRAGMA foreign_keys").scalar_one() == 1
        assert conn.exec_driver_sql("PRAGMA journal_mode").scalar_one() == "wal"


def test_database_from_another_schema_version_is_refused(tmp_path: Path) -> None:
    engine = make_engine(tmp_path / "old.sqlite")
    init_schema(engine, StorePurpose.REAL)
    with engine.begin() as conn:
        conn.exec_driver_sql("PRAGMA user_version = 999")
    with pytest.raises(SchemaVersionError, match="schema version 999"):
        init_schema(engine, StorePurpose.REAL)
    with pytest.raises(SchemaVersionError, match="schema version 999"):
        check_store(engine, StorePurpose.REAL)


def test_non_mappa_database_is_refused(tmp_path: Path) -> None:
    engine = make_engine(tmp_path / "other.sqlite")
    with engine.begin() as conn:
        conn.exec_driver_sql("CREATE TABLE something_else (x INTEGER)")
    with pytest.raises(SchemaVersionError, match="not a MAPPA database"):
        init_schema(engine, StorePurpose.REAL)


def test_a_store_keeps_its_purpose(engine: Engine, synthetic_engine: Engine) -> None:
    """Real and synthetic stores refuse to be opened (or re-initialised) as the other."""
    for wrong, store in ((StorePurpose.SYNTHETIC, engine), (StorePurpose.REAL, synthetic_engine)):
        with pytest.raises(StorePurposeError):
            check_store(store, wrong)
        with pytest.raises(StorePurposeError):
            init_schema(store, wrong)
    with (
        pytest.raises(IntegrityError, match="fixed when the store is created"),
        engine.begin() as conn,
    ):
        conn.exec_driver_sql("UPDATE store_meta SET value = 'synthetic'")


@pytest.mark.parametrize(
    ("store_fixture", "snapshot_id", "app_id"),
    [
        ("engine", "synthetic-01", None),
        ("engine", "dev-01", "invalid.mappa.synthetic.app01"),
        ("synthetic_engine", "dev-01", None),
        ("synthetic_engine", "synthetic-01", "com.example.app"),
    ],
)
def test_synthetic_and_real_ids_never_mix(
    request: pytest.FixtureRequest, store_fixture: str, snapshot_id: str, app_id: str | None
) -> None:
    """Checked by triggers, so even code that skips the Python checks can't mix them."""
    store: Engine = request.getfixturevalue(store_fixture)
    if app_id is None:
        with pytest.raises(IntegrityError, match="never mix"), store.begin() as conn:
            add_snapshot(conn, snapshot_id)
        return
    with store.begin() as conn:
        add_snapshot(conn, snapshot_id)
    with pytest.raises(IntegrityError, match="never mix"), store.begin() as conn:
        conn.execute(insert(fetch_log).values(_log_row(snapshot_id, app_id)))


def test_matching_ids_are_accepted(engine: Engine, synthetic_engine: Engine) -> None:
    with engine.begin() as conn:
        add_snapshot(conn, "dev-01")
        conn.execute(insert(fetch_log).values(_log_row("dev-01", "com.example.app")))
    with synthetic_engine.begin() as conn:
        add_snapshot(conn, "synthetic-01")
        conn.execute(
            insert(fetch_log).values(_log_row("synthetic-01", "invalid.mappa.synthetic.app01"))
        )


def test_a_frozen_snapshot_cannot_change(engine: Engine) -> None:
    with engine.begin() as conn:
        add_snapshot(conn)
        conn.execute(insert(fetch_log).values(_log_row()))
        conn.execute(update(snapshots).values(frozen_at=T0))
    for statement in (
        insert(fetch_log).values(_log_row(app_id="com.example.other")),
        update(fetch_log).values(error="edited"),
        fetch_log.delete(),
        update(snapshots).values(notes="edited"),
        snapshots.delete(),
    ):
        with pytest.raises(IntegrityError, match="frozen"), engine.begin() as conn:
            conn.execute(statement)


def test_blob_records_are_write_once(engine: Engine, store: BlobStore) -> None:
    digest = store.put(b"evidence", "text/plain")
    with pytest.raises(IntegrityError, match="write-once"), engine.begin() as conn:
        conn.exec_driver_sql("UPDATE blobs SET content_type = 'text/html'")
    with pytest.raises(IntegrityError, match="write-once"), engine.begin() as conn:
        conn.exec_driver_sql(f"DELETE FROM blobs WHERE sha256 = '{digest}'")


@pytest.mark.parametrize("bad_status", ["missing", "", "OK", None])
def test_status_accepts_only_the_six_values(engine: Engine, bad_status: str | None) -> None:
    """Checked by SQLite itself, so even a hand-written INSERT can't store a vague status."""
    with engine.begin() as conn:
        add_snapshot(conn)
    with pytest.raises(IntegrityError), engine.begin() as conn:
        conn.exec_driver_sql(
            "INSERT INTO app_metadata (snapshot_id, app_id, fetched_at, status) "
            "VALUES ('dev-01', 'com.example.app', ?, ?)",
            (T0_TEXT, bad_status),
        )


def test_unknown_status_is_also_refused_before_reaching_sql(engine: Engine) -> None:
    with engine.begin() as conn:
        add_snapshot(conn)
    with (
        pytest.raises(StatementError, match="not among the defined enum values"),
        engine.begin() as conn,
    ):
        conn.execute(insert(app_metadata).values(_metadata_row(status="missing")))


def test_a_failure_is_recorded_as_a_failure_without_evidence(engine: Engine) -> None:
    """Unknown != absent: a failed fetch is a row saying 'failed', not a missing row."""
    with engine.begin() as conn:
        add_snapshot(conn)
        conn.execute(insert(app_metadata).values(_metadata_row(status="failed")))
        assert conn.execute(select(app_metadata.c.status)).scalar_one() == "failed"


def test_ok_row_must_point_at_evidence(engine: Engine) -> None:
    with engine.begin() as conn:
        add_snapshot(conn)
    with pytest.raises(IntegrityError, match="ok_has_evidence"), engine.begin() as conn:
        conn.execute(insert(app_metadata).values(_metadata_row(status="ok", raw_blob=None)))


def test_evidence_pointer_must_name_a_stored_blob(engine: Engine) -> None:
    with engine.begin() as conn:
        add_snapshot(conn)
    with pytest.raises(IntegrityError, match="FOREIGN KEY"), engine.begin() as conn:
        conn.execute(
            insert(app_metadata).values(
                _metadata_row(status="ok", raw_blob="a" * 64, parsed_blob="b" * 64)
            )
        )


def test_ok_row_with_stored_evidence_is_accepted(engine: Engine, store: BlobStore) -> None:
    raw = store.put(b"<html>listing</html>", "text/html")
    parsed = store.put(b'{"title": "Example"}', "application/json")
    with engine.begin() as conn:
        add_snapshot(conn)
        conn.execute(
            insert(app_metadata).values(
                _metadata_row(status="ok", raw_blob=raw, parsed_blob=parsed)
            )
        )


def test_rows_need_an_existing_snapshot(engine: Engine) -> None:
    with pytest.raises(IntegrityError, match="FOREIGN KEY"), engine.begin() as conn:
        conn.execute(insert(app_metadata).values(_metadata_row(snapshot_id="ghost")))


def test_label_needs_the_apps_metadata_first(engine: Engine) -> None:
    with engine.begin() as conn:
        add_snapshot(conn)
    with pytest.raises(IntegrityError, match="FOREIGN KEY"), engine.begin() as conn:
        conn.execute(
            insert(label_status).values(
                snapshot_id="dev-01", app_id="com.never.fetched", fetched_at=T0, status="failed"
            )
        )


@pytest.mark.parametrize(
    "inclusion",
    [
        {"included": False},  # excluded without a reason
        {"included": True},  # included without a basis
        {"included": True, "exclusion_reason": "paid", "inclusion_basis": "seed"},
    ],
)
def test_inclusion_decisions_must_be_complete(engine: Engine, inclusion: dict[str, Any]) -> None:
    with engine.begin() as conn:
        add_snapshot(conn)
    with pytest.raises(IntegrityError, match="inclusion_decided"), engine.begin() as conn:
        conn.execute(insert(app_metadata).values(_metadata_row(**inclusion)))


def test_timestamps_are_stored_as_utc_text(engine: Engine) -> None:
    aest = timezone(timedelta(hours=10))
    local = datetime(2026, 10, 5, 19, 0, tzinfo=aest)  # 09:00 UTC
    with engine.begin() as conn:
        conn.execute(
            insert(snapshots).values(
                snapshot_id="dev-01", sample="full", started_at=local, config_json={}
            )
        )
        stored = conn.exec_driver_sql("SELECT started_at FROM snapshots").scalar_one()
        loaded = conn.execute(select(snapshots.c.started_at)).scalar_one()
    assert stored == T0_TEXT
    assert loaded == T0
    assert loaded.tzinfo is not None


def test_naive_timestamps_are_refused(engine: Engine) -> None:
    naive = datetime(2026, 10, 5, 9, 0)  # noqa: DTZ001 - deliberately naive
    with pytest.raises(StatementError, match="naive datetime refused"), engine.begin() as conn:
        conn.execute(
            insert(snapshots).values(
                snapshot_id="dev-01", sample="full", started_at=naive, config_json={}
            )
        )


def test_config_json_cannot_be_null(engine: Engine) -> None:
    """Python None must become SQL NULL (and be refused), not the JSON text 'null'."""
    with pytest.raises(IntegrityError, match="NOT NULL"), engine.begin() as conn:
        conn.execute(
            insert(snapshots).values(
                snapshot_id="dev-01", sample="full", started_at=T0, config_json=None
            )
        )


@pytest.mark.parametrize(
    "snapshot_id",
    ["2026-10-S1", "dev-01", "a", "x" * 64, "bad id", "-x", "a/b", "x" * 65, "é", ""],
)
def test_snapshot_id_rule_is_the_same_in_python_and_in_the_database(
    engine: Engine, snapshot_id: str
) -> None:
    try:
        with engine.begin() as conn:
            conn.exec_driver_sql(
                "INSERT INTO snapshots (snapshot_id, sample, started_at, config_json) "
                "VALUES (?, 'full', ?, '{}')",
                (snapshot_id, T0_TEXT),
            )
        accepted_by_db = True
    except IntegrityError:
        accepted_by_db = False
    assert accepted_by_db == is_valid_snapshot_id(snapshot_id)
