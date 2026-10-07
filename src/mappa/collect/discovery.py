"""M1: build the candidate list from Google Play search and the seed file (or, for a dev
snapshot, from the dev sample only), recording how every app got in.

Each search term and each input file gets one ``discovery_queries`` row, so a term that
found nothing is still on record, and a re-run knows exactly what is left to do:

- fetched and parsed -> skipped, no network;
- fetched but not parsed (parser error, or an older parser version) -> re-parsed from
  the stored page, no network;
- blocked / failed / never tried -> fetched again (with backoff).

The inputs of a snapshot are fixed once used: if seed_apps.csv changes, or a term that
was already searched disappears from queries.txt, discovery stops and asks for a new
snapshot ID rather than producing a sample nobody can describe.

Top charts (the task's optional source) are not implemented: the only available route,
the Node library, can't be checked offline, and the task allows skipping it if it can't
be verified. SAMPLING.md says so.
"""

import hashlib
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime

from sqlalchemy import Connection, func, select
from sqlalchemy.dialects.sqlite import Insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

from mappa.collect.attempts import fetch_logged
from mappa.collect.context import Fetchers, StepContext
from mappa.collect.inputs import Query, load_queries, parse_app_list
from mappa.collect.play_store import search_url
from mappa.collect.synthetic import dev_sample_csv, seed_csv
from mappa.log import get_logger
from mappa.models.enums import DiscoverySource, FetchKind, SampleMode, Status
from mappa.models.ids import is_synthetic_app_id
from mappa.models.tables import discovery, discovery_queries
from mappa.models.types import utc_now
from mappa.parse.play_data import (
    SEARCH_PARSER_VERSION,
    PlayParseError,
    SearchHit,
    parse_search_page,
)
from mappa.provenance import git_commit
from mappa.storage.snapshots import ensure_snapshot

log = get_logger(__name__)

LIST_PARSER_VERSION = "csv-1"
_DONE = (Status.OK, Status.NOT_FOUND)


class DiscoveryError(RuntimeError):
    """Discovery can't continue without a decision from the researcher."""


@dataclass
class DiscoveryReport:
    queries_total: int = 0
    queries_fetched: int = 0
    queries_reparsed: int = 0
    queries_skipped: int = 0
    queries_not_ok: Counter[str] = field(default_factory=Counter)
    zero_result_queries: list[str] = field(default_factory=list)
    apps_by_source: dict[str, int] = field(default_factory=dict)
    unique_apps: int = 0


async def discover(ctx: StepContext, fetchers: Fetchers, *, sample: SampleMode) -> DiscoveryReport:
    with ctx.engine.begin() as conn:
        ensure_snapshot(
            conn,
            ctx.snapshot_id,
            sample=sample,
            config_json=ctx.settings.snapshot_config(),
            git_commit=git_commit(),
        )
    report = DiscoveryReport()
    if sample is SampleMode.DEV:
        name, content = _input(ctx, "dev")
        _record_list(ctx, DiscoverySource.DEV_FILE, name, content)
    else:
        name, content = _input(ctx, "seed")
        _record_list(ctx, DiscoverySource.SEED_FILE, name, content)
        await _search(ctx, fetchers, load_queries(ctx.settings.queries_file), report)

    with ctx.engine.connect() as conn:
        rows = conn.execute(
            select(discovery.c.source, func.count(func.distinct(discovery.c.app_id)))
            .where(discovery.c.snapshot_id == ctx.snapshot_id)
            .group_by(discovery.c.source)
        ).all()
        report.apps_by_source = {str(source): int(count) for source, count in rows}
        report.unique_apps = len(candidates(conn, ctx.snapshot_id))
    log.info("discover.done", **_loggable(report))
    return report


def candidates(conn: Connection, snapshot_id: str) -> list[str]:
    """Distinct discovered apps: listed ones (seed / dev) first, then by best search rank,
    then by package name. Deterministic, so ``--limit N`` always picks the same N."""
    listed = discovery.c.source.in_([DiscoverySource.SEED_FILE, DiscoverySource.DEV_FILE])
    query = (
        select(discovery.c.app_id)
        .where(discovery.c.snapshot_id == snapshot_id)
        .group_by(discovery.c.app_id)
        .order_by(
            func.max(listed).desc(),
            func.min(func.coalesce(discovery.c.rank, 1_000_000)),
            discovery.c.app_id,
        )
    )
    return [str(row[0]) for row in conn.execute(query).all()]


