"""Keyword baseline with made-up term lists (the real features.yml is never in tests)."""

import pytest
import yaml

from mappa.reader.baselines.keyword import FeaturesError, KeywordBaseline, load_terms
from mappa.reader.canonical import Answer
from mappa.reader.datasets.app350 import DATA_TYPES
from tests.reader.conftest import FIRST, THIRD, policy

TERMS = {"Location_GPS": ("gps",), "Contact_E_Mail_Address": ("e-mail",), "SSO": ("sign in with",)}


def _claims(text: str) -> set[tuple[str, str, str]]:
    return {
        (c.practice, c.party.value, c.modality.value)
        for c in KeywordBaseline(TERMS).segment_claims(text)
    }


def test_first_party_by_default() -> None:
    assert _claims("The app reads GPS data.") == {("Location_GPS", "first_party", "performed")}


def test_third_party_cue_and_both_parties() -> None:
    assert _claims("Advertisers may receive GPS data.") == {
        ("Location_GPS", "third_party", "performed")
    }
    assert _claims("We collect GPS data and give it to partners.") == {
        ("Location_GPS", "first_party", "performed"),
        ("Location_GPS", "third_party", "performed"),
    }


def test_negation_in_the_same_sentence_only() -> None:
    assert _claims("We don't track GPS. We store your e-mail.") == {
        ("Location_GPS", "first_party", "not_performed"),
        ("Contact_E_Mail_Address", "first_party", "performed"),
    }
    assert _claims("We will not use GPS!") == {("Location_GPS", "first_party", "not_performed")}


def test_sso_and_facebook() -> None:
    assert _claims("You can sign in with Facebook.") == {
        ("SSO", "first_party", "performed"),
        ("Facebook_SSO", "first_party", "performed"),
    }
    assert _claims("You can sign in with a made-up provider.") == {
        ("SSO", "first_party", "performed")
    }


def test_terms_match_case_insensitively_and_keep_punctuation() -> None:
    baseline = KeywordBaseline({"Contact": ("identify, contact",)})
    assert baseline.segment_claims("We IDENTIFY, CONTACT and greet you.")
    assert not baseline.segment_claims("We identify and contact you.")


def test_policy_answers() -> None:
    doc = policy("made-up", ("We use GPS.", ()), ("GPS data is never sold to advertisers.", ()))
    answers = KeywordBaseline(TERMS).predict(
        doc, [("Location_GPS", FIRST), ("Location_GPS", THIRD), ("SSO", FIRST)]
    )
    assert answers == {
        ("Location_GPS", FIRST): Answer.YES,
        ("Location_GPS", THIRD): Answer.NO,
        ("SSO", FIRST): Answer.NOT_MENTIONED,
    }


def _features(groups: list[dict[str, object]]) -> str:
    return yaml.safe_dump({"data_types": groups, "modalities": {}, "parties": {}})


def _all_groups() -> list[dict[str, object]]:
    groups: list[dict[str, object]] = [
        {"practices": [f"{t}_1stParty", f"{t}_3rdParty"], "features": [f"made-up {t}"]}
        for t in DATA_TYPES
    ]
    return [*groups, {"practices": ["SSO", "Facebook_SSO"], "features": ["Made-Up Login"]}]


def test_load_terms_reads_every_group_in_lower_case() -> None:
    terms = load_terms(_features(_all_groups()))
    assert len(terms) == len(DATA_TYPES) + 1
    assert terms["SSO"] == ("made-up login",)


def test_load_terms_refuses_missing_or_mixed_groups() -> None:
    with pytest.raises(FeaturesError, match="no terms for"):
        load_terms(_features(_all_groups()[1:]))
    mixed = [
        *_all_groups(),
        {"practices": ["Location_GPS_1stParty", "Contact_1stParty"], "features": ["x"]},
    ]
    with pytest.raises(FeaturesError, match="unexpected group"):
        load_terms(_features(mixed))
