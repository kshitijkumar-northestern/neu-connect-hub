"""The team chat: a project's conversation is shared by every member but listed once, and its
messages carry the author's display name. The teams HTTP routes are built elsewhere, so projects
here are created straight in the database, the way those routes will do it."""

import asyncio

import pytest
from sqlalchemy.ext.asyncio import AsyncEngine

from tests.conftest import MemoryMailer
from tests.helpers import (
    PASSWORD,
    Student,
    connect_pair,
    eventually,
    history,
    make_project_chat,
    make_student,
    scalar,
    student,
    user_id,
)

pytestmark = pytest.mark.usefixtures("clean_database")


async def test_project_conversations_are_listed_once_with_the_project_name(
    live_server: str, mailer: MemoryMailer, clean_database: AsyncEngine
) -> None:
    ada = await make_student(live_server, mailer, "ada")
    grace = await make_student(live_server, mailer, "grace")
    eve = await make_student(live_server, mailer, "eve")
    direct = await connect_pair(ada, grace)
    project_id, team_chat = await make_project_chat(
        clean_database, ada.id, [grace.id, eve.id], title="Falcon"
    )

    try:
        items = (await ada.http.get("/api/v1/conversations")).json()
        assert len(items) == 2
        by_id = {item["id"]: item for item in items}

        team_item = by_id[team_chat]
        assert (
            team_item["kind"],
            team_item["name"],
            team_item["project_id"],
            team_item["other"],
        ) == (
            "project",
            "Falcon",
            project_id,
            None,
        )

        direct_item = by_id[direct]
        assert (direct_item["kind"], direct_item["name"], direct_item["project_id"]) == (
            "direct",
            None,
            None,
        )
        assert direct_item["other"]["user_id"] == grace.id

        # Every member sees the team chat exactly once, whatever the team size.
        for student_ in (grace, eve):
            theirs = (await student_.http.get("/api/v1/conversations")).json()
            assert [item["id"] for item in theirs if item["id"] == team_chat] == [team_chat]
    finally:
        for s in (ada, grace, eve):
            await s.close()


async def test_get_conversation_returns_a_project_chat_to_members_only(
    live_server: str, mailer: MemoryMailer, clean_database: AsyncEngine
) -> None:
    ada = await make_student(live_server, mailer, "ada")
    grace = await make_student(live_server, mailer, "grace")
    outsider = await make_student(live_server, mailer, "eve")
    project_id, team_chat = await make_project_chat(
        clean_database, ada.id, [grace.id], title="Orbit"
    )

    try:
        response = await grace.http.get(f"/api/v1/conversations/{team_chat}")
        assert response.status_code == 200
        body = response.json()
        assert (body["kind"], body["project_id"], body["name"], body["other"]) == (
            "project",
            project_id,
            "Orbit",
            None,
        )
        assert (await outsider.http.get(f"/api/v1/conversations/{team_chat}")).status_code == 404
    finally:
        for s in (ada, grace, outsider):
            await s.close()


