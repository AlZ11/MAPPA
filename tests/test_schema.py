"""The schema creates cleanly and enforces the project's principles by itself."""

from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import Connection, Engine, insert, inspect, select
from sqlalchemy.exc import IntegrityError, StatementError

from mappa.models.ids import is_valid_snapshot_id
from mappa.models.tables import app_metadata, label_status, metadata, snapshots
from mappa.storage.blobs import BlobStore
from mappa.storage.db import (
    SCHEMA_VERSION,
    SchemaVersionError,
    init_schema,
    make_engine,
    user_version,
)

EXPECTED_TABLES = {
    "snapshots",
    "blobs",
    "discovery",
    "app_metadata",
    "fetch_log",
    "policy_docs",
    "label_status",
    "label_facts",
    "label_practices",
    "apks",
}
T0 = datetime(2026, 10, 5, 9, 0, tzinfo=UTC)
T0_TEXT = "2026-10-05T09:00:00.000000+00:00"


def _add_snapshot(conn: Connection, snapshot_id: str = "dev-01") -> None:
    conn.execute(
        insert(snapshots).values(snapshot_id=snapshot_id, started_at=T0, config_json={"k": 1})
    )


def _metadata_row(**overrides: Any) -> dict[str, Any]:
    row: dict[str, Any] = {
        "snapshot_id": "dev-01",
        "app_id": "com.example.app",
        "fetched_at": T0,
        "status": "failed",
    }
    row.update(overrides)
    return row


def test_schema_creates_every_table_cleanly(engine: Engine) -> None:
    assert set(inspect(engine).get_table_names()) == EXPECTED_TABLES
    with engine.connect() as conn:
        assert user_version(conn) == SCHEMA_VERSION
        assert conn.exec_driver_sql("PRAGMA integrity_check").scalar_one() == "ok"
        assert conn.exec_driver_sql("PRAGMA foreign_key_check").all() == []


def test_init_schema_is_idempotent(engine: Engine) -> None:
    init_schema(engine)
    init_schema(engine)
    assert set(inspect(engine).get_table_names()) == EXPECTED_TABLES


def test_every_table_except_blobs_is_keyed_by_snapshot() -> None:
    for table in metadata.sorted_tables:
        if table.name == "blobs":  # content is shared by every snapshot that saw it
            continue
        assert not table.c.snapshot_id.nullable, table.name
        if table.name != "snapshots":
            assert table.c.snapshot_id.foreign_keys, table.name


def test_connections_enforce_foreign_keys_and_use_wal(engine: Engine) -> None:
    with engine.connect() as conn:
        assert conn.exec_driver_sql("PRAGMA foreign_keys").scalar_one() == 1
        assert conn.exec_driver_sql("PRAGMA journal_mode").scalar_one() == "wal"


def test_database_from_another_schema_version_is_refused(tmp_path: Path) -> None:
    engine = make_engine(tmp_path / "old.sqlite")
    init_schema(engine)
    with engine.begin() as conn:
        conn.exec_driver_sql("PRAGMA user_version = 999")
    with pytest.raises(SchemaVersionError, match="schema version 999"):
        init_schema(engine)


def test_non_mappa_database_is_refused(tmp_path: Path) -> None:
    engine = make_engine(tmp_path / "other.sqlite")
    with engine.begin() as conn:
        conn.exec_driver_sql("CREATE TABLE something_else (x INTEGER)")
    with pytest.raises(SchemaVersionError, match="not a MAPPA database"):
        init_schema(engine)


@pytest.mark.parametrize("bad_status", ["missing", "", "OK", None])
def test_status_accepts_only_the_six_values(engine: Engine, bad_status: str | None) -> None:
    """Checked by SQLite itself, so even a hand-written INSERT can't store a vague status."""
    with engine.begin() as conn:
        _add_snapshot(conn)
    with pytest.raises(IntegrityError), engine.begin() as conn:
        conn.exec_driver_sql(
            "INSERT INTO app_metadata (snapshot_id, app_id, fetched_at, status) "
            "VALUES ('dev-01', 'com.example.app', ?, ?)",
            (T0_TEXT, bad_status),
        )


