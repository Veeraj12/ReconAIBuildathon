"""
Pattern signature generation.

WHY DETERMINISTIC BUCKETED SIGNATURES INSTEAD OF ML CLUSTERING FIRST
─────────────────────────────────────────────────────────────────────
Financial exceptions recur for structural reasons: a fixed bank
charge, a batch-posting delay, a rounding convention. Those reasons
show up as an exact or near-exact repetition of a small number of
features (absolute_amount_difference, date offset, reference/ledger
relationship) — not as a diffuse cloud in high-dimensional space.
A grouped signature (effectively a GROUP BY on tolerance-bucketed
features) finds these directly, in O(n), with a result that is
trivially explainable ("147 records share this exact signature") and
100% reproducible.

An unsupervised clustering algorithm (DBSCAN, hierarchical, k-means)
would need: a distance metric across mixed categorical/continuous
features, a scale-normalization decision, and a density/linkage
parameter — three extra design decisions, each adding room for
nondeterminism and each solving a problem this data doesn't yet
demonstrate (see ARCHITECTURE.md §"Deterministic grouping vs
clustering" for the fuller comparison and the specific conditions
under which clustering would earn its complexity later).

TOLERANCE STRATEGY
───────────────────
- exception_type, reference_match, amount_match, date_match,
  duplicate_flag, sign_reversal_flag: exact (they're already booleans
  or a fixed small vocabulary — no bucketing needed).
- absolute_amount_difference: bucketed to the nearest currency unit
  (default: nearest 1.0). Amounts are already rounded to 2 decimals
  upstream, so this only absorbs paisa-level noise, never masks a
  genuinely different fixed-charge amount.
- percentage_difference: bucketed to the nearest 0.5 percentage point
  by default — percentage-based differences (e.g. "~2% deducted")
  won't be bit-identical across different base amounts, so this needs
  real tolerance, unlike the fixed-amount case.
- date_difference_days: used exactly up to a small cap (values 0-5
  are common and meaningfully distinct — same-day vs 1-day vs 3-day
  posting lag are different stories); anything larger collapses to a
  single ">5" bucket, since large date gaps are rarely a "pattern" so
  much as isolated timing noise.

All thresholds are constructor parameters, not hardcoded — the
architecture explicitly calls for evaluating tolerances against real
data before commiting to defaults.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from .features import ExceptionFeature


@dataclass
class SignatureConfig:
    amount_diff_bucket_size: float = 1.0
    percentage_bucket_size: float = 0.5
    date_diff_exact_cap: int = 5


class PatternSignatureGenerator:
    def __init__(self, config: Optional[SignatureConfig] = None):
        self.config = config or SignatureConfig()

    def signature_for(self, ef: ExceptionFeature, mode: str = "amount") -> str:
        """
        `mode` selects which numeric-difference hypothesis this
        signature tests:

          - "amount":     bucket on absolute_amount_difference; ignore
                           percentage_difference. Tests the "fixed
                           charge/deduction" hypothesis (e.g. always
                           exactly ₹251 off, regardless of the base
                           amount).
          - "percentage": bucket on percentage_difference; ignore
                           absolute_amount_difference. Tests the
                           "proportional" hypothesis (e.g. always
                           ~2% off, which is a different absolute
                           amount for every base value and would
                           otherwise never share a bucket with itself
                           under the "amount" mode).

        These are deliberately mutually exclusive, not combined into
        one signature — a fixed ₹251 charge on varying base amounts
        has a constant absolute difference but a *drifting*
        percentage, and a proportional ~2% charge has the opposite
        profile. Requiring both to bucket together would silently
        fragment either real pattern into many tiny ones. See
        DeterministicPatternDiscoveryEngine.discover for how the two
        modes are tried in sequence (amount first, then percentage on
        whatever's left over).
        """
        f = ef.features
        parts = [ef.exception_type]

        if ef.exception_type == "GROUPED_MATCH":
            # Grouped matches are already a confirmed many-to-one
            # match by construction (the amounts sum exactly) — the
            # only meaningful grouping dimension left is direction.
            parts.append(f.get("group_direction") or "")
            return "|".join(parts)

        # bank_exists / tally_exists partition the space first: a
        # missing-in-tally record can never share a signature with an
        # amount-mismatch record even if some numeric field coincided.
        parts.append(f"BE{int(bool(f.get('bank_exists')))}")
        parts.append(f"TE{int(bool(f.get('tally_exists')))}")

        parts.append(self._bucket_bool(f.get("reference_match")))
        parts.append(self._bucket_bool(f.get("amount_match")))
        parts.append(self._bucket_bool(f.get("date_match")))
        parts.append(self._bucket_bool(f.get("duplicate_flag")))
        parts.append(self._bucket_bool(f.get("sign_reversal_flag")))

        if mode == "percentage":
            parts.append(self._bucket_percentage(f.get("percentage_difference")))
        else:
            parts.append(self._bucket_amount_diff(f.get("absolute_amount_difference")))

        parts.append(self._bucket_date_diff(f.get("date_difference_days")))

        return "|".join(parts)

    # ────────────────────────────────────────────────────────────

    @staticmethod
    def _bucket_bool(value) -> str:
        if value is None:
            return "N"
        return "T" if value else "F"

    def _bucket_amount_diff(self, value: Optional[float]) -> str:
        if value is None:
            return "AD:N"
        size = self.config.amount_diff_bucket_size
        bucket = round(value / size) * size if size else value
        return f"AD:{bucket:.2f}"

    def _bucket_percentage(self, value: Optional[float]) -> str:
        if value is None:
            return "PD:N"
        size = self.config.percentage_bucket_size
        bucket = round(value / size) * size if size else value
        return f"PD:{bucket:.2f}"

    def _bucket_date_diff(self, value: Optional[int]) -> str:
        if value is None:
            return "DD:N"
        if value > self.config.date_diff_exact_cap:
            return f"DD:>{self.config.date_diff_exact_cap}"
        return f"DD:{value}"
