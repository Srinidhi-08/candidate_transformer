"""
src/database/repository.py
============================
MODULE 15 – Canonical Record & Database (Repository Layer)

Responsibilities:
  - Create / update PostgreSQL tables (create_all).
  - Persist a CanonicalCandidateRecord inside a single transaction.
  - Look up existing records for candidate matching.
  - All DB interaction happens here — no other module touches the DB.

DB credentials can be overridden via environment variables:
  DB_HOST, DB_PORT, DB_NAME, DB_USER, DB_PASSWORD
This avoids storing the password in pipeline_config.yaml for production use.
"""
from __future__ import annotations

import logging
import os
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Generator
from urllib.parse import quote_plus

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from src.core.config_loader import get_config
from src.core.exceptions import DatabaseError, RecordNotFoundError
from src.core.models import (
    CanonicalCandidateRecord,
    CertificationEntry,
    EducationEntry,
    ExperienceEntry,
    LinksInfo,
    LocationInfo,
    ProjectEntry,
    SkillEntry,
)
from src.database.db_models import (
    Base,
    CandidateCertificationModel,
    CandidateEducationModel,
    CandidateEmailModel,
    CandidateExperienceModel,
    CandidateModel,
    CandidatePhoneModel,
    CandidateProjectModel,
    CandidateSkillModel,
    CandidateSourceModel,
    ProcessingLogModel,
    ReferralModel,
)

logger = logging.getLogger("database.repository")



def _build_dsn(db_cfg: dict) -> str:
    """
    Build a SQLAlchemy DSN.
    Environment variables take priority over pipeline_config.yaml values.
    The password is URL-encoded so special characters (!, @, #, etc.) don't
    break DSN parsing — this was the root cause of the 'no password supplied' error.
    """
    host     = os.environ.get("DB_HOST", db_cfg.get("host", "localhost"))
    port     = os.environ.get("DB_PORT", str(db_cfg.get("port", 5432)))
    name     = os.environ.get("DB_NAME", db_cfg.get("name", "candidate_db"))
    user     = os.environ.get("DB_USER", db_cfg.get("user", "postgres"))
    password = os.environ.get("DB_PASSWORD", str(db_cfg.get("password", "")))

    # URL-encode the password to handle special characters safely
    encoded_password = quote_plus(password)

    dsn = f"postgresql+psycopg2://{user}:{encoded_password}@{host}:{port}/{name}"
    logger.debug("DB DSN constructed for host=%s port=%s db=%s user=%s", host, port, name, user)
    return dsn

