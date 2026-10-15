"""Combine one method's paragraph-level claims into one answer per practice per policy.

The rule is ``canonical.combine``, the same one that turns expert labels into gold
answers, so a method is never scored by a different rule than the one that built the
answer key. Only verified claims count: rejected ones are already filtered out.
"""

from collections.abc import Iterable

from mappa.reader.canonical import Answer, Claim, PracticeKey, answers_from_labels
from mappa.reader.extract import VerifiedClaim


def answers_from_claims(
    claims: Iterable[Claim | VerifiedClaim], keys: Iterable[PracticeKey]
) -> dict[PracticeKey, Answer]:
    plain = (c.claim if isinstance(c, VerifiedClaim) else c for c in claims)
    return answers_from_labels(plain, keys)
