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
from speech import learn_player_name, polish_reply


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

    charged = session.game.charge(3, "a bed")
    assert "paid 3 gold" in charged
    assert session.game.gold == 9
    refused = session.game.charge(-5, "a refund")
    assert "positive" in refused
    assert session.game.gold == 9


def test_language_rule_and_affinity_rubric_are_in_the_prompt() -> None:
    """The prompt tells the model which language to use, and when to deduct."""
    config = load_town_config()
    npc = config.npc("bram")
    prompt = build_system_prompt(
        town_name=config.town_name,
        npc=npc,
        state_text="Player: Traveler\nGold: 12",
        affinity=0,
        emotion="neutral",
    )
    assert "Reply in the language the player just used." in prompt
    assert "If that message is Chinese, reply in Chinese." in prompt
    assert "only for rudeness, threats, or a broken promise" in prompt
    assert "changes affinity by 0" in prompt
    assert "one or two sentences" in prompt
    assert "stage directions" in prompt
    assert "adjust_gold" not in prompt
    assert "update_quest" not in prompt


def test_player_name_updates_from_an_introduction(tmp_path: Path) -> None:
    """English and Chinese introductions replace the default name."""
    assert learn_player_name("I'm a traveling carpenter.") is None
    assert learn_player_name("My name is Kestrel.") == "Kestrel"
    assert learn_player_name("我叫Kestrel") == "Kestrel"

    session, model = _session(tmp_path)
    asyncio.run(
        session.talk("rowan", "Hello, my name is Kestrel."),
    )
    assert session.game.player_name == "Kestrel"
    assert "Player: Kestrel" in model.calls[0]["system"]

    chinese, _model = _session(tmp_path / "cn")
    asyncio.run(chinese.talk("mira", "你好，我叫Kestrel。"))
    assert chinese.game.player_name == "Kestrel"


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
    """Asterisks, bare emotion lines, and the reason do not reach the CLI."""
    spoken = polish_reply(
        "*wipes a mug* The stew is hot. Sit down. The third should go.",
    )
    assert spoken == "The stew is hot. Sit down."
    assert "*" not in spoken
    leaked = polish_reply(
        "Good evening.\nneutral\n0\nThe player was polite.",
        "The player was polite.",
    )
    assert leaked == "Good evening."


def _act_tools(model: ScriptedNpcModel, npc_name: str) -> set[str]:
    for call in model.calls:
        if call["phase"] != "act":
            continue
        if f"You are {npc_name}," not in call["system"]:
            continue
        return set(call["tools"].split(","))
    raise AssertionError(f"no act call for {npc_name}")