class CandidateRepository:
    """
    All database operations for candidates.
    Uses a single SQLAlchemy engine (connection pool).
    """

    def __init__(self) -> None:
        cfg = get_config()
        db_cfg = cfg.database.raw()
        dsn = _build_dsn(db_cfg)

        self._engine = create_engine(
            dsn,
            pool_size=db_cfg.get("pool_size", 10),
            max_overflow=db_cfg.get("max_overflow", 20),
            echo=db_cfg.get("echo", False),
        )
        self._Session = sessionmaker(bind=self._engine)
        logger.info(
            "Database engine created: %s@%s/%s",
            db_cfg.get("user"), db_cfg.get("host"), db_cfg.get("name"),
        )

    def create_tables(self) -> None:
        """Create all tables if they don't exist."""
        Base.metadata.create_all(self._engine)
        logger.info("Database tables created / verified.")

    @contextmanager
    def _session(self) -> Generator[Session, None, None]:
        session = self._Session()
        try:
            yield session
            session.commit()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    # ──────────────────────────────────────────────────────────
    # Write
    # ──────────────────────────────────────────────────────────

    def save(self, record: CanonicalCandidateRecord, context_id: str | None = None) -> str:
        """
        Upsert the canonical record.  If the candidate_id already exists,
        replace it (for the merge case).  Returns the stored candidate_id.
        """
        try:
            with self._session() as session:
                existing = session.get(CandidateModel, record.candidate_id)
                if existing:
                    session.delete(existing)
                    session.flush()

                model = self._to_model(record)
                session.add(model)

                # Persist processing logs
                if context_id:
                    for src in record.source_history:
                        session.add(ProcessingLogModel(
                            candidate_id=record.candidate_id,
                            context_id=context_id,
                            module="pipeline",
                            level="INFO",
                            message=f"Source ingested: {src.get('source_type')} via {src.get('resolved_by')}",
                            timestamp=datetime.now(timezone.utc),
                        ))

        except Exception as exc:
            raise DatabaseError(f"Failed to save candidate {record.candidate_id}: {exc}") from exc

        logger.info("Saved candidate to DB: %s (%s)", record.candidate_id, record.full_name)
        return record.candidate_id

    # ──────────────────────────────────────────────────────────
    # Read
    # ──────────────────────────────────────────────────────────

    def get_by_id(self, candidate_id: str) -> CanonicalCandidateRecord:
        with self._session() as session:
            model = session.get(CandidateModel, candidate_id)
            if model is None:
                raise RecordNotFoundError(f"Candidate not found: {candidate_id}")
            return self._from_model(model)

    def get_all(self, limit: int = 1000) -> list[CanonicalCandidateRecord]:
        with self._session() as session:
            models = session.query(CandidateModel).limit(limit).all()
            return [self._from_model(m) for m in models]

    def search_by_email(self, email: str) -> list[CanonicalCandidateRecord]:
        with self._session() as session:
            rows = (
                session.query(CandidateModel)
                .join(CandidateEmailModel)
                .filter(CandidateEmailModel.email == email.lower())
                .all()
            )
            return [self._from_model(r) for r in rows]

    # ──────────────────────────────────────────────────────────
    # Model ↔ Domain conversion
    # ──────────────────────────────────────────────────────────

    @staticmethod
    def _to_model(r: CanonicalCandidateRecord) -> CandidateModel:
        model = CandidateModel(
            candidate_id=r.candidate_id,
            full_name=r.full_name,
            date_of_birth=r.date_of_birth,
            gender=r.gender,
            nationality=r.nationality,
            city=r.location.city,
            region=r.location.region,
            country=r.location.country,
            linkedin_url=r.links.linkedin,
            github_url=r.links.github,
            portfolio_url=r.links.portfolio,
            headline=r.headline,
            summary=r.summary,
            years_experience=r.years_experience,
            overall_confidence=r.overall_confidence,
            confidence_breakdown=r.confidence_breakdown,
            is_merged=r.is_merged,
            merged_from=r.merged_from,
        )

        model.emails = [CandidateEmailModel(email=e) for e in r.emails]
        model.phones = [CandidatePhoneModel(phone=p) for p in r.phones]
        model.skills = [
            CandidateSkillModel(name=s.name, confidence=s.confidence, sources=s.sources)
            for s in r.skills
        ]
        model.experience = [
            CandidateExperienceModel(
                company=e.company, title=e.title, start=e.start, end=e.end,
                location=e.location, description=e.description,
            )
            for e in r.experience
        ]
        model.education = [
            CandidateEducationModel(
                institution=e.institution, degree=e.degree,
                field_of_study=e.field_of_study, start_year=e.start_year,
                end_year=e.end_year, grade=e.grade,
            )
            for e in r.education
        ]
        model.certifications = [
            CandidateCertificationModel(
                name=c.name, issuer=c.issuer, date=c.date, url=c.url
            )
            for c in r.certifications
        ]
        model.projects = [
            CandidateProjectModel(
                name=p.name, description=p.description,
                technologies=p.technologies, url=p.url,
            )
            for p in r.projects
        ]
        model.sources = [
            CandidateSourceModel(
                source_type=s.get("source_type"),
                resolved_by=s.get("resolved_by"),
                file_name=s.get("file_name"),
                file_type=s.get("file_type"),
                confidence=s.get("confidence", 0.0),
            )
            for s in r.source_history
        ]
        return model

    @staticmethod
    def _from_model(m: CandidateModel) -> CanonicalCandidateRecord:
        r = CanonicalCandidateRecord(candidate_id=m.candidate_id)
        r.full_name = m.full_name
        r.date_of_birth = m.date_of_birth
        r.gender = m.gender
        r.nationality = m.nationality
        r.location = LocationInfo(city=m.city, region=m.region, country=m.country)
        r.links = LinksInfo(
            linkedin=m.linkedin_url,
            github=m.github_url,
            portfolio=m.portfolio_url,
        )
        r.headline = m.headline
        r.summary = m.summary
        r.years_experience = m.years_experience
        r.overall_confidence = m.overall_confidence or 0.0
        r.confidence_breakdown = m.confidence_breakdown or {}
        r.is_merged = m.is_merged or False
        r.merged_from = m.merged_from or []

        r.emails = [e.email for e in m.emails]
        r.phones = [p.phone for p in m.phones]
        r.skills = [
            SkillEntry(name=s.name, confidence=s.confidence, sources=s.sources or [])
            for s in m.skills
        ]
        r.experience = [
            ExperienceEntry(
                company=e.company, title=e.title, start=e.start, end=e.end,
                location=e.location, description=e.description,
            )
            for e in m.experience
        ]
        r.education = [
            EducationEntry(
                institution=e.institution, degree=e.degree,
                field_of_study=e.field_of_study, start_year=e.start_year,
                end_year=e.end_year, grade=e.grade,
            )
            for e in m.education
        ]
        r.certifications = [
            CertificationEntry(name=c.name, issuer=c.issuer, date=c.date, url=c.url)
            for c in m.certifications
        ]
        r.projects = [
            ProjectEntry(
                name=p.name, description=p.description,
                technologies=p.technologies or [], url=p.url,
            )
            for p in m.projects
        ]
        r.source_history = [
            {
                "source_type": s.source_type,
                "resolved_by": s.resolved_by,
                "file_name": s.file_name,
                "file_type": s.file_type,
                "confidence": s.confidence,
            }
            for s in m.sources
        ]
        return r
