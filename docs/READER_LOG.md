# Policy reader log (Task 02a)

One entry per milestone: what was built, every decision and why, results, and doubts.
Session of 2026-10-15: R0, R1, R2 and the offline parts of R3 done in one go, with
this log kept instead of stopping between milestones (Alex's instruction).

Dataset: APP-350 v1.1 (Zimmeck et al., *MAPS*, PoPETs 2019). No APP-350 text is in git.

---

## Before R0: housekeeping

- `data-public/` was already gitignored (`/data-public/`, added by Alex in `e38c09b`),
  so there was nothing to commit. I checked it instead: the zip, copied from the upload
  folder to `data-public/raw/APP-350_v1.1.zip`, is matched by `.gitignore:16`, and
  `git status --untracked-files=all` lists nothing under `data-public/`.
- **Draft code found:** uncommitted `src/mappa/reader/`, `tests/reader/`,
  `config/reader.toml`, and `scikit-learn` added to `pyproject.toml` / `uv.lock`, all
  timestamped 01:49-01:52 today. The zip arrived at 01:53, so this draft was written
  without seeing the data, most likely by an interrupted earlier attempt at this
  session. Decision: treat it as unreviewed; do R0 first; then keep, fix or replace each
  file against the real data. It's in my style and mostly sound, so most of it was kept
  (see R1).
- Also noticed: `e38c09b` committed `.DS_Store` files (macOS junk) at the repo root and
  in `docs/`. Not touched (outside the reader); listed for Alex.

---

## R0: inspect the data

**Built:** a throwaway inspection script (scratchpad only, not in the repo) and
`docs/DATA_APP350.md`, documenting files, format, labels, split, synthetic data, the
paper's metrics and per-practice counts.

**Findings that change the plan**
1. **The official split is included** (`policy_type`: 188 train / 62 validation /
   100 test). No seeded split; results can be compared with the paper on the same test set.
2. **Train and validation contain synthetic negated policies** (106 of 188 and 36 of 62
   carry `contains_synthetic: true`; test has none). The authors copied policies and
   rewrote positive statements as negative. This inflates `conflict` answers in
   train/validation (7-9 per synthetic policy, versus 0.1-0.3 in real ones).
3. **23 of 58 practices are too rare to evaluate** (fewer than 10 positive test
   policies): nearly all third-party fine-grained types. 35 practices are evaluable.
4. **Negative answers are rare in test:** 65 `no` answers across 20 practices, and only
   13 `conflict` answers in total.
5. The dataset ships `features.yml`, the authors' own keyword lists per practice,
   made from train + validation only.

**Decisions**
| Decision | Why |
|---|---|
| Use the official split from `policy_type`; no seeded split, no `eval/app350_split.json` | The brief says to use the official split if present. A seeded file would only be needed without it |
| Keep synthetic policies in train and validation, as the original authors did | Replicates the paper's setup, so the comparison stays like-for-like. Validation results are *also* reported on the 26 real policies, because synthetic text makes validation look different from test. Dropping synthetic data is a research choice for Alex |
| "Positive" for binary metrics = answer `yes` or `conflict` (the policy says "performed" somewhere) | A `conflict` policy does state the practice is performed; treating it as negative would hide real statements. Also used for the "≥ 10 positive test policies" rule |
| Rare-practice threshold: 10 positive test policies (the brief's example) | Fewer than 10 gives F1 values that swing wildly with one policy |
| Inter-annotator agreement noted per practice | 16 practices have alpha < 0.67; errors there partly reflect expert disagreement |

**Results:** see the per-practice table in `docs/DATA_APP350.md` §7. Totals: test has
1,131 positive (policy, practice) answers, 65 `no`, 13 `conflict`.

**Unsure about**
- **The paper's metrics and level:** its hosts are blocked here, so DATA_APP350.md §6
  relies on the brief (NPV / specificity / negative F1) and on reasoning (per-policy
  level). Needs checking against the PDF, along with whether the paper's "negative"
  includes `NOT_PERFORMED`.
- PyYAML isn't a project dependency yet; the inspection used a throwaway environment.
  The loader needs it (R1).

---

## R1: loader and canonical format

**Built:** a loader that reads APP-350's YAML straight from the zip (nothing extracted or
changed) and writes `data-public/canonical/app350.jsonl` plus `app350_split.json`. It
works out each policy's gold answer per practice (yes / no / not_mentioned / conflict),
maps the 58 APP-350 labels to our Data Safety taxonomy in `config/app350_mapping.csv`,
and adds `uv run python -m mappa.reader load`. Tests use made-up data only.

**Decisions**
| Decision | Why |
|---|---|
| CLI is `python -m mappa.reader load`, not `mappa reader load` | Wiring the group into `src/mappa/cli.py` changes collector code: a hard stop. It's a one-line `app.add_typer(...)` for Alex |
| A label is stored as `(practice, party)` + modality, e.g. `("Location_GPS", third_party)` | Party is reported separately in evaluation, and the AI fills it in as its own field. The loader converts both ways and refuses unknown names |
| `SSO` / `Facebook_SSO` (no party in APP-350) stored as `first_party` | The app developer receives the data. The party is ignored when converting back to a name, so this choice can't create a fake 3rd-party SSO label |
| Strict loader: an unknown label, modality or split stops the load | A silently skipped label would quietly change the answer key |
| `contains_synthetic` kept on each policy | So validation can be reported on all 62 policies and on the 26 real ones |
| Dropped the draft's seeded split (`split_seed`, `split_sizes`) | The official split is present (R0). The `Split` model records `source="official"`, `seed=None` |
| Mapping: 36 `mapped`, 8 `category_only`, 14 `unmapped` | `category_only` = APP-350's broad terms (e.g. `Location`, "unspecified location"), which match a Data Safety category but no single type. Unmapped: password, cookies, IMSI, IP address (as an identifier), mobile carrier, SSID/BSSID, both SSO labels, each with a reason |
| Bluetooth / Wi-Fi → Precise location; cell tower / IP → Approximate | APP-350's annotator instructions group them that way (fine- vs coarse-grained) |
| City / ZIP → Personal info / Address | The annotator instructions say Contact = static data, Location = dynamic data |
| Age, Gender → Personal info / Other info | Data Safety has no own type for them. Weakest mapping call |
| Mapping names checked against `mappa.parse.taxonomy` | Same vocabulary as the Data Safety parser, so they can't drift apart. That's a parser module, not `mappa.collect`, and it is imported, not changed |
| Added `pyyaml` (and `scikit-learn` for R2) to `pyproject.toml`; `yaml`/`sklearn` added to the mypy "untyped library" override | Needed to read the YAML. Only the dependency lists and the mypy override changed; listed for Alex because the file is shared with the collector |

**Results:** `load` on the real zip (about 19 s):

| split | policies | synthetic | segments | labels |
|---|---:|---:|---:|---:|
| train | 188 | 106 | 8,068 | 5,808 |
| validation | 62 | 36 | 2,651 | 1,887 |
| test | 100 | 0 | 4,788 | 2,506 |

Gold answers (policy × practice cells, 58 practices):

| split | yes | no | conflict | not_mentioned |
|---|---:|---:|---:|---:|
| train | 1,186 | 196 | 810 | 8,712 |
| validation | 388 | 68 | 319 | 2,821 |
| test | 1,118 | 65 | 13 | 4,604 |

Test totals match R0's independent count from the raw YAML (1,118 / 65 / 13). Train label
total 5,808 = 4,404 performed + 1,404 not performed, as in R0.

Checks: `ruff` and `mypy` (strict, 60 files) clean. `pytest`: 242 passed (22 of them new
reader tests). One pre-existing collector test fails in this container:
`test_browser_fetcher` needs Chromium build 1243, but the container has 1194. It also
fails on a clean checkout without my changes. Its leaked subprocess warning then fails
`test_cli::test_init_is_idempotent` when run in the same session; that test passes on
its own. No collector code touched.

**Unsure about**
- `Demographic_Age/Gender → Other info`: Google may intend "Other info" for something
  narrower. Alex to check against the Data Safety help page.
- `Identifier_IP_Address` unmapped vs `Device or other IDs`: Google's definition of
  device IDs doesn't clearly cover IP addresses. I chose unmapped and gave the reason.
- `Identifier` (broad) → `Device or other IDs` category only: could also be "User IDs"
  under Personal info.

---

## R2: baselines (no API cost), validation only

**Built:** two baselines: keyword rules (the dataset authors' term lists + party and
negation cues per sentence) and TF-IDF + logistic regression (one classifier per
practice, trained on train, threshold chosen on validation). Also a scoring report, one
folder per run in `data-public/runs/<run_id>/` (predictions, metrics, run record with
commit + input hashes), and `python -m mappa.reader baseline --method keyword|tfidf --split val|test`.
**The test split was not run.** `--split test` needs `--final` and refuses a second test run per method.

**Decisions**
| Decision | Why |
|---|---|
| Keyword term lists = APP-350's `features.yml` (Story et al., PAL 2019), read from the zip at runtime | Writing 29 lists myself while watching validation scores would be tuning on validation by hand. The authors' lists were made from train + validation (never test). Never committed (licence) |
| Party and negation cues = the brief's examples (+ contractions), in code | The brief specifies them. `features.yml` also has richer party/modality cue lists; not used, to keep the baseline as the brief describes it |
| A sentence with both a 1st- and a 3rd-party cue gives *both* claims | An annotator labels both. Known side effect: "we never sell X to advertisers" also gives a first-party negation |
| SSO: one `features.yml` list for `SSO` + `Facebook_SSO`; "facebook" in the sentence adds `Facebook_SSO` | In train, 61 of 86 Facebook-login paragraphs are also labelled `SSO` |
| TF-IDF: word 1–2-grams, `min_df=2`, sublinear tf; logistic regression, `class_weight=balanced`; positive paragraph = labelled *performed* | Standard, explainable defaults; not tuned. So TF-IDF answers only `yes` / `not_mentioned` (never `no` / `conflict`), as the brief's "one classifier per practice" implies |
| Practices with < 3 positive train paragraphs get no classifier (always `not_mentioned`) | 5 practices: IMSI (both), SIM serial 3rd, SSID/BSSID (both). None is evaluable |
| Thresholds from a fixed grid 0.05…0.95; **default mode `global`** (one threshold, chosen 0.65) | A split-half check on validation (tune on 31 policies, score the other 31, 20 splits) gave held-out micro F1 **0.770 global vs 0.748 per-practice** (macro 0.678 vs 0.657). Per-practice thresholds overfit 62 policies. Proposed as the frozen R4 setting; Alex to confirm |
| Overall averages over the 35 evaluable practices, fixed in code from R0's counts (`EVALUABLE_NAMES`) | So R2/R3 never read test labels to decide what to score |
| Validation reported on all 62 policies and on the 26 real (non-synthetic) ones | Synthetic rewrites make validation unlike test (no synthetic policies there) |

**Results (validation; TF-IDF and keyword numbers are in-sample, see caveats)**

| Method | Subset | Policies | P | R | F1 | Macro F1 | NPV | Specificity | Neg. F1 | F1 1st party | F1 3rd party | 4-way exact |
|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| keyword | all | 62 | 0.713 | 0.843 | 0.772 | 0.700 | 0.926 | 0.854 | 0.888 | 0.807 | 0.640 | 0.777 |
| keyword | real | 26 | 0.712 | 0.802 | 0.754 | 0.695 | 0.922 | 0.879 | 0.900 | 0.792 | 0.600 | 0.827 |
| tfidf-global | all | 62 | 0.745 | 0.810 | 0.777 | 0.671 | 0.915 | 0.881 | 0.897 | 0.795 | 0.685 | 0.724 |
| tfidf-global | real | 26 | 0.719 | 0.785 | 0.750 | 0.685 | 0.917 | 0.885 | 0.901 | 0.769 | 0.667 | 0.842 |
| tfidf-per-practice | all | 62 | 0.796 | 0.827 | 0.811 | 0.731 | 0.924 | 0.908 | 0.916 | 0.826 | 0.733 | 0.745 |
| tfidf-per-practice | real | 26 | 0.779 | 0.798 | 0.788 | 0.724 | 0.924 | 0.916 | 0.920 | 0.804 | 0.690 | 0.863 |

*Per-practice thresholds were tuned on these same 62 policies, so that row is inflated (see split-half check above). P/R/F1 count `yes` + `conflict` as positive; NPV / specificity / negative F1 are the paper's metric family, computed on the same per-policy answers.*

`no` and `conflict` handling (all 58 practices):

| Method | Subset | gold `no` | predicted `no` exactly | not predicted as disclosed | gold `conflict` | predicted `conflict` exactly |
|---|---|---:|---:|---:|---:|---:|
| keyword | all | 68 | 20 (0.294) | 38 (0.559) | 319 | 188 (0.589) |
| keyword | real | 24 | 14 (0.583) | 16 (0.667) | 4 | 4 (1.000) |
| tfidf-global | all | 68 | 0 (0.000) | 45 (0.662) | 319 | 0 (0.000) |
| tfidf-global | real | 24 | 0 (0.000) | 13 (0.542) | 4 | 0 (0.000) |
| tfidf-per-practice | all | 68 | 0 (0.000) | 51 (0.750) | 319 | 0 (0.000) |
| tfidf-per-practice | real | 24 | 0 (0.000) | 17 (0.708) | 4 | 0 (0.000) |

Per practice (F1, evaluable practices, all 62 validation policies):

| Practice (val, all 62) | val positives | F1 keyword | F1 tfidf-global | F1 tfidf-per-practice |
|---|---:|---:|---:|---:|
| `Contact_1stParty` | 19 | 0.623 | 0.865 | 0.865 |
| `Contact_Address_Book_1stParty` | 16 | 0.732 | 0.839 | 0.839 |
| `Contact_City_1stParty` | 7 | 0.737 | 0.250 | 0.444 |
| `Contact_E_Mail_Address_1stParty` | 46 | 0.903 | 0.968 | 0.968 |
| `Contact_E_Mail_Address_3rdParty` | 8 | 0.235 | 0.333 | 0.333 |
| `Contact_Password_1stParty` | 21 | 0.808 | 0.780 | 0.800 |
| `Contact_Phone_Number_1stParty` | 34 | 0.805 | 0.849 | 0.857 |
| `Contact_Postal_Address_1stParty` | 29 | 0.690 | 0.774 | 0.787 |
| `Contact_ZIP_1stParty` | 6 | 1.000 | 0.667 | 0.667 |
| `Demographic_1stParty` | 13 | 0.813 | 0.632 | 0.720 |
| `Demographic_3rdParty` | 5 | 0.400 | 0.667 | 0.769 |
| `Demographic_Age_1stParty` | 20 | 0.682 | 0.651 | 0.655 |
| `Demographic_Age_3rdParty` | 2 | 0.000 | 0.400 | 0.400 |
| `Demographic_Gender_1stParty` | 19 | 0.842 | 0.632 | 0.667 |
| `Facebook_SSO` | 15 | 0.333 | 0.778 | 0.815 |
| `Identifier_1stParty` | 9 | 0.450 | 0.200 | 0.560 |
| `Identifier_Ad_ID_1stParty` | 11 | 0.833 | 0.667 | 0.667 |
| `Identifier_Ad_ID_3rdParty` | 7 | 0.444 | 0.364 | 0.471 |
| `Identifier_Cookie_or_similar_Tech_1stParty` | 44 | 0.978 | 0.977 | 0.989 |
| `Identifier_Cookie_or_similar_Tech_3rdParty` | 31 | 0.912 | 0.833 | 0.909 |
| `Identifier_Device_ID_1stParty` | 37 | 0.917 | 0.877 | 0.877 |
| `Identifier_Device_ID_3rdParty` | 14 | 0.609 | 0.710 | 0.741 |
| `Identifier_IMEI_1stParty` | 6 | 0.923 | 0.364 | 0.571 |
| `Identifier_IP_Address_1stParty` | 42 | 0.929 | 0.921 | 0.921 |
| `Identifier_IP_Address_3rdParty` | 16 | 0.435 | 0.684 | 0.703 |
| `Identifier_MAC_1stParty` | 9 | 0.900 | 0.667 | 0.727 |
| `Identifier_Mobile_Carrier_1stParty` | 11 | 0.643 | 0.480 | 0.600 |
| `Location_1stParty` | 49 | 0.950 | 0.926 | 0.938 |
| `Location_3rdParty` | 14 | 0.765 | 0.629 | 0.720 |
| `Location_Bluetooth_1stParty` | 10 | 0.737 | 0.519 | 0.625 |
| `Location_Cell_Tower_1stParty` | 17 | 0.903 | 0.703 | 0.750 |
| `Location_GPS_1stParty` | 19 | 0.900 | 0.711 | 0.778 |
| `Location_IP_Address_1stParty` | 14 | 0.441 | 0.688 | 0.786 |
| `Location_WiFi_1stParty` | 13 | 0.846 | 0.703 | 0.846 |
| `SSO` | 21 | 0.385 | 0.773 | 0.816 |

**Reading the numbers**
- The two baselines are close overall (micro F1 ≈ 0.77–0.78 on all validation; ≈ 0.75 on
  real policies). TF-IDF is better on third-party practices (0.685 vs 0.640) and on SSO;
  keywords are better on specific technical terms (GPS, IMEI, cell tower, MAC).
- Keyword SSO / Facebook_SSO are low (F1 0.39 / 0.33): precision 1.0, recall ~0.25. The
  authors' SSO terms are long specific phrases that rarely match. `features.yml` was
  written as classifier features, not as standalone rules, so this baseline under-uses it.
- Negations: TF-IDF can never answer `no` (0 of 68). Keywords get 20 of 68 exactly right
  (14 of 24 on real policies).
- Third-party practices are the weak spot for both, as the brief expected.

**Unsure about**
- Validation numbers are optimistic for both baselines: the keyword lists were written
  with validation in view, and the TF-IDF threshold is chosen on validation. Only R4's
  test run gives clean numbers.
- I have not checked these against the paper's published per-practice numbers. Its PDF is
  blocked here (R0), and comparing with test-set numbers would be like-for-unlike anyway.
- The `global` vs `per_practice` default is a method choice made on validation evidence;
  flagged for Alex before R4.

---

## R3 (offline parts only): the AI engine, no live calls

**Built:** everything in the AI engine except a real provider:
- the versioned prompt (`app350-v1`), with definitions and train-only few-shot examples loaded at runtime;
- a schema that forces the reply shape, plus pydantic validation;
- quote verification (exact / normalised / rejected);
- a cache that makes re-runs free, plus budget stop and `--dry-run` cost estimate;
- the per-paragraph loop and the per-policy combining step;
- `python -m mappa.reader extract --split val|test [--runs N] [--dry-run] [--max-cost AUD]`.

No provider is registered, so a real run refuses with "nothing was sent". Tests use recorded, made-up replies.

**Decisions**
| Decision | Why |
|---|---|
| The model outputs 30 practice names (`Location_GPS`, `SSO`...) with `party` as a separate field; its schema `enum` lists only those 30 | As the brief's claim table specifies. A reply outside the list fails pydantic validation even if a provider ignores the schema |
| Definitions: the 30 one-liners from the dataset's top-level README table, read from the zip at each run | The brief says "definitions from the dataset documentation". Never committed (licence). Loader refuses if any is missing |
| `SSO` / `Facebook_SSO`: the prompt says "always first_party"; code folds any stray `third_party` to first | APP-350 has no party for them (R1 convention); a stray party can't create a label that doesn't exist |
| Few-shot: 4 train paragraphs, one each of first-party, third-party, negation, no-practice; chosen at random with seed 0; only real (non-synthetic) policies, 40–300 characters; the quote = the whole paragraph | Covers each behaviour the rules ask for. Test has no synthetic text. APP-350 labels paragraphs, not sentences, so a whole short paragraph is the only quote the labels support. Chosen: policies 202, 198, 140, 181 (all train). `build_prompt` refuses any non-train example |
| Cache key = provider + model + prompt version + temperature + max tokens + **system-prompt hash + schema hash** + paragraph hash + run number | Brief's key plus two hashes: definitions and examples are loaded at runtime, so the version string alone can't prove two prompts are identical |
| Identical paragraph text → one call per run (shared cache key) | Follows from keying on the paragraph hash. Only 6 of 2,651 validation paragraphs are repeats, so this barely matters |
| A failed call or an off-schema reply → paragraph `failed`, counted in `stats.segments_failed`; its claims are never "none" | CLAUDE.md principle 4. The policy's answers then come from the paragraphs that succeeded; the failure count is in the run record |
| `BudgetExceeded` stops the run (record stays `started`); finished paragraphs are cached, so re-running continues for free | Principle 5 (resumable); `--max-cost` is checked before each call using the worst case (max output tokens) |
| `--dry-run` reports token counts even without prices | Prices aren't set (open decision 1). Token counts let Alex price any provider |
| `--split test` needs `--final` even for a dry run | So test policies aren't read for anything but the one final run (hard stop) |
| Metrics per AI run, plus made-up-answer rate (rejected / all claims) and stability (share of answers identical across runs) | The brief's R4 table; computed now so R4 is just running it |

**Results: `--dry-run`, validation split (estimate, 4 characters/token, 150 output tokens/paragraph)**

| | 1 run | 3 runs (stability) |
|---|---:|---:|
| Policies / paragraphs | 62 / 2,651 (2,645 distinct) | same |
| Calls | 2,645 | 7,935 |
| Input tokens | ~5.40 M | ~16.19 M |
| Output tokens | ~0.40 M | ~1.19 M |
| Cost (AUD) | **prices not set** | **prices not set** |

Cost = input Mtok × input price + output Mtok × output price, so roughly
`5.40 × input + 0.40 × output` AUD per validation run at per-million-token prices.

**Key finding:** the system prompt (definitions + 4 examples) is ~1,940 tokens and is
repeated in every call. That's **95% of input tokens**; the paragraphs themselves are only
~0.26 M tokens. Two ways to cut cost, neither done (both need Alex's choice):
1. **provider prompt caching** (cheaper repeated prefix): no change to the method;
2. **several paragraphs per call**: changes the method; needs a new prompt version.

Checks: `ruff`, `mypy` (strict, 62 files) clean; `pytest` 275 passed (69 reader tests),
with the one container-only browser test excluded as in R1. A real
`extract --split val` refused with "no AI provider/model set ... nothing was sent", and
no `llm_cache/` was created.

**Unsure about**
- The 4-character-per-token estimate and 150 output tokens per paragraph are guesses.
  Most paragraphs have no claims (short reply), so output is probably overestimated. A
  10-paragraph pilot run would calibrate both.
- Whole-paragraph quotes in examples teach the model that long quotes are fine. The
  rules ask for "the exact supporting sentence(s)", and the examples are short, but a
  pilot should check quote length.
- No error analysis or accuracy for the AI engine yet: that needs live calls.

---

## For Alex to review

Stopped here: the next step (any AI call) is a hard stop. Ordered by what blocks progress.

**Decisions only you / your supervisor can make (block R3 live runs)**
1. **Provider, model, who pays, budget cap** (open decision 1). Then fill `[llm]` and
   `[prices]` in `config/reader.toml`, and set the key's environment variable name in `api_key_env`.
   One provider adapter is then needed in `engine.PROVIDERS` (small, but it means a live call to verify).
2. **Licence: may APP-350 text be sent to an external AI service?** (open decisions 2–3).
   If not: a local open-weight model.
3. **Prompt caching / batching** to cut the ~95% repeated-prompt cost (R3 key finding).

**Please check**
4. **The paper's metrics and level** (DATA_APP350.md §6): blocked here. Confirm that its main table is
   per-policy NPV / specificity / negative F1, and what counts as "negative".
5. **`config/app350_mapping.csv`**, especially:
   - Age/Gender → Other info;
   - Identifier_IP_Address unmapped;
   - broad `Identifier` → Device or other IDs;
   - City/ZIP → Address (R1 "Unsure about").
6. **Synthetic data**: kept in train/validation (like the paper); validation is also reported on the 26 real
   policies. Dropping them from training is a research choice.
7. **TF-IDF threshold mode for R4**: I propose `global` (split-half evidence in R2).
   Confirm before the test run.
8. **Using `features.yml`** (the authors' term lists) as the keyword baseline's terms:
   cite Story et al., PAL 2019. The alternative is hand-written lists.
9. **Few-shot choice** (4 examples, seed 0, real train policies only) and whole-paragraph quotes in them.
10. **SSO party convention**: stored as first party, party ignored.

**Changes outside `src/mappa/reader/` (please approve)**
11. `pyproject.toml` / `uv.lock`: added `pyyaml` and `scikit-learn`, plus `yaml` / `sklearn` in the
    mypy override. `config/reader.toml` and `config/app350_mapping.csv` are new files.
12. **`mappa reader` hookup**: commands run as `uv run python -m mappa.reader ...`.
    Adding `app.add_typer(reader_app, name="reader")` to `src/mappa/cli.py` is one line, but
    it's collector code, so I didn't touch it.

**Housekeeping noticed (not touched)**
13. `.DS_Store` files were committed in `e38c09b` (repo root, `docs/`). Suggest `git rm --cached`
    them and add `.DS_Store` to `.gitignore`.
14. `tests/test_browser_fetcher.py` fails in this cloud container (Playwright wants Chromium
    1243, but 1194 is installed). It's not a code problem, but it also breaks
    `test_cli::test_init_is_idempotent` in the same run, through a leaked-subprocess warning.

**Data hygiene check:** no APP-350 text is in git. Tests use made-up sentences, committed
results are aggregate numbers and label names, and definitions, `features.yml` and few-shot
paragraphs are read from the zip at runtime. Raw zip, canonical JSONL and runs stay in
the gitignored `data-public/`.
