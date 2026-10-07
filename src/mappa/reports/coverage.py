"""Coverage report: what was collected for a snapshot, what wasn't, and why.

Run after every milestone and at the end of the snapshot (M6). It contains:

- counts at each stage: discovered -> listing ok -> included -> policy -> label -> APK
  (later stages count included apps only; an app with no row yet is "not attempted");
- the 10 most common failure reasons (URLs blanked, so the same error groups together);
- total run time, from the ``runs`` table;
- 10 included apps picked at random (seeded by the snapshot ID, so the same 10 each
  time) with the blobs behind them, for spot checks by hand;
- the task's acceptance criteria that can be measured from the database.

Written as ``coverage_<snapshot>.md`` (for people) and ``.csv`` (one row per candidate
app, for analysis) into the reports directory: ``reports/`` for real snapshots, inside
the synthetic data directory for synthetic ones.
"""

import csv
import hashlib
import io
import re
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from sqlalchemy import Connection, Engine, select

from mappa.models.enums import Status
from mappa.models.tables import (
    apks,
    app_metadata,
    discovery,
    discovery_queries,
    label_facts,
    label_practices,
    label_status,
    policy_docs,
    runs,
)
from mappa.storage.layout import StoreLayout
from mappa.storage.snapshots import SnapshotInfo, require_snapshot

NOT_ATTEMPTED = "not attempted"
_URL = re.compile(r"https?://\S+")
_REASON_WIDTH = 140
_SPOT_CHECKS = 10


@dataclass
class AppLine:
    """One candidate app across all stages: a row of the CSV."""

    app_id: str
    sources: str
    best_rank: int | None
    title: str | None = None
    metadata_status: str = NOT_ATTEMPTED
    genre_id: str | None = None
    installs_min: int | None = None
    installs_real: int | None = None
    free: bool | None = None
    included: bool | None = None
    inclusion_basis: str | None = None
    exclusion_reason: str | None = None
    metadata_blob: str | None = None
    policy_status: str = NOT_ATTEMPTED
    policy_format: str | None = None
    policy_words: int | None = None
    policy_language: str | None = None
    policy_review_flag: str | None = None
    policy_blob: str | None = None
    policy_text_blob: str | None = None
    label_status: str = NOT_ATTEMPTED
    label_parse_error: str | None = None
    label_facts: int = 0
    label_practices: int = 0
    label_blob: str | None = None
    apk_status: str = NOT_ATTEMPTED


@dataclass
class Coverage:
    snapshot: SnapshotInfo
    purpose: str
    data_dir: Path
    queries: Counter[str] = field(default_factory=Counter)
    zero_result_queries: int = 0
    discovered_by_source: dict[str, int] = field(default_factory=dict)
    apps: list[AppLine] = field(default_factory=list)
    failures: list[tuple[str, str, str, int]] = field(default_factory=list)
    runs: int = 0
    run_seconds: float = 0.0
    first_start: datetime | None = None
    last_finish: datetime | None = None

    @property
    def included(self) -> list[AppLine]:
        return [a for a in self.apps if a.included]

    def stage_counts(self) -> dict[str, dict[str, int]]:
        """Stage -> {status: count}. The basis of the console summary and manifest."""
        included = self.included
        return {
            "discovered": {
                "apps": len(self.apps),
                **{f"via {k}": v for k, v in self.discovered_by_source.items()},
            },
            "listing": dict(Counter(a.metadata_status for a in self.apps)),
            "included": {
                "apps": len(included),
                **dict(Counter(f"basis {a.inclusion_basis}" for a in included)),
            },
            "policy": dict(Counter(a.policy_status for a in included)),
            "label": dict(Counter(a.label_status for a in included)),
            "label parse": {
                "parse errors": sum(1 for a in included if a.label_parse_error),
                "facts": sum(a.label_facts for a in included),
                "practices": sum(a.label_practices for a in included),
            },
            "apk": dict(Counter(a.apk_status for a in included)),
        }

    def acceptance(self) -> list[tuple[str, str, bool | None]]:
        included = self.included
        fetched = [a for a in self.apps if a.metadata_status != NOT_ATTEMPTED]
        bad = (Status.FAILED.value, Status.BLOCKED.value, NOT_ATTEMPTED)

        def share(hits: int, total: int) -> tuple[str, float]:
            return (f"{hits}/{total} ({hits / total:.1%})", hits / total) if total else ("0/0", 0.0)

        listing_text, listing = share(sum(a.metadata_status == "ok" for a in fetched), len(fetched))
        policy_text, policy = share(
            sum(a.policy_status not in bad for a in included), len(included)
        )
        label_text, label = share(sum(a.label_status not in bad for a in included), len(included))
        checks: list[tuple[str, str, bool | None]] = [
            (
                "listing ok >= 95% (of candidates fetched; included apps are ok by definition)",
                listing_text,
                listing >= 0.95,
            ),
            ("policy status not failed/blocked >= 90% of included", policy_text, policy >= 0.90),
            ("label status not failed/blocked >= 90% of included", label_text, label >= 0.90),
        ]
        for line in self.apps:
            if "seed_file" in line.sources:
                complete = (
                    line.included and line.policy_status == "ok" and line.label_status == "ok"
                )
                checks.append(
                    (
                        f"seed app {line.app_id} in sample with policy + label",
                        f"policy {line.policy_status}, label {line.label_status}",
                        bool(complete),
                    )
                )
        checks.append(
            (
                "every record has snapshot_id, fetched_at and a blob hash when ok",
                "enforced by the database schema",
                True,
            )
        )
        checks.append(
            ("20 hand-checked labels match their parsed rows", "see docs/VALIDATION.md", None)
        )
        checks.append(
            ("re-running makes no requests for completed items", "covered by the test suite", None)
        )
        return checks


