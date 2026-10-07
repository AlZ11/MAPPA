"""Headless Chromium fetcher (Playwright) for pages that need JavaScript to show text.

Many privacy policies are built by JavaScript, so a plain HTTP fetch would see an empty
shell. The browser loads the page, waits for the network to go quiet, and we keep the
rendered HTML. The whole wait is capped (``page_timeout_s``): a page that never goes
quiet is saved as it stands rather than holding up the run.

User-Agent: Chromium's own string with the research User-Agent appended. That keeps
sites rendering normally while still naming the project and a contact address, and we
keep the honest "HeadlessChrome" token rather than disguising automation.

PDFs: Chromium either starts a download or shows its viewer, neither of which gives us
the document, so PDFs are re-requested as raw bytes through the browser's own request
API (same cookies and User-Agent), and the rate limiter counts that request too.
"""

import asyncio
import contextlib
from pathlib import Path
from types import TracebackType
from typing import Self

from playwright.async_api import Browser, BrowserContext, Playwright, async_playwright
from playwright.async_api import Error as PlaywrightError
from playwright.async_api import TimeoutError as PlaywrightTimeoutError

from mappa.collect.fetching import Response, host_of
from mappa.collect.polite import DomainRateLimiter

RENDERED_HTML = "text/html; charset=utf-8"


class BrowserFetcher:
    def __init__(
        self,
        *,
        research_user_agent: str,
        limiter: DomainRateLimiter,
        timeout_s: float,
        locale: str,
        executable: Path | None = None,
    ) -> None:
        self._research_ua = research_user_agent
        self._limiter = limiter
        self._timeout_ms = timeout_s * 1000
        self._locale = locale
        self._executable = executable
        self._playwright: Playwright | None = None
        self._browser: Browser | None = None
        self._context: BrowserContext | None = None

    async def __aenter__(self) -> Self:
        self._playwright = await async_playwright().start()
        self._browser = await self._playwright.chromium.launch(
            headless=True, executable_path=self._executable
        )
        probe = await self._browser.new_page()
        chromium_ua = await probe.evaluate("navigator.userAgent")
        await probe.close()
        self._context = await self._browser.new_context(
            user_agent=f"{chromium_ua} {self._research_ua}",
            locale=self._locale,
            accept_downloads=True,
        )
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        if self._context is not None:
            await self._context.close()
        if self._browser is not None:
            await self._browser.close()
        if self._playwright is not None:
            await self._playwright.stop()

    async def fetch(self, url: str) -> Response:
        context = self._require_context()
        await self._limiter.acquire(host_of(url))
        loop = asyncio.get_running_loop()
        deadline = loop.time() + self._timeout_ms / 1000
        page = await context.new_page()
        try:
            try:
                main = await page.goto(url, wait_until="load", timeout=self._timeout_ms)
            except PlaywrightError as exc:
                if "Download is starting" in str(exc):
                    return await self._download(url)
                return Response(url=url, error=f"navigation failed: {_first_line(exc)}")
            content_type = main.headers.get("content-type") if main is not None else None
            if content_type and "application/pdf" in content_type.lower():
                return await self._download(url)
            remaining_ms = (deadline - loop.time()) * 1000
            if remaining_ms > 0:
                # A page that never goes quiet within the cap is kept as rendered so far.
                with contextlib.suppress(PlaywrightTimeoutError):
                    await page.wait_for_load_state("networkidle", timeout=remaining_ms)
            html = await page.content()
            return Response(
                url=url,
                final_url=page.url,
                http_status=main.status if main is not None else None,
                body=html.encode("utf-8"),
                content_type=RENDERED_HTML,
            )
        finally:
            await page.close()

    async def _download(self, url: str) -> Response:
        await self._limiter.acquire(host_of(url))
        try:
            reply = await self._require_context().request.get(url, timeout=self._timeout_ms)
        except PlaywrightError as exc:
            return Response(url=url, error=f"download failed: {_first_line(exc)}")
        return Response(
            url=url,
            final_url=reply.url,
            http_status=reply.status,
            body=await reply.body(),
            content_type=reply.headers.get("content-type"),
        )

    def _require_context(self) -> BrowserContext:
        if self._context is None:
            raise RuntimeError("BrowserFetcher used outside 'async with'")
        return self._context


def _first_line(exc: BaseException) -> str:
    return str(exc).strip().splitlines()[0][:300] if str(exc).strip() else type(exc).__name__
