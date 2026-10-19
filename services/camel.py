import re

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


# -------------------------------------------------------
# Wazn (pattern) -- the ف-ع-ل placeholder template
# -------------------------------------------------------
#
# CAMeL's per-analysis `pattern` feature is a digit-templated skeleton of
# the diacritized form: every slot the word's own root radicals occupy is
# written as that radical's 1-based position in the root (1st, 2nd, 3rd,
# and -- for a quadriliteral root -- 4th), while everything else the
# pattern itself contributes -- inserted long vowels, diacritics, a
# doubling shadda, an augment letter like the ت of تَفْعِيل -- is left as
# literal Arabic text exactly as CAMeL supplies it. E.g. for كَاتِب (root
# ك.ت.ب) CAMeL's pattern comes back as something like "1َا2ِ3": slot 1 is
# ك, slot 2 is ت, slot 3 is ب.
#
# Traditional Arabic grammar describes this exact same shape using three
# fixed placeholder letters -- ف (fā'), ع (ʿayn), ل (lām) -- standing in
# for "1st root letter", "2nd root letter", "3rd root letter"
# respectively (a repeated ل covers a 4th, quadriliteral radical, by the
# same convention grammar books use for quadriliteral patterns like
# فَعْلَل). Substituting those placeholder letters for CAMeL's digits is
# what turns "1َا2ِ3" into the familiar وزن (wazn) فَاعِل.

_WAZN_SLOT_LETTERS = ("ف", "ع", "ل", "ل")


def _fa_ala_template(pattern):
    """
    Converts CAMeL's digit-templated `pattern` feature into the
    traditional ف-ع-ل placeholder form (e.g. "1َا2ِ3" -> "فَاعِل"),
    preserving every other character -- diacritics, long vowels, augment
    letters, shadda -- exactly as CAMeL supplied them. Only the digit
    characters are substituted; anything CAMeL emits outside 1-4 (which
    shouldn't happen for a real triliteral/quadriliteral root, but isn't
    assumed impossible) is left as-is rather than guessed at.

    Returns None if `pattern` is falsy.
    """

    if not pattern:
        return None

    out_chars = []
    for ch in pattern:
        if ch.isdigit():
            slot = int(ch) - 1
            if 0 <= slot < len(_WAZN_SLOT_LETTERS):
                out_chars.append(_WAZN_SLOT_LETTERS[slot])
                continue
        out_chars.append(ch)

    return "".join(out_chars)


# CAMeL's own `pattern` feature (used by _fa_ala_template above) is
# unreliable on exactly the words this feature exists to help with most:
# roots containing a hamza radical, or a radical letter that happens to
# repeat elsewhere in the word as a non-root augment. Both come from the
# same root cause -- CAMeL's pattern-templating appears to match root
# radicals against the diacritized surface by literal character identity
# rather than true morphological alignment:
#
#   - Hamza (root radical أ) surfaces differently depending on its
#     carrier letter (ء أ إ آ ؤ ئ) purely by orthographic rule, not as a
#     different letter -- but a literal-character match against the
#     root's own bare ء fails to recognize ؤ/ئ/etc. as "the same
#     radical", so that slot never gets digit-tagged and shows up as
#     literal, un-substituted Arabic in the output.
#   - A radical letter that also happens to appear earlier in the word
#     as an unrelated augment (e.g. مُؤْمِن's Form IV prefix م
#     coincidentally sharing a letter with its own root's 2nd radical
#     م) can get matched at that earlier, wrong position instead, again
#     leaving the true radical's own occurrence un-substituted later on.
#
# Confirmed empirically against a real lookup: مُؤْمِن (root أ.م.ن) came
# back as عُؤْمِل instead of the correct مُفْعِل -- literal ؤ where
# radical-1 should have substituted to ف, and a wrongly-matched ع where
# the *augment* م should have stayed literal, while the *true* radical-2
# م later in the word stayed un-substituted instead of becoming ع.
#
# _build_wazn_from_diac below reconstructs the same digit-template
# ourselves from the analysis's own `diac` (fully diacritized surface)
# and `root` fields, instead of trusting CAMeL's pre-built `pattern`:
# it walks the root's radicals IN ORDER and, for each one, finds its
# first remaining occurrence in `diac` scanning strictly left-to-right
# from just after the previous radical's match. That ordering means an
# earlier unrelated occurrence of the same letter is never available to
# be mismatched onto a later radical, and hamza-carrier spelling no
# longer breaks the match since carriers are treated as interchangeable.
#
# This is tried FIRST (see camel_wazn below); the old CAMeL-`pattern`-
# based _fa_ala_template is kept only as a fallback for analyses that
# somehow lack a usable `diac`/`root` pair.

