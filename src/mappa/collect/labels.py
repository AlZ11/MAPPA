"""M4: fetch each included app's Data Safety page, and parse stored pages into label rows.

Fetching and parsing are separate commands on purpose (principle 2). ``fetch-datasafety``
stores the page and records only the fetch outcome; ``parse-datasafety`` reads stored
pages, makes no network calls, and can be re-run whenever the parser changes. A re-parse
replaces an app's label rows in one transaction, so the rows always match the
``parser_version`` recorded for the app.

``label_status.status`` ends up as: ``not_found`` / ``blocked`` / ``failed`` from the
fetch, or, after parsing, ``ok`` or ``not_provided`` (the page says the developer gave
no information). A page that couldn't be parsed stays ``ok`` (we have the page) with
``parse_error`` set and no label rows.
"""

from dataclasses import dataclass, field
from datetime import datetime

from sqlalchemy import Connection, delete, insert, select, update
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

from mappa.collect.attempts import fetch_logged
from mappa.collect.context import Fetchers, StepContext
from mappa.collect.metadata import included_apps
from mappa.collect.play_store import datasafety_url
from mappa.log import get_logger
from mappa.models.enums import FetchKind, Status
from mappa.models.tables import label_facts, label_practices, label_status
from mappa.models.types import utc_now
from mappa.parse.datasafety import (
    PARSER_VERSION,
    LabelParseError,
    ParsedLabel,
    parse_datasafety_page,
)
from mappa.storage.queries import string_map
from mappa.storage.snapshots import require_snapshot

log = get_logger(__name__)

_DONE = (Status.OK, Status.NOT_PROVIDED, Status.NOT_FOUND)


@dataclass
class LabelFetchReport:
    apps: int = 0
    fetched: int = 0
    skipped: int = 0
    by_status: dict[str, int] = field(default_factory=dict)


@dataclass
class LabelParseReport:
    pages: int = 0
    ok: int = 0
    not_provided: int = 0
    parse_errors: int = 0
    facts: int = 0
    unmapped_facts: int = 0
    practices: int = 0


async def fetch_labels(
    ctx: StepContext, fetchers: Fetchers, *, limit: int | None
) -> LabelFetchReport:
    with ctx.engine.connect() as conn:
        require_snapshot(conn, ctx.snapshot_id)
        done = string_map(
            conn,
            select(label_status.c.app_id, label_status.c.status).where(
                label_status.c.snapshot_id == ctx.snapshot_id
            ),
        )
    apps = included_apps(ctx, limit=limit)
    report = LabelFetchReport(apps=len(apps))
    use_http = ctx.synthetic or ctx.settings.datasafety_fetcher == "http"
    fetcher = fetchers.pages if use_http else fetchers.require_browser()
    attempts = fetchers.attempts(ctx)
    for app_id in apps:
        if done.get(app_id) in _DONE:
            report.skipped += 1
            continue
        url = datasafety_url(app_id, lang=ctx.settings.lang, country=ctx.settings.country)
        outcome = await fetch_logged(
            fetcher, url, ctx=attempts, kind=FetchKind.DATASAFETY, app_id=app_id
        )
        report.fetched += 1
        row = {
            "snapshot_id": ctx.snapshot_id,
            "app_id": app_id,
            "fetched_at": outcome.fetched_at,
            "status": outcome.status,
            "raw_blob": outcome.raw_blob,
            "parser_version": None,
            "parsed_at": None,
            "parse_error": None,
            "detail": outcome.verdict.reason,
        }
        stmt = sqlite_insert(label_status).values(**row)
        with ctx.engine.begin() as conn:
            conn.execute(
                stmt.on_conflict_do_update(
                    index_elements=["snapshot_id", "app_id"],
                    set_={k: stmt.excluded[k] for k in row if k not in ("snapshot_id", "app_id")},
                )
            )
    with ctx.engine.connect() as conn:
        statuses = string_map(
            conn,
            select(label_status.c.app_id, label_status.c.status).where(
                label_status.c.snapshot_id == ctx.snapshot_id
            ),
        )
    for app_id in apps:
        key = str(statuses.get(app_id, "not attempted"))
        report.by_status[key] = report.by_status.get(key, 0) + 1
    log.info("labels.fetched", **report.__dict__)
    return report


def parse_labels(ctx: StepContext) -> LabelParseReport:
    """Parse every stored Data Safety page of the snapshot. No network; safe to re-run."""
    with ctx.engine.connect() as conn:
        require_snapshot(conn, ctx.snapshot_id)
        pages = conn.execute(
            select(label_status.c.app_id, label_status.c.raw_blob, label_status.c.fetched_at).where(
                label_status.c.snapshot_id == ctx.snapshot_id,
                label_status.c.raw_blob.is_not(None),
                label_status.c.status.in_([Status.OK, Status.NOT_PROVIDED]),
            )
        ).all()
    report = LabelParseReport(pages=len(pages))
    for page in pages:
        html = ctx.store.get(page.raw_blob).decode("utf-8", errors="replace")
        parsed: ParsedLabel | None = None
        error: str | None = None
        try:
            parsed = parse_datasafety_page(html)
        except LabelParseError as exc:
            error = str(exc)
        with ctx.engine.begin() as conn:
            keys = {"snapshot_id": ctx.snapshot_id, "app_id": page.app_id}
            for table in (label_facts, label_practices):
                conn.execute(delete(table).filter_by(**keys))
            if parsed is not None:
                _insert_rows(conn, keys, page.raw_blob, page.fetched_at, parsed)
            conn.execute(
                update(label_status)
                .filter_by(**keys)
                .values(
                    status=parsed.status if parsed else Status.OK,
                    parser_version=PARSER_VERSION,
                    parsed_at=utc_now(),
                    parse_error=error,
                    detail=_unmapped_note(parsed),
                )
            )
        if parsed is None:
            report.parse_errors += 1
            log.warning("labels.parse_error", app_id=page.app_id, error=error)
        elif parsed.status == Status.NOT_PROVIDED:
            report.not_provided += 1
        else:
            report.ok += 1
            report.facts += len(parsed.facts)
            report.unmapped_facts += sum(not f.mapped for f in parsed.facts)
            report.practices += len(parsed.practices)
    log.info("labels.parsed", **report.__dict__)
    return report


def _insert_rows(
    conn: Connection, keys: dict[str, str], raw_blob: str, fetched_at: datetime, parsed: ParsedLabel
) -> None:
    for fact in parsed.facts:
        conn.execute(
            insert(label_facts).values(
                **keys,
                section=fact.section,
                category=fact.category,
                data_type=fact.data_type,
                mapped=fact.mapped,
                raw_category=fact.raw_category,
                raw_label=fact.raw_label,
                optional=fact.optional,
                purposes=fact.purposes,
                raw_blob=raw_blob,
                evidence_text=fact.evidence_text,
                parser_version=PARSER_VERSION,
                fetched_at=fetched_at,
            )
        )
    for practice in parsed.practices:
        conn.execute(
            insert(label_practices).values(
                **keys,
                practice=practice.practice,
                value=practice.value,
                evidence_text=practice.evidence_text,
                raw_blob=raw_blob,
                parser_version=PARSER_VERSION,
                fetched_at=fetched_at,
            )
        )


def _unmapped_note(parsed: ParsedLabel | None) -> str | None:
    if parsed is None or not parsed.unmapped_statements:
        return None
    return "unmapped security statements: " + " || ".join(parsed.unmapped_statements)
