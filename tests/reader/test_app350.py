"""The APP-350 loader, on a made-up zip in the same shape (no APP-350 text)."""

import zipfile
from pathlib import Path

import pytest
import yaml

from mappa.reader.canonical import Answer, Modality, Party, gold_answers
from mappa.reader.datasets.app350 import (
    App350Error,
    label_name,
    parse_label_name,
    practice_keys,
    read_canonical,
    read_zip,
    write_canonical,
)


def _document(policy_id: int, policy_type: str, segments: list[dict[str, object]]) -> str:
    return yaml.safe_dump(
        {
            "policy_id": policy_id,
            "policy_name": f"Made-up App {policy_id}",
            "policy_type": policy_type,
            "contains_synthetic": policy_type != "TEST",
            "segments": segments,
        }
    )


def _segment(idx: int, text: str, *labels: tuple[str, str]) -> dict[str, object]:
    return {
        "segment_id": idx,
        "segment_text": text,
        "annotations": [{"practice": p, "modality": m} for p, m in labels],
        "sentences": [],
    }


def _zip(tmp_path: Path, documents: dict[str, str]) -> Path:
    path = tmp_path / "made-up.zip"
    with zipfile.ZipFile(path, "w") as archive:
        for name, text in documents.items():
            archive.writestr(name, text)
    return path


@pytest.fixture
def made_up_zip(tmp_path: Path) -> Path:
    return _zip(
        tmp_path,
        {
            "APP/annotations/policy_10.yml": _document(
                10,
                "TEST",
                [
                    _segment(0, "Welcome to the made-up app."),
                    _segment(
                        1,
                        "We use your GPS position. Ad partners get your ad ID.",
                        ("Location_GPS_1stParty", "PERFORMED"),
                        ("Identifier_Ad_ID_3rdParty", "PERFORMED"),
                    ),
                    _segment(
                        2,
                        "We never sell your ad ID.",
                        ("Identifier_Ad_ID_3rdParty", "NOT_PERFORMED"),
                    ),
                ],
            ),
            "APP/annotations/policy_2.yml": _document(
                2,
                "TRAINING",
                [
                    _segment(
                        0,
                        "We do not collect your email.",
                        ("Contact_E_Mail_Address_1stParty", "NOT_PERFORMED"),
                    )
                ],
            ),
            "APP/annotations/policy_3.yml": _document(
                3,
                "VALIDATION",
                [_segment(0, "Sign in with a made-up account.", ("SSO", "PERFORMED"))],
            ),
            # Not annotations: must be skipped.
            "__MACOSX/APP/annotations/._policy_2.yml": "binary junk",
            "APP/features.yml": "Location_GPS: [gps]",
        },
    )


def test_loads_policies_and_official_split(made_up_zip: Path) -> None:
    policies, split = read_zip(made_up_zip)
    assert [p.policy_id for p in policies] == ["2", "3", "10"]  # numeric order
    assert (split.source, split.seed) == ("official", None)
    assert (split.train, split.validation, split.test) == (("2",), ("3",), ("10",))
    assert [p.contains_synthetic for p in policies] == [True, True, False]

    test_policy = policies[2]
    assert [s.idx for s in test_policy.segments] == [0, 1, 2]
    assert test_policy.segments[0].labels == ()
    answers = gold_answers(test_policy, practice_keys())
    assert answers[("Location_GPS", Party.FIRST)] is Answer.YES
    assert answers[("Identifier_Ad_ID", Party.THIRD)] is Answer.CONFLICT
    assert answers[("Location_GPS", Party.THIRD)] is Answer.NOT_MENTIONED
    no = gold_answers(policies[0], practice_keys())[("Contact_E_Mail_Address", Party.FIRST)]
    assert no is Answer.NO


def test_loading_is_deterministic_and_round_trips(made_up_zip: Path, tmp_path: Path) -> None:
    first, split = read_zip(made_up_zip)
    second, split_again = read_zip(made_up_zip)
    assert (first, split) == (second, split_again)
    out = tmp_path / "canonical"
    canonical, split_path = write_canonical(first, split, out)
    before = (canonical.read_bytes(), split_path.read_bytes())
    write_canonical(second, split_again, out)
    assert (canonical.read_bytes(), split_path.read_bytes()) == before
    assert read_canonical(out) == (first, split)


def test_sso_has_no_party(made_up_zip: Path) -> None:
    policies, _ = read_zip(made_up_zip)
    (sso,) = policies[1].segments[0].labels
    assert (sso.practice, sso.party, sso.modality) == ("SSO", Party.FIRST, Modality.PERFORMED)


@pytest.mark.parametrize(
    ("bad", "message"),
    [
        (("Location_Teleport_1stParty", "PERFORMED"), "unknown APP-350 label"),
        (("Location_GPS_1stParty", "MAYBE"), "unknown modality"),
    ],
)
def test_unknown_values_stop_the_load(tmp_path: Path, bad: tuple[str, str], message: str) -> None:
    path = _zip(
        tmp_path, {"A/annotations/policy_1.yml": _document(1, "TEST", [_segment(0, "x", bad)])}
    )
    with pytest.raises(App350Error, match=message):
        read_zip(path)


def test_unknown_split_stops_the_load(tmp_path: Path) -> None:
    path = _zip(
        tmp_path, {"A/annotations/policy_1.yml": _document(1, "HOLDOUT", [_segment(0, "x")])}
    )
    with pytest.raises(App350Error, match="HOLDOUT"):
        read_zip(path)


def test_empty_zip_is_an_error(tmp_path: Path) -> None:
    with pytest.raises(App350Error, match="no annotations"):
        read_zip(_zip(tmp_path, {"readme.txt": "nothing"}))


def test_58_label_names_round_trip() -> None:
    keys = practice_keys()
    names = [label_name(*key) for key in keys]
    assert len(keys) == len(set(keys)) == len(set(names)) == 58
    assert [parse_label_name(name) for name in names] == keys
    assert label_name("SSO", Party.THIRD) == "SSO"