_HAMZA_FORMS = set("ءأإآؤئ")


def _radicals_from_root(root):
    """
    Splits a root string into its ordered list of radical characters.
    Handles both CAMeL's own dot-separated format (e.g. "أ.م.ن") and
    this corpus's plain concatenated format (e.g. "أمن", no dots) --
    if there's no "." in the string, each character is treated as one
    radical.

    Returns None if `root` is falsy, or if any radical is CAMeL's "#"
    (letter unclear) placeholder -- a template can't be built
    confidently around an unknown radical, so this is treated the same
    as "no root to work from" rather than guessed at.
    """

    if not root:
        return None

    radicals = root.split(".") if "." in root else list(root)
    if not radicals or any((not r or r == "#") for r in radicals):
        return None

    return radicals


def _letters_match(diac_char, radical_char):
    """
    True if `diac_char` (a character from the fully-diacritized surface)
    is the same radical as `radical_char` (a character from CAMeL's root
    string) -- treating any hamza spelling (ء أ إ آ ؤ ئ) as
    interchangeable, since which hamza-carrier a radical surfaces as is
    an orthographic rule driven by neighboring vowels, not a different
    letter. Any other letter must match exactly.
    """

    if radical_char in _HAMZA_FORMS:
        return diac_char in _HAMZA_FORMS

    return diac_char == radical_char


def _build_wazn_from_diac(analysis, expected_root=None):
    """
    Reconstructs the ف-ع-ل template directly from `analysis`'s own
    `diac` (fully-diacritized surface), walking a set of root radicals
    in order and matching each one's first remaining occurrence
    left-to-right in `diac` -- see the module comment above for why
    this is more trustworthy than CAMeL's own `pattern` feature on
    hamza/repeated-letter roots.

    `expected_root` (the CORPUS's own root for this word, e.g. "أمن" --
    same value camel_wazn already receives and uses for scoring) is
    tried FIRST, ahead of analysis.get("root"). This matters because
    CAMeL's own per-analysis root can itself be incomplete: for
    مُؤْمِن CAMeL's analysis carries root "#.م.ن" -- literally marking
    the hamza radical "unclear" -- so building off analysis["root"]
    alone reproduces the same missing-radical problem this function
    exists to route around. The corpus's root doesn't have that gap
    (confirmed directly against quran.db's `roots` table: plain,
    undotted, no "#" placeholders anywhere), and by the time this runs
    the analysis has already been confirmed root-agreeing against
    expected_root via _analysis_score, so it's a trustworthy source of
    the FULL radical set even when CAMeL's own is partial. Falls back
    to analysis.get("root") only if no expected_root was supplied.

    Returns None (rather than a guessed-at wazn) if:
      - there's no diac, and/or no usable root from either source (see
        _radicals_from_root),
      - the root has more radicals than _WAZN_SLOT_LETTERS covers,
      - or any radical genuinely can't be found in the remaining diac
        text at all -- which shouldn't happen for a real analysis, but
        isn't assumed impossible, and is exactly the kind of case
        that should fall back rather than show a wrong wazn.
    """

    diac = analysis.get("diac")
    radicals = _radicals_from_root(expected_root) or _radicals_from_root(analysis.get("root"))

    if not diac or not radicals or len(radicals) > len(_WAZN_SLOT_LETTERS):
        return None

    slot_letter_by_index = {}
    search_from = 0

    for slot, radical in enumerate(radicals):
        found_at = None
        for i in range(search_from, len(diac)):
            if _letters_match(diac[i], radical):
                found_at = i
                break

        if found_at is None:
            return None

        slot_letter_by_index[found_at] = _WAZN_SLOT_LETTERS[slot]
        search_from = found_at + 1

    return "".join(slot_letter_by_index.get(i, ch) for i, ch in enumerate(diac))


