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

from .imageio import imread


# ======================================================================= sources
class FrameSource:
    origin = (0, 0)  # screen position of frame pixel (0, 0), used to place clicks

    def grab(self) -> np.ndarray:
        raise NotImplementedError

    def time(self) -> float:
        return time.monotonic()

    def close(self):
        pass


class ImageSource(FrameSource):
    def __init__(self, path: Path):
        img = imread(path)
        if img is None:
            raise FileNotFoundError(path)
        self.img = img

    def grab(self):
        return self.img.copy()


def _find_window_rect(title: str):
    """Client-area rect (left, top, w, h) of the first visible window whose title
    contains `title`. Windows only."""
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
                found.append(hwnd)
                return False
        return True

    user32.EnumWindows(cb, 0)
    if not found:
        raise RuntimeError(f"no visible window with title containing {title!r}")
    hwnd = found[0]
    rect = wintypes.RECT()
    user32.GetClientRect(hwnd, ctypes.byref(rect))
    pt = wintypes.POINT(0, 0)
    user32.ClientToScreen(hwnd, ctypes.byref(pt))
    return pt.x, pt.y, rect.right, rect.bottom


class ScreenSource(FrameSource):
    """Screen capture via mss. On Windows mss's device contexts belong to the thread
    that made them (they die with it, and BitBlt / ReleaseDC from any other thread fail
    with "access denied"), so every thread that grabs gets its own mss instance."""

    def __init__(self, region=None, window: str | None = None, monitor: int = 1):
        self._local = threading.local()
        self._all = []
        self._all_lock = threading.Lock()
        self.window = window
        self.monitor = monitor
        self.region = region
        self._resolve()

    @property
    def sct(self):
        sct = getattr(self._local, "sct", None)
        if sct is None:
            import mss

            sct = getattr(mss, "MSS", mss.mss)()  # mss.mss() is deprecated since mss 10.2
            self._local.sct = sct
            with self._all_lock:
                self._all.append(sct)
        return sct

    def _resolve(self):
        if self.window:
            l, t, w, h = _find_window_rect(self.window)
        elif self.region:
            l, t, w, h = (int(v) for v in self.region)
        else:
            m = self.sct.monitors[self.monitor]
            l, t, w, h = m["left"], m["top"], m["width"], m["height"]
        if w <= 0 or h <= 0:
            raise RuntimeError("capture area is empty (is the window minimized?)")
        self.box = {"left": l, "top": t, "width": w, "height": h}
        self.origin = (l, t)

    def grab(self):
        if self.window:  # follow the window if it moved
            self._resolve()
        shot = np.asarray(self.sct.grab(self.box))
        return cv2.cvtColor(shot, cv2.COLOR_BGRA2BGR)

    def close(self):
        with self._all_lock:
            all_, self._all = self._all, []
        for sct in all_:
            try:  # instances made on other (maybe finished) threads can fail to close
                sct.close()
            except Exception:
                pass


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
        title = (spec.get("title") or "").strip()
        if not title:  # "" would match the first window of all
            raise RuntimeError("no window title set")
        return ScreenSource(window=title)
    if kind == "screen":
        return ScreenSource(region=spec.get("region"), monitor=spec.get("monitor", 1))
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

    def click(self, x: float, y: float):  # frame coordinates
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

    MOVE_SETTLE = 0.03   # seconds between moving the cursor and pressing the button

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

    def click(self, x, y):
        ox, oy = self.source.origin
        self._mouse.position = (int(ox + x), int(oy + y))
        # Let the game see the move first: many games (SDL, Unity...) take the click
        # position from the last mouse-move message, so an instant click lands where the
        # cursor was before.
        time.sleep(self.MOVE_SETTLE)
        self._mouse.click(self._Button.left)

    def drag(self, fx, fy, tx, ty, seconds=0.15):
        """Press at (fx, fy), glide to (tx, ty), release - a touch-style swipe.
        Blocks for `seconds`."""
        ox, oy = self.source.origin
        steps = max(4, int(seconds / 0.015))
        self._mouse.position = (int(ox + fx), int(oy + fy))
        time.sleep(self.MOVE_SETTLE)
        self._mouse.press(self._Button.left)
        try:
            for i in range(1, steps + 1):
                f = i / steps
                self._mouse.position = (int(ox + fx + (tx - fx) * f), int(oy + fy + (ty - fy) * f))
                time.sleep(seconds / steps)
        finally:
            self._mouse.release(self._Button.left)

    def close(self):
        self.set_move(None)


def build_input(spec: dict, source: FrameSource) -> InputBackend:
    kind = spec.get("type", "keyboard_mouse")
    if kind == "none":
        return NullInput()
    if kind == "source":
        return source.input_backend()
    if kind == "keyboard_mouse":
        return KeyboardMouseInput(source, spec.get("move_keys"), spec.get("deadzone", 0.35))
    raise ValueError(f"unknown input type {kind!r}")
