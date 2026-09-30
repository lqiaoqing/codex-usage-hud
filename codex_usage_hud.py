"""Codex Usage HUD — geek-styled always-on-top widget for 5h / 7d quotas."""
from __future__ import annotations

import bisect
import ctypes
import json
import math
import os
import re
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import webbrowser
from datetime import datetime, timezone
from pathlib import Path
import tkinter as tk
from tkinter import font as tkfont

try:
    from PIL import Image, ImageChops, ImageDraw, ImageFont, ImageTk

    HAS_PIL = True
except Exception:
    HAS_PIL = False

CODEX_HOME = Path.home() / ".codex"
AUTH_PATH = CODEX_HOME / "auth.json"
CONFIG_PATH = Path.home() / ".codex_usage_hud.json"
HWND_PATH = Path.home() / ".codex_usage_hud.hwnd"
SESSIONS_DIR = CODEX_HOME / "sessions"
SESSION_INDEX_PATH = CODEX_HOME / "session_index.jsonl"
ARCHIVED_SESSIONS_DIR = CODEX_HOME / "archived_sessions"
CODEX_LOGS_DIR = Path(os.environ.get("LOCALAPPDATA") or (Path.home() / "AppData" / "Local")) / "Codex" / "Logs"
SINGLETON_MUTEX = "Local\\CodexUsageHud.SingleInstance"
USAGE_URL = "https://chatgpt.com/backend-api/wham/usage"
USAGE_PAGE = "https://chatgpt.com/codex/settings/usage"

BG = "#05070a"
PANEL = "#0b1018"
PANEL2 = "#101826"
LINE = "#1c2a3a"
CYAN = "#3ee0c6"
CYAN_DIM = "#1a8f80"
SEL_BG = "#0c2226"  # task list: row of the selected task
AMBER = "#f0b429"
RED = "#ff5c7a"
TEXT = "#d7e2ee"
MUTED = "#7b8ea3"
GRID = "#12202e"
CHROMA = "#ff00ff"  # mini capsule transparency key (Windows)

DEFAULT_REFRESH_SEC = 60
MIN_REFRESH_SEC = 5
MAX_REFRESH_SEC = 3600
DEFAULT_LOCAL_REFRESH_SEC = 4
MIN_LOCAL_REFRESH_SEC = 1
MAX_LOCAL_REFRESH_SEC = 60
ROLLOUT_RESCAN_SEC = 20  # re-list sessions dir for newly created tasks
ROLLOUT_CANDIDATES = 6  # newest-by-mtime + newest-by-name rollouts to watch
ROLLOUT_TAIL_CHUNK = 256 * 1024
ROLLOUT_TAIL_LIMIT = 32 * 1024 * 1024  # max bytes scanned backwards per task switch
ROLLOUT_ID_RESCAN_SEC = 3  # min gap between full rescans when a selected id has no rollout yet
LOG_DAY_DIRS = 2  # newest Codex desktop log day folders to watch
LOG_TAIL_LIMIT = 16 * 1024 * 1024  # max bytes scanned backwards per log file on first sight
LOG_HISTORY_LIMIT = 24 * 1024 * 1024  # per log file, once: "last viewed" times for the task list
ROLLOUT_FULL_SCAN_LIMIT = 512 * 1024 * 1024  # bigger rollouts: usage from the tail only, cost unknown
ROLLOUT_READ_CHUNK = 8 * 1024 * 1024

# Estimated task cost at OpenAI API list prices (Standard tier, short context), USD per 1M tokens.
# Source: https://developers.openai.com/api/docs/pricing and the model pages
# https://developers.openai.com/api/docs/models/<model-id>, checked 2026-09-30.
# cache_write=None: no separate cache-write price published -> cache writes billed as normal input.
# long_context: requests with > LONG_CONTEXT_TOKENS input are billed 2x input/cache and 1.5x output.
# Not listed on purpose (no official API price): codex-auto-review (internal Codex slug), custom
# providers. Override or add models via "model_prices" in the config file.
MODEL_PRICES = {
    "gpt-6.1-sol": {"input": 2.00, "cached_input": 0.10, "cache_write": 2.50, "output": 10.00, "long_context": True},
    "gpt-6-astra": {"input": 10.00, "cached_input": 1.00, "cache_write": 12.50, "output": 50.00, "long_context": True},
    "gpt-6-luna": {"input": 0.10, "cached_input": 0.01, "cache_write": 0.125, "output": 0.50, "long_context": True},
    # GPT-5.6 Sol: promotional price, "available at least through November 21, 2026".
    "gpt-5.6-sol": {"input": 4.00, "cached_input": 0.40, "cache_write": 5.00, "output": 20.00, "long_context": True},
    "gpt-5.6-terra": {"input": 2.00, "cached_input": 0.20, "cache_write": 2.50, "output": 12.00, "long_context": True},
    "gpt-5.6-luna": {"input": 0.20, "cached_input": 0.02, "cache_write": 0.25, "output": 1.20, "long_context": True},
    "gpt-5.5": {"input": 5.00, "cached_input": 0.50, "cache_write": None, "output": 30.00, "long_context": True},
    "gpt-5.4": {"input": 2.50, "cached_input": 0.25, "cache_write": None, "output": 15.00, "long_context": True},
    "gpt-5.4-mini": {"input": 0.75, "cached_input": 0.075, "cache_write": None, "output": 4.50, "long_context": False},
}
MODEL_PRICES_SOURCE = "developers.openai.com/api/docs/pricing (2026-09-30)"
LONG_CONTEXT_TOKENS = 272_000
LONG_CONTEXT_INPUT_MULT = 2.0
LONG_CONTEXT_OUTPUT_MULT = 1.5

STRINGS = {
    "en": {
        "window_title": "CODEX // USAGE",
        "brand": "CODEX.USAGE",
        "boot": "boot sequence...",
        "syncing": "syncing telemetry...",
        "online": "SYS.ONLINE",
        "interval": "INTERVAL={sec}s",
        "card5": "PRIMARY  //  5H WINDOW",
        "card7": "SECONDARY  //  7D WINDOW",
        "card5_compact": "5H",
        "card7_compact": "7D",
        "awaiting": "awaiting telemetry",
        "refresh_label": "REFRESH.SEC",
        "apply": "APPLY",
        "pin": "PIN",
        "sync_now": "SYNC NOW",
        "open_usage": "OPEN USAGE",
        "compact": "COMPACT",
        "detail": "DETAIL",
        "mini": "MINI",
        "expand": "EXPAND",
        "sync": "SYNC",
        "lang_switch": "中文",
        "used_line": "USED {used:.1f}%   RESET IN {reset}",
        "fault": "FAULT // {msg}",
        "meta_ok": "{email}   PLAN={plan}   STATE={status}   @{checked_at}",
        "state_ok": "OK",
        "state_limit": "LIMIT",
        "extra": (
            "credits.balance        {balance}\n"
            "credits.has            {has_credits}   unlimited={unlimited}\n"
            "banked.resets          {banked_resets}   applicable={banked_applicable}\n"
            "reached.type           {reached_type}\n"
            "source                 chatgpt.com/backend-api/wham/usage\n"
            "note                   5h AND 7d must both have remaining quota"
        ),
        "err_missing_auth": "MISSING ~/.codex/auth.json — login Codex first",
        "err_apikey": "API-key login has no ChatGPT 5h/7d windows",
        "err_missing_tokens": "auth.json missing access_token / account_id",
        "err_expired": "session expired — re-login Codex",
        "err_refresh": "refresh failed: {err}",
        "err_http": "HTTP {code}: {detail}",
        "err_request": "request failed: {err}",
        "task_card": "TASK  //  CURRENT SESSION",
        "task_card_compact": "TASK",
        "task_none": "no local Codex session found",
        "task_wait": "no token data yet",
        "task_title_line": "{title}   @{started}   {model}",
        "task_tokens_line": "TOTAL {total}   IN {inp}   OUT {out}",
        "task_cache_line": "CACHED {cached} ({hit:.1f}%)   REASONING {reasoning}",
        "task_ctx_line": "CTX {ctx} / {window}   UPDATED {age} AGO",
        "task_compact_line": "TOTAL {total}   CTX {ctx} / {window}   ≈{cost}",
        "task_cost_line": "API EST ≈ {total}   IN {inp}  CACHED {cached}  OUT {out}",
        "task_cost_unknown": "API EST —   no API price for {models}",
        "task_cost_na": "API EST —",
        "mini_tokens": "TOK",
        "sec_tasks": "TASKS  //  RECENT",
        "sec_account": "ACCOUNT  //  CREDITS",
        "win5": "5H",
        "win7": "7D",
        "rate_h": "{v:.1f}%/h",
        "rate_d": "{v:.1f}%/d",
        "fc_runs_out": "EST {rate}{basis} → 100% @{eta}  ⚠ BEFORE RESET",
        "fc_ok": "EST {rate}{basis} → 100% @{eta}, resets first",
        "fc_flat": "EST not rising — lasts until reset",
        "fc_na": "EST —  not enough data yet",
        "fc_reset": "EST window reset — waiting for data",
        "fc_basis_window": "~",
        "fc_turns": "   ≈{left} turns @{avg:.1f}%",
        "fc_turns_inf": "   ~0%/turn",
        "fc_warn_compact": "⚠ {win} runs out ~{eta}, before reset {reset}",
        "st_running": "RUN",
        "st_waiting": "WAIT",
        "st_done": "DONE",
        "st_idle": "IDLE",
        "st_stuck": "STUCK",
        "cnt_running": "{n} running",
        "cnt_waiting": "{n} waiting",
        "cnt_stuck": "{n} stuck",
        "cnt_done": "{n} done",
        "tasks_none": "no task active in the last {h}h",
        "tasks_hint": "click: open in Codex   right-click: show here",
        "tasks_hint_select": "click: show this task here",
        "act_prefix": "NOW",
        "act_cmd": "$ {text}",
        "act_patch": "patch {text}",
        "act_web": "web {text}",
        "act_image": "view image {text}",
        "act_stdin": "polling process output",
        "act_tool": "tool {text}",
        "act_browser": "browser {text}",
        "act_ask": "asking you {text}",
        "act_wait": "waiting for sub-agent",
        "act_think": "thinking {text}",
        "act_after": "thinking (after {text})",
        "act_reply": "writing reply",
        "act_start": "turn started",
        "act_await": "waiting for your approval / answer {text}",
        "act_review": "auto-review of approval {text}",
        "act_done": "done · last {text}",
        "act_aborted": "interrupted",
        "act_line": "{prefix} ▸ {what}   {age}",
        "alert_silent": "⚠ STUCK? no new events for {age} · {title}",
        "alert_repeat": "⚠ LOOP? same failure {n}× · {cmd}",
        "alert_waiting": "⏸ waiting for approval · {title}",
        "n_stuck_title": "Codex task may be stuck",
        "n_stuck_silent": "{title}: no new events for {age}",
        "n_stuck_repeat": "{title}: same command failed {n}× — {cmd}",
        "n_approval_title": "Codex is waiting for you",
        "n_approval_body": "{title}: needs approval / an answer",
        "n_complete_title": "Codex task finished",
        "n_complete_body": "{title}",
    },
    "zh": {
        "window_title": "CODEX // 用量",
        "brand": "CODEX.USAGE",
        "boot": "正在启动...",
        "syncing": "正在同步额度...",
        "online": "系统在线",
        "interval": "间隔={sec}秒",
        "card5": "主额度  //  5小时",
        "card7": "周额度  //  7天",
        "card5_compact": "5小时",
        "card7_compact": "7天",
        "awaiting": "等待数据",
        "refresh_label": "刷新间隔(秒)",
        "apply": "应用",
        "pin": "置顶",
        "sync_now": "立即同步",
        "open_usage": "打开用量页",
        "compact": "简洁",
        "detail": "详细",
        "mini": "迷你",
        "expand": "展开",
        "sync": "同步",
        "lang_switch": "EN",
        "used_line": "已用 {used:.1f}%   {reset} 后重置",
        "fault": "故障 // {msg}",
        "meta_ok": "{email}   套餐={plan}   状态={status}   @{checked_at}",
        "state_ok": "正常",
        "state_limit": "已达上限",
        "extra": (
            "积分余额                {balance}\n"
            "是否有积分              {has_credits}   不限量={unlimited}\n"
            "储蓄重置次数            {banked_resets}   可用={banked_applicable}\n"
            "触达类型                {reached_type}\n"
            "数据来源                chatgpt.com/backend-api/wham/usage\n"
            "说明                    5小时和7天额度都要有剩余才能继续用"
        ),
        "err_missing_auth": "缺少 ~/.codex/auth.json，请先登录 Codex",
        "err_apikey": "API Key 登录没有 ChatGPT 的 5小时/7天额度",
        "err_missing_tokens": "auth.json 缺少 access_token 或 account_id",
        "err_expired": "登录已过期，请重新登录 Codex",
        "err_refresh": "刷新令牌失败：{err}",
        "err_http": "HTTP {code}: {detail}",
        "err_request": "请求失败：{err}",
        "task_card": "当前任务  //  本地会话",
        "task_card_compact": "任务",
        "task_none": "未找到本地 Codex 会话",
        "task_wait": "暂无 token 数据",
        "task_title_line": "{title}   @{started}   {model}",
        "task_tokens_line": "总计 {total}   输入 {inp}   输出 {out}",
        "task_cache_line": "缓存 {cached}（命中 {hit:.1f}%）   推理 {reasoning}",
        "task_ctx_line": "上下文 {ctx} / {window}   {age}前更新",
        "task_compact_line": "总计 {total}   上下文 {ctx} / {window}   ≈{cost}",
        "task_cost_line": "API 估价 ≈ {total}   输入 {inp}  缓存 {cached}  输出 {out}",
        "task_cost_unknown": "API 估价 —   {models} 无公开 API 价格",
        "task_cost_na": "API 估价 —",
        "mini_tokens": "总量",
        "sec_tasks": "任务一览",
        "sec_account": "账户  //  积分",
        "win5": "5小时",
        "win7": "7天",
        "rate_h": "{v:.1f}%/时",
        "rate_d": "{v:.1f}%/天",
        "fc_runs_out": "预测 {rate}{basis} → {eta} 用完 ⚠ 早于重置",
        "fc_ok": "预测 {rate}{basis} → {eta} 满，重置在先",
        "fc_flat": "预测 未增长，可撑到重置",
        "fc_na": "预测 —  数据不足",
        "fc_reset": "预测 窗口已重置，等待数据",
        "fc_basis_window": "~",
        "fc_turns": "  约剩{left}轮（{avg:.1f}%/轮）",
        "fc_turns_inf": "   每轮≈0%",
        "fc_warn_compact": "⚠ {win}额度约 {eta} 用完，早于重置 {reset}",
        "st_running": "运行",
        "st_waiting": "待批准",
        "st_done": "完成",
        "st_idle": "空闲",
        "st_stuck": "卡住",
        "cnt_running": "{n} 运行",
        "cnt_waiting": "{n} 待批准",
        "cnt_stuck": "{n} 卡住",
        "cnt_done": "{n} 完成",
        "tasks_none": "最近 {h} 小时没有活跃任务",
        "tasks_hint": "单击：在 Codex 打开   右键：在 HUD 显示",
        "tasks_hint_select": "单击：在 HUD 显示该任务",
        "act_prefix": "当前",
        "act_cmd": "$ {text}",
        "act_patch": "修改 {text}",
        "act_web": "搜索 {text}",
        "act_image": "查看图片 {text}",
        "act_stdin": "等待进程输出",
        "act_tool": "工具 {text}",
        "act_browser": "浏览器 {text}",
        "act_ask": "向你提问 {text}",
        "act_wait": "等待子任务",
        "act_think": "思考中 {text}",
        "act_after": "思考中（上一步 {text}）",
        "act_reply": "撰写回复",
        "act_start": "本轮开始",
        "act_await": "等待你批准 / 回复 {text}",
        "act_review": "自动审批审核中 {text}",
        "act_done": "已完成 · 最后 {text}",
        "act_aborted": "已中断",
        "act_line": "{prefix} ▸ {what}   {age}",
        "alert_silent": "⚠ 疑似卡住：{age}无新事件 · {title}",
        "alert_repeat": "⚠ 疑似死循环：同样失败 {n} 次 · {cmd}",
        "alert_waiting": "⏸ 等待批准 · {title}",
        "n_stuck_title": "Codex 任务可能卡住了",
        "n_stuck_silent": "{title}：已 {age} 没有新事件",
        "n_stuck_repeat": "{title}：同一命令连续失败 {n} 次 — {cmd}",
        "n_approval_title": "Codex 在等你",
        "n_approval_body": "{title}：需要批准 / 回复",
        "n_complete_title": "Codex 任务完成",
        "n_complete_body": "{title}",
    },
}


EXTRA_ROWS = {
    "en": [
        ("credits.balance", "{balance}"),
        ("credits.has", "{has_credits}   unlimited={unlimited}"),
        ("banked.resets", "{banked_resets}   applicable={banked_applicable}"),
        ("reached.type", "{reached_type}"),
        ("source", "chatgpt.com/backend-api/wham/usage"),
        ("note", "5h AND 7d must both have remaining quota"),
    ],
    "zh": [
        ("积分余额", "{balance}"),
        ("是否有积分", "{has_credits}   不限量={unlimited}"),
        ("储蓄重置次数", "{banked_resets}   可用={banked_applicable}"),
        ("触达类型", "{reached_type}"),
        ("数据来源", "chatgpt.com/backend-api/wham/usage"),
        ("说明", "5小时和7天额度都要有剩余才能继续用"),
    ],
}



MONITOR_INT_KEYS = (
    # key, default, min, max
    ("forecast_lookback_min", 60, 10, 300),
    ("task_list_max", 4, 1, 10),
    ("task_list_hours", 6, 1, 72),
    ("idle_minutes", 30, 5, 1440),
    ("stuck_minutes", 5, 1, 240),
    ("stuck_repeat_count", 3, 2, 10),
    ("notify_cooldown_min", 10, 1, 240),
)
MONITOR_BOOL_KEYS = (
    ("forecast_enabled", True),
    ("task_list_enabled", True),
    ("notify_enabled", True),
    ("notify_stuck", True),
    ("notify_approval", True),
    ("notify_complete", False),
    ("task_list_show_subagents", False),
    ("section_tasks_open", True),
    ("section_account_open", False),
)


def load_ui_config() -> dict:
    data = _read_json(CONFIG_PATH) or {}
    if not isinstance(data, dict):
        data = {}
    sec = data.get("refresh_sec", DEFAULT_REFRESH_SEC)
    try:
        sec = int(sec)
    except Exception:
        sec = DEFAULT_REFRESH_SEC
    data["refresh_sec"] = max(MIN_REFRESH_SEC, min(MAX_REFRESH_SEC, sec))
    local_sec = data.get("local_refresh_sec", DEFAULT_LOCAL_REFRESH_SEC)
    try:
        local_sec = int(local_sec)
    except Exception:
        local_sec = DEFAULT_LOCAL_REFRESH_SEC
    data["local_refresh_sec"] = max(MIN_LOCAL_REFRESH_SEC, min(MAX_LOCAL_REFRESH_SEC, local_sec))
    # Mini capsule task section (total tokens). `mini_task_ctx` is the legacy name, still honored.
    legacy = data.pop("mini_task_ctx", True)
    data["mini_task_tokens"] = bool(data.get("mini_task_tokens", legacy))
    data["task_follow_selected"] = bool(data.get("task_follow_selected", True))
    # Task monitor / forecast / alerts (feat/task-monitor).
    for key, default, lo, hi in MONITOR_INT_KEYS:
        try:
            val = int(data.get(key, default))
        except Exception:
            val = default
        data[key] = max(lo, min(hi, val))
    for key, default in MONITOR_BOOL_KEYS:
        data[key] = bool(data.get(key, default))
    method = str(data.get("notify_method") or "toast").lower()
    data["notify_method"] = method if method in ("toast", "popup", "off") else "toast"
    click = str(data.get("task_click_action") or "open").lower()
    data["task_click_action"] = click if click in ("open", "select") else "open"
    data.setdefault("topmost", True)
    mode = str(data.get("mode") or "detail").lower()
    if mode not in ("detail", "compact", "mini"):
        mode = "detail"
    data["mode"] = mode
    expand = str(data.get("expand_mode") or "compact").lower()
    data["expand_mode"] = "detail" if expand == "detail" else "compact"
    lang = str(data.get("lang") or "en").lower()
    data["lang"] = "zh" if lang in ("zh", "zh-cn", "zh_cn", "cn", "chinese") else "en"
    return data


