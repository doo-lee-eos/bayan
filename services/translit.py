"""
Latin transliteration -> Qur'anic Arabic, forgiving about spelling.

Someone types "allah", "rahman", "ar-rahmaan", "subhanallah" or "sabr" and
gets the Qur'anic words that sound like it, best first.

How it matches
--------------
Every distinct Qur'anic word form (and every lemma) is reduced to a
CONSONANT KEY: its letters mapped to sound classes, with the letters a
learner's spelling rarely shows reliably taken out --

  * alif / hamza / ʿayn / alif maqsura  -> dropped (people write them as a
    vowel, an apostrophe or nothing at all)
  * letters English speakers write the same way are merged:
    ت ط -> t,  س ص ث -> s,  د ض -> d,  ذ ز ظ -> z,  ح ه ة -> h,  ك ق -> k
  * doubled letters collapse (shadda is rarely typed twice)

The typed text gets the same treatment (sh -> ش, kh -> خ, gh -> غ,
th/dh -> both ث/ذ readings, q/c/ck -> k, vowels dropped, "ee"/"oo"/"ou"
read as long vowels ...). Words whose key equals the typed key, or is one
edit away (a letter missing, extra or swapped -- weak letters و/ي and h
count as half an edit), are candidates.

Candidates are then ranked by how close a full romanisation of the
vowelled Arabic is to what was typed, and by how often the word occurs,
so "allah" puts ٱللَّه first and "rahman" puts ٱلرَّحْمَٰن first.
"""
import re
import unicodedata
from difflib import SequenceMatcher
from functools import lru_cache

from services.database import get_connection

ARABIC_RE = re.compile(r"[؀-ۿ]")
LATIN_RE = re.compile(r"[A-Za-zÀ-ɏḀ-ỿ]")
MIN_SCORE = 2.0      # below this a "match" is noise (e.g. "zzz")

# ---------------------------------------------------------------- Arabic side
_A_CLASS = {
    "ب": "b", "ت": "t", "ط": "t", "ث": "s", "س": "s", "ص": "s", "ج": "j",
    "ح": "h", "ه": "h", "ة": "h", "خ": "x", "د": "d", "ض": "d", "ذ": "z",
    "ز": "z", "ظ": "z", "ر": "r", "ش": "c", "غ": "g", "ف": "f", "ق": "k",
    "ك": "k", "ل": "l", "م": "m", "ن": "n", "و": "w", "ي": "y",
}
_A_DROP = set("اأإآٱءؤئىع")
_MARKS = re.compile(r"[ً-ٰٟۖ-ۭـ]")


def arabic_key(word):
    """Consonant key of an Arabic word (diacritics ignored)."""
    out = []
    for ch in _MARKS.sub("", word or ""):
        if ch in _A_DROP:
            continue
        c = _A_CLASS.get(ch)
        if c and (not out or out[-1] != c):
            out.append(c)
    return "".join(out)


_ROM = {
    "ب": "b", "ت": "t", "ث": "th", "ج": "j", "ح": "h", "خ": "kh", "د": "d",
    "ذ": "dh", "ر": "r", "ز": "z", "س": "s", "ش": "sh", "ص": "s", "ض": "d",
    "ط": "t", "ظ": "z", "ع": "", "غ": "gh", "ف": "f", "ق": "q", "ك": "k",
    "ل": "l", "م": "m", "ن": "n", "ه": "h", "ة": "h", "و": "w", "ي": "y",
    "ء": "", "أ": "", "إ": "", "ؤ": "", "ئ": "", "ا": "a", "ٱ": "", "آ": "a",
    "ى": "a",
}
_VOW = {"َ": "a", "ِ": "i", "ُ": "u", "ً": "an", "ٍ": "in", "ٌ": "un"}


