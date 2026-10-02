# -*- coding: utf-8 -*-
"""One visit to Millhaven: talk to NPCs and keep the save on disk."""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from agentscope.agent import Agent, InjectionConfig, ReActConfig
from agentscope.message import TextBlock, UserMsg
from agentscope.middleware import AgenticMemoryMiddleware, MiddlewareBase
from agentscope.model import ChatModelBase, ChatResponse
from agentscope.permission import PermissionMode
from agentscope.state import AgentState
from agentscope.tool import Toolkit

from game_state import GameState
from gossip import narrate_wait, prompt_block, record_public_events
from memory_store import remember_fact
from npc_config import TownConfig
from prompts import NPC_MEMORY_INSTRUCTIONS, build_system_prompt
from schema import NpcTurn
from speech import (
    ACT_CUE,
    REFUND_EN,
    REFUND_ZH,
    SPEAK_CUE,
    denies_paid_service,
    guard_unproven_transfer,
    learn_player_name,
    normalize_emotion,
    polite_question,
    polish_reply,
    reply_language,
)
from tools import GAME_TOOL_NAMES, build_npc_tools

_DELTA_MIN = -3
_DELTA_MAX = 3
_NOTABLE_DELTA = 2
_GAME_TOOLS = frozenset(GAME_TOOL_NAMES)
_PAID = re.compile(r"paid (\d+) gold \(([^)]*)\)", re.IGNORECASE)


@dataclass
class TurnResult:
    """Parsed NPC turn after affinity has been saved."""

    npc_id: str
    npc_name: str
    reply: str
    emotion: str
    affinity_delta: int
    affinity: int
    affinity_reason: str


