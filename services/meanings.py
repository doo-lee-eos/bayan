import html
import re
import unicodedata

from spellchecker import SpellChecker

from services.database import get_connection, get_meanings_connection
from services.normalize import strip_harakat
from services.camel import camel_stem_gloss, strip_definite_article_gloss, expected_voice_from_features


# ==========================================================
# Matching key
# ==========================================================
#
# meanings.db's prefixes/suffixes/roots/lemmas tables are hand-curated with
# canonical, undiacritized spellings. Corpus morphemes carry full Qur'anic
# diacritics plus a few Uthmani-script decorations (dagger alif, small waw/
# yeh signs) that need folding down before they'll match a curated entry.
#
# This is deliberately a bit more aggressive than the plain strip_harakat()
# used elsewhere for searching:
#   - dagger alif (\u0670) is folded to a full alif "ا", not deleted —
#     it stands in for a missing alif letter in Qur'anic spelling, so
#     dropping it entirely would lose a real letter (e.g. "نَٰ" ~ "نا").
#   - alif maqsura (ى) is folded to yeh (ي) — pure spelling variation.
#   - ta marbuta (ة) is folded to ه — pure spelling variation.
#   - hamza forms (أ/إ/آ vs ا) are deliberately left DISTINCT, since your
#     own prefixes table treats them as different morphemes (e.g. "أ" is
#     tagged as the 1st-person imperfect marker, not folded into "ا").

# meanings.db's prefixes/suffixes/roots/lemmas tables are hand-curated with
# canonical, undiacritized spellings, computed the same way as every other
# `stripped` column in this project: strip_harakat() deletes combining marks
# (including dagger alif, \u0670) outright rather than expanding them.
# On top of that baseline, two further spelling-only folds are applied here:
#   - alif maqsura (ى) -> yeh (ي)
#   - ta marbuta (ة) -> ه
# Hamza forms (أ/إ/آ vs ا) are deliberately left distinct, since your own
# prefixes table treats them as different morphemes (e.g. "أ" is tagged as
# the 1st-person imperfect marker, not folded into "ا").

def _meaning_key(text):
    if not text:
        return ""
    text = strip_harakat(text)
    text = text.replace('\u0649', '\u064A')   # ى -> ي
    text = text.replace('\u0629', '\u0647')   # ة -> ه
    return text


# ==========================================================
# Pronoun-suffix disambiguation via `features`
# ==========================================================
#
# Several attached-pronoun suffixes in this corpus share the EXACT same
# surface -- even fully diacritized -- across different person/gender/
# number combinations. Confirmed directly against quran.db's morphemes
# table, e.g.:
#   وا۟  -> PRON|SUFF|3MP ("they [did]") in some occurrences,
#           PRON|SUFF|2MP ("you all, do!") in others
#   نَ   -> PRON|SUFF|3FP in some occurrences, PRON|SUFF|2FP in others
#   تَ / تُ / تِ  -> collapse to the identical stripped text "ت" despite
#           being 2MS / 1S / 2FS respectively
# A `suffixes` table row keyed on surface/stripped text can only ever
# hold ONE meaning -- it is structurally unable to disambiguate the
# rest, no matter how many rows are added. The corpus's own `features`
# tag (e.g. "PRON|SUFF|3MP") is already resolved per-occurrence by the
# QAC annotators using context this code doesn't have (verb aspect,
# mood, etc.), so it's consulted here FIRST, ahead of the ordinary
# surface/stripped DB lookup, for this specific closed set of suffixes.
#
# Residual limitation: `features` still can't distinguish a verb's own
# SUBJECT-agreement suffix ("we [did]") from an attached OBJECT/
# possessive pronoun ("us"/"our") for 1st- and 3rd-person suffixes --
# the corpus uses the same tag (e.g. "1P") for both roles, and there's
# no verb-aspect/attachment column here to tell them apart. Rather than
# confidently pick one, the gloss spells out both possibilities for
# those specific tags.

_PRON_SUFFIX_MEANINGS = {
    "1S":  ("I / me / my", "1st person singular"),
    "2MS": ("you", "2nd person masculine singular"),
    "2FS": ("you", "2nd person feminine singular"),
    "3MS": ("he / him / his", "3rd person masculine singular"),
    "3FS": ("she / her", "3rd person feminine singular"),
    "1P":  ("we / us / our", "1st person plural"),
    "2MP": ("you all", "2nd person masculine plural"),
    "2FP": ("you all", "2nd person feminine plural"),
    "3MP": ("they / them", "3rd person masculine plural"),
    "3FP": ("they / them", "3rd person feminine plural"),
    "2D":  ("you two", "2nd person dual"),
    "3D":  ("they two", "3rd person dual"),
    "3MD": ("they two", "3rd person masculine dual"),
    "3FD": ("they two", "3rd person feminine dual"),
}

# ==========================================================
# Extra pronoun word-forms for stripping (not for display)
# ==========================================================
#
# `_PRON_SUFFIX_MEANINGS` above is written for the SUFFIX card -- it only
# needs to list the forms that card should show. But `words.english`
# leaks a PRON|SUFF suffix's own contribution the same way it leaks a
# PREF's (see `_strip_leading_affix_glosses`), and the word-form that
# shows up there is often a possessive determiner
# `_PRON_SUFFIX_MEANINGS` never spells out -- confirmed directly against
# quran.db: رَبِّكُمْ (رَبّ + كُمْ, PRON|SUFF|2MP) carries english
# "your Lord", but `_PRON_SUFFIX_MEANINGS["2MP"]` only has "you all", so a
# strip attempt using that table alone would silently fail to match
# "your" and leave the whole-word gloss in place.
#
# This table exists only to make that strip attempt succeed; it is never
# shown to the user, so it doesn't need to be exhaustive or precisely
# correct the way the SUFFIX card's own text does -- listing a few
# plausible surface forms per tag is enough, since a wrong guess here
# just means the strip doesn't fire (see `_strip_leading_affix_glosses`'s
# docstring on why a missed match is the safe failure mode).
_PRON_SUFFIX_STRIP_FORMS = {
    "1S":  ("i", "me", "my"),
    "2MS": ("you", "your"),
    "2FS": ("you", "your"),
    "3MS": ("he", "him", "his"),
    "3FS": ("she", "her"),
    "1P":  ("we", "us", "our"),
    "2MP": ("you", "you all", "your"),
    "2FP": ("you", "you all", "your"),
    "3MP": ("they", "them", "their"),
    "3FP": ("they", "them", "their"),
    "2D":  ("you", "you two", "your"),
    "3D":  ("they", "they two", "their"),
    "3MD": ("they", "they two", "their"),
    "3FD": ("they", "they two", "their"),
}


# ==========================================================
# Non-pronoun suffix disambiguation via `features`
# ==========================================================
#
# ها as a SUFF morpheme is used for two entirely distinct grammatical
# roles -- confirmed directly against quran.db's morphemes table:
#   PRON|SUFF|3FS   -- the attached object/possessive pronoun "her/it"
#                       (e.g. بَيْتُهَا, "her house") -- already handled
#                       correctly above by `_pron_suffix_from_features`.
#   ATT|SUFF|LEM:ه  (154 occurrences) -- hā' al-tanbīh (the "particle of
#                       attention"), which attaches to أَيّ/أَيَّة
#                       specifically in vocative address (يَٰٓأَيُّهَا,
#                       "O you...", and أَيَّتُهَا). It is NOT a pronoun
#                       and does not mean "her/it" -- it contributes no
#                       independent meaning of its own, the way its
#                       PREF-morpheme counterpart on demonstratives
#                       (هَٰذَا, هَٰٓؤُلَآءِ -- already correctly glossed
#                       "lo, behold" in the `prefixes` table) doesn't
#                       either.
#
# `suffixes` table row keyed on stripped "ها" can only ever hold ONE
# meaning -- the pronoun sense -- so without this check, every one of
# those 154 attention-particle occurrences silently gets glossed as
# "her/it" instead.
#
# Similarly, م as a SUFF morpheme (VOC|SUFF|LEM:م, 5 occurrences) is the
# vocative particle substituting for a separate يَا, as in ٱللَّهُمَّ
# ("O Allah") -- unrelated to any pronoun sense of م, and with no row at
# all in the `suffixes` table, so it previously surfaced no gloss.
_ATT_VOC_SUFFIX_MEANINGS = {
    "ATT": ("(no separate meaning)", "Hā' al-tanbīh (particle of attention) -- attaches to أَيّ/أَيَّة in vocative address; not the attached object/possessive pronoun \u201cها\u201d (\"her/it\")."),
    "VOC": ("O", "Vocative particle -- substitutes for a separate يَا, e.g. in ٱللَّهُمَّ (\"O Allah\")."),
}


def _att_voc_suffix_from_features(features):
    """Returns a meaning dict derived directly from the morpheme's own
    `features` tag for the closed set of non-pronoun ها/م suffix roles
    that plain surface/stripped text can't disambiguate from the
    pronoun senses (see `_ATT_VOC_SUFFIX_MEANINGS` above), or None if
    `features` isn't one of those recognized tags -- in which case the
    caller falls through to `_pron_suffix_from_features` and then the
    ordinary DB lookup."""

    if not features:
        return None

    tag = features.split("|")[0]
    entry = _ATT_VOC_SUFFIX_MEANINGS.get(tag)
    if not entry:
        return None

    meaning, notes = entry
    return {"suffix": None, "meaning": meaning, "notes": notes, "stripped": None}


def _pron_suffix_from_features(features):
    """Returns a meaning dict derived directly from the morpheme's own
    `features` tag for the closed set of attached-pronoun suffixes, or
    None if `features` isn't a recognized PRON|SUFF|<tag> string (e.g.
    it's some other suffix type entirely, in which case the caller falls
    through to the ordinary DB lookup)."""

    if not features:
        return None

    parts = features.split("|")
    if len(parts) < 3 or parts[0] != "PRON" or parts[1] != "SUFF":
        return None

    entry = _PRON_SUFFIX_MEANINGS.get(parts[2])
    if not entry:
        return None

    meaning, notes = entry
    return {"suffix": None, "meaning": meaning, "notes": notes, "stripped": None}


# ==========================================================
# Individual lookups
# ==========================================================

