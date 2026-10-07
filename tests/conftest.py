"""Shared fixtures. Tests never use the real environment, the real data dirs or the network.

Tests that use synthetic data live in tests/synthetic/, so it is always clear which
tests rely on made-up pages. Parsers are validated against real pages saved by hand
into tests/fixtures/ (see the README there).
"""

from collections.abc import Callable, Iterator
from datetime import UTC, datetime
from pathlib import Path

import pytest
from sqlalchemy import Connection, Engine, insert

from mappa.log import remove_handlers
from mappa.models.enums import SampleMode, StorePurpose
from mappa.models.tables import snapshots
from mappa.storage.blobs import BlobStore
from mappa.storage.db import init_schema, make_engine

# example.org is reserved for documentation (RFC 2606): obviously fake, reaches nobody.
TEST_CONTACT = "mappa-tests@example.org"
VALID_TOML = f'data_dir = "data"\ncontact_email = "{TEST_CONTACT}"\n'
T0 = datetime(2026, 10, 5, 9, 0, tzinfo=UTC)
T0_TEXT = "2026-10-05T09:00:00.000000+00:00"

_MAPPA_ENV = ("MAPPA_CONFIG", "MAPPA_DATA_DIR", "MAPPA_CONTACT_EMAIL", "ANDROZOO_API_KEY")


@pytest.fixture(autouse=True)
def _isolate(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Keep the developer's own MAPPA_* variables out of tests; detach log handlers."""
    for name in _MAPPA_ENV:
        monkeypatch.delenv(name, raising=False)
    yield
    remove_handlers()


@pytest.fixture
def write_config(tmp_path: Path) -> Callable[[str], Path]:
    def _write(text: str) -> Path:
        path = tmp_path / "config" / "config.toml"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        return path

    return _write


@pytest.fixture
def config_path(write_config: Callable[[str], Path]) -> Path:
    return write_config(VALID_TOML)


@pytest.fixture
def engine(tmp_path: Path) -> Iterator[Engine]:
    """A fresh store marked as holding real data."""
    engine = make_engine(tmp_path / "test.sqlite")
    init_schema(engine, StorePurpose.REAL)
    yield engine
    engine.dispose()


@pytest.fixture
def synthetic_engine(tmp_path: Path) -> Iterator[Engine]:
    """A fresh store marked as holding synthetic data."""
    engine = make_engine(tmp_path / "synthetic.sqlite")
    init_schema(engine, StorePurpose.SYNTHETIC)
    yield engine
    engine.dispose()


@pytest.fixture
def store(tmp_path: Path, engine: Engine) -> BlobStore:
    return BlobStore(tmp_path / "blobs", engine)


def add_snapshot(
    conn: Connection, snapshot_id: str = "dev-01", sample: SampleMode = SampleMode.FULL
) -> None:
    conn.execute(
        insert(snapshots).values(
            snapshot_id=snapshot_id, sample=sample, started_at=T0, config_json={"k": 1}
        )
    )
