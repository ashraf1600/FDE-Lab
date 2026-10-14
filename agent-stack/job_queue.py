"""Queue: the ordered work list, stored on disk.

Named job_queue.py (not queue.py) so it does not shadow Python's stdlib
`queue` module, which requests/urllib3 import.
"""
import os

import storage


def _path():
    return os.path.join(storage.data_dir(), "queue.json")


def seed(tasks):
    ids = [t["id"] for t in tasks]
    if len(ids) != len(set(ids)):
        raise ValueError("duplicate task ids in queue")
    storage.atomic_write_json(_path(), tasks)


def load():
    return storage.read_json(_path(), [])
