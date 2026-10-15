import csv
from pathlib import Path

import pytest

from mappa.reader.canonical import Party
from mappa.reader.datasets.app350 import practice_keys
from mappa.reader.mapping import DEFAULT_PATH, MappingError, load_mapping


def test_committed_mapping_covers_all_58_labels() -> None:
    mapping = load_mapping(DEFAULT_PATH)
    assert set(mapping) == set(practice_keys())
    gps = mapping[("Location_GPS", Party.THIRD)]
    assert (gps.status, gps.recipient) == ("mapped", Party.THIRD)
    assert all(m.note for m in mapping.values() if m.status == "unmapped")


def _write(tmp_path: Path, rows: list[dict[str, str]]) -> Path:
    path = tmp_path / "mapping.csv"
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    return path


def _rows() -> list[dict[str, str]]:
    with DEFAULT_PATH.open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def test_missing_row_is_refused(tmp_path: Path) -> None:
    with pytest.raises(MappingError, match="missing"):
        load_mapping(_write(tmp_path, _rows()[1:]))


def test_category_outside_taxonomy_is_refused(tmp_path: Path) -> None:
    rows = _rows()
    mapped = next(r for r in rows if r["status"] == "mapped")
    mapped["category"] = "Made-up category"
    with pytest.raises(MappingError, match="not in the taxonomy"):
        load_mapping(_write(tmp_path, rows))


def test_unmapped_needs_a_reason(tmp_path: Path) -> None:
    rows = _rows()
    unmapped = next(r for r in rows if r["status"] == "unmapped")
    unmapped["note"] = ""
    with pytest.raises(MappingError, match="reason"):
        load_mapping(_write(tmp_path, rows))
