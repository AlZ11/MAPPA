"""M3: fetch each included app's privacy policy and extract its text.

- No URL in the listing -> ``not_provided``: a finding about the developer, not an
  error. The row's ``raw_blob`` points at the listing that shows no policy link.
- Each distinct URL is fetched once per snapshot. Many apps share one developer policy;
  every app still gets its own row, pointing at the same blobs.
- Pages are rendered in headless Chromium (load plus network idle, capped). PDFs are read
  as bytes and extracted with pypdf. Cookie banners are left alone: the text is
  extracted as it stands.
- Policies split over several pages are not crawled: a short page that links to other
  privacy pages is flagged for review instead.
- ``--reextract`` recomputes the text columns from the stored pages, with no network.
"""

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any
from urllib.parse import urlsplit

from sqlalchemy import select
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

from mappa.collect.attempts import FetchOutcome, fetch_logged
from mappa.collect.context import Fetchers, StepContext, StepCrashed
from mappa.collect.fetching import host_of
from mappa.collect.metadata import included_apps
from mappa.collect.polite import run_per_domain
from mappa.log import get_logger
from mappa.models.enums import FetchKind, Status
from mappa.models.tables import app_metadata, blobs, policy_docs
from mappa.parse.policy_text import EXTRACTOR_VERSION, PolicyTextError, detect_format, extract
from mappa.storage.snapshots import require_snapshot

log = get_logger(__name__)

_DONE = (Status.OK, Status.NOT_FOUND, Status.NOT_PROVIDED)
_REUSABLE = (Status.OK, Status.NOT_FOUND)
_TEXT_COLUMNS = (
    "format",
    "text_blob",
    "text_sha256_normalized",
    "word_count",
    "language",
    "extraction_method",
    "looks_like_policy",
    "review_flag",
)


@dataclass
class PolicyReport:
    apps: int = 0
    skipped: int = 0
    not_provided: int = 0
    urls_fetched: int = 0
    rows_reused: int = 0
    by_status: dict[str, int] = field(default_factory=dict)


async def fetch_policies(
    ctx: StepContext, fetchers: Fetchers, *, limit: int | None
) -> PolicyReport:
    with ctx.engine.connect() as conn:
        require_snapshot(conn, ctx.snapshot_id)
    apps = included_apps(ctx, limit=limit)
    report = PolicyReport(apps=len(apps))
    existing = _existing_rows(ctx)
    listings = _listings(ctx)
    reusable = {
        row["url"]: row for row in existing.values() if row["status"] in _REUSABLE and row["url"]
    }

    todo: dict[str, list[str]] = {}
    for app_id in apps:
        if app_id in existing and existing[app_id]["status"] in _DONE:
            report.skipped += 1
            continue
        url, listing_blob, listing_time = listings[app_id]
        if not url:
            _write(
                ctx,
                app_id,
                {
                    "url": None,
                    "status": Status.NOT_PROVIDED,
                    "raw_blob": listing_blob,
                    "fetched_at": listing_time,
                    "detail": "the store listing gives no privacy policy URL",
                },
            )
            report.not_provided += 1
        elif not _fetchable(url):
            _write(
                ctx,
                app_id,
                {
                    "url": url,
                    "status": Status.FAILED,
                    "fetched_at": listing_time,
                    "detail": "policy URL is not an http(s) address",
                },
            )
        elif url in reusable:
            source = reusable[url]
            copied = {
                k: source[k]
                for k in ("url", "final_url", "status", "raw_blob", "fetched_at", *_TEXT_COLUMNS)
            }
            _write(
                ctx, app_id, {**copied, "detail": f"same URL as {source['app_id']}; fetched once"}
            )
            report.rows_reused += 1
        else:
            todo.setdefault(url, []).append(app_id)

    browser = fetchers.require_browser()
    attempts = fetchers.attempts(ctx)

    async def worker(item: tuple[str, list[str]]) -> None:
        url, sharing = item
        outcome = await fetch_logged(
            browser, url, ctx=attempts, kind=FetchKind.POLICY, app_id=sharing[0]
        )
        values = _from_outcome(ctx, url, outcome)
        for position, app_id in enumerate(sharing):
            detail = values.get("detail")
            if position:
                detail = f"same URL as {sharing[0]}; fetched once" + (
                    f"; {detail}" if detail else ""
                )
            _write(ctx, app_id, {**values, "detail": detail})

    crashed = await run_per_domain(
        list(todo.items()),
        host=lambda item: host_of(item[0]),
        worker=worker,
        max_parallel_domains=ctx.settings.max_parallel_domains,
    )
    report.urls_fetched = len(todo) - len(crashed)
    report.by_status = _status_counts(ctx, apps)
    log.info("policy.done", **report.__dict__)
    if crashed:
        raise StepCrashed(
            f"{len(crashed)} policy URLs hit a bug; see the log. Re-run to retry them."
        )
    return report


