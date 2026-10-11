"""Run from the backend folder:  python -m unittest discover -s tests -v"""
import csv
import os
import sqlite3
import sys
import tempfile
import unittest
from contextlib import closing
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import database  # noqa: E402
import predictor as p  # noqa: E402


def college(cid, closing, state="X", name="C", city="Y"):
    return {"id": cid, "college": name, "city": city, "state": state, "type": "T",
            "closing_rank_2025": {"UR": closing, "EWS": closing, "OBC": closing, "SC": closing * 2, "ST": closing * 3}}


def query(sql):
    """Run a read query and close the connection (required on Windows)."""
    with closing(sqlite3.connect(database.db_path())) as conn:
        return conn.execute(sql).fetchall()


class RankTests(unittest.TestCase):
    def test_every_supplied_anchor_is_exact(self):
        with open(p.DATA_DIR / "neet_2025_score_to_rank.csv") as f:
            rows = list(csv.DictReader(f))
        self.assertEqual(len(rows), 18)
        for r in rows:
            status, air, _ = p.estimate_air(int(r["score"]), "UR")
            self.assertEqual((status, air), ("ok", int(r["approx_air"])), r)

    def test_midpoint_interpolation(self):
        # 600 -> 1300 and 575 -> 4500, so 587.5 sits halfway = 2900. Check with whole-score 588 / 587 too.
        self.assertAlmostEqual(p.interpolate_air(587.5, p.ANCHORS), 2900)
        self.assertEqual(p.estimate_air(500, "UR")[1], 41406)  # between 501->39500 and 485->70000

    def test_above_top_anchor_is_capped_with_note(self):
        status, air, note = p.estimate_air(700, "UR")
        self.assertEqual((status, air), ("ok", 1))
        self.assertIn("AIR 1", note)

    def test_below_qualifying_mark(self):
        self.assertEqual(p.estimate_air(143, "UR")[0], "not_qualified")
        self.assertEqual(p.estimate_air(143, "EWS")[0], "not_qualified")
        self.assertEqual(p.estimate_air(112, "SC")[0], "not_qualified")

    def test_reserved_between_113_and_143_is_out_of_range_not_invented(self):
        for cat in ("OBC", "SC", "ST"):
            status, air, _ = p.estimate_air(130, cat)
            self.assertEqual((status, air), ("out_of_range", None))


