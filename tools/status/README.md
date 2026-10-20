# tools/status: the status board

One place to see whether the scanner stack is healthy: green / yellow / red
for each of ten components, with the reason.

```
watchdog.sh (cron, every 5 min)
  ├─ measures: gateway probe, containers, Redis PING, disk, memory, backup age, recorder lag …
  ├─ probes/journal_stats.py  ── docker compose exec -T scanner  python - <  (journal, last bar, last alert)
  ├─ probes/stream_stats.py   ── docker compose exec -T recorder python - <  (newest live bar in the stream)
  └─ python3 -m tools.status.write --out ~/scanner-health.json key=value …
          evaluate.py + thresholds.toml  →  health.json (atomic write)

python3 -m tools.status --once    one line per component (stdlib only)
python -m tools.status            Textual board, refreshes every 5 s (needs textual)
```

## Components

| Component | Green when | From |
|---|---|---|
| gateway | the API handshake works | the watchdog's probe (clientId 996) |
| feed | the newest live bar is recent (market-open minutes) | stream mode: the newest non-history entry in `bars.xauusd.1m`; ibkr mode: the scanner's own last bar |
| redis | PING answers | stream profile only, else OFF |
| recorder | the store's lag is small and `rejected_messages` hasn't grown in 24 h | stream profile only, else OFF |
| scanner | the container runs, its heartbeat is healthy, no recent restarts, the last bar is recent | docker + the journal's `last_live_bar_ts`; the reason also names the last alert |
| journal | readable; no alert of today waiting > 60 min for a reply | today's first decisions: TAKE, SKIP, MISSED, unanswered |
| backups | the last successful backup is < 25 h old | `~/.scanner-backup` (`backup.sh`); yellow if `BACKUP_S3_URI` isn't set |
| disk | < 75 % used | highest of `/` and Docker's data dir |
| memory | ≥ 20 % available | `/proc/meminfo`, with swap used |
| agents | OFF: not deployed yet | reserved: an agents runner will report its last run |

**Thresholds** are all in [`thresholds.toml`](thresholds.toml), with a comment
per value. The watchdog's Telegram alert thresholds are the `red` values in
that file. `watchdog.sh` keeps the same numbers as a fallback, and
`test_status_config.py` keeps the two equal.

**Rules:**
- A threshold is inclusive (≥), except memory, which is red when strictly
  below.
- A value the watchdog couldn't measure, or sent malformed, is **red**
  ("no data: …"), never green.
- Bar ages are counted in **market-open minutes**, so a weekend or the daily
  break never turns the board yellow.
- A component that isn't part of this deployment shows **OFF**: Redis and the
  recorder without the stream profile, and agents for now.
- **Stale or missing `health.json`:** the reader shows every component red,
  with a banner. Stale means older than `stale_after_s`, 15 min, i.e. three
  missed watchdog runs. A missing, invalid, truncated or oversized file is
  treated the same way. Nothing in the reader raises.

## Run

On the host, from the repo root (`~/oparli`):

```bash
python3 -m tools.status --once                 # needs Python >= 3.11 (tomllib); no packages
python3 -m tools.status --once --no-color      # or NO_COLOR=1, or TERM=dumb; piped output is never coloured
echo $?                                        # 0 all green, 1 something yellow, 2 something red

# the board (once): a venv with only textual and its dependencies, hash-locked
python3 -m venv ~/status-venv
~/status-venv/bin/pip install --require-hashes -r tools/status/requirements-status.lock
~/status-venv/bin/python -m tools.status       # q quits, r refreshes now
```

From your machine: `ssh -t ubuntu@<host> 'cd oparli && ~/status-venv/bin/python -m tools.status'`.

`--file PATH` reads another `health.json`; the default is `$HEALTH_JSON`, else
`~/scanner-health.json`. `--interval S` changes the refresh period (default 5).

**Writing** is done by `watchdog.sh`, nothing else: there's no extra cron. To
write by hand:

```bash
python3 -m tools.status.write --out ~/scanner-health.json now=$(date +%s) gateway_ok=1 …
```

Every fact is listed in `evaluate.py`'s docstring.

## Cost (measured 2026-10-17, macOS, Python 3.13)

| What | How often | Time | Memory |
|---|---|---|---|
| `tools.status.write` | once per watchdog run (5 min) | ≈ 40 ms wall | ≈ 20 MB peak RSS, then exits |
| each probe (`docker compose exec … python -`) | once per run | well under a second (a Python start in the container) | the container's own |
| the Textual board | while you watch it | ≈ 0.4 ms per refresh; ≈ 0.3 % of one core idle at the 5 s refresh | ≈ 40 MB RSS |

`health.json` is about 1.7 kB.

## Tests

```bash
python -m pytest tests/status_tests tests/deploy/test_watchdog_health.py -q
```

- `test_status_config`: the file and its validation, and that the
  watchdog's fallback numbers match it;
- `test_status_evaluate`: every threshold at its boundary, and missing or bad
  facts;
- `test_status_io`: the atomic writer, the reader on missing, invalid, stale,
  future-dated and huge files, and `--once` output and exit codes, with and
  without colour;
- `test_status_tui`: the board, headless (Textual `run_test`): refreshes, and
  no crash on bad input;
- `test_status_probes`: both probes, on real temporary SQLite journals and
  XREVRANGE-shaped data;
- `test_status_lock`: the status-only lock matches `requirements.lock`;
- `tests/deploy/test_watchdog_health.py`: the watchdog with stub
  docker/curl, writing `health.json` and reading its thresholds from the
  TOML, and keeping going when the tooling is broken.

No test uses the network, Docker, IBKR or Redis.
