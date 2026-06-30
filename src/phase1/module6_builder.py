"""
src/phase1/module6_builder.py
==============================
MODULE 6 – Candidate Object Builder

Responsibility
--------------
Convert every type of parsed content into one CandidateObject.

Two distinct strategies, selected by ParsedContent.data_category:

A) STRUCTURED (CSV / JSON)
   Direct field mapping using config aliases.
   Each row → one CandidateObject.
   No NLP needed.

B) UNSTRUCTURED (PDF / DOCX / TXT / etc.)
   1. Section Splitter   — splits raw text into named sections
                           (experience / education / skills / summary / …)
   2. Global Extractors  — regex for fixed-format fields
                           (email, phone, LinkedIn URL, GitHub URL)
   3. NLP Extractors     — spaCy PERSON for name, PhraseMatcher for skills,
                           sentence segmentation for summary, ORG + DATE
                           entities for experience / education entries.
   4. Regex Fallback     — activated automatically when spaCy is not installed;
                           heuristic line-pattern extractor replaces every NLP
                           step without changing the public interface.

Provenance is recorded for every extracted field so the Confidence Engine
and the Projection Layer can explain how each value was obtained.
"""

from __future__ import annotations

import logging
import re
import uuid
from abc import ABC, abstractmethod
from typing import Any

from src.core.config_loader import get_config
from src.core.exceptions import CandidateBuildError, StructuredMappingError
from src.core.models import (
    CandidateObject,
    CertificationEntry,
    EducationEntry,
    ExperienceEntry,
    LinksInfo,
    LocationInfo,
    ParsedContent,
    ProjectEntry,
    ProvenanceEntry,
    SkillEntry,
)

logger = logging.getLogger("phase1.module6_builder")

# ──────────────────────────────────────────────────────────────
# Fixed-format regex patterns (email / phone / URLs)
# These are NEVER replaced by NLP — regex IS the right tool here.
# ──────────────────────────────────────────────────────────────

_EMAIL = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")
_PHONE = re.compile(r"(\+?[\d][\d\s\-().]{6,}\d)")
_LINKEDIN = re.compile(
    r"(?:https?://)?(?:www\.)?linkedin\.com/in/[\w\-]+(?:/[\w\-]*)*", re.IGNORECASE
)
_GITHUB = re.compile(
    r"(?:https?://)?(?:www\.)?github(?:\.com)?/[\w\-]+(?:/[\w\-]*)*", re.IGNORECASE
)
_PORTFOLIO = re.compile(
    r"(?:https?://)?(?:www\.)?[\w\-]+\.(?:dev|me|io|app|net)(?:/[\w\-./?%&=]*)?",
    re.IGNORECASE,
)
_GENERIC_URL = re.compile(
    r"https?://[\w\-.]+\.[\w]{2,}(?:/[\w\-./?%&=]*)?", re.IGNORECASE
)
_YEAR_4 = re.compile(r"\b(?:19|20)\d{2}\b")
_DATE_RANGE = re.compile(
    r"((?:19|20)\d{2}(?:[-/]\d{1,2})?)\s*(?:\s*[-–—to]+\s*)((?:19|20)\d{2}(?:[-/]\d{1,2})?|present|current|now)",
    re.IGNORECASE,
)
_OCR_AT = re.compile(r"\s*@\s*")

# Separators that split job-title from company in experience headings
_EXP_SEP = re.compile(r"\s*[–—|@,]\s*")  # Fix 3/4

# Known certification issuers — if a cert line is ONLY one of these, treat as issuer
_KNOWN_ISSUERS = {
    "udemy", "coursera", "edx", "forage", "google", "microsoft", "amazon",
    "aws", "nptel", "infosys", "tcs", "oracle", "ibm", "linkedin learning",
    "simplilearn", "great learning", "hackerrank", "leetcode", "nasscom",
}

# Field-of-study keywords  (Fix 2)
_FIELD_KEYWORDS = re.compile(
    r"(?:computer science|information technology|electronics|electrical|"
    r"mechanical|civil|chemical|biotechnology|mathematics|physics|"
    r"artificial intelligence|data science|it|cse|ece|eee|mech|ce)",
    re.IGNORECASE,
)

# Location patterns for Indian resumes  (Fix 6)
_LOCATION_RE = re.compile(
    r"(?P<city>[A-Z][a-zA-Z\s]{2,20}?)\s*,\s*(?P<region>[A-Z][a-zA-Z\s]{2,20}?)(?:\s*,\s*(?P<country>[A-Z][a-zA-Z\s]{2,20}))?|\b(?:Chennai|Mumbai|Bangalore|Bengaluru|Hyderabad|Delhi|Pune|Kolkata|Ahmedabad|Jaipur|Coimbatore|Noida|Gurgaon|Kochi|Trivandrum|Mysore|Mangalore|Nagpur|Bhopal|Lucknow|Kanpur|Surat|Vadodara|Indore|Visakhapatnam)\b",
    re.IGNORECASE,
)


# ──────────────────────────────────────────────────────────────
# spaCy lazy-load  (None → graceful fallback)
# ──────────────────────────────────────────────────────────────

def _load_spacy(model_name: str):
    """Returns the loaded spaCy Language object, or None if unavailable."""
    try:
        import spacy  # noqa: PLC0415
        return spacy.load(model_name)
    except (ImportError, OSError):
        return None


