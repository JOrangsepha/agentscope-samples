# 米尔黑文 NPC 对话：技术笔记

给审阅用的简短说明。实现见同目录代码，使用方式见 `README_zh.md`。

## 目标

在 AgentScope **2.0.9** 上做一个可放进简历的游戏向示例，覆盖贡献指南里点名的
“NPC dialogue systems”，并刻意只用 2.x API。范围控制在一座小镇、三名 NPC、
一份本地存档：

- 人设放在 `town_config.json`，不写死在提示词里。
- 跨进程记住玩家说过的身份，不引入向量数据库。
- 工具真正改写金币、背包和任务。
- 每轮给出情绪和好感度，持久化后影响下一轮提示。
- 命令行可以切换 NPC。没有 API Key 时用脚本模型把上述路径跑通。

## 架构

```mermaid
flowchart TD
    CLI["main.py 命令行"] --> Session["TownSession"]
    Session --> Prompt["系统提示: 人设 + 好感度 + 游戏状态"]
    Session --> Agent["agentscope.agent.Agent"]
    Prompt --> Agent
    Agent --> Model["ChatModel: DashScope / OpenAI / Ollama / mock"]
    Agent --> Act["第一次 reply: 该 NPC 的游戏工具"]
    Act --> Speak["第二次 reply: 仅 GenerateStructuredOutput"]
    Agent --> Memory["AgenticMemoryMiddleware"]
    Act --> Save["save/game_state.json"]
    Act --> Notes["save/memory/npc/Memory/MEMORY.md"]
    Memory --> Notes
    Speak --> Polish["polish_reply"]
    Polish --> Save
```

一次 `talk()` 会建两个 `Agent`：第一个带着该 NPC 的游戏工具，不要求结构化
输出；第二个不带游戏工具，只生成 `NpcTurn`。两者复用该 NPC 的 `AgentState`，
所以同一次进程里的短期对话还在，第二段也能看到第一段的工具结果。进程退出后
短期上下文丢弃。下次来访只靠两份本地文件：游戏状态，以及每个 NPC 自己的
`MEMORY.md`。

## 用到的 AgentScope 2.x API，以及为什么

| API | 用途 |
| --- | --- |
| `agentscope.agent.Agent` | 2.x 的统一智能体。1.x 的 `ReActAgent` 已不存在。 |
| `Agent.reply(..., structured_schema=NpcTurn)` | 要求本轮以结构化结果结束。字段在 `Msg.structured_output`，不在 1.x 的 `metadata`。 |
| `Toolkit` + `FunctionTool` | 把普通函数注册成工具。2.x 不再使用 `toolkit.register_tool_function`。 |
| `PermissionDecision(ALLOW)` 与 `PermissionMode.BYPASS` | 自定义工具默认会向用户确认。游戏循环不能停下来等确认，因此显式放行。 |
| `AgenticMemoryMiddleware` | 2.x 的文件型长期记忆。本地 Markdown，不需要 mem0 / Qdrant。 |
| `UserMsg` | 2.x 的用户消息构造函数。 |
| `DashScopeChatModel` + `DashScopeCredential` | 默认模型。密钥来自 `DASHSCOPE_API_KEY`，不再把 `api_key=` 直接传给模型。 |
| `OpenAIChatModel` / `OllamaChatModel` | 可选提供方。OpenAI 凭证上的 `base_url` 即兼容接口。 |
| `InjectionConfig(inject_runtime_state=False)` | 关掉默认的时间与任务注入，避免游戏提示被编码助手风格的运行时状态淹没。 |

`AgenticMemoryMiddleware` 的 `retrieval_async=False`。打开后，中间件会再用
一次对话模型挑选记忆文件；离线测试和没有额外调用预算的 CLI 都不适合。
因此索引行本身写上事实全文，下一轮系统提示就能直接看到，不必再 `Read` 文件。

没有使用 `Mem0Middleware`：开源 mem0 默认走向量库。也没有使用 `ReMeMiddleware`：
它会再调一次模型做抽取，没有密钥就无法在测试里落盘。

## 记忆、工具、情绪如何配合

