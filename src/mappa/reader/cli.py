"""Command-line entry point for the policy reader (Task 02a).

Run as ``uv run python -m mappa.reader ...``. The brief asks for ``mappa reader ...``, but
that means adding this app to ``src/mappa/cli.py``, which is collector code this session
must not change; the one-line hookup is left for Alex (READER_LOG.md).

Known problems (missing zip, bad config, unset prices...) print one line, not a traceback.
"""

import json
from pathlib import Path
from typing import Annotated, Any, NoReturn

import typer

from mappa.reader.baselines.keyword import FEATURES_FILE, FeaturesError, KeywordBaseline, load_terms
from mappa.reader.baselines.tfidf import TfidfBaseline
from mappa.reader.canonical import Answer, Party, Policy, PracticeKey, gold_answers
from mappa.reader.datasets.app350 import (
    CANONICAL_FILE,
    NO_PARTY,
    SOURCE_FILE,
    App350Error,
    evaluable_keys,
    label_name,
    practice_keys,
    read_canonical,
    read_zip,
    read_zip_text,
    write_canonical,
)
from mappa.reader.datasets.split import CLI_NAMES
from mappa.reader.engine import METHOD as LLM_METHOD
from mappa.reader.engine import EngineError, dry_run, extract_split, prompt_parts, provider_for
from mappa.reader.evaluate import POSITIVE, report
from mappa.reader.llm.client import BudgetExceeded, CostEstimate, PricesMissing
from mappa.reader.llm.prompt import PROMPT_VERSION, PromptError
from mappa.reader.mapping import DEFAULT_PATH as MAPPING_PATH
from mappa.reader.mapping import MappingError, load_mapping
from mappa.reader.runs import Run, completed_test_runs, file_sha256
from mappa.reader.settings import (
    DEFAULT_PATH,
    ReaderConfigError,
    ReaderSettings,
    load_reader_settings,
)

CellKey = tuple[str, PracticeKey]

app = typer.Typer(help="Policy reader on APP-350 (Task 02a).", no_args_is_help=True)

_KNOWN_ERRORS: tuple[type[Exception], ...] = (
    App350Error,
    BudgetExceeded,
    EngineError,
    PricesMissing,
    PromptError,
    FeaturesError,
    MappingError,
    ReaderConfigError,
)

ConfigOption = Annotated[Path, typer.Option("--config", help="Reader config file.")]


@app.callback()
def main() -> None:
    """Keeps the commands a group even while there is only one."""


def _fail(message: str) -> NoReturn:
    typer.secho(f"error: {message}", fg=typer.colors.RED, err=True)
    raise typer.Exit(1)


def _settings(config: Path) -> ReaderSettings:
    try:
        return load_reader_settings(config)
    except ReaderConfigError as exc:
        _fail(str(exc))


@app.command()
def load(config: ConfigOption = DEFAULT_PATH) -> None:
    """APP-350 zip -> canonical JSONL + official split, in data-public/canonical/."""
    settings = _settings(config)
    zip_path = settings.raw_dir / SOURCE_FILE
    if not zip_path.exists():
        _fail(f"{zip_path} not found: download APP-350 by hand and put it there")
    try:
        policies, split = read_zip(zip_path)
        load_mapping(MAPPING_PATH)
        canonical, split_path = write_canonical(policies, split, settings.canonical_dir)
    except _KNOWN_ERRORS as exc:
        _fail(str(exc))
    by_id = {p.policy_id: p for p in policies}
    typer.echo(f"wrote {canonical} and {split_path} (split: {split.source})")
    typer.echo(f"{'split':<11}{'policies':>9}{'synthetic':>10}{'segments':>9}{'labels':>8}")
    for name in ("train", "validation", "test"):
        members = [by_id[i] for i in split.ids(name)]
        segments = [s for p in members for s in p.segments]
        typer.echo(
            f"{name:<11}{len(members):>9}{sum(p.contains_synthetic for p in members):>10}"
            f"{len(segments):>9}{sum(len(s.labels) for s in segments):>8}"
        )


SplitOption = Annotated[str, typer.Option("--split", help="val or test.")]
FinalOption = Annotated[
    bool, typer.Option("--final", help="Required for --split test: the one test run (R4).")
]


