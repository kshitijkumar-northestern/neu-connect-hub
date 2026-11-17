"""The background worker: claiming stale, unleased rows; building each one's text without ever
touching anything but a public profile a stranger could actually see; encoding off the event loop
with no transaction open; and settling the batch.
"""

import asyncio
import contextlib
import logging
import math
import socket as socket_module
import time
import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Any

import httpx
import pytest
import socketio
import uvicorn
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker

import app.main as main_module
from app.core.config import Settings
from app.db.session import Database
from app.main import create_http_app
from app.ml.encoders import FakeEncoder
from app.ml.models import EMBEDDING_DIMENSIONS, Embedding
from app.ml.worker import EmbeddingWorker
from app.realtime.server import create_socketio_server
from tests.conftest import MemoryMailer, make_settings
from tests.helpers import PROFILE, execute, make_project_chat, profile, student

pytestmark = pytest.mark.usefixtures("clean_database")


@pytest.fixture
def database(engine: AsyncEngine) -> Database:
    return Database(engine=engine, sessionmaker=async_sessionmaker(engine, expire_on_commit=False))


async def fetch_embedding(database: Database, user_id: str) -> Embedding | None:
    async with database.sessionmaker() as session:
        return await session.get(Embedding, uuid.UUID(user_id))


async def lease_in_the_past(engine: AsyncEngine, user_id: str) -> None:
    """Simulates a crashed worker's claim: leased, but the lease already expired."""
    await execute(
        engine,
        "UPDATE embeddings SET claimed_until = now() - interval '1 minute' WHERE user_id = :id",
        id=user_id,
    )


async def unverify(engine: AsyncEngine, user_id: str) -> None:
    await execute(engine, "UPDATE users SET verified_at = NULL WHERE id = :id", id=user_id)


async def suspend(engine: AsyncEngine, user_id: str) -> None:
    await execute(engine, "UPDATE users SET suspended_at = now() WHERE id = :id", id=user_id)


async def hide(engine: AsyncEngine, user_id: str) -> None:
    await execute(
        engine, "UPDATE profiles SET discoverable = false WHERE user_id = :id", id=user_id
    )


# --- test doubles -----------------------------------------------------------------------------


@dataclass
class SpyEncoder:
    """Records every batch it was asked to encode, so a test can tell whether (and with what)
    `encode` was called, without ever needing a real model."""

    model_version: str = "spy-v1"
    dimension: int = EMBEDDING_DIMENSIONS
    calls: list[list[str]] = field(default_factory=list)

    def encode(self, texts: list[str]) -> list[list[float]]:
        self.calls.append(list(texts))
        return FakeEncoder().encode(texts)

    @property
    def call_count(self) -> int:
        return len(self.calls)


@dataclass
class SlowSpyEncoder(SpyEncoder):
    """Blocks the calling *thread* (not the event loop) for `delay_seconds`, the way a real CPU-
    bound model would."""

    delay_seconds: float = 0.5

    def encode(self, texts: list[str]) -> list[list[float]]:
        time.sleep(self.delay_seconds)
        return super().encode(texts)


@dataclass
class BrokenEncoder:
    """Hands back whatever `vectors` says, regardless of `texts`: a stand-in for a model that
    misbehaves. Defaults to the wrong dimension."""

    model_version: str = "broken-v1"
    dimension: int = EMBEDDING_DIMENSIONS
    vectors: list[list[float]] | None = None
    calls: list[list[str]] = field(default_factory=list)

    def encode(self, texts: list[str]) -> list[list[float]]:
        self.calls.append(list(texts))
        if self.vectors is not None:
            return self.vectors
        return [[0.1] * (EMBEDDING_DIMENSIONS - 1) for _ in texts]


@dataclass
class SelectivelyBrokenEncoder:
    """Rejects only batches containing `poison_marker`; everything else encodes normally. Models
    a bad row that keeps failing without taking the whole pipeline down with it."""

    model_version: str = "selective-v1"
    dimension: int = EMBEDDING_DIMENSIONS
    poison_marker: str = "POISON_MARKER"
    calls: list[list[str]] = field(default_factory=list)

    def encode(self, texts: list[str]) -> list[list[float]]:
        self.calls.append(list(texts))
        if any(self.poison_marker in text for text in texts):
            return [[0.1] * (EMBEDDING_DIMENSIONS - 1) for _ in texts]
        return FakeEncoder().encode(texts)


