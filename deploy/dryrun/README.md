# deploy/dryrun: BTC through the whole pipeline, next to production

The dry run sends BTC (24/7) through the stream path end to end:

**feed → Redis → recorder → scanner (stream mode) → journal + Telegram**

Its purpose is to exercise all of that while gold is closed. Production keeps
running and is never stopped, rebuilt or reconfigured.

| | Production (`~/oparli`, project `scanner`) | Dry run (`~/oparli-dryrun`, project `dryrun`) |
|---|---|---|
| Gateway | `ibgateway` | **none**: the feed uses production's gateway, read-only, client id **44** |
| Redis | production's own | its own, reached as `dryrun-redis` |
| Journal and store | `scanner_*` volumes | `dryrun_*` volumes |
| Stream | `bars.xauusd.1m` | `bars.btcusd.1m` |
| Telegram | production bot | a **separate bot**; every message starts `[DRYRUN]` |
| Image | production's | `oparli-scanner:dryrun` |
| S3 | `…/scanner` | `…/dryrun` (backups only if you run them) |
| Memory limits | unchanged | 96 + 192 + 128 + 320 = 736 MB |

**Why it is built this way:**
- **No gateway service.** A second IB Gateway would log in a second session,
  and that drops production's.
- **A separate bot.** Two processes long-polling one bot (`getUpdates`) take
  each other's updates. The dry-run scanner would then eat your production
  TAKE/SKIP replies. `check_env.sh` refuses production's token.

## Step 1: is BTC available? (on your Mac; nothing is installed on EC2)

```bash
cd ~/Desktop/algo_project.worktrees/instrument-config      # or any checkout of this branch
ssh -i <your-key.pem> ubuntu@<IP> \
  'cd ~/oparli/deploy/scanner && docker compose run --rm -T --no-deps --entrypoint python scanner - --host ibgateway --port 4004' \
  < tools/ibkr_instrument_check.py | tee ~/btc_check.json
```

- **What it is:** a one-off container of production's scanner image. With
  `--entrypoint python` the scanner itself never starts.
- **What it does:** a read-only connection with client id 43, which refuses a
  non-paper port or a non-`DU` account. It asks for the contract details, 30
  min of 1-min history for each `whatToShow`, and 30 s of live ticks.
- **Go on only if** the `verdict` is `available`. Note which `whatToShow`
  values returned bars. If `MIDPOINT` has none, set `INSTRUMENT_WHAT_TO_SHOW` in
  `.env` below to one that does, e.g. `AGGTRADES`.

The **"10 D" check** (does `durationStr "10 D"` mean calendar or trading
days?) runs the same way, with client id 41. It is best run on a Thursday or
Friday:

```bash
ssh -i <your-key.pem> ubuntu@<IP> \
  'cd ~/oparli/deploy/scanner && docker compose run --rm -T --no-deps --entrypoint python scanner - --host ibgateway --port 4004' \
  < tools/ibkr_duration_check.py
```

## Start (on EC2)

```bash
ssh -i <your-key.pem> ubuntu@<IP>
free -m                                  # want >= 1 GB "available" before starting

# a separate checkout; production's ~/oparli stays on its branch, untouched
git clone --branch feat/instrument-config "$(git -C ~/oparli remote get-url origin)" ~/oparli-dryrun
cd ~/oparli-dryrun/deploy/dryrun
cp .env.example .env && chmod 600 .env
nano .env                                # the dry-run bot's token + your chat id; INSTRUMENT_WHAT_TO_SHOW from step 1

./check_env.sh                           # must end with "all checks passed"
docker compose config --quiet && docker compose build
docker compose up -d
```

## Watch

```bash
cd ~/oparli-dryrun/deploy/dryrun
docker compose ps                        # redis -> feed -> recorder become healthy; scanner healthy after ~5 min
docker compose logs --tail 30 feed       # "ibkr: N history bars published, streaming BTCUSD"
docker compose logs --tail 30 scanner    # "warm-up via store: ..." then "LIVE (Telegram): scanning BTCUSD from stream bars.btcusd.1m ..."
docker stats --no-stream                 # every dryrun-* container well under its limit
# the dry-run journal, read-only:
docker compose exec -T scanner python -c "import sqlite3; c = sqlite3.connect('file:/data/scanner_decisions.sqlite?mode=ro', uri=True); print(c.execute('SELECT status, COUNT(*) FROM alerts GROUP BY status').fetchall(), c.execute('SELECT decision, COUNT(*) FROM decisions GROUP BY decision').fetchall())"
# production, unchanged:
cd ~/oparli/deploy/scanner && docker compose ps
```

Alerts arrive from the dry-run bot as `[DRYRUN] A2026…`. Reply to that bot with
the usual `<id> TAKE long <entry> sl <sl> tp <tp> <reason>` or
`<id> SKIP <reason>`.

**Optional backup** of the dry run to `…/dryrun`. It uses production's
`backup.sh`, pinned to the dry-run project, with its own state file
`~/.dryrun-backup`:

```bash
~/oparli-dryrun/deploy/dryrun/backup.sh
```

## Stop and remove completely

```bash
cd ~/oparli-dryrun/deploy/dryrun
docker compose down --volumes            # dry-run containers, its network, its 3 volumes (journal, store, Redis)
docker image rm oparli-scanner:dryrun
cd ~ && rm -rf ~/oparli-dryrun ~/.dryrun-backup

# nothing left:
docker ps -a        --filter label=com.docker.compose.project=dryrun
docker volume ls    --filter label=com.docker.compose.project=dryrun
docker network ls   --filter label=com.docker.compose.project=dryrun
# production untouched (its network is external to the dry run and never removed by it):
cd ~/oparli/deploy/scanner && docker compose ps
```

- If you added a cron line for `backup.sh`, remove it (`crontab -e`).
- Backups in S3 stay under `…/dryrun/`; the scripts never delete anything.
  To remove them: `aws s3 rm --recursive s3://<bucket>/oparli/dryrun/`
  (needs a role that may delete; production's prefix is never touched).

## Tests

```bash
python -m pytest tests/deploy/test_dryrun.py tests/deploy/test_ibkr_instrument_check.py -q
```

- **Compose file:** no gateway, no published ports, the dry run's own
  aliases, volumes and image, memory limits, the prefix, the instrument, and
  only the feed on production's network.
- **`check_env.sh`:** every refusal, with stub docker; no token is ever
  printed.
- **The backup wrapper:** checks its prefix and its state file.

`docker compose config` itself needs Docker; run it on the host (above).
