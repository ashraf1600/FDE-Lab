FROM python:3.11-slim

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends curl \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY tools_impl.py .
COPY server.py .

RUN useradd -m -s /bin/bash mcp
USER mcp

EXPOSE 8080
CMD ["python", "server.py"]
