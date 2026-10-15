"""Keyword baseline: term lists per practice, plus party and negation cues per sentence.

Term lists: APP-350 ships ``features.yml``, keyword lists per data type that the dataset
authors wrote by hand from the train and validation policies (Story et al., PAL 2019).
They are loaded at runtime from ``data-public/`` (licence: never committed). Using them,
rather than lists written here, means nobody tuned the terms while looking at our
validation results. They were written *with* the validation policies in view, though,
so validation scores for this baseline are optimistic. Test was not used by the authors.

The authors match terms as lower-case substrings (some terms carry deliberate spaces,
e.g. ``" age "``), so this does the same, on the sentence padded with spaces.

For every sentence containing a practice's term:

- **party** (cues from the task brief): a ``third_party`` claim if a third-party cue
  ("third parties", "partners", "advertisers") is present; a ``first_party`` claim if a
  first-party cue ("we", "our", "us") is present *or* no third-party cue is. A sentence
  like "we collect your location and share it with partners" yields both, as an expert
  annotator would label it.
- **modality**: ``not_performed`` if a negation cue ("do not", "never", "will not", and
  their contractions) is in
  the sentence, else ``performed``. Crude by design: "we collect X but never sell it"
  becomes a negation. That is what a keyword baseline can do.
- **SSO**: ``features.yml`` has one term list for ``SSO`` and ``Facebook_SSO``. A match
  gives an ``SSO`` claim, plus ``Facebook_SSO`` if "facebook" is in the sentence (in
  train, 61 of 86 Facebook-sign-in paragraphs are also labelled ``SSO``).
"""

import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Any

import yaml

from mappa.reader.aggregate import answers_from_claims
from mappa.reader.canonical import Answer, Claim, Modality, Party, Policy, PracticeKey
from mappa.reader.datasets.app350 import DATA_TYPES, NO_PARTY, parse_label_name

FEATURES_FILE = "features.yml"
FIRST_PARTY_CUES = (" we ", " our ", " us ")
THIRD_PARTY_CUES = (
    "third part",  # third party, third parties
    "third-part",
    "partner",
    "advertiser",
)
NEGATION_CUES = (
    " do not ",
    " does not ",
    " did not ",
    " will not ",
    " would not ",
    " never ",
    " don't ",
    " doesn't ",
    " won't ",
)
FACEBOOK = "facebook"
_SENTENCE_END = re.compile(r"(?<=[.!?;])\s+|\n+")


class FeaturesError(ValueError):
    """features.yml isn't in the shape documented in DATA_APP350.md."""


def sentences(text: str) -> list[str]:
    return [s.strip() for s in _SENTENCE_END.split(text) if s.strip()]


def padded(sentence: str) -> str:
    """Lower case, whitespace collapsed, padded with spaces. Punctuation is kept, because
    some of the authors' terms contain it (e.g. ``"identify, contact"``)."""
    return (
        " "
        + " ".join(sentence.lower().replace("\N{RIGHT SINGLE QUOTATION MARK}", "'").split())
        + " "
    )


def words(sentence: str) -> str:
    """As ``padded``, with punctuation turned into spaces, for the cue words: so
    ``" we "`` matches "We," and ``" don't "`` matches "don't."."""
    return " " + " ".join(re.sub(r"[^\w']+", " ", padded(sentence)).split()) + " "


def load_terms(text: str, path: str = FEATURES_FILE) -> dict[str, tuple[str, ...]]:
    """features.yml text -> {data type or "SSO": lower-case terms}. Strict about its shape."""
    try:
        groups: Any = yaml.safe_load(text)["data_types"]
    except (yaml.YAMLError, KeyError, TypeError) as exc:
        raise FeaturesError(f"{path}: cannot read data_types ({exc})") from None
    terms: dict[str, tuple[str, ...]] = {}
    for group in groups:
        practices = {parse_label_name(name)[0] for name in group["practices"]}
        if practices == set(NO_PARTY):
            key = "SSO"
        elif len(practices) == 1 and practices <= set(DATA_TYPES):
            (key,) = practices
        else:
            raise FeaturesError(f"{path}: unexpected group {group['practices']}")
        if key in terms or not group["features"]:
            raise FeaturesError(f"{path}: {key} listed twice or without terms")
        terms[key] = tuple(str(term).lower() for term in group["features"])
    missing = (set(DATA_TYPES) | {"SSO"}) - set(terms)
    if missing:
        raise FeaturesError(f"{path}: no terms for {sorted(missing)}")
    return terms


@dataclass
class KeywordBaseline:
    terms: Mapping[str, tuple[str, ...]]  # data type or "SSO" -> lower-case terms

    def segment_claims(self, text: str) -> list[Claim]:
        claims: list[Claim] = []
        for sentence in sentences(text):
            lowered, cue_text = padded(sentence), words(sentence)
            modality = (
                Modality.NOT_PERFORMED
                if any(cue in cue_text for cue in NEGATION_CUES)
                else Modality.PERFORMED
            )
            third = any(cue in cue_text for cue in THIRD_PARTY_CUES)
            first = any(cue in cue_text for cue in FIRST_PARTY_CUES) or not third
            parties = [p for p, on in ((Party.FIRST, first), (Party.THIRD, third)) if on]
            for practice, terms in self.terms.items():
                if not any(term in lowered for term in terms):
                    continue
                if practice == "SSO":
                    names = ["SSO", "Facebook_SSO"] if FACEBOOK in lowered else ["SSO"]
                    claims += [_claim(n, Party.FIRST, modality, sentence) for n in names]
                else:
                    claims += [_claim(practice, p, modality, sentence) for p in parties]
        return claims

    def predict(self, policy: Policy, keys: Iterable[PracticeKey]) -> dict[PracticeKey, Answer]:
        claims = [c for segment in policy.segments for c in self.segment_claims(segment.text)]
        return answers_from_claims(claims, keys)


def _claim(practice: str, party: Party, modality: Modality, quote: str) -> Claim:
    return Claim(practice=practice, party=party, modality=modality, quote=quote)
