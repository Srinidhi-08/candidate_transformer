"""
pipeline.py
============
Main Pipeline Orchestrator

Coordinates all 15 modules across 3 phases for a single file.

Usage (programmatic):
    from pipeline import Pipeline
    pipeline = Pipeline()
    output = pipeline.run(
        file_path="resume.pdf",
        upload_channel="resume_upload",
        schema="ats",
    )

Phase 1  (Modules 1–7):  Input → ProcessingContext
Phase 2  (Modules 8–14): ProcessingContext → CanonicalCandidateRecord
Phase 3  (Projection):   CanonicalCandidateRecord → Final JSON

The DB write (Module 15) is optional — pass `persist=False` to skip it.
"""

from __future__ import annotations

import copy
import logging
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from src.core.config_loader import get_config
from src.core.exceptions import CandidateBuildError, PipelineBaseError
from src.core.models import (
    CanonicalCandidateRecord,
    CandidateObject,
    ProcessingContext,
)

# Phase 1
from src.phase1.module1_input import InputCollector
from src.phase1.module2_validator import InputValidationPipeline
from src.phase1.module3_source import SourceIdentificationPipeline
from src.phase1.module4_parsers import ParserSelectionPipeline
from src.phase1.module6_builder import CandidateObjectBuilder
from src.phase1.module7_context import ProcessingContextFactory

# Phase 2
from src.phase2.module8_data_validator import DataValidator
from src.phase2.module9_missing_values import MissingValueManager
from src.phase2.module10_normalizer import Normalizer
from src.phase2.module11_matcher import CandidateMatcher
from src.phase2.module12_merge import MergeEngine
from src.phase2.module13_conflict import ConflictResolver
from src.phase2.module14_confidence import ConfidenceEngine

# Phase 3
from src.phase3.projection_layer import ProjectionLayer

logger = logging.getLogger("pipeline")


