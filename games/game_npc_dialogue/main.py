# -*- coding: utf-8 -*-
"""Command-line visit to Millhaven."""
from __future__ import annotations

import argparse
import asyncio
from pathlib import Path

import agentscope
from agentscope.model import ChatModelBase

from model_factory import PROVIDERS, build_chat_model, resolve_provider
from npc_config import TownConfig, load_town_config
from session import TownSession

_HELP = """
Commands:
  /npc <id>   talk to another resident (bram, mira, rowan)
  /npcs       list residents
  /state      gold, inventory, quests, affinity
  /help       show this help
  /quit       leave town
Anything else is said to the current resident.
""".strip()


def parse_args() -> argparse.Namespace:
    """CLI flags. The provider defaults to DashScope."""
    parser = argparse.ArgumentParser(
        description="Talk to the residents of Millhaven.",
    )
    parser.add_argument(
        "--provider",
        choices=PROVIDERS,
        default=None,
        help=(
            "Model provider. Default: NPC_MODEL_PROVIDER or dashscope. "
            "Use mock to run without an API key."
        ),
    )
    parser.add_argument(
        "--model",
        default=None,
        help="Model name. Overrides NPC_MODEL and the provider default.",
    )
    parser.add_argument(
        "--config",
        default=None,
        help="Path to town_config.json. Defaults to the copy beside main.py.",
    )
    parser.add_argument(
        "--save-dir",
        default="save",
        help="Directory for game_state.json and NPC memory files.",
    )
    parser.add_argument(
        "--player-name",
        default=None,
        help="Player name used when creating a new save.",
    )
    return parser.parse_args()


async def run_cli(
    session: TownSession,
    config: TownConfig,
    model: ChatModelBase,
) -> None:
    """Read lines until the player quits or stdin closes."""
    current = "bram"
    _print_banner(config, session, model, current)
    while True:
        npc = config.npc(current)
        try:
            line = input(f"You ({npc.name})> ")
        except EOFError:
            print()
            break
        stripped = line.strip()
        if not stripped:
            continue
        if stripped.startswith("/"):
            current, should_stop = _handle_command(
                stripped,
                current,
                session,
                config,
            )
            if should_stop:
                break
            continue
        result = await session.talk(current, stripped)
        _print_turn(result)


def _handle_command(
    line: str,
    current: str,
    session: TownSession,
    config: TownConfig,
) -> tuple[str, bool]:
    """Return the selected NPC id and whether the loop should stop."""
    parts = line.split(maxsplit=1)
    command = parts[0].lower()
    argument = parts[1].strip() if len(parts) > 1 else ""
    if command in {"/quit", "/exit", "/q"}:
        print("The road out of Millhaven is quiet. Farewell.")
        return current, True
    _run_simple_command(command, current, session, config)
    if command == "/npc":
        return _switch_npc(argument, current, config), False
    return current, False


def _run_simple_command(
    command: str,
    current: str,
    session: TownSession,
    config: TownConfig,
) -> None:
    """Print help, state, the roster, or an unknown-command note."""
    if command == "/help":
        print(_HELP)
    elif command == "/state":
        print(session.game.describe_for_player())
    elif command == "/npcs":
        for npc_id, npc in config.npcs.items():
            mark = "*" if npc_id == current else " "
            print(f" {mark} {npc_id}: {npc.name}, {npc.role}")
    elif command not in {"/npc", "/quit", "/exit", "/q"}:
        print(f"Unknown command '{command}'. Type /help.")


def _switch_npc(argument: str, current: str, config: TownConfig) -> str:
    """Switch resident, or stay put when the id is unknown."""
    if argument not in config.npcs:
        known = ", ".join(sorted(config.npcs))
        print(f"Unknown resident '{argument}'. Known: {known}.")
        return current
    npc = config.npc(argument)
    print(f"You walk over to {npc.name}, the {npc.role}.")
    return argument


def _print_banner(
    config: TownConfig,
    session: TownSession,
    model: ChatModelBase,
    current: str,
) -> None:
    npc = config.npc(current)
    print(f"Welcome to {config.town_name}.")
    print(
        f"Model: {model.model}. Save: {session.save_dir}. "
        f"You are speaking with {npc.name}, the {npc.role}.",
    )
    print("Type /help for commands.")


def _print_turn(result) -> None:
    sign = f"{result.affinity_delta:+d}"
    print(
        f"[{result.npc_name} | {result.emotion} | "
        f"affinity {result.affinity} ({sign})]",
    )
    print(result.reply)


def main() -> None:
    """Entry point."""
    agentscope.setup_logger("WARNING")
    args = parse_args()
    provider = resolve_provider(args.provider)
    config = load_town_config(args.config)
    if args.player_name:
        save_file = Path(args.save_dir) / "game_state.json"
        if not save_file.exists():
            config.player_name = args.player_name
    model = build_chat_model(provider, args.model)
    session = TownSession(config, args.save_dir, model)
    asyncio.run(run_cli(session, config, model))


if __name__ == "__main__":
    main()
