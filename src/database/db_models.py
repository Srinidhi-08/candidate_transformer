"""
src/database/db_models.py
===========================
SQLAlchemy ORM models for PostgreSQL storage (Module 15).

Tables
------
  candidates          – one row per canonical candidate
  candidate_emails    – normalised; one row per email
  candidate_phones    – normalised; one row per phone
  candidate_skills    – one row per skill per candidate
  candidate_experience
  candidate_education
  candidate_certifications
  candidate_projects
  candidate_sources   – source history entries
  referrals           – referral records
  processing_logs     – audit trail
"""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    JSON,
    String,
    Text,
    UniqueConstraint,
    Index,
)
from sqlalchemy.orm import DeclarativeBase, relationship


class Base(DeclarativeBase):
    __mapper_args__ = {"confirm_deleted_rows": False}


def _now():
    return datetime.now(timezone.utc)


# ──────────────────────────────────────────────────────────────
# Candidate (master table)
# ──────────────────────────────────────────────────────────────

class CandidateModel(Base):
    __tablename__ = "candidates"

    candidate_id     = Column(String(32), primary_key=True)
    full_name        = Column(String(255), nullable=True)
    date_of_birth    = Column(String(20), nullable=True)
    gender           = Column(String(50), nullable=True)
    nationality      = Column(String(100), nullable=True)

    # Location (denormalised for query speed)
    city             = Column(String(150), nullable=True)
    region           = Column(String(150), nullable=True)
    country          = Column(String(150), nullable=True)

    # Links
    linkedin_url     = Column(String(500), nullable=True)
    github_url       = Column(String(500), nullable=True)
    portfolio_url    = Column(String(500), nullable=True)

    headline         = Column(String(500), nullable=True)
    summary          = Column(Text, nullable=True)
    years_experience = Column(Float, nullable=True)

    # Scores
    overall_confidence   = Column(Float, default=0.0)
    confidence_breakdown = Column(JSON, nullable=True)

    # Merge info
    is_merged   = Column(Boolean, default=False)
    merged_from = Column(JSON, nullable=True)   # list of candidate_ids

    created_at = Column(DateTime(timezone=True), default=_now)
    updated_at = Column(DateTime(timezone=True), default=_now, onupdate=_now)

    # Relationships
    emails           = relationship("CandidateEmailModel",         back_populates="candidate", cascade="all, delete-orphan")
    phones           = relationship("CandidatePhoneModel",         back_populates="candidate", cascade="all, delete-orphan")
    skills           = relationship("CandidateSkillModel",         back_populates="candidate", cascade="all, delete-orphan")
    experience       = relationship("CandidateExperienceModel",    back_populates="candidate", cascade="all, delete-orphan")
    education        = relationship("CandidateEducationModel",     back_populates="candidate", cascade="all, delete-orphan")
    certifications   = relationship("CandidateCertificationModel", back_populates="candidate", cascade="all, delete-orphan")
    projects         = relationship("CandidateProjectModel",       back_populates="candidate", cascade="all, delete-orphan")
    sources          = relationship("CandidateSourceModel",        back_populates="candidate", cascade="all, delete-orphan")
    referrals        = relationship("ReferralModel",               back_populates="candidate", cascade="all, delete-orphan")
    processing_logs  = relationship("ProcessingLogModel",          back_populates="candidate", cascade="all, delete-orphan")

    __table_args__ = (
        Index("ix_candidates_full_name", "full_name"),
    )


# ──────────────────────────────────────────────────────────────
# Contact tables
# ──────────────────────────────────────────────────────────────

class CandidateEmailModel(Base):
    __tablename__ = "candidate_emails"
    id           = Column(Integer, primary_key=True, autoincrement=True)
    candidate_id = Column(String(32), ForeignKey("candidates.candidate_id"), nullable=False)
    email        = Column(String(320), nullable=False)
    candidate    = relationship("CandidateModel", back_populates="emails")
    __table_args__ = (
        Index("ix_candidate_emails_email", "email"),
        UniqueConstraint("candidate_id", "email", name="uq_candidate_email"),
    )


class CandidatePhoneModel(Base):
    __tablename__ = "candidate_phones"
    id           = Column(Integer, primary_key=True, autoincrement=True)
    candidate_id = Column(String(32), ForeignKey("candidates.candidate_id"), nullable=False)
    phone        = Column(String(50), nullable=False)
    candidate    = relationship("CandidateModel", back_populates="phones")
    __table_args__ = (
        Index("ix_candidate_phones_phone", "phone"),
    )


# ──────────────────────────────────────────────────────────────
# Skill
# ──────────────────────────────────────────────────────────────

class CandidateSkillModel(Base):
    __tablename__ = "candidate_skills"
    id           = Column(Integer, primary_key=True, autoincrement=True)
    candidate_id = Column(String(32), ForeignKey("candidates.candidate_id"), nullable=False)
    name         = Column(String(200), nullable=False)
    confidence   = Column(Float, default=1.0)
    sources      = Column(JSON, nullable=True)
    candidate    = relationship("CandidateModel", back_populates="skills")
    __table_args__ = (
        UniqueConstraint("candidate_id", "name", name="uq_candidate_skill"),
    )


