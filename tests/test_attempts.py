"""Retries with backoff, and a fetch_log row plus the raw body for every attempt."""

import asyncio
from collections.abc import Sequence

import pytest
from sqlalchemy import Engine, select

from mappa.collect.attempts import AttemptContext, FetchOutcome, fetch_logged
from mappa.collect.fetching import Response
from mappa.config import RetrySettings
from mappa.models.enums import FetchKind, Status
from mappa.models.tables import fetch_log
from mappa.storage.blobs import BlobStore
from tests.conftest import add_snapshot

URL = "https://play.google.com/store/apps/details?id=com.example.app&hl=en&gl=AU"


class ScriptedFetcher:
    """Answers with the given HTTP statuses in turn, and counts calls."""

    def __init__(self, statuses: Sequence[int | None]) -> None:
        self.statuses = list(statuses)
        self.calls = 0

    async def fetch(self, url: str) -> Response:
        status = self.statuses[min(self.calls, len(self.statuses) - 1)]
        self.calls += 1
        if status is None:
            return Response(url=url, error="ReadTimeout: timed out")
        return Response(url=url, final_url=url, http_status=status, body=f"page {status}".encode())


def _run(
    engine: Engine, store: BlobStore, statuses: Sequence[int | None], attempts: int = 4
) -> tuple[FetchOutcome, ScriptedFetcher, list[float]]:
    with engine.begin() as conn:
        add_snapshot(conn)
    waits: list[float] = []

    async def no_wait(seconds: float) -> None:
        waits.append(seconds)

    ctx = AttemptContext(
        engine=engine,
        store=store,
        snapshot_id="dev-01",
        retry=RetrySettings(max_attempts=attempts, backoff_initial_s=2.0, backoff_max_s=60.0),
        sleep=no_wait,
    )
    fetcher = ScriptedFetcher(statuses)
    outcome = asyncio.run(
        fetch_logged(fetcher, URL, ctx=ctx, kind=FetchKind.METADATA, app_id="com.example.app")
    )
    return outcome, fetcher, waits


def _log(engine: Engine) -> list[tuple[int, str, int | None]]:
    with engine.connect() as conn:
        rows = conn.execute(
            select(fetch_log.c.attempt, fetch_log.c.status, fetch_log.c.http_status).order_by(
                fetch_log.c.id
            )
        )
        return [(r.attempt, str(r.status), r.http_status) for r in rows]


def test_success_first_time_is_one_logged_attempt(engine: Engine, store: BlobStore) -> None:
    outcome, fetcher, waits = _run(engine, store, [200])
    assert (outcome.status, outcome.attempts, fetcher.calls, waits) == (Status.OK, 1, 1, [])
    assert outcome.raw_blob is not None
    assert store.get(outcome.raw_blob) == b"page 200"
    assert _log(engine) == [(1, "ok", 200)]


def test_transient_failure_is_retried_with_backoff(engine: Engine, store: BlobStore) -> None:
    outcome, fetcher, waits = _run(engine, store, [503, None, 200])
    assert (outcome.status, outcome.attempts, fetcher.calls) == (Status.OK, 3, 3)
    assert len(waits) == 2
    assert waits[1] > waits[0] >= 2.0  # exponential backoff
    assert _log(engine) == [(1, "failed", 503), (2, "failed", None), (3, "ok", 200)]


def test_blocked_backs_off_then_records_blocked_without_bypassing(
    engine: Engine, store: BlobStore
) -> None:
    outcome, fetcher, waits = _run(engine, store, [429], attempts=4)
    assert (outcome.status, outcome.attempts, fetcher.calls, len(waits)) == (
        Status.BLOCKED,
        4,
        4,
        3,
    )
    assert _log(engine) == [(n, "blocked", 429) for n in (1, 2, 3, 4)]


@pytest.mark.parametrize(("http_status", "status"), [(404, Status.NOT_FOUND), (400, Status.FAILED)])
def test_final_answers_are_not_retried(
    engine: Engine, store: BlobStore, http_status: int, status: Status
) -> None:
    outcome, fetcher, _ = _run(engine, store, [http_status])
    assert (outcome.status, fetcher.calls) == (status, 1)
    assert outcome.raw_blob is not None  # the 404 page itself is kept as evidence
