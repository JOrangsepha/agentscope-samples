# -*- coding: utf-8 -*-
"""Structured output produced on every NPC turn."""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class NpcTurn(BaseModel):
    """What the NPC says, and how the relationship shifts."""

    reply: str = Field(
        description="Spoken line, in character, one or two sentences.",
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
        description="Favorability change this turn, from -3 to 3.",
        ge=-3,
        le=3,
    )
    affinity_reason: str = Field(
        description="Short reason for the affinity change.",
    )
