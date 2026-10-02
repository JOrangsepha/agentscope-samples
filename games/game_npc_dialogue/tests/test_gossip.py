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
    assert asks_for_news("What news have you heard about me?")
    assert asks_for_news("镇上最近有什么新鲜事？")
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
