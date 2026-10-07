"""Database tables for Task 01 (collector + Data Safety label reader), as SQLAlchemy Core.

Why Core and not the ORM: these rows are records of evidence, not objects with
behaviour. Core keeps the SQL visible (easy to audit, easy to reproduce in DuckDB)
without an identity map or lazy loading getting in the way of bulk, resumable writes.

Rules the schema enforces itself, so a buggy collector cannot quietly break them:

- Every evidence table's key starts with ``snapshot_id``, a foreign key to
  ``snapshots``: later snapshots sit alongside this one, nothing exists outside one.
- Status columns accept only the six status values (principle 4). There is no "empty".
- A row with ``status = 'ok'`` must point at its raw evidence, and every blob pointer is
  a foreign key into ``blobs``: the database cannot reference evidence that was never
  stored (principle 1).
- Per-app results (policy, label, APK) need a metadata row for the same app, and parsed
  label rows need a fetched label: the pipeline's order is written into the schema.
- Once a snapshot is frozen, triggers refuse every insert, update or delete of its rows,
  and blob records can never be changed or removed.
- Synthetic and real data never mix: a store records its purpose once, and triggers
  refuse synthetic snapshot or app IDs in a real store (and real ones in a synthetic
  store), whatever code path tries to write them.
- Timestamps are timezone-aware UTC (see ``types.py``).

Additions to the column lists in docs/tasks/01_collection.md, and why:

- ``blobs``: the blob store's sidecar table (content type, size, first-seen time). It is
  not per-snapshot: identical bytes are one blob, shared by every snapshot that saw them.
- ``store_meta``: records whether this data directory holds real or synthetic data.
- ``runs``: one row per CLI command, for the audit trail and the report's run time.
- ``snapshots.sample`` (full or dev) and ``snapshots.frozen_at``.
- ``fetched_at`` on policy_docs, label_status, label_facts, label_practices and apks:
  principle 3 says every record carries it; the task's column lists omit it.
- ``discovery_queries``: one row per search query / input file, so a query that found
  nothing is still on record and discovery can resume. ``discovery`` rows point at the
  saved results page they came from.
- ``fetch_log.kind`` also allows ``search`` and ``chart``, with ``app_id`` null.
- ``app_metadata``: ``installs_real`` (Google's exact count, to rank apps that share an
  install bucket), ``currency``, ``parsed_blob`` (the parsed listing as JSON),
  ``inclusion_basis`` (which stratum selected the app) and ``detail``.
- ``detail`` on app_metadata, policy_docs, label_status and apks: why a row has the
  status it has (last error, skip reason), so failure reasons can be counted.
- ``label_facts.raw_category``: the raw category heading, so an unmapped category is
  never lost. ``category``/``data_type`` hold taxonomy values only, null when unmapped.
- ``label_status.parsed_at``: when the current parse was written.
- ``apks.dex_date`` is text exactly as the source gives it: it carries no time zone, so
  storing it as UTC would invent precision we don't have.

Changing any table means bumping ``SCHEMA_VERSION`` in storage/db.py.
"""

from enum import StrEnum
from typing import Any

from sqlalchemy import (
    DDL,
    JSON,
    Boolean,
    CheckConstraint,
    Column,
    Enum,
    Float,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    MetaData,
    PrimaryKeyConstraint,
    String,
    Table,
    Text,
    UniqueConstraint,
    event,
)

from mappa.models.enums import (
    DiscoverySource,
    FetchKind,
    InclusionBasis,
    LabelPractice,
    LabelSection,
    PolicyFormat,
    RunStatus,
    SampleMode,
    Status,
)
from mappa.models.ids import (
    SNAPSHOT_ID_MAX_LEN,
    SNAPSHOT_ID_SQL_CHECK,
    SYNTHETIC_APP_PREFIX,
    SYNTHETIC_SNAPSHOT_PREFIX,
)
from mappa.models.types import UTCDateTime