def romanise(word):
    """Loose romanisation of a vowelled Arabic word, in the same simplified
    spelling the typed text is normalised to (see _norm_latin)."""
    text = (word or "").replace("ٰ", "ا")
    # small waw / small ya after a pronoun ha are its long vowel (ـهُۥ hu, ـهِۦ hi)
    text = text.replace("\u06E5", "و").replace("\u06E6", "ي")
    text = re.sub(r"[ۖ-ۭـ]", "", text)
    out = []
    longs = []            # same as out, with long vowels in capitals (A I U)
    i = 0
    letters = list(text)
    # the vowel on the last letter is usually a case ending people leave
    # off when typing (لِلَّهِ is typed "lillah"), so it is not romanised
    last = max((n for n, ch in enumerate(letters) if not re.match(r"[\u064B-\u0652]", ch)), default=-1)
    while i < len(letters):
        ch = letters[i]
        piece = long_piece = None
        if ch in ("\u064B", "\u064C", "\u064D"):
            pass                                   # tanwin: a case ending, not typed
        elif ch == "ا" and i > 0 and letters[i - 1] == "\u064B":
            pass                                   # the alif that carries -an
        elif ch in _VOW:
            if i < last:
                piece = _VOW[ch]
        elif ch == "\u0651":          # shadda: double the previous consonant
            if out and out[-1] and out[-1][-1] not in "aiuAIU":
                piece = out[-1]
        elif ch == "\u0652":
            pass
        elif ch in "وي" and i + 1 < len(letters) and re.match(r"[\u064B-\u0651]", letters[i + 1]):
            piece = _ROM[ch]                       # carries its own vowel / shadda: a consonant (إِيَّاكَ)
        elif ch == "و" and out and out[-1].endswith("u"):
            piece, long_piece = "u", "U"          # long u
        elif ch == "ي" and out and out[-1].endswith("i"):
            piece, long_piece = "i", "I"          # long i
        elif ch in _ROM:
            piece = _ROM[ch]
            if ch in "اآى":
                long_piece = "A"
        if piece is not None:
            out.append(piece)
            longs.append(long_piece or piece)
        i += 1
    r = "".join(out)
    rl = "".join(longs)
    if r.startswith("l") and text.startswith("ٱل"):
        r = "a" + r                    # article: ٱلْ -> al
        rl = "a" + rl
    return _squash(r), _squash_long(rl)


def _squash_long(s):
    """_squash, but keeping long vowels (A I U) apart from short ones."""
    s = re.sub(r"aA|Aa", "A", s)
    s = re.sub(r"iI|Ii", "I", s)
    s = re.sub(r"uU|Uu", "U", s)
    return re.sub(r"(.)\1+", r"\1", s)


def _squash(s):
    """Shared simplification for comparing two romanisations."""
    s = s.replace("aa", "a").replace("ii", "i").replace("uu", "u")
    s = re.sub(r"(.)\1+", r"\1", s)
    return s


# ----------------------------------------------------------------- Latin side
def _strip_accents(s):
    s = unicodedata.normalize("NFD", s)
    return "".join(ch for ch in s if not unicodedata.combining(ch))


def _norm_latin(q):
    q = _strip_accents(q.lower())
    # "Arabizi" digits: 2 ء, 3 ع, 5 خ, 6 ط, 7 ح, 8 غ, 9 ص
    q = q.replace("2", "'").replace("3", "'").replace("5", "kh").replace("6", "t")
    q = q.replace("7", "h").replace("8", "gh").replace("9", "s")
    q = re.sub(r"[ʿʾ'`’‘\-_.\s]", "", q)
    q = q.replace("ee", "i").replace("oo", "u").replace("ou", "u")
    q = q.replace("au", "aw").replace("ai", "ay").replace("ei", "ay")
    q = q.replace("e", "i").replace("o", "u")
    q = q.replace("ph", "f").replace("ck", "k").replace("q", "k").replace("c", "k").replace("x", "ks")
    q = q.replace("kh", "K").replace("sh", "C").replace("gh", "G").replace("dh", "D").replace("th", "T")
    return q


