"""Synthetic Google Play and policy websites, for plumbing runs with no network at all.

Marked so it can never pass for evidence:

- app IDs start with ``invalid.mappa.synthetic.`` (the reserved ``.invalid`` domain,
  reversed), and the database refuses them in a real store;
- every page carries ``SYNTHETIC_MARKER``;
- policies live under ``https://policy.synthetic.mappa.invalid/``;
- the CLI uses it only with a store marked synthetic and ``synthetic-`` snapshot IDs.

The pages copy the *structure* our parsers read (the embedded ``ds:N`` data blocks, at
the same positions), with every case the pipeline must handle: a removed app, paid apps,
off-topic genres, small apps, a server error that clears on retry, a site that keeps
blocking us, missing and odd policies, a PDF, and each kind of Data Safety label.

A synthetic run proves the plumbing works end to end. It proves nothing about the
parsers: they are validated only on real pages saved into tests/fixtures/.
"""

import hashlib
import json
from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any
from urllib.parse import parse_qs, urlsplit

from mappa.collect.fetching import Response
from mappa.collect.play_store import details_url
from mappa.models.ids import SYNTHETIC_APP_PREFIX

SYNTHETIC_MARKER = "<!-- MAPPA SYNTHETIC DATA: not from Google Play or any real website -->"
POLICY_HOST = "policy.synthetic.mappa.invalid"
APP_COUNT = 40
DEV_SAMPLE_SIZE = 20
SEED_APPS = (1, 2)

_INSTALL_BUCKETS = (500, 1_000, 5_000, 10_000, 50_000, 100_000, 500_000, 1_000_000, 5_000_000)
_POLICY_CASES = (
    "html",
    "shared",
    "none",
    "short_with_links",
    "non_english",
    "pdf",
    "store_page",
    "gone",
)
_LABEL_CASES = (
    "full",
    "optional",
    "no_data_collected",
    "no_data_shared",
    "not_provided",
    "all_practices",
    "gone",
)
_HTML = "text/html; charset=utf-8"


@dataclass(frozen=True)
class SyntheticApp:
    n: int
    app_id: str
    title: str
    genre_id: str
    genre: str
    min_installs: int
    real_installs: int
    price_micros: int
    policy_case: str
    label_case: str
    listed: bool  # False: the store answers 404, as for a removed app
    flaky: bool  # the listing fails once with HTTP 503, then works
    blocks: bool  # the listing always answers HTTP 429

    @property
    def policy_url(self) -> str | None:
        case = self.policy_case
        if case == "none":
            return None
        if case == "shared":
            return f"https://{POLICY_HOST}/shared/developer-policy.html"
        if case == "store_page":
            return details_url(self.app_id, lang="en", country="au")
        if case == "pdf":
            return f"https://{POLICY_HOST}/apps/{self.n:02d}/privacy.pdf"
        if case == "gone":
            return f"https://{POLICY_HOST}/gone/{self.n:02d}.html"
        return f"https://{POLICY_HOST}/apps/{self.n:02d}/{case}.html"


def synthetic_app_id(n: int) -> str:
    return f"{SYNTHETIC_APP_PREFIX}app{n:02d}"


def world() -> dict[str, SyntheticApp]:
    apps: dict[str, SyntheticApp] = {}
    for n in range(1, APP_COUNT + 1):
        bucket = _INSTALL_BUCKETS[n % len(_INSTALL_BUCKETS)]
        genre_id = (
            "LIFESTYLE" if n % 10 == 9 else "MEDICAL" if n % 10 == 8 else "HEALTH_AND_FITNESS"
        )
        app = SyntheticApp(
            n=n,
            app_id=synthetic_app_id(n),
            title=f"SYNTHETIC Health App {n:02d}",
            genre_id=genre_id,
            genre={"LIFESTYLE": "Lifestyle", "MEDICAL": "Medical"}.get(
                genre_id, "Health & Fitness"
            ),
            min_installs=bucket,
            real_installs=bucket + 37 * n,
            price_micros=2_990_000 if n in (5, 18) else 0,
            policy_case=_POLICY_CASES[n % len(_POLICY_CASES)],
            label_case=_LABEL_CASES[n % len(_LABEL_CASES)],
            listed=n != 7,
            flaky=n == 11,
            blocks=n == 13,
        )
        apps[app.app_id] = app
    return apps


