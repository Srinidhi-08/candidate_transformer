"""
src/phase3/projection_layer.py
================================
PHASE 3 – Projection Layer

Transforms a CanonicalCandidateRecord into a final JSON output dict
according to a named schema defined in config.

The canonical record is NEVER modified.

Config controls (per schema):
  include_fields       : list of field paths (supports "links.linkedin") or ["*"]
  include_confidence   : bool
  include_provenance   : bool
  field_rename         : {original_name: output_name}
  missing_value_policy : "null" | "exclude" | "empty_string"
"""

from __future__ import annotations

import logging
from typing import Any

from src.core.config_loader import get_config
from src.core.exceptions import ProjectionError, UnknownSchemaError
from src.core.models import CanonicalCandidateRecord

logger = logging.getLogger("phase3.projection_layer")

_SENTINEL = object()


def _deep_get(obj: Any, path: str) -> Any:
    """
    Traverse a dot-path like 'links.linkedin' on a dataclass/dict.
    Returns _SENTINEL if any segment is missing.
    """
    parts = path.split(".")
    current = obj
    for part in parts:
        if current is None:
            return _SENTINEL
        if isinstance(current, dict):
            current = current.get(part, _SENTINEL)
        else:
            current = getattr(current, part, _SENTINEL)
        if current is _SENTINEL:
            return _SENTINEL
    return current


def _to_serializable(val: Any) -> Any:
    """Recursively convert dataclass instances to dicts."""
    if hasattr(val, "to_dict"):
        return val.to_dict()
    if isinstance(val, list):
        return [_to_serializable(v) for v in val]
    if isinstance(val, dict):
        return {k: _to_serializable(v) for k, v in val.items()}
    return val


class ProjectionLayer:
    """
    Entry point: call `project(record, schema_name)` to get the
    final output dict for a given schema.
    """

    def __init__(self) -> None:
        cfg = get_config()
        proj_cfg = cfg.projection.raw() if hasattr(cfg.projection, "raw") else cfg.projection
        self._schemas: dict[str, dict] = proj_cfg.get("schemas", {})
        self._default_schema: str = proj_cfg.get("default_schema", "full")
        self._global_missing_policy: str = proj_cfg.get("missing_value_policy", "null")

    def project(
        self,
        record: CanonicalCandidateRecord,
        schema_name: str | None = None,
    ) -> dict:
        name = schema_name or self._default_schema

        if name not in self._schemas:
            raise UnknownSchemaError(
                f"Projection schema '{name}' is not defined in config. "
                f"Available: {list(self._schemas.keys())}"
            )

        schema = self._schemas[name]
        include_fields: list[str] = schema.get("include_fields", ["*"])
        include_confidence: bool = schema.get("include_confidence", True)
        include_provenance: bool = schema.get("include_provenance", False)
        renames: dict[str, str] = schema.get("field_rename", {}) or {}
        missing_policy: str = schema.get("missing_value_policy", self._global_missing_policy)

        # Build flat field list
        if include_fields == ["*"]:
            raw = record.to_dict()
            output = {
                renames.get(k, k): _to_serializable(v)
                for k, v in raw.items()
            }
        else:
            output: dict = {}
            for field_path in include_fields:
                val = _deep_get(record, field_path)
                if val is _SENTINEL or val is None:
                    if missing_policy == "exclude":
                        continue
                    elif missing_policy == "empty_string":
                        val = ""
                    else:  # null
                        val = None
                out_key = renames.get(field_path, field_path.split(".")[-1])
                output[out_key] = _to_serializable(val)

        # Optionally strip confidence / provenance
        if not include_confidence:
            output.pop("overall_confidence", None)
            output.pop("confidence_breakdown", None)
        if not include_provenance:
            output.pop("provenance", None)
            output.pop("source_history", None)

        # Always include candidate_id for traceability
        output.setdefault("candidate_id", record.candidate_id)

        logger.info(
            "Projection complete: candidate=%s  schema=%s  fields=%d",
            record.candidate_id, name, len(output),
        )
        return output

    def list_schemas(self) -> list[str]:
        return list(self._schemas.keys())