# ──────────────────────────────────────────────────────────────
# Section Splitter
# ──────────────────────────────────────────────────────────────

class SectionSplitter:
    """
    Splits a flat resume text into named sections.

    A line is treated as a section header when:
      - It matches one of the known header strings (case-insensitive,
        after stripping punctuation / whitespace), AND
      - It is short (≤ 60 chars), AND
      - It is on a line by itself (or the surrounding lines are blank)

    Returns a dict:  {section_name: [list of content lines]}
    Unassigned lines before the first header go into the "preamble" bucket.
    """

    def __init__(self, cfg) -> None:
        headers_cfg: dict[str, list[str]] = cfg.extraction["section_headers"]
        ignored_cfg: list[str] = cfg.extraction.get("ignored_sections", [])

        # Build a lookup: normalised header text → canonical section name
        self._header_lookup: dict[str, str] = {}
        for section, aliases in headers_cfg.items():
            for alias in aliases:
                self._header_lookup[alias.lower().strip()] = section

        # Ignored sections — recognised as boundaries but not collected
        self._ignored: set[str] = set(ignored_cfg)
        for ign in ignored_cfg:
            self._header_lookup[ign.lower().strip()] = f"__ignored__{ign}"

    def split(self, text: str) -> dict[str, list[str]]:
        """
        Returns dict like:
          {
            "preamble":   ["Jane Doe", "jane@example.com", ...],
            "summary":    ["Experienced Python developer ..."],
            "experience": ["Senior Engineer @ Google  2019 – 2022", ...],
            "education":  ["B.Tech Computer Science, IIT  2015", ...],
            "skills":     ["Python, Java, Docker, Kubernetes", ...],
          }
        """
        sections: dict[str, list[str]] = {"preamble": []}
        current = "preamble"

        for raw_line in text.splitlines():
            line = raw_line.strip()
            if not line:
                continue  # skip blank lines

            detected = self._detect_header(line)
            if detected is not None:
                current = detected
                if current not in sections and not current.startswith("__ignored__"):
                    sections[current] = []
                elif current.startswith("__ignored__"):
                    # Ensure bucket exists so we don't dump lines into previous section
                    if current not in sections:
                        sections[current] = []
            else:
                if current not in sections:
                    sections[current] = []
                sections[current].append(line)

        return sections

    def _detect_header(self, line: str) -> str | None:
        """
        Returns the canonical section name if `line` is a section header,
        otherwise None.
        """
        if len(line) > 80:
            return None
        # Strip bullets, dashes, underscores, colons
        normalised = re.sub(r"[•\-=_:|*]+", "", line).strip().lower()
        if not normalised:
            return None
        return self._header_lookup.get(normalised)


# ──────────────────────────────────────────────────────────────
# Fixed-format field extractor  (always used, regardless of NLP)
# ──────────────────────────────────────────────────────────────

class FixedFormatExtractor:
    """
    Extracts fields that have a single, unambiguous format.
    NLP would add false negatives here, not accuracy.
    """

    def extract(self, text: str) -> dict[str, Any]:
        # Normalise OCR-broken emails first (e.g. "jane @ example .com")
        clean = _OCR_AT.sub("@", text)

        emails = list(dict.fromkeys(_EMAIL.findall(clean)))
        phones = list(dict.fromkeys(
            m.strip() for m in _PHONE.findall(text)
            # Reject strings that are mostly non-digit (e.g. date-like)
            if sum(c.isdigit() for c in m) >= 7
        ))

        linkedin_m = _LINKEDIN.search(text)
        github_m = _GITHUB.search(text)

        # Portfolio: first URL that is NOT linkedin/github
        portfolio = None
        for line in text.split("\n"):
            ll = line.lower()
            if "linkedin.com" in ll or "github.com" in ll:
                continue
            m = _PORTFOLIO.search(line)
            if m:
                portfolio = m.group(0)
                break

        # Other URLs
        all_urls = _GENERIC_URL.findall(text)
        known = {linkedin_m.group(0) if linkedin_m else None,
                 github_m.group(0) if github_m else None,
                 portfolio}
        other_urls = [u for u in all_urls if u not in known]

        return {
            "emails":    emails,
            "phones":    phones,
            "linkedin":  linkedin_m.group(0) if linkedin_m else None,
            "github":    github_m.group(0) if github_m else None,
            "portfolio": portfolio,
            "other_urls": other_urls,
        }


# ──────────────────────────────────────────────────────────────
# NLP-based free-text extractor  (spaCy)
# ──────────────────────────────────────────────────────────────

