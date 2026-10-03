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
├── speech.py                 # Player name, and cleanup of the spoken line
├── sentiment.py              # Lexicon sentiment of the player's line
├── tools.py                  # Per-NPC give, charge, quest, and memory tools
├── session.py                # One visit: act, then speak, then save affinity
├── gossip.py                 # Public rumors; private facts stay per NPC
├── model_factory.py          # DashScope, OpenAI-compatible, Ollama, or mock
├── mock_model.py             # Scripted model for tests and offline play
├── eval_harness.py           # Scripted eval; writes markdown and JSON
├── web_demo.py               # Browser UI on the same TownSession
├── web/index.html            # One page, no extra frontend package
├── docs/web_demo.png         # Screenshot of the mock web demo
├── requirements.txt
└── tests/                    # Offline pytest suite
```

## 📖 Overview

Millhaven starts the player with a worn cloak, 12 gold, and the quest
*The Lost Hammer*. Talk to:

| id | resident | role | can give |
| --- | --- | --- | --- |
| `bram` | Bram | blacksmith | horseshoe, iron nail |
| `mira` | Mira | innkeeper | brown loaf; the forging hammer once the quest is accepted; she can charge for a bed |
| `rowan` | Rowan | elder | no free gifts; he alone can accept and complete The Lost Hammer |

Each line is an action `Agent.reply` followed by a spoken one. The action
step ends once a game tool has run. If that first response calls no tool,
it is asked once more to call one, including `no_action`. The spoken step
has no game tools and must finish with `GenerateStructuredOutput`, so the
line is written after the tool results. The sample reads `reply`, `emotion`, and `affinity_delta` from
`message.structured_output`, clamps the delta, and writes it to
`save/game_state.json`. The next turn's system prompt includes that score,
so a friendlier history changes later wording.

Quest rewards are not a free-form gold tool. Rowan can complete The Lost
Hammer only while the player is carrying a forging hammer. The game then
removes the hammer and pays the 8 gold in `town_config.json` once.

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
| `/gossip` | public rumors shared between residents |
| `/wait` | a short scripted exchange about the latest rumor |
| `/help` | show commands |
| `/quit` | leave town |

Anything else is said to the current resident. A second launch with the
same `--save-dir` continues the save and reloads NPC memory.

Example visit with `--provider mock` (player lines are what you type):

```text
Welcome to Millhaven.
Model: scripted-npc. Save: ./save. You are speaking with Bram, the blacksmith.
You (Bram)> Hello, my name is Lira and I bake bread.
[Bram | neutral | affinity 0 (+0)]
Lira, is it? I will remember a baker.
You (Bram)> Please give me a horseshoe.
[Bram | warm | affinity 1 (+1)]
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
[Bram | warm | affinity 2 (+1)]
Aye, I remember you, Lira the baker.
```

The scripted model only reacts to a few phrases (introductions, gifts,
the hammer quest, thanks, insults, and "do you remember me"). A live
DashScope, OpenAI, or Ollama model follows the persona and tools instead.

### Gossip

Residents share a small public log in `save/gossip.json`. An insult is
written only when the player's own line is rude and the affinity delta
is -2 or lower. Quest news (accepted or completed) is written too. Both
appear in the next resident's system prompt under "Town rumors". The
player's name, trade, gold, and inventory stay in that NPC's own
`MEMORY.md` and are not copied into the log.

A question about news, rumors, or whether anyone complained injects the
latest public fact into the spoken step, addressed to the player as
"you", and labels an insult as a rude remark with its verbatim quote.
If that reply neither quotes the remark nor names it as rude, the speak
step runs once more. The standing rumor list stays in the stored
wording. A question about whether the NPC remembers the player, how
they spoke, or what they said injects that NPC's stored name, trade,
and insult, and tells him to say "you said". News and recall questions
that call no game tool record `no_action` without an extra nudge.
`/wait` prints a short exchange and shows the rumor with "you". It does
not call the model, so a normal spoken turn stays at two calls. The
spoken step is given the player's current gold and inventory only when
the player asked about them or this turn changed them. The inn is the
Oak and Lantern (橡树与灯笼旅店). One roster line says Bram forges
blades and handles, Mira rents beds only, and Rowan has no craft. A
repeat payout says the 8-gold reward was already paid only in the quest
tool result.

### Evaluation

The harness plays a fixed 13-turn script: introduction, gift, rudeness,
quest accept, a claim with no hammer, receiving the hammer, a real
completion, a repeat reward, a paid bed, a refused hot meal, a Chinese
line, a new session whose reply must use the name and the insult, and Mira
asked for news so her reply must quote the rumor. It writes
`report.md` and `report.json`.

Offline, no API key (this is what CI can run):

```bash
python eval_harness.py --provider mock --out eval_reports
```

With a live provider (needs that provider's key; not run in CI):

```bash
python eval_harness.py --provider dashscope --out eval_reports
```

`--judge` adds one extra model call per turn that scores persona fit
from 1 to 5. It sees the game state after the turn, not the tool trace.
It is skipped for `--provider mock` and is not part of the two-call
dialogue budget. The report states what each metric measures. State is
this turn's own effect. Memory recall matches the spoken reply without
case: "thief" counts for "stupid thief". "told Bram", "heard you call",
"you called him", "heard the player say", and "insulted" count for the
gossip sentence. Persona
checks that the reply does not use another resident's marker. A gift, a
successful charge, or a quest accept or complete is at least +1. Other
model deltas stay at 0, except a small lexicon score of the player's
own words (`sentiment.py`, -2 to +2). That small-talk score is capped
at +2 and -4 per resident until the next `/wait`. Greetings, questions,
item requests, and thanks for a reward do not use it. Mock token counts
stay 0.

### Web demo

Same `TownSession` as the CLI, served by the Python standard library.
Every request uses one background event loop:

```bash
python web_demo.py --provider mock --port 8765
```

Open http://127.0.0.1:8765 . You walk a pixel town as a carpenter.
WASD or the arrow keys move, and a click on the ground walks there.
Stand next to Bram, Mira, or Rowan and the hint reads
**Press E / click to talk**. Clicking that resident opens a fresh
wooden dialogue box: a pixel portrait, friendship hearts inside the
frame, and the reply typed out underneath. Switching residents, or
closing and opening again, clears the previous lines. A late reply
cannot land in the new box. Click the box or press Space to finish
the line. An emote bubble over the sprite follows the structured
emotion. Kind or rude small talk moves the hearts by a small amount
and shows `+1 ♥` or `-1 ♥`. Gold, the pack, The Lost Hammer, and the
clock sit beside the map. The quest panel states the next step, and
a `!` or `?` floats over the resident who can do it: accept the quest
with Rowan, ask Mira for the hammer, then return the hammer to Rowan.
**Sleep / Next day** (the button, or the bed by the inn)
is the same scripted exchange as `/wait`, shown as a town notice.
Sleep also refreshes the small-talk affinity budget.
Movement keys are ignored while the line is focused. An unknown
`npc_id` returns HTTP 400 and is not sent to the current resident.

![Millhaven web demo with the mock model](docs/web_demo.png)

### Tests

No API key is required:

```bash
python -m pytest tests -q
```

## 🛠️ Features

- Three personas in `town_config.json`, each with its own gift list
- Cross-visit memory via AgentScope 2.x `AgenticMemoryMiddleware` (local Markdown, no vector DB)
- Tools that update inventory, a charge, and quest progress in `game_state.json`, with the reward paid by code
- Per-turn emotion and affinity, parsed from structured output and shown to the NPC next turn
- Providers: DashScope (default), OpenAI-compatible endpoints, Ollama, and an offline mock
- Public gossip (`gossip.json`) separate from private per-NPC memory, plus `/wait`
- A scripted eval harness (`eval_harness.py`) that runs offline or against a live provider
- A browser demo (`web_demo.py`) on the same session, with no extra web dependency
