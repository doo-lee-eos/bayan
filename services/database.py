import sqlite3
from pathlib import Path

DB_PATH = Path(__file__).resolve().parent.parent / "databases" / "quran.db"
MEANINGS_DB_PATH = Path(__file__).resolve().parent.parent / "databases" / "meanings.db"


def get_connection():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def get_meanings_connection():
    conn = sqlite3.connect(MEANINGS_DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn