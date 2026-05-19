"""Drive Mixed In Key 11 and Rekordbox 7 via desktop UI automation.

The pipeline needs MIK auto-cues / energy / key (in MIKStore.db SQLite) and
Rekordbox beat grids / phrase analysis (in ANLZ files in RB's data folder).
Both apps are GUI-only — no CLI, no watch folders.

Design principle: **do NOT take over the user's mouse**. The user is usually
working in Ableton or elsewhere in parallel. So every UI interaction goes
through Windows messages (`pywinauto.click()` → BM_CLICK; `set_edit_text()`
→ WM_SETTEXT; UIA `.invoke()` → InvokePattern) which target a specific HWND
without moving the cursor or stealing focus.

Mouse-click fallback (`pyautogui.click`) is used ONLY where a non-standard
custom control refuses to respond to messages — currently just MIK's blue
"Add tracks" button (WPF custom control, image-template-matched).

Public entry points:
    analyze_folder_with_mik(folder, expected_tracks=None)
    analyze_folder_with_rekordbox(folder, expected_tracks=None)
    ensure_all_analyzed(audio_paths)
"""

from __future__ import annotations

import re
import sqlite3
import subprocess
import time
from pathlib import Path

import pyautogui  # mouse fallback only
from mutagen.id3 import ID3, ID3NoHeaderError
from pywinauto import Desktop
from pywinauto.application import Application

# ---------- Paths ----------

MIK_SHORTCUT = Path("C:/Users/Carillon/Desktop/Mixed In Key 11.lnk")
MIK_EXE = Path(
    "C:/Users/Carillon/AppData/Local/Programs/Mixed In Key/Mixed in Key 11/MixedInKey.exe"
)
MIK_USER_CONFIG = Path(
    "C:/Users/Carillon/AppData/Local/Mixed_In_Key_LLC/"
    "MixedInKey.exe_Url_cx00oimrmmmuepp4wmlrv3xpklukfe3q/11.1.0.846/user.config"
)
MIK_STORE_DB = Path(
    "C:/Users/Carillon/AppData/Local/Mixed In Key/Mixed In Key/11.0/MIKStore.db"
)

RB_EXE = Path("C:/Program Files/rekordbox/rekordbox.exe")

TEMPLATES_DIR = Path(__file__).parent / "templates"
MIK_ADD_TRACKS_TEMPLATE = TEMPLATES_DIR / "mik_add_tracks_button.png"

# ---------- Timeouts ----------

WINDOW_TIMEOUT_SEC = 60
ANALYSIS_TIMEOUT_PER_TRACK_SEC = 90


# ---------- Window helpers ----------

def _find_window(title_contains: str, backend: str = "uia", largest: bool = True):
    """Return a top-level window whose title contains the substring.

    Args:
        title_contains: substring to match in window title
        backend: pywinauto backend ("uia" or "win32")
        largest: if True, return the largest matching window. This handles
            apps that create same-titled popup windows for menus/dropdowns
            (Rekordbox is a notable case — its File menu dropdown is its own
            "rekordbox"-titled top-level window).
    """
    matches = []
    for w in Desktop(backend=backend).windows():
        try:
            if title_contains in w.window_text():
                if largest:
                    r = w.rectangle()
                    matches.append((r.width() * r.height(), w))
                else:
                    return w
        except Exception:
            continue
    if matches:
        matches.sort(key=lambda x: -x[0])
        return matches[0][1]
    return None