# ==========================================================
# Prefix disambiguation via `features`
# ==========================================================
#
# The bare letter أ, as a PREF morpheme, is used for at least two entirely
# distinct grammatical roles in this corpus -- confirmed directly against
# quran.db's morphemes table (513 PREF morphemes with lemma "أ" total):
#   INTG|PREF|LEM:أ  (507 occurrences) -- interrogative particle, "is/are..?"
#   EQ|PREF|LEM:أ    (6 occurrences)   -- equivalence particle, "whether"
# NEITHER of these is "I / 1st person singular imperfect" -- that's a verb's
# own subject-agreement prefix (as in أَعْلَمُ, "I know"), which in this
# corpus's schema is apparently never split out as its own PREF morpheme at
# all (it lives inside the stem morpheme instead). So the one row this
# project's `prefixes` table has for stripped "أ" -- "I", clearly meant for
# that verb-agreement sense -- can never correctly match anything reachable
# through this lookup; it was winning by default on every single one of
# those 513 occurrences regardless of the morpheme's real role.
#
# As with `_pron_suffix_from_features`, the fix is to consult the
# morpheme's own `features` tag FIRST for this specific ambiguous surface,
# rather than trust a surface/stripped table lookup that structurally
# cannot distinguish the roles.

_PREFIX_FEATURE_MEANINGS = {
    "INTG": ("is / are", "Interrogative particle (hamzat al-istifhām)"),
    "EQ":   ("whether", "Equivalence particle (introduces \"whether...or\")"),

    # ي as a PREF morpheme is likewise used for two entirely distinct
    # grammatical roles -- confirmed directly against quran.db's
    # morphemes table:
    #   VOC|PREF|LEM:ي  (361 occurrences) -- the vocative particle "yā"
    #                                         ("O..."), e.g. the ي in
    #                                         يَٰٓأَيُّهَا ("O you...")
    # NEITHER of these is an imperfect verb's own subject-agreement
    # prefix ("he ...", as in يَفْعَلُ) -- that sense, like the "أ" case
    # above, lives inside the verb's own stem morpheme in this corpus's
    # schema and is never split out as its own PREF morpheme. But the
    # `prefixes` table's one curated row for stripped "ي" is "he / 3rd
    # person masculine imperfect", clearly meant for that verb-agreement
    # sense -- so, exactly as with "أ", it was winning by default on
    # every one of those 361 vocative occurrences too.
    "VOC":  ("O", "Vocative particle (yā al-nidā') -- calls out to someone/something; not the imperfect-verb subject-agreement \u201cي\u201d."),

    # لَ as a PREF morpheme is used for at least four distinct
    # grammatical roles in this corpus -- confirmed directly against
    # quran.db's morphemes table, every one of them sharing the same
    # lemma "ل" (so a plain lemma lookup against the `prefixes` table
    # collapses all of them down to that table's single row for "ل":
    # the ordinary preposition "for, to"):
    #
    #   P|PREF|LEM:ل     (2449 occurrences) -- ordinary preposition, "for, to"
    #                                          (already resolves correctly via
    #                                          the `prefixes` table -- untouched)
    #   EMPH|PREF|LEM:ل  (1001 occurrences) -- emphatic lam (lām al-tawkīd),
    #                                          e.g. لَقَدْ "certainly/indeed"
    #   PRP|PREF|LEM:ل   ( 319 occurrences) -- purpose lam (lām al-taʿlīl),
    #                                          e.g. لِيُحَآجُّوكُم "so that
    #                                          they may argue with you"
    #   IMPV|PREF|LEM:ل  (  78 occurrences) -- imperative/jussive lam
    #                                          (lām al-amr), e.g. فَلْيَصُمْهُ
    #                                          "then let him fast [in] it"
    #
    # These three tags never occur on any lemma other than "ل" among PREF
    # morphemes, so -- unlike the وَ-prefix table below -- they don't need
    # to be lemma-gated in `_prefix_from_features`; a bare tag match is
    # unambiguous on its own.
    "EMPH": ("indeed", "Emphatic particle (lām al-tawkīd, \"lam of emphasis\")"),
    "PRP":  ("so that", "Purpose particle (lām al-taʿlīl, \"lam of purpose\")"),
    "IMPV": ("let", "Imperative/jussive particle (lām al-amr, \"lam of command\")"),
}

# ==========================================================
# وَ-prefix disambiguation via `features`
# ==========================================================
#
# The bare letter و, as a PREF morpheme, is used for AT LEAST six
# distinct grammatical roles in this corpus -- confirmed directly
# against quran.db's morphemes table (every PREF morpheme with
# lemma "و" is tagged "P" at the coarse `tag` column, but the FIRST
# token of its `features` string tells the roles apart):
#
#   CONJ|PREF|LEM:و  (8177 occurrences) -- coordinating conjunction, "and"
#   REM|PREF|LEM:و   (1034 occurrences) -- resumption particle, "and"
#   CIRC|PREF|LEM:و  ( 293 occurrences) -- circumstantial particle, "while"
#   SUP|PREF|LEM:و   (  59 occurrences) -- supplemental particle, "and"
#   P|PREF|LEM:و     (  28 occurrences) -- OATH particle, "by" (e.g. وَالْعَصْرِ
#                                          "By time", Al-'Asr 103:1)
#   COM|PREF|LEM:و   (   3 occurrences) -- comitative particle, "together with"
#
# `prefixes.stripped = 'و'` can only ever hold ONE curated row, so a
# plain surface/lemma lookup collapses every one of these down to
# whatever that single row says (here: "and") -- including the 28 oath
# occurrences, which is the bug this table exists to fix.
#
# The `features` FIRST TOKEN alone isn't reusable across every prefix,
# though: "P" is also the everyday tag QAC gives ب/ل/ك/ت's own ordinary
# (non-ambiguous) prepositional roles, which already resolve correctly
# via the `prefixes` table lookup below. So this table is deliberately
# keyed and consulted ONLY when the morpheme's lemma is و itself --
# see `_prefix_from_features` -- to avoid clobbering those other
# prefixes' meanings.
_WAW_FEATURE_MEANINGS = {
    "CONJ": ("and", "Coordinating conjunction"),
    "REM":  ("and", "Resumption particle (marks the start of a new sentence)"),
    "CIRC": ("while", "Circumstantial particle"),
    "SUP":  ("and", "Supplemental particle"),
    "P":    ("by", "Oath particle (wāw al-qasam, \"by\")"),
    "COM":  ("together with", "Comitative particle"),
}


def _prefix_from_features(features, lemma=None):
    """Returns a meaning dict derived directly from the morpheme's own
    `features` tag for the closed sets of ambiguous prefix roles that
    plain surface/lemma text can't disambiguate (see `_PREFIX_FEATURE_
    MEANINGS` and `_WAW_FEATURE_MEANINGS` above), or None if `features`
    isn't one of those recognized tags -- in which case the caller falls
    through to the ordinary DB lookup."""

    if not features:
        return None

    tag = features.split("|")[0]

    # وَ specifically: only consult the waw-specific table when this
    # morpheme's own lemma really is و, so "P" (etc.) doesn't get
    # reinterpreted for ب/ل/ك/ت's own P-tagged (but unambiguous) rows.
    if _meaning_key(lemma) == "و":
        entry = _WAW_FEATURE_MEANINGS.get(tag)
        if entry:
            meaning, notes = entry
            return {"prefix": None, "meaning": meaning, "notes": notes, "stripped": None}

    entry = _PREFIX_FEATURE_MEANINGS.get(tag)
    if not entry:
        return None

    meaning, notes = entry
    return {"prefix": None, "meaning": meaning, "notes": notes, "stripped": None}


def get_prefix_meaning(cur, morpheme):
    """Tries the morpheme's own `features` tag first, for the closed set of
    hamza-prefix and وَ-prefix roles that plain surface/lemma text can't
    disambiguate (see `_prefix_from_features`). Otherwise tries `lemma`
    (this is populated and reliable for PREF morphemes in this corpus),
    falling back to `surface` if lemma is missing."""

    from_features = _prefix_from_features(morpheme.get("features"), morpheme.get("lemma"))
    if from_features:
        return from_features

    for candidate in (morpheme.get("lemma"), morpheme.get("surface")):
        key = _meaning_key(candidate)
        if not key:
            continue
        cur.execute("SELECT * FROM prefixes WHERE stripped = ?", (key,))
        row = cur.fetchone()
        if row:
            return dict(row)

    return None


def get_suffix_meaning(cur, morpheme):
    """SUFF morphemes in this corpus usually have `lemma = NULL` (the
    grammatical info lives in `features` instead), so `surface` is tried
    first here — the opposite priority to prefixes.

    Attached-pronoun suffixes (features starting "PRON|SUFF|") are
    resolved from `features` directly first -- see
    `_pron_suffix_from_features` for why surface/stripped text alone
    can't be trusted for this suffix family. The non-pronoun ها/م
    suffixes (features starting "ATT|" or "VOC|") are resolved from
    `features` next, for the same reason -- see
    `_att_voc_suffix_from_features`."""

    from_features = _pron_suffix_from_features(morpheme.get("features"))
    if from_features:
        return from_features

    from_att_voc = _att_voc_suffix_from_features(morpheme.get("features"))
    if from_att_voc:
        return from_att_voc

    for candidate in (morpheme.get("surface"), morpheme.get("lemma")):
        key = _meaning_key(candidate)
        if not key:
            continue
        cur.execute("SELECT * FROM suffixes WHERE stripped = ?", (key,))
        row = cur.fetchone()
        if row:
            return dict(row)

    return None


# ==========================================================
# Junk lemma meanings
# ==========================================================
#
# `lemmas.meaning` is mostly short and that's fine BY DESIGN -- particles
# and conjunctions genuinely do have one- or two-word glosses (e.g. أَو ->
# "or", عَلَى -> "on", إِنّ -> "that"). So a length- or stopword-based
# filter would be wrong here: it'd flag a large fraction of legitimately
# correct short glosses as junk.
#
# What actually shows up broken is a small, specific set of leftover
# lexicographic cross-reference markers that ended up stored as the
# meaning itself instead of being resolved -- e.g. "Synonym" or "See"
# where the curator/importer meant "this lemma's gloss is a synonym of
# [some other entry]" or "see [some other entry]" but only the marker
# word survived. These are meta-commentary about the data, not glosses
# of the word, so they're checked for by exact value rather than by any
# property of length or wordlist membership.
_JUNK_LEMMA_MEANINGS = {
    "synonym", "synonyms", "see", "see also", "cf", "cf.",
    "n/a", "na", "tbd", "unknown", "-", "?", "",
}


