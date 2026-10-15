"""TF-IDF + logistic regression baseline: one classifier per practice.

Roughly what the original MAPS paper did (it used linear SVMs):

- Paragraphs are turned into TF-IDF word/bigram vectors (vocabulary from the train split
  only).
- One classifier per practice key learns "does this paragraph say the practice is
  performed?". Paragraphs labelled only ``not_performed`` count as negatives, so this
  baseline can answer ``yes`` or ``not_mentioned`` but never ``no`` or ``conflict``.
- A policy's score for a practice is its highest paragraph probability. Thresholds are
  chosen on the **validation** split from a fixed grid (0.05, 0.10 ... 0.95; ties go to
  the higher threshold, i.e. fewer positives). The test split is never seen while
  fitting or tuning. Two modes:

  - ``global``: one threshold for all practices, best micro F1;
  - ``per_practice``: each practice's own best F1, but only where validation has at
    least ``min_val_positives`` positive policies; others use the global threshold.
    With 62 validation policies, a threshold tuned on two or three positives would
    just memorise them.
- Practices with fewer than ``min_train_positives`` positive paragraphs in train get no
  classifier and always answer ``not_mentioned``; they are listed in ``skipped``.

Deterministic: fixed ``random_state``, sorted inputs.
"""

from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field

import numpy as np
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.linear_model import LogisticRegression

from mappa.reader.canonical import Answer, Modality, Policy, PracticeKey
from mappa.reader.evaluate import Confusion, confusion, micro

GRID = tuple(round(0.05 * i, 2) for i in range(1, 20))


@dataclass
class TfidfBaseline:
    min_train_positives: int = 3
    min_val_positives: int = 5
    random_state: int = 0
    global_threshold: float = 0.5
    vectorizer: TfidfVectorizer = field(init=False)
    models: dict[PracticeKey, LogisticRegression] = field(default_factory=dict, init=False)
    thresholds: dict[PracticeKey, float] = field(default_factory=dict, init=False)
    skipped: list[PracticeKey] = field(default_factory=list, init=False)

    def fit(self, train: Sequence[Policy], keys: Iterable[PracticeKey]) -> None:
        texts = [s.text for p in train for s in p.segments]
        self.vectorizer = TfidfVectorizer(ngram_range=(1, 2), min_df=2, sublinear_tf=True)
        features = self.vectorizer.fit_transform(texts)
        for key in sorted(keys):
            target = np.array(
                [
                    any(lab.key == key and lab.modality is Modality.PERFORMED for lab in s.labels)
                    for p in train
                    for s in p.segments
                ]
            )
            if target.sum() < self.min_train_positives:
                self.skipped.append(key)
                continue
            model = LogisticRegression(
                class_weight="balanced", max_iter=2000, random_state=self.random_state
            )
            model.fit(features, target)
            self.models[key] = model
            self.thresholds[key] = 0.5

    def policy_scores(self, policies: Sequence[Policy]) -> dict[tuple[str, PracticeKey], float]:
        """Highest paragraph probability per (policy, key) for the fitted keys."""
        scores: dict[tuple[str, PracticeKey], float] = {}
        for policy in policies:
            if not policy.segments:
                continue
            features = self.vectorizer.transform([s.text for s in policy.segments])
            for key, model in self.models.items():
                scores[(policy.policy_id, key)] = float(model.predict_proba(features)[:, 1].max())
        return scores

    def tune(
        self,
        validation: Sequence[Policy],
        gold: Mapping[tuple[str, PracticeKey], Answer],
        positive: frozenset[Answer],
        *,
        mode: str,
    ) -> None:
        if mode not in ("global", "per_practice"):
            raise ValueError(f"unknown threshold mode {mode!r}")
        scores = self.policy_scores(validation)
        cells = {key: sorted(c for c in scores if c[1] == key) for key in self.models}

        def counts(key: PracticeKey, threshold: float) -> Confusion:
            predicted = [
                Answer.YES if scores[c] >= threshold else Answer.NOT_MENTIONED for c in cells[key]
            ]
            return confusion((gold[c] for c in cells[key]), predicted, positive)

        def best(score: Callable[[float], float | None]) -> float | None:
            found: tuple[float, float] | None = None  # (score, threshold)
            for threshold in sorted(GRID, reverse=True):
                value = score(threshold)
                if value is not None and (found is None or value > found[0]):
                    found = (value, threshold)
            return None if found is None else found[1]

        overall = best(lambda t: micro(counts(key, t) for key in self.models).f1)
        self.global_threshold = 0.5 if overall is None else overall
        for key in self.models:
            self.thresholds[key] = self.global_threshold
            positives = sum(gold[c] in positive for c in cells[key])
            if mode == "per_practice" and positives >= self.min_val_positives:

                def key_f1(threshold: float, k: PracticeKey = key) -> float | None:
                    return counts(k, threshold).f1

                own = best(key_f1)
                if own is not None:
                    self.thresholds[key] = own

    def predict(self, policy: Policy, keys: Iterable[PracticeKey]) -> dict[PracticeKey, Answer]:
        scores = self.policy_scores([policy])
        return {
            key: Answer.YES
            if key in self.models
            and scores.get((policy.policy_id, key), 0.0) >= self.thresholds[key]
            else Answer.NOT_MENTIONED
            for key in keys
        }