def _wait_for_window(title_contains: str, timeout: float = WINDOW_TIMEOUT_SEC, backend: str = "uia"):
    """Block until a window appears, or timeout."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        w = _find_window(title_contains, backend=backend)
        if w:
            return w
        time.sleep(0.5)
    return None


def _close_process(name_pattern: str):
    """Kill all processes whose name matches the regex pattern."""
    try:
        subprocess.run(
            ["powershell", "-NoProfile", "-Command",
             f"Get-Process | Where-Object {{ $_.ProcessName -match '{name_pattern}' }} | Stop-Process -Force"],
            capture_output=True, check=False, timeout=10,
        )
        time.sleep(2)
    except Exception as e:
        print(f"  close_process({name_pattern}) error: {e}")


def _force_focus(window) -> bool:
    """Force a window to the foreground via AttachThreadInput +
    SetForegroundWindow. No keystrokes, no mouse."""
    import ctypes
    user32 = ctypes.windll.user32
    kernel32 = ctypes.windll.kernel32
    SW_SHOW = 5

    try:
        hwnd = window.handle
        fg_hwnd = user32.GetForegroundWindow()
        if fg_hwnd == hwnd:
            user32.ShowWindow(hwnd, SW_SHOW)
            return True

        fg_thread = user32.GetWindowThreadProcessId(fg_hwnd, None)
        cur_thread = kernel32.GetCurrentThreadId()

        attached = False
        if fg_thread and fg_thread != cur_thread:
            attached = bool(user32.AttachThreadInput(cur_thread, fg_thread, True))

        try:
            user32.ShowWindow(hwnd, SW_SHOW)
            user32.BringWindowToTop(hwnd)
            user32.SetForegroundWindow(hwnd)
        finally:
            if attached:
                user32.AttachThreadInput(cur_thread, fg_thread, False)

        time.sleep(0.3)
        if user32.GetForegroundWindow() == hwnd:
            return True

        try:
            window.set_focus()
        except Exception:
            pass
        return user32.GetForegroundWindow() == hwnd
    except Exception as e:
        print(f"  _force_focus failed: {e}")
        return False


def _alt_tap_to_open_rb_menu():
    """Tap Alt to open Rekordbox's File menu (JUCE quirk).

    JUCE apps interpret bare Alt as 'enter menu mode + open first menu' —
    in RB's case the leftmost menu (File). This is a side effect we exploit
    rather than fight: instead of clicking File, we Alt-tap and File pops
    open with Import etc. exposed in the UIA tree."""
    import ctypes
    user32 = ctypes.windll.user32
    VK_MENU = 0x12
    KEYEVENTF_KEYUP = 0x0002
    user32.keybd_event(VK_MENU, 0, 0, 0)
    user32.keybd_event(VK_MENU, 0, KEYEVENTF_KEYUP, 0)


# ---------- Message-based interaction helpers ----------

def _invoke_uia_control(ctrl) -> bool:
    """Invoke a UIA control via the Invoke pattern (no mouse, no focus steal).
    Returns True on success."""
    try:
        ctrl.invoke()
        return True
    except Exception:
        try:
            # Some UIA controls expose Select instead of Invoke
            ctrl.select()
            return True
        except Exception:
            return False


def _find_uia_descendant_by_text(window, text: str, control_type: str | None = None):
    """Find a UIA descendant whose window_text matches exactly."""
    try:
        kwargs = {}
        if control_type:
            kwargs["control_type"] = control_type
        for ctrl in window.descendants(**kwargs):
            try:
                if ctrl.window_text() == text:
                    return ctrl
            except Exception:
                continue
    except Exception:
        pass
    return None


def _click_uia_by_text(window, text: str, control_type: str | None = None,
                       timeout: float = 5.0) -> bool:
    """Find a UIA control by text and invoke it (no mouse)."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        ctrl = _find_uia_descendant_by_text(window, text, control_type)
        if ctrl and _invoke_uia_control(ctrl):
            return True
        time.sleep(0.3)
    return False


