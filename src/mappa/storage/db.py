"""SQLite engine setup, schema creation, and the checks every command makes on open.

Why SQLite: a single file with no server, easy to copy, checksum and freeze at the end of
a snapshot (M6), and more than enough for ~1,000 apps with a handful of rows each.
DuckDB can query the file directly for analysis.

Settings applied to every connection:

- ``foreign_keys = ON``: SQLite ships with foreign keys off. Without this, every
  "must point at stored evidence" rule in the schema would be decorative.
- ``journal_mode = WAL``: readers (a coverage report, a DuckDB session) don't block the
  collector while it writes, and vice versa.
- ``busy_timeout = 5000``: wait up to 5 s for a lock instead of failing at once.

Two things are checked whenever a database is opened, so mistakes fail loudly:

- Schema version, kept in SQLite's ``user_version`` header field. New code never writes
  into old tables. Until snapshot 1 exists, a bump just means a fresh dev data dir;
  after that, it means writing a migration.
- Purpose (real or synthetic), written once into ``store_meta`` when the store is
  created. Synthetic test data and real evidence can never share a database.
"""

from pathlib import Path
from typing import Any

from sqlalchemy import Connection, Engine, create_engine, event, insert, inspect, select

from mappa.models.enums import StorePurpose
from mappa.models.tables import metadata, store_meta

SCHEMA_VERSION = 2
_PURPOSE_KEY = "purpose"


class StoreError(RuntimeError):
    """The database can't be used for this command. The message says why."""


class SchemaVersionError(StoreError):
    """The database file doesn't match the schema this code expects."""


class StorePurposeError(StoreError):
    """The database holds the other kind of data (real vs synthetic)."""


def make_engine(db_path: Path) -> Engine:
    engine = create_engine(f"sqlite:///{db_path}")
    event.listen(engine, "connect", _set_sqlite_pragmas)
    return engine


def _set_sqlite_pragmas(dbapi_connection: Any, _connection_record: Any) -> None:
    cursor = dbapi_connection.cursor()
    try:
        cursor.execute("PRAGMA foreign_keys = ON")
        cursor.execute("PRAGMA journal_mode = WAL")
        cursor.execute("PRAGMA busy_timeout = 5000")
    finally:
        cursor.close()


def init_schema(engine: Engine, purpose: StorePurpose) -> None:
    """Create every table and record the store's purpose. Safe to re-run.

    The version is written *before* the tables. If init is killed halfway, the file
    already says which schema it is meant to hold, and re-running finishes the job
    (``create_all`` only creates what is missing).
    """
    with engine.begin() as conn:
        version = user_version(conn)
        if version == 0:
            if inspect(conn).get_table_names():
                raise SchemaVersionError(
                    f"{engine.url.database} has tables but no MAPPA schema version, so it "
                    "is not a MAPPA database. Move it aside or point data_dir elsewhere."
                )
            conn.exec_driver_sql(f"PRAGMA user_version = {SCHEMA_VERSION}")
        elif version != SCHEMA_VERSION:
            raise _version_error(engine, version)
        metadata.create_all(conn)
        recorded = _recorded_purpose(conn)
        if recorded is None:
            conn.execute(insert(store_meta).values(key=_PURPOSE_KEY, value=purpose.value))
        elif recorded != purpose.value:
            raise _purpose_error(engine, recorded, purpose)


def check_store(engine: Engine, purpose: StorePurpose) -> None:
    """For every command except ``init``: right schema version, right kind of data."""
    with engine.connect() as conn:
        version = user_version(conn)
        if version != SCHEMA_VERSION:
            raise _version_error(engine, version)
        recorded = _recorded_purpose(conn)
    if recorded != purpose.value:
        raise _purpose_error(engine, recorded, purpose)


def user_version(conn: Connection) -> int:
    return int(conn.exec_driver_sql("PRAGMA user_version").scalar_one())


def _recorded_purpose(conn: Connection) -> str | None:
    query = select(store_meta.c.value).where(store_meta.c.key == _PURPOSE_KEY)
    return conn.execute(query).scalar_one_or_none()


def _version_error(engine: Engine, version: int) -> SchemaVersionError:
    return SchemaVersionError(
        f"{engine.url.database} uses schema version {version}; this code expects "
        f"{SCHEMA_VERSION}. Use a fresh data dir for dev runs, or migrate the data."
    )


def _purpose_error(engine: Engine, recorded: str | None, wanted: StorePurpose) -> StorePurposeError:
    return StorePurposeError(
        f"{engine.url.database} holds {recorded or 'unmarked'} data; refusing to use it "
        f"for {wanted.value} data. Synthetic and real data never share a store."
    )
