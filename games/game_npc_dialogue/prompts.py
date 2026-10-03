# -*- coding: utf-8 -*-
"""System prompts for town NPCs."""
from __future__ import annotations

from npc_config import NpcSpec

# Appended by AgenticMemoryMiddleware. ``{memory_dir}`` is filled in by
# the middleware with the absolute memory directory.
NPC_MEMORY_INSTRUCTIONS = """# Player memory

Facts live at `{memory_dir}`. Call `remember_player` for a name,
trade, promise, insult, or quest decision. Skip small talk.
MEMORY.md below is loaded every visit.
"""

_TOOL_HELP = {
    "remember_player": "save a durable fact about the player.",
    "give_item": "hand over an item listed under stock.",
    "charge_player": "charge for a listed service only, such as a bed.",
    "accept_quest": "accept a quest you give, once, while it is available.",
    "complete_quest": (
        "complete your quest when the player carries the item. "
        "The game pays the reward once."
    ),
    "no_action": (
        "gold, items, quests, and memory stay unchanged. "
        "Speech cannot give an item or take gold."
    ),
}


def hammer_fact(
    status: str,
    place: str = "",
    viewer: str = "",
) -> str:
    """Describe the hammer from the quest status and where it is now.

    ``place`` is ``lost``, ``with_mira``, ``carried``, or ``returned``.
    An empty place is inferred from ``status`` alone. Only Mira is told
    when she is holding the hammer. Other residents are told not to
    reveal that location.
    """
    where = place or _place_for_status(status)
    if where == "returned" or status == "completed":
        return (
            "The hammer was returned; the quest is completed. "
            "Do not say the hammer is still lost or was never lost."
        )
    if where == "carried":
        return (
            "The player is carrying the forging hammer. "
            "If they hand it over, call complete_quest. "
            "Do not call the hammer missing."
        )
    if where == "with_mira":
        if viewer and viewer != "mira":
            return (
                "The quest is accepted and is not completed. "
                "Do not say where the hammer is. "
                "Turn the hammer in to Rowan. "
                "Do not say to bring it to Bram."
            )
        return (
            "Mira is holding Bram's forging hammer and can give it. "
            "The quest is accepted and is not completed. "
            "Do not call the hammer missing."
        )
    return (
        "Bram lost his forging hammer. The quest is not completed, "
        "so the hammer is still lost. "
        "Do not say the hammer was never lost."
    )


def _place_for_status(status: str) -> str:
    if status == "completed":
        return "returned"
    if status == "accepted":
        return "with_mira"
    return "lost"


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
    hammer_status: str = "available",
    hammer_place: str = "",
    paid_note: str = "",
    granted_note: str = "",
    news_note: str = "",
    ledger_note: str = "",
    recall_note: str = "",
    rumors: str = "",
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
        "Stay in character. A negative change is only for rudeness, "
        "threats, or a broken promise. "
        "A polite question is never rudeness. "
        "A request you cannot fulfill changes affinity by 0.\n\n"
        "# Game state\n"
        f"{state_text}\n"
        f"Stock you can give now: {stock_text}.\n"
        f"{_service_line(npc)}"
        "# Town rumors\n"
        f"{_rumor_text(rumors)}\n\n"
        "# Facts\n"
        f"{hammer_fact(hammer_status, hammer_place, npc.npc_id)}\n"
        "Bram is the blacksmith, Mira the innkeeper of the "
        "Oak and Lantern, Rowan the elder. "
        "Rowan is not a craftsman and does not carve handles. "
        "The Oak and Lantern charges for a bed or a room, not for meals. "
        "Do not say the inn serves a warm meal. "
        "The inn is the Oak and Lantern. Do not call it by another name. "
        "Do not give a resident the player's trade.\n"
        "Give only items listed under stock.\n\n"
    )
    if speaking:
        return head + _speech_rules(
            player_text,
            language,
            paid_note,
            granted_note,
            news_note,
            ledger_note,
            recall_note,
        )
    tools = _tool_lines(npc, gives_quests)
    return (
        head + "# Action\n"
        "Call one tool. Do not speak and do not write a sentence.\n"
        "If gold, items, quests, and memory stay the same, call the "
        "no_action tool. That includes greetings, memory questions, "
        "a repeat reward, and begging for an item you will not give. "
        "Do not write the tool name as a sentence.\n"
        "A sentence cannot give an item or take gold.\n\n"
        "# Tools\n"
        f"{tools}\n"
    )


def _rumor_text(rumors: str) -> str:
    cleaned = rumors.strip()
    if cleaned:
        return cleaned
    return (
        "None yet. Insults and quest news become public. "
        "A player's name, trade, gold, and inventory stay private."
    )


def _service_line(npc: NpcSpec) -> str:
    if not npc.services:
        return "\n"
    listed = ", ".join(npc.services)
    return f"Services you can charge for: {listed}.\n\n"


def language_banner(language: str, *, items: bool = True) -> str:
    """The language rule, stated before the rest of the speak prompt."""
    if language == "Simplified Chinese":
        names = (
            " Items: forging hammer=锻造锤, horseshoe=马掌, "
            "iron nail=铁钉, brown loaf=黑面包."
            if items
            else ""
        )
        return (
            "Reply in Simplified Chinese. 只用简体中文。 "
            "Keep the player's name exactly as written. 姓名不要音译。"
            " Inn: Oak and Lantern=橡树与灯笼旅店."
            f"{names}"
        )
    if language == "English":
        return "Reply in English."
    if language:
        return f"Reply in {language}."
    return "Reply in English only."


_WORLD = (
    "Roster: Bram the blacksmith forges blades and handles. "
    "Mira the innkeeper rents beds only; no meals are sold in town. "
    "Rowan the elder has no craft.\n"
)


def _speech_rules(
    player_text: str,
    language: str,
    paid_note: str,
    granted_note: str = "",
    news_note: str = "",
    ledger_note: str = "",
    recall_note: str = "",
) -> str:
    spoken = player_text.strip() or "(empty)"
    paid = f"{paid_note}\n" if paid_note else ""
    granted = f"{granted_note}\n" if granted_note else ""
    ledger = f"{ledger_note}\n" if ledger_note else ""
    news = f"{news_note}\n" if news_note else ""
    recall = f"{recall_note}\n" if recall_note else ""
    return (
        "# This turn\n"
        f"{language_banner(language)}\n"
        f"{_WORLD}"
        f"The player said: {spoken}\n"
        f"{paid}"
        f"{granted}"
        f"{ledger}"
        f"{news}"
        f"{recall}"
        "Speak in one or two sentences. No asterisks or stage directions.\n"
        "Emotion: neutral, happy, annoyed, grateful, warm, suspicious. "
        "affinity_delta is an integer from -3 to 3.\n"
        "A positive affinity_delta only if a tool result this turn "
        "gave an item, took coins, or accepted or completed a quest. "
        "A question, small talk, or already/cannot does not.\n"
        "Call GenerateStructuredOutput directly. Do not write the "
        "emotion, affinity_delta, or affinity_reason as plain text.\n"
        "Do not claim a gift or a payment the tool result did not "
        "make. If the player paid, confirm it. Do not deny it.\n"
    )


def _tool_lines(npc: NpcSpec, gives_quests: bool) -> str:
    names = ["remember_player", "give_item", "no_action"]
    if npc.can_charge:
        names.append("charge_player")
    if gives_quests:
        names.extend(["accept_quest", "complete_quest"])
    return "\n".join(f"- {name}: {_TOOL_HELP[name]}" for name in names)