class TownSession:
    """CLI-facing session. Each process start is a new visit.

    Short-term chat stays in ``AgentState`` until the process exits.
    Player facts survive in per-NPC memory files, and inventory, gold,
    quests, emotion, and affinity survive in ``game_state.json``.
    """

    def __init__(
        self,
        config: TownConfig,
        save_dir: str | Path,
        model: ChatModelBase,
    ) -> None:
        self.config = config
        self.save_dir = Path(save_dir)
        self.model = model
        self.game = GameState(self.save_dir / "game_state.json", config)
        self._agent_states: dict[str, AgentState] = {}

    def memory_dir(self, npc_id: str) -> Path:
        """Directory whose ``MEMORY.md`` this NPC reloads next visit."""
        self.config.npc(npc_id)
        return self.save_dir / "memory" / npc_id / "Memory"

    async def talk(self, npc_id: str, player_text: str) -> TurnResult:
        """Act with tools, then speak once those results are known."""
        learned = learn_player_name(player_text)
        if learned:
            self.game.set_player_name(learned)
            remember_fact(
                self.memory_dir(npc_id),
                f"The player's name is {learned}.",
            )
        before_items = list(self.game.inventory)
        before_gold = self.game.gold
        npc = self.config.npc(npc_id)
        language = reply_language(player_text)
        state = self._agent_states.setdefault(npc_id, AgentState())
        prior = list(state.context)
        try:
            result = await self._talk_body(
                npc_id,
                npc.name,
                player_text,
                language,
                before_items,
                before_gold,
            )
        except Exception:
            _drop_unfinished_turn(state, player_text, prior)
            raise
        return result

    async def _talk_body(
        self,
        npc_id: str,
        npc_name: str,
        player_text: str,
        language: str,
        before_items: list[str],
        before_gold: int,
    ) -> TurnResult:
        """Run one turn. The caller rolls history back if this raises."""
        actor = self._make_agent(npc_id, with_tools=True)
        await actor.reply(
            UserMsg(name=self.game.player_name, content=player_text),
        )
        state = self._agent_states[npc_id]
        if not _turn_has_game_tool(state, player_text):
            await actor.reply(UserMsg(name="director", content=_act_cue()))
        _drop_action_prose(state, player_text)
        paid = _payment(state, player_text)
        speaker = self._make_agent(
            npc_id,
            with_tools=False,
            player_text=player_text,
            language=language,
            paid_note=_paid_note(paid),
        )
        message = await speaker.reply(
            UserMsg(
                name="director",
                content=_speak_cue(player_text, language),
            ),
            structured_schema=NpcTurn,
        )
        if _needs_chinese_retry(language, message):
            message = await speaker.reply(
                UserMsg(
                    name="director",
                    content=(
                        f"{_speak_cue(player_text, language)}\n"
                        "The previous reply was not Simplified Chinese. "
                        "Reply in Simplified Chinese only."
                    ),
                ),
                structured_schema=NpcTurn,
            )
        result = _apply_turn(
            self.game,
            npc_id,
            npc_name,
            message,
            player_text,
            self.memory_dir(npc_id),
            language,
            self.game.stock(npc_id),
            _new_items(before_items, self.game.inventory),
            self.game.gold < before_gold,
            _quest_failed(state, player_text),
            paid,
        )
        record_public_events(
            self.save_dir,
            npc_id,
            npc_name,
            delta=result.affinity_delta,
            player_text=player_text,
            quest_event=_public_quest_event(state, player_text),
        )
        _scrub_history(state)
        return result

    def wait_in_town(self) -> str:
        """Let residents repeat the latest public rumor. No model call."""
        return narrate_wait(self.save_dir, self.config)

    def _make_agent(
        self,
        npc_id: str,
        *,
        with_tools: bool,
        player_text: str = "",
        language: str = "",
        paid_note: str = "",
    ) -> Agent:
        state = self._agent_states.setdefault(npc_id, AgentState())
        state.permission_context.mode = PermissionMode.BYPASS
        middlewares: list = [
            _memory_middleware(self.memory_dir(npc_id).parent),
        ]
        if with_tools:
            toolkit = Toolkit(
                tools=build_npc_tools(
                    self.game,
                    npc_id,
                    self.memory_dir(npc_id),
                ),
            )
            middlewares.append(ActionStopMiddleware())
            limit = 8
        else:
            toolkit = Toolkit()
            limit = 4
        return Agent(
            name=self.config.npc(npc_id).name,
            system_prompt=self._prompt(
                npc_id,
                speaking=not with_tools,
                player_text=player_text,
                language=language,
                paid_note=paid_note,
            ),
            model=self.model,
            toolkit=toolkit,
            middlewares=middlewares,
            state=state,
            injection_config=InjectionConfig(inject_runtime_state=False),
            react_config=ReActConfig(max_iters=limit),
        )

    def _prompt(
        self,
        npc_id: str,
        *,
        speaking: bool = False,
        player_text: str = "",
        language: str = "",
        paid_note: str = "",
    ) -> str:
        npc = self.config.npc(npc_id)
        return build_system_prompt(
            town_name=self.config.town_name,
            npc=npc,
            state_text=self.game.describe(),
            affinity=self.game.affinity(npc_id),
            emotion=self.game.emotion(npc_id),
            notable=self.game.notable(npc_id),
            stock=self.game.stock(npc_id),
            gives_quests=self.config.gives_quests(npc_id),
            speaking=speaking,
            player_text=player_text,
            language=language,
            hammer_status=_hammer_status(self.game),
            hammer_place=self.game.hammer_place(),
            paid_note=paid_note,
            rumors=prompt_block(self.save_dir),
        )


def _memory_middleware(workdir: Path) -> AgenticMemoryMiddleware:
    """File-backed long-term memory. No vector store and no extra LLM call."""
    return AgenticMemoryMiddleware(
        workdir=str(workdir),
        parameters=AgenticMemoryMiddleware.Parameters(
            retrieval_async=False,
            memory_instructions=NPC_MEMORY_INSTRUCTIONS,
        ),
    )


class ActionStopMiddleware(MiddlewareBase):
    """End the action loop once a game tool has already run."""

    async def on_model_call(self, agent, input_kwargs, next_handler):
        """Skip the model when this turn already has a game tool result."""
        del agent
        messages = input_kwargs.get("messages") or []
        if _game_tools_done(messages):
            return ChatResponse(
                content=[TextBlock(text="Done.")],
                is_last=True,
            )
        return await next_handler(**input_kwargs)


