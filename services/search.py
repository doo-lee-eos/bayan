from services.database import get_connection, get_meanings_connection
from services.normalize import strip_harakat, normalize, normalize_hamza_notation
from services.camel import camel_root_candidates
from services.meanings import attach_meanings, attach_family_meanings


# ==========================================================
# Database helpers
# ==========================================================

def fetch_morphemes(cur, word_id):
    """Fetches morphemes linked to a specific word ID."""
    cur.execute("""
        SELECT *
        FROM morphemes
        WHERE word_id = ?
        ORDER BY morpheme_number
    """, (word_id,))
    return cur.fetchall()


def fetch_occurrences_by_root(cur, root):
    """Every word in the Qur'an sharing this root."""
    if not root:
        return []

    cur.execute("""
        SELECT
            w.surface,
            w.surah,
            w.ayah,
            w.word_number,
            v.text,
            t.text AS translation
        FROM words w
        JOIN verses v
            ON v.surah = w.surah
           AND v.ayah = w.ayah
        LEFT JOIN translations t
            ON t.surah = w.surah
           AND t.ayah = w.ayah
           AND t.translator = 'sahih_international'
        WHERE w.root = ?
        ORDER BY w.surah, w.ayah, w.word_number
    """, (root,))

    return cur.fetchall()


def fetch_word_family_by_root(cur, root):
    """
    Every distinct LEMMA sharing this root, with how many times each one
    occurs in the corpus.

    This is different from fetch_occurrences_by_root: that returns one row
    per AYAH the root appears in (for the verse-by-verse list), while this
    collapses the corpus down to the distinct derived FORMS the root takes
    (e.g. a verb, its active participle, its verbal noun...) so the page
    can show "here's the word family" rather than just "here's every ayah".

    NOTE: this intentionally does NOT group by words.lemma/words.pos.
    Those two columns are populated (at import time) from whichever
    morpheme happened to land in a fixed morpheme-slot, not from the
    actual stem -- so a word with a prefix ("fa-atu") and a word with no
    prefix ("atawna") end up with the *stem itself* landing in different
    slot positions. The result: the same verb gets recorded with two
    different (and sometimes outright wrong) lemma/pos pairs depending on
    its prefix/suffix shape, which fractures one lemma into duplicate
    rows here, and in the worst case (a word whose suffix is the emphatic
    "-anna") overwrites the real lemma with the suffix's own lemma
    entirely.

    Instead, this joins each word to its own STEM morpheme -- identified
    as the morpheme row whose `root` matches the word's `root` -- and
    groups on that morpheme's lemma/tag. That's the one piece of
    morphological data that's reliably tied to the actual stem regardless
    of what prefixes/suffixes surround it.
    """
    if not root:
        return []

    cur.execute("""
        SELECT m.lemma AS lemma, m.tag AS pos, COUNT(DISTINCT w.id) AS occurrence_count
        FROM words w
        JOIN morphemes m
            ON m.word_id = w.id
           AND m.root = w.root
        WHERE w.root = ?
        GROUP BY m.lemma, m.tag
        ORDER BY occurrence_count DESC, m.lemma
    """, (root,))
    return cur.fetchall()


def fetch_occurrences_by_word(cur, surface):
    """
    Every occurrence of this exact surface form elsewhere in the Qur'an.

    Fallback used when the matched word has no root recorded at all (some
    closed-class words -- particles, certain pronouns -- genuinely have no
    root), so there's nothing for fetch_occurrences_by_root to key off of.
    Without this, such a word would silently show zero occurrences despite
    appearing in the corpus multiple times.
    """
    if not surface:
        return []

    cur.execute("""
        SELECT
            w.surface,
            w.surah,
            w.ayah,
            w.word_number,
            v.text,
            t.text AS translation
        FROM words w
        JOIN verses v
            ON v.surah = w.surah
           AND v.ayah = w.ayah
        LEFT JOIN translations t
            ON t.surah = w.surah
           AND t.ayah = w.ayah
           AND t.translator = 'sahih_international'
        WHERE w.surface = ?
        ORDER BY w.surah, w.ayah, w.word_number
    """, (surface,))

    return cur.fetchall()


# ==========================================================
# Search stages
# ==========================================================

def _register_normalize(conn):
    conn.create_function("NORMALIZE", 1, lambda t: normalize(t) if t else "")


def search_surface(cur, query):
    cur.execute("""
        SELECT *
        FROM words
        WHERE surface = ?
        LIMIT 1
    """, (query,))
    return cur.fetchone()


def search_stripped(cur, query):
    cur.execute("""
        SELECT *
        FROM words
        WHERE stripped = ?
        LIMIT 1
    """, (strip_harakat(query),))
    return cur.fetchone()


def search_surface_notation(cur, query):
    """
    Same as search_surface, but first fixes hamza-on-tatweel notation
    (e.g. "ـَٔ" -> "ء") without touching any other diacritic. This bridges
    Qur'an text sources that spell a hamza differently while keeping the
    word fully vocalized -- unlike search_stripped/search_normalized
    below, which discard short vowels entirely and so can't tell apart
    homographs that only differ by one vowel (e.g. ٱلْءَاخَرِينَ "others"
    vs ٱلْءَاخِرِينَ "the last/latter", both of which collapse to the same
    stripped/normalized value).
    """
    cur.execute("""
        SELECT *
        FROM words
        WHERE surface = ?
        LIMIT 1
    """, (normalize_hamza_notation(query),))
    return cur.fetchone()


