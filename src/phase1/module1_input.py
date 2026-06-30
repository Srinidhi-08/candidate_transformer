"""
src/phase1/module1_input.py
============================
MODULE 1 – Candidate Input Collector

Responsibility
--------------
Accept candidate information from any supported channel and package
it into a typed InputBundle.  No validation, parsing, or extraction
is done here — only intake and structuring of what the caller gave us.

Supported channels (from config):
  resume_upload, ats, linkedin, github, referral,
  csv_bulk_import, api, manual, unknown

Usage
-----
    collector = InputCollector()
    bundle = collector.collect(
        file_path="path/to/resume.pdf",
        upload_channel="resume_upload",
        referral_code="REF-001",
        referred_by="alice@company.com",
    )
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from pathlib import Path

from src.core.config_loader import get_config
from src.core.exceptions import InputCollectionError
from src.core.models import InputBundle

logger = logging.getLogger("phase1.module1_input")


class InputCollector:
    """
    Collects all information available at upload time and returns a
    populated InputBundle.  The caller must supply at minimum a file
    path; everything else is optional.
    """

    def __init__(self) -> None:
        cfg = get_config()
        self._known_channels: set[str] = set(cfg.sources["known_types"])

    # ──────────────────────────────────────────────────────────
    # Public API
    # ──────────────────────────────────────────────────────────

    def collect(
        self,
        file_path: str,
        upload_channel: str | None = None,
        source_hint: str | None = None,
        request_metadata: dict[str, str] | None = None,
        content_sample: str | None = None,
        referral_code: str | None = None,
        referred_by: str | None = None,
        upload_time: datetime | None = None,
    ) -> InputBundle:
        """
        Build and return an InputBundle from the provided arguments.

        Parameters
        ----------
        file_path       : Absolute or relative path to the candidate file.
        upload_channel  : Caller-declared source channel (e.g. "resume_upload").
        source_hint     : Free-text hint used by Module 3 for fallback detection.
        request_metadata: HTTP headers / API request metadata key-value pairs.
        content_sample  : Short content snippet (≤2000 chars) for content-based
                          source detection.
        referral_code   : Referral code if the upload came via an employee referral.
        referred_by     : Identifier of the employee who made the referral.
        upload_time     : Override the upload timestamp (defaults to now UTC).
        """
        if not file_path or not str(file_path).strip():
            raise InputCollectionError("file_path must be a non-empty string.")

        path = Path(file_path)
        channel = self._normalise_channel(upload_channel)

        bundle = InputBundle(
            file_name=path.name,
            file_path=str(path),
            upload_time=upload_time or datetime.now(timezone.utc),
            upload_channel=channel,
            source_hint=source_hint,
            request_metadata=request_metadata or {},
            content_sample=content_sample,
            referral_code=referral_code,
            referred_by=referred_by,
        )

        logger.info(
            "InputBundle created — file=%s  channel=%s  referral=%s",
            bundle.file_name,
            bundle.upload_channel or "not declared",
            "yes" if bundle.referral_code or bundle.referred_by else "no",
        )
        return bundle

    # ──────────────────────────────────────────────────────────
    # Helpers
    # ──────────────────────────────────────────────────────────

    def _normalise_channel(self, channel: str | None) -> str | None:
        if channel is None:
            return None
        normalised = channel.strip().lower()
        if normalised not in self._known_channels:
            logger.warning(
                "Declared upload channel '%s' is not in the known list %s — "
                "storing as-is; Module 3 will attempt fallback detection.",
                normalised,
                sorted(self._known_channels),
            )
        return normalised
