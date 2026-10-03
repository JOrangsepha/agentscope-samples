# -*- coding: utf-8 -*-
"""Write player facts into the AgenticMemoryMiddleware file layout.

The middleware injects ``MEMORY.md`` into the system prompt on every
later session. Each index line therefore carries the fact itself, not
only a filename, because async file retrieval is left off.
"""
from __future__ import annotations

import re
from pathlib import Path

from gossip import load_entries


def remember_fact(memory_dir: Path, fact: str) -> str:
    """Append one fact to ``MEMORY.md`` and a matching topic file.

    Args:
        memory_dir: Directory that contains ``MEMORY.md``.
        fact: One sentence about the player.

    Returns:
        A short tool result for the model.
    """
    text = " ".join(fact.split())
    if not text:
        return "Nothing to remember."
    memory_dir.mkdir(parents=True, exist_ok=True)
    index_path = memory_dir / "MEMORY.md"
    existing = ""
    if index_path.exists():
        existing = index_path.read_text(encoding="utf-8")
    if text in existing:
        return f"Already remembered: {text}"
    number = existing.count("- [") + 1
    filename = f"fact_{number}.md"
    topic = (
        "---\n"
        f"name: fact {number}\n"
        "description: When the player returns and this fact is not "
        "already in context, recall it before you answer.\n"
        "type: user\n"
        "---\n\n"
        f"{text}\n"
    )
    (memory_dir / filename).write_text(topic, encoding="utf-8")
    line = f"- [Fact {number}]({filename}) — {text}\n"
    with index_path.open("a", encoding="utf-8") as handle:
        handle.write(line)
    return f"Remembered: {text}"


_RECALL_CUES = (
    "remember me",
    "how i spoke",
    "what did i say",
    "还记得我",
    "我上次怎么说",
    "我说过什么",
)
_NAME_RE = re.compile(r"name is ([A-Za-z]+)", re.IGNORECASE)
_TRADE_RE = re.compile(
    r"name is [A-Za-z]+, an? ([A-Za-z]+)",
    re.IGNORECASE,
)
_SAID_RE = re.compile(r"(?:the player|you) said:\s*(.+)", re.IGNORECASE)
_QUOTE_RE = re.compile(r'say: "(.*)"')


def asks_for_recall(text: str) -> bool:
    """True when the player asks what this NPC remembers about them."""
    lowered = text.lower().replace("’", "'")
    return any(cue in lowered for cue in _RECALL_CUES)


def recall_note(
    memory_dir: Path,
    save_dir: str | Path,
    npc_id: str,
    language: str,
) -> str:
    """Facts this NPC stored, addressed to the player as you."""
    facts = index_facts(memory_dir)
    name = ""
    trade = ""
    said = ""
    for fact in facts:
        name_match = _NAME_RE.search(fact)
        if name_match:
            name = name_match.group(1)
        trade_match = _TRADE_RE.search(fact)
        if trade_match:
            trade = trade_match.group(1)
        said_match = _SAID_RE.search(fact)
        if said_match:
            said = said_match.group(1).strip().strip(".")
    quote = insult_quote(save_dir, npc_id) or said
    return _format_recall(language, name, trade, quote)


def index_facts(memory_dir: Path) -> list[str]:
    """Sentences stored on ``MEMORY.md`` index lines."""
    path = memory_dir / "MEMORY.md"
    if not path.exists():
        return []
    facts = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if "—" not in line:
            continue
        fact = line.split("—", 1)[1].strip()
        if fact:
            facts.append(fact)
    return facts


def insult_quote(save_dir: str | Path, npc_id: str) -> str:
    """Quote from an insult this NPC heard. Empty when there is none."""
    for entry in reversed(load_entries(save_dir)):
        if entry.get("kind") != "insult" or entry.get("source") != npc_id:
            continue
        match = _QUOTE_RE.search(str(entry.get("text", "")))
        if match:
            return match.group(1).strip().rstrip(".!?。！？")
    return ""


def _format_recall(
    language: str,
    name: str,
    trade: str,
    quote: str,
) -> str:
    if language == "Simplified Chinese":
        bits = []
        if name:
            bits.append(f"你的名字是 {name}。")
        if trade:
            bits.append(f"你的职业是 {trade}。")
        if quote:
            bits.append(f"你对我说过：“{quote}”。")
        body = "".join(bits) or "我没有记下你的名字、职业或骂人的话。"
        return "你在问我记不记得。用“你说过”，不要把对方说成别人。" + "用自己的口气说，不要逐条念。" + body
    bits = []
    if name:
        bits.append(f"Your name is {name}.")
    if trade:
        bits.append(f"Your trade is {trade}.")
    if quote:
        bits.append(f'You said to me: "{quote}."')
    body = " ".join(bits) or "I have no stored name, trade, or insult."
    return (
        'You asked what I remember. Say "you said", '
        f"in your own words, not as a list. {body}"
    )
