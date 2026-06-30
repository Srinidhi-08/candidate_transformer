"""
src/core/exceptions.py
======================
All custom exceptions for the entire pipeline, grouped by module.
Every exception carries a human-readable `message` attribute so
callers can log it without inspecting args[0].
"""

from __future__ import annotations


# ──────────────────────────────────────────────────────────────
# Base
# ──────────────────────────────────────────────────────────────

class PipelineBaseError(Exception):
    """Root for every pipeline-specific exception."""

    def __init__(self, message: str) -> None:
        self.message = message
        super().__init__(message)


# ──────────────────────────────────────────────────────────────
# Module 1 – Input Collection
# ──────────────────────────────────────────────────────────────

class InputCollectionError(PipelineBaseError):
    """Generic input-collection failure."""


# ──────────────────────────────────────────────────────────────
# Module 2 – File Validation & Classification
# ──────────────────────────────────────────────────────────────

class ValidationBaseError(PipelineBaseError):
    """Root for all validation failures."""


class FileNotFoundInPipelineError(ValidationBaseError):
    """File path does not exist on disk."""


class EmptyFileError(ValidationBaseError):
    """File exists but contains zero bytes."""


class FileTooLargeError(ValidationBaseError):
    """File exceeds the configured maximum size."""


class UnsupportedExtensionError(ValidationBaseError):
    """File extension is not in the supported list."""


class SignatureMismatchError(ValidationBaseError):
    """Binary file magic-number does not match its declared extension."""


class LightweightParseError(ValidationBaseError):
    """Text file fails the first-few-lines structural check."""


class ExtensionTypeMismatchError(ValidationBaseError):
    """Declared extension and detected file type disagree."""


class ClassificationError(ValidationBaseError):
    """Detected file type cannot be mapped to a data category."""


class NoDetectorRegisteredError(ValidationBaseError):
    """Registry has no detector for the given extension."""


# ──────────────────────────────────────────────────────────────
# Module 3 – Source & Referral Management
# ──────────────────────────────────────────────────────────────

class SourceBaseError(PipelineBaseError):
    """Root for all source-identification failures."""


class SourceNotResolvedError(SourceBaseError):
    """No detector in the chain could resolve a source type."""


class NoSourceDetectorRegisteredError(SourceBaseError):
    """Registry has no detector for the given priority key."""


# ──────────────────────────────────────────────────────────────
# Module 4 – Parser Selection
# ──────────────────────────────────────────────────────────────

class ParserBaseError(PipelineBaseError):
    """Root for all parser failures."""


class NoParserRegisteredError(ParserBaseError):
    """No parser is registered for the given file type."""


class ParsingFailedError(ParserBaseError):
    """Parser could not read the file's content."""


# ──────────────────────────────────────────────────────────────
# Module 6 – Candidate Object Builder
# ──────────────────────────────────────────────────────────────

class BuilderBaseError(PipelineBaseError):
    """Root for all candidate-builder failures."""


class CandidateBuildError(BuilderBaseError):
    """Candidate object could not be built from the given content."""


class StructuredMappingError(BuilderBaseError):
    """Field mapping from structured data failed."""


# ──────────────────────────────────────────────────────────────
# Module 7 – Processing Context
# ──────────────────────────────────────────────────────────────

class ContextError(PipelineBaseError):
    """Processing context is missing required data."""


# ──────────────────────────────────────────────────────────────
# Module 8 – Data Validation (Phase 2)
# ──────────────────────────────────────────────────────────────

class DataValidationError(PipelineBaseError):
    """A validated field value failed format checks."""


# ──────────────────────────────────────────────────────────────
# Module 10 – Normalization
# ──────────────────────────────────────────────────────────────

class NormalizationError(PipelineBaseError):
    """Normalization of a field value failed."""


# ──────────────────────────────────────────────────────────────
# Module 11 – Candidate Matching
# ──────────────────────────────────────────────────────────────

class MatchingError(PipelineBaseError):
    """Candidate matching encountered an unrecoverable error."""


# ──────────────────────────────────────────────────────────────
# Module 12 – Merge Engine
# ──────────────────────────────────────────────────────────────

class MergeError(PipelineBaseError):
    """Candidate merge operation failed."""


# ──────────────────────────────────────────────────────────────
# Module 14 – Confidence Engine
# ──────────────────────────────────────────────────────────────

class ConfidenceError(PipelineBaseError):
    """Confidence calculation failed."""


# ──────────────────────────────────────────────────────────────
# Module 15 – Database / Repository
# ──────────────────────────────────────────────────────────────

class DatabaseError(PipelineBaseError):
    """Database operation failed."""


class RecordNotFoundError(DatabaseError):
    """Expected database record was not found."""


# ──────────────────────────────────────────────────────────────
# Phase 3 – Projection Layer
# ──────────────────────────────────────────────────────────────

class ProjectionError(PipelineBaseError):
    """Output projection failed."""


class UnknownSchemaError(ProjectionError):
    """Requested projection schema is not defined in config."""


# ──────────────────────────────────────────────────────────────
# Config
# ──────────────────────────────────────────────────────────────

class ConfigurationError(PipelineBaseError):
    """Pipeline configuration is missing or invalid."""
