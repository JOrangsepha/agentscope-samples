# Millhaven NPC Dialogue

A small-town NPC dialogue sample built on **AgentScope 2.x**. Three residents
(a blacksmith, an innkeeper, and a quest-giving elder) talk to the player
from editable personas, remember the player across visits, and change a
local save when they give items, move gold, or advance a quest. Every turn
also returns a structured emotion and affinity change, which is stored and
fed back into the next reply.

This sample pins `agentscope==2.0.9`. It uses the 2.x `Agent` API.
`games/game_werewolves` in this repository still uses the 1.x `ReActAgent`
API and is a separate example.

## 🌳 Project Structure

```
.
├── README.md                 # This document
├── README_zh.md              # Chinese documentation
├── TECHNICAL_NOTES_zh.md     # Design notes (Simplified Chinese)
├── main.py                   # CLI entry point
├── town_config.json          # Editable town, NPCs, gifts, and quests
├── npc_config.py             # Config loader
├── game_state.py             # Inventory, gold, quests, affinity, emotion
├── memory_store.py           # Writes MEMORY.md for long-term memory
├── prompts.py                # Persona and relationship system prompt
├── schema.py                 # Per-turn structured output
├── tools.py                  # give_item, adjust_gold, update_quest, remember_player
├── session.py                # One visit: agent, tools, memory, affinity
├── model_factory.py          # DashScope, OpenAI-compatible, Ollama, or mock
├── mock_model.py             # Scripted model for tests and offline play
├── requirements.txt
└── tests/                    # Offline pytest suite
```

## 📖 Overview

Millhaven starts the player with a worn cloak, 12 gold, and the quest
*The Lost Hammer*. Talk to:

| id | resident | role | can give |
| --- | --- | --- | --- |
| `bram` | Bram | blacksmith | horseshoe, iron nail |
| `mira` | Mira | innkeeper | brown loaf |
| `rowan` | Rowan | elder | town seal |

Each line is one `Agent.reply` call. The model may call tools, then must
finish with `GenerateStructuredOutput`. The sample reads `reply`,
`emotion`, and `affinity_delta` from `message.structured_output`, clamps
the delta, and writes it to `save/game_state.json`. The next turn's system
prompt includes that score, so a friendlier history changes later wording.

Facts worth keeping (a name, a trade, a promise) are written under
`save/memory/<npc_id>/Memory/` by `AgenticMemoryMiddleware`. A new process
is a new visit: the chat transcript is gone, and `MEMORY.md` is injected
into the system prompt again. No vector database is required.

## 🚀 Getting Started

### Prerequisites

- Python 3.11 or newer (AgentScope 2.x does not install on 3.10)
- A DashScope API key for the default provider, **or** `--provider mock`
  to run with no key

### Installation

```bash
cd games/game_npc_dialogue
pip install -r requirements.txt
```

### Setup

The default provider is DashScope (`qwen-plus`):

```bash
export DASHSCOPE_API_KEY="your-key"
```

Other providers:

```bash
# OpenAI, or any OpenAI-compatible endpoint
export OPENAI_API_KEY="your-key"
export OPENAI_BASE_URL="https://example.com/v1"   # optional
python main.py --provider openai --model gpt-4o-mini

# Local Ollama
export OLLAMA_HOST="http://127.0.0.1:11434"        # optional
python main.py --provider ollama --model qwen2.5:7b
```

`NPC_MODEL_PROVIDER` selects the provider when `--provider` is omitted.
`NPC_MODEL` selects the model name when `--model` is omitted.

Edit `town_config.json` to change names, personas, gifts, starting gold,
and the quest. Runtime changes are stored under `--save-dir` (default
`./save`), not in the config file.

### Usage

```bash
python main.py
```

Offline, with the scripted model:

```bash
python main.py --provider mock --save-dir ./save
```

Commands inside the loop:

| command | effect |
| --- | --- |
| `/npc bram` | talk to Bram (`mira`, `rowan` work the same way) |
| `/npcs` | list residents |
| `/state` | gold, inventory, quests, affinity, last emotion |
| `/help` | show commands |
| `/quit` | leave town |

Anything else is said to the current resident. A second launch with the
same `--save-dir` continues the save and reloads NPC memory.

Example visit with `--provider mock` (player lines are what you type):

```text
Welcome to Millhaven.
Model: scripted-npc. Save: ./save. You are speaking with Bram, the blacksmith.
You (Bram)> Hello, my name is Lira and I bake bread.
[Bram | grateful | affinity 2 (+2)]
Lira, is it? I will remember a baker.
You (Bram)> Please give me a horseshoe.
[Bram | warm | affinity 3 (+1)]
Take the horseshoe. Do not waste good work.
You (Bram)> /npc rowan
You walk over to Rowan, the elder.
You (Rowan)> I accept the lost hammer quest.
[Rowan | neutral | affinity 1 (+1)]
The Lost Hammer is yours. Bring it back to Millhaven.
```

Quit, start the program again on the same save, and Bram still knows Lira:

```text
You (Bram)> Do you remember me?
[Bram | warm | affinity 4 (+1)]
Aye, I remember you, Lira the baker.
```

The scripted model only reacts to a few phrases (introductions, gifts,
the hammer quest, thanks, insults, and "do you remember me"). A live
DashScope, OpenAI, or Ollama model follows the persona and tools instead.

### Tests

No API key is required:

```bash
python -m pytest tests -q
```

## 🛠️ Features

- Three personas in `town_config.json`, each with its own gift list
- Cross-visit memory via AgentScope 2.x `AgenticMemoryMiddleware` (local Markdown, no vector DB)
- Tools that update inventory, gold, and quest progress in `game_state.json`
- Per-turn emotion and affinity, parsed from structured output and shown to the NPC next turn
- Providers: DashScope (default), OpenAI-compatible endpoints, Ollama, and an offline mock
