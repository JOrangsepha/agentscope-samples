# -*- coding: utf-8 -*-
"""Write player facts into the AgenticMemoryMiddleware file layout.

The middleware injects ``MEMORY.md`` into the system prompt on every
later session. Each index line therefore carries the fact itself, not
only a filename, because async file retrieval is left off.
"""
from __future__ import annotations

from pathlib import Path


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