# ──────────────────────────────────────────────────────────────
# Experience
# ──────────────────────────────────────────────────────────────

class CandidateExperienceModel(Base):
    __tablename__ = "candidate_experience"
    id           = Column(Integer, primary_key=True, autoincrement=True)
    candidate_id = Column(String(32), ForeignKey("candidates.candidate_id"), nullable=False)
    company      = Column(String(300), nullable=True)
    title        = Column(String(300), nullable=True)
    start        = Column(String(20), nullable=True)
    end          = Column(String(20), nullable=True)
    location     = Column(String(200), nullable=True)
    description  = Column(Text, nullable=True)
    candidate    = relationship("CandidateModel", back_populates="experience")


# ──────────────────────────────────────────────────────────────
# Education
# ──────────────────────────────────────────────────────────────

class CandidateEducationModel(Base):
    __tablename__ = "candidate_education"
    id             = Column(Integer, primary_key=True, autoincrement=True)
    candidate_id   = Column(String(32), ForeignKey("candidates.candidate_id"), nullable=False)
    institution    = Column(String(300), nullable=True)
    degree         = Column(String(200), nullable=True)
    field_of_study = Column(String(200), nullable=True)
    start_year     = Column(Integer, nullable=True)
    end_year       = Column(Integer, nullable=True)
    grade          = Column(String(50), nullable=True)
    candidate      = relationship("CandidateModel", back_populates="education")


# ──────────────────────────────────────────────────────────────
# Certifications
# ──────────────────────────────────────────────────────────────

class CandidateCertificationModel(Base):
    __tablename__ = "candidate_certifications"
    id           = Column(Integer, primary_key=True, autoincrement=True)
    candidate_id = Column(String(32), ForeignKey("candidates.candidate_id"), nullable=False)
    name         = Column(String(300), nullable=True)
    issuer       = Column(String(200), nullable=True)
    date         = Column(String(20), nullable=True)
    url          = Column(String(500), nullable=True)
    candidate    = relationship("CandidateModel", back_populates="certifications")


# ──────────────────────────────────────────────────────────────
# Projects
# ──────────────────────────────────────────────────────────────

class CandidateProjectModel(Base):
    __tablename__ = "candidate_projects"
    id           = Column(Integer, primary_key=True, autoincrement=True)
    candidate_id = Column(String(32), ForeignKey("candidates.candidate_id"), nullable=False)
    name         = Column(String(300), nullable=True)
    description  = Column(Text, nullable=True)
    technologies = Column(JSON, nullable=True)
    url          = Column(String(500), nullable=True)
    candidate    = relationship("CandidateModel", back_populates="projects")


# ──────────────────────────────────────────────────────────────
# Source history
# ──────────────────────────────────────────────────────────────

class CandidateSourceModel(Base):
    __tablename__ = "candidate_sources"
    id           = Column(Integer, primary_key=True, autoincrement=True)
    candidate_id = Column(String(32), ForeignKey("candidates.candidate_id"), nullable=False)
    source_type  = Column(String(100), nullable=False)
    resolved_by  = Column(String(100), nullable=True)
    upload_time  = Column(DateTime(timezone=True), nullable=True)
    file_name    = Column(String(500), nullable=True)
    file_type    = Column(String(50), nullable=True)
    confidence   = Column(Float, default=0.0)
    candidate    = relationship("CandidateModel", back_populates="sources")
    __table_args__ = (
        Index("ix_candidate_sources_source_type", "source_type"),
    )


# ──────────────────────────────────────────────────────────────
# Referrals
# ──────────────────────────────────────────────────────────────

class ReferralModel(Base):
    __tablename__ = "referrals"
    id             = Column(Integer, primary_key=True, autoincrement=True)
    candidate_id   = Column(String(32), ForeignKey("candidates.candidate_id"), nullable=False)
    referral_code  = Column(String(200), nullable=True)
    referred_by    = Column(String(500), nullable=True)
    is_verified    = Column(Boolean, default=False)
    created_at     = Column(DateTime(timezone=True), default=_now)
    candidate      = relationship("CandidateModel", back_populates="referrals")


# ──────────────────────────────────────────────────────────────
# Processing logs
# ──────────────────────────────────────────────────────────────

class ProcessingLogModel(Base):
    __tablename__ = "processing_logs"
    id           = Column(Integer, primary_key=True, autoincrement=True)
    candidate_id = Column(String(32), ForeignKey("candidates.candidate_id"), nullable=True)
    context_id   = Column(String(32), nullable=True)
    module       = Column(String(100), nullable=True)
    level        = Column(String(20), nullable=True)
    message      = Column(Text, nullable=True)
    timestamp    = Column(DateTime(timezone=True), default=_now)
    candidate    = relationship("CandidateModel", back_populates="processing_logs")
