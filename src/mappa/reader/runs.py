"""One folder per evaluation run: ``data-public/runs/<run_id>/``.

- ``run.json``: what was run (method, split, settings, code commit, input file hash,
  UTC times) and whether it finished. Written first with ``status: started`` so an
  interrupted run is visible as such, then rewritten as ``complete``.
- ``predictions.jsonl``: one answer per (policy, practice).
- ``metrics.json``: the ``evaluate.report`` output, per policy subset.

Runs live under the gitignored ``data-public/`` because predictions sit next to policy
ids and, for the AI engine, quotes. Only aggregate numbers are copied into git.

The test split may be scored once per method (task brief R4). ``completed_test_runs``
lets the CLI refuse a second test run instead of trusting memory.
"""

import hashlib
import json
import uuid
from collections.abc import Callable, Iterable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from mappa.provenance import git_commit
from mappa.reader.canonical import Answer, PracticeKey

RUN_FILE, PREDICTIONS_FILE, METRICS_FILE = "run.json", "predictions.jsonl", "metrics.json"


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


class Run:
    def __init__(
        self,
        runs_dir: Path,
        *,
        method: str,
        split: str,
        params: dict[str, Any],
        inputs: dict[str, str],
    ) -> None:
        started = datetime.now(UTC)
        # The random suffix keeps two runs started in the same second apart.
        self.run_id = f"{started:%Y%m%dT%H%M%SZ}-{method}-{split}-{uuid.uuid4().hex[:6]}"
        self.dir = runs_dir / self.run_id
        self.dir.mkdir(parents=True, exist_ok=False)
        self.record: dict[str, Any] = {
            "run_id": self.run_id,
            "method": method,
            "split": split,
            "params": params,
            "inputs": inputs,
            "git_commit": git_commit(),
            "started_at": started.isoformat(),
            "finished_at": None,
            "status": "started",
        }
        self._write_record()

    def write_predictions(
        self, predictions: dict[tuple[str, PracticeKey], Answer], name: Callable[[PracticeKey], str]
    ) -> None:
        with (self.dir / PREDICTIONS_FILE).open("w", encoding="utf-8") as handle:
            for (policy_id, key), answer in sorted(predictions.items()):
                row = {"policy_id": policy_id, "practice": name(key), "answer": answer.value}
                handle.write(json.dumps(row) + "\n")

    def write_jsonl(self, filename: str, rows: Iterable[dict[str, Any]]) -> None:
        with (self.dir / filename).open("w", encoding="utf-8") as handle:
            for row in rows:
                handle.write(json.dumps(row) + "\n")

    def finish(self, metrics: dict[str, Any], **extra: Any) -> None:
        (self.dir / METRICS_FILE).write_text(json.dumps(metrics, indent=2) + "\n", "utf-8")
        self.record |= extra | {"finished_at": datetime.now(UTC).isoformat(), "status": "complete"}
        self._write_record()

    def _write_record(self) -> None:
        (self.dir / RUN_FILE).write_text(json.dumps(self.record, indent=2) + "\n", "utf-8")


def completed_test_runs(runs_dir: Path, method: str) -> list[str]:
    if not runs_dir.exists():
        return []
    found = []
    for path in sorted(runs_dir.glob(f"*/{RUN_FILE}")):
        record = json.loads(path.read_text(encoding="utf-8"))
        if (record.get("method"), record.get("split"), record.get("status")) == (
            method,
            "test",
            "complete",
        ):
            found.append(record["run_id"])
    return found
