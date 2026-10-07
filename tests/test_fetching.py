"""How a response becomes a status: one table, used by every collector."""

import pytest

from mappa.collect.fetching import Response, classify, host_of
from mappa.models.enums import Status

URL = "https://play.google.com/store/apps/details?id=com.example.app"


@pytest.mark.parametrize(
    ("http_status", "status", "retryable"),
    [
        (200, Status.OK, False),
        (204, Status.OK, False),
        (404, Status.NOT_FOUND, False),
        (410, Status.NOT_FOUND, False),
        (401, Status.BLOCKED, True),
        (403, Status.BLOCKED, True),
        (429, Status.BLOCKED, True),
        (500, Status.FAILED, True),
        (503, Status.FAILED, True),
        (400, Status.FAILED, False),
    ],
)
def test_http_status_mapping(http_status: int, status: Status, retryable: bool) -> None:
    verdict = classify(Response(url=URL, final_url=URL, http_status=http_status, body=b""))
    assert (verdict.status, verdict.retryable) == (status, retryable)
    assert (verdict.reason is None) == (status is Status.OK)


def test_no_response_at_all_is_a_retryable_failure_never_an_empty_success() -> None:
    verdict = classify(Response(url=URL, error="ConnectTimeout: timed out"))
    assert (verdict.status, verdict.retryable, verdict.reason) == (
        Status.FAILED,
        True,
        "ConnectTimeout: timed out",
    )


@pytest.mark.parametrize(
    "response",
    [
        Response(
            url=URL,
            final_url="https://www.google.com/sorry/index?continue=x",
            http_status=200,
            body=b"",
        ),
        Response(
            url=URL,
            final_url=URL,
            http_status=200,
            body=b"<p>Our systems have detected unusual traffic from your computer network.</p>",
        ),
    ],
    ids=["sorry-url", "sorry-text"],
)
def test_google_unusual_traffic_page_is_blocked_even_with_http_200(response: Response) -> None:
    verdict = classify(response)
    assert (verdict.status, verdict.retryable) == (Status.BLOCKED, True)


def test_host_is_lowercased_hostname() -> None:
    assert host_of("https://Policy.Example.ORG:8443/a?b=c") == "policy.example.org"
    assert host_of("not a url") == ""
