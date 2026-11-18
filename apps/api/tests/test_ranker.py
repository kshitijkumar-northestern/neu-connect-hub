"""The ranker: eligibility survivors only, scored, and justified."""

import logging
import uuid
from collections.abc import Awaitable, Callable
from typing import Any

import httpx
import pytest
from sqlalchemy import event
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from app.ml.scoring import REASON_FIELDS
from app.modules.discovery import ranker as ranker_module
from app.modules.discovery.ranker import (
    CANDIDATE_POOL,
    MAX_RECOMMENDATIONS,
    Ranker,
    ViewerNotEligible,
)
from tests.conftest import MemoryMailer
from tests.helpers import (
    block,
    execute,
    make_admin,
    profile,
    student_with_profile,
    suspend,
    unverify,
    user_id,
)

# A cartesian product between the pool query's tables would silently duplicate candidates; turn
# that warning into a failure so it can never slip back in unnoticed.
pytestmark = [
    pytest.mark.usefixtures("clean_database"),
    pytest.mark.filterwarnings("error::sqlalchemy.exc.SAWarning"),
]

DIM = 384
MODEL_VERSION = "rank-v1"


def unit_vector(index: int, dim: int = DIM) -> list[float]:
    vector = [0.0] * dim
    vector[index] = 1.0
    return vector


def _vector_literal(values: list[float]) -> str:
    return "[" + ",".join(str(v) for v in values) + "]"


async def set_embedding(
    engine: AsyncEngine,
    uid: str,
    vector: list[float],
    *,
    model_version: str = MODEL_VERSION,
    stale: bool = False,
) -> None:
    """Writes straight to the embeddings table, overriding whatever the profile save queued."""
    await execute(
        engine,
        "INSERT INTO embeddings (user_id, model_version, embedding, source_hash, stale,"
        " claimed_until)"
        " VALUES (:id, :mv, (:vec)::vector, 'test-hash', :stale, NULL)"
        " ON CONFLICT (user_id) DO UPDATE SET"
        " model_version = :mv, embedding = (:vec)::vector, source_hash = 'test-hash',"
        " stale = :stale, claimed_until = NULL",
        id=uid,
        mv=model_version,
        vec=_vector_literal(vector),
        stale=stale,
    )


async def recommendations(
    session: AsyncSession, viewer_id: str, **kwargs: Any
) -> list[ranker_module.Recommendation]:
    return await Ranker(session).recommend(uuid.UUID(viewer_id), **kwargs)


# --- the viewer gate --------------------------------------------------------------------------


async def test_a_viewer_not_opted_into_discovery_raises_before_any_other_query(
    app: object, mailer: MemoryMailer, engine: AsyncEngine, db_session: AsyncSession
) -> None:
    ada = await student_with_profile(app, mailer, "ada@northeastern.edu", discoverable=False)
    viewer_id = await user_id(ada)

    statements: list[str] = []

    def listener(conn: object, cursor: object, stmt: str, *args: object) -> None:
        statements.append(stmt)

    event.listen(engine.sync_engine, "before_cursor_execute", listener)
    try:
        with pytest.raises(ViewerNotEligible):
            await recommendations(db_session, viewer_id)
    finally:
        event.remove(engine.sync_engine, "before_cursor_execute", listener)
    queries = [s for s in statements if not s.startswith("SAVEPOINT")]
    assert len(queries) == 1  # the eligibility check, and nothing past it


@pytest.mark.parametrize("limit", [0, 6, -1])
async def test_an_out_of_range_limit_is_rejected(
    app: object, mailer: MemoryMailer, db_session: AsyncSession, limit: int
) -> None:
    ada = await student_with_profile(app, mailer, "ada@northeastern.edu")
    viewer_id = await user_id(ada)
    with pytest.raises(ValueError):
        await recommendations(db_session, viewer_id, limit=limit)


# --- eligibility survives rigging: scoring never resurrects an excluded candidate -------------


VIEWER_PROFILE = profile(
    goals=["research"],
    interests=["music-and-arts"],
    skills=[{"slug": "python", "level": "advanced"}],
    seeking_skills=["ui-design"],
    availability=[{"weekday": 0, "start_min": 600, "end_min": 900}],
    timezone="America/New_York",
)

