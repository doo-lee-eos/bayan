from camel_tools.morphology.database import MorphologyDB
from camel_tools.morphology.analyzer import Analyzer

from services.normalize import strip_harakat

# -------------------------------------------------------
# Initialise CAMeL once
# -------------------------------------------------------

_db = MorphologyDB.builtin_db()

_analyzer = Analyzer(_db)


# -------------------------------------------------------
# Lookup
# -------------------------------------------------------
def strip_definite_article_gloss(text):
    """Public wrapper around _strip_baked_in_definite_article for callers
    outside this module (e.g. services/meanings.py)."""
    return _strip_baked_in_definite_article(text)
def camel_lookup(word):
    """
    Returns every CAMeL analysis for a word.

    Returns:
        None
            if CAMeL finds nothing.

        List[dict]
            if analyses exist.
    """

    analyses = _analyzer.analyze(_quranic_to_msa_orthography(word))

    if not analyses:
        return None

    return analyses


# -------------------------------------------------------
# Root candidates
# -------------------------------------------------------

def camel_root_candidates(word):
    """
    Returns the distinct roots CAMeL considers possible for `word`, converted
    to the QAC-comparable format (dots removed, e.g. "ر.ح.م" -> "رحم") so
    they can be checked directly against the `roots` table.

    Returns an empty list if CAMeL finds nothing.
    """

    analyses = camel_lookup(word)

    if not analyses:
        return []

    roots = []
    seen = set()

    for analysis in analyses:
        root = analysis.get("root")
        if not root:
            continue

        undotted = root.replace(".", "")

        if undotted not in seen:
            seen.add(undotted)
            roots.append(undotted)

    return roots


# -------------------------------------------------------
# Stem gloss (inflection-aware "meaning of this exact form")
# -------------------------------------------------------

def _first_sense(stemgloss):
    """
    Cleans CAMeL's `stemgloss` feature into a short, readable phrase.

    `stemgloss` is CAMeL's own isolated gloss for just the stem morpheme --
    already separated internally from the glosses of attached subject
    prefixes/suffixes and clitics, so no splitting on "+" is needed here
    (unlike the combined `gloss` feature, which packs everything together
    and orders it differently depending on whether the verb is prefix- or
    suffix-conjugated).

    Alternate senses are ";"-separated (e.g. "beg_forgiveness;apologize");
    we keep the first. Multi-word senses are "_"-joined; turned into
    spaces here.
    """

    if not stemgloss:
        return None

    stemgloss = stemgloss.split(';')[0]
    stemgloss = stemgloss.replace('_', ' ').strip()
    stemgloss = _strip_baked_in_definite_article(stemgloss)

    return stemgloss or None


def _root_matches(analysis_root, expected_root, allow_hash=True):
    """
    True if `analysis_root` (CAMeL's dotted form, e.g. "ع.ل.م") denotes the
    same root as `expected_root` (QAC-style undotted, e.g. "علم").

    CAMeL uses "#" as a generic "not recoverable / not applicable" root-slot
    placeholder -- not specifically "same root, this radical unclear on a
    weak/irregular form" as it might first appear (e.g. "ع.#.ن" for "عون").
    It shows up far more broadly, including on entirely rootless/closed-class
    entries (e.g. "ف.#" for the preposition "في"). So "#" alone is NOT
    reliable evidence that `analysis_root` is really the same root as
    `expected_root` -- it can coincidentally line up in length and non-#
    letters with a completely unrelated root.

    `allow_hash=True` (the default) treats "#" as matching anything, for
    callers that need the permissive comparison. `allow_hash=False` requires
    every letter to match exactly, with no "#" credit -- used by
    `_analysis_score` to avoid trusting a "#"-assisted match unless it is
    independently corroborated by something else (e.g. a lemma match).
    """

    if not analysis_root or not expected_root:
        return False

    letters = analysis_root.split(".")

    if len(letters) != len(expected_root):
        return False

    if allow_hash:
        return all(a == "#" or a == e for a, e in zip(letters, expected_root))

    return all(a == e for a, e in zip(letters, expected_root))


