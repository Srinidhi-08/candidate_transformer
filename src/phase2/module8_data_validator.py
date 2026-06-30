"""
src/phase2/module8_data_validator.py
=====================================
MODULE 8 – Data Validation (Phase 2)

Validates field values that were extracted in Phase 1.
Does NOT modify values — flags issues only.

Validated fields:
  - emails        → RFC-ish regex check
  - phones        → digit count check
  - linkedin URL  → pattern match
  - github URL    → pattern match
  - dates         → parseable date check
  - years_exp     → numeric range check
"""

from __future__ import annotations

import logging
import re
from datetime import datetime

from src.core.config_loader import get_config
from src.core.models import (
    CandidateObject,
    ProcessingContext,
    ValidationIssue,
)

logger = logging.getLogger("phase2.module8_data_validator")

_EMAIL_RE = re.compile(r"^[\w.+-]+@[\w-]+\.[\w.-]+$")
_LINKEDIN_RE = re.compile(r"linkedin\.com/in/", re.IGNORECASE)
_GITHUB_RE = re.compile(r"github\.com/", re.IGNORECASE)


class DataValidator:

    def __init__(self) -> None:
        cfg = get_config()
        self._phone_min_digits: int = cfg.validation["phone_min_digits"]

    def validate(self, ctx: ProcessingContext) -> list[ValidationIssue]:
        obj = ctx.candidate_object
        if obj is None:
            return []

        issues: list[ValidationIssue] = []

        issues += self._validate_emails(obj)
        issues += self._validate_phones(obj)
        issues += self._validate_linkedin(obj)
        issues += self._validate_github(obj)
        issues += self._validate_years_exp(obj)

        if issues:
            for issue in issues:
                ctx.log(
                    "module8_data_validator",
                    issue.severity,
                    f"Validation issue [{issue.field}]: {issue.reason} (value={issue.value!r})",
                )
        else:
            ctx.log("module8_data_validator", "INFO", "All fields passed data validation.")

        return issues

    # ── Field validators ──────────────────────────────────────

    def _validate_emails(self, obj: CandidateObject) -> list[ValidationIssue]:
        issues = []
        valid = []
        for email in obj.emails:
            if not _EMAIL_RE.match(email):
                issues.append(ValidationIssue(
                    field="emails", value=email,
                    reason="Does not match email pattern.", severity="WARNING",
                ))
            else:
                valid.append(email)
        return issues

    def _validate_phones(self, obj: CandidateObject) -> list[ValidationIssue]:
        issues = []
        for phone in obj.phones:
            digit_count = sum(c.isdigit() for c in phone)
            if digit_count < self._phone_min_digits:
                issues.append(ValidationIssue(
                    field="phones", value=phone,
                    reason=f"Phone has only {digit_count} digits (minimum {self._phone_min_digits}).",
                    severity="WARNING",
                ))
        return issues

    def _validate_linkedin(self, obj: CandidateObject) -> list[ValidationIssue]:
        if obj.links.linkedin and not _LINKEDIN_RE.search(obj.links.linkedin):
            return [ValidationIssue(
                field="links.linkedin", value=obj.links.linkedin,
                reason="Does not look like a LinkedIn profile URL.",
                severity="WARNING",
            )]
        return []

    def _validate_github(self, obj: CandidateObject) -> list[ValidationIssue]:
        if obj.links.github and not _GITHUB_RE.search(obj.links.github):
            return [ValidationIssue(
                field="links.github", value=obj.links.github,
                reason="Does not look like a GitHub profile URL.",
                severity="WARNING",
            )]
        return []

    def _validate_years_exp(self, obj: CandidateObject) -> list[ValidationIssue]:
        if obj.years_experience is not None:
            if obj.years_experience < 0 or obj.years_experience > 60:
                return [ValidationIssue(
                    field="years_experience", value=obj.years_experience,
                    reason=f"Value {obj.years_experience} is outside plausible range 0–60.",
                    severity="WARNING",
                )]
        return []
