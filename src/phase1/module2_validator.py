"""
src/phase1/module2_validator.py
================================
MODULE 2 – Input Validation & File Classification

Hybrid Validation Strategy (config-driven)
-------------------------------------------
Step 1  Extension check          — is the extension in the supported list?
Step 2  Binary files (PDF/DOCX/PNG/JPEG)
        → Magic-number / file-signature validation
        → DOCX: additionally verifies ZIP internal structure
Step 3  Text files (CSV/JSON/TXT/RTF/HTML)
        → Read only the first few lines
        → Verify format-specific structural rules
        → Lightweight — never loads the whole file

Output
------
  FileMetadata  with
    - validation_status   (True / False)
    - validation_message
    - detected_file_type  (e.g. "pdf", "csv")
    - data_category       ("structured" | "unstructured")
"""

from __future__ import annotations

import csv
import io
import json
import logging
import zipfile
from abc import ABC, abstractmethod
from pathlib import Path

from src.core.config_loader import get_config
from src.core.exceptions import (
    ClassificationError,
    EmptyFileError,
    ExtensionTypeMismatchError,
    FileTooLargeError,
    FileNotFoundInPipelineError,
    LightweightParseError,
    NoDetectorRegisteredError,
    SignatureMismatchError,
    UnsupportedExtensionError,
    ValidationBaseError,
)
from src.core.models import FileMetadata, InputBundle

logger = logging.getLogger("phase1.module2_validator")


# ──────────────────────────────────────────────────────────────
# Low-level file helpers
# ──────────────────────────────────────────────────────────────

def _read_first_bytes(path: Path, n: int) -> bytes:
    with open(path, "rb") as f:
        return f.read(n)


def _read_first_lines(path: Path, n: int) -> list[str]:
    lines: list[str] = []
    with open(path, "r", encoding="utf-8", errors="replace") as f:
        for _ in range(n):
            line = f.readline()
            if not line:
                break
            lines.append(line)
    return lines


def _hex_to_bytes(hex_str: str) -> bytes:
    return bytes.fromhex(hex_str)


# ──────────────────────────────────────────────────────────────
# Detector base class
# ──────────────────────────────────────────────────────────────

class BaseDetector(ABC):
    @abstractmethod
    def detect(self, path: Path, declared_ext: str) -> str:
        """Return the confirmed file type string or raise a ValidationBaseError."""
        raise NotImplementedError


# ──────────────────────────────────────────────────────────────
# Binary detector  (magic numbers)
# ──────────────────────────────────────────────────────────────

class BinarySignatureDetector(BaseDetector):
    """
    Validates PDF, DOCX, PNG, JPEG by comparing the file's leading
    bytes to the expected magic number in config.
    For DOCX it also verifies the internal ZIP structure.
    """

    def __init__(self, signatures: dict[str, str], docx_entry: str) -> None:
        # signatures is {ext: hex_string}
        self._signatures: dict[str, bytes] = {
            ext: _hex_to_bytes(hex_str) for ext, hex_str in signatures.items()
        }
        self._docx_entry = docx_entry
        self._logger = logging.getLogger("module2.BinarySignatureDetector")

    def detect(self, path: Path, declared_ext: str) -> str:
        sig_len = max(len(b) for b in self._signatures.values()) + 4
        header = _read_first_bytes(path, sig_len)
        expected = self._signatures.get(declared_ext)

        if expected is None or not header.startswith(expected):
            raise SignatureMismatchError(
                f"File header does not match the expected magic number for "
                f"'.{declared_ext}': {path.name}"
            )

        if declared_ext == "docx":
            self._verify_docx(path)

        self._logger.info("Binary signature OK: %s → .%s", path.name, declared_ext)
        return declared_ext

    def _verify_docx(self, path: Path) -> None:
        try:
            with zipfile.ZipFile(path) as z:
                if self._docx_entry not in z.namelist():
                    raise SignatureMismatchError(
                        f"ZIP header matched but required entry '{self._docx_entry}' "
                        f"is missing — not a valid DOCX: {path.name}"
                    )
        except zipfile.BadZipFile as exc:
            raise SignatureMismatchError(
                f"File has .docx extension but is not a valid ZIP archive: {path.name}"
            ) from exc


