"""
One-off data fix: the `lemmas` table in meanings.db has an incorrect
meaning for إِلٰه ("a god, a deity") -- it was accidentally given the
meaning of the unrelated, similarly-spelled word آلة ("a tool, an
instrument, a machine"). This corrects that single row.

Usage:
    python3 fix_ilah_meaning.py /path/to/meanings.db
"""

import sqlite3
import sys


def main():
    db_path = sys.argv[1] if len(sys.argv) > 1 else "databases/meanings.db"

    conn = sqlite3.connect(db_path)
    cur = conn.cursor()

    cur.execute(
        "SELECT lemma, root, meaning FROM lemmas WHERE lemma = ? AND root = ?",
        ("إِلٰه", "أله"),
    )
    before = cur.fetchone()
    print("Before:", before)

    cur.execute(
        "UPDATE lemmas SET meaning = ? WHERE lemma = ? AND root = ?",
        ("a god, a deity, a divinity", "إِلٰه", "أله"),
    )
    conn.commit()

    cur.execute(
        "SELECT lemma, root, meaning FROM lemmas WHERE lemma = ? AND root = ?",
        ("إِلٰه", "أله"),
    )
    after = cur.fetchone()
    print("After: ", after)

    conn.close()


if __name__ == "__main__":
    main()