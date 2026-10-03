# -*- coding: utf-8 -*-
"""Player-line sentiment moves affinity a little, then stops for the day."""
from __future__ import annotations

import asyncio
from pathlib import Path

from game_state import GameState, quest_guidance
from mock_model import ScriptedNpcModel
from npc_config import load_town_config
from sentiment import conversational_sentiment, score_player_line
from session import TownSession


def test_lexicon_covers_polarity_negation_and_both_languages() -> None:
    """Positive, negative, neutral, negation, Chinese, and English."""
    assert score_player_line("You are very kind.").delta == 2
    assert score_player_line("Thanks.").label == "positive"
    assert score_player_line("请，谢谢。").delta == 2
    assert score_player_line("你人真好").delta == 1
    assert score_player_line("我很喜欢你").delta == 2
    assert score_player_line("I hate this.").delta == -1
    assert score_player_line("你真讨厌").delta == -2
    assert score_player_line("你非常讨厌").delta == -2
    assert score_player_line("Hello.").delta == 0
    assert score_player_line("你好米拉").label == "neutral"
    assert score_player_line("The forge is busy today.").delta == 0
    assert score_player_line("I do not like this.").delta < 0
    assert score_player_line("我不喜欢").delta < 0
    assert score_player_line("not very kind").delta < 0
    assert score_player_line("Don't be stupid.").delta > 0
    assert conversational_sentiment("How much gold do I have?").delta == 0
    assert conversational_sentiment("Please give me a horseshoe.").delta == 0
    assert (
        conversational_sentiment(
            "Thank you for the reward for the hammer.",
        ).delta
        == 0
    )
    assert conversational_sentiment("谢谢你").delta == 1


def test_kind_chinese_raises_affinity_until_the_daily_cap(
    tmp_path: Path,
) -> None:
    """Mock mode applies the lexicon, then /wait opens the budget again."""
    model = ScriptedNpcModel()
    session = TownSession(load_town_config(), tmp_path, model)
    first = asyncio.run(session.talk("bram", "谢谢你"))
    assert first.affinity_delta == 1
    assert first.affinity == 1
    assert first.emotion == "grateful"
    assert "你这么说，我很高兴。" in first.reply
    speak = [call for call in model.calls if call["phase"] == "speak"]
    assert "Player sentiment: positive" in speak[-1]["system"]
    second = asyncio.run(session.talk("bram", "你人真好"))
    assert second.affinity_delta == 1
    assert session.game.affinity("bram") == 2
    third = asyncio.run(session.talk("bram", "我很喜欢你"))
    assert third.affinity_delta == 0
    assert session.game.affinity("bram") == 2
    rude = asyncio.run(session.talk("mira", "你真讨厌"))
    assert rude.affinity_delta == -2
    assert rude.emotion == "annoyed"
    assert session.game.affinity("mira") == 3
    session.wait_in_town()
    again = asyncio.run(session.talk("bram", "谢谢"))
    assert again.affinity_delta == 1
    assert session.game.affinity("bram") == 3
    saved = GameState(tmp_path / "game_state.json", load_town_config())
    assert saved.affinity("bram") == 3


def test_quest_guidance_follows_the_hammer(tmp_path: Path) -> None:
    """Objectives name the resident the current step actually uses."""
    config = load_town_config()
    state = GameState(tmp_path / "game_state.json", config)
    first = quest_guidance(state, "lost_hammer")
    assert first["marker_npc"] == "rowan"
    assert first["marker"] == "!"
    assert "Talk to Rowan" in first["objective"]
    assert "Bram lost" in first["objective"]
    state.accept_quest("rowan", "lost_hammer")
    fetched = quest_guidance(state, "lost_hammer")
    assert fetched["marker_npc"] == "mira"
    assert fetched["marker"] == "?"
    assert "Oak and Lantern" in fetched["objective"]
    state.give_item("mira", "forging hammer")
    carried = quest_guidance(state, "lost_hammer")
    assert carried["marker_npc"] == "rowan"
    assert "Return the forging hammer to Rowan" in carried["objective"]
    state.complete_quest("rowan", "lost_hammer")
    done = quest_guidance(state, "lost_hammer")
    assert done["completed"] is True
    assert done["marker"] == ""
    assert done["reward_gold"] == 8
    assert "8 gold" in done["objective"]
