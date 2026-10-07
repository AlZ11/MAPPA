"""Data Safety vocabulary: exact (case/space-insensitive) matching, nothing dropped."""

import pytest

from mappa.models.enums import LabelPractice
from mappa.parse.taxonomy import CATEGORIES, PURPOSES, map_data_type, map_practice, split_purposes


def test_taxonomy_matches_the_project_outline() -> None:
    assert len(CATEGORIES) == 14
    assert sum(len(types) for types in CATEGORIES.values()) == 38
    assert len(PURPOSES) == 7


@pytest.mark.parametrize(
    ("raw_category", "raw_label", "expected"),
    [
        ("Personal info", "Email address", ("Personal info", "Email address")),
        ("  personal INFO ", "email   address", ("Personal info", "Email address")),
        ("Health and fitness", "Fitness info", ("Health and fitness", "Fitness info")),
        ("Personal info", "Passport number", ("Personal info", None)),  # new data type
        ("Location", "Email address", ("Location", None)),  # type under the wrong category
        ("Biometrics", "Face data", (None, None)),  # new category
    ],
)
def test_data_types_map_only_under_their_own_category(
    raw_category: str, raw_label: str, expected: tuple[str | None, str | None]
) -> None:
    assert map_data_type(raw_category, raw_label) == expected


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("App functionality", ["App functionality"]),
        ("App functionality, Analytics", ["App functionality", "Analytics"]),
        (
            "Analytics, Fraud prevention, security, and compliance, Personalization",
            ["Analytics", "Fraud prevention, security, and compliance", "Personalization"],
        ),
        ("Analytics, Brand new purpose", ["Analytics", "Brand new purpose"]),
        ("", []),
    ],
)
def test_purposes_split_without_breaking_purposes_that_contain_commas(
    raw: str, expected: list[str]
) -> None:
    assert split_purposes(raw) == expected


@pytest.mark.parametrize(
    ("statement", "expected"),
    [
        ("Data is encrypted in transit", (LabelPractice.ENCRYPTED_IN_TRANSIT, True)),
        (
            "Data isn\N{RIGHT SINGLE QUOTATION MARK}t encrypted",
            (LabelPractice.ENCRYPTED_IN_TRANSIT, False),
        ),
        ("You can request that data be deleted", (LabelPractice.DELETION_REQUEST, True)),
        ("Data can't be deleted", (LabelPractice.DELETION_REQUEST, False)),
        ("Committed to follow the Play Families Policy", (LabelPractice.FAMILIES_POLICY, True)),
        ("Independent security review", (LabelPractice.INDEPENDENT_REVIEW, True)),
        ("Something Google adds next year", None),
    ],
)
def test_security_statements_map_both_positive_and_negative(
    statement: str, expected: tuple[LabelPractice, bool] | None
) -> None:
    assert map_practice(statement) == expected
