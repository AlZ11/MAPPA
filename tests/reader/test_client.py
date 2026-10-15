import pytest

from mappa.reader.llm.client import (
    BudgetExceeded,
    BudgetGuard,
    Reply,
    api_key,
    cache_key,
    estimate_cost,
)
from mappa.reader.settings import Estimate, Prices

PRICES = Prices(input_aud_per_mtok=1.0, output_aud_per_mtok=10.0)


def test_estimate_without_prices_still_counts_tokens() -> None:
    estimate = estimate_cost([("s" * 40, "u" * 40)] * 3, Prices(), Estimate())
    assert (estimate.calls, estimate.input_tokens, estimate.output_tokens) == (3, 60, 450)
    assert estimate.aud is None


def test_estimate_with_prices() -> None:
    estimate = estimate_cost(
        [("s" * 4_000_000, "")], PRICES, Estimate(output_tokens_per_paragraph=0)
    )
    assert estimate.aud == pytest.approx(1.0)


def test_budget_stops_before_the_cap() -> None:
    guard = BudgetGuard(PRICES, max_aud=0.001)
    guard.record(Reply({}, input_tokens=500, output_tokens=0))  # 0.0005 AUD
    guard.check(0.0004)
    with pytest.raises(BudgetExceeded):
        guard.check(0.0006)


def test_cache_key_covers_every_setting() -> None:
    base: dict[str, object] = {
        "provider": "p",
        "model": "m",
        "prompt_version": "v1",
        "settings": {"t": 0},
        "paragraph_sha256": "abc",
        "run": 1,
    }
    keys = {cache_key(**base)}  # type: ignore[arg-type]
    for field, value in [
        ("provider", "q"),
        ("model", "n"),
        ("prompt_version", "v2"),
        ("settings", {"t": 1}),
        ("paragraph_sha256", "abd"),
        ("run", 2),
    ]:
        keys.add(cache_key(**(base | {field: value})))  # type: ignore[arg-type]
    assert len(keys) == 7


def test_api_key_comes_from_the_environment_only(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("MADE_UP_KEY", raising=False)
    with pytest.raises(RuntimeError, match="MADE_UP_KEY"):
        api_key("MADE_UP_KEY")
    monkeypatch.setenv("MADE_UP_KEY", "not-a-real-key")
    assert api_key("MADE_UP_KEY") == "not-a-real-key"
