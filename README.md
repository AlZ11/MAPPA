# MAPPA 2.0

Research software for a CSIRO Data61 internship. It collects dated, hashed evidence about
Australian Google Play health apps (store listing, privacy policy, Data Safety label, APK),
then scores each app against the ACSIMA 2.0 checklist with that evidence attached.

- Project guide and non-negotiable rules: [`CLAUDE.md`](CLAUDE.md)
- Whole-project plan: [`docs/PROJECT_OUTLINE.md`](docs/PROJECT_OUTLINE.md)
- Tasks: [`docs/tasks/`](docs/tasks/) (the current task is the lowest-numbered unfinished one)

## Setup

Needs [uv](https://docs.astral.sh/uv/), which installs Python 3.12 if it's missing.

```sh
uv sync
export MAPPA_CONTACT_EMAIL=...   # project/CSIRO contact address (or set it in config/config.toml)
uv run mappa init                # creates data/ (gitignored): database, blobs/, apks/, logs/
uv run mappa --help
```

The contact address goes in the User-Agent of every request, so no command runs without it.
Secrets such as `ANDROZOO_API_KEY` are read from the environment only, never from files.

## Checks

```sh
uv run pytest -q
uv run ruff check . && uv run mypy src
```
