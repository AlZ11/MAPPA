# MAPPA 2.0 — project guide for Claude Code

## What this project is

Research software for a CSIRO Data61 internship. It extends the ACSIMA / MAPPA paper
(Sellak et al., CSIRO Data61, unpublished draft). That paper built ACSIMA, a 50-criterion
cyber security checklist for Australian mHealth apps. Human reviewers scored 2 apps
(Calm, MyFitnessPal) against it using only documents the developer published. MAPPA,
the proposed automation tool, was never written up.

MAPPA 2.0 automates the assessment and adds evidence of what apps actually do. For each
app it gathers four evidence sources:

1. Privacy policy (what the developer says, long form)
2. Google Play Data Safety label (what the developer says, structured)
3. Static analysis of the APK (what the app CAN do: permissions, embedded tracker SDKs)
4. Network traffic (what the app DOES send) — stretch goal

It stores every finding as an evidence row, flags contradictions between sources, and
scores each app against ACSIMA 2.0 (ACSIMA + criteria for Australian privacy rules that
start on 10 December 2026).

- Whole-project plan: `docs/PROJECT_OUTLINE.md`
- Current task: the lowest-numbered unfinished file in `docs/tasks/`

## Principles (non-negotiable)

1. **Evidence or nothing.** Every derived fact stores a pointer to raw evidence
   (blob hash + location in it). No evidence pointer → do not record the fact.
2. **Raw is immutable.** Raw downloads are written once to a content-addressed blob
   store and never modified. Parsers read raw, write derived rows, and can be re-run.
3. **Dated and reproducible.** Every record carries `snapshot_id` and `fetched_at` (UTC).
   Record how each app entered the sample (query, rank, source).
4. **Unknown ≠ absent.** Keep these distinct everywhere: `ok`, `not_provided` (developer
   gave nothing — this is a finding), `not_found` (404 / app removed), `blocked`
   (403 / 429 / captcha), `failed` (other error). Never turn a failure into empty/false.
5. **Resumable and idempotent.** Any job can be killed and re-run. It skips completed
   work, retries failures with backoff, and makes no network calls for finished items.
6. **Small first.** New code runs on the 20-app dev sample, gets inspected by hand,
   then scales.

## Ethics and access rules

- Public data only. No logins, no bypassing paywalls, captchas or blocks, no proxies or
  IP rotation to dodge rate limits. If blocked: back off, record `blocked`, report it.
- Default rate limit ≤ 1 request/second per domain, ≤ 4 domains in parallel. Configurable.
- User-Agent names the research project and a contact address taken from config.
  Never hardcode a personal email.
- APKs come only from AndroZoo (academic licence) unless config lists another source
  approved by the supervisor. Never commit APKs or raw data to git. Never redistribute.
- No real user data, ever. Test devices and test accounts only (later phases).

## Tech stack

- Python 3.12, managed with `uv`
- CLI: `typer` (entry point `mappa`)
- Models: `pydantic` v2
- HTTP: `httpx`; browser fetching: `playwright` (chromium); retries: `tenacity`
- Text extraction: `trafilatura`; PDFs: `pypdf`
- Storage: SQLite through SQLAlchemy 2.0 Core for metadata; files on disk for blobs.
  DuckDB is fine for ad-hoc analysis.
- Logging: `structlog` (JSON lines to file + readable console)
- Quality: `pytest`, `ruff`, `mypy` (strict on `src/mappa/models`)

## Layout

```
src/mappa/
  cli.py            # typer app, one command per pipeline step
  config.py         # loads config.toml + env vars
  models/           # pydantic models + table definitions
  storage/          # blob store, db engine, schema
  collect/          # discovery, metadata, policy, datasafety, apk
  parse/            # datasafety parser, policy text cleaning
  reports/          # coverage report
tests/
  fixtures/         # small saved raw pages (committed)
config/             # config.toml, queries.txt, seed_apps.csv
data/               # gitignored: sqlite db + blobs + apks (path set in config)
docs/
```

## Commands

- `uv run mappa --help`
- `uv run pytest -q`
- `uv run ruff check . && uv run mypy src`

## How to work in this repo

- Read the current task file first. Plan before coding. When the spec is ambiguous or
  looks wrong, list the question instead of guessing.
- **Verify external APIs and page structures before relying on them.** Check the
  installed package's real functions and return fields. Fetch one real page and save it
  as a test fixture. Do not code against APIs from memory.
- Parsers get fixture-based tests. When a parse fails in the wild, save that page as a
  new fixture and add a test for it.
- Keep modules small. Docstrings explain *why*, not just what: every design choice
  must be explainable in a job interview.
- After each milestone: run tests, run on the 20-app dev sample, print the coverage
  summary, then stop and report.
- Do not build beyond the current task file. Put ideas in `docs/IDEAS.md`.