def _click_uia_globally_by_text(text: str, control_type: str | None = None,
                                timeout: float = 5.0) -> bool:
    """Find a UIA control by text across ALL windows, invoke it.
    Useful for menu items which often appear in their own popup windows."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        for w in Desktop(backend="uia").windows():
            ctrl = _find_uia_descendant_by_text(w, text, control_type)
            if ctrl and _invoke_uia_control(ctrl):
                return True
        time.sleep(0.3)
    return False


def _click_menuitem_with_cursor_restore(
    text: str,
    parent_window=None,
    parent_rect=None,
    timeout: float = 5.0,
) -> bool:
    """Click a UIA MenuItem by text using a brief mouse-click, then restore
    the cursor to its previous position.

    JUCE apps (Rekordbox) draw their own menus and don't honour UIA Invoke
    for dropping down submenus — mouse click is the only reliable way.

    Args:
        text: the MenuItem text to click
        parent_window: scope search to this window's descendants only.
            Recommended to avoid clicking a same-named menu in another app
            (e.g. Ableton's File menu vs. Rekordbox's File menu).
        parent_rect: alternatively, restrict matches to those inside this
            screen rectangle. Useful for submenu items that pop out into
            their own window but appear nearby the parent menu.
    """
    try:
        prev_pos = pyautogui.position()
    except Exception:
        prev_pos = None

    def _candidates():
        """Yield (ctrl, rect) for matching MenuItems, filtered by scope."""
        if parent_window is not None:
            # Search only inside the parent window's UIA tree
            ctrl = _find_uia_descendant_by_text(parent_window, text, control_type="MenuItem")
            if ctrl:
                try:
                    yield ctrl, ctrl.rectangle()
                except Exception:
                    pass
        else:
            # Global search — but optionally filter by location
            for w in Desktop(backend="uia").windows():
                ctrl = _find_uia_descendant_by_text(w, text, control_type="MenuItem")
                if not ctrl:
                    continue
                try:
                    r = ctrl.rectangle()
                except Exception:
                    continue
                if parent_rect is not None:
                    # Only accept items reasonably close to the parent rect
                    margin = 600
                    if (r.left < parent_rect.left - margin or
                            r.left > parent_rect.right + margin or
                            r.top < parent_rect.top - margin or
                            r.top > parent_rect.bottom + 800):
                        continue
                yield ctrl, r

    deadline = time.time() + timeout
    clicked = False
    try:
        while time.time() < deadline:
            for ctrl, r in _candidates():
                try:
                    if r.width() > 0:
                        cx, cy = r.left + r.width() // 2, r.top + r.height() // 2
                        pyautogui.click(cx, cy, _pause=False)
                        clicked = True
                        break
                except Exception:
                    continue
            if clicked:
                break
            time.sleep(0.3)
    finally:
        if prev_pos is not None:
            try:
                pyautogui.moveTo(prev_pos[0], prev_pos[1], _pause=False)
            except Exception:
                pass

    return clicked


def _click_win32_button(window, button_text: str, timeout: float = 5.0) -> bool:
    """Find a Button by text in a win32-backed window and send BM_CLICK.
    No mouse movement, no focus stealing."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            for child in window.descendants():
                try:
                    if (child.window_text() == button_text and
                            child.class_name() == "Button"):
                        child.click()  # message-based for win32
                        return True
                except Exception:
                    continue
        except Exception:
            pass
        time.sleep(0.3)
    return False


def _set_edit_text(window, text: str, edit_label_hint: str | None = None) -> bool:
    """Find an Edit control in a window and set its text via SendMessage.
    No keyboard input, no focus stealing.

    Args:
        window: the parent dialog (win32 or uia backend OK)
        text: the text to set
        edit_label_hint: substring of the Edit's accessible name to prefer
            (e.g. "File name", "Folder")
    """
    try:
        edits = []
        try:
            edits = list(window.descendants(class_name="Edit"))
        except Exception:
            pass
        if not edits:
            # UIA backend uses control_type
            try:
                edits = list(window.descendants(control_type="Edit"))
            except Exception:
                pass

        # Prefer the labelled one if hint given
        chosen = None
        if edit_label_hint:
            for e in edits:
                try:
                    name = (e.element_info.name or "")
                    if edit_label_hint.lower() in name.lower():
                        chosen = e
                        break
                except Exception:
                    continue
        if chosen is None and edits:
            # Prefer the largest visible Edit
            biggest_area = 0
            for e in edits:
                try:
                    r = e.rectangle()
                    area = r.width() * r.height()
                    if area > biggest_area:
                        biggest_area = area
                        chosen = e
                except Exception:
                    continue

        if chosen is None:
            return False

        try:
            chosen.set_edit_text(text)
            return True
        except Exception:
            try:
                chosen.set_text(text)
                return True
            except Exception:
                return False
    except Exception:
        return False