def test_unknown_status_is_also_refused_before_reaching_sql(engine: Engine) -> None:
    with engine.begin() as conn:
        _add_snapshot(conn)
    with (
        pytest.raises(StatementError, match="not among the defined enum values"),
        engine.begin() as conn,
    ):
        conn.execute(insert(app_metadata).values(_metadata_row(status="missing")))


def test_a_failure_is_recorded_as_a_failure_without_evidence(engine: Engine) -> None:
    """Unknown != absent: a failed fetch is a row saying 'failed', not a missing row."""
    with engine.begin() as conn:
        _add_snapshot(conn)
        conn.execute(insert(app_metadata).values(_metadata_row(status="failed")))
        assert conn.execute(select(app_metadata.c.status)).scalar_one() == "failed"


def test_ok_row_must_point_at_evidence(engine: Engine) -> None:
    with engine.begin() as conn:
        _add_snapshot(conn)
    with pytest.raises(IntegrityError, match="ok_has_evidence"), engine.begin() as conn:
        conn.execute(insert(app_metadata).values(_metadata_row(status="ok", raw_blob=None)))


def test_evidence_pointer_must_name_a_stored_blob(engine: Engine) -> None:
    with engine.begin() as conn:
        _add_snapshot(conn)
    with pytest.raises(IntegrityError, match="FOREIGN KEY"), engine.begin() as conn:
        conn.execute(insert(app_metadata).values(_metadata_row(status="ok", raw_blob="a" * 64)))


def test_ok_row_with_stored_evidence_is_accepted(engine: Engine, store: BlobStore) -> None:
    digest = store.put(b'{"title": "Example"}', "application/json")
    with engine.begin() as conn:
        _add_snapshot(conn)
        conn.execute(insert(app_metadata).values(_metadata_row(status="ok", raw_blob=digest)))


def test_rows_need_an_existing_snapshot(engine: Engine) -> None:
    with pytest.raises(IntegrityError, match="FOREIGN KEY"), engine.begin() as conn:
        conn.execute(insert(app_metadata).values(_metadata_row(snapshot_id="ghost")))


def test_label_needs_the_apps_metadata_first(engine: Engine) -> None:
    with engine.begin() as conn:
        _add_snapshot(conn)
    with pytest.raises(IntegrityError, match="FOREIGN KEY"), engine.begin() as conn:
        conn.execute(
            insert(label_status).values(
                snapshot_id="dev-01", app_id="com.never.fetched", fetched_at=T0, status="failed"
            )
        )


def test_excluded_app_must_have_a_reason(engine: Engine) -> None:
    with engine.begin() as conn:
        _add_snapshot(conn)
    with pytest.raises(IntegrityError, match="exclusion_has_reason"), engine.begin() as conn:
        conn.execute(insert(app_metadata).values(_metadata_row(included=False)))


def test_timestamps_are_stored_as_utc_text(engine: Engine) -> None:
    aest = timezone(timedelta(hours=10))
    local = datetime(2026, 10, 5, 19, 0, tzinfo=aest)  # 09:00 UTC
    with engine.begin() as conn:
        conn.execute(
            insert(snapshots).values(snapshot_id="dev-01", started_at=local, config_json={})
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
            insert(snapshots).values(snapshot_id="dev-01", started_at=naive, config_json={})
        )


def test_config_json_cannot_be_null(engine: Engine) -> None:
    """Python None must become SQL NULL (and be refused), not the JSON text 'null'."""
    with pytest.raises(IntegrityError, match="NOT NULL"), engine.begin() as conn:
        conn.execute(
            insert(snapshots).values(snapshot_id="dev-01", started_at=T0, config_json=None)
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
                "INSERT INTO snapshots (snapshot_id, started_at, config_json) VALUES (?, ?, '{}')",
                (snapshot_id, T0_TEXT),
            )
        accepted_by_db = True
    except IntegrityError:
        accepted_by_db = False
    assert accepted_by_db == is_valid_snapshot_id(snapshot_id)
