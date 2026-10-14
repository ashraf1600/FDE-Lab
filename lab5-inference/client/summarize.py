"""Client for an OpenAI-compatible chat endpoint (the Ollama sidecar).

Speaks only HTTP + JSON: no Ollama SDK, no model files, no GPU knowledge.

Exit codes:  0 ok | 2 server unreachable/timeout | 3 model not on server |
             4 bad or failed response
Config (env): INFERENCE_URL, INFERENCE_MODEL, INFERENCE_TIMEOUT (read, s),
  INFERENCE_RETRIES, INFERENCE_RETRY_BACKOFF, INFERENCE_MAX_TOKENS,
  INFERENCE_TEMPERATURE, STRIP_THINK (1 = drop <think>...</think> blocks)
"""
import os
import re
import sys
import time

import requests

OK, UNREACHABLE, MODEL_MISSING, BAD_RESPONSE = 0, 2, 3, 4


class InferenceError(Exception):
    def __init__(self, exit_code, message):
        super().__init__(message)
        self.exit_code = exit_code


def _url():
    return os.environ.get("INFERENCE_URL", "http://inference-server:11434").rstrip("/")


def _model():
    return os.environ.get("INFERENCE_MODEL", "deepseek-r1:1.5b")


def _timeout():
    return (5, float(os.environ.get("INFERENCE_TIMEOUT", "120")))  # (connect, read)


def _retries():
    return int(os.environ.get("INFERENCE_RETRIES", "3"))


def _backoff():
    return float(os.environ.get("INFERENCE_RETRY_BACKOFF", "1.0"))


def strip_think(text):
    """DeepSeek-R1 may emit its reasoning inside <think>...</think>."""
    return re.sub(r"<think>.*?</think>\s*", "", text, flags=re.DOTALL).strip()


def list_models():
    """Model ids the server has, or None if the backend has no /v1/models."""
    try:
        resp = requests.get(f"{_url()}/v1/models", timeout=_timeout())
    except requests.RequestException as e:
        raise InferenceError(UNREACHABLE, f"cannot reach {_url()}: {e}") from e
    if resp.status_code == 404:
        return None
    if resp.status_code != 200:
        raise InferenceError(BAD_RESPONSE, f"/v1/models returned HTTP {resp.status_code}")
    return {m["id"] for m in resp.json().get("data", [])}


def ensure_model(model):
    available = list_models()
    if available is None:
        return  # backend cannot list models; let the chat call decide
    wanted = {model} | ({f"{model}:latest"} if ":" not in model else set())
    if not wanted & available:
        raise InferenceError(
            MODEL_MISSING,
            f"model '{model}' is not on the server (has: {sorted(available) or 'nothing'}).\n"
            f"Pull it:  docker compose exec inference-server ollama pull {model}",
        )


def chat(prompt):
    model = _model()
    body = {
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
        "temperature": float(os.environ.get("INFERENCE_TEMPERATURE", "0")),
        "max_tokens": int(os.environ.get("INFERENCE_MAX_TOKENS", "1024")),
        "stream": False,
    }
    attempts = _retries()
    last = None
    for i in range(attempts):
        try:
            resp = requests.post(f"{_url()}/v1/chat/completions", json=body, timeout=_timeout())
        except requests.RequestException as e:  # connection error or timeout
            last = InferenceError(UNREACHABLE, f"request failed: {e}")
        else:
            if resp.status_code == 404:
                raise InferenceError(MODEL_MISSING, f"server answered 404 for model '{model}'")
            if resp.status_code >= 500:
                last = InferenceError(BAD_RESPONSE, f"server error HTTP {resp.status_code}")
            elif resp.status_code >= 400:
                raise InferenceError(BAD_RESPONSE, f"HTTP {resp.status_code}: {resp.text[:200]}")
            else:
                try:
                    text = resp.json()["choices"][0]["message"]["content"]
                except (ValueError, KeyError, IndexError, TypeError) as e:
                    raise InferenceError(BAD_RESPONSE, f"unexpected response shape: {e}") from e
                if os.environ.get("STRIP_THINK", "1") == "1":
                    text = strip_think(text)
                return text
        if i < attempts - 1:
            time.sleep(_backoff() * 2**i)
    raise last


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    path = argv[0] if argv else "/prompts/vendor_incident.txt"
    try:
        with open(path) as f:
            prompt = f.read()
    except OSError as e:
        print(f"ERROR: cannot read prompt file: {e}", file=sys.stderr)
        return BAD_RESPONSE
    try:
        ensure_model(_model())
        print(chat(prompt))
    except InferenceError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return e.exit_code
    return OK


if __name__ == "__main__":
    sys.exit(main())
