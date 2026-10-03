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
    public = _public_entries(save_dir)
    if not public:
        return (
            "None yet. Insults and quest news become public. "
            "A player's name, trade, gold, and inventory stay private."
        )
    lines = [str(entry.get("text", "")) for entry in public[-6:]]
    joined = "\n".join(f"- {line}" for line in lines if line)
    return (
        f"{joined}\n"
        "Each line names who heard it. An insult was said by "
        "the player, not by that resident. Do not invent a rumor."
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
            (f"{source_name} heard the player accepted " "The Lost Hammer."),
        )
    elif quest_event == "completed":
        _append(
            save_dir,
            source_id,
            source_name,
            "quest",
            (f"{source_name} heard the player completed " "The Lost Hammer."),
        )


def narrate_wait(save_dir: str | Path, config: TownConfig) -> str:
    """Mira (or Rowan) repeats the latest public rumor. No model call."""
    public = _public_entries(save_dir)
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


_NEWS_CUES = (
    "gossip",
    "rumor",
    "rumour",
    "what news",
    "any news",
    "latest news",
    "what's new",
    "heard anything",
    "any rumors",
    "heard about me",
    "what do people",
    "complain",
    "complained",
    "新鲜事",
    "什么消息",
    "传闻",
    "闲话",
    "新闻",
    "镇上有什么事",
    "最近有什么事",
    "有什么新鲜事",
    "投诉",
    "抱怨",
    "有人说我",
)


def asks_for_news(text: str) -> bool:
    """True when this player line is asking what the town is saying."""
    lowered = text.lower().replace("’", "'")
    return any(cue in lowered for cue in _NEWS_CUES)


def news_to_repeat(
    save_dir: str | Path,
    language: str,
    listener_name: str = "",
) -> str:
    """Facts the speak step must say. Empty when nobody asked or none exist.

    An insult names its target: the resident who heard the player say it.
    The listener is that target only when they are the same resident.
    """
    public = _public_entries(save_dir)
    if not public:
        return ""
    chosen = [
        _to_you(str(public[-1].get("text", "")).strip(), listener_name),
    ]
    insults = [entry for entry in public if entry.get("kind") == "insult"]
    target = ""
    if insults:
        insult = _to_you(
            str(insults[-1].get("text", "")).strip(),
            listener_name,
        )
        target = str(insults[-1].get("source_name", "")).strip()
        if insult and insult not in chosen:
            chosen.append(insult)
    facts = " ".join(line for line in chosen if line)
    if not facts:
        return ""
    who = _insult_target_sentence(language, listener_name, target)
    tail = f"{who} {facts}".strip()
    if language == "Simplified Chinese":
        prefix = "你在打听消息。用自己的口气说，不要逐条念。"
        return f"{prefix}{tail}"
    return (
        "You asked for news. Retell this in your own words, "
        f"not as a list. {tail}"
    )


def _insult_target_sentence(
    language: str,
    listener_name: str,
    target_name: str,
) -> str:
    """Say who was insulted. Empty when this rumor is not an insult."""
    if not target_name:
        return ""
    listener = listener_name.strip()
    same = bool(listener) and listener == target_name
    if language == "Simplified Chinese":
        if same:
            sentence = "你骂的是我，我听见了。不要说成别人被骂。"
        elif listener:
            sentence = (
                f"你侮辱的是{target_name}，{target_name}听见了。"
                f"你是{listener}，不是被骂的人。不要说这句是在骂你。"
            )
        else:
            sentence = (
                f"你侮辱的是{target_name}，{target_name}听见了。"
                f"听的人不是被骂的人，除非听的人就是{target_name}。"
            )
        return sentence
    if same:
        sentence = (
            "You insulted me; I heard it. "
            "Do not name someone else as the target."
        )
    elif listener:
        sentence = (
            f"You insulted {target_name}; {target_name} heard it. "
            f"You are {listener}. You were not insulted. "
            "Do not say the insult was about you."
        )
    else:
        sentence = (
            f"You insulted {target_name}; {target_name} heard it. "
            "The listener is not the target unless they are that person."
        )
    return sentence


def _to_you(text: str, listener_name: str) -> str:
    """Address the player as you. Only the news injection uses this."""
    listener = listener_name.strip()
    own = f"{listener} heard the player"
    if listener and text.startswith(own):
        return "I heard you" + text[len(own) :]
    return text.replace(" heard the player ", " heard you ")


def _public_entries(save_dir: str | Path) -> list[dict]:
    return [
        entry
        for entry in load_entries(save_dir)
        if entry.get("kind") in {"insult", "quest"}
    ]


def _insult_line(source_name: str, player_text: str) -> str:
    """One sentence. The period stays inside the quotation."""
    said = " ".join(player_text.split()).strip("'\"")
    said = said.rstrip(".!?。！？")
    return f'{source_name} heard the player say: "{said}."'


def _wait_reply(listener_name: str, rumor: str) -> str:
    """Do not ask the subject of the rumor to spread it."""
    if rumor.startswith(f"{listener_name} heard"):
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