# Plain-English labels for the wazns a reader most often runs into in the
# Qur'an, keyed on the FULLY DIACRITIZED ف-ع-ل template exactly as
# _fa_ala_template produces it -- deliberately not stripped of harakat,
# because several noun and verb patterns share the identical bare
# consonant skeleton and only the vowelling tells them apart (e.g. فاعل
# is both the Form I active participle فَاعِل, "one who does [x]", and the
# Form III perfect verb فَاعَلَ, "he did [x] [to/with someone]" -- stripping
# diacritics would collide the two).
#
# This is intentionally NOT exhaustive -- Arabic derivational morphology
# has dozens of minor/rare patterns beyond these -- it covers the common
# noun/adjective shapes. Anything not listed here still gets *a* label
# via _generic_wazn_label() below, just a less specific one. Extend this
# table freely as gaps are noticed in real lookups.
_NOUN_WAZN_LABELS = {
    "فَاعِل": "Active participle — one who does the action",
    "مَفْعُول": "Passive participle — one to whom the action is done",
    "فَعَّال": "Intensive / professional noun — one who does it habitually",
    "فَعُول": "Intensive active participle",
    "فَعِيل": "Intensive adjective",
    "فَعْلَان": "Adjective describing a state or condition",
    "أَفْعَل": "Elative (comparative/superlative), or a color/defect adjective",
    "فِعَال": "Verbal noun (masdar), Form I",
    "فُعُول": "Verbal noun (masdar), Form I",
    "مِفْعَال": "Instrument noun (intensive)",
    "مِفْعَل": "Instrument noun",
    "مَفْعَل": "Noun of place or time",
    "مَفْعَلَة": "Noun of place or time",
    "تَفْعِيل": "Verbal noun (masdar), Form II",
    "مُفَعِّل": "Active participle, Form II",
    "مُفَعَّل": "Passive participle, Form II",
    "مُفَاعَلَة": "Verbal noun (masdar), Form III",
    "مُفَاعِل": "Active participle, Form III",
    "إِفْعَال": "Verbal noun (masdar), Form IV",
    "مُفْعِل": "Active participle, Form IV",
    "مُفْعَل": "Passive participle, Form IV",
    "تَفَعُّل": "Verbal noun (masdar), Form V",
    "مُتَفَعِّل": "Active participle, Form V",
    "تَفَاعُل": "Verbal noun (masdar), Form VI",
    "مُتَفَاعِل": "Active participle, Form VI",
    "اِنْفِعَال": "Verbal noun (masdar), Form VII",
    "مُنْفَعِل": "Active participle, Form VII",
    "اِفْتِعَال": "Verbal noun (masdar), Form VIII",
    "مُفْتَعِل": "Active participle, Form VIII",
    "اِسْتِفْعَال": "Verbal noun (masdar), Form X",
    "مُسْتَفْعِل": "Active participle, Form X",
    "مُسْتَفْعَل": "Passive participle, Form X",
}


# What each derived "Form" (measure) typically adds to the bare root's
# meaning -- keyed on the roman numeral exactly as it appears inside the
# _NOUN_WAZN_LABELS strings above (e.g. "Form II" -> "II"). Form I has no
# roman numeral in those strings (it's the unmarked base shape), so it's
# looked up separately by _wazn_specific_note() below rather than being
# matched out of a label. Form IX (colors/physical defects, e.g. اِحْمَرَّ
# "to turn red") is rare enough in Qur'anic vocabulary that it's left out
# rather than guessed at.
_WAZN_FORM_MEANINGS = {
    "I": "This is the base, unaugmented shape of the root — no letters added beyond the pattern's own vowelling.",
    "II": "The root's middle letter is doubled. This usually makes the action causative or intensive — e.g. \u0639َلِمَ \"he knew\" becomes عَلَّمَ \"he taught\" (caused someone to know); كَسَرَ \"he broke\" becomes كَسَّرَ \"he smashed\" (broke intensively).",
    "III": "A long alif is added after the root's 1st letter. This usually implies the action is directed at, done together with, or attempted against someone — e.g. كَتَبَ \"he wrote\" becomes كَاتَبَ \"he corresponded with [someone]\".",
    "IV": "A hamza is prefixed to the root. This usually makes the action causative — e.g. خَرَجَ \"he went out\" becomes أَخْرَجَ \"he brought out\" (caused to go out).",
    "V": "Form II with a ت prefixed. This is usually the reflexive or resultative counterpart of Form II — e.g. عَلَّمَ \"he taught\" becomes تَعَلَّمَ \"he learned\" (taught himself).",
    "VI": "Form III with a ت prefixed. This usually implies the action happening mutually, between two or more parties — e.g. كَاتَبَ \"he corresponded with\" becomes تَكَاتَبَ \"they corresponded with each other\".",
    "VII": "An ن is prefixed to the root. This gives a passive or reflexive sense — e.g. كَسَرَ \"he broke [something]\" becomes اِنْكَسَرَ \"it broke/got broken\" — and is never used with a human agent acting on someone else.",
    "VIII": "A ت is inserted after the root's 1st letter. This usually gives a reflexive or \"middle voice\" sense, where the subject acts on or for itself — e.g. جَمَعَ \"he gathered [something]\" becomes اِجْتَمَعَ \"he gathered [himself, i.e. assembled]\".",
    "IX": "The root's last letter is doubled after a helping alif. This shape is used for colours and physical defects — e.g. اِبْيَضَّ \"it turned white\", اِسْوَدَّ \"it turned black\".",
    "XI": "Like Form IX but with a long ā before the doubled last letter. It is used for colours, with a stronger sense — e.g. اِدْهَامَّ \"it became deep dark green\".",
    "X": "اِسْتَ is prefixed to the root. This usually implies seeking, requesting, or regarding something as having the root's quality — e.g. غَفَرَ \"he forgave\" becomes اِسْتَغْفَرَ \"he sought forgiveness\".",
}