def dev_sample_csv() -> bytes:
    """The synthetic stand-in for config/dev_apps.csv (which holds real package names)."""
    rows = ["app_id,name,reason,verified_on_au_store"]
    for n in range(1, DEV_SAMPLE_SIZE + 1):
        rows.append(f"{synthetic_app_id(n)},SYNTHETIC app {n:02d},synthetic plumbing test,")
    return ("\n".join(rows) + "\n").encode()


def seed_csv() -> bytes:
    """The synthetic stand-in for config/seed_apps.csv."""
    rows = ["app_id,name,reason,verified_on_au_store"]
    rows += [
        f"{synthetic_app_id(n)},SYNTHETIC seed {n:02d},synthetic plumbing test," for n in SEED_APPS
    ]
    return ("\n".join(rows) + "\n").encode()


class SyntheticFetcher:
    """Answers requests from the synthetic world. Never opens a network connection."""

    def __init__(self) -> None:
        self._apps = world()
        self._calls: Counter[str] = Counter()

    async def fetch(self, url: str) -> Response:
        self._calls[url] += 1
        parts = urlsplit(url)
        params = {k: v[0] for k, v in parse_qs(parts.query).items()}
        if parts.hostname == "play.google.com":
            if parts.path == "/store/search":
                return _ok(url, _search_page(self._apps, params.get("q", "")))
            app = self._apps.get(params.get("id", ""))
            if parts.path == "/store/apps/details":
                return self._listing(url, app)
            if parts.path == "/store/apps/datasafety":
                if app is None or not app.listed or app.label_case == "gone":
                    return _not_found(url)
                return _ok(url, _datasafety_page(app))
        if parts.hostname == POLICY_HOST:
            return _policy(url, parts.path, self._apps)
        return _not_found(url)

    def _listing(self, url: str, app: SyntheticApp | None) -> Response:
        if app is None or not app.listed:
            return _not_found(url)
        if app.blocks:
            return Response(url=url, final_url=url, http_status=429, body=b"", content_type=_HTML)
        if app.flaky and self._calls[url] == 1:
            return Response(url=url, final_url=url, http_status=503, body=b"", content_type=_HTML)
        return _ok(url, _listing_page(app))


# --- pages ---------------------------------------------------------------------------


def _ok(url: str, html: str, content_type: str = _HTML) -> Response:
    return Response(
        url=url, final_url=url, http_status=200, body=html.encode(), content_type=content_type
    )


def _not_found(url: str) -> Response:
    page = f"<html><head>{SYNTHETIC_MARKER}</head><body>Not found</body></html>"
    return Response(url=url, final_url=url, http_status=404, body=page.encode(), content_type=_HTML)


def _page(body: str, *datasets: tuple[str, Any]) -> str:
    scripts = "".join(
        f"<script>AF_initDataCallback({{key: '{key}', hash: '1', data:{json.dumps(data)}, "
        "sideChannel: {}});</script>"
        for key, data in datasets
    )
    return f"<html><head>{SYNTHETIC_MARKER}</head><body>{body}{scripts}</body></html>"


def _place(root: list[Any], path: Sequence[int], value: Any) -> None:
    """Set ``root[a][b]...[z] = value``, padding lists with None as needed."""
    node = root
    for depth, index in enumerate(path):
        while len(node) <= index:
            node.append(None)
        if depth == len(path) - 1:
            node[index] = value
        else:
            if not isinstance(node[index], list):
                node[index] = []
            node = node[index]


def _rank_key(query: str, app_id: str) -> int:
    return int(hashlib.sha256(f"{query}|{app_id}".encode()).hexdigest()[:12], 16)


def _search_page(apps: dict[str, SyntheticApp], query: str) -> str:
    hits = sorted(
        (a for a in apps.values() if _rank_key(query, a.app_id) % 3 == 0),
        key=lambda a: _rank_key(query, a.app_id),
    )[:20]
    items = []
    for app in hits:
        item: list[Any] = []
        _place(item, [0, 0, 0], app.app_id)
        _place(item, [0, 3], app.title)
        _place(item, [0, 5], app.genre)
        _place(item, [0, 14], "Synthetic Developer")
        _place(item, [0, 15], f"{app.min_installs:,}+")
        items.append(item)
    section: list[Any] = []
    _place(section, [22, 0], items)
    if hits and _rank_key(query, "top-card") % 4 == 0:  # a "top result" card repeating hit 1
        top: list[Any] = []
        _place(top, [11, 0, 0], hits[0].app_id)
        _place(top, [2, 0, 0], hits[0].title)
        _place(section, [23, 16], top)
    ds4: list[Any] = []
    _place(ds4, [0, 1], [section])
    return _page(f"<h1>Results for {query}</h1>", ("ds:4", ds4))


