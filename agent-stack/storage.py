"""Small helpers for durable JSON files.

Writes are atomic: data goes to a temp file, is fsynced, then os.replace()
swaps it in. A crash at any moment leaves either the old file or the new
file, never a half-written one.
"""
import json
import os


def data_dir():
    return os.environ.get("AGENT_DATA_DIR", "memory")


def log_dir():
    return os.environ.get("AGENT_LOG_DIR", "logs")


def atomic_write_json(path, obj):
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    tmp = f"{path}.tmp"
    with open(tmp, "w") as f:
        json.dump(obj, f, indent=2)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)


def read_json(path, default):
    try:
        with open(path) as f:
            return json.load(f)
    except FileNotFoundError:
        return default
    except json.JSONDecodeError as e:
        raise RuntimeError(
            f"{path} is corrupt ({e}). Inspect it before deleting anything."
        ) from e