def save_ui_config(cfg: dict) -> None:
    try:
        CONFIG_PATH.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception:
        pass


def _read_json(path: Path):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def _save_auth(data: dict) -> None:
    try:
        AUTH_PATH.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception:
        pass


def _refresh_chatgpt_token(auth: dict) -> str | None:
    tokens = auth.get("tokens") or {}
    refresh = tokens.get("refresh_token")
    if not refresh:
        return tokens.get("access_token")

    body = urllib.parse.urlencode(
        {
            "client_id": "app_EMoamEEZ73f0CfXyEx0FJNpk",
            "grant_type": "refresh_token",
            "redirect_uri": "http://localhost:1455/auth/callback",
            "refresh_token": refresh,
        }
    ).encode()
    req = urllib.request.Request(
        "https://auth.openai.com/oauth/token",
        data=body,
        headers={"Content-Type": "application/x-www-form-urlencoded", "Accept": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            payload = json.loads(resp.read().decode("utf-8", "replace"))
        at = payload.get("access_token")
        if at:
            tokens["access_token"] = at
            if payload.get("refresh_token"):
                tokens["refresh_token"] = payload["refresh_token"]
            if payload.get("id_token"):
                tokens["id_token"] = payload["id_token"]
            auth["tokens"] = tokens
            auth["last_refresh"] = datetime.now(timezone.utc).isoformat()
            _save_auth(auth)
            return at
    except Exception:
        pass
    return tokens.get("access_token")


def fetch_usage() -> tuple[str, dict | None]:
    auth = _read_json(AUTH_PATH)
    if not isinstance(auth, dict):
        return "MISSING ~/.codex/auth.json — login Codex first", None
    if auth.get("auth_mode") not in (None, "chatgpt") and not (auth.get("tokens") or {}).get("access_token"):
        if auth.get("OPENAI_API_KEY") or auth.get("auth_mode") == "apikey":
            return "API-key login has no ChatGPT 5h/7d windows", None

    tokens = auth.get("tokens") or {}
    access = tokens.get("access_token")
    account_id = tokens.get("account_id")
    if not access or not account_id:
        return "auth.json missing access_token / account_id", None

    def _request(token: str):
        req = urllib.request.Request(
            USAGE_URL,
            headers={
                "Authorization": f"Bearer {token}",
                "chatgpt-account-id": account_id,
                "Accept": "application/json",
                "User-Agent": "CodexUsageHud/2.0",
                "originator": "codex-cli",
            },
        )
        with urllib.request.urlopen(req, timeout=25) as resp:
            return json.loads(resp.read().decode("utf-8", "replace"))

    try:
        return "ok", _request(access)
    except urllib.error.HTTPError as e:
        if e.code == 401:
            fresh = _refresh_chatgpt_token(auth)
            if not fresh:
                return "session expired — re-login Codex", None
            try:
                return "ok", _request(fresh)
            except Exception as e2:
                return f"refresh failed: {e2}", None
        detail = ""
        try:
            detail = e.read().decode("utf-8", "replace")[:180]
        except Exception:
            pass
        return f"HTTP {e.code}: {detail}", None
    except Exception as e:
        return f"request failed: {e}", None


def _fmt_duration(seconds: int | float | None, lang: str = "en") -> str:
    if seconds is None:
        return "--"
    try:
        s = int(max(0, seconds))
    except Exception:
        return "--"
    d, rem = divmod(s, 86400)
    h, remaining = divmod(rem, 3600)
    m, sec = divmod(remaining, 60)
    if lang == "zh":
        if d:
            return f"{d}天 {h:02d}小时 {m:02d}分"
        if h:
            return f"{h:02d}小时 {m:02d}分 {sec:02d}秒"
        return f"{m:02d}分 {sec:02d}秒"
    if d:
        return f"{d}d {h:02d}h {m:02d}m"
    if h:
        return f"{h:02d}h {m:02d}m {sec:02d}s"
    return f"{m:02d}m {sec:02d}s"


def summarize(data: dict) -> dict:
    rl = data.get("rate_limit") or {}
    credits = data.get("credits") or {}
    resets = data.get("rate_limit_reset_credits") or {}
    return {
        "email": data.get("email"),
        "plan": data.get("plan_type"),
        "allowed": rl.get("allowed"),
        "limit_reached": rl.get("limit_reached"),
        "reached_type": data.get("rate_limit_reached_type"),
        "primary": rl.get("primary_window") or {},
        "secondary": rl.get("secondary_window") or {},
        "credits": credits,
        "banked_resets": resets.get("available_count"),
        "banked_applicable": resets.get("applicable_available_count"),
        "checked_at": datetime.now().astimezone().strftime("%H:%M:%S"),
    }


def _fmt_tokens(n) -> str:
    try:
        n = float(n)
    except Exception:
        return "--"
    a = abs(n)
    if a >= 1e9:
        return f"{n / 1e9:.2f}B"
    if a >= 1e6:
        return f"{n / 1e6:.2f}M"
    if a >= 1e3:
        return f"{n / 1e3:.1f}K"
    return f"{int(n)}"


def _fmt_age(seconds: int | float | None, lang: str = "en") -> str:
    if seconds is None:
        return "--"
    try:
        s = int(max(0, seconds))
    except Exception:
        return "--"
    d, rem = divmod(s, 86400)
    h, rem = divmod(rem, 3600)
    m, sec = divmod(rem, 60)
    if lang == "zh":
        if d:
            return f"{d}天{h:02d}小时"
        if h:
            return f"{h}小时{m:02d}分"
        if m:
            return f"{m}分{sec:02d}秒"
        return f"{sec}秒"
    if d:
        return f"{d}d{h:02d}h"
    if h:
        return f"{h}h{m:02d}m"
    if m:
        return f"{m}m{sec:02d}s"
    return f"{sec}s"


_ROLLOUT_RE = re.compile(r"rollout-(\d{4}-\d{2}-\d{2}T\d{2}-\d{2}-\d{2})-([0-9a-fA-F-]{36})\.jsonl$")
_TS_RE = re.compile(rb'\{"timestamp":"(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?)Z"')


def _parse_iso_utc(text: str | None) -> float | None:
    if not text:
        return None
    try:
        t = str(text).rstrip("Z")
        if "." in t:
            head, frac = t.split(".", 1)
            t = f"{head}.{frac[:6]}"
        return datetime.fromisoformat(t).replace(tzinfo=timezone.utc).timestamp()
    except Exception:
        return None


def _parse_token_line(raw: bytes) -> dict | None:
    """Parse one rollout line; return {ts, info, rate_limits} for token_count events."""
    if b'"token_count"' not in raw:
        return None
    try:
        d = json.loads(raw.decode("utf-8", "replace"))
    except Exception:
        return None
    if not isinstance(d, dict) or d.get("type") != "event_msg":
        return None
    pl = d.get("payload") or {}
    if not isinstance(pl, dict) or pl.get("type") != "token_count":
        return None
    info = pl.get("info")
    return {
        "ts": _parse_iso_utc(d.get("timestamp")),
        "info": info if isinstance(info, dict) else None,
        "rate_limits": pl.get("rate_limits") if isinstance(pl.get("rate_limits"), dict) else None,
    }


_UUID_RE = re.compile(r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}")
_LOG_ROUTE_RE = re.compile(rb"ownerRoutePath=(\S*)")
_LOG_VIEW_RE = re.compile(rb"thread_stream_view_activity_changed active=true conversationId=(\S+)")
_LOG_NAV_RE = re.compile(rb"ownerRoutePath=|thread_stream_view_activity_changed active=true")


def _parse_selection_line(raw: bytes) -> tuple[str, str | None] | None:
    """Return (iso_ts, thread_id | None) for a Codex desktop UI navigation log line.

    - "IAB_LIFECYCLE received browser sidebar owner sync ... ownerRoutePath=/local/<id>"
      is logged every time a thread is opened in the main window. A route without a
      thread id (new chat, settings, ...) yields None, i.e. "no task selected".
    - "thread_stream_view_activity_changed active=true conversationId=<id>" (primary
      window) is logged when a thread view becomes active; kept as a second signal.
    """
    if b"ownerRoutePath=" in raw:
        m = _LOG_ROUTE_RE.search(raw)
        if not m:
            return None
        route = m.group(1).decode("utf-8", "replace")
        ids = _UUID_RE.findall(route)
        tid = ids[-1].lower() if ids and "client-new-thread" not in route else None
    elif b"thread_stream_view_activity_changed active=true" in raw:
        if b"rendererWindowAppearance=primary" not in raw:
            return None
        m = _LOG_VIEW_RE.search(raw)
        if not m:
            return None
        conv = m.group(1).decode("utf-8", "replace")
        ids = _UUID_RE.findall(conv)
        if not ids or "client-new-thread" in conv:
            return None
        tid = ids[0].lower()
    else:
        return None
    ts = raw[:40].split(b" ", 1)[0].decode("ascii", "replace")
    if not ts[:4].isdigit():
        return None
    return ts, tid


class SelectedThreadTracker:
    """Which thread is open in the Codex desktop app, from its log files.

    Read-only and incremental: each log file is scanned backwards once (newest
    files first, stopping as soon as older files cannot hold a newer event), then
    only appended bytes are parsed on later polls. NTFS directory listings report
    stale sizes/mtimes for files Codex keeps open, so sizes come from os.stat().
    """

    def __init__(self, logs_dir: Path = CODEX_LOGS_DIR):
        self.logs_dir = Path(logs_dir)
        self._files: dict[str, dict] = {}  # path -> {"offset": int, "event": (ts, id) | None}
        self._primed = False
        # thread id -> epoch of the last time it was opened in the Codex UI (all watched logs)
        self.history: dict[str, float] = {}

    def _note(self, ev):
        if not ev or not ev[1]:
            return
        ts = _parse_iso_utc(ev[0])
        if ts and ts > self.history.get(ev[1], 0.0):
            self.history[ev[1]] = ts

    def _scan_history(self, path: str, end: int):
        """One-time: every navigation line in the last LOG_HISTORY_LIMIT bytes of a log."""
        try:
            with open(path, "rb") as fh:
                start = max(0, end - LOG_HISTORY_LIMIT)
                fh.seek(start)
                data = fh.read(end - start)
        except OSError:
            return
        for m in _LOG_NAV_RE.finditer(data):
            a = data.rfind(b"\n", 0, m.start()) + 1
            b = data.find(b"\n", m.end())
            self._note(_parse_selection_line(data[a : b if b >= 0 else len(data)]))

    def _day_dirs(self) -> list[str]:
        out = []

        def _sub(path: str) -> list[str]:
            try:
                return sorted(
                    (e.path for e in os.scandir(path) if e.is_dir() and e.name.isdigit()), reverse=True
                )
            except OSError:
                return []

        for y in _sub(str(self.logs_dir)):
            for m in _sub(y):
                for d in _sub(m):
                    out.append(d)
                    if len(out) >= LOG_DAY_DIRS:
                        return out
        return out

    def _log_files(self) -> list[str]:
        files = []
        for d in self._day_dirs():
            try:
                with os.scandir(d) as it:
                    for e in it:
                        if e.name.startswith("codex-desktop-") and e.name.endswith(".log"):
                            files.append(e.path)
            except OSError:
                continue
        return files

    @staticmethod
    def _last_ts(path: str, size: int) -> str:
        try:
            with open(path, "rb") as fh:
                start = max(0, size - 8192)
                fh.seek(start)
                chunk = fh.read(size - start)
        except OSError:
            return ""
        for raw in reversed(chunk.split(b"\n")):
            ts = raw[:40].split(b" ", 1)[0]
            if ts[:4].isdigit() and ts.endswith(b"Z"):
                return ts.decode("ascii", "replace")
        return ""

    @staticmethod
    def _scan_backwards(path: str, end: int):
        try:
            fh = open(path, "rb")
        except OSError:
            return None
        with fh:
            pos, carry = end, b""
            while pos > 0 and end - pos < LOG_TAIL_LIMIT:
                n = min(ROLLOUT_TAIL_CHUNK, pos)
                pos -= n
                fh.seek(pos)
                lines = (fh.read(n) + carry).split(b"\n")
                carry = lines[0] if pos > 0 else b""
                for raw in reversed(lines[1:] if pos > 0 else lines):
                    ev = _parse_selection_line(raw)
                    if ev:
                        return ev
        return None

    def _read_forward(self, path: str, st: dict, size: int):
        try:
            with open(path, "rb") as fh:
                fh.seek(st["offset"])
                data = fh.read(size - st["offset"])
        except OSError:
            return
        cut = data.rfind(b"\n")
        if cut < 0:
            return
        st["offset"] += cut + 1
        for raw in data[:cut].split(b"\n"):
            ev = _parse_selection_line(raw)
            if ev and (st["event"] is None or ev[0] >= st["event"][0]):
                st["event"] = ev
            self._note(ev)

    def poll(self) -> tuple[str, str | None] | None:
        """Return (iso_ts, thread_id | None) of the newest UI navigation, or None if unknown."""
        paths = self._log_files()
        sizes = {}
        for p in paths:
            try:
                sizes[p] = os.stat(p).st_size
            except OSError:
                continue
        for p in list(self._files):
            if p not in sizes:
                self._files.pop(p, None)
        if not self._primed:
            # First pass: newest files first; stop once no older file can beat the best event.
            order = sorted(sizes, key=lambda p: self._last_ts(p, sizes[p]), reverse=True)
            best = None
            for p in order:
                last = self._last_ts(p, sizes[p])
                if best is not None and last and last < best[0]:
                    self._files[p] = {"offset": sizes[p], "event": None}
                    continue
                ev = self._scan_backwards(p, sizes[p])
                self._files[p] = {"offset": sizes[p], "event": ev}
                if ev and (best is None or ev[0] > best[0]):
                    best = ev
            for p in order:
                self._scan_history(p, sizes[p])
            self._primed = True
        else:
            for p, size in sizes.items():
                st = self._files.get(p)
                if st is None:
                    st = self._files[p] = {"offset": 0, "event": None}  # new / rotated log
                if size < st["offset"]:
                    st["offset"] = 0
                if size > st["offset"]:
                    self._read_forward(p, st, size)
        events = [st["event"] for st in self._files.values() if st.get("event")]
        return max(events, key=lambda e: e[0]) if events else None


class TaskUsageReader:
    """Follow the Codex task open in the desktop UI (else the most recently
    active rollout) and extract its token usage.

    Pure local file reads, meant to run on a worker thread. The active file is
    tailed incrementally (only bytes appended since the last poll are parsed);
    on a task switch the file is scanned backwards from EOF in chunks.
    NTFS often keeps a stale mtime while Codex holds the rollout open, so file
    activity = max(mtime, timestamp of the last line in the file tail).
    """

    def __init__(
        self,
        sessions_dir: Path = SESSIONS_DIR,
        index_path: Path = SESSION_INDEX_PATH,
        logs_dir: Path | None = CODEX_LOGS_DIR,
        archived_dir: Path | None = ARCHIVED_SESSIONS_DIR,
    ):
        self.sessions_dir = Path(sessions_dir)
        self.index_path = Path(index_path)
        self.archived_dir = Path(archived_dir) if archived_dir else None
        self.selector = SelectedThreadTracker(logs_dir) if logs_dir else None
        self.follow_selected = True
        # (thread id, selection event seen at click time): a task picked in the HUD task list.
        # Cleared by the next newer Codex UI navigation (when following the Codex selection).
        self.override: tuple | None = None
        self._last_sel = None
        self._by_id: dict[str, str] = {}  # thread id -> rollout path
        self._id_scan_at = 0.0
        self._selection = None  # last (iso_ts, id | None) from the desktop log
        self._source = "latest"
        self._tally: CostTally | None = CostTally()
        self._model: str | None = None
        self._candidates: list[str] = []
        self._scan_at = 0.0
        self._stat: dict[str, tuple[int, float, float]] = {}  # path -> (size, mtime, activity)
        self._path: str | None = None
        self._offset = 0
        self._usage: dict | None = None  # last token_count with info
        self._last_event_ts: float | None = None
        self._rate_limits: dict | None = None
        self._titles: dict[str, str] = {}
        self._titles_sig = None

    # -- discovery -------------------------------------------------------
    def _list_rollouts(self, root: Path | None = None) -> list[tuple[str, float]]:
        out = []
        stack = [str(root or self.sessions_dir)]
        while stack:
            cur = stack.pop()
            try:
                with os.scandir(cur) as it:
                    for e in it:
                        try:
                            if e.is_dir(follow_symlinks=False):
                                stack.append(e.path)
                            elif e.name.startswith("rollout-") and e.name.endswith(".jsonl"):
                                out.append((e.path, e.stat().st_mtime))
                        except OSError:
                            continue
            except OSError:
                continue
        return out

    def _rescan(self):
        files = self._list_rollouts()
        by_id = {}
        extra = self._list_rollouts(self.archived_dir) if self.archived_dir else []
        for p, _ in extra + files:  # live sessions win over archived copies
            m = _ROLLOUT_RE.search(os.path.basename(p))
            if m:
                by_id[m.group(2).lower()] = p
        self._by_id = by_id
        by_mtime = [p for p, _ in sorted(files, key=lambda x: x[1], reverse=True)[:ROLLOUT_CANDIDATES]]
        by_name = [p for p, _ in sorted(files, key=lambda x: os.path.basename(x[0]), reverse=True)[:ROLLOUT_CANDIDATES]]
        seen = []
        for p in by_mtime + by_name + ([self._path] if self._path else []):
            if p and p not in seen:
                seen.append(p)
        self._candidates = seen
        for p in list(self._stat):
            if p not in seen:
                self._stat.pop(p, None)
        self._scan_at = time.monotonic()

    @staticmethod
    def _tail_timestamp(path: str, size: int) -> float | None:
        try:
            with open(path, "rb") as fh:
                start = max(0, size - 65536)
                fh.seek(start)
                chunk = fh.read(size - start)
        except OSError:
            return None
        last = None
        for m in _TS_RE.finditer(chunk):
            last = m
        return _parse_iso_utc(last.group(1).decode()) if last else None

    def _pick_active(self) -> str | None:
        best, best_act = None, -1.0
        for p in self._candidates:
            try:
                st = os.stat(p)
            except OSError:
                self._stat.pop(p, None)
                continue
            prev = self._stat.get(p)
            if prev and prev[0] == st.st_size and prev[1] == st.st_mtime:
                act = prev[2]
            else:
                ts = self._tail_timestamp(p, st.st_size)
                act = max(st.st_mtime, ts or 0.0)
                self._stat[p] = (st.st_size, st.st_mtime, act)
            if act > best_act:
                best, best_act = p, act
        return best

    # -- reading ---------------------------------------------------------
    def _scan_backwards(self, path: str, end: int):
        """Find the newest token_count (with info) scanning from EOF in chunks."""
        self._usage = None
        self._last_event_ts = None
        self._rate_limits = None
        try:
            fh = open(path, "rb")
        except OSError:
            return
        with fh:
            pos = end
            carry = b""
            while pos > 0 and end - pos < ROLLOUT_TAIL_LIMIT:
                n = min(ROLLOUT_TAIL_CHUNK, pos)
                pos -= n
                fh.seek(pos)
                buf = fh.read(n) + carry
                lines = buf.split(b"\n")
                # lines[0] may be a partial line unless we reached BOF
                carry = lines[0] if pos > 0 else b""
                body = lines[1:] if pos > 0 else lines
                for raw in reversed(body):
                    ev = _parse_token_line(raw)
                    if not ev:
                        continue
                    if self._last_event_ts is None:
                        self._last_event_ts = ev["ts"]
                    if self._rate_limits is None and ev["rate_limits"]:
                        self._rate_limits = ev["rate_limits"]
                    if ev["info"]:
                        self._usage = ev
                        return

    def _consume(self, raw: bytes):
        """Handle one complete rollout line (turn_context model / token_count usage)."""
        if b'"turn_context"' in raw[:256]:
            try:
                d = json.loads(raw.decode("utf-8", "replace"))
            except Exception:
                return
            if isinstance(d, dict) and d.get("type") == "turn_context":
                model = (d.get("payload") or {}).get("model")
                if self._tally is not None:
                    self._tally.on_model(model)
                if model:
                    self._model = str(model)
            return
        ev = _parse_token_line(raw)
        if not ev:
            return
        self._last_event_ts = ev["ts"] or self._last_event_ts
        if ev["rate_limits"]:
            self._rate_limits = ev["rate_limits"]
        if ev["info"]:
            self._usage = ev
            if self._tally is not None:
                self._tally.on_usage(ev["info"])

    def _scan_full(self, path: str, size: int) -> bool:
        """Parse the whole rollout forward (model history + cost). Returns False if too big."""
        self._usage = None
        self._last_event_ts = None
        self._rate_limits = None
        self._model = None
        self._tally = CostTally()
        self._offset = 0
        if size > ROLLOUT_FULL_SCAN_LIMIT:
            self._tally = None
            return False
        try:
            with open(path, "rb") as fh:
                carry = b""
                pos = 0
                while pos < size:
                    chunk = fh.read(min(ROLLOUT_READ_CHUNK, size - pos))
                    if not chunk:
                        break
                    pos += len(chunk)
                    buf = carry + chunk
                    cut = buf.rfind(b"\n")
                    if cut < 0:
                        carry = buf
                        continue
                    for raw in buf[:cut].split(b"\n"):
                        self._consume(raw)
                    carry = buf[cut + 1:]
                    self._offset = pos - len(carry)
        except OSError:
            pass
        return True

    def _read_forward(self, path: str, size: int):
        try:
            with open(path, "rb") as fh:
                fh.seek(self._offset)
                data = fh.read(size - self._offset)
        except OSError:
            return
        cut = data.rfind(b"\n")
        if cut < 0:
            return  # wait for the line to finish
        self._offset += cut + 1
        for raw in data[:cut].split(b"\n"):
            self._consume(raw)

    def _title_for(self, sid: str | None) -> str | None:
        if not sid:
            return None
        try:
            st = self.index_path.stat()
            sig = (st.st_size, st.st_mtime)
        except OSError:
            return None
        if sig != self._titles_sig or sid not in self._titles:
            titles = {}
            try:
                with open(self.index_path, "rb") as fh:
                    for raw in fh:
                        try:
                            d = json.loads(raw.decode("utf-8", "replace"))
                        except Exception:
                            continue
                        if isinstance(d, dict) and d.get("id") and d.get("thread_name"):
                            titles[str(d["id"])] = str(d["thread_name"])
            except OSError:
                return None
            self._titles = titles
            self._titles_sig = sig
        return self._titles.get(sid)

    def set_override(self, tid: str | None):
        """Show this thread in the HUD (task-list click); None returns to normal following."""
        self.override = (tid.lower(), self._last_sel) if tid else None

    def _rollout_for_id(self, tid: str) -> str | None:
        path = self._by_id.get(tid)
        if path and os.path.exists(path):
            return path
        # New thread (rollout created after the last rescan) or moved file: rescan, rate-limited.
        if time.monotonic() - self._id_scan_at >= ROLLOUT_ID_RESCAN_SEC:
            self._id_scan_at = time.monotonic()
            self._rescan()
            path = self._by_id.get(tid)
            if path and os.path.exists(path):
                return path
        return None

    def poll(self) -> dict | None:
        if not self._candidates or time.monotonic() - self._scan_at >= ROLLOUT_RESCAN_SEC:
            self._rescan()
        path = None
        self._source = "latest"
        self._selection = None
        if self.follow_selected and self.selector is not None:
            try:
                self._selection = self.selector.poll()
            except Exception:
                self._selection = None
        if self._selection is not None:
            self._last_sel = self._selection
        override = self.override
        if override is not None and self.follow_selected and self._selection:
            base = override[1]
            if base is None:
                self.override = override = (override[0], self._selection)
            elif self._selection[0] > base[0]:
                self.override = override = None  # user navigated in Codex after the click
        if override is not None:
            path = self._rollout_for_id(override[0])
            if path:
                self._source = "pinned"
        if not path and self.follow_selected:
            tid = (self._selection or (None, None))[1]
            if tid:
                path = self._rollout_for_id(tid)
                if path:
                    self._source = "selected"
        if not path:
            path = self._pick_active()
        if not path:
            self._path = None
            return None
        try:
            size = os.stat(path).st_size
        except OSError:
            return None
        if path != self._path or size < self._offset:
            self._path = path
            if not self._scan_full(path, size):
                # Huge file: newest usage from the tail only; cost left unknown.
                self._scan_backwards(path, size)
                self._offset = size
                try:
                    with open(path, "rb") as fh:
                        if size:
                            fh.seek(size - 1)
                            if fh.read(1) != b"\n":
                                self._offset = self._line_start(fh, size)
                except OSError:
                    pass
        elif size > self._offset:
            self._read_forward(path, size)
        return self.snapshot()

    @staticmethod
    def _line_start(fh, size: int) -> int:
        pos = size
        while pos > 0:
            n = min(65536, pos)
            pos -= n
            fh.seek(pos)
            chunk = fh.read(n)
            i = chunk.rfind(b"\n")
            if i >= 0:
                return pos + i + 1
        return 0

    def snapshot(self) -> dict | None:
        if not self._path:
            return None
        name = os.path.basename(self._path)
        m = _ROLLOUT_RE.search(name)
        sid = m.group(2) if m else None
        started = None
        if m:
            try:
                started = datetime.strptime(m.group(1), "%Y-%m-%dT%H-%M-%S")
            except Exception:
                started = None
        info = (self._usage or {}).get("info") or {}
        total = info.get("total_token_usage") or {}
        last = info.get("last_token_usage") or {}
        return {
            "path": self._path,
            "id": sid,
            "title": self._title_for(sid),
            "started": started,
            "has_usage": bool(total),
            "total": total,
            "last": last,
            "window": info.get("model_context_window"),
            "updated_ts": self._last_event_ts or (self._usage or {}).get("ts"),
            "rate_limits": self._rate_limits,
            "source": self._source,  # "selected" (open in Codex UI) or "latest" (last written)
            "selected_id": (self._selection or (None, None))[1],
            "selected_at": (self._selection or (None, None))[0],
            "model": self._model,
            "cost_buckets": (
                {k: dict(v) for k, v in self._tally.buckets.items()} if self._tally is not None else None
            ),
        }


def task_view(snap: dict | None, prices: dict | None = None) -> dict | None:
    """Derive display numbers from a TaskUsageReader snapshot (cost only when prices given)."""
    if not snap or not snap.get("has_usage"):
        return None
    total = snap.get("total") or {}
    last = snap.get("last") or {}

    def _i(d, k):
        try:
            return int(d.get(k) or 0)
        except Exception:
            return 0

    inp = _i(total, "input_tokens")
    cached = _i(total, "cached_input_tokens")
    ctx = _i(last, "input_tokens") or _i(last, "total_tokens")
    window = _i(snap, "window")
    return {
        "total": _i(total, "total_tokens") or inp + _i(total, "output_tokens"),
        "input": inp,
        "cached": cached,
        "hit": (cached * 100.0 / inp) if inp else 0.0,
        "output": _i(total, "output_tokens"),
        "reasoning": _i(total, "reasoning_output_tokens"),
        "ctx": ctx,
        "window": window,
        "ctx_pct": min(100.0, ctx * 100.0 / window) if window else 0.0,
        "model": snap.get("model"),
        "cost": estimate_cost(snap.get("cost_buckets"), prices) if prices is not None else None,
    }


_DATE_SUFFIX_RE = re.compile(r"-\d{4}-\d{2}-\d{2}$")
_USAGE_FIELDS = (
    "input_tokens",
    "cached_input_tokens",
    "cache_write_input_tokens",
    "output_tokens",
    "reasoning_output_tokens",
)


def merged_model_prices(cfg: dict | None) -> dict:
    """Built-in price table updated with the user's "model_prices" overrides (USD per 1M)."""
    prices = {k: dict(v) for k, v in MODEL_PRICES.items()}
    overrides = (cfg or {}).get("model_prices")
    if not isinstance(overrides, dict):
        return prices
    for model, entry in overrides.items():
        if not isinstance(entry, dict):
            continue
        try:
            item = {
                "input": float(entry["input"]),
                "cached_input": float(entry.get("cached_input", entry["input"])),
                "output": float(entry["output"]),
            }
            cw = entry.get("cache_write")
            item["cache_write"] = None if cw is None else float(cw)
            item["long_context"] = bool(entry.get("long_context", True))
        except Exception:
            continue
        prices[str(model).strip().lower()] = item
    return prices


def price_for(model: str | None, prices: dict) -> dict | None:
    if not model:
        return None
    key = str(model).strip().lower()
    return prices.get(key) or prices.get(_DATE_SUFFIX_RE.sub("", key))


class CostTally:
    """Attribute each token_count delta to the model active at that point.

    Deltas of total_token_usage between consecutive token_count events (they match
    last_token_usage in practice); if totals go backwards, last_token_usage is used.
    Buckets are keyed by (model, long_context_request) and priced at display time,
    so config price overrides apply without re-reading the rollout.
    """

    def __init__(self):
        self.model: str | None = None
        self.prev: dict | None = None
        self.buckets: dict[tuple, dict] = {}

    def on_model(self, model):
        if model:
            self.model = str(model)

    def on_usage(self, info: dict):
        tot = info.get("total_token_usage") or {}
        last = info.get("last_token_usage") or {}
        if not tot:
            return

        def _g(d, k):
            try:
                return int(d.get(k) or 0)
            except Exception:
                return 0

        if self.prev is None:
            delta = {k: _g(tot, k) for k in _USAGE_FIELDS}
        else:
            delta = {k: _g(tot, k) - _g(self.prev, k) for k in _USAGE_FIELDS}
            if any(v < 0 for v in delta.values()):
                delta = {k: _g(last, k) for k in _USAGE_FIELDS}
        self.prev = tot
        if not any(delta.values()):
            return
        is_long = _g(last, "input_tokens") > LONG_CONTEXT_TOKENS
        b = self.buckets.setdefault((self.model, is_long), dict.fromkeys(_USAGE_FIELDS, 0))
        for k, v in delta.items():
            b[k] += v


def estimate_cost(buckets: dict | None, prices: dict) -> dict | None:
    """USD estimate from CostTally buckets. Unpriced models are reported, not guessed."""
    if buckets is None:
        return None
    out = {"input": 0.0, "cached": 0.0, "output": 0.0, "total": 0.0,
           "unknown_models": [], "unknown_tokens": 0, "models": {}}
    for (model, is_long), b in buckets.items():
        tokens = b["input_tokens"] + b["output_tokens"]
        name = model or "?"
        out["models"][name] = out["models"].get(name, 0) + tokens
        pr = price_for(model, prices)
        if not pr:
            if tokens:
                out["unknown_tokens"] += tokens
                if name not in out["unknown_models"]:
                    out["unknown_models"].append(name)
            continue
        mi = LONG_CONTEXT_INPUT_MULT if (is_long and pr.get("long_context", True)) else 1.0
        mo = LONG_CONTEXT_OUTPUT_MULT if (is_long and pr.get("long_context", True)) else 1.0
        cached = b["cached_input_tokens"]
        cw = b["cache_write_input_tokens"]
        uncached = max(0, b["input_tokens"] - cached - cw)
        cw_rate = pr["input"] if pr.get("cache_write") is None else pr["cache_write"]
        # output_tokens already includes reasoning_output_tokens (billed as output once).
        inp = (uncached * pr["input"] + cw * cw_rate) * mi / 1e6
        cac = cached * pr["cached_input"] * mi / 1e6
        outp = b["output_tokens"] * pr["output"] * mo / 1e6
        out["input"] += inp
        out["cached"] += cac
        out["output"] += outp
    out["total"] = out["input"] + out["cached"] + out["output"]
    out["priced"] = not out["unknown_models"]
    return out


def _fmt_usd(v) -> str:
    if v is None:
        return "—"
    if 0 < v < 0.01:
        return "<$0.01"
    if v >= 1000:
        return f"${v:,.0f}"
    return f"${v:,.2f}"


# ---------------------------------------------------------------------------
# Task monitor: per-task status, live activity, stuck detection, quota forecast.
#
# Rollout event facts (checked on real ~/.codex/sessions files, Codex desktop 26.928):
#   event_msg/task_started            a turn begins (payload.turn_id, started_at)
#   event_msg/task_complete           the turn ended normally (last_agent_message)
#   event_msg/turn_aborted            the turn was interrupted (codex-rs name; rare)
#   event_msg/token_count             usage + rate_limits (primary 5h / secondary 7d)
#   event_msg/item_completed          finished items: CommandExecution (command, status,
#                                     exit_code, aggregated_output), FileChange, WebSearch,
#                                     McpToolCall, Reasoning (summary_text), AgentMessage ...
#   response_item/custom_tool_call    "exec" code-mode call; input is JS calling
#                                     tools.exec_command({cmd:...}), tools.apply_patch("*** Begin Patch..."),
#                                     tools.web__run({search_query:[{q:...}]}), tools.view_image, ...
#   response_item/function_call       direct tools (js browser REPL, wait, request_user_input_async)
#   response_item/*_call_output       result for a call_id (a call without output = still running)
#   response_item/reasoning|message   model thinking / assistant text
# Approval requests are NOT written to rollouts (codex-rs does not persist them and this
# setup uses approvals_reviewer=auto_review). The desktop log line
#   "[desktop-notifications] show notification conversationId=<id> kind=permission|question"
# is the only local signal that a thread waits for the user.
# ---------------------------------------------------------------------------

MONITOR_RESCAN_SEC = 15
MONITOR_TAIL_BYTES = 6 * 1024 * 1024  # per task on first sight (turn boundaries, recent turns)
QUOTA_SEED_BYTES = 4 * 1024 * 1024  # rate_limits history seed per rollout active in the last 24h
QUOTA_SEED_HOURS = 24
QUOTA_RESET_TOL = 120  # resets_at jitters by ~1s between events; same window if within this
TOAST_AUMID = r"{1AC14E77-02E7-4E5D-B744-2EB1AE5198B7}\WindowsPowerShell\v1.0\powershell.exe"

_TYPE_RE = re.compile(rb'"type":"([A-Za-z_]+)"')
_CALL_ID_RE = re.compile(rb'"call_id":"([^"]+)"')
_LOG_NOTIFY_RE = re.compile(
    rb"\[desktop-notifications\] show notification conversationId=(\S+) kind=(permission|question)\b"
)
_JS_STR = r"""("(?:[^"\\]|\\.)*"|'(?:[^'\\]|\\.)*'|`(?:[^`\\]|\\.)*`)"""
_TOOLS_CALL_RE = re.compile(r"tools\.([A-Za-z0-9_]+)\s*\(")
_CMD_ARG_RE = re.compile(r"\bcmd\s*:\s*" + _JS_STR)
_PATCH_FILE_RE = re.compile(r"\*\*\* (?:Update|Add|Delete) File: ([^\n\\]+?)(?:\\n|\n)")
_WEB_Q_RE = re.compile(r"\b(?:q|ref_id)\s*:\s*" + _JS_STR)
_PATH_ARG_RE = re.compile(r"\bpath\s*:\s*" + _JS_STR)


def _js_unquote(lit: str) -> str:
    if not lit:
        return ""
    q, body = lit[0], lit[1:-1]
    if q == '"':
        try:
            return json.loads(lit)
        except Exception:
            pass
    return re.sub(r"\\(.)", lambda m: {"n": " ", "t": " "}.get(m.group(1), m.group(1)), body)


def _one_line(text: str, limit: int = 160) -> str:
    text = re.sub(r"\s+", " ", str(text or "")).strip()
    return text if len(text) <= limit else text[: limit - 1] + "…"


_ABS_PATH_RE = re.compile(r"""(['"]?)((?:[A-Za-z]:)?[\\/](?:[^\s'"|;<>]*[\\/])+)([^\s'"\\/|;<>]+)\1""")


def _short_paths(text: str) -> str:
    """Absolute paths -> base names, so a command line stays readable when truncated."""
    return _ABS_PATH_RE.sub(lambda m: m.group(3) if len(m.group(2)) > 12 else m.group(0), text)


def _base(path: str) -> str:
    path = str(path or "").strip().strip("'\"`").rstrip("/\\")
    return re.split(r"[\\/]", path)[-1] if path else ""


def describe_tool_call(name: str | None, payload: str | dict | None, namespace: str | None = None) -> tuple[str, str]:
    """(kind, text) for a pending/last tool call. kind is a STRINGS key suffix."""
    name = str(name or "")
    if name == "exec" and isinstance(payload, str):
        tools = _TOOLS_CALL_RE.findall(payload)
        first = tools[0] if tools else ""
        extra = len(tools) - 1
        suffix = f" +{extra}" if extra > 0 else ""
        if first == "exec_command":
            m = _CMD_ARG_RE.search(payload)
            return "cmd", _one_line(_short_paths(_js_unquote(m.group(1))) if m else "?") + suffix
        if first == "apply_patch":
            files = [_base(f) for f in _PATCH_FILE_RE.findall(payload)]
            uniq = list(dict.fromkeys(f for f in files if f))
            txt = ", ".join(uniq[:2]) + (f" +{len(uniq) - 2}" if len(uniq) > 2 else "")
            return "patch", (txt or "?") + suffix
        if first == "web__run":
            m = _WEB_Q_RE.search(payload)
            return "web", _one_line(_js_unquote(m.group(1)) if m else "") + suffix
        if first == "view_image":
            m = _PATH_ARG_RE.search(payload)
            return "image", (_base(_js_unquote(m.group(1))) if m else "") + suffix
        if first == "write_stdin":
            return "stdin", suffix.strip()
        if first.startswith("mcp__"):
            parts = first.split("__")
            return "tool", ".".join(p for p in parts[1:] if p) + suffix
        if first:
            return "tool", first + suffix
        return "tool", "exec"
    args = payload
    if isinstance(args, str):
        try:
            args = json.loads(args)
        except Exception:
            args = {}
    if not isinstance(args, dict):
        args = {}
    if name.startswith("request_user_input"):
        return "ask", _one_line(args.get("question") or args.get("prompt") or "", 80)
    if name == "wait":
        return "wait", ""
    if name in ("js", "js_reset") and str(namespace or "").endswith("repl"):
        return "browser", _one_line(args.get("title") or args.get("code") or "", 120)
    if name in ("shell", "exec_command", "local_shell"):
        cmd = args.get("cmd") or args.get("command") or ""
        if isinstance(cmd, list):
            cmd = cmd[-1] if cmd else ""
        return "cmd", _one_line(cmd)
    if name == "apply_patch":
        return "patch", ", ".join(_base(f) for f in _PATCH_FILE_RE.findall(str(args.get("input") or "")))
    return "tool", name or "?"


def _cmd_result(item: dict) -> dict | None:
    """Normalize an item_completed CommandExecution for repeat detection."""
    cmd = item.get("command")
    parsed = item.get("parsed_cmd")
    text = ""
    if isinstance(parsed, list) and parsed and isinstance(parsed[0], dict):
        text = " && ".join(str(p.get("cmd") or "") for p in parsed if isinstance(p, dict))
    if not text:
        text = cmd[-1] if isinstance(cmd, list) and cmd else str(cmd or "")
    status = str(item.get("status") or "")
    code = item.get("exit_code")
    try:
        code = int(code) if code is not None else None
    except Exception:
        code = None
    failed = status == "failed" or (code not in (None, 0))
    err = str(item.get("stderr") or "").strip() or str(item.get("aggregated_output") or "").strip()
    lines = [ln.strip() for ln in err.splitlines() if ln.strip()]
    sig = ""
    for ln in lines:
        if re.search(r"error|exception|cannot|not found|failed|denied|无法|错误", ln, re.I):
            sig = ln
            break
    if not sig and lines:
        sig = lines[-1]
    sig = re.sub(r"\d+", "#", sig)[:160]
    return {"cmd": _one_line(text, 400), "failed": failed, "code": code, "sig": sig}


def detect_repeat_failures(results: list[dict], k: int) -> dict | None:
    """Last k command results all failed and share the same command or the same error line."""
    if k < 2 or len(results) < k:
        return None
    tail = results[-k:]
    if not all(r.get("failed") for r in tail):
        return None
    cmds = {r.get("cmd") for r in tail}
    sigs = {r.get("sig") for r in tail}
    if len(cmds) == 1:
        return {"count": k, "cmd": tail[-1].get("cmd"), "sig": tail[-1].get("sig"), "same": "cmd"}
    if len(sigs) == 1 and next(iter(sigs)):
        return {"count": k, "cmd": tail[-1].get("cmd"), "sig": tail[-1].get("sig"), "same": "error"}
    return None


class RolloutState:
    """Incremental parser of one rollout: turn state, pending calls, activity, failures."""

    def __init__(self, path: str):
        self.path = path
        self.offset = 0
        self.last_ts: float | None = None
        self.turn_open: bool | None = None  # None = no turn boundary seen in the scanned tail
        self.turn_started_ts: float | None = None
        self.turn_ended_ts: float | None = None
        self.turn_start_primary: tuple | None = None
        self.last_primary: tuple | None = None  # (ts, used, resets_at)
        self.pending: dict[str, tuple] = {}  # call_id -> (ts, kind, text)
        self.last_activity: tuple | None = None  # (ts, kind, text)
        self.last_tool: tuple | None = None  # (ts, kind, text) of the newest tool call
        self.results: list[dict] = []  # command results of the current turn
        self.turns: list[dict] = []  # completed turns: start/end primary samples
        self.samples: list[tuple] = []  # (ts, rate_limits) appended since last drain

    def _set_activity(self, ts, kind, text=""):
        self.last_activity = (ts, kind, text)

    def feed(self, raw: bytes):
        head = raw[:400]
        types = _TYPE_RE.findall(head)
        if len(types) < 1:
            return
        outer = types[0]
        inner = types[1] if len(types) > 1 else b""
        m = _TS_RE.match(raw)
        ts = _parse_iso_utc(m.group(1).decode()) if m else None
        if ts:
            self.last_ts = max(self.last_ts or 0.0, ts)
        if outer == b"event_msg":
            if inner == b"task_started":
                self.turn_open = True
                self.turn_started_ts = ts
                self.turn_start_primary = self.last_primary
                self.pending.clear()
                self.results = []
                self._set_activity(ts, "start")
            elif inner in (b"task_complete", b"turn_aborted"):
                self.turn_open = False
                self.turn_ended_ts = ts
                self.pending.clear()
                if self.turn_started_ts and self.last_primary:
                    self.turns.append(
                        {"start_ts": self.turn_started_ts, "start": self.turn_start_primary, "end": self.last_primary}
                    )
                    del self.turns[:-20]
                self._set_activity(ts, "done" if inner == b"task_complete" else "aborted")
            elif inner == b"token_count":
                if b'"rate_limits"' not in raw:
                    return
                try:
                    pl = json.loads(raw.decode("utf-8", "replace")).get("payload") or {}
                except Exception:
                    return
                rl = pl.get("rate_limits")
                if isinstance(rl, dict) and ts:
                    self.samples.append((ts, rl))
                    p = rl.get("primary") or {}
                    try:
                        self.last_primary = (ts, float(p["used_percent"]), float(p.get("resets_at") or 0))
                    except Exception:
                        pass
            elif inner == b"item_completed":
                if b'"CommandExecution"' in head:
                    try:
                        item = (json.loads(raw.decode("utf-8", "replace")).get("payload") or {}).get("item") or {}
                    except Exception:
                        return
                    res = _cmd_result(item)
                    if res:
                        res["ts"] = ts
                        self.results.append(res)
                        del self.results[:-12]
                elif b'"Reasoning"' in head:
                    try:
                        item = (json.loads(raw.decode("utf-8", "replace")).get("payload") or {}).get("item") or {}
                    except Exception:
                        return
                    summ = item.get("summary_text") or []
                    if summ and isinstance(summ, list):
                        txt = str(summ[-1]).strip().splitlines()[0] if str(summ[-1]).strip() else ""
                        self._set_activity(ts, "think", _one_line(txt.strip("* "), 100))
        elif outer == b"response_item":
            if inner in (b"custom_tool_call", b"function_call"):
                try:
                    pl = json.loads(raw.decode("utf-8", "replace")).get("payload") or {}
                except Exception:
                    return
                kind, text = describe_tool_call(
                    pl.get("name"), pl.get("input") if inner == b"custom_tool_call" else pl.get("arguments"),
                    pl.get("namespace"),
                )
                cid = str(pl.get("call_id") or pl.get("id") or len(self.pending))
                self.pending[cid] = (ts, kind, text)
                self.last_tool = (ts, kind, text)
                self._set_activity(ts, kind, text)
            elif inner in (b"custom_tool_call_output", b"function_call_output"):
                mm = _CALL_ID_RE.search(raw[:600])
                if mm:
                    self.pending.pop(mm.group(1).decode("utf-8", "replace"), None)
                if self.turn_open:
                    self._set_activity(ts, "after", self.last_tool[2] if self.last_tool else "")
            elif inner == b"reasoning":
                if not self.last_activity or self.last_activity[1] != "think" or (ts or 0) - (self.last_activity[0] or 0) > 2:
                    self._set_activity(ts, "think", "")
            elif inner == b"message" and b'"role":"assistant"' in raw[:600]:
                self._set_activity(ts, "reply", "")

    def read_new(self, size: int, first_tail: int = MONITOR_TAIL_BYTES):
        """Parse bytes appended since the last call (first call: the file tail)."""
        try:
            fh = open(self.path, "rb")
        except OSError:
            return
        with fh:
            if self.offset == 0 and size > first_tail:
                fh.seek(size - first_tail)
                data = fh.read(first_tail)
                nl = data.find(b"\n")
                data = data[nl + 1:] if nl >= 0 else b""
                base = size - len(data)
            else:
                if size < self.offset:
                    self.__init__(self.path)
                fh.seek(self.offset)
                data = fh.read(size - self.offset)
                base = self.offset
        cut = data.rfind(b"\n")
        if cut < 0:
            if self.offset == 0:
                self.offset = base
            return
        self.offset = base + cut + 1
        for raw in data[:cut].split(b"\n"):
            if raw:
                self.feed(raw)

    def drain_samples(self) -> list[tuple]:
        out, self.samples = self.samples, []
        return out


def read_session_meta(path: str) -> dict:
    """First rollout line (session_meta): sub-agent / guardian-review threads carry
    source={"subagent": ...}, thread_source (e.g. "guardian_review") and parent_thread_id."""
    try:
        with open(path, "rb") as fh:
            raw = fh.readline(2 * 1024 * 1024)
        pl = json.loads(raw.decode("utf-8", "replace")).get("payload") or {}
    except Exception:
        return {"sub": False, "parent": None, "kind": None}
    src = pl.get("source")
    parent = pl.get("parent_thread_id")
    kind = pl.get("thread_source")
    sub = bool(parent) or (isinstance(src, dict) and "subagent" in src) or (kind not in (None, "user"))
    return {"sub": sub, "parent": str(parent).lower() if parent else None, "kind": kind}


def classify_task(st: RolloutState, notify: tuple | None, now: float, cfg: dict, review: float | None = None) -> dict:
    """Status rules (documented in README):
      waiting : turn open AND (a desktop "permission"/"question" notification for this thread is
                not older than the newest rollout event, OR a request_user_input call is pending,
                OR a guardian_review sub-thread of this task is mid-turn = auto-review of an
                approval request)
      running : turn open (task_started after the last task_complete/turn_aborted) and the last
                event is younger than idle_minutes
      done    : last turn completed/aborted and the last event is younger than idle_minutes
      idle    : nothing written for idle_minutes (also: an open turn that went silent that long)
      stuck   : flag on running/waiting-less tasks: no new event for stuck_minutes, or the last
                stuck_repeat_count command results in this turn failed the same way
    """
    idle_s = max(60, int(cfg.get("idle_minutes", 30)) * 60)
    stuck_s = max(60, int(cfg.get("stuck_minutes", 5)) * 60)
    last = st.last_ts
    age = (now - last) if last else None
    open_ = st.turn_open
    if open_ is None:  # tail had no turn boundary: a long turn is still writing, or unknown
        open_ = age is not None and age < idle_s
    asking = any(p[1] == "ask" for p in st.pending.values())
    waiting_log = bool(notify and last and notify[0] >= last - 2)
    reviewing = review is not None
    if age is None or age >= idle_s:
        status = "idle"
    elif open_ and (asking or waiting_log or reviewing):
        status = "waiting"
    elif open_:
        status = "running"
    else:
        status = "done"
    stuck = None
    if status == "running":
        if age is not None and age >= stuck_s:
            stuck = {"why": "silent", "age": age}
        rep = detect_repeat_failures(st.results, int(cfg.get("stuck_repeat_count", 3)))
        if rep:
            stuck = {"why": "repeat", "age": age, **rep}
    kind = notify[1] if waiting_log else ("question" if asking else ("review" if reviewing else None))
    return {"status": status, "age": age, "stuck": stuck, "waiting_kind": kind if status == "waiting" else None}


def activity_of(st: RolloutState | None, status: str | None, waiting_kind: str | None = None) -> tuple | None:
    """(kind, text, since_ts) describing what the task is doing now."""
    if st is None:
        return None
    if status == "waiting":
        pend = max(st.pending.values(), key=lambda p: p[0] or 0) if st.pending else None
        what = pend[2] if pend else ""
        return ("review" if waiting_kind == "review" else "await", what, pend[0] if pend else st.last_ts)
    if st.turn_open and st.pending:
        ts, kind, text = max(st.pending.values(), key=lambda p: p[0] or 0)
        return (kind, text, ts)
    if st.last_activity:
        ts, kind, text = st.last_activity
        if not st.turn_open and status in ("done", "idle"):
            last_tool = st.last_tool[2] if st.last_tool else ""
            return ("done" if kind not in ("aborted",) else "aborted", last_tool, st.turn_ended_ts or ts)
        return (kind, text, ts)
    return None


def _rl_window(rl: dict, key: str) -> tuple | None:
    w = rl.get(key) if isinstance(rl, dict) else None
    if not isinstance(w, dict):
        return None
    try:
        used = float(w.get("used_percent"))
    except Exception:
        return None
    resets = w.get("resets_at")
    try:
        resets = float(resets) if resets else None
    except Exception:
        resets = None
    mins = w.get("window_minutes")
    try:
        mins = float(mins) if mins else None
    except Exception:
        mins = None
    return used, resets, mins


class QuotaForecaster:
    """Rate-limit history (rollout token_count events + online polls) -> burn-rate forecast."""

    WINDOWS = {"primary": (300, 10 * 60), "secondary": (10080, 3 * 3600)}  # minutes, min span (s)

    def __init__(self):
        self.samples = {"primary": [], "secondary": []}  # (ts, used, resets_at)
        self.window_minutes = {"primary": 300.0, "secondary": 10080.0}

    def add(self, ts: float, rl: dict):
        for key in ("primary", "secondary"):
            w = _rl_window(rl, key)
            if not w or not w[1]:
                continue
            used, resets, mins = w
            if mins:
                self.window_minutes[key] = mins
            self._insert(key, (ts, used, resets))

    def add_online(self, ts: float, primary: dict | None, secondary: dict | None):
        for key, w in (("primary", primary), ("secondary", secondary)):
            if not isinstance(w, dict) or w.get("used_percent") is None:
                continue
            try:
                used = float(w["used_percent"])
                resets = float(w.get("reset_at") or 0) or (ts + float(w["reset_after_seconds"]))
            except Exception:
                continue
            if w.get("limit_window_seconds"):
                try:
                    self.window_minutes[key] = float(w["limit_window_seconds"]) / 60
                except Exception:
                    pass
            self._insert(key, (ts, used, resets))

    def _insert(self, key: str, sample: tuple):
        arr = self.samples[key]
        if arr and arr[-1][0] <= sample[0]:
            if arr[-1][1:] == sample[1:] and sample[0] - arr[-1][0] < 30:
                return
            arr.append(sample)
        else:
            bisect.insort(arr, sample)
        cutoff = sample[0] - 8 * 86400
        if arr and arr[0][0] < cutoff:
            self.samples[key] = [s for s in arr if s[0] >= cutoff]

    def value_before(self, key: str, ts: float, resets: float | None) -> tuple | None:
        best = None
        for s in self.samples[key]:
            if s[0] > ts:
                break
            if resets is None or abs(s[2] - resets) <= QUOTA_RESET_TOL:
                best = s
        return best

    def forecast(self, key: str, now: float, lookback_s: float) -> dict | None:
        arr = self.samples.get(key) or []
        if not arr:
            return None
        cur = arr[-1]
        ts, used, resets = cur
        win_s = self.window_minutes.get(key, 300.0) * 60
        if resets and resets <= now:
            return {"used": 0.0, "resets_at": None, "reset_passed": True, "rate_h": None, "eta": None, "runs_out": False, "basis": None}
        same = [s for s in arr if abs(s[2] - resets) <= QUOTA_RESET_TOL]
        min_span = self.WINDOWS[key][1]
        recent = [s for s in same if s[0] >= now - lookback_s]
        rate, basis = None, None
        if len(recent) >= 2 and recent[-1][0] - recent[0][0] >= min_span:
            rate = (recent[-1][1] - recent[0][1]) / (recent[-1][0] - recent[0][0])
            basis = "recent"
        else:
            start = resets - win_s
            elapsed = ts - start
            if elapsed >= min_span:
                rate = used / elapsed
                basis = "window"
        eta = None
        if rate is not None and rate > 0:
            eta = ts + max(0.0, 100.0 - used) / rate
        return {
            "used": used,
            "sample_ts": ts,
            "resets_at": resets,
            "reset_passed": False,
            "rate_h": rate * 3600 if rate is not None else None,
            "eta": eta,
            "runs_out": bool(eta is not None and resets and eta < resets),
            "basis": basis,
        }

    def turns_left(self, st: RolloutState | None, now: float, max_turns: int = 5) -> dict | None:
        """Average 5h-% per recent completed turn of a task -> turns the remaining quota allows."""
        if st is None:
            return None
        cur = self.samples["primary"][-1] if self.samples["primary"] else None
        deltas = []
        for t in reversed(st.turns):
            end = t.get("end")
            if not end:
                continue
            start = self.value_before("primary", t["start_ts"], end[2]) or t.get("start")
            if not start or abs(start[2] - end[2]) > QUOTA_RESET_TOL:
                continue  # crosses a 5h reset or no reference point
            deltas.append(max(0.0, end[1] - start[1]))
            if len(deltas) >= max_turns:
                break
        if not deltas or cur is None:
            return None
        avg = sum(deltas) / len(deltas)
        remain = max(0.0, 100.0 - cur[1])
        return {"avg": avg, "n": len(deltas), "left": (remain / avg) if avg > 0 else None, "remain": remain}


def seed_quota_samples(path: str, size: int, limit: int = QUOTA_SEED_BYTES) -> list[tuple]:
    """(ts, rate_limits) from the tail of a rollout, cheap substring filter first."""
    out = []
    try:
        with open(path, "rb") as fh:
            start = max(0, size - limit)
            fh.seek(start)
            data = fh.read(size - start)
    except OSError:
        return out
    for raw in data.split(b"\n"):
        if b'"rate_limits"' not in raw or b'"token_count"' not in raw[:400]:
            continue
        ev = _parse_token_line(raw)
        if ev and ev.get("rate_limits") and ev.get("ts"):
            out.append((ev["ts"], ev["rate_limits"]))
    return out


class DesktopNotifyWatcher:
    """Newest Codex desktop 'permission'/'question' notification per thread, from its logs."""

    def __init__(self, logs_dir: Path = CODEX_LOGS_DIR):
        self.logs_dir = Path(logs_dir)
        self._files: dict[str, int] = {}
        self.latest: dict[str, tuple] = {}  # thread id -> (ts, kind)
        self._helper = SelectedThreadTracker(logs_dir)

    def _parse(self, data: bytes):
        for m in _LOG_NOTIFY_RE.finditer(data):
            ids = _UUID_RE.findall(m.group(1).decode("utf-8", "replace"))
            if not ids:
                continue
            line_start = data.rfind(b"\n", 0, m.start()) + 1
            ts = _parse_iso_utc(data[line_start:line_start + 30].split(b" ", 1)[0].decode("ascii", "replace"))
            if ts:
                tid = ids[0].lower()
                if tid not in self.latest or ts >= self.latest[tid][0]:
                    self.latest[tid] = (ts, m.group(2).decode())

    def poll(self) -> dict[str, tuple]:
        for p in self._helper._log_files():
            try:
                size = os.stat(p).st_size
            except OSError:
                continue
            off = self._files.get(p)
            if off is None:
                off = max(0, size - 2 * 1024 * 1024)  # first sight: recent tail only
            if size < off:
                off = 0
            if size > off:
                try:
                    with open(p, "rb") as fh:
                        fh.seek(off)
                        data = fh.read(size - off)
                except OSError:
                    continue
                cut = data.rfind(b"\n")
                if cut < 0:
                    self._files[p] = off
                    continue
                self._parse(data[:cut])
                off += cut + 1
            self._files[p] = off
        return self.latest


class TaskMonitor:
    """Recently active tasks with status + activity; quota forecast; alert transitions.

    Runs on the HUD's local worker thread. Rollouts are listed at most every
    MONITOR_RESCAN_SEC; tracked files are parsed incrementally (first sight: tail only).
    """

    def __init__(self, reader: "TaskUsageReader", logs_dir: Path | None = CODEX_LOGS_DIR):
        self.reader = reader
        self.notify_watch = DesktopNotifyWatcher(logs_dir) if logs_dir else None
        self.states: dict[str, RolloutState] = {}
        self.forecaster = QuotaForecaster()
        self._scan_at = 0.0
        self._files: list[tuple[str, float]] = []
        self._seeded: set[str] = set()
        self._prev: dict[str, dict] = {}
        self._primed = False
        self._meta: dict[str, dict] = {}
        self._tail_ts: dict[str, tuple] = {}

    def _rescan(self, cfg: dict):
        files = self.reader._list_rollouts()
        now = time.time()
        horizon = max(float(cfg.get("task_list_hours", 6)), QUOTA_SEED_HOURS) * 3600
        by_mtime = sorted(files, key=lambda x: x[1], reverse=True)
        recent = [(p, mt) for p, mt in by_mtime[:40] if now - mt <= horizon]
        # NTFS may report a stale mtime for files Codex keeps open: add newest-by-name too.
        by_name = sorted(files, key=lambda x: os.path.basename(x[0]), reverse=True)[:8]
        seen = {p for p, _ in recent}
        recent += [(p, mt) for p, mt in by_name if p not in seen]
        self._files = recent
        self._scan_at = time.monotonic()

    def poll(self, cfg: dict, selected_path: str | None) -> dict:
        now = time.time()
        if not self._files or time.monotonic() - self._scan_at >= MONITOR_RESCAN_SEC:
            self._rescan(cfg)
        hours = float(cfg.get("task_list_hours", 6)) * 3600
        max_n = int(cfg.get("task_list_max", 5))
        idle_s = max(60, int(cfg.get("idle_minutes", 30)) * 60)
        show_sub = bool(cfg.get("task_list_show_subagents", False))
        sel = getattr(self.reader, "selector", None)
        viewed = dict(sel.history) if sel is not None else {}
        files = list(self._files)
        known = {p for p, _ in files}
        # Tasks opened in the Codex UI recently rank up even if their rollout is old.
        for tid, vts in sorted(viewed.items(), key=lambda kv: kv[1], reverse=True)[:20]:
            if now - vts > hours:
                break
            vp = self.reader._rollout_for_id(tid)
            if vp and vp not in known:
                files.append((vp, 0.0))
                known.add(vp)
        acts = []  # (event_ts, path, size)
        for p, _mt in files:
            try:
                st_ = os.stat(p)
            except OSError:
                continue
            act = st_.st_mtime
            state = self.states.get(p)
            if state is not None and state.last_ts:
                act = max(act, state.last_ts)
            else:
                # NTFS keeps a stale mtime while Codex holds the file open: use the newest
                # line timestamp in the tail (cached per size/mtime).
                sig = (st_.st_size, st_.st_mtime)
                cached = self._tail_ts.get(p)
                if cached is None or cached[0] != sig:
                    cached = (sig, self.reader._tail_timestamp(p, st_.st_size) or 0.0)
                    self._tail_ts[p] = cached
                act = max(act, cached[1])
            acts.append((act, p, st_.st_size))
        acts.sort(reverse=True)
        for _a, p, _s in acts:
            if p not in self._meta and (now - _a <= hours or p == selected_path):
                self._meta[p] = read_session_meta(p)
        last_act: dict[str, float] = {}
        for a, p, _s in acts:
            m = _ROLLOUT_RE.search(os.path.basename(p))
            last_act[p] = max(a, viewed.get(m.group(2).lower(), 0.0) if m else 0.0)
        is_sub = lambda p: bool((self._meta.get(p) or {}).get("sub"))
        # Candidates: the selected task, every task written within idle_minutes (only those
        # can be running/waiting), and the top task_list_max by last activity.
        pool = [x for x in acts if (show_sub or not is_sub(x[1])) and now - last_act[x[1]] <= hours]
        pool.sort(key=lambda x: last_act[x[1]], reverse=True)
        cand = [x for x in pool if now - x[0] < idle_s][:10]
        cpaths = {x[1] for x in cand}
        cand += [x for x in pool if x[1] not in cpaths][:max_n]
        if selected_path and selected_path not in {x[1] for x in cand}:
            try:
                cand.insert(0, (0.0, selected_path, os.stat(selected_path).st_size))
            except OSError:
                pass
        if selected_path and selected_path not in self._meta:
            self._meta[selected_path] = read_session_meta(selected_path)
        cand_paths = {x[1] for x in cand}
        # Active guardian-review / sub-agent threads: tracked (not listed) to mark their parent.
        subs = [x for x in acts if is_sub(x[1]) and now - x[0] <= idle_s and x[1] not in cand_paths][:8]
        tracked = cand + subs
        keep = {p for _, p, _ in tracked}
        # Quota history: rate_limits from other rollouts active in the last 24h (tail, once).
        for a, p, size in acts:
            if p in self._seeded or p in keep:
                continue
            self._seeded.add(p)
            if now - a <= QUOTA_SEED_HOURS * 3600:
                for ts, rl in seed_quota_samples(p, size):
                    self.forecaster.add(ts, rl)
        for _a, p, size in tracked:
            self._seeded.add(p)
            state = self.states.get(p)
            if state is None:
                state = self.states[p] = RolloutState(p)
            if size != state.offset:
                state.read_new(size)
            for ts, rl in state.drain_samples():
                self.forecaster.add(ts, rl)
        for p in list(self.states):
            if p not in keep:
                self.states.pop(p, None)
        notify = {}
        if self.notify_watch is not None:
            try:
                notify = self.notify_watch.poll()
            except Exception:
                notify = {}
        reviews: dict[str, float] = {}  # parent thread id -> guardian review activity ts
        for _a, p, _size in subs:
            meta = self._meta.get(p) or {}
            state = self.states.get(p)
            if meta.get("kind") == "guardian_review" and meta.get("parent") and state is not None:
                if state.turn_open and state.last_ts and now - state.last_ts < idle_s:
                    reviews[meta["parent"]] = state.last_ts
        tasks = []
        for a, p, _size in cand:
            state = self.states.get(p)
            if state is None:
                continue
            m = _ROLLOUT_RE.search(os.path.basename(p))
            tid = m.group(2).lower() if m else None
            info = classify_task(state, notify.get(tid) if tid else None, now, cfg, reviews.get(tid) if tid else None)
            info.update(
                {
                    "id": tid,
                    "path": p,
                    "title": self.reader._title_for(m.group(2) if m else None),
                    "activity": activity_of(state, info["status"], info.get("waiting_kind")),
                    "selected": p == selected_path,
                    "viewed_at": viewed.get(tid) if tid else None,
                    "last_act": max(last_act.get(p, 0.0), state.last_ts or 0.0),
                    "sub": is_sub(p),
                }
            )
            tasks.append(info)
        # Order: selected first (always listed), then running/waiting, then done/idle; each by
        # last activity = max(last rollout event, last time opened in the Codex UI).
        tasks.sort(key=lambda t: (not t["selected"], t["status"] not in ("running", "waiting"), -t["last_act"]))
        for i, t in enumerate(tasks):
            t["listed"] = i < max_n
        events = self._transitions(tasks, cfg)
        sel_state = self.states.get(selected_path) if selected_path else None
        lookback = max(10, int(cfg.get("forecast_lookback_min", 60))) * 60
        forecast = {
            "primary": self.forecaster.forecast("primary", now, lookback),
            "secondary": self.forecaster.forecast("secondary", now, 24 * 3600),
            "turns": self.forecaster.turns_left(sel_state, now),
        }
        return {"tasks": tasks, "events": events, "forecast": forecast, "at": now}

    def _transitions(self, tasks: list[dict], cfg: dict) -> list[dict]:
        """Alert events vs the previous poll (the first poll only sets the baseline)."""
        events = []
        cur = {}
        for t in tasks:
            tid = t.get("id")
            if not tid:
                continue
            prev = self._prev.get(tid)
            stuck_key = None
            if t.get("stuck"):
                stuck_key = t["stuck"]["why"]
            user_wait = t["status"] == "waiting" and t.get("waiting_kind") in ("permission", "question")
            cur[tid] = {"status": t["status"], "stuck": stuck_key, "user_wait": user_wait}
            if not self._primed or prev is None:
                continue
            if user_wait and not prev.get("user_wait"):
                events.append({"kind": "approval", "task": t})
            if t["status"] == "done" and prev["status"] in ("running", "waiting"):
                events.append({"kind": "complete", "task": t})
            if stuck_key and prev.get("stuck") != stuck_key:
                events.append({"kind": "stuck", "task": t})
        self._prev.update(cur)
        self._primed = True
        return events


def show_windows_toast(title: str, body: str, timeout: float = 20.0) -> tuple[bool, str]:
    """Dependency-free Windows toast: WinRT ToastNotificationManager via powershell.exe.

    Uses Windows PowerShell's registered AppUserModelID so no shortcut/registration is
    needed. Blocking (~1 s); call from a worker thread. Returns (ok, error text).
    """
    import base64
    import subprocess

    def esc(s: str) -> str:
        return str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;")

    xml = (
        '<toast><visual><binding template="ToastGeneric"><text>%s</text><text>%s</text>'
        "</binding></visual></toast>" % (esc(title), esc(body))
    )
    script = (
        "$ErrorActionPreference='Stop';"
        "[Windows.UI.Notifications.ToastNotificationManager,Windows.UI.Notifications,ContentType=WindowsRuntime]|Out-Null;"
        "[Windows.Data.Xml.Dom.XmlDocument,Windows.Data.Xml.Dom.XmlDocument,ContentType=WindowsRuntime]|Out-Null;"
        "$x=New-Object Windows.Data.Xml.Dom.XmlDocument;"
        "$x.LoadXml([Text.Encoding]::UTF8.GetString([Convert]::FromBase64String('%s')));"
        "$t=[Windows.UI.Notifications.ToastNotification]::new($x);"
        "[Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier('%s').Show($t)"
    ) % (base64.b64encode(xml.encode("utf-8")).decode(), TOAST_AUMID)
    enc = base64.b64encode(script.encode("utf-16-le")).decode()
    try:
        r = subprocess.run(
            ["powershell.exe", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-EncodedCommand", enc],
            capture_output=True,
            timeout=timeout,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000),
        )
    except Exception as e:
        return False, str(e)
    if r.returncode != 0:
        return False, r.stderr.decode("utf-8", "replace").strip()[:300]
    return True, ""


def open_codex_thread(tid: str) -> bool:
    """Open a thread in the Codex desktop app via its registered codex:// protocol
    (MSIX windows.protocol; the app routes codex://threads/<id> to the local conversation)."""
    if not tid or not _UUID_RE.fullmatch(tid):
        return False
    try:
        os.startfile(f"codex://threads/{tid}")  # type: ignore[attr-defined]
        return True
    except Exception:
        return False


def _focus_existing_hwnd(hwnd: int) -> bool:
    user32 = ctypes.windll.user32
    if not hwnd or not user32.IsWindow(hwnd):
        return False
    SW_RESTORE = 9
    user32.ShowWindow(hwnd, SW_RESTORE)
    # Allow SetForegroundWindow from a second process.
    try:
        user32.AllowSetForegroundWindow(-1)  # ASFW_ANY
    except Exception:
        pass
    foreground = user32.GetForegroundWindow()
    tid_target = user32.GetWindowThreadProcessId(hwnd, None)
    tid_this = ctypes.windll.kernel32.GetCurrentThreadId()
    if foreground:
        tid_fore = user32.GetWindowThreadProcessId(foreground, None)
        if tid_fore and tid_this:
            user32.AttachThreadInput(tid_fore, tid_this, True)
        user32.ShowWindow(hwnd, SW_RESTORE)
        user32.BringWindowToTop(hwnd)
        user32.SetForegroundWindow(hwnd)
        if tid_fore and tid_this:
            user32.AttachThreadInput(tid_fore, tid_this, False)
    else:
        user32.SetForegroundWindow(hwnd)
    return True


def acquire_single_instance() -> ctypes.c_void_p | None:
    """Return mutex handle if this is the primary instance; otherwise focus existing and return None."""
    kernel32 = ctypes.windll.kernel32
    ERROR_ALREADY_EXISTS = 183
    handle = kernel32.CreateMutexW(None, False, SINGLETON_MUTEX)
    if not handle:
        return ctypes.c_void_p(1)  # fail open: allow run
    if kernel32.GetLastError() == ERROR_ALREADY_EXISTS:
        try:
            hwnd = int(HWND_PATH.read_text(encoding="utf-8").strip())
        except Exception:
            hwnd = 0
        if not _focus_existing_hwnd(hwnd):
            # Fallback: find by title prefix.
            titles = ["CODEX // USAGE", "CODEX // 用量"]
            found = ctypes.c_void_p()

            @ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)
            def _enum(hwnd, _lparam):
                buf = ctypes.create_unicode_buffer(512)
                ctypes.windll.user32.GetWindowTextW(hwnd, buf, 512)
                title = buf.value or ""
                if any(title.startswith(t) or t in title for t in titles):
                    found.value = hwnd
                    return False
                return True

            ctypes.windll.user32.EnumWindows(_enum, 0)
            if found.value:
                _focus_existing_hwnd(int(found.value))
        try:
            kernel32.CloseHandle(handle)
        except Exception:
            pass
        return None
    return handle


class Meter(tk.Canvas):
    def __init__(self, master, height=18, **kw):
        super().__init__(master, height=height, bg=PANEL, highlightthickness=0, bd=0, **kw)
        self._value = 0.0
        self.bind("<Configure>", lambda e: self.redraw())

    def set_value(self, used: float):
        self._value = max(0.0, min(100.0, float(used)))
        self.redraw()

    def _color(self):
        if self._value >= 90:
            return RED
        if self._value >= 70:
            return AMBER
        return CYAN

    def redraw(self):
        self.delete("all")
        w = self.winfo_width() or 1
        h = self.winfo_height() or 18
        self.create_rectangle(0, 0, w, h, fill=PANEL2, outline=LINE)
        # scanlines
        for y in range(2, h, 3):
            self.create_line(1, y, w - 1, y, fill=GRID)
        fill_w = int((w - 4) * self._value / 100.0)
        if fill_w > 0:
            self.create_rectangle(2, 2, 2 + fill_w, h - 2, fill=self._color(), outline="")
            # leading tick
            self.create_line(2 + fill_w, 1, 2 + fill_w, h - 1, fill="#eafff9")


class Hud(tk.Tk):
    def __init__(self):
        super().__init__()
        self._cfg = load_ui_config()
        self.title("CODEX // USAGE")
        self.geometry("460x470+80+80")
        self.minsize(420, 430)
        self.configure(bg=BG)
        self.attributes("-topmost", bool(self._cfg.get("topmost", True)))
        try:
            self.attributes("-alpha", 0.97)
        except Exception:
            pass

        self._stop = threading.Event()
        self._local_wake = threading.Event()  # task-list click: poll local files right away
        self._toast_error = None
        self._lock = threading.Lock()
        self._msg = "BOOT"
        self._data = None
        self._refreshing = False
        self._tick = 0
        self._next_due = time.monotonic()
        self._task_reader = TaskUsageReader()
        self._task_reader.follow_selected = bool(self._cfg.get("task_follow_selected", True))
        self._prices = merged_model_prices(self._cfg)
        self._task = None  # latest TaskUsageReader snapshot
        self._task_polling = False
        self._mini_tokens = None  # capsule task text: None = hidden, "—" = no data
        self._monitor = TaskMonitor(self._task_reader)
        self._mon_lock = threading.Lock()  # TaskMonitor state: worker thread + online feed
        self._mon = None  # latest TaskMonitor.poll() result
        self._notify_last: dict = {}  # (task id, kind) -> time.time() of the last notification

        self._font_title = tkfont.Font(family="Consolas", size=13, weight="bold")
        self._font_mono = tkfont.Font(family="Consolas", size=9)
        self._font_big = tkfont.Font(family="Consolas", size=20, weight="bold")
        self._font_tiny = tkfont.Font(family="Consolas", size=8)
        self._apply_fonts()
        self.title(self.t("window_title"))

        self._build()
        self.after(200, self._publish_hwnd)
        self.after(120, self.refresh_now)
        threading.Thread(target=self._loop, daemon=True).start()
        threading.Thread(target=self._local_loop, daemon=True).start()
        self.after(250, self._pulse)

    def lang(self) -> str:
        return "zh" if str(self._cfg.get("lang") or "en") == "zh" else "en"

    def t(self, key: str, **kwargs) -> str:
        table = STRINGS.get(self.lang()) or STRINGS["en"]
        text = table.get(key) or STRINGS["en"].get(key) or key
        if kwargs:
            try:
                return text.format(**kwargs)
            except Exception:
                return text
        return text

    def _apply_fonts(self):
        # Numbers / English stay Consolas in both languages.
        # Chinese labels use YaHei at the same point sizes (no scale-up).
        self._font_title.configure(family="Consolas", size=13, weight="bold")
        self._font_mono.configure(family="Consolas", size=9)
        self._font_big.configure(family="Consolas", size=20, weight="bold")
        self._font_tiny.configure(family="Consolas", size=8)
        cjk = "Microsoft YaHei UI"
        try:
            tkfont.Font(family=cjk, size=9).actual()
        except Exception:
            cjk = "Microsoft YaHei"
        if not hasattr(self, "_font_cjk"):
            self._font_cjk = tkfont.Font(family=cjk, size=9)
            self._font_cjk_tiny = tkfont.Font(family=cjk, size=8)
        else:
            self._font_cjk.configure(family=cjk, size=9)
            self._font_cjk_tiny.configure(family=cjk, size=8)

    def _build(self):
        header = tk.Frame(self, bg=BG)
        header.pack(fill="x", padx=14, pady=(12, 6))
        self.brand = tk.Label(header, text=self.t("brand"), fg=CYAN, bg=BG, font=self._font_title)
        self.brand.pack(side="left")
        right = tk.Frame(header, bg=BG)
        right.pack(side="right")
        self.lang_btn = self._btn(right, self.t("lang_switch"), self.toggle_lang)
        self.lang_btn.configure(width=4)
        self.lang_btn.pack(side="left")
        # Fixed character width so countdown digits never shove the EN/中文 button.
        self.hdr_status = tk.Label(
            right,
            text=self.t("online"),
            fg=MUTED,
            bg=BG,
            font=self._font_tiny,
            width=10,
            anchor="e",
        )
        self.hdr_status.pack(side="left", padx=(8, 0))

        self.meta = tk.Label(self, text=self.t("boot"), fg=MUTED, bg=BG, font=self._font_mono, anchor="w", justify="left")
        self.meta.pack(fill="x", padx=14)

        self.card5 = self._card(self.t("card5"))
        self.card7 = self._card(self.t("card7"))
        self.task_card = self._task_card()
        # Quota forecast: one line inside each window card (detail mode), between text and meter.
        for card in (self.card5, self.card7):
            card["fc"] = tk.Label(card["wrap"], text="", fg=MUTED, bg=PANEL, font=self._font_mono, anchor="w")
        self.tasks_sec = self._section("sec_tasks", "section_tasks_open")
        self.task_rows = []
        for i in range(10):
            row = tk.Frame(self.tasks_sec["body"], bg=PANEL, cursor="hand2")
            tag = tk.Label(row, text="", fg=MUTED, bg=PANEL, font=self._font_tiny, width=7, anchor="w", cursor="hand2")
            tag.pack(side="left")
            age = tk.Label(row, text="", fg=MUTED, bg=PANEL, font=self._font_tiny, anchor="e", cursor="hand2")
            age.pack(side="right")
            title = tk.Label(row, text="", fg=TEXT, bg=PANEL, font=self._font_tiny, anchor="w", cursor="hand2")
            title.pack(side="left", fill="x", expand=True)
            for w in (row, tag, title, age):
                w.bind("<Button-1>", lambda e, i=i: self._task_row_click(i, False))
                w.bind("<Button-3>", lambda e, i=i: self._task_row_click(i, True))
                w.bind("<Enter>", lambda e, r=row: self._row_hover(r, True))
                w.bind("<Leave>", lambda e, r=row: self._row_hover(r, False))
            self.task_rows.append({"row": row, "tag": tag, "title": title, "age": age, "task": None})
        self.tasks_empty = tk.Label(self.tasks_sec["body"], text="", fg=MUTED, bg=PANEL, font=self._font_mono, anchor="w")
        # Compact mode: one status line (task counts / alerts / quota warning), hidden when empty.
        self.compact_status = tk.Label(
            self, text="", fg=AMBER, bg=BG, font=self._font_mono, anchor="w", justify="left"
        )

        # Account / credits rows: collapsible (collapsed by default to keep detail mode short).
        self.extra_sec = self._section("sec_account", "section_account_open")
        self.extra_wrap = self.extra_sec["wrap"]
        self.extra_wrap.pack(fill="both", expand=True, padx=14, pady=(0, 8))
        self.extra_body = self.extra_sec["body"]
        self.extra_rows = []
        for _ in range(6):
            row = tk.Frame(self.extra_body, bg=PANEL)
            row.pack(fill="x", pady=1)
            lab = tk.Label(row, text="", fg=MUTED, bg=PANEL, font=self._font_cjk, width=12, anchor="w")
            lab.pack(side="left")
            val = tk.Label(row, text="", fg=TEXT, bg=PANEL, font=self._font_mono, anchor="w")
            val.pack(side="left", fill="x", expand=True)
            self.extra_rows.append((lab, val))

        ctrl = tk.Frame(self, bg=BG)
        self.ctrl = ctrl
        ctrl.pack(fill="x", padx=14, pady=(0, 6))
        self.refresh_label = tk.Label(ctrl, text=self.t("refresh_label"), fg=MUTED, bg=BG, font=self._font_tiny)
        self.refresh_label.pack(side="left")
        self.refresh_var = tk.StringVar(value=str(self._cfg["refresh_sec"]))
        entry = tk.Entry(
            ctrl,
            textvariable=self.refresh_var,
            width=6,
            bg=PANEL2,
            fg=CYAN,
            insertbackground=CYAN,
            relief="flat",
            font=self._font_mono,
            highlightthickness=1,
            highlightbackground=LINE,
            highlightcolor=CYAN,
        )
        entry.pack(side="left", padx=(8, 4))
        entry.bind("<Return>", lambda e: self.apply_refresh())
        self.apply_btn = self._btn(ctrl, self.t("apply"), self.apply_refresh)
        self.apply_btn.pack(side="left", padx=4)
        self.topmost_var = tk.BooleanVar(value=bool(self._cfg.get("topmost", True)))
        self.pin_btn = tk.Checkbutton(
            ctrl,
            text=self.t("pin"),
            variable=self.topmost_var,
            command=self._toggle_topmost,
            bg=BG,
            fg=MUTED,
            selectcolor=PANEL2,
            activebackground=BG,
            activeforeground=CYAN,
            font=self._font_tiny,
        )
        self.pin_btn.pack(side="right")

        btns = tk.Frame(self, bg=BG)
        self.btns = btns
        btns.pack(fill="x", padx=14, pady=(0, 12))
        self.sync_btn = self._btn(btns, self.t("sync_now"), self.refresh_now)
        self.sync_btn.pack(side="left")
        self.open_btn = self._btn(btns, self.t("open_usage"), lambda: webbrowser.open(USAGE_PAGE))
        self.open_btn.pack(side="left", padx=8)
        self.mode_btn = self._btn(btns, self.t("compact"), self.toggle_mode)
        self.mode_btn.pack(side="right")
        self.mini_btn = self._btn(btns, self.t("mini"), self.enter_mini)
        self.mini_btn.pack(side="right", padx=(0, 8))

        self.apply_mode()

    def _btn(self, parent, text, cmd):
        return tk.Button(
            parent,
            text=text,
            command=cmd,
            bg=PANEL2,
            fg=CYAN,
            activebackground=CYAN_DIM,
            activeforeground="#04110e",
            relief="flat",
            font=self._font_tiny,
            padx=10,
            pady=4,
            highlightthickness=1,
            highlightbackground=LINE,
            cursor="hand2",
        )

    def _card(self, title: str):
        wrap = tk.Frame(self, bg=PANEL, highlightbackground=LINE, highlightthickness=1)
        wrap.pack(fill="x", padx=14, pady=6)
        head = tk.Frame(wrap, bg=PANEL)
        head.pack(fill="x", padx=10, pady=(8, 2))
        title_l = tk.Label(head, text=title, fg=MUTED, bg=PANEL, font=self._font_tiny)
        title_l.pack(side="left")
        pct = tk.Label(head, text="00%", fg=CYAN, bg=PANEL, font=self._font_big)
        pct.pack(side="right")
        detail = tk.Label(wrap, text=self.t("awaiting"), fg=TEXT, bg=PANEL, font=self._font_mono, anchor="w")
        detail.pack(fill="x", padx=10)
        meter = Meter(wrap, height=16)
        meter.pack(fill="x", padx=10, pady=(6, 10))
        return {"wrap": wrap, "title": title_l, "pct": pct, "detail": detail, "meter": meter}

    def _section(self, title_key: str, cfg_key: str):
        """Collapsible panel: clicking the header toggles the body (state kept in config)."""
        wrap = tk.Frame(self, bg=PANEL, highlightbackground=LINE, highlightthickness=1)
        head = tk.Frame(wrap, bg=PANEL, cursor="hand2")
        head.pack(fill="x", padx=10, pady=(6, 6))
        title = tk.Label(head, text="", fg=MUTED, bg=PANEL, font=self._font_tiny, cursor="hand2")
        title.pack(side="left")
        summary = tk.Label(head, text="", fg=TEXT, bg=PANEL, font=self._font_tiny, anchor="e", cursor="hand2")
        summary.pack(side="right")
        body = tk.Frame(wrap, bg=PANEL)
        sec = {"wrap": wrap, "head": head, "title": title, "summary": summary, "body": body,
               "key": title_key, "cfg": cfg_key}
        for w in (head, title, summary):
            w.bind("<Button-1>", lambda e, s=sec: self._toggle_section(s))
        self._sync_section(sec)
        return sec

    def _sync_section(self, sec):
        is_open = bool(self._cfg.get(sec["cfg"], True))
        arrow = "▾" if is_open else "▸"
        sec["title"].configure(text=f"{arrow} {self.t(sec['key'])}", font=self._tiny_label_font())
        if is_open:
            sec["head"].pack_configure(pady=(5, 1))
            sec["body"].pack(fill="x", padx=10, pady=(0, 6))
        else:
            sec["head"].pack_configure(pady=(5, 5))
            sec["body"].pack_forget()

    def _toggle_section(self, sec):
        self._cfg[sec["cfg"]] = not bool(self._cfg.get(sec["cfg"], True))
        save_ui_config(self._cfg)
        self._sync_section(sec)
        self._fit_window()

    def _task_card(self):
        card = self._card(self.t("task_card"))
        wrap = card["wrap"]
        # Extra lines sit between the headline detail and the context meter:
        # [0] tokens [1] cache [2] cost [3] ctx/age [4] live activity [5] alert (packed only when set)
        lines = []
        for _ in range(6):
            lab = tk.Label(wrap, text="", fg=TEXT, bg=PANEL, font=self._font_mono, anchor="w")
            lines.append(lab)
        card["lines"] = lines
        card["pct"].configure(text="--")
        card["detail"].configure(text=self.t("task_wait"), fg=MUTED)
        return card

    def current_refresh_sec(self) -> int:
        try:
            sec = int(str(self.refresh_var.get()).strip())
        except Exception:
            sec = self._cfg.get("refresh_sec", DEFAULT_REFRESH_SEC)
        return max(MIN_REFRESH_SEC, min(MAX_REFRESH_SEC, sec))

    def apply_refresh(self):
        sec = self.current_refresh_sec()
        self.refresh_var.set(str(sec))
        self._cfg["refresh_sec"] = sec
        save_ui_config(self._cfg)
        self._next_due = time.monotonic() + sec
        self.hdr_status.configure(text=self.t("interval", sec=sec))

    def _toggle_topmost(self):
        on = bool(self.topmost_var.get())
        self.attributes("-topmost", on)
        self._cfg["topmost"] = on
        save_ui_config(self._cfg)

    def toggle_lang(self):
        self._cfg["lang"] = "zh" if self.lang() == "en" else "en"
        save_ui_config(self._cfg)
        self.apply_lang()

    def apply_lang(self):
        self._apply_fonts()
        self.title(self.t("window_title"))
        self.brand.configure(text=self.t("brand"), font=self._font_title)
        self.lang_btn.configure(text=self.t("lang_switch"), font=self._font_tiny, width=4)
        self.refresh_label.configure(text=self.t("refresh_label"), font=self._tiny_label_font())
        self.apply_btn.configure(text=self.t("apply"), font=self._font_tiny)
        self.pin_btn.configure(text=self.t("pin"), font=self._tiny_label_font())
        self.sync_btn.configure(text=self.t("sync_now"), font=self._font_tiny)
        self.open_btn.configure(text=self.t("open_usage"), font=self._font_tiny)
        self.mode_btn.configure(font=self._font_tiny)
        self.hdr_status.configure(font=self._font_tiny)
        self.meta.configure(font=self._label_font())
        for card in (self.card5, self.card7, self.task_card):
            card["title"].configure(font=self._tiny_label_font())
            card["pct"].configure(font=self._font_big)
            card["detail"].configure(font=self._label_font())
        for lab in self.task_card["lines"]:
            lab.configure(font=self._label_font())
        for sec in (self.tasks_sec, self.extra_sec):
            self._sync_section(sec)
            sec["summary"].configure(font=self._tiny_label_font())
        for card in (self.card5, self.card7):
            card["fc"].configure(font=self._label_font())
        for row in self.task_rows:
            row["tag"].configure(font=self._tiny_label_font())
            row["title"].configure(font=self._tiny_label_font())
        self.tasks_empty.configure(font=self._label_font())
        self.compact_status.configure(font=self._label_font())
        for lab, val in self.extra_rows:
            lab.configure(font=self._label_font(), width=12)
            val.configure(font=self._font_mono)
        if hasattr(self, "compact_sync_btn"):
            self.compact_sync_btn.configure(text=self.t("sync"), font=self._font_tiny)
            self.compact_mode_btn.configure(font=self._font_tiny)
        if hasattr(self, "mini_btn"):
            self.mini_btn.configure(text=self.t("mini"), font=self._font_tiny)
        if hasattr(self, "compact_mini_btn"):
            self.compact_mini_btn.configure(text=self.t("mini"), font=self._font_tiny)
        if self.is_mini():
            self._redraw_capsule()
        self.apply_mode()
        if self._msg not in ("BOOT",):
            self._paint()
        else:
            self.meta.configure(text=self.t("boot"))
        self._paint_task()

    def _loop(self):
        while not self._stop.is_set():
            now = time.monotonic()
            due = self._next_due
            if now >= due:
                self.after(0, self.refresh_now)
                # next due is set after paint; fallback if refresh hangs
                self._next_due = now + max(MIN_REFRESH_SEC, self.current_refresh_sec())
            self._stop.wait(0.4)

    def current_local_refresh_sec(self) -> int:
        try:
            sec = int(self._cfg.get("local_refresh_sec", DEFAULT_LOCAL_REFRESH_SEC))
        except Exception:
            sec = DEFAULT_LOCAL_REFRESH_SEC
        return max(MIN_LOCAL_REFRESH_SEC, min(MAX_LOCAL_REFRESH_SEC, sec))

    def _local_loop(self):
        # Local rollout polling: independent of the online sync, never on the UI thread.
        while not self._stop.is_set():
            try:
                snap = self._task_reader.poll()
            except Exception:
                snap = None
            mon = None
            try:
                with self._mon_lock:
                    mon = self._monitor.poll(self._cfg, (snap or {}).get("path"))
            except Exception:
                mon = None
            with self._lock:
                self._task = snap
                self._mon = mon
            if mon and mon.get("events"):
                try:
                    self._dispatch_events(mon["events"])
                except Exception:
                    pass
            try:
                self.after(0, self._paint_task)
            except Exception:
                return
            self._local_wake.wait(self.current_local_refresh_sec())
            self._local_wake.clear()

    def _task_age_text(self, snap: dict | None) -> str:
        ts = (snap or {}).get("updated_ts")
        return _fmt_age(time.time() - ts if ts else None, self.lang())

    def _task_title_text(self, snap: dict) -> str:
        title = (snap.get("title") or "").strip()
        if not title:
            sid = snap.get("id") or "?"
            title = f"#{sid[-8:]}"
        limit = 20 if any(ord(ch) > 0x2E7F for ch in title) else 30
        if len(title) > limit:
            title = title[: limit - 1] + "…"
        started = snap.get("started")
        if isinstance(started, datetime):
            fmt = "%H:%M" if started.date() == datetime.now().date() else "%m-%d %H:%M"
            stamp = started.strftime(fmt)
        else:
            stamp = "--"
        return self.t("task_title_line", title=title, started=stamp, model=snap.get("model") or "").rstrip()

    def _paint_task(self):
        if not hasattr(self, "task_card"):
            return
        with self._lock:
            snap = self._task
        card = self.task_card
        view = task_view(snap, self._prices)
        compact = self.is_compact()
        font = self._label_font()
        if not view:
            card["pct"].configure(text="--", fg=MUTED)
            card["detail"].configure(
                text=self.t("task_wait" if snap else "task_none"), fg=MUTED, font=font
            )
            if snap and not compact:
                card["lines"][0].configure(text=self._task_title_text(snap), font=font)
                for lab in card["lines"][1:]:
                    lab.configure(text="")
            else:
                for lab in card["lines"]:
                    lab.configure(text="")
            card["meter"].set_value(0)
            tokens_mini = None
        else:
            pct = view["ctx_pct"]
            card["pct"].configure(text=f"{pct:5.1f}%", fg=self._pct_color(pct))
            ctx_txt = _fmt_tokens(view["ctx"])
            win_txt = _fmt_tokens(view["window"]) if view["window"] else "--"
            if compact:
                card["detail"].configure(
                    text=self.t(
                        "task_compact_line",
                        total=_fmt_tokens(view["total"]),
                        ctx=ctx_txt,
                        window=win_txt,
                        cost=self._cost_total_text(view.get("cost")),
                    ),
                    fg=TEXT,
                    font=font,
                )
            else:
                card["detail"].configure(text=self._task_title_text(snap), fg=MUTED, font=font)
                card["lines"][0].configure(
                    text=self.t(
                        "task_tokens_line",
                        total=_fmt_tokens(view["total"]),
                        inp=_fmt_tokens(view["input"]),
                        out=_fmt_tokens(view["output"]),
                    ),
                    font=font,
                )
                card["lines"][1].configure(
                    text=self.t(
                        "task_cache_line",
                        cached=_fmt_tokens(view["cached"]),
                        hit=view["hit"],
                        reasoning=_fmt_tokens(view["reasoning"]),
                    ),
                    font=font,
                )
                card["lines"][2].configure(text=self._cost_line_text(view.get("cost")), font=font)
                card["lines"][3].configure(
                    text=self.t("task_ctx_line", ctx=ctx_txt, window=win_txt, age=self._task_age_text(snap)),
                    font=font,
                )
            card["meter"].set_value(pct)
            tokens_mini = _fmt_tokens(view["total"])
        self._paint_monitor()
        if not self._cfg.get("mini_task_tokens", True):
            new_mini = None
        else:
            new_mini = tokens_mini or "—"
        if new_mini != self._mini_tokens:
            self._mini_tokens = new_mini
            if self.is_mini():
                self._redraw_capsule()

    # -- task monitor UI -------------------------------------------------
    @staticmethod
    def _clip(text: str, cols: int) -> str:
        """Truncate to a display width (CJK counts double) so labels never widen the window."""
        text = str(text or "")
        width, out = 0, []
        for ch in text:
            w = 2 if ord(ch) > 0x2E7F else 1
            if width + w > cols:
                return "".join(out).rstrip() + "…"
            out.append(ch)
            width += w
        return text

    def _clock(self, ts: float | None) -> str:
        """HH:MM today, weekday + HH:MM within 6 days, else MM-DD HH:MM (local time)."""
        if not ts:
            return "--"
        dt = datetime.fromtimestamp(ts)
        days = (dt.date() - datetime.now().date()).days
        if days == 0:
            return dt.strftime("%H:%M")
        if 0 < days <= 6:
            wd = ("周一", "周二", "周三", "周四", "周五", "周六", "周日") if self.lang() == "zh" else (
                "Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")
            return f"{wd[dt.weekday()]} {dt.strftime('%H:%M')}"
        return dt.strftime("%m-%d %H:%M")

    def _task_name(self, task: dict | None) -> str:
        if not task:
            return "?"
        title = (task.get("title") or "").strip()
        return title or f"#{(task.get('id') or '?')[-8:]}"

    def _activity_text(self, act) -> str:
        prefix = self.t("act_prefix")
        if not act:
            return self._clip(self.t("act_line", prefix=prefix, what="—", age="").rstrip(), 54)
        kind, text, ts = act
        what = self.t("act_" + kind, text=_one_line(text, 120)).strip()
        age = _fmt_age(time.time() - ts, self.lang()) if ts else ""
        age_w = len(age) + 4
        return self.t("act_line", prefix=prefix, what=self._clip(what, 54 - age_w - len(prefix)), age=age).rstrip()

    def _alert(self, tasks: list, sel: dict | None):
        """Most important alert: selected task first, then any stuck task, then any waiting."""
        ordered = ([sel] if sel else []) + [t for t in tasks if t is not sel]
        for t in ordered:
            st = t.get("stuck")
            if st and st.get("why") == "repeat":
                return self.t("alert_repeat", n=st.get("count"), cmd=_one_line(st.get("cmd"), 80)), RED
            if st:
                return self.t("alert_silent", age=_fmt_age(st.get("age"), self.lang()), title=self._task_name(t)), AMBER
        for t in ordered:
            if t.get("status") == "waiting" and t.get("waiting_kind") != "review":
                return self.t("alert_waiting", title=self._task_name(t)), AMBER
        return None

    def _fc_text(self, key: str, fc: dict | None):
        """Forecast line for a window card: burn rate -> time of 100% vs the card's reset."""
        if not fc or fc.get("rate_h") is None and not fc.get("reset_passed"):
            return self.t("fc_na"), MUTED
        if fc.get("reset_passed"):
            return self.t("fc_reset"), MUTED
        rate = self.t("rate_h", v=fc["rate_h"]) if key == "primary" else self.t("rate_d", v=fc["rate_h"] * 24)
        basis = self.t("fc_basis_window") if fc.get("basis") == "window" else ""
        if fc.get("eta") is None:
            return self.t("fc_flat"), MUTED
        eta = self._clock(fc["eta"])
        if fc.get("runs_out"):
            soon = fc["eta"] - time.time() < 3600
            return self.t("fc_runs_out", rate=rate, basis=basis, eta=eta), (RED if soon else AMBER)
        return self.t("fc_ok", rate=rate, basis=basis, eta=eta), MUTED

    def _turns_text(self, turns: dict | None) -> str:
        """Suffix for the 5h forecast line: turns of the shown task the remaining 5h quota allows."""
        if not turns:
            return ""
        if turns.get("left") is None:
            return self.t("fc_turns_inf")
        left = turns["left"]
        left_txt = "999+" if left >= 999 else f"{left:.0f}"
        return self.t("fc_turns", left=left_txt, avg=turns["avg"], n=turns["n"])

    def _status_counts(self, tasks: list) -> str:
        cnt = {"running": 0, "waiting": 0, "stuck": 0}
        for t in tasks:
            if t.get("stuck"):
                cnt["stuck"] += 1
            elif t.get("status") in cnt:
                cnt[t["status"]] += 1
        parts = [self.t("cnt_" + k, n=v) for k, v in cnt.items() if v]
        return " · ".join(parts)

    def _paint_monitor(self):
        if not hasattr(self, "tasks_sec"):
            return
        with self._lock:
            mon = self._mon
            snap = self._task
        mode = self.mode()
        font = self._label_font()
        tasks = (mon or {}).get("tasks") or []
        max_n = int(self._cfg.get("task_list_max", 5))
        listed = [t for t in tasks if t.get("listed")][:max_n]
        sel = next((t for t in tasks if t.get("selected")), None)
        card = self.task_card
        act = sel.get("activity") if sel else None
        self._act_cache = act
        alert = self._alert(listed + ([sel] if sel and sel not in listed else []), sel)
        fc = (mon or {}).get("forecast") or {}
        fc_on = bool(self._cfg.get("forecast_enabled", True))
        # detail: activity + alert lines in the task card
        if mode == "detail":
            status = (sel or {}).get("status")
            color = CYAN if status == "running" else AMBER if status == "waiting" else MUTED
            card["lines"][4].configure(text=self._activity_text(act) if snap else "", fg=color, font=font)
            lab = card["lines"][5]
            if alert:
                lab.configure(text=self._clip(alert[0], 54), fg=alert[1], font=font)
                if not lab.winfo_ismapped():
                    lab.pack(fill="x", padx=10, before=card["meter"])
            else:
                lab.configure(text="")
                if lab.winfo_ismapped():
                    lab.pack_forget()
            # forecast: one line per window card (+ turns estimate on the 5h card)
            for key, card_ in (("primary", self.card5), ("secondary", self.card7)):
                txt, col = self._fc_text(key, fc.get(key))
                if key == "primary":
                    txt += self._turns_text(fc.get("turns"))
                card_["fc"].configure(text=self._clip(txt, 56), fg=col, font=font)
            # task list section
            if not getattr(self, "_rows_hover", False):
                self.tasks_sec["summary"].configure(
                    text=self._status_counts(listed), font=self._tiny_label_font(),
                    fg=AMBER if any(t.get("stuck") or t.get("status") == "waiting" for t in listed) else TEXT)
            shown = 0
            for i, row in enumerate(self.task_rows):
                t = listed[i] if i < len(listed) else None
                row["task"] = t
                if t is None:
                    continue
                shown += 1
                key = "stuck" if t.get("stuck") else t.get("status", "idle")
                tag_col = {"running": CYAN, "waiting": AMBER, "stuck": RED, "done": CYAN_DIM}.get(key, MUTED)
                row["tag"].configure(text=self.t("st_" + key), fg=tag_col, font=self._tiny_label_font())
                name = ("▶ " if t.get("selected") else "") + self._task_name(t)
                row["title"].configure(text=self._clip(name, 44), fg=CYAN if t.get("selected") else TEXT,
                                       font=self._tiny_label_font())
                row["age"].configure(text=_fmt_age(t.get("age"), self.lang()), font=self._font_tiny)
                bg = SEL_BG if t.get("selected") else PANEL
                if row["row"].cget("bg") not in (bg, PANEL2):
                    for w in (row["row"], row["tag"], row["title"], row["age"]):
                        w.configure(bg=bg)
            if getattr(self, "_rows_shown", None) != shown:
                for row in self.task_rows:
                    row["row"].pack_forget()
                for row in self.task_rows[:shown]:
                    row["row"].pack(fill="x", pady=0)
                self.tasks_empty.pack_forget()
                self._rows_shown = shown
            if shown == 0:
                self.tasks_empty.configure(text=self.t("tasks_none", h=self._cfg.get("task_list_hours", 6)), font=font)
                if not self.tasks_empty.winfo_ismapped():
                    self.tasks_empty.pack(fill="x")
            elif self.tasks_empty.winfo_ismapped():
                self.tasks_empty.pack_forget()
            sig = (shown, bool(alert))
            if sig != getattr(self, "_detail_sig", None):
                self._detail_sig = sig
                self._fit_window()
        # compact: a single short status line only when there is something to say
        if mode == "compact":
            lines = []
            if self._cfg.get("task_list_enabled", True):
                counts = self._status_counts(listed)
                if counts:
                    lines.append(counts)
            if alert:
                lines.append(self._clip(alert[0], 46))
            if fc_on:
                for k in ("primary", "secondary"):
                    f = fc.get(k) or {}
                    if f.get("runs_out"):
                        lines.append(self.t("fc_warn_compact", win=self.t("win5" if k == "primary" else "win7"),
                                            eta=self._clock(f.get("eta")), reset=self._clock(f.get("resets_at"))))
            text = "\n".join(lines)
            color = RED if alert and alert[1] == RED else AMBER if (alert or len(lines) > (1 if listed else 0)) else MUTED
            self.compact_status.configure(text=text, fg=color, font=font)
            if text and not self.compact_status.winfo_ismapped():
                self.compact_status.pack(fill="x", padx=16, pady=(0, 2), after=self.task_card["wrap"])
                self._fit_window()
            elif not text and self.compact_status.winfo_ismapped():
                self.compact_status.pack_forget()
                self._fit_window()
            elif text != getattr(self, "_compact_status_prev", ""):
                self._fit_window()
            self._compact_status_prev = text

    def _row_hover(self, row, on: bool):
        t = next((r["task"] for r in self.task_rows if r["row"] is row), None)
        bg = PANEL2 if on else (SEL_BG if t and t.get("selected") else PANEL)
        row.configure(bg=bg)
        for w in row.winfo_children():
            w.configure(bg=bg)
        # The click hint lives in the section header while hovering (saves a line).
        self._rows_hover = on
        if on:
            hint = "tasks_hint" if self._cfg.get("task_click_action") == "open" else "tasks_hint_select"
            self.tasks_sec["summary"].configure(text=self.t(hint), fg=MUTED)
        else:
            self._paint_monitor()

    def _task_row_click(self, i: int, select_only: bool):
        t = self.task_rows[i]["task"] if i < len(self.task_rows) else None
        if not t or not t.get("id"):
            return
        self._task_reader.set_override(t["id"])
        if not select_only and self._cfg.get("task_click_action") == "open":
            open_codex_thread(t["id"])
        self._local_wake.set()

    # -- notifications -----------------------------------------------------
    def _dispatch_events(self, events: list):
        """Worker thread: debounce alert transitions and fire notifications."""
        cfg = self._cfg
        if not cfg.get("notify_enabled", True) or cfg.get("notify_method") == "off":
            return
        now = time.time()
        cool = int(cfg.get("notify_cooldown_min", 10)) * 60
        toggles = {"stuck": "notify_stuck", "approval": "notify_approval", "complete": "notify_complete"}
        for ev in events:
            kind = ev.get("kind")
            task = ev.get("task") or {}
            if not cfg.get(toggles.get(kind, ""), False):
                continue
            key = (task.get("id"), kind)
            if now - self._notify_last.get(key, 0) < cool:
                continue
            self._notify_last[key] = now
            title, body = self._event_message(ev)
            self._notify(title, body)

    def _event_message(self, ev: dict) -> tuple[str, str]:
        task = ev.get("task") or {}
        name = self._task_name(task)
        kind = ev.get("kind")
        if kind == "stuck":
            st = task.get("stuck") or {}
            if st.get("why") == "repeat":
                body = self.t("n_stuck_repeat", title=name, n=st.get("count"), cmd=_one_line(st.get("cmd"), 100))
            else:
                body = self.t("n_stuck_silent", title=name, age=_fmt_age(st.get("age"), self.lang()))
            return self.t("n_stuck_title"), body
        if kind == "approval":
            return self.t("n_approval_title"), self.t("n_approval_body", title=name)
        return self.t("n_complete_title"), self.t("n_complete_body", title=name)

    def _notify(self, title: str, body: str):
        if self._cfg.get("notify_method") == "popup":
            self.after(0, self._popup, title, body)
            return

        def work():
            ok, err = show_windows_toast(title, body)
            self._toast_error = None if ok else err
            if not ok:
                try:
                    self.after(0, self._popup, title, body)
                except Exception:
                    pass

        threading.Thread(target=work, daemon=True).start()

    def _popup(self, title: str, body: str, ms: int = 10000):
        """Fallback notification: small topmost window in the screen's bottom-right corner."""
        try:
            top = tk.Toplevel(self)
            top.overrideredirect(True)
            top.attributes("-topmost", True)
            top.configure(bg=PANEL, highlightbackground=AMBER, highlightthickness=1)
            tk.Label(top, text=title, fg=AMBER, bg=PANEL, font=self._label_font(), anchor="w").pack(fill="x", padx=12, pady=(10, 2))
            tk.Label(top, text=self._clip(body, 60), fg=TEXT, bg=PANEL, font=self._label_font(), anchor="w",
                     justify="left").pack(fill="x", padx=12, pady=(0, 10))
            top.update_idletasks()
            w, h = top.winfo_reqwidth(), top.winfo_reqheight()
            x = top.winfo_screenwidth() - w - 24
            y = top.winfo_screenheight() - h - 72
            top.geometry(f"+{x}+{y}")
            for wdg in [top] + list(top.winfo_children()):
                wdg.bind("<Button-1>", lambda e: top.destroy())
            top.after(ms, top.destroy)
        except Exception:
            pass

    @staticmethod
    def _cost_total_text(cost: dict | None) -> str:
        if not cost or (not cost.get("priced") and cost.get("total", 0) <= 0):
            return "—"
        txt = _fmt_usd(cost["total"])
        return txt if cost.get("priced") else txt + "+?"

    def _cost_line_text(self, cost: dict | None) -> str:
        if not cost:
            return self.t("task_cost_na")
        if not cost.get("priced") and cost.get("total", 0) <= 0:
            return self.t("task_cost_unknown", models=", ".join(cost.get("unknown_models") or ["?"]))
        return self.t(
            "task_cost_line",
            total=self._cost_total_text(cost),
            inp=_fmt_usd(cost["input"]),
            cached=_fmt_usd(cost["cached"]),
            out=_fmt_usd(cost["output"]),
        )

    def _tick_task_age(self):
        """Cheap per-pulse refresh of the 'updated N ago' / activity age texts."""
        if self.is_compact() or self.is_mini() or not hasattr(self, "task_card"):
            return
        if getattr(self, "_act_cache", None):
            self.task_card["lines"][4].configure(text=self._activity_text(self._act_cache))
        with self._lock:
            snap = self._task
        view = task_view(snap)
        if not view:
            return
        self.task_card["lines"][3].configure(
            text=self.t(
                "task_ctx_line",
                ctx=_fmt_tokens(view["ctx"]),
                window=_fmt_tokens(view["window"]) if view["window"] else "--",
                age=self._task_age_text(snap),
            )
        )

    def _pulse(self):
        if self._stop.is_set():
            return
        self._tick += 1
        remain = max(0, int(self._next_due - time.monotonic()))
        blink = "_" if (self._tick % 2 == 0) else " "
        self.hdr_status.configure(text=f"T-{remain:03d}s{blink}")
        if self._tick % 2 == 0:
            self._tick_task_age()
        self.after(500, self._pulse)

    def refresh_now(self):
        if self._refreshing:
            return
        self._refreshing = True
        self.meta.configure(text=self.t("syncing"))

        def work():
            msg, data = fetch_usage()
            with self._lock:
                self._msg = msg
                self._data = data
            if msg == "ok" and isinstance(data, dict):
                try:
                    s = summarize(data)
                    with self._mon_lock:
                        self._monitor.forecaster.add_online(time.time(), s.get("primary"), s.get("secondary"))
                except Exception:
                    pass
            self.after(0, self._paint)

        threading.Thread(target=work, daemon=True).start()

    def _set_extra(self, content: str | None = None, rows=None):
        if rows is None:
            rows = [("", content or "")]
        for i, (lab, val) in enumerate(self.extra_rows):
            if i < len(rows):
                k, v = rows[i]
                lab.configure(text=k)
                val.configure(text=str(v))
            else:
                lab.configure(text="")
                val.configure(text="")

    def _label_font(self):
        return self._font_cjk if self.lang() == "zh" else self._font_mono

    def _tiny_label_font(self):
        return self._font_cjk_tiny if self.lang() == "zh" else self._font_tiny

    def _fmt_err(self, msg: str) -> str:
        mapping = {
            "MISSING ~/.codex/auth.json — login Codex first": "err_missing_auth",
            "API-key login has no ChatGPT 5h/7d windows": "err_apikey",
            "auth.json missing access_token / account_id": "err_missing_tokens",
            "session expired — re-login Codex": "err_expired",
        }
        if msg in mapping:
            return self.t(mapping[msg])
        if msg.startswith("refresh failed:"):
            return self.t("err_refresh", err=msg.split(":", 1)[-1].strip())
        if msg.startswith("HTTP "):
            rest = msg[5:]
            code, _, detail = rest.partition(":")
            return self.t("err_http", code=code.strip(), detail=detail.strip())
        if msg.startswith("request failed:"):
            return self.t("err_request", err=msg.split(":", 1)[-1].strip())
        return msg

    def _paint_card(self, card, win: dict):
        used = float(win.get("used_percent") or 0)
        reset_after = win.get("reset_after_seconds")
        color = RED if used >= 90 else AMBER if used >= 70 else CYAN
        card["pct"].configure(text=f"{used:5.1f}%", fg=color)
        card["detail"].configure(
            text=self.t("used_line", used=used, reset=_fmt_duration(reset_after, self.lang()))
        )
        card["meter"].set_value(used)

    def _paint(self):
        self._refreshing = False
        self._next_due = time.monotonic() + self.current_refresh_sec()
        with self._lock:
            msg = self._msg
            data = self._data
        if msg != "ok" or not data:
            shown = self._fmt_err(msg)
            self.meta.configure(text=self.t("fault", msg=shown), font=self._label_font())
            self._set_extra(rows=[("", shown)])
            if self.is_mini():
                self._paint_mini(fault=shown)
            return
        s = summarize(data)
        status = self.t("state_ok") if s.get("allowed") and not s.get("limit_reached") else self.t("state_limit")
        self.meta.configure(
            text=self.t(
                "meta_ok",
                email=s.get("email"),
                plan=str(s.get("plan") or "-").upper(),
                status=status,
                checked_at=s.get("checked_at"),
            ),
            font=self._label_font(),
        )
        self._paint_card(self.card5, s.get("primary") or {})
        self._paint_card(self.card7, s.get("secondary") or {})
        if self.is_mini():
            self._paint_mini(s.get("primary") or {}, s.get("secondary") or {})
        credits = s.get("credits") or {}
        fmt = {
            "balance": credits.get("balance"),
            "has_credits": credits.get("has_credits"),
            "unlimited": credits.get("unlimited"),
            "banked_resets": s.get("banked_resets"),
            "banked_applicable": s.get("banked_applicable"),
            "reached_type": s.get("reached_type"),
        }
        rows = []
        for label, template in EXTRA_ROWS.get(self.lang()) or EXTRA_ROWS["en"]:
            try:
                value = template.format(**fmt)
            except Exception:
                value = template
            rows.append((label, value))
        self._set_extra(rows=rows)

    def mode(self) -> str:
        m = str(self._cfg.get("mode") or "detail")
        return m if m in ("detail", "compact", "mini") else "detail"

    def is_compact(self) -> bool:
        return self.mode() == "compact"

    def is_mini(self) -> bool:
        return self.mode() == "mini"

    def toggle_mode(self):
        # detail <-> compact only
        if self.is_mini():
            self.expand_from_mini()
            return
        self._cfg["mode"] = "detail" if self.is_compact() else "compact"
        if self._cfg["mode"] != "mini":
            self._cfg["expand_mode"] = self._cfg["mode"]
        save_ui_config(self._cfg)
        self.apply_mode()

    def enter_mini(self):
        if self.mode() != "mini":
            self._cfg["expand_mode"] = "detail" if self.mode() == "detail" else "compact"
        self._cfg["mode"] = "mini"
        save_ui_config(self._cfg)
        self.apply_mode()

    def expand_from_mini(self):
        target = str(self._cfg.get("expand_mode") or "compact")
        self._cfg["mode"] = "detail" if target == "detail" else "compact"
        save_ui_config(self._cfg)
        self.apply_mode()

    def _set_mini_chrome(self, enabled: bool):
        if enabled:
            self.overrideredirect(True)
            self.configure(bg=CHROMA)
            # Prefer per-pixel alpha; fall back to color-key if layered API fails.
            self._mini_layered = False
            try:
                self.wm_attributes("-transparentcolor", "")
            except Exception:
                pass
            self.attributes("-topmost", True)
            self.after(10, self._enable_layered_style)
        else:
            self._bind_mini_root(False)
            self._disable_layered_style()
            try:
                self.wm_attributes("-transparentcolor", "")
            except Exception:
                pass
            self.overrideredirect(False)
            self.configure(bg=BG)
            self.attributes("-topmost", bool(self._cfg.get("topmost", True)))

    def _mini_hwnd(self) -> int:
        hwnd = int(self.winfo_id())
        parent = ctypes.windll.user32.GetParent(hwnd)
        return int(parent or hwnd)

    def _enable_layered_style(self):
        if not self.is_mini():
            return
        try:
            hwnd = self._mini_hwnd()
            GWL_EXSTYLE = -20
            WS_EX_LAYERED = 0x80000
            style = ctypes.windll.user32.GetWindowLongW(hwnd, GWL_EXSTYLE)
            ctypes.windll.user32.SetWindowLongW(hwnd, GWL_EXSTYLE, style | WS_EX_LAYERED)
            self._mini_layered = True
            self._redraw_capsule()
        except Exception:
            self._mini_layered = False
            try:
                self.wm_attributes("-transparentcolor", CHROMA)
            except Exception:
                pass
            self._redraw_capsule()

    def _disable_layered_style(self):
        self._mini_layered = False
        try:
            hwnd = self._mini_hwnd()
            GWL_EXSTYLE = -20
            WS_EX_LAYERED = 0x80000
            style = ctypes.windll.user32.GetWindowLongW(hwnd, GWL_EXSTYLE)
            ctypes.windll.user32.SetWindowLongW(hwnd, GWL_EXSTYLE, style & ~WS_EX_LAYERED)
        except Exception:
            pass

    def _apply_layered_image(self, im: "Image.Image", x: int | None = None, y: int | None = None) -> bool:
        """Push a premultiplied-alpha RGBA image to the window (smooth edges)."""
        if not getattr(self, "_mini_layered", False):
            return False
        try:
            im = im.convert("RGBA")
            w, h = im.size
            # Premultiply for UpdateLayeredWindow
            r, g, b, a = im.split()
            r = ImageChops.multiply(r, a)
            g = ImageChops.multiply(g, a)
            b = ImageChops.multiply(b, a)
            premul = Image.merge("RGBA", (r, g, b, a))
            # Windows wants BGRA byte order
            bgra = premul.tobytes("raw", "BGRA")

            class BITMAPINFOHEADER(ctypes.Structure):
                _fields_ = [
                    ("biSize", ctypes.c_uint32),
                    ("biWidth", ctypes.c_int32),
                    ("biHeight", ctypes.c_int32),
                    ("biPlanes", ctypes.c_uint16),
                    ("biBitCount", ctypes.c_uint16),
                    ("biCompression", ctypes.c_uint32),
                    ("biSizeImage", ctypes.c_uint32),
                    ("biXPelsPerMeter", ctypes.c_int32),
                    ("biYPelsPerMeter", ctypes.c_int32),
                    ("biClrUsed", ctypes.c_uint32),
                    ("biClrImportant", ctypes.c_uint32),
                ]

            class BITMAPINFO(ctypes.Structure):
                _fields_ = [("bmiHeader", BITMAPINFOHEADER), ("bmiColors", ctypes.c_uint32 * 3)]

            class POINT(ctypes.Structure):
                _fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long)]

            class SIZE(ctypes.Structure):
                _fields_ = [("cx", ctypes.c_long), ("cy", ctypes.c_long)]

            class BLENDFUNCTION(ctypes.Structure):
                _fields_ = [
                    ("BlendOp", ctypes.c_byte),
                    ("BlendFlags", ctypes.c_byte),
                    ("SourceConstantAlpha", ctypes.c_byte),
                    ("AlphaFormat", ctypes.c_byte),
                ]

            hwnd = self._mini_hwnd()
            screen_dc = ctypes.windll.user32.GetDC(0)
            mem_dc = ctypes.windll.gdi32.CreateCompatibleDC(screen_dc)
            bmi = BITMAPINFO()
            ctypes.memset(ctypes.byref(bmi), 0, ctypes.sizeof(bmi))
            bmi.bmiHeader.biSize = ctypes.sizeof(BITMAPINFOHEADER)
            bmi.bmiHeader.biWidth = w
            bmi.bmiHeader.biHeight = -h  # top-down
            bmi.bmiHeader.biPlanes = 1
            bmi.bmiHeader.biBitCount = 32
            bmi.bmiHeader.biCompression = 0
            bits = ctypes.c_void_p()
            dib = ctypes.windll.gdi32.CreateDIBSection(mem_dc, ctypes.byref(bmi), 0, ctypes.byref(bits), None, 0)
            if not dib:
                ctypes.windll.gdi32.DeleteDC(mem_dc)
                ctypes.windll.user32.ReleaseDC(0, screen_dc)
                return False
            ctypes.memmove(bits, bgra, len(bgra))
            old = ctypes.windll.gdi32.SelectObject(mem_dc, dib)
            blend = BLENDFUNCTION(0, 0, 255, 1)  # AC_SRC_OVER, AC_SRC_ALPHA
            src_pt = POINT(0, 0)
            dx = int(self.winfo_x() if x is None else x)
            dy = int(self.winfo_y() if y is None else y)
            dst_pt = POINT(dx, dy)
            size = SIZE(w, h)
            ULW_ALPHA = 0x00000002
            ok = ctypes.windll.user32.UpdateLayeredWindow(
                hwnd,
                screen_dc,
                ctypes.byref(dst_pt),
                ctypes.byref(size),
                mem_dc,
                ctypes.byref(src_pt),
                0,
                ctypes.byref(blend),
                ULW_ALPHA,
            )
            ctypes.windll.gdi32.SelectObject(mem_dc, old)
            ctypes.windll.gdi32.DeleteObject(dib)
            ctypes.windll.gdi32.DeleteDC(mem_dc)
            ctypes.windll.user32.ReleaseDC(0, screen_dc)
            return bool(ok)
        except Exception:
            return False

    def _ensure_mini_bar(self):
        if hasattr(self, "mini_bar"):
            return
        self.mini_bar = tk.Frame(self, bg=CHROMA, highlightthickness=0, bd=0)
        self.mini_label = tk.Label(self.mini_bar, bg=CHROMA, bd=0, highlightthickness=0, cursor="hand2")
        self.mini_label.pack(fill="both", expand=True)
        self._mini_photo = None
        self._mini_primary = {}
        self._mini_secondary = {}
        self._mini_fault = None
        self._drag = None
        self._mini_layered = False
        for seq, fn in (
            ("<ButtonPress-1>", self._mini_press),
            ("<B1-Motion>", self._mini_motion),
            ("<ButtonRelease-1>", self._mini_release),
        ):
            self.mini_label.bind(seq, fn)
            self.mini_bar.bind(seq, fn)

    def _bind_mini_root(self, enabled: bool):
        for seq in ("<ButtonPress-1>", "<B1-Motion>", "<ButtonRelease-1>"):
            try:
                self.unbind(seq)
            except Exception:
                pass
            try:
                self.mini_label.unbind(seq)
            except Exception:
                pass
            try:
                self.mini_bar.unbind(seq)
            except Exception:
                pass
        if not enabled:
            self._drag = None
            return
        for w in (self, self.mini_bar, self.mini_label):
            w.bind("<ButtonPress-1>", self._mini_press)
            w.bind("<B1-Motion>", self._mini_motion)
            w.bind("<ButtonRelease-1>", self._mini_release)

    def _mini_press(self, e):
        self._drag = {
            "x0": e.x_root,
            "y0": e.y_root,
            "wx": self.winfo_x(),
            "wy": self.winfo_y(),
            "moved": False,
        }
        try:
            self.grab_set_global()
        except Exception:
            try:
                self.grab_set()
            except Exception:
                pass
        self._mini_drag_poll()

    def _mini_drag_poll(self):
        """Backup drag loop: layered HWNDs sometimes swallow B1-Motion."""
        if not self._drag or not self.is_mini():
            return
        pressed = ctypes.windll.user32.GetAsyncKeyState(0x01) & 0x8000
        class POINT(ctypes.Structure):
            _fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long)]
        pt = POINT()
        ctypes.windll.user32.GetCursorPos(ctypes.byref(pt))
        if not pressed:
            moved = bool(self._drag.get("moved"))
            self._mini_end_drag()
            if not moved:
                self.expand_from_mini()
            return
        dx = pt.x - self._drag["x0"]
        dy = pt.y - self._drag["y0"]
        if abs(dx) + abs(dy) > 4:
            self._drag["moved"] = True
        if self._drag["moved"]:
            nx = self._drag["wx"] + dx
            ny = self._drag["wy"] + dy
            self.geometry(f"+{nx}+{ny}")
            self._present_mini_at(nx, ny)
        self.after(16, self._mini_drag_poll)

    def _mini_motion(self, e):
        # Primary path when Tk still delivers motion events.
        if not self._drag:
            return
        dx = e.x_root - self._drag["x0"]
        dy = e.y_root - self._drag["y0"]
        if abs(dx) + abs(dy) > 4:
            self._drag["moved"] = True
        if self._drag["moved"]:
            nx = self._drag["wx"] + dx
            ny = self._drag["wy"] + dy
            self.geometry(f"+{nx}+{ny}")
            self._present_mini_at(nx, ny)

    def _mini_release(self, e):
        if not self._drag:
            return
        moved = bool(self._drag.get("moved"))
        self._mini_end_drag()
        if not moved:
            self.expand_from_mini()

    def _mini_end_drag(self):
        self._drag = None
        try:
            self.grab_release()
        except Exception:
            pass

    def _present_mini_at(self, x: int, y: int):
        """Reposition layered bitmap without rebuilding pixels."""
        im = getattr(self, "_mini_rgba", None)
        if im is not None and getattr(self, "_mini_layered", False):
            self._apply_layered_image(im, x=x, y=y)

    def _pct_color(self, used: float) -> str:
        if used >= 90:
            return RED
        if used >= 70:
            return AMBER
        return CYAN

    def _hex_rgb(self, color: str) -> tuple[int, int, int]:
        c = color.lstrip("#")
        return int(c[0:2], 16), int(c[2:4], 16), int(c[4:6], 16)

    def _mini_font(self, size: int):
        if not HAS_PIL:
            return None
        if self.lang() == "zh":
            paths = [
                r"C:\Windows\Fonts\msyh.ttc",
                r"C:\Windows\Fonts\simhei.ttf",
                r"C:\Windows\Fonts\msyhbd.ttc",
            ]
        else:
            paths = [
                r"C:\Windows\Fonts\consola.ttf",
                r"C:\Windows\Fonts\consolab.ttf",
                r"C:\Windows\Fonts\arial.ttf",
            ]
        for path in paths:
            try:
                return ImageFont.truetype(path, size=size, index=0)
            except Exception:
                continue
        return ImageFont.load_default()

    def _mini_metrics(self) -> tuple[int, int, object, object, list]:
        """Return w,h, fonts and drawable text clusters based on current values."""
        # Restore pre-shrink visual type size; keep padding tight (small border).
        lab_font = self._mini_font(12)
        pct_font = self._mini_font(13)
        if self._mini_fault:
            clusters = [("ERR", RED, pct_font)]
        else:
            p = float((self._mini_primary or {}).get("used_percent") or 0)
            s = float((self._mini_secondary or {}).get("used_percent") or 0)
            lab5 = self.t("card5_compact")
            lab7 = self.t("card7_compact")
            c5 = f"{p:4.1f}%"
            c7 = f"{s:4.1f}%"
            clusters = [
                (lab5 + " ", MUTED, lab_font),
                (c5, self._pct_color(p), pct_font),
                (lab7 + " ", MUTED, lab_font),
                (c7, self._pct_color(s), pct_font),
            ]
            tokens = getattr(self, "_mini_tokens", None)
            if tokens is not None:
                # Plain count: no percentage thresholds, normal color (muted when empty).
                clusters += [
                    (self.t("mini_tokens") + " ", MUTED, lab_font),
                    (tokens, MUTED if tokens == "—" else CYAN, pct_font),
                ]
        # Measure with a scratch image
        scratch = ImageDraw.Draw(Image.new("RGB", (8, 8)))
        widths = []
        for text, _col, font in clusters:
            box = scratch.textbbox((0, 0), text, font=font)
            widths.append(box[2] - box[0])
        gap = 12
        pairs = max(1, len(clusters) // 2)
        content_w = sum(widths) + (0 if self._mini_fault else gap * (pairs - 1))
        pad_x = 12  # tight side padding
        pad_y = 6
        w = content_w + pad_x * 2
        h = 36  # slim border, fonts stay readable
        return w, h, lab_font, pct_font, clusters

    def _redraw_capsule(self):
        if not hasattr(self, "mini_label"):
            return
        if not HAS_PIL:
            return
        w, h, lab_font, pct_font, clusters = self._mini_metrics()
        # Keep Tk geometry matched to bitmap
        try:
            self.geometry(f"{w}x{h}+{self.winfo_x()}+{self.winfo_y()}")
        except Exception:
            self.geometry(f"{w}x{h}")

        scale = 4
        W, H = w * scale, h * scale
        panel = self._hex_rgb(PANEL)
        outline = self._hex_rgb("#2f5a72")
        hi = self._hex_rgb("#1a2f42")

        base = Image.new("RGBA", (W, H), (0, 0, 0, 0))
        draw = ImageDraw.Draw(base)
        pad = 1 * scale
        box = [pad, pad, W - pad - 1, H - pad - 1]
        radius = (box[3] - box[1]) / 2
        # Slightly translucent outline via dual pass for softer rim
        draw.rounded_rectangle(box, radius=radius, fill=panel + (255,))
        draw.rounded_rectangle(box, radius=radius, outline=outline + (160,), width=max(1, scale // 2))
        ib = [pad + 2 * scale, pad + 2 * scale, W - pad - 1 - 2 * scale, pad + int(8 * scale)]
        if ib[2] > ib[0] and ib[3] > ib[1]:
            draw.rounded_rectangle(ib, radius=radius * 0.55, fill=hi + (180,))

        img = base.resize((w, h), Image.Resampling.LANCZOS)
        d = ImageDraw.Draw(img)
        if self._mini_fault:
            d.text((w / 2, h / 2), "ERR", fill=self._hex_rgb(RED) + (255,), font=pct_font, anchor="mm")
        else:
            scratch = ImageDraw.Draw(Image.new("RGB", (8, 8)))
            widths = []
            for text, _col, font in clusters:
                box = scratch.textbbox((0, 0), text, font=font)
                widths.append(box[2] - box[0])
            gap = 12
            pairs = len(clusters) // 2
            total = sum(widths) + gap * (pairs - 1)
            x = (w - total) / 2
            y = h / 2
            for i in range(pairs):
                if i:
                    x += gap
                    cx = x - gap / 2
                    d.ellipse((cx - 1.4, y - 1.4, cx + 1.4, y + 1.4), fill=self._hex_rgb(CYAN_DIM) + (255,))
                for j in (2 * i, 2 * i + 1):
                    text, col, font = clusters[j]
                    d.text((x, y), text, fill=self._hex_rgb(col) + (255,), font=font, anchor="lm")
                    x += widths[j]

        self._mini_rgba = img
        self._bind_mini_root(True)
        if self._apply_layered_image(img):
            return

        # Fallback: chroma-key supersample (more jagged, but works)
        chroma = self._hex_rgb(CHROMA)
        rgba = img.split()
        rgb = Image.merge("RGB", rgba[:3])
        alpha = rgba[3]
        out = Image.new("RGB", (w, h), chroma)
        out.paste(rgb, mask=alpha.point(lambda a: 255 if a >= 72 else 0))
        self._mini_photo = ImageTk.PhotoImage(out)
        self.mini_label.configure(image=self._mini_photo)
        try:
            self.wm_attributes("-transparentcolor", CHROMA)
        except Exception:
            pass

    def _paint_mini(self, primary: dict | None = None, secondary: dict | None = None, fault: str | None = None):
        self._mini_primary = primary or {}
        self._mini_secondary = secondary or {}
        self._mini_fault = fault
        self._redraw_capsule()

    def apply_mode(self):
        mode = self.mode()
        # Always hide mini first unless entering it.
        if hasattr(self, "mini_bar") and mode != "mini":
            self.mini_bar.pack_forget()
        if mode == "mini":
            for w in (self.brand.master, self.meta, self.extra_wrap, self.ctrl, self.btns):
                try:
                    w.pack_forget()
                except Exception:
                    pass
            self.card5["wrap"].pack_forget()
            self.card7["wrap"].pack_forget()
            self.task_card["wrap"].pack_forget()
            for w in (self.tasks_sec["wrap"], self.compact_status):
                w.pack_forget()
            if hasattr(self, "compact_bar"):
                self.compact_bar.pack_forget()
            self._ensure_mini_bar()
            self.mini_bar.pack(fill="both", expand=True)
            self._set_mini_chrome(True)
            self._bind_mini_root(True)
            self.minsize(160, 30)
            self.maxsize(420, 48)
            with self._lock:
                data = self._data
                msg = self._msg
            if msg == "ok" and data:
                s = summarize(data)
                self._paint_mini(s.get("primary") or {}, s.get("secondary") or {})
            elif msg not in ("BOOT",):
                self._paint_mini(fault=msg)
            else:
                self._paint_mini()
            self._fit_window()
            self.after(30, self._redraw_capsule)
            return

        # Leaving mini: restore normal window chrome + header.
        if hasattr(self, "mini_bar"):
            self.mini_bar.pack_forget()
        self._set_mini_chrome(False)
        if not self.brand.master.winfo_ismapped():
            self.brand.master.pack(fill="x", padx=14, pady=(12, 6))

        compact = mode == "compact"
        if compact:
            self.meta.pack_forget()
            self.extra_wrap.pack_forget()
            self.ctrl.pack_forget()
            self.btns.pack_forget()
            if not hasattr(self, "compact_bar"):
                self.compact_bar = tk.Frame(self, bg=BG)
                self.compact_sync_btn = self._btn(self.compact_bar, self.t("sync"), self.refresh_now)
                self.compact_sync_btn.pack(side="left")
                self.compact_mini_btn = self._btn(self.compact_bar, self.t("mini"), self.enter_mini)
                self.compact_mini_btn.pack(side="right")
                self.compact_mode_btn = self._btn(self.compact_bar, self.t("detail"), self.toggle_mode)
                self.compact_mode_btn.pack(side="right", padx=(0, 8))
            else:
                self.compact_sync_btn.configure(text=self.t("sync"))
                self.compact_mode_btn.configure(text=self.t("detail"))
                if hasattr(self, "compact_mini_btn"):
                    self.compact_mini_btn.configure(text=self.t("mini"))
                else:
                    self.compact_mini_btn = self._btn(self.compact_bar, self.t("mini"), self.enter_mini)
                    self.compact_mini_btn.pack(side="right")
            self.card5["title"].configure(text=self.t("card5_compact"))
            self.card7["title"].configure(text=self.t("card7_compact"))
            self.task_card["title"].configure(text=self.t("task_card_compact"))
            self.card5["wrap"].pack(fill="x", padx=14, pady=(2, 4))
            self.card7["wrap"].pack(fill="x", padx=14, pady=(2, 4))
            self.task_card["wrap"].pack(fill="x", padx=14, pady=(2, 4))
            self.tasks_sec["wrap"].pack_forget()
            self.compact_bar.pack(fill="x", padx=14, pady=(4, 10))
            self.minsize(300, 200)
            self.maxsize(560, 480)
        else:
            if hasattr(self, "compact_bar"):
                self.compact_bar.pack_forget()
            self.card5["title"].configure(text=self.t("card5"))
            self.card7["title"].configure(text=self.t("card7"))
            self.task_card["title"].configure(text=self.t("task_card"))
            self.maxsize(1400, 1200)
            self.minsize(420, 400)
            self.card5["wrap"].pack(fill="x", padx=14, pady=6)
            # before= keeps the account line under the header after compact -> detail
            self.meta.pack(fill="x", padx=14, before=self.card5["wrap"])
            self.card7["wrap"].pack(fill="x", padx=14, pady=6)
            self.task_card["wrap"].pack(fill="x", padx=14, pady=6)
            self.compact_status.pack_forget()
            if self._cfg.get("task_list_enabled", True):
                self.tasks_sec["wrap"].pack(fill="x", padx=14, pady=(0, 6), after=self.task_card["wrap"])
            else:
                self.tasks_sec["wrap"].pack_forget()
            self.extra_wrap.pack(fill="both", expand=True, padx=14, pady=(0, 8))
            self.ctrl.pack(fill="x", padx=14, pady=(0, 6))
            self.btns.pack(fill="x", padx=14, pady=(0, 12))
            self.mode_btn.configure(text=self.t("compact"))
            if hasattr(self, "mini_btn"):
                self.mini_btn.configure(text=self.t("mini"))
        pad = (4, 6) if compact else (6, 10)
        for card in (self.card5, self.card7, self.task_card):
            card["meter"].pack_configure(pady=pad)
            # show meter/detail again if leaving mini
            card["detail"].pack(fill="x", padx=10)
            for idx, lab in enumerate(card.get("lines", ())):
                if compact or idx == 5:  # [5] = alert line, packed by _paint_monitor when set
                    lab.pack_forget()
                else:
                    lab.pack(fill="x", padx=10, before=card["meter"])
            card["meter"].pack(fill="x", padx=10, pady=pad)
            if "fc" in card:
                if compact or not self._cfg.get("forecast_enabled", True):
                    card["fc"].pack_forget()
                else:
                    card["fc"].pack(fill="x", padx=10, before=card["meter"])
        self._paint_task()
        self._fit_window()

    def _fit_window(self):
        self.update_idletasks()
        w = int(self.winfo_reqwidth())
        h = int(self.winfo_reqheight())
        mode = self.mode()
        if mode == "mini":
            w, h, *_ = self._mini_metrics()
        elif mode == "compact":
            w = max(w, 340)
            h = max(h, 220)
        else:
            w = max(w, 460)
            h = max(h, 470)
        # keep current top-left when resizing
        try:
            geo = self.geometry()
            parts = geo.split("+")
            pos = f"+{parts[1]}+{parts[2]}" if len(parts) >= 3 else ""
        except Exception:
            pos = ""
        self.geometry(f"{w}x{h}{pos}")
        self.after(50, self._publish_hwnd)

    def _publish_hwnd(self):
        try:
            hwnd = self._mini_hwnd() if self.is_mini() else int(self.winfo_id())
            # Prefer top-level HWND.
            parent = ctypes.windll.user32.GetParent(hwnd)
            if parent:
                hwnd = int(parent)
            HWND_PATH.write_text(str(int(hwnd)), encoding="utf-8")
        except Exception:
            pass

    def on_close(self):
        self._stop.set()
        self._local_wake.set()
        try:
            HWND_PATH.unlink(missing_ok=True)
        except Exception:
            pass
        self.destroy()


def main():
    mutex = acquire_single_instance()
    if mutex is None:
        return
    app = Hud()
    app._singleton_mutex = mutex
    app.protocol("WM_DELETE_WINDOW", app.on_close)
    app.mainloop()


if __name__ == "__main__":
    main()
