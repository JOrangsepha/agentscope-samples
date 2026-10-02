# -*- coding: utf-8 -*-
"""Guards for the bugs found in the DashScope live transcript."""
from __future__ import annotations

import asyncio
from pathlib import Path

from game_state import GameState
from mock_model import ScriptedNpcModel
from npc_config import load_town_config
from prompts import build_system_prompt
from session import TownSession
from speech import (
    ACT_CUE,
    HONEST_EN,
    HONEST_ZH,
    SPEAK_CUE,
    guard_unproven_transfer,
    learn_player_name,
    normalize_emotion,
    polish_reply,
    reply_language,
)


def _session(
    tmp_path: Path,
    model: ScriptedNpcModel | None = None,
) -> tuple[TownSession, ScriptedNpcModel]:
    scripted = model or ScriptedNpcModel()
    return TownSession(load_town_config(), tmp_path, scripted), scripted


def test_speech_is_not_proof_and_only_the_giver_can_finish(
    tmp_path: Path,
) -> None:
    """Saying the hammer is found does not invent it or pay gold."""
    session, _model = _session(tmp_path)
    bram = asyncio.run(
        session.talk(
            "bram",
            "I found your forging hammer behind the inn. Here it is.",
        ),
    )
    assert "forging hammer" not in session.game.inventory
    assert session.game.gold == 12
    assert session.game.data["quests"]["lost_hammer"]["status"] == "available"
    assert "do not see the forging hammer" in bram.reply

    asyncio.run(session.talk("rowan", "I accept the lost hammer quest."))
    rowan = asyncio.run(
        session.talk("rowan", "I found the hammer. Here it is."),
    )
    quest = session.game.data["quests"]["lost_hammer"]
    assert quest["status"] == "accepted"
    assert session.game.gold == 12
    assert "forging hammer" not in session.game.inventory
    assert "do not see the forging hammer" in rowan.reply


def test_reward_is_paid_once_from_config(tmp_path: Path) -> None:
    """The configured reward is paid by code, and a second claim is a no-op."""
    config = load_town_config()
    state = GameState(tmp_path / "game_state.json", config)
    assert "Only Rowan" in state.complete_quest("bram", "lost_hammer")
    state.accept_quest("rowan", "lost_hammer")
    assert "not carrying" in state.complete_quest("rowan", "lost_hammer")
    assert state.gold == 12

    state.data["player"]["inventory"].append("forging hammer")
    paid = state.complete_quest("rowan", "lost_hammer")
    assert "gained 8 gold" in paid
    assert state.gold == 20
    assert "forging hammer" not in state.inventory
    again = state.complete_quest("rowan", "lost_hammer")
    assert "No further reward" in again
    assert state.gold == 20

    session, _model = _session(tmp_path / "talk")
    asyncio.run(session.talk("rowan", "I accept the lost hammer quest."))
    asyncio.run(session.talk("mira", "Please give me a forging hammer."))
    asyncio.run(session.talk("rowan", "I found the hammer. Here it is."))
    asyncio.run(
        session.talk("rowan", "Thank you for the reward for the hammer."),
    )
    assert session.game.gold == 20
    assert session.game.data["quests"]["lost_hammer"]["rewarded"] is True


def test_tools_are_limited_to_the_npc(tmp_path: Path) -> None:
    """Bram cannot edit quests or gold. Only Mira can charge."""
    session, model = _session(tmp_path)
    asyncio.run(session.talk("bram", "Hello."))
    asyncio.run(session.talk("rowan", "Hello."))
    asyncio.run(session.talk("mira", "Hello."))
    by_npc = {
        "Bram": _act_tools(model, "Bram"),
        "Rowan": _act_tools(model, "Rowan"),
        "Mira": _act_tools(model, "Mira"),
    }
    assert "accept_quest" not in by_npc["Bram"]
    assert "complete_quest" not in by_npc["Bram"]
    assert "charge_player" not in by_npc["Bram"]
    assert "adjust_gold" not in by_npc["Bram"]
    assert "update_quest" not in by_npc["Bram"]
    assert "accept_quest" in by_npc["Rowan"]
    assert "complete_quest" in by_npc["Rowan"]
    assert "charge_player" not in by_npc["Rowan"]
    assert "charge_player" in by_npc["Mira"]
    assert "accept_quest" not in by_npc["Mira"]
    refused_seal = session.game.give_item("rowan", "town seal")
    assert "cannot give" in refused_seal
    assert "town seal" not in session.game.inventory

    charged = session.game.charge(3, "a bed")
    assert "paid 3 gold" in charged
    assert session.game.gold == 9
    refused = session.game.charge(-5, "a refund")
    assert "positive" in refused
    assert session.game.gold == 9