# ---------- MIK config patching ----------

def ensure_mik_no_rename():
    """Set MIK's RenameAfterProcessing to False so source files keep their
    original names. Patches user.config. MIK reads this on launch."""
    if not MIK_USER_CONFIG.exists():
        print(f"  MIK user.config not found at {MIK_USER_CONFIG} — skipping")
        return

    content = MIK_USER_CONFIG.read_text(encoding="utf-8")
    new_content = re.sub(
        r'(<setting name="RenameAfterProcessing"[^>]*>\s*<value>)True(</value>)',
        r"\1False\2",
        content,
    )
    if new_content != content:
        MIK_USER_CONFIG.write_text(new_content, encoding="utf-8")
        print("  MIK: RenameAfterProcessing set to False")
    else:
        print("  MIK: RenameAfterProcessing already False")


# ---------- MIK analysis state ----------

def is_mik_analyzed(audio_path: Path) -> bool:
    """True if MIK has analysed this track (recorded in MIKStore.db Song table)."""
    if MIK_STORE_DB.exists():
        try:
            conn = sqlite3.connect(f"file:{MIK_STORE_DB}?mode=ro", uri=True, timeout=2.0)
            cur = conn.cursor()
            abs_path = str(audio_path.resolve())
            for variant in (abs_path, abs_path.replace("/", "\\"), abs_path.replace("\\", "/")):
                cur.execute(
                    "SELECT IsAnalyzed, LastAnalyzedUtc FROM Song WHERE File = ?",
                    (variant,),
                )
                row = cur.fetchone()
                if row:
                    is_analyzed, last_utc = row
                    conn.close()
                    return bool(is_analyzed) and last_utc is not None
            conn.close()
        except Exception:
            pass

    # MP3s might have GEOB tags too — fall back to that
    try:
        tags = ID3(audio_path)
        if len(tags.getall("GEOB")) > 0:
            return True
    except (ID3NoHeaderError, Exception):
        pass

    return False


# ---------- MIK driving ----------

def _click_add_tracks_button_via_image(timeout: float = 10.0) -> tuple[int, int] | None:
    """Mouse-based fallback: locate MIK's blue +Add tracks button by image
    template and click it via the real cursor. This is the ONE case where
    we can't avoid moving the mouse — the button is a custom WPF control
    that doesn't expose itself via UIA.

    Returns the (x, y) clicked, or None if not found.

    !! This moves the user's actual mouse cursor !!
    """
    print("  [MOUSE FALLBACK] locating MIK Add tracks button by image")
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            loc = pyautogui.locateCenterOnScreen(
                str(MIK_ADD_TRACKS_TEMPLATE), confidence=0.85,
            )
            if loc:
                pyautogui.click(loc[0], loc[1])
                return (loc[0], loc[1])
        except Exception:
            pass
        time.sleep(0.5)
    return None


def _dismiss_mik_startup_dialogs(mik):
    """Click Skip on the 'Import MIK 10 data' dialog if present.
    Uses BM_CLICK via win32 backend — no mouse movement."""
    try:
        # Try UIA invoke first
        for btn in mik.descendants(title="Skip", control_type="Button"):
            try:
                if _invoke_uia_control(btn):
                    return
            except Exception:
                continue
    except Exception:
        pass


