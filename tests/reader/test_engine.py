"""The AI engine end to end on made-up policies with a recorded provider (no network)."""

import hashlib
import json
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

from mappa.reader.canonical import Answer, Policy
from mappa.reader.cli import app
from mappa.reader.datasets.app350 import write_canonical
from mappa.reader.datasets.split import Split
from mappa.reader.engine import EngineError, PromptParts, extract_split, provider_for
from mappa.reader.llm.client import RecordedProvider
from mappa.reader.llm.prompt import USER_TEMPLATE, PracticeDefinition
from mappa.reader.settings import LLMSettings, ReaderSettings
from tests.reader.conftest import label, policy

GPS = "We use the GPS position of your phone."
OTHER = "This made-up app has a blue icon."


def _setup(tmp_path: Path) -> tuple[ReaderSettings, list[Policy]]:
    settings = ReaderSettings(
        data_dir=tmp_path, llm=LLMSettings(provider="recorded", model="made-up")
    )
    policies = [
        policy("1", (GPS, (label("Location_GPS"),)), (OTHER, ())),
        policy("2", (OTHER, ())),
    ]
    split = Split(source="official", seed=None, train=(), validation=("1", "2"), test=())
    write_canonical(policies, split, settings.canonical_dir)
    return settings, policies


def _provider() -> RecordedProvider:
    def key(paragraph: str) -> str:
        return hashlib.sha256(USER_TEMPLATE.format(paragraph=paragraph).encode()).hexdigest()

    claim: dict[str, Any] = {
        "practice": "Location_GPS",
        "party": "first_party",
        "modality": "performed",
        "quote": GPS,
    }
    return RecordedProvider({key(GPS): {"claims": [claim]}, key(OTHER): {"claims": []}})


def _score(members: list[Policy], table: dict[Any, Answer]) -> dict[str, Any]:
    return {"yes": sum(a is Answer.YES for a in table.values())}


def test_end_to_end_and_rerun_costs_nothing(tmp_path: Path) -> None:
    settings, policies = _setup(tmp_path)
    parts = PromptParts([PracticeDefinition("Location_GPS", "made-up")], [])
    provider = _provider()
    run, metrics = extract_split(
        settings,
        parts,
        policies,
        split="validation",
        runs=2,
        max_cost=None,
        provider=provider,
        score=_score,
    )
    stats = metrics["stats"]
    # 3 paragraphs x 2 runs, but the repeated paragraph is a cache hit (same text, same key)
    assert (stats["api_calls"], stats["cache_hits"]) == (4, 2)
    assert (stats["segments_failed"], stats["made_up_answer_rate"], stats["stability"]) == (
        0,
        0.0,
        1.0,
    )
    assert metrics["run1"] == {"yes": 1}
    rows = [
        json.loads(line) for line in (run.dir / "predictions_run1.jsonl").read_text().splitlines()
    ]
    assert {"policy_id": "1", "practice": "Location_GPS_1stParty", "answer": "yes"} in rows
    assert json.loads((run.dir / "run.json").read_text())["status"] == "complete"

    _, again = extract_split(
        settings,
        parts,
        policies,
        split="validation",
        runs=2,
        max_cost=None,
        provider=provider,
        score=_score,
    )
    assert (again["stats"]["api_calls"], again["stats"]["cache_hits"]) == (0, 6)
    assert provider.calls == 4


def test_no_provider_means_nothing_is_sent(tmp_path: Path) -> None:
    with pytest.raises(EngineError, match="nothing was sent"):
        provider_for(ReaderSettings(data_dir=tmp_path))
    with pytest.raises(EngineError, match="not implemented"):
        provider_for(ReaderSettings(data_dir=tmp_path, llm=LLMSettings(provider="x", model="y")))


def test_extract_test_split_needs_final_even_for_dry_run(tmp_path: Path) -> None:
    config = tmp_path / "reader.toml"
    config.write_text('data_dir = "data"\n', encoding="utf-8")
    result = CliRunner().invoke(
        app, ["extract", "--split", "test", "--dry-run", "--config", str(config)]
    )
    assert result.exit_code == 1
    assert "--final" in result.output
