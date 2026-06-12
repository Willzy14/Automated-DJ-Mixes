"""Minimal Ableton Live UI driver — screenshot-stepper pattern.

Live's UI is custom-drawn (no native child controls), so automation is
screenshot -> (agent reads the PNG) -> click/key -> screenshot. This CLI
exposes one primitive per invocation; the agent supplies coordinates read
from the screenshots. Native dialogs (file save) accept typed text.

Used for: post-build visual verification of transitions (zoom + eyeball)
and driving File > Export Audio/Video for the mix bounce.

Usage:
    python Source/ableton_ui.py status                 # window titles + state
    python Source/ableton_ui.py launch "<set.als>"     # open a set in Live
    python Source/ableton_ui.py kill                   # kill Live (discards unsaved!)
    python Source/ableton_ui.py shot "<out.png>"       # full-screen screenshot
    python Source/ableton_ui.py click <x> <y>          # left click (screen px)
    python Source/ableton_ui.py dclick <x> <y>
    python Source/ableton_ui.py drag <x0> <y0> <x1> <y1>
    python Source/ableton_ui.py key <combo>            # e.g. ctrl+shift+r, z, enter
    python Source/ableton_ui.py type "<text>"
    python Source/ableton_ui.py focus                  # bring Live to foreground
"""
from __future__ import annotations

import subprocess
import sys
import time
from pathlib import Path

import ctypes

# Per-monitor DPI awareness: without this, click coordinates are virtualized
# on scaled monitors and miss — screenshot pixels and click coords must share
# one space (physical pixels).
try:
    ctypes.windll.shcore.SetProcessDpiAwareness(2)
except Exception:
    pass

import pyautogui

pyautogui.FAILSAFE = False


def _img2scr(x: int, y: int) -> tuple[int, int]:
    """Translate screenshot-image coords to screen coords. ImageGrab's
    all_screens image starts at the VIRTUAL DESKTOP origin, which is
    (-36, 0) on this rig (a display extends left of the primary) — clicks
    without this translation land 36px off target."""
    u = ctypes.windll.user32
    return x + u.GetSystemMetrics(76), y + u.GetSystemMetrics(77)

LIVE_EXE = Path(r"C:\ProgramData\Ableton\Live 12 Suite\Program\Ableton Live 12 Suite.exe")


def _live_windows():
    import pywinauto
    out = []
    for w in pywinauto.Desktop(backend="win32").windows():
        try:
            t = w.window_text()
            if "Ableton Live" in t or t.endswith(".als"):
                out.append((t, w))
        except Exception:
            continue
    return out


def status() -> None:
    import psutil
    procs = [p.info for p in psutil.process_iter(["name", "pid"])
             if p.info["name"] and "Ableton" in p.info["name"]]
    print("processes:", procs or "none")
    for t, w in _live_windows():
        try:
            r = w.rectangle()
            print(f"window: {t!r} rect=({r.left},{r.top},{r.right},{r.bottom})"
                  f" visible={w.is_visible()}")
        except Exception:
            print(f"window: {t!r}")


def focus() -> None:
    import win32api
    import win32con
    import win32gui

    def cb(hwnd, acc):
        if win32gui.IsWindowVisible(hwnd) and "Ableton Live" in win32gui.GetWindowText(hwnd):
            acc.append(hwnd)
    acc: list[int] = []
    win32gui.EnumWindows(cb, acc)
    if not acc:
        print("no Live window")
        return
    # Alt-tap unlocks SetForegroundWindow from a background process
    win32api.keybd_event(win32con.VK_MENU, 0, 0, 0)
    try:
        win32gui.SetForegroundWindow(acc[0])
    finally:
        win32api.keybd_event(win32con.VK_MENU, 0, win32con.KEYEVENTF_KEYUP, 0)
    time.sleep(0.4)
    print("focused:", win32gui.GetWindowText(acc[0]))


def main() -> int:
    cmd = sys.argv[1]
    if cmd == "status":
        status()
    elif cmd == "launch":
        als = Path(sys.argv[2]).resolve()
        exe = LIVE_EXE if LIVE_EXE.exists() else None
        if exe is None:
            # resolve any installed Live 12 exe
            hits = sorted(Path(r"C:\ProgramData\Ableton").glob("**/Ableton Live*.exe"))
            if not hits:
                print("Live exe not found under C:\\ProgramData\\Ableton")
                return 1
            exe = hits[-1]
        subprocess.Popen([str(exe), str(als)])
        print(f"launched {exe.name} with {als.name}")
    elif cmd == "kill":
        subprocess.run(["taskkill", "/IM", "Ableton Live 12 Suite.exe", "/F"],
                       capture_output=True)
        print("killed")
    elif cmd == "shot":
        # all_screens: Live may sit on a secondary monitor (virtual-screen
        # coords match click coords as long as no monitor is left/above the
        # primary). Crop with x0 y0 x1 y1 args if given.
        from PIL import ImageGrab
        out = Path(sys.argv[2])
        out.parent.mkdir(parents=True, exist_ok=True)
        img = ImageGrab.grab(all_screens=True)
        if len(sys.argv) >= 7:
            x0, y0, x1, y1 = map(int, sys.argv[3:7])
            img = img.crop((x0, y0, x1, y1))
        img.save(str(out))
        print(f"saved {out} size={img.size}")
    elif cmd == "click":
        pyautogui.click(*_img2scr(int(sys.argv[2]), int(sys.argv[3])))
        print("clicked")
    elif cmd == "dclick":
        pyautogui.doubleClick(*_img2scr(int(sys.argv[2]), int(sys.argv[3])))
        print("double-clicked")
    elif cmd == "drag":
        x0, y0, x1, y1 = map(int, sys.argv[2:6])
        pyautogui.moveTo(*_img2scr(x0, y0))
        pyautogui.dragTo(*_img2scr(x1, y1), duration=0.6, button="left")
        print("dragged")
    elif cmd == "key":
        keys = sys.argv[2].split("+")
        pyautogui.hotkey(*keys) if len(keys) > 1 else pyautogui.press(keys[0])
        print(f"pressed {sys.argv[2]}")
    elif cmd == "type":
        pyautogui.typewrite(sys.argv[2], interval=0.02)
        print("typed")
    elif cmd == "scroll":
        x, y = _img2scr(int(sys.argv[2]), int(sys.argv[3]))
        pyautogui.moveTo(x, y)
        pyautogui.scroll(int(sys.argv[4]))  # +up / -down
        print("scrolled")
    elif cmd == "focus":
        focus()
    else:
        print(__doc__)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
