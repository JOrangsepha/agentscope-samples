# -*- coding: utf-8 -*-
"""Keyword-scripted chat model for offline tests and the demo CLI.

The script inspects the latest player line and the system prompt (which
already contains persona, affinity, and MEMORY.md). It emits the same
tool calls a live model is asked to emit, then finishes the turn with
``GenerateStructuredOutput``.
"""
from __future__ import annotations

import json
import re
from typing import Any

from agentscope.credential import CredentialBase
from agentscope.formatter import DashScopeChatFormatter
from agentscope.message import ToolCallBlock
from agentscope.model import ChatModelBase, ChatResponse

_NAME_RE = re.compile(r"my name is ([a-z]+)", re.IGNORECASE)
_AFFINITY_RE = re.compile(r"Affinity:\s*(-?\d+)")
_NPC_RE = re.compile(r"^You are ([^,]+),", re.MULTILINE)

_GIFTS = ("horseshoe", "iron nail", "brown loaf", "town seal")
_GREETINGS = {"hello", "hello.", "hi", "hi."}
_DEFAULT_LINES = {
    "Bram": "The forge is hot. Speak plainly.",
    "Mira": "Sit by the fire. The stew is on.",
    "Rowan": "The council is listening.",
}


class MockCredential(CredentialBase):
    """Credential placeholder. The scripted model never calls a provider."""

    @classmethod
    def get_chat_model_class(cls) -> type[ChatModelBase]:
        """Return the scripted model class."""
        return ScriptedNpcModel


class ScriptedNpcModel(ChatModelBase):
    """Offline stand-in for DashScope / OpenAI / Ollama."""

    def __init__(self) -> None:
        super().__init__(
            credential=MockCredential(),
            model="scripted-npc",
            parameters=ChatModelBase.Parameters(),
            stream=False,
        )
        # Built-in models set this. Agent.reply reads it before each call.
        self.formatter = DashScopeChatFormatter()
        self.calls: list[dict[str, str]] = []

    async def _call_api(
        self,
        model_name: str,
        messages: list,
        tools: list[dict] | None = None,
        tool_choice: Any = None,
        **kwargs: Any,
    ) -> ChatResponse:
        """Return scripted tool calls for one reasoning step."""
        del model_name, tool_choice, kwargs
        system = _system_text(messages)
        user = _latest_user_text(messages)
        self.calls.append(
            {
                "system": system,
                "user": user,
                "tools": ",".join(_tool_names(tools)),
            },
        )
        blocks = _game_tool_calls(system, user)
        reply, emotion, delta, reason = _choose_reply(system, user)
        blocks.append(
            ToolCallBlock(
                id=f"structured-{len(self.calls)}",
                name="GenerateStructuredOutput",
                input=json.dumps(
                    {
                        "reply": reply,
                        "emotion": emotion,
                        "affinity_delta": delta,
                        "affinity_reason": reason,
                    },
                ),
            ),
        )
        return ChatResponse(content=blocks, is_last=True)


def _tool_names(tools: list[dict] | None) -> list[str]:
    names = []
    for tool in tools or []:
        function = tool.get("function", tool)
        name = function.get("name")
        if isinstance(name, str):
            names.append(name)
    return names


def _system_text(messages: list) -> str:
    parts = []
    for message in messages:
        if getattr(message, "role", None) == "system":
            parts.append(message.get_text_content() or "")
    return "\n".join(parts)


def _latest_user_text(messages: list) -> str:
    for message in reversed(messages):
        if getattr(message, "role", None) == "user":
            return message.get_text_content() or ""
    return ""


def _npc_name(system: str) -> str:
    match = _NPC_RE.search(system)
    if match is None:
        return "NPC"
    return match.group(1)


def _affinity(system: str) -> int:
    match = _AFFINITY_RE.search(system)
    if match is None:
        return 0
    return int(match.group(1))


def _call(name: str, arguments: dict, suffix: str) -> ToolCallBlock:
    return ToolCallBlock(
        id=f"{name}-{suffix}",
        name=name,
        input=json.dumps(arguments),
    )


