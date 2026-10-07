"""Read the data Google Play embeds in its pages, reusing google-play-scraper's parsers.

Google Play pages carry their content as JSON inside script blocks of the form
``AF_initDataCallback({key: 'ds:N', ..., data: [...], sideChannel: {}});``. The library
knows where each field sits inside those nested lists (its ``ElementSpecs``), and keeps
that knowledge up to date as Google changes the page. We reuse exactly that, and none of
its network code (see ``collect/http_fetcher.py`` for why).

Importing the library switches off TLS certificate checks for Python's built-in HTTPS,
for the whole process (``google_play_scraper/utils/request.py`` replaces
``ssl._create_default_https_context``). We import it here, once, and put the verifying
default straight back. A test checks this stays true.
"""

import json
import ssl
from collections.abc import Sequence
from dataclasses import dataclass
from importlib.metadata import version
from typing import Any

_VERIFYING_DEFAULT = ssl._create_default_https_context
from google_play_scraper.constants.element import ElementSpecs  # noqa: E402
from google_play_scraper.constants.regex import Regex  # noqa: E402
from google_play_scraper.features.app import parse_dom  # noqa: E402

ssl._create_default_https_context = _VERIFYING_DEFAULT

LIBRARY = f"google-play-scraper=={version('google-play-scraper')}"
SEARCH_PARSER_VERSION = f"search-1+{LIBRARY}"
LISTING_PARSER_VERSION = f"listing-1+{LIBRARY}"


class PlayParseError(ValueError):
    """The page is not what we expected: wrong kind of page, or Google changed the layout."""


@dataclass(frozen=True)
class SearchHit:
    rank: int  # 1-based position on the results page, after removing duplicates
    app_id: str
    title: str | None
    developer: str | None
    genre: str | None
    installs: str | None


@dataclass(frozen=True)
class SearchPage:
    hits: list[SearchHit]
    note: str | None  # set when the page parsed but something looked odd


def extract_datasets(html: str) -> dict[str, Any]:
    """All ``ds:N`` data blocks in a Google Play page, keyed by ``ds:N``."""
    datasets: dict[str, Any] = {}
    for block in Regex.SCRIPT.findall(html):
        keys = Regex.KEY.findall(block)
        values = Regex.VALUE.findall(block)
        if keys and values:
            try:
                datasets[keys[0]] = json.loads(values[0])
            except json.JSONDecodeError as exc:
                raise PlayParseError(f"{keys[0]}: embedded data is not valid JSON: {exc}") from None
    return datasets


def lookup(source: Any, path: Sequence[int]) -> Any:
    """``source[a][b][c]...`` or None as soon as a step doesn't exist."""
    for index in path:
        if not isinstance(source, list) or not -len(source) <= index < len(source):
            return None
        source = source[index]
    return source


def parse_search_page(html: str) -> SearchPage:
    """The apps on a search results page, in page order.

    Differences from the library's ``search()``, on purpose: no cap on the number of
    hits, nothing dropped from the end of the list when a "top result" card is shown,
    and duplicates (the top card repeating the first list item) removed by package name.
    """
    ds4 = extract_datasets(html).get("ds:4")
    if ds4 is None:
        raise PlayParseError("no ds:4 data block: not a search results page, or the layout changed")
    sections = lookup(ds4, [0, 1])
    if not isinstance(sections, list):
        raise PlayParseError("ds:4 has no result sections at [0][1]")

    raw: list[dict[str, Any]] = []
    top = lookup(ds4, [0, 1, 0, 23, 16])
    if top:
        raw.append(
            {k: spec.extract_content(top) for k, spec in ElementSpecs.SearchResultOnTop.items()}
        )
    items = next((s[22][0] for s in sections if isinstance(lookup(s, [22, 0]), list)), None)
    note = None if items is not None else "no results list in ds:4 (no results, or layout changed)"
    for item in items or []:
        raw.append({k: spec.extract_content(item) for k, spec in ElementSpecs.SearchResult.items()})

    hits: list[SearchHit] = []
    seen: set[str] = set()
    for entry in raw:
        app_id = entry.get("appId")
        if not isinstance(app_id, str) or not app_id or app_id in seen:
            continue
        seen.add(app_id)
        hits.append(
            SearchHit(
                rank=len(hits) + 1,
                app_id=app_id,
                title=_str(entry.get("title")),
                developer=_str(entry.get("developer")),
                genre=_str(entry.get("genre")),
                installs=_str(entry.get("installs")),
            )
        )
    return SearchPage(hits=hits, note=note)


def parse_listing_page(html: str, app_id: str, url: str) -> dict[str, Any]:
    """The app's store listing as the library's field dictionary (``title``,
    ``minInstalls``, ``privacyPolicy``, ...). Raises if the page has no listing."""
    try:
        fields: dict[str, Any] = parse_dom(dom=html, app_id=app_id, url=url)
    except json.JSONDecodeError as exc:
        raise PlayParseError(f"embedded data is not valid JSON: {exc}") from None
    if not fields.get("title"):
        raise PlayParseError("no app title found: not an app listing, or the layout changed")
    return fields


def _str(value: Any) -> str | None:
    return value if isinstance(value, str) else None
