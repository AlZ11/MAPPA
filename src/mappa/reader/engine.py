"""The AI engine end to end: prompts -> cached calls -> verified claims -> answers -> scores.

``PROVIDERS`` maps a provider name (``[llm] provider`` in reader.toml) to a factory.
It is **empty on purpose**: which provider and model to use, and whether APP-350 text
may be sent to an external service at all, are open decisions for Alex and the
supervisor (task brief, open decisions 1-3). Until one is added, ``extract`` without
``--dry-run`` refuses before anything is sent. Tests register a ``RecordedProvider``.
"""

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from mappa.reader.aggregate import answers_from_claims
from mappa.reader.canonical import Answer, Policy, PracticeKey
from mappa.reader.datasets.app350 import (
    CANONICAL_FILE,
    SOURCE_FILE,
    label_name,
    practice_definitions,
    practice_keys,
)
from mappa.reader.extract import Extractor, SegmentResult
from mappa.reader.llm.client import (
    BudgetGuard,
    CachedClient,
    CostEstimate,
    Provider,
    ResponseCache,
    estimate_cost,
    estimate_tokens,
)
from mappa.reader.llm.prompt import (
    PROMPT_VERSION,
    FewShotExample,
    PracticeDefinition,
    build_prompt,
    select_examples,
)
from mappa.reader.runs import Run, file_sha256
from mappa.reader.settings import ReaderSettings

PROVIDERS: dict[str, Callable[[ReaderSettings], Provider]] = {}
METHOD = "llm"


class EngineError(RuntimeError):
    """The engine can't run as configured; the message says what to set."""


@dataclass(frozen=True)
class PromptParts:
    definitions: list[PracticeDefinition]
    examples: list[FewShotExample]


def prompt_parts(settings: ReaderSettings, train: list[Policy]) -> PromptParts:
    definitions = practice_definitions(settings.raw_dir / SOURCE_FILE)
    return PromptParts(
        [PracticeDefinition(name, text) for name, text in definitions.items()],
        select_examples(train),
    )


def dry_run(
    settings: ReaderSettings, parts: PromptParts, policies: list[Policy], runs: int
) -> tuple[CostEstimate, int]:
    """(estimate for every distinct paragraph x run, tokens of the shared system prompt).

    Identical paragraphs share a cache key (the key holds the paragraph's hash), so a
    paragraph repeated across policies costs one call per run, not one per copy.
    """
    distinct = dict.fromkeys(segment.text for policy in policies for segment in policy.segments)
    prompts = [build_prompt(parts.definitions, parts.examples, text) for text in distinct] * runs
    system_tokens = estimate_tokens(
        build_prompt(parts.definitions, parts.examples, "")[0], settings.estimate
    )
    return estimate_cost(prompts, settings.prices, settings.estimate), system_tokens


def extract_split(
    settings: ReaderSettings,
    parts: PromptParts,
    policies: list[Policy],
    *,
    split: str,
    runs: int,
    max_cost: float | None,
    provider: Provider,
    score: Callable[[list[Policy], dict[tuple[str, PracticeKey], Answer]], dict[str, Any]],
) -> tuple[Run, dict[str, Any]]:
    client = CachedClient(
        provider=provider,
        cache=ResponseCache(settings.cache_dir),
        budget=BudgetGuard(settings.prices, max_cost),
        estimate=settings.estimate,
        prompt_version=PROMPT_VERSION,
    )
    extractor = Extractor(client, settings.llm, parts.definitions, parts.examples)
    run = Run(
        settings.runs_dir,
        method=METHOD,
        split=split,
        params={
            "provider": provider.name,
            "model": settings.llm.model,
            "prompt_version": PROMPT_VERSION,
            "temperature": settings.llm.temperature,
            "max_output_tokens": settings.llm.max_output_tokens,
            "runs": runs,
            "max_cost_aud": max_cost,
            "few_shot": [(e.policy_id, e.split) for e in parts.examples],
        },
        inputs={"canonical_sha256": file_sha256(settings.canonical_dir / CANONICAL_FILE)},
    )
    keys = practice_keys()
    results: list[SegmentResult] = []
    per_run: dict[int, dict[tuple[str, PracticeKey], Answer]] = {}
    for number in range(1, runs + 1):
        table: dict[tuple[str, PracticeKey], Answer] = {}
        for policy in policies:
            segments = extractor.policy(policy, number)
            results += segments
            claims = [v for s in segments if s.status == "ok" for v in s.verified]
            for key, answer in answers_from_claims(claims, keys).items():
                table[(policy.policy_id, key)] = answer
        per_run[number] = table
        run.write_jsonl(
            f"predictions_run{number}.jsonl",
            (
                {"policy_id": pid, "practice": label_name(*key), "answer": answer.value}
                for (pid, key), answer in sorted(table.items())
            ),
        )
    run.write_jsonl("segments.jsonl", (_segment_row(r) for r in results))
    stats = _stats(results, per_run, client)
    metrics = {"stats": stats} | {f"run{n}": score(policies, t) for n, t in per_run.items()}
    run.finish(metrics, stats=stats)
    return run, metrics


def _stats(
    results: list[SegmentResult],
    per_run: dict[int, dict[tuple[str, PracticeKey], Answer]],
    client: CachedClient,
) -> dict[str, Any]:
    verified = sum(len(r.verified) for r in results)
    rejected = sum(len(r.rejected) for r in results)
    cells = list(per_run[1]) if per_run else []
    stable = sum(len({per_run[n][c] for n in per_run}) == 1 for c in cells)
    return {
        "segments": len(results),
        "segments_failed": sum(r.status == "failed" for r in results),
        "claims_verified": verified,
        "claims_rejected_quote_not_found": rejected,
        "made_up_answer_rate": rejected / (verified + rejected) if verified + rejected else None,
        "api_calls": client.api_calls,
        "cache_hits": client.cache_hits,
        "spent_aud": client.budget.spent_aud,
        "stability": stable / len(cells) if len(per_run) > 1 and cells else None,
    }


def _segment_row(result: SegmentResult) -> dict[str, Any]:
    return {
        "policy_id": result.policy_id,
        "segment_idx": result.segment_idx,
        "run": result.run,
        "status": result.status,
        "error": result.error,
        "verified": [
            {**v.claim.model_dump(mode="json"), "match": v.match.value} for v in result.verified
        ],
        "rejected": [r.claim.model_dump(mode="json") for r in result.rejected],
    }


def provider_for(settings: ReaderSettings) -> Provider:
    name = settings.llm.provider
    if not name or not settings.llm.model:
        raise EngineError(
            "no AI provider/model set in reader.toml [llm] (open decisions 1-2); nothing was sent"
        )
    if name not in PROVIDERS:
        raise EngineError(f"provider {name!r} is not implemented yet; nothing was sent")
    return PROVIDERS[name](settings)