def search_normalized(cur, query):
    cur.execute("""
        SELECT *
        FROM words
        WHERE normalized = ?
        LIMIT 1
    """, (normalize(query),))
    return cur.fetchone()


def search_lemma(cur, query):
    """Compares NORMALIZED query against the NORMALIZED lemma column."""
    cur.execute("""
        SELECT *
        FROM words
        WHERE NORMALIZE(lemma) = ?
        LIMIT 1
    """, (normalize(query),))
    return cur.fetchone()


def search_root(cur, query):
    """Normalize both sides before comparing against the roots table."""
    cur.execute("""
        SELECT root
        FROM roots
        WHERE NORMALIZE(root) = ?
    """, (normalize(query),))
    
    row = cur.fetchone()
    return row["root"] if row else None


# ==========================================================
# Main lookup
# ==========================================================

def lookup(query):
    """
    Always returns a single dict with a "type" key: "word", "root", "camel_root", or "none".
    """
    query = (query or "").strip()

    if not query:
        return {"type": "none", "query": query}

    conn = get_connection()
    _register_normalize(conn)
    cur = conn.cursor()

    # 1. Surface matching
    word = search_surface(cur, query)

    # 1b. Surface matching after fixing hamza-on-tatweel notation only
    # (keeps every other diacritic, so it can still disambiguate
    # homographs that stripped/normalized matching below cannot)
    if word is None:
        word = search_surface_notation(cur, query)

    # 2. Stripped matching
    if word is None:
        word = search_stripped(cur, query)

    # 3. Normalized matching
    if word is None:
        word = search_normalized(cur, query)

    # 4. Lemma matching
    if word is None:
        word = search_lemma(cur, query)

    # Word found (stages 1-4)
    if word:
        # Fetch morphemes via the clean foreign key relational field 'id'
        morphemes = fetch_morphemes(cur, word["id"])

        # Some closed-class words (particles, certain pronouns, etc.) have
        # no root at all -- fetch_occurrences_by_root would just return []
        # for those, so fall back to every occurrence of this exact
        # surface elsewhere in the corpus instead.
        if word["root"]:
            occurrences = fetch_occurrences_by_root(cur, word["root"])
            word_family = fetch_word_family_by_root(cur, word["root"])
        else:
            occurrences = fetch_occurrences_by_word(cur, word["surface"])
            word_family = []

        conn.close()

        result = {
            "type": "word",
            "query": query,
            "word": dict(word),
            "root": word["root"],
            "morphemes": [dict(x) for x in morphemes],
            "occurrences": [dict(x) for x in occurrences],
            "word_family": [dict(x) for x in word_family],
        }

        return attach_meanings(result)

    # 5. Root Matching (Direct match inside the database)
    root = search_root(cur, query)

    if root:
        occurrences = fetch_occurrences_by_root(cur, root)
        word_family = fetch_word_family_by_root(cur, root)
        conn.close()

        result = {
            "type": "root",
            "query": query,
            "root": root,
            "occurrences": [dict(x) for x in occurrences],
            "word_family": [dict(x) for x in word_family],
        }
        return attach_family_meanings(result)

    # 6. CAMeL Falling back to guessed roots
    for candidate_root in camel_root_candidates(query):
        cur.execute("SELECT root FROM roots WHERE root = ?", (candidate_root,))
        row = cur.fetchone()

        if row:
            occurrences = fetch_occurrences_by_root(cur, candidate_root)
            word_family = fetch_word_family_by_root(cur, candidate_root)
            conn.close()

            result = {
                "type": "camel_root",
                "query": query,
                "root": candidate_root,
                "occurrences": [dict(x) for x in occurrences],
                "word_family": [dict(x) for x in word_family],
            }
            return attach_family_meanings(result)

    # 7. No matches found
    conn.close()
    return {"type": "none", "query": query}


# ==========================================================
# Roots index (for the /roots page)
# ==========================================================

def fetch_all_roots():
    """
    Every root that occurs in the Qur'an, alphabetically ordered by its
    normalized ("stripped") spelling, each carrying:

      - occurrence_count: how many words in the corpus carry this root
        (from quran.db -- a single GROUP BY, not one query per root)
      - meaning:  the full classical (Lane's Lexicon) entry, or None
      - short:    the best available brief gloss, using the exact same
                  short_override -> short_meaning (if short_confident)
                  -> None precedence result.html's own root card already
                  uses, so the brief text shown here always agrees with
                  what a search for this same root would show.

    Powers the /roots index page: one "island" per root, brief gloss up
    front, full classical definition tucked behind a toggle.
    """

    conn = get_connection()
    cur = conn.cursor()
    cur.execute("""
        SELECT root, COUNT(*) AS occurrence_count
        FROM words
        WHERE root IS NOT NULL AND root != ''
        GROUP BY root
    """)
    counts = {row["root"]: row["occurrence_count"] for row in cur.fetchall()}
    conn.close()

    mconn = get_meanings_connection()
    mcur = mconn.cursor()
    mcur.execute("SELECT * FROM roots ORDER BY stripped")
    rows = [dict(r) for r in mcur.fetchall()]
    mconn.close()

    roots = []
    for r in rows:
        short = r.get("short_override") or (
            r.get("short_meaning") if r.get("short_confident") else None
        )
        roots.append({
            "root": r["root"],
            "meaning": r.get("meaning"),
            "short": short,
            "occurrence_count": counts.get(r["root"], 0),
        })

    return roots