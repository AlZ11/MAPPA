# Task 02a — Policy reader MVP on public labelled data

**Standalone. No dependency on the collector, the contact email, or live data.**
Target: about 2 weeks. Supersedes the start of Task 02. Task 02 becomes 02b: the
Australian health-app answer key and the new ACSIMA 2.0 claim types, built on this engine.

## Goal

Build the core engine of the policy reader and measure its accuracy on **APP-350**:
350 Android app privacy policies whose paragraphs were labelled by a privacy lawyer.
Compare it with two cheap baselines and with the numbers published in the original
paper (Zimmeck et al., "MAPS", PoPETs 2019).

## Why this comes before Task 02

- APP-350 comes with expert labels, so accuracy can be measured **now**, without first
  spending 15–20 hours hand-labelling.
- It's a public benchmark with published results, which makes for a stronger claim than
  "measured against my own labels".
- The engine (fixed-form extraction + quote verification + per-policy combining +
  evaluation) is the same engine Task 02b needs. Only the label vocabulary changes.

## What this task deliberately does NOT do

- No connection to the collector's database or snapshots.
- No Australian policies and no new ACSIMA 2.0 claim types (automated decisions, AI
  provider, children). That's 02b.
- No user interface.
- No OPP-115 yet (phase 2, at the end of this file).

## Data rules (licence)

APP-350 is licensed for research, teaching and scholarship only, in the spirit of a
Creative Commons non-commercial licence. Therefore:

- Alex downloads `APP-350_v1.1.zip` by hand from https://usableprivacy.org/data and puts
  it in `data-public/raw/`. Claude Code does not download it.
- `data-public/` is gitignored. **Never commit APP-350 files or any text taken from
  them**, including in test fixtures. Tests use small made-up examples in the same shape.
- Committed results contain aggregate numbers only, plus at most short quoted fragments
  in the error analysis.
- Every report cites the dataset and the paper.

## Layout

Keep it in the same repo, but as its own subpackage with its own storage. It must not
import from `mappa.collect` or touch the collector's SQLite database.

```
src/mappa/reader/
  datasets/app350.py   # load the raw files -> canonical format
  canonical.py         # Policy, Segment, Label, PolicyAnswer models (pydantic)
  mapping.py           # APP-350 practice -> project taxonomy
  baselines/keyword.py
  baselines/tfidf.py
  llm/client.py        # provider-agnostic client + response cache
  llm/prompt.py        # prompt builder, versioned
  extract.py           # segment -> claims (LLM), quote verification
  aggregate.py         # claims -> one answer per practice per policy
  evaluate.py          # metrics + report
config/app350_mapping.csv
data-public/           # gitignored: raw/, canonical/, runs/, llm_cache/
docs/DATA_APP350.md
docs/EVAL_READER_MVP.md
```

CLI group: `mappa reader ...` (commands listed per milestone).

## Milestones

Small steps (see CLAUDE.md). **Stop after every milestone**, report in at most 10
lines, and wait for Alex.

### R0 — Inspect the data (no pipeline code yet)

Write only a throwaway inspection script, then document what's actually in the zip in
`docs/DATA_APP350.md`:

- the file formats, folder layout and number of policies
- how paragraphs (segments) and their labels are stored, with one made-up example in
  the same shape (no real APP-350 text)
- the exact list of practice labels, and how "performed" vs "not performed" is
  recorded
- **whether the official split (188 train / 62 validation / 100 test) is included.**
  If it isn't, say so. We'll make a seeded split, record it in `eval/app350_split.json`,
  and state in every report that numbers aren't directly comparable with the paper's.
- from the paper: **at what level the published results are reported** (per paragraph
  or per policy) and **which metrics** (the paper's main table reports negative
  predictive value, specificity and negative F1, not ordinary F1). Only compare like
  with like.
- label counts per practice. Note which practices are too rare to evaluate reliably
  (for example, fewer than 10 positive policies in the test set).

**Stop.** Alex reviews DATA_APP350.md before any loader is written.

### R1 — Loader and canonical format

- `canonical.py`: `Policy(policy_id, segments)`, `Segment(idx, text, labels)`,
  `Label(practice, party, modality)` with modality `performed | not_performed`.
- `datasets/app350.py`: raw files → canonical JSONL in `data-public/canonical/`,
  plus the split file.
- **Per-policy gold answers** for each practice: `yes` if any paragraph says performed;
  `no` if paragraphs only say not performed; `not_mentioned` if neither; `conflict` if
  both.
- `config/app350_mapping.csv`: each APP-350 practice → project taxonomy
  (category, data_type, recipient first/third party), or `unmapped` with a reason
  (e.g. cookies and IMSI have no Data Safety equivalent). Alex reviews this file.
- Command: `mappa reader load`.
- Tests on made-up data: loading, per-policy combining (all four answers), split is
  deterministic.

### R2 — Baselines first (no API cost)

Getting numbers before spending money on an AI model:

1. **Keyword rules** (`baselines/keyword.py`): term lists per practice (e.g. "GPS",
   "precise location"), plus first/third-party cues ("we", "our" vs "third parties",
   "partners", "advertisers") and negation cues ("do not", "never", "will not") in the
   same sentence.
