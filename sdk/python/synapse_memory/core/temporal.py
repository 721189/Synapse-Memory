"""Shared temporal eligibility rules for retrieval-visible memories."""

import math
from typing import Any, Mapping


def validate_as_of(as_of: float) -> float:
    """Validate and normalize a non-negative Unix timestamp."""
    if (
        isinstance(as_of, bool)
        or not isinstance(as_of, (int, float))
        or not math.isfinite(as_of)
        or as_of < 0
    ):
        raise ValueError("as_of must be a finite non-negative timestamp")
    return float(as_of)


def is_retrieval_eligible(memory: Mapping[str, Any], as_of: float) -> bool:
    """Return whether a memory is eligible at the requested Unix timestamp.

    Validity intervals are half-open: valid_from <= as_of < valid_until.
    Legacy rows without observed_at use created_at as their observation time.
    Superseded and retracted records are never eligible.
    """
    as_of = validate_as_of(as_of)

    if memory.get("superseded_by") is not None:
        return False
    if memory.get("retracted_at") is not None:
        return False

    observed_at = memory.get("observed_at")
    if observed_at is None:
        observed_at = memory.get("created_at")
    if observed_at is not None:
        try:
            if not math.isfinite(float(observed_at)) or float(observed_at) < 0 or float(observed_at) > float(as_of):
                return False
        except (TypeError, ValueError):
            return False

    valid_from = memory.get("valid_from")
    if valid_from is not None:
        try:
            if not math.isfinite(float(valid_from)) or float(valid_from) < 0 or float(valid_from) > float(as_of):
                return False
        except (TypeError, ValueError):
            return False

    valid_until = memory.get("valid_until")
    if valid_until is not None:
        try:
            if not math.isfinite(float(valid_until)) or float(valid_until) < 0 or float(valid_until) <= float(as_of):
                return False
        except (TypeError, ValueError):
            return False

    return True
