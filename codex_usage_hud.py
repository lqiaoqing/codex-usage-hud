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

DEFAULT_REFRESH_SEC = 60
MIN_REFRESH_SEC = 5
MAX_REFRESH_SEC = 3600


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
    data["mode"] = "compact" if mode == "compact" else "detail"
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


def _fmt_duration(seconds: int | float | None) -> str:
    if seconds is None:
        return "--"
    try:
        s = int(max(0, seconds))
    except Exception:
        return "--"
    d, rem = divmod(s, 86400)
    h, remaining = divmod(rem, 3600)
    m, sec = divmod(remaining, 60)
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

        self._build()
        self.after(120, self.refresh_now)
        threading.Thread(target=self._loop, daemon=True).start()
        self.after(250, self._pulse)

    def _build(self):
        header = tk.Frame(self, bg=BG)
        header.pack(fill="x", padx=14, pady=(12, 6))
        tk.Label(header, text="CODEX.USAGE", fg=CYAN, bg=BG, font=self._font_title).pack(side="left")
        self.hdr_status = tk.Label(header, text="SYS.ONLINE", fg=MUTED, bg=BG, font=self._font_tiny)
        self.hdr_status.pack(side="right")

        self.meta = tk.Label(self, text="boot sequence...", fg=MUTED, bg=BG, font=self._font_mono, anchor="w", justify="left")
        self.meta.pack(fill="x", padx=14)

        self.card5 = self._card("PRIMARY  //  5H WINDOW")
        self.card7 = self._card("SECONDARY  //  7D WINDOW")

        extra = tk.Frame(self, bg=PANEL, highlightbackground=LINE, highlightthickness=1)
        self.extra_wrap = extra
        extra.pack(fill="both", expand=True, padx=14, pady=8)
        self.extra = tk.Text(
            extra,
            height=6,
            wrap="word",
            bg=PANEL,
            fg=TEXT,
            insertbackground=CYAN,
            relief="flat",
            font=self._font_mono,
            padx=8,
            pady=8,
        )
        self.extra.pack(fill="both", expand=True)
        self.extra.configure(state="disabled")

        ctrl = tk.Frame(self, bg=BG)
        self.ctrl = ctrl
        ctrl.pack(fill="x", padx=14, pady=(0, 6))
        tk.Label(ctrl, text="REFRESH.SEC", fg=MUTED, bg=BG, font=self._font_tiny).pack(side="left")
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
        self._btn(ctrl, "APPLY", self.apply_refresh).pack(side="left", padx=4)
        self.topmost_var = tk.BooleanVar(value=bool(self._cfg.get("topmost", True)))
        tk.Checkbutton(
            ctrl,
            text="PIN",
            variable=self.topmost_var,
            command=self._toggle_topmost,
            bg=BG,
            fg=MUTED,
            selectcolor=PANEL2,
            activebackground=BG,
            activeforeground=CYAN,
            font=self._font_tiny,
        ).pack(side="right")

        btns = tk.Frame(self, bg=BG)
        self.btns = btns
        btns.pack(fill="x", padx=14, pady=(0, 12))
        self._btn(btns, "SYNC NOW", self.refresh_now).pack(side="left")
        self._btn(btns, "OPEN USAGE", lambda: webbrowser.open(USAGE_PAGE)).pack(side="left", padx=8)
        self.mode_btn = self._btn(btns, "COMPACT", self.toggle_mode)
        self.mode_btn.pack(side="right")

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
        detail = tk.Label(wrap, text="awaiting telemetry", fg=TEXT, bg=PANEL, font=self._font_mono, anchor="w")
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
        self.hdr_status.configure(text=f"interval={sec}s")

    def _toggle_topmost(self):
        on = bool(self.topmost_var.get())
        self.attributes("-topmost", on)
        self._cfg["topmost"] = on
        save_ui_config(self._cfg)

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
        self.hdr_status.configure(text=f"T-{remain:03d}s  {blink}")
        self.after(500, self._pulse)

    def refresh_now(self):
        if self._refreshing:
            return
        self._refreshing = True
        self.meta.configure(text="syncing telemetry...")

        def work():
            msg, data = fetch_usage()
            with self._lock:
                self._msg = msg
                self._data = data
            self.after(0, self._paint)

        threading.Thread(target=work, daemon=True).start()

    def _set_extra(self, content: str):
        self.extra.configure(state="normal")
        self.extra.delete("1.0", "end")
        self.extra.insert("1.0", content)
        self.extra.configure(state="disabled")

    def _paint_card(self, card, win: dict):
        used = float(win.get("used_percent") or 0)
        reset_after = win.get("reset_after_seconds")
        color = RED if used >= 90 else AMBER if used >= 70 else CYAN
        card["pct"].configure(text=f"{used:05.1f}%".replace("0", "0") if False else f"{used:5.1f}%", fg=color)
        card["detail"].configure(text=f"USED {used:.1f}%   RESET IN {_fmt_duration(reset_after)}")
        card["meter"].set_value(used)

    def _paint(self):
        self._refreshing = False
        self._next_due = time.monotonic() + self.current_refresh_sec()
        with self._lock:
            msg = self._msg
            data = self._data
        if msg != "ok" or not data:
            self.meta.configure(text=f"FAULT // {msg}")
            self._set_extra(msg)
            return
        s = summarize(data)
        status = "OK" if s.get("allowed") and not s.get("limit_reached") else "LIMIT"
        self.meta.configure(
            text=f"{s.get('email')}   PLAN={str(s.get('plan') or '-').upper()}   STATE={status}   @{s.get('checked_at')}"
        )
        self._paint_card(self.card5, s.get("primary") or {})
        self._paint_card(self.card7, s.get("secondary") or {})
        credits = s.get("credits") or {}
        lines = [
            f"credits.balance        {credits.get('balance')}",
            f"credits.has            {credits.get('has_credits')}   unlimited={credits.get('unlimited')}",
            f"banked.resets          {s.get('banked_resets')}   applicable={s.get('banked_applicable')}",
            f"reached.type           {s.get('reached_type')}",
            f"source                 chatgpt.com/backend-api/wham/usage",
            f"note                   5h AND 7d must both have remaining quota",
        ]
        self._set_extra("\n".join(lines))


    def is_compact(self) -> bool:
        return str(self._cfg.get("mode") or "detail") == "compact"

    def toggle_mode(self):
        self._cfg["mode"] = "detail" if self.is_compact() else "compact"
        save_ui_config(self._cfg)
        self.apply_mode()

    def apply_mode(self):
        compact = self.is_compact()
        if compact:
            self.meta.pack_forget()
            self.extra_wrap.pack_forget()
            self.ctrl.pack_forget()
            self.btns.pack_forget()
            if not hasattr(self, "compact_bar"):
                self.compact_bar = tk.Frame(self, bg=BG)
                self._btn(self.compact_bar, "SYNC", self.refresh_now).pack(side="left")
                self.compact_mode_btn = self._btn(self.compact_bar, "DETAIL", self.toggle_mode)
                self.compact_mode_btn.pack(side="right")
            self.card5["title"].configure(text="5H")
            self.card7["title"].configure(text="7D")
            self.card5["wrap"].pack(fill="x", padx=14, pady=(2, 4))
            self.card7["wrap"].pack(fill="x", padx=14, pady=(2, 4))
            self.compact_bar.pack(fill="x", padx=14, pady=(4, 10))
            self.minsize(300, 200)
            self.maxsize(520, 360)
        else:
            if hasattr(self, "compact_bar"):
                self.compact_bar.pack_forget()
            self.card5["title"].configure(text="PRIMARY  //  5H WINDOW")
            self.card7["title"].configure(text="SECONDARY  //  7D WINDOW")
            self.maxsize(1400, 1200)
            self.minsize(420, 400)
            self.meta.pack(fill="x", padx=14)
            self.card5["wrap"].pack(fill="x", padx=14, pady=6)
            self.card7["wrap"].pack(fill="x", padx=14, pady=6)
            self.extra_wrap.pack(fill="both", expand=True, padx=14, pady=8)
            self.ctrl.pack(fill="x", padx=14, pady=(0, 6))
            self.btns.pack(fill="x", padx=14, pady=(0, 12))
            self.mode_btn.configure(text="COMPACT")
        pad = (4, 6) if compact else (6, 10)
        for card in (self.card5, self.card7):
            card["meter"].pack_configure(pady=pad)
        self._fit_window()

    def _fit_window(self):
        self.update_idletasks()
        w = int(self.winfo_reqwidth())
        h = int(self.winfo_reqheight())
        if self.is_compact():
            w = max(w, 340)
            h = max(h, 220)
        else:
            w = max(w, 460)
            h = max(h, 470)
        self.geometry(f"{w}x{h}")

    def on_close(self):
        self._stop.set()
        self.destroy()


def main():
    app = Hud()
    app.protocol("WM_DELETE_WINDOW", app.on_close)
    app.mainloop()


if __name__ == "__main__":
    main()