RIGGED_PROFILE = profile(
    goals=["research"],
    interests=["music-and-arts"],
    skills=[{"slug": "ui-design", "level": "advanced"}],
    seeking_skills=[],
    availability=[{"weekday": 0, "start_min": 600, "end_min": 900}],
    timezone="America/New_York",
)

WEAK_PROFILE = profile(
    goals=[],
    interests=["education"],
    skills=[],
    seeking_skills=[],
    availability=[{"weekday": 0, "start_min": 650, "end_min": 700}],
    timezone="America/New_York",
)

Mutator = Callable[[AsyncEngine, httpx.AsyncClient, httpx.AsyncClient, str, str], Awaitable[None]]


async def _not_discoverable(
    engine: AsyncEngine,
    viewer: httpx.AsyncClient,
    client: httpx.AsyncClient,
    viewer_id: str,
    uid: str,
) -> None:
    await execute(engine, "UPDATE profiles SET discoverable = false WHERE user_id = :id", id=uid)


async def _unverified(
    engine: AsyncEngine,
    viewer: httpx.AsyncClient,
    client: httpx.AsyncClient,
    viewer_id: str,
    uid: str,
) -> None:
    await unverify(engine, uid)


async def _suspended(
    engine: AsyncEngine,
    viewer: httpx.AsyncClient,
    client: httpx.AsyncClient,
    viewer_id: str,
    uid: str,
) -> None:
    await suspend(engine, uid)


async def _admin(
    engine: AsyncEngine,
    viewer: httpx.AsyncClient,
    client: httpx.AsyncClient,
    viewer_id: str,
    uid: str,
) -> None:
    await make_admin(engine, uid)


async def _blocked_by_viewer(
    engine: AsyncEngine,
    viewer: httpx.AsyncClient,
    client: httpx.AsyncClient,
    viewer_id: str,
    uid: str,
) -> None:
    await block(viewer, uid)


async def _blocking_viewer(
    engine: AsyncEngine,
    viewer: httpx.AsyncClient,
    client: httpx.AsyncClient,
    viewer_id: str,
    uid: str,
) -> None:
    await block(client, viewer_id)


async def _pending(
    engine: AsyncEngine,
    viewer: httpx.AsyncClient,
    client: httpx.AsyncClient,
    viewer_id: str,
    uid: str,
) -> None:
    response = await viewer.post("/api/v1/connections", json={"user_id": uid})
    assert response.status_code == 201, response.text


async def _accepted(
    engine: AsyncEngine,
    viewer: httpx.AsyncClient,
    client: httpx.AsyncClient,
    viewer_id: str,
    uid: str,
) -> None:
    sent = (await viewer.post("/api/v1/connections", json={"user_id": uid})).json()
    response = await client.post(f"/api/v1/connections/{sent['id']}/accept")
    assert response.status_code == 200, response.text


async def _no_availability(
    engine: AsyncEngine,
    viewer: httpx.AsyncClient,
    client: httpx.AsyncClient,
    viewer_id: str,
    uid: str,
) -> None:
    response = await client.put("/api/v1/me/profile", json={**RIGGED_PROFILE, "availability": []})
    assert response.status_code == 200, response.text


EXCLUDED_CASES: list[tuple[str, Mutator]] = [
    ("not_discoverable", _not_discoverable),
    ("unverified", _unverified),
    ("suspended", _suspended),
    ("admin", _admin),
    ("blocked_by_viewer", _blocked_by_viewer),
    ("blocking_viewer", _blocking_viewer),
    ("pending", _pending),
    ("accepted", _accepted),
    ("no_availability", _no_availability),
]


