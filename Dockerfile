FROM python:3.13-slim

WORKDIR /app
COPY pyproject.toml README.md ./
COPY src ./src
RUN pip install --no-cache-dir .

ENV TZ=Asia/Tokyo PYTHONUNBUFFERED=1

# ponytail: sleep loop instead of cron/supervisord — one process, and
# `restart: unless-stopped` is the supervisor. Swap to host cron +
# `docker run --rm` only if you need per-run isolation.
CMD ["sh", "-c", "sleep ${START_DELAY:-0}; while :; do dl-poll --config /app/config.local.json --state /app/state/snapshot.json $DL_RES_FLAGS; sleep ${POLL_INTERVAL:-300}; done"]
