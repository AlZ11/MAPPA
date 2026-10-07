"""The hand-written inputs: search terms, the seed list and the dev sample.

They are validated strictly, because a typo here silently changes the sample: a
duplicated term double-counts, and a malformed package name just never matches.
"""

import csv
import io
import re
from dataclasses import dataclass
from pathlib import Path

from mappa.parse.taxonomy import norm

# Android package names: two or more dot-separated parts, each starting with a letter.
PACKAGE_NAME_RE = re.compile(r"[A-Za-z][A-Za-z0-9_]*(?:\.[A-Za-z][A-Za-z0-9_]*)+")


class InputError(ValueError):
    """An input file is malformed. The message names the file and line."""


@dataclass(frozen=True)
class Query:
    text: str
    category: str | None


def load_queries(path: Path) -> list[Query]:
    """Read queries.txt: one term per line, ``## `` starts a category, ``#`` comments."""
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except FileNotFoundError:
        raise InputError(f"search terms file not found: {path}") from None
    queries: list[Query] = []
    seen: dict[str, int] = {}
    category: str | None = None
    for number, raw in enumerate(lines, start=1):
        line = raw.strip()
        if not line:
            continue
        if line.startswith("## "):
            category = line[3:].strip()
            continue
        if line.startswith("#"):
            continue
        key = norm(line)
        if key in seen:
            raise InputError(f"{path.name}:{number}: {line!r} repeats line {seen[key]}")
        seen[key] = number
        queries.append(Query(text=line, category=category))
    if not queries:
        raise InputError(f"{path.name}: no search terms")
    return queries


def parse_app_list(content: bytes, name: str) -> list[str]:
    """App IDs from a seed/dev CSV (column ``app_id``), in file order."""
    try:
        text = content.decode("utf-8")
    except UnicodeDecodeError:
        raise InputError(f"{name}: not UTF-8 text") from None
    reader = csv.DictReader(io.StringIO(text))
    if reader.fieldnames is None or "app_id" not in reader.fieldnames:
        raise InputError(f"{name}: needs an app_id column")
    app_ids: list[str] = []
    for number, row in enumerate(reader, start=2):
        app_id = (row.get("app_id") or "").strip()
        if not PACKAGE_NAME_RE.fullmatch(app_id):
            raise InputError(f"{name}:{number}: {app_id!r} is not an Android package name")
        if app_id in app_ids:
            raise InputError(f"{name}:{number}: {app_id} is listed twice")
        app_ids.append(app_id)
    if not app_ids:
        raise InputError(f"{name}: no apps listed")
    return app_ids
