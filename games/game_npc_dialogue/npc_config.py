# -*- coding: utf-8 -*-
"""Load the editable town and NPC configuration."""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path


DEFAULT_CONFIG_PATH = Path(__file__).resolve().parent / "town_config.json"


@dataclass
class QuestGift:
    """An item handed over only while a quest is in a given status."""

    item: str
    when_quest: str
    when_status: str


@dataclass
class NpcSpec:
    """One town resident the player can talk to."""

    npc_id: str
    name: str
    role: str
    persona: str
    gifts: list[str]
    starting_affinity: int
    can_charge: bool = False
    quest_gifts: list[QuestGift] = field(default_factory=list)


@dataclass
class QuestSpec:
    """A quest stored in the town configuration."""

    quest_id: str
    title: str
    description: str
    status: str
    progress: int
    goal: int
    giver: str = ""
    required_item: str = ""
    reward_gold: int = 0


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

    def gives_quests(self, npc_id: str) -> bool:
        """True when this NPC is the giver of at least one quest."""
        return any(quest.giver == npc_id for quest in self.quests.values())


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
            giver=str(quest.get("giver", "")),
            required_item=str(quest.get("required_item", "")),
            reward_gold=int(quest.get("reward_gold", 0)),
        )
        for quest_id, quest in raw["quests"].items()
    }
    npcs = {
        npc_id: _load_npc(npc_id, npc) for npc_id, npc in raw["npcs"].items()
    }
    return TownConfig(
        town_name=raw["town_name"],
        player_name=player["name"],
        gold=int(player["gold"]),
        inventory=list(player["inventory"]),
        quests=quests,
        npcs=npcs,
    )


def _load_npc(npc_id: str, npc: dict) -> NpcSpec:
    gifts = [
        QuestGift(
            item=gift["item"],
            when_quest=gift["when_quest"],
            when_status=gift["when_status"],
        )
        for gift in npc.get("quest_gifts", [])
    ]
    return NpcSpec(
        npc_id=npc_id,
        name=npc["name"],
        role=npc["role"],
        persona=npc["persona"],
        gifts=list(npc["gifts"]),
        starting_affinity=int(npc["starting_affinity"]),
        can_charge=bool(npc.get("can_charge", False)),
        quest_gifts=gifts,
    )