def _wazn_specific_note(wazn, label):
    """
    Word-specific explanation for the wazn popover, meant to sit
    *alongside* the general fa-la-system intro (data-fused-notes on the
    info-badge in result.html) rather than replace it -- the intro
    explains the ف-ع-ل placeholder convention in the abstract; this adds
    a sentence about what THIS word's particular derived form (measure)
    grammatically contributes.

    `label` is whatever _NOUN_WAZN_LABELS/_generic_wazn_label already
    produced (e.g. "Active participle, Form II" or the label-less
    "Active participle — one who does the action" for a Form I shape).
    Form I patterns don't spell out "Form I" in the label text the way
    II-X do, so a wazn found in _NOUN_WAZN_LABELS with no roman numeral
    in its label is treated as Form I; a wazn NOT in that curated table
    (i.e. label came from the generic verb/adjective/noun fallback) gets
    a shorter, non-form-specific sentence instead of a guessed Form
    number -- see camel_wazn's docstring on why verb forms in particular
    aren't reliably distinguishable from the pattern alone.

    Returns None if there's nothing to say (no wazn/label at all).
    """

    if not wazn or not label:
        return None

    match = re.search(r"Form\s+([IVX]+)\b", label)
    if match:
        form = match.group(1)
    elif wazn in _NOUN_WAZN_LABELS:
        form = "I"
    else:
        form = None

    # PATCH: the Form I sentence says no letters were added, so it only
    # fits a shape built from ف ع ل and vowels alone (not مَفْعَل, أَفْعَل...)
    if form == "I" and re.sub(r"[\u064b-\u0652\u0670اوي]", "", wazn) not in ("فعل", "فعلة"):
        form = None

    if form and form in _WAZN_FORM_MEANINGS:
        return (
            "For this word specifically, the pattern is {wazn} — {label}. "
            "{form_meaning}"
        ).format(wazn=wazn, label=label, form_meaning=_WAZN_FORM_MEANINGS[form])

    return (
        "For this word specifically, the pattern is {wazn} — {label}. "
        "It shows where the root letters sit in the dictionary form of the "
        "word, with ف, ع and ل standing for the 1st, 2nd and 3rd root letters."
    ).format(wazn=wazn, label=label)


