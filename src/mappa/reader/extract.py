"""Quote verification: an AI claim is only used if its quote is really in the paragraph.

The rule (task brief R3), applied to every claim:

1. the quote appears exactly in the paragraph -> ``exact``;
2. it appears after normalising whitespace, curly quotes and dashes -> ``normalized``;
3. otherwise the claim is rejected (``quote_not_found``): counted for the
   made-up-answer rate, never used for scoring.

Nothing else is normalised, case included: a quote that only matches after rewording is
not a quote.

``Extractor`` runs the loop: one request per paragraph and run number, through the
cached client. A paragraph whose call fails or whose reply breaks the schema is
``failed``, counted and reported, and never treated as "no claims" (CLAUDE.md principle
4: unknown is not absent). ``BudgetExceeded`` is not caught: it stops the whole run.
"""

from collections.abc import Sequence
from dataclasses import dataclass, field
from enum import StrEnum

from mappa.reader.canonical import Claim, Policy, Segment
from mappa.reader.datasets.app350 import normalize_key
from mappa.reader.llm.client import CachedClient, ProviderError, Request
from mappa.reader.llm.prompt import (
    FewShotExample,
    PracticeDefinition,
    PromptError,
    build_prompt,
    claim_tool_schema,
    parse_claims,
)
from mappa.reader.settings import LLMSettings

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


@dataclass
class SegmentResult:
    policy_id: str
    segment_idx: int
    run: int
    status: str  # "ok" or "failed"
    verified: list[VerifiedClaim] = field(default_factory=list)
    rejected: list[RejectedClaim] = field(default_factory=list)
    error: str | None = None


@dataclass
class Extractor:
    client: CachedClient
    llm: LLMSettings
    definitions: Sequence[PracticeDefinition]
    examples: Sequence[FewShotExample]

    def __post_init__(self) -> None:
        self.names = [d.name for d in self.definitions]
        self.schema = claim_tool_schema(self.names)

    def request(self, paragraph: str) -> Request:
        if not self.llm.model:
            raise PromptError("no model configured in reader.toml [llm]")
        system, user = build_prompt(self.definitions, self.examples, paragraph)
        return Request(
            model=self.llm.model,
            system=system,
            user=user,
            tool_schema=self.schema,
            temperature=self.llm.temperature,
            max_output_tokens=self.llm.max_output_tokens,
        )

    def segment(self, policy_id: str, segment: Segment, run: int) -> SegmentResult:
        try:
            reply = self.client.complete(
                self.request(segment.text), paragraph=segment.text, run=run
            )
            claims = [_fold_sso(c) for c in parse_claims(reply.payload, self.names)]
        except (ProviderError, PromptError) as exc:
            return SegmentResult(policy_id, segment.idx, run, "failed", error=str(exc))
        verified, rejected = verify_claims(claims, segment.text, segment.idx)
        return SegmentResult(policy_id, segment.idx, run, "ok", verified, rejected)

    def policy(self, policy: Policy, run: int) -> list[SegmentResult]:
        return [self.segment(policy.policy_id, s, run) for s in policy.segments]


def _fold_sso(claim: Claim) -> Claim:
    """SSO practices carry no party in APP-350; the prompt asks for first_party, and
    this makes sure a stray third_party can't create a label that doesn't exist."""
    _, party = normalize_key(claim.key)
    return claim if party is claim.party else claim.model_copy(update={"party": party})