def _game_tool_calls(system: str, user: str) -> list[ToolCallBlock]:
    """Side-effect tools for this line. Structured output is separate."""
    user_l = user.lower()
    system_l = system.lower()
    calls: list[ToolCallBlock] = []
    name_match = _NAME_RE.search(user)
    if name_match:
        name = name_match.group(1).capitalize()
        if "bak" in user_l:
            fact = f"The player's name is {name}, a baker."
        else:
            fact = f"The player's name is {name}."
        calls.append(_call("remember_player", {"fact": fact}, name))
    gift = _requested_gift(user_l, system_l)
    if gift is not None:
        calls.append(_call("give_item", {"item": gift}, gift))
    if "found the hammer" in user_l or "hammer is back" in user_l:
        calls.append(
            _call(
                "update_quest",
                {
                    "quest_id": "lost_hammer",
                    "status": "completed",
                    "progress": 1,
                },
                "done",
            ),
        )
        calls.append(
            _call(
                "adjust_gold",
                {
                    "amount": 8,
                    "reason": "reward for returning the hammer",
                },
                "reward",
            ),
        )
    elif "quest" in user_l or ("accept" in user_l and "hammer" in user_l):
        calls.append(
            _call(
                "update_quest",
                {
                    "quest_id": "lost_hammer",
                    "status": "accepted",
                    "progress": 0,
                },
                "accepted",
            ),
        )
    return calls


def _requested_gift(user_l: str, system_l: str) -> str | None:
    for gift in _GIFTS:
        if gift in user_l and gift in system_l:
            return gift
    return None


def _choose_reply(system: str, user: str) -> tuple[str, str, int, str]:
    """Pick the spoken line, emotion, and affinity delta."""
    user_l = user.lower()
    matchers = (
        _recalled_reply,
        _introduction_reply,
        _rude_reply,
        _thanks_reply,
        _hammer_reply,
        _quest_reply,
        _gift_reply,
        _warm_greeting_reply,
    )
    for matcher in matchers:
        chosen = matcher(system, user_l)
        if chosen is not None:
            return chosen
    npc = _npc_name(system)
    return (
        _DEFAULT_LINES.get(npc, "Speak, traveler."),
        "neutral",
        0,
        "No change.",
    )


def _recalled_reply(
    system: str,
    user_l: str,
) -> tuple[str, str, int, str] | None:
    if "lira" in system.lower() and "remember" in user_l:
        return (
            "Aye, I remember you, Lira the baker.",
            "warm",
            1,
            "The player returned and I recalled their name.",
        )
    return None


def _introduction_reply(
    system: str,
    user_l: str,
) -> tuple[str, str, int, str] | None:
    del system
    match = _NAME_RE.search(user_l)
    if match is None:
        return None
    spoken = _name_reply(match.group(1).capitalize(), user_l)
    return (spoken, "grateful", 2, "The player shared their name.")


def _thanks_reply(
    system: str,
    user_l: str,
) -> tuple[str, str, int, str] | None:
    del system
    if "thank" not in user_l:
        return None
    return (
        "You are welcome. I will not forget the courtesy.",
        "grateful",
        2,
        "The player was polite.",
    )


def _hammer_reply(
    system: str,
    user_l: str,
) -> tuple[str, str, int, str] | None:
    del system
    if "found the hammer" in user_l or "hammer is back" in user_l:
        return (
            "The hammer is home. Take these coins.",
            "happy",
            2,
            "The player finished the quest.",
        )
    return None


def _quest_reply(
    system: str,
    user_l: str,
) -> tuple[str, str, int, str] | None:
    del system
    if "quest" in user_l or ("accept" in user_l and "hammer" in user_l):
        return (
            "The Lost Hammer is yours. Bring it back to Millhaven.",
            "neutral",
            1,
            "The player accepted a quest.",
        )
    return None


def _gift_reply(
    system: str,
    user_l: str,
) -> tuple[str, str, int, str] | None:
    gift = _requested_gift(user_l, system.lower())
    if gift is None:
        return None
    return (
        f"Take the {gift}. Do not waste good work.",
        "warm",
        1,
        "The player asked for a gift I can give.",
    )


def _warm_greeting_reply(
    system: str,
    user_l: str,
) -> tuple[str, str, int, str] | None:
    if _affinity(system) >= 2 and user_l.strip() in _GREETINGS:
        return (
            "It is good to see you again.",
            "happy",
            0,
            "Affinity is already high, so the greeting is warm.",
        )
    return None


def _name_reply(name: str, user_l: str) -> str:
    if "bak" in user_l:
        return f"{name}, is it? I will remember a baker."
    return f"{name}, is it? I will remember you."


def _rude_reply(
    system: str,
    user_l: str,
) -> tuple[str, str, int, str] | None:
    del system
    if "thief" in user_l or "stupid" in user_l:
        return (
            "Watch your tongue.",
            "annoyed",
            -2,
            "The player was rude.",
        )
    return None
