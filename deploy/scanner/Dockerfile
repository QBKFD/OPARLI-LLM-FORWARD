# One image for the scanner stack: the scanner (python -m backend.scanner, either
# bar source) and, in the "stream" profile, the datafeed feed and recorder, which
# override the entrypoint. ib_insync + pandas + redis + the source files; no DB
# server, no psycopg2.
FROM python:3.12-slim

# Pinned to the versions the selftest and test suite ran with, so the container
# runs the scanner exactly as validated (an unpinned build once got pandas 3).
# tzdata: the market hours are in America/New_York, and the slim image may ship
# no system zoneinfo for Python to fall back on. redis: the datafeed bus.
RUN pip install --no-cache-dir ib_insync==0.9.86 pandas==2.3.3 numpy==2.2.6 \
        pyarrow==25.0.0 tzdata==2025.3 redis==5.2.1

WORKDIR /app
# The scanner runs as `python -m backend.scanner` from /app (the working
# directory, so /app is on sys.path; also for `python -m` in the feed and
# recorder and `python -` in the watchdog's probes). Everything is imported
# from /app/backend, not pip-installed: backend.scanner and backend.levels (the
# scanner), backend.datafeed (stream mode, feed, recorder), backend.scoring
# (TAKE outcomes; scoring -> risk, backend.datafeed.store) and backend.risk.
COPY backend/__init__.py                /app/backend/__init__.py
COPY backend/scanner                    /app/backend/scanner
COPY backend/levels                     /app/backend/levels
COPY backend/datafeed                   /app/backend/datafeed
COPY backend/scoring                    /app/backend/scoring
COPY backend/risk                       /app/backend/risk
COPY deploy/scanner/backup_sqlite.py    /app/backup_sqlite.py
COPY deploy/scanner/export_parquet.py   /app/export_parquet.py
COPY deploy/scanner/entrypoint.sh       /app/entrypoint.sh
RUN chmod +x /app/entrypoint.sh && find /app -name __pycache__ -prune -exec rm -rf {} +

ENV PYTHONUNBUFFERED=1 SCANNER_DB=/data/scanner_decisions.sqlite
VOLUME ["/data"]
ENTRYPOINT ["/app/entrypoint.sh"]
