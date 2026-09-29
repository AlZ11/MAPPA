# Task 01 — Collector + Data Safety label reader

**Weeks 1–2: Mon 5 Oct – Sun 18 Oct 2026**

## Goal

Take snapshot 1 (`2026-10-S1`) of about 1,000 Australian Google Play health apps. For
each app, store its store metadata, privacy policy, Data Safety label and, where
available, APK. Everything is dated, hashed and resumable. Data Safety labels are parsed
into structured rows.

## Why it matters

This snapshot is the "before" half of the before/after study of the automated-decision
disclosure rule that starts on 10 December 2026. It must be complete and reproducible
well before then. Policies and labels change silently and cannot be re-fetched from the
past; APKs can (AndroZoo keeps dated versions), so APKs are the least time-critical part.

## Deliverables

1. `mappa` CLI with the commands listed below
2. SQLite database + blob store matching the schema below
3. Data Safety parser with fixture tests, writing to `label_facts` and `label_practices`
4. Coverage report (`reports/coverage_<snapshot>.md` + `.csv`)
5. `docs/SAMPLING.md` — how the sample was built (sources, queries, filters, dates, counts
   at every stage). This becomes the paper's Methods section.
6. `docs/VALIDATION.md` — results of the 20-app manual check of parsed labels
7. Snapshot manifest + backup (see M6)

## CLI commands

```
mappa init                                   # create data dir, db schema
mappa discover        --snapshot ID          # build candidate list
mappa fetch-metadata  --snapshot ID          # store listing per candidate, then apply inclusion rules
mappa fetch-policy    --snapshot ID
mappa fetch-datasafety --snapshot ID
mappa parse-datasafety --snapshot ID         # re-runnable from raw blobs, no network
mappa fetch-apk       --snapshot ID
mappa snapshot run    --snapshot ID [--limit N] [--dev]   # all steps in order
mappa report coverage --snapshot ID
```

`--dev` runs on the 20-app dev sample in `config/dev_apps.csv`. `--limit N` caps the
number of apps. All commands are resumable.

## Config (`config/config.toml` + env)

- `data_dir`, `country = "au"`, `lang = "en"`, `target_n = 1000`
- `user_agent`, `contact_email` (project/CSIRO address, supplied by Alex)
- `rate_limit_per_domain_rps = 1.0`, `max_parallel_domains = 4`, retry settings
- `random_seed` (for the long-tail sample)
- `apk_sources = ["androzoo"]`
- Secret from environment only: `ANDROZOO_API_KEY`

## Database schema (SQLite)

Primary keys include `snapshot_id` so later snapshots sit alongside this one.
Status values everywhere: `ok | not_provided | not_found | blocked | failed | skipped`.

**snapshots** — snapshot_id PK, started_at, finished_at, git_commit, config_json, notes

**discovery** — snapshot_id, app_id, source (`search | chart | seed_file`), query,
rank, discovered_at

**app_metadata** — PK (snapshot_id, app_id); fetched_at, status, title, developer,
developer_id, developer_email, developer_website, genre_id, installs_min, price, free,
score, ratings_count, version, updated_at, released, contains_ads, content_rating,
privacy_policy_url, raw_blob, included (bool), exclusion_reason

**fetch_log** — id, snapshot_id, app_id, kind (`metadata | policy | datasafety | apk`),
url, final_url, http_status, status, error, attempt, started_at, finished_at, raw_blob

**policy_docs** — PK (snapshot_id, app_id); url, final_url, status, format
(`html | pdf | gdoc | other`), raw_blob, text_blob, text_sha256_normalized, word_count,
language, extraction_method, looks_like_policy (bool), review_flag (text or null)

**label_facts** — id, snapshot_id, app_id, section (`collected | shared`), category,
data_type, mapped (bool), raw_label, optional (bool or null), purposes (JSON list),
raw_blob, evidence_text, parser_version

**label_practices** — id, snapshot_id, app_id, practice (`encrypted_in_transit |
deletion_request | families_policy | independent_review | no_data_collected |
no_data_shared`), value (bool), evidence_text, raw_blob, parser_version

