# -*- coding: utf-8 -*-
"""The web demo serves the ledger and talks through TownSession."""
from __future__ import annotations

import asyncio
import json
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import web_demo
from mock_model import ScriptedNpcModel
from session import TownSession
from web_demo import build_session, ensure_loop, serve


def test_web_demo_talks_and_shows_state(tmp_path) -> None:
    """A mock session answers and the ledger shows gold and the NPC."""
    build_session("mock", None, str(tmp_path))
    server = serve("127.0.0.1", 0)
    port = server.server_address[1]
    base = f"http://127.0.0.1:{port}"
    try:
        with urlopen(base + "/", timeout=5) as page:
            html = page.read().decode("utf-8")
        assert "Millhaven" in html
        render_fn = html.split("function render", 1)[1]
        render_fn = render_fn.split("async function refresh", 1)[0]
        assert "current = state.npc_id" not in render_fn
        assert "Say something to" in render_fn
        with urlopen(base + "/api/state", timeout=5) as state_page:
            state = json.loads(state_page.read())
        assert state["player"]["gold"] == 12
        assert state["npc_id"] == "bram"
        body = json.dumps(
            {
                "npc_id": "bram",
                "text": "Hello, my name is Lira and I bake bread.",
            },
        ).encode("utf-8")
        request = Request(
            base + "/api/talk",
            data=body,
            headers={"Content-Type": "application/json"},
        )
        with urlopen(request, timeout=10) as talked:
            payload = json.loads(talked.read())
        assert "Lira" in payload["turn"]["reply"]
        assert payload["state"]["player"]["name"] == "Lira"
        wait_request = Request(base + "/api/wait", data=b"{}", method="POST")
        with urlopen(wait_request, timeout=5) as waited_page:
            waited = json.loads(waited_page.read())
        assert "quiet" in waited["text"]
        again = json.dumps(
            {"npc_id": "bram", "text": "Hello."},
        ).encode("utf-8")
        second = Request(
            base + "/api/talk",
            data=again,
            headers={"Content-Type": "application/json"},
        )
        with urlopen(second, timeout=10) as talked_again:
            follow = json.loads(talked_again.read())
        assert follow["turn"]["reply"]
        assert not ensure_loop().is_closed()
    finally:
        server.shutdown()


def test_talk_error_is_json_and_drops_the_line(tmp_path) -> None:
    """A model error is HTTP 500 JSON and does not keep the player line."""

    class Exploding(ScriptedNpcModel):
        """Fail before any tool result is stored."""

        async def _call_api(self, *args, **kwargs):
            raise RuntimeError("Event loop is closed")

    build_session("mock", None, str(tmp_path))
    server = serve("127.0.0.1", 0)
    port = server.server_address[1]
    base = f"http://127.0.0.1:{port}"
    try:
        session = web_demo._SESSION  # pylint: disable=protected-access
        assert session is not None
        asyncio.run(session.talk("bram", "Hello."))
        kept = _texts(session)
        session.model = Exploding()
        body = json.dumps(
            {
                "npc_id": "bram",
                "text": "Please give me a horseshoe.",
            },
        ).encode("utf-8")
        request = Request(
            base + "/api/talk",
            data=body,
            headers={"Content-Type": "application/json"},
        )
        try:
            with urlopen(request, timeout=10) as failed:
                failed.read()
            raise AssertionError("expected HTTP 500")
        except HTTPError as err:
            assert err.code == 500
            payload = json.loads(err.read())
        assert "Event loop is closed" in payload["error"]
        assert _texts(session) == kept
        assert all("horseshoe" not in text for text in kept)
    finally:
        server.shutdown()


def test_unknown_npc_is_a_client_error(tmp_path) -> None:
    """An unknown npc_id is HTTP 400 and does not talk to Bram."""
    build_session("mock", None, str(tmp_path))
    server = serve("127.0.0.1", 0)
    port = server.server_address[1]
    base = f"http://127.0.0.1:{port}"
    try:
        body = json.dumps({"npc_id": "nobody", "text": "hi"}).encode("utf-8")
        request = Request(
            base + "/api/talk",
            data=body,
            headers={"Content-Type": "application/json"},
        )
        try:
            with urlopen(request, timeout=10) as talked:
                talked.read()
            raise AssertionError("expected HTTP 400")
        except HTTPError as err:
            assert err.code == 400
            payload = json.loads(err.read())
        assert "nobody" in payload["error"]
        with urlopen(base + "/api/state", timeout=5) as state_page:
            state = json.loads(state_page.read())
        assert state["npc_id"] == "bram"
        assert state["player"]["gold"] == 12
        assert state["gossip"] == []
    finally:
        server.shutdown()


def _texts(session: TownSession) -> list[str]:
    state = session._agent_states["bram"]  # pylint: disable=protected-access
    return [message.get_text_content() or "" for message in state.context]