def _act_cue() -> str:
    """One retry when the action step returned no game tool."""
    return (
        f"{ACT_CUE}\n"
        "You must call a tool now. Call no_action if nothing should "
        "change. Do not write a spoken line."
    )


def _hammer_status(game: GameState) -> str:
    quest = game.data.get("quests", {}).get("lost_hammer", {})
    return str(quest.get("status") or "available")


def _paid_note(paid: tuple[int, str] | None) -> str:
    if paid is None:
        return ""
    amount, reason = paid
    return (
        f"This turn the player paid {amount} gold for {reason}. "
        "Confirm that service. Do not deny it."
    )


def _payment(
    state: AgentState,
    player_text: str,
) -> tuple[int, str] | None:
    """Amount and reason from a successful charge_player result."""
    start = _player_index(state.context, player_text)
    if start is None:
        return None
    found = None
    for message in state.context[start + 1 :]:
        for block in _named_tool_blocks(message, {"tool_result"}):
            if block.name != "charge_player":
                continue
            match = _PAID.search(_result_text(block))
            if match:
                found = (int(match.group(1)), match.group(2).strip())
    return found


def _needs_chinese_retry(language: str, message) -> bool:
    if language != "Simplified Chinese":
        return False
    text = _structured_reply(message)
    if not text.strip():
        return False
    return reply_language(text) != "Simplified Chinese"


def _structured_reply(message) -> str:
    payload = getattr(message, "structured_output", None) or {}
    if payload.get("reply"):
        return str(payload["reply"])
    return message.get_text_content() or ""


def _speak_cue(player_text: str, language: str) -> str:
    """Turn-local instruction. It is removed from history after the reply."""
    return (
        f"{SPEAK_CUE}\n"
        f"The player said: {player_text}\n"
        f"Reply in {language}. "
        "One or two sentences, no stage directions. "
        "Mention a gift or a payment only if a tool result this turn "
        "says it succeeded."
    )


def _apply_turn(
    game: GameState,
    npc_id: str,
    npc_name: str,
    message,
    player_text: str,
    memory_dir: Path,
    language: str,
    stock: list[str],
    granted: list[str],
    charged: bool,
    quest_failed: bool,
    paid: tuple[int, str] | None,
) -> TurnResult:
    """Read structured output and write emotion plus affinity."""
    payload = message.structured_output or {}
    if not payload:
        text = polish_reply(message.get_text_content() or "")
        return TurnResult(
            npc_id=npc_id,
            npc_name=npc_name,
            reply=text or "(no reply)",
            emotion=game.emotion(npc_id),
            affinity_delta=0,
            affinity=game.affinity(npc_id),
            affinity_reason="The model did not return structured output.",
        )
    try:
        delta = int(payload.get("affinity_delta", 0))
    except (TypeError, ValueError):
        delta = 0
    delta = max(_DELTA_MIN, min(_DELTA_MAX, delta))
    if quest_failed and delta > 0:
        delta = 0
    if delta < 0 and polite_question(player_text):
        delta = 0
    emotion = normalize_emotion(str(payload.get("emotion", "")))
    reason = str(payload["affinity_reason"])
    reply = polish_reply(str(payload["reply"]), reason) or "(no reply)"
    if paid and denies_paid_service(reply):
        amount, reason_paid = paid
        game.adjust_gold(amount, f"refund {reason_paid}")
        charged = False
        reply = REFUND_ZH if language == "Simplified Chinese" else REFUND_EN
    reply = guard_unproven_transfer(
        reply,
        stock,
        granted,
        charged,
        language,
    )
    affinity = game.apply_affinity(npc_id, delta)
    game.set_emotion(npc_id, emotion)
    if abs(delta) >= _NOTABLE_DELTA:
        note = (
            f"Affinity {delta:+d} ({emotion}): {reason} "
            f"The player said: {player_text.strip()}"
        )
        game.set_notable(npc_id, note)
        remember_fact(memory_dir, note)
    return TurnResult(
        npc_id=npc_id,
        npc_name=npc_name,
        reply=reply,
        emotion=emotion,
        affinity_delta=delta,
        affinity=affinity,
        affinity_reason=reason,
    )