**记忆。** `remember_player(fact)` 在该 NPC 的 `Memory/` 下追加 `fact_N.md`
（带 frontmatter，符合中间件扫描格式），并在 `MEMORY.md` 增加一行
`- [Fact N](fact_N.md) — <事实>`。中间件的 `on_system_prompt` 会把 `MEMORY.md`
拼进系统提示。测试用两个 `TownSession`、同一 `save_dir`、两份新的 `AgentState`
证明：第二次调用时模型收到的系统提示含有 “Lira”，回复也点出这个名字。

**工具。** 每个 NPC 只有自己用得上的工具。三人都有 `give_item` 和
`remember_player`。只有 `can_charge` 的 Mira 有 `charge_player`，而且它只扣
金币。只有任务发布者 Rowan 有 `accept_quest` 和 `complete_quest`。模型没有
`update_quest`，也不能直接加金币。完成「失落的锤子」必须由 Rowan 发起、任务
已是 `accepted`、背包里有 `forging hammer`。代码收走锤子，把状态写成
`completed`，并按配置里的 `reward_gold`（8）支付一次，同时置 `rewarded`。
已经完成或已经发过奖的调用是空操作。旧存档如果只有 `completed`、没有
`rewarded`，也视为已经发过。锤子本身由 Mira 的 `quest_gifts` 在任务被接受后
送出，口头宣称不会凭空生成物品。写操作都 `is_concurrency_safe=False`，并立即
`save()`。

**情绪与好感。** `NpcTurn` 含 `reply`、`emotion`、`affinity_delta`（-3 到 3）
和 `affinity_reason`。应用层把分数夹在 -100 到 100，并把情绪记在同一份 JSON。
下一轮 `build_system_prompt` 写入 `Affinity: N` 和上次情绪。脚本模型见到
`Affinity: 2` 以上的问候时，会改说 “It is good to see you again.”，用来证明
持久化后的分数真的进入了后续提示，而不是只打印出来。

## 测试策略与结果

全部离线，模型是 `ScriptedNpcModel`（实现 `ChatModelBase._call_api`，不访问网络）。

```bash
cd games/game_npc_dialogue
python -m pytest tests -q
```

在 Python 3.12.3、`agentscope==2.0.9` 上结果为 **19 passed**。覆盖：

- 人设加载，以及三份系统提示互不串人设（`test_persona.py`）
- 记忆文件跨 session 注入（`test_memory.py`）
- 工具调用改写背包、任务、金币，以及拒绝不属于该 NPC 的赠礼（`test_tools.py`）
- 情绪与好感度解析、落盘，并改变下一次问候（`test_emotion.py`）
- 两个进程的 CLI：介绍自己、拿马掌、接任务，再次启动后记得 Lira（`test_cli.py`）
- 实机里暴露的规则：空口交任务被拒绝、奖励只发一次、工具按 NPC 限制、
  语言和评分规则写在提示里、自报姓名会更新、强烈好感变化写入记忆、
  台词发生在工具结果之后（`test_live_fixes.py`）

另外用同一脚本模型手工跑过 CLI，记录见 `README_zh.md` 的示例访问。
DashScope `qwen-plus` 的一次实机记录见下一节。OpenAI 与 Ollama 没有跑。

静态检查按仓库 `.pre-commit-config.yaml` 的参数，在本示例的 Python 文件上
通过了 Black 23.3.0（行宽 79）、Flake8、Mypy 1.7.0 和 Pylint 3.0.2。
仓库 CI 的 pre-commit 跑在 Python 3.10 上，只做语法和风格，不导入 agentscope，
因此示例源码保持 3.10 语法；真正运行仍要 3.11+。

## 2.x 上实际踩到的差异

- 最终那条 `reply()` 消息的正文是固定的 “The required structured output is generated.” 台词在 `structured_output["reply"]`。
- 自定义 `ChatModelBase` 必须自己设置 `formatter`。`Agent` 在调用模型前会读 `model.formatter.supported_input_media_types`，内置模型在构造函数里赋值，子类不会自动有。
- `FunctionTool` 不传 `permission` 时行为是 ASK，无人值守循环会停在确认上。
- 结构化输出不是模型直接吐 JSON，而是调用内置工具 `GenerateStructuredOutput`。参数名必须和 Pydantic 字段一致。
- `AgenticMemoryMiddleware` 默认的记忆说明是面向编程助手的长提示，并且默认会再发一次检索模型调用。游戏示例换成了短说明，并关闭了异步检索。