@dataclass
class FlakyEncoder:
    """Raises on its first call (simulating an unexpected model failure), then behaves."""

    model_version: str = "flaky-v1"
    dimension: int = EMBEDDING_DIMENSIONS
    fail_times: int = 1
    calls: int = 0

    def encode(self, texts: list[str]) -> list[list[float]]:
        self.calls += 1
        if self.calls <= self.fail_times:
            raise RuntimeError("the model exploded")
        return FakeEncoder().encode(texts)


# --- a stale profile becomes fresh --------------------------------------------------------------


async def test_a_stale_profile_becomes_fresh_with_a_full_vector(
    app: object, mailer: MemoryMailer, database: Database, settings: Settings
) -> None:
    client = await student(app, mailer, "ada@northeastern.edu")
    user_id = (await client.put("/api/v1/me/profile", json=PROFILE)).json()["user_id"]

    worker = EmbeddingWorker(database, FakeEncoder(), settings)
    settled = await worker.run_once()

    assert settled == 1
    row = await fetch_embedding(database, user_id)
    assert row is not None
    assert row.stale is False
    assert row.claimed_until is None
    assert row.model_version == "fake-v1"
    assert row.source_hash is not None
    assert len(row.source_hash) == 64
    assert row.embedding is not None
    assert len(row.embedding) == EMBEDDING_DIMENSIONS


async def test_run_once_returns_zero_when_nothing_is_stale(
    database: Database, settings: Settings
) -> None:
    worker = EmbeddingWorker(database, FakeEncoder(), settings)
    assert await worker.run_once() == 0


async def test_run_once_never_exceeds_the_configured_batch_size(
    app: object, mailer: MemoryMailer, database: Database
) -> None:
    for i in range(3):
        client = await student(app, mailer, f"student{i}@northeastern.edu")
        await client.put("/api/v1/me/profile", json=PROFILE)

    small_batches = make_settings(embedding_backend="fake", embedding_batch_size=2)
    worker = EmbeddingWorker(database, FakeEncoder(), small_batches)
    settled = await worker.run_once()

    assert settled == 2


# --- unchanged text is skipped; changed text is re-encoded --------------------------------------


async def test_unchanged_text_is_marked_fresh_without_calling_encode(
    app: object, mailer: MemoryMailer, database: Database, settings: Settings
) -> None:
    client = await student(app, mailer, "ada@northeastern.edu")
    await client.put("/api/v1/me/profile", json=PROFILE)
    spy = SpyEncoder()
    worker = EmbeddingWorker(database, spy, settings)
    assert await worker.run_once() == 1
    assert spy.call_count == 1

    await client.put("/api/v1/me/profile", json=PROFILE)  # the same content, saved again
    settled = await worker.run_once()

    assert settled == 1
    assert spy.call_count == 1  # no second encode call


async def test_changed_text_is_re_encoded(
    app: object, mailer: MemoryMailer, database: Database, settings: Settings
) -> None:
    client = await student(app, mailer, "ada@northeastern.edu")
    user_id = (await client.put("/api/v1/me/profile", json=PROFILE)).json()["user_id"]
    spy = SpyEncoder()
    worker = EmbeddingWorker(database, spy, settings)
    assert await worker.run_once() == 1
    first = await fetch_embedding(database, user_id)
    assert first is not None

    await client.put("/api/v1/me/profile", json=profile(headline="A brand new headline"))
    settled = await worker.run_once()

    assert settled == 1
    assert spy.call_count == 2
    second = await fetch_embedding(database, user_id)
    assert second is not None
    assert second.source_hash != first.source_hash
    assert second.embedding != first.embedding


# --- invalid batches are rejected, never stored, and logged without the text --------------------


async def test_a_wrong_dimension_result_is_rejected_rows_stay_stale_and_no_text_is_logged(
    app: object,
    mailer: MemoryMailer,
    database: Database,
    settings: Settings,
    caplog: pytest.LogCaptureFixture,
) -> None:
    client = await student(app, mailer, "ada@northeastern.edu")
    user_id = (await client.put("/api/v1/me/profile", json=PROFILE)).json()["user_id"]

    worker = EmbeddingWorker(database, BrokenEncoder(), settings)
    with caplog.at_level(logging.ERROR, logger="app.ml"):
        settled = await worker.run_once()

    assert settled == 0
    row = await fetch_embedding(database, user_id)
    assert row is not None
    assert row.stale is True
    assert row.embedding is None
    assert row.model_version is None
    assert row.source_hash is None

    errors = [r for r in caplog.records if r.name == "app.ml" and r.levelno == logging.ERROR]
    assert len(errors) == 1
    logged = repr(errors[0].__dict__)
    assert PROFILE["headline"] not in logged
    assert PROFILE["bio"] not in logged
    assert str(user_id) in logged  # counts and user ids are fine to log


