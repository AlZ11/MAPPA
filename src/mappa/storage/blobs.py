"""Content-addressed, write-once store for raw downloads (principle 2: raw is immutable).

Why content addressing (a blob's file name is the SHA-256 of its bytes):

- Identical downloads are stored once. Many apps share one developer privacy policy;
  they all point at the same blob.
- The name proves the content: re-hashing a blob shows it hasn't changed since the day
  it was fetched, which is what makes a frozen snapshot trustworthy.
- Concurrent writers can't conflict: the same bytes always produce the same file.

Why write-once: parsers are re-run against raw evidence many times. If raw files could
change, earlier results could no longer be reproduced from what is on disk. Files are
written to a temp name, flushed to disk, made read-only, then renamed into place, so a
crash never leaves a half-written file under a real hash.

Layout: ``<root>/sha256/<2 hex>/<2 hex>/<64 hex>``. Two fan-out levels keep each
directory to a few hundred entries, which filesystems and backup tools handle well.

The sidecar (content type, size, first-seen time) is the ``blobs`` table rather than a
JSON file per blob: every ``raw_blob`` column is a foreign key into it, so the database
refuses to reference a blob this store never recorded.
"""

import hashlib
import os
import re
import uuid
from pathlib import Path

from sqlalchemy import Connection, Engine
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

from mappa.models.tables import blobs
from mappa.models.types import utc_now

_SHA256_RE = re.compile(r"[0-9a-f]{64}")


class BlobError(Exception):
    """Base class for blob store problems."""


class BlobNotFoundError(BlobError):
    """No blob with this hash is on disk."""


class BlobCorruptError(BlobError):
    """A blob's bytes no longer match its name. Everything derived from it is suspect."""


class BlobStore:
    """Write-once blob files under ``root`` plus a sidecar row per blob in the database."""

    def __init__(self, root: Path, engine: Engine) -> None:
        self.root = root
        self._engine = engine

    def path_for(self, sha256: str) -> Path:
        """Where a blob lives. Rejects anything that isn't a lowercase hex SHA-256, so a
        bad value can never turn into a path outside the store."""
        if not _SHA256_RE.fullmatch(sha256):
            raise ValueError(f"not a lowercase hex SHA-256: {sha256!r}")
        return self.root / "sha256" / sha256[:2] / sha256[2:4] / sha256

    def exists(self, sha256: str) -> bool:
        return self.path_for(sha256).is_file()

    def put(self, data: bytes, content_type: str, *, conn: Connection | None = None) -> str:
        """Store ``data`` if it isn't stored already and return its SHA-256.

        Identical content is a no-op: the existing file is not touched and the sidecar
        keeps its original content type and first-seen time. Pass ``conn`` to record the
        sidecar row inside the caller's transaction (SQLite allows one writer at a time,
        so a second connection would wait on the caller's own open transaction).
        """
        if not content_type.strip():
            raise ValueError("content_type is required")
        digest = hashlib.sha256(data).hexdigest()
        path = self.path_for(digest)
        if path.exists():
            # Cheap tripwire: same hash but different size means the file was damaged.
            if path.stat().st_size != len(data):
                raise BlobCorruptError(f"{path} exists with the wrong size")
        else:
            _write_once(path, data)

        record = (
            sqlite_insert(blobs)
            .values(
                sha256=digest,
                content_type=content_type,
                size_bytes=len(data),
                first_seen_at=utc_now(),
            )
            .on_conflict_do_nothing(index_elements=["sha256"])
        )
        if conn is not None:
            conn.execute(record)
        else:
            with self._engine.begin() as own_conn:
                own_conn.execute(record)
        return digest

    def get(self, sha256: str) -> bytes:
        """Read a blob and check it still hashes to its name."""
        path = self.path_for(sha256)
        try:
            data = path.read_bytes()
        except FileNotFoundError:
            raise BlobNotFoundError(sha256) from None
        actual = hashlib.sha256(data).hexdigest()
        if actual != sha256:
            raise BlobCorruptError(f"{path} now hashes to {actual}")
        return data


def _write_once(path: Path, data: bytes) -> None:
    """Atomically create ``path`` with ``data`` as a read-only file.

    The temp file sits in the same directory so the final rename stays on one filesystem
    (and is therefore atomic). If two writers race on the same new blob, both rename
    identical bytes into place, so the result is the same either way.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        with tmp.open("xb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        tmp.chmod(0o444)
        tmp.replace(path)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise
