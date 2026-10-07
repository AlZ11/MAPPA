"""Inclusion rules and the two-stratum sample: deterministic, stable, explainable."""

from dataclasses import replace

import pytest

from mappa.collect.inclusion import NOT_SELECTED, Candidate, Decision, decide, long_tail_key
from mappa.config import InclusionSettings
from mappa.models.enums import InclusionBasis, SampleMode, Status

RULES = InclusionSettings(top_n=2, long_tail_n=2)
SEED = 20261005


def app(name: str, installs: int = 10_000, **changes: object) -> Candidate:
    base = Candidate(
        app_id=f"com.example.{name}",
        status=Status.OK,
        genre_id="HEALTH_AND_FITNESS",
        free=True,
        installs_min=installs,
        installs_real=installs,
        ratings_count=100,
        is_seed=False,
    )
    return replace(base, **changes)  # type: ignore[arg-type]


def by_id(decisions: list[Decision]) -> dict[str, Decision]:
    return {d.app_id.removeprefix("com.example."): d for d in decisions}


@pytest.mark.parametrize(
    ("candidate", "reason"),
    [
        (app("gone", status=Status.NOT_FOUND), "listing not_found in the AU store"),
        (app("blocked", status=Status.BLOCKED), "listing blocked in the AU store"),
        (app("game", genre_id="GAME_PUZZLE"), "genre GAME_PUZZLE"),
        (app("nogenre", genre_id=None), "genre unknown"),
        (app("paid", free=False), "paid"),
        (app("noprice", free=None), "price unknown"),
        (app("tiny", installs=500, installs_real=600), "installs below 1,000"),
        (app("noinstalls", installs_min=None), "installs unknown"),
    ],
)
def test_each_rule_records_why_an_app_is_excluded(candidate: Candidate, reason: str) -> None:
    [decision] = decide([candidate], RULES, seed=SEED, sample=SampleMode.FULL)
    assert (decision.included, decision.exclusion_reason, decision.basis) == (False, reason, None)


def test_top_n_ranks_by_exact_installs_within_a_bucket() -> None:
    """All four share the 100,000+ bucket; Google's exact figure decides the order."""
    pool = [
        app("a", installs=100_000, installs_real=150_000),
        app("b", installs=100_000, installs_real=420_000),
        app("c", installs=100_000, installs_real=310_000),
        app("d", installs=100_000, installs_real=None),
    ]
    decisions = by_id(
        decide(pool, InclusionSettings(top_n=2, long_tail_n=0), seed=SEED, sample=SampleMode.FULL)
    )
    assert {k for k, d in decisions.items() if d.basis is InclusionBasis.TOP_INSTALLS} == {"b", "c"}
    assert decisions["a"].exclusion_reason == NOT_SELECTED


def test_ties_are_broken_by_ratings_then_package_name() -> None:
    pool = [app("z", ratings_count=5), app("y", ratings_count=9), app("x", ratings_count=9)]
    decisions = by_id(
        decide(pool, InclusionSettings(top_n=2, long_tail_n=0), seed=SEED, sample=SampleMode.FULL)
    )
    assert {k for k, d in decisions.items() if d.included} == {"x", "y"}


def test_long_tail_is_a_seeded_draw_from_the_rest() -> None:
    pool = [app(f"app{i:02d}", installs=1_000 + i) for i in range(30)]
    decisions = decide(pool, RULES, seed=SEED, sample=SampleMode.FULL)
    tail = {d.app_id for d in decisions if d.basis is InclusionBasis.RANDOM_LONG_TAIL}
    rest = sorted(
        (c for c in pool if c.app_id not in {"com.example.app29", "com.example.app28"}),
        key=lambda c: long_tail_key(SEED, c.app_id),
    )
    assert tail == {c.app_id for c in rest[:2]}
    assert (
        decide(pool, RULES, seed=SEED, sample=SampleMode.FULL) == decisions
    )  # same input, same sample
    assert (
        decide(pool, RULES, seed=SEED + 1, sample=SampleMode.FULL) != decisions
    )  # the seed matters


def test_a_late_arrival_changes_the_long_tail_by_at_most_one_app() -> None:
    """Why the draw orders by hash instead of calling random.sample on the pool."""
    rules = InclusionSettings(top_n=0, long_tail_n=10)
    pool = [app(f"app{i:02d}") for i in range(40)]
    before = {
        d.app_id for d in decide(pool, rules, seed=SEED, sample=SampleMode.FULL) if d.included
    }
    after = {
        d.app_id
        for d in decide([*pool, app("late")], rules, seed=SEED, sample=SampleMode.FULL)
        if d.included
    }
    assert len(before - after) <= 1


def test_seed_apps_are_included_even_when_the_rules_say_no() -> None:
    pool = [
        app("big", installs=10**7),
        app("seed", installs=10, is_seed=True),
        app("gone", status=Status.NOT_FOUND, is_seed=True),
    ]
    decisions = by_id(
        decide(pool, InclusionSettings(top_n=1, long_tail_n=0), seed=SEED, sample=SampleMode.FULL)
    )
    assert decisions["big"].basis is InclusionBasis.TOP_INSTALLS
    assert decisions["seed"].basis is InclusionBasis.SEED
    assert decisions["gone"].included is False  # no listing, nothing to include


def test_a_seed_selected_by_the_rules_keeps_its_stratum() -> None:
    [decision] = decide([app("calm", is_seed=True)], RULES, seed=SEED, sample=SampleMode.FULL)
    assert decision.basis is InclusionBasis.TOP_INSTALLS


def test_dev_sample_includes_every_fetched_listing_regardless_of_rules() -> None:
    pool = [app("paid", free=False), app("tiny", installs=1), app("gone", status=Status.NOT_FOUND)]
    decisions = by_id(decide(pool, RULES, seed=SEED, sample=SampleMode.DEV))
    assert decisions["paid"].basis is InclusionBasis.DEV_SAMPLE
    assert decisions["tiny"].basis is InclusionBasis.DEV_SAMPLE
    assert decisions["gone"].included is False