def _split_name(split: str) -> str:
    if split not in ("val", "test"):
        _fail(f"--split must be val or test, not {split!r}")
    return CLI_NAMES[split]


def _guard_test(
    settings: ReaderSettings, split: str, method: str, final: bool, *, scoring: bool = True
) -> None:
    """The test split is scored once per method, after the method is frozen (brief R4)."""
    if split != "test":
        return
    if not final:
        _fail("--split test is the one final run per method (R4); add --final to confirm")
    if not scoring:
        return
    done = completed_test_runs(settings.runs_dir, method)
    if done:
        _fail(f"{method} already has a test run ({done[0]}); the test split is used once")


@app.command()
def baseline(
    method: Annotated[str, typer.Option("--method", help="keyword or tfidf.")],
    split: SplitOption = "val",
    thresholds: Annotated[
        str, typer.Option("--thresholds", help="tfidf only: per_practice or global.")
    ] = "global",
    final: FinalOption = False,
    config: ConfigOption = DEFAULT_PATH,
) -> None:
    """Score a no-cost baseline on one split; results go to data-public/runs/<run_id>/."""
    if method not in ("keyword", "tfidf"):
        _fail(f"--method must be keyword or tfidf, not {method!r}")
    settings = _settings(config)
    split_name = _split_name(split)
    _guard_test(settings, split_name, method, final)
    try:
        policies, official = read_canonical(settings.canonical_dir)
        zip_path = settings.raw_dir / SOURCE_FILE
        by_id = {p.policy_id: p for p in policies}
        chosen = [by_id[i] for i in official.ids(split_name)]
        keys = practice_keys()
        params: dict[str, Any] = {}
        if method == "keyword":
            model: KeywordBaseline | TfidfBaseline = KeywordBaseline(
                load_terms(read_zip_text(zip_path, FEATURES_FILE))
            )
            params["terms_source"] = f"{SOURCE_FILE}:{FEATURES_FILE}"
        else:
            validation = [by_id[i] for i in official.validation]
            model = TfidfBaseline()
            model.fit([by_id[i] for i in official.train], keys)
            model.tune(validation, gold_table(validation, keys), POSITIVE, mode=thresholds)
            params |= {
                "thresholds_mode": thresholds,
                "global_threshold": model.global_threshold,
                "min_train_positives": model.min_train_positives,
                "min_val_positives": model.min_val_positives,
                "no_classifier": [label_name(*k) for k in model.skipped],
                "thresholds": {label_name(*k): t for k, t in sorted(model.thresholds.items())},
            }
    except _KNOWN_ERRORS as exc:
        _fail(str(exc))
    inputs = {
        "canonical_sha256": file_sha256(settings.canonical_dir / CANONICAL_FILE),
        "zip_sha256": file_sha256(zip_path),
    }
    run = Run(settings.runs_dir, method=method, split=split_name, params=params, inputs=inputs)
    predicted = {
        (policy.policy_id, key): answer
        for policy in chosen
        for key, answer in model.predict(policy, keys).items()
    }
    run.write_predictions(predicted, lambda key: label_name(*key))
    metrics = _metrics_by_subset(chosen, keys, predicted)
    run.finish(metrics)
    typer.echo(f"run {run.run_id} -> {run.dir}")
    _echo_summary(metrics)


def gold_table(policies: list[Policy], keys: list[PracticeKey]) -> dict[CellKey, Answer]:
    return {
        (policy.policy_id, key): answer
        for policy in policies
        for key, answer in gold_answers(policy, keys).items()
    }


def _metrics_by_subset(
    chosen: list[Policy], keys: list[PracticeKey], predicted: dict[CellKey, Answer]
) -> dict[str, Any]:
    """Scores on all policies of the split and on real (non-synthetic) ones only."""
    groups = {
        "first_party": [k for k in keys if k[1] is Party.FIRST and k[0] not in NO_PARTY],
        "third_party": [k for k in keys if k[1] is Party.THIRD],
    }
    subsets = {"all": chosen, "real": [p for p in chosen if not p.contains_synthetic]}
    result: dict[str, Any] = {}
    for name, members in subsets.items():
        gold = gold_table(members, keys)
        result[name] = report(
            gold,
            {cell: predicted[cell] for cell in gold},
            evaluable=evaluable_keys(),
            groups=groups,
            name=lambda key: label_name(*key),
        )
    return result


