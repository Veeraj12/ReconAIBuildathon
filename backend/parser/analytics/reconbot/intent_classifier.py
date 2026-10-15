"""
Recon Bot — intent classification.

Deliberately rule-based (keyword/phrase matching), not ML or an LLM —
consistent with the rest of the analytics layer's local-and-
deterministic design (see explanation/scoring.py's module docstring
for the same reasoning applied to cause confidence). The same
question always produces the same intent, which makes the bot's
behavior testable and auditable rather than probabilistic — a
property that matters for a finance tool.

This module only decides WHAT the user is asking and extracts any
reference (an exception type, a pattern ordinal like "pattern 2") from
the wording. It never looks at any reconciliation data — resolving a
reference against the actual current patterns/exceptions is
service.py's job, kept deliberately separate so this classifier stays
trivially unit-testable with just strings in, structured intent out.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Optional


class BotIntent:
    GREETING = "GREETING"
    HELP = "HELP"
    SUMMARY_OVERVIEW = "SUMMARY_OVERVIEW"
    EXCEPTION_TYPE_COUNT = "EXCEPTION_TYPE_COUNT"
    PATTERNS_OVERVIEW = "PATTERNS_OVERVIEW"
    PATTERN_EXPLAIN = "PATTERN_EXPLAIN"
    SHOW_TRANSACTIONS = "SHOW_TRANSACTIONS"
    HIGHEST_IMPACT = "HIGHEST_IMPACT"
    MOST_FREQUENT = "MOST_FREQUENT"
    OUTLIERS = "OUTLIERS"
    UNKNOWN = "UNKNOWN"


# Maps a Phase 1 ExceptionType constant to the phrases a user might
# type. Order within each list doesn't matter; order of the dict does
# NOT determine priority (see _find_exception_type, which picks the
# longest/most specific match to avoid "duplicate" alone shadowing
# "duplicate bank").
EXCEPTION_TYPE_ALIASES: dict = {
    "AMOUNT_MISMATCH": ["amount mismatch", "amount mismatches", "amount differ", "amount difference", "amount discrepan"],
    "TYPE_MISMATCH": ["type mismatch", "type mismatches", "transaction type mismatch"],
    "MISSING_IN_TALLY": ["missing in tally", "missing from tally", "not in tally", "not recorded in tally"],
    "MISSING_IN_BANK": ["missing in bank", "missing from bank", "not in bank", "not recorded in bank"],
    "DUPLICATE_BANK": ["duplicate bank", "bank duplicate", "duplicate entries in bank", "duplicates in the bank"],
    "DUPLICATE_TALLY": ["duplicate tally", "tally duplicate", "duplicate entries in tally", "duplicates in the tally"],
    "SIGN_REVERSAL_HINT": ["sign reversal", "sign reversals", "debit credit mismatch", "reversed sign", "opposite sign"],
    "GROUPED_MATCH": ["grouped match", "grouped matches", "split transaction", "consolidated transaction", "many to one", "one to many"],
}

# A generic mention of "duplicate(s)" without "bank"/"tally" refers to
# both sides combined — handled specially in service.py, flagged here
# as its own pseudo-type so the classifier still recognizes the word.
GENERIC_DUPLICATE_PHRASES = ["duplicate", "duplicates"]

PATTERN_TYPE_ALIASES: dict = {
    "RECURRING_AMOUNT_DIFFERENCE": ["fixed amount difference", "recurring amount difference", "fixed difference"],
    "RECURRING_PERCENTAGE_DIFFERENCE": ["percentage difference", "percent difference", "proportional difference"],
    "DATE_OFFSET": ["date offset", "date offsets", "posting delay", "day offset"],
    "MISSING_SOURCE": ["missing source", "missing transactions"],
    "DUPLICATE": ["duplicate pattern"],
    "SIGN_REVERSAL": ["sign reversal pattern"],
    "REFERENCE_MATCH_ONLY": ["reference pattern", "reference only"],
    "GROUPED_MATCH": ["grouped match pattern", "split or consolidated"],
}

_ORDINAL_WORDS = {
    "first": 1, "second": 2, "third": 3, "fourth": 4, "fifth": 5,
    "sixth": 6, "seventh": 7, "eighth": 8, "ninth": 9, "tenth": 10,
}

_GREETING_PATTERN = re.compile(r"^\s*(hi|hello|hey|good morning|good afternoon|good evening)\b[!.\s]*$", re.I)
_SHOW_TRIGGER = re.compile(r"\b(show|list|display|open|pull up)\b.{0,30}\b(those|them|it|transactions|records|entries)\b", re.I)
_WHY_TRIGGER = re.compile(r"\b(why|explain|cause|reason|what.?s causing|root cause)\b", re.I)
_IMPACT_TRIGGER = re.compile(r"\b(highest|biggest|largest|most)\s+(financial\s+)?impact|costliest|most expensive\b", re.I)
_FREQUENT_TRIGGER = re.compile(r"\bmost (frequent|common)|highest frequency\b", re.I)
_OUTLIER_TRIGGER = re.compile(r"\boutlier|unclassified|didn.?t fit|leftover\b", re.I)
_PATTERNS_TRIGGER = re.compile(r"\bpatterns?\b", re.I)
_COUNT_TRIGGER = re.compile(r"\bhow many|how much|count|number of|total\b", re.I)
_SUMMARY_TRIGGER = re.compile(r"\b(summary|overview|match rate|how (are|is) (we|it) doing|health|status)\b", re.I)
_HELP_TRIGGER = re.compile(r"\b(help|what can you do|capabilities|commands)\b", re.I)
_PATTERN_ORDINAL_PATTERN = re.compile(r"pattern[_\s#]*0*(\d+)", re.I)


@dataclass
class ClassifiedIntent:
    intent: str
    raw_message: str
    exception_type: Optional[str] = None  # a real ExceptionType constant, or "DUPLICATE" (generic, both sides)
    pattern_type: Optional[str] = None  # a real Pattern pattern_type constant
    pattern_ordinal: Optional[int] = None  # 1-indexed position the user referred to ("pattern 2", "the first pattern")


class IntentClassifier:
    def classify(self, message: str) -> ClassifiedIntent:
        text = (message or "").strip()
        lowered = text.lower()

        if not lowered:
            return ClassifiedIntent(intent=BotIntent.UNKNOWN, raw_message=text)

        if _GREETING_PATTERN.match(lowered):
            return ClassifiedIntent(intent=BotIntent.GREETING, raw_message=text)

        exception_type = self._find_exception_type(lowered)
        pattern_type = self._find_pattern_type(lowered)
        ordinal = self._find_pattern_ordinal(lowered)

        # Order matters: more specific intents are checked first so a
        # message like "why are there so many amount mismatches, show
        # me those" is classified by its dominant verb ("why") rather
        # than accidentally matching a later, weaker rule.
        if _SHOW_TRIGGER.search(lowered):
            return ClassifiedIntent(
                intent=BotIntent.SHOW_TRANSACTIONS, raw_message=text,
                exception_type=exception_type, pattern_ordinal=ordinal,
            )

        if _WHY_TRIGGER.search(lowered):
            return ClassifiedIntent(
                intent=BotIntent.PATTERN_EXPLAIN, raw_message=text,
                exception_type=exception_type, pattern_type=pattern_type, pattern_ordinal=ordinal,
            )

        if _IMPACT_TRIGGER.search(lowered):
            return ClassifiedIntent(intent=BotIntent.HIGHEST_IMPACT, raw_message=text)

        if _FREQUENT_TRIGGER.search(lowered):
            return ClassifiedIntent(intent=BotIntent.MOST_FREQUENT, raw_message=text)

        if _OUTLIER_TRIGGER.search(lowered):
            return ClassifiedIntent(intent=BotIntent.OUTLIERS, raw_message=text)

        if _PATTERNS_TRIGGER.search(lowered) and not exception_type:
            return ClassifiedIntent(
                intent=BotIntent.PATTERNS_OVERVIEW, raw_message=text, pattern_type=pattern_type,
            )

        if exception_type:
            return ClassifiedIntent(intent=BotIntent.EXCEPTION_TYPE_COUNT, raw_message=text, exception_type=exception_type)

        if _SUMMARY_TRIGGER.search(lowered) or _COUNT_TRIGGER.search(lowered):
            return ClassifiedIntent(intent=BotIntent.SUMMARY_OVERVIEW, raw_message=text)

        if _HELP_TRIGGER.search(lowered):
            return ClassifiedIntent(intent=BotIntent.HELP, raw_message=text)

        return ClassifiedIntent(intent=BotIntent.UNKNOWN, raw_message=text)

    @staticmethod
    def _find_exception_type(lowered: str) -> Optional[str]:
        best_type, best_len = None, 0
        for exc_type, phrases in EXCEPTION_TYPE_ALIASES.items():
            for phrase in phrases:
                if phrase in lowered and len(phrase) > best_len:
                    best_type, best_len = exc_type, len(phrase)
        if best_type:
            return best_type
        for phrase in GENERIC_DUPLICATE_PHRASES:
            if phrase in lowered:
                return "DUPLICATE"
        return None

    @staticmethod
    def _find_pattern_type(lowered: str) -> Optional[str]:
        best_type, best_len = None, 0
        for pat_type, phrases in PATTERN_TYPE_ALIASES.items():
            for phrase in phrases:
                if phrase in lowered and len(phrase) > best_len:
                    best_type, best_len = pat_type, len(phrase)
        return best_type

    @staticmethod
    def _find_pattern_ordinal(lowered: str) -> Optional[int]:
        match = _PATTERN_ORDINAL_PATTERN.search(lowered)
        if match:
            return int(match.group(1))
        for word, value in _ORDINAL_WORDS.items():
            if re.search(rf"\b{word}\b.{{0,10}}\bpattern\b", lowered):
                return value
        return None
