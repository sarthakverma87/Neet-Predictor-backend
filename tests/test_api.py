"""API tests. Needs: pip install -r requirements.txt   Run:  python -m unittest tests.test_api -v
(Written but NOT yet run in the build environment, which had no internet to install FastAPI.)"""
import os
import sys
import tempfile
import sqlite3
import unittest
from contextlib import closing
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
_tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
os.environ["DB_PATH"] = os.path.join(_tmp.name, "api.db")

from fastapi.testclient import TestClient  # noqa: E402
import database  # noqa: E402
import main  # noqa: E402

client = TestClient(main.app)
GOOD = {"name": "Asha Rao", "mobile": "9876543210", "score": 600, "category": "OBC",
        "home_state": "Delhi", "utm_source": "google", "utm_medium": "cpc",
        "utm_campaign": "neet26", "is_test": True}


def _lead_count():
    with closing(sqlite3.connect(database.db_path())) as conn:
        return conn.execute("SELECT COUNT(*) FROM leads").fetchone()[0]


class ApiTests(unittest.TestCase):
    def test_health_and_metadata(self):
        self.assertEqual(client.get("/api/health").json(), {"status": "ok"})
        meta = client.get("/api/metadata").json()
        self.assertEqual([c["code"] for c in meta["categories"]], ["UR", "EWS", "OBC", "SC", "ST"])

    def test_valid_prediction_saves_lead_and_returns_results(self):
        r = client.post("/api/predict", json=GOOD)
        self.assertEqual(r.status_code, 200)
        body = r.json()
        self.assertEqual(body["estimated_air"], 1300)
        self.assertIn("safe", body["groups"])
        with closing(sqlite3.connect(database.db_path())) as conn:
            row = conn.execute("SELECT utm_source, is_test FROM leads WHERE mobile='9876543210'").fetchone()
        self.assertEqual(row, ("google", 1))

    def test_estimate_returns_counts_only_and_saves_no_lead(self):
        before = _lead_count()
        r = client.post("/api/estimate", json={"score": 600, "category": "UR", "home_state": "Delhi"})
        self.assertEqual(r.status_code, 200)
        body = r.json()
        self.assertEqual(body["estimated_air"], 1300)
        self.assertEqual(set(body["counts"]), {"safe", "moderate", "reach"})
        self.assertNotIn("groups", body)
        self.assertEqual(_lead_count(), before)
        bad = client.post("/api/estimate", json={"score": 800, "category": "UR", "home_state": "Delhi"})
        self.assertEqual(bad.status_code, 422)

    def test_shared_results_returns_full_list_and_saves_no_lead(self):
        before = _lead_count()
        r = client.post("/api/shared-results", json={"score": 600, "category": "UR", "home_state": "Delhi"})
        self.assertEqual(r.status_code, 200)
        body = r.json()
        self.assertEqual(body["estimated_air"], 1300)
        self.assertGreater(len(body["groups"]["safe"]["colleges"]), 10)
        self.assertEqual(_lead_count(), before)
        bad = client.post("/api/shared-results", json={"score": 999, "category": "UR", "home_state": "Delhi"})
        self.assertEqual(bad.status_code, 422)

    def test_invalid_inputs_rejected_and_nothing_saved(self):
        bad = [dict(GOOD, mobile="12345"), dict(GOOD, mobile="5876543210"), dict(GOOD, score=721),
               dict(GOOD, score=-1), dict(GOOD, category="XX"), dict(GOOD, home_state="Atlantis"),
               dict(GOOD, name=" ")]
        for payload in bad:
            self.assertEqual(client.post("/api/predict", json=dict(payload, mobile=payload["mobile"])).status_code, 422, payload)

    def test_prediction_saves_analytics_fields(self):
        r = client.post("/api/predict", json=dict(GOOD, mobile="9811111111", device="mobile"))
        self.assertEqual(r.status_code, 200)
        groups = r.json()["groups"]
        with closing(sqlite3.connect(database.db_path())) as conn:
            row = conn.execute("SELECT score_band, device, safe_count, moderate_count, reach_count "
                               "FROM leads WHERE mobile='9811111111'").fetchone()
        self.assertEqual(row, ("600-649", "mobile", groups["safe"]["total"], groups["moderate"]["total"],
                               groups["reach"]["total"]))

    def test_event_is_saved_and_unknown_events_rejected(self):
        ok = client.post("/api/event", json={"event": "page_view", "utm_source": "google", "device": "desktop"})
        self.assertEqual(ok.status_code, 200)
        self.assertEqual(ok.json(), {"ok": True})
        full = client.post("/api/event", json={"event": "result_view", "safe_count": 4, "moderate_count": 6,
                                               "reach_count": 8, "is_test": True})
        self.assertEqual(full.status_code, 200)
        with closing(sqlite3.connect(database.db_path())) as conn:
            self.assertEqual(conn.execute("SELECT utm_source, device FROM events WHERE event='page_view'").fetchone(),
                             ("google", "desktop"))
            self.assertEqual(conn.execute("SELECT safe_count, moderate_count, reach_count, is_test "
                                          "FROM events WHERE event='result_view'").fetchone(), (4, 6, 8, 1))
        for bad in ({"event": "hack"}, {"event": "page_view", "safe_count": -1}, {}):
            self.assertEqual(client.post("/api/event", json=bad).status_code, 422, bad)

    def test_no_endpoint_lists_leads(self):
        for path in ("/api/leads", "/leads", "/api/admin/leads", "/api/events", "/events"):
            self.assertEqual(client.get(path).status_code, 404)

    def test_db_failure_returns_error_and_no_results(self):
        original = database.save_lead
        database.save_lead = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("disk full"))
        try:
            r = client.post("/api/predict", json=dict(GOOD, mobile="9123456780"))
        finally:
            database.save_lead = original
        self.assertEqual(r.status_code, 500)
        self.assertNotIn("groups", r.json())


if __name__ == "__main__":
    unittest.main()
