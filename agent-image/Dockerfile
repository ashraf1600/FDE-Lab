# Build context is the repo root:  docker compose --profile job build
FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    AGENT_DATA_DIR=/data \
    AGENT_LOG_DIR=/data/logs

WORKDIR /app

COPY agent-stack/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY agent-stack/storage.py agent-stack/memory.py agent-stack/job_queue.py \
     agent-stack/tools.py agent-stack/orchestrator.py agent-stack/seed_queue.py ./

# /data is created AND owned by the agent user in the image. A named volume
# mounted there inherits that ownership, so no host-side chown is needed.
RUN useradd --system --uid 10001 --shell /usr/sbin/nologin agent \
    && mkdir -p /data/logs \
    && chown -R agent:agent /data
USER agent

# One-shot job: runs, exits with a meaningful code (0 ok / 1 failed / 2 service down).
CMD ["python", "orchestrator.py"]
