"""Register a Windows shortcut on a dedicated Win32 message thread."""
import ctypes as ct
import string
from queue import Queue
from ctypes import wintypes as wt
from threading import Thread

MODIFIERS = {"alt": 1, "ctrl": 2, "shift": 4, "win": 8}
KEYS = {key.lower(): ord(key) for key in string.ascii_uppercase + string.digits}
KEYS.update({f"f{i}": 0x6F + i for i in range(1, 25)})
KEYS.update(space=32, enter=13, tab=9, insert=45, home=36, end=35)
MOUSE = {"middle", "x1", "x2"}


def parse_shortcut(value):
    parts = [part.strip().lower() for part in value.split("+")]
    key = parts.pop()
    if key in MOUSE and not parts:
        return key, 0
    if key not in KEYS or not all(part in MODIFIERS for part in parts):
        raise ValueError("请输入 Ctrl+Shift+S、F8、middle、x1 或 x2 等快捷键")
    flags = 0
    for part in parts:
        flags |= MODIFIERS[part]
    return key, flags


def listen(binding, events):
    user = ct.WinDLL("user32", use_last_error=True)
    kernel = ct.WinDLL("kernel32", use_last_error=True)
    key, modifiers = binding
    hook = None
    registered = False
    try:
        if key in MOUSE:
            # MSLLHOOKSTRUCT begins with POINT followed by DWORD mouseData.
            class MouseData(ct.Structure):
                _fields_ = [("point", wt.POINT), ("data", wt.DWORD)]

            proc = ct.WINFUNCTYPE(ct.c_ssize_t, ct.c_int, wt.WPARAM, wt.LPARAM)
            user.CallNextHookEx.argtypes = [wt.HANDLE, ct.c_int, wt.WPARAM, wt.LPARAM]
            user.CallNextHookEx.restype = ct.c_ssize_t

            @proc
            def callback(code, message, address):
                if code == 0 and message in (0x207, 0x208, 0x20B, 0x20C):
                    button = "middle"
                    if message in (0x20B, 0x20C):
                        button = {1: "x1", 2: "x2"}.get(MouseData.from_address(address).data >> 16)
                    if button == key:
                        if message in (0x207, 0x20B):
                            events.put(("trigger", None))
                        return 1
                return user.CallNextHookEx(None, code, message, address)

            kernel.GetModuleHandleW.restype = wt.HMODULE
            user.SetWindowsHookExW.argtypes = [ct.c_int, proc, wt.HMODULE, wt.DWORD]
            user.SetWindowsHookExW.restype = wt.HANDLE
            hook = user.SetWindowsHookExW(14, callback, kernel.GetModuleHandleW(None), 0)
            if not hook:
                raise ct.WinError(ct.get_last_error())
        else:
            registered = user.RegisterHotKey(None, 1, modifiers | 0x4000, KEYS[key])
            if not registered:
                raise OSError(f"{ct.WinError(ct.get_last_error())}\n请在快捷键设置中选择其他组合。")
        msg = wt.MSG()
        user.PeekMessageW(ct.byref(msg), None, 0, 0, 0)
        events.put(("ready", None))
        while True:
            result = user.GetMessageW(ct.byref(msg), None, 0, 0)
            if result == -1:
                raise ct.WinError(ct.get_last_error())
            if result == 0:
                break
            if msg.message == 0x0312:
                events.put(("trigger", None))
    except OSError as exc:
        events.put(("shortcut_error", str(exc)))
    finally:
        if hook:
            user.UnhookWindowsHookEx(wt.HANDLE(hook))
        if registered:
            user.UnregisterHotKey(None, 1)


class Listener:
    def __init__(self, binding):
        self.events = Queue()
        self.thread = Thread(target=listen, args=(binding, self.events), daemon=True)
        self.thread.start()
        kind, error = self.events.get()
        if kind != "ready":
            raise OSError(error)

    def stop(self):
        if self.thread.is_alive():
            ct.windll.user32.PostThreadMessageW(self.thread.native_id, 0x0012, 0, 0)
            self.thread.join(timeout=1)
