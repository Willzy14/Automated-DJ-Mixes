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

RB_EXE = Path("C:/Program Files/rekordbox/rekordbox.exe")  # fallback
RB_SHORTCUT = Path("C:/Users/Carillon/Desktop/rekordbox 7.lnk")

TEMPLATES_DIR = Path(__file__).parent / "templates"
MIK_ADD_TRACKS_TEMPLATE = TEMPLATES_DIR / "mik_add_tracks_button.png"

# ---------- Master-file validation ----------

_MASTER_PATTERN = re.compile(
    r"(24\s*Bit\s*MASTER|SW\s+V\d+)",
    re.IGNORECASE,
)


def _validate_masters_only(audio_paths: list[Path]) -> None:
    """Hard gate: refuse to feed stems, freezes, or raw audio to MIK / RB."""
    non_masters = [p.name for p in audio_paths if not _MASTER_PATTERN.search(p.stem)]
    if non_masters:
        raise ValueError(
            f"Refusing to analyze {len(non_masters)} non-master file(s) — "
            "only '24 Bit MASTER' or 'SW V<N>' WAVs are allowed:\n"
            + "\n".join(f"  - {n}" for n in non_masters[:10])
            + ("\n  ..." if len(non_masters) > 10 else "")
        )


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
    """Force a window to the foreground. Uses the Alt-tap trick to bypass
    Windows' SetForegroundWindow restrictions."""
    import ctypes
    user32 = ctypes.windll.user32
    kernel32 = ctypes.windll.kernel32
    SW_SHOW = 5
    VK_MENU = 0x12
    KEYEVENTF_KEYUP = 0x0002

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
            # Alt-tap trick: Windows allows SetForegroundWindow after
            # a recent keyboard event from the calling thread
            user32.keybd_event(VK_MENU, 0, 0, 0)
            user32.keybd_event(VK_MENU, 0, KEYEVENTF_KEYUP, 0)

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
    """True if MIK has analysed this track (recorded in MIKStore.db Song table).

    Checks exact path first, then falls back to filename match. The fallback
    handles files analyzed via the Desktop staging folder — MIK records the
    staging path, but we query with the original Audio/ path.
    """
    if MIK_STORE_DB.exists():
        try:
            conn = sqlite3.connect(f"file:{MIK_STORE_DB}?mode=ro", uri=True, timeout=2.0)
            cur = conn.cursor()
            abs_path = str(audio_path.resolve())

            # Check 1: exact path match
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

            # Check 2: filename match (staging folder workaround)
            filename = audio_path.name
            cur.execute(
                "SELECT IsAnalyzed, LastAnalyzedUtc FROM Song WHERE File LIKE ?",
                (f"%{filename}",),
            )
            row = cur.fetchone()
            if row:
                is_analyzed, last_utc = row
                conn.close()
                return bool(is_analyzed) and last_utc is not None

            conn.close()
        except Exception:
            pass

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


def _read_edit_text(window) -> str | None:
    """Read text from the Edit control in a dialog. Returns None if not found."""
    try:
        edits = list(window.descendants(class_name="Edit"))
        if not edits:
            edits = list(window.descendants(control_type="Edit"))
        if edits:
            # Pick the largest visible edit
            biggest = max(edits, key=lambda e: (
                e.rectangle().width() * e.rectangle().height()
            ), default=None)
            if biggest:
                return biggest.window_text()
    except Exception:
        pass
    return None


