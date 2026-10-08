# Codex Usage HUD

Windows 桌面小窗，用来盯 Codex / ChatGPT 的 **5 小时** 和 **7 天** 额度。数字和 `codex /status`、chatgpt.com 用量页同一套接口。

另外会读本机 Codex 会话记录，显示**当前任务**（Codex 桌面端里正打开的那个对话）用了多少 token、上下文占了多少、此刻在做什么，列出最近活跃的任务及其状态，预测额度什么时候用完，任务疑似卡住或等你批准时弹通知。这部分纯本地、只读，不联网。

不爬浏览器，也不读 ChatGPT 桌面端。登录态来自本机 Codex CLI 的 `~/.codex/auth.json`。

## 需要什么

- Windows
- Python 3.10+（自带 `tkinter`，没有第三方依赖；迷你胶囊的圆角绘制用到 Pillow，可选）
- 已用 ChatGPT 账号登录 Codex CLI（`auth_mode: chatgpt`）

API Key 登录没有 5h / 7d 窗口，这个面板读不到额度。

## 怎么跑

仓库里只有一个脚本：

```bat
pythonw codex_usage_hud.py
```

也可以双击 `Start-CodexUsageHud.bat`。用 `pythonw` 不会弹出控制台。

第一次建议先在项目目录执行一次 `python codex_usage_hud.py`，方便看报错。

## 面板

| 模式 | 内容 |
| --- | --- |
| 详细 `detail` | 账号 / 套餐、5H / 7D 进度条和额度预测、当前任务 token / API 估价 / 当前动作、任务一览（可折叠）、账户积分（可折叠）、刷新间隔、置顶、同步、打开用量页 |
| 简洁 `compact` | 5H / 7D 额度、当前任务总 token、上下文占比和 API 估价、同步按钮；有情况时多一行状态（任务计数、卡住提醒、额度会在重置前用完的警告） |
| 迷你 `mini` | 胶囊条：5H / 7D 百分比 + 当前任务总 token + 展开 |

详细面板用 COMPACT / 简洁 切布局，简洁面板用 DETAIL / 详细切回来。切换会按内容改窗口大小。

详细/简洁里点 MINI / 迷你可缩成胶囊；点胶囊或 EXPAND / 展开回到上一布局。标题栏右侧可切换 EN / 中文，默认英文。中文界面用微软雅黑。语言会写进配置。

额度颜色：正常青色，≥70% 琥珀色，≥90% 红色。

### 当前任务

5H / 7D 下面多一张「当前任务 // 本地会话」卡片：

- 任务名 + 开始时间 + 当前模型（任务名取自 `~/.codex/session_index.jsonl`；没有时显示会话 id 末 8 位）
- 总计 / 输入 / 输出 token
- 缓存命中：已缓存的输入 token 和占比，以及推理 token
- API 估价：按 OpenAI API 标价折算的美元金额，以及输入 / 缓存 / 输出三部分（见下文）
- 上下文：最近一轮的输入 token ÷ 模型上下文窗口，进度条颜色规则同上
- 距离 Codex 上一次写入 token 统计过了多久

数字格式如 `2.78M`、`19.4K`。简洁模式只显示总计和上下文；迷你胶囊多一段 `TOK` / `总量`，即当前任务的总 token（`total_tokens`，不套用百分比颜色，没数据时显示 `—`；可用 `mini_task_tokens` 关掉）。

“当前任务”怎么定：

