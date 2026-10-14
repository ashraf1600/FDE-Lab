"""Tools: plain functions. They know nothing about memory, queue or orchestrator."""
import csv
import io
import os

import requests


def parse_manifest(csv_text):
    """Turn raw CSV text into a list of shipment rows."""
    return list(csv.DictReader(io.StringIO(csv_text)))


def _weight_is_anomalous(value):
    if value is None or str(value).strip() == "":
        return True
    try:
        return float(value) == 0
    except ValueError:
        return True  # non-numeric weight is itself an anomaly, not a crash


def flag_anomalies(rows):
    """Rows whose declared weight is missing, zero or not a number."""
    return [row for row in rows if _weight_is_anomalous(row.get("weight"))]


def fetch_vendor_status(vendor_id):
    """Call the vendor API. Reports failure honestly: ok=False plus an error."""
    url = os.environ.get("VENDOR_API_URL", "https://httpbin.org/get")
    try:
        resp = requests.get(url, params={"vendor": vendor_id}, timeout=5)
    except requests.RequestException as e:
        return {"vendor": vendor_id, "ok": False, "error": str(e)}
    if resp.status_code != 200:
        return {"vendor": vendor_id, "ok": False, "error": f"HTTP {resp.status_code}"}
    return {"vendor": vendor_id, "ok": True}
