"""Set both native Windows icon slots after Qt creates the top-level HWND."""
import ctypes
import sys
from ctypes import wintypes
from pathlib import Path

_handles = []


def apply_taskbar_icon(window, icon_path: Path) -> None:
    if sys.platform != "win32":
        return
    user = ctypes.WinDLL("user32", use_last_error=True)
    user.LoadImageW.argtypes = [wintypes.HINSTANCE, wintypes.LPCWSTR, wintypes.UINT, ctypes.c_int, ctypes.c_int, wintypes.UINT]
    user.LoadImageW.restype = wintypes.HANDLE
    user.SendMessageW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
    user.SendMessageW.restype = wintypes.LPARAM
    for slot, size in ((0, 16), (1, 32)):
        handle = user.LoadImageW(None, str(icon_path), 1, size, size, 0x10)
        if handle:
            _handles.append(handle)  # Window lifetime; handles remain valid until process exit.
            user.SendMessageW(int(window.winId()), 0x80, slot, handle)
