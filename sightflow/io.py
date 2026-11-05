"""Frame sources (where pixels come from) and input backends (where actions go).

Sources:
  window   a window's client area, found by title (Windows)
  screen   a fixed screen region [left, top, width, height] (or the whole monitor)
  image    a still image file (for testing detectors / flows)
  python   your own object: factory "module:function" -> object with grab()

Inputs:
  keyboard_mouse   real OS input via pynput (movement mapped to held keys)
  source           the python source object handles input itself (simulators)
  none             dry run: decide but never press anything
"""
from __future__ import annotations

import importlib
import sys
import threading
import time
from pathlib import Path

import cv2
import numpy as np


# ======================================================================= sources
class FrameSource:
    origin = (0, 0)  # screen position of frame pixel (0, 0), used to place clicks
    scale = 1.0      # screen pixels per frame pixel (frames may be resized)

    def to_screen(self, x, y):
        ox, oy = self.origin
        return int(ox + x * self.scale), int(oy + y * self.scale)

    def grab(self) -> np.ndarray:
        raise NotImplementedError

    def time(self) -> float:
        return time.monotonic()

    def close(self):
        pass


class ImageSource(FrameSource):
    def __init__(self, path: Path):
        img = cv2.imread(str(path), cv2.IMREAD_COLOR)
        if img is None:
            raise FileNotFoundError(path)
        self.img = img

    def grab(self):
        return self.img.copy()


def _process_name(pid: int) -> str:
    import ctypes
    from ctypes import wintypes

    k32 = ctypes.windll.kernel32
    handle = k32.OpenProcess(0x1000, False, int(pid))   # QUERY_LIMITED_INFORMATION
    if not handle:
        return ""
    try:
        size = wintypes.DWORD(1024)
        buf = ctypes.create_unicode_buffer(1024)
        if k32.QueryFullProcessImageNameW(handle, 0, buf, ctypes.byref(size)):
            return buf.value
        return ""
    finally:
        k32.CloseHandle(handle)


def _find_window(title: str, process: str | None = None):
    """Handle of the first visible window whose title contains `title`.

    `process` optionally restricts the match to an executable name
    (e.g. ``dnplayer.exe`` for LDPlayer), so another window that happens to
    contain the same words is ignored.
    """
    if sys.platform != "win32":
        raise RuntimeError("window source is Windows-only; use type: screen with a region")
    import ctypes
    from ctypes import wintypes

    user32 = ctypes.windll.user32
    try:  # real pixels on high-DPI displays
        ctypes.windll.shcore.SetProcessDpiAwareness(2)
    except Exception:
        pass
    found = []

    @ctypes.WINFUNCTYPE(ctypes.c_bool, wintypes.HWND, wintypes.LPARAM)
    def cb(hwnd, _):
        if user32.IsWindowVisible(hwnd):
            n = user32.GetWindowTextLengthW(hwnd)
            buf = ctypes.create_unicode_buffer(n + 1)
            user32.GetWindowTextW(hwnd, buf, n + 1)
            if title.lower() in buf.value.lower():
                pid = wintypes.DWORD()
                user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
                if process and not _process_name(pid.value).lower().endswith(process.lower()):
                    return True
                found.append(hwnd)
                return False
        return True

    user32.EnumWindows(cb, 0)
    if not found:
        raise RuntimeError(f"no visible window with title containing {title!r}")
    return found[0]


def _find_child(parent, name: str):
    """First visible child whose class name or title contains `name` (LDPlayer's
    game area is a child called ``TheRender``)."""
    if not name:
        return None
    import ctypes
    from ctypes import wintypes

    user32 = ctypes.windll.user32
    found = []

    @ctypes.WINFUNCTYPE(ctypes.c_bool, wintypes.HWND, wintypes.LPARAM)
    def cb(hwnd, _):
        cls = ctypes.create_unicode_buffer(256)
        user32.GetClassNameW(hwnd, cls, 256)
        n = user32.GetWindowTextLengthW(hwnd)
        buf = ctypes.create_unicode_buffer(n + 1)
        user32.GetWindowTextW(hwnd, buf, n + 1)
        if user32.IsWindowVisible(hwnd) and (name.lower() in cls.value.lower()
                                             or name.lower() in buf.value.lower()):
            found.append(hwnd)
            return False
        return True

    user32.EnumChildWindows(parent, cb, 0)
    return found[0] if found else None


def _window_rect(hwnd):
    """Client-area rect (left, top, w, h) in screen pixels."""
    import ctypes
    from ctypes import wintypes

    user32 = ctypes.windll.user32
    rect = wintypes.RECT()
    user32.GetClientRect(hwnd, ctypes.byref(rect))
    pt = wintypes.POINT(0, 0)
    user32.ClientToScreen(hwnd, ctypes.byref(pt))
    return pt.x, pt.y, rect.right, rect.bottom


def _find_window_rect(title: str, process: str | None = None):
    return _window_rect(_find_window(title, process))