def _listing_page(app: SyntheticApp) -> str:
    ds5: list[Any] = []
    fields: dict[tuple[int, ...], Any] = {
        (1, 2, 0, 0): app.title,
        (1, 2, 13, 0): f"{app.min_installs:,}+",
        (1, 2, 13, 1): app.min_installs,
        (1, 2, 13, 2): app.real_installs,
        (1, 2, 51, 0, 1): 4.1,
        (1, 2, 51, 2, 1): 1000 + app.n,
        (1, 2, 57, 0, 0, 0, 0, 1, 0, 0): app.price_micros,
        (1, 2, 57, 0, 0, 0, 0, 1, 0, 1): "AUD",
        (1, 2, 68, 0): "Synthetic Developer",
        (1, 2, 68, 1, 4, 2): "/store/apps/dev?id=5550000000000000001",
        (1, 2, 69, 1, 0): "developer@synthetic.mappa.invalid",
        (1, 2, 69, 0, 5, 2): f"https://{POLICY_HOST}/",
        (1, 2, 79, 0, 0, 0): app.genre,
        (1, 2, 79, 0, 0, 2): app.genre_id,
        (1, 2, 9, 0): "Everyone",
        (1, 2, 48): app.n % 2 == 0 or None,
        (1, 2, 10, 0): "Mar 3, 2021",
        (1, 2, 145, 0, 1, 0): 1_790_000_000 + app.n,
        (1, 2, 140, 0, 0, 0): f"1.{app.n}.0",
    }
    if app.policy_url is not None:
        fields[(1, 2, 99, 0, 5, 2)] = app.policy_url
    for path, value in fields.items():
        _place(ds5, path, value)
    return _page(f"<h1>{app.title}</h1>", ("ds:5", ds5))


_FULL_LABEL = {
    "shared": [
        ("Personal info", [("Email address", None, "App functionality, Developer communications")]),
        ("App activity", [("App interactions", None, "Analytics")]),
    ],
    "collected": [
        (
            "Personal info",
            [
                ("Name", None, "Account management"),
                ("Email address", None, "App functionality, Account management"),
            ],
        ),
        ("Health and fitness", [("Fitness info", None, "App functionality, Personalization")]),
        (
            "Device or other IDs",
            [
                (
                    "Device or other IDs",
                    None,
                    "Analytics, Fraud prevention, security, and compliance",
                )
            ],
        ),
        ("Synthetic category", [("Synthetic unmapped type", None, "Analytics")]),
    ],
}
_PRACTICES = {
    "encrypted": (
        "Data is encrypted in transit",
        "Your data is transferred over a secure connection",
    ),
    "deletion": (
        "You can request that data be deleted",
        "The developer provides a way to request deletion",
    ),
    "families": ("Committed to follow the Play Families Policy", "Synthetic description"),
    "review": ("Independent security review", "Synthetic description"),
}


def _datasafety_page(app: SyntheticApp) -> str:
    case = app.label_case
    if case == "not_provided":
        text = (
            "<h2>No information available</h2><p>The developer has not provided "
            "information about its data collection and sharing practices.</p>"
        )
        return _page(text)
    shared: list[Any] = list(_FULL_LABEL["shared"])
    collected: list[Any] = list(_FULL_LABEL["collected"])
    practices = ["encrypted", "deletion"]
    headlines: list[str] = []
    if case == "optional":
        shared = []
        collected = [*collected, ("Location", [("Approximate location", 1, "App functionality")])]
    if case == "no_data_collected":
        shared, collected, practices = [], [], ["encrypted"]
    if case == "no_data_shared":
        shared = []
    if case == "all_practices":
        practices = list(_PRACTICES)
    if not shared:
        headlines.append("No data shared with third parties")
    if not collected:
        headlines.append("No data collected")

    def entries(groups: list[Any]) -> list[Any]:
        out = []
        for category, details in groups:
            entry: list[Any] = []
            _place(entry, [0, 1], category)
            _place(entry, [4], [list(detail) for detail in details])
            out.append(entry)
        return out

    label: list[Any] = []
    if shared:
        _place(label, [4, 0, 0], entries(shared))
    if collected:
        _place(label, [4, 1, 0], entries(collected))
    _place(label, [9, 2], [[None, _PRACTICES[p][0], [None, _PRACTICES[p][1]]] for p in practices])
    ds3: list[Any] = []
    _place(ds3, [1, 2, 1, 138], label)
    visible = "".join(f"<p>{h}</p>" for h in headlines)
    return _page(f"<h1>Data safety</h1>{visible}", ("ds:3", ds3))


