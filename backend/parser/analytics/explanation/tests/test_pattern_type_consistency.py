from django.test import SimpleTestCase

from ...patterns import PatternType
from ..knowledge.loader import KNOWN_PATTERN_TYPES


class PatternTypeConsistencyTests(SimpleTestCase):
    """The one test guarding the Phase 2 <-> Phase 3 'loose connection':
    if patterns.py's PatternType vocabulary is ever extended (e.g. a
    future clustering-based discovery engine — see patterns.py's own
    module docstring on this) without a matching update to
    knowledge/loader.py's KNOWN_PATTERN_TYPES, this test fails
    immediately and mechanically, rather than the drift only
    surfacing at runtime as a silent 'unrecognized_pattern_type' log
    line (see explanation/service.py) that nobody happened to notice.

    This replaces a fragile, regex-based static-source-scan of
    `_classify()` that this codebase used previously (see
    ARCHITECTURE_PHASE3_LOCAL.md §19) with a structural comparison of
    two real, importable vocabularies — robust to _classify() being
    refactored, rewritten, or replaced entirely by a different
    PatternDiscoveryEngine implementation, as long as whatever new
    engine is written declares its own output vocabulary in
    PatternType (or an equivalent constant) rather than hand-typing
    strings.
    """

    def test_every_phase2_pattern_type_is_known_to_the_knowledge_package(self):
        unknown = set(PatternType.ALL) - KNOWN_PATTERN_TYPES
        self.assertEqual(
            unknown, set(),
            f"patterns.PatternType declares {sorted(unknown)} which "
            f"knowledge/loader.py's KNOWN_PATTERN_TYPES does not recognize. "
            f"Update KNOWN_PATTERN_TYPES (and ideally add a catalogue.py "
            f"entry) to cover it.",
        )

    def test_pattern_type_all_has_no_duplicates(self):
        self.assertEqual(len(PatternType.ALL), len(set(PatternType.ALL)))

    def test_known_pattern_types_not_missing_anything_pattern_type_declares(self):
        # The inverse direction: KNOWN_PATTERN_TYPES is allowed to be a
        # strict superset (e.g. it may retain an old type Phase 2 no
        # longer produces without breaking anything), but never a
        # subset that's missing something PatternType currently
        # declares — that's exactly the drift this test exists to
        # catch.
        self.assertTrue(set(PatternType.ALL).issubset(KNOWN_PATTERN_TYPES))
