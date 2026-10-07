"""Google Play URLs. Every URL pins the store country (``gl``) and language (``hl``).

There is deliberately no fallback without ``gl``: an app missing from the Australian
store must come back as ``not_found``, never as another country's listing.
"""

from urllib.parse import urlencode

BASE_URL = "https://play.google.com"


def search_url(query: str, *, lang: str, country: str) -> str:
    params = {"q": query, "c": "apps", "hl": lang, "gl": country.upper()}
    return f"{BASE_URL}/store/search?{urlencode(params)}"


def details_url(app_id: str, *, lang: str, country: str) -> str:
    params = {"id": app_id, "hl": lang, "gl": country.upper()}
    return f"{BASE_URL}/store/apps/details?{urlencode(params)}"


def datasafety_url(app_id: str, *, lang: str, country: str) -> str:
    params = {"id": app_id, "hl": lang, "gl": country.upper()}
    return f"{BASE_URL}/store/apps/datasafety?{urlencode(params)}"
