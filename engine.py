"""打字节奏引擎。

build_plan()  把文本变成一串带延迟的按键步骤（含随机节奏、停顿、错字与改正），
              并把整体时长缩放到与目标 WPM 精确一致。
Typist        后台线程，按计划把步骤发给键盘适配器，支持暂停 / 继续 / 停止。

本模块不依赖 Windows，可单独测试。
"""
from __future__ import annotations

import math
import random
import threading
import time
from dataclasses import dataclass

WS = " \t\n"
SENTENCE_END = ".!?。！？…"
CLAUSE_END = ",;:，；：、"

# 英文常见双字母组合：熟练打字者打这些组合更快
BIGRAMS = set(
    "th he in er an re on at en nd ti es or te of ed is it al ar st to nt ng se ha as ou io "
    "le ve co me de hi ri ro ic ne ea ra ce li ch ll be ma si om ur".split()
)

_ROWS = ["1234567890", "qwertyuiop", "asdfghjkl", "zxcvbnm"]


def _build_neighbors() -> dict[str, list[str]]:
    pos = {c: (r, i) for r, row in enumerate(_ROWS) for i, c in enumerate(row)}
    result = {}
    for c, (r, i) in pos.items():
        if not c.isalpha():
            continue
        near = []
        for dr in (-1, 0, 1):
            rr = r + dr
            if not 1 <= rr < len(_ROWS):  # 错字只落在字母行
                continue
            for di in (-1, 0, 1):
                ii = i + di
                if (dr or di) and 0 <= ii < len(_ROWS[rr]):
                    near.append(_ROWS[rr][ii])
        result[c] = near
    return result


NEIGHBORS = _build_neighbors()


def char_weight(ch: str) -> float:
    """一个字符大约相当于多少次“标准键击”。WPM 按 5 次键击 = 1 个词计算。"""
    o = ord(ch)
    if 0x3000 <= o <= 0x303F or 0xFF00 <= o <= 0xFF65:  # 中文标点、全角符号
        return 1.0
    if 0x3040 <= o <= 0x30FF:  # 日文假名
        return 2.0
    if 0xAC00 <= o <= 0xD7AF:  # 韩文音节
        return 2.5
    if 0x3400 <= o <= 0x9FFF or 0xF900 <= o <= 0xFAFF or 0x20000 <= o <= 0x3FFFF:  # 汉字
        return 3.0
    return 1.0


def normalize_text(text: str) -> str:
    return text.replace("\r\n", "\n").replace("\r", "\n")


def _char_factor(prev: str | None, ch: str) -> float:
    """相对耗时：首字母、大写、符号更慢；常见组合、重复字母更快。"""
    f = char_weight(ch)
    if ch == "\n":
        return f * 1.2
    if ch == " ":
        f *= 0.85
    elif prev is None or prev in WS:
        f *= 1.3
    elif (prev + ch).lower() in BIGRAMS:
        f *= 0.75
    elif prev == ch:
        f *= 0.85
    if ch.isascii():
        if ch.isupper():
            f *= 1.35
        elif ch.isdigit():
            f *= 1.2
        elif not ch.isalnum() and ch not in WS:
            f *= 1.5
    return f


def _pause_after(prev: str) -> float:
    if prev == "\n":
        return 3.5
    if prev in SENTENCE_END:
        return 3.0
    if prev in CLAUSE_END:
        return 1.2
    return 0.0


@dataclass
class TypingSettings:
    wpm: float = 60.0
    variability: float = 0.35       # 0–1，节奏随机程度
    typo_rate: float = 0.01         # 每个英文字母打错（随后改正）的概率
    thinking_rate: float = 0.03     # 每个单词前出现“思考停顿”的概率
    punctuation_pause: bool = True  # 标点、换行后自然停顿
    skip_leading_ws: bool = False   # 跳过行首空格/Tab（编辑器会自动缩进时使用）
    seed: int | None = None


@dataclass
class Step:
    kind: str     # "char" | "newline" | "backspace"
    ch: str
    delay: float  # 执行这一步之前等待的秒数
    src: int      # 执行完这一步后，原文已完成到第几个字符


