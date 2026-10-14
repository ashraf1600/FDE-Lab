#!/bin/sh
# Is the model REALLY running on the GPU? Run after at least one request,
# because `ollama ps` lists only models currently loaded in memory.
set -eu
echo "--- host GPU ---"
nvidia-smi --query-gpu=name,memory.used,memory.total --format=csv || echo "nvidia-smi not found on host"
echo "--- loaded models (PROCESSOR column) ---"
docker compose exec inference-server ollama ps
echo
echo "'100% GPU' = good. '100% CPU' = passthrough is NOT working (forgot docker-compose.gpu.yml?)."