class NlpExtractor:
    """
    Uses spaCy for name / skills / summary / experience / education.

    Falls back automatically to regex heuristics if spaCy is absent.
    """

    _NAME_LINE = re.compile(r"^[A-Z][a-zA-Z'.\-]*(?:\s+[A-Z][a-zA-Z'.\-]*){0,4}$")
    _GRADE = re.compile(
        r"(?:cgpa|gpa|grade|percentage|%)\s*[:\-]?\s*([\d.]+(?:\s*/\s*[\d.]+)?)",
        re.IGNORECASE,
    )

    def __init__(self, cfg) -> None:
        model_name: str = cfg.extraction["nlp_model"]
        self._nlp = _load_spacy(model_name)
        self.nlp_available = self._nlp is not None

        # Config-driven lists
        self._name_scan_lines: int = cfg.extraction["name_scan_lines"]
        self._name_excluded: set[str] = set(cfg.extraction["name_excluded_headers"])
        self._degree_keywords: list[str] = cfg.extraction["degree_keywords"]
        self._section_skill_conf: float = cfg.extraction["skills"]["section_confidence"]
        self._body_skill_conf: float = cfg.extraction["skills"]["body_confidence"]
        self._canonical_skills: list[str] = cfg.extraction["skills"]["canonical_list"]

        if self.nlp_available:
            self._skill_matcher = self._build_skill_matcher()
            logger.info("spaCy model '%s' loaded — NLP extraction active.", model_name)
        else:
            logger.warning(
                "spaCy / '%s' not found — falling back to regex heuristics. "
                "Run: pip install spacy && python -m spacy download %s",
                model_name, model_name,
            )

    # ── Name ────────────────────────────────────────────────

    def extract_name(self, text: str, preamble_lines: list[str]) -> tuple[str | None, str]:
        """Returns (name, method_used)."""
        if self.nlp_available:
            # Only process the top portion of the document for speed
            top_text = "\n".join(preamble_lines[: self._name_scan_lines])
            doc = self._nlp(top_text)
            for ent in doc.ents:
                if ent.label_ != "PERSON":
                    continue
                candidate = ent.text.strip()
                if self._is_valid_name(candidate):
                    return candidate, "nlp_ner"
            # spaCy found nothing — fall through to heuristic
        return self._heuristic_name(preamble_lines), "regex_heuristic"

    def _is_valid_name(self, name: str) -> bool:
        if not name:
            return False
        nl = name.lower()
        if nl in self._name_excluded:
            return False
        if "@" in name or any(ch.isdigit() for ch in name):
            return False
        if "/" in name or "\\" in name or ":" in name:
            return False
        # Filter out links and domains
        if any(kw in nl for kw in ["linkedin", "github", "http", "www.", ".com", ".org", ".net", ".edu", ".in"]):
            return False
        if len(name.split()) < 1 or len(name) > 60:
            return False
        return True

    def _heuristic_name(self, preamble_lines: list[str]) -> str | None:
        for line in preamble_lines[: self._name_scan_lines]:
            candidate = re.sub(r"[•\-:|*]+", "", line).strip()
            if self._is_valid_name(candidate) and self._NAME_LINE.match(candidate):
                return candidate
        return None


    # ── Headline ─────────────────────────────────────────────

    def extract_headline(
        self, preamble_lines: list[str], name: str | None
    ) -> str | None:
        """The line immediately after the name that looks like a job title."""
        # Fix 7: raise cap to 20 words, add address-line guard
        found_name = False
        for line in preamble_lines[:20]:
            stripped = line.strip()
            if not stripped:
                continue
            if name and stripped == name:
                found_name = True
                continue
            if found_name or name is None:
                # Skip lines that are clearly contact info
                if _EMAIL.search(stripped) or _PHONE.search(stripped):
                    continue
                if any(kw in stripped.lower() for kw in ["linkedin", "github", "http", "www."]):
                    continue
                # Reject purely numeric lines
                if re.match(r"^[\d\s\-+().]+$", stripped):
                    continue
                # Reject address-like lines (city, state patterns)
                if re.search(r"\d{5,6}|pincode|p\.o\.box", stripped, re.IGNORECASE):
                    continue
                if _LOCATION_RE.search(stripped) and "|" not in stripped:
                    continue
                # Must have at least 2 words and fewer than 20 words
                words = stripped.split()
                if 2 <= len(words) <= 20:
                    return stripped
        return None

    # ── Summary ──────────────────────────────────────────────

    def extract_summary(self, summary_lines: list[str]) -> str | None:
        if not summary_lines:
            return None
        text = " ".join(summary_lines)

        if self.nlp_available:
            doc = self._nlp(text)
            sents = [s.text.strip() for s in doc.sents if len(s.text.strip()) > 30]
            if sents:
                return " ".join(sents[:3])  # First 3 sentences

        # Fallback: return first non-trivial paragraph
        paragraphs = [p.strip() for p in text.split("  ") if len(p.strip()) > 30]
        return paragraphs[0] if paragraphs else (text.strip() if text.strip() else None)

    # ── Skills ──────────────────────────────────────────────

    def _build_skill_matcher(self):
        from spacy.matcher import PhraseMatcher  # noqa: PLC0415
        matcher = PhraseMatcher(self._nlp.vocab, attr="LOWER")
        patterns = [self._nlp.make_doc(sk) for sk in self._canonical_skills]
        matcher.add("SKILLS", patterns)
        return matcher

    def extract_skills(
        self, skill_lines: list[str], full_text: str
    ) -> list[SkillEntry]:
        """
        Two-pass extraction:
        Pass 1 — skills section text (higher confidence)
        Pass 2 — scan full document (lower confidence, deduplication)
        """
        found: dict[str, SkillEntry] = {}

        skill_text = "\n".join(skill_lines)

        if self.nlp_available:
            # Pass 1 — inside skills section
            doc = self._nlp(skill_text)
            for _, start, end in self._skill_matcher(doc):
                name = doc[start:end].text.lower()
                found[name] = SkillEntry(
                    name=name,
                    confidence=self._section_skill_conf,
                    sources=["nlp_section"],
                )

            # Pass 2 — full document (only add skills not already found)
            doc_full = self._nlp(full_text[:20_000])  # cap for performance
            for _, start, end in self._skill_matcher(doc_full):
                name = doc_full[start:end].text.lower()
                if name not in found:
                    found[name] = SkillEntry(
                        name=name,
                        confidence=self._body_skill_conf,
                        sources=["nlp_body"],
                    )
        else:
            # Regex fallback — case-insensitive word-boundary match
            combined = (skill_text + "\n" + full_text).lower()
            for sk in self._canonical_skills:
                pattern = re.compile(
                    r"\b" + re.escape(sk.lower()) + r"\b", re.IGNORECASE
                )
                if sk.lower() in found:
                    continue
                in_section = bool(pattern.search(skill_text.lower()))
                in_body = bool(pattern.search(combined))
                if in_section:
                    found[sk.lower()] = SkillEntry(
                        sk, self._section_skill_conf, ["regex_section"]
                    )
                elif in_body:
                    found[sk.lower()] = SkillEntry(
                        sk, self._body_skill_conf, ["regex_body"]
                    )

        return list(found.values())

    # ── Experience ──────────────────────────────────────────

    def extract_experience(self, exp_lines: list[str]) -> list[ExperienceEntry]:
        """
        Parses experience section lines into structured entries.
        Each entry is separated by a blank line OR a new company/date-range line.
        Uses spaCy ORG entities (if available) to identify company names.
        """
        entries: list[ExperienceEntry] = []
        if not exp_lines:
            return entries

        # Group lines into blocks (separated by blank lines within section)
        blocks = self._group_into_blocks(exp_lines)

        for block in blocks:
            if not block:
                continue
            entry = self._parse_exp_block(block)
            if entry.company or entry.title or entry.start:
                entries.append(entry)

        return entries

    def _parse_exp_block(self, lines: list[str]) -> ExperienceEntry:
        """Extract company, title, dates, description from a block of lines."""
        entry = ExperienceEntry()
        description_lines: list[str] = []

        for i, line in enumerate(lines):
            # Date range detection (always regex)
            date_m = _DATE_RANGE.search(line)
            if date_m and entry.start is None:
                entry.start = date_m.group(1)
                entry.end = date_m.group(2)

            # Fix 3 & 4: Try to split title and company from the first line
            # using common separators (–, |, @, comma before company name)
            if i == 0 and entry.company is None and entry.title is None:
                split_result = self._split_title_company(line)
                if split_result:
                    entry.title, entry.company = split_result
                else:
                    # Fall back to ORG detection
                    company = self._extract_org(line)
                    if company:
                        entry.company = company
            elif entry.company is None and i < 3:
                company = self._extract_org(line)
                if company:
                    entry.company = company

            # Title heuristics — line before date-range, or after company
            if entry.title is None and i < 3:
                title = self._extract_title_hint(line, entry.company)
                if title:
                    entry.title = title

            # Description: everything after the first few header lines
            if i >= 2 or (i >= 1 and entry.company and entry.title):
                stripped = line.lstrip("•–-* \t")
                if stripped:
                    description_lines.append(stripped)

        if description_lines:
            entry.description = " ".join(description_lines[:5])  # cap at 5 sentences

        return entry

    def _split_title_company(self, line: str) -> tuple[str, str] | None:
        """
        Fix 3 & 4: Split a heading line like 'FSD Developer – Rane' or
        'Senior Engineer | Google | 2022–2024' into (title, company).
        Returns (title, company) or None if no clear separator found.
        """
        # Remove date ranges first
        clean = _DATE_RANGE.sub("", line).strip()
        parts = [p.strip() for p in _EXP_SEP.split(clean) if p.strip()]
        if len(parts) < 2:
            return None
        # Heuristic: first part = title (role-like), second part = company
        # Title part should NOT start with a capital acronym that looks like org
        title_candidate = parts[0]
        company_candidate = parts[1]
        # Basic sanity: title has lowercase chars (real words), not just acronym
        if re.search(r"[a-z]", title_candidate) and len(title_candidate) >= 3:
            return title_candidate, company_candidate
        return None

    def _extract_org(self, line: str) -> str | None:
        """Return ORG entity from line if spaCy available, else heuristic."""
        if self.nlp_available:
            doc = self._nlp(line)
            for ent in doc.ents:
                if ent.label_ == "ORG":
                    return ent.text.strip()
        # Heuristic: capitalised proper-noun phrase before separator
        m = re.match(
            r"^([A-Z][A-Za-z0-9 &.,'\-]{1,50}?)(?:\s*[|–\-@,]|\s{2,})", line
        )
        return m.group(1).strip() if m else None

    def _extract_title_hint(self, line: str, company: str | None) -> str | None:
        """
        A line that is NOT the company line AND looks like a title.
        E.g. 'Senior Software Engineer' or 'Lead Data Scientist | Google'
        """
        if company and line.startswith(company):
            return None
        # Strip dates
        clean = _DATE_RANGE.sub("", line).strip()
        clean = _YEAR_4.sub("", clean).strip(" |–-,")
        if not clean or len(clean) < 3 or len(clean) > 80:
            return None
        # Must contain at least one capitalised word
        if not re.search(r"[A-Z][a-z]+", clean):
            return None
        return clean or None

    # ── Education ───────────────────────────────────────────

    def extract_education(self, edu_lines: list[str]) -> list[EducationEntry]:
        entries: list[EducationEntry] = []
        if not edu_lines:
            return entries

        blocks = self._group_into_blocks(edu_lines)
        for block in blocks:
            if not block:
                continue
            entry = self._parse_edu_block(block)
            if entry.institution or entry.degree:
                entries.append(entry)

        # Deduplicate: merge entries that share the same institution
        # (happens when institution+degree are on line 1 and CGPA on line 2 → two blocks)
        merged: list[EducationEntry] = []
        for e in entries:
            matched = next(
                (m for m in merged if m.institution and e.institution
                 and m.institution.lower() == e.institution.lower()), None
            )
            if matched:
                # Prefer the entry with more information
                if matched.degree is None and e.degree:
                    matched.degree = e.degree
                if matched.field_of_study is None and e.field_of_study:
                    matched.field_of_study = e.field_of_study
                if matched.grade is None and e.grade:
                    matched.grade = e.grade
                if matched.start_year is None and e.start_year:
                    matched.start_year = e.start_year
                if matched.end_year is None and e.end_year:
                    matched.end_year = e.end_year
            else:
                merged.append(e)

        return merged

    def _parse_edu_block(self, lines: list[str]) -> EducationEntry:
        entry = EducationEntry()

        for line in lines:
            lower = line.lower()
            # Fix 2: normalise internal spaces before degree matching
            # e.g. "B. E" → "B.E", "B. Tech" → "B.Tech"
            lower_compact = re.sub(r"\.\s+", ".", lower)

            # Fix 1: Year extraction — also handle "Month YYYY" format
            # Only accept valid 4-digit years [1950, 2050]
            years = [
                int(y) for y in _YEAR_4.findall(line)
                if 1950 <= int(y) <= 2050
            ]
            if years:
                entry.end_year = max(years)
                if len(years) >= 2:
                    entry.start_year = min(years)

            # Fix 2: Degree keyword detection with compact form
            if entry.degree is None:
                for kw in self._degree_keywords:
                    # Match against both original and space-collapsed form
                    pattern = r"\b" + re.escape(kw) + r"\b"
                    found_in_compact = re.search(pattern, lower_compact)
                    found_in_lower   = re.search(pattern, lower)
                    if found_in_compact or found_in_lower:
                        # Prefer position from original lower string; fall back to compact
                        match_pos = found_in_lower or found_in_compact
                        # Trim everything before the degree keyword start
                        degree_part = line[match_pos.start():].strip()
                        # Remove trailing date/year ranges for cleanliness
                        degree_part = _DATE_RANGE.sub("", degree_part).strip(" –-,")
                        entry.degree = degree_part or line.strip()
                        break

            # Fix 2: Field of study extraction — prefer longest match
            if entry.field_of_study is None:
                best_match = None
                for fos_m in _FIELD_KEYWORDS.finditer(line):
                    candidate = fos_m.group(0).strip()
                    if best_match is None or len(candidate) > len(best_match):
                        best_match = candidate
                if best_match:
                    entry.field_of_study = best_match.title()

            # Grade / GPA
            if entry.grade is None:
                grade_m = self._GRADE.search(line)
                if grade_m:
                    entry.grade = grade_m.group(1)

            # Institution via ORG entity or heuristic
            # When institution and degree are on the same line, extract up to the degree keyword
            if entry.institution is None:
                # Try to isolate institution name before degree keyword
                inst_line = line
                for kw in self._degree_keywords:
                    pattern = r"\b" + re.escape(kw) + r"\b"
                    m = re.search(pattern, lower, re.IGNORECASE)
                    if not m:
                        m = re.search(pattern, lower_compact, re.IGNORECASE)
                    if m:
                        # Take everything before the degree keyword
                        inst_line = line[:m.start()].strip()
                        break
                if inst_line:
                    org = self._extract_org(inst_line)
                    if org and not any(
                        kw in org.lower() for kw in self._degree_keywords
                    ):
                        entry.institution = org
                # If still not found, try full line
                if entry.institution is None:
                    org = self._extract_org(line)
                    if org and not any(
                        kw in org.lower() for kw in self._degree_keywords
                    ):
                        entry.institution = org

        return entry

    # ── Certifications ───────────────────────────────────────

    def extract_certifications(self, cert_lines: list[str]) -> list[CertificationEntry]:
        """
        Fix 5: Group certification lines so that an issuer name on the very
        next line (e.g. "Udemy", "Forage") is merged into the same entry
        instead of becoming a separate certification.
        """
        entries: list[CertificationEntry] = []
        ignored_cert_keywords = [
            "leetcode", "codechef", "codeforces", "hackerrank", "hackerearth",
            "kaggle", "atcoder", "topcoder", "geeksforgeeks", "gfg", "max rating",
            "rating:", "global rank", "highest rank", "contest rank", "competitive programming"
        ]

        # Filter and clean non-empty lines
        filtered: list[str] = [
            line for line in cert_lines
            if line.strip() and not any(kw in line.lower() for kw in ignored_cert_keywords)
        ]

        i = 0
        while i < len(filtered):
            line = filtered[i]
            line_stripped = line.strip()
            line_lower = line_stripped.lower()

            # If this line is purely an issuer name (and we have a previous entry), skip
            # — it will have been consumed as the issuer of the previous cert
            if line_lower in _KNOWN_ISSUERS and entries:
                # Attach as issuer to previous entry if not already set
                if entries[-1].issuer is None:
                    entries[-1].issuer = line_stripped
                i += 1
                continue

            cert = CertificationEntry()
            # Strip bullet, year, separators from name
            cert.name = re.sub(r"\b(19|20)\d{2}\b", "", line_stripped).strip("• ,|–-")
            year_m = _YEAR_4.search(line_stripped)
            if year_m:
                cert.date = year_m.group(0)
            url_m = _GENERIC_URL.search(line_stripped)
            if url_m:
                cert.url = url_m.group(0)

            # Look ahead: if the next line is a known issuer, consume it
            if i + 1 < len(filtered):
                next_line = filtered[i + 1].strip()
                if next_line.lower() in _KNOWN_ISSUERS:
                    cert.issuer = next_line
                    i += 1  # consume issuer line

            if cert.name:
                entries.append(cert)
            i += 1

        return entries

    # ── Projects ─────────────────────────────────────────────

    def extract_projects(self, proj_lines: list[str]) -> list[ProjectEntry]:
        """
        Fix 8: Parse project title lines — if `|` is present, split on `|`;
        the right part contains a comma-separated tech list.
        """
        entries: list[ProjectEntry] = []
        blocks = self._group_into_blocks(proj_lines)
        for block in blocks:
            if not block:
                continue
            proj = ProjectEntry()
            title_line = block[0].strip()

            # Fix 8: Extract technologies from title line after `|`
            if "|" in title_line:
                title_part, _, tech_part = title_line.partition("|")
                proj.name = title_part.strip()
                techs = [
                    t.strip() for t in re.split(r"[,;]", tech_part)
                    if t.strip()
                ]
                proj.technologies = techs
            else:
                proj.name = title_line

            if len(block) > 1:
                # Take just the first descriptive line as a clean one-liner
                first_line = block[1].strip().lstrip("•- \t")
                proj.description = first_line
            url_m = _GENERIC_URL.search("\n".join(block))
            if url_m:
                proj.url = url_m.group(0)
            entries.append(proj)
        return entries

    # ── Years of experience ──────────────────────────────────

    def estimate_years_experience(self, experience: list[ExperienceEntry]) -> float | None:
        """
        Rough estimate: latest end year minus earliest start year across
        all experience entries.  Returns None if we can't determine either.
        """
        starts, ends = [], []
        for e in experience:
            if e.start:
                m = _YEAR_4.search(e.start)
                if m:
                    starts.append(int(m.group(0)))
            if e.end:
                m = _YEAR_4.search(e.end)
                if m:
                    ends.append(int(m.group(0)))
                elif e.end.lower() in ("present", "current", "now"):
                    from datetime import datetime  # noqa: PLC0415
                    ends.append(datetime.now().year)

        if starts and ends:
            return float(max(ends) - min(starts))
        return None

    # ── Location ─────────────────────────────────────────────

    def extract_location(self, preamble_lines: list[str], full_text: str) -> tuple[str | None, str | None, str | None]:
        """
        Fix 6: Extract city / region / country from preamble.
        Returns (city, region, country).
        """
        city = region = country = None

        # Try spaCy GPE entities first (if available)
        if self.nlp_available:
            top_text = "\n".join(preamble_lines[:15])
            doc = self._nlp(top_text)
            gpe_list = [ent.text.strip() for ent in doc.ents if ent.label_ == "GPE"]
            if gpe_list:
                city = gpe_list[0]
                if len(gpe_list) >= 2:
                    region = gpe_list[1]
                if len(gpe_list) >= 3:
                    country = gpe_list[2]

        # Regex fallback: look for "City, State" or "City, Country" patterns
        if city is None:
            search_text = "\n".join(preamble_lines[:15])
            m = _LOCATION_RE.search(search_text)
            if m:
                if m.lastindex and m.lastindex >= 1 and m.group("city"):
                    city = m.group("city").strip()
                if m.lastindex and m.lastindex >= 2 and m.group("region"):
                    region = m.group("region").strip()
                if m.lastindex and m.lastindex >= 3 and m.group("country"):
                    country = m.group("country").strip()
                elif m.group(0):  # Named city match
                    city = m.group(0).strip()

        return city, region, country

    # ── Utilities ────────────────────────────────────────────

    @staticmethod
    def _group_into_blocks(lines: list[str]) -> list[list[str]]:
        """
        Groups consecutive non-empty lines into blocks.
        Each block is a potential distinct entry (job, degree, project).
        A new block also starts when we see a clear date-range pattern or
        an ALL-CAPS heading-like line.
        """
        blocks: list[list[str]] = []
        current: list[str] = []

        for line in lines:
            stripped = line.strip()

            if not stripped:
                if current:
                    blocks.append(current)
                    current = []
                continue

            # New block trigger: line has a date range AND current block already has content
            if current and _DATE_RANGE.search(stripped):
                # If the current block already has dates, start a new one
                if any(_DATE_RANGE.search(l) for l in current):
                    blocks.append(current)
                    current = []

            current.append(stripped)

        if current:
            blocks.append(current)

        return blocks


