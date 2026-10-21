"""
The فَعَلَ scale (الميزان الصرفي) for the course.

A pattern such as مَفْعُول is a word made from the "ruler" root ف ع ل:
ف stands for the first root letter, ع for the second and ل for the third,
and every other letter and vowel belongs to the pattern. build() puts a real
root into those three slots, so ك ت ب + مَفْعُول gives مَكْتُوب.

Nothing built here reaches a lesson unless it matches Bayan's own data:
find_word() looks the root + pattern up in meanings.db (wazn_entries) and
check_against() compares the built word with the corpus lemma letter by
letter and vowel by vowel. A mismatch raises PatternError, which stops the
course from loading, so a wrong word can't quietly appear on a page.
"""
import re
import unicodedata

from services.database import get_meanings_connection

# Combining marks: harakat, tanwin, shadda, sukun, dagger alif, Qur'anic signs.
_MARK_RE = re.compile(r"[ً-ٰٟۖ-ۭ]")
SHORT = {"َ": "a", "ِ": "i", "ُ": "u", "ْ": "", "ً": "an", "ٍ": "in", "ٌ": "un"}
SHADDA = "ّ"
SLOTS = {"ف": 0, "ع": 1, "ل": 2}
WEAK = set("وياىءأإؤئآ")
HAMZAS = set("ءأإؤئآ")


class PatternError(Exception):
    pass


def canon(text):
    """NFC with shadda before the vowel, so the same word always compares equal."""
    text = unicodedata.normalize("NFC", text or "")
    return re.sub(r"([ً-ِْٰ]+)ّ", lambda m: "ّ" + m.group(1), text)


def units(text):
    """Split into [letter, marks] units. A dagger alif becomes its own ا unit."""
    out = []
    for ch in canon(text).replace("ـ", ""):
        if _MARK_RE.match(ch):
            if not out:
                continue
            if ch == "ٰ":
                out.append(["ا", ""])
            else:
                out[-1][1] += ch
        else:
            out.append([ch, ""])
    return out


def root_letters(root):
    letters = [c for c in root if not _MARK_RE.match(c) and not c.isspace()]
    if len(letters) != 3:
        raise PatternError(f"{root}: the course's patterns are for three-letter roots")
    return letters


def is_sound(root):
    """No weak letter or hamza, and not a doubled root (like ح ق ق)."""
    r = root_letters(root)
    return not (set(r) & WEAK) and r[1] != r[2]


def spaced(root):
    return " ".join(root_letters(root))


def build(root, wazn):
    """Put a root into a pattern. Returns (word, parts) where parts is a list of
    (text, slot) and slot is 0/1/2 for a root letter or None for a pattern letter."""
    r = root_letters(root)
    pat = units(wazn)
    seen = [b for b, _ in pat if b in SLOTS]
    if sorted(seen) != sorted(SLOTS) or len(seen) != 3:
        raise PatternError(f"{wazn}: a pattern needs ف, ع and ل exactly once each")
    parts = []
    for base, marks in pat:
        if base in SLOTS:
            parts.append((r[SLOTS[base]] + marks, SLOTS[base]))
        else:
            parts.append((base + marks, None))
    return canon("".join(t for t, _ in parts)), parts


def pattern_parts(wazn):
    """The pattern itself split the same way, for the scale display."""
    return [(b + m, SLOTS.get(b)) for b, m in units(wazn)]


def _letters(text):
    return [("ا" if b in "ٱآ" else b) for b, _ in units(text)]


def _vowel(marks):
    for ch in marks:
        if ch in SHORT:
            return SHORT[ch]
    return None


def check_against(built, lemma):
    """True when the built word and the corpus lemma have the same letters in the
    same order and no vowel or shadda that disagrees."""
    a, b = units(built), units(lemma)
    if len(a) != len(b):
        return False
    for (la, ma), (lb, mb) in zip(a, b):
        la = "ا" if la in "ٱآ" else la
        lb = "ا" if lb in "ٱآ" else lb
        if la != lb and not (la in HAMZAS and lb in HAMZAS):
            return False
        va, vb = _vowel(ma), _vowel(mb)
        if va is not None and vb is not None and va != vb:
            return False
        if mb and ma and ((SHADDA in ma) != (SHADDA in mb)):
            return False
        if SHADDA in mb and SHADDA not in ma:
            return False
    return True


def is_verb_pattern(wazn):
    """Past-tense verb patterns end in a fatḥa (فَعَلَ, أَفْعَلَ); noun patterns don't."""
    u = units(wazn)
    return bool(u) and _vowel(u[-1][1]) == "a"


def same_pattern(a, b):
    """Two spellings of one pattern (فَعّال / فَعَّال) count as the same."""
    return check_against(a, b) and check_against(b, a)


_cache = {}


