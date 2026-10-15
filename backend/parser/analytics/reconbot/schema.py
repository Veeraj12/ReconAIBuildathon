"""
Recon Bot — output schema.

ARCHITECTURAL PRINCIPLE (carried over from the Phase 2/3 briefs this
was built against): the bot is an INTERFACE, not a new analysis
engine. It never independently inspects raw transactions or invents
statistics — every number in a BotResponse traces back to something
Phase 1 (ExceptionFeatureExtractor), Phase 2 (PatternAnalysisService),
or Phase 3 (PatternExplanationService) already computed. See
service.py for how that boundary is enforced in code, not just in
this docstring.

STATELESSNESS
───────────────
Same reasoning as the rest of the analytics layer (see
ARCHITECTURE.md §1/§15/§19): nothing is persisted server-side. A
"conversation" is therefore just the client resending a small
`BotContext` alongside each message — at most a previously-referenced
pattern_id and exception_type, never full message history, never raw
transaction data. This is what makes a follow-up like "show me those"
resolvable without the server remembering anything.
"""

from __future__ import annotations

from dataclasses import dataclass, field, asdict
from typing import Optional


@dataclass
class BotContext:
    """The only "memory" Recon Bot has — round-tripped by the client
    on every request. Deliberately tiny: enough to resolve a pronoun
    ("show me those", "why does that happen") against the last thing
    the bot talked about, nothing more."""

    last_pattern_id: Optional[str] = None
    last_exception_type: Optional[str] = None

    def to_dict(self) -> dict:
        return {"lastPatternId": self.last_pattern_id, "lastExceptionType": self.last_exception_type}

    @classmethod
    def from_dict(cls, d: Optional[dict]) -> "BotContext":
        d = d or {}
        return cls(
            last_pattern_id=d.get("lastPatternId"),
            last_exception_type=d.get("lastExceptionType"),
        )


@dataclass
class BotResponse:
    answer: str
    intent: str

    # Every ID here is one Phase 2 already produced (a real pattern_id)
    # or one Phase 1 already produced (a real exception_id) — never
    # fabricated by the bot. Capped lengths (see service.py) keep the
    # response bounded regardless of dataset size, same principle as
    # PatternContext's bounded representative_samples in Phase 3.
    referenced_pattern_ids: list = field(default_factory=list)
    referenced_exception_ids: list = field(default_factory=list)

    suggested_follow_ups: list = field(default_factory=list)
    context: BotContext = field(default_factory=BotContext)

    def to_dict(self) -> dict:
        return {
            "answer": self.answer,
            "intent": self.intent,
            "referencedPatternIds": self.referenced_pattern_ids,
            "referencedExceptionIds": self.referenced_exception_ids,
            "suggestedFollowUps": self.suggested_follow_ups,
            "context": self.context.to_dict(),
        }
