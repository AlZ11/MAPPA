# How the sample is built (snapshot 1, `2026-10-S1`)

Draft of the paper's Methods section. Everything below describes what the code in
`src/mappa/collect/` does. Counts are filled in from `reports/coverage_2026-10-S1.md`
after the real run, and **open decisions** are marked as such.

Status: **no live data collected yet.** The pipeline has been run end to end only on
synthetic data (`mappa --synthetic snapshot run`). The first live run waits for the
project contact address (open decision 5).

## 1. Store and date

- Google Play, Australian store (`gl=AU`), English listings (`hl=en`).
- Snapshot 1 is taken before the automated-decision disclosure rules start on
  10 December 2026. It is the "before" half of the before/after comparison (RQ3).
- Every record carries the snapshot ID and a UTC timestamp. Each snapshot also stores
  the git commit and the full settings it was collected with.

## 2. Candidate apps (discovery)

Apps enter the candidate list from two sources, and each entry records how it got in
(source, search term or file, rank):

1. **Search.** 153 search terms (`config/queries.txt`) across the task's 18 topic areas
   (fitness, running and cycling, diet, weight loss, sleep, meditation, mental health,
   women's health, baby and child health, chronic conditions, medication reminders,
   pharmacy, telehealth and GP booking, symptom checkers and AI assistants, health
   records, quitting smoking or alcohol, hearing and vision, physiotherapy), plus five
   general terms. The terms are generic phrases, never brand names, in Australian wording
   where it differs (chemist, bulk billing, eScript, Medicare, immunisation...). Six terms
   target AI features (RQ3).
   - Each term is searched once. The first results page is recorded in full, in page
     order. The task's original choice, the Python `google-play-scraper`, caps each
     search at 30 results; the real number per page is confirmed in the first live run
     (count: _TBD_).
2. **Seed list.** `config/seed_apps.csv`: Calm and MyFitnessPal, the two apps scored by
   human reviewers in the 2021 ACSIMA study. They are kept so the human and automated
   scores can be compared directly. Package names: _to verify on the AU store_.

Not used:

- **Top charts** (optional in the task). The only route, the Node `google-play-scraper`
  `list` function, could not be verified, because the development environment has no
  network access. Decision: _skipped / verified in the first live run_.

Duplicates are merged by package name. A snapshot's inputs are fixed: if
`seed_apps.csv` changes, or a term already searched is removed from `queries.txt`,
collection stops and asks for a new snapshot ID.

## 3. Store listings

For every candidate, the AU listing page is fetched and stored twice: the page as
received, and the parsed fields as JSON. There is no fallback to other countries: an app
missing from the AU store is recorded as `not_found`. (The `google-play-scraper` library
would silently return another country's listing; it is used only to read fields, never
to fetch.)

## 4. Inclusion rules (open decisions 1-2: to confirm with the supervisor)

An app is **eligible** when all of these hold:

| Rule | Default | Setting |
|---|---|---|
| Listing fetched from the AU store | required | - |
| Genre | `HEALTH_AND_FITNESS` or `MEDICAL` | `inclusion.genres` |
| Price | free only (paid apps can't be tested dynamically later) | `inclusion.free_only` |
| Installs | at least 1,000 | `inclusion.min_installs` |

Eligible apps are then **selected** in two strata, so the study isn't only about big
apps:

1. **Top 800 by installs.** Google shows installs in buckets ("100,000+"), and many apps
   share a bucket. Ranking therefore uses the exact install count embedded in the listing
   page where present, then the number of ratings, then the package name. Every tie is
   broken.
2. **Random 200 from the rest.** Remaining eligible apps are ordered by
   SHA-256(`"20261005:" + package name`), and the first 200 are taken. This is a random
   draw fixed by a seed chosen before any data was seen. It is also *stable*: if a
   retried listing adds one app to the pool, at most one app in this stratum changes.

Seed-list apps are included whenever their listing was fetched, even if the rules would
exclude them. They are recorded with stratum `seed`.

Every candidate gets a recorded decision: `included` with its stratum, or excluded with a
reason (e.g. `genre GAME_PUZZLE`, `paid`, `installs below 1,000`,
`listing not_found in the AU store`, `eligible, not selected`).

**For analysis:** the two strata have different selection probabilities. Market-level
figures must weight the long-tail stratum (weight = eligible-but-not-top / 200), or
report the strata separately.

## 5. Evidence collected per included app

| Evidence | How | Status values |
|---|---|---|
| Privacy policy | URL from the listing; rendered in headless Chromium (page load plus network idle, 30 s cap); PDFs read with pypdf; text extracted with trafilatura, with a full-text fallback under 200 words | `ok`, `not_provided` (no URL: a finding), `not_found`, `blocked`, `failed` |
| Data Safety label | `play.google.com/store/apps/datasafety?id=...&hl=en&gl=AU`, rendered in headless Chromium; parsed from the label data Google embeds in the page | `ok`, `not_provided` (developer gave no information: a finding), `not_found`, `blocked`, `failed` |
| APK | **not collected for snapshot 1** (open decision 3): AndroZoo needs an API key; F-Droid was ruled out (open-source apps only, built differently from the Play versions). Recorded as `skipped` with the store version, for a later backfill by snapshot date | `skipped` |

Policies shared by several apps are fetched once per snapshot, and every app gets its own
record. Policies are never crawled across pages. Short pages that link to other privacy
pages, pages that don't look like policies (no "privacy", or 300 words or fewer), store
pages, homepages and non-English pages are flagged for manual review, never dropped.

## 6. Ethics and politeness

- Public pages only. No logins, and no attempt to get past blocks or captchas: a block is
  retried with exponential backoff (4 attempts), then recorded as `blocked`.
- At most 1 request per second per site, and at most 4 sites in parallel.
- Every request names the project and a contact address in its User-Agent.
- Every request attempt is logged with its response. Raw pages are kept unchanged,
  addressed by SHA-256, and re-verified when the snapshot is frozen.

## 7. Counts at every stage (fill in from the coverage report)

| Stage | Count |
|---|---|
| Search terms run / failed / zero results | _TBD_ |
| Candidate apps (unique) | _TBD_ |
| ... of which from search / seed list | _TBD_ |
| Listing fetched `ok` / `not_found` / `blocked` / `failed` | _TBD_ |
| Excluded: genre / paid / installs / other | _TBD_ |
| Eligible | _TBD_ |
| Included: top installs / random long tail / seed | _TBD_ |
| Policy `ok` / `not_provided` / `not_found` / `blocked` / `failed` | _TBD_ |
| Label `ok` / `not_provided` / `not_found` / `blocked` / `failed` / parse errors | _TBD_ |
| APK `skipped` | _TBD_ |

Collection dates: _TBD_ to _TBD_ (UTC). Total run time: _TBD_. Code version: _TBD_.

## 8. Known limitations

- Search results depend on Google's ranking on the collection date. Recording the rank
  and date makes this transparent, but doesn't remove it.
- The genre filter relies on the developer's chosen genre, so health apps listed under
  other genres (e.g. Lifestyle) are excluded.
- APKs are backfilled later, so their version may not match the listing seen on the
  snapshot date. The match rate will be reported.
- The Data Safety parser reads Google's embedded label data. Its positions come from a
  maintained open-source scraper and are validated against hand-checked pages (see
  VALIDATION.md).
