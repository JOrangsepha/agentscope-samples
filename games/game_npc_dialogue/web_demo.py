# -*- coding: utf-8 -*-
"""Browser UI for one Millhaven visit. Uses the same TownSession as the CLI.

The page and the JSON API are served by the Python standard library.
No extra web framework is required.

    python web_demo.py --provider mock --port 8765

Open http://127.0.0.1:8765
"""
from __future__ import annotations

import argparse
import asyncio
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import agentscope

from gossip import load_entries
from model_factory import PROVIDERS, build_chat_model, resolve_provider
from npc_config import load_town_config
from session import TownSession

_INDEX = Path(__file__).resolve().parent / "web" / "index.html"
_LOCK = threading.Lock()
_LOOP_LOCK = threading.Lock()
_SESSION: TownSession | None = None
_NPC = "bram"
_LOOP: asyncio.AbstractEventLoop | None = None


def ensure_loop() -> asyncio.AbstractEventLoop:
    """One loop for every request, so client connections stay open."""
    global _LOOP
    with _LOOP_LOCK:
        if _LOOP is not None and not _LOOP.is_closed():
            return _LOOP
        loop = asyncio.new_event_loop()
        ready = threading.Event()

        def _spin() -> None:
            asyncio.set_event_loop(loop)
            ready.set()
            loop.run_forever()

        threading.Thread(
            target=_spin,
            name="millhaven-loop",
            daemon=True,
        ).start()
        ready.wait()
        _LOOP = loop
        return loop


def _run(coro):
    """Run ``coro`` on the shared loop and return its result."""
    future = asyncio.run_coroutine_threadsafe(coro, ensure_loop())
    return future.result()


def parse_args() -> argparse.Namespace:
    """CLI flags. Provider defaults the same way as main.py."""
    parser = argparse.ArgumentParser(description="Millhaven web demo.")
    parser.add_argument("--provider", choices=PROVIDERS, default=None)
    parser.add_argument("--model", default=None)
    parser.add_argument("--save-dir", default="save")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    return parser.parse_args()


def build_session(
    provider: str,
    model_name: str | None,
    save_dir: str,
) -> None:
    """Create the process-wide session used by every request."""
    global _SESSION
    ensure_loop()
    config = load_town_config()
    model = build_chat_model(provider, model_name)
    _SESSION = TownSession(config, save_dir, model)


def state_payload() -> dict:
    """Ledger shown beside the chat."""
    assert _SESSION is not None
    game = _SESSION.game
    config = _SESSION.config
    quests = []
    for quest_id, quest in game.data["quests"].items():
        quests.append(
            {
                "id": quest_id,
                "title": quest["title"],
                "status": quest["status"],
            },
        )
    residents = []
    for npc_id, npc in config.npcs.items():
        residents.append(
            {
                "id": npc_id,
                "name": npc.name,
                "role": npc.role,
                "affinity": game.affinity(npc_id),
                "emotion": game.emotion(npc_id),
            },
        )
    return {
        "town": config.town_name,
        "model": _SESSION.model.model,
        "npc_id": _NPC,
        "player": {
            "name": game.player_name,
            "gold": game.gold,
            "inventory": list(game.inventory),
        },
        "quests": quests,
        "residents": residents,
        "gossip": load_entries(_SESSION.save_dir)[-8:],
    }


class _Handler(BaseHTTPRequestHandler):
    """JSON API plus the single static page."""

    def do_GET(self) -> None:  # pylint: disable=invalid-name
        """Serve the page or the current ledger."""
        try:
            self._get()
        except Exception as exc:  # pylint: disable=broad-exception-caught
            self._send_error(exc)

    def _get(self) -> None:
        """Pages and the ledger."""
        if self.path.split("?", 1)[0] in {"/", "/index.html"}:
            body = _INDEX.read_bytes()
            self._send(200, "text/html; charset=utf-8", body)
            return
        if self.path.split("?", 1)[0] == "/api/state":
            self._send_json(state_payload())
            return
        self._send(404, "text/plain; charset=utf-8", b"not found")

    def do_POST(self) -> None:  # pylint: disable=invalid-name
        """Talk, or let the town repeat a rumor."""
        try:
            self._post()
        except Exception as exc:  # pylint: disable=broad-exception-caught
            self._send_error(exc)

    def _post(self) -> None:
        """Talk and wait endpoints."""
        path = self.path.split("?", 1)[0]
        payload = self._read_json()
        if path == "/api/talk":
            self._send_json(_talk(payload))
            return
        if path == "/api/wait":
            self._send_json(_wait())
            return
        self._send(404, "text/plain; charset=utf-8", b"not found")

    def log_message(self, fmt: str, *args) -> None:
        """Keep the demo terminal quiet."""
        del fmt, args

    def _read_json(self) -> dict:
        length = int(self.headers.get("Content-Length", "0") or 0)
        raw = self.rfile.read(length) if length else b"{}"
        data = json.loads(raw.decode("utf-8") or "{}")
        if isinstance(data, dict):
            return data
        return {}

    def _send(self, status: int, content_type: str, body: bytes) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _send_json(self, payload: dict, status: int = 200) -> None:
        body = json.dumps(payload).encode("utf-8")
        self._send(status, "application/json; charset=utf-8", body)

    def _send_error(self, exc: BaseException) -> None:
        self._send_json({"error": str(exc)}, status=500)


def _talk(payload: dict) -> dict:
    global _NPC
    assert _SESSION is not None
    npc_id = str(payload.get("npc_id") or _NPC)
    text = str(payload.get("text") or "").strip()
    if npc_id not in _SESSION.config.npcs:
        npc_id = _NPC
    _NPC = npc_id
    if not text:
        return {"turn": None, "state": state_payload()}
    with _LOCK:
        result = _run(_SESSION.talk(npc_id, text))
    return {
        "turn": {
            "npc_name": result.npc_name,
            "reply": result.reply,
            "emotion": result.emotion,
            "affinity": result.affinity,
            "affinity_delta": result.affinity_delta,
        },
        "state": state_payload(),
    }


def _wait() -> dict:
    assert _SESSION is not None
    with _LOCK:
        text = _SESSION.wait_in_town()
    return {"text": text, "state": state_payload()}


def serve(host: str, port: int) -> ThreadingHTTPServer:
    """Start the server. ``port`` 0 asks the OS for a free port."""
    server = ThreadingHTTPServer((host, port), _Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server


def main() -> None:
    """Entry point."""
    agentscope.setup_logger("WARNING")
    args = parse_args()
    provider = resolve_provider(args.provider)
    build_session(provider, args.model, args.save_dir)
    server = serve(args.host, args.port)
    bound = server.server_address[0]
    host = bound.decode() if isinstance(bound, bytes) else bound
    port = server.server_address[1]
    print(f"Millhaven web demo at http://{host}:{port}")
    print("Ctrl-C to stop.")
    try:
        threading.Event().wait()
    except KeyboardInterrupt:
        server.shutdown()


if __name__ == "__main__":
    main()
