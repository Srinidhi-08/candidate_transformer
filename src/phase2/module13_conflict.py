"""
src/phase2/module13_conflict.py
================================
MODULE 13 – Conflict Resolution

Strategies (config: conflict_resolution.strategy):
  source_priority   → win based on source reliability score in config
  confidence_score  → win based on extraction confidence
  latest_update     → win based on upload_time (most recent wins)
  manual_review     → flag for human review, keep existing value

Fields listed in config `manual_review_fields` always escalate
to manual review regardless of the strategy.
"""

from __future__ import annotations

import logging

from src.core.config_loader import get_config

logger = logging.getLogger("phase2.module13_conflict")


class ConflictResolver:

    def __init__(self) -> None:
        cfg = get_config()
        cr_cfg = cfg.conflict_resolution
        self._strategy: str = cr_cfg["strategy"]
        self._manual_fields: set[str] = set(cr_cfg.get("manual_review_fields", []) or [])
        src_cfg = cfg.sources
        self._reliability: dict[str, float] = src_cfg["reliability_scores"]

    def resolve(
        self,
        conflicts: dict[str, tuple],
        existing_source: str,
        incoming_source: str,
        existing_time=None,
        incoming_time=None,
        existing_confidence: float = 0.0,
        incoming_confidence: float = 0.0,
    ) -> dict[str, object]:
        """
        For each conflicting field, decide which value wins.

        Returns a dict of {field_name: resolved_value}.
        Fields flagged for manual review are returned as the EXISTING value
        and a warning is logged.
        """
        resolved: dict[str, object] = {}

        for field, (existing_val, incoming_val) in conflicts.items():
            if field in self._manual_fields:
                logger.warning(
                    "Field '%s' flagged for manual review — keeping existing value. "
                    "existing=%r  incoming=%r",
                    field, existing_val, incoming_val,
                )
                resolved[field] = existing_val
                continue

            winner = self._apply_strategy(
                field, existing_val, incoming_val,
                existing_source, incoming_source,
                existing_time, incoming_time,
                existing_confidence, incoming_confidence,
            )
            resolved[field] = winner
            logger.info(
                "Conflict resolved [%s] strategy=%s: kept %r (rejected %r)",
                field, self._strategy, winner,
                incoming_val if winner == existing_val else existing_val,
            )

        return resolved

    def _apply_strategy(
        self, field, existing_val, incoming_val,
        existing_source, incoming_source,
        existing_time, incoming_time,
        existing_confidence, incoming_confidence,
    ):
        if self._strategy == "source_priority":
            ex_score = self._reliability.get(existing_source, 0.0)
            in_score = self._reliability.get(incoming_source, 0.0)
            return existing_val if ex_score >= in_score else incoming_val

        elif self._strategy == "confidence_score":
            return existing_val if existing_confidence >= incoming_confidence else incoming_val

        elif self._strategy == "latest_update":
            if existing_time and incoming_time:
                return incoming_val if incoming_time > existing_time else existing_val
            return existing_val

        else:  # manual_review (default safe)
            logger.warning(
                "Unknown strategy '%s' — defaulting to keep existing value for field '%s'.",
                self._strategy, field,
            )
            return existing_val
