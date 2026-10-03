# -*- coding: utf-8 -*-
"""Keyword-scripted chat model for offline tests and the demo CLI.

Act steps may call the game tools that were actually offered. The spoken
line is a later call that only emits ``GenerateStructuredOutput``, after
tool results are already in the conversation.
"""
from __future__ import annotations

import json
import re
from typing import Any

from agentscope.credential import CredentialBase
from agentscope.formatter import DashScopeChatFormatter
from agentscope.message import TextBlock, ToolCallBlock
from agentscope.model import ChatModelBase, ChatResponse

from speech import ACT_CUE, SPEAK_CUE, learn_player_name, reply_language

_AFFINITY_RE = re.compile(r"Affinity:\s*(-?\d+)")
_NPC_RE = re.compile(r"^You are ([^,]+),", re.MULTILINE)
_STRUCTURED = "GenerateStructuredOutput"

_GIFTS = (
    "horseshoe",
    "iron nail",
    "brown loaf",
    "town seal",
    "forging hammer",
)
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

    def __init__(
        self,
        act_text: str | None = None,
        forced_emotion: str | None = None,
        forced_delta: int | None = None,
    ) -> None:
        super().__init__(
            credential=MockCredential(),
            model="scripted-npc",
            parameters=ChatModelBase.Parameters(),
            stream=False,
        )
        # Built-in models set this. Agent.reply reads it before each call.
        self.formatter = DashScopeChatFormatter()
        self.calls: list[dict[str, str]] = []
        # When set, the action phase returns this text and no tool call.
        self.act_text = act_text
        self.forced_emotion = forced_emotion
        self.forced_delta = forced_delta
        # Tests set these to force one English speak, or a refusal line.
        self.english_first = False
        self.forced_reply: str | None = None
        self.gave_english = False

    async def _call_api(
        self,
        model_name: str,
        messages: list,
        tools: list[dict] | None = None,
        tool_choice: Any = None,
        **kwargs: Any,
    ) -> ChatResponse:
        """Return one act step, or the spoken structured output."""
        del model_name, kwargs
        system = _system_text(messages)
        player = _player_text(messages)
        names = _tool_names(tools)
        this_turn = _since_player(messages)
        saw = _saw_tool_result(this_turn)
        phase = "speak" if _STRUCTURED in names else "act"
        choice = getattr(tool_choice, "mode", None)
        self.calls.append(
            {
                "phase": phase,
                "system": system,
                "user": player,
                "tools": ",".join(names),
                "saw_tool_result": "yes" if saw else "no",
                "tool_choice": "" if choice is None else str(choice),
            },
        )
        if phase == "speak":
            response = _speak_response(
                len(self.calls),
                self.act_text,
                system,
                player,
                _tool_result_text(this_turn),
            )
            response = _with_forced_score(
                response,
                self.forced_emotion,
                self.forced_delta,
            )
            return _apply_speak_overrides(response, system, self)
        return _act_response(
            self.act_text,
            saw,
            set(names),
            system,
            player,
        )


def _speak_response(
    number: int,
    act_text: str | None,
    system: str,
    player: str,
    tool_text: str,
) -> ChatResponse:
    if act_text:
        reply, emotion, delta, reason = (
            act_text,
            "neutral",
            0,
            "Copied the action line.",
        )
    else:
        reply, emotion, delta, reason = _choose_reply(
            system,
            player,
            tool_text,
        )
    return _structured(number, reply, emotion, delta, reason)


def _act_response(
    act_text: str | None,
    saw: bool,
    offered: set[str],
    system: str,
    player: str,
) -> ChatResponse:
    if act_text and not saw:
        return _text(act_text)
    if saw:
        return _text("The tools have finished.")
    blocks = _game_tool_calls(system, player, offered)
    if blocks:
        return ChatResponse(content=blocks, is_last=True)
    if "no_action" in offered:
        block = _call(
            "no_action",
            {"reason": "Nothing to change."},
            "none",
        )
        return ChatResponse(content=[block], is_last=True)
    return _text("Nothing to change.")


