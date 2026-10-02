# -*- coding: utf-8 -*-
"""Game tools registered on each NPC agent."""
from __future__ import annotations

from pathlib import Path

from agentscope.permission import PermissionBehavior, PermissionDecision
from agentscope.tool import FunctionTool

from game_state import GameState
from memory_store import remember_fact

_ALLOW = PermissionDecision(
    behavior=PermissionBehavior.ALLOW,
    message="Town gameplay tools are allowed.",
)


def build_npc_tools(
    state: GameState,
    npc_id: str,
    memory_dir: Path,
) -> list[FunctionTool]:
    """Tools that mutate this town's save for one NPC."""

    def give_item(item: str) -> str:
        """Give the player an item from this NPC's stock.

        Args:
            item: Item name. It must be one of the gifts you carry.
        """
        return state.give_item(npc_id, item)

    def adjust_gold(amount: int, reason: str) -> str:
        """Change how much gold the player is carrying.

        Args:
            amount: Coins to add. Use a negative number when the player pays.
            reason: Short reason, such as a reward or a purchase.
        """
        return state.adjust_gold(amount, reason)

    def update_quest(quest_id: str, status: str, progress: int) -> str:
        """Update a quest's status and absolute progress.

        Args:
            quest_id: Quest id from the game state, such as lost_hammer.
            status: One of available, accepted, or completed.
            progress: Absolute progress count, not a delta.
        """
        return state.update_quest(quest_id, status, progress)

    def remember_player(fact: str) -> str:
        """Save one durable fact about the player for later sessions.

        Args:
            fact: One sentence, such as the player's name or trade.
        """
        return remember_fact(memory_dir, fact)

    functions = (give_item, adjust_gold, update_quest, remember_player)
    return [
        FunctionTool(
            func,
            is_concurrency_safe=False,
            is_read_only=False,
            permission=_ALLOW,
        )
        for func in functions
    ]