def _select_folder_in_browse_dialog(folder_path: Path, timeout: float = 15.0):
    """Drive a folder-picker dialog to select folder_path.

    Handles TWO dialog types:
      1. Old-style SHBrowseForFolder (MIK) — SysTreeView32. The OK button
         follows the TREE selection, so we navigate the tree directly.
      2. Modern IFileDialog (Rekordbox) — has an address bar, Quick Access
         panel, and a "Folder:" text field at the bottom. Typing a full
         path in the Folder field + clicking "Select Folder" works.

    Detection: if a "Select Folder" or "Open" button exists AND there's an
    Edit labelled "Folder" → modern dialog. Otherwise → old-style tree.
    """
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
                if cls == "#32770" and ("folder" in title.lower() or
                                        "browse" in title.lower() or
                                        "import" in title.lower()):
                    dlg = w
                    break
            except Exception:
                continue
        if dlg:
            break
        time.sleep(0.5)

    if not dlg:
        raise RuntimeError("Folder-picker dialog did not appear")

    dlg_title = dlg.window_text()
    print(f"  Folder dialog: '{dlg_title}' (class={dlg.class_name()})")
    path_str = str(folder_path.resolve())

    # --- Detect dialog type ---
    # Modern IFileDialog has a ComboBox or Edit with "Folder" in its name,
    # plus a "Select Folder" button. Old-style has SysTreeView32 + "OK".
    is_modern = False
    try:
        for desc in dlg.descendants():
            try:
                cn = desc.class_name()
                if cn == "ComboBoxEx32" or (
                    cn == "Edit" and "folder" in (desc.element_info.name or "").lower()
                ):
                    is_modern = True
                    break
            except Exception:
                continue
    except Exception:
        pass

    # Also check for address bar (Breadcrumb / ToolbarWindow32)
    if not is_modern:
        try:
            for desc in dlg.descendants():
                try:
                    if desc.class_name() == "ToolbarWindow32" and "Address" in (
                        desc.element_info.name or ""
                    ):
                        is_modern = True
                        break
                except Exception:
                    continue
        except Exception:
            pass

    if is_modern:
        return _drive_modern_folder_dialog(dlg, folder_path)
    else:
        return _drive_old_style_browse_dialog(dlg, folder_path)


