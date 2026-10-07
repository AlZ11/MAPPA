"""Which code produced the data: the git commit, recorded with every snapshot and run.

A commit alone isn't enough when the working tree has uncommitted edits, because the
data would then come from code that exists nowhere else. Those runs are recorded as
``<sha>-dirty`` so they stand out in the record and in the snapshot manifest.
"""

import subprocess
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]


def git_commit(repo: Path = REPO_ROOT) -> str | None:
    """``<sha>`` or ``<sha>-dirty``; None when not running from a git checkout."""
    try:
        head = _git(repo, "rev-parse", "HEAD")
        dirty = _git(repo, "status", "--porcelain", "--untracked-files=no")
    except (OSError, subprocess.SubprocessError):
        return None
    return f"{head}-dirty" if dirty else head


def _git(repo: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(repo), *args], capture_output=True, text=True, check=True, timeout=10
    )
    return result.stdout.strip()
