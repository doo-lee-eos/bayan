import sys
import os
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


from services import camel  # noqa: E402


def analysis(root=None, lex=None, pos=None, stemgloss=None):
    """Builds a fake CAMeL analysis dict with just the fields camel.py reads."""
    d = {}
    if root is not None:
        d["root"] = root
    if lex is not None:
        d["lex"] = lex
    if pos is not None:
        d["pos"] = pos
    if stemgloss is not None:
        d["stemgloss"] = stemgloss
    return d


class TestAnalysisScore(unittest.TestCase):
    """Direct tests of the tiering logic, independent of CAMeL itself."""

    def test_lemma_match_is_tier_0(self):
        a = analysis(root="ل.ق.ي", lex="لَقِيَ_1", pos="verb")
        score = camel._analysis_score(a, expected_root="لقي", expected_lemma="لَقِيَ", expected_pos="V")
        self.assertEqual(score, 0)

    def test_pos_confirmed_without_lemma_match_is_tier_1(self):
        a = analysis(root="ل.ق.ي", lex="لَقًى_2", pos="verb")
        score = camel._analysis_score(a, expected_root="لقي", expected_lemma="لَقِيَ", expected_pos="V")
        self.assertEqual(score, 1)

    def test_pos_conflict_is_now_tier_3_not_tier_2(self):
        """
        The core bug: an offal(noun) analysis sharing a root with a corpus
        VERB occurrence must NOT tie with a genuine "POS unknown" tier-2
        guess -- it actively contradicts what the corpus already knows.
        """
        a = analysis(root="ل.ق.ي", lex="لُقًى_1", pos="noun", stemgloss="offal")
        score = camel._analysis_score(a, expected_root="لقي", expected_lemma="لَقِيَ", expected_pos="V")
        self.assertEqual(score, 3)

    def test_root_match_with_no_pos_info_is_tier_2(self):
        a = analysis(root="ل.ق.ي", lex="لَقًى_2", pos="noun")
        score = camel._analysis_score(a, expected_root="لقي", expected_lemma="لَقِيَ", expected_pos=None)
        self.assertEqual(score, 2)

    def test_no_root_match_is_tier_3(self):
        a = analysis(root="ك.ت.ب", lex="كَتَبَ_1", pos="verb")
        score = camel._analysis_score(a, expected_root="لقي", expected_lemma="لَقِيَ", expected_pos="V")
        self.assertEqual(score, 3)

    def test_hash_wildcard_root_radical_still_matches(self):
        a = analysis(root="ع.#.ن")
        score = camel._analysis_score(a, expected_root="عون", expected_lemma=None, expected_pos=None)
        self.assertNotEqual(score, 3)


class TestStemGloss(unittest.TestCase):
    """Tests of camel_stem_gloss's tier-selection behavior end to end,
    with camel_lookup mocked so no real CAMeL Tools install is needed."""

    def test_offal_vs_meet_homograph_returns_none_when_only_noun_analysis_exists(self):
        """
        The reported bug case: لَقِيتُمُ ("you [pl.] met") stemmed down to
        the bare root form, where CAMeL's only analysis on that root is
        the unrelated noun "offal". Previously this scored tier 2 and its
        gloss ("offal") was returned. Now it must be rejected (tier 3),
        so the caller falls back to word_meaning instead.
        """
        analyses = [
            analysis(root="ل.ق.ي", lex="لُقًى_1", pos="noun", stemgloss="offal"),
        ]
        with patch.object(camel, "camel_lookup", return_value=analyses):
            result = camel.camel_stem_gloss(
                "لقي", expected_root="لقي", expected_lemma="لَقِيَ", expected_pos="V"
            )
        self.assertIsNone(result)

    def test_correct_verb_analysis_wins_over_conflicting_noun_homograph(self):
        """When both a same-root noun (POS-conflicting) and a same-root verb
        (POS-confirmed) analysis exist, the verb one must be selected even
        if it's listed after the noun."""
        analyses = [
            analysis(root="ل.ق.ي", lex="لُقًى_1", pos="noun", stemgloss="offal"),
            analysis(root="ل.ق.ي", lex="لَقًى_2", pos="verb", stemgloss="meet"),
        ]
        with patch.object(camel, "camel_lookup", return_value=analyses):
            result = camel.camel_stem_gloss(
                "لقي", expected_root="لقي", expected_lemma="لَقِيَ", expected_pos="V"
            )
        self.assertEqual(result, "meet")

    def test_returns_none_when_no_analysis_shares_the_root(self):
        """Second gap this fix closed: previously best_score==3 (no root
        match at all) was never special-cased, so a wrong-root gloss could
        still be returned. Now it must be None."""
        analyses = [
            analysis(root="ك.ت.ب", lex="كَتَبَ_1", pos="verb", stemgloss="write"),
        ]
        with patch.object(camel, "camel_lookup", return_value=analyses):
            result = camel.camel_stem_gloss(
                "لقي", expected_root="لقي", expected_lemma="لَقِيَ", expected_pos="V"
            )
        self.assertIsNone(result)

    def test_tier_0_lemma_match_unaffected_by_the_fix(self):
        analyses = [
            analysis(root="ل.ق.ي", lex="لُقًى_1", pos="noun", stemgloss="offal"),
            analysis(root="ل.ق.ي", lex="لَقِيَ_1", pos="verb", stemgloss="meet;encounter"),
        ]
        with patch.object(camel, "camel_lookup", return_value=analyses):
            result = camel.camel_stem_gloss(
                "لقي", expected_root="لقي", expected_lemma="لَقِيَ", expected_pos="V"
            )
        self.assertEqual(result, "meet")

    def test_still_skips_within_tier_to_find_a_usable_stemgloss(self):
        """Regression check: the existing 'don't break out of the best
        tier just because the first candidate lacks a stemgloss' behavior
        must still work after the fix."""
        analyses = [
            analysis(root="ل.ق.ي", lex="لَقًى_2", pos="verb", stemgloss=None),
            analysis(root="ل.ق.ي", lex="لَقًى_3", pos="verb", stemgloss="encounter"),
        ]
        with patch.object(camel, "camel_lookup", return_value=analyses):
            result = camel.camel_stem_gloss(
                "لقي", expected_root="لقي", expected_lemma="لَقِيَ", expected_pos="V"
            )
        self.assertEqual(result, "encounter")

    def test_no_analyses_at_all_returns_none(self):
        with patch.object(camel, "camel_lookup", return_value=None):
            result = camel.camel_stem_gloss("لقي", expected_root="لقي")
        self.assertIsNone(result)


if __name__ == "__main__":
    unittest.main()