# MAPPA 2.0

Research software for a CSIRO Data61 internship. It collects dated, hashed evidence about
Australian Google Play health apps (store listing, privacy policy, Data Safety label, APK),
then scores each app against the ACSIMA 2.0 checklist with that evidence attached.

- Project guide and non-negotiable rules: [`CLAUDE.md`](CLAUDE.md)
- Whole-project plan: [`docs/PROJECT_OUTLINE.md`](docs/PROJECT_OUTLINE.md)
- Tasks: [`docs/tasks/`](docs/tasks/) (the current task is the lowest-numbered unfinished one)
- How the sample is built: [`docs/SAMPLING.md`](docs/SAMPLING.md)

## Setup

Needs [uv](https://docs.astral.sh/uv/), which installs Python 3.12 if it's missing.

```sh
uv sync
uv run playwright install chromium   # the browser used for privacy policies and labels
```

Set the project contact address, either in `config/config.toml` or with
`export MAPPA_CONTACT_EMAIL=...`. It goes in the User-Agent of every request: no command
runs without it, and live requests refuse placeholder addresses such as `@example.org`.
Secrets such as `ANDROZOO_API_KEY` come from the environment only, never from files.

## Running

```sh
uv run mappa init                                       # create data/ (gitignored)
uv run mappa snapshot run --snapshot dev-01 --dev       # the 20-app dev sample
uv run mappa snapshot run --snapshot 2026-10-S1         # the full snapshot
uv run mappa report coverage --snapshot 2026-10-S1
uv run mappa snapshot freeze --snapshot 2026-10-S1 --backup-to /path/to/second/location
```

Each step is also its own command (`discover`, `fetch-metadata`, `fetch-policy`,
`fetch-datasafety`, `parse-datasafety`, `fetch-apk`), and every command can be stopped and
re-run: finished items are skipped without any network request.

### Synthetic data (no network)

`--synthetic` runs every command against a fake Google Play and fake policy sites, in a
separate store (`data-dev/`), with snapshot IDs starting `synthetic-`. It is for checking
the plumbing; it never touches real data or the network.

```sh
uv run mappa --synthetic init
uv run mappa --synthetic snapshot run --snapshot synthetic-01
```

## Checks

```sh
uv run pytest -q
uv run ruff check . && uv run mypy src
```

The browser test needs Chromium (`uv run playwright install chromium`). Where Playwright's
own build can't be installed, set `MAPPA_CHROMIUM_EXECUTABLE` to a Chromium binary.
