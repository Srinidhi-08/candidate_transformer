"""
src/core/models.py
==================
All data models for the pipeline.

Hierarchy
─────────
  InputBundle                 Module 1  –  raw intake
  FileMetadata                Module 2  –  after validation
  SourceMetadata              Module 3  –  source identity
  ReferralInfo                Module 3  –  referral detail
  ParsedContent               Module 4  –  raw parser output
  CandidateObject             Module 6  –  canonical candidate (Phase 1 output)
  ProcessingContext           Module 7  –  temp in-memory envelope
  CanonicalCandidateRecord    Module 15 –  after Phase 2, stored in DB

Sub-models used in CandidateObject / CanonicalCandidateRecord:
  LocationInfo
  LinksInfo
  SkillEntry
  ExperienceEntry
  EducationEntry
  CertificationEntry
  ProjectEntry
  ProvenanceEntry
  ProcessingLog
  ValidationIssue
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any


# ──────────────────────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────────────────────

def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


def _new_id() -> str:
    return uuid.uuid4().hex


# ──────────────────────────────────────────────────────────────
# Module 1 – Input Bundle
# ──────────────────────────────────────────────────────────────

@dataclass(slots=True)
class InputBundle:
    """
    Everything collected at the point of upload / intake.
    No validation has been performed yet.
    """
    file_name: str
    file_path: str
    upload_time: datetime = field(default_factory=_now_utc)
    upload_channel: str | None = None          # declared by caller
    source_hint: str | None = None             # extra caller hint
    request_metadata: dict[str, str] = field(default_factory=dict)
    content_sample: str | None = None          # small text sample for source detection
    referral_code: str | None = None
    referred_by: str | None = None


# ──────────────────────────────────────────────────────────────
# Module 2 – File Metadata (post-validation)
# ──────────────────────────────────────────────────────────────

@dataclass(slots=True)
class FileMetadata:
    file_name: str
    file_path: str
    extension: str
    detected_file_type: str | None         # None if validation failed
    data_category: str | None              # "structured" | "unstructured" | None
    validation_status: bool
    validation_message: str
    file_size_bytes: int = 0
    source_hint: str | None = None

    def to_dict(self) -> dict:
        return {
            "file_name": self.file_name,
            "file_path": self.file_path,
            "extension": self.extension,
            "detected_file_type": self.detected_file_type,
            "data_category": self.data_category,
            "validation_status": self.validation_status,
            "validation_message": self.validation_message,
            "file_size_bytes": self.file_size_bytes,
            "source_hint": self.source_hint,
        }


# ──────────────────────────────────────────────────────────────
# Module 3 – Source & Referral
# ──────────────────────────────────────────────────────────────

@dataclass(slots=True)
class SourceMetadata:
    source_type: str
    resolved_by: str
    upload_time: datetime
    file_name: str
    file_type: str | None
    confidence: float

    def to_dict(self) -> dict:
        return {
            "source_type": self.source_type,
            "resolved_by": self.resolved_by,
            "upload_time": self.upload_time.isoformat(),
            "file_name": self.file_name,
            "file_type": self.file_type,
            "confidence": self.confidence,
        }


@dataclass(slots=True)
class ReferralInfo:
    has_referral: bool
    referral_code: str | None = None
    referred_by: str | None = None
    is_verified: bool = False

    def to_dict(self) -> dict:
        return {
            "has_referral": self.has_referral,
            "referral_code": self.referral_code,
            "referred_by": self.referred_by,
            "is_verified": self.is_verified,
        }


# ──────────────────────────────────────────────────────────────
# Module 4 – Raw Parser Output
# ──────────────────────────────────────────────────────────────

@dataclass(slots=True)
class ParsedContent:
    """
    Immutable snapshot of what a parser returned.

    - `data_category`  : "structured" or "unstructured"
    - `content`        : list[dict] for structured, str for unstructured
    - `parser_used`    : name of the parser class
    - `raw_length`     : len(rows) for structured, len(text) for unstructured
    - `parse_warning`  : e.g. "pypdf not installed, no text extracted"
    """
    data_category: str
    content: Any                    # list[dict] | str
    parser_used: str
    raw_length: int
    parse_warning: str | None = None

    def to_dict(self) -> dict:
        return {
            "data_category": self.data_category,
            "content": self.content if isinstance(self.content, str) else f"[{self.raw_length} rows]",
            "parser_used": self.parser_used,
            "raw_length": self.raw_length,
            "parse_warning": self.parse_warning,
        }


# ──────────────────────────────────────────────────────────────
# Module 6 – Candidate Object sub-models
# ──────────────────────────────────────────────────────────────

@dataclass(slots=True)
class LocationInfo:
    city: str | None = None
    region: str | None = None
    country: str | None = None

    def to_dict(self) -> dict:
        return {"city": self.city, "region": self.region, "country": self.country}

    def is_empty(self) -> bool:
        return not any([self.city, self.region, self.country])


@dataclass(slots=True)
class LinksInfo:
    linkedin: str | None = None
    github: str | None = None
    portfolio: str | None = None
    other: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "linkedin": self.linkedin,
            "github": self.github,
            "portfolio": self.portfolio,
            "other": self.other,
        }


@dataclass(slots=True)
class SkillEntry:
    name: str
    confidence: float = 1.0
    sources: list[str] = field(default_factory=list)  # e.g. ["nlp_section", "structured"]

    def to_dict(self) -> dict:
        return {"name": self.name, "confidence": self.confidence, "sources": self.sources}


@dataclass(slots=True)
class ExperienceEntry:
    company: str | None = None
    title: str | None = None
    start: str | None = None
    end: str | None = None
    location: str | None = None
    description: str | None = None

    def to_dict(self) -> dict:
        return {
            "company": self.company,
            "title": self.title,
            "start": self.start,
            "end": self.end,
            "location": self.location,
            "description": self.description,
        }


@dataclass(slots=True)
class EducationEntry:
    institution: str | None = None
    degree: str | None = None
    field_of_study: str | None = None
    start_year: int | None = None
    end_year: int | None = None
    grade: str | None = None

    def to_dict(self) -> dict:
        return {
            "institution": self.institution,
            "degree": self.degree,
            "field_of_study": self.field_of_study,
            "start_year": self.start_year,
            "end_year": self.end_year,
            "grade": self.grade,
        }


@dataclass(slots=True)
class CertificationEntry:
    name: str | None = None
    issuer: str | None = None
    date: str | None = None
    url: str | None = None

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "issuer": self.issuer,
            "date": self.date,
            "url": self.url,
        }


@dataclass(slots=True)
class ProjectEntry:
    name: str | None = None
    description: str | None = None
    technologies: list[str] = field(default_factory=list)
    url: str | None = None

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "description": self.description,
            "technologies": self.technologies,
            "url": self.url,
        }


@dataclass(slots=True)
class ProvenanceEntry:
    """Records where a specific field value came from and how it was extracted."""
    field: str
    source: str                # e.g. "resume_upload", "csv_bulk_import"
    method: str                # e.g. "regex", "nlp_ner", "direct_mapping", "spacy_matcher"
    confidence: float = 1.0
    raw_value: str | None = None

    def to_dict(self) -> dict:
        return {
            "field": self.field,
            "source": self.source,
            "method": self.method,
            "confidence": self.confidence,
            "raw_value": self.raw_value,
        }


# ──────────────────────────────────────────────────────────────
# Module 6 – Canonical Candidate Object  (Phase 1 output)
# ──────────────────────────────────────────────────────────────

@dataclass(slots=True)
class CandidateObject:
    """
    Single canonical representation of a candidate produced by Phase 1.
    This object is temporary — it lives inside the ProcessingContext
    and is never persisted directly.  Phase 2 transforms it into a
    CanonicalCandidateRecord, which is what gets stored.
    """
    candidate_id: str = field(default_factory=_new_id)

    # Personal
    full_name: str | None = None
    date_of_birth: str | None = None
    gender: str | None = None
    nationality: str | None = None

    # Contact
    emails: list[str] = field(default_factory=list)
    phones: list[str] = field(default_factory=list)
    location: LocationInfo = field(default_factory=LocationInfo)
    links: LinksInfo = field(default_factory=LinksInfo)

    # Professional
    headline: str | None = None
    summary: str | None = None
    years_experience: float | None = None

    # Collections
    skills: list[SkillEntry] = field(default_factory=list)
    experience: list[ExperienceEntry] = field(default_factory=list)
    education: list[EducationEntry] = field(default_factory=list)
    certifications: list[CertificationEntry] = field(default_factory=list)
    projects: list[ProjectEntry] = field(default_factory=list)

    # Metadata
    provenance: list[ProvenanceEntry] = field(default_factory=list)
    extraction_warnings: list[str] = field(default_factory=list)
    overall_confidence: float = 0.0

    def to_dict(self) -> dict:
        return {
            "candidate_id": self.candidate_id,
            "full_name": self.full_name,
            "date_of_birth": self.date_of_birth,
            "gender": self.gender,
            "nationality": self.nationality,
            "emails": self.emails,
            "phones": self.phones,
            "location": self.location.to_dict(),
            "links": self.links.to_dict(),
            "headline": self.headline,
            "summary": self.summary,
            "years_experience": self.years_experience,
            "skills": [s.to_dict() for s in self.skills],
            "experience": [e.to_dict() for e in self.experience],
            "education": [e.to_dict() for e in self.education],
            "certifications": [c.to_dict() for c in self.certifications],
            "projects": [p.to_dict() for p in self.projects],
            "provenance": [p.to_dict() for p in self.provenance],
            "extraction_warnings": self.extraction_warnings,
            "overall_confidence": self.overall_confidence,
        }


# ──────────────────────────────────────────────────────────────
# Module 7 – Processing Context (temporary, in-memory envelope)
# ──────────────────────────────────────────────────────────────

@dataclass
class ProcessingLog:
    module: str
    level: str          # INFO | WARNING | ERROR
    message: str
    timestamp: datetime = field(default_factory=_now_utc)

    def to_dict(self) -> dict:
        return {
            "module": self.module,
            "level": self.level,
            "message": self.message,
            "timestamp": self.timestamp.isoformat(),
        }


@dataclass
class ProcessingContext:
    """
    The single in-memory envelope that travels through the entire
    pipeline.  Every module reads from it and writes back to it.
    Nothing is persisted to the database until Phase 2 is complete.
    """
    context_id: str = field(default_factory=_new_id)
    created_at: datetime = field(default_factory=_now_utc)

    # Phase 1 outputs (progressively populated)
    input_bundle: InputBundle | None = None
    file_metadata: FileMetadata | None = None
    source_metadata: SourceMetadata | None = None
    referral_info: ReferralInfo | None = None
    parsed_content: ParsedContent | None = None
    candidate_object: CandidateObject | None = None

    # Phase 2 outputs
    canonical_record: "CanonicalCandidateRecord | None" = None

    # Audit
    processing_logs: list[ProcessingLog] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    phase1_complete: bool = False
    phase2_complete: bool = False

    def log(self, module: str, level: str, message: str) -> None:
        self.processing_logs.append(ProcessingLog(module=module, level=level, message=message))

    def add_error(self, error: str) -> None:
        self.errors.append(error)
        self.processing_logs.append(ProcessingLog(module="pipeline", level="ERROR", message=error))

    def is_healthy(self) -> bool:
        return len(self.errors) == 0

    def to_dict(self) -> dict:
        return {
            "context_id": self.context_id,
            "created_at": self.created_at.isoformat(),
            "file_metadata": self.file_metadata.to_dict() if self.file_metadata else None,
            "source_metadata": self.source_metadata.to_dict() if self.source_metadata else None,
            "referral_info": self.referral_info.to_dict() if self.referral_info else None,
            "parsed_content": self.parsed_content.to_dict() if self.parsed_content else None,
            "candidate_object": self.candidate_object.to_dict() if self.candidate_object else None,
            "processing_logs": [l.to_dict() for l in self.processing_logs],
            "errors": self.errors,
            "phase1_complete": self.phase1_complete,
            "phase2_complete": self.phase2_complete,
        }


# ──────────────────────────────────────────────────────────────
# Phase 2 – Validation Issues
# ──────────────────────────────────────────────────────────────

@dataclass(slots=True)
class ValidationIssue:
    field: str
    value: Any
    reason: str
    severity: str = "WARNING"   # WARNING | ERROR

    def to_dict(self) -> dict:
        return {
            "field": self.field,
            "value": str(self.value),
            "reason": self.reason,
            "severity": self.severity,
        }


# ──────────────────────────────────────────────────────────────
# Phase 2 / Module 15 – Canonical Candidate Record (DB-ready)
# ──────────────────────────────────────────────────────────────

@dataclass
class CanonicalCandidateRecord:
    """
    The immutable truth about a candidate after Phase 2.
    Stored in PostgreSQL.  Never mutated by the Projection Layer.
    """
    candidate_id: str = field(default_factory=_new_id)
    created_at: datetime = field(default_factory=_now_utc)
    updated_at: datetime = field(default_factory=_now_utc)

    # Personal
    full_name: str | None = None
    date_of_birth: str | None = None
    gender: str | None = None
    nationality: str | None = None

    # Contact (all normalized)
    emails: list[str] = field(default_factory=list)
    phones: list[str] = field(default_factory=list)
    location: LocationInfo = field(default_factory=LocationInfo)
    links: LinksInfo = field(default_factory=LinksInfo)

    # Professional
    headline: str | None = None
    summary: str | None = None
    years_experience: float | None = None

    # Collections
    skills: list[SkillEntry] = field(default_factory=list)
    experience: list[ExperienceEntry] = field(default_factory=list)
    education: list[EducationEntry] = field(default_factory=list)
    certifications: list[CertificationEntry] = field(default_factory=list)
    projects: list[ProjectEntry] = field(default_factory=list)

    # Metadata
    source_history: list[dict] = field(default_factory=list)  # list of SourceMetadata.to_dict()
    referral_history: list[dict] = field(default_factory=list)
    provenance: list[ProvenanceEntry] = field(default_factory=list)
    validation_issues: list[ValidationIssue] = field(default_factory=list)

    # Scores
    overall_confidence: float = 0.0
    confidence_breakdown: dict[str, float] = field(default_factory=dict)

    # Merge tracking
    is_merged: bool = False
    merged_from: list[str] = field(default_factory=list)  # candidate_ids

    def to_dict(self) -> dict:
        return {
            "candidate_id": self.candidate_id,
            "created_at": self.created_at.isoformat(),
            "updated_at": self.updated_at.isoformat(),
            "full_name": self.full_name,
            "date_of_birth": self.date_of_birth,
            "gender": self.gender,
            "nationality": self.nationality,
            "emails": self.emails,
            "phones": self.phones,
            "location": self.location.to_dict(),
            "links": self.links.to_dict(),
            "headline": self.headline,
            "summary": self.summary,
            "years_experience": self.years_experience,
            "skills": [s.to_dict() for s in self.skills],
            "experience": [e.to_dict() for e in self.experience],
            "education": [e.to_dict() for e in self.education],
            "certifications": [c.to_dict() for c in self.certifications],
            "projects": [p.to_dict() for p in self.projects],
            "source_history": self.source_history,
            "referral_history": self.referral_history,
            "provenance": [p.to_dict() for p in self.provenance],
            "validation_issues": [v.to_dict() for v in self.validation_issues],
            "overall_confidence": self.overall_confidence,
            "confidence_breakdown": self.confidence_breakdown,
            "is_merged": self.is_merged,
            "merged_from": self.merged_from,
        }