# ──────────────────────────────────────────────────────────────
# Structured field mapper
# ──────────────────────────────────────────────────────────────

class StructuredFieldMapper:
    """
    Maps structured row dicts (from CSV / JSON) directly to
    CandidateObject fields using the alias lists in config.
    """

    def __init__(self, cfg) -> None:
        self._aliases: dict[str, list[str]] = cfg.extraction["field_aliases"]

    def _get(self, row: dict, canonical_key: str) -> str | None:
        """Return first matching alias value from the row, or None."""
        for alias in self._aliases.get(canonical_key, [canonical_key]):
            # Case-insensitive key lookup
            for k, v in row.items():
                if k.lower().strip() == alias.lower().strip() and v:
                    return str(v).strip()
        return None

    def map_row(self, row: dict, source: str) -> CandidateObject:
        obj = CandidateObject()

        def _prov(field: str, method: str = "direct_mapping") -> ProvenanceEntry:
            return ProvenanceEntry(field=field, source=source, method=method)

        # Personal
        name = self._get(row, "full_name")
        if name:
            obj.full_name = name
            obj.provenance.append(_prov("full_name"))

        # Contact
        email = self._get(row, "email")
        if email:
            obj.emails = [email]
            obj.provenance.append(_prov("emails"))

        phone = self._get(row, "phone")
        if phone:
            obj.phones = [phone]
            obj.provenance.append(_prov("phones"))

        # Links
        linkedin = self._get(row, "linkedin")
        github = self._get(row, "github")
        if linkedin or github:
            obj.links = LinksInfo(linkedin=linkedin, github=github)
            if linkedin:
                obj.provenance.append(_prov("links.linkedin"))
            if github:
                obj.provenance.append(_prov("links.github"))

        # Location
        city = self._get(row, "city")
        region = self._get(row, "region")
        country = self._get(row, "country")
        if any([city, region, country]):
            obj.location = LocationInfo(city=city, region=region, country=country)
            obj.provenance.append(_prov("location"))

        # Professional
        headline = self._get(row, "headline")
        if headline:
            obj.headline = headline
            obj.provenance.append(_prov("headline"))

        summary = self._get(row, "summary")
        if summary:
            obj.summary = summary
            obj.provenance.append(_prov("summary"))

        years = self._get(row, "years_exp")
        if years:
            try:
                obj.years_experience = float(years)
                obj.provenance.append(_prov("years_experience"))
            except ValueError:
                pass

        # Skills — comma-separated list in a single cell
        for col in ["skills", "skill_list", "competencies", "technologies"]:
            val = row.get(col, "") or ""
            if val.strip():
                for sk in re.split(r"[,;|/]+", val):
                    sk = sk.strip()
                    if sk:
                        obj.skills.append(
                            SkillEntry(name=sk.lower(), confidence=0.95, sources=["direct_mapping"])
                        )
                if obj.skills:
                    obj.provenance.append(_prov("skills"))
                break

        # Current company / title → one experience entry
        company = self._get(row, "company")
        title = self._get(row, "headline") or self._get(row, "headline")
        if company or title:
            obj.experience.append(ExperienceEntry(company=company, title=title))
            obj.provenance.append(_prov("experience"))

        obj.overall_confidence = 0.90 if obj.full_name and obj.emails else 0.65
        return obj


