# feat/data-manifest

Ops job 3 of 2026-10-20 has three parts: a data manifest (source, cleaning
steps, date range, SHA-256) for `XAUUSD.txt` and the clean parquet; loaders
that verify the hash; and a small committed fixture, so that tests and the
scanner selftest run in CI.

This branch does the manifest, the check and the rebuild. **Three parts wait
for the owner**: committing real bars, the scanner's loader, and CI (DM7–DM9).
These are the decisions I took without the owner, each with its alternative.

| # | Decision | Conservative because | Alternative |
|---|---|---|---|
| DM1 | **The manifest is `backend/datafeed/store/manifest.json`, shipped as package data.** `store/manifest.py` reads it; the datafeed README had planned that module. Keys are paths under `database/`. | It is in every image that has the datafeed (the replay container reads `/parquet/...`, where `database/` doesn't exist). | `database/MANIFEST.json` (not in any image). |
| DM2 | **Two hashes.** `sha256` is of the file's bytes, and loaders check it. `content_sha256` (parquet only) is of the bars: timestamps as int64 ns UTC, then open, high, low, close and volume as float64 little-endian, in timestamp order. With it, a rebuilt file can be checked, since parquet writers don't reproduce bytes. | A byte check catches any change; the content check makes the rebuild verifiable. | Bytes only (a rebuild could never be verified). |
| DM3 | **Files are matched by name.** A file named like a listed one must match it, or the loader raises `ManifestError`. Other names (test fixtures, scratch extracts) aren't checked. The byte hash is computed once per process per unchanged file (realpath, size, mtime). | The real files can't be swapped silently, and the tests' small parquets keep working. | Check by path (a copy elsewhere would escape the check), or require every parquet to be listed (breaks every synthetic test). |
| DM4 | **Source of `XAUUSD.txt`: `vendor: null`.** Nothing in the repo records it: database/README says "a one-off download". The manifest records what is known: the format (including the stray header prefix "should contai" and the CRLF line ends), the UTC finding of DATA_AUDIT_LONG.md, the known issues and the file's mtime. **The owner needs to fill in the vendor** (question 1). | No invented provenance. | A guess from the file format. |
| DM5 | **The parquet's cleaning steps were reconstructed and verified, not assumed.** On 2026-10-22, all 2,156,239 parquet rows equal the `XAUUSD.txt` rows with the same timestamp in all five values, with no row missing either way. `tools/data/build_clean_parquet.py` applies that rule: skip the header, parse as UTC, keep rows from 2020-01-01 on, sort, and stop on a duplicate or a bad bar (the kept range has none). It reproduces `content_sha256` (24 s). | The "script that isn't in git" now is, and is checked against the original. | Record "unknown" for the cleaning. |
| DM6 | **Which loaders verify.** The datafeed's `ReplaySource` checks before reading (0.1 s for the parquet). `build_clean_parquet.py` checks its input and `make_fixture.py` its source. Nothing else changed:<br>- **The scanner's `load_data` (selftest, `--date`) is unchanged, because the scanner is on hold.**<br>- The research loaders (`audit_long.py`, r3) are unchanged: studies stay as they were run.<br>Instead, `python -m backend.datafeed.store.manifest check` checks every listed file on a machine. | It respects the scanner hold and leaves study code alone. | Hook the scanner and research loaders now. |
| DM7 | **The fixture isn't committed.** The repo is public and the vendor's terms are unknown (DM4). Once pushed, bars stay in GitHub's history, which can't be undone. The GEX study kept exchange data out of git for the same reason (GF10). `tools/data/make_fixture.py` cuts the selftest's three windows: 2023-06-01..20, 2024-03-04..14 and 2025-05-30..06-17, all before the seal. That is 45,440 bars, 1.2 MB. **Checked: the selftest passes on that fixture** (`SELFTEST: ALL PASSED`, with the loader pointed at it from outside, no code change). Dropping the 2024+ windows makes it fail, so the fixture really carries the test. "A few days" became about 32: case 1 builds weekly levels, and one case warms up on 14 days. | Nothing irreversible before the owner confirms redistribution is allowed. | Commit it now. |
| DM8 | **The selftest doesn't read the fixture yet.** That needs the scanner's loader to take a path, for example `SCANNER_PARQUET`, defaulting to today's file. That is a scanner change, and the scanner is on hold. | The hold. | Change `backend/scanner/sources/replay.py` now. |
| DM9 | **CI is unchanged.** Running the selftest on the fixture in CI needs a `ci.yml` edit, and CI edits are the owner's (auto mode blocks them). | Standing rule. | none |

## Owner questions

1. **Where does `XAUUSD.txt` come from** (vendor or product, and roughly
   when)? It goes into `source.vendor`.
2. **May a few weeks of those bars be in a public repo?** If yes:
   - run `python tools/data/make_fixture.py --out tests/fixtures/xauusd_selftest.parquet`;
   - add its entry (the tool prints it) to the manifest;
   - un-ignore that one file in `.gitignore`.
   If not: a private fixture via a CI secret or artifact, or a synthetic one
   (which can't stand in for the real-data cases).
3. **When the scanner hold lifts:** should `load_data` verify the manifest and
   take `SCANNER_PARQUET`? Then CI runs
   `SCANNER_PARQUET=tests/fixtures/xauusd_selftest.parquet python -m backend.scanner --selftest`
   in place of today's skip-with-warning (your `ci.yml` edit).
