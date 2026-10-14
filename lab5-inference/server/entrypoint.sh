#!/bin/sh
# Start Ollama, make sure the model is present, then keep serving.
#
#  PULL_ON_START=1 (default)  download the model if it is missing (needs internet)
#  PULL_ON_START=0            offline mode: never download; fail loudly if the
#                             model is not already in the volume
#
# Any failure exits non-zero, so the container is visibly broken instead of
# "running" without a model.
set -eu

: "${OLLAMA_MODEL:?OLLAMA_MODEL must be set}"
PULL_ON_START="${PULL_ON_START:-1}"

ollama serve &
SERVER_PID=$!

PULL_PID=""
# Whatever the reason we exit (error, docker stop), stop the children too,
# so nothing is left running. The exit status of the script is preserved.
cleanup() {
  [ -n "$PULL_PID" ] && kill -TERM "$PULL_PID" 2>/dev/null || true
  kill -TERM "$SERVER_PID" 2>/dev/null || true
  wait "$SERVER_PID" 2>/dev/null || true
}
trap cleanup EXIT
# docker stop sends SIGTERM: exit 0 (a clean stop), which runs cleanup.
trap 'exit 0' TERM INT

# Wait for the API to answer (max ~60s).
i=0
until ollama list >/dev/null 2>&1; do
  i=$((i + 1))
  if [ "$i" -gt 60 ]; then
    echo "ERROR: ollama did not become ready" >&2
    exit 1
  fi
  if ! kill -0 "$SERVER_PID" 2>/dev/null; then
    echo "ERROR: ollama serve exited early" >&2
    exit 1
  fi
  sleep 1
done

if ollama list | grep -qE "^${OLLAMA_MODEL}[[:space:]]"; then
  echo "Model ${OLLAMA_MODEL} already present."
elif [ "$PULL_ON_START" = "1" ]; then
  echo "Pulling ${OLLAMA_MODEL} ..."
  ollama pull "${OLLAMA_MODEL}" &
  PULL_PID=$!
  if ! wait "$PULL_PID"; then
    echo "ERROR: pulling ${OLLAMA_MODEL} failed (network? wrong model name?)" >&2
    exit 1
  fi
else
  echo "ERROR: model ${OLLAMA_MODEL} is not in the volume and PULL_ON_START=0 (offline mode)." >&2
  exit 1
fi

wait "$SERVER_PID"