# ──────────────────────────────────────────────────────────────
# Free-text (unstructured) builder
# ──────────────────────────────────────────────────────────────

class FreeTextBuilder:
    """
    Converts a raw text string (from PdfParser, DocxParser, etc.)
    into a CandidateObject.

    Pipeline:
    1. SectionSplitter splits text into named section buckets.
    2. FixedFormatExtractor pulls emails / phones / URLs (regex).
    3. NlpExtractor pulls name / skills / experience / education / etc.
    4. Provenance is attached to every extracted value.
    """

    def __init__(self, cfg, source: str) -> None:
        self._splitter = SectionSplitter(cfg)
        self._fixed = FixedFormatExtractor()
        self._nlp = NlpExtractor(cfg)
        self._source = source

    def build(self, text: str) -> CandidateObject:
        if not text or not text.strip():
            logger.warning("Empty text passed to FreeTextBuilder — returning empty CandidateObject.")
            return CandidateObject()

        obj = CandidateObject()

        # ── 1. Section split ─────────────────────────────────
        sections = self._splitter.split(text)
        preamble = sections.get("preamble", [])

        def _prov(field: str, method: str) -> ProvenanceEntry:
            return ProvenanceEntry(field=field, source=self._source, method=method)

        # ── 2. Fixed-format fields (regex) ───────────────────
        ff = self._fixed.extract(text)

        if ff["emails"]:
            obj.emails = ff["emails"]
            obj.provenance.append(_prov("emails", "regex"))
        if ff["phones"]:
            obj.phones = ff["phones"]
            obj.provenance.append(_prov("phones", "regex"))

        linkedin_url = ff["linkedin"]
        github_url = ff["github"]
        portfolio_url = ff["portfolio"]
        other_urls = ff["other_urls"]
        if any([linkedin_url, github_url, portfolio_url, other_urls]):
            obj.links = LinksInfo(
                linkedin=linkedin_url,
                github=github_url,
                portfolio=portfolio_url,
                other=[u for u in other_urls if u not in {linkedin_url, github_url, portfolio_url}][:5],
            )
            if linkedin_url:
                obj.provenance.append(_prov("links.linkedin", "regex"))
            if github_url:
                obj.provenance.append(_prov("links.github", "regex"))

        # ── 3. NLP fields ────────────────────────────────────

        # Name
        name, name_method = self._nlp.extract_name(text, preamble)
        if name:
            obj.full_name = name
            obj.provenance.append(_prov("full_name", name_method))

        # Headline
        headline = self._nlp.extract_headline(preamble, name)
        if headline:
            obj.headline = headline
            obj.provenance.append(_prov("headline", "heuristic"))

        # Summary
        summary_lines = sections.get("summary", [])
        summary = self._nlp.extract_summary(summary_lines)
        if summary:
            obj.summary = summary
            obj.provenance.append(_prov("summary", "nlp_sentence_seg" if self._nlp.nlp_available else "heuristic"))

        # Skills
        skill_lines = sections.get("skills", [])
        skills = self._nlp.extract_skills(skill_lines, text)
        if skills:
            obj.skills = skills
            method = "nlp_phrase_matcher" if self._nlp.nlp_available else "regex_match"
            obj.provenance.append(_prov("skills", method))

        # Fix 6: Location extraction from preamble
        loc_city, loc_region, loc_country = self._nlp.extract_location(preamble, text)
        if any([loc_city, loc_region, loc_country]):
            obj.location = LocationInfo(city=loc_city, region=loc_region, country=loc_country)
            obj.provenance.append(_prov("location", "nlp_gpe" if self._nlp.nlp_available else "regex_heuristic"))

        # Experience
        exp_lines = sections.get("experience", [])
        experience = self._nlp.extract_experience(exp_lines)
        if experience:
            obj.experience = experience
            method = "nlp_ner+regex" if self._nlp.nlp_available else "regex_heuristic"
            obj.provenance.append(_prov("experience", method))

        # Years experience (derived)
        yoe = self._nlp.estimate_years_experience(experience)
        if yoe is not None:
            obj.years_experience = yoe
            obj.provenance.append(_prov("years_experience", "derived_from_dates"))

        # Education
        edu_lines = sections.get("education", [])
        education = self._nlp.extract_education(edu_lines)
        if education:
            obj.education = education
            method = "nlp_ner+regex" if self._nlp.nlp_available else "regex_heuristic"
            obj.provenance.append(_prov("education", method))

        # Certifications
        cert_section_key = next(
            (k for k in sections if "certification" in k or "certificate" in k), None
        )
        if cert_section_key:
            certs = self._nlp.extract_certifications(sections[cert_section_key])
            if certs:
                obj.certifications = certs
                obj.provenance.append(_prov("certifications", "section_parse"))

        # Projects
        proj_section_key = next(
            (k for k in sections if "project" in k), None
        )
        if proj_section_key:
            projects = self._nlp.extract_projects(sections[proj_section_key])
            if projects:
                obj.projects = projects
                obj.provenance.append(_prov("projects", "section_parse"))

        # ── 4. Confidence score ──────────────────────────────
        obj.overall_confidence = self._compute_confidence(obj)

        logger.info(
            "FreeTextBuilder complete — name=%s  emails=%d  skills=%d  "
            "experience=%d  education=%d  confidence=%.2f",
            obj.full_name or "?",
            len(obj.emails),
            len(obj.skills),
            len(obj.experience),
            len(obj.education),
            obj.overall_confidence,
        )
        return obj

    @staticmethod
    def _compute_confidence(obj: CandidateObject) -> float:
        score = 0.0
        if obj.full_name:      score += 0.20
        if obj.emails:         score += 0.20
        if obj.phones:         score += 0.10
        if obj.skills:         score += 0.15
        if obj.experience:     score += 0.20
        if obj.education:      score += 0.10
        if obj.links.linkedin: score += 0.05
        return round(min(score, 1.0), 4)