def _drive_modern_folder_dialog(dlg, folder_path: Path):
    """Handle a modern IFileDialog (Vista+ style) folder picker.

    Strategy: type the full path in the "Folder:" text field at the bottom,
    then click "Select Folder". No mouse needed — all via SendMessage.
    """
    path_str = str(folder_path.resolve())
    print(f"  Modern dialog detected — using Folder field approach")

    # Find the filename/folder Edit field (usually a ComboBoxEx32 → Edit,
    # or a direct Edit control near the bottom of the dialog)
    set_ok = _set_edit_text(dlg, path_str, edit_label_hint="folder")
    if not set_ok:
        set_ok = _set_edit_text(dlg, path_str, edit_label_hint="file name")
    if not set_ok:
        set_ok = _set_edit_text(dlg, path_str)

    if set_ok:
        print(f"  Set folder path via SendMessage: {path_str}")
    else:
        print("  [KEYBOARD FALLBACK] pasting path via clipboard")
        import pyperclip
        pyperclip.copy(path_str)
        try:
            dlg.set_focus()
        except Exception:
            pass
        time.sleep(0.3)
        pyautogui.hotkey("ctrl", "l")
        time.sleep(0.3)
        pyautogui.hotkey("ctrl", "a")
        time.sleep(0.1)
        pyautogui.hotkey("ctrl", "v")
        time.sleep(0.5)

    time.sleep(0.3)

    # Two-step confirmation for IFileDialog:
    # Step 1: Enter in the Folder field NAVIGATES into the folder
    # Step 2: A second action CONFIRMS the selection (Select Folder / IDOK)
    import ctypes
    user32 = ctypes.windll.user32
    VK_RETURN = 0x0D
    WM_KEYDOWN = 0x0100
    WM_KEYUP = 0x0101
    WM_COMMAND = 0x0111
    BM_CLICK = 0x00F5
    IDOK = 1

    # Step 1: Send Enter to the Edit to navigate into the folder
    try:
        edits = list(dlg.descendants(class_name="Edit"))
        if not edits:
            edits = list(dlg.descendants(control_type="Edit"))
        for edit in edits:
            try:
                if path_str.lower() in (edit.window_text() or "").lower():
                    user32.PostMessageW(edit.handle, WM_KEYDOWN, VK_RETURN, 0)
                    time.sleep(0.1)
                    user32.PostMessageW(edit.handle, WM_KEYUP, VK_RETURN, 0)
                    print("  Step 1: Enter sent to Folder field (navigating...)")
                    break
            except Exception:
                continue
    except Exception:
        pass

    # Wait for navigation to complete
    time.sleep(1.5)

    # Step 2: Confirm the folder selection
    # Try WM_COMMAND IDOK first (standard Windows "click default button")
    dlg_hwnd = dlg.handle
    user32.PostMessageW(dlg_hwnd, WM_COMMAND, IDOK, 0)
    print("  Step 2: Sent WM_COMMAND IDOK to confirm")
    time.sleep(1.0)

    # Check if dialog closed (success)
    dialog_gone = not user32.IsWindow(dlg_hwnd)
    if dialog_gone:
        print("  Dialog closed — folder selected successfully")
        return

    # IDOK didn't close it — try finding and clicking the button directly
    print("  Dialog still open — trying button click fallbacks")

    # Find ANY window with "Select Folder" text and send BM_CLICK
    buf = ctypes.create_unicode_buffer(256)
    def _find_and_click_button(parent_hwnd, target_text):
        child_hwnd = 0
        while True:
            child_hwnd = user32.FindWindowExW(parent_hwnd, child_hwnd, None, None)
            if not child_hwnd:
                break
            user32.GetWindowTextW(child_hwnd, buf, 256)
            if target_text in buf.value:
                user32.PostMessageW(child_hwnd, BM_CLICK, 0, 0)
                return True
            # Check grandchildren too
            if _find_and_click_button(child_hwnd, target_text):
                return True
        return False

    for label in ("Select Folder", "Open", "OK"):
        if _find_and_click_button(dlg_hwnd, label):
            print(f"  Clicked '{label}' via recursive FindWindowEx + BM_CLICK")
            return

    # Last resort: UIA invoke
    dlg_uia = _find_window(dlg_title, backend="uia")
    if dlg_uia:
        for label in ("Select Folder", "Open", "OK"):
            if _click_uia_by_text(dlg_uia, label, control_type="Button", timeout=3):
                print(f"  Clicked '{label}' via UIA invoke")
                return

    print("  WARNING: could not confirm folder selection")


def _drive_old_style_browse_dialog(dlg, folder_path: Path):
    """Handle an old-style SHBrowseForFolder dialog (MIK uses this).

    Strategy: navigate the SysTreeView32 to select the target folder node,
    then click OK. The Edit field is ignored — OK follows the tree selection.
    """
    tree = None
    for desc in dlg.descendants():
        try:
            if desc.class_name() == "SysTreeView32":
                tree = desc
                break
        except Exception:
            continue

    if tree is None:
        raise RuntimeError("No SysTreeView32 found in old-style folder dialog")

    target_name = folder_path.name
    print(f"  Old-style dialog — tree navigation for '{target_name}' ({tree.item_count()} items)")

    selected = False

    # Method 1: pywinauto path lookup
    for path_str in (
        f"\\Desktop\\{target_name}",
        f"\\{target_name}",
    ):
        try:
            item = tree.get_item(path_str)
            item.ensure_visible()
            time.sleep(0.2)
            item.select()
            print(f"  Tree: selected via path '{path_str}'")
            selected = True
            break
        except Exception:
            continue

    # Method 2: walk tree roots → expand → find child by name
    if not selected:
        try:
            for root in tree.roots():
                root_text = root.text()
                root.expand()
                time.sleep(0.5)
                for child in root.children():
                    if child.text() == target_name:
                        child.ensure_visible()
                        time.sleep(0.2)
                        child.select()
                        print(f"  Tree: selected '{target_name}' under '{root_text}'")
                        selected = True
                        break
                if selected:
                    break
        except Exception as e:
            print(f"  Tree walk failed: {e}")

    if not selected:
        raise RuntimeError(
            f"Could not find '{target_name}' in folder dialog tree. "
            f"Ensure {folder_path} exists on Desktop."
        )

    time.sleep(0.5)

    # Click OK
    for ok_label in ("OK", "Open", "Select Folder", "&OK"):
        if _click_win32_button(dlg, ok_label, timeout=2):
            print(f"  Clicked '{ok_label}' via BM_CLICK")
            return

    print("  [KEYBOARD FALLBACK] pressing Enter")
    try:
        dlg.set_focus()
        time.sleep(0.3)
    except Exception:
        pass
    pyautogui.press("enter")


