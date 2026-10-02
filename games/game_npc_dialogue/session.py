# -*- coding: utf-8 -*-
"""One visit to Millhaven: talk to NPCs and keep the save on disk."""
from __future__ import annotations

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
from memory_store import remember_fact
from npc_config import TownConfig
from prompts import NPC_MEMORY_INSTRUCTIONS, build_system_prompt
from schema import NpcTurn
from speech import (
    ACT_CUE,
    SPEAK_CUE,
    guard_unproven_transfer,
    learn_player_name,
    normalize_emotion,
    polish_reply,
    reply_language,
)
from tools import GAME_TOOL_NAMES, build_npc_tools

_DELTA_MIN = -3
_DELTA_MAX = 3
_NOTABLE_DELTA = 2
_GAME_TOOLS = frozenset(GAME_TOOL_NAMES)


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
        actor = self._make_agent(npc_id, with_tools=True)
        await actor.reply(
            UserMsg(name=self.game.player_name, content=player_text),
        )
        state = self._agent_states[npc_id]
        if not _turn_has_game_tool(state, player_text):
            await actor.reply(UserMsg(name="director", content=_act_cue()))
        _drop_action_prose(state, player_text)
        speaker = self._make_agent(
            npc_id,
            with_tools=False,
            player_text=player_text,
            language=language,
        )
        message = await speaker.reply(
            UserMsg(
                name="director",
                content=_speak_cue(player_text, language),
            ),
            structured_schema=NpcTurn,
        )
        result = _apply_turn(
            self.game,
            npc_id,
            npc.name,
            message,
            player_text,
            self.memory_dir(npc_id),
            language,
            self.game.stock(npc_id),
            _new_items(before_items, self.game.inventory),
            self.game.gold < before_gold,
        )
        _scrub_history(state)
        return result

    def _make_agent(
        self,
        npc_id: str,
        *,
        with_tools: bool,
        player_text: str = "",
        language: str = "",
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
    emotion = normalize_emotion(str(payload.get("emotion", "")))
    reason = str(payload["affinity_reason"])
    reply = polish_reply(str(payload["reply"]), reason) or "(no reply)"
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
