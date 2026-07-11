import re

# -------------------------------------------------------
# Arabic diacritics
# -------------------------------------------------------

HARAKAT = re.compile(
    r'[\u0640\u064B-\u065F\u0670\u06D6-\u06ED]'
)

# Qur'anic text sometimes spells a medial/final hamza as a tatweel
# placeholder carrying a combining hamza-above or hamza-below mark
# (e.g. "ـَٔ" or "ـُٔ", as in "ٱلْـَٔاخِرِينَ" or "أَسْـَٔلَكَ") rather than a
# precomposed hamza letter (ء/أ/إ/ؤ/ئ). Different Qur'an text sources
# order the combining marks differently -- quran.db's own stored surfaces
# put the hamza mark directly after the tatweel and the vowel diacritic
# after that (tatweel, hamza-mark, vowel), while text pasted in from
# elsewhere can have the vowel diacritic before the hamza mark instead
# (tatweel, vowel, hamza-mark) -- so both orders are matched here, with
# any vowel diacritic relocated to follow the converted hamza letter
# either way. This has to happen BEFORE the general HARAKAT strip below:
# \u0654/\u0655 (the combining hamza marks) fall inside that regex's
# range, so without this step strip_harakat() would silently delete the
# hamza consonant entirely instead of converting it -- losing a real
# letter, not just a vowel mark, and violating this function's own
# contract of leaving letters untouched.
HAMZA_ON_TATWEEL = re.compile(
    r'\u0640(?:([\u064B-\u0653\u0656-\u065F])?([\u0654\u0655])|([\u0654\u0655])([\u064B-\u0653\u0656-\u065F])?)'
)


def _hamza_on_tatweel_sub(match):
    vowel = match.group(1) or match.group(4) or ""
    return "\u0621" + vowel

# Whitespace left behind once diacritics/annotation marks are removed.
# Qur'anic text conventionally separates a waqf (pause) mark from the
# preceding word with a space (e.g. "قَتَلَهُمْ ۚ"), so once the mark
# itself is stripped by HARAKAT above, a stray space remains. Every caller
# of strip_harakat()/normalize() in this project (search.py's exact-match
# queries, meanings.py's _meaning_key, camel.py's _disambig_key) treats
# the result as a single space-free token to compare against DB columns
# that never contain internal whitespace -- so any leftover whitespace,
# anywhere in the string, is stripped rather than just trimmed from the
# ends, to also cover text pasted with multiple marks (e.g. "... ۚ فَقَالَ").
WHITESPACE = re.compile(r'\s+')

# -------------------------------------------------------
# Letter normalization
# -------------------------------------------------------

TRANSLATION = str.maketrans({

    # Alifs
    "ٱ": "ا",
    "أ": "ا",
    "إ": "ا",
    "آ": "ا",

})


def normalize_hamza_notation(text: str) -> str:
    """
    Converts a hamza spelled via the tatweel + combining-hamza-mark
    convention into a standalone hamza letter, WITHOUT touching any other
    harakat/vowel marks or removing anything else. Unlike strip_harakat(),
    this keeps the word fully vocalized -- it only fixes hamza notation,
    so short vowels are preserved and can still disambiguate homographs
    that share the same stripped/normalized form (e.g. ٱلْءَاخَرِينَ
    "others" vs ٱلْءَاخِرِينَ "the last/latter", which only differ by the
    vowel on the خ and collapse to the same value once harakat is
    stripped).

    Example:
        ٱلْـَٔاخِرِينَ
            ↓
        ٱلْءَاخِرِينَ
    """
    if not text:
        return ""
    return HAMZA_ON_TATWEEL.sub(_hamza_on_tatweel_sub, text)


def strip_harakat(text: str) -> str:
    """
    Removes Arabic vowel marks (harakat), Qur'anic annotation/waqf marks,
    and any whitespace left behind by their removal -- but leaves the
    letters themselves untouched (including a hamza spelled via the
    tatweel + combining-hamza-mark convention, which is converted to a
    standalone hamza letter rather than deleted).

    Example:
        ٱلرَّحْمَٰنِ
            ↓
        ٱلرحمن

        قَتَلَهُمْ ۚ   (word + waqf mark, as commonly pasted from Qur'an text)
            ↓
        قتلهم         (no trailing space)

        ٱلْـَٔاخِرِينَ   (hamza spelled via tatweel + combining hamza-above)
            ↓
        ٱلءاخرين      (hamza kept as a real letter, not dropped)
    """
    if not text:
        return ""
    text = normalize_hamza_notation(text)
    return WHITESPACE.sub("", HARAKAT.sub("", text))


def normalize(text: str) -> str:
    """
    Produces a search-friendly version.

    Example:
        ٱلرَّحْمَٰنِ
            ↓
        الرحمن
    """
    if not text:
        return ""

    return strip_harakat(text).translate(TRANSLATION)