@pytest.mark.parametrize(
    "vectors",
    [
        [[math.nan] * EMBEDDING_DIMENSIONS],
        [[math.inf] * EMBEDDING_DIMENSIONS],
        [],
        [[0.1] * EMBEDDING_DIMENSIONS, [0.1] * EMBEDDING_DIMENSIONS],
    ],
    ids=["nan", "infinite", "too-few", "too-many"],
)
async def test_other_invalid_results_are_also_rejected(
    app: object,
    mailer: MemoryMailer,
    database: Database,
    settings: Settings,
    vectors: list[list[float]],
) -> None:
    client = await student(app, mailer, "ada@northeastern.edu")
    user_id = (await client.put("/api/v1/me/profile", json=PROFILE)).json()["user_id"]

    worker = EmbeddingWorker(database, BrokenEncoder(vectors=vectors), settings)
    settled = await worker.run_once()

    assert settled == 0
    row = await fetch_embedding(database, user_id)
    assert row is not None
    assert row.stale is True


async def test_a_rejected_subset_does_not_block_the_rest_of_the_same_batch(
    app: object, mailer: MemoryMailer, engine: AsyncEngine, database: Database, settings: Settings
) -> None:
    """One claim settles independent rows independently: a deletion and an unchanged-hash
    freshening still happen even though another row's encode call came back malformed."""
    good_client = await student(app, mailer, "good@northeastern.edu")
    good_id = (await good_client.put("/api/v1/me/profile", json=PROFILE)).json()["user_id"]
    assert await EmbeddingWorker(database, FakeEncoder(), settings).run_once() == 1
    await good_client.put("/api/v1/me/profile", json=PROFILE)  # unchanged: should be skipped

    suspended_client = await student(app, mailer, "suspended@northeastern.edu")
    suspended_id = (await suspended_client.put("/api/v1/me/profile", json=PROFILE)).json()[
        "user_id"
    ]
    await suspend(engine, suspended_id)

    broken_client = await student(app, mailer, "broken@northeastern.edu")
    broken_id = (
        await broken_client.put("/api/v1/me/profile", json=profile(headline="Something else"))
    ).json()["user_id"]

    # Same model version as the first pass, so "good"'s unchanged hash still matches and it is
    # skipped rather than re-encoded just because the encoder instance changed.
    broken_encoder = BrokenEncoder(model_version="fake-v1")
    settled = await EmbeddingWorker(database, broken_encoder, settings).run_once()

    assert settled == 2  # good (skipped) + suspended (deleted); broken stays stale
    assert len(broken_encoder.calls) == 1
    assert len(broken_encoder.calls[0]) == 1  # only the row that actually needed encoding

    assert (await fetch_embedding(database, good_id)).stale is False  # type: ignore[union-attr]
    assert await fetch_embedding(database, suspended_id) is None
    assert (await fetch_embedding(database, broken_id)).stale is True  # type: ignore[union-attr]


async def test_a_batch_that_always_fails_does_not_block_other_stale_rows_on_the_next_pass(
    app: object, mailer: MemoryMailer, database: Database
) -> None:
    poison_client = await student(app, mailer, "poison@northeastern.edu")
    poison_id = (
        await poison_client.put(
            "/api/v1/me/profile", json=profile(headline="POISON_MARKER headline")
        )
    ).json()["user_id"]  # created (and so claimed) first

    good_client = await student(app, mailer, "good@northeastern.edu")
    good_id = (await good_client.put("/api/v1/me/profile", json=PROFILE)).json()["user_id"]

    one_at_a_time = make_settings(embedding_backend="fake", embedding_batch_size=1)
    encoder = SelectivelyBrokenEncoder()
    worker = EmbeddingWorker(database, encoder, one_at_a_time)

    first_pass = await worker.run_once()
    assert first_pass == 0  # poison's row was claimed and rejected

    second_pass = await worker.run_once()
    assert second_pass == 1  # good was claimed instead of waiting behind poison's lease

    poison_row = await fetch_embedding(database, poison_id)
    assert poison_row is not None
    assert poison_row.stale is True
    assert poison_row.claimed_until is not None  # leased until it's retried

    good_row = await fetch_embedding(database, good_id)
    assert good_row is not None
    assert good_row.stale is False


