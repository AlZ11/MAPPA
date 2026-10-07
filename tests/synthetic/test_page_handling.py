"""Our handling of Google Play pages, on SYNTHETIC pages built to the parsers' expected
structure: de-duplication, error paths, unknown != absent. Not a check of the real layout.
"""

import asyncio
import json

import pytest

from mappa.collect import synthetic
from mappa.collect.metadata import contains_ads, listing_columns
from mappa.collect.play_store import datasafety_url, details_url, search_url
from mappa.models.enums import LabelPractice, LabelSection, Status
from mappa.parse.datasafety import LabelParseError, parse_datasafety_page
from mappa.parse.play_data import PlayParseError, parse_listing_page, parse_search_page

APPS = synthetic.world()


def _page(url: str) -> str:
    response = asyncio.run(synthetic.SyntheticFetcher().fetch(url))
    assert response.body is not None
    return response.body.decode()


def _app(case: str, field: str = "label_case") -> synthetic.SyntheticApp:
    return next(a for a in APPS.values() if getattr(a, field) == case and a.listed)


def test_every_synthetic_page_is_marked() -> None:
    for url in (
        search_url("sleep", lang="en", country="au"),
        details_url(synthetic.synthetic_app_id(1), lang="en", country="au"),
        datasafety_url(synthetic.synthetic_app_id(1), lang="en", country="au"),
    ):
        assert synthetic.SYNTHETIC_MARKER in _page(url)


def test_search_results_are_ranked_and_the_top_card_is_not_double_counted() -> None:
    query = next(
        q for q in (f"term {i}" for i in range(200)) if synthetic._rank_key(q, "top-card") % 4 == 0
    )
    hits = parse_search_page(_page(search_url(query, lang="en", country="au"))).hits
    assert [h.rank for h in hits] == list(range(1, len(hits) + 1))
    assert len({h.app_id for h in hits}) == len(hits)


def test_a_page_without_search_data_is_an_error_not_zero_results() -> None:
    with pytest.raises(PlayParseError, match="ds:4"):
        parse_search_page("<html><body>Something else entirely</body></html>")


def test_search_data_without_a_results_list_is_flagged() -> None:
    html = synthetic._page("", ("ds:4", [[None, [[None]]]]))
    page = parse_search_page(html)
    assert page.hits == []
    assert page.note is not None
    assert "no results list" in page.note


def test_listing_fields_are_mapped_with_types_checked() -> None:
    app = APPS[synthetic.synthetic_app_id(5)]  # a paid app
    url = details_url(app.app_id, lang="en", country="au")
    html = _page(url)
    columns = listing_columns(parse_listing_page(html, app.app_id, url), html)
    assert columns["title"] == app.title
    assert columns["installs_min"] == app.min_installs
    assert columns["installs_real"] == app.real_installs
    assert (columns["free"], columns["price"], columns["currency"]) == (False, 2.99, "AUD")
    assert columns["privacy_policy_url"] == app.policy_url
    assert columns["updated_at"].year == 2026


def test_contains_ads_unknown_when_the_field_is_missing() -> None:
    """The library would say False ('no ads'); a missing field must stay unknown."""
    assert contains_ads(synthetic._page("", ("ds:5", [None, [None, None, []]]))) is None
    assert (
        contains_ads(_page(details_url(synthetic.synthetic_app_id(2), lang="en", country="au")))
        is True
    )
    assert (
        contains_ads(_page(details_url(synthetic.synthetic_app_id(1), lang="en", country="au")))
        is False
    )


def test_a_page_that_is_not_a_listing_raises() -> None:
    with pytest.raises(PlayParseError, match="no app title"):
        parse_listing_page("<html><body>hi</body></html>", "a.b", "https://x")


def test_full_label_keeps_unmapped_strings_and_splits_purposes() -> None:
    label = parse_datasafety_page(
        _page(datasafety_url(_app("full").app_id, lang="en", country="au"))
    )
    assert label.status is Status.OK
    unmapped = [f for f in label.facts if not f.mapped]
    assert [(f.raw_category, f.raw_label, f.category) for f in unmapped] == [
        ("Synthetic category", "Synthetic unmapped type", None)
    ]
    device = next(f for f in label.facts if f.raw_label == "Device or other IDs")
    assert device.purposes == ["Analytics", "Fraud prevention, security, and compliance"]
    assert device.evidence_text.startswith(
        "Data collected > Device or other IDs > Device or other IDs"
    )
    assert {f.section for f in label.facts} == {LabelSection.SHARED, LabelSection.COLLECTED}


def test_optional_data_is_marked() -> None:
    label = parse_datasafety_page(
        _page(datasafety_url(_app("optional").app_id, lang="en", country="au"))
    )
    location = next(f for f in label.facts if f.raw_label == "Approximate location")
    assert location.optional is True
    assert "(Optional)" in location.evidence_text


def test_headline_statements_become_practices_only_when_stated() -> None:
    html = _page(datasafety_url(_app("no_data_collected").app_id, lang="en", country="au"))
    practices = {p.practice: p.value for p in parse_datasafety_page(html).practices}
    assert practices[LabelPractice.NO_DATA_COLLECTED] is True
    assert practices[LabelPractice.NO_DATA_SHARED] is True
    full = parse_datasafety_page(
        _page(datasafety_url(_app("full").app_id, lang="en", country="au"))
    )
    assert LabelPractice.NO_DATA_SHARED not in {p.practice for p in full.practices}


def test_not_provided_is_a_status_not_an_empty_label() -> None:
    label = parse_datasafety_page(
        _page(datasafety_url(_app("not_provided").app_id, lang="en", country="au"))
    )
    assert (label.status, label.facts, label.practices) == (Status.NOT_PROVIDED, [], [])


def test_a_page_contradicting_itself_is_an_error() -> None:
    html = _page(datasafety_url(_app("full").app_id, lang="en", country="au"))
    contradiction = html.replace(
        "<h1>Data safety</h1>", "<h1>Data safety</h1><p>No data shared with third parties</p>"
    )
    with pytest.raises(LabelParseError, match="also lists"):
        parse_datasafety_page(contradiction)


def test_a_label_block_with_nothing_in_it_is_an_error_not_an_empty_label() -> None:
    label: list[object] = []
    synthetic._place(label, [3], "something unrelated")
    ds3: list[object] = []
    synthetic._place(ds3, [1, 2, 1, 138], label)
    with pytest.raises(LabelParseError, match="no data types"):
        parse_datasafety_page(synthetic._page("", ("ds:3", ds3)))


def test_unknown_security_statements_are_kept_not_dropped() -> None:
    html = _page(datasafety_url(_app("full").app_id, lang="en", country="au"))
    novel = html.replace(
        "You can request that data be deleted", "Data deletion handled by the moon"
    )
    label = parse_datasafety_page(novel)
    assert any("handled by the moon" in s for s in label.unmapped_statements)


def test_a_page_with_no_label_data_is_an_error() -> None:
    with pytest.raises(LabelParseError, match="no Data Safety label data"):
        parse_datasafety_page(synthetic._page("<p>hello</p>", ("ds:9", json.loads("[1, 2]"))))
