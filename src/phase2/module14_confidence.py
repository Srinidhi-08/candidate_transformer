"""
src/phase2/module14_confidence.py
====================================
MODULE 14 – Confidence Engine

Calculates an overall_confidence score for a CanonicalCandidateRecord
after all validation, normalization, matching, and merging is done.

Score is a weighted sum of six dimensions (all weights configurable):
  source_reliability      – reliability score of the primary source
  validation_success      – fraction of important fields that passed validation
  extraction_accuracy     – fraction of important fields that are non-null
  matching_strength       – 1.0 if matched by email/phone, 0.5 by name, 0.0 if new
  cross_source_agreement  – bonus when multiple sources agree on key fields
  freshness               – decays with age of the most recent source upload
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from src.core.config_loader import get_config
from src.core.models import CanonicalCandidateRecord, ValidationIssue

logger = logging.getLogger("phase2.module14_confidence")

_IMPORTANT_FIELDS = [
    "full_name", "emails", "phones", "headline", "summary",
    "years_experience", "skills", "experience", "education",
]


class ConfidenceEngine:

    def __init__(self) -> None:
        cfg = get_config()
        c_cfg = cfg.confidence
        self._weights: dict[str, float] = c_cfg["weights"]
        self._decay_days: int = c_cfg["freshness_decay_days"]
        src_cfg = cfg.sources
        self._reliability: dict[str, float] = src_cfg["reliability_scores"]

    def calculate(
        self,
        record: CanonicalCandidateRecord,
        validation_issues: list[ValidationIssue],
        match_method: str | None,
        source_type: str,
    ) -> tuple[float, dict[str, float]]:
        """
        Returns (overall_confidence, breakdown_dict).
        """
        breakdown: dict[str, float] = {}

        # 1. Source reliability
        breakdown["source_reliability"] = self._reliability.get(source_type, 0.3)

        # 2. Validation success
        issue_fields = {i.field for i in validation_issues if i.severity == "ERROR"}
        failed = len(issue_fields)
        total = len(_IMPORTANT_FIELDS)
        breakdown["validation_success"] = max(0.0, 1.0 - (failed / total))

        # 3. Extraction accuracy (non-null completeness)
        filled = 0
        for f in _IMPORTANT_FIELDS:
            val = getattr(record, f, None)
            if val is not None and val != [] and val != "" and val != {}:
                filled += 1
        breakdown["extraction_accuracy"] = filled / total

        # 4. Matching strength
        if match_method in ("email", "phone"):
            breakdown["matching_strength"] = 1.0
        elif match_method in ("linkedin", "github"):
            breakdown["matching_strength"] = 0.85
        elif match_method == "name_similarity":
            breakdown["matching_strength"] = 0.5
        else:
            breakdown["matching_strength"] = 0.0  # new candidate

        # 5. Cross-source agreement
        num_sources = len(record.source_history)
        if num_sources >= 2:
            breakdown["cross_source_agreement"] = min(1.0, 0.5 + 0.25 * (num_sources - 1))
        else:
            # Fix 9: Use 0.5 (neutral) instead of 0.3 for single-source candidates.
            # 0.3 incorrectly implied disagreement when there's simply nothing to compare.
            breakdown["cross_source_agreement"] = 0.5  # single source: neutral baseline

        # 6. Freshness
        breakdown["freshness"] = self._compute_freshness(record.source_history)

        # Weighted sum
        total_score = sum(
            breakdown[dim] * self._weights.get(dim, 0.0)
            for dim in breakdown
        )
        overall = round(min(1.0, max(0.0, total_score)), 4)

        logger.info(
            "Confidence calculated for %s: %.4f  breakdown=%s",
            record.candidate_id, overall, breakdown,
        )
        return overall, breakdown

    def _compute_freshness(self, source_history: list[dict]) -> float:
        if not source_history:
            return 0.5
        # Find the most recent upload_time across all sources
        latest: datetime | None = None
        for src in source_history:
            try:
                t = datetime.fromisoformat(src["upload_time"])
                if latest is None or t > latest:
                    latest = t
            except (KeyError, ValueError):
                pass

        if latest is None:
            return 0.5

        now = datetime.now(timezone.utc)
        if latest.tzinfo is None:
            latest = latest.replace(tzinfo=timezone.utc)
        age_days = (now - latest).days
        if age_days <= 0:
            return 1.0
        # Linear decay: full freshness at 0 days, 0 freshness at decay_days
        return max(0.0, 1.0 - (age_days / self._decay_days))
