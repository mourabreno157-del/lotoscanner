import sqlite3
from pathlib import Path

DB_PATH = Path(__file__).with_name("lotoscanner.db")

class Database:
    def connect(self):
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
        return conn

db = Database()

def _has_column(c, table, column):
    return any(row[1] == column for row in c.execute(f"PRAGMA table_info({table})").fetchall())

def _migrate_name_column(c, table):
    cols = [row[1] for row in c.execute(f"PRAGMA table_info({table})").fetchall()]
    if "name" in cols:
        return
    if "nome" in cols:
        c.execute(f"ALTER TABLE {table} RENAME COLUMN nome TO name")

def init():
    with db.connect() as c:
        c.execute("""CREATE TABLE IF NOT EXISTS teams (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            provider TEXT NOT NULL, api_id TEXT NOT NULL, name TEXT NOT NULL,
            short_name TEXT, country TEXT, UNIQUE(provider, api_id))""")
        c.execute("""CREATE TABLE IF NOT EXISTS competitions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            provider TEXT NOT NULL, api_id TEXT NOT NULL, name TEXT NOT NULL,
            country TEXT, UNIQUE(provider, api_id))""")
        _migrate_name_column(c, "teams")
        _migrate_name_column(c, "competitions")
        c.execute("""CREATE TABLE IF NOT EXISTS raw_stats (
            id INTEGER PRIMARY KEY AUTOINCREMENT, source_fixture_id TEXT,
            canonical_id TEXT NOT NULL, venue TEXT NOT NULL, match_date TEXT NOT NULL,
            payload_json TEXT NOT NULL)""")
        c.execute("CREATE INDEX IF NOT EXISTS idx_raw_stats_team_venue_date ON raw_stats(canonical_id, venue, match_date)")
        c.execute("CREATE INDEX IF NOT EXISTS idx_raw_stats_fixture_date ON raw_stats(source_fixture_id, match_date)")
        c.commit()

init()
