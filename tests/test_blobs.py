"""The blob store is content-addressed and write-once."""

import hashlib
import stat
from datetime import timedelta
from pathlib import Path

import pytest
from sqlalchemy import Engine, insert, select

from mappa.models.tables import blobs, discovery, snapshots
from mappa.models.types import utc_now
from mappa.storage.blobs import BlobCorruptError, BlobNotFoundError, BlobStore

EMPTY_SHA256 = "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"


def _sidecar_rows(engine: Engine) -> list[dict[str, object]]:
    with engine.connect() as conn:
        return [dict(row._mapping) for row in conn.execute(select(blobs))]


def test_put_returns_the_sha256_and_uses_the_sharded_layout(
    store: BlobStore, tmp_path: Path
) -> None:
    data = b"<html>privacy policy</html>"
    digest = store.put(data, "text/html")
    assert digest == hashlib.sha256(data).hexdigest()
    expected = tmp_path / "blobs" / "sha256" / digest[:2] / digest[2:4] / digest
    assert store.path_for(digest) == expected
    assert expected.read_bytes() == data


def test_identical_content_is_stored_once_and_never_rewritten(
    store: BlobStore, engine: Engine
) -> None:
    data = b"one developer policy, shared by many apps"
    digest = store.put(data, "text/html")
    before = store.path_for(digest).stat()
    [first_row] = _sidecar_rows(engine)

    again = store.put(data, "application/octet-stream")  # a server claiming another type

    after = store.path_for(digest).stat()
    assert again == digest
    assert (after.st_ino, after.st_mtime_ns) == (before.st_ino, before.st_mtime_ns)
    assert _sidecar_rows(engine) == [first_row]  # first sighting's type and time are kept


def test_different_content_gets_a_different_blob(store: BlobStore, engine: Engine) -> None:
    first = store.put(b"policy v1", "text/html")
    second = store.put(b"policy v2", "text/html")
    assert first != second
    assert store.get(first) == b"policy v1"
    assert store.get(second) == b"policy v2"
    assert len(_sidecar_rows(engine)) == 2


def test_blob_files_are_read_only(store: BlobStore) -> None:
    path = store.path_for(store.put(b"raw page", "text/html"))
    assert stat.S_IMODE(path.stat().st_mode) == 0o444


def test_sidecar_records_content_type_size_and_first_seen_time(
    store: BlobStore, engine: Engine
) -> None:
    before = utc_now()
    digest = store.put(b"12345", "application/json")
    [row] = _sidecar_rows(engine)
    assert row["sha256"] == digest
    assert row["content_type"] == "application/json"
    assert row["size_bytes"] == 5
    first_seen = row["first_seen_at"]
    assert isinstance(first_seen, type(before))
    assert before <= first_seen <= utc_now() + timedelta(seconds=1)


def test_empty_content_is_a_valid_blob(store: BlobStore) -> None:
    assert store.put(b"", "text/plain") == EMPTY_SHA256
    assert store.get(EMPTY_SHA256) == b""


def test_get_detects_a_tampered_blob(store: BlobStore) -> None:
    digest = store.put(b"original evidence", "text/html")
    path = store.path_for(digest)
    path.chmod(0o644)
    path.write_bytes(b"edited evidence!!")
    with pytest.raises(BlobCorruptError):
        store.get(digest)


def test_put_notices_a_damaged_copy_of_the_same_blob(store: BlobStore) -> None:
    digest = store.put(b"original evidence", "text/html")
    path = store.path_for(digest)
    path.chmod(0o644)
    path.write_bytes(b"orig")  # truncated, e.g. by a bad copy from backup
    with pytest.raises(BlobCorruptError):
        store.put(b"original evidence", "text/html")


def test_get_of_an_unknown_blob_says_so(store: BlobStore) -> None:
    with pytest.raises(BlobNotFoundError):
        store.get("0" * 64)
    assert not store.exists("0" * 64)


@pytest.mark.parametrize("bad", ["../../etc/passwd", "abc", "g" * 64, "A" * 64, ""])
def test_malformed_hashes_never_become_paths(store: BlobStore, bad: str) -> None:
    with pytest.raises(ValueError, match="SHA-256"):
        store.path_for(bad)


@pytest.mark.parametrize("content_type", ["", "   "])
def test_content_type_is_required(store: BlobStore, content_type: str) -> None:
    with pytest.raises(ValueError, match="content_type"):
        store.put(b"data", content_type)


def test_no_temp_files_are_left_behind(store: BlobStore) -> None:
    for i in range(5):
        store.put(f"page {i}".encode(), "text/html")
    leftovers = [p for p in store.root.rglob("*") if p.is_file() and p.name.startswith(".")]
    assert leftovers == []


def test_sidecar_row_is_written_if_a_crash_left_only_the_file(
    store: BlobStore, engine: Engine
) -> None:
    """put() writes the file, then the row. Simulate dying in between: file, no row."""
    data = b"written, then the process died"
    digest = hashlib.sha256(data).hexdigest()
    path = store.path_for(digest)
    path.parent.mkdir(parents=True)
    path.write_bytes(data)
    assert _sidecar_rows(engine) == []

    assert store.put(data, "text/html") == digest
    assert [row["sha256"] for row in _sidecar_rows(engine)] == [digest]


def test_put_can_share_the_callers_transaction(store: BlobStore, engine: Engine) -> None:
    """A blob and the row that cites it can be committed together."""
    with engine.begin() as conn:
        conn.execute(
            insert(snapshots).values(
                snapshot_id="dev-01", sample="full", started_at=utc_now(), config_json={}
            )
        )
        digest = store.put(b'["com.example.app"]', "application/json", conn=conn)
        conn.execute(
            insert(discovery).values(
                snapshot_id="dev-01",
                app_id="com.example.app",
                source="seed_file",
                query="config/seed_apps.csv",
                rank=1,
                discovered_at=utc_now(),
                raw_blob=digest,
            )
        )
    with engine.connect() as conn:
        assert conn.execute(select(discovery.c.raw_blob)).scalar_one() == digest