# Plain-English Form labels for the ten classical verb "measures", keyed
# on the fully diacritized PERFECT (past-tense, 3rd-person-masc-singular)
# template -- i.e. the citation form Arabic dictionaries use, which is
# also what _camel_guess_wazn in meanings.py prefers (it computes the
# wazn from word["lemma"] when one is available, falling back to the raw
# surface only when it isn't). That's deliberate here too: it means this
# table only needs the ~15 perfect-tense shapes below rather than every
# tense/mood/person combination _build_wazn_from_diac could otherwise
# hand back (an imperfect verb's template carries its own ي/ت/أ/ن subject
# prefix, e.g. Form IV imperfect يُفْعِلُ vs perfect أَفْعَلَ) -- those
# still resolve, just via _generic_wazn_label's shorter fallback.
#
# Form I has three possible middle-vowel shapes depending on the root
# (فَعَلَ/فَعِلَ/فَعُلَ) rather than one fixed pattern the way Forms II-X
# do; all three are listed. Form IX (اِفْعَلَّ, colors/physical defects)
# is left out for the same reason _WAZN_FORM_MEANINGS leaves it out --
# rare enough in Qur'anic vocabulary that it's not worth guessing at.
_VERB_WAZN_LABELS = {
    "فَعَلَ": "Verb, Form I",
    "فَعِلَ": "Verb, Form I",
    "فَعُلَ": "Verb, Form I",
    "فُعِلَ": "Verb, Form I (passive)",

    "فَعَّلَ": "Verb, Form II",
    "فُعِّلَ": "Verb, Form II (passive)",

    "فَاعَلَ": "Verb, Form III",
    "فُوعِلَ": "Verb, Form III (passive)",

    "أَفْعَلَ": "Verb, Form IV",
    "أُفْعِلَ": "Verb, Form IV (passive)",

    "تَفَعَّلَ": "Verb, Form V",

    "تَفَاعَلَ": "Verb, Form VI",

    "اِنْفَعَلَ": "Verb, Form VII",

    "اِفْتَعَلَ": "Verb, Form VIII",
    "اُفْتُعِلَ": "Verb, Form VIII (passive)",

    "اِسْتَفْعَلَ": "Verb, Form X",
    "اُسْتُفْعِلَ": "Verb, Form X (passive)",
}


def _generic_wazn_label(pos, expected_voice):
    """
    Fallback label for a wazn that isn't in _NOUN_WAZN_LABELS -- most
    often a verb (this table only curates noun/adjective shapes; verb
    templates collide too heavily with noun ones on bare letters to
    label reliably without deeper feature support) or an uncommon noun
    shape. Built from whatever the corpus/CAMeL already tells us about
    the word rather than guessing the specific pattern name.
    """

    pos = (pos or "").lower()

    if pos == "verb":
        return "Verb pattern (passive)" if expected_voice == "passive" else "Verb pattern"

    if pos in ("noun", "noun_prop", "noun_num", "noun_quant"):
        return "Noun pattern"

    if pos.startswith("adj"):
        return "Adjective pattern"

    return "Word pattern"


def _diac_matches_surface(analysis, surface):
    """
    True if `analysis`'s own fully-diacritized `diac` reduces to the same
    consonant skeleton (letters + shadda, vowels/tanween stripped -- see
    _disambig_key) as `surface` itself.

    CAMeL can return several analyses that all share the corpus's
    root+lemma but represent genuinely different inflected shapes of that
    same lemma -- most commonly an energetic-mood (تَوْكِيد) form with a
    heavy tawkid nun fused onto the final radical as an *extra*, non-root
    ن. Those still tie with the plain form at tier 0 in _analysis_score
    (same root, same lemma), so nothing there rules them out.

    This matters specifically for camel_wazn: _build_wazn_from_diac
    reconstructs the template from whichever analysis's `diac` it's
    handed, letter-for-letter. If that analysis is secretly a different
    inflected form of the lemma, its extra letters (and any shadda they
    carry) land in the output as if they belonged to the displayed word.
    Confirmed against a real lookup: أَمِنَ was coming back as فَعِلَّ
    (spurious trailing shadda) instead of فَعِلَ, because the analysis
    used to build the pattern wasn't actually أَمِنَ's own -- it was a
    same-root/same-lemma analysis whose diac carried an extra ن.

    Returns True if `surface` is falsy, so a caller with nothing to
    compare against isn't blocked from using this analysis at all.
    """

    if not surface:
        return True

    return _disambig_key(analysis.get("diac")) == _disambig_key(surface)


