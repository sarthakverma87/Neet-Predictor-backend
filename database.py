"""Card 5 - Lead database (SQLite, standard library only).

Only the backend touches this. There is deliberately NO endpoint that lists leads or events.
Duplicate handling: one row per (mobile, is_test). A repeat submission updates the
row and bumps submit_count instead of creating a second lead.
"""
import os
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path


def db_path():
    return os.environ.get("DB_PATH", str(Path(__file__).parent / "leads.db"))


@contextmanager
def _connect():
    """Open a connection, commit on success / roll back on error, and ALWAYS close it
    (sqlite's own `with` does not close; on Windows an open file cannot be deleted)."""
    conn = sqlite3.connect(db_path())
    conn.row_factory = sqlite3.Row
    try:
        with conn:
            yield conn
    finally:
        conn.close()


def init_db():
    Path(db_path()).parent.mkdir(parents=True, exist_ok=True)
    with _connect() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS leads (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                mobile TEXT NOT NULL,
                score INTEGER NOT NULL,
                category TEXT NOT NULL,
                home_state TEXT NOT NULL,
                estimated_air INTEGER,
                utm_source TEXT, utm_medium TEXT, utm_campaign TEXT,
                is_test INTEGER NOT NULL DEFAULT 0,
                submit_count INTEGER NOT NULL DEFAULT 1,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                score_band TEXT, device TEXT,
                safe_count INTEGER, moderate_count INTEGER, reach_count INTEGER
            )""")
        conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS uq_leads_mobile ON leads (mobile, is_test)")
        # A leads.db created before the analytics export existed lacks the newer columns: add them in place.
        have = {r["name"] for r in conn.execute("PRAGMA table_info(leads)")}
        for col, kind in (("score_band", "TEXT"), ("device", "TEXT"), ("safe_count", "INTEGER"),
                          ("moderate_count", "INTEGER"), ("reach_count", "INTEGER")):
            if col not in have:
                conn.execute(f"ALTER TABLE leads ADD COLUMN {col} {kind}")
        # Funnel / analytics events (page_view, form_start, ...). Never holds a name or phone number.
        conn.execute("""
            CREATE TABLE IF NOT EXISTS events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                created_at TEXT NOT NULL,
                event TEXT NOT NULL,
                utm_source TEXT, utm_medium TEXT, utm_campaign TEXT,
                device TEXT, score_band TEXT, category TEXT, state TEXT,
                safe_count INTEGER, moderate_count INTEGER, reach_count INTEGER,
                cta_type TEXT,
                is_test INTEGER NOT NULL DEFAULT 0
            )""")


def save_lead(name, mobile, score, category, home_state, estimated_air,
              utm_source=None, utm_medium=None, utm_campaign=None, is_test=False,
              device=None, score_band=None, safe_count=None, moderate_count=None, reach_count=None):
    """Insert a lead, or update it if this mobile already exists. Returns True if it was a repeat."""
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    flag = 1 if is_test else 0
    with _connect() as conn:  # commits on success, rolls back on error
        row = conn.execute("SELECT id FROM leads WHERE mobile = ? AND is_test = ?", (mobile, flag)).fetchone()
        if row:
            conn.execute(
                """UPDATE leads SET name=?, score=?, category=?, home_state=?, estimated_air=?,
                   score_band=?, device=?, safe_count=?, moderate_count=?, reach_count=?,
                   submit_count = submit_count + 1, updated_at=? WHERE id=?""",
                (name, score, category, home_state, estimated_air,
                 score_band, device, safe_count, moderate_count, reach_count, now, row["id"]))
            return True
        conn.execute(
            """INSERT INTO leads (name, mobile, score, category, home_state, estimated_air,
               utm_source, utm_medium, utm_campaign, is_test, created_at, updated_at,
               score_band, device, safe_count, moderate_count, reach_count)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (name, mobile, score, category, home_state, estimated_air,
             utm_source, utm_medium, utm_campaign, flag, now, now,
             score_band, device, safe_count, moderate_count, reach_count))
        return False


def save_event(event, utm_source=None, utm_medium=None, utm_campaign=None, device=None, score_band=None,
               category=None, state=None, safe_count=None, moderate_count=None, reach_count=None,
               cta_type=None, is_test=False):
    """Append one analytics event. Always a new row (an event log, unlike leads, is never de-duplicated)."""
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    with _connect() as conn:
        conn.execute(
            """INSERT INTO events (created_at, event, utm_source, utm_medium, utm_campaign, device, score_band,
               category, state, safe_count, moderate_count, reach_count, cta_type, is_test)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (now, event, utm_source, utm_medium, utm_campaign, device, score_band,
             category, state, safe_count, moderate_count, reach_count, cta_type, 1 if is_test else 0))
