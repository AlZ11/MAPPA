"""Command-line entry point (``mappa``): one command per pipeline step in Task 01.

Each step is its own command, so a long run can be resumed or redone one stage at a time,
and each stage can be tried on the dev sample before it is scaled up. ``snapshot run``
runs them all in order.

``--synthetic`` switches every command to the synthetic data directory and the synthetic
web (no network at all, snapshot IDs starting ``synthetic-``). There is no way to point a
synthetic run at the real store or the reverse: the directory, the database's recorded
purpose, the snapshot ID and every app ID are each checked.

Every command is recorded in the ``runs`` table, including failed and interrupted ones.
Known problems (bad config, a frozen snapshot, a placeholder contact address...) print a
one-line explanation instead of a traceback.
"""

import asyncio
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Annotated, NoReturn

import structlog
import typer
from sqlalchemy import Engine

from mappa.collect.apk import ApkSourceError, record_apks
from mappa.collect.context import StepContext, StepCrashed, open_fetchers
from mappa.collect.discovery import DiscoveryError, discover
from mappa.collect.inputs import InputError
from mappa.collect.labels import fetch_labels, parse_labels
from mappa.collect.live import LiveFetchRefused
from mappa.collect.metadata import fetch_metadata
from mappa.collect.policy import fetch_policies, reextract_policies
from mappa.config import ConfigError, Settings, load_config
from mappa.log import configure_logging, get_logger
from mappa.models.enums import RunStatus, SampleMode, StorePurpose
from mappa.models.ids import is_valid_snapshot_id
from mappa.models.tables import metadata
from mappa.provenance import git_commit
from mappa.reports import coverage as coverage_report
from mappa.reports.freeze import FreezeError, freeze
from mappa.storage.blobs import BlobStore
from mappa.storage.db import SCHEMA_VERSION, StoreError, check_store, init_schema, make_engine
from mappa.storage.layout import StoreLayout
from mappa.storage.runs import finish_run, start_run
from mappa.storage.snapshots import SnapshotError, check_id_matches_store

log = get_logger(__name__)

# Problems the researcher can fix; shown as one clear line, never as a traceback.
_KNOWN_ERRORS = (
    ApkSourceError,
    DiscoveryError,
    FreezeError,
    InputError,
    LiveFetchRefused,
    SnapshotError,
    StepCrashed,
    StoreError,
)

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
    synthetic: bool


@dataclass(frozen=True)
class Session:
    settings: Settings
    purpose: StorePurpose
    layout: StoreLayout
    engine: Engine
    store: BlobStore
    run_id: str

    def step(self, snapshot_id: str) -> StepContext:
        return StepContext(
            settings=self.settings,
            purpose=self.purpose,
            layout=self.layout,
            engine=self.engine,
            store=self.store,
            snapshot_id=snapshot_id,
        )


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
LimitOption = Annotated[
    int | None, typer.Option(min=1, help="Cap the number of apps (deterministic order).")
]
DevOption = Annotated[
    bool,
    typer.Option("--dev", help="Use the 20-app dev sample instead of search + seed list."),
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
    synthetic: Annotated[
        bool,
        typer.Option(
            "--synthetic",
            help="Synthetic data only: separate data dir, no network, IDs start 'synthetic-'.",
        ),
    ] = False,
) -> None:
    ctx.obj = _Options(config_path=config, verbose=verbose, synthetic=synthetic)


def _fail(message: str) -> NoReturn:
    typer.echo(f"error: {message}", err=True)
    raise typer.Exit(code=1)


def _options(ctx: typer.Context) -> _Options:
    return ctx.find_object(_Options) or _Options(config_path=None, verbose=False, synthetic=False)


@contextmanager
def _session(
    ctx: typer.Context, command: str, *, snapshot_id: str | None = None, create: bool = False
) -> Iterator[Session]:
    """Load config, open the right store, record the run, and turn known errors into
    clean messages. ``create`` is for ``init`` only."""
    options = _options(ctx)
    configure_logging(verbose=options.verbose)  # console only until the store is known
    try:
        settings = load_config(options.config_path)
    except ConfigError as exc:
        _fail(str(exc))
    purpose = StorePurpose.SYNTHETIC if options.synthetic else StorePurpose.REAL
    layout = settings.layout(purpose)
    if snapshot_id is not None:
        try:
            check_id_matches_store(snapshot_id, purpose)
        except SnapshotError as exc:
            _fail(str(exc))
    if create:
        for directory in (layout.root, layout.blobs_dir, layout.apks_dir):
            directory.mkdir(parents=True, exist_ok=True)
    elif not layout.db_path.exists():
        hint = "mappa --synthetic init" if options.synthetic else "mappa init"
        _fail(f"no {purpose.value} data store at {layout.root}: run `{hint}` first")

    run_id = uuid.uuid4().hex[:12]
    configure_logging(layout.log_file, verbose=options.verbose)
    structlog.contextvars.clear_contextvars()
    structlog.contextvars.bind_contextvars(
        run_id=run_id, command=command, snapshot_id=snapshot_id, purpose=purpose.value
    )
    engine = make_engine(layout.db_path)
    try:
        try:
            if create:
                init_schema(engine, purpose)
            else:
                check_store(engine, purpose)
        except StoreError as exc:
            log.error("store.refused", error=str(exc))
            _fail(str(exc))
        start_run(
            engine, run_id=run_id, command=command, snapshot_id=snapshot_id, git_commit=git_commit()
        )
        session = Session(
            settings, purpose, layout, engine, BlobStore(layout.blobs_dir, engine), run_id
        )
        try:
            yield session
        except typer.Exit as exc:
            finish_run(engine, run_id, RunStatus.OK if exc.exit_code == 0 else RunStatus.FAILED)
            raise
        except _KNOWN_ERRORS as exc:
            finish_run(engine, run_id, RunStatus.FAILED, str(exc))
            log.error("run.failed", error=str(exc))
            _fail(str(exc))
        except BaseException as exc:
            finish_run(engine, run_id, RunStatus.FAILED, repr(exc))
            raise
        else:
            finish_run(engine, run_id, RunStatus.OK)
    finally:
        engine.dispose()


