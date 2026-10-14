import os
from flask import Flask, request, jsonify
import tools_impl

app = Flask(__name__)

# Tool registry with JSON schemas
TOOLS = {
    "parse_manifest": {
        "fn": tools_impl.parse_manifest,
        "description": "Parse raw CSV manifest text into shipment dict rows",
        "parameters": {
            "type": "object",
            "properties": {"csv_text": {"type": "string"}},
            "required": ["csv_text"]
        }
    },
    "flag_anomalies": {
        "fn": tools_impl.flag_anomalies,
        "description": "Flag shipments with missing or zero weight",
        "parameters": {
            "type": "object",
            "properties": {"rows": {"type": "array"}},
            "required": ["rows"]
        }
    },
    "fetch_vendor_status": {
        "fn": tools_impl.fetch_vendor_status,
        "description": "Query external vendor status API",
        "parameters": {
            "type": "object",
            "properties": {"vendor_id": {"type": "string"}},
            "required": ["vendor_id"]
        }
    }
}

@app.route("/health", methods=["GET"])
def health():
    return jsonify({"ok": True, "tools": list(TOOLS.keys())})

@app.route("/tools", methods=["GET"])
def list_tools():
    """Discovery endpoint: returns tool names and their JSON schemas."""
    return jsonify({
        name: {
            "description": meta["description"],
            "parameters": meta["parameters"]
        }
        for name, meta in TOOLS.items()
    })

@app.route("/tools/<name>", methods=["POST"])
def invoke(name):
    if name not in TOOLS:
        return jsonify({"error": f"unknown tool: {name}"}), 404
    
    args = request.get_json(silent=True) or {}
    try:
        result = TOOLS[name]["fn"](**args)
        return jsonify({"result": result})
    except TypeError as e:
        return jsonify({"error": f"bad arguments: {e}"}), 400
    except Exception as e:
        return jsonify({"error": str(e)}), 500

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8080)