async def test_an_expired_lease_is_reclaimed(
    app: object, mailer: MemoryMailer, engine: AsyncEngine, database: Database, settings: Settings
) -> None:
    client = await student(app, mailer, "ada@northeastern.edu")
    user_id = (await client.put("/api/v1/me/profile", json=PROFILE)).json()["user_id"]
    await lease_in_the_past(engine, user_id)  # as if a worker claimed it, then crashed

    settled = await EmbeddingWorker(database, FakeEncoder(), settings).run_once()

    assert settled == 1
    row = await fetch_embedding(database, user_id)
    assert row is not None
    assert row.stale is False
    assert row.claimed_until is None


async def test_a_profile_save_during_the_encode_is_not_blocked_and_the_stale_write_back_is_refused(
    app: object, mailer: MemoryMailer, database: Database, settings: Settings
) -> None:
    client = await student(app, mailer, "ada@northeastern.edu")
    user_id = (await client.put("/api/v1/me/profile", json=PROFILE)).json()["user_id"]

    worker = EmbeddingWorker(database, SlowSpyEncoder(delay_seconds=0.4), settings)
    encode_task = asyncio.create_task(worker.run_once())
    await asyncio.sleep(0.1)  # the claim transaction (fast) has committed; the encode is in flight

    start = time.monotonic()
    saved = await client.put("/api/v1/me/profile", json=profile(headline="Updated while encoding"))
    save_elapsed = time.monotonic() - start

    assert saved.status_code == 200, saved.text
    assert save_elapsed < 0.2  # far under the encode's 0.4s: the save never waits on it

    settled = await encode_task
    assert settled == 0  # the write-back's claim stamp no longer matches: it's refused

    row = await fetch_embedding(database, user_id)
    assert row is not None
    assert row.stale is True  # the save already requeued it with the newer text
    assert row.claimed_until is None  # the save cleared the old lease


# --- eligibility: only what a stranger could see ------------------------------------------------


@pytest.mark.parametrize(
    "make_ineligible",
    [hide, unverify, suspend],
    ids=["no-longer-discoverable", "unverified", "suspended"],
)
async def test_an_ineligible_profile_has_its_row_deleted_without_being_encoded(
    app: object,
    mailer: MemoryMailer,
    engine: AsyncEngine,
    database: Database,
    settings: Settings,
    make_ineligible: Any,
) -> None:
    client = await student(app, mailer, "ada@northeastern.edu")
    user_id = (await client.put("/api/v1/me/profile", json=PROFILE)).json()["user_id"]
    await make_ineligible(engine, user_id)

    spy = SpyEncoder()
    settled = await EmbeddingWorker(database, spy, settings).run_once()

    assert settled == 1  # deleted, not encoded
    assert spy.call_count == 0
    assert await fetch_embedding(database, user_id) is None


# --- the pipeline never sees anything but public, discoverable profile fields -------------------


