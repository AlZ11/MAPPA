from mappa.reader.canonical import Answer
from mappa.reader.evaluate import POSITIVE, Confusion, macro, report
from tests.reader.conftest import FIRST, THIRD

Y, NO, NM, C = Answer.YES, Answer.NO, Answer.NOT_MENTIONED, Answer.CONFLICT
A, B = ("gps", FIRST), ("gps", THIRD)


def test_undefined_metrics_are_none_not_zero() -> None:
    nothing_predicted = Confusion(tp=0, fp=0, fn=3, tn=5)
    assert nothing_predicted.precision is None
    assert nothing_predicted.recall == 0.0
    assert nothing_predicted.f1 is None
    assert macro([None, 0.5, 1.0]) == 0.75
    assert macro([None]) is None


def test_report_counts_conflict_as_positive_and_tracks_no_answers() -> None:
    gold = {("p1", A): Y, ("p2", A): C, ("p3", A): NO, ("p1", B): NO, ("p2", B): NM}
    predicted = {("p1", A): Y, ("p2", A): NM, ("p3", A): NO, ("p1", B): Y, ("p2", B): NM}
    result = report(
        gold, predicted, evaluable=[A], groups={"first": [A], "third": [B]}, name=lambda k: k[1]
    )
    assert result["evaluable_practices"] == 1
    assert result["micro"] | {} == result["groups"]["first"]  # only A is evaluable
    assert (result["micro"]["tp"], result["micro"]["fn"], result["micro"]["tn"]) == (1, 1, 1)
    assert result["answers"]["exact_accuracy"] == 2 / 3
    assert result["answers"]["no"] == {
        "gold": 2,
        "exact": 1,
        "exact_rate": 0.5,
        "binary_correct": 1,
        "binary_rate": 0.5,
    }
    assert result["per_practice"]["third_party"]["evaluable"] is False
    assert result["positive_answers"] == sorted(a.value for a in POSITIVE)
