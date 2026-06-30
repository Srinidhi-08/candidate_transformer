"""
src/phase2/module10_normalizer.py
===================================
MODULE 10 – Standardization / Normalization

Normalizes:
  - Dates          → ISO 8601 (YYYY-MM or YYYY)
  - Phone numbers  → E.164 where deterministic, else stripped digits
  - Emails         → lowercase
  - URLs           → lowercase scheme + netloc, strip trailing slash
  - Skill names    → lowercase, trimmed
  - Degree names   → title-case canonical form
  - Company names  → stripped, collapsed whitespace

Modifies a COPY of the CandidateObject; original is never touched.
"""

from __future__ import annotations

import copy
import logging
import re
from datetime import datetime

from src.core.models import (
    CandidateObject,
    EducationEntry,
    ExperienceEntry,
    SkillEntry,
)

logger = logging.getLogger("phase2.module10_normalizer")

# Month name → numeric
_MONTHS = {
    "jan": "01", "feb": "02", "mar": "03", "apr": "04",
    "may": "05", "jun": "06", "jul": "07", "aug": "08",
    "sep": "09", "oct": "10", "nov": "11", "dec": "12",
}

_YEAR_MONTH = re.compile(
    r"(jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|jun(?:e)?|"
    r"jul(?:y)?|aug(?:ust)?|sep(?:tember)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)"
    r"[\s.,\-]*(\d{4})",
    re.IGNORECASE,
)
_YYYY_MM = re.compile(r"(\d{4})[-/](\d{1,2})")
_YYYY = re.compile(r"\b(19|20)\d{2}\b")
_NON_DIGIT = re.compile(r"\D")

_DEGREE_MAP = {
    r"b\.?tech|bachelor of technology": "Bachelor of Technology",
    r"b\.?e\.?|bachelor of engineering": "Bachelor of Engineering",
    r"b\.?sc?\.?|bachelor of science": "Bachelor of Science",
    r"b\.?a\.?|bachelor of arts": "Bachelor of Arts",
    r"m\.?tech|master of technology": "Master of Technology",
    r"m\.?e\.?|master of engineering": "Master of Engineering",
    r"m\.?sc?\.?|master of science": "Master of Science",
    r"m\.?b\.?a\.?": "MBA",
    r"ph\.?d\.?|doctor of philosophy": "PhD",
    r"diploma": "Diploma",
    r"associate": "Associate Degree",
}


class Normalizer:

    def normalize(self, obj: CandidateObject) -> CandidateObject:
        """Returns a deep-copied, normalized CandidateObject."""
        n = copy.deepcopy(obj)

        # Emails
        n.emails = [e.lower().strip() for e in n.emails if e]

        # Phones
        n.phones = [self._normalize_phone(p) for p in n.phones if p]
        n.phones = [p for p in n.phones if p]  # remove empties

        # Links
        if n.links.linkedin:
            n.links.linkedin = self._normalize_url(n.links.linkedin)
        if n.links.github:
            n.links.github = self._normalize_url(n.links.github)
        if n.links.portfolio:
            n.links.portfolio = self._normalize_url(n.links.portfolio)
        n.links.other = [self._normalize_url(u) for u in n.links.other if u]

        # Skills
        n.skills = [self._normalize_skill(s) for s in n.skills]

        # Experience
        n.experience = [self._normalize_exp(e) for e in n.experience]

        # Education
        n.education = [self._normalize_edu(e) for e in n.education]

        # Name: title-case
        if n.full_name:
            n.full_name = n.full_name.strip().title()

        logger.debug("Normalization applied to candidate %s", n.candidate_id)
        return n

    # ── Normalizers ──────────────────────────────────────────

    @staticmethod
    def _normalize_phone(raw: str) -> str:
        digits = _NON_DIGIT.sub("", raw)
        if len(digits) >= 10:
            return digits  # store raw digits; E.164 needs country context
        return digits if len(digits) >= 7 else ""

    @staticmethod
    def _normalize_url(url: str) -> str:
        url = url.strip().rstrip("/")
        if not url.startswith("http"):
            url = "https://" + url
        return url.lower()

    @staticmethod
    def _normalize_skill(s: SkillEntry) -> SkillEntry:
        return SkillEntry(
            name=s.name.strip().lower(),
            confidence=s.confidence,
            sources=s.sources,
        )

    @staticmethod
    def _normalize_date(raw: str | None) -> str | None:
        if not raw:
            return None
        raw = raw.strip()
        # Already ISO
        if re.match(r"^\d{4}(-\d{2})?$", raw):
            return raw
        # present / current / now
        if raw.lower() in ("present", "current", "now"):
            return "present"
        # "Month YYYY"
        m = _YEAR_MONTH.search(raw)
        if m:
            month_str = m.group(1)[:3].lower()
            year = m.group(2)
            month_num = _MONTHS.get(month_str, "01")
            return f"{year}-{month_num}"
        # YYYY-MM or YYYY/MM
        m = _YYYY_MM.search(raw)
        if m:
            return f"{m.group(1)}-{int(m.group(2)):02d}"
        # Just YYYY
        m = _YYYY.search(raw)
        if m:
            return m.group(0)
        return raw

    def _normalize_exp(self, e: ExperienceEntry) -> ExperienceEntry:
        from src.core.models import ExperienceEntry as _E  # avoid circular at top
        return _E(
            company=e.company.strip() if e.company else None,
            title=e.title.strip() if e.title else None,
            start=self._normalize_date(e.start),
            end=self._normalize_date(e.end),
            location=e.location,
            description=e.description,
        )

    def _normalize_edu(self, e: EducationEntry) -> EducationEntry:
        from src.core.models import EducationEntry as _Ed
        degree = self._normalize_degree(e.degree)
        return _Ed(
            institution=e.institution.strip() if e.institution else None,
            degree=degree,
            field_of_study=e.field_of_study,
            start_year=e.start_year,
            end_year=e.end_year,
            grade=e.grade,
        )

    @staticmethod
    def _normalize_degree(raw: str | None) -> str | None:
        if not raw:
            return None
        lower = raw.strip().lower()
        for pattern, canonical in _DEGREE_MAP.items():
            if re.search(pattern, lower, re.IGNORECASE):
                return canonical
        return raw.strip()
