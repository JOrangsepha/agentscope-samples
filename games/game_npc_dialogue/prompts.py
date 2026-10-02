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
) -> str:
    """Build the persona prompt, including live game state and affinity."""
    gifts = ", ".join(npc.gifts) or "(none)"
    tone = affinity_tone(affinity)
    return (
        f"You are {npc.name}, the {npc.role} of {town_name}.\n\n"
        "# Persona\n"
        f"{npc.persona}\n\n"
        "# How you feel about the player\n"
        f"Affinity: {affinity} (range -100 to 100). {tone}\n"
        f"Last emotion you showed: {emotion}.\n"
        "Let the affinity change your wording. A high score is warm "
        "and helpful. A low score is short or suspicious. "
        "Stay in character either way.\n\n"
        "# Game state\n"
        f"{state_text}\n"
        f"Gifts you may give: {gifts}.\n\n"
        "# Tools\n"
        "- remember_player: save a durable fact about the player.\n"
        "- give_item: hand over an item you actually have in stock.\n"
        "- adjust_gold: change the player's gold (reward or payment).\n"
        "- update_quest: move a quest's status and progress.\n"
        "Call a tool when the world should change. Do not claim an item "
        "was given, gold moved, or a quest advanced unless the tool "
        "result says so.\n"
        "End every turn by calling GenerateStructuredOutput with the "
        "spoken reply, the emotion you are showing, the affinity_delta "
        "from -3 to 3, and a short affinity_reason.\n"
    )
