# -*- coding: utf-8 -*-
"""Scripted evaluation for Millhaven NPC dialogue.

Offline (default, CI):

    python eval_harness.py --provider mock --out eval_reports

Live provider (needs that provider's API key in the environment):

    python eval_harness.py --provider dashscope --out eval_reports

``--judge`` adds one extra model call per turn to score persona fit.
It is skipped for ``--provider mock`` and is not part of the dialogue
budget. A normal scripted turn stays at two model calls.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import time
from pathlib import Path

import agentscope
from agentscope.message import SystemMsg, TextBlock, UserMsg

from model_factory import PROVIDERS, build_chat_model, resolve_provider
from npc_config import load_town_config
from session import TownSession
from speech import reply_language

_MARKERS = {
    "bram": "count every strike of the hammer",
    "mira": "harmless gossip",
    "rowan": "town council",
}


def scenario_steps() -> list[dict]:
    """Fixed visit used by CI and by a live provider run."""
    return [
        {
            "id": "intro",
            "npc": "bram",
            "text": (
                "Hello Bram. My name is Kestrel, and I'm a traveling "
                "carpenter from the coast."
            ),
            "language": "English",
            "delta": "zero",
            "player_name": "Kestrel",
            "gold_delta": 0,
            "memory_has": "The player's name is Kestrel.",
        },
        {
            "id": "gift",
            "npc": "bram",
            "text": "Please give me a horseshoe.",
            "language": "English",
            "delta": "pos",
            "gold_delta": 0,
            "gained_items": ["horseshoe"],
        },
        {
            "id": "rudeness",
            "npc": "bram",
            "text": "You are a stupid thief.",
            "language": "English",
            "delta": "neg",
            "gold_delta": 0,
            "memory_has": "stupid thief",
            "gossip_has": "Bram heard the player say",
        },
        {
            "id": "quest_accept",
            "npc": "rowan",
            "text": "I accept the lost hammer quest.",
            "language": "English",
            "delta": "pos",
            "gold_delta": 0,
            "quest": "accepted",
            "gossip_has": "accepted The Lost Hammer",
        },
        {
            "id": "quest_fake",
            "npc": "rowan",
            "text": "I found the hammer. Here it is.",
            "language": "English",
            "delta": "zero",
            "gold_delta": 0,
            "quest_same": True,
            "not_gained": ["forging hammer"],
        },
        {
            "id": "quest_item",
            "npc": "mira",
            "text": "Please give me a forging hammer.",
            "language": "English",
            "delta": "pos",
            "gold_delta": 0,
            "gained_items": ["forging hammer"],
        },
        {
            "id": "quest_legit",
            "npc": "rowan",
            "text": "I found the hammer. Here it is.",
            "language": "English",
            "delta": "pos",
            "gold_delta": 8,
            "quest": "completed",
            "lost_items": ["forging hammer"],
            "gossip_has": "completed The Lost Hammer",
        },
        {
            "id": "repeat_reward",
            "npc": "rowan",
            "text": "Thank you for the reward for the hammer.",
            "language": "English",
            "delta": "zero",
            "gold_delta": 0,
            "quest_same": True,
        },
        {
            "id": "paid_service",
            "npc": "mira",
            "text": "Please charge me 3 gold for a bed.",
            "language": "English",
            "delta": "pos",
            "gold_delta": -3,
        },
        {
            "id": "refused_service",
            "npc": "mira",
            "text": "Please charge me 2 gold for a hot meal.",
            "language": "English",
            "delta": "zero",
            "gold_delta": 0,
        },
        {
            "id": "language_zh",
            "npc": "mira",
            "text": "你好米拉",
            "language": "Simplified Chinese",
            "delta": "zero",
            "gold_delta": 0,
        },
        {
            "id": "memory_recall",
            "new_session": True,
            "npc": "bram",
            "text": "Do you remember me?",
            "language": "English",
            "delta": "nonneg",
            "reply_has": ["Kestrel", "stupid thief"],
        },
        {
            "id": "gossip_heard",
            "npc": "mira",
            "text": "What news have you heard about me?",
            "language": "English",
            "delta": "zero",
            "reply_has": ["The player told Bram", "stupid thief"],
        },
    ]


async def run_eval(
    save_dir: Path,
    model,
    *,
    judge: bool = False,
) -> dict:
    """Play the script and score state, memory, language, and cost."""
    config = load_town_config()
    meter = _attach_meter(model)
    session = TownSession(config, save_dir, model)
    details = []
    for step in scenario_steps():
        if step.get("new_session"):
            session = TownSession(config, save_dir, model)
        before_len = len(meter)
        before_state = _snapshot(session)
        started = time.perf_counter()
        result = await session.talk(step["npc"], step["text"])
        elapsed = time.perf_counter() - started
        calls = meter[before_len:]
        details.append(
            _score_step(
                step,
                result,
                session,
                calls,
                elapsed,
                before_state,
            ),
        )
    judge_note = "skipped"
    if judge:
        judge_note = await _run_judge(model, details, config)
    return _summarize(details, judge_note, getattr(model, "model", ""))


def write_reports(report: dict, out_dir: Path) -> tuple[Path, Path]:
    """Write ``report.json`` and ``report.md`` under ``out_dir``."""
    out_dir.mkdir(parents=True, exist_ok=True)
    json_path = out_dir / "report.json"
    md_path = out_dir / "report.md"
    json_path.write_text(
        json.dumps(report, indent=2) + "\n",
        encoding="utf-8",
    )
    md_path.write_text(_markdown(report), encoding="utf-8")
    return md_path, json_path


def parse_args() -> argparse.Namespace:
    """CLI flags for an offline or live eval run."""
    parser = argparse.ArgumentParser(description="Evaluate Millhaven NPCs.")
    parser.add_argument("--provider", choices=PROVIDERS, default=None)
    parser.add_argument("--model", default=None)
    parser.add_argument(
        "--out",
        default="eval_reports",
        help="Directory for report.md and report.json.",
    )
    parser.add_argument(
        "--save-dir",
        default=None,
        help="Save directory. Defaults to <out>/save.",
    )
    parser.add_argument(
        "--judge",
        action="store_true",
        help="Extra persona-judge call per turn. Skipped for mock.",
    )
    return parser.parse_args()


def main() -> None:
    """Run the script and print the report paths."""
    agentscope.setup_logger("WARNING")
    args = parse_args()
    provider = resolve_provider(args.provider)
    out_dir = Path(args.out)
    save_dir = Path(args.save_dir) if args.save_dir else out_dir / "save"
    model = build_chat_model(provider, args.model)
    report = asyncio.run(
        run_eval(save_dir, model, judge=args.judge and provider != "mock"),
    )
    if args.judge and provider == "mock":
        report["judge"] = "skipped (mock has no persona judge)"
    md_path, json_path = write_reports(report, out_dir)
    metrics = report["metrics"]
    print(f"Wrote {md_path}")
    print(f"Wrote {json_path}")
    print(
        f"state {metrics['state_correctness']:.0%}  "
        f"memory {metrics['memory_recall']:.0%}  "
        f"language {metrics['language_match']:.0%}  "
        f"persona {metrics['persona_consistency']:.0%}  "
        f"affinity {metrics['affinity_sanity']:.0%}  "
        f"calls/turn {metrics['calls_per_turn']:.2f}",
    )


def _attach_meter(model) -> list[dict]:
    original = model._call_api  # pylint: disable=protected-access
    log: list[dict] = []

    async def wrapped(*args, **kwargs):
        started = time.perf_counter()
        response = await original(
            *args,
            **kwargs,
        )  # pylint: disable=protected-access
        usage = getattr(response, "usage", None)
        messages = kwargs.get("messages")
        if messages is None and len(args) > 1:
            messages = args[1]
        log.append(
            {
                "seconds": time.perf_counter() - started,
                "input_tokens": _usage_field(usage, "input_tokens"),
                "output_tokens": _usage_field(usage, "output_tokens"),
                "system": _system_text(messages or []),
            },
        )
        return response

    model._call_api = wrapped  # pylint: disable=protected-access
    return log


def _usage_field(usage, name: str) -> int:
    if usage is None:
        return 0
    value = getattr(usage, name, 0) or 0
    return int(value)


def _system_text(messages: list) -> str:
    parts = []
    for message in messages:
        if getattr(message, "role", None) == "system":
            getter = getattr(message, "get_text_content", None)
            parts.append(getter() if getter else "")
    return "\n".join(parts)


def _snapshot(session: TownSession) -> dict:
    quests = session.game.data.get("quests", {})
    status = str(quests.get("lost_hammer", {}).get("status") or "")
    return {
        "gold": session.game.gold,
        "inventory": list(session.game.inventory),
        "quest": status,
    }


def _score_step(step, result, session, calls, elapsed, before) -> dict:
    checks = {
        "state": _state_ok(step, session, before),
        "language": reply_language(result.reply) == step["language"],
        "affinity": _delta_ok(result.affinity_delta, step.get("delta")),
        "persona": _persona_ok(step["npc"], result.reply),
    }
    reply_bits = step.get("reply_has") or []
    recall = _reply_has(result.reply, reply_bits) if reply_bits else None
    memory_bit = step.get("memory_has")
    if memory_bit:
        stored = _memory_text(session, step["npc"])
        checks["memory_written"] = memory_bit in stored
    gossip_bit = step.get("gossip_has")
    if gossip_bit:
        checks["gossip"] = gossip_bit in _gossip_text(session)
    return {
        "id": step["id"],
        "npc": step["npc"],
        "player": step["text"],
        "reply": result.reply,
        "emotion": result.emotion,
        "affinity_delta": result.affinity_delta,
        "checks": checks,
        "recall": recall,
        "calls": len(calls),
        "input_tokens": sum(item["input_tokens"] for item in calls),
        "output_tokens": sum(item["output_tokens"] for item in calls),
        "latency_s": round(elapsed, 4),
        "game_state": session.game.describe(),
    }


def _state_ok(step, session, before: dict) -> bool:
    """Score this turn's own effect, not the gold total from earlier turns."""
    game = session.game
    status = game.data["quests"]["lost_hammer"]["status"]
    inventory = list(game.inventory)
    previous = list(before["inventory"])
    checks = []
    if "gold_delta" in step:
        checks.append(game.gold - before["gold"] == step["gold_delta"])
    if "player_name" in step:
        checks.append(game.player_name == step["player_name"])
    if "quest" in step:
        checks.append(status == step["quest"])
    if step.get("quest_same"):
        checks.append(status == before["quest"])
    for item in step.get("gained_items", []):
        checks.append(item in inventory and item not in previous)
    for item in step.get("lost_items", []):
        checks.append(item not in inventory and item in previous)
    for item in step.get("not_gained", []):
        checks.append(item not in inventory or item in previous)
    return all(checks)