def build(engine: Engine, snapshot_id: str, *, purpose: str, data_dir: Path) -> Coverage:
    with engine.connect() as conn:
        info = require_snapshot(conn, snapshot_id, writable=False)
        coverage = Coverage(snapshot=info, purpose=purpose, data_dir=data_dir)
        _queries(conn, coverage)
        lines = _discovered(conn, coverage)
        _listings(conn, snapshot_id, lines)
        _policies(conn, snapshot_id, lines)
        _labels(conn, snapshot_id, lines)
        _apks(conn, snapshot_id, lines)
        coverage.apps = sorted(lines.values(), key=lambda a: a.app_id)
        coverage.failures = _failures(conn, snapshot_id, coverage)
        _runs(conn, coverage)
    return coverage


def write(coverage: Coverage, layout: StoreLayout) -> tuple[Path, Path]:
    layout.reports_dir.mkdir(parents=True, exist_ok=True)
    stem = layout.reports_dir / f"coverage_{coverage.snapshot.snapshot_id}"
    md_path, csv_path = stem.with_suffix(".md"), stem.with_suffix(".csv")
    md_path.write_text(render_markdown(coverage), encoding="utf-8")
    csv_path.write_text(render_csv(coverage), encoding="utf-8")
    return md_path, csv_path


def render_summary(coverage: Coverage) -> str:
    lines = [
        f"Coverage for {coverage.snapshot.snapshot_id} "
        f"({coverage.snapshot.sample.value} sample, {coverage.purpose} data)"
    ]
    for stage, counts in coverage.stage_counts().items():
        parts = ", ".join(
            f"{k} {v}" for k, v in sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))
        )
        lines.append(f"  {stage:<12} {parts}")
    if coverage.failures:
        lines.append("  top failure reasons:")
        lines += [
            f"    {count:>5}  {stage}/{status}: {reason}"
            for stage, status, reason, count in coverage.failures[:5]
        ]
    return "\n".join(lines)


