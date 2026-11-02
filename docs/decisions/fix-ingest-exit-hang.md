# fix/ingest-exit-hang

Fixes **F4**: `python -m backend.datafeed.ingest --stdout` could hang, as on
#33's CI (2026-10-21), where `tests/datafeed/test_sources.py::test_entry_point_stdout`
hit pytest's 120 s timeout and a re-run passed. That command (without
`--stdout`) is the `feed` container's entry point. Decisions taken without
the owner, each with its alternative.

## Cause

Reproduced locally by running the test's exact command thousands of times, 12
at a time, on 8 cores, while the full test suite ran:
- **2 hangs in 2,000 runs** (and 2 more in an earlier loop). They hadn't ended
  after 10 h.
- The 3 bars had been printed. The process was **stuck in `exit()`**, after
  `main()` had returned.
- Native stacks (macOS `sample`), the same in both hung processes:
  - the main thread is in `Py_Exit` → `exit` → `__cxa_finalize_ranges` (C++
    static destructors) → `std::shared_ptr<arrow::internal::ThreadPool>`
    destructor → `ThreadPool::~ThreadPool` → `ThreadPool::Shutdown(wait=true)`,
    waiting on a condition variable;
  - the last Arrow worker thread is also waiting on a condition variable.
  - So the pool's shutdown and its worker each wait for the other: a deadlock
    inside pyarrow 25.0.0, at process exit.
- The pool exists because the replay source reads its parquet with
  `pd.read_parquet(..., filters=...)` (pyarrow's dataset reader, which starts
  Arrow worker threads).
- A `faulthandler.dump_traceback_later` watchdog never fired, which agrees
  with the stacks: interpreter finalization had already cancelled it.

**Production (`DATAFEED_SOURCE=ibkr`) isn't affected:** the ibkr source, the
bus and the recorder never import pyarrow (checked: after `build_source` in
ibkr mode, `pyarrow` and `pandas` aren't in `sys.modules`). The replay source
is affected: CI, local runs, and `deploy/datafeed`'s local stack. A hung
container never exits, so it is never restarted (crash-only depends on the
exit).

| # | Decision | Conservative because | Alternative |
|---|---|---|---|
| IE1 | **The entry point ends with `os._exit`** (`run()` in `backend/datafeed/ingest/__main__.py`). It runs after `logging.shutdown()` and a flush of stdout and stderr, so C++ static destructors, Arrow's thread pool teardown among them, never run. The exit status is the one Python would have given: `main()`'s return value; a `SystemExit`'s code (a message is printed to stderr and gives 1, as Python does); the traceback and 1 for any other exception. | Whatever Arrow's threads are doing, nothing at exit waits for them. The output and statuses are the same as before. The process is crash-only and holds nothing that needs atexit handlers: Redis needs no close, and the stdout JSON lines and the logs are flushed. | Avoid Arrow's thread pool. Measured: `use_threads=False` still starts 3 Arrow threads, so it narrows the race without removing it. Fixing Arrow, or pinning another pyarrow, is outside this repo and the two-week rule. |
| IE2 | **Ctrl-C** ends with status 130 and the traceback. Before, the process was killed by `SIGINT` (a shell shows 130 either way). `SIGTERM` (`docker stop`) is unchanged: Python has no handler for it, so the process is killed before any of this runs. | Same visible status. | Re-raise the signal. |
| IE3 | **Tests:** `test_run_exits_hard_with_pythons_status` checks the 7 ways `main()` can end (return 0 or another status, `sys.exit(2)` as argparse does, `sys.exit()`, `sys.exit("message")`, an exception, Ctrl-C), with `os._exit` faked. `test_entry_point_stdout` now passes `timeout=60` to `subprocess.run`, so a hang fails there, naming the command, instead of hitting pytest's 120 s. | Behaviour is tested at the function the entry point calls; the real command still runs end to end. | A stress test in CI (minutes per run, and still probabilistic). |

**Proof:** the real command (`python -m backend.datafeed.ingest --stdout`, the
test's environment and parquet), looped 12 at a time on 8 cores, each run with
a 20 s limit:
- `main`: **7 hangs in 5,000 runs** (0.14 %);
- this branch: **0 hangs in 10,000 runs**. At `main`'s rate about 14 were
  expected, so 0 by chance has a probability of about 1e-6.
- No other failures; the median run is 1.8 s on the branch and 2.0 s on `main`.

## Found on the way, not fixed

- **F6. Other processes that read parquet with pyarrow can hang at exit the
  same way:**
  - `python -m backend.scanner --date` / `--selftest`
    (`sources/replay.py` `load_data`);
  - `tools/sigma_calibration/calibrate.py`;
  - research scripts.
  - These are development-only: the live scanner (`--live`), the feed (ibkr)
    and the recorder never read parquet. CI's selftest step skips there,
    because the parquet isn't in git.
  - `deploy/scanner/export_parquet.py` runs in production (daily, from
    `backup.sh`, with no timeout), but it only writes parquet. A write
    started no Arrow threads when measured.
  - Fix, if wanted: the same `os._exit` ending for the scanner's `--date` and
    `--selftest` paths, in its own PR.
