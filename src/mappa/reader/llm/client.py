"""Provider-agnostic AI client with a response cache, cost estimates and a budget stop.

- **Providers are swappable.** A provider only has to turn a ``Request`` into a
  ``Reply``. No provider is wired in yet: which one to use, and whether APP-350 text may
  be sent to it at all, are open decisions for Alex and the supervisor.
- **The cache makes re-runs free.** Its key is provider + model + prompt version +
  settings + paragraph hash + run number (task brief R3), plus a hash of the system
  prompt and output schema: definitions and few-shot examples are loaded at runtime, so
  the version string alone can't prove two prompts are identical. A hit makes no API call.
  Entries are written once and never changed, like the collector's blobs.
- **Money is checked before it is spent.** ``estimate_cost`` gives the ``--dry-run``
  figure from text lengths and the prices in reader.toml. ``BudgetGuard`` refuses a call
  that could push spending past ``--max-cost``.

No test calls a live API: tests use ``RecordedProvider`` with made-up replies.
"""

import hashlib
import json
import math
import os
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

from mappa.reader.settings import Estimate, Prices


@dataclass(frozen=True)
class Request:
    model: str
    system: str
    user: str
    tool_schema: Mapping[str, Any]  # the forced output shape (structured output / tools)
    temperature: float = 0.0
    max_output_tokens: int = 1024


@dataclass(frozen=True)
class Reply:
    payload: dict[str, Any]  # the structured output, before pydantic validation
    input_tokens: int
    output_tokens: int


class Provider(Protocol):
    name: str

    def complete(self, request: Request) -> Reply: ...


class ProviderError(RuntimeError):
    """A provider call failed (network, refusal, malformed reply). The paragraph is
    recorded as ``failed``, never as "no claims"."""


class BudgetExceeded(RuntimeError):
    """The next call could take spending past the --max-cost cap."""


class PricesMissing(RuntimeError):
    """Cost can't be estimated until Alex enters prices in reader.toml."""


@dataclass(frozen=True)
class CostEstimate:
    calls: int
    input_tokens: int
    output_tokens: int
    aud: float | None  # None until Alex enters prices in reader.toml


def estimate_tokens(text: str, estimate: Estimate) -> int:
    """Rough token count from length. Real tokenisers differ by provider; this is for
    budgeting only, and reports must call it an estimate."""
    return math.ceil(len(text) / estimate.chars_per_token)


def estimate_cost(
    prompts: list[tuple[str, str]], prices: Prices, estimate: Estimate
) -> CostEstimate:
    """Tokens (and AUD, if prices are set) for sending each (system, user) prompt once,
    before any call is made. Token counts are useful on their own: they let Alex compare
    providers before choosing one."""
    input_tokens = sum(estimate_tokens(system + user, estimate) for system, user in prompts)
    output_tokens = estimate.output_tokens_per_paragraph * len(prompts)
    priced = prices.input_aud_per_mtok is not None and prices.output_aud_per_mtok is not None
    aud = _aud(input_tokens, output_tokens, prices) if priced else None
    return CostEstimate(len(prompts), input_tokens, output_tokens, aud)


def cache_key(
    *,
    provider: str,
    model: str,
    prompt_version: str,
    settings: Mapping[str, Any],
    paragraph_sha256: str,
    run: int,
) -> str:
    material = {
        "provider": provider,
        "model": model,
        "prompt_version": prompt_version,
        "settings": dict(settings),
        "paragraph_sha256": paragraph_sha256,
        "run": run,
    }
    return hashlib.sha256(json.dumps(material, sort_keys=True).encode()).hexdigest()


