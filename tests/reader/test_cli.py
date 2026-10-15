"""The reader CLI's refusals; no real data needed."""

import json
from pathlib import Path

from typer.testing import CliRunner

from mappa.reader.cli import app
from mappa.reader.runs import completed_test_runs

runner = CliRunner()


def _config(tmp_path: Path) -> Path:
    path = tmp_path / "reader.toml"
    path.write_text('data_dir = "data"\n', encoding="utf-8")
    return path


def test_test_split_needs_final(tmp_path: Path) -> None:
    result = runner.invoke(
        app,
        ["baseline", "--method", "keyword", "--split", "test", "--config", str(_config(tmp_path))],
    )
    assert result.exit_code == 1
    assert "--final" in result.output


def test_test_split_is_used_once_per_method(tmp_path: Path) -> None:
    run = tmp_path / "data" / "runs" / "earlier"
    run.mkdir(parents=True)
    (run / "run.json").write_text(
        json.dumps(
            {"run_id": "earlier", "method": "keyword", "split": "test", "status": "complete"}
        )
    )
    assert completed_test_runs(tmp_path / "data" / "runs", "keyword") == ["earlier"]
    assert completed_test_runs(tmp_path / "data" / "runs", "tfidf") == []
    result = runner.invoke(
        app,
        [
            "baseline",
            "--method",
            "keyword",
            "--split",
            "test",
            "--final",
            "--config",
            str(_config(tmp_path)),
        ],
    )
    assert result.exit_code == 1
    assert "already has a test run" in result.output


def test_missing_canonical_data_is_one_line(tmp_path: Path) -> None:
    result = runner.invoke(
        app, ["baseline", "--method", "keyword", "--config", str(_config(tmp_path))]
    )
    assert result.exit_code == 1
    assert "load` first" in result.output
