# -*- coding: utf-8 -*-
"""The web demo serves the ledger and talks through TownSession."""
from __future__ import annotations

import json
from urllib.request import Request, urlopen

from web_demo import build_session, serve


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
    finally:
        server.shutdown()
