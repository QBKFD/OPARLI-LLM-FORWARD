# research/gex_feasibility

Is listed gold options positioning big enough for a GEX level effect to be
plausible? The rules are in [PREREG.md](PREREG.md), committed before any data.
Decisions: [docs/decisions/research-gex-feasibility.md](../../docs/decisions/research-gex-feasibility.md).

| File | What |
|---|---|
| `../../tools/options_probe.py` | the read-only IBKR snapshot (OG chain, GC futures, GC daily bars) → one JSON file |
| `gex.py` | Black-76 gamma and unsigned dollar gamma per 1 % move |
| `analyze.py` | probe JSON → `REPORT.md`: gates, R, verdict, descriptives |
| `fake_ib.py` | a fake IBKR client for the probe's tests |
| `test_*.py` | the tests (`pytest research/gex_feasibility`) |
| `data/` | the probe's JSON, git-ignored (exchange market data, public repo) |

## 1. Run the probe (owner, on the EC2 host)

**When:** while COMEX is open on a regular trading day, e.g. **05:00 UTC,
Monday to Friday** (the probe refuses weekends, the daily 17:00–18:00 New York
break and CME holidays, exit 3). The live IBKR account must be **logged out
everywhere** (TWS, mobile, web); otherwise COMEX data on the paper gateway is
cut (error 10197, exit 4).

**Get the file** without touching the checkout's working tree:

```
cd ~/oparli
git fetch origin research/gex-feasibility
git show FETCH_HEAD:tools/options_probe.py > /tmp/options_probe.py
```

(or copy `tools/options_probe.py` to `/tmp/` on the host with `scp`.)

**Run it** inside the existing scanner image: the same command whether the
host still runs c62810d or runs `main` (both images have Python 3.12 and
ib_insync 0.9.86). The probe uses only the standard library and ib_insync,
reads `IB_HOST`/`IB_PORT` from the scanner's environment, and connects
read-only with **client id 61**.

```
cd ~/oparli/deploy/scanner
docker compose run --rm -T --no-deps --entrypoint python scanner - \
    < /tmp/options_probe.py > ~/gex_probe_$(date -u +%Y%m%dT%H%MZ).json
echo "exit status $?"
```

- The JSON goes to stdout (the file above); progress goes to the terminal.
  Nothing is written inside the container or to its volumes.
- `--rm` removes the container afterwards; `--no-deps` starts nothing else.
  The running scanner and gateway are untouched (the scanner keeps client ids
  11 and 21).
- Exit status: 0 done; 3 COMEX closed; 4 error 10197 (log out the live
  account everywhere, run again); 5 market data line limit (add `--batch 30`
  after `python -`, i.e. `... scanner - --batch 30 < /tmp/options_probe.py`);
  1 anything else (the message says what).
- **Runtime:** at most 15 s per batch of 50 contracts (live), then 15 s per
  batch for the contracts that sent nothing live (frozen); a batch ends sooner
  once every contract has all its values. A chain of 2,000 contracts takes at
  most about 20 minutes.
- If a run fails, keep a note of its time and exit status: PREREG.md asks for
  every attempt to be listed in the report.

## 2. Analyse (on the Mac)

```
scp <ec2-host>:~/gex_probe_<time>.json research/gex_feasibility/data/
python research/gex_feasibility/analyze.py research/gex_feasibility/data/gex_probe_<time>.json \
    [--attempt "<UTC time>: exit 4, error 10197"]
pytest research/gex_feasibility                    # now also checks our gamma against IBKR's in the file
```

`analyze.py` writes `REPORT.md`: the verdict first, then the gates, the
snapshot (time, open-interest date, live/frozen shares) and the descriptives.
Only REPORT.md is committed, never the JSON.