def _create_staging_folder(audio_folder: Path) -> Path:
    """Copy audio files to a shallow Desktop folder that MIK/RB's tree
    dialog can actually navigate to.

    MIK's Browse For Folder uses an old-style tree that can't handle deep
    paths. Copying to Desktop/_Pipeline_Import puts the files 1 level deep
    in the tree. The MIK reader matches by filename (LIKE %name%), so the
    different path doesn't matter.

    Returns the staging folder path. Caller MUST clean up via _remove_staging_folder.
    """
    import shutil
    staging = Path.home() / "Desktop" / "_Pipeline_Import"

    # Clean any stale staging folder
    if staging.exists():
        shutil.rmtree(staging, ignore_errors=True)

    staging.mkdir(parents=True, exist_ok=True)

    # Copy only WAV files (masters already validated by caller)
    copied = 0
    for wav in sorted(audio_folder.glob("*.wav")):
        shutil.copy2(wav, staging / wav.name)
        copied += 1

    print(f"  Staging folder: {staging} ({copied} WAVs copied)")
    return staging


def _copy_mik_tags_to_originals(staging: Path, original_folder: Path):
    """Copy MIK GEOB tags from staging copies back to the original audio files.

    MIK writes cue/energy/key data as GEOB ID3 tags into the files it
    analyzes. Since we analyze copies in the staging folder, the originals
    don't get the tags. This copies them back before the staging folder is
    deleted.
    """
    copied = 0
    for staging_wav in sorted(staging.glob("*.wav")):
        original = original_folder / staging_wav.name
        if not original.exists():
            continue
        try:
            staging_tags = ID3(staging_wav)
        except (ID3NoHeaderError, Exception):
            continue

        geob_tags = staging_tags.getall("GEOB")
        if not geob_tags:
            continue

        try:
            try:
                orig_tags = ID3(original)
            except ID3NoHeaderError:
                orig_tags = ID3()

            for geob in geob_tags:
                orig_tags.add(geob)

            orig_tags.save(original)
            copied += 1
        except Exception as e:
            print(f"  Tag copy failed for {staging_wav.name}: {e}")

    if copied:
        print(f"  MIK tags copied to {copied} original file(s)")


def _remove_staging_folder(staging: Path):
    """Remove the Desktop staging folder after analysis."""
    import shutil
    try:
        if staging.exists():
            shutil.rmtree(staging, ignore_errors=True)
            if not staging.exists():
                print(f"  Staging folder removed: {staging}")
            else:
                print(f"  WARNING: could not remove staging {staging}")
    except Exception as e:
        print(f"  WARNING: staging cleanup failed: {e}")