## 实机测试发现与修复

用 DashScope `qwen-plus` 跑了 3 次来访、16 轮对话，没有异常。结构化输出每轮
都有，三个人设能分开，跨 session 的名字记忆也生效。合计 25 次模型调用，输入
37457 token、输出 2219 token。16 轮延迟合计 72.3 秒，平均约 4.5 秒/轮（约
4.6 秒）。

这次记录暴露的问题，以及代码里的对应改动：

1. 背包里没有锻造锤时，Bram 仍调用 `update_quest(completed)`。任何 NPC 都能改
   任何任务和金币。现在规则在配置和 `GameState` 里：只有发布者能接受和完成，
   只能完成一次，必须先带着要求的物品。奖励由代码支付。
2. 同一会话里，Rowan 对一句无关的话又调用了 `adjust_gold(+5)` 和
   `update_quest`，金币从 9 加到 14，下一轮中文提问又加到 19。已完成的任务
   再更新是空操作，不会第二次付钱。
3. 游戏工具和 `GenerateStructuredOutput` 出现在同一次响应里，台词在工具结果
   返回之前就写好了。`talk()` 先行动、再单独生成结构化台词，展示前再走
   `polish_reply`。
4. 玩家说中文时，除非明确要求，回复仍是英文。提示要求使用玩家刚才的语言。
5. 礼貌但做不到的请求（钢剑）被扣了 2 点好感。提示写明：只有粗鲁、威胁或
   失信才扣分，做不到的礼貌请求是 0。
6. 辱骂和接任务没有写入记忆，所以第二次 Bram 说自己不记得语气，尽管好感已经
   是 -4。`|delta| >= 2` 时自动写一条记忆，并把上一次强烈印象放进提示。接受
   和完成任务也会记一笔。
7. `player.name` 一直是 Traveler，Rowan 继续这么称呼。英文 “my name is …” 和
   中文 “我叫…” 会在生成本轮提示之前更新名字。
8. Mira 超过一两句，还写了 `*wipes a mug*`，并且口头答应给面包但没有调用工具。
   提示限制句数和舞台指示；`polish_reply` 去掉星号动作并只留两句。
9. 纯文本里曾漏出 `neutral`、`0` 和 `affinity_reason`。命令行本来就读
   `structured_output`；`polish_reply` 再丢掉单独成行的情绪词、整数，以及与
   理由完全相同的那一行。

离线测试盖住了空口交任务、重复发奖、按 NPC 限制工具、提示里的语言规则，以及
台词发生在工具结果之后。它们不能代替再跑一次真实模型。仍需实机确认的是：中文
回复、钢剑这类请求不再扣分、Mira 的句长，以及模型是否真的等工具结果再开口、
不再口头送出没有调用工具的物品。

## 局限

- 脚本模型是关键词分支，不能代表真实模型是否稳定调用工具或遵守人设。
- 关闭异步检索后，只有 `MEMORY.md` 的索引行进入上下文。事实写在索引行里，文件正文不会自动展开。
- 同一进程内的聊天没有单独序列化。只保证游戏状态和长期记忆跨进程。
- 三名 NPC 不共享记忆，也不会互相交谈。
- 好感度是一个整数，没有事件时间线。
- 工具在 `BYPASS` 模式下执行，适合这个本地单人循环，不适合不可信环境。

## 可能的下一步

- 接 `agentscope.middleware.TTSMiddleware` 或 DashScope CosyVoice，把 `reply` 读出来。2.0.9 已有 TTS 模型类，本示例没有声音输出。
- 打开 `retrieval_async`，用真实模型从多份 `fact_N.md` 里挑选相关记忆。
- 让旅店老板的记忆对铁匠可见，做成镇上流言。
- 为真实模型补一小段人工对话记录，核对它是否在该调用工具时调用、并让好感度变化合理。
