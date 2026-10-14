import csv
import io
import requests

def parse_manifest(csv_text):
    """Tool: turn raw CSV text into a list of shipment rows."""
    reader = csv.DictReader(io.StringIO(csv_text))
    return [row for row in reader]

def flag_anomalies(rows):
    """Tool: return rows where declared weight is missing or zero."""
    flagged = []
    for row in rows:
        weight = row.get("weight", "0")
        try:
            if not weight or float(weight) == 0:
                flagged.append(row)
        except ValueError:
            flagged.append(row)
    return flagged

def fetch_vendor_status(vendor_id):
    """Tool: call an external API for a vendor's current status."""
    try:
        resp = requests.get(
            f"https://httpbin.org/get?vendor={vendor_id}",
            timeout=5,
        )
        return {"vendor": vendor_id, "ok": resp.status_code == 200}
    except Exception as e:
        return {"vendor": vendor_id, "ok": False, "error": str(e)}
