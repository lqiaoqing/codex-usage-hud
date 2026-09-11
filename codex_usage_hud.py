"""Codex Usage HUD — geek-styled always-on-top widget for 5h / 7d quotas."""
from __future__ import annotations

import json
import math
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
    from PIL import Image, ImageDraw, ImageFont, ImageTk

    HAS_PIL = True
except Exception:
    HAS_PIL = False

CODEX_HOME = Path.home() / ".codex"
AUTH_PATH = CODEX_HOME / "auth.json"
CONFIG_PATH = Path.home() / ".codex_usage_hud.json"
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

        self._font_title = tkfont.Font(family="Consolas", size=13, weight="bold")
        self._font_mono = tkfont.Font(family="Consolas", size=9)
        self._font_big = tkfont.Font(family="Consolas", size=20, weight="bold")
        self._font_tiny = tkfont.Font(family="Consolas", size=8)
        self._apply_fonts()
        self.title(self.t("window_title"))

        self._build()
        self.after(120, self.refresh_now)
        threading.Thread(target=self._loop, daemon=True).start()
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
        for card in (self.card5, self.card7):
            card["title"].configure(font=self._tiny_label_font())
            card["pct"].configure(font=self._font_big)
            card["detail"].configure(font=self._label_font())
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

    def _loop(self):
        while not self._stop.is_set():
            now = time.monotonic()
            due = self._next_due
            if now >= due:
                self.after(0, self.refresh_now)
                # next due is set after paint; fallback if refresh hangs
                self._next_due = now + max(MIN_REFRESH_SEC, self.current_refresh_sec())
            self._stop.wait(0.4)

    def _pulse(self):
        if self._stop.is_set():
            return
        self._tick += 1
        remain = max(0, int(self._next_due - time.monotonic()))
        blink = "_" if (self._tick % 2 == 0) else " "
        self.hdr_status.configure(text=f"T-{remain:03d}s{blink}")
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
            try:
                self.wm_attributes("-transparentcolor", CHROMA)
            except Exception:
                pass
            self.attributes("-topmost", True)
        else:
            try:
                self.wm_attributes("-transparentcolor", "")
            except Exception:
                pass
            self.overrideredirect(False)
            self.configure(bg=BG)
            self.attributes("-topmost", bool(self._cfg.get("topmost", True)))

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
        for seq, fn in (
            ("<ButtonPress-1>", self._mini_press),
            ("<B1-Motion>", self._mini_motion),
            ("<ButtonRelease-1>", self._mini_release),
        ):
            self.mini_label.bind(seq, fn)
            self.mini_bar.bind(seq, fn)
        self.mini_label.bind("<Configure>", lambda e: self._redraw_capsule())

    def _mini_press(self, e):
        self._drag = (e.x_root, e.y_root, self.winfo_x(), self.winfo_y(), False)

    def _mini_motion(self, e):
        if not self._drag:
            return
        x0, y0, wx, wy, moved = self._drag
        dx = e.x_root - x0
        dy = e.y_root - y0
        if abs(dx) + abs(dy) > 5:
            moved = True
        self._drag = (x0, y0, wx, wy, moved)
        if moved:
            self.geometry(f"+{wx + dx}+{wy + dy}")

    def _mini_release(self, e):
        dragged = bool(self._drag and self._drag[4])
        self._drag = None
        if not dragged:
            self.expand_from_mini()

    def _pct_color(self, used: float) -> str:
        if used >= 90:
            return RED
        if used >= 70:
            return AMBER
        return CYAN

    def _hex_rgb(self, color: str) -> tuple[int, int, int]:
        c = color.lstrip("#")
        return int(c[0:2], 16), int(c[2:4], 16), int(c[4:6], 16)

    def _mini_font(self, size: int, bold: bool = False):
        if not HAS_PIL:
            return None
        if self.lang() == "zh":
            paths = [
                r"C:\Windows\Fonts\msyh.ttc",
                r"C:\Windows\Fonts\msyhbd.ttc",
                r"C:\Windows\Fonts\simhei.ttf",
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

    def _mini_size(self) -> tuple[int, int]:
        # Compact capsule; Chinese labels need a bit more width.
        if self.lang() == "zh":
            return 248, 34
        return 228, 34

    def _redraw_capsule(self):
        if not hasattr(self, "mini_label"):
            return
        w, h = self._mini_size()
        if HAS_PIL:
            self._redraw_capsule_pil(w, h)
        else:
            self._redraw_capsule_canvas_fallback(w, h)

    def _redraw_capsule_pil(self, w: int, h: int):
        scale = 4  # supersample then hard-key → smoother silhouette than Tk arcs
        W, H = w * scale, h * scale
        chroma = self._hex_rgb(CHROMA)
        panel = self._hex_rgb(PANEL)
        outline = self._hex_rgb("#2a4a62")
        hi = self._hex_rgb("#152536")

        base = Image.new("RGBA", (W, H), (0, 0, 0, 0))
        draw = ImageDraw.Draw(base)
        pad = 2 * scale
        box = [pad, pad, W - pad - 1, H - pad - 1]
        radius = (box[3] - box[1]) / 2
        draw.rounded_rectangle(box, radius=radius, fill=panel + (255,), outline=outline + (255,), width=scale)
        # soft inner sheen
        ib = [pad + 2 * scale, pad + 2 * scale, W - pad - 1 - 2 * scale, pad + int(9 * scale)]
        if ib[2] > ib[0] and ib[3] > ib[1]:
            draw.rounded_rectangle(ib, radius=radius * 0.55, fill=hi + (220,))

        img = base.resize((w, h), Image.Resampling.LANCZOS)
        # Hard chroma key after downsample (keeps edge smooth vs native Tk)
        rgba = img.split()
        rgb = Image.merge("RGB", rgba[:3])
        alpha = rgba[3]
        out = Image.new("RGB", (w, h), chroma)
        out.paste(rgb, mask=alpha.point(lambda a: 255 if a >= 96 else 0))

        d = ImageDraw.Draw(out)
        lab_font = self._mini_font(10)
        pct_font = self._mini_font(11)

        if self._mini_fault:
            d.text((w / 2, h / 2), "ERR", fill=self._hex_rgb(RED), font=pct_font, anchor="mm")
        else:
            p = float((self._mini_primary or {}).get("used_percent") or 0)
            s = float((self._mini_secondary or {}).get("used_percent") or 0)
            lab5 = self.t("card5_compact")
            lab7 = self.t("card7_compact")
            t5 = f"{lab5} {p:4.1f}%"
            t7 = f"{lab7} {s:4.1f}%"
            # measure clusters
            def tw(text, font):
                box = d.textbbox((0, 0), text, font=font)
                return box[2] - box[0]

            # draw label muted + pct colored separately for each cluster
            gap = 14
            c5 = f"{p:4.1f}%"
            c7 = f"{s:4.1f}%"
            w_lab5 = tw(lab5 + " ", lab_font)
            w_pct5 = tw(c5, pct_font)
            w_lab7 = tw(lab7 + " ", lab_font)
            w_pct7 = tw(c7, pct_font)
            total = w_lab5 + w_pct5 + gap + w_lab7 + w_pct7
            x = (w - total) / 2
            y = h / 2
            d.text((x, y), lab5 + " ", fill=self._hex_rgb(MUTED), font=lab_font, anchor="lm")
            x += w_lab5
            d.text((x, y), c5, fill=self._hex_rgb(self._pct_color(p)), font=pct_font, anchor="lm")
            x += w_pct5 + gap
            # tiny separator
            d.ellipse((x - gap / 2 - 1.2, y - 1.2, x - gap / 2 + 1.2, y + 1.2), fill=self._hex_rgb(CYAN_DIM))
            d.text((x, y), lab7 + " ", fill=self._hex_rgb(MUTED), font=lab_font, anchor="lm")
            x += w_lab7
            d.text((x, y), c7, fill=self._hex_rgb(self._pct_color(s)), font=pct_font, anchor="lm")

        self._mini_photo = ImageTk.PhotoImage(out)
        self.mini_label.configure(image=self._mini_photo)

    def _redraw_capsule_canvas_fallback(self, w: int, h: int):
        # Rare path if Pillow missing: keep a tiny canvas.
        if not hasattr(self, "mini_canvas"):
            self.mini_canvas = tk.Canvas(self.mini_bar, width=w, height=h, bg=CHROMA, highlightthickness=0, bd=0, cursor="hand2")
            self.mini_canvas.pack(fill="both", expand=True)
            self.mini_label.pack_forget()
            self.mini_canvas.bind("<ButtonPress-1>", self._mini_press)
            self.mini_canvas.bind("<B1-Motion>", self._mini_motion)
            self.mini_canvas.bind("<ButtonRelease-1>", self._mini_release)
        c = self.mini_canvas
        c.delete("all")
        c.configure(width=w, height=h)
        r = h / 2 - 1
        c.create_oval(1, 1, h - 1, h - 1, fill=PANEL, outline="#2a4a62")
        c.create_oval(w - h + 1, 1, w - 1, h - 1, fill=PANEL, outline="#2a4a62")
        c.create_rectangle(r, 1, w - r, h - 1, fill=PANEL, outline=PANEL)
        c.create_line(r, 1, w - r, 1, fill="#2a4a62")
        c.create_line(r, h - 1, w - r, h - 1, fill="#2a4a62")
        if self._mini_fault:
            c.create_text(w / 2, h / 2, text="ERR", fill=RED, font=self._font_tiny)
            return
        p = float((self._mini_primary or {}).get("used_percent") or 0)
        s = float((self._mini_secondary or {}).get("used_percent") or 0)
        text = f"{self.t('card5_compact')} {p:4.1f}%   {self.t('card7_compact')} {s:4.1f}%"
        c.create_text(w / 2, h / 2, text=text, fill=TEXT, font=self._font_tiny)

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
            if hasattr(self, "compact_bar"):
                self.compact_bar.pack_forget()
            self._ensure_mini_bar()
            self.mini_bar.pack(fill="both", expand=True)
            self._set_mini_chrome(True)
            self.minsize(200, 28)
            self.maxsize(280, 40)
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
            self.card5["wrap"].pack(fill="x", padx=14, pady=(2, 4))
            self.card7["wrap"].pack(fill="x", padx=14, pady=(2, 4))
            self.compact_bar.pack(fill="x", padx=14, pady=(4, 10))
            self.minsize(300, 200)
            self.maxsize(560, 360)
        else:
            if hasattr(self, "compact_bar"):
                self.compact_bar.pack_forget()
            self.card5["title"].configure(text=self.t("card5"))
            self.card7["title"].configure(text=self.t("card7"))
            self.maxsize(1400, 1200)
            self.minsize(420, 400)
            self.meta.pack(fill="x", padx=14)
            self.card5["wrap"].pack(fill="x", padx=14, pady=6)
            self.card7["wrap"].pack(fill="x", padx=14, pady=6)
            self.extra_wrap.pack(fill="both", expand=True, padx=14, pady=8)
            self.ctrl.pack(fill="x", padx=14, pady=(0, 6))
            self.btns.pack(fill="x", padx=14, pady=(0, 12))
            self.mode_btn.configure(text=self.t("compact"))
            if hasattr(self, "mini_btn"):
                self.mini_btn.configure(text=self.t("mini"))
        pad = (4, 6) if compact else (6, 10)
        for card in (self.card5, self.card7):
            card["meter"].pack_configure(pady=pad)
            # show meter/detail again if leaving mini
            card["detail"].pack(fill="x", padx=10)
            card["meter"].pack(fill="x", padx=10, pady=pad)
        self._fit_window()

    def _fit_window(self):
        self.update_idletasks()
        w = int(self.winfo_reqwidth())
        h = int(self.winfo_reqheight())
        mode = self.mode()
        if mode == "mini":
            w, h = self._mini_size()
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

    def on_close(self):
        self._stop.set()
        self.destroy()


def main():
    app = Hud()
    app.protocol("WM_DELETE_WINDOW", app.on_close)
    app.mainloop()


if __name__ == "__main__":
    main()
