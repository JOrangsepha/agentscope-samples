# -*- coding: utf-8 -*-
"""The offline eval script scores the fixed Millhaven visit."""
from __future__ import annotations

import asyncio
from pathlib import Path

from eval_harness import _reply_has, run_eval, write_reports
from mock_model import ScriptedNpcModel


def test_offline_eval_meets_the_dialogue_budget(tmp_path: Path) -> None:
    """Mock run: state, memory, language, and two calls per turn."""
    report = asyncio.run(
        run_eval(tmp_path / "save", ScriptedNpcModel(), judge=False),
    )
    metrics = report["metrics"]
    assert metrics["state_correctness"] == 1.0
    assert metrics["memory_recall"] == 1.0
    assert metrics["language_match"] == 1.0
    assert metrics["persona_consistency"] == 1.0
    assert metrics["affinity_sanity"] == 1.0
    assert metrics["calls_per_turn"] == 2.0
    assert metrics["input_tokens"] == 0
    assert report["judge"] == "skipped"
    md_path, json_path = write_reports(report, tmp_path / "out")
    report_text = md_path.read_text(encoding="utf-8")
    assert "state correctness" in report_text
    assert "What each metric measures" in report_text
    assert "spoken reply contains the remembered facts" in report_text
    assert json_path.exists()


def test_recall_accepts_a_synonym() -> None:
    """'thief' and 'told Bram' satisfy the stored fact strings."""
    remembered = "You called me a thief. I remember both, Kestrel."
    assert _reply_has(remembered, ["Kestrel", "stupid thief"])
    heard = "You told Bram he was a thief."
    assert _reply_has(heard, ["The player told Bram", "stupid thief"])
    vague = "I told Bram you were rude."
    assert not _reply_has(vague, ["The player told Bram", "stupid thief"])
    heard = "Bram heard you call him a stupid thief"
    assert _reply_has(heard, ["The player told Bram", "stupid thief"])
    stored = 'Bram heard the player say: "You are a stupid thief."'
    assert _reply_has(stored, ["The player told Bram", "stupid thief"])
