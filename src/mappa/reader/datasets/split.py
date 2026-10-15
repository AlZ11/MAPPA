"""Train / validation / test splits.

APP-350 ships the paper's official split (188 / 62 / 100) in every annotation file
(DATA_APP350.md section 4), so that split is used as is. No seeded split is needed, and
results are comparable with the paper's test set.

Rules enforced elsewhere and kept in mind here: methods are developed and tuned on
``validation`` only; ``test`` is loaded, never looked at, until R4.
"""

from pydantic import BaseModel, ConfigDict, model_validator

SPLITS = ("train", "validation", "test")
CLI_NAMES = {"train": "train", "val": "validation", "validation": "validation", "test": "test"}


class Split(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    source: str  # "official"
    seed: int | None
    train: tuple[str, ...]
    validation: tuple[str, ...]
    test: tuple[str, ...]

    @model_validator(mode="after")
    def _disjoint(self) -> "Split":
        ids = [*self.train, *self.validation, *self.test]
        if len(ids) != len(set(ids)):
            raise ValueError("a policy appears in more than one split, or twice in one")
        return self

    def ids(self, name: str) -> tuple[str, ...]:
        ids: tuple[str, ...] = getattr(self, CLI_NAMES[name])
        return ids

    def of(self, policy_id: str) -> str:
        for name in SPLITS:
            if policy_id in getattr(self, name):
                return name
        raise KeyError(policy_id)