# Named constraints make error messages readable ("CHECK constraint failed:
# ck_app_metadata_ok_has_evidence") and keep future migrations possible.
NAMING_CONVENTION = {
    "ix": "ix_%(table_name)s_%(column_0_N_name)s",
    "uq": "uq_%(table_name)s_%(column_0_N_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_N_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}

metadata = MetaData(naming_convention=NAMING_CONVENTION)

SHA256_SQL_CHECK = "length({col}) = 64 AND {col} NOT GLOB '*[^0-9a-f]*'"
FROZEN_MESSAGE = "snapshot is frozen: its rows cannot change"


def _enum_values(enum_cls: type[StrEnum]) -> list[str]:
    return [member.value for member in enum_cls]


def _enum(name: str, enum_cls: type[StrEnum], *, nullable: bool = False) -> Column[Any]:
    """Text column limited to the enum's values, checked in Python and by SQLite."""
    return Column(
        name,
        Enum(
            enum_cls,
            name=name,
            native_enum=False,
            create_constraint=True,
            validate_strings=True,
            values_callable=_enum_values,
        ),
        nullable=nullable,
    )


def _status() -> Column[Any]:
    return _enum("status", Status)


def _flag(name: str, *, nullable: bool = True) -> Column[Any]:
    """Boolean stored as 0/1. Nullable by default: null means "not known", never false."""
    return Column(name, Boolean(create_constraint=True, name=name), nullable=nullable)


def _timestamp(name: str, *, nullable: bool = False) -> Column[Any]:
    return Column(name, UTCDateTime(), nullable=nullable)


def _blob(name: str, *, nullable: bool = True) -> Column[Any]:
    """Pointer to raw evidence; must name a blob the blob store has recorded."""
    return Column(name, String(64), ForeignKey("blobs.sha256"), nullable=nullable)


def _snapshot_id() -> Column[Any]:
    return Column(
        "snapshot_id",
        String(SNAPSHOT_ID_MAX_LEN),
        ForeignKey("snapshots.snapshot_id"),
        nullable=False,
    )


def _app_id() -> Column[Any]:
    return Column("app_id", String, nullable=False)


def _detail() -> Column[Any]:
    return Column("detail", Text)


def _ok_has_evidence(expression: str = "raw_blob IS NOT NULL") -> CheckConstraint:
    return CheckConstraint(f"status != 'ok' OR ({expression})", name="ok_has_evidence")


def _requires(table: str) -> ForeignKeyConstraint:
    """(snapshot_id, app_id) must already exist in ``table``."""
    return ForeignKeyConstraint(
        ["snapshot_id", "app_id"], [f"{table}.snapshot_id", f"{table}.app_id"]
    )


store_meta = Table(
    "store_meta",
    metadata,
    Column("key", String, primary_key=True),
    Column("value", String, nullable=False),
)

snapshots = Table(
    "snapshots",
    metadata,
    Column("snapshot_id", String(SNAPSHOT_ID_MAX_LEN), primary_key=True),
    _enum("sample", SampleMode),
    _timestamp("started_at"),
    _timestamp("finished_at", nullable=True),
    _timestamp("frozen_at", nullable=True),
    Column("git_commit", String, nullable=True),
    Column("config_json", JSON(none_as_null=True), nullable=False),
    Column("notes", Text, nullable=True),
    CheckConstraint(SNAPSHOT_ID_SQL_CHECK, name="snapshot_id_format"),
)

runs = Table(
    "runs",
    metadata,
    Column("run_id", String(32), primary_key=True),
    Column("command", String, nullable=False),
    # Not a foreign key: a run can fail before its snapshot exists, and is still recorded.
    Column("snapshot_id", String(SNAPSHOT_ID_MAX_LEN), nullable=True),
    _timestamp("started_at"),
    _timestamp("finished_at", nullable=True),
    _enum("status", RunStatus),
    Column("git_commit", String, nullable=True),
    Column("detail", Text),
)

blobs = Table(
    "blobs",
    metadata,
    Column("sha256", String(64), primary_key=True),
    Column("content_type", String, nullable=False),
    Column("size_bytes", Integer, nullable=False),
    _timestamp("first_seen_at"),
    CheckConstraint(SHA256_SQL_CHECK.format(col="sha256"), name="sha256_format"),
    CheckConstraint("length(content_type) > 0", name="content_type_present"),
    CheckConstraint("size_bytes >= 0", name="size_non_negative"),
)

discovery_queries = Table(
    "discovery_queries",
    metadata,
    Column("id", Integer, primary_key=True),
    _snapshot_id(),
    _enum("source", DiscoverySource),
    Column("query", String, nullable=False),
    Column("category", String),
    _timestamp("fetched_at"),
    _status(),
    _blob("raw_blob"),
    Column("n_results", Integer),
    Column("parser_version", String),
    Column("parse_error", Text),
    _detail(),
    UniqueConstraint("snapshot_id", "source", "query"),
    _ok_has_evidence(),
    CheckConstraint("n_results IS NULL OR n_results >= 0", name="n_results_non_negative"),
)

discovery = Table(
    "discovery",
    metadata,
    Column("id", Integer, primary_key=True),
    _snapshot_id(),
    _app_id(),
    _enum("source", DiscoverySource),
    Column("query", String, nullable=False),
    Column("rank", Integer, nullable=True),
    _timestamp("discovered_at"),
    _blob("raw_blob", nullable=False),
    UniqueConstraint("snapshot_id", "source", "query", "app_id"),
    Index("ix_discovery_snapshot_app", "snapshot_id", "app_id"),
)

app_metadata = Table(
    "app_metadata",
    metadata,
    _snapshot_id(),
    _app_id(),
    _timestamp("fetched_at"),
    _status(),
    Column("title", Text),
    Column("developer", Text),
    Column("developer_id", String),
    Column("developer_email", String),
    Column("developer_website", Text),
    Column("genre_id", String),
    Column("installs_min", Integer),
    Column("installs_real", Integer),
    Column("price", Float),
    Column("currency", String),
    _flag("free"),
    Column("score", Float),
    Column("ratings_count", Integer),
    Column("version", String),
    _timestamp("updated_at", nullable=True),  # the store's "updated on", not this row's
    Column("released", String),
    _flag("contains_ads"),
    Column("content_rating", String),
    Column("privacy_policy_url", Text),
    _blob("raw_blob"),
    _blob("parsed_blob"),
    _flag("included"),  # null until the inclusion rules have run
    Column("exclusion_reason", Text),
    _enum("inclusion_basis", InclusionBasis, nullable=True),
    _detail(),
    PrimaryKeyConstraint("snapshot_id", "app_id"),
    _ok_has_evidence("raw_blob IS NOT NULL AND parsed_blob IS NOT NULL"),
    CheckConstraint(
        "(included IS NULL AND exclusion_reason IS NULL AND inclusion_basis IS NULL)"
        " OR (included = 1 AND exclusion_reason IS NULL AND inclusion_basis IS NOT NULL)"
        " OR (included = 0 AND exclusion_reason IS NOT NULL AND inclusion_basis IS NULL)",
        name="inclusion_decided",
    ),
)

fetch_log = Table(
    "fetch_log",
    metadata,
    Column("id", Integer, primary_key=True),
    _snapshot_id(),
    Column("app_id", String, nullable=True),  # null for search/chart requests
    _enum("kind", FetchKind),
    Column("url", Text, nullable=False),
    Column("final_url", Text),
    Column("http_status", Integer),
    _status(),
    Column("error", Text),
    Column("attempt", Integer, nullable=False),
    _timestamp("started_at"),
    _timestamp("finished_at"),
    _blob("raw_blob"),
    _ok_has_evidence(),
    CheckConstraint("attempt >= 1", name="attempt_positive"),
    CheckConstraint(
        "kind IN ('search', 'chart') OR app_id IS NOT NULL", name="app_request_has_app_id"
    ),
    Index("ix_fetch_log_lookup", "snapshot_id", "kind", "app_id"),
)

policy_docs = Table(
    "policy_docs",
    metadata,
    _snapshot_id(),
    _app_id(),
    _timestamp("fetched_at"),
    Column("url", Text),  # null when the listing gives no policy URL (not_provided)
    Column("final_url", Text),
    _status(),
    _enum("format", PolicyFormat, nullable=True),
    # The evidence for this row's status: the policy as fetched, or, for not_provided,
    # the store listing that shows no policy link.
    _blob("raw_blob"),
    _blob("text_blob"),
    Column("text_sha256_normalized", String(64)),
    Column("word_count", Integer),
    Column("language", String),
    Column("extraction_method", String),
    _flag("looks_like_policy"),
    Column("review_flag", Text),
    _detail(),
    PrimaryKeyConstraint("snapshot_id", "app_id"),
    _requires("app_metadata"),
    _ok_has_evidence("raw_blob IS NOT NULL AND text_blob IS NOT NULL"),
)

label_status = Table(
    "label_status",
    metadata,
    _snapshot_id(),
    _app_id(),
    _timestamp("fetched_at"),
    _status(),
    _blob("raw_blob"),
    Column("parser_version", String),
    _timestamp("parsed_at", nullable=True),
    Column("parse_error", Text),
    _detail(),
    PrimaryKeyConstraint("snapshot_id", "app_id"),
    _requires("app_metadata"),
    _ok_has_evidence(),
)

label_facts = Table(
    "label_facts",
    metadata,
    Column("id", Integer, primary_key=True),
    _snapshot_id(),
    _app_id(),
    _enum("section", LabelSection),
    Column("category", String),  # taxonomy value; null when unmapped
    Column("data_type", String),  # taxonomy value; null when unmapped
    _flag("mapped", nullable=False),
    Column("raw_category", String, nullable=False),
    Column("raw_label", String, nullable=False),
    _flag("optional"),
    Column("purposes", JSON(none_as_null=True)),
    _blob("raw_blob", nullable=False),
    Column("evidence_text", Text, nullable=False),
    Column("parser_version", String, nullable=False),
    _timestamp("fetched_at"),
    _requires("label_status"),
    CheckConstraint(
        "mapped = 0 OR (category IS NOT NULL AND data_type IS NOT NULL)",
        name="mapped_has_taxonomy",
    ),
    Index("ix_label_facts_snapshot_app", "snapshot_id", "app_id"),
)

label_practices = Table(
    "label_practices",
    metadata,
    Column("id", Integer, primary_key=True),
    _snapshot_id(),
    _app_id(),
    _enum("practice", LabelPractice),
    _flag("value", nullable=False),
    Column("evidence_text", Text, nullable=False),
    _blob("raw_blob", nullable=False),
    Column("parser_version", String, nullable=False),
    _timestamp("fetched_at"),
    _requires("label_status"),
    UniqueConstraint("snapshot_id", "app_id", "practice"),
)

apks = Table(
    "apks",
    metadata,
    _snapshot_id(),
    _app_id(),
    _timestamp("fetched_at"),
    _status(),
    Column("source", String),
    Column("sha256", String(64)),
    Column("vercode", Integer),
    Column("version_name", String),
    Column("dex_date", String),
    Column("size_bytes", Integer),
    Column("path", Text),
    Column("store_version", String),
    _flag("version_match"),
    _detail(),
    PrimaryKeyConstraint("snapshot_id", "app_id"),
    _requires("app_metadata"),
    _ok_has_evidence("sha256 IS NOT NULL AND path IS NOT NULL"),
    CheckConstraint(
        f"sha256 IS NULL OR ({SHA256_SQL_CHECK.format(col='sha256')})", name="sha256_format"
    ),
)

# Tables whose rows belong to one snapshot and must not change once it is frozen.
SNAPSHOT_TABLES = (
    discovery_queries,
    discovery,
    app_metadata,
    fetch_log,
    policy_docs,
    label_status,
    label_facts,
    label_practices,
    apks,
)


def _refuse(table: Table, name: str, op: str, message: str, when: str | None = None) -> None:
    """Attach a trigger that aborts ``op`` on ``table`` (when ``when`` holds)."""
    condition = f"WHEN {when} " if when else ""
    ddl = DDL(  # type: ignore[no-untyped-call]  # SQLAlchemy leaves DDL() unannotated
        f"CREATE TRIGGER trg_{table.name}_{name} BEFORE {op} ON {table.name} "
        f"{condition}BEGIN SELECT RAISE(ABORT, '{message}'); END"
    )
    event.listen(table, "after_create", ddl)


def _frozen(ref: str) -> str:
    return f"(SELECT frozen_at FROM snapshots WHERE snapshot_id = {ref}.snapshot_id) IS NOT NULL"


MIXING_MESSAGE = "synthetic and real data never mix: this ID does not match the store"
_STORE_IS = "(SELECT value FROM store_meta WHERE key = 'purpose') = '{}'"


def _mismatched(column: str, prefix: str) -> str:
    """True when a synthetic-looking ID goes into a real store, or the reverse."""
    synthetic = f"substr(NEW.{column}, 1, {len(prefix)}) = '{prefix}'"
    return (
        f"({_STORE_IS.format('real')} AND {synthetic})"
        f" OR ({_STORE_IS.format('synthetic')} AND NOT {synthetic})"
    )


_refuse(
    snapshots,
    "store_mismatch",
    "INSERT",
    MIXING_MESSAGE,
    _mismatched("snapshot_id", SYNTHETIC_SNAPSHOT_PREFIX),
)
for _table in SNAPSHOT_TABLES:
    if "app_id" in _table.c:
        _refuse(
            _table,
            "store_mismatch",
            "INSERT",
            MIXING_MESSAGE,
            f"NEW.app_id IS NOT NULL AND ({_mismatched('app_id', SYNTHETIC_APP_PREFIX)})",
        )

for _table in SNAPSHOT_TABLES:
    _refuse(_table, "frozen_insert", "INSERT", FROZEN_MESSAGE, _frozen("NEW"))
    _refuse(
        _table, "frozen_update", "UPDATE", FROZEN_MESSAGE, f"{_frozen('OLD')} OR {_frozen('NEW')}"
    )
    _refuse(_table, "frozen_delete", "DELETE", FROZEN_MESSAGE, _frozen("OLD"))

_refuse(snapshots, "frozen_update", "UPDATE", FROZEN_MESSAGE, "OLD.frozen_at IS NOT NULL")
_refuse(snapshots, "frozen_delete", "DELETE", FROZEN_MESSAGE, "OLD.frozen_at IS NOT NULL")
_refuse(blobs, "write_once_update", "UPDATE", "blob records are write-once")
_refuse(blobs, "write_once_delete", "DELETE", "blob records are write-once")
_refuse(store_meta, "fixed_update", "UPDATE", "store_meta is fixed when the store is created")
_refuse(store_meta, "fixed_delete", "DELETE", "store_meta is fixed when the store is created")
