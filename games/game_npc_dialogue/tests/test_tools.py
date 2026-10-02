# -*- coding: utf-8 -*-
"""Tool calls from the scripted model mutate the saved game state."""
from __future__ import annotations

import asyncio
from pathlib import Path

from game_state import GameState
from npc_config import load_town_config
from session import TownSession

from mock_model import ScriptedNpcModel


def _session(tmp_path: Path) -> TownSession:
    return TownSession(
        load_town_config(),
        tmp_path,
        ScriptedNpcModel(),
    )


def test_give_item_and_quest_tools_persist(tmp_path: Path) -> None:
    """A gift, an accepted quest, and a reward are written to disk."""
    session = _session(tmp_path)
    asyncio.run(session.talk("bram", "Please give me a horseshoe."))
    asyncio.run(
        session.talk("rowan", "I accept the lost hammer quest."),
    )
    asyncio.run(session.talk("bram", "I found the hammer."))

    reloaded = GameState(tmp_path / "game_state.json", load_town_config())
    assert "horseshoe" in reloaded.inventory
    quest = reloaded.data["quests"]["lost_hammer"]
    assert quest["status"] == "completed"
    assert quest["progress"] == 1
    assert reloaded.gold == 20


def test_tool_rejects_a_gift_the_npc_does_not_stock(tmp_path: Path) -> None:
    """Mira cannot hand over the blacksmith's horseshoe."""
    session = _session(tmp_path)
    result = asyncio.run(
        session.talk("mira", "Please give me a horseshoe."),
    )
    assert "horseshoe" not in session.game.inventory
    assert "stew" in result.reply


def test_gold_cannot_drop_below_zero(tmp_path: Path) -> None:
    """A payment the player cannot afford leaves the save unchanged."""
    config = load_town_config()
    state = GameState(tmp_path / "game_state.json", config)
    message = state.adjust_gold(-100, "a bribe")
    assert "Not enough gold" in message
    reloaded = GameState(tmp_path / "game_state.json", config)
    assert reloaded.gold == 12