def _echo_counts(title: str, counts: dict[str, int]) -> None:
    parts = ", ".join(f"{k} {v}" for k, v in sorted(counts.items(), key=lambda kv: (-kv[1], kv[0])))
    typer.echo(f"{title}: {parts or 'none'}")


@app.command()
def init(ctx: typer.Context) -> None:
    """Create the data directory, blob and APK stores, and the database. Safe to re-run."""
    with _session(ctx, "init", create=True) as session:
        log.info("init.done", data_dir=str(session.layout.root), schema_version=SCHEMA_VERSION)
        layout = session.layout
        typer.echo(
            f"MAPPA {session.purpose.value} data store ready "
            f"(schema v{SCHEMA_VERSION}, {len(metadata.tables)} tables)"
        )
        typer.echo(f"  database  {layout.db_path}")
        typer.echo(f"  blobs     {layout.blobs_dir}")
        typer.echo(f"  apks      {layout.apks_dir}")
        typer.echo(f"  log file  {layout.log_file}")


@app.command("discover")
def discover_command(ctx: typer.Context, snapshot: SnapshotOption, dev: DevOption = False) -> None:
    """Build the candidate list from search and the seed list (or the dev sample)."""
    with _session(ctx, "discover", snapshot_id=snapshot) as session:
        _discover(session.step(snapshot), dev=dev)


def _discover(step: StepContext, *, dev: bool) -> None:
    async def run() -> None:
        async with open_fetchers(step, browser=False) as fetchers:
            report = await discover(
                step, fetchers, sample=SampleMode.DEV if dev else SampleMode.FULL
            )
        typer.echo(
            f"discover: {report.queries_total} search terms ({report.queries_fetched} fetched, "
            f"{report.queries_reparsed} re-parsed, {report.queries_skipped} already done); "
            f"{report.unique_apps} unique apps"
        )
        _echo_counts("  apps by source", report.apps_by_source)
        if report.queries_not_ok:
            _echo_counts("  terms not fetched", dict(report.queries_not_ok))
        if report.zero_result_queries:
            typer.echo(f"  terms with zero results: {report.zero_result_queries}")

    asyncio.run(run())


@app.command("fetch-metadata")
def fetch_metadata_command(
    ctx: typer.Context, snapshot: SnapshotOption, limit: LimitOption = None
) -> None:
    """Store each candidate's AU store listing, then apply the inclusion rules."""
    with _session(ctx, "fetch-metadata", snapshot_id=snapshot) as session:
        _fetch_metadata(session.step(snapshot), limit=limit)


def _fetch_metadata(step: StepContext, *, limit: int | None) -> None:
    async def run() -> None:
        async with open_fetchers(step, browser=False) as fetchers:
            report = await fetch_metadata(step, fetchers, limit=limit)
        typer.echo(
            f"fetch-metadata: {report.candidates} candidates ({report.fetched} fetched, "
            f"{report.skipped} already done); {report.included} included"
        )
        _echo_counts("  listings", report.by_status)
        _echo_counts("  included by basis", report.by_basis)
        if report.seeds_missing:
            typer.echo(f"  WARNING seed apps not in the sample: {report.seeds_missing}")

    asyncio.run(run())


@app.command("fetch-policy")
def fetch_policy_command(
    ctx: typer.Context,
    snapshot: SnapshotOption,
    limit: LimitOption = None,
    reextract: Annotated[
        bool, typer.Option("--reextract", help="Recompute text from stored pages; no network.")
    ] = False,
) -> None:
    """Fetch each included app's privacy policy and extract its text."""
    with _session(ctx, "fetch-policy", snapshot_id=snapshot) as session:
        step = session.step(snapshot)
        if reextract:
            typer.echo(
                f"fetch-policy --reextract: {reextract_policies(step)} policies re-extracted"
            )
        else:
            _fetch_policies(step, limit=limit)


