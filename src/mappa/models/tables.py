"""Database tables for Task 01 (collector + Data Safety label reader), as SQLAlchemy Core.

Why Core and not the ORM: these rows are records of evidence, not objects with
behaviour. Core keeps the SQL visible (easy to audit, easy to reproduce in DuckDB)
without an identity map or lazy loading getting in the way of bulk, resumable writes.

Rules the schema enforces itself, so a buggy collector cannot quietly break them:

- Every table's key starts with ``snapshot_id``, a foreign key to ``snapshots``: later
  snapshots sit alongside this one, and nothing exists outside a snapshot.
- Status columns accept only the six status values (principle 4). There is no "empty".
- A row with ``status = 'ok'`` must point at its raw evidence, and every blob pointer is
  a foreign key into ``blobs``: the database cannot reference evidence that was never
  stored (principle 1).
- Per-app results (policy, label, APK) need a metadata row for the same app, and parsed
  label rows need a fetched label: the pipeline's order is written into the schema.
- Timestamps are timezone-aware UTC (see ``types.py``).

Deviations from the column lists in docs/tasks/01_collection.md, and why:

- ``blobs``: the blob store's sidecar table (content type, size, first-seen time). It is
  not per-snapshot: identical bytes are one blob, shared by every snapshot that saw them.
- ``fetched_at`` added to policy_docs, label_status, label_facts, label_practices and
  apks: principle 3 says every record carries it; the task's column lists omit it.
- ``discovery``: an ``id`` key, ``raw_blob`` (the saved search results / chart / seed
  file the row came from) and a uniqueness rule, so re-running discovery adds nothing.
  ``query`` is never null: for charts it names the chart, for seed files the file.
- ``fetch_log``: ``kind`` also allows ``search`` and ``chart``, with ``app_id`` null,
  so discovery requests are logged and resumable like every other request.
- ``label_facts.raw_category``: the raw category heading, so an unmapped category is
  never lost. ``category``/``data_type`` hold taxonomy values only, null when unmapped.
- ``label_status.parsed_at``: when the current parse was written.
- ``apks.dex_date`` is text exactly as AndroZoo gives it: it carries no time zone, so
  storing it as UTC would invent precision we don't have.

Column types for store-listing fields are provisional until M2 checks them against a
real response. Changing any table means bumping ``SCHEMA_VERSION`` in storage/db.py.
"""

from enum import StrEnum
from typing import Any

from sqlalchemy import (
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
)

from mappa.models.enums import (
    DiscoverySource,
    FetchKind,
    LabelPractice,
    LabelSection,
    PolicyFormat,
    Status,
)
from mappa.models.ids import SNAPSHOT_ID_MAX_LEN, SNAPSHOT_ID_SQL_CHECK
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


def _ok_has_evidence(expression: str = "raw_blob IS NOT NULL") -> CheckConstraint:
    return CheckConstraint(f"status != 'ok' OR ({expression})", name="ok_has_evidence")


def _requires(table: str) -> ForeignKeyConstraint:
    """(snapshot_id, app_id) must already exist in ``table``."""
    return ForeignKeyConstraint(
        ["snapshot_id", "app_id"], [f"{table}.snapshot_id", f"{table}.app_id"]
    )


snapshots = Table(
    "snapshots",
    metadata,
    Column("snapshot_id", String(SNAPSHOT_ID_MAX_LEN), primary_key=True),
    _timestamp("started_at"),
    _timestamp("finished_at", nullable=True),
    Column("git_commit", String, nullable=True),
    Column("config_json", JSON(none_as_null=True), nullable=False),
    Column("notes", Text, nullable=True),
    CheckConstraint(SNAPSHOT_ID_SQL_CHECK, name="snapshot_id_format"),
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
    Column("price", Float),
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
    _flag("included"),  # null until the inclusion rules have run
    Column("exclusion_reason", Text),
    PrimaryKeyConstraint("snapshot_id", "app_id"),
    _ok_has_evidence(),
    CheckConstraint(
        "(included IS NULL AND exclusion_reason IS NULL)"
        " OR (included = 1 AND exclusion_reason IS NULL)"
        " OR (included = 0 AND exclusion_reason IS NOT NULL)",
        name="exclusion_has_reason",
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
    _blob("raw_blob"),
    _blob("text_blob"),
    Column("text_sha256_normalized", String(64)),
    Column("word_count", Integer),
    Column("language", String),
    Column("extraction_method", String),
    _flag("looks_like_policy"),
    Column("review_flag", Text),
    PrimaryKeyConstraint("snapshot_id", "app_id"),
    _requires("app_metadata"),
    _ok_has_evidence(),
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
    PrimaryKeyConstraint("snapshot_id", "app_id"),
    _requires("app_metadata"),
    _ok_has_evidence("sha256 IS NOT NULL AND path IS NOT NULL"),
    CheckConstraint(
        f"sha256 IS NULL OR ({SHA256_SQL_CHECK.format(col='sha256')})", name="sha256_format"
    ),
)
