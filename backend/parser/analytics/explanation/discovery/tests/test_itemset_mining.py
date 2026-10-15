from django.test import SimpleTestCase

from ..itemset_mining import deduplicate_equivalent_rules, frequent_itemsets, generate_rules, remove_redundant_rules

_BASKET_TRANSACTIONS = [
    frozenset(["bread", "milk"]),
    frozenset(["bread", "diaper", "beer", "eggs"]),
    frozenset(["milk", "diaper", "beer", "cola"]),
    frozenset(["bread", "milk", "diaper", "beer"]),
    frozenset(["bread", "milk", "diaper", "cola"]),
]


class FrequentItemsetsTests(SimpleTestCase):
    """Verified against the standard textbook bread/milk/diaper/beer/
    cola market-basket example, whose correct answer is independently
    known — not just internally self-consistent."""

    def test_empty_transactions_returns_empty(self):
        self.assertEqual(frequent_itemsets([], min_support=0.5), {})

    def test_single_item_supports_are_correct(self):
        itemsets = frequent_itemsets(_BASKET_TRANSACTIONS, min_support=0.6)
        self.assertAlmostEqual(itemsets[frozenset(["bread"])], 0.8)
        self.assertAlmostEqual(itemsets[frozenset(["milk"])], 0.8)
        self.assertAlmostEqual(itemsets[frozenset(["diaper"])], 0.8)
        self.assertAlmostEqual(itemsets[frozenset(["beer"])], 0.6)

    def test_pair_supports_are_correct(self):
        itemsets = frequent_itemsets(_BASKET_TRANSACTIONS, min_support=0.6)
        self.assertAlmostEqual(itemsets[frozenset(["bread", "milk"])], 0.6)
        self.assertAlmostEqual(itemsets[frozenset(["bread", "diaper"])], 0.6)
        self.assertAlmostEqual(itemsets[frozenset(["milk", "diaper"])], 0.6)
        self.assertAlmostEqual(itemsets[frozenset(["beer", "diaper"])], 0.6)

    def test_apriori_property_prunes_infrequent_superset(self):
        # {bread, milk, diaper} has support 2/5=0.4, below the 0.6
        # threshold — must NOT appear, proving the apriori
        # downward-closure pruning actually works, not just that it
        # returns *something*.
        itemsets = frequent_itemsets(_BASKET_TRANSACTIONS, min_support=0.6)
        self.assertNotIn(frozenset(["bread", "milk", "diaper"]), itemsets)

    def test_higher_min_support_returns_fewer_itemsets(self):
        loose = frequent_itemsets(_BASKET_TRANSACTIONS, min_support=0.4)
        strict = frequent_itemsets(_BASKET_TRANSACTIONS, min_support=0.8)
        self.assertGreaterEqual(len(loose), len(strict))


class GenerateRulesTests(SimpleTestCase):
    def test_known_rule_confidence_and_lift_are_exact(self):
        itemsets = frequent_itemsets(_BASKET_TRANSACTIONS, min_support=0.6)
        rules = generate_rules(itemsets, min_confidence=0.5, min_lift=1.0)

        diaper_to_beer = next(r for r in rules if r.antecedent == frozenset(["diaper"]) and r.consequent == frozenset(["beer"]))
        self.assertAlmostEqual(diaper_to_beer.confidence, 0.75)
        self.assertAlmostEqual(diaper_to_beer.lift, 1.25)

    def test_min_confidence_filters_out_weak_rules(self):
        itemsets = frequent_itemsets(_BASKET_TRANSACTIONS, min_support=0.6)
        # {beer}->{diaper} genuinely has confidence 1.0 in this dataset,
        # so the threshold must exceed 1.0 to filter out every rule.
        strict_rules = generate_rules(itemsets, min_confidence=1.01, min_lift=0.0)
        self.assertEqual(strict_rules, [])

    def test_consequent_filter_restricts_output(self):
        itemsets = frequent_itemsets(_BASKET_TRANSACTIONS, min_support=0.6)
        rules = generate_rules(itemsets, min_confidence=0.5, min_lift=1.0, consequent_filter=frozenset(["beer"]))
        self.assertTrue(all(r.consequent == frozenset(["beer"]) for r in rules))

    def test_sorted_by_lift_confidence_support_descending(self):
        itemsets = frequent_itemsets(_BASKET_TRANSACTIONS, min_support=0.6)
        rules = generate_rules(itemsets, min_confidence=0.0, min_lift=0.0)
        lifts = [r.lift for r in rules]
        self.assertEqual(lifts, sorted(lifts, reverse=True))

    def test_deterministic_across_repeated_calls(self):
        itemsets = frequent_itemsets(_BASKET_TRANSACTIONS, min_support=0.6)
        rules_a = generate_rules(itemsets, min_confidence=0.5, min_lift=1.0)
        rules_b = generate_rules(itemsets, min_confidence=0.5, min_lift=1.0)
        self.assertEqual(rules_a, rules_b)