def _is_junk_lemma_meaning(text):
    if not text:
        return True
    return text.strip().strip(".").lower() in _JUNK_LEMMA_MEANINGS


# ==========================================================
# Suspicious curated lemma meanings (word-family use only)
# ==========================================================
#
# Separate from _is_junk_lemma_meaning above -- that catches leftover
# lexicographic markers ("Synonym", "See"). This catches a different,
# worse problem confirmed by spot-checking real rows: a meaningful chunk
# of `lemmas.meaning` -- verbs especially, since that table has no
# part-of-speech column to signal "this needs a verb-shaped gloss" -- is
# not a mistranslation so much as not a translation at all: bare
# transliterations of the Arabic passed through as if they were English
# ("Etaa" for إِيتاء, "Qasm" for قَصَمْ, "Yessir" for يَسْرِ), first-person
# phrasing no citation-form dictionary gloss would use ("When I come",
# "We get fucked"), or outright vulgar slang.
#
# This can only be a heuristic, not a semantic check -- it has no way to
# know "ball" is the wrong meaning for كَرَّهَ ("to hate") when "ball" is a
# perfectly good English word. What it CAN catch reliably:
#   - a single-word meaning that isn't a real English word at all (the
#     transliteration cases above all fail this)
#   - a standalone first/second-person pronoun in the meaning text (no
#     legitimate entry in this table's correct rows phrases a gloss that
#     way -- they read like "womb" or "to speak, to talk, to address",
#     never "I ..." or "we ...")
#   - a short, fixed list of explicit vulgar terms
#
# `_SPELLCHECKER` is loaded once at import time from the `pyspellchecker`
# package's bundled English word-frequency corpus (no network access
# needed at runtime). This is used instead of a bare dictionary headword
# list specifically because it needs to recognize ordinary inflected
# forms (plurals, past tense, gerunds, comparatives -- "newspapers",
# "displaced", "reaped", "greatest") as real English, which a headword-
# only list does not; testing a headword-only list here produced an
# unacceptable false-positive rate, flagging plainly correct curated
# glosses as suspicious just because the inflected form wasn't a
# dictionary entry on its own.
_SPELLCHECKER = SpellChecker()

_FIRST_SECOND_PERSON_PRONOUNS = {
    "i", "i'm", "i've", "i'll", "i'd",
    "we", "we're", "we've", "we'll", "we'd",
    "you", "you're", "you've", "you'll", "you'd",
    "my", "our", "us", "your",
}

# Deliberately short and limited to unambiguous slang/profanity -- words
# with legitimate clinical/dictionary use (e.g. "sex") are NOT included,
# since a correct gloss like "to marry; to have sex with" must survive
# this filter untouched.
_VULGAR_TERMS = {
    "fuck", "fucked", "fucking", "fucker", "shit", "bitch", "bastard",
    "asshole", "cunt", "dick", "pussy",
}

_WORD_RE = re.compile(r"[a-zA-Z']+")


def _looks_like_bad_curated_meaning(text):
    """
    Heuristic-only red flag for `lemmas.meaning` text, used solely as a
    gate on the CURATED-TABLE fallback in _meaning_for_family_lemma (see
    that function's docstring for the verb-vs-noun priority order this
    sits inside of). Never applied to `family_glosses` (Wiktionary-
    sourced) text, which doesn't show this failure pattern.

    Returns True (i.e. "don't show this") when the text is a single
    token that isn't a recognized English word, contains a standalone
    first/second-person pronoun, or contains an explicit vulgar term.
    Returns False otherwise -- which is NOT a claim that the text is
    correct, only that it didn't trip one of these specific patterns.
    """
    if not text:
        return False

    tokens = _WORD_RE.findall(text.lower())
    if not tokens:
        return False

    if any(t in _VULGAR_TERMS for t in tokens):
        return True

    if any(t in _FIRST_SECOND_PERSON_PRONOUNS for t in tokens):
        return True

    if len(tokens) == 1 and tokens[0] not in _SPELLCHECKER:
        return True

    return False


def _representative_occurrences_for_lemma(quran_cur, root, lemma, limit=5):
    """
    A few real quran.db occurrences of this exact stem-morpheme lemma
    under this root, for `_words_english_fallback_for_family_lemma`.

    Uses the SAME join search.fetch_word_family_by_root relies on to
    identify a word's STEM morpheme in the first place -- the morpheme
    row whose own `root` matches the word's `root` (see that function's
    docstring for why the ordinary words.lemma/words.pos columns can't
    be trusted for this). Matching `m.lemma` here against the exact
    string already pulled from that same column means no re-normalizing
    is needed: it's the identical value, from the identical table.

    Ordered by each candidate word's TOTAL morpheme count (prefixes +
    stem + suffixes) ascending, so a bare, unaffixed occurrence -- the
    one least likely to need any stripping at all -- is tried first,
    with more heavily-affixed occurrences as later fallbacks only if the
    bare form doesn't exist in the corpus for this lemma.

    Returns a list of dicts, each `{"id", "english", "morphemes"}`, where
    "morphemes" is every morpheme belonging to that word (not just the
    stem), fetched in one extra query rather than one-per-candidate.
    """
    if not root or not lemma:
        return []

    quran_cur.execute("""
        SELECT w.id, w.english
        FROM words w
        JOIN morphemes m
            ON m.word_id = w.id
           AND m.root = w.root
        WHERE w.root = ? AND m.lemma = ?
        ORDER BY (
            SELECT COUNT(*) FROM morphemes m2 WHERE m2.word_id = w.id
        ) ASC
        LIMIT ?
    """, (root, lemma, limit))
    rows = [dict(r) for r in quran_cur.fetchall()]

    word_ids = [r["id"] for r in rows]
    morphemes_by_word = {}
    if word_ids:
        placeholders = ",".join("?" for _ in word_ids)
        quran_cur.execute(
            f"SELECT * FROM morphemes WHERE word_id IN ({placeholders}) "
            f"ORDER BY word_id, morpheme_number",
            word_ids,
        )
        for m in quran_cur.fetchall():
            morphemes_by_word.setdefault(m["word_id"], []).append(dict(m))

    for r in rows:
        r["morphemes"] = morphemes_by_word.get(r["id"], [])

    return rows


def _words_english_fallback_for_family_lemma(quran_cur, meanings_cur, lemma, root, pos):
    """
    Third-tier gloss source for a word-family entry, tried only after
    both `lemmas` (curated) and `family_glosses` (output.json) have come
    up empty for this (lemma, root) pair -- see _meaning_for_family_lemma's
    docstring for where this sits in the overall lookup order.

    quran.db's words.english is a per-OCCURRENCE gloss, already
    disambiguated by the QAC annotators using the actual verse context --
    real corpus ground truth, not a lemma-level dictionary entry the way
    the other two tiers are. Since a word-family entry is keyed on
    (stem lemma, root) rather than one specific occurrence, this pulls a
    few representative occurrences via `_representative_occurrences_for_lemma`
    and tries each one's words.english in turn, running it through the
    exact same `_strip_leading_affix_glosses` pipeline already used for
    the hero word's own STEM MEANING card -- so a leaked prefix/suffix
    gloss (e.g. "and jugs" rather than "jugs") is caught here exactly the
    way it already is there.

    Stops at the first candidate whose gloss strips CLEANLY
    (fully_stripped=True) and only returns that. If none of the tried
    candidates strip cleanly, returns None rather than risk showing a
    whole affixed word's meaning mislabeled as this lemma's own --
    consistent with this whole three-tier chain preferring an honest
    blank over a guess (see _meaning_for_family_lemma's own docstring).
    """
    candidates = _representative_occurrences_for_lemma(quran_cur, root, lemma)

    for candidate in candidates:
        english = candidate.get("english")
        if not english:
            continue

        affix_morphemes = [
            m for m in candidate["morphemes"]
            if "PREF" in (m.get("features") or "") or "SUFF" in (m.get("features") or "")
        ]

        stripped, fully_stripped = _strip_leading_affix_glosses(
            english, meanings_cur, affix_morphemes
        )
        if fully_stripped and stripped:
            return strip_definite_article_gloss(stripped)

    return None


