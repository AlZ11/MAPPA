from collections.abc import Callable
from pathlib import Path

import pytest
from pydantic import ValidationError

from mappa.config import ConfigError, load_config
from tests.conftest import TEST_CONTACT, VALID_TOML

REPO_CONFIG = Path(__file__).resolve().parents[1] / "config" / "config.toml"


def test_valid_config_loads_with_the_task_defaults(config_path: Path) -> None:
    settings = load_config(config_path, env={})
    assert settings.contact_email == TEST_CONTACT
    assert (settings.country, settings.lang, settings.target_n) == ("au", "en", 1000)
    assert settings.rate_limit_per_domain_rps == 1.0
    assert settings.max_parallel_domains == 4
    assert settings.apk_sources == ("androzoo",)
    assert settings.androzoo_api_key is None


@pytest.mark.parametrize(
    "contact_line",
    ["", 'contact_email = ""', 'contact_email = "   "'],
    ids=["absent", "empty", "blank"],
)
def test_missing_contact_email_is_rejected(
    write_config: Callable[[str], Path], contact_line: str
) -> None:
    path = write_config(f'data_dir = "data"\n{contact_line}\n')
    with pytest.raises(ConfigError, match="contact_email is required"):
        load_config(path, env={})


def test_malformed_contact_email_is_rejected(write_config: Callable[[str], Path]) -> None:
    path = write_config('data_dir = "data"\ncontact_email = "research team"\n')
    with pytest.raises(ConfigError, match="not an email address"):
        load_config(path, env={})


def test_contact_email_can_come_from_the_environment(
    write_config: Callable[[str], Path],
) -> None:
    path = write_config('data_dir = "data"\n')
    settings = load_config(path, env={"MAPPA_CONTACT_EMAIL": TEST_CONTACT})
    assert settings.contact_email == TEST_CONTACT


def test_empty_environment_variables_count_as_unset(write_config: Callable[[str], Path]) -> None:
    path = write_config('data_dir = "data"\n')
    with pytest.raises(ConfigError, match="contact_email is required"):
        load_config(path, env={"MAPPA_CONTACT_EMAIL": ""})


def test_relative_data_dir_follows_the_config_file_not_the_cwd(
    config_path: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    monkeypatch.chdir(elsewhere)
    settings = load_config(config_path, env={})
    assert settings.data_dir == (config_path.parent / "data").resolve()


def test_environment_overrides_data_dir(config_path: Path, tmp_path: Path) -> None:
    settings = load_config(config_path, env={"MAPPA_DATA_DIR": str(tmp_path / "other")})
    assert settings.data_dir == (tmp_path / "other").resolve()


def test_config_path_can_come_from_the_environment(config_path: Path) -> None:
    settings = load_config(env={"MAPPA_CONFIG": str(config_path)})
    assert settings.contact_email == TEST_CONTACT


def test_typo_in_a_key_is_rejected_not_ignored(write_config: Callable[[str], Path]) -> None:
    path = write_config(VALID_TOML + "rate_limit_per_domian_rps = 5.0\n")
    with pytest.raises(ConfigError, match="rate_limit_per_domian_rps"):
        load_config(path, env={})


@pytest.mark.parametrize(
    "line",
    [
        "rate_limit_per_domain_rps = 0",
        "max_parallel_domains = 0",
        'country = "AUS"',
        "target_n = -1",
        'apk_sources = ["apkpure"]',
        "[retry]\nbackoff_initial_s = 10.0\nbackoff_max_s = 1.0",
    ],
)
def test_invalid_values_are_rejected(write_config: Callable[[str], Path], line: str) -> None:
    path = write_config(VALID_TOML + line + "\n")
    with pytest.raises(ConfigError, match="invalid configuration"):
        load_config(path, env={})


def test_androzoo_key_in_the_config_file_is_rejected(write_config: Callable[[str], Path]) -> None:
    path = write_config(VALID_TOML + 'androzoo_api_key = "abc123"\n')
    with pytest.raises(ConfigError, match="ANDROZOO_API_KEY"):
        load_config(path, env={})


def test_androzoo_key_comes_from_the_environment_and_is_never_serialised(
    config_path: Path,
) -> None:
    settings = load_config(config_path, env={"ANDROZOO_API_KEY": "s3cret-key"})
    assert settings.androzoo_api_key is not None
    assert settings.androzoo_api_key.get_secret_value() == "s3cret-key"
    # This dump is what gets stored with each snapshot: the key must never be in it.
    assert "androzoo_api_key" not in settings.model_dump()
    assert "s3cret-key" not in settings.model_dump_json()
    assert "s3cret-key" not in repr(settings)


def test_user_agent_names_the_project_and_the_contact(config_path: Path) -> None:
    user_agent = load_config(config_path, env={}).http_user_agent
    assert user_agent.startswith("MAPPA-2.0")
    assert TEST_CONTACT in user_agent


def test_settings_cannot_change_during_a_run(config_path: Path) -> None:
    settings = load_config(config_path, env={})
    with pytest.raises(ValidationError):
        settings.target_n = 5  # type: ignore[misc]


def test_missing_config_file_is_a_clear_error(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="config file not found"):
        load_config(tmp_path / "missing.toml", env={})


def test_invalid_toml_is_a_clear_error(write_config: Callable[[str], Path]) -> None:
    path = write_config("data_dir = \n")
    with pytest.raises(ConfigError, match="not valid TOML"):
        load_config(path, env={})


def test_committed_config_is_valid_once_a_contact_is_supplied() -> None:
    settings = load_config(REPO_CONFIG, env={"MAPPA_CONTACT_EMAIL": TEST_CONTACT})
    assert settings.data_dir == (REPO_CONFIG.parent.parent / "data").resolve()
