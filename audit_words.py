"""
One-time audit of words.db against CAMeL Tools.

Why: the `words` table appears to have been built by analyzing the
*undiacritized* `stripped` spelling of each token rather than its already-
correct, fully-vocalized `original` spelling. That's what produced rows like
109 separate occurrences of "الحق" (the Truth) all being permanently
mistagged as lemma="أَلْحَق" / pos="verb" ("to attach") — the undiacritized
skeleton is genuinely ambiguous, but the Uthmani diacritics already present
in `original` are not.

This script re-analyzes every row's `original` field, and instead of
guessing by part-of-speech frequency, picks whichever CAMeL analysis
reproduces the SAME diacritics already present in the Qur'anic text. That's
a much stronger signal than any POS heuristic — Uthmani vowel marks
determine case/mood almost unambiguously.

This is a DRY RUN by default: it only prints rows where CAMeL's re-analysis
disagrees with what's currently stored, it does not write anything back.
Spot-check the flagged list (some disagreements will be CAMeL being wrong,
not the DB) before uncommenting the UPDATE at the bottom.
"""

import sqlite3

from camel_tools.morphology.database import MorphologyDB
from camel_tools.morphology.analyzer import Analyzer

DB_PATH = "quran.db"

db = MorphologyDB.builtin_db()
analyzer = Analyzer(db)


def strip_tatweel(text):
    return (text or "").replace("ـ", "")


def best_matching_analysis(original):
    """Prefer the analysis whose full diacritized surface form matches the
    Qur'an's own vocalization exactly, over any POS-frequency guess.

    Returns (analysis, confident) — confident=True only when an analysis's
    diacritics matched the Qur'an's own spelling exactly. If nothing matched
    and we fell back to CAMeL's own top-ranked guess, confident=False —
    that's the same "just took index 0" situation that caused this whole
    problem, so it should be reviewed by hand, not auto-applied.
    """
    analyses = analyzer.analyze(original)
    if not analyses:
        return None, False

    target = strip_tatweel(original)

    for a in analyses:
        diac = strip_tatweel(a.get("diac", ""))
        if diac == target:
            return a, True

    return analyses[0], False


def main():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()

    cur.execute("SELECT id, original, lemma, pos, root FROM words")
    rows = cur.fetchall()

    confident_changes = []
    needs_review = []
    updates = []

    for row in rows:
        analysis, confident = best_matching_analysis(row["original"])
        if not analysis:
            continue

        new_lemma = analysis.get("lex")
        new_pos = analysis.get("pos")
        new_root = analysis.get("root")

        if not new_lemma or new_lemma == row["lemma"]:
            continue

        entry = {
            "id": row["id"],
            "original": row["original"],
            "old_lemma": row["lemma"],
            "new_lemma": new_lemma,
            "old_pos": row["pos"],
            "new_pos": new_pos,
            "old_root": row["root"],
            "new_root": new_root,
        }

        if confident:
            confident_changes.append(entry)
            updates.append((new_lemma, new_pos, new_root, row["id"]))
        else:
            needs_review.append(entry)

    def _print_entries(entries, limit=80):
        for f in entries[:limit]:
            print(
                f"id={f['id']:<6} {f['original']:<12} "
                f"lemma: {f['old_lemma']!r} -> {f['new_lemma']!r}   "
                f"pos: {f['old_pos']!r} -> {f['new_pos']!r}   "
                f"root: {f['old_root']!r} -> {f['new_root']!r}"
            )
        if len(entries) > limit:
            print(f"... and {len(entries) - limit} more")

    print(f"{len(rows)} rows checked.")
    print(f"{len(confident_changes)} confident changes (exact diacritic match — safe to auto-apply).")
    print(f"{len(needs_review)} need manual review (CAMeL couldn't match the exact vocalization — same 'took index 0' risk as before).\n")

    print("--- CONFIDENT ---")
    _print_entries(confident_changes)

    print("\n--- NEEDS REVIEW ---")
    _print_entries(needs_review)

    # Only the confident, diacritic-matched changes are wired up here.
    # Rows in needs_review should be checked by hand before touching them —
    # applying those blindly would just repeat the original mistake.
    #
    # cur.executemany(
    #     "UPDATE words SET lemma = ?, pos = ?, root = ? WHERE id = ?",
    #     updates
    # )
    # conn.commit()
    # print(f"\nApplied {len(updates)} confident updates.")

    conn.close()


if __name__ == "__main__":
    main()