async def _build_scenario(
    app: object, mailer: MemoryMailer, engine: AsyncEngine, *, with_vectors: bool
) -> tuple[str, str]:
    """A viewer, one excluded candidate per `EXCLUDED_CASES` (all rigged with an identical,
    fully-matching profile and embedding), and one weak eligible candidate. Returns
    (viewer_id, weak_id)."""
    viewer = await student_with_profile(app, mailer, "viewer@northeastern.edu", **VIEWER_PROFILE)
    viewer_id = await user_id(viewer)
    if with_vectors:
        await set_embedding(engine, viewer_id, unit_vector(0))

    for name, mutate in EXCLUDED_CASES:
        client = await student_with_profile(
            app, mailer, f"{name}@northeastern.edu", **RIGGED_PROFILE
        )
        uid = await user_id(client)
        if with_vectors:
            await set_embedding(engine, uid, unit_vector(0))  # identical to the viewer's
        await mutate(engine, viewer, client, viewer_id, uid)
        if with_vectors and name == "no_availability":
            await set_embedding(engine, uid, unit_vector(0))  # the save above requeued it stale

    weak = await student_with_profile(app, mailer, "weak@northeastern.edu", **WEAK_PROFILE)
    weak_id = await user_id(weak)
    if with_vectors:
        await set_embedding(engine, weak_id, unit_vector(1))  # orthogonal: weak semantic signal

    return viewer_id, weak_id


async def test_rigged_excluded_candidates_never_outrank_a_weak_eligible_one_with_vectors(
    app: object, mailer: MemoryMailer, engine: AsyncEngine, db_session: AsyncSession
) -> None:
    viewer_id, weak_id = await _build_scenario(app, mailer, engine, with_vectors=True)
    results = await recommendations(db_session, viewer_id)
    assert [str(r.user_id) for r in results] == [weak_id]


async def test_the_same_holds_with_no_embeddings_at_all(
    app: object, mailer: MemoryMailer, engine: AsyncEngine, db_session: AsyncSession
) -> None:
    viewer_id, weak_id = await _build_scenario(app, mailer, engine, with_vectors=False)
    results = await recommendations(db_session, viewer_id)
    assert [str(r.user_id) for r in results] == [weak_id]
    assert results[0].fallback == "viewer_embedding_missing"


# --- fallback reasons --------------------------------------------------------------------------


async def test_with_no_vectors_every_result_falls_back_to_the_interest_jaccard(
    app: object, mailer: MemoryMailer, db_session: AsyncSession
) -> None:
    ada = await student_with_profile(
        app, mailer, "ada@northeastern.edu", interests=["music-and-arts", "education"]
    )
    await student_with_profile(app, mailer, "grace@northeastern.edu", interests=["education"])
    viewer_id = await user_id(ada)

    results = await recommendations(db_session, viewer_id)

    assert len(results) == 1
    assert results[0].fallback == "viewer_embedding_missing"
    # {education} / {music-and-arts, education} = 1/2
    assert results[0].components.semantic == pytest.approx(0.5)


async def test_a_stale_candidate_vector_falls_back_to_candidate_embedding_missing(
    app: object, mailer: MemoryMailer, engine: AsyncEngine, db_session: AsyncSession
) -> None:
    ada = await student_with_profile(
        app, mailer, "ada@northeastern.edu", interests=["music-and-arts", "education"]
    )
    grace = await student_with_profile(
        app, mailer, "grace@northeastern.edu", interests=["education"]
    )
    viewer_id, candidate_id = await user_id(ada), await user_id(grace)
    await set_embedding(engine, viewer_id, unit_vector(0))
    await set_embedding(engine, candidate_id, unit_vector(0), stale=True)

    results = await recommendations(db_session, viewer_id)

    assert len(results) == 1
    assert results[0].fallback == "candidate_embedding_missing"
    assert results[0].components.semantic == pytest.approx(0.5)


async def test_a_different_model_version_falls_back_to_model_mismatch(
    app: object, mailer: MemoryMailer, engine: AsyncEngine, db_session: AsyncSession
) -> None:
    ada = await student_with_profile(
        app, mailer, "ada@northeastern.edu", interests=["music-and-arts", "education"]
    )
    grace = await student_with_profile(
        app, mailer, "grace@northeastern.edu", interests=["education"]
    )
    viewer_id, candidate_id = await user_id(ada), await user_id(grace)
    await set_embedding(engine, viewer_id, unit_vector(0))
    await set_embedding(engine, candidate_id, unit_vector(0), model_version="some-other-model")

    results = await recommendations(db_session, viewer_id)

    assert len(results) == 1
    assert results[0].fallback == "model_mismatch"
    assert results[0].components.semantic == pytest.approx(0.5)


