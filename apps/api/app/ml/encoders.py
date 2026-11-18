"""Encoders turn profile text into vectors. Synchronous and CPU-bound by design: callers must
always run them off the event loop (`asyncio.to_thread`), never inline in a request or the
worker's own coroutine."""

import hashlib
import logging
import math
from typing import Any, Protocol

from app.core.config import Settings
from app.ml.models import EMBEDDING_DIMENSIONS

logger = logging.getLogger("app.ml")


class Encoder(Protocol):
    model_version: str
    dimension: int

    def encode(self, texts: list[str]) -> list[list[float]]: ...


class FakeEncoder:
    """Deterministic and dependency-free: a text's SHA-256 digest seeds a unit-length vector, so
    the same text always encodes to the same vector without loading any model. Tests only."""

    model_version = "fake-v1"
    dimension = EMBEDDING_DIMENSIONS

    def encode(self, texts: list[str]) -> list[list[float]]:
        return [_unit_vector(text) for text in texts]


def _unit_vector(text: str) -> list[float]:
    values: list[float] = []
    digest = hashlib.sha256(text.encode()).digest()
    while len(values) < EMBEDDING_DIMENSIONS:
        values.extend(byte / 255 - 0.5 for byte in digest)
        digest = hashlib.sha256(digest).digest()
    values = values[:EMBEDDING_DIMENSIONS]
    norm = math.sqrt(sum(value * value for value in values))
    if norm == 0:
        # A pathological digest (astronomically unlikely); nudge one component so the result
        # stays a unit vector instead of dividing by zero.
        values[0] = 1.0
        norm = 1.0
    return [value / norm for value in values]


def _model_dimension(model: Any) -> int | None:
    """`sentence-transformers` renamed `get_sentence_embedding_dimension` to
    `get_embedding_dimension` in 6.x; our `>=3.0` floor may only have the older name, so this
    prefers the current one and falls back to the one it deprecated."""
    getter = getattr(model, "get_embedding_dimension", None) or getattr(
        model, "get_sentence_embedding_dimension", None
    )
    return getter() if getter is not None else None


class SentenceTransformerEncoder:
    """The real model. `sentence_transformers` is imported lazily, inside `__init__`, so this
    module always imports cleanly even where the optional `ml` extra isn't installed."""

    def __init__(self, model_id: str) -> None:
        from sentence_transformers import SentenceTransformer

        self.model_version = model_id
        self._model = SentenceTransformer(model_id, device="cpu")
        dimension = _model_dimension(self._model)
        if dimension is None:
            raise RuntimeError("the model did not report an embedding dimension")
        self.dimension = dimension

    def encode(self, texts: list[str]) -> list[list[float]]:
        vectors = self._model.encode(texts, normalize_embeddings=True, convert_to_numpy=True)
        return [vector.tolist() for vector in vectors]


def create_encoder(settings: Settings) -> Encoder | None:
    """None means embeddings stay stale and matching falls back to rules: a missing optional
    dependency, a model that fails to load for any reason (offline, missing cache, wrong model
    id), or one that doesn't produce our vector size must never stop the API from starting."""
    if settings.embedding_backend == "fake":
        return FakeEncoder()
    try:
        # Any failure (offline, missing cache, a bad model id, ...) must degrade, never crash
        # startup, so this intentionally catches more than a specific exception type.
        encoder = SentenceTransformerEncoder(settings.embedding_model_id)
    except Exception as exc:
        _unavailable(settings, f"the model failed to load: {exc}")
        return None
    if encoder.dimension != EMBEDDING_DIMENSIONS:
        _unavailable(
            settings,
            f"the model reports {encoder.dimension} dimensions, not {EMBEDDING_DIMENSIONS}",
        )
        return None
    return encoder


def _unavailable(settings: Settings, reason: str) -> None:
    # Outside production this is routine (the ml extra often isn't installed locally); in
    # production it's worth paging on, so it logs at ERROR there.
    level = logging.ERROR if settings.is_production else logging.WARNING
    logger.log(level, "Profile embeddings are unavailable: %s", reason)


def validate_batch(vectors: list[list[float]], expected_count: int) -> bool:
    """A result count that doesn't match the input, a wrong dimension, or any non-finite value
    rejects the whole batch: nothing from it is ever stored."""
    if len(vectors) != expected_count:
        return False
    return all(
        len(vector) == EMBEDDING_DIMENSIONS and all(math.isfinite(value) for value in vector)
        for vector in vectors
    )
