# -*- coding: utf-8 -*-
"""Lexicon sentiment of the player's own line.

No third-party NLP. English and Chinese phrases, a short negation
window, and a few intensifiers. The score becomes a small affinity
delta. Questions, item requests, and thanks for a reward stay neutral
so those turns keep the older tool rules.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

_POS = (
    ("thank you", 1.1),
    ("thanks", 1.0),
    ("please", 0.7),
    ("wonderful", 1.2),
    ("amazing", 1.2),
    ("appreciate", 1.1),
    ("lovely", 1.0),
    ("kind", 1.0),
    ("great", 0.8),
    ("love", 1.2),
    ("like", 0.8),
    ("非常感谢", 1.4),
    ("谢谢你", 1.2),
    ("感谢", 1.0),
    ("谢谢", 1.0),
    ("多谢", 1.0),
    ("请", 0.7),
    ("非常好", 1.6),
    ("太好了", 1.4),
    ("真好", 1.2),
    ("很好", 1.2),
    ("不错", 0.8),
    ("善良", 1.0),
    ("温柔", 1.0),
    ("喜欢", 1.0),
    ("棒", 0.8),
    ("厉害", 0.8),
)
_NEG = (
    ("shut up", 1.4),
    ("stupid", 1.2),
    ("idiot", 1.2),
    ("thief", 1.2),
    ("useless", 1.2),
    ("hate", 1.2),
    ("fool", 1.0),
    ("rude", 1.0),
    ("damn", 0.8),
    ("讨厌", 1.2),
    ("白痴", 1.4),
    ("废物", 1.4),
    ("混蛋", 1.4),
    ("闭嘴", 1.4),
    ("恶心", 1.2),
    ("可恶", 1.2),
    ("去死", 1.6),
    ("恨", 1.0),
    ("滚", 1.2),
    ("蠢", 1.0),
)
_GREETINGS = (
    "good morning",
    "good evening",
    "good afternoon",
    "hello",
    "hey",
    "hi",
    "大家好",
    "您好",
    "你好",
    "嗨",
)
_NEG_EN = re.compile(
    r"\b(not|never|no|don't|dont|isn't|isnt|wasn't|wasnt)\b",
)
_INT_EN = re.compile(r"\b(very|really|extremely|so)\b")
_NEG_ZH = ("没有", "不是", "不", "没", "别")
_INT_ZH = ("非常", "特别", "很", "太", "超", "真")
_REQUEST = (
    "give me",
    "charge me",
    "accept the",
    "found the hammer",
    "here it is",
    "请给我",
    "给我",
    "收费",
)
_WINDOW = 16


@dataclass(frozen=True)
class PlayerSentiment:
    """One scored player line."""

    label: str
    score: float
    delta: int


def score_player_line(text: str) -> PlayerSentiment:
    """Score ``text``. ``delta`` is an integer from -2 to 2."""
    raw = " ".join(text.split())
    if not raw:
        return PlayerSentiment("neutral", 0.0, 0)
    lowered = _strip_greetings(raw.lower())
    total = 0.0
    for start, _end, weight, sign in _spans(lowered):
        neg, intense = _flags(lowered, start)
        value = weight * (1.6 if intense else 1.0) * sign
        if neg:
            value = -value
        total += value
    delta = _to_delta(total)
    if delta > 0:
        label = "positive"
    elif delta < 0:
        label = "negative"
    else:
        label = "neutral"
    return PlayerSentiment(label, round(total, 3), delta)


def conversational_sentiment(text: str) -> PlayerSentiment:
    """Sentiment that may move affinity on small talk.

    A question, a give or charge request, and thanks for a reward do
    not. Those turns already have their own affinity rules.
    """
    scored = score_player_line(text)
    if scored.delta == 0:
        return scored
    if _is_question(text):
        return PlayerSentiment("neutral", scored.score, 0)
    if scored.delta > 0 and _blocks_praise(text):
        return PlayerSentiment("neutral", scored.score, 0)
    return scored


def tone_note(result: PlayerSentiment) -> str:
    """Prompt line so the NPC's emotion can follow the player's tone."""
    if result.delta == 0:
        return ""
    word = "positive" if result.delta > 0 else "negative"
    mood = "grateful or warm" if result.delta > 0 else "annoyed"
    return (
        f"Player sentiment: {word} ({result.delta:+d}). "
        f"Let this turn's emotion be {mood}. "
        "Do not say the word sentiment aloud."
    )


def _strip_greetings(text: str) -> str:
    cleaned = text
    for phrase in sorted(_GREETINGS, key=len, reverse=True):
        if _cjk(phrase):
            cleaned = cleaned.replace(phrase, " " * len(phrase))
        else:
            cleaned = re.sub(
                rf"\b{re.escape(phrase)}\b",
                " " * len(phrase),
                cleaned,
            )
    return cleaned


def _spans(text: str) -> list[tuple[int, int, float, int]]:
    found: list[tuple[int, int, float, int]] = []
    for phrase, weight in _POS:
        found.extend(_locate(text, phrase, weight, 1))
    for phrase, weight in _NEG:
        found.extend(_locate(text, phrase, weight, -1))
    found.sort(key=lambda item: (-(item[1] - item[0]), item[0]))
    taken = [False] * len(text)
    kept: list[tuple[int, int, float, int]] = []
    for start, end, weight, sign in found:
        if any(taken[start:end]):
            continue
        for index in range(start, end):
            taken[index] = True
        kept.append((start, end, weight, sign))
    return kept


def _locate(
    text: str,
    phrase: str,
    weight: float,
    sign: int,
) -> list[tuple[int, int, float, int]]:
    hits = []
    if _cjk(phrase):
        start = 0
        while True:
            index = text.find(phrase, start)
            if index < 0:
                break
            hits.append((index, index + len(phrase), weight, sign))
            start = index + len(phrase)
        return hits
    pattern = rf"\b{re.escape(phrase)}\b"
    for match in re.finditer(pattern, text):
        hits.append((match.start(), match.end(), weight, sign))
    return hits


def _flags(text: str, start: int) -> tuple[bool, bool]:
    window = text[max(0, start - _WINDOW) : start]
    neg = _NEG_EN.search(window) is not None or any(
        token in window for token in _NEG_ZH
    )
    intense = _INT_EN.search(window) is not None or any(
        token in window for token in _INT_ZH
    )
    return neg, intense


def _to_delta(score: float) -> int:
    if score >= 1.5:
        return 2
    if score >= 0.5:
        return 1
    if score <= -1.5:
        return -2
    if score <= -0.5:
        return -1
    return 0


def _is_question(text: str) -> bool:
    return "?" in text or "？" in text


def _blocks_praise(text: str) -> bool:
    lowered = text.lower()
    if any(phrase in lowered for phrase in _REQUEST):
        return True
    if "奖励" in text:
        return True
    return "reward" in lowered and ("thank" in lowered or "谢" in text)


def _cjk(text: str) -> bool:
    return any("\u4e00" <= char <= "\u9fff" for char in text)
