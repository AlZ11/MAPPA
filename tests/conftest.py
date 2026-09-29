"""Shared fixtures. Tests never use the real environment, data dir or network."""

from collections.abc import Callable, Iterator
from pathlib import Path

import pytest
from sqlalchemy import Engine

from mappa.log import remove_handlers
from mappa.storage.blobs import BlobStore
from mappa.storage.db import init_schema, make_engine

# example.org is reserved for documentation (RFC 2606): obviously fake, reaches nobody.
TEST_CONTACT = "mappa-tests@example.org"
VALID_TOML = f'data_dir = "data"\ncontact_email = "{TEST_CONTACT}"\n'

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
    engine = make_engine(tmp_path / "test.sqlite")
    init_schema(engine)
    yield engine
    engine.dispose()


@pytest.fixture
def store(tmp_path: Path, engine: Engine) -> BlobStore:
    return BlobStore(tmp_path / "blobs", engine)
