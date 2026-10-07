# Ideas beyond the current task

Parked here so they don't creep into the task being built (CLAUDE.md: do not build beyond
the current task file).

- **More search results per term, if the candidate pool is too small.** The first live
  run shows how many apps one results page holds. The Node `google-play-scraper` pages
  further, up to 250 per term, using a token in the page. Only worth the extra requests if
  the eligible pool falls well short of 1,000.
- **Fetch Data Safety pages over plain HTTP.** The label is embedded in the server's HTML,
  which is where the Node scraper reads it. If the saved fixtures confirm it, a plain HTTP
  fetch is faster than rendering and uses the same client as the other Google requests
  (`datasafety_fetcher = "http"`).
- **Rate-limit by registrable domain instead of hostname.** `a.example.com` and
  `b.example.com` currently count as different sites. Needs the Public Suffix List.
- **Compress blobs only if disk becomes a problem.** Hash the raw bytes, store them
  zstd-compressed. Decide after the dev run measures real page sizes.
- **Warn when `data_dir` is inside the git repo but not ignored.** `.gitignore` covers
  `data/`, `data-dev/`, `*.apk` and `*.sqlite*`, but not a custom directory name.
- **Done since first noted:** live requests refusing placeholder contact addresses
  (`collect/live.py`), and full blob verification before freezing (`reports/freeze.py`).
