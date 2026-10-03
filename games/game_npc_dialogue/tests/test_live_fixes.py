# -*- coding: utf-8 -*-
"""Guards for the bugs found in the DashScope live transcript."""
from __future__ import annotations

import asyncio
from pathlib import Path

from game_state import GameState
from mock_model import ScriptedNpcModel
from npc_config import load_town_config
from prompts import build_system_prompt, hammer_fact
from agentscope.message import TextBlock, ToolCallBlock
from agentscope.model import ChatResponse
from session import (  # pylint: disable=protected-access
    TownSession,
    _parse_structured_call,
    _promote_structured_text,
)
from speech import (
    ACT_CUE,
    CHARGED_EN,
    CHARGED_ZH,
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

    charged = session.game.charge("mira", 3, "a bed")
    assert "paid 3 gold" in charged
    assert session.game.gold == 9
    meal = session.game.charge("mira", 2, "hot meal")
    assert "cannot charge" in meal
    assert session.game.gold == 9
    refused = session.game.charge("mira", -5, "a refund")
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
    assert "只用简体中文" in chinese
    assert "锻造锤" in chinese
    assert "姓名不要音译" in chinese
    assert "Rowan the elder" in chinese
    assert "The player said: 你好米拉" in chinese
    assert "only for rudeness, threats, or a broken promise" in chinese
    assert "changes affinity by 0" in chinese
    assert "one or two sentences" in chinese
    assert "stage directions" in chinese
    assert "neutral, happy, annoyed, grateful, warm, suspicious" in chinese
    assert "integer from -3 to 3" in chinese
    assert "was never lost" in chinese
    assert "positive affinity_delta" in chinese
    assert "GenerateStructuredOutput directly" in chinese
    assert "plain text" in chinese
    action = build_system_prompt(
        town_name=config.town_name,
        npc=npc,
        state_text="Player: Kestrel",
        affinity=5,
        emotion="warm",
    )
    assert "-> no_action" not in action
    assert "no_action tool" in action
    assert "repeat reward" in action
    assert "begging" in action
    assert "Do not write the tool name as a sentence." in action
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
    asked = "my name is Kestrel. Could you give me a horseshoe?"
    assert learn_player_name(asked) == "Kestrel"
    nice = "My name is Kestrel. Nice to meet you."
    assert learn_player_name(nice) == "Kestrel"
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
    assert hello.affinity_delta == 0
    assert (
        len([call for call in calm_model.calls if call["phase"] == "speak"])
        == 1
    )
    gift = asyncio.run(calm.talk("bram", "Please give me a horseshoe."))
    assert gift.affinity_delta == 3


def test_a_successful_charge_is_not_rewritten_as_a_gift() -> None:
    """'Here you go' after a charge is the service, not a false gift."""
    bed = "Here you go, Kestrel — a warm bed and quiet night."
    assert (
        guard_unproven_transfer(bed, ["brown loaf"], [], True, "English")
        == bed
    )
    chinese = "给你，今晚住下吧。"
    assert (
        guard_unproven_transfer(
            chinese,
            ["brown loaf"],
            [],
            True,
            "Simplified Chinese",
        )
        == chinese
    )
    claimed = guard_unproven_transfer(
        "Here you go! Take the horseshoe.",
        ["horseshoe"],
        [],
        True,
        "English",
    )
    assert claimed == CHARGED_EN
    assert "unchanged" not in claimed.lower()
    taken = guard_unproven_transfer(
        "拿去。",
        ["brown loaf"],
        [],
        True,
        "Simplified Chinese",
    )
    assert taken == CHARGED_ZH
    assert "没有变化" not in taken
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


def test_a_failed_quest_call_cannot_raise_affinity(tmp_path: Path) -> None:
    """already/cannot from a quest tool cannot increase affinity."""
    session, _model = _session(tmp_path)
    asyncio.run(session.talk("rowan", "I accept the lost hammer quest."))
    asyncio.run(session.talk("mira", "Please give me a forging hammer."))
    asyncio.run(session.talk("rowan", "I found the hammer. Here it is."))
    assert session.game.gold == 20
    before = session.game.affinity("rowan")
    session.model = ScriptedNpcModel(forced_emotion="grateful", forced_delta=2)
    again = asyncio.run(
        session.talk("rowan", "I found the hammer. Here it is."),
    )
    assert again.affinity_delta <= 0
    assert session.game.affinity("rowan") == before
    memory = (session.memory_dir("rowan") / "MEMORY.md").read_text(
        encoding="utf-8",
    )
    assert memory.count("Affinity +") == 1

    refused, _model = _session(tmp_path / "missing")
    asyncio.run(refused.talk("rowan", "I accept the lost hammer quest."))
    refused.model = ScriptedNpcModel(forced_emotion="grateful", forced_delta=3)
    blocked = asyncio.run(
        refused.talk("rowan", "I found the hammer. Here it is."),
    )
    assert refused.game.gold == 12
    assert blocked.affinity_delta <= 0
    assert refused.game.affinity("rowan") <= 1


def test_hammer_fact_follows_the_quest(tmp_path: Path) -> None:
    """The fact follows the quest and where the hammer is."""
    assert "still lost" in hammer_fact("available")
    assert "Mira is holding" in hammer_fact("accepted")
    assert "still missing" not in hammer_fact("accepted")
    carried = hammer_fact("accepted", "carried")
    assert "carrying the forging hammer" in carried
    assert "complete_quest" in carried
    assert "was returned" in hammer_fact("completed")
    assert "stays lost" not in hammer_fact("completed")
    session, model = _session(tmp_path)
    asyncio.run(session.talk("bram", "Hello."))
    assert "still lost" in model.calls[0]["system"]
    assert "polite question is never rudeness" in model.calls[0]["system"]
    asyncio.run(session.talk("rowan", "I accept the lost hammer quest."))
    model.calls.clear()
    asyncio.run(session.talk("rowan", "Hello."))
    hidden = model.calls[0]["system"]
    assert "Mira is holding" not in hidden
    assert "Do not say where the hammer is" in hidden
    model.calls.clear()
    asyncio.run(session.talk("mira", "Hello."))
    held = model.calls[0]["system"]
    assert "Mira is holding" in held
    assert "still missing" not in held
    asyncio.run(session.talk("mira", "Please give me a forging hammer."))
    model.calls.clear()
    asyncio.run(session.talk("rowan", "Hello."))
    packed = model.calls[0]["system"]
    assert "carrying the forging hammer" in packed
    assert "complete_quest" in packed
    asyncio.run(session.talk("rowan", "I found the hammer. Here it is."))
    model.calls.clear()
    asyncio.run(session.talk("bram", "Hello."))
    system = model.calls[0]["system"]
    assert "The hammer was returned" in system
    assert "stays lost" not in system

    rude = ScriptedNpcModel(forced_emotion="annoyed", forced_delta=-1)
    asked = TownSession(load_town_config(), tmp_path / "ask", rude)
    result = asyncio.run(asked.talk("bram", "铁匠，你的锤子找回来了吗？"))
    assert result.affinity_delta == 0
    assert asked.game.affinity("bram") == 0


def test_charges_follow_the_service_list(tmp_path: Path) -> None:
    """A hot meal is refused. A denied bed line refunds the coins."""
    session, model = _session(tmp_path)
    meal = asyncio.run(
        session.talk("mira", "Please charge me 2 gold for a hot meal."),
    )
    assert session.game.gold == 12
    assert "cannot charge" in meal.reply.lower()
    speak = [call for call in model.calls if call["phase"] == "speak"]
    assert "paid 2 gold" not in speak[-1]["system"]

    paid_model = ScriptedNpcModel()
    paid = TownSession(load_town_config(), tmp_path / "bed", paid_model)
    bed = asyncio.run(
        paid.talk("mira", "Please charge me 3 gold for a bed."),
    )
    assert paid.game.gold == 9
    assert "bed" in bed.reply.lower()
    paid_speak = [
        call for call in paid_model.calls if call["phase"] == "speak"
    ]
    assert "paid 3 gold for bed" in paid_speak[-1]["system"]
    assert "Do not deny it." in paid_speak[-1]["system"]

    deny_model = ScriptedNpcModel()
    deny_model.forced_reply = "I'm sorry, but I don't serve beds."
    denied = TownSession(load_town_config(), tmp_path / "deny", deny_model)
    refused = asyncio.run(
        denied.talk("mira", "Please charge me 3 gold for a bed."),
    )
    assert denied.game.gold == 12
    assert (
        refused.reply == "I do not offer that, so I have returned your coins."
    )


def test_chinese_reply_is_regenerated_once(tmp_path: Path) -> None:
    """An English line to a Chinese player is spoken again in Chinese."""
    model = ScriptedNpcModel()
    model.english_first = True
    session = TownSession(load_town_config(), tmp_path, model)
    result = asyncio.run(session.talk("mira", "米拉，能给我一条黑面包吗？"))
    assert reply_language(result.reply) == "Simplified Chinese"
    speak = [call for call in model.calls if call["phase"] == "speak"]
    assert len(speak) == 2
    assert "Reply in Simplified Chinese." in speak[1]["system"]


def test_a_successful_help_is_at_least_plus_one(tmp_path: Path) -> None:
    """A gift, quest, or charge is +1 even when the model says 0."""
    model = ScriptedNpcModel(forced_delta=0)
    session = TownSession(load_town_config(), tmp_path, model)
    accepted = asyncio.run(
        session.talk("rowan", "I accept the lost hammer quest."),
    )
    assert accepted.affinity_delta >= 1
    gift = asyncio.run(session.talk("bram", "Please give me a horseshoe."))
    assert "horseshoe" in session.game.inventory
    assert gift.affinity_delta >= 1
    speak = [call for call in model.calls if call["phase"] == "speak"]
    assert "own voice" in speak[-1]["system"]
    assert "horseshoe" in speak[-1]["system"]
    assert "already had" in speak[-1]["system"]
    assert "just now" not in speak[-1]["system"]
    hammer = asyncio.run(
        session.talk("mira", "Please give me a forging hammer."),
    )
    assert "forging hammer" in session.game.inventory
    assert hammer.affinity_delta >= 1
    hammer_speak = [call for call in model.calls if call["phase"] == "speak"]
    assert "Bram's lost forging hammer" in hammer_speak[-1]["system"]
    bed = asyncio.run(
        session.talk("mira", "Please charge me 3 gold for a bed."),
    )
    assert session.game.gold == 9
    assert bed.affinity_delta >= 1

    chinese_model = ScriptedNpcModel(forced_delta=0)
    chinese = TownSession(
        load_town_config(),
        tmp_path / "zh",
        chinese_model,
    )
    asyncio.run(chinese.talk("bram", "请给我 horseshoe"))
    zh_speak = [
        call for call in chinese_model.calls if call["phase"] == "speak"
    ]
    note = zh_speak[-1]["system"]
    assert "你现在把马掌交给玩家" in note
    assert "自己的口气" in note
    assert "本来就有" in note
    assert "刚才我已经" not in note
    assert "锻造锤" in note

    denied_model = ScriptedNpcModel(forced_delta=0)
    denied_model.forced_reply = "I'm sorry, but I don't serve beds."
    denied = TownSession(load_town_config(), tmp_path / "deny", denied_model)
    refused = asyncio.run(
        denied.talk("mira", "Please charge me 3 gold for a bed."),
    )
    assert denied.game.gold == 12
    assert refused.affinity_delta == 0

    thanked = ScriptedNpcModel(forced_delta=1)
    thanks = TownSession(load_town_config(), tmp_path / "thanks", thanked)
    asyncio.run(thanks.talk("rowan", "I accept the lost hammer quest."))
    again = asyncio.run(
        thanks.talk("rowan", "Thank you for the reward for the hammer."),
    )
    assert again.affinity_delta == 0
    chat = asyncio.run(thanks.talk("rowan", "What else should I know?"))
    assert chat.affinity_delta == 0


def test_speak_step_quotes_current_gold_and_inventory(
    tmp_path: Path,
) -> None:
    """The spoken line is given the gold left after a charge."""
    model = ScriptedNpcModel()
    session = TownSession(load_town_config(), tmp_path, model)
    bed = asyncio.run(
        session.talk("mira", "Please charge me 3 gold for a bed."),
    )
    assert session.game.gold == 9
    assert bed.affinity_delta >= 1
    speak = [call for call in model.calls if call["phase"] == "speak"]
    act = [call for call in model.calls if call["phase"] == "act"]
    charged_note = speak[-1]["system"]
    assert "Reference only" in charged_note
    assert "gold 9" in charged_note
    assert "inventory worn cloak" in charged_note
    assert "This turn changed gold." in charged_note
    assert "Never list the inventory unprompted" in charged_note
    assert "Do not invent a different number" in charged_note
    assert "Never list the inventory unprompted" not in act[-1]["system"]
    thanked = asyncio.run(
        session.talk("rowan", "Thank you for the reward."),
    )
    assert thanked.affinity_delta == 0
    assert session.game.gold == 9
    later = [call for call in model.calls if call["phase"] == "speak"]
    quiet = later[-1]["system"]
    assert "Reference only" not in quiet
    assert "Never list the inventory unprompted" not in quiet
    assert "Gold: 9" in quiet
    asked = asyncio.run(
        session.talk("rowan", "How much gold do I have?"),
    )
    assert asked.affinity_delta == 0
    asked_note = [call for call in model.calls if call["phase"] == "speak"][
        -1
    ]["system"]
    assert "Reference only" in asked_note
    assert "gold 9" in asked_note
    assert "changed neither gold nor inventory" in asked_note
    remembered = asyncio.run(
        session.talk("bram", "Do you remember me?"),
    )
    assert remembered.affinity_delta == 0
    memory_note = [call for call in model.calls if call["phase"] == "speak"][
        -1
    ]["system"]
    assert "Reference only" not in memory_note
    assert "inventory worn cloak" not in memory_note
    asyncio.run(session.talk("mira", "你好米拉"))
    zh = [call for call in model.calls if call["phase"] == "speak"]
    zh_note = zh[-1]["system"]
    assert "仅供对照" not in zh_note
    assert "橡树与灯笼旅店" in zh_note
    asyncio.run(session.talk("mira", "我现在有多少金币？"))
    zh_gold = [call for call in model.calls if call["phase"] == "speak"][-1][
        "system"
    ]
    assert "仅供对照" in zh_gold
    assert "金币 9" in zh_gold
    assert "不要主动报背包" in zh_gold
    assert "本轮金币和背包都没有变化" in zh_gold


def test_a_greeting_is_not_recorded_as_an_insult(tmp_path: Path) -> None:
    """A large negative delta on 'hi' is dropped and is not gossip."""
    model = ScriptedNpcModel(forced_emotion="annoyed", forced_delta=-3)
    session = TownSession(load_town_config(), tmp_path, model)
    result = asyncio.run(session.talk("bram", "hi"))
    assert result.affinity_delta == 0
    assert session.game.affinity("bram") == 0
    assert not (tmp_path / "gossip.json").exists()


def test_text_form_structured_output_is_parsed(tmp_path: Path) -> None:
    """A written structured call is not sent back to the model."""

    class TextSpeak(ScriptedNpcModel):
        """The first speak step writes the tool as plain text."""

        def __init__(self) -> None:
            super().__init__()
            self._text_used = False

        async def _call_api(self, *args, **kwargs):
            tools = kwargs.get("tools")
            if tools is None and len(args) >= 3:
                tools = args[2]
            if (
                not self._text_used
                and tools
                and "GenerateStructuredOutput" in str(tools)
            ):
                self._text_used = True
                text = (
                    'GenerateStructuredOutput(reply="The forge is quiet.", '
                    'emotion="neutral", affinity_delta=0, '
                    'affinity_reason="small talk")'
                )
                self.calls.append(
                    {
                        "phase": "speak",
                        "system": "",
                        "user": "",
                        "tools": "GenerateStructuredOutput",
                        "saw_tool_result": "no",
                        "tool_choice": "",
                    },
                )
                return ChatResponse(
                    content=[TextBlock(text=text)],
                    is_last=True,
                )
            return await super()._call_api(*args, **kwargs)

    model = TextSpeak()
    session = TownSession(load_town_config(), tmp_path, model)
    result = asyncio.run(session.talk("bram", "Hello."))
    assert result.reply == "The forge is quiet."
    speak = [call for call in model.calls if call["phase"] == "speak"]
    assert len(speak) == 1
    kwargs = _parse_structured_call(
        'GenerateStructuredOutput(reply="Quiet.", emotion="warm", '
        'affinity_delta=2, affinity_reason="gift")',
    )
    assert kwargs is not None
    assert kwargs["reply"] == "Quiet."
    assert kwargs["affinity_delta"] == 2
    parsed = _parse_structured_call(
        'GenerateStructuredOutput({"reply": "Quiet.", '
        '"emotion": "neutral", "affinity_delta": 1, '
        '"affinity_reason": "gift"})',
    )
    assert parsed is not None
    assert parsed["affinity_delta"] == 1
    real = ToolCallBlock(
        id="real",
        name="GenerateStructuredOutput",
        input="{}",
    )
    mixed = ChatResponse(
        content=[
            TextBlock(text='GenerateStructuredOutput(reply="x")'),
            real,
        ],
        is_last=True,
    )
    assert _promote_structured_text(mixed).content[1].id == "real"


def test_plain_text_scores_are_parsed(tmp_path: Path) -> None:
    """Emotion, delta, and reason after the reply do not need a re-call."""
    labeled = (
        "Very well, Kestrel. The quest is yours.\n\n"
        ' neutral 0 "Quest accepted as expected."'
    )
    parsed = _parse_structured_call(labeled)
    assert parsed is not None
    assert parsed["reply"].startswith("Very well")
    assert parsed["emotion"] == "neutral"
    assert parsed["affinity_delta"] == 0
    assert "Quest accepted" in parsed["affinity_reason"]
    stacked = (
        "You are not holding the hammer.\n\n"
        "suspicious\n"
        "-1\n"
        "False claim without the item."
    )
    stacked_parsed = _parse_structured_call(stacked)
    assert stacked_parsed is not None
    assert stacked_parsed["emotion"] == "suspicious"
    assert stacked_parsed["affinity_delta"] == -1
    assert "False claim" in stacked_parsed["affinity_reason"]
    assert _parse_structured_call("Hello there.") is None
    comma = "锻造锤就在你手里呢，Kestrel。快去还给长老吧！\n" + "warm, 0, 没有变化"
    comma_parsed = _parse_structured_call(comma)
    assert comma_parsed is not None
    assert comma_parsed["reply"].startswith("锻造锤")
    assert "warm" not in comma_parsed["reply"]
    assert comma_parsed["emotion"] == "warm"
    assert comma_parsed["affinity_delta"] == 0
    assert comma_parsed["affinity_reason"] == "没有变化"

    class PlainSpeak(ScriptedNpcModel):
        """The first speak step writes reply, emotion, delta, and reason."""

        def __init__(self) -> None:
            super().__init__()
            self._text_used = False

        async def _call_api(self, *args, **kwargs):
            tools = kwargs.get("tools")
            if tools is None and len(args) >= 3:
                tools = args[2]
            if (
                not self._text_used
                and tools
                and "GenerateStructuredOutput" in str(tools)
            ):
                self._text_used = True
                text = "The forge is quiet.\n\nneutral 0 small talk"
                self.calls.append(
                    {
                        "phase": "speak",
                        "system": "",
                        "user": "",
                        "tools": "GenerateStructuredOutput",
                        "saw_tool_result": "no",
                        "tool_choice": "",
                    },
                )
                return ChatResponse(
                    content=[TextBlock(text=text)],
                    is_last=True,
                )
            return await super()._call_api(*args, **kwargs)

    model = PlainSpeak()
    session = TownSession(load_town_config(), tmp_path, model)
    result = asyncio.run(session.talk("bram", "Hello."))
    assert result.reply == "The forge is quiet."
    speak = [call for call in model.calls if call["phase"] == "speak"]
    assert len(speak) == 1


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
