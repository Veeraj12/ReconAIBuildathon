"""
Phase 3 — explanation caching.

DESIGN: cache on demand, keyed by a content hash of the PatternContext
(not just pattern_id) — chosen deliberately over persisting every
explanation to the database.

WHY CONTENT-HASH KEYING INSTEAD OF PATTERN_ID KEYING
───────────────────────────────────────────────────────
Because reconciliation and Phase 1/2 are entirely stateless (see
ARCHITECTURE.md §1/§19), a `pattern_id` like "PATTERN_001" is only
meaningful for one specific input — the same ID could represent a
completely different set of transactions on a different upload. Keying
the cache on a hash of the actual context content means:
  - identical patterns (same stats, same samples) always hit cache,
    even across different reconciliation runs
  - a pattern whose underlying data changed automatically gets a fresh
    key, and therefore a fresh explanation — no separate invalidation
    logic is needed at all ("regenerated when underlying pattern data
    changes" falls out of this for free)

WHY IN-MEMORY (Django cache framework) INSTEAD OF A DB TABLE, FOR MVP
───────────────────────────────────────────────────────────────────
Trade-off, explicit:
  + Zero new migrations/models — nothing to keep in sync with Phase 1/2
  + Zero risk to existing reconciliation data or schema
  - Not shared across processes/workers unless a shared cache backend
    (e.g. Redis) is configured in Django's CACHES setting
  - Lost on process restart / cache eviction — acceptable for an MVP
    where regenerating a cache miss just means one more (bounded-cost,
    see PatternExplanationService) local scoring/generation pass

If explanation history/audit trail becomes a real requirement (e.g. as
part of Phase 5's human-approval trail), a persisted
`PatternExplanationRecord` model is the natural next step — but that's
a deliberate, visible schema decision for later, not something to
sneak in under Phase 3.
"""

from __future__ import annotations

import hashlib
import json
from typing import Optional

from django.core.cache import cache as django_cache

DEFAULT_TTL_SECONDS = 60 * 60 * 24  # 1 day
CACHE_KEY_PREFIX = "reconai:pattern_explanation:"


def context_cache_key(context_dict: dict) -> str:
    canonical = json.dumps(context_dict, sort_keys=True, default=str)
    digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    return f"{CACHE_KEY_PREFIX}{context_dict.get('pattern_id', 'unknown')}:{digest[:24]}"


def get_cached_explanation(context_dict: dict) -> Optional[dict]:
    return django_cache.get(context_cache_key(context_dict))


def set_cached_explanation(context_dict: dict, explanation_dict: dict, ttl: int = DEFAULT_TTL_SECONDS) -> None:
    django_cache.set(context_cache_key(context_dict), explanation_dict, timeout=ttl)