import re

# Quranic (Uthmani) orthography marks a long-a with a superscript dagger
# alif (ٰ, U+0670) in cases where standard MSA spelling -- which is what
# CAMeL's dictionary is built on -- writes a full alif (ا, U+0627) instead.
# For example the Quranic surface جَنَّٰتٍ ("gardens") is spelled جَنَّات in
# MSA. CAMeL's analyzer does exact string lookups against its dictionary,
# so a literal dagger-alif string that isn't MSA-spelled has no entry at
# all and analyze() silently returns nothing -- not "no good match", but
# genuinely zero analyses, which is why this word skipped straight past
# CAMeL's fallback instead of resolving via it.
#
# This is intentionally narrow: it must NOT strip harakat generally (that
# would break the inflection-aware disambiguation the rest of this module
# depends on -- see camel_stem_gloss/_analysis_score) and must not fold
# hamza-alif variants (that's a distinction meanings.py's own lemma
# matching cares about elsewhere). It only substitutes the one Quranic-only
# character that has no MSA counterpart at all.
_DAGGER_ALIF_RE = re.compile('\u0670')


# -------------------------------------------------------
# Stripping CAMeL's baked-in citation-form "the"
# -------------------------------------------------------
#
# CAMeL's stemgloss dictionary bakes a leading "the" onto many noun/
# adjective entries as a fixed citation-form translation convention (e.g.
# "رحمن" -> "the_Most_Gracious") -- independent of whether the SPECIFIC
# token being analyzed actually carries a definite article. Confirmed by
# the fact this shows up even though camel_stem_gloss's caller
# (meanings.py) only ever passes the bare stem/core morpheme's own
# surface, with any attached ال PREF morpheme already split off and
# analyzed separately -- so CAMeL never even sees an ال here to react to;
# the "the" is purely a lexicographic convention on the dictionary entry
# itself (the same way many English dictionary glosses of Arabic nouns
# default to "the X"), not a per-token definiteness judgement.
#
# This corpus already represents a word's actual definiteness correctly
# and independently via its own ال PREF morpheme (surfaced in
# result["prefix_card"]), so leaving CAMeL's baked-in "the" in the stem
# gloss double-reports it when the token does carry ال (e.g.
# "ٱلرَّحْمَٰنِ" ending up with a stem meaning that's really the whole
# word's meaning), and wrongly implies definiteness when the token
# doesn't carry ال at all (any indefinite occurrence of the same lemma).
# Stripping it here leaves definiteness entirely to the prefix card, and
# the stem gloss to describe only the stem's own sense.
#
# The same double-reporting also happens one level up, in
# services/meanings.py's word["english"] branch: quran.db's own
# per-occurrence `words.english` column is a WHOLE-WORD gloss, so for a
# word carrying BOTH a conjunction prefix and the definite article (e.g.
# وَٱلْمُؤْمِنِينَ, "and the believing men") it bakes in translations for
# BOTH attached prefixes, not just "the". A bare `^the\s+` match doesn't
# fire on text that starts with "and"/"then"/"so", so that whole phrase
# was falling straight through into the STEM MEANING card untouched --
# duplicating what the prefix card already shows for وَ ("and") and ٱلْ
# ("the") separately. "and"/"then"/"so" are exactly the two conjunction
# prefixes this project's `prefixes` table carries (و -> "and",
# ف -> "then, so"), so an optional leading conjunction is included here
# ahead of the optional "the", covering single-prefix ("the X"),
# conjunction-only ("and X"), and the combined ("and the X") cases alike.
_LEADING_DEFINITE_ARTICLE_RE = re.compile(
    r'^(?:(?:and|then|so)\s+)?the\s+(?=\S)', re.IGNORECASE
)


def _strip_baked_in_definite_article(text):
    if not text:
        return text
    return _LEADING_DEFINITE_ARTICLE_RE.sub('', text, count=1)


