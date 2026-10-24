"""TypeBot —— 以人类自然节奏打字的机器人（Windows）。

用法：在界面中填好文本、设置速度 → 点击任意程序里的文本框 → 按全局快捷键（默认 Ctrl+Alt+\\）。
再按一次暂停，再按继续；打字过程中按 Esc 立即停止。
"""
from __future__ import annotations

import ctypes
import json
import os
import queue
import sys
import tkinter as tk
import traceback
from tkinter import filedialog, messagebox, ttk

import engine
import winapi

APP = "TypeBot"
CONFIG_PATH = os.path.join(os.environ.get("APPDATA") or os.path.expanduser("~"), APP, "settings.json")
HOTKEY_TOGGLE = 1
NEWLINE_LABELS = {"enter": "Enter", "shift_enter": "Shift+Enter（聊天软件里换行不发送）"}
SAMPLE_TEXT = (
    "The quick brown fox jumps over the lazy dog. Typing at a natural pace means "
    "small hesitations, quick bursts on familiar words, and the occasional typo.\n"
    "这是一段中文示例，中文按每字约三次键击计算速度。"
)

DEFAULTS = {
    "wpm": 60,
    "variability": 35,       # %
    "typo_rate": 1.0,        # %
    "thinking": 3.0,         # %
    "punct_pause": True,
    "start_delay": 0.5,      # 秒
    "newline_mode": "enter",
    "skip_indent": False,
    "focus_guard": True,
    "esc_stop": True,
    "topmost": False,
    "source": "text",
    "hotkey_mods": winapi.MOD_CONTROL | winapi.MOD_ALT,
    "hotkey_vk": winapi.VK_OEM_5,
    "text": "",
}

COLORS = {"idle": "#374151", "busy": "#2563eb", "paused": "#b45309", "ok": "#15803d", "err": "#b91c1c"}


def load_config() -> dict:
    cfg = dict(DEFAULTS)
    try:
        with open(CONFIG_PATH, encoding="utf-8") as f:
            data = json.load(f)
        cfg.update({k: v for k, v in data.items() if k in DEFAULTS})
    except (OSError, ValueError):
        pass
    return cfg


def save_config(cfg: dict) -> None:
    try:
        os.makedirs(os.path.dirname(CONFIG_PATH), exist_ok=True)
        with open(CONFIG_PATH, "w", encoding="utf-8") as f:
            json.dump(cfg, f, ensure_ascii=False, indent=2)
    except OSError:
        pass


def fmt_duration(sec: float) -> str:
    sec = max(0, int(round(sec)))
    if sec < 60:
        return f"{sec} 秒"
    m, s = divmod(sec, 60)
    if m < 60:
        return f"{m} 分 {s:02d} 秒"
    h, m = divmod(m, 60)
    return f"{h} 小时 {m:02d} 分"


