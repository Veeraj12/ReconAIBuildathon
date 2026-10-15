"""
PatternAnalysisService — the single entry point the view (and, later,
any other caller — a management command, a Celery task, Recon Bot)
uses to go from raw reconciliation exceptions to ranked patterns.

    reconciliation exceptions (dict)
            |  ExceptionFeatureExtractor.extract_all
    ExceptionFeature[]
            |  DeterministicPatternDiscoveryEngine.discover
    (Pattern[], outlier_exception_ids)
            |  PatternScorer.score  (per pattern)
            |  PatternRanker.rank   (all patterns)
    PatternAnalysisResult

This module has no Django imports — it's plain Python so Phase 1/2
stay testable and reusable outside the request/response cycle
(management command, Celery task, notebook) without spinning up
Django.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from .features import ExceptionFeatureExtractor, ExceptionFeature
from .patterns import (
    DeterministicPatternDiscoveryEngine,
    PatternScorer,
    PatternRanker,
    Pattern,
)
from .signatures import PatternSignatureGenerator, SignatureConfig


@dataclass
class PatternAnalysisResult:
    total_exceptions: int
    patterns_found: int
    patterns: list = field(default_factory=list)
    outliers: int = 0
    outlier_exception_ids: list = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "totalExceptions": self.total_exceptions,
            "patternsFound": self.patterns_found,
            "patterns": [p.to_dict() for p in self.patterns],
            "outliers": self.outliers,
            "outlierExceptionIds": self.outlier_exception_ids,
        }


class PatternAnalysisService:
    def __init__(
        self,
        feature_extractor: Optional[ExceptionFeatureExtractor] = None,
        discovery_engine=None,
        scorer: Optional[PatternScorer] = None,
        ranker: Optional[PatternRanker] = None,
        signature_config: Optional[SignatureConfig] = None,
        min_pattern_size: int = 3,
        min_pattern_fraction: float = 0.0,
        frequency_weight: float = 0.5,
        impact_weight: float = 0.5,
    ):
        self.feature_extractor = feature_extractor or ExceptionFeatureExtractor()
        self.discovery_engine = discovery_engine or DeterministicPatternDiscoveryEngine(
            signature_generator=PatternSignatureGenerator(signature_config),
            min_pattern_size=min_pattern_size,
            min_pattern_fraction=min_pattern_fraction,
        )
        self.scorer = scorer or PatternScorer()
        self.ranker = ranker or PatternRanker(frequency_weight, impact_weight)

    def analyze(self, reconciliation_exceptions: dict) -> PatternAnalysisResult:
        features: list[ExceptionFeature] = self.feature_extractor.extract_all(
            reconciliation_exceptions
        )
        return self.analyze_features(features)

    def analyze_features(self, features: list[ExceptionFeature]) -> PatternAnalysisResult:
        total = len(features)
        patterns, outlier_ids = self.discovery_engine.discover(features)

        by_id = {ef.exception_id: ef for ef in features}
        for pattern in patterns:
            members = [by_id[eid] for eid in pattern.exception_ids if eid in by_id]
            pattern.confidence = self.scorer.score(pattern, members)

        ranked = self.ranker.rank(patterns, total)

        return PatternAnalysisResult(
            total_exceptions=total,
            patterns_found=len(ranked),
            patterns=ranked,
            outliers=len(outlier_ids),
            outlier_exception_ids=outlier_ids,
        )