def test_language_is_chosen_from_the_player_line() -> None:
    """The speak prompt names Chinese or English from this player line."""
    assert reply_language("你好米拉") == "Simplified Chinese"
    assert reply_language("Hello again.") == "English"
    config = load_town_config()
    npc = config.npc("mira")
    chinese = build_system_prompt(
        town_name=config.town_name,
        npc=npc,
        state_text="Player: Kestrel\nGold: 12",
        affinity=5,
        emotion="warm",
        speaking=True,
        player_text="你好米拉",
        language="Simplified Chinese",
    )
    assert "Reply in Simplified Chinese." in chinese
    assert "The player said: 你好米拉" in chinese
    assert "only for rudeness, threats, or a broken promise" in chinese
    assert "changes affinity by 0" in chinese
    assert "one or two sentences" in chinese
    assert "stage directions" in chinese
    assert "neutral, happy, annoyed, grateful, warm, suspicious" in chinese
    assert "integer from -3 to 3" in chinese
    assert "was never lost" in chinese
    assert "adjust_gold" not in chinese
    assert "update_quest" not in chinese
    english = build_system_prompt(
        town_name=config.town_name,
        npc=npc,
        state_text="Player: Kestrel",
        affinity=5,
        emotion="warm",
        speaking=True,
        player_text="Hello again.",
        language="English",
    )
    assert "Reply in English." in english
    assert "Reply in Simplified Chinese." not in english


def test_player_name_updates_from_an_introduction(tmp_path: Path) -> None:
    """Introductions set the name. Questions and disclaimers do not."""
    assert learn_player_name("I'm a traveling carpenter.") is None
    assert learn_player_name("My name is Kestrel.") == "Kestrel"
    assert learn_player_name("My name is Mary Ann.") == "Mary Ann"
    assert learn_player_name("My name is not important") is None
    assert learn_player_name("我叫Kestrel") == "Kestrel"
    assert learn_player_name("你还记得我叫什么吗？") is None

    session, model = _session(tmp_path)
    asyncio.run(session.talk("rowan", "Hello, my name is Kestrel."))
    assert session.game.player_name == "Kestrel"
    assert "Player: Kestrel" in model.calls[0]["system"]
    asyncio.run(session.talk("rowan", "你还记得我叫什么吗？"))
    assert session.game.player_name == "Kestrel"

    chinese, _model = _session(tmp_path / "cn")
    asyncio.run(chinese.talk("mira", "你好，我叫Kestrel。"))
    assert chinese.game.player_name == "Kestrel"
    stored = (chinese.memory_dir("mira") / "MEMORY.md").read_text(
        encoding="utf-8",
    )
    assert "The player's name is Kestrel." in stored

    named, _model = _session(tmp_path / "ann")
    asyncio.run(named.talk("bram", "My name is Mary Ann."))
    assert named.game.player_name == "Mary Ann"
    refused, _model = _session(tmp_path / "not")
    asyncio.run(refused.talk("bram", "My name is not important"))
    assert refused.game.player_name == "Traveler"


