# agents_v1

The plan for agents v1, the deterministic Technical agent. **Nothing here is
evidence, and nothing is pre-registered yet.**

| File | What |
|---|---|
| `PREREG.md` | The experiment, as a **DRAFT**. It becomes binding only when copied unchanged to `agents/experiments/<id>/PREREG.md` before the first evidence decision |
| `DECISIONS.md` | Choices made without the owner |
| `expected_exclusions.py` | Replays the production scanner's level engine over `XAUUSD.txt` 2007–2020 and counts the alerts whose holding window would cross a close at H = 60 and 240 (PREREG §2, §8) |
| `results/expected_exclusions.json` | Its output: alerts per trading day and crossing shares, overall and by year |
| `test_expected_exclusions.py` | Synthetic tests: entry timing, the crossing rule at the daily break and the weekend (both DST seasons), the summary, the I/O edges |

## Run

```
.venv/bin/python research/agents_v1/expected_exclusions.py
```

- Needs the untracked research cache
  (`research/vol_features_r2/cache/xauusd_txt_pre2024_filetime.parquet`),
  read through `research/levels`' loader. Nothing from 2021 on is read.
- Takes about 85 s and peaks at about 1.8 GB RSS on the development Mac. It
  is a one-off research script, not a service.
- Its output is deterministic: a rerun rewrites the same JSON.

## Test

```
.venv/bin/pytest research/agents_v1
```

No data files are needed; one test runs the real level engine on a 3-day
synthetic random walk.
