"""Plain HTTP fetcher (httpx) for pages the server renders completely, like Google Play's.

We fetch Google Play ourselves instead of through google-play-scraper's network code,
which disables TLS certificate checks process-wide, can't send our User-Agent, and
silently retries a missing Australian listing in another country's store. Here: our
User-Agent, certificate checks on (httpx builds its own TLS context), the rate limiter
on every request, and the raw bytes returned untouched for the blob store.
"""

import httpx

from mappa.collect.fetching import Response, host_of
from mappa.collect.polite import DomainRateLimiter


class HttpFetcher:
    def __init__(
        self,
        *,
        user_agent: str,
        limiter: DomainRateLimiter,
        timeout_s: float,
        accept_language: str,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._limiter = limiter
        self._client = httpx.AsyncClient(
            headers={"User-Agent": user_agent, "Accept-Language": accept_language},
            timeout=timeout_s,
            follow_redirects=True,
            transport=transport,
        )

    async def fetch(self, url: str) -> Response:
        await self._limiter.acquire(host_of(url))
        try:
            reply = await self._client.get(url)
        except httpx.HTTPError as exc:
            return Response(url=url, error=f"{type(exc).__name__}: {exc}")
        return Response(
            url=url,
            final_url=str(reply.url),
            http_status=reply.status_code,
            body=reply.content,
            content_type=reply.headers.get("content-type"),
        )

    async def aclose(self) -> None:
        await self._client.aclose()