def latin_keys(q):
    """All consonant keys the typed text could stand for (th/dh/w/y/h
    readings branch), best-guess first."""
    q = _norm_latin(q)
    keys = [""]
    i = 0
    while i < len(q):
        ch = q[i]
        opts = None
        if ch in "aiu":
            # a long vowel might be written as و / ي in Arabic
            opts = [""] + (["w"] if ch == "u" else ["y"] if ch == "i" else [])
        elif ch == "K":
            opts = ["x"]
        elif ch == "C":
            opts = ["c"]
        elif ch == "G":
            opts = ["g"]
        elif ch == "D":
            opts = ["z", "d"]
        elif ch == "T":
            opts = ["s", "z", "t"]
        elif ch == "y":
            opts = ["y"]
        elif ch == "w" or ch == "v":
            opts = ["w"]
        elif ch in "bdfhjklmnrstz":
            opts = [ch]
        elif ch == "g":
            opts = ["g", "j"]
        elif ch == "p":
            opts = ["b"]
        else:
            opts = [""]
        keys = [k + o for k in keys for o in opts][:256]
        i += 1
    extra = []
    for k in keys:
        if k.endswith("t"):
            extra.append(k[:-1] + "h")      # salat -> صلاة (ة written h/t)
    if q.endswith("a"):
        extra += [k + "h" for k in keys]     # janna -> جنة
    keys = keys + extra
    out, seen = [], set()
    for k in keys:
        k = re.sub(r"(.)\1+", r"\1", k)
        if k and k not in seen:
            seen.add(k)
            out.append(k)
    return out


def is_latin_query(q):
    return bool(q) and bool(LATIN_RE.search(q)) and not ARABIC_RE.search(q)


# ------------------------------------------------------------------- index
@lru_cache(maxsize=1)
def _index():
    """{key: [(surface, count, is_lemma)]} over every Qur'anic word form
    and lemma. Built once per process (about 25k entries, under a second)."""
    conn = get_connection()
    cur = conn.cursor()
    forms = {}
    for surface, n in cur.execute(
            "SELECT surface, COUNT(*) FROM words GROUP BY surface"):
        if surface:
            forms[surface] = [n, False]
    for lemma, n in cur.execute(
            "SELECT lemma, COUNT(*) FROM morphemes WHERE lemma IS NOT NULL "
            "AND features NOT LIKE '%PREF%' AND features NOT LIKE '%SUFF%' GROUP BY lemma"):
        if lemma and lemma not in forms:
            forms[lemma] = [n, True]
    conn.close()
    idx = {}
    for word, (n, is_lemma) in forms.items():
        k = arabic_key(word)
        if k:
            rom, rom_long = romanise(word)
            idx.setdefault(k, []).append((word, n, is_lemma, rom, rom_long))
    return idx


def _edits1(key):
    letters = "bcdfghjklmnrstwxyz"
    out = set()
    for i in range(len(key) + 1):
        for c in letters:
            out.add(key[:i] + c + key[i:])                 # missing letter
        if i < len(key):
            out.add(key[:i] + key[i + 1:])                 # extra letter
            for c in letters:
                out.add(key[:i] + c + key[i + 1:])         # wrong letter
            if i + 1 < len(key):
                out.add(key[:i] + key[i + 1] + key[i] + key[i + 2:])
    return out


