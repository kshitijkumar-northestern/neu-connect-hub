"""Encoders: the deterministic fake, lazy-loading the real model, and the batch validation that
guards the worker against anything an encoder might hand back."""

import builtins
import logging
import math
import sys
import types
from typing import Any

import pytest

import app.ml.encoders as encoders_module
from app.ml.encoders import FakeEncoder, SentenceTransformerEncoder, create_encoder, validate_batch
from app.ml.models import EMBEDDING_DIMENSIONS
from tests.conftest import make_settings, production_settings


def _norm(vector: list[float]) -> float:
    return math.sqrt(sum(value * value for value in vector))


# --- FakeEncoder --------------------------------------------------------------------------


def test_fake_encoder_reports_its_version_and_dimension() -> None:
    encoder = FakeEncoder()
    assert encoder.model_version == "fake-v1"
    assert encoder.dimension == EMBEDDING_DIMENSIONS


def test_fake_encoder_is_deterministic() -> None:
    encoder = FakeEncoder()
    assert encoder.encode(["hello world"]) == encoder.encode(["hello world"])


def test_fake_encoder_vectors_are_384_dimensional_and_unit_length() -> None:
    vectors = FakeEncoder().encode(["a profile", "another, different profile"])
    assert len(vectors) == 2
    for vector in vectors:
        assert len(vector) == EMBEDDING_DIMENSIONS
        assert _norm(vector) == pytest.approx(1.0, abs=1e-9)


def test_fake_encoder_gives_different_texts_different_vectors() -> None:
    first, second = FakeEncoder().encode(["alpha", "beta"])
    assert first != second


def test_fake_encoder_handles_an_empty_batch() -> None:
    assert FakeEncoder().encode([]) == []


# --- create_encoder -----------------------------------------------------------------------


def test_create_encoder_returns_the_fake_for_the_fake_backend() -> None:
    encoder = create_encoder(make_settings(embedding_backend="fake"))
    assert isinstance(encoder, FakeEncoder)


class _BlockedImport:
    """Patches `builtins.__import__` so `import sentence_transformers` always raises
    ImportError, regardless of whether the package actually happens to be installed."""

    def __init__(self, blocked: str) -> None:
        self._blocked = blocked
        self._real = builtins.__import__

    def __call__(self, name: str, *args: Any, **kwargs: Any) -> Any:
        if name == self._blocked or name.startswith(f"{self._blocked}."):
            raise ImportError(f"No module named {name!r}")
        return self._real(name, *args, **kwargs)


