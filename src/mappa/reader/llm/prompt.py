"""The extraction prompt: versioned, built from the dataset's own practice definitions.

Rules from the task brief (R3), all enforced here:

- practices are listed with one-line definitions from the dataset README, read from
  the zip at runtime (``app350.practice_definitions``), never stored in the repo;
- few-shot examples come only from the train split: ``build_prompt`` refuses any other,
  and ``select_examples`` picks them from train policies only;
- "return no claims if the paragraph describes none of these practices";
- "negations are claims": "we never share your location" is ``not_performed``;
- the output shape is forced with a tool / structured-output schema *and* validated
  with pydantic afterwards (``parse_claims``).

Versioning: ``PROMPT_VERSION`` goes into every cache key and run record. A test compares
the template's hash with ``TEMPLATE_SHA256``, so editing the wording without bumping the
version fails the test suite instead of silently mixing two prompts in one run.
"""

import hashlib
import json
import random
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel, ConfigDict, ValidationError

from mappa.reader.canonical import Claim, Modality, Party, Policy, Segment

PROMPT_VERSION = "app350-v1"

SYSTEM_TEMPLATE = """\
You read one paragraph of a mobile app's privacy policy and list the data practices it \
states, using only the practice names below.

Practices:
{practices}

Rules:
- Return no claims if the paragraph describes none of these practices.
- A claim has: practice (exactly one name from the list), party (first_party if the app's \
own company does it, third_party if another company does it), modality (performed or \
not_performed), and quote.
- Negations are claims: "we never share your location" is a claim with modality \
not_performed.
- SSO and Facebook_SSO have no party: always use first_party for them.
- The quote must be the exact supporting sentence or sentences, copied word for word from \
the paragraph.
"""

USER_TEMPLATE = "Paragraph:\n{paragraph}"

TEMPLATE_SHA256 = "75405a7a6ffc88526ebb8bb3481e92b93332f098d63d3f89ff3ef28bd90ce9df"


class PromptError(ValueError):
    """The prompt would break a rule (e.g. a few-shot example not from the train split)."""


@dataclass(frozen=True)
class PracticeDefinition:
    name: str
    definition: str


@dataclass(frozen=True)
class FewShotExample:
    policy_id: str
    split: str
    paragraph: str
    claims: tuple[Claim, ...]


class _ClaimOut(BaseModel):
    model_config = ConfigDict(extra="forbid")
    practice: str
    party: Party
    modality: Modality
    quote: str


class _ClaimsOut(BaseModel):
    model_config = ConfigDict(extra="forbid")
    claims: list[_ClaimOut]


def template_sha256() -> str:
    return hashlib.sha256((SYSTEM_TEMPLATE + USER_TEMPLATE).encode()).hexdigest()


def build_prompt(
    practices: Sequence[PracticeDefinition],
    examples: Sequence[FewShotExample],
    paragraph: str,
) -> tuple[str, str]:
    """(system, user) messages for one paragraph."""
    leaked = [e.policy_id for e in examples if e.split != "train"]
    if leaked:
        raise PromptError(f"few-shot examples must come from the train split; not: {leaked}")
    listing = "\n".join(f"- {p.name}: {p.definition}" for p in practices)
    system = SYSTEM_TEMPLATE.format(practices=listing)
    if examples:
        shots = "\n\n".join(
            f"Example paragraph:\n{e.paragraph}\nClaims:\n"
            + json.dumps({"claims": [c.model_dump(mode="json") for c in e.claims]})
            for e in examples
        )
        system += f"\nExamples:\n\n{shots}\n"
    return system, USER_TEMPLATE.format(paragraph=paragraph)


# Few-shot selection (see select_examples).
EXAMPLE_SEED = 0
EXAMPLE_MAX_CHARS = 300


def select_examples(train: Sequence[Policy], *, seed: int = EXAMPLE_SEED) -> list[FewShotExample]:
    """Four short train paragraphs, one of each kind the model must handle:

    a first-party practice, a third-party practice, a negation, and a paragraph with no
    practice at all (the "return no claims" case).

    - Only real (non-synthetic) train policies: test has no synthetic text, so examples
      should look like what the model will be scored on.
    - Only paragraphs of at most ``EXAMPLE_MAX_CHARS`` characters, and the quote is the
      whole paragraph: APP-350 labels paragraphs, not sentences, so the whole paragraph
      is the only quote the labels can vouch for. Short paragraphs keep that honest.
    - Seeded random choice among the candidates, so the prompt is reproducible.
    """
    kinds: dict[str, list[tuple[str, Segment]]] = {
        "first_party": [],
        "third_party": [],
        "not_performed": [],
        "none": [],
    }
    for policy in sorted(train, key=lambda p: int(p.policy_id)):
        if policy.contains_synthetic:
            continue
        for segment in policy.segments:
            if not 40 <= len(segment.text) <= EXAMPLE_MAX_CHARS:
                continue
            labels = segment.labels
            if not labels:
                kinds["none"].append((policy.policy_id, segment))
            elif all(lab.modality is Modality.NOT_PERFORMED for lab in labels):
                kinds["not_performed"].append((policy.policy_id, segment))
            elif all(lab.modality is Modality.PERFORMED for lab in labels):
                party = {lab.party for lab in labels}
                if party == {Party.FIRST}:
                    kinds["first_party"].append((policy.policy_id, segment))
                elif party == {Party.THIRD}:
                    kinds["third_party"].append((policy.policy_id, segment))
    rng = random.Random(seed)
    examples: list[FewShotExample] = []
    for kind, candidates in kinds.items():
        if not candidates:
            raise PromptError(f"no short train paragraph of kind {kind!r} for a few-shot example")
        policy_id, segment = rng.choice(candidates)
        claims = tuple(
            dict.fromkeys(
                Claim(
                    practice=lab.practice,
                    party=lab.party,
                    modality=lab.modality,
                    quote=segment.text,
                )
                for lab in segment.labels
            )
        )
        examples.append(FewShotExample(policy_id, "train", segment.text, claims))
    return examples


def claim_tool_schema(practice_names: Sequence[str]) -> dict[str, Any]:
    """JSON schema forcing the reply's shape; practice names are an enum."""
    return {
        "type": "object",
        "properties": {
            "claims": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "practice": {"type": "string", "enum": list(practice_names)},
                        "party": {"type": "string", "enum": [p.value for p in Party]},
                        "modality": {"type": "string", "enum": [m.value for m in Modality]},
                        "quote": {"type": "string"},
                    },
                    "required": ["practice", "party", "modality", "quote"],
                    "additionalProperties": False,
                },
            }
        },
        "required": ["claims"],
        "additionalProperties": False,
    }


def parse_claims(payload: dict[str, Any], practice_names: Sequence[str]) -> list[Claim]:
    """Validate a reply with pydantic even though the provider was told the shape.
    Raises ``PromptError`` on anything off-schema, including unknown practice names."""
    try:
        parsed = _ClaimsOut.model_validate(payload)
    except ValidationError as exc:
        raise PromptError(f"reply doesn't match the claim schema: {exc}") from None
    allowed = set(practice_names)
    unknown = sorted({c.practice for c in parsed.claims} - allowed)
    if unknown:
        raise PromptError(f"reply uses practice names outside the list: {unknown}")
    return [Claim(**c.model_dump()) for c in parsed.claims]
