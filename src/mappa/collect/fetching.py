"""What a fetch returns, and how a response becomes one of the six statuses.

Fetchers (plain HTTP, headless browser, synthetic) only report what happened. Deciding
what it *means* happens here, in one place, so "blocked" means the same thing for a
Google Play page and a privacy policy, and a failure can never turn into an empty
success (principle 4).
"""

from dataclasses import dataclass
from typing import Protocol
from urllib.parse import urlsplit

from mappa.models.enums import Status

# Google's "unusual traffic" interstitial. We record it as blocked and back off; we
# never try to get past it (CLAUDE.md ethics rules).
_GOOGLE_SORRY_PATH = "/sorry/"
_GOOGLE_SORRY_TEXT = b"unusual traffic from your computer network"


@dataclass(frozen=True)
class Response:
    url: str
    final_url: str | None = None
    http_status: int | None = None
    body: bytes | None = None
    content_type: str | None = None
    error: str | None = None  # set when no HTTP response was received at all


class Fetcher(Protocol):
    async def fetch(self, url: str) -> Response: ...


@dataclass(frozen=True)
class Verdict:
    status: Status
    retryable: bool
    reason: str | None  # why the status is not ok; stored in fetch_log.error


def classify(response: Response) -> Verdict:
    """Map a response to a status. Blocked and transient failures are retryable."""
    if response.error is not None:
        return Verdict(Status.FAILED, retryable=True, reason=response.error)
    code = response.http_status
    if code is None:
        return Verdict(Status.FAILED, retryable=True, reason="no HTTP status received")
    if _is_google_sorry_page(response):
        return Verdict(Status.BLOCKED, retryable=True, reason="Google unusual-traffic page")
    if 200 <= code < 300:
        return Verdict(Status.OK, retryable=False, reason=None)
    if code in (404, 410):
        return Verdict(Status.NOT_FOUND, retryable=False, reason=f"HTTP {code}")
    if code in (401, 403, 429):
        return Verdict(Status.BLOCKED, retryable=True, reason=f"HTTP {code}")
    if code >= 500:
        return Verdict(Status.FAILED, retryable=True, reason=f"HTTP {code}")
    return Verdict(Status.FAILED, retryable=False, reason=f"HTTP {code}")


def host_of(url: str) -> str:
    """The rate-limiting unit. Hostname rather than registrable domain: simpler, and it
    errs towards treating subdomains as separate sites only where they usually are."""
    return (urlsplit(url).hostname or "").lower()


def _is_google_sorry_page(response: Response) -> bool:
    final = urlsplit(response.final_url or response.url)
    if (final.hostname or "").endswith("google.com") and final.path.startswith(_GOOGLE_SORRY_PATH):
        return True
    return response.body is not None and _GOOGLE_SORRY_TEXT in response.body