def _structured(
    number: int,
    reply: str,
    emotion: str,
    delta: int,
    reason: str,
) -> ChatResponse:
    block = ToolCallBlock(
        id=f"structured-{number}",
        name=_STRUCTURED,
        input=json.dumps(
            {
                "reply": reply,
                "emotion": emotion,
                "affinity_delta": delta,
                "affinity_reason": reason,
            },
        ),
    )
    return ChatResponse(content=[block], is_last=True)


def _with_forced_score(response, emotion, delta):
    """Overwrite the scripted emotion or delta for one speak call."""
    if emotion is None and delta is None:
        return response
    block = response.content[0]
    payload = json.loads(block.input)
    if emotion is not None:
        payload["emotion"] = emotion
    if delta is not None:
        payload["affinity_delta"] = delta
    block.input = json.dumps(payload)
    return response


def _set_reply(response, reply: str):
    block = response.content[0]
    payload = json.loads(block.input)
    payload["reply"] = reply
    block.input = json.dumps(payload)
    return response


def _apply_speak_overrides(response, system: str, model: ScriptedNpcModel):
    """Force a test reply, or supply Chinese when the prompt requires it."""
    if model.forced_reply:
        return _set_reply(response, model.forced_reply)
    if "Reply in Simplified Chinese." not in system:
        return response
    reply = json.loads(response.content[0].input).get("reply", "")
    if reply_language(str(reply)) == "Simplified Chinese":
        return response
    if model.english_first and not model.gave_english:
        model.gave_english = True
        return _set_reply(response, "Here's your brown loaf.")
    return _set_reply(response, "给你。")


def _text(text: str) -> ChatResponse:
    return ChatResponse(content=[TextBlock(text=text)], is_last=True)


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


def _player_text(messages: list) -> str:
    for message in reversed(messages):
        if getattr(message, "role", None) != "user":
            continue
        text = message.get_text_content() or ""
        if _is_cue(text):
            continue
        return text
    return ""


def _since_player(messages: list) -> list:
    """Messages after the latest real player line, excluding the speak cue."""
    start = 0
    for index, message in enumerate(messages):
        if getattr(message, "role", None) != "user":
            continue
        text = message.get_text_content() or ""
        if _is_cue(text):
            continue
        start = index + 1
    return list(messages[start:])


def _saw_tool_result(messages: list) -> bool:
    for message in messages:
        if _tool_blocks(message):
            return True
    return False


def _tool_result_text(messages: list) -> str:
    parts: list[str] = []
    for message in messages:
        for block in _tool_blocks(message):
            output = getattr(block, "output", "")
            if isinstance(output, str):
                parts.append(output)
            else:
                for item in output:
                    parts.append(getattr(item, "text", str(item)))
    return "\n".join(parts)


def _tool_blocks(message) -> list:
    getter = getattr(message, "get_content_blocks", None)
    if getter is None:
        return []
    return list(getter("tool_result"))


def _is_cue(text: str) -> bool:
    return text.startswith((SPEAK_CUE, ACT_CUE))


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


def _stock_text(system: str) -> str:
    for line in system.splitlines():
        if line.lower().startswith("stock you can give now:"):
            return line.lower()
    return ""


def _call(name: str, arguments: dict, suffix: str) -> ToolCallBlock:
    return ToolCallBlock(
        id=f"{name}-{suffix}",
        name=name,
        input=json.dumps(arguments),
    )


def _claims_hammer(user_l: str) -> bool:
    if "found the hammer" in user_l or "found your forging hammer" in user_l:
        return True
    if "hammer is back" in user_l or "returned the hammer" in user_l:
        return True
    if "here it is" in user_l and "hammer" in user_l:
        return True
    if "reward" in user_l and "hammer" in user_l:
        return True
    return False


def _wants_quest(user_l: str) -> bool:
    if _claims_hammer(user_l):
        return False
    if "quest" in user_l:
        return True
    return "accept" in user_l and "hammer" in user_l


