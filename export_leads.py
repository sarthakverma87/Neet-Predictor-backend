"""Owner-only helper: print leads with masked phone numbers (for your screenshot).
Run on the machine/server where the database lives:
    python export_leads.py            # all leads
    python export_leads.py --real     # exclude test leads
"""
import sys
import sqlite3
import database

real_only = "--real" in sys.argv
database.init_db()  # makes the table if the server has never run
conn = sqlite3.connect(database.db_path())
sql = ("SELECT id, name, mobile, score, category, home_state, estimated_air, utm_source, utm_medium, "
       "utm_campaign, is_test, submit_count, created_at FROM leads")
if real_only:
    sql += " WHERE is_test = 0"
rows = conn.execute(sql + " ORDER BY id").fetchall()
print("id | name | mobile | score | cat | state | AIR | utm_source | utm_medium | utm_campaign | test | submits | created")
for r in rows:
    r = list(r)
    r[1] = r[1][0] + "***"                 # mask name
    r[2] = r[2][:2] + "XXXXX" + r[2][-3:]   # mask phone
    print(" | ".join("" if v is None else str(v) for v in r))
print(f"\n{len(rows)} lead(s)")
print("\nReal leads by UTM source:")
for src, n in conn.execute("SELECT COALESCE(utm_source,'(none)'), COUNT(*) FROM leads WHERE is_test=0 GROUP BY 1 ORDER BY 2 DESC"):
    print(f"  {src}: {n}")
