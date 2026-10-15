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
