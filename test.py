"""
Quick diagnostic: dump every field CAMeL actually returns for a
word, so we can see the REAL key names/formats instead of guessing.

Run this on the machine where camel_tools + its DB are installed:

    python3 diagnose_wazn.py
"""

from camel_tools.morphology.database import MorphologyDB
from camel_tools.morphology.analyzer import Analyzer

db = MorphologyDB.builtin_db()
analyzer = Analyzer(db)

# Undiacritized is usually more reliable for the analyzer than a fully
# vowelled Qur'anic string -- try both so we can see if that matters too.
for word in ["مؤمن", "مُؤْمِن"]:
    print("=" * 60)
    print("WORD:", word)
    analyses = analyzer.analyze(word)
    if not analyses:
        print("  (no analyses)")
        continue
    for i, a in enumerate(analyses):
        print(f"  --- analysis {i} ---")
        for k in sorted(a.keys()):
            print(f"    {k!r}: {a[k]!r}")