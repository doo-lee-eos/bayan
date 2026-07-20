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
    """
    Compares NORMALIZED query against the NORMALIZED lemma column.

    Guards against the empty string: NORMALIZE() (registered in
    _register_normalize) maps a NULL lemma to "", and normalize() maps a
    punctuation-only/diacritic-only query (e.g. a bare waqf mark like
    "ۚ") to "" as well. Without this guard, WHERE NORMALIZE(lemma) = ''
    would match *any* row with no lemma recorded, and -- with no ORDER BY
    -- silently return whichever such row happens to come first in table
    order, rather than reporting "no match" for what is not actually a
    word.
    """
    normalized_query = normalize(query)
    if not normalized_query:
        return None

    cur.execute("""
        SELECT *
        FROM words
        WHERE NORMALIZE(lemma) = ?
        LIMIT 1
    """, (normalized_query,))
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


def fetch_occurrences_by_phrase(cur, tokens):
    """
    Finds every place in the Qur'an where `tokens` occur as a run of
    CONSECUTIVE words (same surah/ayah, word_number increasing by exactly
    1 per position) -- i.e. an occurrence of the whole PHRASE, as opposed
    to looping fetch_occurrences_by_word/root over each token separately,
    which would surface any ayah containing any ONE of the words on its
    own, regardless of whether they ever actually sit next to each other.

    Each position is matched the same way an individual word search's
    first three stages would (surface, then stripped, then normalized --
    see search_surface/search_stripped/search_normalized above). Lemma
    matching is deliberately left out here: it's loose on its own, and
    applying it across several positions in the same query would let
    entirely unrelated ayat surface just because they share inflected
    forms of the same roots in the same slots.

    Returns a FLAT list with N rows per match (N = len(tokens)), one row
    per token position, each carrying that position's own `surface`
    alongside the shared surah/ayah -- not one row per match. This
    intentionally mirrors fetch_occurrences_by_root's own shape, where
    occurrence_list()'s existing same-surah+ayah grouping already
    collapses consecutive same-ayah rows into a single verse card and
    highlights every one of that card's `surface` values inside the verse
    text; giving it N rows per match here (instead of one) is what makes
    it highlight the whole phrase, not just one word of it, without any
    changes to occurrence_list() itself.

    Returns [] if `tokens` is empty or has fewer than 2 entries (a
    "phrase" of zero or one word isn't a phrase-adjacency search at all).
    """

    if not tokens or len(tokens) < 2:
        return []

    n = len(tokens)

    from_sql = ["words w0"]
    for i in range(1, n):
        from_sql.append(
            f"JOIN words w{i} "
            f"ON w{i}.surah = w0.surah AND w{i}.ayah = w0.ayah "
            f"AND w{i}.word_number = w0.word_number + {i}"
        )

    where_sql = []
    params = []
    for i, token in enumerate(tokens):
        where_sql.append(f"(w{i}.surface = ? OR w{i}.stripped = ? OR w{i}.normalized = ?)")
        params.extend([token, strip_harakat(token), normalize(token)])

    select_sql = ", ".join(
        f"w{i}.surface AS surface_{i}, w{i}.word_number AS word_number_{i}"
        for i in range(n)
    )

    cur.execute(
        f"""
        SELECT w0.surah, w0.ayah, v.text, t.text AS translation, {select_sql}
        FROM {' '.join(from_sql)}
        JOIN verses v
            ON v.surah = w0.surah AND v.ayah = w0.ayah
        LEFT JOIN translations t
            ON t.surah = w0.surah AND t.ayah = w0.ayah
           AND t.translator = 'sahih_international'
        WHERE {' AND '.join(where_sql)}
        ORDER BY w0.surah, w0.ayah, w0.word_number
        """,
        params,
    )

    occurrences = []
    for row in cur.fetchall():
        for i in range(n):
            occurrences.append({
                "surface": row[f"surface_{i}"],
                "surah": row["surah"],
                "ayah": row["ayah"],
                "word_number": row[f"word_number_{i}"],
                "text": row["text"],
                "translation": row["translation"],
            })

    return occurrences


# ==========================================================
# Phrase lookup
# ==========================================================

def lookup_phrase(query):
    """
    Runs the exact same per-word lookup() used for a single-word search
    over every whitespace-separated token in `query`, so a phrase gets
    broken down word-by-word instead of being searched as one literal
    (multi-word) string against columns that only ever hold single words.

    Always returns {"type": "phrase", "query": query, "words": [...]},
    where each entry of "words" is itself a full lookup() result dict
    (type "word" / "root" / "camel_root" / "none") for that token, in the
    same order the words appeared in the original phrase -- so the
    template can render each one with the exact same word/root/none
    breakdown it already uses for a single-word search.

    Tokens that are punctuation/annotation-only once harakat is stripped
    (a stray "،" or a waqf/pause mark like "ۚ" split off by whitespace)
    are dropped before lookup() runs, so the word count/order in the
    rendered breakdown matches the number of actual *words* the person
    typed -- not the raw number of whitespace-separated pieces, which can
    include a mark that isn't a word at all.

    If, after dropping those non-word tokens, fewer than 2 real words are
    left, this ISN'T a phrase -- it's a single word (optionally trailed
    by a pause mark, e.g. "وَٱلْـَٔاخِرَةَ ۚ"), or nothing at all. Wrapping
    a single word in {"type": "phrase", "words": [...]} would still make
    the template render the multi-word "phrase breakdown" UI around it
    (just showing "1 of 1" instead of "1 of 2"), which is the wrong view
    for what the person actually typed. So in that case this delegates
    straight to lookup() and returns ITS result type ("word" / "root" /
    "camel_root" / "none") instead, unwrapped -- same shape a plain
    single-word search would return, regardless of the stray whitespace
    or mark that was trimmed off. "phrase" is now only ever returned when
    there are genuinely 2+ real words being searched as a run.
    """

    query = (query or "").strip()

    # Split on whitespace, then drop any token that has no letters left
    # once harakat/waqf marks are stripped (e.g. a bare pause mark like
    # "ۚ" pasted with its conventional leading space -- see
    # strip_harakat()'s docstring). Such tokens aren't words at all, so
    # running them through lookup() only invites a spurious match (see
    # search_lemma above); dropping them here keeps the word count/order
    # in sync with what a person would actually call "the words" of the
    # phrase.
    tokens = [t for t in query.split() if strip_harakat(t)]

    if len(tokens) < 2:
        return lookup(tokens[0]) if tokens else {"type": "none", "query": query}

    conn = get_connection()
    _register_normalize(conn)
    cur = conn.cursor()
    phrase_occurrences = fetch_occurrences_by_phrase(cur, tokens)
    conn.close()

    # phrase_occurrences is flat (N rows per match -- see
    # fetch_occurrences_by_phrase's docstring), so the actual number of
    # ayat this phrase occurs in is that length divided back down by the
    # token count, not the raw row count itself.
    occurrence_count = len(phrase_occurrences) // len(tokens) if tokens else 0

    return {
        "type": "phrase",
        "query": query,
        "words": [lookup(token) for token in tokens],
        "phrase_occurrences": phrase_occurrences,
        "phrase_occurrence_count": occurrence_count,
    }


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