"""
src/phase2/module11_matcher.py
================================
MODULE 11 – Candidate Matching

Matching Priority (config-driven):
  1. Email        (exact, case-insensitive)
  2. Phone        (normalised digits)
  3. LinkedIn URL (normalised)
  4. GitHub URL   (normalised)
  5. Name similarity (rapidfuzz, configurable threshold)

If a match is found → return the existing candidate_id.
Else → signal that this is a new candidate.
"""

from __future__ import annotations

import logging
import re

from src.core.config_loader import get_config
from src.core.models import CanonicalCandidateRecord, CandidateObject

logger = logging.getLogger("phase2.module11_matcher")

_NON_DIGIT = re.compile(r"\D")


def _digits_only(s: str) -> str:
    return _NON_DIGIT.sub("", s)


class CandidateMatcher:

    def __init__(self) -> None:
        cfg = get_config()
        m_cfg = cfg.matching
        self._priority: list[str] = m_cfg["priority"]
        self._name_threshold: float = m_cfg["name_similarity_threshold"]

    def find_match(
        self,
        candidate: CandidateObject,
        existing_records: list[CanonicalCandidateRecord],
    ) -> tuple[str | None, str | None]:
        """
        Returns (matched_candidate_id, match_method) or (None, None) if new.
        """
        for method in self._priority:
            matched_id = self._match_by(method, candidate, existing_records)
            if matched_id:
                logger.info(
                    "Candidate matched: id=%s  method=%s  name=%s",
                    matched_id, method, candidate.full_name,
                )
                return matched_id, method

        logger.info("No match found — new candidate: %s", candidate.full_name)
        return None, None

    def _match_by(
        self,
        method: str,
        candidate: CandidateObject,
        records: list[CanonicalCandidateRecord],
    ) -> str | None:
        if method == "email":
            c_emails = {e.lower() for e in candidate.emails}
            for rec in records:
                if c_emails & {e.lower() for e in rec.emails}:
                    return rec.candidate_id

        elif method == "phone":
            c_phones = {_digits_only(p) for p in candidate.phones if _digits_only(p)}
            for rec in records:
                if c_phones & {_digits_only(p) for p in rec.phones}:
                    return rec.candidate_id

        elif method == "linkedin":
            if candidate.links.linkedin:
                c_li = candidate.links.linkedin.lower().rstrip("/")
                for rec in records:
                    if rec.links.linkedin and rec.links.linkedin.lower().rstrip("/") == c_li:
                        return rec.candidate_id

        elif method == "github":
            if candidate.links.github:
                c_gh = candidate.links.github.lower().rstrip("/")
                for rec in records:
                    if rec.links.github and rec.links.github.lower().rstrip("/") == c_gh:
                        return rec.candidate_id

        elif method == "name_similarity":
            if candidate.full_name:
                try:
                    from rapidfuzz import fuzz  # noqa: PLC0415
                    for rec in records:
                        if rec.full_name:
                            score = fuzz.token_sort_ratio(
                                candidate.full_name.lower(),
                                rec.full_name.lower(),
                            ) / 100.0
                            if score >= self._name_threshold:
                                return rec.candidate_id
                except ImportError:
                    logger.warning(
                        "rapidfuzz not installed — name similarity matching skipped. "
                        "Run `pip install rapidfuzz` to enable."
                    )

        return None
