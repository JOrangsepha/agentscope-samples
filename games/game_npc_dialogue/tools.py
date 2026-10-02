# -*- coding: utf-8 -*-
"""Game tools registered on each NPC agent."""
from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from agentscope.permission import PermissionBehavior, PermissionDecision
from agentscope.tool import FunctionTool

from game_state import GameState
from memory_store import remember_fact

_ALLOW = PermissionDecision(
    behavior=PermissionBehavior.ALLOW,
    message="Town gameplay tools are allowed.",
)

# Removed before the spoken reply so that line cannot move gold or quests.
GAME_TOOL_NAMES = [
    "give_item",
    "remember_player",
    "charge_player",
    "accept_quest",
    "complete_quest",
    "no_action",
]


def build_npc_tools(
    state: GameState,
    npc_id: str,
    memory_dir: Path,
) -> list[FunctionTool]:
    """Tools this NPC is allowed to call. Quest and gold are not universal."""
    npc = state.config.npc(npc_id)

    def give_item(item: str) -> str:
        """Give the player an item from the stock you can give now.

        Args:
            item: Item name. It must be on your current stock list.
        """
        return state.give_item(npc_id, item)

    def no_action(reason: str) -> str:
        """Record that gold, items, quests, and memory stay as they are.

        Call this when you will not give an item, charge the player,
        accept or complete a quest, or save a new fact. Do not describe
        a gift or a payment in text instead of calling a tool.

        Args:
            reason: Short reason, such as small talk or a refusal.
        """
        cleaned = " ".join(reason.split()) or "Nothing to change."
        return f"No game state changed ({cleaned})."

    def remember_player(fact: str) -> str:
        """Save one durable fact about the player for later sessions.

        Args:
            fact: One sentence, such as the player's name or trade.
        """
        return remember_fact(memory_dir, fact)

    def charge_player(amount: int, reason: str) -> str:
        """Charge the player for a service you offer. This never adds gold.

        Args:
            amount: Coins the player pays. Must be greater than zero.
            reason: A service from your list, such as a bed or a room.
        """
        return state.charge(npc_id, amount, reason)

    def accept_quest(quest_id: str) -> str:
        """Accept a quest you give, once, while it is still available.

        Args:
            quest_id: Quest id from the game state, such as lost_hammer.
        """
        result = state.accept_quest(npc_id, quest_id)
        if "now accepted" in result:
            remember_fact(
                memory_dir,
                f"The player accepted quest {quest_id}.",
            )
        return result

    def complete_quest(quest_id: str) -> str:
        """Complete a quest you give if the player is carrying the proof.

        The reward is paid by the game, once. Calling this again does not
        pay a second time, and speech alone is not proof.

        Args:
            quest_id: Quest id from the game state, such as lost_hammer.
        """
        result = state.complete_quest(npc_id, quest_id)
        if "is completed" in result and "already" not in result:
            remember_fact(
                memory_dir,
                f"The player completed quest {quest_id}.",
            )
        return result

    functions: list[Callable[..., str]] = [
        give_item,
        remember_player,
        no_action,
    ]
    if npc.can_charge:
        functions.append(charge_player)
    if state.config.gives_quests(npc_id):
        functions.extend([accept_quest, complete_quest])
    return [
        FunctionTool(
            func,
            is_concurrency_safe=False,
            is_read_only=False,
            permission=_ALLOW,
        )
        for func in functions
    ]