def _select_folder_in_browse_dialog(folder_path: Path, timeout: float = 15.0):
    """Drive a 'Browse For Folder' or 'Import Folder' picker (Win32 #32770).

    Strategy: find the dialog via win32 backend, set its Edit control text
    to the folder path via SendMessage, then send BM_CLICK to the OK button.
    No mouse, no focus stealing.
    """
    # Wait for the dialog
    dlg = None
    deadline = time.time() + timeout
    while time.time() < deadline:
        for w in Desktop(backend="win32").windows():
            try:
                title = w.window_text()
                cls = w.class_name()
                if title in ("Import Folder", "Browse For Folder", "Browse for folder",
                             "Select Folder", "Open", "Select folder", "Browse"):
                    dlg = w
                    break
                if cls == "#32770" and ("folder" in title.lower() or "browse" in title.lower()):
                    dlg = w
                    break
            except Exception:
                continue
        if dlg:
            break
        time.sleep(0.5)

    if not dlg:
        raise RuntimeError("Folder-picker dialog did not appear")

    print(f"  Folder dialog: '{dlg.window_text()}' (class={dlg.class_name()})")
    path_str = str(folder_path.resolve())

    # Strategy 1: find Edit control and set text via SendMessage (no mouse)
    set_ok = _set_edit_text(dlg, path_str, edit_label_hint="folder")
    if not set_ok:
        set_ok = _set_edit_text(dlg, path_str)

    if set_ok:
        print(f"  Pasted path via SendMessage: {path_str}")
    else:
        # No Edit control found — fall back to clipboard + WM_KEYDOWN via SetForegroundWindow
        print("  [MOUSE FALLBACK] no Edit control found, using clipboard paste")
        import pyperclip
        pyperclip.copy(path_str)
        try:
            dlg.set_focus()
        except Exception:
            pass
        time.sleep(0.3)
        pyautogui.hotkey("ctrl", "a")
        time.sleep(0.1)
        pyautogui.press("delete")
        time.sleep(0.1)
        pyautogui.hotkey("ctrl", "v")
        time.sleep(0.5)

    # Click OK via BM_CLICK (no mouse)
    time.sleep(0.5)
    for ok_label in ("OK", "Open", "Select Folder", "&OK"):
        if _click_win32_button(dlg, ok_label, timeout=2):
            print(f"  Clicked '{ok_label}' via BM_CLICK")
            return

    # No OK button found — fall back to pressing Enter (does need keyboard focus)
    print("  [KEYBOARD FALLBACK] no OK button found, sending Enter")
    try:
        dlg.set_focus()
        time.sleep(0.3)
    except Exception:
        pass
    pyautogui.press("enter")