class ScreenSource(FrameSource):
    def __init__(self, region=None, window: str | None = None, monitor: int = 1,
                 child: str | None = None, width: int | None = None,
                 process: str | None = None):
        self._local = threading.local()
        self.window = window
        self.process = process
        self.width = int(width) if width else None
        self.child = child
        self.hwnd = None
        self.monitor = monitor
        self.region = region
        self._resolve()

    @property
    def sct(self):
        # mss keeps GDI handles that only work in the thread that made them,
        # so the UI thread and the engine thread each get their own.
        sct = getattr(self._local, "sct", None)
        if sct is None:
            import mss

            sct = self._local.sct = mss.mss()
        return sct

    def _resolve(self):
        if self.window:
            self.hwnd = _find_window(self.window, self.process)
            area = (_find_child(self.hwnd, self.child) if self.child else None) or self.hwnd
            l, t, w, h = _window_rect(area)
        elif self.region:
            l, t, w, h = (int(v) for v in self.region)
        else:
            m = self.sct.monitors[self.monitor]
            l, t, w, h = m["left"], m["top"], m["width"], m["height"]
        self.box = {"left": l, "top": t, "width": w, "height": h}
        self.origin = (l, t)

    def grab(self):
        if self.window:
            self._resolve()
        shot = cv2.cvtColor(np.asarray(self.sct.grab(self.box)), cv2.COLOR_BGRA2BGR)
        self.scale = 1.0
        if self.width and shot.shape[1] != self.width:
            self.scale = shot.shape[1] / self.width
            h = max(1, round(shot.shape[0] / self.scale))
            interp = cv2.INTER_AREA if self.scale > 1 else cv2.INTER_LINEAR
            shot = cv2.resize(shot, (self.width, h), interpolation=interp)
        return shot

    def is_foreground(self) -> bool:
        if not self.window:
            return True
        import ctypes

        user32 = ctypes.windll.user32
        fg = user32.GetForegroundWindow()
        return bool(fg) and user32.GetAncestor(fg, 2) == user32.GetAncestor(self.hwnd, 2)

    def focus(self):
        if not self.window:
            return
        import ctypes

        user32 = ctypes.windll.user32
        self._resolve()
        if user32.IsIconic(self.hwnd):
            user32.ShowWindow(self.hwnd, 9)      # SW_RESTORE
        # A synthetic Alt press lets a background process hand focus over.
        user32.keybd_event(0x12, 0, 0, 0)
        user32.keybd_event(0x12, 0, 2, 0)
        user32.SetForegroundWindow(self.hwnd)

    def close(self):
        sct = getattr(self._local, "sct", None)
        if sct is not None:
            sct.close()
            self._local.sct = None


class VideoSource(FrameSource):
    """A video file, played in a loop at its own frame rate. Good for checking a
    detector on recorded gameplay before touching the real game."""

    def __init__(self, path: Path):
        self.cap = cv2.VideoCapture(str(path))
        if not self.cap.isOpened():
            raise FileNotFoundError(path)
        self.fps = self.cap.get(cv2.CAP_PROP_FPS) or 30
        self.frame_i = 0

    def grab(self):
        ok, frame = self.cap.read()
        if not ok:
            self.cap.set(cv2.CAP_PROP_POS_FRAMES, 0)
            self.frame_i = 0
            ok, frame = self.cap.read()
            if not ok:
                raise RuntimeError("cannot read video")
        self.frame_i += 1
        return frame

    def time(self):
        return self.frame_i / self.fps

    def close(self):
        self.cap.release()


def list_windows() -> list[str]:
    """Titles of visible top-level windows (Windows only; [] elsewhere)."""
    if sys.platform != "win32":
        return []
    import ctypes
    from ctypes import wintypes

    user32 = ctypes.windll.user32
    titles = []

    @ctypes.WINFUNCTYPE(ctypes.c_bool, wintypes.HWND, wintypes.LPARAM)
    def cb(hwnd, _):
        if user32.IsWindowVisible(hwnd):
            n = user32.GetWindowTextLengthW(hwnd)
            if n:
                buf = ctypes.create_unicode_buffer(n + 1)
                user32.GetWindowTextW(hwnd, buf, n + 1)
                titles.append(buf.value)
        return True

    user32.EnumWindows(cb, 0)
    return sorted(set(titles))


def load_factory(ref: str, base_dir: Path):
    mod_name, _, func = ref.partition(":")
    for p in (str(base_dir), str(Path.cwd())):
        if p not in sys.path:
            sys.path.insert(0, p)
    return getattr(importlib.import_module(mod_name), func or "make")


def build_source(spec: dict, base_dir: Path) -> FrameSource:
    kind = spec.get("type", "screen")
    if kind == "window":
        return ScreenSource(window=spec["title"], child=spec.get("child"),
                            width=spec.get("width"), process=spec.get("process"))
    if kind == "screen":
        return ScreenSource(region=spec.get("region"), monitor=spec.get("monitor", 1),
                            width=spec.get("width"))
    if kind == "image":
        return ImageSource(base_dir / spec["path"])
    if kind == "video":
        return VideoSource(base_dir / spec["path"])
    if kind == "python":
        args = {k: v for k, v in spec.items() if k not in ("type", "factory")}
        return load_factory(spec["factory"], base_dir)(**args)
    raise ValueError(f"unknown source type {kind!r}")