async def test_team_messages_carry_author_name_in_the_live_event_ack_and_history(
    live_server: str, mailer: MemoryMailer, clean_database: AsyncEngine
) -> None:
    ada = await make_student(live_server, mailer, "ada")
    grace = await make_student(live_server, mailer, "grace")
    noprof_http = await student(live_server, mailer, "noprof@northeastern.edu")
    noprof_id = await user_id(noprof_http)
    noprof = Student(http=noprof_http, id=noprof_id, base_url=live_server)
    _, team_chat = await make_project_chat(
        clean_database, ada.id, [grace.id, noprof.id], title="Rocket"
    )

    try:
        for s in (ada, grace, noprof):
            await s.open_socket()

        ack_ada = await ada.send(team_chat, "hello team")
        assert ack_ada["ok"] is True and ack_ada["message"]["author_name"] == "Ada"

        ack_noprof = await noprof.send(team_chat, "hi from nobody")
        assert ack_noprof["ok"] is True and ack_noprof["message"]["author_name"] is None

        ack_grace = await grace.send(team_chat, "grace says hi")
        assert ack_grace["ok"] is True and ack_grace["message"]["author_name"] == "Grace"

        await eventually(lambda: len(noprof.received()) == 3)
        assert [m["author_name"] for m in noprof.received()] == ["Ada", None, "Grace"]

        # Grace leaves the project by deleting her account; her message becomes a tombstone,
        # the chat and the other members' messages stay.
        deleted = await grace.http.request("DELETE", "/api/v1/me", json={"password": PASSWORD})
        assert deleted.status_code == 204

        page = (await history(ada, team_chat)).json()
        removed = [m for m in page["messages"] if m["removed"]]
        assert len(removed) == 1 and removed[0]["author_name"] is None
        kept = {m["body"]: m["author_name"] for m in page["messages"] if not m["removed"]}
        assert kept == {"hello team": "Ada", "hi from nobody": None}
        assert await scalar(clean_database, "SELECT count(*) FROM projects") == 1
    finally:
        for s in (ada, grace, noprof):
            await s.close()


async def test_a_block_between_teammates_stops_their_presence_but_not_a_thirds(
    live_server: str, mailer: MemoryMailer, clean_database: AsyncEngine
) -> None:
    owner = await make_student(live_server, mailer, "owner")
    ada = await make_student(live_server, mailer, "ada")
    bea = await make_student(live_server, mailer, "bea")
    cleo = await make_student(live_server, mailer, "cleo")
    # ada, bea and cleo are ordinary members of the same team: a block between two of them
    # changes nothing about their membership, so the leak has to be plugged in presence itself.
    await make_project_chat(clean_database, owner.id, [ada.id, bea.id, cleo.id], title="Squad")

    try:
        for s in (ada, bea, cleo):
            await s.open_socket()
        await asyncio.sleep(0.3)  # let the join announcements settle before the block

        assert (await bea.http.post("/api/v1/blocks", json={"user_id": ada.id})).status_code == 204
        for s in (ada, bea, cleo):
            s.events.clear()

        # ada goes offline then online: cleo (unblocked teammate) hears both; bea (blocked
        # either way) hears neither.
        assert ada.socket is not None
        await ada.socket.disconnect()
        await eventually(lambda: cleo.received("presence.changed"))
        assert cleo.received("presence.changed") == [{"user_id": ada.id, "online": False}]
        await asyncio.sleep(0.2)
        assert bea.received("presence.changed") == []

        for s in (ada, bea, cleo):
            s.events.clear()
        await ada.open_socket()
        await eventually(lambda: cleo.received("presence.changed"))
        assert cleo.received("presence.changed") == [{"user_id": ada.id, "online": True}]
        await asyncio.sleep(0.2)
        assert bea.received("presence.changed") == []

        # The other direction: bea goes offline, ada hears nothing, cleo still does.
        for s in (ada, bea, cleo):
            s.events.clear()
        assert bea.socket is not None
        await bea.socket.disconnect()
        await eventually(lambda: cleo.received("presence.changed"))
        assert cleo.received("presence.changed") == [{"user_id": bea.id, "online": False}]
        await asyncio.sleep(0.2)
        assert ada.received("presence.changed") == []
    finally:
        for s in (owner, ada, bea, cleo):
            await s.close()


async def test_messaging_a_project_chat_needs_membership(
    live_server: str, mailer: MemoryMailer, clean_database: AsyncEngine
) -> None:
    ada = await make_student(live_server, mailer, "ada")
    outsider = await make_student(live_server, mailer, "eve")
    _, team_chat = await make_project_chat(clean_database, ada.id, [])

    try:
        await outsider.open_socket()
        ack = await outsider.send(team_chat, "let me in")
        assert ack == {
            "ok": False,
            "error": {"code": "not_found", "message": ack["error"]["message"]},
        }
        assert (await history(outsider, team_chat)).status_code == 404
    finally:
        for s in (ada, outsider):
            await s.close()