1. **跟随桌面端选中的任务**：读 Codex 桌面端日志 `%LOCALAPPDATA%\Codex\Logs\YYYY\MM\DD\codex-desktop-*.log`（应用商店 / MSIX 版在 `%LOCALAPPDATA%\Packages\OpenAI.Codex_*\LocalCache\Local\Codex\Logs\`，两种都会自动识别），找最近一次切换对话留下的记录（`ownerRoutePath=/local/<任务 id>`，以及 `thread_stream_view_activity_changed active=true conversationId=<任务 id>`），再按 id 找到 `~/.codex/sessions/**/rollout-*-<任务 id>.jsonl`。在桌面端点到别的任务，下一次本地刷新（默认 4 秒内）就会切过去，只是查看、没发消息也行。
2. **兜底：最近写入的任务**：日志里找不到、当前页面不是某个任务（比如新对话页），或者这个任务还没有 rollout 文件时，用 `~/.codex/sessions/` 下最近有写入的 `rollout-*.jsonl`。

选中的任务如果还没有 `token_count`，卡片只显示任务名和“暂无 token 数据”，不会沿用上一个任务的数字。

rollout 里取最后一条 `token_count` 事件。大文件只从尾部往回读，之后只读新增的字节；日志文件同理。读取都在后台线程，不卡界面。Windows 在文件被占用期间经常不更新修改时间和目录里的大小，所以会直接 `stat` 文件、并参考文件末尾那条记录的时间戳。

### 当前在做什么

当前任务卡片里多一行 `NOW ▸` / `当前 ▸`，显示所选任务此刻的动作和持续时间，例如：

- `$ git status --short` 正在执行的命令（长路径缩成文件名）
- `修改 codex_usage_hud.py` 正在打补丁（apply_patch）
- `搜索 …` / `查看图片 …` / `浏览器 …` / `工具 mcp.xxx` 网页搜索、看图、浏览器、MCP 工具
- `思考中` / `思考中（上一步 …）` 模型在推理，或上一步工具已返回、在等模型下一步
- `撰写回复`、`等待你批准 / 回复`、`自动审批审核中`、`已完成 · 最后 …`

来源是 rollout 的最新事件：`response_item` 里的 `custom_tool_call`（Codex 代码模式 `exec`，从 JS 里解析 `tools.exec_command({cmd})`、`tools.apply_patch("*** Update File: …")`、`tools.web__run` 等）和 `function_call`，没有对应 `*_call_output` 的调用算“正在执行”；`reasoning`、`message`、`event_msg/item_completed` 的 `Reasoning` 摘要用于“思考中”。

### 任务一览

详细模式里「任务一览 // TASKS」（点标题折叠 / 展开），最多 `task_list_max`（默认 4）行：状态、任务名、距 rollout 最后一条事件多久。标题右侧是计数，如 `1 运行 · 1 待批准`（只统计列出的行）；简洁模式把这串计数放在卡片下方。

入选和排序规则：

1. **当前任务永远在第一行**：HUD 正在显示的任务（桌面端选中的对话，或在任务一览里手动选的那个）固定置顶，前面带 ▶、整行高亮，不受 `task_list_hours` 和行数限制，占一行名额。
2. **其余按“最后活跃时间”排**：最后活跃 = max(rollout 最后一条事件的时间, 桌面端日志里最后一次打开这个任务的时间)。所以只是在桌面端点开看过的旧任务也会排上来。桌面端日志的打开记录来自 `ownerRoutePath=/local/<id>` 和 `thread_stream_view_activity_changed active=true conversationId=<id>`，启动时扫描最近两个日期目录里每个日志文件末尾最多 24 MB，之后增量读取。
3. **运行 / 待批准优先**：在置顶行之后，先放运行、待批准的任务，再放完成、空闲的；同组内按最后活跃时间从新到旧。
4. 最后活跃时间超过 `task_list_hours`（默认 6）小时的任务不列出（当前任务除外）；子会话默认不列出。

状态规则（实测 Codex 桌面端 26.928 的 rollout 得出）：

| 状态 | 判定 |
| --- | --- |
| 运行 RUN | 最近一次 `event_msg/task_started` 之后还没有 `task_complete` / `turn_aborted`，且最后一条事件在 `idle_minutes` 以内 |
| 待批准 WAIT | 运行中，并且：桌面端日志出现 `[desktop-notifications] show notification conversationId=<id> kind=permission`（或 `question`）且不早于 rollout 最后一条事件；或有未返回的 `request_user_input` 调用；或这个任务的 `guardian_review` 子会话正在跑（自动审批在审核） |
| 完成 DONE | 最后一轮已 `task_complete`（或 `turn_aborted`），最后写入在 `idle_minutes` 以内 |
| 空闲 IDLE | `idle_minutes`（默认 30）分钟内没有新写入；包括轮次没结束但长时间不动（例如被强制关掉） |
| 卡住 STUCK | 运行中的附加标记，见下文“卡住提醒” |

说明：审批请求本身不会写进 rollout（这台机器用的是 `approvals_reviewer=auto_review`，自动审批由 `thread_source=guardian_review` 的子会话完成），所以“待批准”靠桌面端日志和 guardian 子会话推断。guardian / 子代理会话（`session_meta` 里有 `parent_thread_id` 或 `source.subagent`）默认不列出，`task_list_show_subagents` 可打开。

点击行：

- **单击**：用 `codex://threads/<任务 id>` 在 Codex 桌面端打开这个任务（Codex 的 MSIX 包注册了 `codex:` 协议，桌面端把 `threads/<id>` 路由到本地对话），同时 HUD 立刻显示它；桌面端随后切过去，HUD 继续跟随。
- **右键**：只在 HUD 里显示这个任务，不动 Codex。在桌面端再切换对话后恢复跟随。
- `task_click_action` 设为 `select` 时单击也只在 HUD 里显示。

