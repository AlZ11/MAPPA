# Validation of parsed Data Safety labels (task M4)

Status: **not started.** It needs real labels. Before the snapshot runs, the parser is
first validated against pages saved by hand (`tests/fixtures/datasafety/`), and then on
20 random labels from the live dev run.

## Procedure

1. Pick 20 random apps with a fetched label from the snapshot, using a fixed seed (the
   coverage report's spot-check list can serve).
2. For each, open the label in a browser:
   `https://play.google.com/store/apps/datasafety?id=<app_id>&hl=en&gl=AU`.
3. Compare the page with the parsed rows (`label_facts`, `label_practices`,
   `label_status`):
   - every data type under "Data shared" and "Data collected", with its category,
     purposes and the "Optional" marker;
   - the headline statements ("No data shared with third parties", "No data
     collected", "has not provided information");
   - the security practices.
4. Record each app below. On any mismatch: save that page as a new fixture, add a test
   that reproduces the problem, fix the parser, bump `PARSER_VERSION`, re-run
   `mappa parse-datasafety`, and re-check. Repeat until all 20 match.

## Results

Parser version: _TBD_. Date checked: _TBD_. Checked by: _TBD_.

| # | App | Label status | Facts (page / parsed) | Practices (page / parsed) | Match | Notes |
|---|---|---|---|---|---|---|
| 1 | | | | | | |
| 2 | | | | | | |
| 3 | | | | | | |
| 4 | | | | | | |
| 5 | | | | | | |
| 6 | | | | | | |
| 7 | | | | | | |
| 8 | | | | | | |
| 9 | | | | | | |
| 10 | | | | | | |
| 11 | | | | | | |
| 12 | | | | | | |
| 13 | | | | | | |
| 14 | | | | | | |
| 15 | | | | | | |
| 16 | | | | | | |
| 17 | | | | | | |
| 18 | | | | | | |
| 19 | | | | | | |
| 20 | | | | | | |

Acceptance: all 20 match.