@dataclass
class Plan:
    text: str
    steps: list[Step]
    prefix_units: list[float]  # prefix_units[i]：原文前 i 个字符折合的键击数
    total_units: float
    est_seconds: float


def estimate_seconds(text: str, wpm: float, skip_leading_ws: bool = False) -> float:
    text = normalize_text(text)
    units = 0.0
    line_start = True
    for ch in text:
        if skip_leading_ws and line_start and ch in " \t":
            continue
        units += char_weight(ch)
        line_start = ch == "\n"
    return units * 60.0 / (max(1.0, wpm) * 5)


def build_plan(text: str, s: TypingSettings) -> Plan:
    text = normalize_text(text)
    rng = random.Random(s.seed)
    sigma = max(0.0, min(1.0, s.variability)) * 0.6
    norm = math.exp(sigma * sigma / 2)  # 让对数正态抖动的均值为 1

    def jitter() -> float:
        return rng.lognormvariate(0.0, sigma) / norm if sigma > 0 else 1.0

    raw: list[tuple[str, str, float, int]] = []
    n = len(text)
    prefix = [0.0] * (n + 1)
    units = 0.0
    prev: str | None = None
    line_start = True
    i = 0
    while i < n:
        ch = text[i]
        if s.skip_leading_ws and line_start and ch in " \t":
            i += 1
            prefix[i] = units
            continue

        d = _char_factor(prev, ch) * jitter()
        if s.punctuation_pause and prev is not None:
            d += _pause_after(prev) * (0.6 + 0.8 * rng.random())
        word_start = ch not in WS and (prev is None or prev in WS)
        if word_start and prev is not None and s.thinking_rate > 0 and rng.random() < s.thinking_rate:
            d += rng.uniform(5.0, 18.0)

        if s.typo_rate > 0 and ch.isascii() and ch.isalpha() and rng.random() < s.typo_rate:
            wrong = rng.choice(NEIGHBORS[ch.lower()])
            if ch.isupper():
                wrong = wrong.upper()
            raw.append(("char", wrong, d, i))
            # 有时会多打一两个字母才发现错误
            extra = 0
            want = rng.choice((0, 0, 1, 1, 2))
            j = i + 1
            while extra < want and j < n and text[j].isascii() and text[j].isalpha():
                raw.append(("char", text[j], _char_factor(text[j - 1], text[j]) * jitter(), i))
                extra += 1
                j += 1
            for b in range(extra + 1):
                raw.append(("backspace", "", (rng.uniform(2.0, 4.5) if b == 0 else 0.7) * jitter(), i))
            d = 1.2 * _char_factor(prev, ch) * jitter()  # 改正时稍微犹豫一下

        raw.append(("newline" if ch == "\n" else "char", ch, d, i + 1))
        units += char_weight(ch)
        prefix[i + 1] = units
        prev = ch
        line_start = ch == "\n"
        i += 1

    # 把总时长缩放到目标 WPM（包含停顿和改错），保证平均速度准确
    target = units * 60.0 / (max(1.0, s.wpm) * 5)
    total_raw = sum(r[2] for r in raw)
    scale = target / total_raw if total_raw > 0 else 0.0
    steps = [Step(k, c, d * scale, src) for k, c, d, src in raw]
    return Plan(text, steps, prefix, units, target)


class PreviewKeyboard:
    """不发送真实按键，只把步骤回调给界面，用于“速度预览”。"""

    def __init__(self, callback):
        self._cb = callback

    def send(self, step: Step) -> None:
        self._cb(step)

    def modifiers_down(self) -> bool:
        return False

    def esc_down(self) -> bool:
        return False

    def foreground(self) -> int:
        return 0

    def self_focused(self) -> bool:
        return False