def _meaning_for_family_lemma(cur, lemma, pos, root, root_meaning, quran_cur=None):
    """
    Best available gloss for one entry in a root's word family -- i.e. one
    row of search.fetch_word_family_by_root's (lemma, pos, occurrence_count)
    list.

    Deliberately does NOT fall back to the root's own short gloss the way
    attach_meanings()'s word_meaning does for the single hero word. That
    fallback is a single fixed sentence per root (roots.short_meaning /
    short_override) -- fine as a last resort for one word shown in
    isolation, but the word-family list shows several DISTINCT lemmas
    side by side specifically to illustrate how they differ (a verb vs.
    its participle vs. its verbal noun, etc.). Every family member that
    lacks its own gloss would silently inherit that exact same root-wide
    sentence verbatim, making unrelated derived forms read as if they
    were synonyms. Returning None instead leaves that entry's gloss
    blank in the UI (result.html's word_family_grid only renders the
    "gloss" div `{% if entry.meaning %}`), which is honest about "no
    specific gloss for this form" rather than implying "this form means
    the same thing as its neighbor."

    `root_meaning` is still accepted (callers already look it up) but is
    no longer consulted here; kept as a parameter so callers don't need
    changing.

    Lookup order depends on `pos`:

      For NOUNS ('N') -- and anything else that isn't 'V':
        1. `lemmas` table, filtered by BOTH stripped lemma AND stripped
           root (see get_lemma_meaning_for_root's docstring for why the
           root filter matters -- several stripped lemma spellings are
           shared by more than one entry under DIFFERENT roots).
        2. `family_glosses` (built from output.json, see
           build_family_glosses.py) as a fallback for pairs the curated
           table has no row for at all.

      For VERBS ('V'), the order is REVERSED -- `family_glosses` is tried
      FIRST, `lemmas` only as a fallback. This was a deliberate, verified
      finding, not an oversight: `lemmas.meaning` has no part-of-speech
      column, and spot-checking real verb entries against quran.db turned
      up a meaningful fraction that are simply wrong for the verb sense
      (e.g. a stripped root/lemma pair landing on a noun-flavored or
      outright garbled English string that has nothing to do with the
      verb's actual meaning). `family_glosses` is sourced from Wiktionary
      with its own pos tagging and was consistently correct on the same
      spot-checked entries, so it's trusted first for verbs; the curated
      table is still consulted afterward since `family_glosses` doesn't
      cover every verb, and a fallback answer is preferred over a blank
      one for this pairing.

    Either way, curated (`lemmas`) text is additionally screened by
    `_looks_like_bad_curated_meaning` before use -- see that function's
    docstring for exactly what it catches (transliterations passed off
    as English, first/second-person pronoun leakage, explicit vulgarity)
    and, just as importantly, what it can't catch (a fluent-sounding but
    simply wrong English word). `family_glosses` text is never screened
    this way; it hasn't shown this failure pattern.
    """
    lemma_meaning = get_lemma_meaning_for_root(cur, lemma, root)
    curated = None
    if lemma_meaning and not _is_junk_lemma_meaning(lemma_meaning.get("meaning")):
        candidate = lemma_meaning.get("meaning")
        if not _looks_like_bad_curated_meaning(candidate):
            curated = candidate

    family_gloss = get_family_gloss(cur, lemma, root, pos)

    if pos == "V":
        if family_gloss:
            return family_gloss
        if curated:
            return curated
    else:
        if curated:
            return curated
        if family_gloss:
            return family_gloss

    # THIRD TIER -- neither the curated `lemmas` table nor `family_glosses`
    # had anything for this (lemma, root) pair. Fall back to quran.db's own
    # per-occurrence words.english, run through the same stripping pipeline
    # already proven out for the hero word's STEM MEANING card. Only runs
    # if the caller supplied a live quran.db cursor; older callers that
    # haven't been updated simply skip this tier, same as before.
    if quran_cur is not None:
        fallback = _words_english_fallback_for_family_lemma(quran_cur, cur, lemma, root, pos)
        if fallback:
            return fallback

    return None


def get_root_meaning(cur, root):
    key = _meaning_key(root)
    if not key:
        return None
    cur.execute("SELECT * FROM roots WHERE stripped = ?", (key,))
    row = cur.fetchone()
    return dict(row) if row else None


def get_names_of_allah_for_root(cur, root):
    """
    Every Name of Allah (names_of_allah table) derived from `root`, e.g.
    root رحم -> Ar-Rahman, Ar-Rahim.

    names_of_allah.root is hand-curated undiacritized text, same as
    roots.root/roots.stripped -- but unlike get_root_meaning() this
    can't lean on the NORMALIZE() SQL function (only registered on the
    quran.db connection in search.py, not on this meanings.db cursor),
    so both sides are folded down with _meaning_key() in Python instead.
    The table only has 99 rows, so filtering client-side after one
    SELECT is cheap and avoids a second sqlite connection just to
    register a function.

    Returns [] (never None) so templates can do a plain truthiness/
    length check without a null guard.
    """
    key = _meaning_key(root)
    if not key:
        return []
    cur.execute("SELECT number, arabic, transliteration, meaning, root, notes FROM names_of_allah")
    matches = [dict(row) for row in cur.fetchall() if _meaning_key(row["root"]) == key]
    matches.sort(key=lambda r: r["number"])
    return matches


def get_lemma_meaning(cur, lemma):
    key = _meaning_key(lemma)
    if not key:
        return None
    cur.execute("SELECT * FROM lemmas WHERE stripped = ?", (key,))
    row = cur.fetchone()
    return dict(row) if row else None


def get_lemma_meaning_for_root(cur, lemma, root):
    """Same as get_lemma_meaning, but also requires the row's own `root`
    column (stripped) to match -- see _meaning_for_family_lemma's
    docstring for why plain stripped-lemma matching isn't safe for the
    word-family use case. Falls back to the plain, non-root-filtered
    lookup when `root` isn't available, so callers that don't have one
    still behave as before."""
    if not root:
        return get_lemma_meaning(cur, lemma)

    lemma_key = _meaning_key(lemma)
    root_key = _meaning_key(root)
    if not lemma_key:
        return None

    # `lemmas.root` isn't pre-stripped/folded, so pull every row sharing
    # this stripped lemma spelling and compare `root` in Python via
    # _meaning_key() rather than trying to replicate that folding in SQL.
    cur.execute("SELECT * FROM lemmas WHERE stripped = ?", (lemma_key,))
    rows = cur.fetchall()
    if not rows:
        return None

    root_matches = [row for row in rows if _meaning_key(row["root"]) == root_key]
    if not root_matches:
        # No row matches this exact root -- don't guess by returning some
        # other root's entry for the same spelling.
        return None

    if len(root_matches) == 1:
        return dict(root_matches[0])

    # More than one diacritically-DISTINCT lemma can share both the same
    # stripped spelling and the same root -- confirmed directly against
    # this table, e.g. under root نزل, مُنزَل / مُنزِل / مُنَزَّل / مُنَزِّل
    # all strip to "منزل", and نَزَلَ / نَزَّلَ / نُزُل all strip to "نزل".
    # This used to be resolved by returning whichever row happened to
    # come first in the table, regardless of which lemma was actually
    # asked about -- e.g. every one of those four منزل-skeleton lemmas
    # showing the SAME "house" gloss.
    #
    # Pin down "the" row for this exact lemma via its own diacritized
    # spelling -- normalizing with NFC first, since combining marks
    # (e.g. shadda + fatha in مُنَزَّل) can be stored in different
    # canonical orders that render identically but don't compare equal
    # as raw strings otherwise.
    lemma_nfc = unicodedata.normalize("NFC", lemma)
    matched_row = None
    for row in root_matches:
        if unicodedata.normalize("NFC", row["lemma"] or "") == lemma_nfc:
            matched_row = row
            break

    if matched_row is None:
        # None of the colliding rows is actually this lemma -- don't
        # guess.
        return None

    # Pinning down the right ROW isn't enough on its own: spot-checking
    # this collision (confirmed directly against the table) shows the
    # underlying curated text was itself copy-pasted across distinct
    # lemmas, not written per-lemma -- e.g. "house" is the literal
    # `meaning` value on all four منزل-skeleton rows (a place noun, a
    # passive participle, and an active participle, which do NOT all
    # mean "house"), and "hostel" is the literal value on all three
    # نزل-skeleton rows (two of which are verbs, not "hostel" at all).
    # A meaning shared verbatim across two or more DIFFERENT lemmas in
    # this same collision group is a strong signal it's this kind of
    # duplication artifact rather than a genuinely correct per-lemma
    # entry. Treat it as unreliable and return None so the caller falls
    # through to family_glosses (or shows no gloss) instead of repeating
    # the same bug for a different lemma each time.
    meaning = matched_row["meaning"]
    duplicated = any(
        row["lemma"] != matched_row["lemma"] and row["meaning"] == meaning
        for row in root_matches
    )
    if duplicated:
        return None

    return dict(matched_row)


def get_family_gloss(cur, lemma, root, pos):
    """Looks up a gloss from the `family_glosses` table (built from
    output.json by build_family_glosses.py), keyed on stripped lemma +
    stripped root. When more than one part-of-speech row exists for that
    pair, prefers the one matching `pos` ('N' or 'V', from the stem
    morpheme's own tag) before falling back to any row for that pair.

    `stripped_root` in this table specifically was built with every
    hamza-bearing letter (أ/إ/آ/ؤ/ئ) collapsed to bare hamza (ء) --
    verified against every hamza-initial root in the table. That's
    inconsistent with `stripped_word` in this same table (which keeps
    normal hamza forms) and with every other stripped root/lemma column
    in the schema (`roots.stripped`, `lemmas.root`, quran.db's own
    `words.root`), all of which preserve the real hamza letter (e.g.
    quran.db root 'أمن' stays أ, never becomes ء). Without normalizing
    just this one lookup's root side to match, every hamza-initial root
    (أمن, أخذ, أمر, أكل, ...) silently misses here, so those verbs fall
    through to the curated `lemmas` fallback -- which isn't guaranteed to
    carry the verb-sense gloss (e.g. آمَنَ landed on the noun-sense
    "security" instead of the verb-sense "to believe" that's actually
    sitting in this table under the mismatched root spelling)."""
    lemma_key = _meaning_key(lemma)
    root_key = _meaning_key(root)
    if not lemma_key or not root_key:
        return None

    for hamza_letter in ('\u0623', '\u0625', '\u0622', '\u0624', '\u0626'):
        root_key = root_key.replace(hamza_letter, '\u0621')

    cur.execute(
        "SELECT pos, meaning FROM family_glosses "
        "WHERE stripped_word = ? AND stripped_root = ?",
        (lemma_key, root_key),
    )
    rows = cur.fetchall()
    if not rows:
        return None

    if pos:
        for row in rows:
            if row["pos"] == pos:
                return row["meaning"]

    return rows[0]["meaning"]


def get_word_meaning(cur, surface):
    key = _meaning_key(surface)
    if not key:
        return None
    cur.execute("SELECT * FROM words WHERE stripped = ?", (key,))
    row = cur.fetchone()
    return dict(row) if row else None


def get_stem_override(cur, surface):
    """
    A hand-curated correction for this EXACT surface form, stored in
    `words.stem_override`, that takes priority over both CAMeL's guess and
    the bulk-loaded root/lemma text.

    This exists because CAMeL's dictionary occasionally has no entry at
    all for a specific derived form (e.g. an active participle) and
    confidently offers an unrelated same-root homograph instead -- a
    failure mode that can't be caught by confidence scoring, since CAMeL
    itself isn't aware of the gap. `words.meaning`/`words.gloss` aren't
    usable for this: they're bulk-loaded root-level Lane excerpts
    duplicated across every word sharing that root, not per-word
    corrections, so a dedicated column is used instead.
    """

    if not surface:
        return None

    cur.execute("SELECT stem_override FROM words WHERE surface = ?", (surface,))
    row = cur.fetchone()
    return row["stem_override"] if row and row["stem_override"] else None


