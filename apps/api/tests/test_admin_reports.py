import asyncio
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import pytest
from sqlalchemy.ext.asyncio import AsyncEngine

from app.core.security import SESSION_COOKIE
from tests.conftest import WEB_ORIGIN, MemoryMailer
from tests.helpers import (
    PASSWORD,
    PROFILE,
    connect,
    connect_pair,
    execute,
    login,
    make_student,
    new_client,
    scalar,
    student,
    user_id,
)

pytestmark = pytest.mark.usefixtures("clean_database")

ADMIN_EMAIL = "admin@northeastern.edu"


async def make_admin(
    app: object,
    mailer: MemoryMailer,
    engine: AsyncEngine,
    email: str = ADMIN_EMAIL,
    *,
    mfa_verified: bool = True,
) -> httpx.AsyncClient:
    """A signed-in administrator. MFA enrolment itself belongs to another module, so this stamps
    the session as already having passed it (or not, for the 403 case) straight in the database."""
    client = await student(app, mailer, email)
    uid = await user_id(client)
    await execute(engine, "UPDATE users SET role = 'admin' WHERE id = :id", id=uid)
    if mfa_verified:
        await execute(
            engine, "UPDATE sessions SET mfa_verified_at = now() WHERE user_id = :id", id=uid
        )
    return client


async def report(
    client: httpx.AsyncClient, target_id: str, reason: str = "spam", details: str = ""
) -> httpx.Response:
    return await client.post(
        "/api/v1/reports", json={"user_id": target_id, "reason": reason, "details": details}
    )


async def delete_account(client: httpx.AsyncClient, password: str = PASSWORD) -> httpx.Response:
    return await client.request("DELETE", "/api/v1/me", json={"password": password})


async def list_reports(client: httpx.AsyncClient, **params: Any) -> httpx.Response:
    return await client.get("/api/v1/admin/reports", params=params)


async def report_id_for(engine: AsyncEngine, reporter_id: str, target_id: str) -> str:
    row = await scalar(
        engine,
        "SELECT id FROM reports WHERE reporter_id = :r AND target_user_id = :t",
        r=reporter_id,
        t=target_id,
    )
    assert row is not None, "no report found for that reporter/target pair"
    return str(row)


async def open_report_ids_for(admin: httpx.AsyncClient, target_id: str) -> list[str]:
    items = (await list_reports(admin, status="open")).json()["items"]
    return [item["id"] for item in items if item["target"]["user_id"] == target_id]


async def audit_count(
    engine: AsyncEngine,
    action: str,
    *,
    admin_id: str | None = None,
    target_id: str | None = None,
    report_id: str | None = None,
) -> int:
    clauses = ["action = :action"]
    params: dict[str, object] = {"action": action}
    if admin_id is not None:
        clauses.append("admin_id = :admin_id")
        params["admin_id"] = admin_id
    if target_id is not None:
        clauses.append("target_user_id = :target_id")
        params["target_id"] = target_id
    if report_id is not None:
        clauses.append("report_id = :report_id")
        params["report_id"] = report_id
    sql = f"SELECT count(*) FROM admin_audit_log WHERE {' AND '.join(clauses)}"  # noqa: S608
    return int(str(await scalar(engine, sql, **params)))


async def set_created_at(engine: AsyncEngine, report_id: str, when: datetime) -> None:
    await execute(
        engine, "UPDATE reports SET created_at = :when WHERE id = :id", when=when, id=report_id
    )


async def insert_message(
    engine: AsyncEngine, conversation_id: str, author_id: str, body: str
) -> None:
    await execute(
        engine,
        "INSERT INTO messages (id, conversation_id, author_id, client_msg_id, body)"
        " VALUES (:id, :cid, :aid, :cm, :body)",
        id=str(uuid.uuid4()),
        cid=conversation_id,
        aid=author_id,
        cm=str(uuid.uuid4()),
        body=body,
    )


# --- access control -----------------------------------------------------------------------


ROUTES = [
    ("GET", "/api/v1/admin/reports"),
    ("GET", f"/api/v1/admin/reports/{uuid.uuid4()}"),
    ("POST", f"/api/v1/admin/reports/{uuid.uuid4()}/dismiss"),
    ("POST", f"/api/v1/admin/reports/{uuid.uuid4()}/suspend"),
]


async def test_anonymous_requests_are_unauthorized(app: object) -> None:
    async with new_client(app) as anon:
        for method, path in ROUTES:
            assert (await anon.request(method, path)).status_code == 401


async def test_students_get_404_on_every_report_route(app: object, mailer: MemoryMailer) -> None:
    client = await student(app, mailer, "ada@northeastern.edu")
    try:
        for method, path in ROUTES:
            response = await client.request(method, path)
            assert response.status_code == 404
            assert response.json()["error"]["code"] == "not_found"
    finally:
        await client.aclose()