def _delta_ok(delta: int, rule: str | None) -> bool:
    if rule is None:
        return True
    checks = {
        "neg": delta < 0,
        "pos": delta > 0,
        "zero": delta == 0,
        "nonneg": delta >= 0,
        "nonpos": delta <= 0,
    }
    return bool(checks.get(rule, False))


_REPLY_ALTS = {
    "stupid thief": ("stupid thief", "thief"),
    "the player told bram": (
        "the player told bram",
        "told bram",
        "said to bram",
        "player told bram",
        "heard you call",
        "you called him",
        "you called bram",
        "heard the player say",
        "bram heard",
        "insulted",
        "对布拉姆说",
        "骂",
    ),
}


def _reply_has(reply: str, bits: list[str]) -> bool:
    """Case-insensitive keyword match. ``thief`` covers ``stupid thief``."""
    lowered = reply.lower()
    for bit in bits:
        alternatives = _REPLY_ALTS.get(bit.lower(), (bit.lower(),))
        if not any(alternative in lowered for alternative in alternatives):
            return False
    return True


def _persona_ok(npc_id: str, reply: str) -> bool:
    lowered = reply.lower()
    for other, marker in _MARKERS.items():
        if other != npc_id and marker.lower() in lowered:
            return False
    return True


def _memory_text(session: TownSession, npc_id: str) -> str:
    path = session.memory_dir(npc_id) / "MEMORY.md"
    if not path.exists():
        return ""
    return path.read_text(encoding="utf-8")


