"""Load the collector's settings from ``config/config.toml`` plus environment variables.

Why a strict, validated settings object instead of a plain dict:

- A typo such as ``rate_limit_per_domian_rps`` must stop the run. A dict would silently
  fall back to the default, and the snapshot would not match what the researcher wrote.
- The settings are copied into the ``snapshots`` table when a snapshot starts, so every
  snapshot can be traced to the exact settings that produced it. Secrets must never be
  in that copy, so the AndroZoo key is read from the environment only and is excluded
  from serialisation and ``repr``.

Precedence (later wins): built-in defaults < config.toml < environment variables.
Relative paths in config.toml (and the default file locations) are resolved against the
file's own directory, so the same file points at the same data wherever the command is
run from. Empty environment variables count as unset.
"""

import os
import re
import tomllib
from collections.abc import Mapping
from pathlib import Path
from typing import Any, Literal, Self

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    SecretStr,
    ValidationError,
    field_validator,
    model_validator,
)

from mappa.models.enums import StorePurpose
from mappa.storage.layout import StoreLayout

DEFAULT_CONFIG_PATH = Path("config/config.toml")
ENV_CONFIG_PATH = "MAPPA_CONFIG"
ENV_DATA_DIR = "MAPPA_DATA_DIR"
ENV_CONTACT_EMAIL = "MAPPA_CONTACT_EMAIL"
ENV_ANDROZOO_KEY = "ANDROZOO_API_KEY"
ENV_CHROMIUM = "MAPPA_CHROMIUM_EXECUTABLE"

# File locations, relative to the config file's directory unless config.toml says otherwise.
_PATH_DEFAULTS = {
    "reports_dir": "../reports",
    "queries_file": "queries.txt",
    "seed_file": "seed_apps.csv",
    "dev_sample_file": "dev_apps.csv",
}

# Deliberately loose: we only need to catch "missing" and "obviously not an address".
_EMAIL_RE = re.compile(r"[^@\s]+@[^@\s]+\.[A-Za-z]{2,}")


class ConfigError(Exception):
    """The configuration is missing or invalid. The message is written for the researcher."""


class RetrySettings(BaseModel):
    """How often and how patiently a failed request is retried."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    max_attempts: int = Field(default=4, ge=1)
    backoff_initial_s: float = Field(default=2.0, gt=0)
    backoff_max_s: float = Field(default=60.0, gt=0)

    @model_validator(mode="after")
    def _max_not_below_initial(self) -> Self:
        if self.backoff_max_s < self.backoff_initial_s:
            raise ValueError("backoff_max_s must be >= backoff_initial_s")
        return self


class InclusionSettings(BaseModel):
    """Which candidates enter the sample (task M2). OPEN DECISIONS 1-2: Alex confirms
    these with the supervisor; they are settings, not code, so changing them is cheap."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    genres: tuple[str, ...] = ("HEALTH_AND_FITNESS", "MEDICAL")
    free_only: bool = True
    min_installs: int = Field(default=1000, ge=0)
    top_n: int = Field(default=800, ge=0)
    long_tail_n: int = Field(default=200, ge=0)


