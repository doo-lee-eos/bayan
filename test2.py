"""
Fixes two curated `lemmas.meaning` rows that were flattened to the bare
adjective gloss "Safe"/"safe", which made them indistinguishable from each
other (and from the actual adjectives آمِنَة/سالِم that legitimately mean
"safe") in the word-family grid:

  مَأْمَن  (noun of place, اسم مكان)  'Safe' -> 'a place of safety, a refuge'
  مَأْمُون (passive participle)        'safe' -> 'trustworthy, secure, dependable'

Run directly against the live meanings.db:
    python3 fix_maman_mamun.py databases/meanings.db
"""
import sqlite3
import sys

FIXES = [
    ("مَأْمَن", "أمن", "Safe", "a place of safety, a refuge"),
    ("مَأْمُون", "أمن", "safe", "trustworthy, secure, dependable"),
]


def main():
    db_path = sys.argv[1] if len(sys.argv) > 1 else "meanings.db"
    conn = sqlite3.connect(db_path)
    cur = conn.cursor()

    for lemma, root, old_meaning, new_meaning in FIXES:
        cur.execute(
            "SELECT COUNT(*) FROM lemmas WHERE lemma = ? AND root = ? AND meaning = ?",
            (lemma, root, old_meaning),
        )
        matched = cur.fetchone()[0]
        if matched == 0:
            print(f"SKIP  {lemma} ({root}): no row matching meaning={old_meaning!r} "
                  f"(already changed, or lemma/root shifted -- check manually)")
            continue

        cur.execute(
            "UPDATE lemmas SET meaning = ? WHERE lemma = ? AND root = ? AND meaning = ?",
            (new_meaning, lemma, root, old_meaning),
        )
        print(f"FIXED {lemma} ({root}): {old_meaning!r} -> {new_meaning!r}  "
              f"[{cur.rowcount} row(s)]")

    conn.commit()
    conn.close()


if __name__ == "__main__":
    main()