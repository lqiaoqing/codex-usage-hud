"""Antigravity 本地会话统计和 Tk 页面。"""
from collections import deque
from contextlib import closing
from datetime import date, datetime
from functools import lru_cache
import json
import os
from pathlib import Path
import platform
import re
import shutil
import sqlite3
import subprocess
import threading
from time import monotonic
from urllib.request import getproxies
import tkinter as tk

ROOT = Path.home() / ".gemini" / "antigravity"
HELPER_DIR = Path(__file__).parent / "antigravity-hud"
PROBE = HELPER_DIR / "publish" / "AntigravityUsageHud.exe"
GUID = re.compile(r"[0-9a-f-]{36}", re.I)
BG, CARD, TEXT, DIM, MINT, WARN = "#0d141f", "#151f2d", "#edf3f9", "#aabbce", "#59dec2", "#ffc277"


def t3_cli_path(home):
    architecture = {"amd64": "x64", "arm64": "arm64"}.get(platform.machine().lower())
    if not architecture:
        return None
    root = home / ".t3" / "tools" / "antigravity-acp" / f"win32-{architecture}"
    try:
        active = json.loads((root / "active.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    release_id = active.get("releaseId") if isinstance(active, dict) else None
    if isinstance(release_id, str) and re.fullmatch(r"[0-9a-f]{64}", release_id):
        executable = root / "versions" / release_id / "agy.exe"
        if executable.is_file():
            return executable
    return None


def cli_path():
    home = Path.home()
    installed = home / "AppData" / "Local" / "agy" / "bin" / "agy.exe"
    bundled = HELPER_DIR / "runtime" / "agy.exe"
    found = shutil.which("agy.exe")
    executable = next((p for p in (installed, bundled, Path(found) if found else None) if p and p.is_file()), None)
    return executable or t3_cli_path(home)


@lru_cache(maxsize=4)
def cli_supports_quota(executable, modified):
    run = subprocess.run([str(executable), "--version"], stdin=subprocess.DEVNULL, capture_output=True,
                         timeout=5, creationflags=subprocess.CREATE_NO_WINDOW)
    match = re.search(rb"(?<!\d)v?(\d+)\.(\d+)\.(\d+)", run.stdout)
    return run.returncode == 0 and bool(match) and tuple(map(int, match.groups())) >= (1, 2, 15)


def quota_groups(payload, zh):
    groups = []
    for group in payload.get("command", {}).get("data", {}).get("groups") or []:
        reading = {"Name": group.get("name") or ""}
        if not reading["Name"]:
            continue
        for bucket in group.get("buckets") or []:
            window = str(bucket.get("window") or "").lower()
            bucket_id = str(bucket.get("id") or "").lower()
            if window in ("weekly", "7d") or (not window and bucket_id.endswith(("-weekly", "-7d"))):
                key = "Weekly"
            elif window in ("5h", "five_hour") or (not window and bucket_id.endswith("-5h")):
                key = "FiveHours"
            else:
                continue
            remaining = bucket.get("remaining_fraction")
            if bucket.get("disabled") or type(remaining) not in (int, float) or not 0 <= remaining <= 1:
                continue
            reset = None
            if bucket.get("reset_time"):
                try:
                    at = datetime.fromisoformat(bucket["reset_time"].replace("Z", "+00:00"))
                    seconds = (at - datetime.now(at.tzinfo)).total_seconds()
                    if seconds > 0:
                        if seconds >= 86400:
                            reset = f"{int(seconds / 86400)} 天" if zh else f"in {int(seconds / 86400)}d"
                        elif seconds >= 3600:
                            reset = f"{int(seconds / 3600)} 小时" if zh else f"in {int(seconds / 3600)}h"
                        else:
                            reset = f"{max(1, int(seconds / 60))} 分钟" if zh else f"in {max(1, int(seconds / 60))}m"
                except (TypeError, ValueError, AttributeError):
                    pass
            reading[key] = {"Remaining": round(remaining * 100, 2), "Reset": reset}
        if len(reading) > 1:
            groups.append(reading)
    return groups


def read_cli_quota(zh, proxy=None):
    executable = cli_path()
    if not executable:
        return [], "未找到 Antigravity CLI" if zh else "Antigravity CLI not installed"
    try:
        if not cli_supports_quota(executable, executable.stat().st_mtime_ns):
            return [], "请升级 Antigravity CLI" if zh else "Update Antigravity CLI"
        env = os.environ.copy()
        proxies = {"http": proxy, "https": proxy} if proxy else getproxies()
        for scheme in ("http", "https"):
            if proxies.get(scheme):
                env[scheme.upper() + "_PROXY"] = proxies[scheme]
        run = subprocess.run([str(executable), "-p", "/usage", "--output-format", "json", "--log-file", "NUL"],
                             cwd=Path.home(), stdin=subprocess.DEVNULL, capture_output=True,
                             env=env, timeout=30, creationflags=subprocess.CREATE_NO_WINDOW)
    except subprocess.TimeoutExpired:
        return [], "Antigravity CLI 查询超时" if zh else "Antigravity CLI timed out"
    except OSError:
        return [], "Antigravity CLI 无法启动" if zh else "Antigravity CLI could not start"
    try:
        payload = json.loads(run.stdout)
        groups = quota_groups(payload, zh) if run.returncode == 0 and payload.get("status") == "SUCCESS" else []
    except (ValueError, AttributeError, TypeError):
        groups = []
    if groups:
        return groups, ""
    diagnostic = (run.stderr + run.stdout).decode("utf-8", "replace").lower()
    if any(message in diagnostic for message in ("not logged in", "not authenticated", "authentication required",
                                                "agy login", "sign in", "no credentials")):
        return [], "请先登录 Antigravity CLI" if zh else "Sign in to Antigravity CLI first"
    return [], "Antigravity CLI 未返回额度" if zh else "Antigravity CLI returned no quota"


def fields(blob):
    def varint():
        nonlocal pos
        value = 0
        for shift in range(0, 70, 7):
            if pos >= len(blob):
                break
            byte = blob[pos]
            pos += 1
            value |= (byte & 127) << shift
            if byte < 128:
                return value
        raise ValueError("invalid varint")

    pos = 0
    while pos < len(blob):
        tag = varint()
        if not tag >> 3:
            raise ValueError("invalid field")
        wire = tag & 7
        if wire == 0:
            yield tag >> 3, varint()
        elif wire == 2:
            size = varint()
            end = pos + size
            if end > len(blob):
                raise ValueError("truncated field")
            yield tag >> 3, blob[pos:end]
            pos = end
        elif wire in (1, 5):
            pos += 8 if wire == 1 else 4
            if pos > len(blob):
                raise ValueError("truncated field")
        else:
            raise ValueError("unsupported wire type")


def db(path):
    wal = path.with_suffix(".db-wal")
    mode = "?mode=ro" if wal.exists() and wal.stat().st_size else "?mode=ro&immutable=1"
    return closing(sqlite3.connect(path.as_uri() + mode, uri=True, timeout=0.3))


def model_price(model, day):
    model = re.sub(r"-(low|medium|high|xhigh|thinking|trial|n)$", "", model)
    argon = (2, .10, None, 10) if day < date(2026, 12, 1) else (4, .20, None, 20)
    flash = (.75, .075, None, 3.75) if day < date(2027, 1, 1) else (1.5, .15, None, 7.5)
    gemini_write = 1.875 if day >= date(2027, 1, 12) else None
    return {
        "gemini-4-hydrogen": (1.5, .075, gemini_write, 7.5),
        "gemini-4-neon": (3, .15, 3.75 if gemini_write else None, 15),
        "gemini-4-argon": argon, "gemini-4-carbon": argon, "gemini-3.8-flash": flash,
        "claude-sonnet-5-5": (2, .10, None, 10), "claude-opus-5-5": (4, .20, None, 20),
        "claude-opus-4-6": (5, .50, None, 25),
    }.get(model)


def estimate_generation(model, counts, day):
    prices = model_price(model, day)
    if prices is None:
        return None, True
    known = [count * price for count, price in zip(counts, prices) if count and price is not None]
    missing = any(count and price is None for count, price in zip(counts, prices))
    return sum(known) / 1e6 if known else None, missing


def read_tasks(selected, current, cache, root=ROOT):
    price_day = date.today()
    with db(root / "conversation_summaries.db") as index:
        summaries = list(index.execute(
            "SELECT conversation_id,title,agent_name,parent_conversation_id "
            "FROM conversation_summaries ORDER BY last_modified_time DESC"))
        relationships = list(index.execute("SELECT conversation_id,parent_id FROM conversation_parents"))
    rows = {row[0]: row for row in summaries if GUID.fullmatch(row[0])}
    parents = [(key, title or key[-8:]) for key, title, agent, parent in rows.values() if not parent and not agent]
    active = selected or current or (parents[0][0] if parents else None)
    if not active or not GUID.fullmatch(active):
        return parents[:8], None, []
    edges = {}
    for child, parent in relationships + [(key, row[3]) for key, row in rows.items() if row[3]]:
        if GUID.fullmatch(child) and parent:
            edges.setdefault(parent, set()).add(child)
    pending, visited, seen, result = deque([(active, None)]), set(), set(), []
    while pending:
        key, parent = pending.popleft()
        if key in visited:
            continue
        visited.add(key)
        path = root / "conversations" / (key + ".db")
        name = rows.get(key, (key, None, None, None))
        label = (name[2] if parent else name[1]) or key[-8:]
        try:
            file_state = tuple((p.stat().st_size, p.stat().st_mtime_ns) if p.exists() else None
                               for p in (path, path.with_suffix(".db-wal")))
            fingerprint = (price_day, file_state)
            cached = cache.get(key)
            if cached and cached[0] == fingerprint:
                refs, generations = cached[1:]
            else:
                with db(path) as connection:
                    refs = [dict(fields(blob)) for blob, in connection.execute("SELECT data FROM parent_references")]
                    generations = []
                    for idx, blob in connection.execute("SELECT idx,data FROM gen_metadata ORDER BY idx"):
                        model = dict(fields(dict(fields(blob)).get(1, b"")))
                        usage = dict(fields(model.get(4, b"")))
                        output = usage.get(3, usage.get(9, 0) + usage.get(10, 0))
                        counts = (usage.get(2, 0), usage.get(5, 0), usage.get(4, 0), output)
                        if any(counts):
                            dollars, missing = estimate_generation(model.get(19, b"").decode(errors="replace"),
                                                                   counts, price_day)
                            generations.append((usage.get(11) or (key, idx), counts, dollars, missing))
                cache[key] = fingerprint, refs, generations
            if parent:
                proven = any(ref.get(5) == 6 and ref.get(6) == parent.encode() for ref in refs)
                if not (proven or (not refs and name[2] and name[3] == parent)):
                    continue
        except (OSError, sqlite3.Error, TypeError, ValueError):
            if not parent or (name[2] and name[3] == parent):
                result.append((label, None))
            continue
        totals, cost, priced, unknown = [0, 0, 0, 0], 0.0, False, False
        for response, counts, dollars, missing in generations:
            if response in seen:
                continue
            seen.add(response)
            totals = [a + b for a, b in zip(totals, counts)]
            unknown |= missing
            if dollars is not None:
                cost += dollars
                priced = True
        result.append((label, (*totals, cost if priced else None, unknown)))
        pending.extend((child, key) for child in sorted(edges.get(key, ())))
    return parents[:8], active, result


def short(n):
    return f"{n/1e6:.2f}M" if n >= 1e6 else f"{n/1e3:.1f}K" if n >= 1000 else str(n)


class AntigravityPanel(tk.Frame):
    def __init__(self, host, resolve_proxy):
        super().__init__(host, bg=BG)
        self.host = host
        self.resolve_proxy = resolve_proxy
        self.cache = {}
        self.snapshot = []
        self.updated = ""
        self.state = 0
        self.busy = False
        self.next_refresh = 0
        self.follow_busy = False
        self.refreshing = False
        self.local_busy = False
        self.current = None
        self.selected = None
        self.error = ""
        self.tasks = ([], None, [])
        self.expanded = False
        self.following = False
        self._rendered = None
        self.after(400, self.tick)

    def set_refresh_interval(self, seconds):
        self.next_refresh = monotonic() + seconds

    def tick(self):
        mini = self.host.is_mini()
        if not mini and self.host.active_tab == "antigravity":
            self.poll_tasks()
            self.follow_desktop()
        visible = ("antigravity" in self.host.mini_providers()) if mini else self.host.active_tab == "antigravity"
        if visible and monotonic() >= self.next_refresh:
            self.poll()
        self.after(4000, self.tick)

    def poll(self, action="read"):
        if action == "open":
            self.follow_desktop(open_page=True)
            return
        if self.busy:
            if action == "refresh":
                self.refreshing = True
                self.update_view()
            return
        self.busy = True
        self.set_refresh_interval(self.host.current_refresh_sec())
        self.refreshing = action == "refresh"
        if self.refreshing:
            self.update_view()
        zh = self.host.lang() == "zh"

        def work():
            groups, error = read_cli_quota(zh, self.resolve_proxy())
            self.after(0, lambda: self.finish_quota(groups, error))

        threading.Thread(target=work, daemon=True).start()
        if not self.host.is_mini():
            self.poll_tasks()
            self.follow_desktop()

    def finish_quota(self, groups, error):
        previous_mini = self.mini_values()
        self.busy = self.refreshing = False
        self.error = error
        self.state = 3 if groups else 0
        if groups:
            self.snapshot = groups
            self.updated = datetime.now().strftime("%H:%M:%S")
        self.update_view(previous_mini)

    def follow_desktop(self, open_page=False):
        if not open_page and (self.follow_busy or self.selected):
            return
        if not open_page:
            self.follow_busy = True

        def work():
            try:
                raw = subprocess.check_output([str(PROBE), "open" if open_page else "conversation"],
                                              timeout=5, creationflags=subprocess.CREATE_NO_WINDOW)
                current = json.loads(raw).get("ConversationId")
            except (OSError, ValueError, subprocess.SubprocessError):
                current = None
            if not open_page:
                self.after(0, lambda: self.finish_follow(current))

        threading.Thread(target=work, daemon=True).start()

    def finish_follow(self, current):
        self.follow_busy = False
        if current != self.current:
            self.current = current
            self.following = bool(current)
            self.poll_tasks()

    def poll_tasks(self):
        if self.local_busy:
            return
        self.local_busy = True
        selected, current = self.selected, self.current

        def work():
            try:
                tasks = read_tasks(selected, current, self.cache)
            except (sqlite3.Error, OSError):
                tasks = ([], None, [])
            self.after(0, lambda: self.finish_tasks(tasks, selected, current))

        threading.Thread(target=work, daemon=True).start()

    def finish_tasks(self, tasks, selected, current):
        self.local_busy = False
        if (selected, current) != (self.selected, self.current):
            if self.host.active_tab == "antigravity":
                self.poll_tasks()
            return
        self.tasks = tasks
        self.update_view()

    def update_view(self, previous_mini=None):
        if self.host.is_mini():
            if ("antigravity" in self.host.mini_providers()
                    and previous_mini is not None and self.mini_values() != previous_mini):
                self.host._redraw_capsule()
        elif self.host.active_tab == "antigravity" and self.render():
            self.host._fit_window()

    def mini_values(self):
        result = []
        for name, aliases in (("G", ("gemini", "gemini models")),
                              ("C", ("claude / gpt", "claude and gpt models", "claude + gpt models"))):
            group = next((g for g in self.snapshot if g["Name"].lower() in aliases), {})
            windows = [group.get(key) for key in ("FiveHours", "Weekly")]
            values = "/".join(f"{win['Remaining']:g}%" if win else "—" for win in windows)
            result.append((name, values))
        return tuple(result)

    def _label(self, parent, value="", color=TEXT, size=10, bold=False, bg=CARD):
        return tk.Label(parent, text=value, fg=color, bg=bg, anchor="w",
                        font=("Microsoft YaHei UI", size, "bold" if bold else "normal"))

    @staticmethod
    def _text(widget, text, color=None):
        if widget.cget("text") != text:
            widget.configure(text=text)
        if color is not None and widget.cget("fg") != color:
            widget.configure(fg=color)

    @staticmethod
    def _meter(parent):
        track = tk.Frame(parent, height=5, bg="#2b394c")
        fill = tk.Frame(track, bg=MINT)
        return track, fill

    @staticmethod
    def _fill(fill, percent, color):
        if fill.cget("bg") != color:
            fill.configure(bg=color)
        position = fill.place_info()
        if not position or float(position["relwidth"]) != percent / 100:
            fill.place(relwidth=percent / 100, relheight=1)

    def _build_view(self, zh, compact):
        for child in self.winfo_children():
            child.destroy()
        self._rendered = zh, compact
        self._menu_choices = None
        self._layout = None

        header = tk.Frame(self, bg=BG)
        header.pack(fill="x", padx=12, pady=(8, 5))
        self._label(header, "账户剩余额度" if zh else "Remaining quota", bold=True, bg=BG).pack(side="left")
        self.status_label = self._label(header, color=DIM, size=8, bg=BG)
        self.status_label.pack(side="right")
        self.groups = tk.Frame(self, bg=BG)
        self.groups.pack(fill="x", padx=12)
        self.group_cards = []

        header = tk.Frame(self, bg=BG)
        header.pack(fill="x", padx=12, pady=(10, 4))
        self._label(header, "当前任务" if zh else "Current task", bold=True, bg=BG).pack(side="left")
        self.selector = tk.Menubutton(header, bg=CARD, fg=TEXT, padx=10, pady=4,
                                      activebackground="#2a3d50", activeforeground=TEXT,
                                      font=("Microsoft YaHei UI", 9), takefocus=True)
        self.selector.pack(side="right")
        self.menu = tk.Menu(self.selector, tearoff=False, bg=CARD, fg=TEXT,
                            activebackground="#2a3d50", activeforeground=TEXT)
        self.selector.configure(menu=self.menu)

        content = tk.Frame(self, bg=CARD, padx=11, pady=9, highlightbackground="#263447", highlightthickness=1)
        content.pack(fill="x", padx=12, pady=(0, 8))
        self.empty = self._label(content, "暂无本地用量记录" if zh else "No local usage available", DIM)
        self.task_body = tk.Frame(content, bg=CARD)
        self.title_label = self._label(self.task_body, bold=True)
        self.title_label.pack(fill="x")
        metrics = tk.Frame(self.task_body, bg=CARD)
        metrics.pack(fill="x", pady=(5, 3))
        self.tokens = self._label(metrics, size=22, bold=True)
        self.tokens.pack(side="left")
        self._label(metrics, "总 Token" if zh else "Total tokens", DIM, 9).pack(side="left", padx=7, anchor="s", pady=5)
        estimate = tk.Frame(metrics, bg=CARD)
        estimate.pack(side="right", anchor="n")
        self.cost = self._label(estimate, color=MINT, size=17, bold=True)
        self.cost.pack(anchor="e")
        self._label(estimate, "API 估价" if zh else "API estimate", DIM, 8).pack(anchor="e")
        self.split = self._label(self.task_body, color=DIM)
        self.hit = self._label(self.task_body, color=DIM)
        track, self.hit_bar = self._meter(self.task_body)
        if not compact:
            self.split.pack(fill="x")
            self.hit.pack(fill="x")
            track.pack(fill="x", pady=(3, 4))
        self.partial = self._label(self.task_body, "部分模型或用量记录无法估价" if zh else "Partial estimate or usage unavailable", WARN, 8)

        self.summary = tk.Frame(self, bg=CARD, padx=11, pady=7, highlightbackground="#263447", highlightthickness=1)
        self.details_button = self.host._btn(self.summary, "", self.toggle_details)
        self.details_button.configure(bg=CARD, fg=TEXT, font=("Microsoft YaHei UI", 10, "bold"),
                                      anchor="w", padx=0, highlightthickness=0)
        self.details_button.pack(fill="x")
        self.table = tk.Frame(self.summary, bg=CARD)
        self.table.columnconfigure(0, weight=1)
        headings = ("会话", "总 Token", "缓存命中", "API 估价") if zh else ("Session", "Total tokens", "Cache read", "API estimate")
        for column, heading in enumerate(headings):
            self._label(self.table, heading, DIM, 8).grid(row=0, column=column, sticky="w", padx=(0, 8))
        self.detail_rows = []
        self.footer = self._label(self, "按公开 API 标价折算" if zh else "Estimate based on public API list prices",
                                  DIM, 8, bg=BG)

    def _render_groups(self, zh, compact):
        groups = {group["Name"]: group for group in self.snapshot}
        ordered = []
        for base, aliases in (("Gemini", ("gemini", "gemini models")),
                              ("Claude / GPT", ("claude / gpt", "claude and gpt models", "claude + gpt models"))):
            name = next((name for name in groups if name.lower() in aliases), None)
            ordered.append((base, groups.pop(name, {})))
        ordered.extend(groups.items())
        for i, (name, group) in enumerate(ordered):
            if i == len(self.group_cards):
                box = tk.Frame(self.groups, bg=CARD, padx=11, pady=9, highlightbackground="#263447", highlightthickness=1)
                title = self._label(box, size=12, bold=True)
                title.pack(fill="x", pady=(0, 4))
                windows = []
                for span in ("5 小时" if zh else "5h", "每周" if zh else "Weekly"):
                    row = tk.Frame(box, bg=CARD)
                    row.pack(fill="x", pady=(2, 0))
                    self._label(row, span, DIM).pack(side="left")
                    value = self._label(row, size=13, bold=True)
                    value.pack(side="right")
                    track, fill = self._meter(box)
                    hint = self._label(box, size=8)
                    if not compact:
                        track.pack(fill="x", pady=(3, 4))
                        hint.pack(fill="x")
                    windows.append((value, fill, hint))
                self.group_cards.append((box, title, windows))
            box, title, windows = self.group_cards[i]
            if not box.winfo_manager():
                self.groups.columnconfigure(i, weight=1, uniform="quota")
                box.grid(row=0, column=i, sticky="nsew", padx=(0 if i == 0 else 8, 0))
            self._text(title, name)
            for key, (value, fill, hint) in zip(("FiveHours", "Weekly"), windows):
                data = group.get(key) or {}
                remaining = data.get("Remaining")
                tone = DIM if remaining is None else "#ff7f8b" if remaining <= 10 else WARN if remaining <= 30 else MINT
                self._text(value, "—" if remaining is None else f"{remaining:g}%", tone)
                self._fill(fill, remaining or 0, tone)
                reset = data.get("Reset")
                message = ("约 " + reset + "后重置" if zh else reset) if reset else " "
                low = remaining is not None and remaining <= 30
                if low:
                    message = ("额度偏低" if zh else "Low quota") + (" · " + message if reset else "")
                self._text(hint, message, tone if low else DIM)
        for i in range(len(ordered), len(self.group_cards)):
            box = self.group_cards[i][0]
            if box.winfo_manager():
                box.grid_remove()
                self.groups.columnconfigure(i, weight=0)
        return len(ordered)

    def _select(self, key):
        self.selected = key
        self.tasks = self.tasks[0], None, []
        self.update_view()
        self.poll_tasks()

    def _render_details(self, zh, details):
        for index, (title, row) in enumerate(details):
            if index == len(self.detail_rows):
                widgets = (self._label(self.table, bold=True), self._label(self.table, bold=True),
                           self._label(self.table, color=DIM, size=9), self._label(self.table, color=MINT, size=9),
                           self._label(self.table, color=DIM, size=8))
                for column, widget in enumerate(widgets[:4]):
                    widget.grid(row=index*2+1, column=column, sticky="nw", padx=(0, 8))
                widgets[-1].grid(row=index*2+2, column=1, sticky="w", pady=(0, 4))
                self.detail_rows.append(widgets)
            if row is None:
                total, split, cache_hit, estimate = "—", "", "—", "—"
            else:
                a, b, c, d, cost, missing = row
                ratio = f"{b/(a+b+c):.0%}" if a+b+c else "—"
                estimate = f"${cost:.2f}" + ("+" if missing else "") if cost is not None else "—"
                total = short(a+b+c+d)
                split = f"{'入' if zh else 'In'} {short(a+b+c)} · {'出' if zh else 'Out'} {short(d)}"
                cache_hit = f"{short(b)} · {ratio}"
            for widget, text in zip(self.detail_rows[index], (("↳ " if index else "") + title[:21], total, cache_hit, estimate, split)):
                if not widget.winfo_manager():
                    widget.grid()
                self._text(widget, text)
        for widgets in self.detail_rows[len(details):]:
            for widget in widgets:
                if widget.winfo_manager():
                    widget.grid_remove()

    def render(self):
        zh, compact = self.host.lang() == "zh", self.host.is_compact()
        if self._rendered != (zh, compact):
            self._build_view(zh, compact)
        status = ("正在刷新额度…" if zh else "Refreshing quota…") if self.refreshing else self.error
        if not status and self.updated:
            prefix = ("更新于 " if self.state == 3 else "上次读取 ") if zh else (
                "Updated " if self.state == 3 else "Last read ")
            status = prefix + self.updated
        if not status:
            status = "正在读取 CLI 额度…" if zh else "Reading CLI quota…"
        self._text(self.status_label, status)
        group_count = self._render_groups(zh, compact)

        choices, active, details = self.tasks
        follow = ("跟随桌面" if zh else "Follow desktop") if self.following else ("最近活动" if zh else "Latest activity")
        menu_choices = [(None, follow), *choices]
        if menu_choices != self._menu_choices:
            self.menu.delete(0, "end")
            for key, title in menu_choices:
                self.menu.add_command(label=title, command=lambda k=key: self._select(k))
            self._menu_choices = menu_choices
        selected = next((title for key, title in choices if key == self.selected), follow)
        self._text(self.selector, selected[:18] + "  ▾")

        counted = [row for _, row in details if row is not None and any(row[:4])]
        uncached, hit, write, out = [sum(row[i] for row in counted) for i in range(4)]
        inp = uncached + hit + write
        costs = [row[4] for row in counted if row[4] is not None]
        partial = bool(counted) and (any(row[5] for row in counted) or any(row is None for _, row in details))
        money = f"${sum(costs):.2f}" + ("+" if partial else "") if costs else "—"
        title = next((title for key, title in choices if key == active), details[0][0] if details else "")
        self._text(self.title_label, title[:48])
        self._text(self.tokens, short(inp + out))
        self._text(self.cost, money)
        self._text(self.split, f"{'输入' if zh else 'Input'} {short(inp)}    {'输出' if zh else 'Output'} {short(out)}")
        rate = f"{hit / inp:.0%}" if inp else "—"
        self._text(self.hit, f"{'缓存命中' if zh else 'Cache read'}  {short(hit)} · {rate}")
        self._fill(self.hit_bar, 100 * hit / inp if inp else 0, MINT)
        self._text(self.details_button, ("主会话与子代理" if zh else "Conversation and subagents") + f"  ({len(details)-1})")

        show_details = bool(counted) and not compact and len(details) > 1
        expanded = show_details and self.expanded
        visible = ((self.empty, not counted, dict(fill="x")),
                   (self.task_body, bool(counted), dict(fill="x")),
                   (self.partial, partial and not compact, dict(fill="x")),
                   (self.summary, show_details, dict(fill="x", padx=12, pady=(0, 8))),
                   (self.table, expanded, dict(fill="x", pady=(2, 0))),
                   (self.footer, bool(counted) and not compact, dict(fill="x", padx=12, pady=(0, 8))))
        for widget, show, pack in visible:
            managed = bool(widget.winfo_manager())
            if show and not managed:
                if widget is self.summary and self.footer.winfo_manager():
                    pack["before"] = self.footer
                widget.pack(**pack)
            elif managed and not show:
                widget.pack_forget()
        if expanded:
            self._render_details(zh, details)
        layout = (group_count, bool(counted), show_details, len(details) if expanded else 0, partial)
        resized = layout != self._layout
        self._layout = layout
        return resized

    def toggle_details(self):
        self.expanded = not self.expanded
        self.update_view()
