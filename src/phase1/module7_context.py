"""
src/phase1/module7_context.py
==============================
MODULE 7 – Candidate Processing Context

Creates and manages the single in-memory ProcessingContext that
travels through every module in the pipeline.

Nothing is persisted to the database in this module.
The context is a live, mutable object that every subsequent module
reads from and writes back to.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from src.core.models import (
    CandidateObject,
    FileMetadata,
    InputBundle,
    ParsedContent,
    ProcessingContext,
    ReferralInfo,
    SourceMetadata,
)

logger = logging.getLogger("phase1.module7_context")


class ProcessingContextFactory:
    """
    Creates the initial ProcessingContext after all Phase 1 modules
    have run and attaches their outputs to it.
    """

    def create(
        self,
        input_bundle: InputBundle,
        file_metadata: FileMetadata,
        source_metadata: SourceMetadata,
        referral_info: ReferralInfo,
        parsed_content: ParsedContent,
        candidate_object: CandidateObject,
    ) -> ProcessingContext:

        ctx = ProcessingContext()
        ctx.input_bundle = input_bundle
        ctx.file_metadata = file_metadata
        ctx.source_metadata = source_metadata
        ctx.referral_info = referral_info
        ctx.parsed_content = parsed_content
        ctx.candidate_object = candidate_object
        ctx.phase1_complete = True

        ctx.log("module7_context", "INFO", "ProcessingContext created — Phase 1 complete.")
        ctx.log(
            "module7_context",
            "INFO",
            (
                f"Summary: file={file_metadata.file_name} "
                f"type={file_metadata.detected_file_type} "
                f"source={source_metadata.source_type} "
                f"name={candidate_object.full_name or '?'} "
                f"emails={len(candidate_object.emails)} "
                f"skills={len(candidate_object.skills)} "
                f"experience={len(candidate_object.experience)} "
                f"confidence={candidate_object.overall_confidence:.2f}"
            ),
        )

        logger.info(
            "ProcessingContext [%s] created — %s → %s (source=%s, confidence=%.2f)",
            ctx.context_id,
            file_metadata.file_name,
            file_metadata.detected_file_type,
            source_metadata.source_type,
            candidate_object.overall_confidence,
        )
        return ctx
