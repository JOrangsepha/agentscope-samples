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
- 公开传闻与私人记忆分开，并有一段不调用模型的居民对白。
- 固定脚本评测可以离线跑完，也可以换真实提供方。
- 浏览器页面与命令行共用同一个 `TownSession`。

## 架构

```mermaid
flowchart TD
    CLI["main.py 命令行"] --> Session["TownSession"]
    Web["web_demo.py 浏览器"] --> Session
    Eval["eval_harness.py 固定脚本"] --> Session
    Session --> Prompt["系统提示: 人设 + 好感度 + 游戏状态 + 镇上流言"]
    Session --> Agent["agentscope.agent.Agent"]
    Prompt --> Agent
    Agent --> Model["ChatModel: DashScope / OpenAI / Ollama / mock"]
    Agent --> Act["第一次 reply: 该 NPC 的游戏工具"]
    Act --> Speak["第二次 reply: 仅 GenerateStructuredOutput"]
    Agent --> Memory["AgenticMemoryMiddleware"]
    Act --> Save["save/game_state.json"]
    Act --> Notes["save/memory/npc/Memory/MEMORY.md"]
    Session --> Gossip["save/gossip.json 公开传闻"]
    Memory --> Notes
    Gossip --> Prompt
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

在 Python 3.12.3、`agentscope==2.0.9` 上结果为 **48 passed**。覆盖：

- 人设加载，以及三份系统提示互不串人设（`test_persona.py`）
- 记忆文件跨 session 注入（`test_memory.py`）
- 工具调用改写背包、任务、金币，以及拒绝不属于该 NPC 的赠礼（`test_tools.py`）
- 情绪与好感度解析、落盘，并改变下一次问候（`test_emotion.py`）
- 两个进程的 CLI：介绍自己、拿马掌、接任务，再次启动后记得 Lira（`test_cli.py`）
- 实机里暴露的规则：空口交任务被拒绝、奖励只发一次、工具按 NPC 限制、
  语言按玩家原句写进说话提示、自报姓名会更新并写入记忆、问句不会改名、
  强烈好感变化写入记忆、台词发生在工具结果之后、只有文字的行动阶段不能
  送出物品或收钱、中文分句和引号分句、收费成功后的 “here you go” /
  “给你” 不再被当成假赠送、任务工具返回 already/cannot 时好感不能上升
  （`test_live_fixes.py`）
- 侮辱写成 “The player told Bram: …”，姓名和职业不进入；被问到消息时
  台词复述传闻；`/wait` 复述最新传闻（`test_gossip.py`）
- 网页连续两次说话仍用同一个事件循环；模型抛错时返回 JSON 500，且
  失败的那句玩家台词不留在历史里（`test_web.py`）
- 固定脚本评测在 mock 上状态、记忆、语言、人设、好感均为 1.0，且每轮
  2 次调用；“thief”、“heard you call him a stupid thief” 算作记住了
  原句（`test_eval.py`）
- 网页返回页面和账本，说话与等待走同一 `TownSession`；不存在的
  `npc_id` 返回 400，不改派给当前居民（`test_web.py`）
- 成功的赠送、收费、接受或完成至少 +1；退款后的拒绝不加这个下限；
  “hi” 即使模型给 -3 也不记传闻；写成文本的
  `GenerateStructuredOutput(...)` 不再触发第二次说话调用
  （`test_live_fixes.py`）
- 侮辱句的句号只在引号内；问候不会生成 `gossip.json`
  （`test_gossip.py`）
- “Do you remember me?” 把该 NPC 记下的名字、职业和侮辱原句写进说话
  步骤，并对玩家称 you；打听消息的注入不出现 “the player”
  （`test_memory.py`、`test_gossip.py`）

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
37457 token、输出 2219 token。16 轮延迟合计 72.3 秒，平均 4.52 秒/轮。

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
台词发生在工具结果之后。第二轮实机见下一小节。

### 第二轮（qwen-plus，commit 64c6089）

3 次来访、23 轮对话，没有异常。合计 58 次模型调用，输入 81607 token、输出
2442 token，约 2.5 次调用/轮。延迟合计 100.1 秒，平均约 4.4 秒/轮。每轮输入
约 3.5k token，比第一轮的约 2.3k 高大约 52%。多出来的主要是写进
`AgentState` 的 `SPEAK_NOW` 指令，以及行动阶段没被丢掉的台词。

这一轮已经按预期工作的部分：没有锤子时任务完不成；米拉交出锤子之后，完成
任务只发配置里的 8 枚金币（吃饭扣掉 2 枚之后是 10，发奖后是 18），再要一次
也不会再发。索要钢剑的好感变化是 0。显示出来的台词没有舞台指示。辱骂在下一
次来访被 Bram 复述。Rowan 在 “My name is Kestrel” 之后使用了这个名字。

这一轮新暴露的问题，以及这次的改动：

1. 行动阶段经常只写台词、不调用工具，说话阶段又把那句台词抄出来。Mira 说
   “Here you go!” 但没有 `give_item`，锤子没进背包；说 “That'll be three
   gold” 但没有 `charge_player`，金币仍是 12。Bram 说给了铁钉，同样没有工具
   调用。现在行动阶段必须调用工具（`tool_choice=required`），无事可做时调用
   `no_action`。说话之前会从上下文里删掉行动阶段的纯文本，只留下工具调用和
   结果。若台词仍声称送了东西或收了钱，而本轮并没有成功的 `give_item` 或
   `charge_player`，代码会换成一句如实的说明，不再另调一次模型。
2. 中文有时得到英文，切回英文后 Mira 仍用中文。原因是说话阶段最后一条用户
   消息是英文的 `SPEAK_NOW`。现在根据玩家原句里有没有汉字，在说话提示里写明
   “Reply in Simplified Chinese” 或 “Reply in English”，并把玩家原句放进该
   提示。这条指令在本轮用完后从 `AgentState` 删除，不进入之后的历史。
3. “你还记得我叫什么吗？” 把名字改成了 “什么吗”。“My name is not important”
   会得到 “Not”。问句和否定说法不再当成自我介绍，英文可以是 “Mary Ann” 这样
   的多词名字。
4. 分句正则要求标点后面有空白，所以中文三句不会被截成两句，`.”` 也不会断开。
   现在标点后可以没有空白，也可以紧跟引号。
5. 见上面的 token 数字。`SPEAK_NOW` 和行动阶段的台词不再留在历史里。
6. 中文自报姓名只改了 `player.name`，Mira 的 `MEMORY.md` 是空的。认出名字时
   代码会写一条 “The player's name is …”。

第三轮实机见下一小节。

### 第三轮（qwen-plus，commit fb3206b）

与第二轮相同的 23 轮脚本：86 次模型调用（3.7 次/轮），输入 119667 token
（约 5.2k/轮），输出 4886 token，延迟合计 182.2 秒，平均 7.9 秒/轮。另有
4 轮附加探针。整份记录是 27 轮、98 次调用、输入 132895、输出 5963、延迟
合计 278.1 秒，其中附加轮有一次 71.4 秒。对照第二轮的 58 次调用、约
3.5k 输入/轮、输出 2442、平均 4.4 秒/轮，成本和延迟都退步了。

正确性是好的：礼物和收费都走了 `give_item` / `charge_player`，该用
`no_action` 的时候用了，代码守卫 0 次替换，任务流程和单次 8 金币正确，
名字解析和 `MEMORY.md` 正确，跨会话辱骂和姓名还在，13 次语言切换里
12 次正确。

退步来自多余的模型往返，这次的改动针对这些：

1. 行动阶段 6 次调用了并不在工具列表里的 `GenerateStructuredOutput`。
   原因是历史里留着上一轮的这个调用和说出来的台词，模型跟着模仿，并且
   英文台词留在上下文里，造成了那一次中文问句得到英文回答。现在
   `AgentState` 只保留玩家原句和真正的游戏工具调用与结果。结构化输出
   调用、`SPEAK_NOW`、`ACT_NOW` 和说出来的台词都在本轮结束时删掉。
2. 情绪不在枚举里（`irritated`、`calm`）或 `affinity_delta=-15` 时，
   schema 的 `Literal` / `ge` / `le` 会在代码夹取之前拒绝，同一轮重试到
   5–6 次、约 1.1 万输入 token。schema 不再限制取值范围。代码把未知情绪
   映射到最近的允许值，并把 delta 夹到 -3..3。说话提示列出六个允许情绪
   和这个范围。
3. DashScope 不支持 `tool_choice=required`，会转成 `auto` 并打印警告，
   所以它并没有强制调用工具。行动步骤不再发送 `required`。如果第一次
   行动响应没有游戏工具结果，只再请求一次，并写明必须调用工具。
4. 工具结果返回后，行动循环还会再生成一段随后被删掉的散文。游戏工具
   一旦执行完，中间件直接结束行动循环，不再为这段散文调用模型。
5. 按句数截断会丢掉短句后面的关键信息（Mira 只剩 “Ah—yes! Bram's
   hammer.”）。现在先把很短的句子并进下一句，再按 240 个字符截断，并
   至少保留第一句。中文句子之间不再多插一个空格。
6. Rowan 的可赠送列表里去掉了镇印，`give_item` 会拒绝它。提示里写明
   锤子在任务完成前一直是丢失的，不要说它从未丢失，也不要把玩家的
   职业安到别的居民身上。

正常一轮预期是 2 次模型调用：1 次行动（响应里已经带了游戏工具）加 1 次
说话。只有第一次行动没有调用工具时才多 1 次补救。不再为工具之后的散文
或 schema 校验失败追加调用。历史不再累积结构化输出和台词，输入 token
应回到第二轮的约 3.5k/轮或更低。

第四轮实机见下一小节。

### 第四轮（qwen-plus，commit fc9817f）

与前几轮可比的是前 3 次来访、23 轮。性能修复生效：行动阶段没有
`GenerateStructuredOutput`，没有结构化输出重试，也没有 DashScope 把
`required` 转成 `auto` 的警告。27 轮语言全部正确（含附加的 4 轮）。
任务和单次 8 金币正确，跨会话记忆正确，截断没有丢掉关键句。27 轮里有
7 轮触发了一次 `ACT_NOW` 补救，多半是闲聊或问记忆。

五轮对照表在下一小节。第 4 轮另有 4 轮附加探针（8 次调用，输入 9239，
输出 599）。整份记录是 27 轮、61 次调用。

这一轮新暴露的问题，以及这次的改动：

1. Mira 成功扣了 3 枚金币，台词是 “Here you go, Kestrel — a warm bed…”。
   守卫把 “here you go” 当成赠送，换成了 “coins are unchanged”，和实际
   扣款相反。中文 “给你” 同样会误伤。收费已经成功时，这两句不再当成
   赠送。若仍然要替换，替换句会写明本轮扣过金币，不会再说金币没变。
2. 再次上交锤子时工具返回 already completed，Rowan 仍给了 +2 并写入记忆。
   Bram 只被问锤子找回来没有，就给了 +3。任务工具结果是 already 或
   cannot（没有真正接受或完成）时，代码把正的 affinity delta 压到 0。
   说话提示写明：只有本轮工具结果里发生的事才能加好感。
3. 行动提示写明闲聊和询问记忆必须调用 `no_action`，并给了一句
   “What is my name?” 的例子，用来减少补救调用。
4. 说话提示要求直接调用 `GenerateStructuredOutput`，不要先把情绪和
   好感写成纯文本。

第五轮实机见下一小节。

### 第五轮（qwen-plus，commit f0cd17c）

前 3 次来访、23 轮是目前最好的一轮：47 次调用（2.04 次/轮），输入
57414 token（约 2.5k/轮），输出 3046 token，延迟合计 108.0 秒，平均
4.7 秒，最大 6.1 秒。27 轮里只补救了 1 次，守卫 0 次，没有错误或
警告。重复上交锤子的好感是 0。另有 4 轮附加探针（9 次调用，输入
9913，输出 566）。整份记录是 27 轮、56 次调用。

四轮之后又加了第五轮（第 2–5 轮是同一份 23 轮脚本）：

| 轮次 | 轮数 | 调用 | 输入 token | 输出 token | 每轮输入 | 延迟 |
| --- | --- | --- | --- | --- | --- | --- |
| 1 | 16 | 25（1.6/轮） | 37457 | 2219 | 约 2.3k | 合计 72.3 秒，均 4.5 秒 |
| 2 | 23 | 58（2.5/轮） | 81607 | 2442 | 约 3.5k | 合计 100.1 秒，均 4.4 秒 |
| 3 | 23 | 86（3.7/轮） | 119667 | 4886 | 约 5.2k | 合计 182.2 秒，均 7.9 秒 |
| 4 | 23 | 53（2.3/轮） | 64007 | 3621 | 约 2.8k | 合计 123.7 秒，均 5.4 秒，最大 10.5 秒 |
| 5 | 23 | 47（2.0/轮） | 57414 | 3046 | 约 2.5k | 合计 108.0 秒，均 4.7 秒，最大 6.1 秒 |

这一轮新暴露的问题，以及这次的改动：

1. 提示里写死了 “Bram lost his forging hammer. It stays lost until that
   quest status is completed.” 任务完成后 Bram 仍说锤子没找回来，并把一句
   礼貌的中文提问记成 rudeness、扣了 1 点。Mira 也说锤子自从丢失后再没见过。
   这句事实改为按任务状态生成：完成后是 “The hammer was returned; the
   quest is completed.” 提示写明礼貌提问绝不是粗鲁。代码里礼貌提问也不会
   再扣好感。
2. Mira 为 “hot meal” 扣了 2 枚金币，接着又说自己不供应热饭。收费只允许
   配置里的服务（床、房间）。说话提示会写明本轮扣款的项目，并要求确认。
   若台词仍然拒绝这项服务，代码退回金币并换一句说明。
3. “米拉，能给我一条黑面包吗？” 得到了英文。玩家原句要求中文、而回复里
   没有汉字时，说话步骤会再生成一次。

### 第六轮（qwen-plus，commit 0e8a016）

同一份 13 轮评测跑了 3 次（不加 `--judge`）加 1 次带裁判。四次准确率相同：
状态 61.5%，记忆 100%，语言 100%，人设 100%，好感 76.9%，约 2.05 次调用/轮，
输入约 32.8k、输出约 1.65k，平均 4.52 秒。其中一份报告是状态 62%（8/13）、
好感 77%（10/13）、2.00 次/轮、输入 31825、输出 1615、平均 4.386 秒、最大
5.295 秒。`--judge` 为 10/13 至少 3 分。27 轮手工脚本里 `ACT_NOW` 补救
升到 8 次。第五轮已修好的收费、礼貌提问和好感、中文重试仍然成立。

这一轮的改动：

1. 主线任务在实机里完不成。玩家拿着锻造锤去找 Rowan 时，他调用
   `no_action`，理由是玩家没有锤子；Mira 也说锤子还丢着，不肯交出。
   原因是进行中的事实写着 “still missing”，和 Mira 的库存、玩家背包矛盾。
   事实现在同时看任务状态和锤子位置：还在丢失、在 Mira 手里、在玩家背包、
   已经交还。背包里有锤子时写明 “call complete_quest”。任务说明也不再写
   “vanished”。
2. 网页 `POST /api/talk` 大约隔一次就 `RuntimeError: Event loop is closed`。
   每次 `asyncio.run` 都会关掉循环，DashScope 客户端的连接还绑在旧循环上。
   现在所有请求共用一条后台事件循环（`run_coroutine_threadsafe`）。
   处理函数把异常收成 JSON 500。失败的回合如果还没有游戏工具结果，会把
   玩家那句话从历史里拿掉。
3. “my name is Kestrel. Could you give me a horseshoe?” 被解析成
   “Kestrel Could You”。名字在句号、问号、感叹号处截断。
4. 补救变多，是因为模型把提示里的 `-> no_action` 例子写成纯文本。
   行动提示改成说明该用哪个工具，不再给出可照抄的句子。
5. 传闻 “Bram finds the player rude. They said: …” 听起来像 Bram 骂了玩家。
   现在写成 “The player told Bram: '…'”。玩家打听消息时，提示要求复述
   相关传闻。
6. 评测好感与提示对齐：自我介绍期望 0（只有赠送、成功收费、接受或完成
   任务才加分）；成功收费期望为正。每一轮只检查自己造成的变化，不再用
   前面失败留下的金币总数判后面的轮次。记忆、传闻是否被听见，都看台词
   本身。人设分看台词有没有串别人的专有标记。裁判能看到回合结束后的
   游戏状态，看不到工具轨迹；报告里写明了这个限度。

### 第七轮（qwen-plus，commit b0bab17）

四次评测的状态正确率都是 100%，语言 100%，人设 100%。记忆召回是 0%：
指标要求台词里出现大小写完全一致的 “stupid thief”，以及传闻句
“The player told Bram”。Bram 实际说了 “called me a thief. I remember
both.”。裁判平均约 4.4，12/13 至少 3 分。好感平均 74.4%，最低 69.2%：
`quest_item` 四次都是 0，`quest_legit` 三次是 0，`paid_service` 三次是
0，`gift` 三次里两次是 0。前三次来访合计 23 轮：52 次调用（2.26 次/轮），
输入 71.6k（约 3.1k/轮），输出 3.3k，平均 5.2 秒，最大 7.5 秒，
`ACT_NOW` 补救 4/27。

一份不加裁判的报告：状态 100%，记忆 0%，语言 100%，人设 100%，好感
69%，2.15 次/轮，输入 35667，输出 1781，平均 4.712 秒，最大 6.835 秒。
`gift`、`quest_item`、`quest_legit`、`paid_service` 的好感为 False；
`language_zh` 3 次调用；`memory_recall` 与 `gossip_heard` 为 False，
后者 3 次调用。

带裁判的一份：12/13 至少 3 分；状态 100%，记忆 0%，语言 100%，人设
100%，好感 85%，2.38 次/轮，输入 40481，输出 1967，平均 5.436 秒，
最大 9.115 秒。`quest_fake` 4 次调用；`quest_item` 好感 False；
`quest_legit` 好感 True。

主线在命令行和网页都走通：Mira 交出锤子，Rowan 完成，8 金币只发一次。
网页连续 12 次 `POST` 都是 200，没有事件循环错误。名字解析已修好，
没有把 `no_action` 写成纯文本。下面的改动还没有再用 DashScope 跑过。

1. 记忆召回改为不区分大小写的关键词。“thief” 算作 “stupid thief”。
   “told Bram”、“said to Bram”、“player told Bram” 算作
   “The player told Bram”。
2. 本轮成功送出物品、成功收费且没有退款、或接受/完成任务时，代码把
   好感至少抬到 +1。任务工具失败仍然不能加分。退款之后不再套这个下限。
3. 网页上不存在的 `npc_id` 返回 JSON 400，不再改派给当前 NPC。好感降到
   -2 或更低、以及侮辱传闻，都要求玩家原句里有粗鲁用词。一句 “hi”
   不会被记成传闻。
4. 说话提示写明本轮刚刚交出的物品，不要说玩家本来就有。中文提示先写明
   只用简体中文，并给出锻造锤、马掌、铁钉、黑面包的译名。中文重试仍是
   后备。
5. 锤子在 Mira 手里时，只有 Mira 的提示写明位置。其他人只被告知任务
   已接受、不要说出锤子在哪。玩家带着锤子或已经交还时，Rowan 仍能看到。
   游戏状态描述不再重复位置。
6. 有传闻时，打听消息要复述最新侮辱，并说玩家告诉了谁。存储句的句号
   只留在引号内。
7. 说话步骤把写成纯文本的 `GenerateStructuredOutput(...)` 解析成工具
   调用，不再为此再调一次模型。响应里已经有真正的工具调用时不替换。

### 第八轮（qwen-plus，commit 74b0401）

四次评测和 27 轮脚本的状态正确率都是 100%，语言 100%。网页
`npc_id=nobody` 返回 400，连续 12 次有效 `POST` 都是 200。好感下限
生效：所附带裁判的那份报告好感 100%。记忆召回在该报告里是 50%
（`memory_recall` 通过，`gossip_heard` 失败）。Mira 说了 “Bram heard
you call him a stupid thief”，四次评测的 `gossip_heard` 都因此失败。
裁判 10/13 至少 3 分。该报告：2.15 次/轮，输入 35871，输出 1710，平均
4.599 秒，最大 8.261 秒；`gossip_heard` 3 次调用；`repeat_reward`
3 次调用。

前几次来访里，说话步骤把 “台词 + 情绪词 + 数字 + 理由” 写成纯文本、
因而多调一次的情况有 4 次。27 轮里 `ACT_NOW` 补救 6 次。每轮输入约
3.3k token。中文里 Kestrel 被写成 “凯斯特尔”。Mira 把侮辱说成
“Rowan reports you told Bram”，而听见的人是 Bram。`/wait` 用了
“Then the town should know.”。下面的改动还没有再用 DashScope 跑过。

1. `gossip_heard` 增加 “heard you call”、“you called him”、
   “insulted”、以及中文 “对布拉姆说”、“骂”。
2. 说话步骤也解析 “台词后面跟情绪、数字、理由” 的纯文本。解析失败
   才再调一次。
3. 行动提示写明：金币、物品、任务、记忆都不变时调用 `no_action`，
   包括重复领奖和乞讨不会给的物品。
4. 交出物品的提示改成用自己的口气说正在交给对方，不再写
   “handed it over just now” 或 “刚才我已经…了”。
5. 中文提示要求玩家姓名保持原文，不要音译。
6. 传闻句以 “Heard by 姓名” 开头，复述时必须用这个见证人。
7. 每位居民的提示都写明 Bram 是铁匠、Mira 是旅店老板、Rowan 是长老，
   不要把玩家的职业安到居民身上。`/wait` 不再说
   “Then the town should know.”；传闻说的就是听者本人时，回答
   “I was there.”
8. 缩短记忆说明、说话规则、传闻说明和工具说明。语言规则仍留在说话
   提示开头；中文物品译名只放在系统提示里。

## 评测方法与离线结果

`eval_harness.py` 用同一份 `TownSession.talk()`，不另写一套对话逻辑。
脚本固定 13 轮，顺序是：向 Bram 自报姓名与职业、要马掌、辱骂、向 Rowan
接受任务、没有锤子时声称已找到、向 Mira 领取锻造锤、交还锤子并领 8 枚
金币、再次索要奖励、向 Mira 支付床位、要求热饭（应拒绝）、一句中文、
新进程里问 Bram 是否记得自己（台词里要点出名字和辱骂）、再问 Mira
有什么消息（存档句是 `Bram heard the player say`，指标接受多种转述）。

指标都按这一份脚本计算，报告正文也写了同一套定义：

| 指标 | 怎么判 |
| --- | --- |
| 状态正确率 | 只看这一轮自己的效果：金币增减、本轮获得或失去的物品、本轮设定或保持不变的任务状态、玩家姓名。前面一轮没完成，不会因为金币总数对不上而判后面的轮次失败 |
| 记忆召回率 | 台词里要出现记住的事实，不区分大小写。玩家发问时，说话步骤会注入这些事实，所以这项量的是注入的事实有没有被说出来，和好感合理性量的是规则有没有被执行一样。“thief” 算作 “stupid thief”。打听消息那一轮的存档句是 `Bram heard the player say`；“told Bram”、“heard you call”、“you called him”、“heard the player say”、“bram heard”、“insulted” 都算。只出现在系统提示里不算 |
| 语言一致率 | `reply_language(reply)` 与玩家原句的语言相同 |
| 人设一致率 | 台词不得包含另一名 NPC 的专有标记（Bram 的锤击、Mira 的 harmless gossip、Rowan 的 town council）。旅店名 Oak and Lantern 是共用事实，不算 Mira 的专有标记。这不是文风打分。`--judge` 另按 1–5 打分，能看到回合结束后的游戏状态，看不到工具轨迹，所以一句和最终状态碰巧相符的台词仍可能得高分。3 分及以上算过。mock 跳过，且这次调用不计入每轮调用 |
| 好感合理性 | 量的是代码是否执行了规则，不是模型自己填的符号。成功赠送、收费、接受或完成才保留正分；没有这类成功工具结果时，正的 delta 被压成 0。辱骂仍要玩家原句里有粗鲁用词 |
| 调用 / token / 延迟 | 包装 `ChatModel._call_api`，按轮汇总对话调用。裁判调用不计入 |

离线命令：

```bash
cd games/game_npc_dialogue
python eval_harness.py --provider mock --out eval_reports
```

在 Python 3.12.3、`agentscope==2.0.9`、模型 `scripted-npc` 上，13 轮结果
（`Judge: skipped`）：

| 指标 | 值 |
| --- | --- |
| 状态正确率 | 100% |
| 记忆召回率 | 100% |
| 语言一致率 | 100% |
| 人设一致率 | 100% |
| 好感合理性 | 100% |
| 每轮调用 | 2.00 |
| 输入 / 输出 token | 0 / 0（脚本模型不报告用量） |
| 平均 / 最大延迟 | 0.018 秒 / 0.048 秒 |

`pytest` 里的 `test_eval.py` 断言同一组比率为 1.0 且每轮 2 次调用，因此
CI 不需要单独跑上面的命令。`eval_reports/` 已加入 `.gitignore`。
上面的第八轮数字来自 `74b0401`。第九轮跑的是 `5ec3cae`：状态、记忆、
语言都是 100%，非裁判评测约 2.05 次/轮（2.08、2.00、2.08），`ACT_NOW` 与说话重试都是 0，
平均延迟约 4.1 秒。所附带裁判的报告是状态 100%、记忆 100%、语言
100%、人设 100%、好感 92%、2.15 次/轮、输入 33265、输出 1516、平均
4.375 秒、最大 5.103 秒、裁判 9/13 至少 3 分；`repeat_reward` 的好感
为 False。这三处已经在 `ce8fda5`。

1. 传闻改成一句：`Bram heard the player say: "…"`。任务也是
   `Rowan heard the player accepted The Lost Hammer.` 玩家打听消息时，
   说话步骤写明必须复述最新公开事实；若有侮辱，连同最新侮辱一起写上，
   并说明话是玩家说的。`/wait` 直接念这句，不再套 “Heard by”。
2. 本轮没有成功的赠送、收费、接受或完成时，正的好感被压成 0。负分仍
   受粗鲁用词限制。好感合理性因此量的是这条规则，不是模型自报的符号。
3. 交出锻造锤时，说话提示写明这是 Bram 丢失的锻造锤。

第十轮网页演示跑的是 `ce8fda5`（qwen-plus）：`npc_id=nobody` 返回 400；
17 次有效 POST 都是 200，空回复 0，平均 4.1 秒，最大 4.9 秒，事件循环
关闭次数 0。`/wait` 没有 “Heard by”。Mira 对 “any news” 复述了侮辱并点名
Bram 听见。道谢的好感是 +0。Mira 交出的是 Bram 的锻造锤。还剩两处：
Rowan 被问 “has anyone complained about me?” 时把侮辱说成是骂自己；
“Thank you for the reward” 说金币是 20，当时存档是 17。
“What's new around here?” 没有带上侮辱。这三处已经在 `76ac015`。

第十一轮跑的是 `76ac015`。投诉、What's new、新闻，以及付过住宿费之后
的金币，这几项目标检查都过了。网页演示：`npc_id=nobody` 返回 400；
17 次有效 POST 都是 200，空回复 0，平均 4.0 秒，最大 4.8 秒。副作用
有三处，已经在 `c584ceb`。

1. “有什么事” 太宽。“米拉，我有什么事可以帮你吗？” 在命令行和网页都
   注入了传闻。改成 “镇上有什么事”、“最近有什么事”、“有什么新鲜事”。
   “有什么事可以帮你” 和 “有什么我能做的” 不算打听消息。
2. 对照用的金币和背包被说了出来。只问金币时也报了背包（命令行说了
   17 枚并列出旧斗篷、铁钉、黑面包和马掌；网页说了 14 枚并列出旧斗篷
   和马掌）。现在写明：只有玩家问到，或本轮有变化，才提；不要主动报背包。
3. 纯文本 `warm, 0, 没有变化` 多了一次说话调用。逗号分隔的
   `warm, 0, reason` 也要能解析。旅店名统一为 Oak and Lantern；
   命令行里 Rowan 说过 Hearthlight Inn。

第十二轮跑的是 `c584ceb`。那四项目标检查都过了。消融实验里，说话步骤
带着金币对照时，Bram 回答 “Do you remember me?” 常常漏掉侮辱
（第十二轮 1/4，第十一轮 1/2）；去掉对照后是 3/3；对照出现之前的
第十轮是 4/4。存档里的 `MEMORY.md` 仍有侮辱。命令行每轮输入升到约
2.74k。这次把对照改成只在问到金币或背包、或本轮工具改了它们时才注入。
另外中文旅店名统一为橡树与灯笼旅店，事实里写明 Rowan 不是工匠、旅店
不供热饭，听者自己的传闻改成 “I heard”。这几处还没有再用 DashScope
跑过。

第十三轮跑的是 `e079299`。记忆没有修好，而且更差：Bram 回答
“Do you remember me?” 时 0/4 提到侮辱（第十轮是 4/4）。消融实验关掉
始终改写的 “I heard” 之后，3/3 恢复成
“Kestrel. The carpenter who called me a stupid thief.”。有一次 Bram 说
“I remember the name, not the insult.”。另外 Bram 2/3 说 Rowan 做把手，
Rowan 说旅店供热饭；假锤子被说成交给 Bram；重复领奖被说成
“You now hold eight gold from that task”。

这次不再靠改提示措辞。始终开启的 “I heard” 改写已经撤掉，只留在打听
消息或抱怨时的注入里，并且改成对玩家说 “you” / “你”，要求用自己的口气
说，不要逐条念。玩家问记不记得、上次怎么说、说过什么时，说话步骤写入
该 NPC 自己存下的名字、职业和侮辱原句。说话步骤另有两行世界事实：谁
做什么、旅店只租床不供饭、锤子交给 Rowan、已经发过的奖励说
“the 8-gold reward was already paid”。第十四轮跑的是 `186de94`：
评测记忆 100%（4/4），状态和语言 100%，2.00 次/轮，平均 4.01 秒。
Bram 的 “Do you remember me?” 带上了名字和侮辱。新的问题是网页中文把
侮辱说成了夸奖，命令行 “What's new” 没有说出侮辱，世界事实里的奖励句
漏进了回忆，回忆问题触发了 3 次 `ACT_NOW`，`/wait` 仍写 “the player”。

第十五轮跑的是 `d11b944`（qwen-plus），按演示标准收尾。四次评测的
状态、语言、人设、好感都是 100%，记忆 87.5%（其中一次引用了侮辱但没有
说出名字），2.00 次/轮，每次 13 步评测输入 33.4k（约 2.57k/步），
平均 / 最大延迟 4.06 / 5.34 秒，`ACT_NOW` 为 0。命令行第 1–3 次来访：
46 次调用（2.00 次/轮），输入 61.6k（约 2.68k/轮），平均 / 最大
4.16 / 5.4 秒，补救、守卫和说话重试都是 0。网页 27 次 POST 都是 200，
平均 4.3 秒，最大 9.8 秒。还剩 3 次罕见的世界事实幻觉。中文回复有时
把英文侮辱原句嵌在句中。这两处是已知限度。中文辱骂词（糊涂、没用、慢、
蠢、难听）补进复述检查，是在这些数字量完之后。

另外两处少见的误读改成了确定规则。“has anyone complained about me”
和 “有人抱怨我” 只表示玩家在问有没有别人抱怨自己，即使镇上还没有传闻
也这么回答，不当成新的投诉或其他请求。台词里如果报了一个和存档不同的
金币总数，会再问一次，并要求说存档里的数字。这两处没有 DashScope 密钥，
没有再跑实机。

## 传闻

`gossip.py` 在 `talk()` 应用好感之后写 `save/gossip.json`，不增加模型
调用。公开的只有两类：玩家原句本身粗鲁且 `affinity_delta <= -2` 的侮辱，
以及任务被接受或完成。侮辱是一句 `Bram heard the player say: "…"`，
句号在引号内。任务是 `Rowan heard the player accepted The Lost Hammer.`
姓名、职业、金币、背包留在各自的 `MEMORY.md`。系统提示始终有
“Town rumors” 一节，原文保留第三人称。玩家打听消息、抱怨或
“what's new” 时，说话步骤另外写上必须复述的事实：最新一条公开传闻，
若另有侮辱则一并带上，并把 “the player” 改成 “you”（听者自己听见的
写成 “I heard you”）。侮辱标成辱骂、粗鲁的话，附上原句，要求引用或
贴近原意，不要改成夸奖。若台词里既没有原句片段也没有辱骂用词，会再
说一次。侮辱句写明侮辱的是听见的那位居民；当前听者不是被骂的人，除非
听者就是那位居民。
“有什么事可以帮你” 和 “有什么我能做的” 不算打听。玩家问
“remember me”、“how I spoke”、“what did I say”、“还记得我”、
“我上次怎么说” 或 “我说过什么” 时，说话步骤写入该 NPC 自己记下的
名字、职业和侮辱原句，对玩家称 “you” / “你”，并写明要说 “you said”。
金币和背包的对照只在玩家问到、或本轮工具改了金币或背包时写进说话步骤，
以免挤掉记忆。旅店中文名是橡树与灯笼旅店。说话步骤另有一行名单：
Bram 是打刀和把手的铁匠，Mira 是旅店老板、只租床，镇上不卖饭，
Rowan 是长老、没有手艺。重复领奖的 “8-gold reward was already paid”
只出现在再次完成任务的工具结果里。打听消息和回忆问题如果行动阶段
没有调用工具，代码直接记 `no_action`，不再发 `ACT_NOW`。
`/wait` 和网页上的 Wait 调用 `narrate_wait`，给玩家看的句子把
“the player” 写成 “you”，存档原文不变，不另加标签。
听者就是见证人时回答 “I was there.”，否则回答 “I'll remember that.”。
这段对白不进入模型。

## 局限

- 脚本模型是关键词分支，不能代表真实模型是否稳定调用工具或遵守人设。
- 关闭异步检索后，只有 `MEMORY.md` 的索引行进入上下文。事实写在索引行里，文件正文不会自动展开。
- 同一进程内的聊天没有单独序列化。只保证游戏状态和长期记忆跨进程。
- 公开传闻只有侮辱和任务进展。居民对白是固定句子，不是模型生成的对话。
- 好感度是一个整数，没有事件时间线。
- 工具在 `BYPASS` 模式下执行，适合这个本地单人循环，不适合不可信环境。
- 第十五轮仍有 3 次罕见的世界事实幻觉（手艺或热饭一类）。
- 中文回复有时把英文侮辱原句原样嵌进句子里。

## 可能的下一步

- 接 `agentscope.middleware.TTSMiddleware` 或 DashScope CosyVoice，把 `reply` 读出来。2.0.9 已有 TTS 模型类，本示例没有声音输出。
- 打开 `retrieval_async`，用真实模型从多份 `fact_N.md` 里挑选相关记忆。
- 第十五轮已经按演示标准收过。不再排实机轮次。留下的是上面两条已知限度。
- 为真实模型补一小段人工对话记录，核对它是否在该调用工具时调用、并让好感度变化合理。
