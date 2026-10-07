"""M2 inclusion rules and sample selection: a pure function, so it is easy to test and to
re-run, and the same metadata always gives the same sample.

Rules (OPEN DECISIONS 1-2, configurable): listing fetched from the AU store, genre in the
health genres, free only, at least ``min_installs`` installs. Eligible apps are then
selected in two strata:

- top ``top_n`` by installs. Google shows installs in buckets ("100,000+"), and hundreds
  of apps share a bucket, so ranking uses ``installs_real`` (the exact figure the page
  embeds) where present, then ratings count, then package name. Every tie is broken.
- ``long_tail_n`` drawn at random from the rest, "random" meaning: order the apps by
  SHA-256 of ``"<random_seed>:<app_id>"`` and take the first N. This is a seeded random
  draw that is *stable*: if a metadata retry adds one app to the pool, at most one app
  in the long tail changes, whereas re-shuffling with ``random.sample`` could replace
  all 200 and waste the work already done on them.

Seed-file apps are always included if their listing was fetched, even when the rules
would exclude them (``inclusion_basis = seed`` records that). Dev snapshots include every
dev-sample app whose listing was fetched.
"""

import hashlib
from collections.abc import Iterable
from dataclasses import dataclass

from mappa.config import InclusionSettings
from mappa.models.enums import InclusionBasis, SampleMode, Status

NOT_SELECTED = "eligible, not selected (beyond top-N and long-tail draw)"


@dataclass(frozen=True)
class Candidate:
    app_id: str
    status: Status  # of the metadata fetch
    genre_id: str | None
    free: bool | None
    installs_min: int | None
    installs_real: int | None
    ratings_count: int | None
    is_seed: bool


@dataclass(frozen=True)
class Decision:
    app_id: str
    included: bool
    exclusion_reason: str | None
    basis: InclusionBasis | None


def decide(
    candidates: Iterable[Candidate], rules: InclusionSettings, *, seed: int, sample: SampleMode
) -> list[Decision]:
    pool = sorted(candidates, key=lambda c: c.app_id)
    decisions: dict[str, Decision] = {}
    eligible: list[Candidate] = []
    for candidate in pool:
        reason = (
            _rule_failure(candidate, rules)
            if sample is SampleMode.FULL
            else _fetch_failure(candidate)
        )
        if reason is None:
            eligible.append(candidate)
        else:
            decisions[candidate.app_id] = Decision(candidate.app_id, False, reason, None)

    if sample is SampleMode.DEV:
        for candidate in eligible:
            decisions[candidate.app_id] = Decision(
                candidate.app_id, True, None, InclusionBasis.DEV_SAMPLE
            )
        return [decisions[c.app_id] for c in pool]

    top = sorted(eligible, key=_install_rank)[: rules.top_n]
    top_ids = {c.app_id for c in top}
    rest = [c for c in eligible if c.app_id not in top_ids]
    tail = sorted(rest, key=lambda c: long_tail_key(seed, c.app_id))[: rules.long_tail_n]
    tail_ids = {c.app_id for c in tail}
    for candidate in eligible:
        if candidate.app_id in top_ids:
            decisions[candidate.app_id] = Decision(
                candidate.app_id, True, None, InclusionBasis.TOP_INSTALLS
            )
        elif candidate.app_id in tail_ids:
            decisions[candidate.app_id] = Decision(
                candidate.app_id, True, None, InclusionBasis.RANDOM_LONG_TAIL
            )
        else:
            decisions[candidate.app_id] = Decision(candidate.app_id, False, NOT_SELECTED, None)

    for candidate in pool:
        if (
            candidate.is_seed
            and candidate.status == Status.OK
            and not decisions[candidate.app_id].included
        ):
            decisions[candidate.app_id] = Decision(
                candidate.app_id, True, None, InclusionBasis.SEED
            )
    return [decisions[c.app_id] for c in pool]


def long_tail_key(seed: int, app_id: str) -> str:
    return hashlib.sha256(f"{seed}:{app_id}".encode()).hexdigest()


def _fetch_failure(candidate: Candidate) -> str | None:
    if candidate.status != Status.OK:
        return f"listing {candidate.status.value} in the AU store"
    return None


def _rule_failure(candidate: Candidate, rules: InclusionSettings) -> str | None:
    if (failure := _fetch_failure(candidate)) is not None:
        return failure
    if candidate.genre_id is None:
        return "genre unknown"
    if candidate.genre_id not in rules.genres:
        return f"genre {candidate.genre_id}"
    if rules.free_only and candidate.free is not True:
        return "paid" if candidate.free is False else "price unknown"
    if candidate.installs_min is None:
        return "installs unknown"
    if candidate.installs_min < rules.min_installs:
        return f"installs below {rules.min_installs:,}"
    return None


def _install_rank(candidate: Candidate) -> tuple[int, int, int, str]:
    installs = (
        candidate.installs_real if candidate.installs_real is not None else candidate.installs_min
    )
    return (
        -(installs or 0),
        -(candidate.installs_min or 0),
        -(candidate.ratings_count or 0),
        candidate.app_id,
    )
