# -*- coding: utf-8 -*-
"""JSON file store for inventory, gold, quests, and affinity."""
from __future__ import annotations

import json
from pathlib import Path

from npc_config import QuestSpec, TownConfig

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
                "rewarded": False,
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
            "notable": {},
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

    def notable(self, npc_id: str) -> str:
        """Last strong impression, kept until another one replaces it."""
        notes = self.data.setdefault("notable", {})
        return str(notes.get(npc_id, ""))

    def set_player_name(self, name: str) -> None:
        """Replace the stored player name."""
        cleaned = " ".join(name.split())
        if not cleaned:
            return
        self.data["player"]["name"] = cleaned
        self.save()

    def set_notable(self, npc_id: str, note: str) -> None:
        """Remember one strong impression. Mild turns do not call this."""
        self.config.npc(npc_id)
        self.data.setdefault("notable", {})[npc_id] = note
        self.save()

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

    def stock(self, npc_id: str) -> list[str]:
        """Gifts this NPC may hand over right now, including quest items."""
        npc = self.config.npc(npc_id)
        items = list(npc.gifts)
        for gift in npc.quest_gifts:
            quest = self.data["quests"].get(gift.when_quest)
            if quest is None or quest.get("status") != gift.when_status:
                continue
            if gift.item not in items:
                items.append(gift.item)
        return items

    def give_item(self, npc_id: str, item: str) -> str:
        """Move one stocked gift into the inventory."""
        npc = self.config.npc(npc_id)
        match = _match_choice(item, self.stock(npc_id))
        if match is None:
            stock = ", ".join(self.stock(npc_id)) or "(nothing)"
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

    def charge(self, npc_id: str, amount: int, reason: str) -> str:
        """Take coins for one configured service. A charge never adds gold."""
        npc = self.config.npc(npc_id)
        amount = int(amount)
        if not npc.can_charge:
            return f"{npc.name} cannot charge the player."
        if amount <= 0:
            return "A charge must be a positive number of coins."
        if not _service_allowed(reason, npc.services):
            listed = ", ".join(npc.services) or "(none)"
            return (
                f"{npc.name} cannot charge for '{reason}'. "
                f"Services: {listed}."
            )
        return self.adjust_gold(-amount, reason)

    def accept_quest(self, npc_id: str, quest_id: str) -> str:
        """Let the quest giver move an available quest to accepted, once."""
        quest, rule, refusal = self._quest_context(npc_id, quest_id)
        if refusal:
            return refusal
        assert quest is not None and rule is not None
        status = str(quest["status"])
        if status == "accepted":
            return f"Quest {quest_id} is already accepted."
        if status == "completed" or self._reward_already_paid(quest):
            return f"Quest {quest_id} is already completed."
        if status != "available":
            return f"Quest {quest_id} cannot be accepted from {status}."
        quest["status"] = "accepted"
        self.save()
        return (
            f"Quest {quest_id} is now accepted "
            f"({quest['progress']}/{quest['goal']})."
        )

    def complete_quest(self, npc_id: str, quest_id: str) -> str:
        """Complete a quest once, with proof, and pay the configured reward."""
        quest, rule, refusal = self._quest_context(npc_id, quest_id)
        if refusal:
            return refusal
        assert quest is not None and rule is not None
        if self._reward_already_paid(quest):
            paid = ""
            if rule.reward_gold:
                paid = f" The {rule.reward_gold}-gold reward was already paid."
            return (
                f"Quest {quest_id} is already completed.{paid} "
                "No further reward."
            )
        if quest.get("status") != "accepted":
            return f"Quest {quest_id} is not accepted yet."
        missing = self._missing_proof(rule)
        if missing:
            return missing
        self._consume_proof(rule)
        quest["status"] = "completed"
        quest["progress"] = int(quest["goal"])
        quest["rewarded"] = True
        paid = self._pay_reward(quest, rule)
        return (
            f"Quest {quest_id} is completed. "
            f"The {rule.required_item or 'proof'} was turned in.{paid}"
        )

    def hammer_place(self) -> str:
        """Where the forging hammer is right now."""
        quest = self.data.get("quests", {}).get("lost_hammer", {})
        status = str(quest.get("status") or "available")
        if status == "completed":
            return "returned"
        if "forging hammer" in self.inventory:
            return "carried"
        if status == "accepted":
            return "with_mira"
        return "lost"

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

    def _quest_context(
        self,
        npc_id: str,
        quest_id: str,
    ) -> tuple[dict | None, QuestSpec | None, str]:
        quest = self.data["quests"].get(quest_id)
        if quest is None:
            known = ", ".join(sorted(self.data["quests"])) or "(none)"
            return (
                None,
                None,
                (f"Unknown quest '{quest_id}'. Known quests: {known}."),
            )
        rule = self.config.quests.get(quest_id)
        giver = rule.giver if rule is not None else ""
        if giver and giver != npc_id:
            giver_name = self.config.npc(giver).name
            return (
                quest,
                rule,
                (f"Only {giver_name} can change quest {quest_id}."),
            )
        if rule is None:
            return quest, None, f"Quest {quest_id} has no configured rules."
        return quest, rule, ""

    def _reward_already_paid(self, quest: dict) -> bool:
        if quest.get("rewarded"):
            return True
        # Older saves marked the quest completed without a rewarded flag.
        return quest.get("status") == "completed"

    def _missing_proof(self, rule: QuestSpec) -> str:
        required = rule.required_item
        if required and required not in self.inventory:
            return (
                f"Cannot complete {rule.quest_id}: the player is not "
                f"carrying {required}. Turn it in to Rowan when they "
                "have it. Do not say to bring it to Bram."
            )
        return ""

    def _consume_proof(self, rule: QuestSpec) -> None:
        required = rule.required_item
        if required and required in self.data["player"]["inventory"]:
            self.data["player"]["inventory"].remove(required)

    def _pay_reward(self, quest: dict, rule: QuestSpec) -> str:
        if rule.reward_gold <= 0:
            self.save()
            return ""
        paid = self.adjust_gold(
            rule.reward_gold,
            f"reward for {quest['title']}",
        )
        return " " + paid


def _service_allowed(reason: str, services: list[str]) -> bool:
    text = reason.lower()
    return any(service.lower() in text for service in services if service)


def _match_choice(requested: str, choices: list[str]) -> str | None:
    needle = requested.strip().lower()
    for choice in choices:
        if choice.lower() == needle:
            return choice
    return None
