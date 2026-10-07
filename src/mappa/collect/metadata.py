"""M2: fetch each candidate's AU store listing, then apply the inclusion rules.

Each listing is stored twice: the page exactly as Google sent it (``raw_blob``, the
evidence) and the library's parse of it as JSON (``parsed_blob``, every field, including
ones we don't map to columns yet, such as the description). The columns are then filled
from the JSON, with type checks: a field that isn't the expected type is stored as null,
never as a guessed value.

``contains_ads`` is read from the page data directly. The library turns "field missing"
into ``False`` ("no ads"), which would turn a layout change into a finding; here a missing
field stays null (unknown != absent).

Resumability: ``ok`` and ``not_found`` listings are never fetched again. The inclusion
rules run over all stored listings after every fetch run; they are deterministic, so
re-running gives the same sample unless the pool of fetched listings changed.
"""

import json
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select, update
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

from mappa.collect.attempts import fetch_logged
from mappa.collect.context import Fetchers, StepContext
from mappa.collect.discovery import candidates
from mappa.collect.inclusion import Candidate, Decision, decide
from mappa.collect.play_store import details_url
from mappa.log import get_logger
from mappa.models.enums import DiscoverySource, FetchKind, InclusionBasis, SampleMode, Status
from mappa.models.tables import app_metadata, discovery
from mappa.parse.play_data import (
    LISTING_PARSER_VERSION,
    PlayParseError,
    extract_datasets,
    lookup,
    parse_listing_page,
)
from mappa.storage.queries import strings
from mappa.storage.snapshots import require_snapshot

log = get_logger(__name__)

_DONE = (Status.OK, Status.NOT_FOUND)
_CONTAINS_ADS_PATH = (1, 2, 48)


@dataclass
class MetadataReport:
    candidates: int = 0
    fetched: int = 0
    skipped: int = 0
    by_status: dict[str, int] = field(default_factory=dict)
    included: int = 0
    by_basis: dict[str, int] = field(default_factory=dict)
    seeds_missing: list[str] = field(default_factory=list)


async def fetch_metadata(
    ctx: StepContext, fetchers: Fetchers, *, limit: int | None
) -> MetadataReport:
    with ctx.engine.connect() as conn:
        info = require_snapshot(conn, ctx.snapshot_id)
        wanted = candidates(conn, ctx.snapshot_id)
        done = set(
            strings(
                conn,
                select(app_metadata.c.app_id).where(
                    app_metadata.c.snapshot_id == ctx.snapshot_id,
                    app_metadata.c.status.in_(_DONE),
                ),
            )
        )
    if limit is not None:
        wanted = wanted[:limit]
    report = MetadataReport(candidates=len(wanted))
    attempts = fetchers.attempts(ctx)
    for app_id in wanted:
        if app_id in done:
            report.skipped += 1
            continue
        url = details_url(app_id, lang=ctx.settings.lang, country=ctx.settings.country)
        outcome = await fetch_logged(
            fetchers.pages, url, ctx=attempts, kind=FetchKind.METADATA, app_id=app_id
        )
        report.fetched += 1
        _store(
            ctx,
            app_id,
            url,
            outcome.status,
            outcome.raw_blob,
            outcome.verdict.reason,
            outcome.fetched_at,
        )

    decisions = apply_inclusion(ctx, sample=info.sample)
    with ctx.engine.connect() as conn:
        statuses = strings(
            conn, select(app_metadata.c.status).where(app_metadata.c.snapshot_id == ctx.snapshot_id)
        )
        for status in statuses:
            report.by_status[str(status)] = report.by_status.get(str(status), 0) + 1
    for decision in decisions:
        if decision.included and decision.basis is not None:
            report.included += 1
            report.by_basis[decision.basis.value] = report.by_basis.get(decision.basis.value, 0) + 1
    report.seeds_missing = _missing_seeds(ctx, decisions)
    log.info("metadata.done", **report.__dict__)
    return report


def _store(
    ctx: StepContext,
    app_id: str,
    url: str,
    status: Status,
    raw_blob: str | None,
    reason: str | None,
    fetched_at: datetime,
) -> None:
    values: dict[str, Any] = {"status": status, "detail": reason}
    if status == Status.OK and raw_blob is not None:
        html = ctx.store.get(raw_blob).decode("utf-8", errors="replace")
        try:
            fields = parse_listing_page(html, app_id, url)
            values.update(listing_columns(fields, html))
            values["parsed_blob"] = ctx.store.put(
                json.dumps(
                    {"parser": LISTING_PARSER_VERSION, "fields": fields},
                    sort_keys=True,
                    ensure_ascii=False,
                ).encode(),
                "application/json",
            )
        except PlayParseError as exc:
            values = {"status": Status.FAILED, "detail": f"listing parse failed: {exc}"}
    row = {
        "snapshot_id": ctx.snapshot_id,
        "app_id": app_id,
        "fetched_at": fetched_at,
        "raw_blob": raw_blob,
        "included": None,
        "exclusion_reason": None,
        "inclusion_basis": None,
        **{column.name: None for column in app_metadata.c if column.name in _LISTING_COLUMNS},
        "parsed_blob": None,
        **values,
    }
    stmt = sqlite_insert(app_metadata).values(**row)
    with ctx.engine.begin() as conn:
        conn.execute(
            stmt.on_conflict_do_update(
                index_elements=["snapshot_id", "app_id"],
                set_={k: stmt.excluded[k] for k in row if k not in ("snapshot_id", "app_id")},
            )
        )