class ClassificationTests(unittest.TestCase):
    def test_boundaries_at_85_100_115_percent(self):
        cs = [college("A", 1000)]
        cases = [(850, "safe"), (851, "moderate"), (1000, "moderate"), (1001, "reach"), (1150, "reach"), (1151, None)]
        for air, expected in cases:
            g = p.classify_colleges(air, "UR", "Z", cs)
            found = [k for k in g if g[k]["total"] == 1]
            self.assertEqual(found, [expected] if expected else [], f"air={air}")

    def test_uses_selected_category_cutoff(self):
        cs = [college("A", 1000)]  # UR 1000, SC 2000, ST 3000
        self.assertEqual(p.classify_colleges(1500, "UR", "Z", cs)["reach"]["total"], 0)
        self.assertEqual(p.classify_colleges(1500, "SC", "Z", cs)["safe"]["total"], 1)  # 1500 <= 85% of 2000 = 1700
        self.assertEqual(p.classify_colleges(1500, "ST", "Z", cs)["safe"]["total"], 1)

    def test_all_colleges_returned_sorted_by_closing_rank(self):
        cs = [college(f"C{i}", 10000 + i * 10, name=f"N{i}") for i in range(15, 0, -1)]
        g = p.classify_colleges(100, "UR", "Z", cs)["safe"]
        self.assertEqual(g["total"], 15)
        self.assertEqual(len(g["colleges"]), 15)   # no cap: the website decides how many to show first
        ranks = [c["closing_rank"] for c in g["colleges"]]
        self.assertEqual(ranks, sorted(ranks))

    def test_home_state_flag(self):
        cs = [college("A", 1000, state="Delhi"), college("B", 1000, state="Bihar")]
        rows = p.classify_colleges(100, "UR", "Delhi", cs)["safe"]["colleges"]
        self.assertEqual({r["id"]: r["in_home_state"] for r in rows}, {"A": True, "B": False})

    def test_empty_groups_and_dead_zone_with_real_data(self):
        r = p.predict(500, "UR", "Delhi")  # AIR ~41,400: beyond every UR closing rank x 1.15
        self.assertEqual(r["status"], "ok")
        self.assertTrue(all(r["groups"][k]["total"] == 0 for k in r["groups"]))
        self.assertIn("none of the 50", r["explanation"])

    def test_assignment_worked_example_ur_600(self):
        """From the assignment doc: UR 600 -> AIR ~1,300; Bhopal/Patna hidden, Raipur Reach, Nagpur Moderate, KEM Safe."""
        r = p.predict(600, "UR", "Delhi")
        self.assertEqual(r["estimated_air"], 1300)
        where = {}
        for key, g in r["groups"].items():
            full = p.classify_colleges(1300, "UR", "Delhi")[key]["colleges"]
            for c in full:
                where[c["college"]] = key
        self.assertEqual(where.get("AIIMS Raipur"), "reach")
        self.assertEqual(where.get("AIIMS Nagpur"), "moderate")
        self.assertEqual(where.get("Seth GS Medical College & KEM Hospital"), "safe")
        self.assertNotIn("AIIMS Bhopal", where)
        self.assertNotIn("AIIMS Patna", where)

    def test_cutoffs_csv_matches_json(self):
        with open(p.DATA_DIR / "neet_2025_college_cutoffs.csv") as f:
            rows = {r["id"]: r for r in csv.DictReader(f)}
        self.assertEqual(len(rows), 50)
        for c in p.COLLEGES:
            for cat, v in c["closing_rank_2025"].items():
                self.assertEqual(int(rows[c["id"]]["closing_rank_" + cat]), v)

    def test_real_data_top_scorer(self):
        r = p.predict(650, "UR", "Delhi")
        self.assertEqual(r["estimated_air"], 88)
        # Classification uses the rounded AIR the student sees (88), so MAMC (closing 103; 85% = 87.55)
        # is Moderate, not Safe.
        self.assertEqual(r["groups"]["safe"]["total"], 48)
        self.assertEqual(r["groups"]["moderate"]["total"], 1)
        self.assertEqual(len(r["groups"]["safe"]["colleges"]), 48)

    def test_summarize_has_counts_and_no_college_names(self):
        s = p.summarize(p.predict(600, "UR", "Delhi"))
        self.assertEqual(s["estimated_air"], 1300)
        self.assertEqual(set(s["counts"]), {"safe", "moderate", "reach"})
        self.assertNotIn("groups", s)
        self.assertNotIn("AIIMS", str(s))
        self.assertIsNone(p.summarize(p.predict(100, "UR", "Delhi"))["counts"])


class DatabaseTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        os.environ["DB_PATH"] = os.path.join(self.tmp.name, "t.db")
        database.init_db()

    def tearDown(self):
        os.environ.pop("DB_PATH", None)
        self.tmp.cleanup()

    def rows(self):
        return query("SELECT * FROM leads")

    def test_saves_all_required_fields(self):
        database.save_lead("Asha", "9876543210", 600, "OBC", "Delhi", 1300, "google", "cpc", "neet26", False)
        r = query("SELECT name,mobile,score,category,home_state,estimated_air,utm_source,utm_medium,"
                  "utm_campaign,is_test,created_at FROM leads")[0]
        self.assertEqual(r[:10], ("Asha", "9876543210", 600, "OBC", "Delhi", 1300, "google", "cpc", "neet26", 0))
        self.assertTrue(r[10])

    def test_duplicate_phone_updates_instead_of_inserting(self):
        self.assertFalse(database.save_lead("A", "9876543210", 500, "UR", "Delhi", 41406))
        self.assertTrue(database.save_lead("A", "9876543210", 600, "UR", "Delhi", 1300))
        rows = self.rows()
        self.assertEqual(len(rows), 1)
        self.assertEqual(query("SELECT score, submit_count FROM leads")[0], (600, 2))

    def test_test_and_real_leads_are_separate(self):
        database.save_lead("A", "9876543210", 500, "UR", "Delhi", None, is_test=True)
        database.save_lead("A", "9876543210", 500, "UR", "Delhi", None, is_test=False)
        self.assertEqual(len(self.rows()), 2)


if __name__ == "__main__":
    unittest.main()