def get_cached_stem_meaning(cur, surface):
    """
    A precomputed CAMeL stem gloss for this EXACT surface form, stored in
    `words.stem_meaning` by scripts/precompute_stem_meanings.py.

    This is filled in bulk, once, for every distinct surface in the corpus
    (picking the majority root/lemma pairing for the ~25 surfaces that are
    genuine homographs -- see that script's docstring), rather than
    calling CAMeL live on every request. It's the same computation
    camel_stem_gloss() would do live; caching it just makes results fast
    and stable, and gives a reviewable place (this column) to spot bad
    glosses before correcting them via `stem_override`.

    Returns None if the cache has no row, or the cached value is NULL --
    i.e. CAMeL itself had nothing for this word (see that script's
    resolution-rate note), in which case the caller should fall back to a
    live camel_stem_gloss() call rather than assume "no gloss exists".
    """

    if not surface:
        return None

    cur.execute("SELECT stem_meaning FROM words WHERE surface = ?", (surface,))
    row = cur.fetchone()
    return row["stem_meaning"] if row and row["stem_meaning"] else None


# ==========================================================
# Core (stem) morpheme
# ==========================================================

def _get_core_morpheme(morphemes):
    """The word's non-affix (stem/core) morpheme -- the same one the result
    template singles out as the 'MAIN BODY' segment. A word can carry more
    than one clitic on either side, so this is picked by feature flags
    rather than by a fixed slot position; falls back to the first morpheme
    if somehow none are un-flagged."""

    for m in morphemes:
        features = m.get("features") or ""
        if "PREF" not in features and "SUFF" not in features:
            return m

    return morphemes[0] if morphemes else None


# ==========================================================
# Grammatical notes for core (non-affix) function-word particles
# ==========================================================
#
# Some words in the corpus are, in their entirety, a single closed-class
# particle rather than a noun/verb with a real lexical stem -- لَا chief
# among them. `words.english` already gives the right per-occurrence
# TRANSLATION for these ("not", "no", "(do) not", ...), but a bare
# translation on its own reads as if that were لَا's one fixed meaning,
# when it's actually at least THREE distinct grammatical roles
# (confirmed directly against quran.db's morphemes table, all sharing
# lemma "لا", tag "P"): categorical negation of a whole noun/genus
# (NEG|FAM:إِنّ, e.g. لَا رَيْبَ فِيهِ "there is [absolutely] no doubt in
# it", Al-Baqarah 2:2), plain clausal negation (bare NEG), and negating
# an imperative (PRO, "do not ...", e.g. لَا تُفْسِدُوا۟ Al-Baqarah 2:11).
#
# This deliberately does NOT try to report WHICH of those three a given
# result actually is: `search_surface` (see above) matches on bare
# surface text with `LIMIT 1`, so every standalone لَا search lands on
# the exact same underlying row regardless of which real occurrence
# prompted it -- there's no reliable way to tell, from a bare-word
# lookup alone, which of the three grammatical roles is actually in
# play for the ayah the person has in mind. Claiming a specific one
# anyway would often be confidently wrong. One honest, role-agnostic
# caption applies correctly no matter which underlying row gets matched.
_CORE_PARTICLE_NOTES = {
    "لا": "Negation particle (lā) — a grammatical negator, not a word with one fixed meaning of its own; its best English rendering (\"not\", \"no\", \"never\", \"do not\"...) depends on what it's negating in context.",
}


def _core_particle_notes(core_morpheme):
    """A short grammatical caption for a core (non-affix) morpheme that's
    really a closed-class function-word particle rather than a word with
    its own independent lexical stem -- or None if this morpheme's lemma
    isn't one of the recognized cases (see `_CORE_PARTICLE_NOTES`
    above)."""

    if not core_morpheme:
        return None

    return _CORE_PARTICLE_NOTES.get(_meaning_key(core_morpheme.get("lemma")))


# ==========================================================
# Fused-in grammatical markers: subject-agreement prefix, tanwīn
# ==========================================================
#
# Two Arabic grammatical markers never get their own morpheme row in this
# corpus at all -- they're fused into one existing morpheme's own surface
# diacritics, unlike وَ/فَ/لَ/ال (genuinely separable PREF morphemes) or an
# attached pronoun (a genuinely separable SUFF morpheme):
#
#   - An IMPERFECT verb's subject is marked by a single fused prefix
#     letter -- أ (I), نَ (we), تَ (you / she), يَ (he/it/they) -- confirmed
#     directly against quran.db: يَقُولُ is ONE morpheme (tag V, features
#     "IMPF|VF:1|ROOT:قول|LEM:قالَ|3MS|MOOD:IND"), not a separate ي
#     morpheme plus a قُولُ stem.
#   - Tanwīn (ً/ٌ/ٍ) marks an indefinite noun's case at the very end of
#     its own surface -- confirmed the same way: عَدُوٌّ is ONE morpheme
#     (tag N, features "ROOT:عدو|LEM:عَدُوّ|M|INDEF|NOM").
#
# Both are still fully derivable, though -- the corpus already tags the
# person/gender/number (for the verb) or indefiniteness+case (for the
# noun) on that SAME morpheme, so which letter is doing the marking, and
# what it means, isn't a guess. What's different is the presentation:
# since neither is a separable clitic the way a real PREF/SUFF is, they
# don't get their own PREFIX/SUFFIX-style card (that would visually claim
# a separability that isn't there) -- just a small tooltip on the exact
# letter/mark itself, right inside the ORIGINAL WORD's MAIN BODY segment.

_SUBJECT_MARKER_MEANINGS = {
    "1S":  "I",
    "2MS": "you",
    "2FS": "you",
    "3MS": "he / it",
    "3FS": "she / it",
    "1P":  "we",
    "2MP": "you all",
    "2FP": "you all",
    "3MP": "they",
    "3FP": "they",
    "2D":  "you two",
    "3D":  "they two",
    "3MD": "they two",
    "3FD": "they two",
}


def _verb_subject_marker(core_morpheme):
    """
    Returns {"letter": ..., "meaning": ..., "notes": ...} for the fused
    subject-agreement prefix letter on an IMPERFECT verb's own surface,
    or None if this morpheme isn't an imperfect verb (see module note
    above). `letter` is always the morpheme's own first character --
    never inferred from the letter alone, since ت is genuinely ambiguous
    between 2MS and 3FS on its own; the corpus's own person/gender/number
    tag on this exact morpheme resolves that, not a guess.
    """
    if not core_morpheme:
        return None
    if (core_morpheme.get("tag") or "").upper() != "V":
        return None

    features = (core_morpheme.get("features") or "").split("|")
    if "IMPF" not in features:
        return None

    surface = core_morpheme.get("surface") or ""
    if not surface:
        return None

    person_tag = next((f for f in features if f in _SUBJECT_MARKER_MEANINGS), None)
    if not person_tag:
        return None

    meaning = _SUBJECT_MARKER_MEANINGS[person_tag]

    return {
        "letter": surface[0],
        "meaning": meaning,
        "notes": "Subject-agreement prefix (\u201c{}\u201d) fused into this imperfect verb \u2014 not a separate word.".format(meaning),
    }


_TANWIN_MARKS = {
    "\u064B": "an",  # fathatan
    "\u064C": "un",  # dammatan
    "\u064D": "in",  # kasratan
}

_TANWIN_CASE_LABELS = {
    "NOM": "nominative",
    "ACC": "accusative",
    "GEN": "genitive",
}


def _tanwin_badge(core_morpheme):
    """
    Returns {"letter": ..., "meaning": ..., "notes": ...} for a trailing
    tanwīn mark (ً/ٌ/ٍ) on an indefinite noun's own surface, or None if
    this morpheme isn't an indefinite noun ending in one (see module note
    above).
    """
    if not core_morpheme:
        return None

    features = (core_morpheme.get("features") or "").split("|")
    if "INDEF" not in features:
        return None

    surface = core_morpheme.get("surface") or ""
    if not surface or surface[-1] not in _TANWIN_MARKS:
        return None

    case_tag = next((f for f in features if f in _TANWIN_CASE_LABELS), None)
    case_label = _TANWIN_CASE_LABELS.get(case_tag)

    notes = "Tanwīn (nunation) \u2014 marks this noun as indefinite"
    if case_label:
        notes += " and " + case_label
    notes += "."

    return {"letter": surface[-1], "meaning": "tanwīn", "notes": notes}


def _annotate_main_segment(core_surface, subject_marker, tanwin_badge):
    """
    Builds an HTML string for the ORIGINAL WORD hero's MAIN BODY segment
    with the fused subject-marker letter and/or trailing tanwīn mark each
    wrapped in their own `<span title="...">`, so hovering explains what
    that one letter/diacritic is doing -- without a separate card
    implying it's removable the way a real prefix/suffix is.

    Returns None (caller should fall back to plain, unannotated text) if
    `core_surface` is empty or neither marker actually applies here.
    """
    if not core_surface or (not subject_marker and not tanwin_badge):
        return None

    body = core_surface
    prefix_html = ""
    suffix_html = ""

    if subject_marker and body.startswith(subject_marker["letter"]):
        prefix_html = '<span class="fused-marker" title="{}">{}</span>'.format(
            html.escape(subject_marker["notes"], quote=True),
            html.escape(subject_marker["letter"]),
        )
        body = body[len(subject_marker["letter"]):]

    if tanwin_badge and body.endswith(tanwin_badge["letter"]):
        suffix_html = '<span class="fused-marker" title="{}">{}</span>'.format(
            html.escape(tanwin_badge["notes"], quote=True),
            html.escape(tanwin_badge["letter"]),
        )
        body = body[: -len(tanwin_badge["letter"])]

    if not prefix_html and not suffix_html:
        return None

    return prefix_html + html.escape(body) + suffix_html