def _input(ctx: StepContext, which: str) -> tuple[str, bytes]:
    """(name, bytes) of the seed or dev list. Synthetic stores use synthetic lists: the
    real files hold real package names, which a synthetic store refuses."""
    if ctx.synthetic:
        return (f"synthetic:{which}_apps.csv", dev_sample_csv() if which == "dev" else seed_csv())
    path = ctx.settings.dev_sample_file if which == "dev" else ctx.settings.seed_file
    try:
        return path.name, path.read_bytes()
    except FileNotFoundError:
        raise DiscoveryError(f"{which} list not found: {path}") from None


def _record_list(ctx: StepContext, source: DiscoverySource, name: str, content: bytes) -> None:
    """Record a seed/dev list once per snapshot; refuse if the file has changed since."""
    with ctx.engine.begin() as conn:
        existing = conn.execute(
            select(discovery_queries.c.raw_blob).where(
                discovery_queries.c.snapshot_id == ctx.snapshot_id,
                discovery_queries.c.source == source,
                discovery_queries.c.query == name,
            )
        ).scalar_one_or_none()
        digest = hashlib.sha256(content).hexdigest()
        if existing is not None:
            if existing != digest:
                raise DiscoveryError(
                    f"{name} has changed since snapshot {ctx.snapshot_id} recorded it; "
                    "a snapshot's inputs are fixed. Restore the file or use a new snapshot ID."
                )
            return
        app_ids = parse_app_list(content, name)
        _check_app_ids(ctx, app_ids, name)
        blob = ctx.store.put(content, "text/csv; charset=utf-8", conn=conn)
        now = utc_now()
        conn.execute(
            insert_query_row(
                ctx, source, name, None, now, Status.OK, blob, len(app_ids), None, None
            )
        )
        for rank, app_id in enumerate(app_ids, start=1):
            conn.execute(_insert_hit(ctx, source, name, app_id, rank, now, blob))
    log.info("discover.list_recorded", source=source.value, name=name, apps=len(app_ids))


async def _search(
    ctx: StepContext, fetchers: Fetchers, queries: list[Query], report: DiscoveryReport
) -> None:
    report.queries_total = len(queries)
    with ctx.engine.connect() as conn:
        recorded = {
            row.query: row
            for row in conn.execute(
                select(discovery_queries).where(
                    discovery_queries.c.snapshot_id == ctx.snapshot_id,
                    discovery_queries.c.source == DiscoverySource.SEARCH,
                )
            )
        }
    removed = sorted(set(recorded) - {q.text for q in queries})
    if removed:
        raise DiscoveryError(
            f"queries.txt no longer has terms this snapshot already searched: {removed[:5]}. "
            "A snapshot's search terms are fixed; restore them or use a new snapshot ID."
        )
    attempts = fetchers.attempts(ctx)
    for query in queries:
        row = recorded.get(query.text)
        if row is not None and row.status in _DONE:
            if row.status == Status.OK and (
                row.parse_error is not None or row.parser_version != SEARCH_PARSER_VERSION
            ):
                _store_search(ctx, query, row.fetched_at, Status.OK, row.raw_blob, None, report)
                report.queries_reparsed += 1
            else:
                report.queries_skipped += 1
            continue
        outcome = await fetch_logged(
            fetchers.pages,
            search_url(query.text, lang=ctx.settings.lang, country=ctx.settings.country),
            ctx=attempts,
            kind=FetchKind.SEARCH,
            app_id=None,
        )
        report.queries_fetched += 1
        _store_search(
            ctx,
            query,
            outcome.fetched_at,
            outcome.status,
            outcome.raw_blob,
            outcome.verdict.reason,
            report,
        )