async def test_a_matching_fresh_vector_uses_the_semantic_component_with_no_fallback(
    app: object, mailer: MemoryMailer, engine: AsyncEngine, db_session: AsyncSession
) -> None:
    ada = await student_with_profile(app, mailer, "ada@northeastern.edu")
    grace = await student_with_profile(app, mailer, "grace@northeastern.edu")
    viewer_id, candidate_id = await user_id(ada), await user_id(grace)
    await set_embedding(engine, viewer_id, unit_vector(0))
    await set_embedding(engine, candidate_id, unit_vector(0))

    results = await recommendations(db_session, viewer_id)

    assert len(results) == 1
    assert results[0].fallback is None
    assert results[0].components.semantic == pytest.approx(1.0)


# --- ordering, limits and justification ---------------------------------------------------------


async def test_a_higher_score_ranks_first(
    app: object, mailer: MemoryMailer, db_session: AsyncSession
) -> None:
    ada = await student_with_profile(
        app,
        mailer,
        "ada@northeastern.edu",
        interests=["music-and-arts", "education", "gaming", "startups"],
    )
    viewer_id = await user_id(ada)

    strong = await student_with_profile(
        app, mailer, "strong@northeastern.edu", interests=["music-and-arts", "education"]
    )
    weak = await student_with_profile(app, mailer, "weak@northeastern.edu", interests=["gaming"])
    strong_id, weak_id = await user_id(strong), await user_id(weak)

    results = await recommendations(db_session, viewer_id, limit=1)

    assert len(results) == 1
    assert str(results[0].user_id) == strong_id
    assert strong_id != weak_id  # sanity: distinct candidates


async def test_a_true_tie_breaks_by_ascending_user_id(
    app: object, mailer: MemoryMailer, db_session: AsyncSession
) -> None:
    """Two candidates with identical features score identically; the tiebreak must then be the
    only thing deciding their order."""
    ada = await student_with_profile(app, mailer, "ada@northeastern.edu")
    viewer_id = await user_id(ada)

    # Both twins keep every field from the shared PROFILE default, so their components (and thus
    # their scores) are exactly equal.
    twin_a = await student_with_profile(app, mailer, "twin-a@northeastern.edu")
    twin_b = await student_with_profile(app, mailer, "twin-b@northeastern.edu")
    twin_a_id, twin_b_id = await user_id(twin_a), await user_id(twin_b)

    results = await recommendations(db_session, viewer_id)

    assert len(results) == 2
    assert results[0].score == pytest.approx(results[1].score)
    assert [str(r.user_id) for r in results] == sorted([twin_a_id, twin_b_id])


async def test_candidates_with_no_reasons_are_dropped(
    app: object, mailer: MemoryMailer, db_session: AsyncSession
) -> None:
    ada = await student_with_profile(
        app,
        mailer,
        "ada@northeastern.edu",
        interests=["music-and-arts"],
        goals=[],
        seeking_skills=[],
    )
    # Shares nothing: no skill overlap, no interest, no goal, and no availability overlap.
    unjustified = await student_with_profile(
        app,
        mailer,
        "unjustified@northeastern.edu",
        interests=["gaming"],
        goals=[],
        skills=[],
        seeking_skills=[],
        availability=[{"weekday": 5, "start_min": 0, "end_min": 60}],
    )
    viewer_id = await user_id(ada)
    await user_id(unjustified)

    results = await recommendations(db_session, viewer_id)
    assert results == []


