"""
src/phase1/module3_source.py
=============================
MODULE 3 – Source & Referral Management

Detection chain (config-driven priority order)
----------------------------------------------
1. UploadChannelDetector     — explicit caller-declared channel (confidence 1.0)
2. RequestMetadataDetector   — request headers / integration markers (confidence 0.85)
3. ContentFallbackDetector   — scan content_sample for URL hints (confidence 0.5)

If the chain is exhausted without a match → source_type = "unknown", confidence = 0.0

Referral is resolved independently of source detection.
A verified referral (both code AND referred_by present) adds a configurable
confidence bonus to the final source confidence.

Outputs
-------
  SourceMetadata
  ReferralInfo
"""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from datetime import datetime, timezone

from src.core.config_loader import get_config
from src.core.exceptions import NoSourceDetectorRegisteredError
from src.core.models import InputBundle, ReferralInfo, SourceMetadata

logger = logging.getLogger("phase1.module3_source")


# ──────────────────────────────────────────────────────────────
# Detector base
# ──────────────────────────────────────────────────────────────

class BaseSourceDetector(ABC):
    name: str

    @abstractmethod
    def detect(self, bundle: InputBundle) -> tuple[str, float] | None:
        """
        Returns (source_type, confidence) if this detector can resolve,
        or None to pass to the next detector in the chain.
        """
        raise NotImplementedError


# ──────────────────────────────────────────────────────────────
# Detector implementations
# ──────────────────────────────────────────────────────────────

class UploadChannelDetector(BaseSourceDetector):
    """Priority 1: explicit channel declared by the caller."""
    name = "upload_channel"

    def __init__(self, known_types: set[str]) -> None:
        self._known = known_types

    def detect(self, bundle: InputBundle) -> tuple[str, float] | None:
        if not bundle.upload_channel:
            return None
        ch = bundle.upload_channel.strip().lower()
        if ch not in self._known:
            logger.warning(
                "Declared channel '%s' is not a known source type — skipping.", ch
            )
            return None
        logger.info("Source resolved via upload_channel: %s", ch)
        return ch, 1.0


class RequestMetadataDetector(BaseSourceDetector):
    """Priority 2: request headers / metadata markers."""
    name = "request_metadata"

    def __init__(self, markers: dict[str, str], known_types: set[str]) -> None:
        # markers: {marker_string → source_type}
        self._markers = markers
        self._known = known_types

    def detect(self, bundle: InputBundle) -> tuple[str, float] | None:
        if not bundle.request_metadata:
            return None
        combined = " ".join(bundle.request_metadata.values()).lower()
        for marker, source_type in self._markers.items():
            if marker in combined and source_type in self._known:
                logger.info(
                    "Source resolved via request_metadata marker '%s' → %s", marker, source_type
                )
                return source_type, 0.85
        return None


class ContentFallbackDetector(BaseSourceDetector):
    """Priority 3 (last resort): scan content_sample for URL / keyword hints."""
    name = "content_fallback"

    def __init__(self, hints: dict[str, list[str]], char_limit: int) -> None:
        # hints: {source_type → [hint_strings]}
        self._hints = hints
        self._char_limit = char_limit

    def detect(self, bundle: InputBundle) -> tuple[str, float] | None:
        if not bundle.content_sample:
            return None
        sample = bundle.content_sample[: self._char_limit].lower()
        for source_type, keywords in self._hints.items():
            if any(kw in sample for kw in keywords):
                logger.info("Source guessed via content_fallback: %s", source_type)
                return source_type, 0.5
        return None


# ──────────────────────────────────────────────────────────────
# Detector Registry
# ──────────────────────────────────────────────────────────────

