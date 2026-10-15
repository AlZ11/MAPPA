import pytest
from pydantic import ValidationError

from mappa.reader.canonical import Answer, Policy, Segment, combine, gold_answers
from tests.reader.conftest import FIRST, THIRD, N, P, label, policy


@pytest.mark.parametrize(
    ("modalities", "answer"),
    [
        ([P], Answer.YES),
        ([P, P], Answer.YES),
        ([N], Answer.NO),
        ([N, N], Answer.NO),
        ([], Answer.NOT_MENTIONED),
        ([P, N], Answer.CONFLICT),
    ],
)
def test_combining_gives_all_four_answers(modalities: list, answer: Answer) -> None:
    assert combine(modalities) is answer


def test_gold_answers_per_practice_and_party() -> None:
    doc = policy(
        "made-up-1",
        ("We collect your email.", (label("email"),)),
        ("We never sell your email to advertisers.", (label("email", THIRD, N),)),
        (
            "We use location. We do not use location.",
            (label("location"), label("location", FIRST, N)),
        ),
    )
    keys = [("email", FIRST), ("email", THIRD), ("location", FIRST), ("contacts", FIRST)]
    assert gold_answers(doc, keys) == {
        ("email", FIRST): Answer.YES,
        ("email", THIRD): Answer.NO,
        ("location", FIRST): Answer.CONFLICT,
        ("contacts", FIRST): Answer.NOT_MENTIONED,
    }


def test_segments_must_be_unique_and_in_order() -> None:
    with pytest.raises(ValidationError, match="unique and ascending"):
        Policy(policy_id="x", segments=(Segment(idx=1, text="a"), Segment(idx=0, text="b")))