def _store_search(
    ctx: StepContext,
    query: Query,
    fetched_at: datetime,
    status: Status,
    raw_blob: str | None,
    reason: str | None,
    report: DiscoveryReport,
) -> None:
    hits: list[SearchHit] = []
    n_results: int | None = None
    parse_error: str | None = None
    note = reason
    if status == Status.OK and raw_blob is not None:
        try:
            page = parse_search_page(ctx.store.get(raw_blob).decode("utf-8", errors="replace"))
            hits, n_results, note = page.hits, len(page.hits), page.note
            _check_app_ids(ctx, [h.app_id for h in hits], f"search {query.text!r}")
        except PlayParseError as exc:
            parse_error = str(exc)
    else:
        report.queries_not_ok[status.value] += 1
    if n_results == 0:
        report.zero_result_queries.append(query.text)
    with ctx.engine.begin() as conn:
        stmt = insert_query_row(
            ctx,
            DiscoverySource.SEARCH,
            query.text,
            query.category,
            fetched_at,
            status,
            raw_blob,
            n_results,
            parse_error,
            note,
        )
        conn.execute(
            stmt.on_conflict_do_update(
                index_elements=["snapshot_id", "source", "query"],
                set_={
                    "fetched_at": stmt.excluded.fetched_at,
                    "status": stmt.excluded.status,
                    "raw_blob": stmt.excluded.raw_blob,
                    "n_results": stmt.excluded.n_results,
                    "parser_version": stmt.excluded.parser_version,
                    "parse_error": stmt.excluded.parse_error,
                    "detail": stmt.excluded.detail,
                },
            )
        )
        for hit in hits:
            assert raw_blob is not None
            conn.execute(
                _insert_hit(
                    ctx,
                    DiscoverySource.SEARCH,
                    query.text,
                    hit.app_id,
                    hit.rank,
                    utc_now(),
                    raw_blob,
                )
            )


def insert_query_row(
    ctx: StepContext,
    source: DiscoverySource,
    query: str,
    category: str | None,
    fetched_at: datetime,
    status: Status,
    raw_blob: str | None,
    n_results: int | None,
    parse_error: str | None,
    detail: str | None,
) -> Insert:
    parser = LIST_PARSER_VERSION if source is not DiscoverySource.SEARCH else SEARCH_PARSER_VERSION
    return sqlite_insert(discovery_queries).values(
        snapshot_id=ctx.snapshot_id,
        source=source,
        query=query,
        category=category,
        fetched_at=fetched_at,
        status=status,
        raw_blob=raw_blob,
        n_results=n_results,
        parser_version=parser if status == Status.OK else None,
        parse_error=parse_error,
        detail=detail,
    )


def _insert_hit(
    ctx: StepContext,
    source: DiscoverySource,
    query: str,
    app_id: str,
    rank: int,
    discovered_at: datetime,
    raw_blob: str,
) -> Insert:
    return (
        sqlite_insert(discovery)
        .values(
            snapshot_id=ctx.snapshot_id,
            app_id=app_id,
            source=source,
            query=query,
            rank=rank,
            discovered_at=discovered_at,
            raw_blob=raw_blob,
        )
        .on_conflict_do_nothing(index_elements=["snapshot_id", "source", "query", "app_id"])
    )


def _check_app_ids(ctx: StepContext, app_ids: list[str], where: str) -> None:
    """Fail with a readable message before the database refuses the write."""
    wrong = [a for a in app_ids if is_synthetic_app_id(a) != ctx.synthetic]
    if wrong:
        kind = "real" if ctx.synthetic else "synthetic"
        raise DiscoveryError(
            f"{where}: {kind} app IDs in a {ctx.purpose.value} store ({wrong[:3]}); "
            "synthetic and real data never mix"
        )


def _loggable(report: DiscoveryReport) -> dict[str, object]:
    return {
        "queries_total": report.queries_total,
        "queries_fetched": report.queries_fetched,
        "queries_reparsed": report.queries_reparsed,
        "queries_skipped": report.queries_skipped,
        "queries_not_ok": dict(report.queries_not_ok),
        "zero_result_queries": len(report.zero_result_queries),
        "apps_by_source": report.apps_by_source,
        "unique_apps": report.unique_apps,
    }