# ==========================================================
# PATCH: wazn from the corpus's own grammatical tags + lemma
# ==========================================================
#
# The audit found 4,262 words with a malformed wazn and 1,254 nouns given
# a verb pattern. Causes:
#   1. When _build_wazn_from_diac couldn't place every radical (hollow /
#      defective / doubled roots: كون, قوي, صوب, ربب), the code fell back
#      to CAMeL's own `pattern`, which just leaves the missing slot out --
#      كانَ -> فالَ, قُوَّة -> فُوَّة, رَبّ -> فَعّ, الله -> اعلّٰل.
#   2. The analysis was picked with vowel-blind matching, so a VERB
#      analysis (خَلِقَ) could win for the NOUN lemma خَلْق -> فَعِلَ.
#
# The corpus already says what the word IS: the stem morpheme's features
# carry the verb form (VF:1..10) for verbs, participles (ACT_PCPL /
# PASS_PCPL) and verbal nouns (VN). For those, the classical wazn is FIXED
# by the form -- مُصِيبَة is مُفْعِلَة and اِتَّقَى is اِفْتَعَلَ whatever the
# spelling of the weak root -- so it is taken from the form, not guessed
# from letters. Everything else is built letter-for-letter from the
# corpus lemma; if any radical isn't literally written there (weak or
# doubled root, a long vowel standing where a radical would be, fused آ,
# assimilation), the real wazn depends on the underlying form and NO wazn
# is returned rather than a wrong one.

_QURANIC_MARKS_RE = re.compile('[ۖ-ۭـ]')
_HARAKAT = set('ًٌٍَُِّْ')

_FORM_PERFECT = {2: "فَعَّلَ", 3: "فَاعَلَ", 4: "أَفْعَلَ", 5: "تَفَعَّلَ", 6: "تَفَاعَلَ",
                 7: "اِنْفَعَلَ", 8: "اِفْتَعَلَ", 9: "اِفْعَلَّ", 10: "اِسْتَفْعَلَ"}
_FORM_ROMAN = {1: "I", 2: "II", 3: "III", 4: "IV", 5: "V", 6: "VI", 7: "VII", 8: "VIII", 9: "IX", 10: "X"}
_FORM_ACT_PCPL = {1: "فَاعِل", 2: "مُفَعِّل", 3: "مُفَاعِل", 4: "مُفْعِل", 5: "مُتَفَعِّل", 6: "مُتَفَاعِل",
                  7: "مُنْفَعِل", 8: "مُفْتَعِل", 9: "مُفْعَلّ", 10: "مُسْتَفْعِل"}
_FORM_PASS_PCPL = {1: "مَفْعُول", 2: "مُفَعَّل", 3: "مُفَاعَل", 4: "مُفْعَل", 5: "مُتَفَعَّل", 6: "مُتَفَاعَل",
                   7: "مُنْفَعَل", 8: "مُفْتَعَل", 10: "مُسْتَفْعَل"}
# Only the derived forms whose verbal noun has a single fixed shape.
_FORM_VN = {4: "إِفْعَال", 5: "تَفَعُّل", 6: "تَفَاعُل", 7: "اِنْفِعَال", 8: "اِفْتِعَال", 10: "اِسْتِفْعَال"}


def _corpus_wazn_template(lemma, root):
    radicals = _radicals_from_root(root)
    if not lemma or not radicals or len(radicals) > len(_WAZN_SLOT_LETTERS):
        return None

    text = _QURANIC_MARKS_RE.sub('', _quranic_to_msa_orthography(lemma))
    out = list(text)
    search_from = 0
    for slot, radical in enumerate(radicals):
        found_at = None
        for i in range(search_from, len(text)):
            if _letters_match(text[i], radical):
                found_at = i
                break
        if found_at is None:
            return None
        ch = text[found_at]
        nxt = text[found_at + 1] if found_at + 1 < len(text) else ""
        prv = text[found_at - 1] if found_at > 0 else ""
        # fused hamza+alif: one letter is a radical AND an augment
        if ch == 'آ':
            return None
        # assimilated first radical (اِتَّبَعَ, يَذَّكَّرُ)
        if slot == 0 and 'ّ' in text[found_at + 1:found_at + 3]:
            return None
        # و/ي written as a LONG VOWEL (no vowel of its own, after a damma /
        # kasra) is not the consonantal radical: مُبِين is مُفْعِل, not مُفِعل
        if ch in 'وي' and nxt not in _HARAKAT and prv in ('ُ', 'ِ'):
            return None
        out[found_at] = _WAZN_SLOT_LETTERS[slot]
        search_from = found_at + 1

    wazn = "".join(out)
    # A letter directly before a long alif carries fatha; corpus lemmas
    # sometimes omit it (عالَم).
    fixed = []
    for i, ch in enumerate(wazn):
        fixed.append(ch)
        if (i + 1 < len(wazn) and wazn[i + 1] == 'ا' and ch not in _HARAKAT
                and ch not in 'اأإآٱٰ'):
            fixed.append('َ')
    wazn = "".join(fixed)

    bare = re.sub('[ً-ٰٕ]', '', wazn)
    if bare.count("ف") != 1 or bare.count("ع") != 1 or not (bare.find("ف") < bare.find("ع") < bare.rfind("ل")):
        return None
    return wazn


