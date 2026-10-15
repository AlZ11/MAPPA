"""TF-IDF baseline on a tiny made-up corpus: plumbing and rules, not accuracy."""

from mappa.reader.baselines.tfidf import GRID, TfidfBaseline
from mappa.reader.canonical import Answer, Policy, gold_answers
from mappa.reader.evaluate import POSITIVE
from tests.reader.conftest import FIRST, N, label, policy

KEYS = [("Location_GPS", FIRST), ("Contact_E_Mail_Address", FIRST)]


def _corpus(prefix: str, n: int) -> list[Policy]:
    docs = []
    for i in range(n):
        gps = i % 2 == 0
        docs.append(
            policy(
                f"{prefix}{i}",
                ("We collect your precise GPS location to show maps.", (label("Location_GPS"),))
                if gps
                else ("Our app has a pleasant colour scheme and menus.", ()),
                (
                    "We ask for your e-mail address to send receipts.",
                    (label("Contact_E_Mail_Address"),),
                ),
                ("We do not collect your GPS location.", (label("Location_GPS", FIRST, N),)),
            )
        )
    return docs


def _gold(policies: list[Policy]) -> dict[tuple[str, tuple[str, object]], Answer]:
    return {(p.policy_id, k): a for p in policies for k, a in gold_answers(p, KEYS).items()}


def test_fits_tunes_and_predicts_only_yes_or_not_mentioned() -> None:
    train, val = _corpus("t", 12), _corpus("v", 6)
    model = TfidfBaseline(min_val_positives=2)
    model.fit(train, KEYS)
    model.tune(val, _gold(val), POSITIVE, mode="per_practice")
    assert set(model.models) == set(KEYS)
    assert all(t in GRID for t in model.thresholds.values())
    answers = [model.predict(p, KEYS) for p in val]
    assert {a for row in answers for a in row.values()} <= {Answer.YES, Answer.NOT_MENTIONED}
    assert answers[0][("Location_GPS", FIRST)] is Answer.YES


def test_rare_practices_get_no_classifier() -> None:
    model = TfidfBaseline(min_train_positives=3)
    model.fit(_corpus("t", 6), [*KEYS, ("Identifier_IMSI", FIRST)])
    assert model.skipped == [("Identifier_IMSI", FIRST)]
    assert model.predict(_corpus("v", 1)[0], [("Identifier_IMSI", FIRST)]) == {
        ("Identifier_IMSI", FIRST): Answer.NOT_MENTIONED
    }


def test_global_mode_uses_one_threshold_and_is_deterministic() -> None:
    results = []
    for _ in range(2):
        model = TfidfBaseline()
        model.fit(_corpus("t", 12), KEYS)
        val = _corpus("v", 6)
        model.tune(val, _gold(val), POSITIVE, mode="global")
        assert set(model.thresholds.values()) == {model.global_threshold}
        results.append((model.global_threshold, [model.predict(p, KEYS) for p in val]))
    assert results[0] == results[1]
