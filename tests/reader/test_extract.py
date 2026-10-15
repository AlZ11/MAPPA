"""Quote verification and the per-paragraph loop, with recorded made-up replies."""

import hashlib
from pathlib import Path
from typing import Any

from mappa.reader.canonical import Party, Segment
from mappa.reader.extract import Extractor, QuoteMatch, verify_quote
from mappa.reader.llm.client import BudgetGuard, CachedClient, RecordedProvider, ResponseCache
from mappa.reader.llm.prompt import USER_TEMPLATE, PracticeDefinition
from mappa.reader.settings import Estimate, LLMSettings, Prices

DEFS = [PracticeDefinition("Location_GPS", "made-up"), PracticeDefinition("SSO", "made-up")]


def test_quote_verification_levels() -> None:
    lq, rq, dash = (
        "\N{LEFT DOUBLE QUOTATION MARK}",
        "\N{RIGHT DOUBLE QUOTATION MARK}",
        "\N{EM DASH}",
    )
    paragraph = f"We use your {lq}GPS{rq}  data {dash} always."
    assert verify_quote(f"your {lq}GPS", paragraph) is QuoteMatch.EXACT
    assert verify_quote('your "GPS" data - always.', paragraph) is QuoteMatch.NORMALIZED
    assert verify_quote("We use your gps", paragraph) is None  # case is not normalised
    assert verify_quote("   ", paragraph) is None


def _claim(practice: str, party: str, quote: str) -> dict[str, str]:
    return {"practice": practice, "party": party, "modality": "performed", "quote": quote}


def _extractor(
    tmp_path: Path, replies: dict[str, dict[str, Any]]
) -> tuple[Extractor, RecordedProvider]:
    provider = RecordedProvider(
        {
            hashlib.sha256(USER_TEMPLATE.format(paragraph=p).encode()).hexdigest(): r
            for p, r in replies.items()
        }
    )
    client = CachedClient(
        provider, ResponseCache(tmp_path), BudgetGuard(Prices(), None), Estimate(), "test-version"
    )
    return Extractor(client, LLMSettings(provider="recorded", model="made-up"), DEFS, []), provider


def test_verified_rejected_and_failed_paragraphs(tmp_path: Path) -> None:
    ok = "We use GPS. Sign in with a made-up provider."
    broken = "A paragraph the made-up model answers badly."
    extractor, _ = _extractor(
        tmp_path,
        {
            ok: {
                "claims": [
                    _claim("Location_GPS", "first_party", "We use GPS."),
                    _claim("Location_GPS", "third_party", "We sell GPS data."),  # not in paragraph
                    _claim("SSO", "third_party", "Sign in with a made-up provider."),
                ]
            },
            broken: {"claims": [{"practice": "Location_GPS"}]},
        },
    )
    result = extractor.segment("p1", Segment(idx=0, text=ok), run=1)
    assert result.status == "ok"
    assert [(v.claim.practice, v.claim.party) for v in result.verified] == [
        ("Location_GPS", Party.FIRST),
        ("SSO", Party.FIRST),  # SSO party folded
    ]
    assert [r.reason for r in result.rejected] == ["quote_not_found"]
    failed = extractor.segment("p1", Segment(idx=1, text=broken), run=1)
    assert (failed.status, failed.verified) == ("failed", [])
    assert failed.error is not None


def test_cache_makes_reruns_free_and_run_number_is_part_of_the_key(tmp_path: Path) -> None:
    text = "We use GPS."
    extractor, provider = _extractor(tmp_path, {text: {"claims": []}})
    for _ in range(3):
        extractor.segment("p1", Segment(idx=0, text=text), run=1)
    assert (provider.calls, extractor.client.cache_hits) == (1, 2)
    extractor.segment("p1", Segment(idx=0, text=text), run=2)
    assert provider.calls == 2