class SourceDetectorRegistry:
    """
    Builds the ordered detector chain from config.
    The `detector_priority` list in config controls evaluation order.
    """

    _DETECTOR_CLASSES = {
        "upload_channel":    UploadChannelDetector,
        "request_metadata":  RequestMetadataDetector,
        "content_fallback":  ContentFallbackDetector,
    }

    def __init__(self) -> None:
        cfg = get_config()
        src_cfg = cfg.sources

        known_types: set[str] = set(src_cfg["known_types"])
        markers: dict[str, str] = src_cfg["metadata_markers"]
        hints: dict[str, list[str]] = src_cfg["content_hints"]
        char_limit: int = src_cfg["content_scan_chars"]
        priority_order: list[str] = src_cfg["detector_priority"]

        # Factories per detector key
        factory = {
            "upload_channel":   lambda: UploadChannelDetector(known_types),
            "request_metadata": lambda: RequestMetadataDetector(markers, known_types),
            "content_fallback": lambda: ContentFallbackDetector(hints, char_limit),
        }

        self._chain: list[BaseSourceDetector] = []
        for key in priority_order:
            if key not in factory:
                raise NoSourceDetectorRegisteredError(
                    f"No detector registered for priority key: '{key}'"
                )
            self._chain.append(factory[key]())

    def get_chain(self) -> list[BaseSourceDetector]:
        return self._chain


# ──────────────────────────────────────────────────────────────
# Referral Resolver
# ──────────────────────────────────────────────────────────────

class ReferralResolver:
    """
    Reads referral fields from the InputBundle.
    A referral is verified only when BOTH code AND referred_by are present.
    """

    def resolve(self, bundle: InputBundle) -> ReferralInfo:
        has_referral = bool(bundle.referral_code or bundle.referred_by)
        is_verified = bool(bundle.referral_code and bundle.referred_by)

        info = ReferralInfo(
            has_referral=has_referral,
            referral_code=bundle.referral_code,
            referred_by=bundle.referred_by,
            is_verified=is_verified,
        )

        if has_referral:
            logger.info(
                "Referral detected — code=%s  referred_by=%s  verified=%s",
                bundle.referral_code,
                bundle.referred_by,
                is_verified,
            )
        return info


# ──────────────────────────────────────────────────────────────
# SourceIdentificationPipeline  (Module 3 orchestrator)
# ──────────────────────────────────────────────────────────────

class SourceIdentificationPipeline:
    """
    Walks the detector chain, resolves the referral, applies the
    verified-referral confidence bonus, and returns:
        (SourceMetadata, ReferralInfo)
    """

    def __init__(self) -> None:
        cfg = get_config()
        self._chain = SourceDetectorRegistry().get_chain()
        self._referral_resolver = ReferralResolver()
        self._referral_bonus: float = cfg.sources["verified_referral_bonus"]

    def run(self, bundle: InputBundle, file_type: str | None) -> tuple[SourceMetadata, ReferralInfo]:
        logger.info("Source identification started for: %s", bundle.file_name)

        source_type, resolved_by, confidence = self._walk_chain(bundle)
        referral_info = self._referral_resolver.resolve(bundle)

        if referral_info.is_verified:
            confidence = min(1.0, confidence + self._referral_bonus)
            logger.info(
                "Verified-referral bonus applied: confidence → %.4f", confidence
            )

        metadata = SourceMetadata(
            source_type=source_type,
            resolved_by=resolved_by,
            upload_time=datetime.now(timezone.utc),
            file_name=bundle.file_name,
            file_type=file_type,
            confidence=round(confidence, 4),
        )

        logger.info(
            "Source resolved: %s → %s  (via %s, confidence=%.2f)",
            bundle.file_name,
            source_type,
            resolved_by,
            confidence,
        )
        return metadata, referral_info

    def _walk_chain(self, bundle: InputBundle) -> tuple[str, str, float]:
        for detector in self._chain:
            result = detector.detect(bundle)
            if result is not None:
                source_type, confidence = result
                return source_type, detector.name, confidence

        logger.warning("No detector resolved a source for: %s", bundle.file_name)
        return "unknown", "none", 0.0
