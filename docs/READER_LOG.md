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
