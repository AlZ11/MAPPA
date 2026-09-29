"""Command-line entry point (``mappa``): one command per pipeline step in Task 01.

Each step is its own command so a long run can be resumed or redone one stage at a
time, and each stage can be tried on the 20-app dev sample before it is scaled up.

Commands from later milestones already exist but fail loudly, naming the milestone that
builds them. A placeholder that "succeeded" would look exactly like a step that ran and
found nothing: the confusion principle 4 (unknown != absent) exists to prevent.
"""

import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Annotated, NoReturn

import structlog
import typer

from mappa.config import ConfigError, Settings, load_config
from mappa.log import configure_logging, get_logger
from mappa.models.ids import is_valid_snapshot_id
from mappa.models.tables import metadata
from mappa.storage.db import SCHEMA_VERSION, SchemaVersionError, init_schema, make_engine

log = get_logger(__name__)

app = typer.Typer(
    name="mappa",
    help="MAPPA 2.0 collector: dated, hashed evidence about Australian mHealth apps.",
    no_args_is_help=True,
    add_completion=False,
    # Never print local variables on a crash: they can hold API keys or raw pages.
    pretty_exceptions_show_locals=False,
)
snapshot_app = typer.Typer(help="Whole-snapshot operations.", no_args_is_help=True)
report_app = typer.Typer(help="Reports on collected data.", no_args_is_help=True)
app.add_typer(snapshot_app, name="snapshot")
app.add_typer(report_app, name="report")


@dataclass(frozen=True)
class _Options:
    config_path: Path | None
    verbose: bool


def _check_snapshot_id(value: str) -> str:
    if not is_valid_snapshot_id(value):
        raise typer.BadParameter(
            "use 1-64 letters, digits, '.', '_' or '-', starting with a letter or digit "
            "(e.g. 2026-10-S1)"
        )
    return value


SnapshotOption = Annotated[
    str,
    typer.Option("--snapshot", help="Snapshot ID, e.g. 2026-10-S1.", callback=_check_snapshot_id),
]


@app.callback()
def main(
    ctx: typer.Context,
    config: Annotated[
        Path | None,
        typer.Option(
            help="Config file. Default: $MAPPA_CONFIG, else config/config.toml.", dir_okay=False
        ),
    ] = None,
    verbose: Annotated[
        bool, typer.Option("--verbose", "-v", help="Show debug logs on the console.")
    ] = False,
) -> None:
    ctx.obj = _Options(config_path=config, verbose=verbose)


def _fail(message: str) -> NoReturn:
    typer.echo(f"error: {message}", err=True)
    raise typer.Exit(code=1)


def _not_built_yet(milestone: str) -> NoReturn:
    _fail(f"not built yet: this command arrives in Task 01 {milestone}")


def _options(ctx: typer.Context) -> _Options:
    return ctx.find_object(_Options) or _Options(config_path=None, verbose=False)


def _load_settings(ctx: typer.Context) -> Settings:
    configure_logging(verbose=_options(ctx).verbose)  # console only until data_dir is known
    try:
        return load_config(_options(ctx).config_path)
    except ConfigError as exc:
        _fail(str(exc))


def _start_run(ctx: typer.Context, settings: Settings, command: str) -> None:
    """Log to the data dir's JSON-lines file and tag every event with this run's id."""
    configure_logging(settings.log_file, verbose=_options(ctx).verbose)
    structlog.contextvars.clear_contextvars()
    structlog.contextvars.bind_contextvars(run_id=uuid.uuid4().hex[:12], command=command)


@app.command()
def init(ctx: typer.Context) -> None:
    """Create the data directory, blob and APK stores, and the database. Safe to re-run."""
    settings = _load_settings(ctx)
    for directory in (settings.data_dir, settings.blobs_dir, settings.apks_dir):
        directory.mkdir(parents=True, exist_ok=True)
    _start_run(ctx, settings, command="init")

    engine = make_engine(settings.db_path)
    try:
        init_schema(engine)
    except SchemaVersionError as exc:
        log.error("init.schema_mismatch", error=str(exc))
        _fail(str(exc))
    finally:
        engine.dispose()

    log.info("init.done", data_dir=str(settings.data_dir), schema_version=SCHEMA_VERSION)
    typer.echo(f"MAPPA data dir ready (schema v{SCHEMA_VERSION}, {len(metadata.tables)} tables)")
    typer.echo(f"  database  {settings.db_path}")
    typer.echo(f"  blobs     {settings.blobs_dir}")
    typer.echo(f"  apks      {settings.apks_dir}")
    typer.echo(f"  log file  {settings.log_file}")


@app.command()
def discover(snapshot: SnapshotOption) -> None:
    """Build the candidate list from search, top charts and the seed file."""
    _not_built_yet("M1")


@app.command("fetch-metadata")
def fetch_metadata(snapshot: SnapshotOption) -> None:
    """Store each candidate's store listing, then apply the inclusion rules."""
    _not_built_yet("M2")


@app.command("fetch-policy")
def fetch_policy(snapshot: SnapshotOption) -> None:
    """Fetch each included app's privacy policy and extract its text."""
    _not_built_yet("M3")


@app.command("fetch-datasafety")
def fetch_datasafety(snapshot: SnapshotOption) -> None:
    """Save each included app's rendered Data Safety page."""
    _not_built_yet("M4")


@app.command("parse-datasafety")
def parse_datasafety(snapshot: SnapshotOption) -> None:
    """Parse saved Data Safety pages into label rows. No network; safe to re-run."""
    _not_built_yet("M4")


@app.command("fetch-apk")
def fetch_apk(snapshot: SnapshotOption) -> None:
    """Download each included app's APK from AndroZoo and read its version."""
    _not_built_yet("M5")


@snapshot_app.command("run")
def snapshot_run(
    snapshot: SnapshotOption,
    limit: Annotated[int | None, typer.Option(min=1, help="Cap the number of apps.")] = None,
    dev: Annotated[
        bool, typer.Option("--dev", help="Use the 20-app dev sample in config/dev_apps.csv.")
    ] = False,
) -> None:
    """Run every step in order. Resumable: finished items are skipped."""
    _not_built_yet("M1, and grows with each milestone")


@report_app.command("coverage")
def report_coverage(snapshot: SnapshotOption) -> None:
    """Counts at each stage, top failure reasons and a spot-check sample."""
    _not_built_yet("M1 (stage counts); the full report is M6")