class App:
    def __init__(self, root: tk.Tk):
        self.root = root
        self.cfg = load_config()
        self.events: queue.Queue = queue.Queue()
        self.state = "idle"          # idle | countdown | typing | paused
        self.typist: engine.Typist | None = None
        self.token = 0               # 区分新旧打字任务，忽略过期事件
        self.plan: engine.Plan | None = None
        self.hotkey_ok = True
        self.preview = None          # (窗口, Text, 信息标签, typist, token)

        self.hotkeys = winapi.HotkeyListener(
            on_hotkey=lambda hid: self.events.put(("hotkey", hid)),
            on_result=lambda res: self.events.put(("hk_result", res)),
        )
        self.hotkeys.start()

        self._build_ui()
        self._apply_hotkey()
        self._set_state("idle")
        self._update_estimate()
        root.protocol("WM_DELETE_WINDOW", self.on_close)
        root.after(15, self._poll)

    # ================================================================ 界面
    def _build_ui(self) -> None:
        root = self.root
        root.title("TypeBot · 拟人打字机器人")
        root.minsize(900, 640)
        root.geometry("1000x700")

        style = ttk.Style()
        try:
            style.theme_use("vista")
        except tk.TclError:
            pass
        ui_font = ("Microsoft YaHei UI", 10)
        style.configure(".", font=ui_font)
        style.configure("Title.TLabel", font=("Microsoft YaHei UI", 16, "bold"))
        style.configure("Sub.TLabel", foreground="#6b7280")
        style.configure("Hint.TLabel", foreground="#6b7280", font=("Microsoft YaHei UI", 9))
        style.configure("Value.TLabel", foreground="#2563eb", font=("Microsoft YaHei UI", 10, "bold"))
        style.configure("Status.TLabel", font=("Microsoft YaHei UI", 11, "bold"))
        style.configure("Hotkey.TLabel", font=("Consolas", 12, "bold"), padding=(10, 4),
                        background="#eef2ff", foreground="#3730a3")
        style.configure("HotkeyBad.TLabel", font=("Consolas", 12, "bold"), padding=(10, 4),
                        background="#fee2e2", foreground="#b91c1c")
        style.configure("Big.TButton", font=("Microsoft YaHei UI", 10, "bold"), padding=(14, 6))
        style.configure("TLabelframe.Label", font=("Microsoft YaHei UI", 10, "bold"))

        self.v_wpm = tk.IntVar(value=int(self.cfg["wpm"]))
        self.v_var = tk.IntVar(value=int(self.cfg["variability"]))
        self.v_typo = tk.DoubleVar(value=float(self.cfg["typo_rate"]))
        self.v_think = tk.DoubleVar(value=float(self.cfg["thinking"]))
        self.v_punct = tk.BooleanVar(value=bool(self.cfg["punct_pause"]))
        self.v_delay = tk.DoubleVar(value=float(self.cfg["start_delay"]))
        self.v_newline = tk.StringVar(value=NEWLINE_LABELS.get(self.cfg["newline_mode"], "Enter"))
        self.v_skip = tk.BooleanVar(value=bool(self.cfg["skip_indent"]))
        self.v_guard = tk.BooleanVar(value=bool(self.cfg["focus_guard"]))
        self.v_esc = tk.BooleanVar(value=bool(self.cfg["esc_stop"]))
        self.v_top = tk.BooleanVar(value=bool(self.cfg["topmost"]))
        self.v_source = tk.StringVar(value=self.cfg["source"])

        outer = ttk.Frame(root, padding=14)
        outer.pack(fill="both", expand=True)
        outer.columnconfigure(0, weight=1)
        outer.rowconfigure(1, weight=1)

        # ---- 标题
        head = ttk.Frame(outer)
        head.grid(row=0, column=0, sticky="ew", pady=(0, 10))
        ttk.Label(head, text="TypeBot", style="Title.TLabel").pack(side="left")
        self.lbl_how = ttk.Label(head, style="Sub.TLabel")
        self.lbl_how.pack(side="left", padx=14, pady=(6, 0))

        body = ttk.Frame(outer)
        body.grid(row=1, column=0, sticky="nsew")
        body.columnconfigure(0, weight=1)
        body.rowconfigure(0, weight=1)

        # ---- 左侧：文本
        left = ttk.LabelFrame(body, text=" 要输入的文本 ", padding=10)
        left.grid(row=0, column=0, sticky="nsew", padx=(0, 12))
        left.columnconfigure(0, weight=1)
        left.rowconfigure(1, weight=1)

        src = ttk.Frame(left)
        src.grid(row=0, column=0, columnspan=2, sticky="ew", pady=(0, 6))
        ttk.Label(src, text="文本来源：").pack(side="left")
        ttk.Radiobutton(src, text="下方文本框", value="text", variable=self.v_source,
                        command=self._on_source).pack(side="left")
        ttk.Radiobutton(src, text="按快捷键时的剪贴板内容", value="clipboard", variable=self.v_source,
                        command=self._on_source).pack(side="left", padx=(10, 0))

        self.txt = tk.Text(left, wrap="word", undo=True, font=("Microsoft YaHei UI", 11),
                           relief="solid", borderwidth=1, padx=8, pady=6,
                           highlightthickness=0, selectbackground="#c7d2fe")
        self.txt.grid(row=1, column=0, sticky="nsew")
        sb = ttk.Scrollbar(left, orient="vertical", command=self.txt.yview)
        sb.grid(row=1, column=1, sticky="ns")
        self.txt.configure(yscrollcommand=sb.set)
        self.txt.insert("1.0", self.cfg.get("text") or "")
        self.txt.edit_modified(False)
        self.txt.bind("<<Modified>>", self._on_text_modified)

        tools = ttk.Frame(left)
        tools.grid(row=2, column=0, columnspan=2, sticky="ew", pady=(8, 0))
        ttk.Button(tools, text="粘贴剪贴板", command=self._paste_clipboard).pack(side="left")
        ttk.Button(tools, text="打开文件…", command=self._open_file).pack(side="left", padx=6)
        ttk.Button(tools, text="清空", command=lambda: self.txt.delete("1.0", "end")).pack(side="left")
        self.lbl_count = ttk.Label(tools, style="Hint.TLabel")
        self.lbl_count.pack(side="right")

        # ---- 右侧：设置
        right = ttk.Frame(body, width=360)
        right.grid(row=0, column=1, sticky="ns")

        sp = ttk.LabelFrame(right, text=" 速度与节奏 ", padding=10)
        sp.pack(fill="x")
        sp.columnconfigure(1, weight=1)

        ttk.Label(sp, text="打字速度").grid(row=0, column=0, sticky="w")
        ttk.Scale(sp, from_=10, to=250, variable=self.v_wpm, length=170,
                  command=lambda v: self.v_wpm.set(int(round(float(v))))).grid(row=0, column=1, sticky="ew", padx=8)
        wpm_box = ttk.Frame(sp)
        wpm_box.grid(row=0, column=2, sticky="e")
        ttk.Spinbox(wpm_box, from_=10, to=250, increment=5, width=5,
                    textvariable=self.v_wpm).pack(side="left")
        ttk.Label(wpm_box, text=" WPM").pack(side="left")
        self.lbl_wpm_hint = ttk.Label(sp, style="Hint.TLabel")
        self.lbl_wpm_hint.grid(row=1, column=1, columnspan=2, sticky="w", padx=8, pady=(0, 6))

        presets = ttk.Frame(sp)
        presets.grid(row=2, column=0, columnspan=3, sticky="w", pady=(0, 8))
        ttk.Label(presets, text="预设：", style="Hint.TLabel").pack(side="left")
        for name, w in (("新手 25", 25), ("普通 40", 40), ("熟练 65", 65), ("高手 90", 90), ("极速 130", 130)):
            ttk.Button(presets, text=name, width=7,
                       command=lambda w=w: self.v_wpm.set(w)).pack(side="left", padx=1)

        self.lbl_var = self._slider(sp, 3, "节奏随机度", self.v_var, 0, 100, lambda v: f"{int(v)}%")
        self.lbl_typo = self._slider(sp, 4, "错字并改正", self.v_typo, 0, 10, lambda v: f"{v:.1f}%", step=0.5)
        self.lbl_think = self._slider(sp, 5, "思考停顿", self.v_think, 0, 15, lambda v: f"{v:.0f}%", step=1)
        ttk.Checkbutton(sp, text="标点、换行后自然停顿", variable=self.v_punct).grid(
            row=6, column=0, columnspan=3, sticky="w", pady=(4, 0))

        bh = ttk.LabelFrame(right, text=" 行为 ", padding=10)
        bh.pack(fill="x", pady=(10, 0))
        row = ttk.Frame(bh)
        row.pack(fill="x")
        ttk.Label(row, text="按快捷键后延迟").pack(side="left")
        ttk.Spinbox(row, from_=0, to=10, increment=0.5, width=5, textvariable=self.v_delay).pack(side="left", padx=6)
        ttk.Label(row, text="秒开始").pack(side="left")
        row = ttk.Frame(bh)
        row.pack(fill="x", pady=(6, 2))
        ttk.Label(row, text="换行按键").pack(side="left")
        ttk.Combobox(row, textvariable=self.v_newline, state="readonly", width=30,
                     values=list(NEWLINE_LABELS.values())).pack(side="left", padx=6)
        ttk.Checkbutton(bh, text="跳过行首缩进（编辑器会自动缩进时勾选）", variable=self.v_skip,
                        command=self._update_estimate).pack(anchor="w", pady=1)
        ttk.Checkbutton(bh, text="切换到其他窗口时自动暂停", variable=self.v_guard).pack(anchor="w", pady=1)
        ttk.Checkbutton(bh, text="打字时按 Esc 立即停止", variable=self.v_esc).pack(anchor="w", pady=1)
        ttk.Checkbutton(bh, text="窗口置顶", variable=self.v_top, command=self._on_topmost).pack(anchor="w", pady=1)

        hk = ttk.LabelFrame(right, text=" 全局快捷键 ", padding=10)
        hk.pack(fill="x", pady=(10, 0))
        row = ttk.Frame(hk)
        row.pack(fill="x")
        ttk.Label(row, text="开始 / 暂停 / 继续").pack(side="left")
        ttk.Button(row, text="更改…", command=self._record_hotkey).pack(side="right")
        self.lbl_hotkey = ttk.Label(row, style="Hotkey.TLabel")
        self.lbl_hotkey.pack(side="right", padx=8)
        ttk.Label(hk, style="Hint.TLabel", wraplength=330, justify="left",
                  text="注：Fn 键由键盘硬件自己处理，Windows 收不到它的信号，所以任何软件都无法把 Fn 设为快捷键。"
                  ).pack(anchor="w", pady=(8, 0))

        # ---- 底部：状态与控制
        foot = ttk.Frame(outer)
        foot.grid(row=2, column=0, sticky="ew", pady=(12, 0))
        foot.columnconfigure(0, weight=1)
        self.lbl_status = tk.Label(foot, anchor="w", font=("Microsoft YaHei UI", 11, "bold"))
        self.lbl_status.grid(row=0, column=0, sticky="ew")
        self.progress = ttk.Progressbar(foot, mode="determinate")
        self.progress.grid(row=1, column=0, sticky="ew", pady=(6, 2))
        self.lbl_detail = ttk.Label(foot, style="Hint.TLabel")
        self.lbl_detail.grid(row=2, column=0, sticky="w")
        btns = ttk.Frame(foot)
        btns.grid(row=0, column=1, rowspan=3, sticky="e", padx=(16, 0))
        ttk.Button(btns, text="速度预览", command=self._open_preview).pack(side="left")
        self.btn_main = ttk.Button(btns, style="Big.TButton", command=self._on_main_button)
        self.btn_main.pack(side="left", padx=8)
        self.btn_stop = ttk.Button(btns, text="■ 停止", command=self._stop)
        self.btn_stop.pack(side="left")

        for v in (self.v_wpm, self.v_var, self.v_typo, self.v_think):
            v.trace_add("write", lambda *_: self._update_labels())
        self._update_labels()
        self._on_source()
        self._on_topmost()

    def _slider(self, parent, row, title, var, lo, hi, fmt, step=1.0):
        ttk.Label(parent, text=title).grid(row=row, column=0, sticky="w", pady=3)
        ttk.Scale(parent, from_=lo, to=hi, variable=var, length=170,
                  command=lambda v: var.set(round(float(v) / step) * step)).grid(
            row=row, column=1, sticky="ew", padx=8)
        lbl = ttk.Label(parent, style="Value.TLabel", width=6, anchor="e")
        lbl.grid(row=row, column=2, sticky="e")
        lbl.fmt = fmt
        return lbl

    # ================================================================ 读取设置
    @staticmethod
    def _num(var, default, lo, hi):
        try:
            v = float(var.get())
        except (tk.TclError, ValueError):
            v = default
        return max(lo, min(hi, v))

    def _collect(self) -> dict:
        nl = next((k for k, v in NEWLINE_LABELS.items() if v == self.v_newline.get()), "enter")
        self.cfg.update(
            wpm=int(self._num(self.v_wpm, 60, 10, 250)),
            variability=int(self._num(self.v_var, 35, 0, 100)),
            typo_rate=self._num(self.v_typo, 1.0, 0, 10),
            thinking=self._num(self.v_think, 3.0, 0, 15),
            punct_pause=self.v_punct.get(),
            start_delay=self._num(self.v_delay, 0.5, 0, 10),
            newline_mode=nl,
            skip_indent=self.v_skip.get(),
            focus_guard=self.v_guard.get(),
            esc_stop=self.v_esc.get(),
            topmost=self.v_top.get(),
            source=self.v_source.get(),
            text=self.txt.get("1.0", "end-1c")[:200_000],
        )
        return self.cfg

    def _settings(self, for_preview=False) -> engine.TypingSettings:
        c = self._collect()
        return engine.TypingSettings(
            wpm=c["wpm"],
            variability=c["variability"] / 100,
            typo_rate=c["typo_rate"] / 100,
            thinking_rate=c["thinking"] / 100,
            punctuation_pause=c["punct_pause"],
            skip_leading_ws=c["skip_indent"] and not for_preview,
        )

    def _source_text(self) -> str:
        if self.v_source.get() == "clipboard":
            try:
                return engine.normalize_text(self.root.clipboard_get())
            except tk.TclError:
                return ""
        return engine.normalize_text(self.txt.get("1.0", "end-1c"))

    # ================================================================ 界面联动
    def _update_labels(self) -> None:
        wpm = int(self._num(self.v_wpm, 60, 10, 250))
        self.lbl_wpm_hint.config(text=f"≈ {wpm * 5} 键/分钟 · 约 {wpm * 5 / 3:.0f} 汉字/分钟")
        for lbl, var in ((self.lbl_var, self.v_var), (self.lbl_typo, self.v_typo), (self.lbl_think, self.v_think)):
            lbl.config(text=lbl.fmt(self._num(var, 0, 0, 100)))
        self._update_estimate()

    def _update_estimate(self) -> None:
        if not hasattr(self, "lbl_count"):
            return
        if self.v_source.get() == "clipboard":
            self.lbl_count.config(text="将在按下快捷键时读取剪贴板")
            return
        text = self.txt.get("1.0", "end-1c")
        wpm = self._num(self.v_wpm, 60, 10, 250)
        est = engine.estimate_seconds(text, wpm, self.v_skip.get())
        self.lbl_count.config(text=f"{len(text)} 字符 · 预计用时 {fmt_duration(est)}")

    def _on_text_modified(self, _e=None) -> None:
        if self.txt.edit_modified():
            self.txt.edit_modified(False)
            self._update_estimate()

    def _on_source(self) -> None:
        self.txt.configure(state="normal")
        self.txt.configure(foreground="#9ca3af" if self.v_source.get() == "clipboard" else "#111827")
        self._update_estimate()

    def _on_topmost(self) -> None:
        self.root.attributes("-topmost", self.v_top.get())

    def _paste_clipboard(self) -> None:
        try:
            data = self.root.clipboard_get()
        except tk.TclError:
            self._status("剪贴板里没有文本", "err")
            return
        self.txt.delete("1.0", "end")
        self.txt.insert("1.0", engine.normalize_text(data))

    def _open_file(self) -> None:
        path = filedialog.askopenfilename(
            title="选择文本文件",
            filetypes=[("文本文件", "*.txt *.md *.py *.js *.ts *.html *.css *.json *.csv"), ("所有文件", "*.*")])
        if not path:
            return
        for enc in ("utf-8-sig", "gbk", "utf-16"):
            try:
                with open(path, encoding=enc) as f:
                    data = f.read()
                break
            except (UnicodeError, OSError):
                continue
        else:
            messagebox.showerror(APP, "无法读取该文件（编码不支持或不是文本文件）")
            return
        self.txt.delete("1.0", "end")
        self.txt.insert("1.0", engine.normalize_text(data))

    def _status(self, text: str, tone: str = "idle") -> None:
        self.lbl_status.config(text=text, fg=COLORS[tone])

    def _hotkey_text(self) -> str:
        return winapi.format_hotkey(self.cfg["hotkey_mods"], self.cfg["hotkey_vk"])

    def _set_state(self, state: str) -> None:
        self.state = state
        delay = max(3.0, self._num(self.v_delay, 0.5, 0, 10))
        labels = {
            "idle": f"▶ 开始（{delay:g} 秒倒计时）",
            "countdown": "✕ 取消",
            "typing": "⏸ 暂停",
            "paused": "▶ 继续（3 秒倒计时）",
        }
        self.btn_main.config(text=labels[state])
        self.btn_stop.state(["disabled"] if state == "idle" else ["!disabled"])
        hk = self._hotkey_text()
        if state == "idle":
            self.lbl_how.config(text=f"点击任意文本框，按 {hk} 开始；再按暂停 / 继续；Esc 停止")

    # ================================================================ 快捷键
    def _apply_hotkey(self) -> None:
        self.lbl_hotkey.config(text=self._hotkey_text())
        self.hotkeys.set_hotkeys({HOTKEY_TOGGLE: (self.cfg["hotkey_mods"], self.cfg["hotkey_vk"])})
        if self.state == "idle":
            self._set_state("idle")

    def _record_hotkey(self) -> None:
        if self.state != "idle":
            return
        self.hotkeys.set_hotkeys({})  # 先释放，才能在对话框里按到当前组合

        dlg = tk.Toplevel(self.root)
        dlg.title("设置快捷键")
        dlg.transient(self.root)
        dlg.resizable(False, False)
        dlg.attributes("-topmost", True)
        frm = ttk.Frame(dlg, padding=24)
        frm.pack()
        ttk.Label(frm, text="请按下新的快捷键组合", style="Status.TLabel").pack()
        ttk.Label(frm, style="Hint.TLabel", justify="center",
                  text="需包含 Ctrl / Alt / Shift / Win 之一（F1–F24 可单独使用）\n按 Esc 取消").pack(pady=(4, 12))
        shown = ttk.Label(frm, text="…", style="Hotkey.TLabel")
        shown.pack()

        def close(_e=None):
            dlg.grab_release()
            dlg.destroy()
            self._apply_hotkey()

        def on_key(e):
            vk = e.keycode
            mods = winapi.current_modifiers()
            if vk in winapi.MODIFIER_VKS:
                shown.config(text=(winapi.format_hotkey(mods, 0) or "…") + " + …")
                return "break"
            if vk == winapi.VK_ESCAPE and not mods:
                close()
                return "break"
            if not mods and not 0x70 <= vk <= 0x87:
                shown.config(text=f"{winapi.vk_name(vk)}：需要加修饰键")
                return "break"
            self.cfg["hotkey_mods"], self.cfg["hotkey_vk"] = mods, vk
            save_config(self.cfg)
            close()
            return "break"

        dlg.bind("<KeyPress>", on_key)
        dlg.protocol("WM_DELETE_WINDOW", close)
        dlg.update_idletasks()
        x = self.root.winfo_rootx() + (self.root.winfo_width() - dlg.winfo_width()) // 2
        y = self.root.winfo_rooty() + (self.root.winfo_height() - dlg.winfo_height()) // 3
        dlg.geometry(f"+{x}+{y}")
        dlg.grab_set()
        dlg.focus_force()

    def _on_hotkey_result(self, results: dict) -> None:
        if not results:
            return
        self.hotkey_ok = results.get(HOTKEY_TOGGLE, False)
        self.lbl_hotkey.config(style="Hotkey.TLabel" if self.hotkey_ok else "HotkeyBad.TLabel")
        if not self.hotkey_ok:
            self._status(f"快捷键 {self._hotkey_text()} 已被其他程序占用，请点“更改…”换一个", "err")
        elif self.state == "idle":
            self._status("就绪", "idle")

    def _on_hotkey(self) -> None:
        if self.state == "idle":
            self._start(from_hotkey=True)
        elif self.state == "countdown":
            self._stop()
        elif self.state == "typing" and self.typist:
            self.typist.pause("user")
        elif self.state == "paused" and self.typist:
            self.typist.resume(0)

    # ================================================================ 控制
    def _on_main_button(self) -> None:
        if self.state == "idle":
            self._start(from_hotkey=False)
        elif self.state == "countdown":
            self._stop()
        elif self.state == "typing" and self.typist:
            self.typist.pause("user")
        elif self.state == "paused" and self.typist:
            self.typist.resume(3)
            self._status("3 秒后继续 —— 请点击回目标文本框", "busy")

    def _start(self, from_hotkey: bool) -> None:
        text = self._source_text()
        if not text:
            self._status("没有可输入的文本" + ("（剪贴板为空）" if self.v_source.get() == "clipboard" else ""), "err")
            return
        if from_hotkey and winapi.foreground_is_own_process():
            self._status("当前焦点在 TypeBot 自己身上 —— 请先点击要输入的文本框，再按快捷键", "err")
            return
        settings = self._settings()
        save_config(self.cfg)
        self.plan = engine.build_plan(text, settings)
        delay = self.cfg["start_delay"] if from_hotkey else max(3.0, self.cfg["start_delay"])
        self.token += 1
        token = self.token
        self.typist = engine.Typist(
            self.plan,
            winapi.WinKeyboard(self.cfg["newline_mode"]),
            emit=lambda *e: self.events.put(("typist", token) + e),
            start_delay=delay,
            focus_guard=self.cfg["focus_guard"],
            esc_stop=self.cfg["esc_stop"],
        )
        self.progress.config(maximum=max(1, len(self.plan.text)), value=0)
        self.lbl_detail.config(text=f"共 {len(self.plan.text)} 字符 · 预计 {fmt_duration(self.plan.est_seconds)}")
        self._set_state("countdown")
        if not from_hotkey:
            self._status(f"{delay:g} 秒后开始 —— 请点击目标文本框", "busy")
        self.typist.start()

    def _stop(self) -> None:
        if self.typist:
            self.typist.stop()

    def _on_typist(self, kind: str, *args) -> None:
        hk = self._hotkey_text()
        if kind == "countdown":
            if self.state == "countdown":
                self._status(f"{args[0]} 秒后开始…（再按 {hk} 取消）", "busy")
            else:
                self._status(f"{args[0]} 秒后继续… 请确保焦点在目标文本框", "busy")
        elif kind == "started":
            self._set_state("typing")
            self._status(f"正在输入…  按 {hk} 暂停" + ("，Esc 停止" if self.cfg["esc_stop"] else ""), "busy")
        elif kind == "resumed":
            self._set_state("typing")
            self._status(f"正在输入…  按 {hk} 暂停", "busy")
        elif kind == "paused":
            self._set_state("paused")
            msg = {
                "user": f"已暂停 —— 回到目标文本框后按 {hk} 继续",
                "focus": f"检测到切换了窗口，已自动暂停 —— 点回目标文本框后按 {hk} 继续",
                "self": f"焦点在 TypeBot 上，无法继续 —— 请先点击目标文本框，再按 {hk}",
            }[args[0]]
            self._status(msg, "paused")
        elif kind == "progress":
            src, elapsed = args
            plan = self.plan
            self.progress.config(value=src)
            units = plan.prefix_units[src]
            live = units / 5 / (elapsed / 60) if elapsed > 1 else 0
            remain = plan.est_seconds * (1 - units / plan.total_units) if plan.total_units else 0
            self.lbl_detail.config(
                text=f"{src} / {len(plan.text)} 字符 · 实际速度 {live:.0f} WPM · 剩余约 {fmt_duration(remain)}")
        elif kind == "done":
            status, msg = args
            self._set_state("idle")
            self.typist = None
            if status == "completed":
                self.progress.config(value=self.progress["maximum"])
                self._status("完成 ✓", "ok")
            elif status == "stopped":
                self._status("已停止" + ("（按下了 Esc）" if msg == "Esc" else ""), "idle")
            else:
                self._status(msg or "出错了", "err")

    # ================================================================ 速度预览
    def _open_preview(self) -> None:
        self._close_preview()
        win = tk.Toplevel(self.root)
        win.title("速度预览（不会向其他程序发送按键）")
        win.geometry("640x360")
        win.transient(self.root)
        frm = ttk.Frame(win, padding=12)
        frm.pack(fill="both", expand=True)
        info = ttk.Label(frm, style="Hint.TLabel")
        info.pack(anchor="w", pady=(0, 6))
        box = tk.Text(frm, wrap="word", font=("Microsoft YaHei UI", 12), relief="solid", borderwidth=1,
                      padx=8, pady=6, highlightthickness=0)
        box.pack(fill="both", expand=True)
        bar = ttk.Frame(frm)
        bar.pack(fill="x", pady=(8, 0))
        ttk.Button(bar, text="用当前设置重新开始", command=self._open_preview).pack(side="left")
        ttk.Button(bar, text="关闭", command=self._close_preview).pack(side="right")
        win.protocol("WM_DELETE_WINDOW", self._close_preview)

        text = self._source_text()[:1500] or SAMPLE_TEXT
        plan = engine.build_plan(text, self._settings(for_preview=True))
        self.token += 1
        token = self.token
        typist = engine.Typist(
            plan,
            engine.PreviewKeyboard(lambda step: self.events.put(("preview_step", token, step))),
            emit=lambda *e: self.events.put(("preview_evt", token) + e),
            focus_guard=False, esc_stop=False,
        )
        self.preview = {"win": win, "box": box, "info": info, "typist": typist, "token": token, "plan": plan}
        info.config(text=f"目标 {self.cfg['wpm']} WPM · 预计 {fmt_duration(plan.est_seconds)}")
        typist.start()

    def _close_preview(self) -> None:
        if self.preview:
            self.preview["typist"].stop()
            self.preview["win"].destroy()
            self.preview = None

    def _on_preview(self, kind: str, token: int, payload) -> None:
        p = self.preview
        if not p or p["token"] != token:
            return
        box = p["box"]
        if kind == "preview_step":
            if payload.kind == "backspace":
                box.delete("end-2c")
            else:
                box.insert("end", "\n" if payload.kind == "newline" else payload.ch)
            box.see("end")
        elif payload[0] == "progress":
            src, elapsed = payload[1:]
            plan = p["plan"]
            live = plan.prefix_units[src] / 5 / (elapsed / 60) if elapsed > 1 else 0
            p["info"].config(text=f"目标 {self.cfg['wpm']} WPM · 实际 {live:.0f} WPM · {src}/{len(plan.text)} 字符")
        elif payload[0] == "done" and payload[1] == "completed":
            p["info"].config(text=p["info"].cget("text") + " · 完成")

    # ================================================================ 事件循环
    def _poll(self) -> None:
        try:
            while True:
                ev = self.events.get_nowait()
                tag = ev[0]
                if tag == "hotkey":
                    self._on_hotkey()
                elif tag == "hk_result":
                    self._on_hotkey_result(ev[1])
                elif tag == "typist":
                    if ev[1] == self.token and self.typist is not None:
                        self._on_typist(*ev[2:])
                elif tag == "preview_step":
                    self._on_preview("preview_step", ev[1], ev[2])
                elif tag == "preview_evt":
                    self._on_preview("preview_evt", ev[1], ev[2:])
        except queue.Empty:
            pass
        self.root.after(15, self._poll)

    def on_close(self) -> None:
        self._collect()
        save_config(self.cfg)
        self._stop()
        self._close_preview()
        self.hotkeys.shutdown()
        self.root.destroy()


def _enable_dpi_awareness() -> None:
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(1)
    except (AttributeError, OSError):
        try:
            ctypes.windll.user32.SetProcessDPIAware()
        except (AttributeError, OSError):
            pass


def main() -> None:
    if sys.platform != "win32":
        print("TypeBot 目前只支持 Windows。")
        return
    _enable_dpi_awareness()
    root = tk.Tk()

    def report(exc, val, tb):
        messagebox.showerror(APP, "".join(traceback.format_exception(exc, val, tb))[-2000:])

    root.report_callback_exception = report
    App(root)
    root.mainloop()


if __name__ == "__main__":
    main()
