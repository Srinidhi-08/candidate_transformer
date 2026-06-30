"""
src/phase2/module9_missing_values.py
=====================================
MODULE 9 – Missing Value Management

Rules:
  - Missing values become NULL (None in Python).
  - No automatic imputation.
  - When later data provides a value for a NULL field, it MAY be
    filled in by the Merge Engine (Module 12).
  - Never overwrite a valid (non-None) value with NULL.

This module audits the CandidateObject and records which fields
are absent, contributing to the confidence calculation.
"""

from __future__ import annotations

import logging
from dataclasses import fields as dataclass_fields

from src.core.models import CandidateObject, ProcessingContext

logger = logging.getLogger("phase2.module9_missing_values")

# Fields we explicitly track as "required for a good profile"
_IMPORTANT_SCALAR_FIELDS = [
    "full_name", "headline", "summary", "years_experience",
]
_IMPORTANT_LIST_FIELDS = [
    "emails", "phones", "skills", "experience", "education",
]


class MissingValueManager:

    def audit(self, ctx: ProcessingContext) -> dict[str, bool]:
        """
        Returns a dict of {field_name: is_missing} for all important fields.
        Also logs a summary to the context.
        """
        obj = ctx.candidate_object
        if obj is None:
            return {}

        missing: dict[str, bool] = {}

        for fname in _IMPORTANT_SCALAR_FIELDS:
            val = getattr(obj, fname, None)
            missing[fname] = val is None or (isinstance(val, str) and not val.strip())

        for fname in _IMPORTANT_LIST_FIELDS:
            val = getattr(obj, fname, [])
            missing[fname] = not val

        # Location
        missing["location"] = obj.location.is_empty()
        # Links
        missing["links.linkedin"] = obj.links.linkedin is None
        missing["links.github"] = obj.links.github is None

        missing_list = [k for k, v in missing.items() if v]
        present_list = [k for k, v in missing.items() if not v]

        ctx.log(
            "module9_missing_values",
            "INFO",
            f"Present: {present_list}  |  Missing/null: {missing_list}",
        )

        if missing_list:
            ctx.log(
                "module9_missing_values",
                "WARNING",
                f"{len(missing_list)} important field(s) are NULL: {missing_list}",
            )

        logger.info(
            "Missing value audit: %d present, %d missing",
            len(present_list), len(missing_list),
        )
        return missing