def _entries(root):
    if root not in _cache:
        conn = get_meanings_connection()
        try:
            _cache[root] = [dict(r) for r in conn.execute(
                "SELECT lemma, display, root, tag, vf, wazn, gloss, freq FROM wazn_entries "
                "WHERE root = ? ORDER BY freq DESC", (root,))]
        finally:
            conn.close()
    return _cache[root]


def find_word(root, wazn):
    """The Qur'anic word for root + pattern, or None if the Qur'an doesn't use it.
    Several rows can share a pattern (noun and adjective readings): the most
    frequent one whose letters really match the pattern wins, and the counts
    of the matching rows are added up."""
    word, parts = build(root, wazn)
    verb = is_verb_pattern(wazn)
    rows = [e for e in _entries(root) if e["wazn"] and same_pattern(e["wazn"], wazn)
            and (e["tag"] == "V") == verb]
    good = [e for e in rows if check_against(word, e["display"] or e["lemma"])]
    if rows and not good:
        raise PatternError(f"{spaced(root)} + {wazn} builds {word}, but Bayan has "
                           + ", ".join(e["display"] or e["lemma"] for e in rows))
    if not good:
        return None
    best = good[0]
    return {
        "root": root, "wazn": canon(wazn), "word": word, "parts": parts,
        "lemma": best["lemma"], "gloss": best["gloss"] or "",
        "freq": sum(e["freq"] or 0 for e in good), "tag": best["tag"],
    }


def root_verb(root):
    """The root's plain (Form I) verb and its gloss, used as the root's meaning."""
    for wazn in ("فَعَلَ", "فَعِلَ", "فَعُلَ"):
        try:
            w = find_word(root, wazn)
        except PatternError:
            w = None
        if w:
            return w
    return None


def pattern_stats():
    """How much of the Qur'an's root-based vocabulary the commonest patterns cover."""
    conn = get_meanings_connection()
    try:
        totals = {}
        for wazn, f in conn.execute("SELECT wazn, SUM(freq) FROM wazn_entries WHERE wazn IS NOT NULL GROUP BY wazn"):
            key = next((k for k in totals if same_pattern(k, wazn)), canon(wazn))
            totals[key] = totals.get(key, 0) + (f or 0)
    finally:
        conn.close()
    ranked = sorted(totals.values(), reverse=True)
    whole = sum(ranked) or 1
    cover = lambda n: round(100 * sum(ranked[:n]) / whole)
    return {"patterns_total": len(ranked), "top10": cover(10), "top20": cover(20)}


def find_plural(root, sing_wazn, pl_wazn, own_entry=False):
    """The broken plural of a Qur'anic noun, built from root + plural pattern and
    checked against the plural forms the corpus files under the singular (it
    tags them MP / FP). Some plurals are their own entry in meanings.db
    (أَصْحَاب), so that is tried too. Returns None if the Qur'an doesn't have it."""
    from services.database import get_connection
    sing = find_word(root, sing_wazn)
    if sing is None:
        raise PatternError(f"{spaced(root)} + {sing_wazn}: no such singular in the Qur'an")
    word, parts = build(root, pl_wazn)
    conn = get_connection()
    try:
        rows = conn.execute(
            "SELECT surface, features, COUNT(*) FROM morphemes WHERE lemma = ? "
            "AND (features LIKE '%|MP|%' OR features LIKE '%|FP|%' OR features LIKE '%|MP' OR features LIKE '%|FP') "
            "GROUP BY surface, features", (sing["lemma"],)).fetchall()
    finally:
        conn.close()
    freq, seen = 0, []
    for surface, _features, n in rows:
        u = units(surface)
        if u and SHADDA in u[0][1]:          # ٱلصُّدُور: the shadda comes from the article
            u[0][1] = u[0][1].replace(SHADDA, "")
        if u and u[-1][0] == "ا" and u[-2:-1] and "ً" in u[-2][1]:   # tanwin -an + alif
            u = u[:-1]
        if check_against(word, "".join(b + m for b, m in u)):
            freq += n
            seen.append(surface)
    # A few plurals are entries of their own in Bayan (أَصْحَاب, رِجَال). Only
    # used when the lesson says so: the same root + pattern can also be an
    # unrelated singular (كِتَاب is on فِعَال too).
    own = find_word(root, pl_wazn) if own_entry else None
    if own_entry and not own:
        raise PatternError(f"{spaced(root)} + {pl_wazn}: no entry of its own in Bayan")
    if own:
        freq += own["freq"]
        seen.append(own["lemma"])
    if not freq:
        return None
    return {"root": root, "wazn": canon(pl_wazn), "word": word, "parts": parts,
            "lemma": sing["lemma"], "singular": sing, "freq": freq, "surfaces": seen,
            "gloss": "", "tag": "N"}
