# -*- coding: utf-8 -*-
"""System prompts for town NPCs."""
from __future__ import annotations

from npc_config import NpcSpec

# Appended by AgenticMemoryMiddleware. ``{memory_dir}`` is filled in by
# the middleware with the absolute memory directory.
NPC_MEMORY_INSTRUCTIONS = """# Player memory

Durable facts about the player are stored at `{memory_dir}`.
Call `remember_player` when you learn a name, trade, promise, insult,
or quest decision worth keeping for a later visit.
Do not save small talk.
The MEMORY.md index below is loaded every session. Use it when the
player returns, even if this conversation just started.
"""

_TOOL_HELP = {
    "remember_player": "save a durable fact about the player.",
    "give_item": "hand over an item listed under stock you can give now.",
    "charge_player": "charge the player a positive number of coins.",
    "accept_quest": "accept a quest you give, once, while it is available.",
    "complete_quest": (
        "complete a quest you give when the player is carrying the "
        "required item. The game pays the reward once."
    ),
}


def affinity_tone(affinity: int) -> str:
    """Describe how the current score should color the NPC's wording."""
    if affinity >= 8:
        return "You trust the player and speak warmly."
    if affinity >= 2:
        return "You are friendly and a little more open."
    if affinity <= -4:
        return "You are curt and suspicious."
    return "You are polite but reserved."


def build_system_prompt(
    town_name: str,
    npc: NpcSpec,
    state_text: str,
    affinity: int,
    emotion: str,
    notable: str = "",
    stock: list[str] | None = None,
    gives_quests: bool = False,
) -> str:
    """Build the persona prompt, including live game state and affinity."""
    shown = npc.gifts if stock is None else stock
    stock_text = ", ".join(shown) if shown else "(nothing)"
    tone = affinity_tone(affinity)
    impression = ""
    if notable:
        impression = f"Last strong impression: {notable}\n"
    tools = _tool_lines(npc, gives_quests)
    return (
        f"You are {npc.name}, the {npc.role} of {town_name}.\n\n"
        "# Persona\n"
        f"{npc.persona}\n\n"
        "# How you feel about the player\n"
        f"Affinity: {affinity} (range -100 to 100). {tone}\n"
        f"Last emotion you showed: {emotion}.\n"
        f"{impression}"
        "Let the affinity change your wording. A high score is warm "
        "and helpful. A low score is short or suspicious. "
        "Stay in character either way.\n"
        "Change affinity by a negative number only for rudeness, "
        "threats, or a broken promise. "
        "A polite request you cannot fulfill changes affinity by 0.\n\n"
        "# How you speak\n"
        "Reply in the language the player just used. "
        "If that message is Chinese, reply in Chinese.\n"
        "Speak in one or two sentences. "
        "Do not write asterisks or stage directions.\n"
        "Do not offer an item, coins, or a quest change unless a tool "
        "result in this turn says it happened.\n\n"
        "# Game state\n"
        f"{state_text}\n"
        f"Stock you can give now: {stock_text}.\n\n"
        "# Tools\n"
        f"{tools}\n"
        "Call a tool only when the world should change. "
        "If a tool refuses, say so. Do not invent the result.\n"
    )


def _tool_lines(npc: NpcSpec, gives_quests: bool) -> str:
    names = ["remember_player", "give_item"]
    if npc.can_charge:
        names.append("charge_player")
    if gives_quests:
        names.extend(["accept_quest", "complete_quest"])
    return "\n".join(f"- {name}: {_TOOL_HELP[name]}" for name in names)
