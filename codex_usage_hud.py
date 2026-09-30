"""Codex Usage HUD — geek-styled always-on-top widget for 5h / 7d quotas."""
from __future__ import annotations

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
SINGLETON_MUTEX = "Local\\CodexUsageHud.SingleInstance"
USAGE_URL = "https://chatgpt.com/backend-api/wham/usage"
USAGE_PAGE = "https://chatgpt.com/codex/settings/usage"

BG = "#05070a"
PANEL = "#0b1018"
PANEL2 = "#101826"
LINE = "#1c2a3a"
CYAN = "#3ee0c6"
CYAN_DIM = "#1a8f80"
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
        "task_wait": "awaiting token_count",
        "task_title_line": "{title}   @{started}",
        "task_tokens_line": "TOTAL {total}   IN {inp}   OUT {out}",
        "task_cache_line": "CACHED {cached} ({hit:.1f}%)   REASONING {reasoning}",
        "task_ctx_line": "CTX {ctx} / {window}   UPDATED {age} AGO",
        "task_compact_line": "TOTAL {total}   CTX {ctx} / {window}",
        "mini_ctx": "CTX",
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
        "task_wait": "等待 token 统计",
        "task_title_line": "{title}   @{started}",
        "task_tokens_line": "总计 {total}   输入 {inp}   输出 {out}",
        "task_cache_line": "缓存 {cached}（命中 {hit:.1f}%）   推理 {reasoning}",
        "task_ctx_line": "上下文 {ctx} / {window}   {age}前更新",
        "task_compact_line": "总计 {total}   上下文 {ctx} / {window}",
        "mini_ctx": "上下文",
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
    data["mini_task_ctx"] = bool(data.get("mini_task_ctx", True))
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


class TaskUsageReader:
    """Follow the most recently active Codex rollout and extract its token usage.

    Pure local file reads, meant to run on a worker thread. The active file is
    tailed incrementally (only bytes appended since the last poll are parsed);
    on a task switch the file is scanned backwards from EOF in chunks.
    NTFS often keeps a stale mtime while Codex holds the rollout open, so file
    activity = max(mtime, timestamp of the last line in the file tail).
    """

    def __init__(self, sessions_dir: Path = SESSIONS_DIR, index_path: Path = SESSION_INDEX_PATH):
        self.sessions_dir = Path(sessions_dir)
        self.index_path = Path(index_path)
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
    def _list_rollouts(self) -> list[tuple[str, float]]:
        out = []
        stack = [str(self.sessions_dir)]
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
            ev = _parse_token_line(raw)
            if not ev:
                continue
            self._last_event_ts = ev["ts"] or self._last_event_ts
            if ev["rate_limits"]:
                self._rate_limits = ev["rate_limits"]
            if ev["info"]:
                self._usage = ev

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

    def poll(self) -> dict | None:
        if not self._candidates or time.monotonic() - self._scan_at >= ROLLOUT_RESCAN_SEC:
            self._rescan()
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
            self._scan_backwards(path, size)
            # resume forward reads from the last complete line
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
        }


def task_view(snap: dict | None) -> dict | None:
    """Derive display numbers from a TaskUsageReader snapshot."""
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
    }


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
        self._lock = threading.Lock()
        self._msg = "BOOT"
        self._data = None
        self._refreshing = False
        self._tick = 0
        self._next_due = time.monotonic()
        self._task_reader = TaskUsageReader()
        self._task = None  # latest TaskUsageReader snapshot
        self._task_polling = False
        self._mini_ctx = None

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

        extra = tk.Frame(self, bg=PANEL, highlightbackground=LINE, highlightthickness=1)
        self.extra_wrap = extra
        extra.pack(fill="both", expand=True, padx=14, pady=8)
        self.extra_body = tk.Frame(extra, bg=PANEL)
        self.extra_body.pack(fill="both", expand=True, padx=10, pady=8)
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

    def _task_card(self):
        card = self._card(self.t("task_card"))
        wrap = card["wrap"]
        # Extra lines sit between the headline detail and the context meter.
        lines = []
        for _ in range(3):
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
            with self._lock:
                self._task = snap
            try:
                self.after(0, self._paint_task)
            except Exception:
                return
            self._stop.wait(self.current_local_refresh_sec())

    def _task_age_text(self, snap: dict | None) -> str:
        ts = (snap or {}).get("updated_ts")
        return _fmt_age(time.time() - ts if ts else None, self.lang())

    def _task_title_text(self, snap: dict) -> str:
        title = (snap.get("title") or "").strip()
        if not title:
            sid = snap.get("id") or "?"
            title = f"#{sid[-8:]}"
        limit = 24 if any(ord(ch) > 0x2E7F for ch in title) else 38
        if len(title) > limit:
            title = title[: limit - 1] + "…"
        started = snap.get("started")
        if isinstance(started, datetime):
            fmt = "%H:%M" if started.date() == datetime.now().date() else "%m-%d %H:%M"
            stamp = started.strftime(fmt)
        else:
            stamp = "--"
        return self.t("task_title_line", title=title, started=stamp)

    def _paint_task(self):
        if not hasattr(self, "task_card"):
            return
        with self._lock:
            snap = self._task
        card = self.task_card
        view = task_view(snap)
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
            ctx_mini = None
        else:
            pct = view["ctx_pct"]
            card["pct"].configure(text=f"{pct:5.1f}%", fg=self._pct_color(pct))
            ctx_txt = _fmt_tokens(view["ctx"])
            win_txt = _fmt_tokens(view["window"]) if view["window"] else "--"
            if compact:
                card["detail"].configure(
                    text=self.t("task_compact_line", total=_fmt_tokens(view["total"]), ctx=ctx_txt, window=win_txt),
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
                card["lines"][2].configure(
                    text=self.t("task_ctx_line", ctx=ctx_txt, window=win_txt, age=self._task_age_text(snap)),
                    font=font,
                )
            card["meter"].set_value(pct)
            ctx_mini = pct if view["window"] else None
        if ctx_mini is None or not self._cfg.get("mini_task_ctx", True):
            new_mini = None
        else:
            new_mini = round(ctx_mini, 1)
        if new_mini != self._mini_ctx:
            self._mini_ctx = new_mini
            if self.is_mini():
                self._redraw_capsule()

    def _tick_task_age(self):
        """Cheap per-pulse refresh of the 'updated N ago' text."""
        if self.is_compact() or self.is_mini() or not hasattr(self, "task_card"):
            return
        with self._lock:
            snap = self._task
        view = task_view(snap)
        if not view:
            return
        self.task_card["lines"][2].configure(
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
            ctx = getattr(self, "_mini_ctx", None)
            if ctx is not None:
                clusters += [
                    (self.t("mini_ctx") + " ", MUTED, lab_font),
                    (f"{ctx:4.1f}%", self._pct_color(ctx), pct_font),
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
            self.meta.pack(fill="x", padx=14)
            self.card5["wrap"].pack(fill="x", padx=14, pady=6)
            self.card7["wrap"].pack(fill="x", padx=14, pady=6)
            self.task_card["wrap"].pack(fill="x", padx=14, pady=6)
            self.extra_wrap.pack(fill="both", expand=True, padx=14, pady=8)
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
            for lab in card.get("lines", ()):
                if compact:
                    lab.pack_forget()
                else:
                    lab.pack(fill="x", padx=10, before=card["meter"])
            card["meter"].pack(fill="x", padx=10, pady=pad)
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
