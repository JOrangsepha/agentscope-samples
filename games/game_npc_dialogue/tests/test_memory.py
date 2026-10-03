# -*- coding: utf-8 -*-
"""Player facts written in one visit are injected into the next visit."""
from __future__ import annotations

import asyncio
from pathlib import Path

from memory_store import asks_for_recall
from mock_model import ScriptedNpcModel
from npc_config import load_town_config
from session import TownSession


def test_memory_survives_a_new_session(tmp_path: Path) -> None:
    """A fresh agent, sharing only the save directory, recalls Lira."""
    config = load_town_config()
    first_model = ScriptedNpcModel()
    first = TownSession(config, tmp_path, first_model)
    result = asyncio.run(
        first.talk(
            "bram",
            "Hello, my name is Lira and I bake bread.",
        ),
    )
    assert "Lira" in result.reply
    memory_index = first.memory_dir("bram") / "MEMORY.md"
    assert memory_index.is_file()
    stored = memory_index.read_text(encoding="utf-8")
    assert "The player's name is Lira, a baker." in stored
    topic = first.memory_dir("bram") / "fact_1.md"
    assert "type: user" in topic.read_text(encoding="utf-8")

    second_model = ScriptedNpcModel()
    second = TownSession(config, tmp_path, second_model)
    recalled = asyncio.run(second.talk("bram", "Do you remember me?"))
    assert second_model.calls, "the second session should call the model"
    assert "Lira" in second_model.calls[0]["system"]
    assert "a baker" in second_model.calls[0]["system"]
    assert recalled.reply == "Aye, I remember you, Lira the baker."
    assert "Lira" not in second_model.calls[0]["user"]


def test_a_recall_question_injects_stored_facts(tmp_path: Path) -> None:
    """Remember-me puts the name, trade, and insult into the speak step."""
    assert asks_for_recall("Do you remember me?")
    assert asks_for_recall("how I spoke last time")
    assert asks_for_recall("what did I say to you?")
    assert asks_for_recall("你还记得我吗？")
    assert asks_for_recall("我上次怎么说的？")
    assert asks_for_recall("我说过什么？")
    assert not asks_for_recall("What's new around here?")
    assert not asks_for_recall("How much gold do I have?")

    session = TownSession(load_town_config(), tmp_path, ScriptedNpcModel())
    asyncio.run(
        session.talk("bram", "Hello, my name is Lira and I bake bread."),
    )
    asyncio.run(session.talk("bram", "You are a stupid thief."))
    asked = asyncio.run(session.talk("bram", "Do you remember me?"))
    assert asked.affinity_delta == 0
    speak = [call for call in session.model.calls if call["phase"] == "speak"]
    note = speak[-1]["system"]
    assert "Your name is Lira" in note
    assert "Your trade is baker" in note
    assert 'You said to me: "You are a stupid thief."' in note
    assert "own words" in note
    assert "not as a list" in note
    assert "Reference only" not in note
    assert "8-gold reward was already paid" in note
    assert "not to Bram" in note
    assert "not meals" in note