def analyze_folder_with_mik(
    audio_folder: Path,
    expected_tracks: list[Path] | None = None,
    timeout_per_track_sec: float = ANALYSIS_TIMEOUT_PER_TRACK_SEC,
):
    """Drive MIK to analyze every audio file in a folder.

    Uses MIK's "Add folder" option. Because MIK's Browse For Folder dialog
    uses an old-style tree that can't navigate deep paths reliably, we create
    a temporary staging copy on the Desktop (1 level deep = easy to
    navigate in the tree dialog).

    The only mouse-required step is clicking the blue "+ Add tracks" button
    on MIK's sidebar (WPF custom control).
    """
    if expected_tracks is None:
        expected_tracks = sorted(audio_folder.glob("*.wav"))

    _validate_masters_only(expected_tracks)

    to_analyze = [p for p in expected_tracks if not is_mik_analyzed(p)]
    if not to_analyze:
        print(f"MIK: all {len(expected_tracks)} tracks already analyzed")
        return

    print(f"MIK: driving analysis for {len(to_analyze)}/{len(expected_tracks)} tracks")

    # 1. Pre-flight: prevent file renaming
    ensure_mik_no_rename()

    # 2. Copy to shallow Desktop staging folder so MIK's tree can reach it
    staging = _create_staging_folder(audio_folder)

    try:
        # 3. Close any existing instance, launch fresh
        _close_process("MixedInKey")
        subprocess.Popen(["cmd", "/c", "start", "", str(MIK_SHORTCUT)], shell=False)

        # 4. Wait for main window
        mik = _wait_for_window("Mixed In Key", timeout=WINDOW_TIMEOUT_SEC)
        if not mik:
            raise RuntimeError("MIK window did not appear within timeout")

        # 5. Let it settle and dismiss startup dialogs
        time.sleep(5)
        _dismiss_mik_startup_dialogs(mik)
        time.sleep(2)

        # 6. Navigate to My Collection tab (UIA invoke — no mouse)
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

        # 7. Click + Add tracks (the ONE mouse step)
        pos = _click_add_tracks_button_via_image(timeout=10)
        if not pos:
            raise RuntimeError(
                f"Could not find Add tracks button (template: {MIK_ADD_TRACKS_TEMPLATE})"
            )
        print(f"  Clicked + Add tracks at {pos}")
        time.sleep(1.5)

        # 8. In-app modal: click 'Add folder' via UIA invoke (no mouse)
        mik = _find_window("Mixed In Key")
        if not _click_uia_by_text(mik, "Add folder", control_type="Button", timeout=10):
            raise RuntimeError("Could not invoke 'Add folder' in modal")
        print("  Invoked 'Add folder' in modal")
        time.sleep(1.5)

        # 9. Snapshot song count BEFORE import
        pre_import_count = 0
        try:
            conn = sqlite3.connect(f"file:{MIK_STORE_DB}?mode=ro", uri=True, timeout=2.0)
            cur = conn.cursor()
            cur.execute("SELECT COUNT(*) FROM Song")
            pre_import_count = cur.fetchone()[0]
            conn.close()
        except Exception:
            pass

        # 10. Drive Browse For Folder dialog — pointing at the staging
        #     staging folder (Desktop/_Pipeline_Import) — 1 level deep in tree
        _select_folder_in_browse_dialog(staging)
        time.sleep(2)

        # 11. Post-import safety check
        max_expected_new = len(to_analyze) + 5
        try:
            time.sleep(5)
            conn = sqlite3.connect(f"file:{MIK_STORE_DB}?mode=ro", uri=True, timeout=2.0)
            cur = conn.cursor()
            cur.execute("SELECT COUNT(*) FROM Song")
            post_count = cur.fetchone()[0]
            new_songs = post_count - pre_import_count
            conn.close()

            if new_songs > max_expected_new:
                print(f"  ABORT: MIK added {new_songs} songs (expected max {max_expected_new})")
                _close_process("MixedInKey")
                raise RuntimeError(
                    f"MIK imported {new_songs} songs instead of expected "
                    f"{len(to_analyze)}. Wrong folder. MIK closed."
                )
            elif new_songs > 0:
                print(f"  Post-import check: {new_songs} new songs OK")
        except RuntimeError:
            raise
        except Exception as e:
            print(f"  Post-import check skipped: {e}")

        # 12. Poll DB for completion
        total_timeout = timeout_per_track_sec * max(1, len(to_analyze))
        print(f"  Polling MIKStore.db (timeout {total_timeout:.0f}s)...")
        deadline = time.time() + total_timeout
        last_count = -1
        while time.time() < deadline:
            analyzed = [p for p in to_analyze if is_mik_analyzed(p)]
            if len(analyzed) > last_count:
                print(f"  {len(analyzed)}/{len(to_analyze)} analyzed")
                last_count = len(analyzed)

            # Wrong-folder growth check
            try:
                conn = sqlite3.connect(
                    f"file:{MIK_STORE_DB}?mode=ro", uri=True, timeout=2.0
                )
                cur = conn.cursor()
                cur.execute("SELECT COUNT(*) FROM Song")
                current_total = cur.fetchone()[0]
                conn.close()
                if current_total - pre_import_count > max_expected_new:
                    print(f"  ABORT: unexpected DB growth to {current_total}")
                    _close_process("MixedInKey")
                    raise RuntimeError("MIK importing from wrong folder. Aborting.")
            except RuntimeError:
                raise
            except Exception:
                pass

            if len(analyzed) == len(to_analyze):
                print("  MIK: analysis complete for all tracks")
                break
            time.sleep(3)
        else:
            missing = [p.name for p in to_analyze if not is_mik_analyzed(p)]
            raise TimeoutError(
                f"MIK did not finish in {total_timeout:.0f}s. Missing: {missing}"
            )

        # 13. Copy GEOB tags from staging copies to originals
        _copy_mik_tags_to_originals(staging, audio_folder)

        # 14. Close MIK
        _close_process("MixedInKey")
        print("MIK: done")

    finally:
        # Always clean up the staging folder
        _remove_staging_folder(staging)


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

    !! MOUSE REQUIRED — this moves the user's cursor briefly !!

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

    _validate_masters_only(expected_tracks)

    to_analyze = [p for p in expected_tracks if not is_rekordbox_analyzed(p)]
    if not to_analyze:
        print(f"RB: all {len(expected_tracks)} tracks already in library")
        return

    print(f"RB: driving import for {len(to_analyze)}/{len(expected_tracks)} tracks")

    # 1. Create staging folder FIRST — the tree dialog populates on open,
    #    so the folder must exist before the dialog appears
    staging = _create_staging_folder(audio_folder)

    def _launch_rb():
        if RB_SHORTCUT.exists():
            subprocess.Popen(
                ["cmd", "/c", "start", "", str(RB_SHORTCUT)], shell=False,
            )
        else:
            rb_base = Path("C:/Program Files/rekordbox")
            candidates = sorted(rb_base.glob("rekordbox */rekordbox.exe"), reverse=True)
            exe = candidates[0] if candidates else RB_EXE
            subprocess.Popen([str(exe)], shell=False)
        rb = _wait_for_window("rekordbox", timeout=WINDOW_TIMEOUT_SEC)
        if not rb:
            raise RuntimeError("Rekordbox did not appear")
        time.sleep(15)
        return rb

    try:
        # 2. Find or launch RB
        rb = _find_window("rekordbox")
        if not rb:
            print("  RB not running — launching")
            rb = _launch_rb()
        else:
            print("  RB already running — using existing instance")

        # 3. Navigate File → Import → Import Folder
        nav_ok = _navigate_rb_menu_to_import_folder(rb)

        # Retry: if navigation failed (e.g. stale state or focus issue),
        # kill RB and relaunch for a clean slate
        if not nav_ok:
            print("  Menu navigation failed — relaunching RB for clean state")
            _close_process("rekordbox")
            rb = _launch_rb()
            nav_ok = _navigate_rb_menu_to_import_folder(rb)

        if not nav_ok:
            raise RuntimeError(
                "Could not navigate File → Import → Import Folder. "
                "Check that Library Protection is OFF in RB."
            )
        print("  Navigated File → Import → Import Folder")
        time.sleep(1.5)

        # 4. Drive folder picker — staging folder already exists on Desktop
        _select_folder_in_browse_dialog(staging)
        time.sleep(2)

        # 5. Poll RB library
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

    finally:
        _remove_staging_folder(staging)


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