async def test_the_encoder_never_receives_anything_but_public_profile_fields(
    app: object, mailer: MemoryMailer, engine: AsyncEngine, database: Database, settings: Settings
) -> None:
    ada = await student(app, mailer, "markeremail9q8w@northeastern.edu")
    grace = await student(app, mailer, "grace@northeastern.edu")
    ada_id = (await ada.get("/api/v1/me")).json()["id"]
    grace_id = (await grace.get("/api/v1/me")).json()["id"]
    await grace.put("/api/v1/me/profile", json=PROFILE)  # so Ada can see and connect with her

    saved = await ada.put(
        "/api/v1/me/profile",
        json=profile(
            display_name="MARKERDISPLAYNAME7f3a",
            headline="A headline with KEEPME_HEADLINE_MARKER",
            bio="A bio with KEEPME_BIO_MARKER",
            timezone="Pacific/Kiritimati",
            availability=[{"weekday": 3, "start_min": 543, "end_min": 777}],
        ),
    )
    assert saved.status_code == 200, saved.text

    # A direct conversation with a message only Ada's own account can see.
    sent = (await ada.post("/api/v1/connections", json={"user_id": grace_id})).json()
    accepted = (await grace.post(f"/api/v1/connections/{sent['id']}/accept")).json()
    await execute(
        engine,
        "INSERT INTO messages (id, conversation_id, author_id, client_msg_id, body)"
        " VALUES (:id, :cid, :uid, :cmid, :body)",
        id=str(uuid.uuid4()),
        cid=accepted["conversation_id"],
        uid=ada_id,
        cmid=str(uuid.uuid4()),
        body="FORBIDDEN_MESSAGE_MARKER_1a2b3c",
    )

    # A report Ada filed, with details that must never reach the encoder.
    report = await ada.post(
        "/api/v1/reports",
        json={"user_id": grace_id, "reason": "spam", "details": "FORBIDDEN_REPORT_MARKER_9z8y7x"},
    )
    assert report.status_code == 202, report.text

    # A project Ada owns, whose title and summary must never reach the encoder.
    await make_project_chat(engine, ada_id, [], title="FORBIDDEN_PROJECT_MARKER_4d5e6f")

    # Grace's own save queued her as stale too; that's fine, since the invariant under test — none
    # of the forbidden fields ever reach the encoder — must hold across the whole batch, not just
    # for Ada's row.
    spy = SpyEncoder()
    settled = await EmbeddingWorker(database, spy, settings).run_once()

    assert settled >= 1
    all_texts = [text for call in spy.calls for text in call]

    for forbidden in (
        "markeremail9q8w",
        "MARKERDISPLAYNAME7f3a",
        "Kiritimati",
        "543",
        "777",
        "FORBIDDEN_MESSAGE_MARKER_1a2b3c",
        "FORBIDDEN_REPORT_MARKER_9z8y7x",
        "FORBIDDEN_PROJECT_MARKER_4d5e6f",
    ):
        assert all(forbidden not in text for text in all_texts), forbidden

    adas_texts = [text for text in all_texts if "KEEPME_HEADLINE_MARKER" in text]
    assert len(adas_texts) == 1
    assert "KEEPME_BIO_MARKER" in adas_texts[0]


# --- concurrency ----------------------------------------------------------------------------


async def test_two_concurrent_run_once_never_encode_the_same_row(
    app: object, mailer: MemoryMailer, database: Database
) -> None:
    user_ids = []
    headlines = []
    for i in range(2):
        client = await student(app, mailer, f"racer{i}@northeastern.edu")
        headline = f"Racer headline {i}"
        user_id = (await client.put("/api/v1/me/profile", json=profile(headline=headline))).json()[
            "user_id"
        ]
        user_ids.append(user_id)
        headlines.append(headline)

    spy = SlowSpyEncoder(delay_seconds=0.2)
    one_at_a_time = make_settings(embedding_backend="fake", embedding_batch_size=1)
    worker_a = EmbeddingWorker(database, spy, one_at_a_time)
    worker_b = EmbeddingWorker(database, spy, one_at_a_time)

    settled_a, settled_b = await asyncio.gather(worker_a.run_once(), worker_b.run_once())

    assert settled_a + settled_b == 2
    encoded_texts = [text for call in spy.calls for text in call]
    assert len(encoded_texts) == 2
    assert len(set(encoded_texts)) == 2  # each racer's distinct text, encoded exactly once
    for headline in headlines:
        assert any(headline in text for text in encoded_texts), headline
    for user_id in user_ids:
        row = await fetch_embedding(database, user_id)
        assert row is not None and row.stale is False


# --- the event loop stays responsive ----------------------------------------------------------


async def _start_server(settings: Settings, mailer: MemoryMailer) -> tuple[str, Database, Any]:
    http_app = create_http_app(settings, mailer=mailer)
    sio, rooms_hooks = create_socketio_server(
        settings,
        http_app.state.db,
        http_app.state.limiter,
        http_app.state.presence,
        http_app.state.calls,
    )
    http_app.state.sio = sio
    http_app.state.room_access = rooms_hooks.recheck_room_access
    asgi_app = socketio.ASGIApp(sio, other_asgi_app=http_app)

    sock = socket_module.socket(socket_module.AF_INET, socket_module.SOCK_STREAM)
    sock.bind(("127.0.0.1", 0))
    config = uvicorn.Config(asgi_app, log_level="warning", timeout_graceful_shutdown=2)
    server = uvicorn.Server(config)
    task = asyncio.create_task(server.serve(sockets=[sock]))
    async with asyncio.timeout(10):
        while not server.started:  # noqa: ASYNC110
            await asyncio.sleep(0.01)
    host, port = sock.getsockname()

    async def stop() -> None:
        server.should_exit = True
        await task
        sock.close()

    return f"http://{host}:{port}", http_app.state.db, stop