def _echo_summary(metrics: dict[str, Any]) -> None:
    def fmt(value: float | None) -> str:
        return "  -  " if value is None else f"{value:.3f}"

    typer.echo(
        f"{'subset':<7}{'pol':>4} {'P':>6}{'R':>6}{'F1':>6}{'macF1':>6}"
        f"{'NPV':>6}{'spec':>6}{'negF1':>6}{'F1-1st':>7}{'F1-3rd':>7}{'no-ok':>7}{'exact':>6}"
    )
    for subset, m in metrics.items():
        mi, no = m["micro"], m["answers"]["no"]
        typer.echo(
            f"{subset:<7}{m['n_policies']:>4} {fmt(mi['precision']):>6}{fmt(mi['recall']):>6}"
            f"{fmt(mi['f1']):>6}{fmt(m['macro']['f1']):>6}{fmt(mi['npv']):>6}"
            f"{fmt(mi['specificity']):>6}{fmt(mi['negative_f1']):>6}"
            f"{fmt(m['groups']['first_party']['f1']):>7}{fmt(m['groups']['third_party']['f1']):>7}"
            f"{fmt(no['exact_rate']):>7}{fmt(m['answers']['exact_accuracy']):>6}"
        )


@app.command()
def extract(
    split: SplitOption = "val",
    runs: Annotated[int, typer.Option("--runs", min=1, help="Repeat runs (stability).")] = 1,
    dry_run_: Annotated[
        bool, typer.Option("--dry-run", help="Estimate tokens and cost; send nothing.")
    ] = False,
    max_cost: Annotated[
        float | None, typer.Option("--max-cost", help="Stop before spending more AUD.")
    ] = None,
    final: FinalOption = False,
    config: ConfigOption = DEFAULT_PATH,
) -> None:
    """The AI engine: per-paragraph claims, verified quotes, per-policy answers."""
    settings = _settings(config)
    split_name = _split_name(split)
    _guard_test(settings, split_name, LLM_METHOD, final, scoring=not dry_run_)
    try:
        policies, official = read_canonical(settings.canonical_dir)
        by_id = {p.policy_id: p for p in policies}
        chosen = [by_id[i] for i in official.ids(split_name)]
        parts = prompt_parts(settings, [by_id[i] for i in official.train])
        if dry_run_:
            estimate, system_tokens = dry_run(settings, parts, chosen, runs)
            _echo_estimate(split_name, chosen, runs, estimate, system_tokens, settings)
            return
        provider = provider_for(settings)
        keys = practice_keys()
        run, metrics = extract_split(
            settings,
            parts,
            chosen,
            split=split_name,
            runs=runs,
            max_cost=max_cost,
            provider=provider,
            score=lambda members, table: _metrics_by_subset(members, keys, table),
        )
    except _KNOWN_ERRORS as exc:
        _fail(str(exc))
    typer.echo(f"run {run.run_id} -> {run.dir}")
    typer.echo(json.dumps(metrics["stats"], indent=2))
    for number in range(1, runs + 1):
        typer.echo(f"run {number}:")
        _echo_summary(metrics[f"run{number}"])


def _echo_estimate(
    split: str,
    chosen: list[Policy],
    runs: int,
    estimate: CostEstimate,
    system_tokens: int,
    settings: ReaderSettings,
) -> None:
    paragraphs = sum(len(p.segments) for p in chosen)
    distinct = len({s.text for p in chosen for s in p.segments})
    cost = (
        "prices not set in reader.toml [prices]"
        if estimate.aud is None
        else f"{estimate.aud:.2f} AUD"
    )
    typer.echo(f"DRY RUN (nothing sent), prompt {PROMPT_VERSION}, split {split}")
    typer.echo(
        f"policies {len(chosen)}, paragraphs {paragraphs} ({distinct} distinct texts), runs {runs}"
    )
    typer.echo(f"calls {estimate.calls}")
    typer.echo(f"system prompt ~{system_tokens} tokens (repeated in every call)")
    typer.echo(
        f"input ~{estimate.input_tokens} tokens, output ~{estimate.output_tokens} tokens "
        f"({settings.estimate.chars_per_token} chars/token, "
        f"{settings.estimate.output_tokens_per_paragraph} output tokens/paragraph)"
    )
    typer.echo(f"cost: {cost}")
