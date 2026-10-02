# -*- coding: utf-8 -*-
"""JSON file store for inventory, gold, quests, and affinity."""
from __future__ import annotations

import json
from pathlib import Path

from npc_config import TownConfig

_QUEST_STATUSES = {"available", "accepted", "completed"}
_AFFINITY_MIN = -100
_AFFINITY_MAX = 100


class GameState:
    """Mutable game state persisted as one local JSON file."""

    def __init__(self, path: str | Path, config: TownConfig) -> None:
        self.path = Path(path)
        self.config = config
        if self.path.exists():
            self.data = json.loads(self.path.read_text(encoding="utf-8"))
        else:
            self.data = self._initial_data(config)
            self.save()

    @staticmethod
    def _initial_data(config: TownConfig) -> dict:
        quests = {
            quest_id: {
                "title": quest.title,
                "description": quest.description,
                "status": quest.status,
                "progress": quest.progress,
                "goal": quest.goal,
            }
            for quest_id, quest in config.quests.items()
        }
        affinity = {
            npc_id: npc.starting_affinity
            for npc_id, npc in config.npcs.items()
        }
        emotion = {npc_id: "neutral" for npc_id in config.npcs}
        return {
            "player": {
                "name": config.player_name,
                "gold": config.gold,
                "inventory": list(config.inventory),
            },
            "quests": quests,
            "affinity": affinity,
            "emotion": emotion,
        }

    def save(self) -> None:
        """Write the current state to disk."""
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(
            json.dumps(self.data, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )

    @property
    def player_name(self) -> str:
        """The name stored for the player."""
        return str(self.data["player"]["name"])

    @property
    def gold(self) -> int:
        """Coins the player is carrying."""
        return int(self.data["player"]["gold"])

    @property
    def inventory(self) -> list[str]:
        """Item names the player is carrying."""
        return list(self.data["player"]["inventory"])

    def affinity(self, npc_id: str) -> int:
        """Favorability of one NPC toward the player."""
        stored = self.data["affinity"].get(npc_id)
        if stored is None:
            return self.config.npc(npc_id).starting_affinity
        return int(stored)

    def emotion(self, npc_id: str) -> str:
        """The emotion this NPC showed on their last turn."""
        return str(self.data["emotion"].get(npc_id, "neutral"))

    def apply_affinity(self, npc_id: str, delta: int) -> int:
        """Add ``delta`` and clamp the result to [-100, 100]."""
        self.config.npc(npc_id)
        updated = self.affinity(npc_id) + int(delta)
        updated = max(_AFFINITY_MIN, min(_AFFINITY_MAX, updated))
        self.data["affinity"][npc_id] = updated
        self.save()
        return updated

    def set_emotion(self, npc_id: str, emotion: str) -> None:
        """Record the emotion shown on this turn."""
        self.config.npc(npc_id)
        self.data["emotion"][npc_id] = emotion
        self.save()

    def give_item(self, npc_id: str, item: str) -> str:
        """Move one of the NPC's stocked gifts into the inventory."""
        npc = self.config.npc(npc_id)
        match = _match_choice(item, npc.gifts)
        if match is None:
            stock = ", ".join(npc.gifts) or "(nothing)"
            return (
                f"{npc.name} cannot give '{item}'. " f"Stock on hand: {stock}."
            )
        if match in self.inventory:
            return f"The player already carries {match}."
        self.data["player"]["inventory"].append(match)
        self.save()
        return f"{npc.name} gave the player {match}."

    def adjust_gold(self, amount: int, reason: str) -> str:
        """Add or remove coins. Refuse a payment the player cannot afford."""
        amount = int(amount)
        updated = self.gold + amount
        if updated < 0:
            return (
                f"Not enough gold for '{reason}'. "
                f"The player has {self.gold}."
            )
        self.data["player"]["gold"] = updated
        self.save()
        verb = "gained" if amount >= 0 else "paid"
        return (
            f"The player {verb} {abs(amount)} gold ({reason}). "
            f"Gold is now {updated}."
        )

    def update_quest(
        self,
        quest_id: str,
        status: str,
        progress: int,
    ) -> str:
        """Set a quest's status and absolute progress."""
        quest = self.data["quests"].get(quest_id)
        if quest is None:
            known = ", ".join(sorted(self.data["quests"])) or "(none)"
            return f"Unknown quest '{quest_id}'. Known quests: {known}."
        if status not in _QUEST_STATUSES:
            allowed = ", ".join(sorted(_QUEST_STATUSES))
            return f"Invalid status '{status}'. Use one of: {allowed}."
        quest["status"] = status
        quest["progress"] = max(0, int(progress))
        self.save()
        return (
            f"Quest {quest_id} is now {status} "
            f"({quest['progress']}/{quest['goal']})."
        )

    def describe(self) -> str:
        """Multi-line snapshot embedded in an NPC system prompt."""
        items = ", ".join(self.inventory) or "(empty)"
        lines = [
            f"Player: {self.player_name}",
            f"Gold: {self.gold}",
            f"Inventory: {items}",
            "Quests:",
        ]
        lines.extend(self._quest_lines())
        return "\n".join(lines)

    def describe_for_player(self) -> str:
        """Snapshot printed by the ``/state`` command."""
        affinity = ", ".join(
            f"{npc_id}={self.affinity(npc_id)}" f"/{self.emotion(npc_id)}"
            for npc_id in self.config.npcs
        )
        return self.describe() + f"\nAffinity (score/emotion): {affinity}"

    def _quest_lines(self) -> list[str]:
        lines = []
        for quest_id, quest in self.data["quests"].items():
            lines.append(
                f"- {quest_id}: {quest['title']} "
                f"[{quest['status']} {quest['progress']}/{quest['goal']}] "
                f"{quest['description']}",
            )
        return lines


def _match_choice(requested: str, choices: list[str]) -> str | None:
    needle = requested.strip().lower()
    for choice in choices:
        if choice.lower() == needle:
            return choice
    return None
