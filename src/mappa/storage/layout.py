"""Where things live inside one data directory.

Real and synthetic data each get their own directory with this same layout, so code
that writes files never needs to know which kind of data it is handling: it is given a
layout, and the layout points into the right directory.
"""

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class StoreLayout:
    root: Path
    reports_dir: Path

    @property
    def db_path(self) -> Path:
        return self.root / "mappa.sqlite"

    @property
    def blobs_dir(self) -> Path:
        return self.root / "blobs"

    @property
    def apks_dir(self) -> Path:
        return self.root / "apks"

    @property
    def logs_dir(self) -> Path:
        return self.root / "logs"

    @property
    def log_file(self) -> Path:
        return self.logs_dir / "mappa.jsonl"

    @property
    def frozen_dir(self) -> Path:
        """Read-only copies of the database and manifests of frozen snapshots."""
        return self.root / "frozen"