def _game_tool_calls(
    system: str,
    user: str,
    offered: set[str],
) -> list[ToolCallBlock]:
    """Side-effect tools for this line. Structured output is a later call."""
    user_l = user.lower()
    calls: list[ToolCallBlock] = []
    learned = learn_player_name(user)
    if learned and "remember_player" in offered:
        if "bak" in user_l:
            fact = f"The player's name is {learned}, a baker."
        else:
            fact = f"The player's name is {learned}."
        calls.append(_call("remember_player", {"fact": fact}, learned))
    gift = _requested_gift(user_l, system)
    if gift is not None and "give_item" in offered:
        calls.append(_call("give_item", {"item": gift}, gift))
    charge = _charge_request(user_l)
    if charge is not None and "charge_player" in offered:
        amount, reason = charge
        calls.append(
            _call(
                "charge_player",
                {"amount": amount, "reason": reason},
                reason,
            ),
        )
    if _claims_hammer(user_l) and "complete_quest" in offered:
        calls.append(
            _call(
                "complete_quest",
                {"quest_id": "lost_hammer"},
                "done",
            ),
        )
    elif _wants_quest(user_l) and "accept_quest" in offered:
        calls.append(
            _call(
                "accept_quest",
                {"quest_id": "lost_hammer"},
                "accepted",
            ),
        )
    return calls


def _charge_request(user_l: str) -> tuple[int, str] | None:
    match = re.search(r"charge me (\d+) gold for (?:a |an )?(.+)", user_l)
    if match is None:
        return None
    return int(match.group(1)), match.group(2).strip(" .")


def _requested_gift(user_l: str, system: str) -> str | None:
    stock = _stock_text(system)
    for gift in _GIFTS:
        if gift in user_l and gift in stock:
            return gift
    return None


def _choose_reply(
    system: str,
    user: str,
    tool_text: str,
) -> tuple[str, str, int, str]:
    """Pick the spoken line, emotion, and affinity delta."""
    user_l = user.lower()
    matchers = (
        _paid_reply,
        _recalled_reply,
        _news_reply,
        _introduction_reply,
        _rude_reply,
        _hammer_reply,
        _thanks_reply,
        _quest_reply,
        _gift_reply,
        _warm_greeting_reply,
        _sentiment_reply,
    )
    for matcher in matchers:
        chosen = matcher(system, user_l, tool_text)
        if chosen is not None:
            return chosen
    npc = _npc_name(system)
    return (
        _DEFAULT_LINES.get(npc, "Speak, traveler."),
        "neutral",
        0,
        "No change.",
    )


def _paid_reply(
    system: str,
    user_l: str,
    tool_text: str,
) -> tuple[str, str, int, str] | None:
    """Confirm a charge, or admit the service is not on the list."""
    del system, user_l
    lowered = tool_text.lower()
    if "cannot charge" in lowered:
        return (
            "I cannot charge for that.",
            "neutral",
            0,
            "Not a listed service.",
        )
    match = re.search(r"paid (\d+) gold \(([^)]*)\)", lowered)
    if match is None:
        return None
    reason = match.group(2).strip()
    return (
        f"Here you go. That covers the {reason}.",
        "warm",
        1,
        "The player paid for a service.",
    )


def _recalled_reply(
    system: str,
    user_l: str,
    tool_text: str,
) -> tuple[str, str, int, str] | None:
    del tool_text
    if "remember" not in user_l:
        return None
    lowered = system.lower()
    if "lira" in lowered:
        return (
            "Aye, I remember you, Lira the baker.",
            "warm",
            1,
            "The player returned and I recalled their name.",
        )
    bits = []
    if "kestrel" in lowered:
        bits.append("Kestrel")
    if "stupid thief" in lowered:
        bits.append("you said stupid thief")
    if not bits:
        return None
    return (
        "Aye, I remember you. " + " ".join(bits) + ".",
        "warm",
        0,
        "Recalled a stored fact in the reply.",
    )


