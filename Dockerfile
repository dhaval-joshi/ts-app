# Built but NOT smoke-tested as part of this round -- the cloud move isn't
# scheduled yet (see AGENTS.md). Building and running this image is the
# first real step of the migration itself, whenever that happens.
#
# Sizing note: this is a single-process, I/O-bound asyncio app (one event
# loop, no database, ~3.7MB of on-disk state) -- it does not benefit from a
# bigger image or more cores. Don't over-provision the box this runs on.
FROM python:3.13-slim

# TZ is belt-and-braces alongside backend/clock.py's own hardcoded
# ZoneInfo("Asia/Kolkata") -- the code no longer actually depends on this,
# but setting it anyway means any stray naive-datetime call this review
# missed still fails safe instead of silently reading UTC.
ENV TZ=Asia/Kolkata \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY backend/ backend/
COPY frontend/ frontend/

# Runs as non-root -- the app has no need for root privileges, and the
# static UID keeps ownership predictable on the mounted data/ volume
# (see docker-compose.yml).
RUN useradd --create-home --uid 1000 appuser \
    && mkdir -p /app/data \
    && chown -R appuser:appuser /app
USER appuser

EXPOSE 8000

# No --reload (that's dev-only) and no --workers (see AGENTS.md /
# README's runbook section -- this app cannot safely run as more than
# one process: two Program tick loops would each independently decide to
# start cycles, producing duplicate real orders).
CMD ["uvicorn", "backend.main:app", "--host", "0.0.0.0", "--port", "8000", "--ws", "wsproto"]
