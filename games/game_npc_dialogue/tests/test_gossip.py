# -*- coding: utf-8 -*-
"""Public rumors spread. Private facts stay in one NPC's memory."""
from __future__ import annotations

import asyncio
from pathlib import Path

from mock_model import ScriptedNpcModel
from npc_config import load_town_config
from session import TownSession


def test_an_insult_spreads_and_a_name_stays_private(tmp_path: Path) -> None:
    """Bram's grudge is town talk. The player's trade is not."""
    session = TownSession(load_town_config(), tmp_path, ScriptedNpcModel())
    asyncio.run(session.talk("bram", "You are a stupid thief."))
    gossip = (tmp_path / "gossip.json").read_text(encoding="utf-8")
    assert "finds the player rude" in gossip
    assert "stupid thief" in gossip
    assert "traveling" not in gossip

    other = TownSession(load_town_config(), tmp_path, ScriptedNpcModel())
    asyncio.run(other.talk("mira", "Hello."))
    system = other.model.calls[0]["system"]
    assert "finds the player rude" in system
    private = (session.memory_dir("bram") / "MEMORY.md").read_text(
        encoding="utf-8",
    )
    assert "stupid thief" in private

    exchange = other.wait_in_town()
    assert "Mira tells" in exchange
    assert "finds the player rude" in exchange
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

    session = TownSession(
        load_town_config(),
        tmp_path / "town",
        ScriptedNpcModel(),
    )
    asyncio.run(session.talk("rowan", "I accept the lost hammer quest."))
    gossip = (tmp_path / "town" / "gossip.json").read_text(encoding="utf-8")
    assert "accepted The Lost Hammer" in gossip
    assert "gold" not in gossip.lower()