### 卡住提醒与通知

运行中的任务满足任一条件就标为“卡住”，卡片里出现红 / 琥珀色提示行，任务一览里状态变成 `卡住`：

- **长时间没动静**：`stuck_minutes`（默认 5）分钟没有任何新事件。跑很久的构建 / 测试命令也会触发，属正常提示。
- **重复失败**：本轮最近 `stuck_repeat_count`（默认 3）条命令结果（`item_completed` 里的 `CommandExecution`）全部失败（`status=failed` 或 `exit_code≠0`），并且命令相同，或者错误信息的关键行（含 error / cannot / not found 等的第一行，数字归一化）相同。

通知（Windows 通知中心 toast）：

- 任务变为“卡住”时（`notify_stuck`，默认开）
- 任务开始等你批准 / 回答时（`notify_approval`，默认开；自动审批审核中不通知）
- 任务完成时（`notify_complete`，默认关，Codex 自己也会发完成通知）
- 同一任务同一类通知在 `notify_cooldown_min`（默认 10）分钟内只发一次；HUD 刚启动的第一轮只记录状态、不补发

toast 用系统自带的 `powershell.exe` 调 WinRT `ToastNotificationManager`，借用 Windows PowerShell 已注册的 AppUserModelID，不装任何模块；发送在后台线程，约 0.4 秒。失败时（比如系统禁用了通知）改为在屏幕右下角弹一个 HUD 自己的小窗，10 秒后自动消失，点一下也会关。`notify_method` 可设 `popup`（只用小窗）或 `off`。

### 额度预测

详细模式的 5H / 7D 卡片各多一行 `EST` / `预测`：

- **消耗速度**：5H 用最近 `forecast_lookback_min`（默认 60）分钟内的用量变化算每小时涨多少；7D 用最近 24 小时，按“每天”显示。样本来自联网同步的结果，以及本机 rollout 里每条 `token_count` 附带的 `rate_limits`（`used_percent`、`resets_at`，带时间戳；只取同一个重置周期内的样本）。近期样本跨度不够（5H 少于 10 分钟、7D 少于 3 小时）时，用“本窗口已用 ÷ 窗口已过去的时间”估算，数字后面带 `~`。
- **什么时候用满**：按这个速度算出到 100% 的时间，和卡片上的重置时间比较。会在重置前用完时整行变琥珀色（1 小时内用完变红）并标 `⚠ 早于重置`；不会就显示“重置在先”；最近没涨显示“未增长”；数据不足显示 `—`。
- **还够几轮**：5H 那行末尾的“约剩 N 轮（x%/轮）”：取 HUD 当前显示任务最近最多 5 个已完成轮次，每轮 5H 用量 = 轮次结束时的 `used_percent` − 轮次开始前最后一个样本，取平均，再用剩余额度除以它。
- 简洁模式只在“会在重置前用完”时显示一行警告，例如 `⚠ 7天额度约 周六 03:10 用完，早于重置 周日 07:49`。
- 时间都是本机时间：当天只显示时分，一周内显示星期，再远显示月-日。

注意：5H / 7D 是账号级额度，几个任务同时跑时，“每轮用量”会把别的任务的消耗算进来，偏高；用量百分比是整数，短时间内的速度会有跳动。

### API 估价（美元）

