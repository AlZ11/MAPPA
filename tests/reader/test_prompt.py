import pytest

from mappa.reader.canonical import Claim, Modality, Policy, Segment
from mappa.reader.llm.prompt import (
    TEMPLATE_SHA256,
    FewShotExample,
    PracticeDefinition,
    PromptError,
    build_prompt,
    parse_claims,
    select_examples,
    template_sha256,
)
from tests.reader.conftest import FIRST, THIRD, N, label

DEFS = [PracticeDefinition("Location_GPS", "made-up definition of GPS location")]
NAMES = ["Location_GPS"]


def test_template_change_needs_a_new_version() -> None:
    assert template_sha256() == TEMPLATE_SHA256, "prompt wording changed: bump PROMPT_VERSION"


def test_few_shot_examples_must_come_from_train() -> None:
    leaked = FewShotExample("9", "validation", "made-up paragraph", ())
    with pytest.raises(PromptError, match="train split"):
        build_prompt(DEFS, [leaked], "We use GPS.")


def test_prompt_lists_definitions_and_the_paragraph() -> None:
    system, user = build_prompt(DEFS, [], "We use GPS.")
    assert "- Location_GPS: made-up definition of GPS location" in system
    assert "Return no claims" in system
    assert user.endswith("We use GPS.")


def test_replies_are_validated_even_with_a_forced_schema() -> None:
    good = {
        "claims": [
            {
                "practice": "Location_GPS",
                "party": "first_party",
                "modality": "performed",
                "quote": "We use GPS.",
            }
        ]
    }
    assert parse_claims(good, NAMES)[0].modality is Modality.PERFORMED
    with pytest.raises(PromptError, match="outside the list"):
        parse_claims({"claims": [{**good["claims"][0], "practice": "Teleport"}]}, NAMES)
    with pytest.raises(PromptError, match="schema"):
        parse_claims({"claims": [{**good["claims"][0], "confidence": 0.9}]}, NAMES)
    with pytest.raises(PromptError, match="schema"):
        parse_claims({"answer": "yes"}, NAMES)


def _policy(policy_id: str, synthetic: bool) -> Policy:
    texts = [
        ("We read the GPS position of your phone to show nearby shops.", (label("Location_GPS"),)),
        (
            "Advertising partners receive your GPS position from the app.",
            (label("Location_GPS", THIRD),),
        ),
        (
            "We do not read the GPS position of your phone at any time.",
            (label("Location_GPS", FIRST, N),),
        ),
        ("This made-up policy was last updated on a made-up date.", ()),
    ]
    segments = tuple(Segment(idx=i, text=t, labels=ls) for i, (t, ls) in enumerate(texts))
    return Policy(policy_id=policy_id, segments=segments, contains_synthetic=synthetic)


def test_select_examples_one_of_each_kind_from_real_train_policies() -> None:
    train = [_policy("1", synthetic=True), _policy("2", synthetic=False)]
    examples = select_examples(train)
    assert [e.policy_id for e in examples] == ["2"] * 4
    assert {e.split for e in examples} == {"train"}
    assert examples == select_examples(train)  # seeded: reproducible
    assert examples[3].claims == ()
    first = examples[0].claims[0]
    assert first == Claim(
        practice="Location_GPS",
        party=FIRST,
        modality=Modality.PERFORMED,
        quote=examples[0].paragraph,
    )


def test_select_examples_needs_every_kind() -> None:
    with pytest.raises(PromptError, match="no short train paragraph"):
        select_examples([_policy("1", synthetic=True)])