# ──────────────────────────────────────────────────────────────
# CandidateObjectBuilder  (Module 6 orchestrator)
# ──────────────────────────────────────────────────────────────

class CandidateObjectBuilder:
    """
    Public entry point for Module 6.

    For structured content: returns a list of CandidateObjects (one per row).
    For unstructured content: returns a list with a single CandidateObject.
    """

    def __init__(self) -> None:
        self._cfg = get_config()

    def build(self, parsed: ParsedContent, source_type: str) -> list[CandidateObject]:
        if parsed.data_category == "structured":
            return self._build_from_structured(parsed.content, source_type)
        elif parsed.data_category == "unstructured":
            return [self._build_from_unstructured(parsed.content, source_type, parsed)]
        else:
            raise CandidateBuildError(
                f"Unknown data_category: '{parsed.data_category}'"
            )

    def _build_from_structured(
        self, rows: list[dict], source: str
    ) -> list[CandidateObject]:
        mapper = StructuredFieldMapper(self._cfg)
        objects: list[CandidateObject] = []
        for i, row in enumerate(rows):
            try:
                obj = mapper.map_row(row, source)
                objects.append(obj)
            except Exception as exc:
                logger.error("Failed to map structured row %d: %s", i, exc)
        logger.info(
            "Structured build complete — %d candidates from %d rows",
            len(objects), len(rows),
        )
        return objects

    def _build_from_unstructured(
        self, text: str, source: str, parsed: ParsedContent
    ) -> CandidateObject:
        if not text or not text.strip():
            logger.warning(
                "Parser returned empty content (%s) — "
                "candidate object will be mostly empty. Warning: %s",
                parsed.parser_used,
                parsed.parse_warning or "none",
            )
            obj = CandidateObject()
            if parsed.parse_warning:
                obj.extraction_warnings.append(parsed.parse_warning)
            return obj

        builder = FreeTextBuilder(self._cfg, source)
        obj = builder.build(text)

        if parsed.parse_warning:
            obj.extraction_warnings.append(parsed.parse_warning)

        return obj
