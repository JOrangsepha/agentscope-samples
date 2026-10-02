# -*- coding: utf-8 -*-
"""One visit to Millhaven: talk to NPCs and keep the save on disk."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from agentscope.agent import Agent, InjectionConfig
from agentscope.message import UserMsg
from agentscope.middleware import AgenticMemoryMiddleware
from agentscope.model import ChatModelBase
from agentscope.permission import PermissionMode
from agentscope.state import AgentState
from agentscope.tool import Toolkit

from game_state import GameState
from npc_config import TownConfig
from prompts import NPC_MEMORY_INSTRUCTIONS, build_system_prompt
from schema import NpcTurn
from tools import build_npc_tools

_DELTA_MIN = -3
_DELTA_MAX = 3


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
        """Send one player line to an NPC and persist the relationship."""
        npc = self.config.npc(npc_id)
        state = self._agent_states.setdefault(npc_id, AgentState())
        state.permission_context.mode = PermissionMode.BYPASS
        agent = Agent(
            name=npc.name,
            system_prompt=build_system_prompt(
                town_name=self.config.town_name,
                npc=npc,
                state_text=self.game.describe(),
                affinity=self.game.affinity(npc_id),
                emotion=self.game.emotion(npc_id),
            ),
            model=self.model,
            toolkit=Toolkit(
                tools=build_npc_tools(
                    self.game,
                    npc_id,
                    self.memory_dir(npc_id),
                ),
            ),
            middlewares=[_memory_middleware(self.memory_dir(npc_id).parent)],
            state=state,
            injection_config=InjectionConfig(inject_runtime_state=False),
        )
        message = await agent.reply(
            UserMsg(name=self.game.player_name, content=player_text),
            structured_schema=NpcTurn,
        )
        return _apply_turn(self.game, npc_id, npc.name, message)


def _memory_middleware(workdir: Path) -> AgenticMemoryMiddleware:
    """File-backed long-term memory. No vector store and no extra LLM call."""
    return AgenticMemoryMiddleware(
        workdir=str(workdir),
        parameters=AgenticMemoryMiddleware.Parameters(
            retrieval_async=False,
            memory_instructions=NPC_MEMORY_INSTRUCTIONS,
        ),
    )


def _apply_turn(
    game: GameState,
    npc_id: str,
    npc_name: str,
    message,
) -> TurnResult:
    """Read structured output and write emotion plus affinity."""
    payload = message.structured_output or {}
    if not payload:
        text = message.get_text_content() or "(no reply)"
        return TurnResult(
            npc_id=npc_id,
            npc_name=npc_name,
            reply=text,
            emotion=game.emotion(npc_id),
            affinity_delta=0,
            affinity=game.affinity(npc_id),
            affinity_reason="The model did not return structured output.",
        )
    delta = int(payload["affinity_delta"])
    delta = max(_DELTA_MIN, min(_DELTA_MAX, delta))
    emotion = str(payload["emotion"])
    affinity = game.apply_affinity(npc_id, delta)
    game.set_emotion(npc_id, emotion)
    return TurnResult(
        npc_id=npc_id,
        npc_name=npc_name,
        reply=str(payload["reply"]),
        emotion=emotion,
        affinity_delta=delta,
        affinity=affinity,
        affinity_reason=str(payload["affinity_reason"]),
    )