def _label_key(wazn):
    """Comparison key for the label tables: ignores vowel marks the corpus
    lemmas sometimes leave out (fatha before alif, kasra/damma on a leading
    hamzat-wasl alif, sukun)."""
    if not wazn:
        return wazn
    k = wazn.replace('ٱ', 'ا').replace('َا', 'ا').replace('ْ', '')
    if k[:2] in ('اِ', 'اُ'):
        k = 'ا' + k[2:]
    return k


_NOUN_LABELS_BY_KEY = {_label_key(k): v for k, v in _NOUN_WAZN_LABELS.items()}
_VERB_LABELS_BY_KEY = {_label_key(k): v for k, v in _VERB_WAZN_LABELS.items()}
_FORM_I_PERFECT_KEYS = {_label_key(k) for k in ("فَعَلَ", "فَعِلَ", "فَعُلَ")}


def _features_info(features):
    parts = (features or "").split("|")
    vf = None
    for p in parts:
        if p.startswith("VF:"):
            try:
                vf = int(p[3:])
            except ValueError:
                vf = None
    return parts, vf


def camel_wazn(surface, expected_root=None, expected_lemma=None, expected_pos=None,
               expected_voice=None, features=None):
    """PATCHED: see the module note above. Returns the same dict shape as
    before, or None when no wazn can be given with certainty."""
    if not expected_root or not expected_lemma:
        return None

    parts, vf = _features_info(features)
    is_verb = (expected_pos or "").upper().startswith("V")
    feminine = _QURANIC_MARKS_RE.sub('', expected_lemma).rstrip('ً-ْ').endswith('ة') or \
        re.sub('[ً-ْ]', '', expected_lemma).endswith('ة')

    wazn = label = None
    if is_verb:
        if vf and vf >= 2:
            wazn = _FORM_PERFECT.get(vf)
            label = f"Verb, Form {_FORM_ROMAN[vf]}" if wazn else None
        else:
            t = _corpus_wazn_template(expected_lemma, expected_root)
            if t:
                # perfect 3MS citation form ends in fatha; corpus lemmas
                # are sometimes written with another final vowel (عَقَلُ)
                t = re.sub('[ً-ْ]*$', '', t) + 'َ'
                if _label_key(t) in _FORM_I_PERFECT_KEYS:
                    wazn, label = t, "Verb, Form I"
    elif vf and ("ACT_PCPL" in parts or "PASS_PCPL" in parts):
        table = _FORM_ACT_PCPL if "ACT_PCPL" in parts else _FORM_PASS_PCPL
        base = table.get(vf)
        if base:
            wazn = base + ('َة' if feminine else '')
            kind = "Active" if "ACT_PCPL" in parts else "Passive"
            label = (f"{kind} participle — " + ("one who does the action" if kind == "Active" else "one to whom the action is done")) \
                if vf == 1 else f"{kind} participle, Form {_FORM_ROMAN[vf]}"
    elif vf and "VN" in parts and vf in _FORM_VN and not feminine:
        wazn = _FORM_VN[vf]
        label = f"Verbal noun (masdar), Form {_FORM_ROMAN[vf]}"
    else:
        t = _corpus_wazn_template(expected_lemma, expected_root)
        if t:
            wazn = t
            table_label = _NOUN_LABELS_BY_KEY.get(_label_key(t))
            # A shape in the label table only says what words of that shape
            # USUALLY are; كِتَاب has the masdar shape فِعَال and سَبِيل the
            # adjective shape فَعِيل without being either. So a label that
            # names a word class is only shown when the corpus's own tags
            # agree with it.
            adjective_like = table_label and table_label.startswith(
                ("Intensive adjective", "Adjective describing", "Intensive active participle",
                 "Intensive / professional noun", "Elative"))
            if "VN" in parts:
                label = f"Verbal noun (masdar), Form {_FORM_ROMAN.get(vf or 1, 'I')}"
            elif "ADJ" in parts:
                label = table_label if adjective_like else "Adjective pattern"
            elif table_label and table_label.startswith("Elative"):
                label = table_label   # أَفْعَل on a non-verb: elative / colour adjective
            else:
                label = "Noun pattern"

    if not wazn:
        return None
    note = _wazn_specific_note(wazn, label)
    return {"wazn": wazn, "wazn_label": label, "wazn_note": note}


