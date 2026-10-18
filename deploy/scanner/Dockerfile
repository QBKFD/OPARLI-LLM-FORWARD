# One image for the scanner stack: the scanner (deterministic_scanner.py, either
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
# services/ is a package with only tws_connector (imports ib_insync + stdlib)
COPY backend/services/__init__.py       /app/services/__init__.py
COPY backend/services/tws_connector.py  /app/services/tws_connector.py
COPY backend/deterministic_scanner.py   /app/deterministic_scanner.py
COPY backend/journal_summary.py         /app/journal_summary.py
# datafeed, scoring and risk are imported from /app (the working directory),
# not pip-installed. scoring scores TAKE outcomes (scoring -> risk, datafeed.store).
COPY datafeed                           /app/datafeed
COPY scoring                            /app/scoring
COPY risk                               /app/risk
COPY deploy/scanner/backup_sqlite.py    /app/backup_sqlite.py
COPY deploy/scanner/export_parquet.py   /app/export_parquet.py
COPY deploy/scanner/entrypoint.sh       /app/entrypoint.sh
RUN chmod +x /app/entrypoint.sh && find /app -name __pycache__ -prune -exec rm -rf {} +

ENV PYTHONUNBUFFERED=1 SCANNER_DB=/data/scanner_decisions.sqlite
VOLUME ["/data"]
ENTRYPOINT ["/app/entrypoint.sh"]
