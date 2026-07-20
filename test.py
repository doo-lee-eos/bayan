"""
One-off data fix: the `lemmas` table in meanings.db has an incorrect
meaning for جاعِل ("one who makes, places, or appoints" -- the active
participle of جَعَلَ, "to make") -- it was accidentally given the
meaning of the unrelated, similarly-spelled word جَائِع/جَاعَ ("hungry",
root جوع). This corrects that single row.

Usage:
    python3 fix_jaail_meaning.py /path/to/meanings.db
"""

import sqlite3
import sys


def main():
    db_path = sys.argv[1] if len(sys.argv) > 1 else "databases/meanings.db"

    conn = sqlite3.connect(db_path)
    cur = conn.cursor()

    cur.execute(
        "SELECT lemma, root, meaning FROM lemmas WHERE lemma = ? AND root = ?",
        ("جاعِل", "جعل"),
    )
    before = cur.fetchone()
    print("Before:", before)

    cur.execute(
        "UPDATE lemmas SET meaning = ? WHERE lemma = ? AND root = ?",
        ("one who makes, places, or appoints", "جاعِل", "جعل"),
    )
    conn.commit()

    cur.execute(
        "SELECT lemma, root, meaning FROM lemmas WHERE lemma = ? AND root = ?",
        ("جاعِل", "جعل"),
    )
    after = cur.fetchone()
    print("After: ", after)

    conn.close()


if __name__ == "__main__":
    main()