**label_status** — PK (snapshot_id, app_id); status, raw_blob, parser_version, parse_error

**apks** — PK (snapshot_id, app_id); status, source, sha256, vercode, version_name,
dex_date, size_bytes, path, store_version, version_match (bool or null)

## Blob store

- Path: `<data_dir>/blobs/sha256/<first 2 hex>/<next 2 hex>/<full hash>`
- `put(bytes, content_type) -> hash` writes only if absent (identical content = no-op).
- A sidecar table or JSON records content_type, size and first-seen time.
- APKs are large: store under `<data_dir>/apks/` named by SHA-256, same write-once rule.
- Normalised text hash for policies: lowercase, collapse whitespace, strip. This stops
  trivial formatting changes from counting as policy changes between snapshots.

## Milestones

Stop after each milestone: run tests, run on the dev sample, print the coverage
summary, report back.

### M0 — Scaffold (day 1, Mon 5 Oct)

- Repo, `uv` project, config loader, db schema, blob store, CLI skeleton, logging, ruff,
  mypy, pytest.
- Tests: blob store is write-once and content-addressed; schema creates cleanly; config
  rejects a missing `contact_email`.

### M1 — Discovery (days 2–3)

Build the candidate list from three sources, recording source, query and rank:

1. **Search.** Draft `config/queries.txt` (~150 terms) covering: fitness and activity,
   running and cycling, diet and nutrition, weight loss, sleep, meditation, mental health
   and therapy, women's health (periods, fertility, pregnancy), baby and child health,
   chronic conditions (diabetes, blood pressure, heart, asthma), medication reminders,
   pharmacy, telehealth and GP booking, symptom checkers and AI health assistants,
   health records, quitting smoking or alcohol, hearing and vision, physiotherapy.
   Alex reviews the list before the full run. Use the `google-play-scraper` Python
   package's `search` with `country="au"`, `lang="en"`. **First check its real maximum
   results per query and the fields it returns.**
2. **Top charts (optional).** The Node `google-play-scraper` package has a `list`
   function for top charts (HEALTH_AND_FITNESS, MEDICAL; country au). Verify it still
   works. If it doesn't, skip it and say so in SAMPLING.md.
3. **Seed file.** `config/seed_apps.csv` — must include Calm and MyFitnessPal, the two
   apps from the 2021 human study (verify their package names on the store).

Deduplicate by package name. Log counts per source.

### M2 — Metadata + inclusion (days 3–4)

- Fetch each candidate's listing with the Python package's `app()` (`country="au"`,
  `lang="en"`). Store the full raw JSON as a blob. **Check field names on one real
  response before mapping.**
