"""Scoring per-policy answers: confusion counts and the metrics built from them.

Two choices here are deliberately left to the caller rather than baked in:

- **Which answers count as positive.** Answers have four values (yes / no /
  not_mentioned / conflict); a metric needs two classes. Where ``conflict`` belongs is a
  research decision (see READER_LOG.md), so every function takes ``positive`` explicitly.
- **Which practices are evaluated.** Practices with too few positive policies give noisy
  numbers; ``evaluable_keys`` filters by a minimum count the caller chooses.

A metric whose denominator is zero is ``None`` (undefined), never 0.0 or 1.0: a practice
the method never predicts has no precision, which is different from bad precision.
"""

from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from mappa.reader.canonical import Answer, PracticeKey

AnswerTable = Mapping[tuple[str, PracticeKey], Answer]  # (policy_id, key) -> answer

# A policy "discloses" a practice if some paragraph says it is performed, which is true of
# both ``yes`` and ``conflict`` (READER_LOG.md, R0 decisions).
POSITIVE = frozenset({Answer.YES, Answer.CONFLICT})
METRICS = ("precision", "recall", "f1", "npv", "specificity", "negative_f1")


@dataclass(frozen=True)
class Confusion:
    tp: int = 0
    fp: int = 0
    fn: int = 0
    tn: int = 0

    def __add__(self, other: "Confusion") -> "Confusion":
        return Confusion(
            self.tp + other.tp, self.fp + other.fp, self.fn + other.fn, self.tn + other.tn
        )

    @property
    def precision(self) -> float | None:
        return _ratio(self.tp, self.tp + self.fp)

    @property
    def recall(self) -> float | None:
        return _ratio(self.tp, self.tp + self.fn)

    @property
    def f1(self) -> float | None:
        return _harmonic(self.precision, self.recall)

    @property
    def npv(self) -> float | None:
        """Negative predictive value: of the policies predicted negative, share right."""
        return _ratio(self.tn, self.tn + self.fn)

    @property
    def specificity(self) -> float | None:
        """Of the truly negative policies, share predicted negative."""
        return _ratio(self.tn, self.tn + self.fp)

    @property
    def negative_f1(self) -> float | None:
        """F1 of the negative class: harmonic mean of NPV and specificity."""
        return _harmonic(self.npv, self.specificity)


def confusion(
    gold: Iterable[Answer], predicted: Iterable[Answer], positive: frozenset[Answer]
) -> Confusion:
    tp = fp = fn = tn = 0
    for g, p in zip(gold, predicted, strict=True):
        gold_pos, pred_pos = g in positive, p in positive
        tp += gold_pos and pred_pos
        fp += pred_pos and not gold_pos
        fn += gold_pos and not pred_pos
        tn += not gold_pos and not pred_pos
    return Confusion(tp, fp, fn, tn)


def per_practice(
    gold: AnswerTable, predicted: AnswerTable, positive: frozenset[Answer]
) -> dict[PracticeKey, Confusion]:
    """Confusion per practice over all policies. Every gold cell needs a prediction."""
    missing = set(gold) - set(predicted)
    if missing:
        raise ValueError(
            f"{len(missing)} gold answers have no prediction, e.g. {sorted(missing)[:3]}"
        )
    keys = sorted({key for _, key in gold})
    result: dict[PracticeKey, Confusion] = {}
    for key in keys:
        cells = sorted(cell for cell in gold if cell[1] == key)
        result[key] = confusion((gold[c] for c in cells), (predicted[c] for c in cells), positive)
    return result


def micro(confusions: Iterable[Confusion]) -> Confusion:
    total = Confusion()
    for c in confusions:
        total = total + c
    return total


def macro(values: Iterable[float | None]) -> float | None:
    """Mean of the defined values; None when none is defined."""
    defined = [v for v in values if v is not None]
    return sum(defined) / len(defined) if defined else None


def evaluable_keys(
    gold: AnswerTable, positive: frozenset[Answer], *, min_positive_policies: int
) -> list[PracticeKey]:
    counts: dict[PracticeKey, int] = {}
    for (_, key), answer in gold.items():
        counts[key] = counts.get(key, 0) + (answer in positive)
    return sorted(key for key, n in counts.items() if n >= min_positive_policies)


def report(
    gold: AnswerTable,
    predicted: AnswerTable,
    *,
    evaluable: Sequence[PracticeKey],
    groups: Mapping[str, Sequence[PracticeKey]],
    name: Callable[[PracticeKey], str],
    positive: frozenset[Answer] = POSITIVE,
) -> dict[str, Any]:
    """Everything the log and EVAL report need, as plain JSON-ready values.

    - ``micro`` / ``macro``: over ``evaluable`` practices only. Micro adds up the counts
      (dominated by common practices); macro averages per-practice values (every practice
      weighs the same, so rare ones pull it around).
    - ``groups``: micro scores per named group of practices (first vs third party).
    - ``answers``: four-way exact accuracy, plus how ``no`` and ``conflict`` gold answers
      were handled, over *all* practices, because they are rare.
    - ``per_practice``: every practice, flagged evaluable or not.
    """
    per = per_practice(gold, predicted, positive)
    chosen = [key for key in evaluable if key in per]
    cells = [cell for cell in gold if cell[1] in set(chosen)]
    return {
        "n_policies": len({policy_id for policy_id, _ in gold}),
        "positive_answers": sorted(a.value for a in positive),
        "evaluable_practices": len(chosen),
        "micro": _metrics(micro(per[key] for key in chosen)),
        "macro": {metric: macro(getattr(per[key], metric) for key in chosen) for metric in METRICS},
        "groups": {
            group: _metrics(micro(per[key] for key in keys if key in set(chosen)))
            for group, keys in groups.items()
        },
        "answers": {
            "exact_accuracy": _ratio(sum(gold[c] is predicted[c] for c in cells), len(cells)),
            "no": _handled(gold, predicted, Answer.NO, positive),
            "conflict": _handled(gold, predicted, Answer.CONFLICT, positive),
        },
        "per_practice": {
            name(key): {
                **_metrics(c),
                "gold_positive": c.tp + c.fn,
                "evaluable": key in set(chosen),
            }
            for key, c in per.items()
        },
    }


def _metrics(c: Confusion) -> dict[str, Any]:
    return {"tp": c.tp, "fp": c.fp, "fn": c.fn, "tn": c.tn} | {
        metric: getattr(c, metric) for metric in METRICS
    }


def _handled(
    gold: AnswerTable, predicted: AnswerTable, answer: Answer, positive: frozenset[Answer]
) -> dict[str, Any]:
    """For gold cells with ``answer``: how many got exactly it, and how many got the right
    binary side (for ``no``: not predicted as disclosed)."""
    cells = [c for c, a in gold.items() if a is answer]
    same_side = sum((predicted[c] in positive) == (answer in positive) for c in cells)
    exact = sum(predicted[c] is answer for c in cells)
    return {
        "gold": len(cells),
        "exact": exact,
        "exact_rate": _ratio(exact, len(cells)),
        "binary_correct": same_side,
        "binary_rate": _ratio(same_side, len(cells)),
    }


def _ratio(numerator: int, denominator: int) -> float | None:
    return numerator / denominator if denominator else None


def _harmonic(a: float | None, b: float | None) -> float | None:
    if a is None or b is None:
        return None
    return 0.0 if a + b == 0 else 2 * a * b / (a + b)