def reextract_policies(ctx: StepContext) -> int:
    """Recompute text columns for every fetched policy from its stored page. No network."""
    with ctx.engine.connect() as conn:
        require_snapshot(conn, ctx.snapshot_id)
        rows = conn.execute(
            select(
                policy_docs.c.app_id,
                policy_docs.c.url,
                policy_docs.c.final_url,
                policy_docs.c.raw_blob,
                blobs.c.content_type,
            )
            .join(blobs, blobs.c.sha256 == policy_docs.c.raw_blob)
            .where(policy_docs.c.snapshot_id == ctx.snapshot_id, policy_docs.c.status == Status.OK)
        ).all()
    for row in rows:
        body = ctx.store.get(row.raw_blob)
        values = _text_values(ctx, row.url, row.final_url, row.content_type, body)
        _write(ctx, row.app_id, values, partial=True)
    log.info("policy.reextracted", rows=len(rows))
    return len(rows)


def _from_outcome(ctx: StepContext, url: str, outcome: FetchOutcome) -> dict[str, Any]:
    values: dict[str, Any] = {
        "url": url,
        "final_url": outcome.response.final_url,
        "status": outcome.status,
        "raw_blob": outcome.raw_blob,
        "fetched_at": outcome.fetched_at,
        "detail": outcome.verdict.reason,
    }
    if outcome.status == Status.OK and outcome.response.body is not None:
        values.update(
            _text_values(
                ctx,
                url,
                outcome.response.final_url,
                outcome.response.content_type,
                outcome.response.body,
            )
        )
    return values


def _text_values(
    ctx: StepContext, url: str, final_url: str | None, content_type: str | None, body: bytes
) -> dict[str, Any]:
    fmt = detect_format(final_url or url, content_type, body)
    try:
        text = extract(body, fmt, final_url=final_url)
    except PolicyTextError as exc:
        return {"status": Status.FAILED, "format": fmt, "detail": str(exc)}
    return {
        "format": fmt,
        "text_blob": ctx.store.put(text.text.encode("utf-8"), "text/plain; charset=utf-8"),
        "text_sha256_normalized": text.normalized_sha256,
        "word_count": text.word_count,
        "language": text.language,
        "extraction_method": f"{text.method}; {EXTRACTOR_VERSION}",
        "looks_like_policy": text.looks_like_policy,
        "review_flag": text.review_flag,
    }


def _write(ctx: StepContext, app_id: str, values: dict[str, Any], *, partial: bool = False) -> None:
    """Upsert one app's policy row. A full write resets every column first, so nothing
    from an earlier attempt survives into a row it no longer describes."""
    base: dict[str, Any] = (
        {}
        if partial
        else {c.name: None for c in policy_docs.c if c.name not in ("snapshot_id", "app_id")}
    )
    row = {**base, **values, "snapshot_id": ctx.snapshot_id, "app_id": app_id}
    stmt = sqlite_insert(policy_docs).values(**row)
    with ctx.engine.begin() as conn:
        conn.execute(
            stmt.on_conflict_do_update(
                index_elements=["snapshot_id", "app_id"],
                set_={k: stmt.excluded[k] for k in row if k not in ("snapshot_id", "app_id")},
            )
        )


def _existing_rows(ctx: StepContext) -> dict[str, dict[str, Any]]:
    with ctx.engine.connect() as conn:
        rows = conn.execute(select(policy_docs).where(policy_docs.c.snapshot_id == ctx.snapshot_id))
        return {row.app_id: dict(row._mapping) for row in rows}


def _listings(ctx: StepContext) -> dict[str, tuple[str | None, str | None, datetime]]:
    with ctx.engine.connect() as conn:
        rows = conn.execute(
            select(
                app_metadata.c.app_id,
                app_metadata.c.privacy_policy_url,
                app_metadata.c.raw_blob,
                app_metadata.c.fetched_at,
            ).where(app_metadata.c.snapshot_id == ctx.snapshot_id)
        )
        return {r.app_id: (r.privacy_policy_url, r.raw_blob, r.fetched_at) for r in rows}


def _status_counts(ctx: StepContext, apps: list[str]) -> dict[str, int]:
    rows = _existing_rows(ctx)
    counts: dict[str, int] = {}
    for app_id in apps:
        status = str(rows[app_id]["status"]) if app_id in rows else "not attempted"
        counts[status] = counts.get(status, 0) + 1
    return counts


def _fetchable(url: str) -> bool:
    parts = urlsplit(url)
    return parts.scheme in ("http", "https") and bool(parts.hostname)