def test_create_encoder_returns_none_and_logs_one_warning_when_the_ml_extra_is_missing(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    monkeypatch.setattr(builtins, "__import__", _BlockedImport("sentence_transformers"))
    settings = make_settings(embedding_backend="sentence-transformers")

    with caplog.at_level(logging.WARNING, logger="app.ml"):
        encoder = create_encoder(settings)

    assert encoder is None
    warnings = [r for r in caplog.records if r.name == "app.ml" and r.levelno == logging.WARNING]
    assert len(warnings) == 1
    assert "unavailable" in warnings[0].getMessage().lower()


class _RaisingEncoder:
    """Stands in for `SentenceTransformerEncoder`: any failure loading the model (offline,
    missing cache, a bad model id, ...) must be caught the same way, not just ImportError."""

    def __init__(self, model_id: str) -> None:
        raise OSError("could not reach the model hub")


def test_create_encoder_returns_none_and_logs_an_error_when_loading_raises_for_any_reason(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    monkeypatch.setattr(encoders_module, "SentenceTransformerEncoder", _RaisingEncoder)
    settings = make_settings(embedding_backend="sentence-transformers")

    with caplog.at_level(logging.WARNING, logger="app.ml"):
        encoder = create_encoder(settings)

    assert encoder is None
    records = [r for r in caplog.records if r.name == "app.ml"]
    assert len(records) == 1
    assert "could not reach the model hub" in records[0].getMessage()


def test_a_loading_failure_logs_at_error_in_production_and_warning_elsewhere(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    monkeypatch.setattr(encoders_module, "SentenceTransformerEncoder", _RaisingEncoder)

    with caplog.at_level(logging.WARNING, logger="app.ml"):
        assert create_encoder(make_settings(embedding_backend="sentence-transformers")) is None
        assert create_encoder(production_settings()) is None

    levels = [r.levelno for r in caplog.records if r.name == "app.ml"]
    assert levels == [logging.WARNING, logging.ERROR]


class _WrongDimensionEncoder:
    """A model that loaded fine but doesn't produce our vector size."""

    model_version = "wrong-dimension-v1"
    dimension = 768

    def __init__(self, model_id: str) -> None:
        pass

    def encode(self, texts: list[str]) -> list[list[float]]:
        return [[0.1] * self.dimension for _ in texts]


def test_a_loaded_model_with_the_wrong_dimension_is_refused(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    monkeypatch.setattr(encoders_module, "SentenceTransformerEncoder", _WrongDimensionEncoder)
    settings = make_settings(embedding_backend="sentence-transformers")

    with caplog.at_level(logging.WARNING, logger="app.ml"):
        encoder = create_encoder(settings)

    assert encoder is None
    records = [r for r in caplog.records if r.name == "app.ml"]
    assert len(records) == 1
    assert "768" in records[0].getMessage()
    assert str(EMBEDDING_DIMENSIONS) in records[0].getMessage()


# --- reading the model's dimension across sentence-transformers versions --------------------
#
# `sentence-transformers` renamed `get_sentence_embedding_dimension` to `get_embedding_dimension`
# in 6.x; our `>=3.0` floor may only have the older name. These stand in for the model itself
# (not the whole encoder), with a fake `sentence_transformers` module installed in `sys.modules`,
# so they run whether or not the real package is actually present.


def _install_fake_sentence_transformers(monkeypatch: pytest.MonkeyPatch, model_cls: type) -> None:
    fake_module = types.ModuleType("sentence_transformers")
    fake_module.SentenceTransformer = model_cls  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "sentence_transformers", fake_module)


class _NewNameOnlyModel:
    def __init__(self, model_id: str, device: str) -> None:
        pass

    def get_embedding_dimension(self) -> int:
        return EMBEDDING_DIMENSIONS


class _OldNameOnlyModel:
    def __init__(self, model_id: str, device: str) -> None:
        pass

    def get_sentence_embedding_dimension(self) -> int:
        return EMBEDDING_DIMENSIONS


class _NoDimensionMethodModel:
    def __init__(self, model_id: str, device: str) -> None:
        pass


class _NoneDimensionModel:
    def __init__(self, model_id: str, device: str) -> None:
        pass

    def get_embedding_dimension(self) -> int | None:
        return None


@pytest.mark.parametrize(
    "model_cls", [_NewNameOnlyModel, _OldNameOnlyModel], ids=["new-name-only", "old-name-only"]
)
def test_the_dimension_is_read_from_whichever_method_name_the_model_has(
    monkeypatch: pytest.MonkeyPatch, model_cls: type
) -> None:
    _install_fake_sentence_transformers(monkeypatch, model_cls)

    encoder = SentenceTransformerEncoder("fake-model-id")

    assert encoder.dimension == EMBEDDING_DIMENSIONS


@pytest.mark.parametrize(
    "model_cls",
    [_NoDimensionMethodModel, _NoneDimensionModel],
    ids=["neither-method", "method-returns-none"],
)
def test_a_model_that_cannot_report_a_dimension_is_refused(
    monkeypatch: pytest.MonkeyPatch, model_cls: type
) -> None:
    _install_fake_sentence_transformers(monkeypatch, model_cls)

    with pytest.raises(RuntimeError):
        SentenceTransformerEncoder("fake-model-id")


def test_the_real_encoder_produces_the_right_shape() -> None:
    # Skips when the ml extra is missing or won't import (CI doesn't install it).
    pytest.importorskip("sentence_transformers")
    from app.ml.encoders import SentenceTransformerEncoder

    settings = make_settings(embedding_backend="sentence-transformers")
    encoder = SentenceTransformerEncoder(settings.embedding_model_id)

    vectors = encoder.encode(["a short profile"])

    assert encoder.model_version == settings.embedding_model_id
    assert len(vectors) == 1
    assert len(vectors[0]) == EMBEDDING_DIMENSIONS


# --- validate_batch -------------------------------------------------------------------------


def test_validate_batch_accepts_well_formed_vectors() -> None:
    assert validate_batch(FakeEncoder().encode(["a", "b"]), 2) is True


def test_validate_batch_rejects_a_mismatched_count() -> None:
    assert validate_batch(FakeEncoder().encode(["a"]), 2) is False


def test_validate_batch_rejects_the_wrong_dimension() -> None:
    assert validate_batch([[0.1] * (EMBEDDING_DIMENSIONS - 1)], 1) is False


@pytest.mark.parametrize("bad_value", [math.nan, math.inf, -math.inf])
def test_validate_batch_rejects_non_finite_values(bad_value: float) -> None:
    vector = [0.1] * EMBEDDING_DIMENSIONS
    vector[0] = bad_value
    assert validate_batch([vector], 1) is False


def test_validate_batch_accepts_an_empty_batch() -> None:
    assert validate_batch([], 0) is True