def suggest(query, limit=8):
    """Arabic candidates for a Latin query, best first:
    [{"arabic", "count", "score"}]. Empty if nothing is close."""
    if not is_latin_query(query):
        return []
    idx = _index()
    # for ranking keep q distinct from k (qul -> قُلْ before كُلّ)
    keep_q = _strip_accents(query.lower()).replace("q", "§")
    typed_plain = _squash(re.sub(r"[KCGDT]", lambda m: {"K": "kh", "C": "sh", "G": "gh", "D": "dh", "T": "th"}[m.group()],
                                 _norm_latin(keep_q).replace("§", "q")))
    typed_plain = typed_plain.replace("dh", "th")
    marked = _strip_accents(query.lower())
    for a, b in (("aa", "①"), ("ee", "②"), ("ii", "②"), ("oo", "③"), ("uu", "③"), ("ou", "③")):
        marked = marked.replace(a, b)
    marked = marked.replace("q", "§")
    typed_long = _squash_long(re.sub(r"[KCGDT]", lambda m: {"K": "kh", "C": "sh", "G": "gh", "D": "dh", "T": "th"}[m.group()],
                                     _norm_latin(marked)).replace("§", "q").replace("①", "A").replace("②", "I").replace("③", "U"))
    typed_long = typed_long.replace("dh", "th")
    keys = latin_keys(query)
    if not keys:
        return []
    scored = {}

    def consider(key, penalty, no_article=False):
        for word, n, is_lemma, rom, rom_long in idx.get(key, ()):
            if no_article and word.startswith(("ٱل", "ال")):
                continue
            # th / dh are both used for ذ (and th for ث): compare them as one sound
            r = rom.replace("aa", "a").replace("dh", "th")
            if "q" not in typed_plain:
                r = r.replace("q", "k")
            sim = SequenceMatcher(None, typed_plain, r).ratio()
            # a typed long vowel (aalem, raheem, nooh) should prefer a word
            # that really has one there (عَالِم over عَلِيم)
            rl = rom_long.replace("dh", "th")
            if "q" not in typed_plain:
                rl = rl.replace("q", "k")
            sim = 0.75 * sim + 0.25 * SequenceMatcher(None, typed_long, rl).ratio()
            if "th" in r:   # ذ is also often typed z ("allazina", "zikr")
                sim = max(sim, SequenceMatcher(None, typed_plain, r.replace("th", "z")).ratio() - 0.03)
            if typed_plain.endswith("t") and r.endswith(("ah", "a")):
                sim = max(sim, SequenceMatcher(None, typed_plain, re.sub("h?$", "", r) + "t").ratio())
            # an article typed or not: compare without it too
            if r.startswith("al") and not typed_plain.startswith("al"):
                sim = max(sim, SequenceMatcher(None, typed_plain, r[2:]).ratio())
            score = sim * 3 - penalty + min(n, 2000) ** 0.25 * 0.09 + (0.1 if is_lemma else 0)
            # an attached pronoun typed at the end (-hi, -hu, -ha, -hum, -ka, -kum, -na)
            # should land on a form that really ends in it
            pm = re.search(r"(hum|hun|kum|hi|hu|ha|ka|ki|na|ya)$", typed_plain)
            if pm and r.endswith(pm.group(1)):
                score += 0.2
            if word not in scored or scored[word][0] < score:
                scored[word] = (score, n)

    for rank, k in enumerate(keys[:24]):
        consider(k, 0.02 * rank)
    # the same word with a joined li-/bi-/wa-/fa-/ka- in the Qur'an
    # (sahibihi is only ever لِصَٰحِبِهِۦ)
    for k in keys[:4]:
        for p in "lbwfk":
            consider(p + k, 0.15, no_article=True)
    if len(scored) < limit:
        for k in keys[:4]:
            for e in _edits1(k):
                if e in idx:
                    # weak letters and h are often missed or added by learners
                    diff = set(e) ^ set(k)
                    consider(e, 0.35 if diff <= set("wyh") else 0.7)
    best = sorted(scored.items(), key=lambda x: -x[1][0])
    # one entry per bare spelling (diacritic variants of the same word)
    out, seen = [], set()
    for word, (score, n) in best:
        bare = _MARKS.sub("", word).replace("ٱ", "ا")
        if bare in seen:
            continue
        seen.add(bare)
        if score < MIN_SCORE:
            break
        out.append({"arabic": word, "count": n, "score": round(score, 3)})
        if len(out) >= limit:
            break
    return out


@lru_cache(maxsize=1)
def _root_index():
    conn = get_connection()
    rows = conn.execute("SELECT DISTINCT root FROM morphemes WHERE root IS NOT NULL").fetchall()
    conn.close()
    idx = {}
    for (root,) in rows:
        idx.setdefault(arabic_key(root), []).append(root)
    return idx


def suggest_roots(latin_letters, limit=6):
    """Roots for a Latin spelling of the root letters, e.g. "ktb" -> كتب,
    "rhm" -> رحم. Hamza/ʿayn/alif root letters may be typed as ' or left out."""
    keys = latin_keys(latin_letters)
    out = []
    idx = _root_index()
    for k in keys[:24]:
        for r in idx.get(k, []):
            if r not in out:
                out.append(r)
    return out[:limit]