class Pipeline:
    """
    Single-file pipeline.  Initialise once and call `run()` per file.

    Parameters
    ----------
    persist : bool
        Whether to write the final canonical record to PostgreSQL.
        Defaults to True.  Set False for offline / test use.
    """

    def __init__(self, persist: bool = True) -> None:
        cfg = get_config()

        # Phase 1
        self._collector = InputCollector()
        self._validator = InputValidationPipeline()
        self._source_pipeline = SourceIdentificationPipeline()
        self._parser_pipeline = ParserSelectionPipeline()
        self._builder = CandidateObjectBuilder()
        self._context_factory = ProcessingContextFactory()

        # Phase 2
        self._data_validator = DataValidator()
        self._missing_mgr = MissingValueManager()
        self._normalizer = Normalizer()
        self._matcher = CandidateMatcher()
        self._merge_engine = MergeEngine()
        self._conflict_resolver = ConflictResolver()
        self._confidence_engine = ConfidenceEngine()

        # Phase 3
        self._projection = ProjectionLayer()

        # DB (lazy)
        self._persist = persist
        self._repo = None
        self._existing_records: list[CanonicalCandidateRecord] = []

        # Thread safety — protects _existing_records during parallel batch runs
        self._lock = threading.Lock()

        if persist:
            try:
                from src.database.repository import CandidateRepository
                self._repo = CandidateRepository()
                self._repo.create_tables()
                self._existing_records = self._repo.get_all()
            except Exception as exc:
                logger.warning(
                    "DB connection failed — running without persistence: %s", exc
                )
                self._persist = False

    # ──────────────────────────────────────────────────────────
    # Public API
    # ──────────────────────────────────────────────────────────

    def run(
        self,
        file_path: str,
        upload_channel: str | None = None,
        source_hint: str | None = None,
        request_metadata: dict | None = None,
        referral_code: str | None = None,
        referred_by: str | None = None,
        schema: str | None = None,
    ) -> dict:
        """
        Full pipeline for one file.
        Returns the final projected JSON dict.
        """
        logger.info("=" * 60)
        logger.info("PIPELINE START: %s", file_path)

        # ── PHASE 1 ──────────────────────────────────────────
        ctx = self._run_phase1(
            file_path, upload_channel, source_hint,
            request_metadata, referral_code, referred_by,
        )

        if not ctx.is_healthy():
            logger.error("Phase 1 failed — aborting pipeline.")
            return {"error": ctx.errors, "context_id": ctx.context_id}

        # ── PHASE 2 ──────────────────────────────────────────
        canonical = self._run_phase2(ctx)
        ctx.canonical_record = canonical
        ctx.phase2_complete = True

        # ── Persist (Module 15) ──────────────────────────────────────
        if self._persist and self._repo:
            try:
                self._repo.save(canonical, ctx.context_id)
                # Lock protects the shared in-memory record list during parallel runs
                with self._lock:
                    self._existing_records = [
                        r for r in self._existing_records
                        if r.candidate_id != canonical.candidate_id
                    ] + [canonical]
            except Exception as exc:
                logger.error("DB persist failed: %s", exc)
                ctx.add_error(f"DB persist failed: {exc}")

        # ── PHASE 3 ──────────────────────────────────────────
        output = self._projection.project(canonical, schema)

        logger.info(
            "PIPELINE COMPLETE: %s → candidate_id=%s confidence=%.2f",
            file_path, canonical.candidate_id, canonical.overall_confidence,
        )
        return output

    def run_batch(
        self,
        file_paths: list[str],
        upload_channel: str | None = None,
        schema: str | None = None,
        workers: int = 4,
    ) -> list[dict]:
        """
        Process multiple files in parallel using a thread pool.

        Parameters
        ----------
        file_paths : list[str]
            Paths to all files to process.
        upload_channel : str, optional
            Upload channel applied to every file.
        schema : str, optional
            Projection schema for the output.
        workers : int
            Number of parallel threads (default: 4).
            Set to 1 to process sequentially.

        Returns
        -------
        list[dict]
            One result dict per file, in the same order as file_paths.
            Each dict has an extra ``_meta`` key:
              {
                "_meta": { "file": "resume.pdf", "status": "ok" | "error",
                           "error": null | "<message>" }
                ...canonical fields...
              }
        """
        # Cap workers to number of files — no point spinning up 8 threads for 2 files
        effective_workers = min(workers, len(file_paths))
        results: dict[str, dict] = {}  # keyed by file_path to preserve order

        logger.info(
            "Batch starting: %d files | %d workers",
            len(file_paths), effective_workers,
        )

        def _run_one(fp: str) -> tuple[str, dict]:
            """Worker function: run pipeline for a single file."""
            try:
                output = self.run(
                    file_path=fp,
                    upload_channel=upload_channel,
                    schema=schema,
                )
                output["_meta"] = {
                    "file": Path(fp).name,
                    "status": "ok",
                    "error": None,
                }
                return fp, output
            except Exception as exc:
                logger.error("Batch: failed on %s — %s", fp, exc)
                return fp, {
                    "_meta": {
                        "file": Path(fp).name,
                        "status": "error",
                        "error": str(exc),
                    }
                }

        with ThreadPoolExecutor(max_workers=effective_workers) as pool:
            futures = {pool.submit(_run_one, fp): fp for fp in file_paths}
            for future in as_completed(futures):
                fp, result = future.result()
                results[fp] = result

        # Return in original order
        ordered = [results[fp] for fp in file_paths]

        ok_count    = sum(1 for r in ordered if r["_meta"]["status"] == "ok")
        error_count = len(ordered) - ok_count
        logger.info(
            "Batch complete: %d/%d succeeded, %d failed",
            ok_count, len(ordered), error_count,
        )
        return ordered

    # ──────────────────────────────────────────────────────────
    # Phase 1
    # ──────────────────────────────────────────────────────────

    def _run_phase1(
        self, file_path, upload_channel, source_hint,
        request_metadata, referral_code, referred_by,
    ) -> ProcessingContext:

        # Module 1 – collect
        bundle = self._collector.collect(
            file_path=file_path,
            upload_channel=upload_channel,
            source_hint=source_hint,
            request_metadata=request_metadata or {},
            referral_code=referral_code,
            referred_by=referred_by,
        )

        # Module 2 – validate & classify
        file_meta = self._validator.run(bundle)
        if not file_meta.validation_status:
            ctx = ProcessingContext()
            ctx.input_bundle = bundle
            ctx.file_metadata = file_meta
            ctx.add_error(f"Validation failed: {file_meta.validation_message}")
            return ctx

        # Module 3 – source + referral
        source_meta, referral_info = self._source_pipeline.run(
            bundle, file_meta.detected_file_type
        )

        # Module 4 – parse
        try:
            parsed = self._parser_pipeline.run(file_meta)
        except PipelineBaseError as exc:
            ctx = ProcessingContext()
            ctx.input_bundle = bundle
            ctx.file_metadata = file_meta
            ctx.source_metadata = source_meta
            ctx.referral_info = referral_info
            ctx.add_error(f"Parsing failed: {exc.message}")
            return ctx

        # Module 5 – classification is already in file_meta.data_category (done in Module 2)

        # Module 6 – build candidate object(s)
        try:
            candidates = self._builder.build(parsed, source_meta.source_type)
        except CandidateBuildError as exc:
            ctx = ProcessingContext()
            ctx.input_bundle = bundle
            ctx.file_metadata = file_meta
            ctx.source_metadata = source_meta
            ctx.referral_info = referral_info
            ctx.parsed_content = parsed
            ctx.add_error(f"Candidate build failed: {exc.message}")
            return ctx

        # Use the first candidate (for structured multi-row, caller should use run_batch)
        candidate = candidates[0] if candidates else CandidateObject()

        # Module 7 – processing context
        ctx = self._context_factory.create(
            input_bundle=bundle,
            file_metadata=file_meta,
            source_metadata=source_meta,
            referral_info=referral_info,
            parsed_content=parsed,
            candidate_object=candidate,
        )

        if parsed.parse_warning:
            ctx.log("module4_parsers", "WARNING", parsed.parse_warning)

        return ctx

    # ──────────────────────────────────────────────────────────
    # Phase 2
    # ──────────────────────────────────────────────────────────

    def _run_phase2(self, ctx: ProcessingContext) -> CanonicalCandidateRecord:
        # Module 8 – data validation
        validation_issues = self._data_validator.validate(ctx)

        # Module 9 – missing value audit
        missing = self._missing_mgr.audit(ctx)

        # Module 10 – normalize (works on a copy, not mutating original)
        normalized = self._normalizer.normalize(ctx.candidate_object)
        ctx.candidate_object = normalized  # update context with normalized object

        # Module 11 – matching
        matched_id, match_method = self._matcher.find_match(
            normalized, self._existing_records
        )

        if matched_id:
            existing_record = next(
                (r for r in self._existing_records if r.candidate_id == matched_id), None
            )
        else:
            existing_record = None

        if existing_record and matched_id:
            # Module 12 – merge
            merged_record, conflicts = self._merge_engine.merge(
                existing_record, normalized, ctx.source_metadata
            )

            # Module 13 – conflict resolution
            if conflicts:
                resolved_values = self._conflict_resolver.resolve(
                    conflicts,
                    existing_source=existing_record.source_history[-1].get("source_type", "unknown")
                        if existing_record.source_history else "unknown",
                    incoming_source=ctx.source_metadata.source_type,
                    existing_time=existing_record.updated_at,
                    incoming_time=ctx.source_metadata.upload_time,
                    existing_confidence=existing_record.overall_confidence,
                    incoming_confidence=normalized.overall_confidence,
                )
                for field, val in resolved_values.items():
                    if "." in field:
                        obj_name, sub = field.split(".", 1)
                        sub_obj = getattr(merged_record, obj_name, None)
                        if sub_obj is not None:
                            setattr(sub_obj, sub, val)
                    else:
                        setattr(merged_record, field, val)

            canonical = merged_record
            ctx.log("pipeline", "INFO", f"Merged into existing candidate {matched_id} via {match_method}")
        else:
            # New candidate – promote CandidateObject → CanonicalCandidateRecord
            canonical = self._candidate_to_canonical(normalized, ctx)
            ctx.log("pipeline", "INFO", f"New candidate created: {canonical.candidate_id}")

        # Module 14 – confidence
        canonical.validation_issues = validation_issues
        confidence, breakdown = self._confidence_engine.calculate(
            canonical, validation_issues, match_method, ctx.source_metadata.source_type
        )
        canonical.overall_confidence = confidence
        canonical.confidence_breakdown = breakdown

        return canonical

    @staticmethod
    def _candidate_to_canonical(
        obj: CandidateObject, ctx: ProcessingContext
    ) -> CanonicalCandidateRecord:
        r = CanonicalCandidateRecord(candidate_id=obj.candidate_id)
        r.full_name = obj.full_name
        r.date_of_birth = obj.date_of_birth
        r.gender = obj.gender
        r.nationality = obj.nationality
        r.emails = list(obj.emails)
        r.phones = list(obj.phones)
        r.location = copy.deepcopy(obj.location)
        r.links = copy.deepcopy(obj.links)
        r.headline = obj.headline
        r.summary = obj.summary
        r.years_experience = obj.years_experience
        r.skills = list(obj.skills)
        r.experience = list(obj.experience)
        r.education = list(obj.education)
        r.certifications = list(obj.certifications)
        r.projects = list(obj.projects)
        r.provenance = list(obj.provenance)
        r.source_history = [ctx.source_metadata.to_dict()]
        if ctx.referral_info.has_referral:
            r.referral_history = [ctx.referral_info.to_dict()]
        return r
