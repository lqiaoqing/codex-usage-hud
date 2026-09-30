# Codex Usage HUD

Windows 桌面小窗，用来盯 Codex / ChatGPT 的 **5 小时** 和 **7 天** 额度。数字和 `codex /status`、chatgpt.com 用量页同一套接口。

另外会读本机 Codex 会话记录，显示**当前任务**（Codex 桌面端里正打开的那个对话）用了多少 token、上下文占了多少。这部分纯本地，不联网。

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
| 详细 `detail` | 账号 / 套餐、5H / 7D 进度条、当前任务 token、credits、刷新间隔、置顶、同步、打开用量页 |
| 简洁 `compact` | 5H / 7D 额度、当前任务总 token 和上下文占比、同步按钮 |
| 迷你 `mini` | 胶囊条：5H / 7D 百分比 + 当前任务上下文百分比 + 展开 |

详细面板用 COMPACT / 简洁 切布局，简洁面板用 DETAIL / 详细切回来。切换会按内容改窗口大小。

详细/简洁里点 MINI / 迷你可缩成胶囊；点胶囊或 EXPAND / 展开回到上一布局。标题栏右侧可切换 EN / 中文，默认英文。中文界面用微软雅黑。语言会写进配置。

额度颜色：正常青色，≥70% 琥珀色，≥90% 红色。

### 当前任务

5H / 7D 下面多一张「当前任务 // 本地会话」卡片：

- 任务名 + 开始时间（任务名取自 `~/.codex/session_index.jsonl`；没有时显示会话 id 末 8 位）
- 总计 / 输入 / 输出 token
- 缓存命中：已缓存的输入 token 和占比，以及推理 token
- 上下文：最近一轮的输入 token ÷ 模型上下文窗口，进度条颜色规则同上
- 距离 Codex 上一次写入 token 统计过了多久

数字格式如 `2.78M`、`19.4K`。简洁模式只显示总计和上下文；迷你胶囊多一个上下文百分比（可用 `mini_task_ctx` 关掉）。

“当前任务”怎么定：

1. **跟随桌面端选中的任务**：读 Codex 桌面端日志 `%LOCALAPPDATA%\Codex\Logs\YYYY\MM\DD\codex-desktop-*.log`，找最近一次切换对话留下的记录（`ownerRoutePath=/local/<任务 id>`，以及 `thread_stream_view_activity_changed active=true conversationId=<任务 id>`），再按 id 找到 `~/.codex/sessions/**/rollout-*-<任务 id>.jsonl`。在桌面端点到别的任务，下一次本地刷新（默认 4 秒内）就会切过去，只是查看、没发消息也行。
2. **兜底：最近写入的任务**：日志里找不到、当前页面不是某个任务（比如新对话页），或者这个任务还没有 rollout 文件时，用 `~/.codex/sessions/` 下最近有写入的 `rollout-*.jsonl`。

选中的任务如果还没有 `token_count`，卡片只显示任务名和“暂无 token 数据”，不会沿用上一个任务的数字。

rollout 里取最后一条 `token_count` 事件。大文件只从尾部往回读，之后只读新增的字节；日志文件同理。读取都在后台线程，不卡界面。Windows 在文件被占用期间经常不更新修改时间和目录里的大小，所以会直接 `stat` 文件、并参考文件末尾那条记录的时间戳。

## 配置

文件：`%USERPROFILE%\.codex_usage_hud.json`

```json
{
  "refresh_sec": 60,
  "topmost": true,
  "mode": "detail",
  "lang": "en",
  "local_refresh_sec": 4,
  "mini_task_ctx": true,
  "task_follow_selected": true
}
```

- `refresh_sec`：自动同步间隔，5–3600 秒，默认 60
- `topmost`：是否置顶（面板上的 PIN）
- `mode`：`detail` / `compact` / `mini`
- `expand_mode`：从迷你展开时回到 `compact` 或 `detail`
- `lang`：`en`（默认）或 `zh`
- `local_refresh_sec`：本地任务 token 的读取间隔，1–60 秒，默认 4，和联网同步互不影响
- `mini_task_ctx`：迷你胶囊是否显示当前任务上下文百分比，默认 `true`
- `task_follow_selected`：当前任务是否跟随桌面端选中的对话，默认 `true`；设为 `false` 则只看最近写入的 rollout

登录文件 `~/.codex/auth.json` 不要放进仓库。

## 数据从哪来

`GET https://chatgpt.com/backend-api/wham/usage`

请求头带 Codex 的 access token 和 `chatgpt-account-id`。401 时用 refresh token 走 `https://auth.openai.com/oauth/token` 续期，并写回 `auth.json`。

Codex 要两边都有剩余额度才能继续用：5h 和 7d 任一打满都会卡住。

当前任务的 token 来自本地文件，不发请求：

- `~/.codex/sessions/**/rollout-*.jsonl` 里 `type=event_msg`、`payload.type=token_count` 的行（`info.total_token_usage`、`info.last_token_usage`、`info.model_context_window`）
- `~/.codex/session_index.jsonl` 里的 `thread_name`
- `%LOCALAPPDATA%\Codex\Logs\` 下桌面端日志里的对话切换记录（只读，用来判断当前选中的任务）

## 文件

```
codex_usage_hud.py       主程序
Start-CodexUsageHud.bat  无控制台启动
```