# ──────────────────────────────────────────────────────────────
# Text / lightweight detector
# ──────────────────────────────────────────────────────────────

class LightweightTextDetector(BaseDetector):
    """
    Reads only the first N lines (config: lightweight_parse_line_limit).
    Applies format-specific structural checks for CSV / JSON / TXT / RTF / HTML.
    """

    def __init__(self, line_limit: int) -> None:
        self._line_limit = line_limit
        self._logger = logging.getLogger("module2.LightweightTextDetector")

    def detect(self, path: Path, declared_ext: str) -> str:
        lines = _read_first_lines(path, self._line_limit)
        if not lines:
            raise LightweightParseError(f"File appears empty or unreadable: {path.name}")

        checker = {
            "csv":  self._check_csv,
            "json": self._check_json,
            "txt":  self._check_txt,
            "rtf":  self._check_rtf,
            "html": self._check_html,
            "htm":  self._check_html,
        }.get(declared_ext)

        if checker is None:
            raise LightweightParseError(
                f"No lightweight parser rule defined for '.{declared_ext}'"
            )

        checker(lines, path.name)
        self._logger.info("Lightweight text check OK: %s → .%s", path.name, declared_ext)
        return declared_ext

    # ── Format-specific checks ──────────────────────────────

    @staticmethod
    def _check_csv(lines: list[str], name: str) -> None:
        sample = "".join(lines)
        try:
            dialect = csv.Sniffer().sniff(sample)
            reader = csv.reader(io.StringIO(sample), dialect)
            first_row = next(reader, None)
        except csv.Error as exc:
            raise LightweightParseError(
                f"Cannot parse as CSV (no consistent delimiter): {name}"
            ) from exc
        if not first_row:
            raise LightweightParseError(f"CSV file has no parseable header row: {name}")

    @staticmethod
    def _check_json(lines: list[str], name: str) -> None:
        sample = "".join(lines).strip()
        if not sample or sample[0] not in "{[":
            raise LightweightParseError(
                f"Content does not begin like a JSON object or array: {name}"
            )
        # Attempt a partial parse — small files will fully parse; large ones won't.
        try:
            json.loads(sample)
        except json.JSONDecodeError:
            pass  # partial content is fine; we only check the opening character above

    @staticmethod
    def _check_txt(lines: list[str], name: str) -> None:
        if not any(ln.strip() for ln in lines):
            raise LightweightParseError(f"TXT file contains no readable text: {name}")

    @staticmethod
    def _check_rtf(lines: list[str], name: str) -> None:
        sample = "".join(lines).lstrip()
        if not sample.startswith("{\\rtf"):
            raise LightweightParseError(
                f"RTF file does not start with '{{\\\\rtf' control word: {name}"
            )

    @staticmethod
    def _check_html(lines: list[str], name: str) -> None:
        sample = "".join(lines).strip().lower()
        if not sample:
            raise LightweightParseError(f"HTML file is empty: {name}")
        if "<!doctype html" not in sample and "<html" not in sample and "<" not in sample:
            raise LightweightParseError(
                f"Content does not look like HTML (no tags in first {len(lines)} lines): {name}"
            )


# ──────────────────────────────────────────────────────────────
# Detector Registry (built from config)
# ──────────────────────────────────────────────────────────────

class DetectorRegistry:
    """Maps every supported extension to the appropriate detector instance."""

    def __init__(self) -> None:
        cfg = get_config()
        ft = cfg.file_types

        binary_sigs: dict[str, str] = {
            k: v for k, v in ft["binary_signatures"].items()
        }
        docx_entry: str = ft["docx_required_zip_entry"]
        line_limit: int = ft["lightweight_parse_line_limit"]

        bin_detector = BinarySignatureDetector(binary_sigs, docx_entry)
        txt_detector = LightweightTextDetector(line_limit)

        # Supported extensions come from config
        structured: list[str] = ft["supported"]["structured"]
        unstructured: list[str] = ft["supported"]["unstructured"]
        all_ext = structured + unstructured

        self._map: dict[str, BaseDetector] = {}
        for ext in all_ext:
            if ext in binary_sigs:
                self._map[ext] = bin_detector
            else:
                self._map[ext] = txt_detector

    def get(self, extension: str) -> BaseDetector:
        detector = self._map.get(extension)
        if detector is None:
            raise NoDetectorRegisteredError(
                f"No detector registered for '.{extension}'"
            )
        return detector


