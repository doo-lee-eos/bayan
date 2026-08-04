"""
One-off fix for 3 roots.short_meaning rows in meanings.db that got a stray
mid-entry clause ("It is said in a trad., ...") extracted as their short
definition instead of the real opening gloss.

Sets roots.short_override for these rows -- the display code already prefers
short_override over short_meaning whenever it's present, so this fixes the
UI without needing to touch or re-run whatever script originally populated
short_meaning.

Usage:
    python fix_short_overrides.py --db /path/to/meanings.db
    python fix_short_overrides.py --db /path/to/meanings.db --dry-run
"""

import argparse
import sqlite3

OVERRIDES = {
    "قدر": "He measured, or computed, the quantity, measure, size, or extent, of a thing.",
    "أزز": "The cooking-pot made a sound in boiling, or boiled vehemently.",
    "زهق": "His soul went forth, passed forth, or departed; it perished, it died.",
}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", required=True, help="Path to meanings.db")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show what would change without writing anything",
    )
    args = parser.parse_args()

    conn = sqlite3.connect(args.db)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()

    for root, override in OVERRIDES.items():
        cur.execute(
            "SELECT root, short_meaning, short_override FROM roots WHERE root = ?",
            (root,),
        )
        row = cur.fetchone()
        if row is None:
            print(f"[skip] root {root!r} not found in roots table")
            continue

        print(f"root: {root}")
        print(f"  current short_meaning:  {row['short_meaning']!r}")
        print(f"  current short_override: {row['short_override']!r}")
        print(f"  new short_override ->   {override!r}")

        if not args.dry_run:
            cur.execute(
                "UPDATE roots SET short_override = ? WHERE root = ?",
                (override, root),
            )

    if args.dry_run:
        print("\nDry run only -- no changes written.")
    else:
        conn.commit()
        print(f"\nCommitted {len(OVERRIDES)} short_override updates.")

    conn.close()


if __name__ == "__main__":
    main()