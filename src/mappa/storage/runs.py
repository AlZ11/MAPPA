"""One row per CLI command: what ran, on which snapshot, from which code, how it ended.

The JSON-lines log says the same at event level, but logs get rotated and grepped; this
table is the durable record. It also gives the coverage report its total run time.
"""

from sqlalchemy import Engine, insert, update

from mappa.models.enums import RunStatus
from mappa.models.tables import runs
from mappa.models.types import utc_now


def start_run(
    engine: Engine, *, run_id: str, command: str, snapshot_id: str | None, git_commit: str | None
) -> None:
    with engine.begin() as conn:
        conn.execute(
            insert(runs).values(
                run_id=run_id,
                command=command,
                snapshot_id=snapshot_id,
                started_at=utc_now(),
                status=RunStatus.RUNNING,
                git_commit=git_commit,
            )
        )


def finish_run(engine: Engine, run_id: str, status: RunStatus, detail: str | None = None) -> None:
    with engine.begin() as conn:
        conn.execute(
            update(runs)
            .where(runs.c.run_id == run_id)
            .values(finished_at=utc_now(), status=status, detail=detail)
        )