# ======================================================================= inputs
class InputBackend:
    def set_move(self, vec):  # vec: (x, y) or None
        pass

    def tap(self, key: str):
        pass

    def hold(self, key: str, seconds: float):
        pass

    def key_down(self, key: str):
        pass

    def key_up(self, key: str):
        pass

    def release_all(self):
        pass

    def click(self, x: float, y: float):  # frame coordinates
        pass

    def cast(self, x: float, y: float, press: str):
        """Point at (x, y) and press `press` ("mouse_right", "mouse_left" or a key)."""
        pass

    def drag(self, fx: float, fy: float, tx: float, ty: float, seconds: float = 0.15):
        pass

    def close(self):
        pass


class NullInput(InputBackend):
    pass


class KeyboardMouseInput(InputBackend):
    """Real input via pynput. Movement vectors become held direction keys
    (8-way). Clicks are offset by the source origin so frame coords map to the
    screen."""

    def __init__(self, source: FrameSource, move_keys: dict | None = None,
                 deadzone: float = 0.35, tap_seconds: float = 0.03):
        from pynput import keyboard, mouse

        self._kb = keyboard.Controller()
        self._mouse = mouse.Controller()
        self._Key = keyboard.Key
        self._Button = mouse.Button
        self.source = source
        mk = move_keys or {"up": "w", "down": "s", "left": "a", "right": "d"}
        self.move_keys = {d: self._key(k) for d, k in mk.items()}
        self.deadzone = deadzone
        self.tap_seconds = tap_seconds
        self.held: set = set()
        self.keys_down: set = set()

    def _key(self, name: str):
        name = str(name)
        return getattr(self._Key, name) if hasattr(self._Key, name) else name

    def set_move(self, vec):
        want = set()
        if vec is not None:
            x, y = vec
            if x > self.deadzone:
                want.add(self.move_keys["right"])
            if x < -self.deadzone:
                want.add(self.move_keys["left"])
            if y > self.deadzone:
                want.add(self.move_keys["down"])
            if y < -self.deadzone:
                want.add(self.move_keys["up"])
        for k in self.held - want:
            self._kb.release(k)
        for k in want - self.held:
            self._kb.press(k)
        self.held = want

    def tap(self, key):
        k = self._key(key)
        self._kb.press(k)
        time.sleep(self.tap_seconds)
        self._kb.release(k)

    def hold(self, key, seconds):
        k = self._key(key)
        self._kb.press(k)
        time.sleep(seconds)
        self._kb.release(k)

    def key_down(self, key):
        k = self._key(key)
        if k not in self.keys_down:
            self._kb.press(k)
            self.keys_down.add(k)

    def key_up(self, key):
        k = self._key(key)
        if k in self.keys_down:
            self._kb.release(k)
            self.keys_down.discard(k)

    def release_all(self):
        for k in list(self.keys_down):
            self._kb.release(k)
        self.keys_down.clear()
        self.set_move(None)

    def click(self, x, y):
        sx, sy = self.source.to_screen(x, y)
        self._mouse.position = (sx, sy)
        self._mouse.click(self._Button.left)

    def cast(self, x, y, press):
        """Smart-cast: move the mouse onto the target, then press a mouse button/key."""
        sx, sy = self.source.to_screen(x, y)
        self._mouse.position = (sx, sy)
        time.sleep(0.02)
        if press in ("mouse_right", "right"):
            self._mouse.click(self._Button.right)
        elif press in ("mouse_left", "left"):
            self._mouse.click(self._Button.left)
        else:
            k = self._key(press)
            self._kb.press(k)
            time.sleep(self.tap_seconds)
            self._kb.release(k)

    def drag(self, fx, fy, tx, ty, seconds=0.15):
        """Press at (fx, fy), glide to (tx, ty), release - a touch-style swipe.
        Blocks for `seconds`."""
        sx0, sy0 = self.source.to_screen(fx, fy)
        sx1, sy1 = self.source.to_screen(tx, ty)
        steps = max(4, int(seconds / 0.015))
        self._mouse.position = (sx0, sy0)
        self._mouse.press(self._Button.left)
        try:
            for i in range(1, steps + 1):
                f = i / steps
                self._mouse.position = (int(sx0 + (sx1 - sx0) * f), int(sy0 + (sy1 - sy0) * f))
                time.sleep(seconds / steps)
        finally:
            self._mouse.release(self._Button.left)

    def close(self):
        self.release_all()


def build_input(spec: dict, source: FrameSource) -> InputBackend:
    kind = spec.get("type", "keyboard_mouse")
    if kind == "none":
        return NullInput()
    if kind == "source":
        return source.input_backend()
    if kind == "keyboard_mouse":
        return KeyboardMouseInput(source, spec.get("move_keys"), spec.get("deadzone", 0.35))
    raise ValueError(f"unknown input type {kind!r}")
