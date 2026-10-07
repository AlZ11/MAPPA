"""Fetch one URL with retries, recording every attempt and keeping every response body.

Why each attempt gets its own ``fetch_log`` row: "blocked after 4 tries over 30 s" and
"blocked once" are different findings, and the report's failure reasons and the ethics
record ("we backed off; we did not bypass") both need the full history.

Why the body is stored even for errors: a 404 page or a captcha page is evidence of
*why* something is missing, and costs almost nothing to keep.
"""

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import Engine, insert
from tenacity import AsyncRetrying, retry_if_result, stop_after_attempt, wait_exponential_jitter

from mappa.collect.fetching import Fetcher, Response, Verdict, classify
from mappa.config import RetrySettings
from mappa.log import get_logger
from mappa.models.enums import FetchKind, Status
from mappa.models.tables import fetch_log
from mappa.models.types import utc_now
from mappa.storage.blobs import BlobStore

log = get_logger(__name__)

Sleep = Callable[[float], Awaitable[None]]


@dataclass(frozen=True)
class FetchOutcome:
    """The last attempt's result: what the item's status row should say."""

    response: Response
    verdict: Verdict
    attempts: int
    raw_blob: str | None
    fetched_at: datetime

    @property
    def status(self) -> Status:
        return self.verdict.status


@dataclass(frozen=True)
class AttemptContext:
    """Everything needed to record attempts for one snapshot."""

    engine: Engine
    store: BlobStore
    snapshot_id: str
    retry: RetrySettings
    sleep: Sleep


async def fetch_logged(
    fetcher: Fetcher,
    url: str,
    *,
    ctx: AttemptContext,
    kind: FetchKind,
    app_id: str | None,
) -> FetchOutcome:
    """Fetch ``url``; retry blocked/transient failures with exponential backoff."""
    attempt = 0

    async def once() -> FetchOutcome:
        nonlocal attempt
        attempt += 1
        started = utc_now()
        response = await fetcher.fetch(url)
        finished = utc_now()
        verdict = classify(response)
        blob = None
        if response.body is not None:
            blob = ctx.store.put(response.body, response.content_type or "application/octet-stream")
        with ctx.engine.begin() as conn:
            conn.execute(
                insert(fetch_log).values(
                    snapshot_id=ctx.snapshot_id,
                    app_id=app_id,
                    kind=kind,
                    url=url,
                    final_url=response.final_url,
                    http_status=response.http_status,
                    status=verdict.status,
                    error=verdict.reason,
                    attempt=attempt,
                    started_at=started,
                    finished_at=finished,
                    raw_blob=blob,
                )
            )
        log.info(
            f"fetch.{verdict.status.value}",
            kind=kind.value,
            app_id=app_id,
            url=url,
            attempt=attempt,
            http_status=response.http_status,
            reason=verdict.reason,
        )
        return FetchOutcome(response, verdict, attempt, blob, finished)

    retrying = AsyncRetrying(
        stop=stop_after_attempt(ctx.retry.max_attempts),
        wait=wait_exponential_jitter(
            initial=ctx.retry.backoff_initial_s, max=ctx.retry.backoff_max_s
        ),
        retry=retry_if_result(lambda outcome: outcome.verdict.retryable),
        retry_error_callback=lambda state: state.outcome.result() if state.outcome else None,
        sleep=ctx.sleep,
    )
    outcome: FetchOutcome = await retrying(once)
    return outcome
