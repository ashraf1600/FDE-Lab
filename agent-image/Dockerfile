FROM python:3.11-slim

WORKDIR /app

RUN pip install --no-cache-dir requests==2.31.0

COPY agent-stack/memory.py .
COPY agent-stack/job_queue.py .
COPY agent-stack/tools.py .
COPY agent-stack/orchestrator.py .

RUN mkdir -p memory logs
RUN useradd -m -s /bin/bash agent
USER agent

CMD ["python", "orchestrator.py"]
