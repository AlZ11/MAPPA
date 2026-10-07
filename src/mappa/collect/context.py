"""What every pipeline step needs, and the one place that picks real or synthetic fetchers.

Steps never construct fetchers themselves. ``open_fetchers`` decides, from the store's
purpose, whether requests go to the real web (through the live guards: real contact
address, TLS checks on, rate limiter) or to the synthetic world (no network, no waiting).
A step therefore cannot accidentally fetch real pages into a synthetic store, or the
reverse.
"""

import asyncio
from collections.abc import AsyncIterator
from contextlib import AsyncExitStack, asynccontextmanager
from dataclasses import dataclass

from sqlalchemy import Engine

from mappa.collect.attempts import AttemptContext, Sleep
from mappa.collect.browser_fetcher import BrowserFetcher
from mappa.collect.fetching import Fetcher
from mappa.collect.http_fetcher import HttpFetcher
from mappa.collect.live import require_real_contact, require_tls_verification
from mappa.collect.polite import DomainRateLimiter
from mappa.collect.synthetic import SyntheticFetcher
from mappa.config import Settings
from mappa.models.enums import StorePurpose
from mappa.storage.blobs import BlobStore
from mappa.storage.layout import StoreLayout


class StepCrashed(RuntimeError):
    """Some items hit a bug (not a fetch failure); they were left unrecorded, so a re-run
    retries them. The command exits non-zero so the bug can't go unnoticed."""


@dataclass(frozen=True)
class StepContext:
    settings: Settings
    purpose: StorePurpose
    layout: StoreLayout
    engine: Engine
    store: BlobStore
    snapshot_id: str

    @property
    def synthetic(self) -> bool:
        return self.purpose is StorePurpose.SYNTHETIC


@dataclass(frozen=True)
class Fetchers:
    pages: Fetcher  # server-rendered pages (Google Play)
    browser: Fetcher | None  # pages needing JavaScript (policies; Data Safety by default)
    sleep: Sleep  # how retries wait: real time for live runs, no time for synthetic ones

    def attempts(self, ctx: StepContext) -> AttemptContext:
        return AttemptContext(
            engine=ctx.engine,
            store=ctx.store,
            snapshot_id=ctx.snapshot_id,
            retry=ctx.settings.retry,
            sleep=self.sleep,
        )

    def require_browser(self) -> Fetcher:
        if self.browser is None:
            raise RuntimeError("this step needs the browser fetcher; open_fetchers(browser=True)")
        return self.browser


async def _no_wait(_seconds: float) -> None:
    return None


@asynccontextmanager
async def open_fetchers(ctx: StepContext, *, browser: bool) -> AsyncIterator[Fetchers]:
    if ctx.synthetic:
        synthetic = SyntheticFetcher()
        yield Fetchers(pages=synthetic, browser=synthetic, sleep=_no_wait)
        return

    require_real_contact(ctx.settings)
    require_tls_verification()
    settings = ctx.settings
    limiter = DomainRateLimiter(1.0 / settings.rate_limit_per_domain_rps)
    accept_language = f"{settings.lang}-{settings.country.upper()},{settings.lang};q=0.9"
    async with AsyncExitStack() as stack:
        pages = HttpFetcher(
            user_agent=settings.http_user_agent,
            limiter=limiter,
            timeout_s=settings.page_timeout_s,
            accept_language=accept_language,
        )
        stack.push_async_callback(pages.aclose)
        rendered: Fetcher | None = None
        if browser:
            rendered = await stack.enter_async_context(
                BrowserFetcher(
                    research_user_agent=settings.http_user_agent,
                    limiter=limiter,
                    timeout_s=settings.page_timeout_s,
                    locale=f"{settings.lang}-{settings.country.upper()}",
                    executable=settings.chromium_executable,
                )
            )
        yield Fetchers(pages=pages, browser=rendered, sleep=asyncio.sleep)
