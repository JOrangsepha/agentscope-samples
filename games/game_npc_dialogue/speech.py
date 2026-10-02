# -*- coding: utf-8 -*-
"""Player language, names, and the line that is shown."""
from __future__ import annotations

import re
from difflib import get_close_matches

SPEAK_CUE = "SPEAK_NOW"
ACT_CUE = "ACT_NOW"
HONEST_EN = "I have not given you anything, and your coins are unchanged."
HONEST_ZH = "背包和金币都没有变化。"

_EMOTIONS = {
    "neutral",
    "happy",
    "annoyed",
    "grateful",
    "warm",
    "suspicious",
}
_EMOTION_ALIASES = {
    "irritated": "annoyed",
    "angry": "annoyed",
    "mad": "annoyed",
    "anger": "annoyed",
    "calm": "neutral",
    "content": "neutral",
    "relaxed": "neutral",
    "pleased": "happy",
    "joyful": "happy",
    "glad": "happy",
    "thankful": "grateful",
    "friendly": "warm",
    "kind": "warm",
    "wary": "suspicious",
    "distrustful": "suspicious",
}
_REPLY_LIMIT = 240
_SHORT_SENTENCE = 24
_STAGE = re.compile(r"\*[^*]*\*")
_END = re.compile(r"([.!?。！？][\"”’')]?)")
_CJK = re.compile(r"[\u4e00-\u9fff]")
_EN_NAME = re.compile(r"\bmy name is\s+(.+)", re.IGNORECASE)
_EN_WORD = re.compile(r"[A-Za-z][A-Za-z'-]*")
_CN_NAME = re.compile(r"我叫\s*([A-Za-z\u4e00-\u9fff·]{1,16})")
_EN_STOP = {"and", "from", "the", "a", "an", "but", "who", "i", "im", "i'm"}
_EN_BAD = {"not", "no", "none", "nobody", "nothing", "unknown"}
_CN_BAD = ("什么", "啥", "谁", "哪", "吗", "么", "呢")
_GIFT = (
    "here you go",
    "here. one",
    "here is one",
    "here's one",
    "take the ",
    "take this",
    "给你",
    "拿去",
    "送给你",
)
_BARE_GIFT = ("here you go", "here. one", "take this", "给你", "拿去", "送给你")
# A paid service often starts with these. They are not a gift claim
# when charge_player already succeeded.
_SERVICE_HANDOFF = ("here you go", "给你")
CHARGED_EN = "You were charged, but I have not given you an item."
CHARGED_ZH = "金币已经扣过了，但没有把物品交给你。"
REFUND_EN = "I do not offer that, so I have returned your coins."
REFUND_ZH = "这个我不提供，金币已经退回。"
_INSULTS = ("stupid", "thief", "fool", "useless", "idiot", "混蛋", "废物")
_DENIALS = (
    "don't serve",
    "do not serve",
    "doesn't serve",
    "does not serve",
    "can't serve",
    "cannot serve",
    "not serve",
    "don't offer",
    "do not offer",
    "不提供",
    "不卖",
)
_CHARGE = (
    "that'll be",
    "that will be",
    "that’ll be",
    "i'll charge",
    "i will charge",
    "charge you",
    "you owe",
)


def reply_language(text: str) -> str:
    """Return the language name the NPC should use for this player line."""
    if _CJK.search(text):
        return "Simplified Chinese"
    return "English"


def learn_player_name(text: str) -> str | None:
    """Return a name from an explicit introduction, if one is present."""
    english = _english_name(text)
    if english:
        return english
    return _chinese_name(text)


def plausible_rudeness(text: str) -> bool:
    """True when the player's line itself looks like an insult."""
    lowered = text.lower()
    return any(word in lowered for word in _INSULTS)


def polite_question(text: str) -> bool:
    """True for a question that does not insult the NPC."""
    stripped = text.strip()
    if "?" not in stripped and "？" not in stripped:
        return False
    lowered = stripped.lower()
    return not any(word in lowered for word in _INSULTS)


def denies_paid_service(reply: str) -> bool:
    """True when the spoken line refuses a service that was just charged."""
    lowered = reply.lower()
    return any(phrase in lowered for phrase in _DENIALS)


def normalize_emotion(value: str) -> str:
    """Map a model emotion onto the six emotions the game stores."""
    cleaned = value.strip().lower()
    if cleaned in _EMOTIONS:
        return cleaned
    if cleaned in _EMOTION_ALIASES:
        return _EMOTION_ALIASES[cleaned]
    match = get_close_matches(cleaned, sorted(_EMOTIONS), n=1, cutoff=0.6)
    if match:
        return match[0]
    return "neutral"