_EN_PARAGRAPH = (
    "This privacy policy explains how {name} collects, uses and shares personal "
    "information when you use the app. We collect the information you give us, such as "
    "your name and email address, and information about how you use the app. We use it "
    "to provide and improve the service, and we share it only with service providers "
    "that process it on our behalf under contract."
)
_DE_PARAGRAPH = (
    "Diese Datenschutzerklärung beschreibt, wie {name} personenbezogene Daten erhebt, "
    "verwendet und weitergibt, wenn Sie die App nutzen. Wir erheben die Daten, die Sie "
    "uns mitteilen, etwa Ihren Namen und Ihre E-Mail-Adresse, sowie Angaben zur Nutzung. "
    "Wir verwenden sie, um den Dienst bereitzustellen und zu verbessern."
)


def _policy(url: str, path: str, apps: dict[str, SyntheticApp]) -> Response:
    if path.startswith("/gone/"):
        return _not_found(url)
    if path == "/shared/developer-policy.html":
        return _ok(url, _policy_html("Synthetic Developer", _EN_PARAGRAPH, repeat=8))
    parts = path.strip("/").split("/")
    if len(parts) != 3 or parts[0] != "apps":
        return _not_found(url)
    app = apps.get(synthetic_app_id(int(parts[1]))) if parts[1].isdigit() else None
    if app is None:
        return _not_found(url)
    name = app.title
    if parts[2] == "privacy.pdf":
        text = " ".join(_EN_PARAGRAPH.format(name=name) for _ in range(7))
        return Response(
            url=url,
            final_url=url,
            http_status=200,
            body=_minimal_pdf(text),
            content_type="application/pdf",
        )
    if parts[2] == "short_with_links.html":
        body = (
            f"<p>{name} respects your privacy. This page is a summary only.</p>"
            '<p><a href="/apps/full-privacy-policy.html">Read our full privacy policy</a></p>'
        )
        return _ok(
            url,
            f"<html><head>{SYNTHETIC_MARKER}<title>Privacy</title></head><body>{body}</body></html>",
        )
    if parts[2] == "non_english.html":
        return _ok(url, _policy_html(name, _DE_PARAGRAPH, repeat=9, title="Datenschutzerklärung"))
    return _ok(url, _policy_html(name, _EN_PARAGRAPH, repeat=8))


def _policy_html(name: str, paragraph: str, *, repeat: int, title: str = "Privacy Policy") -> str:
    paragraphs = "".join(
        f"<p>{paragraph.format(name=name)} Section {i + 1}.</p>" for i in range(repeat)
    )
    return (
        f"<html><head>{SYNTHETIC_MARKER}<title>{title}</title></head>"
        f"<body><main><h1>{title}</h1>{paragraphs}</main></body></html>"
    )


def _minimal_pdf(text: str) -> bytes:
    """A one-page PDF whose text pypdf can extract, written by hand (no PDF library)."""
    escaped = text.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
    stream = f"BT /F1 9 Tf 36 750 Td ({escaped}) Tj ET".encode("latin-1", errors="replace")
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
        b"/Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
        b"<< /Length %d >>\nstream\n" % len(stream) + stream + b"\nendstream",
    ]
    out = bytearray(b"%PDF-1.4\n")
    offsets = []
    for number, obj in enumerate(objects, start=1):
        offsets.append(len(out))
        out += b"%d 0 obj\n" % number + obj + b"\nendobj\n"
    xref = len(out)
    out += b"xref\n0 %d\n0000000000 65535 f \n" % (len(objects) + 1)
    for offset in offsets:
        out += b"%010d 00000 n \n" % offset
    out += b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (
        len(objects) + 1,
        xref,
    )
    return bytes(out)