def strip_definite_article_gloss(text):
    """
    Public wrapper around `_strip_baked_in_definite_article` for callers
    outside this module.

    The double-reporting problem described above isn't unique to live
    CAMeL output -- the same baked-in "the" shows up in meanings.db's
    hand-curated `words.stem_override` column (e.g. "the Most Gracious"
    for ٱلرَّحْمَٰنِ) and, potentially, in `words.stem_meaning`. Those are
    plain curator-written English, not CAMeL's `stemgloss` feature, but
    they need the exact same normalization before being shown in the
    STEM MEANING card, since that card is meant to describe only the
    stem -- definiteness is already reported independently via the
    prefix card. Exposing this here lets services/meanings.py apply the
    same rule uniformly, regardless of which of the three sources
    (override, cache, or live CAMeL) ends up supplying the gloss.
    """
    return _strip_baked_in_definite_article(text)


def _quranic_to_msa_orthography(word):
    """
    Rewrites Quranic-only spelling conventions into their MSA equivalent
    so CAMeL's analyzer has a chance of recognizing the word at all.
    Currently handles the dagger alif; see _DAGGER_ALIF_RE above.
    """

    if not word:
        return word

    return _DAGGER_ALIF_RE.sub('\u0627', word)


_HAMZA_ALIF_VARIANTS = str.maketrans({
    '\u0623': '\u0627',  # أ
    '\u0625': '\u0627',  # إ
    '\u0622': '\u0627',  # آ
    '\u0671': '\u0627',  # ٱ (hamzat wasl)
})

# Vowel/tanween/sukun/tatweel marks only -- deliberately excludes shadda
# (\u0651). strip_harakat() (used elsewhere for search) strips shadda too,
# which is fine for search but wrong here: shadda (gemination) is often
# the ONLY thing distinguishing two genuinely different same-root words
# (e.g. Form II مُعَرَّض "exposed" vs Form IV مُعْرِض "one who turns away"),
# so folding it away can make a wrong CAMeL candidate look like a
# confirmed exact match.
_VOWEL_MARKS_RE = re.compile(r'[\u064B-\u0650\u0652\u0670\u0640]')


def _disambig_key(text):
    """
    A looser key than meanings.py's own matching key, used ONLY to compare
    CAMeL's lemma spelling against this corpus's lemma spelling when
    picking the right analysis -- NOT for meanings.db lookups.

    CAMeL's past-tense citation form and this corpus's lemma can spell the
    same word's leading hamza differently (e.g. CAMeL's "ٱِسْتَعان" vs this
    corpus's "اسْتَعانَ"), which meanings.py deliberately treats as
    distinct (see its own comments) since its curated tables key off that
    distinction. That distinction isn't relevant here, so it's folded away
    on top of vowel-stripping. Shadda is kept (see above) rather than
    stripped, unlike strip_harakat().
    """

    if not text:
        return ""

    return _VOWEL_MARKS_RE.sub('', text).translate(_HAMZA_ALIF_VARIANTS)


def _voice_matches(analysis, expected_voice):
    """
    True if `analysis`'s own voice feature ("vox": "active"/"passive", per
    CAMeL Tools' standard feature set) agrees with `expected_voice` ("active"
    or "passive", as derived from the corpus morpheme's own `features` tag --
    see `expected_voice_from_features` in this module).

    Returns True (i.e. "no mismatch") whenever either side doesn't actually
    know the voice -- `expected_voice` is None (caller didn't pass one, or
    the corpus features didn't clearly say), or the analysis itself carries
    no vox feature at all (non-verb analyses, e.g. nouns/particles, have
    nothing to compare) -- so voice only ever acts as a tiebreaker between
    analyses that both know their voice, never as a penalty for analyses
    that simply don't carry the feature.
    """

    if not expected_voice:
        return True

    analysis_voice = analysis.get("vox")
    if not analysis_voice:
        return True

    return analysis_voice == expected_voice