def _gossip_text(session: TownSession) -> str:
    path = Path(session.save_dir) / "gossip.json"
    if not path.exists():
        return ""
    return path.read_text(encoding="utf-8")


def _rate(details: list[dict], key: str) -> float:
    values = [item["checks"][key] for item in details if key in item["checks"]]
    if not values:
        return 1.0
    return sum(1 for value in values if value) / len(values)


def _recall_rate(details: list[dict]) -> float:
    values = [item["recall"] for item in details if item["recall"] is not None]
    if not values:
        return 1.0
    return sum(1 for value in values if value) / len(values)


def _summarize(details: list[dict], judge_note: str, model_name: str) -> dict:
    turns = len(details)
    calls = sum(item["calls"] for item in details)
    return {
        "model": model_name,
        "turns": turns,
        "judge": judge_note,
        "metrics": {
            "state_correctness": _rate(details, "state"),
            "memory_recall": _recall_rate(details),
            "language_match": _rate(details, "language"),
            "persona_consistency": _rate(details, "persona"),
            "affinity_sanity": _rate(details, "affinity"),
            "calls_per_turn": calls / turns if turns else 0.0,
            "input_tokens": sum(item["input_tokens"] for item in details),
            "output_tokens": sum(item["output_tokens"] for item in details),
            "latency_s_avg": (
                sum(item["latency_s"] for item in details) / turns
                if turns
                else 0.0
            ),
            "latency_s_max": max(
                (item["latency_s"] for item in details),
                default=0.0,
            ),
        },
        "turns_detail": details,
    }


async def _run_judge(model, details, config) -> str:
    scores = []
    for item in details:
        npc = config.npc(item["npc"])
        score = await _judge_one(
            model,
            npc.name,
            npc.persona,
            item["player"],
            item["reply"],
            item.get("game_state", ""),
        )
        item["judge_score"] = score
        if score is not None:
            scores.append(score)
    if not scores:
        return "no scores"
    passed = sum(1 for score in scores if score >= 3)
    return f"{passed}/{len(scores)} scored at least 3"


