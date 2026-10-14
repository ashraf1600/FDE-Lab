FROM ollama/ollama:0.5.7

WORKDIR /app
COPY entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

ENV OLLAMA_MODEL=deepseek-r1:1.5b
ENV OLLAMA_KEEP_ALIVE=24h

EXPOSE 11434

ENTRYPOINT ["/entrypoint.sh"]
