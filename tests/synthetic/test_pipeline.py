"""The whole Task 01 pipeline on SYNTHETIC data, through the CLI: every step, resuming,
reporting and freezing. No network: the synthetic web answers every request."""

from collections.abc import Callable, Iterator
from pathlib import Path

import pytest
from sqlalchemy import Engine, func, select
from typer.testing import CliRunner

from mappa.cli import app
from mappa.models.tables import apks, app_metadata, fetch_log, label_status, policy_docs
from mappa.storage.db import make_engine
from tests.conftest import TEST_CONTACT

runner = CliRunner()
SNAPSHOT = "synthetic-01"


@pytest.fixture
def config(write_config: Callable[[str], Path], tmp_path: Path) -> Path:
    queries = tmp_path / "queries.txt"
    queries.write_text("## Sleep\nsleep tracker\nsnoring\n## Diet\ncalorie counter\nmeal planner\n")
    return write_config(
        f'data_dir = "data"\ncontact_email = "{TEST_CONTACT}"\nqueries_file = "{queries}"\n'
        "target_n = 15\n[inclusion]\ntop_n = 10\nlong_tail_n = 5\n"
    )


def cli(config: Path, *args: str) -> str:
    result = runner.invoke(app, ["--config", str(config), "--synthetic", *args])
    assert result.exit_code == 0, result.stderr
    return result.stdout


@pytest.fixture
def db(config: Path) -> Iterator[Engine]:
    cli(config, "init")
    engine = make_engine(config.parent / "data-dev" / "mappa.sqlite")
    yield engine
    engine.dispose()


def counts(engine: Engine, table: object, column: str = "status") -> dict[str, int]:
    with engine.connect() as conn:
        column_ = table.c[column]  # type: ignore[attr-defined]
        rows = conn.execute(select(column_, func.count()).group_by(column_)).all()
    return {str(k): int(v) for k, v in rows}


def test_a_full_synthetic_snapshot_covers_every_case(config: Path, db: Engine) -> None:
    out = cli(config, "snapshot", "run", "--snapshot", SNAPSHOT)
    assert "Coverage for synthetic-01 (full sample, synthetic data)" in out

    listings = counts(db, app_metadata)
    assert (
        listings.get("blocked", 0) + listings.get("not_found", 0) <= 2
    )  # app13 blocks, app07 removed
    basis = counts(db, app_metadata, "inclusion_basis")
    assert basis["top_installs"] == 10
    assert basis["random_long_tail"] <= 5
    assert set(counts(db, policy_docs)) <= {"ok", "not_provided", "not_found"}
    assert set(counts(db, label_status)) <= {"ok", "not_provided", "not_found"}
    assert counts(db, apks) == {"skipped": sum(v for k, v in basis.items() if k != "None")}

    reports = config.parent / "data-dev" / "reports"
    assert (reports / f"coverage_{SNAPSHOT}.md").read_text().count("SYNTHETIC DATA") == 1
    assert (reports / f"coverage_{SNAPSHOT}.csv").exists()
    assert not (config.parent.parent / "reports").exists()  # never next to real reports


def test_a_rerun_makes_no_requests_for_finished_items(config: Path, db: Engine) -> None:
    cli(config, "snapshot", "run", "--snapshot", SNAPSHOT)
    with db.connect() as conn:
        before = conn.execute(
            select(fetch_log.c.app_id, func.count()).group_by(fetch_log.c.app_id)
        ).all()
    cli(config, "snapshot", "run", "--snapshot", SNAPSHOT)
    with db.connect() as conn:
        after = conn.execute(
            select(fetch_log.c.app_id, func.count()).group_by(fetch_log.c.app_id)
        ).all()
    grew = {app_id for (app_id, n) in after if (app_id, n) not in set(before)}
    assert grew <= {"invalid.mappa.synthetic.app13"}  # only the blocked app is retried


def test_dev_sample_and_full_sample_never_share_a_snapshot(config: Path, db: Engine) -> None:
    out = cli(config, "discover", "--snapshot", "synthetic-dev", "--dev")
    assert "20 unique apps" in out
    result = runner.invoke(
        app, ["--config", str(config), "--synthetic", "discover", "--snapshot", "synthetic-dev"]
    )
    assert result.exit_code == 1
    assert "holds the dev sample" in result.stderr


def test_search_terms_cannot_change_inside_a_snapshot(
    config: Path, db: Engine, tmp_path: Path
) -> None:
    cli(config, "discover", "--snapshot", SNAPSHOT)
    (tmp_path / "queries.txt").write_text("sleep tracker\nsnoring\ncalorie counter\n")
    result = runner.invoke(
        app, ["--config", str(config), "--synthetic", "discover", "--snapshot", SNAPSHOT]
    )
    assert result.exit_code == 1
    assert "meal planner" in result.stderr


def test_parse_is_repeatable_from_stored_pages(config: Path, db: Engine) -> None:
    cli(config, "snapshot", "run", "--snapshot", SNAPSHOT)
    with db.connect() as conn:
        first = conn.execute(select(func.count()).select_from(label_status)).scalar_one()
    out = cli(config, "parse-datasafety", "--snapshot", SNAPSHOT)
    assert "parse errors" in out
    with db.connect() as conn:
        assert conn.execute(select(func.count()).select_from(label_status)).scalar_one() == first


def test_freezing_locks_the_snapshot_and_records_a_manifest(
    config: Path, db: Engine, tmp_path: Path
) -> None:
    cli(config, "snapshot", "run", "--snapshot", SNAPSHOT)
    out = cli(
        config,
        "snapshot",
        "freeze",
        "--snapshot",
        SNAPSHOT,
        "--backup-to",
        str(tmp_path / "backup"),
    )
    assert "frozen; manifest:" in out
    assert "backup verified" in out
    frozen = config.parent / "data-dev" / "frozen" / SNAPSHOT
    assert (frozen / "snapshot_manifest.json").stat().st_mode & 0o222 == 0
    assert (frozen / "mappa.sqlite").stat().st_mode & 0o222 == 0

    result = runner.invoke(
        app, ["--config", str(config), "--synthetic", "fetch-apk", "--snapshot", SNAPSHOT]
    )
    assert result.exit_code == 1
    assert "frozen" in result.stderr
    assert "already frozen" in cli(config, "snapshot", "freeze", "--snapshot", SNAPSHOT)
