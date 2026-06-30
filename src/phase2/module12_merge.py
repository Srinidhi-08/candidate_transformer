"""
src/phase2/module12_merge.py
==============================
MODULE 12 – Merge Engine

Merge rules (applied field-by-field):
  NULL + Value      → take the incoming value
  Value + NULL      → keep the existing value (no overwrite with NULL)
  Same value        → no change
  Different values  → escalate to Conflict Resolver (Module 13)

Also:
  - list fields (skills, experience, education, certifications,
    projects, emails, phones): union-merge without duplicates.
  - source_history: appended unconditionally.
"""

from __future__ import annotations

import copy
import logging

from src.core.models import (
    CanonicalCandidateRecord,
    CandidateObject,
    SkillEntry,
    SourceMetadata,
)

logger = logging.getLogger("phase2.module12_merge")

# Scalar fields subject to merge / conflict detection
_SCALAR_FIELDS = [
    "full_name", "date_of_birth", "gender", "nationality",
    "headline", "summary", "years_experience",
]


class MergeEngine:
    """
    Merges an incoming CandidateObject into an existing
    CanonicalCandidateRecord.

    Returns (merged_record, conflict_fields).
    `conflict_fields` is a dict of {field: (existing_val, incoming_val)}
    that Module 13 will resolve.
    """

    def merge(
        self,
        existing: CanonicalCandidateRecord,
        incoming: CandidateObject,
        source_metadata: SourceMetadata,
    ) -> tuple[CanonicalCandidateRecord, dict]:
        merged = copy.deepcopy(existing)
        conflicts: dict[str, tuple] = {}

        # ── Scalar fields ────────────────────────────────────
        for field in _SCALAR_FIELDS:
            existing_val = getattr(merged, field, None)
            incoming_val = getattr(incoming, field, None)
            new_val, conflict = self._merge_scalar(field, existing_val, incoming_val)
            setattr(merged, field, new_val)
            if conflict:
                conflicts[field] = conflict

        # ── Location ─────────────────────────────────────────
        for sub in ("city", "region", "country"):
            ex = getattr(merged.location, sub, None)
            inc = getattr(incoming.location, sub, None)
            new_val, conflict = self._merge_scalar(f"location.{sub}", ex, inc)
            setattr(merged.location, sub, new_val)
            if conflict:
                conflicts[f"location.{sub}"] = conflict

        # ── Links ────────────────────────────────────────────
        for sub in ("linkedin", "github", "portfolio"):
            ex = getattr(merged.links, sub, None)
            inc = getattr(incoming.links, sub, None)
            new_val, conflict = self._merge_scalar(f"links.{sub}", ex, inc)
            setattr(merged.links, sub, new_val)
            if conflict:
                conflicts[f"links.{sub}"] = conflict

        # ── List fields (union-merge) ─────────────────────────
        merged.emails = _union(merged.emails, incoming.emails, key=str.lower)
        merged.phones = _union(merged.phones, incoming.phones, key=lambda x: "".join(c for c in x if c.isdigit()))

        # Skills — deduplicate by name
        merged.skills = _union_skills(merged.skills, incoming.skills)

        # Experience / education / certifications / projects — append if not duplicate
        merged.experience = _union_by_repr(merged.experience, incoming.experience)
        merged.education  = _union_by_repr(merged.education,  incoming.education)
        merged.certifications = _union_by_repr(merged.certifications, incoming.certifications)
        merged.projects   = _union_by_repr(merged.projects,   incoming.projects)

        # ── Source history ────────────────────────────────────
        merged.source_history.append(source_metadata.to_dict())
        merged.is_merged = True
        merged.merged_from.append(incoming.candidate_id)

        # ── Provenance ────────────────────────────────────────
        merged.provenance.extend(incoming.provenance)

        logger.info(
            "Merge complete for candidate %s — conflicts: %s",
            existing.candidate_id,
            list(conflicts.keys()) or "none",
        )
        return merged, conflicts

    @staticmethod
    def _merge_scalar(
        field: str, existing, incoming
    ) -> tuple[object, tuple | None]:
        """
        Returns (resolved_value, conflict_tuple_or_None).
        """
        if incoming is None:
            return existing, None          # NULL incoming → keep existing
        if existing is None:
            return incoming, None          # NULL existing → take incoming
        if existing == incoming:
            return existing, None          # same → no change
        # Different non-null values → conflict
        return existing, (existing, incoming)


# ── Utility functions ─────────────────────────────────────────

def _union(a: list, b: list, key=None) -> list:
    """Union of two lists; deduplication based on key function."""
    seen = {key(x) if key else x for x in a}
    result = list(a)
    for item in b:
        k = key(item) if key else item
        if k not in seen:
            seen.add(k)
            result.append(item)
    return result


def _union_skills(existing: list[SkillEntry], incoming: list[SkillEntry]) -> list[SkillEntry]:
    existing_names = {s.name.lower() for s in existing}
    result = list(existing)
    for s in incoming:
        if s.name.lower() not in existing_names:
            existing_names.add(s.name.lower())
            result.append(s)
    return result


def _union_by_repr(existing: list, incoming: list) -> list:
    """Append incoming items that are not already represented in existing."""
    existing_reprs = {repr(e.to_dict()) for e in existing}
    result = list(existing)
    for item in incoming:
        if repr(item.to_dict()) not in existing_reprs:
            result.append(item)
    return result