async def test_needed_skills_overrides_seeking_and_outranks_a_candidate_without_it(
    app: object, mailer: MemoryMailer, db_session: AsyncSession
) -> None:
    ada = await student_with_profile(
        app, mailer, "ada@northeastern.edu", seeking_skills=["ui-design"]
    )
    viewer_id = await user_id(ada)

    covers = await student_with_profile(
        app,
        mailer,
        "covers@northeastern.edu",
        skills=[{"slug": "docker", "level": "advanced"}],
        interests=["music-and-arts"],
    )
    lacks = await student_with_profile(
        app,
        mailer,
        "lacks@northeastern.edu",
        skills=[{"slug": "python", "level": "advanced"}],
        interests=["music-and-arts"],
    )
    covers_id, lacks_id = await user_id(covers), await user_id(lacks)

    # Overridden to "docker", which only `covers` has; the viewer's own seeking_skills (ui-design)
    # is ignored for this call.
    results = await recommendations(db_session, viewer_id, needed_skills=["docker"])

    assert [str(r.user_id) for r in results] == [covers_id, lacks_id]
    assert results[0].components.complementary == pytest.approx(1.0)
    assert results[1].components.complementary == pytest.approx(0.0)


async def test_every_score_is_in_bounds_and_every_reason_kind_is_known(
    app: object, mailer: MemoryMailer, db_session: AsyncSession
) -> None:
    ada = await student_with_profile(app, mailer, "ada@northeastern.edu")
    await student_with_profile(app, mailer, "grace@northeastern.edu")
    viewer_id = await user_id(ada)

    results = await recommendations(db_session, viewer_id)

    assert results
    for result in results:
        assert 0.0 <= result.score <= 1.0
        for reason in result.reasons:
            assert reason.kind in REASON_FIELDS


# --- the pool bound ------------------------------------------------------------------------------


