# Scanner container: runs deterministic_scanner.py --live against an IB Gateway.
# Only needs ib_insync + pandas + the two source files (no DB, no psycopg2).
FROM python:3.12-slim

# tzdata: the heartbeat's market hours are in America/New_York, and the slim
# image may ship no system zoneinfo for Python to fall back on.
# pandas/numpy pinned to the versions the selftest and dry-live runs used, so the
# container runs the scanner exactly as validated (an unpinned build got pandas 3).
RUN pip install --no-cache-dir ib_insync==0.9.86 pandas==2.3.3 numpy==2.2.6 pyarrow tzdata

WORKDIR /app
# services/ is a package with only tws_connector (imports ib_insync + stdlib)
COPY backend/services/__init__.py       /app/services/__init__.py
COPY backend/services/tws_connector.py  /app/services/tws_connector.py
COPY backend/deterministic_scanner.py   /app/deterministic_scanner.py
COPY deploy/scanner/entrypoint.sh       /app/entrypoint.sh
RUN chmod +x /app/entrypoint.sh

ENV PYTHONUNBUFFERED=1 SCANNER_DB=/data/scanner_decisions.sqlite
VOLUME ["/data"]
ENTRYPOINT ["/app/entrypoint.sh"]
