# -*- coding: utf-8 -*-
"""Persona configuration is editable and reaches the system prompt."""
from __future__ import annotations

from pathlib import Path

import pytest

from npc_config import load_town_config
from prompts import build_system_prompt

_MARKERS = {
    "bram": "count every strike of the hammer",
    "mira": "Oak and Lantern",
    "rowan": "town council",
}


def test_three_distinct_personas() -> None:
    """The shipped town has three residents with different roles."""
    config = load_town_config()
    assert set(config.npcs) == {"bram", "mira", "rowan"}
    personas = [npc.persona for npc in config.npcs.values()]
    assert len(set(personas)) == 3
    assert config.npcs["bram"].role == "blacksmith"
    assert config.npcs["mira"].role == "innkeeper"
    assert config.npcs["rowan"].role == "elder"
    assert "horseshoe" in config.npcs["bram"].gifts
    assert config.npcs["rowan"].gifts == []
    assert config.quests["lost_hammer"].title == "The Lost Hammer"


def test_system_prompt_contains_only_that_persona() -> None:
    """Each prompt carries its own persona and not another NPC's marker."""
    config = load_town_config()
    prompts = {
        npc_id: build_system_prompt(
            town_name=config.town_name,
            npc=npc,
            state_text="Player: Traveler\nGold: 12",
            affinity=npc.starting_affinity,
            emotion="neutral",
        )
        for npc_id, npc in config.npcs.items()
    }
    for npc_id, prompt in prompts.items():
        assert _MARKERS[npc_id] in prompt
        assert f"You are {config.npcs[npc_id].name}," in prompt
        for other_id, marker in _MARKERS.items():
            if other_id != npc_id:
                assert marker not in prompt


def test_unknown_npc_is_rejected() -> None:
    """A typo in an NPC id fails with the known ids."""
    config = load_town_config()
    with pytest.raises(KeyError, match="Unknown NPC"):
        config.npc("blacksmith")


def test_custom_config_file(tmp_path: Path) -> None:
    """Personas can be replaced by pointing at another JSON file."""
    path = tmp_path / "town.json"
    path.write_text(
        """
        {
          "town_name": "Testford",
          "player": {"name": "Ada", "gold": 1, "inventory": []},
          "quests": {},
          "npcs": {
            "ada": {
              "name": "Ada",
              "role": "scribe",
              "persona": "You catalog every rumor in the margins.",
              "gifts": ["ink"],
              "starting_affinity": 1
            }
          }
        }
        """,
        encoding="utf-8",
    )
    config = load_town_config(path)
    assert config.town_name == "Testford"
    assert config.npc("ada").persona.startswith("You catalog")
