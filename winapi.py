"""Windows 底层接口：模拟按键（SendInput）、全局快捷键（RegisterHotKey）、焦点与按键状态查询。

只依赖 ctypes，无需安装第三方库。
"""
from __future__ import annotations

import ctypes
import os
import threading
from ctypes import wintypes

user32 = ctypes.WinDLL("user32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

# ---------------------------------------------------------------- 常量
INPUT_KEYBOARD = 1
KEYEVENTF_KEYUP = 0x0002
KEYEVENTF_UNICODE = 0x0004

VK_BACK = 0x08
VK_TAB = 0x09
VK_RETURN = 0x0D
VK_SHIFT = 0x10
VK_CONTROL = 0x11
VK_MENU = 0x12
VK_ESCAPE = 0x1B
VK_LWIN = 0x5B
VK_RWIN = 0x5C
VK_OEM_5 = 0xDC  # 美式键盘上的 \ 键

MOD_ALT = 0x0001
MOD_CONTROL = 0x0002
MOD_SHIFT = 0x0004
MOD_WIN = 0x0008
MOD_NOREPEAT = 0x4000

# 按下后不算“主键”的修饰键（含左右区分的版本）
MODIFIER_VKS = {VK_SHIFT, VK_CONTROL, VK_MENU, VK_LWIN, VK_RWIN, 0xA0, 0xA1, 0xA2, 0xA3, 0xA4, 0xA5}

WM_QUIT = 0x0012
WM_HOTKEY = 0x0312
WM_APP_REREGISTER = 0x8000 + 1

ULONG_PTR = ctypes.c_size_t


# ---------------------------------------------------------------- SendInput 结构体
class MOUSEINPUT(ctypes.Structure):
    _fields_ = [
        ("dx", wintypes.LONG),
        ("dy", wintypes.LONG),
        ("mouseData", wintypes.DWORD),
        ("dwFlags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ULONG_PTR),
    ]


class KEYBDINPUT(ctypes.Structure):
    _fields_ = [
        ("wVk", wintypes.WORD),
        ("wScan", wintypes.WORD),
        ("dwFlags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ULONG_PTR),
    ]


class HARDWAREINPUT(ctypes.Structure):
    _fields_ = [
        ("uMsg", wintypes.DWORD),
        ("wParamL", wintypes.WORD),
        ("wParamH", wintypes.WORD),
    ]


class _INPUTUNION(ctypes.Union):
    _fields_ = [("mi", MOUSEINPUT), ("ki", KEYBDINPUT), ("hi", HARDWAREINPUT)]


class INPUT(ctypes.Structure):
    _anonymous_ = ("u",)
    _fields_ = [("type", wintypes.DWORD), ("u", _INPUTUNION)]


user32.SendInput.argtypes = (wintypes.UINT, ctypes.POINTER(INPUT), ctypes.c_int)
user32.SendInput.restype = wintypes.UINT
user32.GetAsyncKeyState.argtypes = (ctypes.c_int,)
user32.GetAsyncKeyState.restype = ctypes.c_short
user32.GetForegroundWindow.restype = wintypes.HWND
user32.GetWindowThreadProcessId.argtypes = (wintypes.HWND, ctypes.POINTER(wintypes.DWORD))
user32.GetWindowThreadProcessId.restype = wintypes.DWORD
user32.RegisterHotKey.argtypes = (wintypes.HWND, ctypes.c_int, wintypes.UINT, wintypes.UINT)
user32.RegisterHotKey.restype = wintypes.BOOL
user32.UnregisterHotKey.argtypes = (wintypes.HWND, ctypes.c_int)
user32.UnregisterHotKey.restype = wintypes.BOOL
user32.GetMessageW.argtypes = (ctypes.POINTER(wintypes.MSG), wintypes.HWND, wintypes.UINT, wintypes.UINT)
user32.GetMessageW.restype = wintypes.BOOL
user32.PeekMessageW.argtypes = (ctypes.POINTER(wintypes.MSG), wintypes.HWND, wintypes.UINT, wintypes.UINT, wintypes.UINT)
user32.PeekMessageW.restype = wintypes.BOOL
user32.PostThreadMessageW.argtypes = (wintypes.DWORD, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM)
user32.PostThreadMessageW.restype = wintypes.BOOL
user32.MapVirtualKeyW.argtypes = (wintypes.UINT, wintypes.UINT)
user32.MapVirtualKeyW.restype = wintypes.UINT
user32.GetKeyNameTextW.argtypes = (wintypes.LONG, wintypes.LPWSTR, ctypes.c_int)
user32.GetKeyNameTextW.restype = ctypes.c_int
kernel32.GetCurrentThreadId.restype = wintypes.DWORD


# ---------------------------------------------------------------- 发送按键
def _key_input(vk: int = 0, scan: int = 0, flags: int = 0) -> INPUT:
    inp = INPUT(type=INPUT_KEYBOARD)
    inp.ki = KEYBDINPUT(wVk=vk, wScan=scan, dwFlags=flags, time=0, dwExtraInfo=0)
    return inp


def _send(inputs: list[INPUT]) -> None:
    n = len(inputs)
    arr = (INPUT * n)(*inputs)
    sent = user32.SendInput(n, arr, ctypes.sizeof(INPUT))
    if sent != n:
        raise OSError(f"SendInput 失败（错误码 {ctypes.get_last_error()}）")


def tap_vk(vk: int, modifiers: tuple[int, ...] = ()) -> None:
    """按下并松开一个虚拟键，可带修饰键（例如 Shift+Enter）。"""
    seq = [_key_input(vk=m) for m in modifiers]
    seq += [_key_input(vk=vk), _key_input(vk=vk, flags=KEYEVENTF_KEYUP)]
    seq += [_key_input(vk=m, flags=KEYEVENTF_KEYUP) for m in reversed(modifiers)]
    _send(seq)


def type_unicode(ch: str) -> None:
    """以 Unicode 方式输入一个字符；与键盘布局、大小写锁定无关，支持中文和 emoji。"""
    data = ch.encode("utf-16-le")
    seq = []
    for k in range(0, len(data), 2):
        unit = int.from_bytes(data[k:k + 2], "little")
        seq.append(_key_input(scan=unit, flags=KEYEVENTF_UNICODE))
        seq.append(_key_input(scan=unit, flags=KEYEVENTF_UNICODE | KEYEVENTF_KEYUP))
    _send(seq)


# ---------------------------------------------------------------- 状态查询
def is_down(vk: int) -> bool:
    return bool(user32.GetAsyncKeyState(vk) & 0x8000)


def modifiers_down() -> bool:
    return any(is_down(vk) for vk in (VK_SHIFT, VK_CONTROL, VK_MENU, VK_LWIN, VK_RWIN))


def current_modifiers() -> int:
    mods = 0
    if is_down(VK_CONTROL):
        mods |= MOD_CONTROL
    if is_down(VK_MENU):
        mods |= MOD_ALT
    if is_down(VK_SHIFT):
        mods |= MOD_SHIFT
    if is_down(VK_LWIN) or is_down(VK_RWIN):
        mods |= MOD_WIN
    return mods


def foreground_window() -> int:
    return user32.GetForegroundWindow() or 0


def foreground_is_own_process() -> bool:
    hwnd = user32.GetForegroundWindow()
    if not hwnd:
        return False
    pid = wintypes.DWORD()
    user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    return pid.value == os.getpid()


# ---------------------------------------------------------------- 快捷键显示名
_VK_NAMES = {
    0x08: "Backspace", 0x09: "Tab", 0x0D: "Enter", 0x13: "Pause", 0x14: "CapsLock",
    0x1B: "Esc", 0x20: "Space", 0x21: "PgUp", 0x22: "PgDn", 0x23: "End", 0x24: "Home",
    0x25: "←", 0x26: "↑", 0x27: "→", 0x28: "↓", 0x2C: "PrtSc", 0x2D: "Insert", 0x2E: "Delete",
    0x6A: "Num *", 0x6B: "Num +", 0x6D: "Num -", 0x6E: "Num .", 0x6F: "Num /",
    0x90: "NumLock", 0x91: "ScrollLock",
}


def vk_name(vk: int) -> str:
    if vk in _VK_NAMES:
        return _VK_NAMES[vk]
    if 0x70 <= vk <= 0x87:
        return f"F{vk - 0x6F}"
    if 0x60 <= vk <= 0x69:
        return f"Num {vk - 0x60}"
    if 0x30 <= vk <= 0x39 or 0x41 <= vk <= 0x5A:
        return chr(vk)
    scan = user32.MapVirtualKeyW(vk, 0)
    if scan:
        buf = ctypes.create_unicode_buffer(32)
        if user32.GetKeyNameTextW(scan << 16, buf, 32) > 0:
            return buf.value
    return f"VK 0x{vk:02X}"


def format_hotkey(mods: int, vk: int) -> str:
    parts = []
    if mods & MOD_CONTROL:
        parts.append("Ctrl")
    if mods & MOD_ALT:
        parts.append("Alt")
    if mods & MOD_SHIFT:
        parts.append("Shift")
    if mods & MOD_WIN:
        parts.append("Win")
    if vk:
        parts.append(vk_name(vk))
    return " + ".join(parts)


# ---------------------------------------------------------------- 键盘适配器（供打字引擎使用）
class WinKeyboard:
    """把引擎的按键计划真正发送到当前焦点窗口。"""

    def __init__(self, newline_mode: str = "enter"):
        self.newline_mode = newline_mode

    def send(self, step) -> None:
        if step.kind == "backspace":
            tap_vk(VK_BACK)
        elif step.kind == "newline":
            tap_vk(VK_RETURN, (VK_SHIFT,) if self.newline_mode == "shift_enter" else ())
        elif step.ch == "\t":
            tap_vk(VK_TAB)
        else:
            type_unicode(step.ch)

    modifiers_down = staticmethod(modifiers_down)
    foreground = staticmethod(foreground_window)
    self_focused = staticmethod(foreground_is_own_process)

    @staticmethod
    def esc_down() -> bool:
        return is_down(VK_ESCAPE)


# ---------------------------------------------------------------- 全局快捷键
class HotkeyListener(threading.Thread):
    """在独立线程里运行消息循环，接收 WM_HOTKEY。

    on_hotkey(id) 与 on_result({id: 是否注册成功}) 都在本线程中调用，调用方需自行转到 UI 线程。
    """

    def __init__(self, on_hotkey, on_result):
        super().__init__(daemon=True, name="HotkeyListener")
        self._on_hotkey = on_hotkey
        self._on_result = on_result
        self._lock = threading.Lock()
        self._wanted: dict[int, tuple[int, int]] = {}
        self._active: list[int] = []
        self._ready = threading.Event()
        self._thread_id = 0

    def set_hotkeys(self, mapping: dict[int, tuple[int, int]]) -> None:
        """mapping: {id: (修饰键, 虚拟键码)}；传空字典即全部注销。"""
        with self._lock:
            self._wanted = dict(mapping)
        self._ready.wait(2)
        user32.PostThreadMessageW(self._thread_id, WM_APP_REREGISTER, 0, 0)

    def shutdown(self) -> None:
        if self._thread_id:
            user32.PostThreadMessageW(self._thread_id, WM_QUIT, 0, 0)

    def run(self) -> None:
        self._thread_id = kernel32.GetCurrentThreadId()
        msg = wintypes.MSG()
        user32.PeekMessageW(ctypes.byref(msg), None, 0, 0, 0)  # 确保线程消息队列已创建
        self._ready.set()
        while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
            if msg.message == WM_HOTKEY:
                self._on_hotkey(int(msg.wParam))
            elif msg.message == WM_APP_REREGISTER:
                self._register()
        self._unregister_all()

    def _unregister_all(self) -> None:
        for hid in self._active:
            user32.UnregisterHotKey(None, hid)
        self._active = []

    def _register(self) -> None:
        self._unregister_all()
        with self._lock:
            wanted = dict(self._wanted)
        results = {}
        for hid, (mods, vk) in wanted.items():
            ok = bool(user32.RegisterHotKey(None, hid, mods | MOD_NOREPEAT, vk))
            results[hid] = ok
            if ok:
                self._active.append(hid)
        self._on_result(results)
