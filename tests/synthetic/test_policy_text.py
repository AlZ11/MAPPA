"""Policy text extraction rules (task M3), on SYNTHETIC pages: fallback, flags, PDFs."""

import asyncio

from mappa.collect import synthetic
from mappa.models.enums import PolicyFormat
from mappa.parse.policy_text import PolicyText, detect_format, extract

POLICY = f"https://{synthetic.POLICY_HOST}/apps/{{n:02d}}/{{case}}.html"


def _fetch(url: str) -> tuple[bytes, str | None]:
    response = asyncio.run(synthetic.SyntheticFetcher().fetch(url))
    assert response.body is not None
    return response.body, response.content_type


def _extract(url: str) -> PolicyText:
    body, content_type = _fetch(url)
    return extract(body, detect_format(url, content_type, body), final_url=url)


def test_a_normal_policy_uses_the_main_text_extractor() -> None:
    text = _extract(POLICY.format(n=1, case="html"))
    assert text.method == "trafilatura"
    assert text.word_count > 300
    assert (text.language, text.looks_like_policy, text.review_flag) == ("en", True, None)


def test_a_short_page_falls_back_to_full_text_and_is_flagged_with_its_links() -> None:
    url = POLICY.format(n=3, case="short_with_links")
    text = _extract(url)
    assert text.method == "full_text"
    assert text.looks_like_policy is False
    assert text.review_flag is not None
    assert "short page linking to other privacy pages" in text.review_flag
    assert f"https://{synthetic.POLICY_HOST}/apps/full-privacy-policy.html" in text.review_flag


def test_a_non_english_policy_is_flagged_not_dropped() -> None:
    text = _extract(POLICY.format(n=4, case="non_english"))
    assert text.language == "de"
    assert text.review_flag is not None
    assert "not English" in text.review_flag
    assert text.word_count > 300


def test_a_pdf_policy_is_read_with_pypdf() -> None:
    url = f"https://{synthetic.POLICY_HOST}/apps/05/privacy.pdf"
    body, content_type = _fetch(url)
    assert detect_format(url, content_type, body) is PolicyFormat.PDF
    text = extract(body, PolicyFormat.PDF, final_url=url)
    assert (text.method, text.looks_like_policy) == ("pypdf", True)


def test_a_store_page_instead_of_a_policy_is_flagged() -> None:
    app = next(a for a in synthetic.world().values() if a.policy_case == "store_page")
    assert app.policy_url is not None
    text = _extract(app.policy_url)
    assert text.review_flag is not None
    assert "app store page" in text.review_flag


def test_formatting_only_changes_keep_the_normalised_hash() -> None:
    a = extract(
        b"<html><body><p>We collect   your NAME.</p></body></html>",
        PolicyFormat.HTML,
        final_url=None,
    )
    b = extract(
        b"<html><body><div>we collect your name.</div></body></html>",
        PolicyFormat.HTML,
        final_url=None,
    )
    c = extract(
        b"<html><body><p>We sell your name.</p></body></html>", PolicyFormat.HTML, final_url=None
    )
    assert a.normalized_sha256 == b.normalized_sha256
    assert a.normalized_sha256 != c.normalized_sha256