def test_a_strong_affinity_change_is_remembered(tmp_path: Path) -> None:
    """An insult is written to memory and shown on the next visit."""
    config = load_town_config()
    first_model = ScriptedNpcModel()
    first = TownSession(config, tmp_path, first_model)
    asyncio.run(first.talk("bram", "You are a stupid thief."))
    stored = (first.memory_dir("bram") / "MEMORY.md").read_text(
        encoding="utf-8",
    )
    assert "stupid thief" in stored
    assert "Last strong impression" not in first_model.calls[0]["system"]

    second_model = ScriptedNpcModel()
    second = TownSession(config, tmp_path, second_model)
    asyncio.run(second.talk("bram", "Hello."))
    system = second_model.calls[0]["system"]
    assert "stupid thief" in system
    assert "Last strong impression" in system


def test_spoken_line_is_a_later_call_without_game_tools(
    tmp_path: Path,
) -> None:
    """Structured output is requested only after tool results exist."""
    session, model = _session(tmp_path)
    asyncio.run(session.talk("bram", "Please give me a horseshoe."))
    act = [call for call in model.calls if call["phase"] == "act"]
    speak = [call for call in model.calls if call["phase"] == "speak"]
    assert act and speak
    assert model.calls.index(speak[0]) > model.calls.index(act[0])
    assert speak[0]["saw_tool_result"] == "yes"
    speak_tools = speak[0]["tools"].split(",")
    assert speak_tools == ["GenerateStructuredOutput"]
    assert "give_item" in act[0]["tools"].split(",")


def test_polish_reply_drops_stage_directions_and_leaked_fields() -> None:
    """Asterisks and leaked fields go. Short sentences stay if they fit."""
    spoken = polish_reply(
        "*wipes a mug* The stew is hot. Sit down. The third should go.",
    )
    assert spoken == "The stew is hot. Sit down. The third should go."
    assert "*" not in spoken
    leaked = polish_reply(
        "Good evening.\nneutral\n0\nThe player was polite.",
        "The player was polite.",
    )
    assert leaked == "Good evening."
    chinese = polish_reply("没有。锤子还在丢着。你要是见着了，就送回来。")
    assert chinese == "没有。锤子还在丢着。你要是见着了，就送回来。"
    assert "。 " not in chinese
    hammer = polish_reply(
        "Ah—yes! Bram's hammer. It was behind the flour sacks.",
    )
    assert "Bram's hammer" in hammer
    assert "flour sacks" in hammer
    quoted = polish_reply('She said "Go." He stayed. A third sentence.')
    assert quoted == 'She said "Go." He stayed. A third sentence.'
    opening = "This opening sentence is long enough to stand alone."
    trimmed = polish_reply(f"{opening} " + ("Tail. " * 50))
    assert trimmed.startswith(opening)
    assert "Tail." in trimmed
    assert len(trimmed) <= 240
    assert trimmed.count("Tail.") < 50


def test_text_only_action_cannot_give_or_charge(tmp_path: Path) -> None:
    """Text from the action phase cannot give an item or take gold."""
    gift_model = ScriptedNpcModel(act_text="Here you go! Take the horseshoe.")
    gift_session = TownSession(
        load_town_config(),
        tmp_path / "gift",
        gift_model,
    )
    gift = asyncio.run(
        gift_session.talk("bram", "Please give me a horseshoe."),
    )
    assert gift_model.calls[0]["tool_choice"] != "required"
    assert gift_model.calls[0]["phase"] == "act"
    gift_acts = [call for call in gift_model.calls if call["phase"] == "act"]
    assert len(gift_acts) == 2
    assert gift_acts[1]["user"] == "Please give me a horseshoe."
    assert "horseshoe" not in gift_session.game.inventory
    assert gift.reply == HONEST_EN
    assert "take the horseshoe" not in gift.reply.lower()

    charge_model = ScriptedNpcModel(
        act_text="That'll be three gold for a warm bed.",
    )
    charge_session = TownSession(
        load_town_config(),
        tmp_path / "bed",
        charge_model,
    )
    charge = asyncio.run(
        charge_session.talk("mira", "I'd like a bed for the night."),
    )
    assert charge_session.game.gold == 12
    assert charge.reply == HONEST_EN
    history = _history_text(charge_session, "mira")
    assert SPEAK_CUE not in history
    assert ACT_CUE not in history
    assert "That'll be three gold" not in history
    assert all(
        call["tool_choice"] != "required" for call in charge_model.calls
    )


