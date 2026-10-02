# -*- coding: utf-8 -*-
"""Structured output produced on every NPC turn."""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class NpcTurn(BaseModel):
    """What the NPC says, and how the relationship shifts."""

    reply: str = Field(
        description=(
            "Spoken line in the player's language, one or two sentences, "
            "with no asterisks or stage directions. Mention an item, "
            "coins, or a quest change only if a tool result this turn "
            "says it happened."
        ),
    )
    emotion: Literal[
        "neutral",
        "happy",
        "annoyed",
        "grateful",
        "warm",
        "suspicious",
    ] = Field(description="Emotion shown to the player this turn.")
    affinity_delta: int = Field(
        description=(
            "Favorability change from -3 to 3. Use a negative number "
            "only for rudeness, threats, or a broken promise. Use 0 "
            "when a polite request cannot be fulfilled."
        ),
        ge=-3,
        le=3,
    )
    affinity_reason: str = Field(
        description="Short reason for the affinity change.",
    )