def analyze_folder_with_mik(
    audio_folder: Path,
    expected_tracks: list[Path] | None = None,
    timeout_per_track_sec: float = ANALYSIS_TIMEOUT_PER_TRACK_SEC,
):
    """Drive MIK to analyze every audio file in a folder.

    Uses MIK's "Add folder" option (one click + one path).
    Most steps are message-based (no mouse). The only mouse-required step
    is clicking the blue "+ Add tracks" button on MIK's sidebar (WPF custom
    control). User will see their cursor jump briefly there.
    """
    if expected_tracks is None:
        expected_tracks = sorted(audio_folder.glob("*.wav"))

    to_analyze = [p for p in expected_tracks if not is_mik_analyzed(p)]
    if not to_analyze:
        print(f"MIK: all {len(expected_tracks)} tracks already analyzed")
        return

    print(f"MIK: driving analysis for {len(to_analyze)}/{len(expected_tracks)} tracks")

    # 1. Pre-flight: prevent file renaming
    ensure_mik_no_rename()

    # 2. Close any existing instance, launch fresh
    _close_process("MixedInKey")
    subprocess.Popen(["cmd", "/c", "start", "", str(MIK_SHORTCUT)], shell=False)

    # 3. Wait for main window
    mik = _wait_for_window("Mixed In Key", timeout=WINDOW_TIMEOUT_SEC)
    if not mik:
        raise RuntimeError("MIK window did not appear within timeout")

    # 4. Let it settle and dismiss startup dialogs
    time.sleep(5)
    _dismiss_mik_startup_dialogs(mik)
    time.sleep(2)

    # 5. Navigate to My Collection tab (UIA invoke — no mouse)
    mik = _find_window("Mixed In Key")
    tab_clicked = False
    for tab in mik.descendants(control_type="TabItem"):
        try:
            if "AnalyzeViewModel" in tab.window_text():
                if _invoke_uia_control(tab):
                    tab_clicked = True
                    break
        except Exception:
            continue
    if not tab_clicked:
        raise RuntimeError("Could not switch to My Collection tab")
    time.sleep(2)

    # 6. Click + Add tracks (the ONE mouse step)
    pos = _click_add_tracks_button_via_image(timeout=10)
    if not pos:
        raise RuntimeError(
            f"Could not find Add tracks button (template: {MIK_ADD_TRACKS_TEMPLATE})"
        )
    print(f"  Clicked + Add tracks at {pos}")
    time.sleep(1.5)

    # 7. In-app modal: click 'Add folder' via UIA invoke (no mouse)
    mik = _find_window("Mixed In Key")
    if not _click_uia_by_text(mik, "Add folder", control_type="Button", timeout=10):
        raise RuntimeError("Could not invoke 'Add folder' in modal")
    print("  Invoked 'Add folder' in modal")
    time.sleep(1.5)

    # 8. Drive Browse For Folder dialog (message-based)
    _select_folder_in_browse_dialog(audio_folder)
    time.sleep(2)

    # 9. Poll DB for completion
    total_timeout = timeout_per_track_sec * max(1, len(to_analyze))
    print(f"  Polling MIKStore.db (timeout {total_timeout:.0f}s)...")
    deadline = time.time() + total_timeout
    last_count = -1
    while time.time() < deadline:
        analyzed = [p for p in to_analyze if is_mik_analyzed(p)]
        if len(analyzed) > last_count:
            print(f"  {len(analyzed)}/{len(to_analyze)} analyzed")
            last_count = len(analyzed)
        if len(analyzed) == len(to_analyze):
            print("  MIK: analysis complete for all tracks")
            break
        time.sleep(3)
    else:
        missing = [p.name for p in to_analyze if not is_mik_analyzed(p)]
        raise TimeoutError(
            f"MIK did not finish in {total_timeout:.0f}s. Missing: {missing}"
        )

    # 10. Close MIK
    _close_process("MixedInKey")
    print("MIK: done")


def analyze_with_mik(audio_paths: list[Path], **kw):
    """Convenience: derive folder from first path."""
    if not audio_paths:
        return
    folder = audio_paths[0].parent
    return analyze_folder_with_mik(folder, expected_tracks=audio_paths, **kw)


# ---------- Rekordbox driving ----------

def is_rekordbox_analyzed(audio_path: Path) -> bool:
    """True if Rekordbox has analyzed this track (ANLZ + library entry)."""
    try:
        from automated_dj_mixes.rekordbox_reader import (
            read_rekordbox_library, find_rekordbox_match,
        )
    except Exception:
        return False
    try:
        lib = read_rekordbox_library()
        match = find_rekordbox_match(audio_path.name, lib)
        return match is not None
    except Exception:
        return False


def _dismiss_rb_menu_popups():
    """Close any rekordbox-titled popup windows (open menus) that aren't
    the main RB window. Uses WM_CLOSE PostMessage — no mouse, no keyboard."""
    import ctypes
    user32 = ctypes.windll.user32
    WM_CLOSE = 0x0010

    # Find main RB window (largest)
    main_rb = _find_window("rekordbox", largest=True)
    main_handle = main_rb.handle if main_rb else None

    closed = 0
    for w in Desktop(backend="uia").windows():
        try:
            if w.window_text() == "rekordbox" and w.handle != main_handle:
                user32.PostMessageW(w.handle, WM_CLOSE, 0, 0)
                closed += 1
        except Exception:
            continue
    if closed:
        print(f"  Closed {closed} stale rekordbox popup(s)")
        time.sleep(0.5)