**只是估算。** Plus / Pro 等 ChatGPT 订阅用 Codex 不按 token 扣钱，这里的金额表示“同样的 token 如果走 OpenAI API 标准价大概要多少钱”，方便比较任务轻重。

算法：

- 模型取 rollout 里 `turn_context` 行的 `model`。任务中途换模型时，每条 `token_count` 相对上一条的 `total_token_usage` 增量记到当时生效的模型上（增量和 `last_token_usage` 一致；重复上报的同一条不会重复计）。
- 未缓存输入 = `input_tokens - cached_input_tokens - cache_write_input_tokens`，按输入价；`cached_input_tokens` 按缓存价；`cache_write_input_tokens` 按缓存写入价（该模型没公布写入价时按输入价）；`output_tokens` 按输出价。`reasoning_output_tokens` 已经包含在 `output_tokens` 里，不重复计。
- 单次请求输入超过 272K token 时按官方规则计 2 倍输入 / 缓存价、1.5 倍输出价（Codex 的上下文窗口约 258K，一般碰不到）。
- 推理强度（effort）不单独计价，只体现在 token 数量上。
- 标准档（Standard）价格；不含 Batch / Flex 折扣、Fast 模式加价、区域处理 10% 加价、工具调用费。
- 没有官方 API 价格的模型不估算：显示 `—`，部分未知时总价后面加 `+?`。例如 `codex-auto-review`（Codex 内部自动审查用的代号）和自定义 provider 的模型。