- Default inclusion rules (Alex confirms with supervisor; keep them configurable):
  - `genre_id` in {`HEALTH_AND_FITNESS`, `MEDICAL`}
  - listing fetch succeeded for the AU store
  - free apps only (paid apps can't be tested dynamically later without buying them)
  - installs ≥ 1,000
- Selection: top 800 by installs + a seeded random 200 from the remaining eligible apps,
  so the study isn't only about big apps. Always include seed-file apps.
- Write `included` and `exclusion_reason` for every candidate. Write stage counts to
  SAMPLING.md.

### M3 — Privacy policies (days 4–6)

- URL from metadata. No URL → `not_provided`. That is a finding, not an error.
- Fetch with Playwright chromium, headless. Wait for load plus network idle
  (30-second cap). Record final URL and HTTP status. Save the rendered HTML as a blob.
- Handle:
  - redirects
  - PDFs: download and extract text with `pypdf`
  - Google Docs / Sites links
  - URLs that point to an app store page or a homepage rather than a policy:
    heuristic `looks_like_policy` (mentions privacy and has > 300 words); flag
    failures for manual review
  - cookie banners: don't click; extract text as-is
  - non-English text: detect the language and flag it
- Extract the main text with `trafilatura` (tables included, favour recall). If that
  gives < 200 words, fall back to the page's full visible text. Record
  `extraction_method`.
- Policies split across several pages: don't crawl. Set `review_flag` when the page is
  short but links to other privacy-looking pages.
- Many apps share one developer policy. The blob store dedupes it; still record a row
  per app.

### M4 — Data Safety labels (days 6–9)

- URL pattern: `https://play.google.com/store/apps/datasafety?id=<app_id>&hl=en&gl=AU`.
  **Verify it renders for AU before building on it.**
- Fetch with Playwright and save the rendered HTML. Check whether the collapsed
  sections' content is already in the HTML. If not, expand them before saving.
- Parser (`parse/datasafety.py`, versioned with `PARSER_VERSION`):
  - Headline statements: "No data shared with third parties", "No data collected",
    "developer has not provided information" (→ `not_provided`)
  - Data shared and Data collected sections: category → data type → purposes, plus the
    optional flag
  - Security practices → `label_practices`
  - Map strings to the taxonomy in PROJECT_OUTLINE.md. Unknown strings: keep the raw
    string, set `mapped = false`, never drop it.
- Fixture tests (at least 8 saved pages):
  1. no data collected
  2. no data shared
  3. many categories
  4. optional data present
  5. label not provided
  6. all security practices present
  7. app removed / 404
  8. one odd layout found in the wild
- Validation: Alex opens 20 random labels in a browser and compares them with the
  parsed rows. Record the results in `docs/VALIDATION.md`. Fix the parser until all 20
  match.
- Optional cross-check: compare against the Node `google-play-scraper` `datasafety`
  output on the dev sample.

### M5 — APKs (days 8–10; depends on the AndroZoo key)

- AndroZoo's `latest.csv.gz` index is very large. Stream-filter it to the included
  package names with `markets` containing `play.google.com`; never load it whole.
- Choose the version with `dex_date` closest to (and not after) the snapshot date.
  Record `vercode`, `sha256`, `dex_date` and size.
- Download through the AndroZoo API using the key from the environment. Verify the
  SHA-256 after download. Follow AndroZoo's published concurrency limits.
- Read `version_name` with `androguard` (cheap, manifest only). Set
  `version_match = (version_name == store_version)`. Report the match rate: a version
  gap is a known limitation.
- **Check free disk space first.** 1,000 APKs can take tens of GB.
- If the key hasn't arrived by day 8: mark APKs `skipped` and carry on. Backfill later
  using the snapshot date, since AndroZoo keeps dated versions.

### M6 — Full run, report, freeze (days 10–12, done by Sun 18 Oct)

- `mappa snapshot run --snapshot 2026-10-S1`
- Coverage report:
  - counts at each stage (discovered → metadata ok → included → policy status → label
    status → APK status)
  - top 10 failure reasons
  - total run time
  - 10 random apps with links to their raw blobs for spot checks
- Freeze:
  - write `snapshot_manifest.json` (counts, git commit, config, db SHA-256)
  - make the db read-only for this snapshot
  - back up `data_dir` to a second location (CSIRO storage)

## Acceptance criteria

- [ ] ≥ 95% of included apps have metadata `ok`
- [ ] ≥ 90% of included apps have a policy status other than `failed` / `blocked`
- [ ] ≥ 90% of included apps have a label status other than `failed` / `blocked`
- [ ] Every stored record has `snapshot_id`, `fetched_at` and a blob hash
- [ ] Re-running any command makes zero network requests for completed items
- [ ] All 20 manually checked labels match their parsed rows
- [ ] Calm and MyFitnessPal are in the snapshot with policy + label
- [ ] SAMPLING.md, VALIDATION.md, coverage report and manifest are written; backup done
- [ ] `pytest`, `ruff` and `mypy` all pass

## Out of scope for this task

LLM policy extraction, code scanning beyond reading `version_name`, traffic capture,
scoring, any UI.

## Open decisions (Alex / supervisor — Claude Code must not decide these)

1. Free apps only, or include paid apps?
2. Install threshold, and the top-N / long-tail split
3. Is AndroZoo approved? Any other APK source?
4. Where the data lives (CSIRO storage vs laptop), and retention rules
5. Contact address for the User-Agent