async def test_an_admin_without_recent_mfa_gets_403(
    app: object, mailer: MemoryMailer, clean_database: AsyncEngine
) -> None:
    client = await make_admin(app, mailer, clean_database, mfa_verified=False)
    try:
        for method, path in ROUTES:
            response = await client.request(method, path)
            assert response.status_code == 403
            assert response.json()["error"]["code"] == "mfa_required"
    finally:
        await client.aclose()


# --- list -----------------------------------------------------------------------------------


async def test_open_reports_list_oldest_first_with_filters_and_paging(
    app: object, mailer: MemoryMailer, clean_database: AsyncEngine
) -> None:
    admin = await make_admin(app, mailer, clean_database)
    reporters = [await student(app, mailer, f"reporter{i}@northeastern.edu") for i in range(3)]
    target = await student(app, mailer, "target@northeastern.edu")
    target_id = await user_id(target)

    base = datetime(2026, 1, 1, tzinfo=UTC)
    report_ids = []
    for i, reporter_client in enumerate(reporters):
        assert (await report(reporter_client, target_id, reason="spam")).status_code == 202
        reporter_id = await user_id(reporter_client)
        report_id = await report_id_for(clean_database, reporter_id, target_id)
        await set_created_at(clean_database, report_id, base + timedelta(minutes=i))
        report_ids.append(report_id)

    try:
        page1 = (await list_reports(admin, limit=2)).json()
        assert [item["id"] for item in page1["items"]] == report_ids[:2]
        assert page1["next_offset"] == 2

        page2 = (await list_reports(admin, limit=2, offset=page1["next_offset"])).json()
        assert [item["id"] for item in page2["items"]] == report_ids[2:]
        assert page2["next_offset"] is None

        default_page = (await list_reports(admin)).json()
        assert [item["id"] for item in default_page["items"]] == report_ids
    finally:
        await admin.aclose()
        for reporter_client in reporters:
            await reporter_client.aclose()
        await target.aclose()


async def test_closed_reports_list_newest_first_and_filter_by_status(
    app: object, mailer: MemoryMailer, clean_database: AsyncEngine
) -> None:
    admin = await make_admin(app, mailer, clean_database)
    reporter = await student(app, mailer, "reporter@northeastern.edu")
    dismissed_target = await student(app, mailer, "dismissed@northeastern.edu")
    open_target = await student(app, mailer, "stays-open@northeastern.edu")

    dismissed_id = await user_id(dismissed_target)
    open_id = await user_id(open_target)
    reporter_id = await user_id(reporter)
    await report(reporter, dismissed_id, reason="spam")
    await report(reporter, open_id, reason="other")

    try:
        to_dismiss = await report_id_for(clean_database, reporter_id, dismissed_id)
        dismissed = await admin.post(f"/api/v1/admin/reports/{to_dismiss}/dismiss")
        assert dismissed.status_code == 200
        assert dismissed.json()["status"] == "dismissed"

        still_open = await report_id_for(clean_database, reporter_id, open_id)
        open_items = (await list_reports(admin, status="open")).json()["items"]
        assert [item["id"] for item in open_items] == [still_open]

        dismissed_items = (await list_reports(admin, status="dismissed")).json()["items"]
        assert [item["id"] for item in dismissed_items] == [to_dismiss]

        resolved_items = (await list_reports(admin, status="resolved")).json()["items"]
        assert resolved_items == []
    finally:
        await admin.aclose()
        await reporter.aclose()
        await dismissed_target.aclose()
        await open_target.aclose()


async def test_the_reporter_is_null_once_they_delete_their_own_account(
    app: object, mailer: MemoryMailer, clean_database: AsyncEngine
) -> None:
    admin = await make_admin(app, mailer, clean_database)
    reporter = await student(app, mailer, "reporter@northeastern.edu")
    target = await student(app, mailer, "target@northeastern.edu")
    target_id = await user_id(target)
    reporter_id = await user_id(reporter)

    await report(reporter, target_id, reason="impersonation")
    report_id = await report_id_for(clean_database, reporter_id, target_id)
    assert (await delete_account(reporter)).status_code == 204

    try:
        detail = (await admin.get(f"/api/v1/admin/reports/{report_id}")).json()
        assert detail["reporter"] is None
        listed = next(
            item for item in (await list_reports(admin)).json()["items"] if item["id"] == report_id
        )
        assert listed["reporter"] is None
    finally:
        await admin.aclose()
        await target.aclose()


# --- detail -----------------------------------------------------------------------------------


