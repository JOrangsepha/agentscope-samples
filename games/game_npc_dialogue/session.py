# -*- coding: utf-8 -*-
"""One visit to Millhaven: talk to NPCs and keep the save on disk."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from agentscope.agent import Agent, InjectionConfig, ReActConfig
from agentscope.message import AssistantMsg, UserMsg
from agentscope.middleware import AgenticMemoryMiddleware, MiddlewareBase
from agentscope.model import ChatModelBase
from agentscope.permission import PermissionMode
from agentscope.state import AgentState
from agentscope.tool import ToolChoice, Toolkit

from game_state import GameState
from memory_store import remember_fact
from npc_config import TownConfig
from prompts import NPC_MEMORY_INSTRUCTIONS, build_system_prompt
from schema import NpcTurn
from speech import (
    SPEAK_CUE,
    guard_unproven_transfer,
    learn_player_name,
    polish_reply,
    reply_language,
)
from tools import build_npc_tools

_DELTA_MIN = -3
_DELTA_MAX = 3
_NOTABLE_DELTA = 2


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
        _keep_turn_local(state, player_text, result.reply, npc.name)
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
            middlewares.append(RequireToolMiddleware())
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


class RequireToolMiddleware(MiddlewareBase):
    """Ask for a tool until this action turn has produced one."""

    async def on_model_call(self, agent, input_kwargs, next_handler):
        """Set tool_choice to required before any tool result exists."""
        del agent
        updated = dict(input_kwargs)
        choice = updated.get("tool_choice")
        mode = getattr(choice, "mode", None)
        pending = _awaiting_action(updated.get("messages") or [])
        if updated.get("tools") and mode in (None, "auto") and pending:
            updated["tool_choice"] = ToolChoice(mode="required")
        return await next_handler(**updated)


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
    delta = int(payload["affinity_delta"])
    delta = max(_DELTA_MIN, min(_DELTA_MAX, delta))
    emotion = str(payload["emotion"])
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


def _awaiting_action(messages: list) -> bool:
    for message in reversed(messages):
        role = getattr(message, "role", None)
        if role == "user":
            return True
        getter = getattr(message, "get_content_blocks", None)
        if getter and getter("tool_result"):
            return False
    return True


def _player_index(context: list, player_text: str) -> int | None:
    found = None
    for index, message in enumerate(context):
        if getattr(message, "role", None) != "user":
            continue
        if (message.get_text_content() or "") == player_text:
            found = index
    return found


def _tool_only(message) -> bool:
    """Keep tool calls and results. Drop the message when none remain."""
    if getattr(message, "role", None) != "assistant":
        return False
    kept = [
        block
        for block in message.content
        if getattr(block, "type", None) in {"tool_call", "tool_result"}
    ]
    if not kept:
        return False
    message.content = kept
    return True


def _drop_action_prose(state: AgentState, player_text: str) -> None:
    """Remove spoken text from the action turn before the reply is written."""
    start = _player_index(state.context, player_text)
    if start is None:
        return
    kept = []
    for message in state.context[start + 1 :]:
        if _tool_only(message):
            kept.append(message)
    state.context = list(state.context[: start + 1]) + kept


def _keep_turn_local(
    state: AgentState,
    player_text: str,
    reply: str,
    npc_name: str,
) -> None:
    """Drop the speak cue and store the spoken line, not action prose."""
    start = _player_index(state.context, player_text)
    if start is None:
        return
    kept = []
    for message in state.context[start + 1 :]:
        text = message.get_text_content() or ""
        if message.role == "user" and text.startswith(SPEAK_CUE):
            continue
        if _tool_only(message):
            kept.append(message)
    if reply:
        kept.append(AssistantMsg(name=npc_name, content=reply))
    state.context = list(state.context[: start + 1]) + kept
