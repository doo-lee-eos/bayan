"""
Populate meanings.db's `roots.meaning` from lexicon.sqlite (Lane's Lexicon).

Matches the exact extraction convention already used for the 1299 currently-
populated rows: take the lexicon `entry` row for that root with itype = '1'
(the primary Form-I verb entry), strip XML tags (each tag -> single space,
then collapse whitespace), and store the plain text.

Handles three root-spelling mismatches between this corpus's QAC-style roots
and lexicon.sqlite's classical spelling convention:
  - hamza-initial roots:  أخذ  -> اخذ   (lexicon uses bare alif)
  - weak final root:      لقي  -> لقى   (lexicon uses alif maqsura)
  - geminate/doubled:     أبب  -> اب    (lexicon contracts to 2 radicals)
Quadriliteral roots (4 letters) are looked up under itype 'R. Q. 1' instead
of '1', since that's how lexicon.sqlite tags the reduplicated Form I.

Only fills rows where meaning IS NULL -- never overwrites existing data.
Safe to re-run.

Usage:
    python populate_root_meanings.py                 # populate all NULL roots
    python populate_root_meanings.py --root لقي        # just one root
    python populate_root_meanings.py --dry-run        # report only, no writes
"""

import argparse
import re
import sqlite3

MEANINGS_DB = "databases/meanings.db"
LEXICON_DB = "databases/lexicon.sqlite"

HAMZA_MAP = str.maketrans({"أ": "ا", "إ": "ا", "آ": "ا", "ٱ": "ا"})


def strip_tags(xml_text):
    """Exact extraction rule reverse-engineered from the already-populated rows:
    replace each tag with a single space (not empty string -- this matters,
    it's what preserves the spaces you see around bracketed/parenthetical
    text in the stored meanings), then collapse whitespace."""
    text = re.sub(r"<[^>]+>", " ", xml_text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def root_candidates(root):
    """Yields (candidate_root, itype) pairs to try against lexicon.sqlite's
    `entry.root`, most-likely-correct first. Tries multiple itype tags per
    candidate rather than inferring one from string length -- length alone
    doesn't disambiguate 'Q. 1' (plain quadriliteral) from 'R. Q. 1'
    (reduplicated quadriliteral), and the reduplicated-quadriliteral
    *contraction* candidate is only 2 letters long despite needing the
    'R. Q. 1' tag."""
    variants = [root]
    folded = root.translate(HAMZA_MAP)
    if folded not in variants:
        variants.append(folded)

    expanded = []
    for v in variants:
        expanded.append(v)
        if v.endswith("ي"):
            expanded.append(v[:-1] + "ى")
        if len(v) == 3 and v[1] == v[2]:
            expanded.append(v[:2])  # geminate contraction, e.g. أبب -> اب
        if len(v) == 4 and v[0:2] == v[2:4]:
            expanded.append(v[0:2])  # reduplicated quadriliteral, e.g. زلزل -> زل

    itypes_to_try = ["1", "R. Q. 1", "Q. 1"]

    seen = set()
    for v in expanded:
        for itype in itypes_to_try:
            key = (v, itype)
            if key in seen:
                continue
            seen.add(key)
            yield v, itype


def find_lexicon_meaning(lcur, root):
    for candidate, itype in root_candidates(root):
        lcur.execute(
            "SELECT xml FROM entry WHERE root = ? AND itype = ? LIMIT 1",
            (candidate, itype),
        )
        row = lcur.fetchone()
        if row:
            return strip_tags(row["xml"]), candidate
    return None, None


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", help="Populate only this one root (e.g. لقي)")
    parser.add_argument("--dry-run", action="store_true", help="Report matches without writing")
    parser.add_argument("--meanings-db", default=MEANINGS_DB)
    parser.add_argument("--lexicon-db", default=LEXICON_DB)
    args = parser.parse_args()

    mconn = sqlite3.connect(args.meanings_db)
    mconn.row_factory = sqlite3.Row
    mcur = mconn.cursor()

    lconn = sqlite3.connect(args.lexicon_db)
    lconn.row_factory = sqlite3.Row
    lcur = lconn.cursor()

    if args.root:
        mcur.execute("SELECT root, meaning FROM roots WHERE root = ?", (args.root,))
    else:
        mcur.execute("SELECT root, meaning FROM roots WHERE meaning IS NULL")
    targets = mcur.fetchall()

    updated, skipped_has_meaning, unresolved = 0, 0, []

    for row in targets:
        root = row["root"]
        if row["meaning"] is not None and not args.root:
            continue  # shouldn't happen given the WHERE clause, but stay safe
        if row["meaning"] is not None and args.root:
            skipped_has_meaning += 1
            print(f"[skip] {root} already has a meaning (use a manual UPDATE to overwrite)")
            continue

        meaning, matched_as = find_lexicon_meaning(lcur, root)
        if meaning is None:
            unresolved.append(root)
            continue

        print(f"[match] {root}  (lexicon root: {matched_as})")
        print(f"        {meaning[:100]}...")
        if not args.dry_run:
            mcur.execute("UPDATE roots SET meaning = ? WHERE root = ?", (meaning, root))
        updated += 1

    if not args.dry_run:
        mconn.commit()

    print()
    print(f"Updated: {updated}")
    print(f"Unresolved (no itype='1'/'R. Q. 1' lexicon entry found): {len(unresolved)}")
    if unresolved:
        print("  ", unresolved[:30], "..." if len(unresolved) > 30 else "")

    mconn.close()
    lconn.close()


if __name__ == "__main__":
    main()