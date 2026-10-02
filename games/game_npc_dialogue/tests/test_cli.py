# -*- coding: utf-8 -*-
"""The CLI runs end to end against the scripted model."""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _run(save_dir: Path, script: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            sys.executable,
            str(ROOT / "main.py"),
            "--provider",
            "mock",
            "--save-dir",
            str(save_dir),
        ],
        input=script,
        text=True,
        capture_output=True,
        check=True,
        cwd=ROOT,
    )


def test_cli_mock_transcript_remembers_across_processes(
    tmp_path: Path,
) -> None:
    """Two process starts share the save and the second recalls Lira."""
    save_dir = tmp_path / "save"
    first = _run(
        save_dir,
        "\n".join(
            [
                "Hello, my name is Lira and I bake bread.",
                "Please give me a horseshoe.",
                "/npc rowan",
                "I accept the lost hammer quest.",
                "/state",
                "/quit",
                "",
            ],
        ),
    )
    assert "Lira, is it?" in first.stdout
    assert "horseshoe" in first.stdout
    assert "The Lost Hammer is yours." in first.stdout
    assert "lost_hammer: The Lost Hammer [accepted" in first.stdout

    second = _run(save_dir, "Do you remember me?\n/quit\n")
    assert "Aye, I remember you, Lira the baker." in second.stdout
