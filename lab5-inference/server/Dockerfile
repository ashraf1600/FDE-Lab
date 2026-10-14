ARG OLLAMA_VERSION=0.5.7
FROM ollama/ollama:${OLLAMA_VERSION}

# Default model; override with LLM_MODEL in .env (compose passes it in).
ENV OLLAMA_MODEL=deepseek-r1:1.5b \
    PULL_ON_START=1 \
    OLLAMA_KEEP_ALIVE=30m

COPY entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

EXPOSE 11434
ENTRYPOINT ["/entrypoint.sh"]