def _click_screen_xy_with_cursor_restore(x: int, y: int) -> bool:
    """Click absolute screen coords, save+restore cursor position."""
    try:
        prev = pyautogui.position()
    except Exception:
        prev = None
    try:
        pyautogui.click(x, y, _pause=False)
    except Exception as e:
        print(f"  click({x},{y}) failed: {e}")
        return False
    finally:
        if prev is not None:
            try:
                pyautogui.moveTo(prev[0], prev[1], _pause=False)
            except Exception:
                pass
    return True


def _hover_menuitem_with_cursor_restore(
    text: str, parent_rect=None, timeout: float = 5.0, hover_dwell: float = 0.6,
) -> bool:
    """Move cursor over a UIA MenuItem to trigger submenu expansion, dwell
    for hover_dwell seconds so the submenu has time to render, then restore
    the cursor. Used for menu items with submenus (e.g. File → Import).
    """
    try:
        prev = pyautogui.position()
    except Exception:
        prev = None

    deadline = time.time() + timeout
    hovered = False
    try:
        while time.time() < deadline:
            for w in Desktop(backend="uia").windows():
                ctrl = _find_uia_descendant_by_text(w, text, control_type="MenuItem")
                if not ctrl:
                    continue
                try:
                    r = ctrl.rectangle()
                except Exception:
                    continue
                if parent_rect is not None:
                    margin = 600
                    if (r.left < parent_rect.left - margin or
                            r.left > parent_rect.right + margin or
                            r.top < parent_rect.top - margin or
                            r.top > parent_rect.bottom + 800):
                        continue
                if r.width() > 0:
                    cx, cy = r.left + r.width() // 2, r.top + r.height() // 2
                    pyautogui.moveTo(cx, cy, _pause=False)
                    hovered = True
                    break
            if hovered:
                break
            time.sleep(0.3)

        if hovered:
            # Dwell so submenu has time to render
            time.sleep(hover_dwell)
    finally:
        if prev is not None:
            try:
                pyautogui.moveTo(prev[0], prev[1], _pause=False)
            except Exception:
                pass

    return hovered


def _navigate_rb_menu_to_import_folder(rb, timeout: float = 8.0) -> bool:
    """Navigate Rekordbox's File → Import → Import Folder.

    The File menu item lives at a known offset within RB's window (top-left
    of the menu bar). UIA can't see it reliably without Alt-activating the
    menu (which itself opens the menu and confuses things), so we click at
    a known offset relative to RB's window rect.

    Once File opens, the submenu items (Import, Import Folder) DO appear
    in the global UIA tree as popup MenuItems — we use UIA for those.

    All clicks save+restore the cursor position so Sam's working state is
    preserved.
    """
    # 1. Dismiss any stale RB menu popups
    _dismiss_rb_menu_popups()

    # 2. Force RB main window to foreground
    rb = _find_window("rekordbox") or rb
    if not _force_focus(rb):
        print("  Could not bring RB to foreground")
        return False
    time.sleep(0.4)

    # Re-find largest after focus
    rb = _find_window("rekordbox") or rb
    rb_rect = rb.rectangle()
    print(f"  RB window rect: {rb_rect}")

    # 1. Click File at known offset (top-left of menu bar).
    #    Probe-confirmed: File menu item center at (rb.left + 29, rb.top + 14).
    file_x = rb_rect.left + 29
    file_y = rb_rect.top + 14
    if not _click_screen_xy_with_cursor_restore(file_x, file_y):
        return False
    print(f"  Clicked File at ({file_x},{file_y})")
    time.sleep(0.8)

    # 2. HOVER Import to expand its submenu — clicking it would dismiss the
    #    menu. Windows menu standard: hover-to-expand for parent items.
    if not _hover_menuitem_with_cursor_restore("Import", parent_rect=rb_rect, timeout=timeout):
        return False
    print("  Hovered Import (submenu should now be open)")

    # 3. Click Import Folder
    if not _click_menuitem_with_cursor_restore("Import Folder", parent_rect=rb_rect, timeout=timeout):
        return False
    print("  Clicked Import Folder")

    return True