@pytest.fixture
async def live_app(settings: Settings, mailer: MemoryMailer) -> AsyncIterator[tuple[str, Database]]:
    base_url, database, stop = await _start_server(settings, mailer)
    try:
        yield base_url, database
    finally:
        await stop()


async def test_the_event_loop_stays_responsive_while_a_slow_encoder_runs(
    live_app: tuple[str, Database], mailer: MemoryMailer, settings: Settings
) -> None:
    base_url, database = live_app
    async with httpx.AsyncClient(base_url=base_url) as http:
        client = await student(base_url, mailer, "slow@northeastern.edu")
        await client.put("/api/v1/me/profile", json=PROFILE)
        await client.aclose()

        worker = EmbeddingWorker(database, SlowSpyEncoder(delay_seconds=0.5), settings)
        # Started inside the measured window: if the encode call ever blocked the event loop
        # (instead of running in a thread), that block would show up here, not be hidden by it.
        start = time.monotonic()
        encode_task = asyncio.create_task(worker.run_once())
        await asyncio.sleep(0.05)  # let the claim transaction start before the health requests

        responses = await asyncio.gather(*(http.get("/api/v1/health") for _ in range(5)))
        elapsed = time.monotonic() - start

        assert all(response.status_code == 200 for response in responses)
        assert elapsed < 0.3  # far under the encoder's 0.5s block
        assert await encode_task == 1


# --- lazy loading -----------------------------------------------------------------------------


async def test_the_worker_disabled_app_never_constructs_an_encoder(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def boom(settings: Settings) -> None:
        raise AssertionError("create_encoder must not be called when the worker is disabled")

    monkeypatch.setattr(main_module, "create_encoder", boom)
    settings = make_settings(embedding_worker_enabled=False)
    http_app = create_http_app(settings)

    async with http_app.router.lifespan_context(http_app):
        pass

    assert http_app.state.embedding_worker is None


# --- run_forever ------------------------------------------------------------------------------


async def test_run_forever_settles_a_pending_row_then_stops_cleanly_on_cancellation(
    app: object, mailer: MemoryMailer, database: Database
) -> None:
    client = await student(app, mailer, "ada@northeastern.edu")
    user_id = (await client.put("/api/v1/me/profile", json=PROFILE)).json()["user_id"]

    fast = make_settings(embedding_backend="fake", embedding_poll_seconds=0.1)
    worker = EmbeddingWorker(database, FakeEncoder(), fast)
    task = asyncio.create_task(worker.run_forever())
    try:
        async with asyncio.timeout(5):
            while True:
                row = await fetch_embedding(database, user_id)
                if row is not None and not row.stale:
                    break
                await asyncio.sleep(0.02)
    finally:
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task

    assert task.done()
    assert task.cancelled() or task.exception() is None


async def test_run_forever_survives_an_iteration_that_raises(
    app: object,
    mailer: MemoryMailer,
    database: Database,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """The claim that raises keeps its (now five-minute) lease rather than being retried
    instantly — that's the point of the lease — but the loop itself must survive the exception
    and go on to claim and settle a *different* row on its next pass."""
    flaky_client = await student(app, mailer, "flaky@northeastern.edu")
    flaky_id = (await flaky_client.put("/api/v1/me/profile", json=PROFILE)).json()["user_id"]
    good_client = await student(app, mailer, "good@northeastern.edu")
    good_id = (await good_client.put("/api/v1/me/profile", json=PROFILE)).json()["user_id"]

    one_at_a_time = make_settings(
        embedding_backend="fake", embedding_poll_seconds=0.1, embedding_batch_size=1
    )
    worker = EmbeddingWorker(database, FlakyEncoder(fail_times=1), one_at_a_time)
    task = asyncio.create_task(worker.run_forever())
    try:
        with caplog.at_level(logging.ERROR, logger="app.ml"):
            async with asyncio.timeout(5):
                while True:
                    row = await fetch_embedding(database, good_id)
                    if row is not None and not row.stale:
                        break
                    await asyncio.sleep(0.02)
    finally:
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task

    assert any(r.name == "app.ml" and r.levelno == logging.ERROR for r in caplog.records)
    flaky_row = await fetch_embedding(database, flaky_id)
    assert flaky_row is not None
    assert flaky_row.stale is True  # leased, not retried within this test
