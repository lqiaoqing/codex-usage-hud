# Codex Usage HUD

Windows 桌面小窗，用来盯 Codex / ChatGPT 的 **5 小时** 和 **7 天** 额度。数字和 `codex /status`、chatgpt.com 用量页同一套接口。

不爬浏览器，也不读 ChatGPT 桌面端。登录态来自本机 Codex CLI 的 `~/.codex/auth.json`。

## 需要什么

- Windows
- Python 3.10+（自带 `tkinter`，没有第三方依赖）
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
| 详细 `detail` | 账号 / 套餐、5H / 7D 进度条、credits、刷新间隔、置顶、同步、打开用量页 |
| 简洁 `compact` | 只留 5H / 7D 额度和同步按钮 |

详细面板右下角 `COMPACT` 切到简洁；简洁面板 `DETAIL` 切回来。切换会按内容改窗口大小，选择会写进配置。

额度颜色：正常青色，≥70% 琥珀色，≥90% 红色。

## 配置

文件：`%USERPROFILE%\\.codex_usage_hud.json`

```json
{
  "refresh_sec": 60,
  "topmost": true,
  "mode": "detail"
}
```

- `refresh_sec`：自动同步间隔，5–3600 秒，默认 60
- `topmost`：是否置顶（面板上的 PIN）
- `mode`：`detail` 或 `compact`

登录文件 `~/.codex/auth.json` 不要放进仓库。

## 数据从哪来

`GET https://chatgpt.com/backend-api/wham/usage`

请求头带 Codex 的 access token 和 `chatgpt-account-id`。401 时用 refresh token 走 `https://auth.openai.com/oauth/token` 续期，并写回 `auth.json`。

Codex 要两边都有剩余额度才能继续用：5h 和 7d 任一打满都会卡住。

## 文件

```
codex_usage_hud.py       主程序
Start-CodexUsageHud.bat  无控制台启动
```