# ==========================================================
# Stripping a word's own affix glosses out of a whole-surface gloss
# ==========================================================
#
# `words.english` (QAC's word-by-word translation) is curated per
# OCCURRENCE, but per SURFACE -- i.e. it glosses the whole surface form,
# clitics included, not just the stem. Confirmed directly against
# quran.db, for both affix kinds this handles:
#   - PREF: فَتَلَقَّىٰٓ (فَ + تَلَقَّىٰٓ) carries english "Then received",
#     فَتَابَ (فَ + تَابَ) carries "So (his Lord) turned" -- the leading
#     word is exactly the same "then, so" gloss the PREFIX card already
#     shows for فَ on its own.
#   - SUFF (attached pronoun): رَّبِّكُمْ (رَبّ + كُمْ, PRON|SUFF|2MP)
#     carries english "your Lord" -- a possessive determiner rendering of
#     the same suffix the SUFFIX card glosses as "you all". لَقُوا۟
#     (لَقُ + وا۟, PRON|SUFF|3MP) carries "they meet" -- here the SAME
#     suffix kind is doing subject-agreement on a verb rather than
#     marking possession on a noun, but English still renders it as a
#     LEADING pronoun ("they ..."), so the same leading-strip approach
#     catches it too.
#
# Used unmodified as the STEM MEANING (which is documented as, and
# should show, the stem's own meaning ALONE -- prefixes and suffixes
# already get their own cards), any of this duplicates that affix's
# contribution inside the stem card too. This mirrors the exact problem
# strip_definite_article_gloss solves for the "ال" prefix specifically;
# this generalizes that fix to any attached PREF clitic or attached-
# pronoun SUFF clitic that shows up leading the gloss, using the same
# meaning lookups (`get_prefix_meaning`, plus `_PRON_SUFFIX_STRIP_FORMS`
# for suffixes) already used to populate the PREFIX/SUFFIX cards, so the
# cards can never disagree with the stem card about what an affix means.
#
# Deliberately conservative, in two ways:
#   - Only strips a literal match, comma/space-delimited, at the very
#     START of the string, for an affix this word actually carries. A
#     leading word that doesn't match any of that affix's known glosses
#     is left alone rather than guessed at -- an occasional unstripped
#     affix leaking through is far less harmful than chopping into the
#     real stem gloss on a false match.
#   - Only ever strips from the FRONT. An attached-pronoun suffix can
#     also be a verb's trailing OBJECT ("Do you tell them" -- "them" is
#     أَتُحَدِّثُونَهُم's own SUFF), and this corpus's `features` tag can't
#     reliably distinguish that role from subject-agreement/possessive
#     (see `_pron_suffix_from_features`'s docstring). Guessing at a
#     trailing strip risks chopping real stem content off the end
#     instead of an attached pronoun, so that case is deliberately left
#     unhandled rather than risk it.

# A hardcoded backstop for the handful of PREF morphemes that recur
# constantly in this corpus (conjunctions, prepositions, the future
# marker, the definite article, the interrogative/equivalence hamza).
# Keyed on the morpheme's own stripped lemma/surface (via _meaning_key),
# same as the `prefixes` table lookup in get_prefix_meaning.
#
# This exists because a literal front-of-string match against
# `prefixes.meaning` alone misses more often than it should: that table
# holds ONE curated gloss per prefix, but a given occurrence's
# `words.english` is free-form per-occurrence English, and can render
# the exact same prefix a handful of different common ways depending on
# the sentence ("So" vs "Then" vs "For" for فَ, "To" vs "So that" vs "In
# order that" vs "Let" for لِ, ...). Rather than expand the curated
# `prefixes` table itself (which is meant to hold ONE canonical gloss,
# shown on the PREFIX card), this supplies EXTRA candidate leading-words
# that are only ever used here, to strip words.english more thoroughly,
# without changing what the PREFIX card itself displays.
_GENERIC_PREF_LEAD_WORDS = {
    "و":  ["and"],
    "ف":  ["so", "then", "for", "and", "so then"],
    "ل":  ["to", "for", "so that", "in order that", "let", "indeed", "surely"],
    "ب":  ["with", "by", "in"],
    "ك":  ["like", "as"],
    "س":  ["will"],
    "ال": ["the"],
    "أ":  ["is", "are", "did", "do", "does", "whether"],
}


def _generic_prefix_lead_words(m):
    key = _meaning_key(m.get("lemma")) or _meaning_key(m.get("surface"))
    return list(_GENERIC_PREF_LEAD_WORDS.get(key, ()))


def _leading_gloss_variants(cur, m):
    """The candidate leading-English word(s) this affix morpheme's own
    meaning could show up as at the front of a whole-surface gloss, or
    None if this morpheme isn't one of the kinds handled here (see
    module docstring above).

    For PREF morphemes, combines whatever the curated `prefixes` table
    has (tried first, since it's the more specific/authoritative source)
    with the generic backstop list above, so a mismatch in ONE source
    doesn't block a strip the OTHER source would have caught."""

    features = m.get("features") or ""

    if "PREF" in features:
        variants = []
        meaning = get_prefix_meaning(cur, m)
        if meaning and meaning.get("meaning"):
            variants.extend(v.strip() for v in meaning["meaning"].split(",") if v.strip())

        for word in _generic_prefix_lead_words(m):
            if word not in variants:
                variants.append(word)

        return variants or None

    if "SUFF" in features:
        parts = features.split("|")
        if len(parts) >= 3 and parts[0] == "PRON" and parts[1] == "SUFF":
            return list(_PRON_SUFFIX_STRIP_FORMS.get(parts[2], ()))

    return None


_LEADING_PARENTHETICAL_RE = re.compile(r'^\(([^()]+)\)\s*')


def _strip_leading_parenthetical(text):
    """
    Strips a single leading bracketed marker -- e.g. "(of)", "(the)",
    "(and)" -- from the front of a words.english / stem_override gloss.

    Sahih-International-style translations use a leading parenthetical to
    mark a word that's grammatically implied (often by a construct-state
    relationship with the PRECEDING word in the ayah, or by context) but
    isn't rendered by any morpheme attached to THIS word at all. E.g.
    "(of) intelligence" for ٱلنُّهَىٰ in أُولِي ٱلنُّهَىٰ ("possessors of
    intelligence"): the "of" documents the construct-state relationship
    with أُولِي, the PRECEDING word -- it isn't the gloss of ٱلنُّهَىٰ's own
    ال prefix (which glosses as "the", not "of") or any other morpheme
    this word carries.

    Because that leading word documents a grammatical relationship rather
    than an attached morpheme's own meaning, _leading_gloss_variants (which
    only knows about morphemes THIS word actually carries) can never match
    it -- so without this, _strip_leading_affix_glosses would report
    fully_stripped=False and discard a perfectly good, correctly-
    disambiguated per-occurrence gloss in favor of a CAMeL guess (which,
    for rare senses CAMeL's dictionary doesn't cover -- e.g. the classical
    "intellect" sense of نُهَى -- can be badly wrong).

    Safe to strip unconditionally, no morpheme-matching needed: by this
    translation convention a parenthesized leading word is always added
    scaffolding, never part of the stem's own core meaning. Only strips
    once, and only if text remains afterward, so a gloss that's nothing
    BUT a parenthetical is left untouched rather than emptied out.
    """
    if not text:
        return text
    match = _LEADING_PARENTHETICAL_RE.match(text)
    if not match:
        return text
    remainder = text[match.end():].strip()
    return remainder or text


def _is_verb_subject_agreement_suffix(suffix_morpheme, core_morpheme):
    """
    True if `suffix_morpheme` (a PRON|SUFF morpheme) is the attached
    realization of the verb's OWN subject-person/gender/number agreement
    -- e.g. the وا۟ in أَصْلِحُوا۟ (imperative "set right!", 2MP) -- rather
    than an independent object or possessive pronoun clitic that happens
    to be attached to the same word.

    Confirmed against quran.db's morphemes table: when a SUFF is subject
    agreement, the corpus already records that SAME person/gender/number
    tag on the core VERB morpheme's own `features` (e.g. the stem's own
    features already read "IMPV|VF:4|ROOT:صلح|LEM:أَصْلَحَ|2MP" for
    أَصْلِحُوا۟, matching the suffix's own "PRON|SUFF|2MP" exactly) --
    since the verb's conjugation and its subject marker are two views of
    the one same agreement, not two independent pieces of information.
    An independent object/possessive pronoun has no reason to duplicate
    onto the stem's own features this way.

    This matters for `_strip_leading_affix_glosses`: a subject-agreement
    suffix like this doesn't surface as an extra leading word in a
    stem-only English gloss at all (nobody writes "you-all set right",
    they write "set right"), unlike a genuine leaked possessive ("your
    Lord"). Treating it the same as a possible leak forces a clean,
    already-correct curated gloss through the same "unconfirmed, don't
    trust it" fallback that exists for the genuinely leaky cases,
    discarding a good hand-curated override in favour of a worse guess.
    """

    if not suffix_morpheme or not core_morpheme:
        return False

    suffix_features = (suffix_morpheme.get("features") or "").split("|")
    if len(suffix_features) < 3 or suffix_features[0] != "PRON" or suffix_features[1] != "SUFF":
        return False

    if (core_morpheme.get("tag") or "").upper() != "V":
        return False

    core_features = (core_morpheme.get("features") or "").split("|")
    return suffix_features[2] in core_features


def _strip_trailing_pronoun_gloss(text, variants):
    """
    Tries to strip a TRAILING " of <pronoun>" contribution off the end of
    `text` -- e.g. the "of them" in "most of them" -- and returns
    (new_text, matched).

    This is the mirror image of what the rest of this module (and its
    name) assumes: `_leading_gloss_variants` exists because a PREF's or
    PRON|SUFF's own gloss usually shows up at the FRONT of a whole-word
    English translation. But an attached pronoun suffix on a genitive/
    elative construct -- أَكْثَرُهُمْ ("most of them"), أَكْثَرُكُم ("most
    of you") -- naturally renders TRAILING instead, the same way English
    always puts "of them" after the noun it modifies rather than before
    it. Without this, a hand-curated `stem_override` like "most of them"
    can never be confirmed fully-stripped (there's nothing to match at
    the front), so it gets distrusted and discarded in favour of a worse
    guess even though it's already exactly right -- see the module
    docstring's `أَكْثَرِهِمْ` example.
    """
    for variant in sorted(variants, key=len, reverse=True):
        suffix_form = " of " + variant
        if text.endswith(suffix_form):
            return text[: -len(suffix_form)], True
    return text, False


