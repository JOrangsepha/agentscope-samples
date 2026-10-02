# -*- coding: utf-8 -*-
"""Public rumors shared between residents. Private memory stays per NPC.

Shared: a strong insult, and quest news (accepted or completed).
Private: the player's name, trade, gold, and inventory. Those stay in
each NPC's own ``MEMORY.md`` and are not copied here.

``/wait`` writes a short scripted exchange. It does not call the model.
"""
from __future__ import annotations

import json
from pathlib import Path

from npc_config import TownConfig
from speech import plausible_rudeness

_FILE = "gossip.json"


def gossip_path(save_dir: str | Path) -> Path:
    """JSON file of public rumors for this save."""
    return Path(save_dir) / _FILE


def load_entries(save_dir: str | Path) -> list[dict]:
    """Return stored rumor entries. Missing files are an empty list."""
    path = gossip_path(save_dir)
    if not path.exists():
        return []
    data = json.loads(path.read_text(encoding="utf-8"))
    entries = data.get("entries", [])
    if isinstance(entries, list):
        return entries
    return []


def prompt_block(save_dir: str | Path) -> str:
    """Public rumors for the system prompt. Empty towns say so."""
    public = [
        entry
        for entry in load_entries(save_dir)
        if entry.get("kind") in {"insult", "quest"}
    ]
    if not public:
        return (
            "None yet. Insults and quest news become public. "
            "A player's name, trade, gold, and inventory stay private."
        )
    lines = [str(entry.get("text", "")) for entry in public[-6:]]
    joined = "\n".join(f"- {line}" for line in lines if line)
    return (
        f"{joined}\n"
        "The name after Heard by is the witness. "
        "When the player asks for news, name that witness and the "
        "latest insult. Do not say a different resident reported it. "
        "Do not invent a rumor or repeat private memory."
    )


def record_public_events(
    save_dir: str | Path,
    source_id: str,
    source_name: str,
    *,
    delta: int,
    player_text: str,
    quest_event: str,
) -> None:
    """Append an insult or quest rumor when this turn produced one."""
    if delta <= -2 and plausible_rudeness(player_text):
        _append(
            save_dir,
            source_id,
            source_name,
            "insult",
            _insult_line(source_name, player_text),
        )
    if quest_event == "accepted":
        _append(
            save_dir,
            source_id,
            source_name,
            "quest",
            (
                f"Heard by {source_name}: the player accepted "
                "The Lost Hammer."
            ),
        )
    elif quest_event == "completed":
        _append(
            save_dir,
            source_id,
            source_name,
            "quest",
            (
                f"Heard by {source_name}: the player completed "
                "The Lost Hammer."
            ),
        )


def narrate_wait(save_dir: str | Path, config: TownConfig) -> str:
    """Mira (or Rowan) repeats the latest public rumor. No model call."""
    public = [
        entry
        for entry in load_entries(save_dir)
        if entry.get("kind") in {"insult", "quest"}
    ]
    if not public:
        return "The square is quiet. No rumor has reached the inn."
    latest = public[-1]
    source = str(latest.get("source", ""))
    speaker_id = "rowan" if source == "mira" else "mira"
    listener_id = "bram" if speaker_id == "mira" else "mira"
    if listener_id == source:
        listener_id = "rowan" if source != "rowan" else "bram"
    speaker = config.npc(speaker_id)
    listener = config.npc(listener_id)
    rumor = str(latest.get("text", ""))
    text = (
        f"{speaker.name} tells {listener.name}: {rumor}\n"
        f"{_wait_reply(listener.name, rumor)}"
    )
    _append(save_dir, speaker_id, speaker.name, "exchange", text)
    return text


def format_log(save_dir: str | Path) -> str:
    """Plain-text gossip log for the CLI."""
    entries = load_entries(save_dir)
    if not entries:
        return "No town rumors yet."
    lines = []
    for entry in entries[-8:]:
        kind = entry.get("kind", "rumor")
        lines.append(f"[{kind}] {entry.get('text', '')}")
    return "\n".join(lines)


def _insult_line(source_name: str, player_text: str) -> str:
    """One sentence. The period stays inside the quotation."""
    said = " ".join(player_text.split()).strip("'\"")
    said = said.rstrip(".!?。！？")
    return (
        f"Heard by {source_name}: " f"The player told {source_name}: '{said}.'"
    )


def _wait_reply(listener_name: str, rumor: str) -> str:
    """Do not ask the subject of the rumor to spread it."""
    about = (
        f"Heard by {listener_name}" in rumor
        or f"told {listener_name}" in rumor
    )
    if about:
        return f"{listener_name}: I was there."
    return f"{listener_name}: I'll remember that."


def _append(
    save_dir: str | Path,
    source_id: str,
    source_name: str,
    kind: str,
    text: str,
) -> None:
    cleaned = " ".join(text.split()) if kind != "exchange" else text.strip()
    if not cleaned:
        return
    entries = load_entries(save_dir)
    if any(entry.get("text") == cleaned for entry in entries):
        return
    entries.append(
        {
            "source": source_id,
            "source_name": source_name,
            "kind": kind,
            "text": cleaned,
        },
    )
    path = gossip_path(save_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps({"entries": entries}, indent=2) + "\n",
        encoding="utf-8",
    )
