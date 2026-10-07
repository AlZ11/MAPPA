"""Guards that run before any live (real network) request.

The config only checks that ``contact_email`` looks like an address, so tests and
synthetic runs can use ``@example.org``. A live crawl must never go out with a fake
contact, so live fetchers refuse reserved domains here: no live crawl until the real
project address is in the config.
"""

import ssl

from mappa.config import Settings
from mappa.log import get_logger

log = get_logger(__name__)

_PLACEHOLDER_DOMAINS = ("example.com", "example.net", "example.org", "localhost")
_PLACEHOLDER_SUFFIXES = (".example", ".invalid", ".test", ".localhost", ".local")


class LiveFetchRefused(RuntimeError):
    """A live request would break the ethics rules. The message says which."""


def require_real_contact(settings: Settings) -> None:
    domain = settings.contact_email.rsplit("@", 1)[-1].lower()
    is_placeholder = (
        domain in _PLACEHOLDER_DOMAINS
        or any(domain.endswith(f".{d}") for d in _PLACEHOLDER_DOMAINS)
        or domain.endswith(_PLACEHOLDER_SUFFIXES)
    )
    if is_placeholder:
        raise LiveFetchRefused(
            f"contact_email {settings.contact_email!r} is a placeholder address. Live "
            "requests must name the real project/CSIRO contact, so no live crawl runs "
            "until it is set (synthetic runs don't need it: use --synthetic)."
        )


def require_tls_verification() -> None:
    """Python's default HTTPS context must verify certificates.

    Importing google-play-scraper switches it off for the whole process; mappa restores
    it right after that import (``parse/play_data.py``). If something has switched it
    off again, restore it and say so loudly rather than run with checks disabled.
    """
    if ssl._create_default_https_context is not ssl.create_default_context:
        ssl._create_default_https_context = ssl.create_default_context
        log.warning("tls.verification_restored")