def _old_camel_wazn(surface, expected_root=None, expected_lemma=None, expected_pos=None, expected_voice=None):
    """
    Best available wazn (morphological pattern) for `surface` -- meant to
    be called the same way as camel_stem_gloss: with the STEM morpheme's
    own surface, and the corpus's own root/lemma/pos/voice as
    disambiguation hints (see camel_stem_gloss's docstring for why --
    an undiacritized surface routinely has several unrelated CAMeL
    analyses spanning different roots, and _analysis_score is what picks
    the one the corpus already confirms is correct, exactly as it does
    for the stem gloss).

    Returns a dict:
        {"wazn": "فَاعِل", "wazn_label": "Active participle — ...", "wazn_note": "..."}
    or None if CAMeL has no analysis at all, or no analysis scoring
    tier < 3 (i.e. root-agreeing -- see _analysis_score) yields a
    trustworthy wazn -- see _build_wazn_from_diac's docstring for what
    "trustworthy" means and why the wazn is deliberately suppressed
    rather than guessed at when it isn't confident.
    """

    analyses = camel_lookup(surface)

    if not analyses:
        return None

    def best_pattern(pool):
        if not pool:
            return None

        scored = sorted(
            pool,
            key=lambda a: _analysis_score(a, expected_root, expected_lemma, expected_pos, expected_voice),
        )

        # Split into analyses that are actually the same word as `surface`
        # (see _diac_matches_surface) and everything else that merely ties
        # on root/lemma -- e.g. an energetic-mood variant of the same
        # lemma. The matching group is tried first and, within it, order
        # is still governed by the existing tier/voice_penalty sort; the
        # non-matching group is kept only as a last resort, for the rare
        # case where CAMeL genuinely has no analysis whose diac matches
        # the surface at all (favoring some wazn over none).
        matching_ids = {id(a) for a in scored if _diac_matches_surface(a, surface)}
        matching = [a for a in scored if id(a) in matching_ids]
        fallback = [a for a in scored if id(a) not in matching_ids]

        for candidate_pool in (matching, fallback):
            for analysis in candidate_pool:
                score = _analysis_score(analysis, expected_root, expected_lemma, expected_pos, expected_voice)

                # Tier 3 means the root doesn't agree with the corpus at
                # all -- CAMeL guessing blind on an unrelated homograph.
                # Stop rather than build a confidently wrong pattern from
                # it (same reasoning as camel_stem_gloss's best_gloss
                # loop). Each candidate_pool is still sorted by score, so
                # once a tier-3 analysis appears, nothing later in that
                # same pool can be better.
                if score[0] >= 3:
                    break

                # Prefer reconstructing the wazn ourselves from diac+root
                # (see the module comment above _HAMZA_FORMS) -- CAMeL's
                # own `pattern` feature is only used as a fallback, for
                # analyses that somehow lack a usable diac/root pair.
                wazn = _build_wazn_from_diac(analysis, expected_root) or _fa_ala_template(analysis.get("pattern"))
                if wazn:
                    return analysis, wazn

        return None

    # Same noun_prop two-pass handling as camel_stem_gloss: excluded on
    # the first pass so a personal name doesn't win an arbitrary tie
    # against the real analysis, but reconsidered on a second pass if the
    # first pass found nothing at all.
    non_proper = [a for a in analyses if (a.get("pos") or "").lower() != "noun_prop"]

    found = best_pattern(non_proper) or best_pattern(analyses)
    if not found:
        return None

    analysis, wazn = found

    label = _NOUN_WAZN_LABELS.get(wazn) or _VERB_WAZN_LABELS.get(wazn) or _generic_wazn_label(analysis.get("pos"), expected_voice)
    note = _wazn_specific_note(wazn, label)

    return {"wazn": wazn, "wazn_label": label, "wazn_note": note}