def _strip_leading_affix_glosses(text, cur, morphemes, core_morpheme=None):
    """
    Returns (stripped_text, fully_stripped).

    `fully_stripped` is True only if EVERY affix morpheme this word
    carries that's expected to leak a leading gloss into `text` (a PREF,
    or a PRON|SUFF suffix -- see `_leading_gloss_variants`) was
    successfully matched and removed.

    This matters because the strip itself is deliberately conservative
    (see module docstring above): if an affix's own gloss can't be found
    at all, or the leading word in `text` doesn't literally match any
    known variant of it, the loop below just leaves that affix's
    contribution sitting in `text` untouched rather than guessing. That's
    the right call for THIS function, but it means `text` alone can't be
    trusted as "definitely stem-only" -- a caller that uses it
    unconditionally whenever `words.english` exists will, in practice,
    very often end up showing the WHOLE WORD's gloss as if it were just
    the stem's, since a literal front-of-string match is the exception
    rather than the rule across free-form per-occurrence translations.
    Callers should fall back to a CAMeL-derived gloss (computed from the
    stem morpheme's own surface alone, so it can never carry this leak)
    whenever `fully_stripped` comes back False.

    `core_morpheme`, if given, lets a SUFF morpheme that's really the
    verb's own subject-agreement marker (see
    `_is_verb_subject_agreement_suffix`) skip the match requirement
    entirely, the same way an unmatched definite-article PREF already
    does just below -- since that kind of suffix was never expected to
    leak a leading word into `text` in the first place.
    """
    if not text or not morphemes:
        return text, True

    # A leading "(of)"/"(the)"/... marker documents an implied word from
    # the translation convention itself (often the grammatical relationship
    # with the PRECEDING word in the ayah), not an attached morpheme's own
    # gloss -- so it can never be matched by the morpheme loop below. Strip
    # it unconditionally first; see _strip_leading_parenthetical's
    # docstring.
    text = _strip_leading_parenthetical(text)

    fully_stripped = True

    for m in morphemes:
        variants = _leading_gloss_variants(cur, m)
        if not variants:
            # No known gloss to check `text` against for this affix, so
            # there's no way to confirm it isn't still sitting in there.
            fully_stripped = False
            continue

        matched = False
        # Longest variant first, so e.g. "you all" isn't shadowed by a
        # shorter variant of the same affix matching a prefix of it.
        for variant in sorted(variants, key=len, reverse=True):
            for candidate in (variant, variant.capitalize()):
                for form in (candidate + ", ", candidate + " "):
                    if text.startswith(form):
                        text = text[len(form):]
                        matched = True
                        break
                else:
                    continue
                break
            if matched:
                break

        if not matched:
            features = m.get("features") or ""
            if "DET" in features and "PREF" in features:
                # The definite article prefix is a special case: even if
                # its "the"/"The" gloss can't be confirmed at the front of
                # `text` here (e.g. this occurrence's translator dropped it
                # entirely, as with "(of) intelligence"), strip_definite_
                # article_gloss() is applied unconditionally by every
                # caller of this function regardless of fully_stripped --
                # so an unmatched "ال" is never actually a leak, and
                # shouldn't force a fallback away from an otherwise-good
                # per-occurrence gloss.
                pass
            elif _is_verb_subject_agreement_suffix(m, core_morpheme):
                # This SUFF is the verb's own subject-agreement marker,
                # not an independent pronoun -- see
                # _is_verb_subject_agreement_suffix's docstring. It was
                # never expected to appear as a leading word in `text` at
                # all, so failing to match it here isn't evidence of a
                # leak either.
                pass
            elif len(features.split("|")) >= 2 and features.split("|")[0] == "PRON" and features.split("|")[1] == "SUFF":
                # An independent attached pronoun that didn't match at
                # the front might still be sitting, entirely legitimately,
                # at the very END of `text` instead -- see
                # _strip_trailing_pronoun_gloss.
                text, trailing_matched = _strip_trailing_pronoun_gloss(text, variants)
                if not trailing_matched:
                    fully_stripped = False
            else:
                fully_stripped = False

    text = text.strip()
    if text:
        text = text[0].upper() + text[1:]
    return text, fully_stripped


# ==========================================================
# High-level: attach meanings onto a "word"-type lookup() result
# ==========================================================

def _camel_guess_stem_meaning(result, word, cur, cached_stem_meaning):
    """
    Best CAMeL-derived stem gloss available: the precomputed cache if
    present, otherwise a live call (see get_cached_stem_meaning's
    docstring for why both are tried). Both are computed from the stem/
    core morpheme's own surface alone, so -- unlike words.english --
    neither can ever carry a leaked prefix/suffix gloss. Used both as
    the ordinary fallback chain (no words.english at all) and as the
    fallback attach_meanings reaches for when words.english exists but
    _strip_leading_affix_glosses couldn't confirm it stripped cleanly.
    """
    if cached_stem_meaning:
        return cached_stem_meaning

    core_morpheme = _get_core_morpheme(result.get("morphemes", []))
    if not core_morpheme:
        return None

    return camel_stem_gloss(
        core_morpheme.get("surface"),
        expected_root=word.get("root"),
        expected_lemma=word.get("lemma"),
        # NOTE: deliberately NOT word.get("pos"). words.pos is built
        # by this project's own import pipeline from the LAST
        # morpheme's tag rather than the STEM's -- so a plain
        # verb+suffix word (e.g. "لَقِيتُمُ" = stem "لَقِي" + subject
        # suffix "تُمُ") gets word.pos = the SUFFIX's tag ('N', since
        # a pronoun clitic isn't itself a verb), not the stem's
        # ('V'). That silently flips is_verb_corpus to False for a
        # large share of suffixed verbs in this corpus (confirmed
        # empirically: word.pos disagreed with the stem morpheme's
        # own tag for ~61% of sampled verb-lemma words), which let
        # wrong-POS homographs (e.g. the noun "offal" for the verb
        # "لَقِيَ", "to meet") silently pass camel.py's POS-agreement
        # check.
        #
        # The core morpheme's own `tag` column doesn't have this bug
        # -- it's set per-morpheme, directly from this exact stem's
        # own analysis, so it's the correct signal for "is the
        # word's own stem a verb", independent of whatever's
        # attached to it.
        expected_pos=core_morpheme.get("tag"),
        # Derived from the core morpheme's own `features` tag (e.g.
        # "PASS" vs. its absence) -- see
        # `expected_voice_from_features`'s docstring. Used only as
        # a same-tier tiebreaker in _analysis_score, so it can only
        # ever choose between analyses CAMeL already considers
        # equally-supported by root/lemma, never override those.
        expected_voice=expected_voice_from_features(core_morpheme.get("features")),
    )


