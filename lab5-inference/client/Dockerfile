FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1
WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY summarize.py .

RUN useradd --system --uid 10001 --shell /usr/sbin/nologin client
USER client

# One-shot: run, print the summary, exit with a meaningful code.
CMD ["python", "summarize.py", "/prompts/vendor_incident.txt"]
