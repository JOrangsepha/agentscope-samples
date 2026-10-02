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
    "no_action": (
        "call this when gold, items, quests, and memory should stay "
        "unchanged. Speech cannot give an item or take gold."
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
    speaking: bool = False,
    player_text: str = "",
    language: str = "",
) -> str:
    """Build the persona prompt, including live game state and affinity."""
    shown = npc.gifts if stock is None else stock
    stock_text = ", ".join(shown) if shown else "(nothing)"
    tone = affinity_tone(affinity)
    impression = ""
    if notable:
        impression = f"Last strong impression: {notable}\n"
    head = (
        f"You are {npc.name}, the {npc.role} of {town_name}.\n\n"
        "# Persona\n"
        f"{npc.persona}\n\n"
        "# How you feel about the player\n"
        f"Affinity: {affinity} (range -100 to 100). {tone}\n"
        f"Last emotion you showed: {emotion}.\n"
        f"{impression}"
        "Let the affinity change your wording. Stay in character.\n"
        "Change affinity by a negative number only for rudeness, "
        "threats, or a broken promise. "
        "A polite request you cannot fulfill changes affinity by 0.\n\n"
        "# Game state\n"
        f"{state_text}\n"
        f"Stock you can give now: {stock_text}.\n\n"
    )
    if speaking:
        return head + _speech_rules(player_text, language)
    tools = _tool_lines(npc, gives_quests)
    return (
        head + "# Action\n"
        "Call one tool. This phase does not speak to the player.\n"
        "If gold, items, quests, and memory should stay unchanged, "
        "call no_action.\n"
        "Do not write a spoken line. A sentence cannot give an item "
        "or take gold.\n\n"
        "# Tools\n"
        f"{tools}\n"
    )


def _speech_rules(player_text: str, language: str) -> str:
    spoken = player_text.strip() or "(empty)"
    lang = language or "English"
    return (
        "# This turn\n"
        f"The player said: {spoken}\n"
        f"Reply in {lang}.\n"
        "Speak in one or two sentences. "
        "Do not write asterisks or stage directions.\n"
        "Do not say you gave an item or took gold unless a tool "
        "result in this turn says that happened.\n"
    )


def _tool_lines(npc: NpcSpec, gives_quests: bool) -> str:
    names = ["remember_player", "give_item", "no_action"]
    if npc.can_charge:
        names.append("charge_player")
    if gives_quests:
        names.extend(["accept_quest", "complete_quest"])
    return "\n".join(f"- {name}: {_TOOL_HELP[name]}" for name in names)