def test_speak_prompt_switches_language_with_the_player(
    tmp_path: Path,
) -> None:
    """The speak prompt names the language of this line, then the next one."""
    session, model = _session(tmp_path)
    asyncio.run(session.talk("mira", "你好米拉"))
    asyncio.run(session.talk("mira", "Hello again."))
    speak = [call for call in model.calls if call["phase"] == "speak"]
    assert "Reply in Simplified Chinese." in speak[0]["system"]
    assert "The player said: 你好米拉" in speak[0]["system"]
    assert "Reply in English." in speak[1]["system"]
    assert SPEAK_CUE not in _history_text(session, "mira")
    assert (
        guard_unproven_transfer(
            "给你！",
            ["brown loaf"],
            [],
            False,
            "Simplified Chinese",
        )
        == HONEST_ZH
    )


def test_history_keeps_only_game_tool_calls(tmp_path: Path) -> None:
    """Spoken lines and GenerateStructuredOutput do not stay in context."""
    session, model = _session(tmp_path)
    first = asyncio.run(session.talk("bram", "Please give me a horseshoe."))
    asyncio.run(session.talk("bram", "Hello."))
    names = _history_tool_names(session, "bram")
    history = _history_text(session, "bram")
    assert "give_item" in names
    assert "GenerateStructuredOutput" not in names
    assert first.reply not in history
    assert "Take the horseshoe" not in history
    assert SPEAK_CUE not in history
    assert ACT_CUE not in history
    assert len([call for call in model.calls if call["phase"] == "act"]) == 2
    assert len([call for call in model.calls if call["phase"] == "speak"]) == 2


def test_out_of_range_emotion_is_mapped_once(tmp_path: Path) -> None:
    """Unknown emotions and large deltas do not retry structured output."""
    assert normalize_emotion("irritated") == "annoyed"
    assert normalize_emotion("calm") == "neutral"
    assert normalize_emotion("not-an-emotion") == "neutral"

    rude_model = ScriptedNpcModel(forced_emotion="irritated", forced_delta=-15)
    rude = TownSession(load_town_config(), tmp_path / "rude", rude_model)
    insult = asyncio.run(rude.talk("bram", "You are a stupid thief."))
    assert insult.emotion == "annoyed"
    assert insult.affinity_delta == -3
    assert rude.game.affinity("bram") == -3
    assert (
        len([call for call in rude_model.calls if call["phase"] == "speak"])
        == 1
    )

    calm_model = ScriptedNpcModel(forced_emotion="calm", forced_delta=9)
    calm = TownSession(load_town_config(), tmp_path / "calm", calm_model)
    hello = asyncio.run(calm.talk("mira", "Hello."))
    assert hello.emotion == "neutral"
    assert hello.affinity_delta == 3
    assert (
        len([call for call in calm_model.calls if call["phase"] == "speak"])
        == 1
    )


def _history_text(session: TownSession, npc_id: str) -> str:
    state = session._agent_states[npc_id]  # pylint: disable=protected-access
    parts = []
    for message in state.context:
        parts.append(message.get_text_content() or "")
    return "\n".join(parts)


def _history_tool_names(session: TownSession, npc_id: str) -> list[str]:
    state = session._agent_states[npc_id]  # pylint: disable=protected-access
    names = []
    for message in state.context:
        content = getattr(message, "content", None)
        if not isinstance(content, list):
            continue
        for block in content:
            if getattr(block, "type", None) in {"tool_call", "tool_result"}:
                names.append(block.name)
    return names


def _act_tools(model: ScriptedNpcModel, npc_name: str) -> set[str]:
    for call in model.calls:
        if call["phase"] != "act":
            continue
        if f"You are {npc_name}," not in call["system"]:
            continue
        return set(call["tools"].split(","))
    raise AssertionError(f"no act call for {npc_name}")
