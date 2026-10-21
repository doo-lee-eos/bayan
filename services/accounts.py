"""
Learner accounts: sign-up / sign-in, lesson progress, quiz results and notes.

Everything is stored in databases/users.db (created on first use), kept apart
from quran.db and meanings.db so the content databases stay read-only and
can be replaced without touching anyone's progress. Passwords are stored
only as salted hashes (werkzeug's generate_password_hash).

users.db holds personal data -- don't commit it to git (add
databases/users.db to .gitignore).
"""
import re
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from werkzeug.security import check_password_hash, generate_password_hash

USERS_DB_PATH = Path(__file__).resolve().parent.parent / "databases" / "users.db"
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
MIN_PASSWORD = 8
MAX_NOTE = 20000

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    email         TEXT NOT NULL UNIQUE COLLATE NOCASE,
    name          TEXT,
    password_hash TEXT NOT NULL,
    created_at    TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS progress (
    user_id      INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    lesson       TEXT NOT NULL,
    completed_at TEXT,
    best_score   INTEGER,
    last_score   INTEGER,
    total        INTEGER,
    attempts     INTEGER NOT NULL DEFAULT 0,
    updated_at   TEXT NOT NULL,
    PRIMARY KEY (user_id, lesson)
);
CREATE TABLE IF NOT EXISTS notes (
    user_id    INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    lesson     TEXT NOT NULL,
    text       TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (user_id, lesson)
);
"""


class AccountError(Exception):
    """A problem the person can fix (shown on the form)."""


def _now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _conn():
    USERS_DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(USERS_DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.executescript(SCHEMA)
    return conn


# ------------------------------------------------------------------ users
def create_user(email, password, name=""):
    email = (email or "").strip()
    name = (name or "").strip()[:80]
    if not EMAIL_RE.match(email):
        raise AccountError("Please enter a valid email address.")
    if len(password or "") < MIN_PASSWORD:
        raise AccountError(f"Your password needs at least {MIN_PASSWORD} characters.")
    conn = _conn()
    try:
        cur = conn.execute(
            "INSERT INTO users (email, name, password_hash, created_at) VALUES (?, ?, ?, ?)",
            (email, name or None, generate_password_hash(password), _now()))
        conn.commit()
        return cur.lastrowid
    except sqlite3.IntegrityError:
        raise AccountError("An account with that email already exists. Try signing in instead.")
    finally:
        conn.close()


def authenticate(email, password):
    """The user id, or None if the email/password don't match."""
    conn = _conn()
    try:
        row = conn.execute("SELECT id, password_hash FROM users WHERE email = ?",
                           ((email or "").strip(),)).fetchone()
    finally:
        conn.close()
    if row and check_password_hash(row["password_hash"], password or ""):
        return row["id"]
    return None


def get_user(user_id):
    if not user_id:
        return None
    conn = _conn()
    try:
        row = conn.execute("SELECT id, email, name FROM users WHERE id = ?", (user_id,)).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


# --------------------------------------------------------------- progress
def get_state(user_id):
    """{"progress": {lesson: {...}}, "notes": {lesson: text}} for one learner."""
    conn = _conn()
    try:
        progress = {
            r["lesson"]: {
                "done": bool(r["completed_at"]),
                "best": r["best_score"], "last": r["last_score"], "total": r["total"],
                "attempts": r["attempts"],
            }
            for r in conn.execute("SELECT * FROM progress WHERE user_id = ?", (user_id,))
        }
        notes = {r["lesson"]: r["text"] for r in conn.execute(
            "SELECT lesson, text FROM notes WHERE user_id = ?", (user_id,))}
        return {"progress": progress, "notes": notes}
    finally:
        conn.close()


def _upsert(conn, user_id, lesson, done=False, score=None, total=None, attempts_add=0, keep_last=False):
    """keep_last: merging browser progress -- don't let it replace the
    account's most recent score, only fill it in if there is none."""
    row = conn.execute("SELECT * FROM progress WHERE user_id = ? AND lesson = ?",
                       (user_id, lesson)).fetchone()
    now = _now()
    if row is None:
        conn.execute(
            "INSERT INTO progress (user_id, lesson, completed_at, best_score, last_score, total, attempts, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (user_id, lesson, now if done else None, score, score, total, attempts_add, now))
        return
    best = row["best_score"]
    if score is not None:
        best = score if best is None else max(best, score)
    conn.execute(
        "UPDATE progress SET completed_at = COALESCE(completed_at, ?), best_score = ?, "
        + ("last_score = COALESCE(last_score, ?), " if keep_last else "last_score = COALESCE(?, last_score), ")
        + "total = COALESCE(?, total), "
        "attempts = attempts + ?, updated_at = ? WHERE user_id = ? AND lesson = ?",
        (now if done else None, best, score, total, attempts_add, now, user_id, lesson))


def record_quiz(user_id, lesson, score, total):
    score, total = int(score), int(total)
    if total <= 0 or not 0 <= score <= total:
        raise AccountError("Bad quiz result.")
    conn = _conn()
    try:
        _upsert(conn, user_id, lesson, done=True, score=score, total=total, attempts_add=1)
        conn.commit()
    finally:
        conn.close()


def mark_complete(user_id, lesson):
    conn = _conn()
    try:
        _upsert(conn, user_id, lesson, done=True)
        conn.commit()
    finally:
        conn.close()


def save_note(user_id, lesson, text):
    text = (text or "")[:MAX_NOTE]
    conn = _conn()
    try:
        if text.strip():
            conn.execute(
                "INSERT INTO notes (user_id, lesson, text, updated_at) VALUES (?, ?, ?, ?) "
                "ON CONFLICT(user_id, lesson) DO UPDATE SET text = excluded.text, updated_at = excluded.updated_at",
                (user_id, lesson, text, _now()))
        else:
            conn.execute("DELETE FROM notes WHERE user_id = ? AND lesson = ?", (user_id, lesson))
        conn.commit()
    finally:
        conn.close()


def merge_guest(user_id, data, valid_lessons):
    """Fold progress and notes saved in the browser before signing in into
    the account. Never lowers a best score; keeps an existing account note
    and adds the browser's note under it if they differ."""
    data = data if isinstance(data, dict) else {}
    conn = _conn()
    try:
        for lesson, p in (data.get("progress") or {}).items():
            if lesson not in valid_lessons or not isinstance(p, dict):
                continue
            best, total = p.get("best"), p.get("total")
            ok_score = isinstance(best, int) and isinstance(total, int) and total > 0 and 0 <= best <= total
            tries = p.get("attempts")
            tries = min(tries, 1000) if isinstance(tries, int) and tries > 0 else 0
            _upsert(conn, user_id, lesson, done=bool(p.get("done")),
                    score=best if ok_score else None, total=total if ok_score else None,
                    attempts_add=tries if ok_score else 0, keep_last=True)
        for lesson, text in (data.get("notes") or {}).items():
            if lesson not in valid_lessons or not isinstance(text, str) or not text.strip():
                continue
            row = conn.execute("SELECT text FROM notes WHERE user_id = ? AND lesson = ?",
                               (user_id, lesson)).fetchone()
            merged = text if not row else (row["text"] if text.strip() in row["text"]
                                           else row["text"].rstrip() + "\n\n" + text.strip())
            conn.execute(
                "INSERT INTO notes (user_id, lesson, text, updated_at) VALUES (?, ?, ?, ?) "
                "ON CONFLICT(user_id, lesson) DO UPDATE SET text = excluded.text, updated_at = excluded.updated_at",
                (user_id, lesson, merged[:MAX_NOTE], _now()))
        conn.commit()
    finally:
        conn.close()
