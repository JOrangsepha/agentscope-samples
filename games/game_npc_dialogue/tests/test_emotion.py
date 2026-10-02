# -*- coding: utf-8 -*-
"""Emotion and affinity are parsed, saved, and shown on the next turn."""
from __future__ import annotations

import asyncio
from pathlib import Path

from game_state import GameState
from mock_model import ScriptedNpcModel
from npc_config import load_town_config
from session import TownSession


def test_affinity_and_emotion_persist_and_change_the_next_reply(
    tmp_path: Path,
) -> None:
    """A polite turn raises affinity; the next visit greets more warmly."""
    config = load_town_config()
    session = TownSession(config, tmp_path, ScriptedNpcModel())
    greeting = asyncio.run(session.talk("bram", "Hello."))
    assert greeting.emotion == "neutral"
    assert greeting.affinity == 0
    assert greeting.reply == "The forge is hot. Speak plainly."

    polite = asyncio.run(session.talk("bram", "Thank you for your help."))
    assert polite.emotion == "grateful"
    assert polite.affinity_delta == 2
    assert polite.affinity == 2
    assert "courtesy" in polite.reply

    reloaded = GameState(tmp_path / "game_state.json", config)
    assert reloaded.affinity("bram") == 2
    assert reloaded.emotion("bram") == "grateful"

    later_model = ScriptedNpcModel()
    later = TownSession(config, tmp_path, later_model)
    again = asyncio.run(later.talk("bram", "Hello."))
    assert "Affinity: 2" in later_model.calls[0]["system"]
    assert (
        "Last emotion you showed: grateful" in later_model.calls[0]["system"]
    )
    assert again.reply == "It is good to see you again."
    assert again.emotion == "happy"


def test_rudeness_lowers_affinity(tmp_path: Path) -> None:
    """An insult is stored as a negative affinity change."""
    config = load_town_config()
    session = TownSession(config, tmp_path, ScriptedNpcModel())
    rude = asyncio.run(session.talk("rowan", "You are a stupid thief."))
    assert rude.emotion == "annoyed"
    assert rude.affinity_delta == -2
    assert rude.affinity == -2
    reloaded = GameState(tmp_path / "game_state.json", config)
    assert reloaded.affinity("rowan") == -2
    assert reloaded.emotion("rowan") == "annoyed"
