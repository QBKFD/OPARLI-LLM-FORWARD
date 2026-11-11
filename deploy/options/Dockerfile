# The daily OG options snapshot (python -m backend.options), one-shot: host
# cron runs it through run_snapshot.sh, so it has no restart policy and no
# long-running process. Its own image, so building it never touches the
# scanner's image. Stdlib + ib_insync; backup_sqlite.py for backup.sh.
FROM python:3.12-slim

# Pinned like the scanner image. tzdata: the market hours are in
# America/New_York, and the slim image may ship no system zoneinfo.
RUN pip install --no-cache-dir ib_insync==0.9.86 tzdata==2025.3

WORKDIR /app
# /app is the working directory, so it is on sys.path for `python -m` and for
# `python -` (tools/options_probe.py fed on stdin).
COPY backend/__init__.py              /app/backend/__init__.py
COPY backend/options                  /app/backend/options
COPY deploy/scanner/backup_sqlite.py  /app/backup_sqlite.py
RUN find /app -name __pycache__ -prune -exec rm -rf {} +

ENV PYTHONUNBUFFERED=1 OPTIONS_DB=/data/options.sqlite
VOLUME ["/data"]
ENTRYPOINT ["python", "-m", "backend.options"]