def render_markdown(coverage: Coverage) -> str:
    info = coverage.snapshot
    out = io.StringIO()
    out.write(f"# Coverage: {info.snapshot_id}\n\n")
    if coverage.purpose == "synthetic":
        out.write("> **SYNTHETIC DATA.** Plumbing test only: nothing here describes real apps.\n\n")
    out.write(f"- Sample: {info.sample.value}\n- Started: {_ts(info.started_at)}\n")
    out.write(f"- Finished: {_ts(info.finished_at)}\n- Frozen: {_ts(info.frozen_at)}\n")
    out.write(
        f"- Code: `{info.git_commit or 'unknown'}`\n- Data directory: `{coverage.data_dir}`\n"
    )
    out.write(f"- Runs: {coverage.runs}, total {coverage.run_seconds / 60:.1f} min")
    if coverage.first_start and coverage.last_finish:
        out.write(f" (wall clock {_ts(coverage.first_start)} to {_ts(coverage.last_finish)})")
    out.write("\n\n## Counts at each stage\n\nLater stages count included apps only.\n\n")
    out.write("| Stage | Status | Apps |\n|---|---|---:|\n")
    for stage, counts in coverage.stage_counts().items():
        for status, count in sorted(counts.items(), key=lambda kv: (-kv[1], kv[0])):
            out.write(f"| {stage} | {status} | {count} |\n")
    out.write(
        f"\nSearch terms by status: {dict(coverage.queries)}; "
        f"terms with zero results: {coverage.zero_result_queries}.\n"
    )

    out.write("\n## Top failure reasons\n\n")
    if coverage.failures:
        out.write("| Stage | Status | Reason | Count |\n|---|---|---|---:|\n")
        for stage, status, reason, count in coverage.failures:
            out.write(f"| {stage} | {status} | {reason.replace('|', '/')} | {count} |\n")
    else:
        out.write("None.\n")

    out.write("\n## Acceptance criteria\n\n| Criterion | Value | Met |\n|---|---|---|\n")
    for criterion, value, met in coverage.acceptance():
        out.write(
            f"| {criterion} | {value} | {'yes' if met else 'n/a' if met is None else '**no**'} |\n"
        )

    out.write(
        f"\n## Spot checks\n\n{_SPOT_CHECKS} included apps, picked by a hash of the snapshot "
        "ID. Blob paths are relative to the data directory.\n\n"
    )
    out.write("| App | Basis | Listing | Policy | Label |\n|---|---|---|---|---|\n")
    for app in spot_checks(coverage):
        out.write(
            f"| `{app.app_id}` | {app.inclusion_basis} | {_blob_cell(app.metadata_blob)} "
            f"| {app.policy_status} {_blob_cell(app.policy_blob)} "
            f"| {app.label_status} {_blob_cell(app.label_blob)} |\n"
        )
    return out.getvalue()


def render_csv(coverage: Coverage) -> str:
    out = io.StringIO()
    columns = list(AppLine.__dataclass_fields__)
    writer = csv.DictWriter(out, fieldnames=columns, lineterminator="\n")
    writer.writeheader()
    for app in coverage.apps:
        writer.writerow({c: "" if getattr(app, c) is None else getattr(app, c) for c in columns})
    return out.getvalue()


def spot_checks(coverage: Coverage) -> list[AppLine]:
    def key(app: AppLine) -> str:
        return hashlib.sha256(f"{coverage.snapshot.snapshot_id}:{app.app_id}".encode()).hexdigest()

    return sorted(coverage.included, key=key)[:_SPOT_CHECKS]


def blob_relative_path(sha256: str) -> str:
    return f"blobs/sha256/{sha256[:2]}/{sha256[2:4]}/{sha256}"


# --- queries -----------------------------------------------------------------------------


def _queries(conn: Connection, coverage: Coverage) -> None:
    rows = conn.execute(
        select(
            discovery_queries.c.source, discovery_queries.c.status, discovery_queries.c.n_results
        ).where(discovery_queries.c.snapshot_id == coverage.snapshot.snapshot_id)
    ).all()
    for row in rows:
        if str(row.source) == "search":
            coverage.queries[str(row.status)] += 1
            coverage.zero_result_queries += int(row.n_results == 0)


def _discovered(conn: Connection, coverage: Coverage) -> dict[str, AppLine]:
    rows = conn.execute(
        select(discovery.c.app_id, discovery.c.source, discovery.c.rank).where(
            discovery.c.snapshot_id == coverage.snapshot.snapshot_id
        )
    ).all()
    sources: dict[str, set[str]] = {}
    ranks: dict[str, int] = {}
    for row in rows:
        sources.setdefault(row.app_id, set()).add(str(row.source))
        if row.rank is not None and str(row.source) == "search":
            ranks[row.app_id] = min(ranks.get(row.app_id, row.rank), row.rank)
    by_source: Counter[str] = Counter()
    for found in sources.values():
        by_source.update(found)
    coverage.discovered_by_source = dict(by_source)
    return {
        app_id: AppLine(app_id, "+".join(sorted(found)), ranks.get(app_id))
        for app_id, found in sources.items()
    }


def _listings(conn: Connection, snapshot_id: str, lines: dict[str, AppLine]) -> None:
    for row in conn.execute(select(app_metadata).where(app_metadata.c.snapshot_id == snapshot_id)):
        line = lines.get(row.app_id)
        if line is None:
            continue
        line.title, line.metadata_status = row.title, str(row.status)
        line.genre_id, line.installs_min, line.installs_real, line.free = (
            row.genre_id,
            row.installs_min,
            row.installs_real,
            row.free,
        )
        line.included, line.exclusion_reason = row.included, row.exclusion_reason
        line.inclusion_basis = None if row.inclusion_basis is None else str(row.inclusion_basis)
        line.metadata_blob = row.raw_blob


