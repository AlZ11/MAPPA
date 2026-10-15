"""Quote verification: an AI claim is only used if its quote is really in the paragraph.

The rule (task brief R3), applied to every claim:

1. the quote appears exactly in the paragraph -> ``exact``;
2. it appears after normalising whitespace, curly quotes and dashes -> ``normalized``;
3. otherwise the claim is rejected (``quote_not_found``): counted for the
   made-up-answer rate, never used for scoring.

Nothing else is normalised, case included: a quote that only matches after rewording is
not a quote.

The per-paragraph AI extraction loop is added once the APP-350 practice list is known.
"""

from dataclasses import dataclass
from enum import StrEnum

from mappa.reader.canonical import Claim

_SAME_CHARACTER = str.maketrans(
    {
        "\N{LEFT SINGLE QUOTATION MARK}": "'",
        "\N{RIGHT SINGLE QUOTATION MARK}": "'",
        "\N{SINGLE LOW-9 QUOTATION MARK}": "'",
        "\N{PRIME}": "'",
        "\N{LEFT DOUBLE QUOTATION MARK}": '"',
        "\N{RIGHT DOUBLE QUOTATION MARK}": '"',
        "\N{DOUBLE LOW-9 QUOTATION MARK}": '"',
        "\N{DOUBLE PRIME}": '"',
        "\N{HYPHEN}": "-",
        "\N{NON-BREAKING HYPHEN}": "-",
        "\N{FIGURE DASH}": "-",
        "\N{EN DASH}": "-",
        "\N{EM DASH}": "-",
        "\N{MINUS SIGN}": "-",
    }
)


class QuoteMatch(StrEnum):
    EXACT = "exact"
    NORMALIZED = "normalized"


@dataclass(frozen=True)
class VerifiedClaim:
    claim: Claim
    segment_idx: int
    match: QuoteMatch


@dataclass(frozen=True)
class RejectedClaim:
    claim: Claim
    segment_idx: int
    reason: str  # "quote_not_found"


def normalize(text: str) -> str:
    """Same quote and dash characters, whitespace runs collapsed to one space."""
    return " ".join(text.translate(_SAME_CHARACTER).split())


def verify_quote(quote: str, paragraph: str) -> QuoteMatch | None:
    if not quote.strip():
        return None
    if quote in paragraph:
        return QuoteMatch.EXACT
    if normalize(quote) in normalize(paragraph):
        return QuoteMatch.NORMALIZED
    return None


def verify_claims(
    claims: list[Claim], paragraph: str, segment_idx: int
) -> tuple[list[VerifiedClaim], list[RejectedClaim]]:
    verified: list[VerifiedClaim] = []
    rejected: list[RejectedClaim] = []
    for claim in claims:
        match = verify_quote(claim.quote, paragraph)
        if match is None:
            rejected.append(RejectedClaim(claim, segment_idx, "quote_not_found"))
        else:
            verified.append(VerifiedClaim(claim, segment_idx, match))
    return verified, rejected
