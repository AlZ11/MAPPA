"""The hand-written inputs: strict parsing, and the committed files themselves."""

from pathlib import Path

import pytest

from mappa.collect.inputs import InputError, load_queries, parse_app_list

CONFIG = Path(__file__).resolve().parents[1] / "config"


def test_queries_keep_categories_and_skip_comments(tmp_path: Path) -> None:
    path = tmp_path / "queries.txt"
    path.write_text("# header\n\n## Sleep\nsleep tracker\n# note\n## Diet\ncalorie counter\n")
    queries = load_queries(path)
    assert [(q.text, q.category) for q in queries] == [
        ("sleep tracker", "Sleep"),
        ("calorie counter", "Diet"),
    ]


@pytest.mark.parametrize("repeat", ["sleep tracker", "Sleep  Tracker", "SLEEP TRACKER"])
def test_duplicate_queries_are_refused(tmp_path: Path, repeat: str) -> None:
    path = tmp_path / "queries.txt"
    path.write_text(f"sleep tracker\n{repeat}\n")
    with pytest.raises(InputError, match="repeats line 1"):
        load_queries(path)


def test_empty_or_missing_query_file_is_refused(tmp_path: Path) -> None:
    (tmp_path / "q.txt").write_text("# only comments\n")
    with pytest.raises(InputError, match="no search terms"):
        load_queries(tmp_path / "q.txt")
    with pytest.raises(InputError, match="not found"):
        load_queries(tmp_path / "missing.txt")


@pytest.mark.parametrize(
    ("csv_text", "message"),
    [
        ("name\nCalm\n", "needs an app_id column"),
        ("app_id\ncom calm android\n", "not an Android package name"),
        ("app_id\ncalm\n", "not an Android package name"),
        ("app_id\n1com.calm\n", "not an Android package name"),
        ("app_id\ncom.calm.android\ncom.calm.android\n", "listed twice"),
        ("app_id\n", "no apps listed"),
    ],
)
def test_app_lists_are_validated(csv_text: str, message: str) -> None:
    with pytest.raises(InputError, match=message):
        parse_app_list(csv_text.encode(), "seed_apps.csv")


def test_committed_search_terms_parse_cleanly() -> None:
    queries = load_queries(CONFIG / "queries.txt")
    assert len(queries) == 153
    assert all(q.category for q in queries)


def test_committed_seed_list_has_the_2021_study_apps() -> None:
    seeds = parse_app_list((CONFIG / "seed_apps.csv").read_bytes(), "seed_apps.csv")
    assert seeds == ["com.calm.android", "com.myfitnesspal.android"]


def test_committed_dev_sample_has_20_apps_including_the_seeds() -> None:
    dev = parse_app_list((CONFIG / "dev_apps.csv").read_bytes(), "dev_apps.csv")
    assert len(dev) == 20
    assert {"com.calm.android", "com.myfitnesspal.android"} <= set(dev)
