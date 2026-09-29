"""SQLite engine setup and schema creation.

Why SQLite: a single file with no server, easy to copy, checksum and freeze at the end of
a snapshot (M6), and more than enough for ~1,000 apps with a handful of rows each.
DuckDB can query the file directly for analysis.

Settings applied to every connection:

- ``foreign_keys = ON``: SQLite ships with foreign keys off. Without this, every
  "must point at stored evidence" rule in the schema would be decorative.
- ``journal_mode = WAL``: readers (a coverage report, a DuckDB session) don't block the
  collector while it writes, and vice versa.
- ``busy_timeout = 5000``: wait up to 5 s for a lock instead of failing at once.

Schema versioning: the version is kept in SQLite's ``user_version`` header field.
Opening a database made by a different schema version fails loudly instead of letting
new code write into old tables. Until snapshot 1 exists, a bump just means starting a
fresh dev data dir; after that, it means writing a migration.
"""

from pathlib import Path
from typing import Any

from sqlalchemy import Connection, Engine, create_engine, event, inspect

from mappa.models.tables import metadata

SCHEMA_VERSION = 1


class SchemaVersionError(RuntimeError):
    """The database file doesn't match the schema this code expects."""


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


def init_schema(engine: Engine) -> None:
    """Create every table. Safe to re-run; refuses a database from another schema version.

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
            raise SchemaVersionError(
                f"{engine.url.database} uses schema version {version}; this code expects "
                f"{SCHEMA_VERSION}. Use a fresh data_dir for dev runs, or migrate the data."
            )
        metadata.create_all(conn)


def user_version(conn: Connection) -> int:
    return int(conn.exec_driver_sql("PRAGMA user_version").scalar_one())