内置价格（美元 / 每 100 万 token，标准档短上下文），来源 [developers.openai.com/api/docs/pricing](https://developers.openai.com/api/docs/pricing) 和各模型页 `developers.openai.com/api/docs/models/<模型>`，2026-09-30 核对：

| 模型 | 输入 | 缓存输入 | 缓存写入 | 输出 |
| --- | --- | --- | --- | --- |
| gpt-6.1-sol | 2.00 | 0.10 | 2.50 | 10.00 |
| gpt-6-astra | 10.00 | 1.00 | 12.50 | 50.00 |
| gpt-6-luna | 0.10 | 0.01 | 0.125 | 0.50 |
| gpt-5.6-sol | 4.00 | 0.40 | 5.00 | 20.00 |
| gpt-5.6-terra | 2.00 | 0.20 | 2.50 | 12.00 |
| gpt-5.6-luna | 0.20 | 0.02 | 0.25 | 1.20 |
| gpt-5.5 | 5.00 | 0.50 | （按输入价） | 30.00 |
| gpt-5.4 | 2.50 | 0.25 | （按输入价） | 15.00 |
| gpt-5.4-mini | 0.75 | 0.075 | （按输入价） | 4.50 |

gpt-5.6-sol 是官方标注的促销价（至少到 2026-11-21）。价格会变，以官网为准；可以在配置里用 `model_prices` 覆盖或补充。

## 配置

文件：`%USERPROFILE%\.codex_usage_hud.json`

```json
{
  "refresh_sec": 60,
  "topmost": true,
  "mode": "detail",
  "lang": "en",
  "local_refresh_sec": 4,
  "mini_task_tokens": true,
  "task_follow_selected": true,
  "forecast_enabled": true,
  "forecast_lookback_min": 60,
  "task_list_enabled": true,
  "task_list_max": 4,
  "task_list_hours": 6,
  "task_list_show_subagents": false,
  "task_click_action": "open",
  "idle_minutes": 30,
  "stuck_minutes": 5,
  "stuck_repeat_count": 3,
  "notify_enabled": true,
  "notify_method": "toast",
  "notify_stuck": true,
  "notify_approval": true,
  "notify_complete": false,
  "notify_cooldown_min": 10,
  "model_prices": {
    "codex-auto-review": { "input": 0.20, "cached_input": 0.02, "output": 1.20 }
  }
}
```

- `refresh_sec`：自动同步间隔，5–3600 秒，默认 60
- `topmost`：是否置顶（面板上的 PIN）
- `mode`：`detail` / `compact` / `mini`
- `expand_mode`：从迷你展开时回到 `compact` 或 `detail`
- `lang`：`en`（默认）或 `zh`
- `local_refresh_sec`：本地任务 token 的读取间隔，1–60 秒，默认 4，和联网同步互不影响
- `mini_task_tokens`：迷你胶囊是否显示当前任务总 token，默认 `true`（旧键名 `mini_task_ctx` 仍然认，保存配置时会改写成新键名）
- `task_follow_selected`：当前任务是否跟随桌面端选中的对话，默认 `true`；设为 `false` 则只看最近写入的 rollout
- `codex_logs_dir`：桌面端日志目录，字符串或字符串列表，默认不填，自动识别普通安装版和应用商店版的日志目录；自动识别不到时再手动指定。也可以用环境变量 `CODEX_HUD_LOGS_DIR` 指定（多个用 `;` 分隔），优先级最高
- `forecast_enabled`：5H / 7D 卡片里的额度预测行，默认 `true`
- `forecast_lookback_min`：5H 消耗速度看最近多少分钟，10–300，默认 60
- `task_list_enabled`：详细模式的任务一览，默认 `true`
- `task_list_max`：任务一览最多几行，1–10，默认 4
- `task_list_hours`：最后活跃（写入或在桌面端打开）在多少小时以内的任务才列出，1–72，默认 6；当前任务总是列出
- `task_list_show_subagents`：是否列出 guardian 审核 / 子代理会话，默认 `false`
- `task_click_action`：单击任务行 `open`（在 Codex 打开，默认）或 `select`（只在 HUD 显示）
- `idle_minutes`：多少分钟没写入算“空闲”，5–1440，默认 30
- `stuck_minutes`：运行中多少分钟没有新事件算“卡住”，1–240，默认 5
- `stuck_repeat_count`：最近几条命令相同失败算“卡住”，2–10，默认 3
- `notify_enabled`：总开关，默认 `true`
- `notify_method`：`toast`（系统通知，失败时退回小窗，默认）、`popup`（只用 HUD 小窗）、`off`
- `notify_stuck` / `notify_approval` / `notify_complete`：卡住 / 等待批准 / 完成时是否通知，默认 开 / 开 / 关
- `notify_cooldown_min`：同一任务同类通知的最短间隔，1–240 分钟，默认 10
- `section_tasks_open` / `section_account_open`：任务一览、账户积分两个折叠区是否展开（点标题切换时自动保存），默认 展开 / 折叠
- `model_prices`：可选，覆盖或补充模型价格（美元 / 每 100 万 token）。每个模型写 `input`、`output`，可选 `cached_input`（默认同输入价）、`cache_write`（默认按输入价）、`long_context`（默认 `true`，是否套用 >272K 加价）。上面的 `codex-auto-review` 只是写法示例，不是官方价格

登录文件 `~/.codex/auth.json` 不要放进仓库。

## 数据从哪来

`GET https://chatgpt.com/backend-api/wham/usage`

请求头带 Codex 的 access token 和 `chatgpt-account-id`。401 时用 refresh token 走 `https://auth.openai.com/oauth/token` 续期，并写回 `auth.json`。

Codex 要两边都有剩余额度才能继续用：5h 和 7d 任一打满都会卡住。

当前任务的 token 来自本地文件，不发请求：

- `~/.codex/sessions/**/rollout-*.jsonl` 里 `type=event_msg`、`payload.type=token_count` 的行（`info.total_token_usage`、`info.last_token_usage`、`info.model_context_window`）
- `~/.codex/session_index.jsonl` 里的 `thread_name`
- `%LOCALAPPDATA%\Codex\Logs\`（应用商店版为 `%LOCALAPPDATA%\Packages\OpenAI.Codex_*\LocalCache\Local\Codex\Logs\`）下桌面端日志里的对话切换记录（只读，用来判断当前选中的任务）和 `kind=permission|question` 的通知记录（判断“待批准”）
- rollout 里的 `task_started` / `task_complete` / `turn_aborted`、`custom_tool_call` / `function_call` 及其输出、`item_completed`（命令结果、推理摘要）和 `token_count.rate_limits`（任务状态、当前动作、卡住判定、额度预测）
- 打开任务用 `codex://threads/<id>`，只是让系统把链接交给 Codex 桌面端

## 文件

```
codex_usage_hud.py       主程序
Start-CodexUsageHud.bat  无控制台启动
```