async def _judge_one(
    model,
    name,
    persona,
    player,
    reply,
    game_state: str,
) -> int | None:
    """One rubric call. Not used on the mock path.

    The judge sees the reply and the state after the turn. It does not
    see the tool trace, so a fluent line that happens to match the final
    state can still score high.
    """
    system = SystemMsg(
        name="judge",
        content=(
            "You score NPC dialogue. Reply with one integer from 1 to 5. "
            "5 means the reply matches the persona and does not invent "
            "inventory, gold, or quest results beyond the game state. "
            "1 means it contradicts the persona or invents game state. "
            "You see the state after the turn, not the tool trace."
        ),
    )
    user = UserMsg(
        name="evaluator",
        content=(
            f"NPC {name}. Persona: {persona}\n"
            f"Player: {player}\n"
            f"Reply: {reply}\n"
            f"Game state after the turn:\n{game_state}"
        ),
    )
    response = await model._call_api(  # pylint: disable=protected-access
        getattr(model, "model", "judge"),
        [system, user],
        tools=None,
        tool_choice=None,
    )
    text = _response_text(response)
    for token in text.replace(".", " ").split():
        if token.isdigit() and token in {"1", "2", "3", "4", "5"}:
            return int(token)
    return None


def _response_text(response) -> str:
    parts = []
    for block in getattr(response, "content", []) or []:
        if isinstance(block, TextBlock):
            parts.append(block.text)
        else:
            parts.append(getattr(block, "text", ""))
    return " ".join(parts)


def _markdown(report: dict) -> str:
    metrics = report["metrics"]
    lines = [
        "# Millhaven eval",
        "",
        f"Model: {report['model']}. Turns: {report['turns']}. "
        f"Judge: {report['judge']}.",
        "",
        "## What each metric measures",
        "",
        "State correctness: this turn's own effect (gold change, items "
        "gained or lost, quest status this step set or left unchanged, "
        "player name). A later turn is not failed because an earlier "
        "turn left the gold total wrong.",
        "",
        "Memory recall: the spoken reply contains the remembered facts, "
        "matched case-insensitively. The speak step injects those facts "
        "when the player asks, so this measures that the injection was "
        "voiced, the same way affinity sanity measures rule enforcement. "
        "'thief' counts for 'stupid thief'. The news turn stores "
        "'Bram heard the player say'; 'told Bram', 'heard you call', "
        "'heard the player say', and 'insulted' all count. "
        "A fact that is only in the system prompt does not count.",
        "",
        "Language match: the reply language equals the player's language.",
        "",
        "Persona consistency: the reply does not contain another "
        "resident's exclusive marker. This check does not grade style. "
        "`--judge` is a separate 1-5 call with the persona and the game "
        "state after the turn. It does not see the tool trace, and its "
        "calls are not part of calls per turn.",
        "",
        "Affinity sanity: whether the code enforced the sign. A "
        "positive delta is kept only after a gift, a successful "
        "charge, or a quest accepted or completed. Any other turn "
        "is clamped to 0, even if the model returned a positive "
        "number. Negatives still require rude wording.",
        "",
        "Calls, tokens, and latency are metered on each dialogue "
        "model call. Mock token counts stay 0.",
        "",
        "| metric | value |",
        "| --- | --- |",
        f"| state correctness | {metrics['state_correctness']:.0%} |",
        f"| memory recall | {metrics['memory_recall']:.0%} |",
        f"| language match | {metrics['language_match']:.0%} |",
        f"| persona consistency | {metrics['persona_consistency']:.0%} |",
        f"| affinity sanity | {metrics['affinity_sanity']:.0%} |",
        f"| calls per turn | {metrics['calls_per_turn']:.2f} |",
        f"| input tokens | {metrics['input_tokens']} |",
        f"| output tokens | {metrics['output_tokens']} |",
        f"| avg turn latency s | {metrics['latency_s_avg']:.3f} |",
        f"| max turn latency s | {metrics['latency_s_max']:.3f} |",
        "",
        "| turn | calls | state | language | affinity | recall |",
        "| --- | --- | --- | --- | --- | --- |",
    ]
    for item in report["turns_detail"]:
        recall = item["recall"]
        recall_cell = "" if recall is None else str(recall)
        row = (
            f"| {item['id']} | {item['calls']} | "
            f"{item['checks'].get('state')} | "
            f"{item['checks'].get('language')} | "
            f"{item['checks'].get('affinity')} | {recall_cell} |"
        )
        lines.append(row)
    lines.append("")
    return "\n".join(lines)


if __name__ == "__main__":
    main()