class ResponseCache:
    """One JSON file per reply, named by its cache key, written once."""

    def __init__(self, root: Path) -> None:
        self.root = root

    def _path(self, key: str) -> Path:
        return self.root / key[:2] / f"{key}.json"

    def get(self, key: str) -> Reply | None:
        path = self._path(key)
        if not path.exists():
            return None
        data = json.loads(path.read_text(encoding="utf-8"))
        return Reply(data["payload"], data["input_tokens"], data["output_tokens"])

    def put(self, key: str, reply: Reply) -> None:
        path = self._path(key)
        if path.exists():
            return
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(
            json.dumps(
                {
                    "payload": reply.payload,
                    "input_tokens": reply.input_tokens,
                    "output_tokens": reply.output_tokens,
                },
                sort_keys=True,
            ),
            encoding="utf-8",
        )
        tmp.replace(path)


@dataclass
class BudgetGuard:
    """Tracks real spending (from reported token counts) against a cap in AUD."""

    prices: Prices
    max_aud: float | None
    spent_aud: float = 0.0

    def check(self, next_call_estimate_aud: float) -> None:
        if self.max_aud is not None and self.spent_aud + next_call_estimate_aud > self.max_aud:
            raise BudgetExceeded(
                f"spent {self.spent_aud:.4f} AUD; the next call (~{next_call_estimate_aud:.4f}) "
                f"could exceed the {self.max_aud:.2f} AUD cap"
            )

    def record(self, reply: Reply) -> None:
        if (
            self.prices.input_aud_per_mtok is not None
            and self.prices.output_aud_per_mtok is not None
        ):
            self.spent_aud += _aud(reply.input_tokens, reply.output_tokens, self.prices)


@dataclass
class CachedClient:
    provider: Provider
    cache: ResponseCache
    budget: BudgetGuard
    estimate: Estimate
    prompt_version: str
    api_calls: int = 0
    cache_hits: int = 0
    calls_log: list[str] = field(default_factory=list)

    def complete(self, request: Request, *, paragraph: str, run: int) -> Reply:
        key = cache_key(
            provider=self.provider.name,
            model=request.model,
            prompt_version=self.prompt_version,
            settings={
                "temperature": request.temperature,
                "max_output_tokens": request.max_output_tokens,
                "system_sha256": hashlib.sha256(request.system.encode()).hexdigest(),
                "schema_sha256": hashlib.sha256(
                    json.dumps(request.tool_schema, sort_keys=True).encode()
                ).hexdigest(),
            },
            paragraph_sha256=hashlib.sha256(paragraph.encode()).hexdigest(),
            run=run,
        )
        cached = self.cache.get(key)
        if cached is not None:
            self.cache_hits += 1
            return cached
        if (
            self.budget.prices.input_aud_per_mtok is not None
            and self.budget.prices.output_aud_per_mtok is not None
        ):
            self.budget.check(
                _aud(
                    estimate_tokens(request.system + request.user, self.estimate),
                    request.max_output_tokens,
                    self.budget.prices,
                )
            )
        elif self.budget.max_aud is not None:
            raise PricesMissing("a --max-cost cap needs prices in reader.toml")
        reply = self.provider.complete(request)
        self.api_calls += 1
        self.calls_log.append(key)
        self.budget.record(reply)
        self.cache.put(key, reply)
        return reply


@dataclass
class RecordedProvider:
    """Replays made-up replies keyed by the user prompt's SHA-256. For tests only."""

    replies: Mapping[str, dict[str, Any]]
    name: str = "recorded"
    calls: int = 0

    def complete(self, request: Request) -> Reply:
        self.calls += 1
        payload = self.replies[hashlib.sha256(request.user.encode()).hexdigest()]
        return Reply(payload=payload, input_tokens=len(request.user) // 4, output_tokens=50)


def api_key(env_name: str | None) -> str:
    """The provider key, from the environment only."""
    if not env_name or not os.environ.get(env_name):
        raise RuntimeError(f"set the API key in environment variable {env_name or '(api_key_env)'}")
    return os.environ[env_name]


def _aud(input_tokens: int, output_tokens: int, prices: Prices) -> float:
    if prices.input_aud_per_mtok is None or prices.output_aud_per_mtok is None:
        raise PricesMissing("enter [prices] in reader.toml")
    return (
        input_tokens * prices.input_aud_per_mtok + output_tokens * prices.output_aud_per_mtok
    ) / 1_000_000