def _analysis_score(analysis, expected_root, expected_lemma, expected_pos, expected_voice=None):
    """
    Ranks a CAMeL analysis by how much it agrees with what the Qur'an
    corpus already knows about this word -- lower is better (compared as a
    (tier, voice_penalty) tuple, so voice only ever breaks a tie *within* a
    tier and never overrides root/lemma/POS agreement). CAMeL analyzes
    forms out of context and an undiacritized (or partially-vowelled)
    string routinely has many candidate analyses spanning unrelated roots,
    so this is what picks the actually-correct one instead of an arbitrary
    first result:

        0 -- root AND lemma both agree with the corpus
        1 -- root agrees, and coarse part-of-speech (verb vs. non-verb)
             also agrees (a corpus lemma spelling quirk, or a citation-form
             convention mismatch with CAMeL's, can occasionally keep an
             otherwise-correct analysis from hitting tier 0)
        2 -- root agrees, but neither lemma nor part-of-speech confirm it
             (CAMeL's best guess when the corpus's exact lemma isn't in
             its dictionary at all -- occasionally imprecise on short,
             heavily-homographic roots, but still the right root family)
        3 -- root doesn't agree; effectively CAMeL guessing blind

    A root that only "agrees" via a "#" placeholder (see _root_matches) is
    not trusted on its own -- "#" is CAMeL's generic "no real root here"
    marker, not specifically "same root, letter unclear", so a coincidental
    "#"-assisted match against an unrelated homograph must not be allowed to
    outrank or substitute for the real analysis. It's only accepted here if
    independently corroborated by a lemma match (i.e. straight to tier 0);
    otherwise it's treated the same as a non-matching root (tier 3).

    Within a tier, an analysis whose voice disagrees with `expected_voice`
    (when the caller supplied one -- see `_voice_matches`) is penalized so
    it sorts after a same-tier analysis whose voice agrees. This is what
    stops e.g. an active Form I "entice" analysis from beating a passive
    "be tried" analysis for a passive-inflected surface: root and lemma
    both tie at tier 0 for the two (they share a lemma), so without this
    the stable sort just kept whichever CAMeL happened to list first.
    """

    analysis_root = analysis.get("root")

    root_ok_exact = bool(
        expected_root and _root_matches(analysis_root, expected_root, allow_hash=False)
    )
    root_ok_hash_only = (
        not root_ok_exact
        and bool(expected_root and _root_matches(analysis_root, expected_root, allow_hash=True))
    )

    lemma_ok = False
    if expected_lemma and analysis.get("lex"):
        lex_bare = analysis["lex"].split("_")[0]
        lemma_ok = _disambig_key(lex_bare) == _disambig_key(expected_lemma)

    voice_penalty = 0 if _voice_matches(analysis, expected_voice) else 1

    if root_ok_exact and lemma_ok:
        return (0, voice_penalty)

    if root_ok_hash_only:
        # Only trust the "#"-assisted match if the lemma independently
        # confirms it; otherwise it's indistinguishable from a coincidental
        # match against an unrelated root and must not outrank a real one.
        return (0, voice_penalty) if lemma_ok else (3, voice_penalty)

    if not root_ok_exact:
        return (3, voice_penalty)

    if expected_pos:
        is_verb_corpus = expected_pos.upper().startswith("V")
        is_verb_camel = analysis.get("pos") == "verb"
        if is_verb_corpus == is_verb_camel:
            return (1, voice_penalty)

    return (2, voice_penalty)


def expected_voice_from_features(features):
    """
    Derives "active"/"passive" from a corpus morpheme's own `features` tag,
    for use as `camel_stem_gloss`'s `expected_voice` argument.

    Confirmed against quran.db's morphemes.features convention: passive is
    marked with an explicit "PASS" flag (e.g.
    "IMPF|VF:1|PASS|ROOT:فتن|LEM:فَتَنُ|3MP|MOOD:IND"); active carries no
    such flag at all -- it's the implicit default, not a separate "ACT"
    marker -- so any non-empty features string that lacks "PASS" is read as
    active. Returns None (rather than guessing) if `features` is empty,
    since that means there's nothing to disambiguate voice with.
    """

    if not features:
        return None

    return "passive" if "PASS" in features.split("|") else "active"


