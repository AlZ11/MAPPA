"""The Playwright fetcher against a local web server (127.0.0.1): no internet involved.

Needs Chromium: ``uv run playwright install chromium`` once. Where Playwright's own build
can't be installed, point MAPPA_CHROMIUM_EXECUTABLE at a Chromium binary instead.
"""

import asyncio
import os
import threading
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from mappa.collect.browser_fetcher import BrowserFetcher
from mappa.collect.fetching import Response
from mappa.collect.polite import DomainRateLimiter
from mappa.collect.synthetic import _minimal_pdf

RESEARCH_UA = "MAPPA-2.0-research-crawler/0.1 (test) (+mailto:mappa-tests@example.org)"
PDF = _minimal_pdf("Privacy policy text inside a PDF document.")


class Site(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        if self.path == "/js.html":
            self._send(
                200,
                b"<html><body><p>loading</p><script>document.body.innerHTML ="
                b" '<p>rendered by javascript</p>';</script></body></html>",
                "text/html",
            )
        elif self.path == "/ua":
            self._send(
                200, f"<html><body>{self.headers['User-Agent']}</body></html>".encode(), "text/html"
            )
        elif self.path == "/doc.pdf":
            self._send(200, PDF, "application/pdf")
        else:
            self._send(404, b"<html><body>gone</body></html>", "text/html")

    def _send(self, status: int, body: bytes, content_type: str) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format: str, *args: object) -> None:
        return  # keep test output quiet


@pytest.fixture(scope="module")
def site() -> Iterator[str]:
    server = ThreadingHTTPServer(("127.0.0.1", 0), Site)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_port}"
    server.shutdown()
    server.server_close()


def _fetch_all(urls: list[str]) -> list[Response]:
    executable = os.environ.get("MAPPA_CHROMIUM_EXECUTABLE")

    async def run() -> list[Response]:
        async with BrowserFetcher(
            research_user_agent=RESEARCH_UA,
            limiter=DomainRateLimiter(0.0),
            timeout_s=15,
            locale="en-AU",
            executable=Path(executable) if executable else None,
        ) as fetcher:
            return [await fetcher.fetch(url) for url in urls]

    return asyncio.run(run())


def test_renders_javascript_identifies_itself_and_reads_pdfs_and_404s(site: str) -> None:
    rendered, ua_page, pdf, missing = _fetch_all(
        [f"{site}/js.html", f"{site}/ua", f"{site}/doc.pdf", f"{site}/missing"]
    )

    assert rendered.http_status == 200
    assert rendered.body is not None
    assert b"rendered by javascript" in rendered.body  # the rendered DOM, not the source

    assert ua_page.body is not None
    assert RESEARCH_UA.encode() in ua_page.body
    assert b"HeadlessChrome" in ua_page.body  # automation is not disguised

    assert pdf.http_status == 200
    assert pdf.body == PDF
    assert pdf.content_type == "application/pdf"

    assert missing.http_status == 404