def resolve(query):
    """For the search route. Returns None when the query is not Latin;
    otherwise {"typed", "arabic" (what to search, or None), "alternatives",
    "kind": "word" | "phrase" | "root"}."""
    q = (query or "").strip()
    if not is_latin_query(q.lstrip("√")):
        return None
    m = re.match(r"^(?:√|root:?\s*)\s*([A-Za-z0-9'ʿʾ`’]+)$", q, re.I)
    if m:
        roots = suggest_roots(m.group(1))
        return {"typed": q, "kind": "root", "arabic": ("√" + roots[0]) if roots else None,
                "alternatives": ["√" + r for r in roots[1:]]}
    phrase = known_phrase(q)
    if phrase:
        return {"typed": q, "kind": "phrase", "arabic": phrase, "alternatives": []}
    tokens = _merge_articles(q.split())
    if len(tokens) == 1:
        single = suggest(tokens[0], 8)
        top = single[0]["score"] if single else 0
        joined = _split_at_article(tokens[0].lower())
        if joined and joined[1] >= 2.9 and (top < 2.9 or joined[1] > top):
            return {"typed": q, "kind": "phrase", "arabic": " ".join(joined[0]),
                    "alternatives": [x["arabic"] for x in single[:5]]}
        if top < 2.6 and len(tokens[0]) >= 6:
            parts = _best_split(tokens[0].lower())
            # only when the split reads clearly better than one word
            if parts and parts[1] > top + 0.4:
                return {"typed": q, "kind": "phrase", "arabic": " ".join(parts[0]),
                        "alternatives": [x["arabic"] for x in single[:5]]}
        return {"typed": q, "kind": "word", "arabic": single[0]["arabic"] if single else None,
                "alternatives": [x["arabic"] for x in single[1:]]}
    if len(tokens) > 1:
        picked = []
        for t in tokens:
            s = suggest(t, 1)
            if not s:
                return {"typed": q, "kind": "phrase", "arabic": None, "alternatives": []}
            picked.append(s[0]["arabic"])
        return {"typed": q, "kind": "phrase", "arabic": " ".join(picked), "alternatives": []}
    s = suggest(q, 8)
    return {"typed": q, "kind": "word", "arabic": s[0]["arabic"] if s else None,
            "alternatives": [x["arabic"] for x in s[1:]]}


_ARTICLES = {"al", "el", "il", "ul", "l", "ar", "as", "ad", "an", "at", "az", "ash", "adh", "ath",
             "ur", "us", "ud", "un", "ut", "uz", "ush", "ir", "is", "id", "in", "it", "iz", "ish"}


def _merge_articles(tokens):
    """'ar rahman' -> 'arrahman' (a detached article belongs to the next word)."""
    # "aalem-ul-ghaib" -> "aalem", "ul", "ghaib"
    split = []
    for t in tokens:
        split.extend(x for x in t.split("-") if x)
    out = []
    for t in split:
        if out and out[-1].lower() in _ARTICLES and len(split) > 1:
            # 'ul ghaib', 'il-lahi': the article joins the next word, said "al"
            out[-1] = "al" + t
        else:
            out.append(t)
    return out


@lru_cache(maxsize=4096)
def _best(token):
    s = suggest(token, 1)
    return (s[0]["arabic"], s[0]["score"]) if s else (None, 0.0)


@lru_cache(maxsize=256)
def _best_split(token, parts_left=3):
    """Split a run-together phrase ("alhamdulillah", "subhanallah",
    "inshallah") into Qur'anic words. Returns ((words...), weakest score)
    or None. Every piece must be a confident match on its own."""
    best = None
    for i in range(3, len(token) - 2):
        head, tail = token[:i], token[i:]
        if head.rstrip("-") in _ARTICLES:
            continue                      # "al" alone is the article, not a word
        h = _best(head)
        if not h[0] or h[1] < 3.0:
            continue
        t = _best(tail)
        options = []
        if t[0] and t[1] >= 3.0:
            options.append(((h[0], t[0]), min(h[1], t[1])))
        if parts_left > 2 and len(tail) >= 4:
            sub = _best_split(tail, parts_left - 1)
            if sub:
                options.append(((h[0],) + sub[0], min(h[1], sub[1]) - 0.2))
        for o in options:
            if best is None or o[1] > best[1]:
                best = o
    return best


