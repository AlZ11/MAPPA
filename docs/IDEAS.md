# Ideas beyond the current task

Parked here so they don't creep into the task being built (CLAUDE.md: do not build beyond
the current task file).

- **Refuse placeholder contact addresses in the HTTP client (M1).** The config only checks
  that `contact_email` looks like an address, so tests can use `@example.org`. The client
  that sends real requests should also refuse reserved domains (`example.*`, `*.invalid`,
  `*.test`), so a real crawl can never go out with a fake contact.
- **`mappa blobs verify` before freezing a snapshot (M6).** Re-hash every blob and check
  every `raw_blob` pointer resolves; record the result in `snapshot_manifest.json`.
  `BlobStore.get` already verifies each blob it reads; this would check the whole store in
  one pass.
- **Compress blobs only if disk becomes a problem.** Hash the raw bytes, store them
  zstd-compressed. Decide after M4 measures real page sizes; not worth the complexity if a
  snapshot stays in the low gigabytes.
- **Warn when `data_dir` is inside the git repo but not ignored.** `.gitignore` covers
  `data/`, `*.apk` and `*.sqlite*`, but a custom data dir such as `snapshots/` inside the
  repo would not be covered.