def _news_reply(
    system: str,
    user_l: str,
    tool_text: str,
) -> tuple[str, str, int, str] | None:
    """Repeat a public rumor when the player asks for news."""
    del tool_text
    asked = any(
        phrase in user_l
        for phrase in ("news", "rumor", "what do people", "what people say")
    )
    if not asked:
        return None
    rumors = _rumor_lines(system)
    if not rumors:
        return ("I have heard no town rumor.", "neutral", 0, "No rumor.")
    heard = " ".join(rumors[:2])
    return (
        f"I heard this: {heard}",
        "neutral",
        0,
        "Shared a public rumor.",
    )


def _rumor_lines(system: str) -> list[str]:
    lines = []
    in_rumors = False
    for line in system.splitlines():
        if line.startswith("# Town rumors"):
            in_rumors = True
            continue
        if in_rumors and line.startswith("# "):
            break
        if in_rumors and line.startswith("- "):
            lines.append(line[2:].strip())
    return lines


def _introduction_reply(
    system: str,
    user_l: str,
    tool_text: str,
) -> tuple[str, str, int, str] | None:
    del system, tool_text
    learned = learn_player_name(user_l)
    if learned is None:
        return None
    return (
        _name_reply(learned, user_l),
        "neutral",
        0,
        "The player shared their name.",
    )


def _thanks_reply(
    system: str,
    user_l: str,
    tool_text: str,
) -> tuple[str, str, int, str] | None:
    del system, tool_text
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
    tool_text: str,
) -> tuple[str, str, int, str] | None:
    del system
    if not _claims_hammer(user_l):
        return None
    result = tool_text.lower()
    if "already completed" in result:
        return (
            "The hammer is already home. I will not pay twice.",
            "neutral",
            0,
            "The quest was already rewarded.",
        )
    if "gained" in result and "gold" in result:
        return (
            "The hammer is home. Take these coins.",
            "happy",
            2,
            "The player finished the quest.",
        )
    return (
        "I do not see the forging hammer in your pack.",
        "neutral",
        0,
        "No proof of the hammer.",
    )


def _quest_reply(
    system: str,
    user_l: str,
    tool_text: str,
) -> tuple[str, str, int, str] | None:
    del system, tool_text
    if _wants_quest(user_l):
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
    tool_text: str,
) -> tuple[str, str, int, str] | None:
    del tool_text
    gift = _requested_gift(user_l, system)
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
    tool_text: str,
) -> tuple[str, str, int, str] | None:
    del tool_text
    if _affinity(system) >= 2 and user_l.strip() in _GREETINGS:
        return (
            "It is good to see you again.",
            "happy",
            0,
            "Affinity is already high, so the greeting is warm.",
        )
    return None


def _sentiment_reply(
    system: str,
    user_l: str,
    tool_text: str,
) -> tuple[str, str, int, str] | None:
    """Speak to the tone note when no quest or gift line matched."""
    del user_l, tool_text
    positive = "Player sentiment: positive" in system
    negative = "Player sentiment: negative" in system
    if not positive and not negative:
        return None
    chinese = "Reply in Simplified Chinese." in system
    if negative:
        reply = "说话客气一点。" if chinese else "Watch your tone."
        return (reply, "annoyed", 0, "The player spoke harshly.")
    reply = "你这么说，我很高兴。" if chinese else "That is kind of you to say."
    return (reply, "grateful", 0, "The player spoke kindly.")


def _name_reply(name: str, user_l: str) -> str:
    if "bak" in user_l:
        return f"{name}, is it? I will remember a baker."
    return f"{name}, is it? I will remember you."


def _rude_reply(
    system: str,
    user_l: str,
    tool_text: str,
) -> tuple[str, str, int, str] | None:
    del system, tool_text
    if "thief" in user_l or "stupid" in user_l:
        return (
            "Watch your tongue.",
            "annoyed",
            -2,
            "The player was rude.",
        )
    return None