# Common phrases people type run together, which word-by-word matching
# can't split reliably. Keys are compared after _phrase_key().
_PHRASES = {
    "bismillah": "بِسْمِ ٱللَّهِ",
    "bismillahirrahmanirrahim": "بِسْمِ ٱللَّهِ ٱلرَّحْمَٰنِ ٱلرَّحِيمِ",
    "bismillahirrahmanirraheem": "بِسْمِ ٱللَّهِ ٱلرَّحْمَٰنِ ٱلرَّحِيمِ",
    "inshallah": "إِن شَآءَ ٱللَّهُ",
    "inshaallah": "إِن شَآءَ ٱللَّهُ",
    "mashallah": "مَا شَآءَ ٱللَّهُ",
    "mashaallah": "مَا شَآءَ ٱللَّهُ",
    "subhanallah": "سُبْحَٰنَ ٱللَّهِ",
    "alhamdulillah": "ٱلْحَمْدُ لِلَّهِ",
    "allahuakbar": "ٱللَّهُ أَكْبَرُ",
    "lailahaillallah": "لَآ إِلَٰهَ إِلَّا ٱللَّهُ",
    "lailahaillaallah": "لَآ إِلَٰهَ إِلَّا ٱللَّهُ",
    "innalillahiwainnailayhirajiun": "إِنَّا لِلَّهِ وَإِنَّآ إِلَيْهِ رَٰجِعُونَ",
    "innalillahwainnailaihirajiun": "إِنَّا لِلَّهِ وَإِنَّآ إِلَيْهِ رَٰجِعُونَ",
    "hasbunallahwanimalwakil": "حَسْبُنَا ٱللَّهُ وَنِعْمَ ٱلْوَكِيلُ",
    "lahawlawalaquwwataillabillah": "لَا قُوَّةَ إِلَّا بِٱللَّهِ",
    "laquwwataillabillah": "لَا قُوَّةَ إِلَّا بِٱللَّهِ",
    "astaghfirullah": "أَسْتَغْفِرُ ٱللَّهَ",
    "assalamualaykum": "سَلَٰمٌ عَلَيْكُمْ",
    "salamunalaykum": "سَلَٰمٌ عَلَيْكُمْ",
}


def _phrase_key(q):
    q = _strip_accents(q.lower())
    q = re.sub(r"[^a-z]", "", q)
    q = q.replace("ee", "i").replace("oo", "u").replace("ei", "ay").replace("ai", "ay").replace("e", "i").replace("o", "u")
    return re.sub(r"(.)\1+", r"\1", q)


_PHRASE_KEYS = {_phrase_key(k): v for k, v in _PHRASES.items()}


def known_phrase(q):
    return _PHRASE_KEYS.get(_phrase_key(q))


_PARTICLES = {"fi", "bi", "li", "wa", "fa", "ka", "la", "ma", "an", "in", "min", "ala", "ila", "inna", "anna", "lam", "lan", "qad"}
_SUN = r"(sh|th|dh|[tdsrzn])"


def _piece(text):
    """Best Arabic for one piece of a run-together phrase, or (None, 0).
    Short particles (fi, bi, min ...) are allowed as pieces."""
    if len(text) < 3 and text not in _PARTICLES:
        return (None, 0.0)
    a, sc = _best(text)
    if text in _PARTICLES:
        sc = max(sc, 3.0)
    return (a, sc)


@lru_cache(maxsize=256)
def _split_at_article(token):
    """Run-together words joined by the article: 'abdullah' (abd + allah),
    'rasulullah', 'ummulkitab' (umm + al-kitab), 'yawmiddin' (yawm + ad-din),
    'fisabilillah' (fi + sabil + allah). Returns ((words...), weakest score)."""
    best = None
    for i in range(2, len(token) - 3):
        head, tail = token[:i], token[i:]
        if tail[0] not in "ui":
            continue
        rest = tail[1:]
        m = re.match(_SUN + r"\1", rest)
        if rest.startswith("l"):
            second = "al" + rest[1:]
        elif m:
            second = "al" + rest[len(m.group(1)):]
        else:
            continue
        t = _best(second) if len(second) > 3 else (None, 0.0)
        if not t[0]:
            continue
        h = _piece(head)
        heads = []
        if h[0] and h[1] >= 2.9:
            heads.append(((h[0],), h[1]))
        for j in range(2, len(head) - 1):           # fi + sabil
            a1, a2 = _piece(head[:j]), _piece(head[j:])
            if a1[0] and a2[0] and min(a1[1], a2[1]) >= 2.9:
                heads.append(((a1[0], a2[0]), min(a1[1], a2[1]) - 0.1))
        for words, sc in heads:
            cand = (words + (t[0],), min(sc, t[1]))
            if best is None or cand[1] > best[1]:
                best = cand
    return best