"""The canonical format every dataset is converted into and every method is scored in.

Keyword rules, TF-IDF and the AI engine all read ``Policy`` objects and produce one
``Answer`` per practice per policy, so one piece of code scores them all against the
same gold answers.

A practice is identified by ``(practice, party)``: whether the policy's own company
collects something and whether it passes it to others are different findings, and the
evaluation reports the two parties separately.
"""

from collections.abc import Iterable
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, model_validator


class Party(StrEnum):
    FIRST = "first_party"
    THIRD = "third_party"


class Modality(StrEnum):
    PERFORMED = "performed"
    NOT_PERFORMED = "not_performed"


class Answer(StrEnum):
    """One policy's answer for one practice (task brief R1)."""

    YES = "yes"  # some paragraph says performed, none says not performed
    NO = "no"  # paragraphs only say not performed
    NOT_MENTIONED = "not_mentioned"  # no paragraph mentions the practice
    CONFLICT = "conflict"  # paragraphs say both


PracticeKey = tuple[str, Party]


class _Frozen(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class Label(_Frozen):
    practice: str = Field(min_length=1)
    party: Party
    modality: Modality

    @property
    def key(self) -> PracticeKey:
        return (self.practice, self.party)


class Segment(_Frozen):
    idx: int = Field(ge=0)
    text: str
    labels: tuple[Label, ...] = ()


class Policy(_Frozen):
    policy_id: str = Field(min_length=1)
    segments: tuple[Segment, ...]
    # APP-350's train/validation sets include policies the authors rewrote to create
    # negative examples (DATA_APP350.md section 5). Kept so results can also be reported
    # on real policies only.
    contains_synthetic: bool = False

    @model_validator(mode="after")
    def _segments_in_order(self) -> "Policy":
        indexes = [s.idx for s in self.segments]
        if indexes != sorted(set(indexes)):
            raise ValueError(f"{self.policy_id}: segment indexes must be unique and ascending")
        return self


class Claim(_Frozen):
    """What a method says one paragraph states. The AI engine also gives a quote."""

    practice: str = Field(min_length=1)
    party: Party
    modality: Modality
    quote: str = ""

    @property
    def key(self) -> PracticeKey:
        return (self.practice, self.party)


def combine(modalities: Iterable[Modality]) -> Answer:
    """The per-policy rule, used for gold labels and for every method's predictions."""
    seen = set(modalities)
    if Modality.PERFORMED in seen and Modality.NOT_PERFORMED in seen:
        return Answer.CONFLICT
    if Modality.PERFORMED in seen:
        return Answer.YES
    if Modality.NOT_PERFORMED in seen:
        return Answer.NO
    return Answer.NOT_MENTIONED


def answers_from_labels(
    labels: Iterable[Label | Claim], keys: Iterable[PracticeKey]
) -> dict[PracticeKey, Answer]:
    """One answer for every key in ``keys``; keys never mentioned are ``not_mentioned``.

    Labels outside ``keys`` are ignored here; callers decide whether that is an error.
    """
    by_key: dict[PracticeKey, list[Modality]] = {key: [] for key in keys}
    for label in labels:
        if label.key in by_key:
            by_key[label.key].append(label.modality)
    return {key: combine(modalities) for key, modalities in by_key.items()}


def gold_answers(policy: Policy, keys: Iterable[PracticeKey]) -> dict[PracticeKey, Answer]:
    return answers_from_labels((label for s in policy.segments for label in s.labels), keys)
