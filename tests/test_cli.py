"""CLI skeleton: every Task 01 command exists; ``init`` works; the rest fail loudly."""

import json
from collections.abc import Callable
from pathlib import Path

import pytest
from sqlalchemy import inspect
from typer.testing import CliRunner

from mappa.cli import app
from mappa.models.tables import metadata
from mappa.storage.db import make_engine

runner = CliRunner()


def _init(config_path: Path) -> tuple[int, str, str]:
    result = runner.invoke(app, ["--config", str(config_path), "init"])
    return result.exit_code, result.stdout, result.stderr


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
    ]:
        assert command in top
    assert "run" in runner.invoke(app, ["snapshot", "--help"]).stdout
    assert "coverage" in runner.invoke(app, ["report", "--help"]).stdout


def test_init_creates_the_data_dir_schema_and_log(config_path: Path) -> None:
    exit_code, stdout, stderr = _init(config_path)
    assert exit_code == 0, stderr

    data_dir = (config_path.parent / "data").resolve()
    for sub in ["blobs", "apks", "logs"]:
        assert (data_dir / sub).is_dir()
    engine = make_engine(data_dir / "mappa.sqlite")
    assert set(inspect(engine).get_table_names()) == set(metadata.tables)
    engine.dispose()
    assert str(data_dir / "mappa.sqlite") in stdout

    last_event = json.loads((data_dir / "logs" / "mappa.jsonl").read_text().splitlines()[-1])
    assert last_event["event"] == "init.done"
    assert last_event["command"] == "init"
    assert last_event["run_id"]
    assert last_event["timestamp"].endswith("Z")  # UTC


def test_init_is_idempotent(config_path: Path) -> None:
    assert _init(config_path)[0] == 0
    assert _init(config_path)[0] == 0


def test_init_refuses_to_run_without_a_contact_email(
    write_config: Callable[[str], Path],
) -> None:
    path = write_config('data_dir = "data"\n')
    exit_code, _, stderr = _init(path)
    assert exit_code == 1
    assert "contact_email is required" in stderr
    assert "Traceback" not in stderr
    assert not (path.parent / "data").exists()  # refused before touching anything


def test_init_reports_a_schema_mismatch_cleanly(config_path: Path) -> None:
    assert _init(config_path)[0] == 0
    engine = make_engine(config_path.parent / "data" / "mappa.sqlite")
    with engine.begin() as conn:
        conn.exec_driver_sql("PRAGMA user_version = 999")
    engine.dispose()

    exit_code, _, stderr = _init(config_path)
    assert exit_code == 1
    assert "schema version 999" in stderr


@pytest.mark.parametrize(
    ("args", "milestone"),
    [
        (["discover"], "M1"),
        (["fetch-metadata"], "M2"),
        (["fetch-policy"], "M3"),
        (["fetch-datasafety"], "M4"),
        (["parse-datasafety"], "M4"),
        (["fetch-apk"], "M5"),
        (["snapshot", "run", "--dev", "--limit", "5"], "M1"),
        (["report", "coverage"], "M1"),
    ],
)
def test_unbuilt_commands_fail_loudly(args: list[str], milestone: str) -> None:
    result = runner.invoke(app, [*args, "--snapshot", "dev-01"])
    assert result.exit_code == 1
    assert f"arrives in Task 01 {milestone}" in result.stderr


@pytest.mark.parametrize("bad", ["bad id", "-x", "a/b", "x" * 65])
def test_snapshot_ids_are_validated(bad: str) -> None:
    result = runner.invoke(app, ["discover", f"--snapshot={bad}"])
    assert result.exit_code == 2
    assert "Invalid value for '--snapshot'" in result.output
