"""
Applies short_gloss_drafts.csv into roots.short_override.

By default only applies rows with flag == "" (the ~193-221 "OK" rows) --
skips CROSS_REF / LOW_CONFIDENCE / EMPTY rows, since those need a human
to actually write the gloss rather than trusting the heuristic extract.
Pass --include-low-confidence to also apply LOW_CONFIDENCE rows that
have non-empty text (still risky -- spot check those specifically
afterwards).

Only ever touches roots whose row has short_override currently NULL, so
re-running this is safe and won't clobber any short_override you've
since edited by hand.

Usage:
    python apply_short_glosses.py [csv_path] [db_path] [--include-low-confidence] [--dry-run]

Defaults: short_gloss_drafts.csv, databases/meanings.db
"""

import csv
import sqlite3
import sys
from pathlib import Path


def main():
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    flags = {a for a in sys.argv[1:] if a.startswith("--")}

    csv_path = Path(args[0]) if len(args) > 0 else Path("short_gloss_drafts.csv")
    db_path = Path(args[1]) if len(args) > 1 else Path("databases/meanings.db")
    include_low_confidence = "--include-low-confidence" in flags
    dry_run = "--dry-run" in flags

    if not csv_path.exists():
        raise SystemExit(f"Couldn't find {csv_path}")
    if not db_path.exists():
        raise SystemExit(f"Couldn't find {db_path}")

    allowed_flags = {""}
    if include_low_confidence:
        allowed_flags.add("LOW_CONFIDENCE")

    with csv_path.open(encoding="utf-8") as f:
        rows = list(csv.DictReader(f))

    conn = sqlite3.connect(db_path)
    cur = conn.cursor()

    applied, skipped_flag, skipped_empty, skipped_not_null = 0, 0, 0, 0

    for row in rows:
        root = row["root"]
        gloss = row["draft_short_gloss"].strip()
        flag = row["flag"].strip()

        if flag not in allowed_flags:
            skipped_flag += 1
            continue
        if not gloss:
            skipped_empty += 1
            continue

        cur.execute("SELECT short_override FROM roots WHERE root = ?", (root,))
        existing = cur.fetchone()
        if existing and existing[0] is not None:
            skipped_not_null += 1
            continue

        if not dry_run:
            cur.execute(
                "UPDATE roots SET short_override = ? WHERE root = ?",
                (gloss, root),
            )
        applied += 1

    if dry_run:
        conn.rollback()
    else:
        conn.commit()
    conn.close()

    mode = "(DRY RUN, nothing written)" if dry_run else ""
    print(f"{applied} short_override values {'would be' if dry_run else ''} applied {mode}")
    print(f"  skipped (flag not in {sorted(allowed_flags)}): {skipped_flag}")
    print(f"  skipped (empty draft text): {skipped_empty}")
    print(f"  skipped (short_override already set): {skipped_not_null}")


if __name__ == "__main__":
    main()