async def test_the_pool_bound_only_scores_the_nearest_candidates_by_vector(
    app: object,
    mailer: MemoryMailer,
    engine: AsyncEngine,
    db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ada = await student_with_profile(app, mailer, "ada@northeastern.edu")
    viewer_id = await user_id(ada)
    await set_embedding(engine, viewer_id, unit_vector(0))

    candidate_ids = []
    for i in range(4):
        client = await student_with_profile(app, mailer, f"candidate{i}@northeastern.edu")
        uid = await user_id(client)
        # Candidate 0 and 1 are exactly the viewer's vector (distance 0); 2 and 3 are orthogonal.
        await set_embedding(engine, uid, unit_vector(0) if i < 2 else unit_vector(i + 1))
        candidate_ids.append(uid)

    monkeypatch.setattr(ranker_module, "CANDIDATE_POOL", 2)
    results = await recommendations(db_session, viewer_id)

    returned_ids = {str(r.user_id) for r in results}
    assert returned_ids == set(candidate_ids[:2])


# --- query count: constant regardless of pool size ------------------------------------------------


async def _count_statements(engine: AsyncEngine, run: Callable[[], Awaitable[object]]) -> int:
    statements: list[str] = []

    def listener(conn: object, cursor: object, stmt: str, *args: object) -> None:
        statements.append(stmt)

    event.listen(engine.sync_engine, "before_cursor_execute", listener)
    try:
        await run()
    finally:
        event.remove(engine.sync_engine, "before_cursor_execute", listener)
    return len(statements)


async def test_the_statement_count_does_not_grow_with_the_pool_size(
    app: object, mailer: MemoryMailer, engine: AsyncEngine
) -> None:
    from tests.conftest import rolled_back_session

    async def scenario(n: int) -> str:
        ada = await student_with_profile(app, mailer, f"viewer{n}@northeastern.edu")
        viewer_id = await user_id(ada)
        for i in range(n):
            await student_with_profile(app, mailer, f"viewer{n}-candidate{i}@northeastern.edu")
        return viewer_id

    small_viewer = await scenario(2)
    async with rolled_back_session(engine) as session:
        small_count = await _count_statements(
            engine, lambda: recommendations(session, small_viewer)
        )

    large_viewer = await scenario(6)
    async with rolled_back_session(engine) as session:
        large_count = await _count_statements(
            engine, lambda: recommendations(session, large_viewer)
        )

    assert small_count == large_count


# --- no private fields -----------------------------------------------------------------------


async def test_the_ranker_never_selects_user_email(
    app: object, mailer: MemoryMailer, engine: AsyncEngine
) -> None:
    from tests.conftest import rolled_back_session

    ada = await student_with_profile(app, mailer, "ada@northeastern.edu")
    await student_with_profile(app, mailer, "grace@northeastern.edu")
    viewer_id = await user_id(ada)

    statements: list[str] = []

    def listener(conn: object, cursor: object, stmt: str, *args: object) -> None:
        statements.append(stmt)

    event.listen(engine.sync_engine, "before_cursor_execute", listener)
    try:
        async with rolled_back_session(engine) as session:
            await recommendations(session, viewer_id)
    finally:
        event.remove(engine.sync_engine, "before_cursor_execute", listener)

    assert statements
    assert all("email" not in stmt.lower() for stmt in statements)


async def test_match_features_carries_no_display_name_email_or_bio() -> None:
    from dataclasses import fields

    from app.ml.scoring import MatchFeatures

    field_names = {f.name for f in fields(MatchFeatures)}
    assert "display_name" not in field_names
    assert "email" not in field_names
    assert "bio" not in field_names


async def test_max_recommendations_and_candidate_pool_constants() -> None:
    assert MAX_RECOMMENDATIONS == 5
    assert CANDIDATE_POOL == 500


# --- no duplicate candidates (a cartesian product in the pool query would double them) --------


async def test_the_pool_never_returns_a_duplicate_candidate(
    app: object, mailer: MemoryMailer, db_session: AsyncSession
) -> None:
    ada = await student_with_profile(app, mailer, "ada@northeastern.edu")
    viewer_id = await user_id(ada)
    for i in range(3):
        await student_with_profile(app, mailer, f"friend{i}@northeastern.edu")

    results = await recommendations(db_session, viewer_id)

    ids = [str(r.user_id) for r in results]
    assert len(ids) == len(set(ids))
    assert len(ids) == 3  # none dropped, none duplicated


# --- exclude -------------------------------------------------------------------------------


async def test_exclude_drops_an_otherwise_eligible_candidate(
    app: object, mailer: MemoryMailer, db_session: AsyncSession
) -> None:
    ada = await student_with_profile(app, mailer, "ada@northeastern.edu")
    viewer_id = await user_id(ada)
    kept = await student_with_profile(app, mailer, "kept@northeastern.edu")
    excluded = await student_with_profile(app, mailer, "excluded@northeastern.edu")
    kept_id, excluded_id = await user_id(kept), await user_id(excluded)

    results = await recommendations(db_session, viewer_id, exclude=[uuid.UUID(excluded_id)])

    returned_ids = {str(r.user_id) for r in results}
    assert kept_id in returned_ids
    assert excluded_id not in returned_ids


# --- the log line carries counts only, never identifying detail ----------------------------


async def test_the_log_line_carries_only_counts(
    app: object,
    mailer: MemoryMailer,
    engine: AsyncEngine,
    db_session: AsyncSession,
    caplog: pytest.LogCaptureFixture,
) -> None:
    ada = await student_with_profile(
        app, mailer, "ada@northeastern.edu", interests=["music-and-arts", "education"]
    )
    viewer_id = await user_id(ada)
    fresh = await student_with_profile(app, mailer, "fresh@northeastern.edu")
    fresh_id = await user_id(fresh)
    await set_embedding(engine, viewer_id, unit_vector(0))
    await set_embedding(engine, fresh_id, unit_vector(0))
    await student_with_profile(app, mailer, "fallback@northeastern.edu", interests=["education"])

    with caplog.at_level(logging.INFO, logger="app.ml"):
        await recommendations(db_session, viewer_id)

    records = [r for r in caplog.records if r.name == "app.ml" and r.levelno == logging.INFO]
    assert len(records) == 1
    record = records[0]
    assert record.getMessage() == "Computed discovery recommendations"

    fields = vars(record)
    assert fields["pool_size"] == 2
    assert fields["returned"] == 2
    assert isinstance(fields["fallbacks"], dict)

    # Nothing identifying (ids, names, skill or interest text) ever reaches the log line.
    logged = repr(fields)
    forbidden = [
        str(viewer_id),
        fresh_id,
        "Ada",
        "Fresh",
        "Fallback",
        "music-and-arts",
        "education",
        "ui-design",
    ]
    for token in forbidden:
        assert token not in logged, token
