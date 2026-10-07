"""The CLI: store creation, the synthetic/real separation, refusals before any network."""

import json
from collections.abc import Callable
from pathlib import Path

import pytest
from sqlalchemy import inspect, select
from typer.testing import CliRunner

from mappa.cli import app
from mappa.models.tables import metadata, runs
from mappa.storage.db import make_engine
from tests.conftest import VALID_TOML

runner = CliRunner()


def _cli(config_path: Path, *args: str) -> tuple[int, str, str]:
    result = runner.invoke(app, ["--config", str(config_path), *args])
    return result.exit_code, result.stdout, result.stderr


def _runs(db: Path) -> list[tuple[str, str]]:
    engine = make_engine(db)
    with engine.connect() as conn:
        rows = [
            (r.command, str(r.status))
            for r in conn.execute(select(runs).order_by(runs.c.started_at))
        ]
    engine.dispose()
    return rows


def test_help_lists_every_task01_command() -> None:
    top = runner.invoke(app, ["--help"]).stdout
    for command in [
        "init",
        "discover",
        "fetch-metadata",
        "fetch-policy",
        "fetch-datasafety",
        "parse-datasafety",
        "fetch-apk",
        "snapshot",
        "report",
        "--synthetic",
    ]:
        assert command in top
    snapshot_help = runner.invoke(app, ["snapshot", "--help"]).stdout
    assert "run" in snapshot_help
    assert "freeze" in snapshot_help
    assert "coverage" in runner.invoke(app, ["report", "--help"]).stdout


def test_init_creates_the_real_store_schema_and_log(config_path: Path) -> None:
    exit_code, stdout, stderr = _cli(config_path, "init")
    assert exit_code == 0, stderr
    data_dir = (config_path.parent / "data").resolve()
    for sub in ["blobs", "apks", "logs"]:
        assert (data_dir / sub).is_dir()
    assert not (config_path.parent / "data-dev").exists()
    engine = make_engine(data_dir / "mappa.sqlite")
    assert set(inspect(engine).get_table_names()) == set(metadata.tables)
    engine.dispose()
    assert "real data store ready" in stdout
    event = json.loads((data_dir / "logs" / "mappa.jsonl").read_text().splitlines()[-1])
    assert (event["event"], event["command"], event["purpose"]) == ("init.done", "init", "real")
    assert event["timestamp"].endswith("Z")
    assert _runs(data_dir / "mappa.sqlite") == [("init", "ok")]


def test_synthetic_init_uses_its_own_directory(config_path: Path) -> None:
    exit_code, stdout, _ = _cli(config_path, "--synthetic", "init")
    assert exit_code == 0
    assert "synthetic data store ready" in stdout
    assert (config_path.parent / "data-dev" / "mappa.sqlite").exists()
    assert not (config_path.parent / "data").exists()


def test_init_is_idempotent(config_path: Path) -> None:
    assert _cli(config_path, "init")[0] == 0
    assert _cli(config_path, "init")[0] == 0


def test_init_refuses_to_run_without_a_contact_email(write_config: Callable[[str], Path]) -> None:
    path = write_config('data_dir = "data"\n')
    exit_code, _, stderr = _cli(path, "init")
    assert exit_code == 1
    assert "contact_email is required" in stderr
    assert "Traceback" not in stderr
    assert not (path.parent / "data").exists()  # refused before touching anything


def test_commands_before_init_say_what_to_do(config_path: Path) -> None:
    exit_code, _, stderr = _cli(config_path, "discover", "--snapshot", "dev-01")
    assert exit_code == 1
    assert "run `mappa init` first" in stderr
    exit_code, _, stderr = _cli(
        config_path, "--synthetic", "discover", "--snapshot", "synthetic-01"
    )
    assert "run `mappa --synthetic init` first" in stderr


def test_init_reports_a_schema_mismatch_cleanly(config_path: Path) -> None:
    assert _cli(config_path, "init")[0] == 0
    engine = make_engine(config_path.parent / "data" / "mappa.sqlite")
    with engine.begin() as conn:
        conn.exec_driver_sql("PRAGMA user_version = 999")
    engine.dispose()
    exit_code, _, stderr = _cli(config_path, "init")
    assert exit_code == 1
    assert "schema version 999" in stderr


@pytest.mark.parametrize(
    ("args", "message"),
    [
        (["discover", "--snapshot", "synthetic-01"], "are for synthetic data"),
        (
            ["--synthetic", "discover", "--snapshot", "2026-10-S1"],
            "need a snapshot ID starting with",
        ),
    ],
)
def test_snapshot_ids_must_match_the_kind_of_store(
    config_path: Path, args: list[str], message: str
) -> None:
    _cli(config_path, "init")
    _cli(config_path, "--synthetic", "init")
    exit_code, _, stderr = _cli(config_path, *args)
    assert exit_code == 1
    assert message in stderr


def test_live_discovery_refuses_a_placeholder_contact_before_any_request(config_path: Path) -> None:
    """No live crawl until the real contact address is in the config."""
    assert _cli(config_path, "init")[0] == 0
    exit_code, _, stderr = _cli(config_path, "discover", "--snapshot", "dev-01")
    assert exit_code == 1
    assert "placeholder address" in stderr
    db = config_path.parent / "data" / "mappa.sqlite"
    assert _runs(db)[-1] == ("discover", "failed")
    engine = make_engine(db)
    with engine.connect() as conn:
        assert conn.exec_driver_sql("SELECT count(*) FROM fetch_log").scalar_one() == 0
    engine.dispose()


def test_an_unimplemented_apk_source_is_refused(write_config: Callable[[str], Path]) -> None:
    path = write_config(VALID_TOML + 'apk_sources = ["androzoo"]\n')
    assert _cli(path, "--synthetic", "init")[0] == 0
    exit_code, _, stderr = _cli(path, "--synthetic", "fetch-apk", "--snapshot", "synthetic-01")
    assert exit_code == 1
    assert "no APK source is implemented yet" in stderr


@pytest.mark.parametrize("bad", ["bad id", "-x", "a/b", "x" * 65])
def test_snapshot_ids_are_validated(bad: str) -> None:
    result = runner.invoke(app, ["discover", f"--snapshot={bad}"])
    assert result.exit_code == 2
    assert "Invalid value for '--snapshot'" in result.output