def _public_quest_event(state: AgentState, player_text: str) -> str:
    """``accepted`` or ``completed`` when a quest tool succeeded."""
    start = _player_index(state.context, player_text)
    if start is None:
        return ""
    event = ""
    for message in state.context[start + 1 :]:
        for block in _named_tool_blocks(message, {"tool_result"}):
            if block.name not in {"accept_quest", "complete_quest"}:
                continue
            if not _quest_succeeded(block.name, _result_text(block)):
                continue
            event = "accepted" if block.name == "accept_quest" else "completed"
    return event


def _quest_failed(state: AgentState, player_text: str) -> bool:
    """True when a quest tool ran and did not accept or complete."""
    start = _player_index(state.context, player_text)
    if start is None:
        return False
    failed = False
    for message in state.context[start + 1 :]:
        for block in _named_tool_blocks(message, {"tool_result"}):
            if block.name not in {"accept_quest", "complete_quest"}:
                continue
            if _quest_succeeded(block.name, _result_text(block)):
                return False
            failed = True
    return failed


def _quest_succeeded(name: str, text: str) -> bool:
    lowered = text.lower()
    if name == "accept_quest":
        return "now accepted" in lowered
    return "is completed" in lowered and "already" not in lowered


def _result_text(block) -> str:
    output = getattr(block, "output", "")
    if isinstance(output, str):
        return output
    parts = []
    for item in output:
        parts.append(getattr(item, "text", str(item)))
    return "\n".join(parts)


def _new_items(before: list[str], after: list[str]) -> list[str]:
    granted = []
    seen = list(before)
    for item in after:
        if item in seen:
            seen.remove(item)
            continue
        granted.append(item)
    return granted


def _game_tools_done(messages: list) -> bool:
    """True when a game tool result already follows the latest user line."""
    for message in reversed(messages):
        if getattr(message, "role", None) == "user":
            return False
        if _named_tool_blocks(message, {"tool_result"}):
            return True
    return False


def _player_index(context: list, player_text: str) -> int | None:
    found = None
    for index, message in enumerate(context):
        if getattr(message, "role", None) != "user":
            continue
        if (message.get_text_content() or "") == player_text:
            found = index
    return found


def _named_tool_blocks(message, kinds: set[str]) -> list:
    content = getattr(message, "content", None)
    if not isinstance(content, list):
        return []
    return [
        block
        for block in content
        if getattr(block, "type", None) in kinds
        and getattr(block, "name", None) in _GAME_TOOLS
    ]


def _keep_game_tools(message) -> bool:
    """Keep real game tool calls and results. Drop prose and other tools."""
    if getattr(message, "role", None) != "assistant":
        return False
    kept = _named_tool_blocks(message, {"tool_call", "tool_result"})
    if not kept:
        return False
    message.content = kept
    return True


def _turn_has_game_tool(state: AgentState, player_text: str) -> bool:
    start = _player_index(state.context, player_text)
    if start is None:
        return False
    for message in state.context[start + 1 :]:
        if _named_tool_blocks(message, {"tool_result"}):
            return True
    return False


def _drop_action_prose(state: AgentState, player_text: str) -> None:
    """Remove spoken text from the action turn before the reply is written."""
    start = _player_index(state.context, player_text)
    if start is None:
        return
    kept = [
        message
        for message in state.context[start + 1 :]
        if _keep_game_tools(message)
    ]
    state.context = list(state.context[: start + 1]) + kept


def _drop_unfinished_turn(
    state: AgentState,
    player_text: str,
    prior: list,
) -> None:
    """Drop a player line that never received a game tool result."""
    if _turn_has_game_tool(state, player_text):
        _scrub_history(state)
        return
    state.context = list(prior)


def _scrub_history(state: AgentState) -> None:
    """Keep player lines and game tool calls. Drop cues and spoken lines."""
    kept = []
    for message in state.context:
        role = getattr(message, "role", None)
        text = message.get_text_content() or ""
        if role == "user":
            if text.startswith((SPEAK_CUE, ACT_CUE)):
                continue
            kept.append(message)
            continue
        if _keep_game_tools(message):
            kept.append(message)
    state.context = kept
