"""Turn a stored privacy-policy page (HTML or PDF) into text plus review signals.

A pure function of the stored bytes, so it can be re-run on every stored policy whenever
the extraction improves, with no network (``mappa fetch-policy --reextract``).

Choices, all from the task file (M3):

- trafilatura pulls out the main text (tables included, recall over precision). If that
  yields fewer than 200 words it probably missed the content, so we fall back to all the
  page's text and record ``extraction_method = full_text``.
- ``looks_like_policy``: mentions "privacy" and has more than 300 words. Crude on
  purpose, it only decides what a person looks at, never what counts as a policy.
- Language is detected (py3langid, the detector trafilatura itself uses), and anything
  not English is flagged rather than dropped.
- The normalised hash (lowercase, whitespace collapsed) ignores formatting-only changes,
  so a policy only counts as changed between snapshots when its words change.
"""

import hashlib
import io
from dataclasses import dataclass
from urllib.parse import urljoin, urlsplit

import lxml.html
import py3langid
import pypdf
import trafilatura

from mappa.models.enums import PolicyFormat

EXTRACTOR_VERSION = (
    f"policy-text-1+trafilatura=={trafilatura.__version__}+pypdf=={pypdf.__version__}"
)
MIN_MAIN_TEXT_WORDS = 200
MIN_POLICY_WORDS = 300
MIN_WORDS_FOR_LANGUAGE = 20
_STORE_HOSTS = ("play.google.com", "apps.apple.com", "itunes.apple.com")
_GOOGLE_DOC_HOSTS = ("docs.google.com", "drive.google.com", "sites.google.com")


class PolicyTextError(ValueError):
    """The stored bytes couldn't be turned into text at all."""


@dataclass(frozen=True)
class PolicyText:
    text: str
    method: str  # trafilatura | full_text | pypdf | plain_text
    word_count: int
    language: str | None
    looks_like_policy: bool
    review_flags: tuple[str, ...]
    normalized_sha256: str

    @property
    def review_flag(self) -> str | None:
        return "; ".join(self.review_flags) or None


def detect_format(url: str, content_type: str | None, body: bytes) -> PolicyFormat:
    if body.startswith(b"%PDF-") or "pdf" in (content_type or "").lower():
        return PolicyFormat.PDF
    if (urlsplit(url).hostname or "").lower() in _GOOGLE_DOC_HOSTS:
        return PolicyFormat.GDOC
    lowered = (content_type or "").lower()
    if "html" in lowered or body.lstrip()[:15].lower().startswith((b"<!doctype", b"<html")):
        return PolicyFormat.HTML
    return PolicyFormat.OTHER


def extract(body: bytes, fmt: PolicyFormat, *, final_url: str | None) -> PolicyText:
    if fmt is PolicyFormat.PDF:
        return _finish(_pdf_text(body), "pypdf", final_url, links=())
    html = body.decode("utf-8", errors="replace")
    if fmt is PolicyFormat.OTHER and "<" not in html[:200]:
        return _finish(html, "plain_text", final_url, links=())
    main = trafilatura.extract(
        html, url=final_url, include_tables=True, favor_recall=True, output_format="txt"
    )
    if isinstance(main, str) and word_count(main) >= MIN_MAIN_TEXT_WORDS:
        text, method = main, "trafilatura"
    else:
        full = trafilatura.html2txt(html)
        text, method = (full if isinstance(full, str) else ""), "full_text"
    return _finish(text, method, final_url, links=_privacy_links(html, final_url))


def word_count(text: str) -> int:
    return len(text.split())


def normalize_for_hash(text: str) -> str:
    return " ".join(text.lower().split())


def normalized_sha256(text: str) -> str:
    return hashlib.sha256(normalize_for_hash(text).encode("utf-8")).hexdigest()


def _finish(text: str, method: str, final_url: str | None, *, links: tuple[str, ...]) -> PolicyText:
    words = word_count(text)
    language = _language(text) if words >= MIN_WORDS_FOR_LANGUAGE else None
    looks_like_policy = "privacy" in text.lower() and words > MIN_POLICY_WORDS
    flags: list[str] = []
    host = (urlsplit(final_url or "").hostname or "").lower()
    if host in _STORE_HOSTS:
        flags.append("points to an app store page")
    elif final_url and urlsplit(final_url).path in ("", "/"):
        flags.append("points to a homepage")
    if not looks_like_policy:
        flags.append(f"does not look like a policy ({words} words)")
    if language is not None and language != "en":
        flags.append(f"not English (detected: {language})")
    if words < MIN_POLICY_WORDS and links:
        flags.append("short page linking to other privacy pages: " + ", ".join(links[:3]))
    return PolicyText(
        text=text,
        method=method,
        word_count=words,
        language=language,
        looks_like_policy=looks_like_policy,
        review_flags=tuple(flags),
        normalized_sha256=normalized_sha256(text),
    )


def _language(text: str) -> str:
    language, _score = py3langid.classify(text[:5000])
    return str(language)


def _pdf_text(body: bytes) -> str:
    try:
        reader = pypdf.PdfReader(io.BytesIO(body))
        if reader.is_encrypted:
            reader.decrypt("")
        return "\n".join(page.extract_text() or "" for page in reader.pages)
    except Exception as exc:  # pypdf raises many types for damaged files
        raise PolicyTextError(f"PDF text extraction failed: {type(exc).__name__}: {exc}") from None


def _privacy_links(html: str, base_url: str | None) -> tuple[str, ...]:
    """Links whose text or address mentions privacy, other than the page itself."""
    if not html.strip():
        return ()
    try:
        document = lxml.html.fromstring(html)
    except (ValueError, lxml.etree.ParserError):
        return ()
    found: list[str] = []
    for anchor in document.xpath("//a[@href]"):
        href = str(anchor.get("href", ""))
        label = " ".join(anchor.text_content().split()).lower()
        if "privacy" not in label and "privacy" not in href.lower():
            continue
        target = urljoin(base_url or "", href)
        if target.split("#")[0] != (base_url or "").split("#")[0] and target not in found:
            found.append(target)
    return tuple(found)