async def test_detail_includes_target_context_and_audits_the_view(
    app: object, mailer: MemoryMailer, clean_database: AsyncEngine
) -> None:
    admin = await make_admin(app, mailer, clean_database)
    admin_id = await user_id(admin)
    reporter = await student(app, mailer, "reporter@northeastern.edu")
    target = await student(app, mailer, "target@northeastern.edu")
    await target.put("/api/v1/me/profile", json={**PROFILE, "headline": "Looking for a team"})
    target_id = await user_id(target)
    reporter_id = await user_id(reporter)

    await report(reporter, target_id, reason="harassment", details="Kept messaging after I asked")
    report_id = await report_id_for(clean_database, reporter_id, target_id)

    try:
        detail = (await admin.get(f"/api/v1/admin/reports/{report_id}")).json()
        assert detail["reason"] == "harassment"
        assert detail["details"] == "Kept messaging after I asked"
        assert detail["target"] == {
            "user_id": target_id,
            "display_name": "Ada Lovelace",
            "suspended": False,
        }
        context = detail["target_context"]
        assert context["headline"] == "Looking for a team"
        assert context["verified"] is True
        assert context["open_reports"] == 1
        assert context["total_reports"] == 1

        assert (
            await audit_count(
                clean_database,
                "report_viewed",
                admin_id=admin_id,
                target_id=target_id,
                report_id=report_id,
            )
            == 1
        )
    finally:
        await admin.aclose()
        await reporter.aclose()
        await target.aclose()


async def test_detail_404s_for_an_unknown_report(
    app: object, mailer: MemoryMailer, clean_database: AsyncEngine
) -> None:
    admin = await make_admin(app, mailer, clean_database)
    try:
        response = await admin.get(f"/api/v1/admin/reports/{uuid.uuid4()}")
        assert response.status_code == 404
    finally:
        await admin.aclose()


async def test_no_message_body_appears_in_any_admin_response(
    live_server: str, mailer: MemoryMailer, clean_database: AsyncEngine
) -> None:
    secret = "zz-the-chat-body-must-never-leak-zz"
    ada = await make_student(live_server, mailer, "ada")
    grace = await make_student(live_server, mailer, "grace")
    conversation_id = await connect_pair(ada, grace)
    await insert_message(clean_database, conversation_id, ada.id, secret)

    admin = await make_admin(live_server, mailer, clean_database)
    await report(grace.http, ada.id, reason="inappropriate_content", details="See our chat")
    report_id = await report_id_for(clean_database, grace.id, ada.id)

    try:
        responses = [
            await list_reports(admin),
            await admin.get(f"/api/v1/admin/reports/{report_id}"),
            await admin.post(f"/api/v1/admin/reports/{report_id}/dismiss"),
        ]
        for response in responses:
            assert secret not in response.text
    finally:
        await admin.aclose()
        await ada.close()
        await grace.close()


# --- dismiss ----------------------------------------------------------------------------------


async def test_dismiss_is_audited_then_409s_on_a_repeat(
    app: object, mailer: MemoryMailer, clean_database: AsyncEngine
) -> None:
    admin = await make_admin(app, mailer, clean_database)
    admin_id = await user_id(admin)
    reporter = await student(app, mailer, "reporter@northeastern.edu")
    target = await student(app, mailer, "target@northeastern.edu")
    target_id = await user_id(target)
    reporter_id = await user_id(reporter)
    await report(reporter, target_id)
    report_id = await report_id_for(clean_database, reporter_id, target_id)

    try:
        first = await admin.post(f"/api/v1/admin/reports/{report_id}/dismiss")
        assert first.status_code == 200
        assert first.json()["status"] == "dismissed"
        assert (
            await audit_count(
                clean_database, "report_dismissed", admin_id=admin_id, report_id=report_id
            )
            == 1
        )

        again = await admin.post(f"/api/v1/admin/reports/{report_id}/dismiss")
        assert again.status_code == 409
        assert again.json()["error"]["code"] == "report_closed"
    finally:
        await admin.aclose()
        await reporter.aclose()
        await target.aclose()


# --- suspend ----------------------------------------------------------------------------------