def _fetch_policies(step: StepContext, *, limit: int | None) -> None:
    async def run() -> None:
        async with open_fetchers(step, browser=True) as fetchers:
            report = await fetch_policies(step, fetchers, limit=limit)
        typer.echo(
            f"fetch-policy: {report.apps} apps; {report.urls_fetched} URLs fetched, "
            f"{report.rows_reused} rows reused from shared URLs, {report.skipped} already done"
        )
        _echo_counts("  policy status", report.by_status)

    asyncio.run(run())


@app.command("fetch-datasafety")
def fetch_datasafety_command(
    ctx: typer.Context, snapshot: SnapshotOption, limit: LimitOption = None
) -> None:
    """Save each included app's Data Safety page (parse it with parse-datasafety)."""
    with _session(ctx, "fetch-datasafety", snapshot_id=snapshot) as session:
        _fetch_labels(session.step(snapshot), limit=limit)


def _fetch_labels(step: StepContext, *, limit: int | None) -> None:
    async def run() -> None:
        browser = not step.synthetic and step.settings.datasafety_fetcher == "browser"
        async with open_fetchers(step, browser=browser) as fetchers:
            report = await fetch_labels(step, fetchers, limit=limit)
        typer.echo(
            f"fetch-datasafety: {report.apps} apps ({report.fetched} fetched, "
            f"{report.skipped} already done)"
        )
        _echo_counts("  label pages", report.by_status)

    asyncio.run(run())


@app.command("parse-datasafety")
def parse_datasafety_command(ctx: typer.Context, snapshot: SnapshotOption) -> None:
    """Parse stored Data Safety pages into label rows. No network; safe to re-run."""
    with _session(ctx, "parse-datasafety", snapshot_id=snapshot) as session:
        report = parse_labels(session.step(snapshot))
        typer.echo(
            f"parse-datasafety: {report.pages} pages -> {report.ok} labels, "
            f"{report.not_provided} not provided, {report.parse_errors} parse errors; "
            f"{report.facts} facts ({report.unmapped_facts} unmapped), {report.practices} practices"
        )


@app.command("fetch-apk")
def fetch_apk_command(
    ctx: typer.Context, snapshot: SnapshotOption, limit: LimitOption = None
) -> None:
    """Record APK status per included app (skipped: no approved APK source yet)."""
    with _session(ctx, "fetch-apk", snapshot_id=snapshot) as session:
        report = record_apks(session.step(snapshot), limit=limit)
        typer.echo(
            f"fetch-apk: {report.apps} apps; {report.recorded_skipped} recorded as skipped, "
            f"{report.already_recorded} already recorded (no approved APK source)"
        )


@snapshot_app.command("run")
def snapshot_run(
    ctx: typer.Context, snapshot: SnapshotOption, limit: LimitOption = None, dev: DevOption = False
) -> None:
    """Run every step in order, then print the coverage summary. Resumable."""
    with _session(ctx, "snapshot run", snapshot_id=snapshot) as session:
        step = session.step(snapshot)
        _discover(step, dev=dev)
        _fetch_metadata(step, limit=limit)
        _fetch_policies(step, limit=limit)
        _fetch_labels(step, limit=limit)
        parse_datasafety_report = parse_labels(step)
        typer.echo(
            f"parse-datasafety: {parse_datasafety_report.ok} labels, "
            f"{parse_datasafety_report.parse_errors} parse errors"
        )
        record_apks(step, limit=limit)
        _report(session, snapshot)


@snapshot_app.command("freeze")
def snapshot_freeze(
    ctx: typer.Context,
    snapshot: SnapshotOption,
    backup_to: Annotated[
        Path | None,
        typer.Option(help="Also copy the data to this second location and verify it there."),
    ] = None,
) -> None:
    """Verify, lock and record a finished snapshot (manifest + read-only database copy)."""
    with _session(ctx, "snapshot freeze", snapshot_id=snapshot) as session:
        result = freeze(
            session.engine,
            session.store,
            session.layout,
            snapshot,
            purpose=session.purpose,
            backup_to=backup_to,
        )
        state = "was already frozen" if result.already_frozen else "frozen"
        typer.echo(f"snapshot {snapshot} {state}; manifest: {result.manifest_path}")
        if result.backup_checked is not None:
            typer.echo(f"  backup verified: {result.backup_checked} blobs re-hashed at {backup_to}")


@report_app.command("coverage")
def report_coverage(ctx: typer.Context, snapshot: SnapshotOption) -> None:
    """Counts at each stage, top failure reasons, run time and a spot-check sample."""
    with _session(ctx, "report coverage", snapshot_id=snapshot) as session:
        _report(session, snapshot)


def _report(session: Session, snapshot_id: str) -> None:
    coverage = coverage_report.build(
        session.engine,
        snapshot_id,
        purpose=session.purpose.value,
        data_dir=session.layout.root,
    )
    md_path, csv_path = coverage_report.write(coverage, session.layout)
    typer.echo(coverage_report.render_summary(coverage))
    typer.echo(f"report: {md_path}\n        {csv_path}")