def polish_reply(text: str, affinity_reason: str = "") -> str:
    """Drop stage directions and leaked fields, then trim by length.

    Very short sentences are merged forward. The result stays within
    240 characters and always keeps the first sentence.

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
    parts = _sentences(merged)
    if not parts:
        return ""
    return _limit_length(_merge_short(parts))


def guard_unproven_transfer(
    reply: str,
    stock: list[str],
    granted: list[str],
    charged: bool,
    language: str,
) -> str:
    """Replace a gift or payment the tools did not actually make."""
    if not reply or not _failed_transfer(reply, stock, granted, charged):
        return reply
    return _honest_line(language, charged, granted)


def _failed_transfer(
    reply: str,
    stock: list[str],
    granted: list[str],
    charged: bool,
) -> bool:
    low = reply.lower()
    false_charge = any(phrase in low for phrase in _CHARGE) and not charged
    return false_charge or _unproven_gift(low, stock, granted, charged)


def _unproven_gift(
    low: str,
    stock: list[str],
    granted: list[str],
    charged: bool,
) -> bool:
    """True when the line claims an item the tools did not give."""
    if not any(phrase in low for phrase in _GIFT):
        return False
    granted_names = {item.lower() for item in granted}
    missing = [
        item
        for item in stock
        if item.lower() in low and item.lower() not in granted_names
    ]
    if missing:
        return True
    bare_phrases = _BARE_GIFT
    if charged:
        bare_phrases = tuple(
            phrase for phrase in _BARE_GIFT if phrase not in _SERVICE_HANDOFF
        )
    named = any(item.lower() in low for item in stock)
    bare = any(phrase in low for phrase in bare_phrases)
    return bare and not named and not granted


def _honest_line(language: str, charged: bool, granted: list[str]) -> str:
    """Describe the transfer that actually happened."""
    chinese = language == "Simplified Chinese"
    if charged and granted:
        items = ", ".join(granted)
        if chinese:
            return f"金币已经扣过了。本轮交给你的只有{items}。"
        return f"You were charged. This turn I only gave you {items}."
    if charged:
        if chinese:
            return CHARGED_ZH
        return CHARGED_EN
    if chinese:
        return HONEST_ZH
    return HONEST_EN


def _merge_short(parts: list[str]) -> list[str]:
    merged: list[str] = []
    current = parts[0]
    for part in parts[1:]:
        if len(current) < _SHORT_SENTENCE:
            current = _join_two(current, part)
            continue
        merged.append(current)
        current = part
    merged.append(current)
    return merged


def _limit_length(parts: list[str]) -> str:
    kept = [parts[0]]
    total = len(parts[0])
    for part in parts[1:]:
        gap = 1 if _needs_space(kept[-1], part) else 0
        if total + gap + len(part) > _REPLY_LIMIT:
            break
        kept.append(part)
        total += gap + len(part)
    text = kept[0]
    for part in kept[1:]:
        text = _join_two(text, part)
    return text


def _join_two(left: str, right: str) -> str:
    if _needs_space(left, right):
        return f"{left} {right}"
    return f"{left}{right}"


def _needs_space(prev: str, nxt: str) -> bool:
    if not prev or not nxt:
        return False
    if prev[-1] in "。！？":
        return False
    return _CJK.match(nxt[0]) is None


def _sentences(text: str) -> list[str]:
    parts: list[str] = []
    start = 0
    for match in _END.finditer(text):
        sentence = text[start : match.end()].strip()
        start = match.end()
        if sentence:
            parts.append(sentence)
    tail = text[start:].strip()
    if tail:
        parts.append(tail)
    return parts


def _english_name(text: str) -> str | None:
    match = _EN_NAME.search(text)
    if match is None:
        return None
    clause = re.split(r"[.!?。！？]", match.group(1), maxsplit=1)[0]
    chosen: list[str] = []
    for word in _EN_WORD.findall(clause):
        if word.lower() in _EN_STOP:
            break
        chosen.append(word)
        if len(chosen) == 3:
            break
    if not chosen or chosen[0].lower() in _EN_BAD:
        return None
    return " ".join(part[:1].upper() + part[1:] for part in chosen)


def _chinese_name(text: str) -> str | None:
    match = _CN_NAME.search(text)
    if match is None:
        return None
    name = match.group(1)
    if any(bad in name for bad in _CN_BAD):
        return None
    window = text[max(0, match.start() - 6) : match.end() + 4]
    if any(mark in window for mark in ("什么", "啥", "谁", "吗", "？", "?")):
        return None
    return name
