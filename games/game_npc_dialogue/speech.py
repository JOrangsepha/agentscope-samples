# -*- coding: utf-8 -*-
"""Clean the line that is actually shown to the player."""
from __future__ import annotations

import re

SPEAK_CUE = "SPEAK_NOW"

_EMOTIONS = {
    "neutral",
    "happy",
    "annoyed",
    "grateful",
    "warm",
    "suspicious",
}
_STAGE = re.compile(r"\*[^*]*\*")
_SENTENCE = re.compile(r"(?<=[.!?。！？])\s+")
_EN_NAME = re.compile(
    r"\bmy name is\s+([A-Za-z][A-Za-z'-]{0,30})",
    re.IGNORECASE,
)
_CN_NAME = re.compile(r"我叫\s*([A-Za-z\u4e00-\u9fff]{1,12})")


def learn_player_name(text: str) -> str | None:
    """Return a name from an explicit introduction, if one is present."""
    english = _EN_NAME.search(text)
    if english is not None:
        return english.group(1).capitalize()
    chinese = _CN_NAME.search(text)
    if chinese is not None:
        return chinese.group(1)
    return None


def polish_reply(text: str, affinity_reason: str = "") -> str:
    """Drop stage directions, leaked fields, and sentences after the second.

    Args:
        text: The model's spoken reply.
        affinity_reason: If this exact line was copied into the reply, drop it.
    """
    cleaned = _STAGE.sub(" ", text)
    reason = affinity_reason.strip().lower()
    kept: list[str] = []
    for line in cleaned.splitlines():
        stripped = " ".join(line.split())
        if not stripped:
            continue
        lowered = stripped.lower()
        if lowered in _EMOTIONS or lowered == reason:
            continue
        if re.fullmatch(r"[+-]?\d+", stripped):
            continue
        kept.append(stripped)
    merged = " ".join(kept).strip()
    if not merged:
        return ""
    parts = [part.strip() for part in _SENTENCE.split(merged) if part.strip()]
    return " ".join(parts[:2])