# ──────────────────────────────────────────────────────────────
# File Validator
# ──────────────────────────────────────────────────────────────

class FileValidator:
    """
    Content-blind validation: existence, size, extension.
    Returns extension string on success; raises ValidationBaseError on failure.
    """

    def __init__(self) -> None:
        cfg = get_config()
        ft = cfg.file_types
        self._min_size: int = ft["min_file_size_bytes"]
        self._max_size: int = ft["max_file_size_bytes"]
        structured: list[str] = ft["supported"]["structured"]
        unstructured: list[str] = ft["supported"]["unstructured"]
        self._supported: set[str] = set(structured + unstructured)

    def validate(self, path: Path) -> tuple[str, int]:
        """Returns (extension, file_size_bytes) or raises."""
        if not path.exists() or not path.is_file():
            raise FileNotFoundInPipelineError(f"File not found: {path}")

        size = path.stat().st_size
        if size < self._min_size:
            raise EmptyFileError(f"File is empty: {path.name}")
        if size > self._max_size:
            raise FileTooLargeError(
                f"File size {size} exceeds max {self._max_size} bytes: {path.name}"
            )

        ext = path.suffix.lstrip(".").lower()
        if ext not in self._supported:
            raise UnsupportedExtensionError(
                f"Extension '.{ext}' is not in the supported list {sorted(self._supported)}"
            )

        return ext, size


# ──────────────────────────────────────────────────────────────
# File Classifier
# ──────────────────────────────────────────────────────────────

class FileClassifier:
    """Maps a confirmed file type to 'structured' or 'unstructured'."""

    def __init__(self) -> None:
        cfg = get_config()
        ft = cfg.file_types
        self._structured: set[str] = set(ft["supported"]["structured"])
        self._unstructured: set[str] = set(ft["supported"]["unstructured"])

    def classify(self, file_type: str) -> str:
        if file_type in self._structured:
            return "structured"
        if file_type in self._unstructured:
            return "unstructured"
        raise ClassificationError(
            f"File type '.{file_type}' does not map to any known data category."
        )


# ──────────────────────────────────────────────────────────────
# InputValidationPipeline  (Module 2 orchestrator)
# ──────────────────────────────────────────────────────────────

class InputValidationPipeline:
    """
    Coordinates the three-step hybrid validation and returns a
    FileMetadata object.  On failure, returns FileMetadata with
    validation_status=False — it never raises; the caller decides
    whether to abort or continue.
    """

    def __init__(self) -> None:
        self._file_validator = FileValidator()
        self._registry = DetectorRegistry()
        self._classifier = FileClassifier()

    def run(self, bundle: InputBundle) -> FileMetadata:
        path = Path(bundle.file_path)
        logger.info("Validating: %s", bundle.file_path)

        try:
            # Step 1 – extension + size
            ext, size = self._file_validator.validate(path)

            # Step 2/3 – signature or lightweight text check
            detector = self._registry.get(ext)
            detected_type = detector.detect(path, ext)

            # Sanity: declared extension must equal detected type
            if ext != detected_type:
                raise ExtensionTypeMismatchError(
                    f"Extension '.{ext}' does not match detected type '.{detected_type}'"
                )

            # Classify
            category = self._classifier.classify(detected_type)

            result = FileMetadata(
                file_name=path.name,
                file_path=str(path),
                extension=ext,
                detected_file_type=detected_type,
                data_category=category,
                validation_status=True,
                validation_message="File validated and classified successfully.",
                file_size_bytes=size,
                source_hint=bundle.source_hint,
            )
            logger.info(
                "Validation passed: %s → %s (%s)", path.name, detected_type, category
            )
            return result

        except ValidationBaseError as exc:
            logger.warning("Validation failed for %s: %s", path.name, exc.message)
            return FileMetadata(
                file_name=path.name,
                file_path=str(path),
                extension=path.suffix.lstrip(".").lower(),
                detected_file_type=None,
                data_category=None,
                validation_status=False,
                validation_message=exc.message,
                file_size_bytes=0,
                source_hint=bundle.source_hint,
            )
