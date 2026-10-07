"""The plain HTTP fetcher, against an in-process fake server (httpx.MockTransport)."""

import asyncio

import httpx

from mappa.collect.fetching import Response
from mappa.collect.http_fetcher import HttpFetcher
from mappa.collect.polite import DomainRateLimiter

UA = (
    "MAPPA-2.0-research-crawler/0.1 (academic mHealth privacy study) "
    "(+mailto:mappa-tests@example.org)"
)


def _fetch(handler: httpx.MockTransport, url: str) -> tuple[Response, list[httpx.Request]]:
    seen: list[httpx.Request] = []

    def record(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return handler.handle_request(request)

    async def run() -> Response:
        fetcher = HttpFetcher(
            user_agent=UA,
            limiter=DomainRateLimiter(0.0),
            timeout_s=5,
            accept_language="en-AU,en;q=0.9",
            transport=httpx.MockTransport(record),
        )
        try:
            return await fetcher.fetch(url)
        finally:
            await fetcher.aclose()

    return asyncio.run(run()), seen


def test_sends_the_research_user_agent_and_returns_raw_bytes() -> None:
    body = b"<html>\xe2\x9c\x93 raw bytes kept as sent</html>"
    response, seen = _fetch(
        httpx.MockTransport(
            lambda r: httpx.Response(200, content=body, headers={"content-type": "text/html"})
        ),
        "https://play.google.com/store/apps/details?id=a.b&hl=en&gl=AU",
    )
    assert seen[0].headers["user-agent"] == UA
    assert seen[0].headers["accept-language"] == "en-AU,en;q=0.9"
    assert response.http_status == 200
    assert response.body == body
    assert response.content_type == "text/html"


def test_follows_redirects_and_records_the_final_url() -> None:
    def server(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/old":
            return httpx.Response(301, headers={"location": "https://site.example/new"})
        return httpx.Response(200, content=b"moved here")

    response, _ = _fetch(httpx.MockTransport(server), "https://site.example/old")
    assert response.final_url == "https://site.example/new"
    assert response.body == b"moved here"


def test_a_transport_error_is_reported_not_raised() -> None:
    def broken(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused", request=request)

    response, _ = _fetch(httpx.MockTransport(broken), "https://down.example/")
    assert response.http_status is None
    assert response.error == "ConnectError: connection refused"
