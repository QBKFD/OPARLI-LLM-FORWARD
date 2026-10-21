# Not deployed. Local testing only.
# One image for both datafeed processes (feed and recorder); the compose file
# picks the command. Build context is the repo root.
FROM python:3.12-slim
WORKDIR /app
COPY pyproject.toml ./
COPY backend/__init__.py ./backend/__init__.py
COPY backend/datafeed ./backend/datafeed
RUN pip install --no-cache-dir '.[feed,replay]'
ENV PYTHONUNBUFFERED=1
