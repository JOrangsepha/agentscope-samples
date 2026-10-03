# -*- coding: utf-8 -*-
"""One visit to Millhaven: talk to NPCs and keep the save on disk."""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path

from agentscope.agent import Agent, InjectionConfig, ReActConfig
from agentscope.message import (
    AssistantMsg,
    TextBlock,
    ToolCallBlock,
    ToolResultBlock,
    ToolResultState,
    UserMsg,
)
from agentscope.middleware import AgenticMemoryMiddleware, MiddlewareBase
from agentscope.model import ChatModelBase, ChatResponse
from agentscope.permission import PermissionMode
from agentscope.state import AgentState
from agentscope.tool import Toolkit

from game_state import GameState
from gossip import (
    asks_for_news,
    insult_retry_line,
    latest_insult_quote,
    narrate_wait,
    news_to_repeat,
    prompt_block,
    record_public_events,
    reply_voices_insult,
)
from memory_store import asks_for_recall, recall_note, remember_fact
from npc_config import TownConfig
from prompts import (
    NPC_MEMORY_INSTRUCTIONS,
    build_system_prompt,
    language_banner,
)
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
    plausible_rudeness,
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
            if _leaves_state_alone(player_text):
                _stamp_no_action(state, npc_name)
            else:
                await actor.reply(
                    UserMsg(name="director", content=_act_cue()),
                )
        _drop_action_prose(state, player_text)
        paid = _payment(state, player_text)
        granted = _new_items(before_items, self.game.inventory)
        news_note = ""
        insult_quote = ""
        if asks_for_news(player_text):
            news_note = news_to_repeat(
                self.save_dir,
                language,
                npc_name,
            )
            if news_note:
                insult_quote = latest_insult_quote(self.save_dir)
        gold_changed = self.game.gold != before_gold
        items_changed = list(self.game.inventory) != list(before_items)
        ledger_note = ""
        if gold_changed or items_changed or _asks_about_ledger(player_text):
            ledger_note = _ledger_note(
                self.game.gold,
                list(self.game.inventory),
                language,
                gold_changed=gold_changed,
                items_changed=items_changed,
            )
        remembered = ""
        if asks_for_recall(player_text):
            remembered = recall_note(
                self.memory_dir(npc_id),
                self.save_dir,
                npc_id,
                language,
            )
        speaker = self._make_agent(
            npc_id,
            with_tools=False,
            player_text=player_text,
            language=language,
            paid_note=_paid_note(paid),
            granted_note=_granted_note(granted, language),
            news_note=news_note,
            ledger_note=ledger_note,
            remembered=remembered,
        )
        cue = _speak_cue(
            player_text,
            language,
            news_note,
            ledger_note,
            remembered,
        )
        message = await _speak_once(speaker, cue)
        if _needs_chinese_retry(language, message):
            message = await _speak_once(
                speaker,
                cue
                + "\nThe previous reply was not Simplified Chinese. "
                + "Reply in Simplified Chinese only.",
            )
        if insult_quote and not reply_voices_insult(
            _structured_reply(message),
            insult_quote,
        ):
            message = await _speak_once(
                speaker,
                f"{cue}\n{insult_retry_line(language, insult_quote)}",
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
            granted,
            self.game.gold < before_gold,
            _quest_failed(state, player_text),
            paid,
            _public_quest_event(state, player_text),
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
        granted_note: str = "",
        news_note: str = "",
        ledger_note: str = "",
        remembered: str = "",
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
            middlewares.append(StructuredTextMiddleware())
            limit = 4
        return Agent(
            name=self.config.npc(npc_id).name,
            system_prompt=self._prompt(
                npc_id,
                speaking=not with_tools,
                player_text=player_text,
                language=language,
                paid_note=paid_note,
                granted_note=granted_note,
                news_note=news_note,
                ledger_note=ledger_note,
                remembered=remembered,
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
        granted_note: str = "",
        news_note: str = "",
        ledger_note: str = "",
        remembered: str = "",
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
            granted_note=granted_note,
            news_note=news_note,
            ledger_note=ledger_note,
            recall_note=remembered,
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


class StructuredTextMiddleware(MiddlewareBase):
    """Turn a written GenerateStructuredOutput(...) into a real tool call."""

    async def on_model_call(self, agent, input_kwargs, next_handler):
        """Parse a text-shaped tool call so the agent does not ask again."""
        del agent
        response = await next_handler(**input_kwargs)
        return _promote_structured_text(response)


def _leaves_state_alone(player_text: str) -> bool:
    """News and recall questions do not change gold, items, or quests."""
    return asks_for_news(player_text) or asks_for_recall(player_text)


def _stamp_no_action(state: AgentState, npc_name: str) -> None:
    """Record no_action without another model call."""
    block = ToolResultBlock(
        id=f"no-action-{len(state.context)}",
        name="no_action",
        output="No game state changed (Nothing to change.).",
        state=ToolResultState.SUCCESS,
    )
    state.context.append(AssistantMsg(name=npc_name, content=[block]))


async def _speak_once(speaker, cue: str):
    """One structured speak call."""
    return await speaker.reply(
        UserMsg(name="director", content=cue),
        structured_schema=NpcTurn,
    )


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


_ITEM_ZH = {
    "forging hammer": "锻造锤",
    "horseshoe": "马掌",
    "iron nail": "铁钉",
    "brown loaf": "黑面包",
    "worn cloak": "旧斗篷",
}


def _granted_note(items: list[str], language: str) -> str:
    """Tell the speaker to mention this handoff in their own voice."""
    if not items:
        return ""
    if language == "Simplified Chinese":
        names = _handed_names_zh(items)
        return f"你现在把{names}交给玩家。用你自己的口气说出来。" "不要说他们本来就有。"
    names = _handed_names_en(items)
    return (
        f"You are handing the player {names} now. "
        "Say so in your own voice. Do not say they already had it."
    )


def _handed_names_en(items: list[str]) -> str:
    shown = []
    for item in items:
        if item == "forging hammer":
            shown.append("Bram's lost forging hammer")
        else:
            shown.append(item)
    return ", ".join(shown)


def _handed_names_zh(items: list[str]) -> str:
    shown = []
    for item in items:
        if item == "forging hammer":
            shown.append("Bram 丢失的锻造锤")
        else:
            shown.append(_ITEM_ZH.get(item, item))
    return "、".join(shown)


_LEDGER_CUES = (
    "gold",
    "coin",
    "inventory",
    "my pack",
    "in my pack",
    "what do i have",
    "what am i carrying",
    "what i'm carrying",
    "金币",
    "多少钱",
    "背包",
    "身上有",
    "带着什么",
    "有什么东西",
)


def _asks_about_ledger(text: str) -> bool:
    """True when this line asks about coins or what the player carries."""
    lowered = text.lower().replace("’", "'")
    return any(cue in lowered for cue in _LEDGER_CUES)


def _ledger_note(
    gold: int,
    inventory: list[str],
    language: str,
    *,
    gold_changed: bool,
    items_changed: bool,
) -> str:
    """Coins and pack as a reference, not a line to recite."""
    items = ", ".join(inventory) or "(empty)"
    changed = _ledger_change(language, gold_changed, items_changed)
    if language == "Simplified Chinese":
        return (
            f"仅供对照，不要主动念出来：金币 {gold}；背包 {items}。{changed}"
            "只有玩家问到金币，或本轮金币有变化时，才提金币。"
            "只有玩家问到某件物品，或本轮背包有变化时，才提那件物品。"
            "不要主动报背包。不要另编数字。"
        )
    return (
        f"Reference only, do not recite it: gold {gold}; "
        f"inventory {items}. {changed}"
        "Mention gold only if the player asked about gold or this "
        "turn changed it. Mention an item only if the player asked "
        "about that item or this turn changed the inventory. "
        "Never list the inventory unprompted. "
        "Do not invent a different number."
    )


def _ledger_change(
    language: str,
    gold_changed: bool,
    items_changed: bool,
) -> str:
    """Say whether this turn already moved coins or the pack."""
    if language == "Simplified Chinese":
        return _ledger_change_zh(gold_changed, items_changed)
    return _ledger_change_en(gold_changed, items_changed)


def _ledger_change_zh(gold_changed: bool, items_changed: bool) -> str:
    if gold_changed and items_changed:
        return "本轮金币和背包都有变化。"
    if gold_changed:
        return "本轮金币有变化。"
    if items_changed:
        return "本轮背包有变化。"
    return "本轮金币和背包都没有变化。"


def _ledger_change_en(gold_changed: bool, items_changed: bool) -> str:
    if gold_changed and items_changed:
        return "This turn changed gold and the inventory. "
    if gold_changed:
        return "This turn changed gold. "
    if items_changed:
        return "This turn changed the inventory. "
    return "This turn changed neither gold nor inventory. "


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


def _speak_cue(
    player_text: str,
    language: str,
    news_note: str = "",
    ledger_note: str = "",
    remembered: str = "",
) -> str:
    """Turn-local instruction. It is removed from history after the reply."""
    extra = ""
    if ledger_note:
        extra += f"\n{ledger_note}"
    if news_note:
        extra += f"\n{news_note}"
    if remembered:
        extra += f"\n{remembered}"
    return (
        f"{SPEAK_CUE}\n"
        f"{language_banner(language, items=False)}\n"
        f"The player said: {player_text}"
        f"{extra}"
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
    quest_event: str,
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
    if delta <= -2 and not plausible_rudeness(player_text):
        delta = 0
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
        paid = None
        reply = REFUND_ZH if language == "Simplified Chinese" else REFUND_EN
    reply = guard_unproven_transfer(
        reply,
        stock,
        granted,
        charged,
        language,
    )
    helped = _helped(granted, paid, quest_event)
    if helped and delta < 1:
        delta = 1
    elif not helped and delta > 0:
        delta = 0
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


def _helped(
    granted: list[str],
    paid: tuple[int, str] | None,
    quest_event: str,
) -> bool:
    """True when this turn gave an item, took coins, or moved the quest."""
    if granted or paid is not None:
        return True
    return quest_event in {"accepted", "completed"}


def _promote_structured_text(response: ChatResponse) -> ChatResponse:
    """Replace a written structured call with a tool call block."""
    content = getattr(response, "content", None) or []
    if not isinstance(content, list):
        return response
    if any(
        getattr(block, "name", None) == "GenerateStructuredOutput"
        for block in content
    ):
        return response
    text = "\n".join(
        getattr(block, "text", "")
        for block in content
        if getattr(block, "text", "")
    )
    parsed = _parse_structured_call(text)
    if parsed is None:
        return response
    response.content = [
        ToolCallBlock(
            id="structured-from-text",
            name="GenerateStructuredOutput",
            input=json.dumps(parsed),
        ),
    ]
    return response


def _parse_structured_call(text: str) -> dict | None:
    """Read reply, emotion, delta, and reason from a text-shaped call."""
    marker = "GenerateStructuredOutput"
    start = text.find(marker)
    if start < 0:
        return _parse_plain_structured(text)
    body = text[start + len(marker) :].lstrip()
    if not body.startswith("("):
        return None
    body = body[1:]
    if body.lstrip().startswith("{"):
        return _parse_structured_json(body)
    return _parse_structured_kwargs(body)


def _parse_structured_json(body: str) -> dict | None:
    raw = body.lstrip()
    end = _matching_brace(raw)
    if end is None:
        return None
    try:
        payload = json.loads(raw[: end + 1])
    except json.JSONDecodeError:
        return None
    if not isinstance(payload, dict) or "reply" not in payload:
        return None
    return _normalize_structured(payload)


def _matching_brace(text: str) -> int | None:
    depth = 0
    quote = ""
    escaped = False
    for index, char in enumerate(text):
        if quote:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == quote:
                quote = ""
            continue
        if char in "\"'":
            quote = char
            continue
        if char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return index
    return None


def _parse_structured_kwargs(body: str) -> dict | None:
    fields: dict = {}
    index = 0
    while index < len(body):
        while index < len(body) and body[index] in " \n\t,":
            index += 1
        if index >= len(body) or body[index] == ")":
            break
        match = re.match(r"([A-Za-z_][A-Za-z0-9_]*)\s*=\s*", body[index:])
        if match is None:
            return None
        key = match.group(1)
        index += match.end()
        if index < len(body) and body[index] in "\"'":
            quote = body[index]
            index += 1
            chars: list[str] = []
            while index < len(body):
                if body[index] == "\\" and index + 1 < len(body):
                    chars.append(body[index + 1])
                    index += 2
                    continue
                if body[index] == quote:
                    index += 1
                    break
                chars.append(body[index])
                index += 1
            fields[key] = "".join(chars)
            continue
        number = re.match(r"-?\d+", body[index:])
        if number is None:
            return None
        fields[key] = int(number.group(0))
        index += number.end()
    if "reply" not in fields:
        return None
    return _normalize_structured(fields)


_EMOTION_WORD = "neutral|happy|annoyed|grateful|warm|suspicious"
_PLAIN_EMOTION = re.compile(_EMOTION_WORD, re.IGNORECASE)
_PLAIN_DELTA = re.compile(
    r"[\s,，:：]*"
    r"(?:affinity[\s_]*delta|affinity[\s_]*change|delta)?"
    r"[\s,，:：]*\(?"
    r"(-?\d+)",
    re.IGNORECASE,
)


def _parse_plain_structured(text: str) -> dict | None:
    """Read reply, emotion, delta, and reason from a trailing label."""
    cleaned = text.strip()
    if len(cleaned) < 8:
        return None
    start = max(0, len(cleaned) - 280)
    tail = cleaned[start:]
    for match in reversed(list(_PLAIN_EMOTION.finditer(tail))):
        number = _PLAIN_DELTA.match(tail[match.end() :])
        if number is None:
            continue
        reply = cleaned[: start + match.start()].strip()
        reply = re.sub(
            r"\s*emotion\s*:?\s*$",
            "",
            reply,
            flags=re.IGNORECASE,
        ).strip()
        if len(reply) < 2:
            continue
        reason = tail[match.end() + number.end() :].strip()
        reason = reason.strip("\"'").strip("()").strip()
        reason = reason.lstrip(",，:：").strip()
        return _normalize_structured(
            {
                "reply": reply,
                "emotion": match.group(0).lower(),
                "affinity_delta": int(number.group(1)),
                "affinity_reason": reason,
            },
        )
    return None


def _normalize_structured(payload: dict) -> dict:
    try:
        delta = int(payload.get("affinity_delta", 0))
    except (TypeError, ValueError):
        delta = 0
    return {
        "reply": str(payload.get("reply", "")),
        "emotion": str(payload.get("emotion", "neutral")),
        "affinity_delta": delta,
        "affinity_reason": str(payload.get("affinity_reason", "")),
    }


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
