# -*- coding: utf-8 -*-
"""Public rumors spread. Private facts stay in one NPC's memory."""
from __future__ import annotations

import asyncio
import json
from pathlib import Path

from gossip import (  # pylint: disable=protected-access
    _wait_reply,
    asks_for_news,
)
from mock_model import ScriptedNpcModel
from npc_config import load_town_config
from session import TownSession


def test_an_insult_spreads_and_a_name_stays_private(tmp_path: Path) -> None:
    """Bram's grudge is town talk. The player's trade is not."""
    session = TownSession(load_town_config(), tmp_path, ScriptedNpcModel())
    asyncio.run(session.talk("bram", "You are a stupid thief."))
    gossip = (tmp_path / "gossip.json").read_text(encoding="utf-8")
    stored = json.loads(gossip)["entries"][0]["text"]
    assert stored == 'Bram heard the player say: "You are a stupid thief."'
    assert "Heard by" not in gossip
    assert "'.'." not in gossip
    assert "finds the player rude" not in gossip
    assert "traveling" not in gossip

    other = TownSession(load_town_config(), tmp_path, ScriptedNpcModel())
    news = asyncio.run(
        other.talk("mira", "What news have you heard about me?"),
    )
    assert "Bram heard the player say" in news.reply
    assert "stupid thief" in news.reply
    speak = [call for call in other.model.calls if call["phase"] == "speak"]
    system = speak[-1]["system"]
    assert "Bram heard the player say" in system
    assert "asks for news" in system
    assert "The player insulted Bram" in system
    assert "You are Mira" in system
    assert "You were not insulted" in system
    assert asks_for_news("What news have you heard about me?")
    assert asks_for_news("镇上最近有什么新鲜事？")
    assert asks_for_news("What's new around here?")
    assert asks_for_news("Have you heard anything?")
    assert asks_for_news("Are there any rumors?")
    assert asks_for_news("镇上有什么新闻吗？")
    assert asks_for_news("镇上有什么事？")
    assert asks_for_news("最近有什么事？")
    assert asks_for_news("有什么新鲜事？")
    assert asks_for_news("Has anyone complained about me?")
    assert asks_for_news("有人投诉我吗？")
    assert asks_for_news("有人抱怨我吗？")
    assert asks_for_news("有人说我什么了？")
    assert not asks_for_news(
        "Good news, I already found the forging hammer.",
    )
    private = (session.memory_dir("bram") / "MEMORY.md").read_text(
        encoding="utf-8",
    )
    assert "stupid thief" in private

    exchange = other.wait_in_town()
    assert "Mira tells" in exchange
    assert "Then the town should know" not in exchange
    assert "Heard by" not in exchange
    assert 'Bram heard the player say: "You are a stupid thief."' in exchange
    assert exchange.strip().endswith("I'll remember that.")
    about_bram = 'Bram heard the player say: "fool."'
    assert _wait_reply("Bram", about_bram) == "Bram: I was there."
    again = (tmp_path / "gossip.json").read_text(encoding="utf-8")
    assert "exchange" in again


def test_an_offer_of_help_is_not_a_news_question() -> None:
    """Asking to help is not a request for town gossip."""
    assert not asks_for_news("米拉，我有什么事可以帮你吗？")
    assert not asks_for_news("有什么事可以帮你")
    assert not asks_for_news("有什么我能做的")
    assert asks_for_news("镇上有什么事？")
    assert asks_for_news("最近有什么事？")
    assert asks_for_news("有什么新鲜事？")


def test_a_complaint_names_who_was_insulted(tmp_path: Path) -> None:
    """Rowan is not the target of an insult Bram heard."""
    session = TownSession(load_town_config(), tmp_path, ScriptedNpcModel())
    asyncio.run(session.talk("bram", "You are a stupid thief."))
    rowan = asyncio.run(
        session.talk("rowan", "Elder Rowan, has anyone complained about me?"),
    )
    assert rowan.affinity_delta == 0
    rowan_speak = [
        call for call in session.model.calls if call["phase"] == "speak"
    ]
    note = rowan_speak[-1]["system"]
    assert "The player insulted Bram" in note
    assert "You are Rowan" in note
    assert "You were not insulted" in note
    assert "Do not say the insult was about you" in note
    assert "stupid thief" in note

    bram = asyncio.run(
        session.talk("bram", "Has anyone complained about me?"),
    )
    assert bram.affinity_delta == 0
    bram_speak = [
        call for call in session.model.calls if call["phase"] == "speak"
    ]
    heard = bram_speak[-1]["system"]
    assert "The player insulted you, Bram" in heard
    assert "you heard it" in heard
    assert "You were not insulted" not in heard

    fresh = TownSession(
        load_town_config(),
        tmp_path / "quest-only",
        ScriptedNpcModel(),
    )
    asyncio.run(fresh.talk("rowan", "I accept the lost hammer quest."))
    asyncio.run(fresh.talk("mira", "What's new around here?"))
    mira_speak = [
        call for call in fresh.model.calls if call["phase"] == "speak"
    ]
    quest_note = mira_speak[-1]["system"]
    assert "asks for news" in quest_note
    assert "accepted The Lost Hammer" in quest_note
    assert "insulted" not in quest_note

    asyncio.run(session.talk("rowan", "有人抱怨我吗？"))
    zh_speak = [
        call for call in session.model.calls if call["phase"] == "speak"
    ]
    zh_note = zh_speak[-1]["system"]
    assert "玩家侮辱的是Bram" in zh_note
    assert "你是Rowan，不是被骂的人" in zh_note
    assert "不要说这句是在骂你" in zh_note


def test_quest_news_is_public_and_wait_is_quiet_at_first(
    tmp_path: Path,
) -> None:
    """Accepting the quest is shared. An empty square stays quiet."""
    quiet = TownSession(
        load_town_config(),
        tmp_path / "empty",
        ScriptedNpcModel(),
    )
    assert "quiet" in quiet.wait_in_town()
    greeted = TownSession(
        load_town_config(),
        tmp_path / "hello",
        ScriptedNpcModel(forced_emotion="annoyed", forced_delta=-3),
    )
    hello = asyncio.run(greeted.talk("bram", "Hello."))
    assert hello.affinity_delta == 0
    assert not (tmp_path / "hello" / "gossip.json").exists()

    session = TownSession(
        load_town_config(),
        tmp_path / "town",
        ScriptedNpcModel(),
    )
    asyncio.run(session.talk("rowan", "I accept the lost hammer quest."))
    gossip = (tmp_path / "town" / "gossip.json").read_text(encoding="utf-8")
    assert "accepted The Lost Hammer" in gossip
    assert "gold" not in gossip.lower()