async def test_suspending_locks_out_the_account_and_resolves_every_open_report(
    live_server: str, mailer: MemoryMailer, clean_database: AsyncEngine
) -> None:
    admin = await make_admin(live_server, mailer, clean_database)
    admin_id = await user_id(admin)
    target = await make_student(live_server, mailer, "target")
    other_reporter = await student(live_server, mailer, "other-reporter@northeastern.edu")
    bystander = await student(live_server, mailer, "bystander@northeastern.edu")

    code = (await target.http.post("/api/v1/me/connection-code/rotate")).json()
    token = code["url"].rsplit("#token=", 1)[1]

    await report(other_reporter, target.id, reason="spam")
    await report(bystander, target.id, reason="other")
    open_ids = await open_report_ids_for(admin, target.id)
    assert len(open_ids) == 2
    acted_on, other_open = open_ids[0], open_ids[1]

    response = await admin.post(f"/api/v1/admin/reports/{acted_on}/suspend")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "resolved"
    assert body["target"]["suspended"] is True

    try:
        # every session row is gone immediately, not merely left for a future lazy check
        assert (
            await scalar(
                clean_database, "SELECT count(*) FROM sessions WHERE user_id = :id", id=target.id
            )
            == 0
        )
        # the target's existing client is signed out
        assert (await target.http.get("/api/v1/me")).status_code == 401
        # they can't log back in
        login_response = await login(bystander, "target@northeastern.edu", PASSWORD)
        assert login_response.status_code == 403
        assert login_response.json()["error"]["code"] == "account_suspended"
        # they're gone from a profile lookup
        assert (await bystander.get(f"/api/v1/profiles/{target.id}")).status_code == 404
        # their connect code no longer resolves
        resolved = await bystander.post("/api/v1/connect/resolve", json={"token": token})
        assert resolved.status_code == 404
        # nobody can send them a new connection request
        requested = await bystander.post("/api/v1/connections", json={"user_id": target.id})
        assert requested.status_code == 404
        # every open report against them closed, not just the one acted on
        assert (
            await scalar(
                clean_database,
                "SELECT count(*) FROM reports WHERE target_user_id = :t AND status = 'open'",
                t=target.id,
            )
            == 0
        )
        other_status = (await admin.get(f"/api/v1/admin/reports/{other_open}")).json()["status"]
        assert other_status == "resolved"
        assert (
            await audit_count(
                clean_database,
                "account_suspended",
                admin_id=admin_id,
                target_id=target.id,
                report_id=acted_on,
            )
            == 1
        )
    finally:
        await admin.aclose()
        await other_reporter.aclose()
        await bystander.aclose()
        await target.close()


async def test_suspending_an_administrator_is_refused(
    app: object, mailer: MemoryMailer, clean_database: AsyncEngine
) -> None:
    admin = await make_admin(app, mailer, clean_database)
    other_admin = await make_admin(app, mailer, clean_database, "other-admin@northeastern.edu")
    other_admin_id = await user_id(other_admin)
    reporter = await student(app, mailer, "reporter@northeastern.edu")
    reporter_id = await user_id(reporter)
    await report(reporter, other_admin_id)
    report_id = await report_id_for(clean_database, reporter_id, other_admin_id)

    try:
        response = await admin.post(f"/api/v1/admin/reports/{report_id}/suspend")
        assert response.status_code == 409
        assert response.json()["error"]["code"] == "cannot_suspend_admin"
        assert (
            await scalar(
                clean_database, "SELECT suspended_at FROM users WHERE id = :id", id=other_admin_id
            )
            is None
        )
        status = await scalar(
            clean_database, "SELECT status FROM reports WHERE id = :id", id=report_id
        )
        assert status == "open"
    finally:
        await admin.aclose()
        await other_admin.aclose()
        await reporter.aclose()


async def test_dismiss_and_suspend_racing_the_same_report_leave_exactly_one_winner(
    app: object, mailer: MemoryMailer, clean_database: AsyncEngine
) -> None:
    admin = await make_admin(app, mailer, clean_database)
    reporter = await student(app, mailer, "reporter@northeastern.edu")
    target = await student(app, mailer, "target@northeastern.edu")
    target_id = await user_id(target)
    reporter_id = await user_id(reporter)
    await report(reporter, target_id)
    report_id = await report_id_for(clean_database, reporter_id, target_id)

    try:
        dismiss_response, suspend_response = await asyncio.gather(
            admin.post(f"/api/v1/admin/reports/{report_id}/dismiss"),
            admin.post(f"/api/v1/admin/reports/{report_id}/suspend"),
        )
        statuses = sorted([dismiss_response.status_code, suspend_response.status_code])
        assert statuses == [200, 409]
    finally:
        await admin.aclose()
        await reporter.aclose()
        await target.aclose()


async def test_suspending_disconnects_the_targets_open_socket(
    live_server: str, mailer: MemoryMailer, clean_database: AsyncEngine
) -> None:
    admin = await make_admin(live_server, mailer, clean_database)
    target = await student(live_server, mailer, "target@northeastern.edu")
    target_id = await user_id(target)
    reporter = await student(live_server, mailer, "reporter@northeastern.edu")
    reporter_id = await user_id(reporter)
    await report(reporter, target_id)
    report_id = await report_id_for(clean_database, reporter_id, target_id)

    socket = await connect(live_server, WEB_ORIGIN, target.cookies[SESSION_COOKIE])
    disconnected = asyncio.Event()
    socket.on("disconnect", lambda *_: disconnected.set())

    try:
        response = await admin.post(f"/api/v1/admin/reports/{report_id}/suspend")
        assert response.status_code == 200
        async with asyncio.timeout(5):
            await disconnected.wait()
    finally:
        await admin.aclose()
        await target.aclose()
        await reporter.aclose()
