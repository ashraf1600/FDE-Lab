FROM python:3.11-slim

WORKDIR /app
RUN pip install --no-cache-dir requests==2.31.0

COPY summarize.py .
RUN useradd -m -s /bin/bash client
USER client

CMD ["python", "summarize.py", "/prompts/vendor_incident.txt"]
