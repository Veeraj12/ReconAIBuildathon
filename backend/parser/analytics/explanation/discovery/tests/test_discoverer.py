import random

from django.test import SimpleTestCase

from ...knowledge.loader import KnowledgeCatalogue, default_catalogue
from ...knowledge.schema import CauseKnowledge, FeatureCondition
from ..discoverer import CauseCandidateDiscoverer
from ..schema import ResolvedCase


def _cases_for_bank_charge(n=200, seed=11, noise=0.05):
    rng = random.Random(seed)
    cases = []
    for _ in range(n):
        ref_rate = rng.choice([0.95, 0.98, 0.4, 0.6])
        impact = rng.choice([50, 80, 300, 500, 900])
        truth = impact <= 100 and ref_rate >= 0.9
        confirmed = truth if rng.random() > noise else (not truth)
        cases.append(ResolvedCase(
            cause_id="BANK_SERVICE_CHARGE", pattern_type="RECURRING_AMOUNT_DIFFERENCE",
            exception_type="AMOUNT_MISMATCH", confirmed=confirmed, recorded_at="2026-01-01T00:00:00Z",
            raw_stats={"reference_match_rate": ref_rate, "common_date_offset_days": 0,
                       "average_absolute_amount_difference": impact},
        ))
    return cases


class SampleCountThresholdTests(SimpleTestCase):
    def test_below_min_sample_count_produces_nothing(self):
        cases = _cases_for_bank_charge(n=5)
        discoverer = CauseCandidateDiscoverer(min_sample_count=10)
        self.assertEqual(discoverer.discover(cases), [])

    def test_at_or_above_min_sample_count_can_produce_drafts(self):
        cases = _cases_for_bank_charge(n=200)
        discoverer = CauseCandidateDiscoverer(min_support=0.1, min_confidence=0.7, min_lift=1.1, min_sample_count=10)
        self.assertGreater(len(discoverer.discover(cases)), 0)


class GroupingByCauseTests(SimpleTestCase):
    def test_different_cause_ids_mined_independently(self):
        bank_cases = _cases_for_bank_charge(n=100)
        other_cases = [
            ResolvedCase(cause_id="OTHER_CAUSE", pattern_type="DATE_OFFSET", exception_type="AMOUNT_MISMATCH",
                         confirmed=(i % 5 != 0), recorded_at="t",
                         raw_stats={"common_date_offset_days": 0 if i % 5 != 0 else 3})
            for i in range(50)
        ]
        discoverer = CauseCandidateDiscoverer(min_support=0.1, min_confidence=0.6, min_lift=1.0, min_sample_count=10)
        drafts = discoverer.discover(bank_cases + other_cases)
        cause_ids_found = {d.suggested_cause_id for d in drafts}
        # Should be possible to see suggestions from either/both without one
        # cause's data ever leaking into the other's mining pool.
        self.assertTrue(cause_ids_found.issubset({"BANK_SERVICE_CHARGE", "OTHER_CAUSE"}))


class ExistingCatalogueCrossReferenceTests(SimpleTestCase):
    def test_rule_matching_already_declared_conditions_is_suppressed(self):
        # BANK_SERVICE_CHARGE already declares reference_match_rate>=0.90
        # and common_date_offset_days==0 in the real catalogue — a
        # discovered rule using ONLY those exact conditions should be
        # filtered out as "already known", not surfaced as a finding.
        catalogue = default_catalogue()
        entry = catalogue.get("BANK_SERVICE_CHARGE")
        cases = [
            ResolvedCase(cause_id="BANK_SERVICE_CHARGE", pattern_type="RECURRING_AMOUNT_DIFFERENCE",
                         exception_type="AMOUNT_MISMATCH", confirmed=True, recorded_at="t",
                         raw_stats={"reference_match_rate": 0.95, "common_date_offset_days": 0})
            for _ in range(30)
        ]
        discoverer = CauseCandidateDiscoverer(min_support=0.1, min_confidence=0.5, min_lift=0.5, min_sample_count=5,
                                               catalogue=catalogue)
        drafts = discoverer.discover(cases)
        for draft in drafts:
            proposed = {(c["feature"], c["operator"], c["value"]) for c in draft.suggested_supporting_conditions}
            declared = {(c.feature, c.operator, c.value) for c in entry.supporting_conditions}
            self.assertFalse(proposed.issubset(declared))

    def test_cause_id_with_no_catalogue_entry_is_flagged_as_new(self):
        cases = [
            ResolvedCase(cause_id="ACCOUNTANT_FREE_TYPED", pattern_type="DATE_OFFSET",
                         exception_type="AMOUNT_MISMATCH", confirmed=(i % 6 != 0), recorded_at="t",
                         raw_stats={"common_date_offset_days": 0 if i % 6 != 0 else 4})
            for i in range(40)
        ]
        discoverer = CauseCandidateDiscoverer(min_support=0.1, min_confidence=0.6, min_lift=1.0, min_sample_count=5)
        drafts = discoverer.discover(cases)
        self.assertTrue(len(drafts) > 0)
        for draft in drafts:
            self.assertFalse(draft.is_new_condition_for_existing_cause)

    def test_cause_id_with_catalogue_entry_but_novel_conditions_is_flagged_as_refinement(self):
        cases = _cases_for_bank_charge(n=200)  # uses average_absolute_amount_difference, not yet declared
        discoverer = CauseCandidateDiscoverer(min_support=0.1, min_confidence=0.7, min_lift=1.1, min_sample_count=10)
        drafts = discoverer.discover(cases)
        self.assertTrue(len(drafts) > 0)
        for draft in drafts:
            self.assertTrue(draft.is_new_condition_for_existing_cause)


class OutputIsSmallAndReviewableTests(SimpleTestCase):
    def test_redundant_and_duplicate_rules_do_not_flood_the_output(self):
        # Regression test for a real bug found during development: before
        # remove_redundant_rules/deduplicate_equivalent_rules were wired
        # in, this exact scenario produced 208 near-duplicate drafts.
        cases = _cases_for_bank_charge(n=200)
        discoverer = CauseCandidateDiscoverer(min_support=0.1, min_confidence=0.7, min_lift=1.1, min_sample_count=10)
        drafts = discoverer.discover(cases)
        self.assertLess(len(drafts), 20)

    def test_top_draft_has_highest_lift(self):
        cases = _cases_for_bank_charge(n=200)
        discoverer = CauseCandidateDiscoverer(min_support=0.1, min_confidence=0.7, min_lift=1.1, min_sample_count=10)
        drafts = discoverer.discover(cases)
        lifts = [d.lift for d in drafts]
        self.assertEqual(lifts, sorted(lifts, reverse=True))


class DeterminismTests(SimpleTestCase):
    def test_same_input_produces_identical_output(self):
        cases = _cases_for_bank_charge(n=150, seed=42)
        discoverer = CauseCandidateDiscoverer(min_support=0.1, min_confidence=0.7, min_lift=1.1, min_sample_count=10)
        first = discoverer.discover(cases)
        second = discoverer.discover(cases)
        self.assertEqual([d.to_dict() for d in first], [d.to_dict() for d in second])
