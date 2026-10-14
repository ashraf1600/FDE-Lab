#!/bin/sh
set -e

# Start Ollama daemon in background
ollama serve &
SERVER_PID=$!

echo "[entrypoint] Waiting for Ollama server to bind..."
while ! wget -qO- http://127.0.0.1:11434/api/tags >/dev/null 2>&1; do
    sleep 1
done

echo "[entrypoint] Checking if model '${OLLAMA_MODEL}' is cached..."
if ! ollama list | grep -q "${OLLAMA_MODEL}"; then
    echo "[entrypoint] Pulling model '${OLLAMA_MODEL}' into persistent volume..."
    ollama pull "${OLLAMA_MODEL}"
else
    echo "[entrypoint] Model '${OLLAMA_MODEL}' already cached. Ready!"
fi

wait "$SERVER_PID"