class Settings(BaseModel):
    """Validated, immutable settings for one run. Frozen so the copy stored with a
    snapshot is exactly what the run used."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    data_dir: Path
    synthetic_data_dir: Path
    reports_dir: Path
    queries_file: Path
    seed_file: Path
    dev_sample_file: Path
    country: str = Field(default="au", pattern=r"^[a-z]{2}$")
    lang: str = Field(default="en", pattern=r"^[a-z]{2}$")
    target_n: int = Field(default=1000, gt=0)
    user_agent: str = Field(
        default="MAPPA-2.0-research-crawler/0.1 (academic mHealth privacy study)", min_length=1
    )
    # No usable default on purpose: who we tell websites we are is not ours to guess.
    contact_email: str = Field(default="", validate_default=True)
    rate_limit_per_domain_rps: float = Field(default=1.0, gt=0)
    max_parallel_domains: int = Field(default=4, ge=1)
    page_timeout_s: float = Field(default=30.0, gt=0)
    # The task says to render Data Safety pages in the browser. The label is also
    # embedded in the plain server HTML (that is where the Node scraper reads it), so
    # "http" is the fallback if the first live check shows the browser copy lacks it.
    datasafety_fetcher: Literal["browser", "http"] = "browser"
    retry: RetrySettings = RetrySettings()
    random_seed: int = 20261005
    inclusion: InclusionSettings = InclusionSettings()
    # Only sources that have an implementation are accepted; adding one takes code and
    # supervisor approval, not just a config edit. An empty tuple means "none approved".
    apk_sources: tuple[Literal["androzoo"], ...] = ()
    chromium_executable: Path | None = None
    androzoo_api_key: SecretStr | None = Field(default=None, exclude=True, repr=False)

    @field_validator("contact_email")
    @classmethod
    def _contact_email_present(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError(
                "contact_email is required. It goes in the User-Agent of every request and "
                "must be the project/CSIRO contact address, never a personal one. Set it in "
                f"config.toml or via {ENV_CONTACT_EMAIL}."
            )
        if not _EMAIL_RE.fullmatch(value):
            raise ValueError(f"contact_email {value!r} is not an email address")
        return value

    @field_validator(
        "data_dir",
        "synthetic_data_dir",
        "reports_dir",
        "queries_file",
        "seed_file",
        "dev_sample_file",
    )
    @classmethod
    def _absolute(cls, value: Path) -> Path:
        if not value.is_absolute():
            raise ValueError("paths must be absolute once resolved (load_config does this)")
        return value

    @model_validator(mode="after")
    def _consistent(self) -> Self:
        real, synthetic = self.data_dir, self.synthetic_data_dir
        if real == synthetic or real in synthetic.parents or synthetic in real.parents:
            raise ValueError(
                "synthetic_data_dir and data_dir must be separate directories, neither "
                "inside the other: synthetic and real data never share a store"
            )
        if self.inclusion.top_n + self.inclusion.long_tail_n != self.target_n:
            raise ValueError("inclusion.top_n + inclusion.long_tail_n must equal target_n")
        return self

    @property
    def http_user_agent(self) -> str:
        """The User-Agent sent with every request: names the project and a contact."""
        return f"{self.user_agent} (+mailto:{self.contact_email})"

    def layout(self, purpose: StorePurpose) -> StoreLayout:
        """The directory layout for real or synthetic data. Synthetic reports stay inside
        the synthetic data dir, so they can never land next to the real ones."""
        if purpose is StorePurpose.SYNTHETIC:
            root = self.synthetic_data_dir
            return StoreLayout(root=root, reports_dir=root / "reports")
        return StoreLayout(root=self.data_dir, reports_dir=self.reports_dir)

    def snapshot_config(self) -> dict[str, Any]:
        """The settings as stored with a snapshot. Secrets are excluded by the model."""
        return self.model_dump(mode="json")


def load_config(path: Path | None = None, env: Mapping[str, str] | None = None) -> Settings:
    """Read config.toml, apply environment overrides and validate.

    ``env`` defaults to ``os.environ``; tests pass a dict instead of patching the process.
    Raises ``ConfigError`` with a readable message on any problem.
    """
    env = os.environ if env is None else env
    config_path = (path or _env_path(env, ENV_CONFIG_PATH) or DEFAULT_CONFIG_PATH).expanduser()
    config_path = config_path.resolve()
    try:
        raw = tomllib.loads(config_path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise ConfigError(
            f"config file not found: {config_path} "
            f"(run from the repo root, or pass --config / set {ENV_CONFIG_PATH})"
        ) from None
    except tomllib.TOMLDecodeError as exc:
        raise ConfigError(f"{config_path} is not valid TOML: {exc}") from None

    if "androzoo_api_key" in raw:
        raise ConfigError(
            f"{config_path}: androzoo_api_key must not be in config.toml, which is committed "
            f"to git. Set {ENV_ANDROZOO_KEY} in the environment instead."
        )

    base = config_path.parent
    values: dict[str, Any] = dict(raw)
    for key, default in _PATH_DEFAULTS.items():
        values[key] = values.get(key, default)
    for key in [*_PATH_DEFAULTS, "data_dir", "synthetic_data_dir"]:
        if isinstance(values.get(key), str):
            values[key] = _resolve(base, values[key])
    if data_dir := _env_path(env, ENV_DATA_DIR):
        values["data_dir"] = data_dir.expanduser().resolve()  # relative to the shell's cwd
    if "synthetic_data_dir" not in values and isinstance(values.get("data_dir"), Path):
        values["synthetic_data_dir"] = values["data_dir"].parent / "data-dev"
    if contact := env.get(ENV_CONTACT_EMAIL):
        values["contact_email"] = contact
    if chromium := _env_path(env, ENV_CHROMIUM):
        values["chromium_executable"] = chromium.expanduser().resolve()
    if api_key := env.get(ENV_ANDROZOO_KEY):
        values["androzoo_api_key"] = api_key

    try:
        return Settings.model_validate(values)
    except ValidationError as exc:
        raise ConfigError(_describe(config_path, exc)) from None


def _resolve(base: Path, value: str) -> Path:
    return (base / Path(value).expanduser()).resolve()


def _env_path(env: Mapping[str, str], name: str) -> Path | None:
    value = env.get(name)
    return Path(value) if value else None


def _describe(config_path: Path, exc: ValidationError) -> str:
    lines = [f"invalid configuration ({config_path}):"]
    for error in exc.errors():
        where = ".".join(str(part) for part in error["loc"]) or "(top level)"
        message = error["msg"].removeprefix("Value error, ")
        lines.append(f"  - {where}: {message}")
    return "\n".join(lines)
