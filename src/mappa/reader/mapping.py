"""APP-350 practice -> project taxonomy (the Data Safety categories in PROJECT_OUTLINE.md).

The reader scores in APP-350's own vocabulary; this mapping is applied afterwards, so the
same answers can later be compared with Data Safety labels (Task 02b and the consistency
engine). It lives in ``config/app350_mapping.csv`` because it is a judgement call that Alex
reviews, not code.

Statuses:

- ``mapped``: one Data Safety category and data type;
- ``category_only``: an APP-350 umbrella term ("unspecified location") that matches a
  category but no single type;
- ``unmapped``: no Data Safety equivalent, with the reason in ``note``.

Category and type names are checked against ``mappa.parse.taxonomy``, the same vocabulary
the Data Safety parser uses, so the two can't drift apart. (That module is part of the
parsers, not ``mappa.collect``; importing it changes nothing in the collector.)
"""

import csv
from dataclasses import dataclass
from pathlib import Path

from mappa.parse.taxonomy import CATEGORIES
from mappa.reader.canonical import Party, PracticeKey
from mappa.reader.datasets.app350 import label_name, practice_keys

DEFAULT_PATH = Path("config/app350_mapping.csv")
STATUSES = ("mapped", "category_only", "unmapped")


class MappingError(ValueError):
    """The mapping file is incomplete or names something outside the taxonomy."""


@dataclass(frozen=True)
class Mapping:
    app350_practice: str
    status: str
    category: str | None
    data_type: str | None
    recipient: Party | None
    note: str


def load_mapping(path: Path = DEFAULT_PATH) -> dict[PracticeKey, Mapping]:
    """Every one of the 58 APP-350 labels, exactly once, or ``MappingError``."""
    with path.open(encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle))
    by_name: dict[str, Mapping] = {}
    for number, row in enumerate(rows, start=2):
        mapping = _row(row, f"{path.name}:{number}")
        if mapping.app350_practice in by_name:
            raise MappingError(f"{path.name}:{number}: {mapping.app350_practice} listed twice")
        by_name[mapping.app350_practice] = mapping
    expected = {
        label_name(practice, party): (practice, party) for practice, party in practice_keys()
    }
    missing, extra = sorted(set(expected) - set(by_name)), sorted(set(by_name) - set(expected))
    if missing or extra:
        raise MappingError(f"{path.name}: missing {missing}, unknown {extra}")
    return {expected[name]: mapping for name, mapping in by_name.items()}


def _row(row: dict[str, str], where: str) -> Mapping:
    status = row["status"].strip()
    category = row["category"].strip() or None
    data_type = row["data_type"].strip() or None
    recipient = row["recipient"].strip() or None
    if status not in STATUSES:
        raise MappingError(f"{where}: status {status!r} not in {STATUSES}")
    if status == "unmapped":
        if category or data_type:
            raise MappingError(f"{where}: unmapped rows have no category or data_type")
        if not row["note"].strip():
            raise MappingError(f"{where}: unmapped rows need a reason in note")
    else:
        if category not in CATEGORIES:
            raise MappingError(f"{where}: category {category!r} is not in the taxonomy")
        if status == "mapped" and data_type not in CATEGORIES[category]:
            raise MappingError(f"{where}: {data_type!r} is not a {category} data type")
        if status == "category_only" and data_type:
            raise MappingError(f"{where}: category_only rows have no data_type")
    return Mapping(
        app350_practice=row["app350_practice"].strip(),
        status=status,
        category=category,
        data_type=data_type,
        recipient=Party(recipient) if recipient else None,
        note=row["note"].strip(),
    )
