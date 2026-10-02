# 米尔黑文小镇 NPC 对话

基于 **AgentScope 2.x** 的小镇 NPC 对话示例。铁匠、旅店老板和任务发布者
各有一份可编辑的人设。他们会跨次访问记住玩家，并在赠送物品、增减金币或
推进任务时修改本地存档。每一轮还会返回结构化的情绪与好感度变化，写入
存档后影响下一轮的说法。

本示例固定依赖 `agentscope==2.0.9`，使用 2.x 的 `Agent` API。
本仓库里的 `games/game_werewolves` 仍使用 1.x 的 `ReActAgent`，两者互不替代。

## 🌳 项目结构

```
.
├── README.md                 # 英文说明
├── README_zh.md              # 本文件
├── TECHNICAL_NOTES_zh.md     # 中文技术笔记
├── main.py                   # 命令行入口
├── town_config.json          # 可编辑的小镇、NPC、赠礼和任务
├── npc_config.py             # 配置加载
├── game_state.py             # 背包、金币、任务、好感度、情绪
├── memory_store.py           # 为长期记忆写入 MEMORY.md
├── prompts.py                # 人设与关系系统提示
├── schema.py                 # 每轮结构化输出
├── tools.py                  # give_item、adjust_gold、update_quest、remember_player
├── session.py                # 一次访问：智能体、工具、记忆、好感度
├── model_factory.py          # DashScope、OpenAI 兼容、Ollama 或 mock
├── mock_model.py             # 供测试和离线游玩的脚本模型
├── requirements.txt
└── tests/                    # 离线 pytest
```

## 📖 概述

玩家初始拥有一件旧斗篷、12 枚金币，以及任务「失落的锤子」。可以交谈的居民：

| id | 居民 | 身份 | 可以赠送 |
| --- | --- | --- | --- |
| `bram` | Bram | 铁匠 | 马掌、铁钉 |
| `mira` | Mira | 旅店老板 | 黑面包 |
| `rowan` | Rowan | 长老 | 镇印 |

玩家的每一句话对应一次 `Agent.reply`。模型可以调用工具，最后必须调用
`GenerateStructuredOutput`。示例从 `message.structured_output` 读取
`reply`、`emotion` 和 `affinity_delta`，把变化量限制在 -3 到 3，并写入
`save/game_state.json`。下一轮的系统提示会带上这个分数，因此关系会影响
之后的措辞。

值得记住的事实（名字、职业、承诺）由 `AgenticMemoryMiddleware` 写到
`save/memory/<npc_id>/Memory/`。重新启动进程就是下一次来访：对话原文不再
保留，但 `MEMORY.md` 会再次注入系统提示。不需要向量数据库。

## 🚀 开始使用

### 环境要求

- Python 3.11 及以上（AgentScope 2.x 无法安装在 3.10 上）
- 默认的 DashScope API Key，或者使用 `--provider mock` 在没有密钥时运行

### 安装

```bash
cd games/game_npc_dialogue
pip install -r requirements.txt
```

### 配置

默认提供方是 DashScope（模型 `qwen-plus`）：

```bash
export DASHSCOPE_API_KEY="your-key"
```

其他提供方：

```bash
# OpenAI，或任何 OpenAI 兼容接口
export OPENAI_API_KEY="your-key"
export OPENAI_BASE_URL="https://example.com/v1"   # 可选
python main.py --provider openai --model gpt-4o-mini

# 本机 Ollama
export OLLAMA_HOST="http://127.0.0.1:11434"        # 可选
python main.py --provider ollama --model qwen2.5:7b
```

未传 `--provider` 时读取环境变量 `NPC_MODEL_PROVIDER`。
未传 `--model` 时读取 `NPC_MODEL`，否则使用该提供方的默认模型名。

修改 `town_config.json` 可以更换姓名、人设、赠礼、初始金币和任务。
运行时的变化写在 `--save-dir`（默认 `./save`），不会改配置文件。

### 运行

```bash
python main.py
```

不调用真实模型：

```bash
python main.py --provider mock --save-dir ./save
```

循环内命令：

| 命令 | 作用 |
| --- | --- |
| `/npc bram` | 改和 Bram 说话（`mira`、`rowan` 同样可用） |
| `/npcs` | 列出居民 |
| `/state` | 查看金币、背包、任务、好感度和上次情绪 |
| `/help` | 显示命令 |
| `/quit` | 离开小镇 |

其他输入都会说给当前居民。用同一个 `--save-dir` 再次启动会继续这份存档，
并重新加载 NPC 记忆。

使用 `--provider mock` 的一次访问（`You` 后面是玩家输入）：

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

退出后用同一存档再启动，Bram 仍然记得 Lira：

```text
You (Bram)> Do you remember me?
[Bram | warm | affinity 4 (+1)]
Aye, I remember you, Lira the baker.
```

脚本模型只识别有限说法（自我介绍、赠礼、锤子任务、道谢、侮辱，以及
“do you remember me”）。DashScope、OpenAI 或 Ollama 会按人设和工具自行对话。

### 测试

不需要 API Key：

```bash
python -m pytest tests -q
```

## 🛠️ 功能

- `town_config.json` 中的三份人设，各自带可赠送物品
- 用 AgentScope 2.x 的 `AgenticMemoryMiddleware` 做跨次访问记忆（本地 Markdown，无向量库）
- 工具会更新 `game_state.json` 里的背包、金币和任务进度
- 每轮情绪与好感度来自结构化输出，下一轮写回系统提示
- 提供方：DashScope（默认）、OpenAI 兼容接口、Ollama，以及离线 mock
