"""Resume-scoring tests: formula correctness, fuzzy/alias matching, and the
edge cases that must not crash (no required skills, no candidate skills)."""

from django.test import TestCase

from .constants import FUZZY_MATCH_THRESHOLD, TIER_POTENTIAL, TIER_STRONG, TIER_WEAK
from .services import compute_score, fit_tier_for, normalize_skill


class ScoreFormulaTests(TestCase):
    def test_all_required_no_nice_is_100(self):
        r = compute_score(["Python", "Django"], [], ["Python", "Django"])
        self.assertEqual(r["score"], 100.0)
        self.assertFalse(r["incomplete_job"])

    def test_half_required_is_50(self):
        r = compute_score(["Python", "Django"], [], ["Python"])
        self.assertEqual(r["score"], 50.0)
        self.assertEqual(r["missing"], ["Django"])

    def test_all_required_zero_nice_matched_is_80(self):
        # Required carries 80, nice-to-have carries the other 20.
        r = compute_score(["Python"], ["Docker"], ["Python"])
        self.assertEqual(r["score"], 80.0)

    def test_required_plus_nice_is_100(self):
        r = compute_score(["Python"], ["Docker"], ["Python", "Docker"])
        self.assertEqual(r["score"], 100.0)
        self.assertEqual([m["job_skill"] for m in r["bonus"]], ["Docker"])

    def test_no_required_skills_flags_incomplete(self):
        r = compute_score([], ["Docker"], ["Python"])
        self.assertEqual(r["score"], 0.0)
        self.assertTrue(r["incomplete_job"])

    def test_candidate_with_no_skills_scores_zero(self):
        r = compute_score(["Python"], ["Docker"], [])
        self.assertEqual(r["score"], 0.0)
        self.assertEqual(r["missing"], ["Python"])


class FuzzyMatchTests(TestCase):
    def test_alias_reactjs_matches_react(self):
        r = compute_score(["React"], [], ["ReactJS"])
        self.assertEqual(r["score"], 100.0)

    def test_alias_js_matches_javascript(self):
        r = compute_score(["JavaScript"], [], ["JS"])
        self.assertEqual(r["score"], 100.0)

    def test_typo_matches(self):
        r = compute_score(["PostgreSQL"], [], ["Postgres"])  # alias
        self.assertEqual(r["score"], 100.0)

    def test_java_does_not_match_javascript(self):
        # Critical false-positive guard: substrings must not match.
        r = compute_score(["Java"], [], ["JavaScript"])
        self.assertEqual(r["score"], 0.0)

    def test_c_does_not_match_cpp(self):
        r = compute_score(["C"], [], ["C++"])
        self.assertEqual(r["score"], 0.0)


class TierTests(TestCase):
    def test_tier_bands(self):
        self.assertEqual(fit_tier_for(75), TIER_STRONG)
        self.assertEqual(fit_tier_for(74.9), TIER_POTENTIAL)
        self.assertEqual(fit_tier_for(50), TIER_POTENTIAL)
        self.assertEqual(fit_tier_for(49.9), TIER_WEAK)

    def test_normalize_applies_alias_and_case(self):
        self.assertEqual(normalize_skill("  ReactJS "), "react")
        self.assertEqual(normalize_skill("C++"), "cpp")
