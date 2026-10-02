# -*- coding: utf-8 -*-
"""Load the editable town and NPC configuration."""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path


DEFAULT_CONFIG_PATH = Path(__file__).resolve().parent / "town_config.json"


@dataclass
class NpcSpec:
    """One town resident the player can talk to."""

    npc_id: str
    name: str
    role: str
    persona: str
    gifts: list[str]
    starting_affinity: int


@dataclass
class QuestSpec:
    """A quest stored in the town configuration."""

    quest_id: str
    title: str
    description: str
    status: str
    progress: int
    goal: int


@dataclass
class TownConfig:
    """Static town data. Runtime changes live in ``GameState``."""

    town_name: str
    player_name: str
    gold: int
    inventory: list[str]
    quests: dict[str, QuestSpec]
    npcs: dict[str, NpcSpec]

    def npc(self, npc_id: str) -> NpcSpec:
        """Return one NPC, or raise ``KeyError`` with the known ids."""
        try:
            return self.npcs[npc_id]
        except KeyError as exc:
            known = ", ".join(sorted(self.npcs))
            raise KeyError(
                f"Unknown NPC '{npc_id}'. Known NPCs: {known}.",
            ) from exc


def load_town_config(path: str | Path | None = None) -> TownConfig:
    """Load ``town_config.json`` into dataclasses."""
    config_path = Path(path) if path else DEFAULT_CONFIG_PATH
    raw = json.loads(config_path.read_text(encoding="utf-8"))
    player = raw["player"]
    quests = {
        quest_id: QuestSpec(
            quest_id=quest_id,
            title=quest["title"],
            description=quest["description"],
            status=quest["status"],
            progress=int(quest["progress"]),
            goal=int(quest["goal"]),
        )
        for quest_id, quest in raw["quests"].items()
    }
    npcs = {
        npc_id: NpcSpec(
            npc_id=npc_id,
            name=npc["name"],
            role=npc["role"],
            persona=npc["persona"],
            gifts=list(npc["gifts"]),
            starting_affinity=int(npc["starting_affinity"]),
        )
        for npc_id, npc in raw["npcs"].items()
    }
    return TownConfig(
        town_name=raw["town_name"],
        player_name=player["name"],
        gold=int(player["gold"]),
        inventory=list(player["inventory"]),
        quests=quests,
        npcs=npcs,
    )
