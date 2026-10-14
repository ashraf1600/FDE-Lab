import os
import unittest
from unittest import mock

import _paths  # noqa: F401
import server

TOKEN = "t0ken"
AUTH = {"Authorization": f"Bearer {TOKEN}"}


class ToolServiceTests(unittest.TestCase):
    def setUp(self):
        p = mock.patch.dict(os.environ, {"MCP_TOKEN": TOKEN})
        p.start()
        self.addCleanup(p.stop)
        self.c = server.app.test_client()

    def test_health_needs_no_auth_and_reports_version_and_tools(self):
        r = self.c.get("/health")
        self.assertEqual(r.status_code, 200)
        self.assertIn("version", r.json)
        self.assertEqual(
            r.json["tools"], ["fetch_vendor_status", "flag_anomalies", "parse_manifest"]
        )

    def test_auth_required_on_tools_and_calls(self):
        self.assertEqual(self.c.get("/tools").status_code, 401)
        self.assertEqual(
            self.c.get("/tools", headers={"Authorization": "Bearer wrong"}).status_code, 401
        )
        self.assertEqual(self.c.post("/tools/parse_manifest", json={}).status_code, 401)

    def test_fails_closed_when_token_not_configured(self):
        with mock.patch.dict(os.environ, {"MCP_TOKEN": ""}):
            self.assertEqual(self.c.get("/tools", headers=AUTH).status_code, 503)

    def test_discovery_lists_schemas(self):
        r = self.c.get("/tools", headers=AUTH)
        self.assertEqual(r.status_code, 200)
        names = [t["name"] for t in r.json["tools"]]
        self.assertEqual(names, ["fetch_vendor_status", "flag_anomalies", "parse_manifest"])
        self.assertIn("input_schema", r.json["tools"][0])

    def test_unknown_tool_is_404(self):
        self.assertEqual(self.c.post("/tools/nope", headers=AUTH, json={}).status_code, 404)

    def test_argument_validation_returns_400(self):
        for body in ({}, {"csv_text": 5}, {"csv_text": "a", "extra": 1}):
            r = self.c.post("/tools/parse_manifest", headers=AUTH, json=body)
            self.assertEqual(r.status_code, 400, body)
            self.assertIn("bad arguments", r.json["error"])

    def test_tools_work(self):
        r = self.c.post(
            "/tools/parse_manifest", headers=AUTH, json={"csv_text": "weight\n5\n0\n"}
        )
        rows = r.json["result"]
        self.assertEqual(len(rows), 2)
        r = self.c.post("/tools/flag_anomalies", headers=AUTH, json={"rows": rows})
        self.assertEqual(r.json["result"], [{"weight": "0"}])

    def test_internal_errors_do_not_leak_details(self):
        boom = mock.Mock(side_effect=RuntimeError("/secret/internal/path"))
        with mock.patch.dict(server.TOOLS["flag_anomalies"], {"fn": boom}):
            r = self.c.post("/tools/flag_anomalies", headers=AUTH, json={"rows": []})
        self.assertEqual(r.status_code, 500)
        self.assertNotIn("secret", r.get_data(as_text=True))


if __name__ == "__main__":
    unittest.main()