class RemoveRedundantRulesTests(SimpleTestCase):
    def test_superset_with_equal_confidence_is_dropped(self):
        itemsets = {
            frozenset(["A"]): 0.5,
            frozenset(["B"]): 0.5,
            frozenset(["C"]): 1.0,
            frozenset(["A", "C"]): 0.5,
            frozenset(["A", "B", "C"]): 0.5,  # same support as {A,C}->confidence 1.0 too -> redundant given {A}
        }
        rules = generate_rules(itemsets, min_confidence=0.5, min_lift=0.0, consequent_filter=frozenset(["C"]))
        pruned = remove_redundant_rules(rules)
        antecedents = {r.antecedent for r in pruned}
        self.assertIn(frozenset(["A"]), antecedents)
        self.assertNotIn(frozenset(["A", "B"]), antecedents)

    def test_does_not_drop_a_genuinely_stronger_specific_rule(self):
        # {A} -> C has confidence 0.5; {A,B} -> C has confidence 1.0 (strictly better)
        # -- the more specific rule must survive since it's NOT dominated.
        itemsets = {
            frozenset(["A"]): 0.6,
            frozenset(["B"]): 0.5,
            frozenset(["C"]): 0.5,
            frozenset(["A", "C"]): 0.3,       # confidence = 0.3/0.6 = 0.5
            frozenset(["A", "B"]): 0.3,
            frozenset(["A", "B", "C"]): 0.3,  # confidence = 0.3/0.3 = 1.0
        }
        rules = generate_rules(itemsets, min_confidence=0.4, min_lift=0.0, consequent_filter=frozenset(["C"]))
        pruned = remove_redundant_rules(rules)
        antecedents = {r.antecedent for r in pruned}
        self.assertIn(frozenset(["A", "B"]), antecedents)


class DeduplicateEquivalentRulesTests(SimpleTestCase):
    def test_statistically_identical_rules_collapse_to_one(self):
        itemsets = {
            frozenset(["X"]): 0.5, frozenset(["Y"]): 0.5, frozenset(["Z"]): 0.5,
            frozenset(["X", "Z"]): 0.5, frozenset(["Y", "Z"]): 0.5,
        }
        rules = generate_rules(itemsets, min_confidence=0.5, min_lift=0.0, consequent_filter=frozenset(["Z"]))
        # Both {X}->Z and {Y}->Z have support=0.5, confidence=1.0, lift=2.0 here.
        deduped = deduplicate_equivalent_rules(rules)
        self.assertEqual(len(deduped), 1)

    def test_genuinely_different_statistics_are_both_kept(self):
        itemsets = {
            frozenset(["X"]): 0.8, frozenset(["Y"]): 0.4, frozenset(["Z"]): 0.5,
            frozenset(["X", "Z"]): 0.4,  # confidence = 0.5
            frozenset(["Y", "Z"]): 0.4,  # confidence = 1.0
        }
        rules = generate_rules(itemsets, min_confidence=0.4, min_lift=0.0, consequent_filter=frozenset(["Z"]))
        deduped = deduplicate_equivalent_rules(rules)
        self.assertEqual(len(deduped), 2)

    def test_deterministic_which_representative_survives(self):
        itemsets = {
            frozenset(["X"]): 0.5, frozenset(["Y"]): 0.5, frozenset(["Z"]): 0.5,
            frozenset(["X", "Z"]): 0.5, frozenset(["Y", "Z"]): 0.5,
        }
        rules = generate_rules(itemsets, min_confidence=0.5, min_lift=0.0, consequent_filter=frozenset(["Z"]))
        first_run = deduplicate_equivalent_rules(rules)
        second_run = deduplicate_equivalent_rules(rules)
        self.assertEqual(first_run, second_run)