def attach_meanings(result):
    """
    Mutates and returns `result` (a "word"-type dict from search.lookup())
    with:
      - result["word"]["root_meaning"]   dict or None
      - result["word"]["word_meaning"]   dict or None (word-level, falling
                                          back to the lemma's meaning) --
                                          kept as a fallback for the hero
                                          MEANING card, see stem_meaning
      - result["word"]["stem_meaning"]   str or None -- an inflection-aware
                                          English gloss for the word's own
                                          STEM morpheme in this exact form
                                          (e.g. distinguishing perfect vs.
                                          imperfect, active vs. passive),
                                          rather than the lemma's generic
                                          citation-form dictionary sense.
                                          Fallback chain, most to least
                                          authoritative: a hand-curated
                                          override (words.stem_override) for
                                          this exact surface; this exact
                                          occurrence's own curated gloss
                                          (words.english); a precomputed
                                          CAMeL guess (words.stem_meaning);
                                          finally a live, voice-aware CAMeL
                                          guess. This is what the hero
                                          MEANING card should prefer to
                                          show.
      - each morpheme classified PREF/SUFF gets morpheme["meaning"] attached
      - result["prefix_card"] / result["suffix_card"]: independent
        {"kind": ..., "parts": [...]} cards, each None if that word has
        no morphemes of that kind. Both can be populated at once -- a word
        can carry a prefix AND a suffix simultaneously -- and each "parts"
        list holds every morpheme on that side (a word can carry more than
        one clitic per side too, e.g. وَ + ٱل before رَّسُولِ).
        See _cards_layout() below.
    """

    if result.get("type") != "word":
        return result

    conn = get_meanings_connection()
    cur = conn.cursor()

    word = result["word"]
    word["root_meaning"] = get_root_meaning(cur, word.get("root"))

    # Compute the same short-gloss precedence fetch_all_roots() uses for
    # the /roots index page (short_override -> short_meaning, if
    # short_confident -> else None), and attach it as "short" directly on
    # word["root_meaning"] itself. Without this, root_meaning is just the
    # bare `roots` table row (short_override/short_meaning/short_confident
    # as separate columns, no "short" key computed from them) -- so a
    # template reading word.root_meaning.short the same way the roots-index
    # template does finds nothing, and the ROOT card silently falls back to
    # showing only the full classical definition with no short gloss above
    # it. Computed once here so both this and the word_meaning fallback
    # below can reuse it.
    root_meaning = word["root_meaning"]
    root_short = None
    if root_meaning:
        root_short = root_meaning.get("short_override") or (
            root_meaning.get("short_meaning") if root_meaning.get("short_confident") else None
        )
        root_meaning["short"] = root_short

    # `lemmas.meaning` is the right source for a short lemma-level gloss --
    # but a handful of rows hold a leftover lexicographic cross-reference
    # marker ("Synonym", "See") instead of an actual translation (see
    # _is_junk_lemma_meaning). When that happens, or there's no lemma
    # match at all, fall back to the root's own cleaned short gloss rather
    # than `words.meaning`: that column was confirmed to be byte-for-byte
    # identical to the *root's* raw Lane's Lexicon text for every word
    # checked (a bulk duplicate, not a real per-word entry), so surfacing
    # it here would just dump the same messy, uncleaned classical text the
    # ROOT card already handles via roots.short_override/short_meaning --
    # reusing that cleaned text instead keeps both cards consistent and
    # gives curators one place (roots.short_override) to fix either.
    lemma_meaning = get_lemma_meaning(cur, word.get("lemma"))
    if lemma_meaning and not _is_junk_lemma_meaning(lemma_meaning.get("meaning")):
        word_meaning = lemma_meaning
    else:
        word_meaning = {"meaning": root_short, "notes": None} if root_short else None

    word["word_meaning"] = word_meaning

    # Any of the 99 Names of Allah built from this word's root (e.g. root
    # رحم surfaces Ar-Rahman and Ar-Rahim) -- shown alongside the word
    # family in the ROOT card. See get_names_of_allah_for_root.
    result["names_of_allah"] = get_names_of_allah_for_root(cur, word.get("root"))

    # Word family (every distinct lemma sharing this word's root, see
    # search.fetch_word_family_by_root) -- reuses the meanings.db
    # connection already open in this function, and the root_meaning
    # already looked up above, rather than opening a second connection.
    # A separate quran.db connection is opened here (and closed right
    # after the loop) purely to power _meaning_for_family_lemma's third
    # fallback tier -- see that function's docstring.
    quran_conn = get_connection()
    quran_cur = quran_conn.cursor()
    for entry in result.get("word_family", []):
        entry["meaning"] = _meaning_for_family_lemma(
            cur, entry.get("lemma"), entry.get("pos"), word.get("root"), word["root_meaning"],
            quran_cur,
        )
    quran_conn.close()

    stem_override = get_stem_override(cur, word.get("surface"))
    cached_stem_meaning = get_cached_stem_meaning(cur, word.get("surface"))

    # Needed ahead of the stem_meaning chain below (see
    # _strip_leading_affix_glosses) as well as for the PREFIX/SUFFIX
    # cards further down -- classified once here and reused for both,
    # rather than classifying morphemes twice.
    prefix_morphemes = []
    suffix_morphemes = []
    for m in result.get("morphemes", []):
        features = m.get("features") or ""
        if "PREF" in features:
            prefix_morphemes.append(m)
        elif "SUFF" in features:
            suffix_morphemes.append(m)

    # Needed so _strip_leading_affix_glosses can tell a verb's own
    # subject-agreement SUFF apart from an independent leaking pronoun --
    # see _is_verb_subject_agreement_suffix.
    core_morpheme = _get_core_morpheme(result.get("morphemes", []))

    if stem_override:
        # Hand-curated text, not CAMeL's `stemgloss` feature -- but it's
        # shown in the exact same STEM MEANING card, so it needs the same
        # treatment as words.english just below: in practice a chunk of
        # this column was seeded from the whole-surface gloss rather than
        # a true stem-only gloss, so it can leak an attached PREF/SUFF
        # clitic's own contribution ("and jugs" for أَبَارِيقَ's وَ +
        # أَبَارِيقَ, not just "jugs") the exact same way words.english
        # does -- see _strip_leading_affix_glosses's docstring. Run it
        # through the same strip, and only trust it once every attached
        # affix expected to leak a leading gloss has been confirmed
        # removed; otherwise prefer a CAMeL-derived guess (computed from
        # the stem morpheme's own surface alone, so it can't carry this
        # leak), on the same reasoning as the words.english branch below.
        stem_only, fully_stripped = _strip_leading_affix_glosses(
            stem_override, cur, prefix_morphemes + suffix_morphemes, core_morpheme
        )
        if fully_stripped:
            word["stem_meaning"] = strip_definite_article_gloss(stem_only)
        else:
            word["stem_meaning"] = (
                _camel_guess_stem_meaning(result, word, cur, cached_stem_meaning)
                or strip_definite_article_gloss(stem_only)
            )
    elif word.get("english"):
        # `words.english` is this corpus's own per-occurrence-curated gloss
        # (e.g. "are tried" / "will be tried" / "will not be tested" for
        # the various occurrences of يُفْتَنُونَ) -- already correctly
        # disambiguated by the QAC annotators using context this code
        # doesn't have, and already sitting right there on the same row as
        # the word's root/lemma/pos. It's checked ahead of BOTH the cached
        # and the live CAMeL guess below: CAMeL (even voice-aware, see
        # camel_stem_gloss's expected_voice) is still inferring from
        # morphology alone, while this column is per-occurrence ground
        # truth, so it should win rather than merely act as a last resort.
        #
        # BUT it's curated per SURFACE, not per STEM -- it bakes in a
        # gloss of any attached PREF clitic (e.g. "Then received" for
        # فَتَلَقَّىٰٓ) AND of a leading-position attached-pronoun SUFF
        # clitic (e.g. "your Lord" for رَّبِّكُمْ). Strip those back out
        # first, or they duplicate the PREFIX/SUFFIX cards' own glosses
        # inside the STEM MEANING card. See _strip_leading_affix_glosses.
        stem_only, fully_stripped = _strip_leading_affix_glosses(
            word["english"], cur, prefix_morphemes + suffix_morphemes, core_morpheme
        )
        if fully_stripped:
            word["stem_meaning"] = strip_definite_article_gloss(stem_only)
        else:
            # _strip_leading_affix_glosses couldn't confirm every attached
            # affix's own gloss was actually removed from the front of
            # `words.english` (e.g. this occurrence's translation phrases
            # the prefix/suffix differently than the curated
            # prefixes/suffixes tables do, or that table has no entry for
            # it at all). Trusting `stem_only` here would risk showing the
            # WHOLE WORD's meaning as the "stem meaning" -- exactly the bug
            # this guards against. A CAMeL-derived gloss is computed from
            # the stem morpheme's own surface alone, so it can't carry that
            # leak; prefer it here even though words.english would
            # otherwise outrank it. Only if CAMeL has nothing either do we
            # fall back to the best-effort (possibly still affixed) strip
            # result, on the theory that a partially-cleaned gloss beats no
            # gloss at all.
            word["stem_meaning"] = (
                _camel_guess_stem_meaning(result, word, cur, cached_stem_meaning)
                or strip_definite_article_gloss(stem_only)
            )
    elif cached_stem_meaning:
        # scripts/precompute_stem_meanings.py already ran the exact same
        # camel_stem_gloss() call below, once, for every surface in the
        # corpus -- reuse that instead of repeating it live on every
        # request. Only surfaces CAMeL had nothing for at all come back
        # NULL from the cache (get_cached_stem_meaning already filters
        # those out), so falling through to a live call below still
        # covers: (a) those CAMeL-has-nothing cases, in case a future,
        # better-covered CAMeL database resolves them, and (b) any word
        # not yet in the cache (e.g. added to quran.db after the last
        # precompute run).
        word["stem_meaning"] = cached_stem_meaning
    else:
        word["stem_meaning"] = _camel_guess_stem_meaning(result, word, cur, cached_stem_meaning)

    word["stem_notes"] = _core_particle_notes(core_morpheme)

    subject_marker = _verb_subject_marker(core_morpheme)
    tanwin_badge = _tanwin_badge(core_morpheme)
    result["main_segment_html"] = _annotate_main_segment(
        (core_morpheme or {}).get("surface"), subject_marker, tanwin_badge
    )

    for m in prefix_morphemes:
        m["meaning"] = get_prefix_meaning(cur, m)
    for m in suffix_morphemes:
        m["meaning"] = get_suffix_meaning(cur, m)

    conn.close()


    result["prefix_card"], result["suffix_card"] = _cards_layout(prefix_morphemes, suffix_morphemes)

    return result


def attach_family_meanings(result):
    """
    Mutates and returns `result` (a "root"- or "camel_root"-type dict from
    search.lookup(), carrying "root" and "word_family") with a "meaning"
    key added to every entry in result["word_family"].

    attach_meanings() above only runs for "word"-type results and already
    handles the word_family case for those inline, since it has an open
    meanings.db connection and the root_meaning to hand already. This is
    the equivalent for the standalone root-family page (query was a root,
    or CAMeL guessed one), which never goes through attach_meanings at
    all, so it opens its own connection.
    """
    family = result.get("word_family")
    if not family:
        result["word_family"] = []
        # A root with literally no word family shouldn't happen for a
        # Name of Allah's root (its root necessarily occurs in the
        # Qur'an), but check anyway rather than silently skipping it --
        # cheap enough to open the connection just for this one lookup.
        conn = get_meanings_connection()
        result["names_of_allah"] = get_names_of_allah_for_root(conn.cursor(), result.get("root"))
        conn.close()
        return result

    conn = get_meanings_connection()
    cur = conn.cursor()

    # Separate quran.db connection purely to power _meaning_for_family_lemma's
    # third fallback tier (words.english) -- see that function's docstring.
    quran_conn = get_connection()
    quran_cur = quran_conn.cursor()

    root_meaning = get_root_meaning(cur, result.get("root"))
    if root_meaning:
        root_meaning["short"] = root_meaning.get("short_override") or (
            root_meaning.get("short_meaning") if root_meaning.get("short_confident") else None
        )
    result["root_meaning"] = root_meaning

    # Any of the 99 Names of Allah built from this root -- see
    # get_names_of_allah_for_root and the matching attach_meanings()
    # branch above (this is the standalone root-page equivalent).
    result["names_of_allah"] = get_names_of_allah_for_root(cur, result.get("root"))

    for entry in family:
        entry["meaning"] = _meaning_for_family_lemma(
            cur, entry.get("lemma"), entry.get("pos"), result.get("root"), root_meaning,
            quran_cur,
        )

    quran_conn.close()
    conn.close()
    return result


def _cards_layout(prefix_morphemes, suffix_morphemes):
    """
    Builds up to two INDEPENDENT cards -- one for prefixes, one for
    suffixes -- since a word can carry both at once (e.g. a conjunction
    prefix AND a pronoun suffix on the same verb) and the hero layout
    gives each its own slot flanking the stem, rather than forcing a
    single pick between them (the old behaviour: prefix always won,
    silently dropping the suffix from the hero row whenever both existed).

    Each card lists every morpheme of that side, in morpheme_number order
    (the same order `result["morphemes"]` was fetched in) under a "parts"
    list, since a word can also carry more than one clitic on the SAME
    side (e.g. وَٱلرَّسُولِ = "وَ" + "ٱل", two PREF morphemes).

    Returns (prefix_card, suffix_card); either is None if that side has
    no morphemes.

    result.html's affix_card_markup() iterates `card.parts` (a LIST) and
    reads `item.text` / `item.meaning` off each entry -- this must return
    that exact shape, not a single flattened text/meaning pair, or the
    template's `for item in card.parts` loop silently sees nothing and
    always renders "No gloss on file yet" regardless of what meaning was
    actually attached to the morpheme.
    """

    prefix_card = None
    suffix_card = None

    if prefix_morphemes:
        prefix_card = {
            "kind": "prefix",
            "parts": [
                {"text": m.get("surface"), "meaning": m.get("meaning")}
                for m in prefix_morphemes
            ],
        }

    if suffix_morphemes:
        suffix_card = {
            "kind": "suffix",
            "parts": [
                {"text": m.get("surface"), "meaning": m.get("meaning")}
                for m in suffix_morphemes
            ],
        }

    return prefix_card, suffix_card