_LISTING_COLUMNS = (
    "title",
    "developer",
    "developer_id",
    "developer_email",
    "developer_website",
    "genre_id",
    "installs_min",
    "installs_real",
    "price",
    "currency",
    "free",
    "score",
    "ratings_count",
    "version",
    "updated_at",
    "released",
    "contains_ads",
    "content_rating",
    "privacy_policy_url",
)


def listing_columns(fields: dict[str, Any], html: str) -> dict[str, Any]:
    """Map the library's field names to our columns, keeping only well-typed values."""
    return {
        "title": _text(fields.get("title")),
        "developer": _text(fields.get("developer")),
        "developer_id": _text(fields.get("developerId")),
        "developer_email": _text(fields.get("developerEmail")),
        "developer_website": _text(fields.get("developerWebsite")),
        "genre_id": _text(fields.get("genreId")),
        "installs_min": _int(fields.get("minInstalls")),
        "installs_real": _int(fields.get("realInstalls")),
        "price": _number(fields.get("price")),
        "currency": _text(fields.get("currency")),
        "free": fields.get("free") if isinstance(fields.get("free"), bool) else None,
        "score": _number(fields.get("score")),
        "ratings_count": _int(fields.get("ratings")),
        "version": _text(fields.get("version")),
        "updated_at": _timestamp(fields.get("updated")),
        "released": _text(fields.get("released")),
        "contains_ads": contains_ads(html),
        "content_rating": _text(fields.get("contentRating")),
        "privacy_policy_url": _text(fields.get("privacyPolicy")),
    }


def contains_ads(html: str) -> bool | None:
    """True/False when the listing data has the ads field, None when the field is absent."""
    ds5 = extract_datasets(html).get("ds:5")
    parent = lookup(ds5, _CONTAINS_ADS_PATH[:-1])
    if not isinstance(parent, list) or len(parent) <= _CONTAINS_ADS_PATH[-1]:
        return None
    return bool(parent[_CONTAINS_ADS_PATH[-1]])


def apply_inclusion(ctx: StepContext, *, sample: SampleMode) -> list[Decision]:
    with ctx.engine.begin() as conn:
        seeds = set(
            strings(
                conn,
                select(discovery.c.app_id).where(
                    discovery.c.snapshot_id == ctx.snapshot_id,
                    discovery.c.source == DiscoverySource.SEED_FILE,
                ),
            )
        )
        rows = conn.execute(
            select(app_metadata).where(app_metadata.c.snapshot_id == ctx.snapshot_id)
        ).all()
        pool = [
            Candidate(
                app_id=row.app_id,
                status=Status(row.status),
                genre_id=row.genre_id,
                free=row.free,
                installs_min=row.installs_min,
                installs_real=row.installs_real,
                ratings_count=row.ratings_count,
                is_seed=row.app_id in seeds,
            )
            for row in rows
        ]
        decisions = decide(
            pool, ctx.settings.inclusion, seed=ctx.settings.random_seed, sample=sample
        )
        for decision in decisions:
            conn.execute(
                update(app_metadata)
                .where(
                    app_metadata.c.snapshot_id == ctx.snapshot_id,
                    app_metadata.c.app_id == decision.app_id,
                )
                .values(
                    included=decision.included,
                    exclusion_reason=decision.exclusion_reason,
                    inclusion_basis=decision.basis,
                )
            )
    return decisions


def included_apps(ctx: StepContext, *, limit: int | None) -> list[str]:
    """Included apps in a fixed order (stratum, then package name) for later steps."""
    order = {
        InclusionBasis.SEED: 0,
        InclusionBasis.DEV_SAMPLE: 0,
        InclusionBasis.TOP_INSTALLS: 1,
        InclusionBasis.RANDOM_LONG_TAIL: 2,
    }
    with ctx.engine.connect() as conn:
        rows = conn.execute(
            select(app_metadata.c.app_id, app_metadata.c.inclusion_basis).where(
                app_metadata.c.snapshot_id == ctx.snapshot_id, app_metadata.c.included.is_(True)
            )
        ).all()
    ordered = sorted(rows, key=lambda r: (order[InclusionBasis(r.inclusion_basis)], r.app_id))
    app_ids = [str(r.app_id) for r in ordered]
    return app_ids if limit is None else app_ids[:limit]


def _missing_seeds(ctx: StepContext, decisions: list[Decision]) -> list[str]:
    with ctx.engine.connect() as conn:
        seeds = strings(
            conn,
            select(discovery.c.app_id).where(
                discovery.c.snapshot_id == ctx.snapshot_id,
                discovery.c.source == DiscoverySource.SEED_FILE,
            ),
        )
        included = {d.app_id for d in decisions if d.included}
        return sorted(str(s) for s in seeds if s not in included)


def _text(value: Any) -> str | None:
    return value if isinstance(value, str) and value.strip() else None


def _int(value: Any) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def _number(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    return float(value)


def _timestamp(value: Any) -> datetime | None:
    seconds = _int(value)
    return datetime.fromtimestamp(seconds, UTC) if seconds is not None else None