def _policies(conn: Connection, snapshot_id: str, lines: dict[str, AppLine]) -> None:
    for row in conn.execute(select(policy_docs).where(policy_docs.c.snapshot_id == snapshot_id)):
        if (line := lines.get(row.app_id)) is not None:
            line.policy_status = str(row.status)
            line.policy_format = None if row.format is None else str(row.format)
            line.policy_words, line.policy_language = row.word_count, row.language
            line.policy_review_flag, line.policy_blob, line.policy_text_blob = (
                row.review_flag,
                row.raw_blob,
                row.text_blob,
            )


def _labels(conn: Connection, snapshot_id: str, lines: dict[str, AppLine]) -> None:
    for row in conn.execute(select(label_status).where(label_status.c.snapshot_id == snapshot_id)):
        if (line := lines.get(row.app_id)) is not None:
            line.label_status, line.label_parse_error, line.label_blob = (
                str(row.status),
                row.parse_error,
                row.raw_blob,
            )
    for table, attribute in ((label_facts, "label_facts"), (label_practices, "label_practices")):
        for row in conn.execute(select(table.c.app_id).where(table.c.snapshot_id == snapshot_id)):
            if (line := lines.get(row.app_id)) is not None:
                setattr(line, attribute, getattr(line, attribute) + 1)


def _apks(conn: Connection, snapshot_id: str, lines: dict[str, AppLine]) -> None:
    for row in conn.execute(
        select(apks.c.app_id, apks.c.status).where(apks.c.snapshot_id == snapshot_id)
    ):
        if (line := lines.get(row.app_id)) is not None:
            line.apk_status = str(row.status)


def _failures(
    conn: Connection, snapshot_id: str, coverage: Coverage
) -> list[tuple[str, str, str, int]]:
    counts: Counter[tuple[str, str, str]] = Counter()
    included = {a.app_id for a in coverage.included}
    for row in conn.execute(
        select(discovery_queries).where(discovery_queries.c.snapshot_id == snapshot_id)
    ):
        if str(row.status) != "ok" or row.parse_error:
            counts[("search", str(row.status), _reason(row.parse_error or row.detail))] += 1
    for row in conn.execute(select(app_metadata).where(app_metadata.c.snapshot_id == snapshot_id)):
        if str(row.status) != "ok":
            counts[("listing", str(row.status), _reason(row.detail))] += 1
    stage_tables: list[tuple[str, Any, tuple[str, ...]]] = [
        ("policy", policy_docs, ("ok", "not_provided")),
        ("label", label_status, ("ok", "not_provided")),
        ("apk", apks, ("ok",)),
    ]
    for stage, table, fine in stage_tables:
        for row in conn.execute(select(table).where(table.c.snapshot_id == snapshot_id)):
            if row.app_id not in included:
                continue
            if str(row.status) not in fine:
                counts[(stage, str(row.status), _reason(row.detail))] += 1
            if stage == "label" and row.parse_error:
                counts[(stage, "parse error", _reason(row.parse_error))] += 1
    return [(s, st, r, n) for (s, st, r), n in counts.most_common(10)]


def _runs(conn: Connection, coverage: Coverage) -> None:
    rows = conn.execute(
        select(runs).where(runs.c.snapshot_id == coverage.snapshot.snapshot_id)
    ).all()
    coverage.runs = len(rows)
    finished = [r for r in rows if r.finished_at is not None]
    coverage.run_seconds = sum((r.finished_at - r.started_at).total_seconds() for r in finished)
    if rows:
        coverage.first_start = min(r.started_at for r in rows)
    if finished:
        coverage.last_finish = max(r.finished_at for r in finished)


def _reason(text: str | None) -> str:
    if not text:
        return "(no detail recorded)"
    cleaned = _URL.sub("<url>", " ".join(text.split()))
    return cleaned if len(cleaned) <= _REASON_WIDTH else cleaned[: _REASON_WIDTH - 3] + "..."


def _ts(value: datetime | None) -> str:
    return value.strftime("%Y-%m-%d %H:%M UTC") if value else "-"


def _blob_cell(sha256: str | None) -> str:
    return f"`{blob_relative_path(sha256)}`" if sha256 else "-"
