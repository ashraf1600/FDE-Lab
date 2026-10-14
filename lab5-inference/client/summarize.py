import os
import re
import sys
import requests

INFERENCE_URL = os.environ.get("INFERENCE_URL", "http://inference-server:11434")
MODEL = os.environ.get("INFERENCE_MODEL", "deepseek-r1:1.5b")

def strip_think_tags(text):
    """DeepSeek-R1 emits <think>...</think> blocks. Strip them for clean output."""
    cleaned = re.sub(r'<think>.*?</think>', '', text, flags=re.DOTALL)
    return cleaned.strip()

def chat(prompt):
    resp = requests.post(
        f"{INFERENCE_URL}/v1/chat/completions",
        json={
            "model": MODEL,
            "messages": [{"role": "user", "content": prompt}],
            "temperature": 0.2
        },
        timeout=120,
    )
    resp.raise_for_status()
    raw_content = resp.json()["choices"][0]["message"]["content"]
    return strip_think_tags(raw_content)

if __name__ == "__main__":
    prompt_path = sys.argv[1] if len(sys.argv) > 1 else "prompts/vendor_incident.txt"
    with open(prompt_path, "r") as f:
        prompt = f.read()
    
    print("\n--- Model Response ---")
    print(chat(prompt))
