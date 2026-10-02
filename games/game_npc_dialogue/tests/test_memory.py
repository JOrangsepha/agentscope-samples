# -*- coding: utf-8 -*-
"""Player facts written in one visit are injected into the next visit."""
from __future__ import annotations

import asyncio
from pathlib import Path

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
