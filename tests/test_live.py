"""Guards that run before any live request, and the TLS fix for google-play-scraper."""

import ssl
from collections.abc import Callable
from pathlib import Path

import pytest

from mappa.collect.live import LiveFetchRefused, require_real_contact, require_tls_verification
from mappa.config import load_config


@pytest.mark.parametrize(
    "address",
    [
        "a@example.org",
        "a@example.com",
        "a@mail.example.net",
        "a@lab.invalid",
        "a@site.test",
        "a@host.localhost",
        "a@team.example",
    ],
)
def test_live_requests_refuse_placeholder_contacts(
    write_config: Callable[[str], Path], address: str
) -> None:
    settings = load_config(
        write_config(f'data_dir = "data"\ncontact_email = "{address}"\n'), env={}
    )
    with pytest.raises(LiveFetchRefused, match="placeholder"):
        require_real_contact(settings)


def test_live_requests_accept_a_real_looking_contact(write_config: Callable[[str], Path]) -> None:
    settings = load_config(
        write_config('data_dir = "data"\ncontact_email = "mappa@csiro.au"\n'), env={}
    )
    require_real_contact(settings)


def test_importing_our_play_parser_leaves_certificate_checks_on() -> None:
    import mappa.parse.play_data  # noqa: F401  (the import itself is under test)

    assert ssl._create_default_https_context is ssl.create_default_context


def test_tls_guard_restores_checks_if_something_turned_them_off() -> None:
    original = ssl._create_default_https_context
    ssl._create_default_https_context = ssl._create_unverified_context
    try:
        require_tls_verification()
        assert ssl._create_default_https_context is ssl.create_default_context
    finally:
        ssl._create_default_https_context = original
