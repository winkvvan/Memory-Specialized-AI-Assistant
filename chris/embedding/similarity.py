"""Cosine similarity over embedding vectors.

Always emits a value in [0, 1]. Zero vectors / malformed inputs never crash:
they score 0. Dimension mismatch raises so the caller can treat the record as
unavailable (fails safe) instead of silently producing a wrong score.
"""

from __future__ import annotations

import math
from typing import Sequence


class EmbeddingDimensionError(ValueError):
    """Raised when two vectors have different lengths."""


def _validate(vector: Sequence[float]) -> Sequence[float]:
    if not vector:
        raise ValueError("empty embedding vector")
    for value in vector:
        if not isinstance(value, (int, float)) or not math.isfinite(float(value)):
            raise ValueError(f"malformed embedding value: {value!r}")
    return vector


def cosine_similarity(a: Sequence[float], b: Sequence[float]) -> float:
    """Cosine similarity in [0,1]. Same vector -> 1, orthogonal -> 0.

    Raises:
        EmbeddingDimensionError: dimension mismatch.
        ValueError: empty / malformed vector.
    """
    va = _validate(a)
    vb = _validate(b)
    if len(va) != len(vb):
        raise EmbeddingDimensionError(
            f"embedding dimension mismatch: {len(va)} vs {len(vb)}"
        )
    norm_a = math.sqrt(sum(x * x for x in va))
    norm_b = math.sqrt(sum(x * x for x in vb))
    if norm_a == 0.0 or norm_b == 0.0:
        return 0.0
    dot = sum(x * y for x, y in zip(va, vb))
    value = dot / (norm_a * norm_b)
    return max(0.0, min(1.0, value))
