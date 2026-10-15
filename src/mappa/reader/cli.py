"""Command-line entry point for the policy reader (Task 02a).

Run as ``uv run python -m mappa.reader ...``. The brief asks for ``mappa reader ...``, but
that means adding this app to ``src/mappa/cli.py``, which is collector code this session
must not change; the one-line hookup is left for Alex (READER_LOG.md).

Known problems (missing zip, bad config, unset prices...) print one line, not a traceback.
"""

from pathlib import Path
from typing import Annotated, NoReturn

import typer

from mappa.reader.datasets.app350 import (
    SOURCE_FILE,
    App350Error,
    read_zip,
    write_canonical,
)
from mappa.reader.mapping import DEFAULT_PATH as MAPPING_PATH
from mappa.reader.mapping import MappingError, load_mapping
from mappa.reader.settings import (
    DEFAULT_PATH,
    ReaderConfigError,
    ReaderSettings,
    load_reader_settings,
)

app = typer.Typer(help="Policy reader on APP-350 (Task 02a).", no_args_is_help=True)

_KNOWN_ERRORS: tuple[type[Exception], ...] = (App350Error, MappingError, ReaderConfigError)

ConfigOption = Annotated[Path, typer.Option("--config", help="Reader config file.")]


@app.callback()
def main() -> None:
    """Keeps the commands a group even while there is only one."""


def _fail(message: str) -> NoReturn:
    typer.secho(f"error: {message}", fg=typer.colors.RED, err=True)
    raise typer.Exit(1)


def _settings(config: Path) -> ReaderSettings:
    try:
        return load_reader_settings(config)
    except ReaderConfigError as exc:
        _fail(str(exc))


@app.command()
def load(config: ConfigOption = DEFAULT_PATH) -> None:
    """APP-350 zip -> canonical JSONL + official split, in data-public/canonical/."""
    settings = _settings(config)
    zip_path = settings.raw_dir / SOURCE_FILE
    if not zip_path.exists():
        _fail(f"{zip_path} not found: download APP-350 by hand and put it there")
    try:
        policies, split = read_zip(zip_path)
        load_mapping(MAPPING_PATH)
        canonical, split_path = write_canonical(policies, split, settings.canonical_dir)
    except _KNOWN_ERRORS as exc:
        _fail(str(exc))
    by_id = {p.policy_id: p for p in policies}
    typer.echo(f"wrote {canonical} and {split_path} (split: {split.source})")
    typer.echo(f"{'split':<11}{'policies':>9}{'synthetic':>10}{'segments':>9}{'labels':>8}")
    for name in ("train", "validation", "test"):
        members = [by_id[i] for i in split.ids(name)]
        segments = [s for p in members for s in p.segments]
        typer.echo(
            f"{name:<11}{len(members):>9}{sum(p.contains_synthetic for p in members):>10}"
            f"{len(segments):>9}{sum(len(s.labels) for s in segments):>8}"
        )
