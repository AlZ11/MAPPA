"""Reader settings, from ``config/reader.toml``: separate from the collector's config.toml,
so nothing here can change how the collector runs.

The AI provider, model and prices are left empty on purpose (task brief, open decision
1): commands that need them refuse to run until Alex fills them in. The API key is never
in the file, only the *name* of the environment variable that holds it.
"""

import tomllib
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, ValidationError

DEFAULT_PATH = Path("config/reader.toml")


class ReaderConfigError(Exception):
    """reader.toml is missing or invalid; the message is written for the researcher."""


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class LLMSettings(_Strict):
    provider: str | None = None
    model: str | None = None
    temperature: float = Field(default=0.0, ge=0.0)
    max_output_tokens: int = Field(default=1024, gt=0)
    api_key_env: str | None = None


class Prices(_Strict):
    """AUD per million tokens. Entered by Alex from the provider's price list."""

    input_aud_per_mtok: float | None = Field(default=None, ge=0)
    output_aud_per_mtok: float | None = Field(default=None, ge=0)


class Estimate(_Strict):
    """How --dry-run turns text lengths into token counts before any call is made."""

    chars_per_token: float = Field(default=4.0, gt=0)
    output_tokens_per_paragraph: int = Field(default=150, ge=0)


class ReaderSettings(_Strict):
    data_dir: Path
    llm: LLMSettings = LLMSettings()
    prices: Prices = Prices()
    estimate: Estimate = Estimate()

    @property
    def raw_dir(self) -> Path:
        return self.data_dir / "raw"

    @property
    def canonical_dir(self) -> Path:
        return self.data_dir / "canonical"

    @property
    def runs_dir(self) -> Path:
        return self.data_dir / "runs"

    @property
    def cache_dir(self) -> Path:
        return self.data_dir / "llm_cache"


def load_reader_settings(path: Path = DEFAULT_PATH) -> ReaderSettings:
    path = path.expanduser().resolve()
    try:
        raw = tomllib.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise ReaderConfigError(f"reader config not found: {path}") from None
    except tomllib.TOMLDecodeError as exc:
        raise ReaderConfigError(f"{path} is not valid TOML: {exc}") from None
    if any("key" in name.lower() and name != "api_key_env" for name in _all_keys(raw)):
        raise ReaderConfigError(f"{path}: API keys go in the environment, never in this file")
    values = dict(raw)
    values["data_dir"] = (path.parent / str(values.get("data_dir", "../data-public"))).resolve()
    try:
        return ReaderSettings.model_validate(values)
    except ValidationError as exc:
        raise ReaderConfigError(f"invalid reader config ({path}): {exc}") from None


def _all_keys(table: dict[str, object]) -> list[str]:
    keys: list[str] = []
    for name, value in table.items():
        keys.append(name)
        if isinstance(value, dict):
            keys += _all_keys(value)
    return keys