def camel_stem_gloss(surface, expected_root=None, expected_lemma=None, expected_pos=None, expected_voice=None):
    """
    Returns a short, inflection-aware English gloss for `surface` -- meant
    to be called with the STEM morpheme's own surface (not the whole
    affixed word) -- taken from CAMeL's per-analysis `stemgloss` feature,
    which reflects this exact form's inflection (tense, voice, etc.)
    rather than a static lemma-level dictionary entry.

    `expected_voice`, if given ("active" or "passive" -- see
    `expected_voice_from_features`), is used only as a *tiebreaker* within
    `_analysis_score`'s existing tiers: when CAMeL offers multiple analyses
    that already tie on root/lemma agreement (e.g. an active Form I verb
    sense and a passive sense of the same root+lemma), the one whose own
    voice agrees with the corpus's is preferred, instead of an arbitrary
    stable-sort pick. It never promotes an analysis across tiers.

    Filters out CAMeL's noun_prop (proper noun) analyses on a first pass
    over `_analysis_score`'s ranking, then only reconsiders them on a
    second pass if that first pass came up empty (see inline comment
    below) -- so genuine Quranic proper nouns like مُحَمَّد or رَمَضَان,
    which CAMeL may only have a noun_prop entry for at all, still get a
    gloss, while an ordinary common word that happens to double as a
    given name doesn't lose to that name by arbitrary tie-break.

    Returns None if CAMeL has no analysis, or no analysis carries a
    stemgloss.
    """

    analyses = camel_lookup(surface)

    if not analyses:
        return None

    def best_gloss(pool):
        if not pool:
            return None

        scored = sorted(
            pool,
            key=lambda a: _analysis_score(a, expected_root, expected_lemma, expected_pos, expected_voice),
        )

        for analysis in scored:
            score = _analysis_score(analysis, expected_root, expected_lemma, expected_pos, expected_voice)

            # A tier of 3 means the root doesn't agree with the corpus at
            # all -- i.e. CAMeL is guessing blind on an unrelated homograph.
            # Once we hit one, every later analysis is also tier 3 or worse
            # (scored is sorted ascending on the (tier, voice_penalty)
            # tuple), so it's safe to stop rather than let a confidently
            # wrong-root stemgloss (e.g. a "mortgage" gloss surfacing for a
            # كره word via an unrelated رهن analysis) win by default just
            # because it happens to have a populated stemgloss field.
            if score[0] >= 3:
                break

            gloss = _first_sense(analysis.get("stemgloss"))
            if gloss:
                return gloss

        return None

    # This corpus's own POS tagging tops out at N / V / P (confirmed
    # against quran.db: words.pos and morphemes.tag never contain
    # anything else) -- there is no "proper noun" category here at all.
    # That means a CAMeL analysis tagged noun_prop can never be
    # corroborated by this corpus's expected_pos, no matter how correct
    # it actually is, so _analysis_score's tiers can't rule it out
    # either: if its root and lemma spelling happen to match (common,
    # since CAMeL tags plenty of ordinary triliteral-root words as
    # noun_prop just because the same spelling doubles as a personal
    # name -- e.g. حَقّ "truth" vs "Haqq" the given name, نُور "light" vs
    # "Nour"), it ties the real common-word analysis at tier 0 and can
    # win by arbitrary sort order, surfacing a bare transliterated name
    # as the STEM MEANING instead of an actual gloss.
    #
    # But some Quranic words genuinely ARE proper nouns (مُحَمَّد, رَمَضَان,
    # مِصْر, قُرَيْش...), and for those CAMeL may have no non-proper-noun
    # entry to fall back on at all. So rather than excluding noun_prop
    # outright, it's only excluded on a first pass; a second pass with it
    # included runs only if the first pass found nothing usable.
    non_proper = [a for a in analyses if (a.get("pos") or "").lower() != "noun_prop"]

    return best_gloss(non_proper) or best_gloss(analyses)