def analyze_folder_with_rekordbox(
    audio_folder: Path,
    expected_tracks: list[Path] | None = None,
    timeout_per_track_sec: float = ANALYSIS_TIMEOUT_PER_TRACK_SEC,
):
    """Drive Rekordbox to import + analyze every audio file in a folder.

    Uses File → Import → Import Folder. Does NOT close Rekordbox if already
    running. All steps message-based — no mouse.

    Pre-requisite: Library Protection must be OFF in RB (toggle the padlock
    icon in the top toolbar). The Import Folder action silently no-ops when
    Library Protection is on.
    """
    if expected_tracks is None:
        expected_tracks = sorted(audio_folder.glob("*.wav"))

    to_analyze = [p for p in expected_tracks if not is_rekordbox_analyzed(p)]
    if not to_analyze:
        print(f"RB: all {len(expected_tracks)} tracks already in library")
        return

    print(f"RB: driving import for {len(to_analyze)}/{len(expected_tracks)} tracks")

    # 1. Find or launch RB
    rb = _find_window("rekordbox")
    if not rb:
        print("  RB not running — launching")
        subprocess.Popen([str(RB_EXE)], shell=False)
        rb = _wait_for_window("rekordbox", timeout=WINDOW_TIMEOUT_SEC)
        if not rb:
            raise RuntimeError("Rekordbox did not appear")
        time.sleep(15)
    else:
        print("  RB already running — using existing instance")

    # 2. Navigate File → Import → Import Folder (message-based)
    if not _navigate_rb_menu_to_import_folder(rb):
        raise RuntimeError(
            "Could not navigate File → Import → Import Folder. "
            "Check that Library Protection is OFF in RB."
        )
    print("  Navigated File → Import → Import Folder")
    time.sleep(1.5)

    # 3. Drive the folder picker (message-based)
    _select_folder_in_browse_dialog(audio_folder)
    time.sleep(2)

    # 4. Poll RB library
    total_timeout = timeout_per_track_sec * max(1, len(to_analyze))
    print(f"  Polling RB library (timeout {total_timeout:.0f}s)...")
    deadline = time.time() + total_timeout
    last_count = -1
    while time.time() < deadline:
        try:
            analyzed = [p for p in to_analyze if is_rekordbox_analyzed(p)]
        except Exception:
            analyzed = []
        if len(analyzed) > last_count:
            print(f"  {len(analyzed)}/{len(to_analyze)} analyzed")
            last_count = len(analyzed)
        if len(analyzed) == len(to_analyze):
            print("  RB: analysis complete for all tracks")
            break
        time.sleep(5)
    else:
        missing = [p.name for p in to_analyze if not is_rekordbox_analyzed(p)]
        raise TimeoutError(
            f"RB did not finish in {total_timeout:.0f}s. Missing: {missing}"
        )

    print("RB: done (left running to preserve session)")


def analyze_with_rekordbox(audio_paths: list[Path], **kw):
    """Convenience: import the parent folder."""
    if not audio_paths:
        return
    folder = audio_paths[0].parent
    return analyze_folder_with_rekordbox(folder, expected_tracks=audio_paths, **kw)


# ---------- Top-level entry ----------

def ensure_all_analyzed(audio_paths: list[Path]):
    """Run MIK + Rekordbox analysis on any track that needs it."""
    analyze_with_mik(audio_paths)
    analyze_with_rekordbox(audio_paths)