2. **TF-IDF + logistic regression** (`baselines/tfidf.py`): one classifier per practice,
   trained on the train split, decision threshold chosen on the validation split. This
   roughly repeats the original paper's approach (they used linear SVMs).

- Command: `mappa reader baseline --method keyword|tfidf --split val|test`.
- Results written to `data-public/runs/<run_id>/` (predictions JSONL + a metrics JSON).

### R3 — The AI engine

**What the model fills in, per paragraph:** a list of claims (often empty). Each claim:

| Field | Allowed values |
|---|---|
| `practice` | exactly one of the APP-350 practice names from R0 |
| `party` | `first_party`, `third_party` |
| `modality` | `performed`, `not_performed` |
| `quote` | the exact supporting sentence(s), copied word for word |

This uses APP-350's own vocabulary on purpose, so the output can be scored directly
against the labels. Mapping to the project taxonomy happens afterwards in `mapping.py`.
Task 02b switches to the general schema in `02_policy_reader.md`.

**Prompt rules:**
- List the practices with one-line definitions taken from the dataset documentation.
- Few-shot examples come **only from the train split**. Never validation, never test.
- "Return no claims if the paragraph describes none of these practices."
- "Negations are claims": "we never share your location" → `not_performed`.
- Temperature 0. Every prompt change gets a new `prompt_version`.
- Use the provider's structured-output or tool-calling feature to force the shape,
  **and** validate with pydantic anyway.

**Quote verification** (`extract.py`), for each claim:
1. exact match in the paragraph → `exact`
2. match after normalising whitespace, curly quotes and dashes → `normalized`
3. otherwise → rejected (`quote_not_found`). Rejected claims are counted for the
   made-up-answer rate and never used.

**Client and cache** (`llm/client.py`):
- One interface, swappable providers. Provider, model and settings come from config;
  the API key comes from the environment only.
- Cache key = provider + model + prompt_version + settings + paragraph hash + run
  number. A cache hit makes no API call, so re-runs cost nothing.
- `--dry-run` estimates tokens and cost from paragraph lengths using per-token prices
  Alex enters in config (never hardcoded). `--max-cost` stops before the budget is
  exceeded.
- **No test calls a live API.** Use recorded responses as fixtures (made-up text).

**Combining** (`aggregate.py`): verified claims → per-policy answer per practice
(yes / no / not_mentioned / conflict), the same rule as the gold answers in R1.

- Command: `mappa reader extract --split val|test [--runs N] [--dry-run] [--max-cost AUD]`.
- Develop and tune **only on the validation split**.

### R4 — Freeze, test once, report

1. Freeze the prompt version. Record it.
2. Run the test split **once per method**: keyword, TF-IDF, and the AI engine 3 times
   (for stability).
3. Write `docs/EVAL_READER_MVP.md`:

| Measure | What |
|---|---|
| Per-practice results | Precision, recall, F1 on per-policy answers; also negative F1 / NPV / specificity where comparing with the paper |
| Overall | Micro and macro averages, over practices with enough examples (R0) |
| Party split | First vs third party accuracy reported separately (where models usually struggle) |
| Negations | Accuracy on `no` (not performed) answers separately; they're rare |
| Made-up-answer rate | Share of AI claims whose quote couldn't be found |
| Stability | Share of answers identical across the 3 AI runs |
| Cost and time | Tokens, AUD and seconds per policy, per method |
| Comparison | Keyword vs TF-IDF vs AI vs published numbers, like-for-like only |
| Error analysis | 20 AI errors **from the validation split**, grouped by cause |
| Limits | Old (≈2018) US-centred policies; no health data in the label set; the model may have seen this public dataset during training, which could inflate scores |

The headline should read like: *"On APP-350's held-out test policies, the reader
reaches F1 = X (TF-IDF baseline Y; keyword Z), with W% of claims rejected for unverifiable
quotes."*

- Command: `mappa reader eval --run RUN_ID [--compare RUN_ID ...]`.

## Acceptance criteria

- [ ] DATA_APP350.md documents the real file contents, the split situation, and what the
      paper's published numbers measure
- [ ] Mapping file reviewed by Alex
- [ ] Both baselines and the AI engine evaluated on the same test split
- [ ] Test split used once per method, with the frozen prompt version recorded
- [ ] Every AI claim used in scoring has a verified quote
- [ ] Re-running with the same settings makes zero API calls
- [ ] EVAL_READER_MVP.md complete, including limits and error analysis
- [ ] No APP-350 text in git; no test calls a live API; `pytest`, `ruff`, `mypy` pass

## Phase 2 (after R4, separate approval): OPP-115

Same engine, OPP-115's broader scheme (115 website policies; categories, purposes,
data types, security, retention, user rights). Adds the claim types APP-350 lacks.
Separate loader, mapping file and evaluation section. Note: it's websites, not apps.

## Open decisions (Alex / supervisor — Claude Code must not decide these)

1. Which AI provider and model, who pays, and the budget cap
2. Is sending APP-350 policy text to an external AI service OK under the dataset licence
   and CSIRO policy? If not: a local open-weight model.
3. Confirm the APP-350 licence covers this CSIRO research use
