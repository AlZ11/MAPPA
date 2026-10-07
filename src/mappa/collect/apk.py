"""M5 fallback: with no approved APK source, record every included app as ``skipped``.

The task plans for this ("if the key hasn't arrived: mark APKs skipped and carry on"),
and it is the honest record: we did not try, and here is why. Each row also keeps the
store's version string, so a later backfill can check whether the APK it finds matches
the listing we saw (``version_match``).

Sources:

- AndroZoo, the task's source, needs an API key that isn't available yet. It keeps dated
  versions, so APKs for this snapshot can be backfilled later by the snapshot date.
- F-Droid was evaluated and ruled out: it carries only open-source apps, and it builds
  them from source with tracking libraries removed or flagged, so its APKs are not the
  binaries Play Store users install. The code scanner's findings would be biased towards
  "no trackers".

``skipped`` is not final: once a source is implemented and approved, ``fetch-apk``
replaces skipped rows. Until then, listing a source in ``apk_sources`` is an error.
"""

from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

from mappa.collect.context import StepContext
from mappa.collect.metadata import included_apps
from mappa.log import get_logger
from mappa.models.enums import Status
from mappa.models.tables import apks, app_metadata
from mappa.models.types import utc_now
from mappa.storage.queries import string_map, strings
from mappa.storage.snapshots import require_snapshot

log = get_logger(__name__)

NO_SOURCE = (
    "no approved APK source: AndroZoo has no API key yet; F-Droid ruled out "
    "(open-source apps only, built differently from the Play Store versions)"
)


class ApkSourceError(RuntimeError):
    """An APK source is configured but has no implementation."""


@dataclass
class ApkReport:
    apps: int = 0
    recorded_skipped: int = 0
    already_recorded: int = 0


def record_apks(ctx: StepContext, *, limit: int | None) -> ApkReport:
    if ctx.settings.apk_sources:
        raise ApkSourceError(
            f"apk_sources lists {list(ctx.settings.apk_sources)}, but no APK source is "
            "implemented yet (AndroZoo needs an API key; see Task 01 M5). Set apk_sources = [] "
            "to record APKs as skipped."
        )
    with ctx.engine.connect() as conn:
        require_snapshot(conn, ctx.snapshot_id)
        recorded = set(
            strings(conn, select(apks.c.app_id).where(apks.c.snapshot_id == ctx.snapshot_id))
        )
        versions = string_map(
            conn,
            select(app_metadata.c.app_id, app_metadata.c.version).where(
                app_metadata.c.snapshot_id == ctx.snapshot_id
            ),
        )
    apps = included_apps(ctx, limit=limit)
    report = ApkReport(apps=len(apps))
    with ctx.engine.begin() as conn:
        for app_id in apps:
            if app_id in recorded:
                report.already_recorded += 1
                continue
            conn.execute(
                sqlite_insert(apks).values(
                    snapshot_id=ctx.snapshot_id,
                    app_id=app_id,
                    fetched_at=utc_now(),
                    status=Status.SKIPPED,
                    store_version=versions.get(app_id),
                    detail=NO_SOURCE,
                )
            )
            report.recorded_skipped += 1
    log.info("apk.done", **report.__dict__)
    return report