class Typist(threading.Thread):
    """按计划执行按键。所有状态通过 emit(事件名, *参数) 通知外部（在本线程中调用）。

    事件：countdown(n) / started() / progress(src, 有效打字秒数) / paused(原因) /
         resumed() / done(状态, 说明)，状态为 completed | stopped | aborted | error。
    """

    def __init__(self, plan: Plan, kb, emit, start_delay: float = 0.0,
                 focus_guard: bool = True, esc_stop: bool = True):
        super().__init__(daemon=True, name="Typist")
        self.plan = plan
        self.kb = kb
        self.emit = emit
        self.start_delay = start_delay
        self.focus_guard = focus_guard
        self.esc_stop = esc_stop
        self._stop_evt = threading.Event()
        self._pause_evt = threading.Event()
        self._pause_reason = "user"
        self._resume_delay = 0.0
        self._target = 0

    # ---- 供界面线程调用
    def pause(self, reason: str = "user") -> None:
        self._pause_reason = reason
        self._pause_evt.set()

    def resume(self, delay: float = 0.0) -> None:
        self._resume_delay = delay
        self._pause_evt.clear()

    def stop(self) -> None:
        self._stop_evt.set()

    # ---- 线程内部
    def run(self) -> None:
        try:
            self._run()
        except Exception as e:  # 让界面能显示错误，而不是静默崩溃
            self.emit("done", "error", str(e))

    def _wait_release(self, timeout: float = 3.0) -> None:
        """等用户松开快捷键里的 Ctrl/Alt/Shift，否则打出来的字会变成组合键。"""
        end = time.perf_counter() + timeout
        while self.kb.modifiers_down() and time.perf_counter() < end and not self._stop_evt.is_set():
            time.sleep(0.01)
        time.sleep(0.03)

    def _countdown(self, seconds: float) -> bool:
        end = time.perf_counter() + seconds
        shown = None
        while True:
            if self._stop_evt.is_set():
                return False
            rem = end - time.perf_counter()
            if rem <= 0:
                return True
            n = math.ceil(rem)
            if n != shown:
                self.emit("countdown", n)
                shown = n
            time.sleep(min(rem, 0.05))

    def _run(self) -> None:
        self._wait_release()
        if not self._countdown(self.start_delay):
            self.emit("done", "stopped", "")
            return
        if self.kb.self_focused():
            self.emit("done", "aborted", "倒计时结束时焦点仍在 TypeBot 窗口上，请先点击目标文本框")
            return
        self._target = self.kb.foreground()
        self.emit("started")

        steps = self.plan.steps
        active = 0.0
        seg_start = t_next = time.perf_counter()
        last_emit = 0.0
        for idx, step in enumerate(steps):
            t_next += step.delay
            while True:
                if self._stop_evt.is_set():
                    self.emit("done", "stopped", "")
                    return
                if self.esc_stop and self.kb.esc_down():
                    self._stop_evt.set()
                    self.emit("done", "stopped", "Esc")
                    return
                if self.focus_guard and not self._pause_evt.is_set() and self.kb.foreground() != self._target:
                    self.pause("focus")
                if self._pause_evt.is_set():
                    active += time.perf_counter() - seg_start
                    self.emit("paused", self._pause_reason)
                    while self._pause_evt.is_set() and not self._stop_evt.is_set():
                        time.sleep(0.03)
                    seg_start = time.perf_counter()
                    if self._stop_evt.is_set():
                        continue
                    self._wait_release()
                    if self._resume_delay > 0 and not self._countdown(self._resume_delay):
                        continue
                    if self.kb.self_focused():
                        seg_start = time.perf_counter()
                        self.pause("self")
                        continue
                    self._target = self.kb.foreground()
                    seg_start = time.perf_counter()
                    t_next = seg_start + min(step.delay, 0.25)
                    self.emit("resumed")
                    continue
                rem = t_next - time.perf_counter()
                if rem <= 0:
                    break
                time.sleep(min(rem, 0.02))

            self.kb.send(step)
            now = time.perf_counter()
            if now - last_emit >= 0.05 or idx == len(steps) - 1:
                self.emit("progress", step.src, active + now - seg_start)
                last_emit = now
        self.emit("done", "completed", "")
