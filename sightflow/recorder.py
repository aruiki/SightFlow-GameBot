"""Record the user's own clicks and key presses (FRep-style), for the macro node.

    rec = Recorder(origin=(x, y), size=(w, h))
    rec.start(); ...; steps = rec.stop()

Each step is {"wait": seconds since the previous step, "kind": "click", "x", "y"}
(x/y as 0-1 of the captured area) or {"wait", "kind": "key", "key": name}.
Clicks outside the captured area (e.g. on SightFlow's own window) are dropped,
and so is F12 (the stop key).
"""
from __future__ import annotations

import threading
import time

STOP_KEYS = {"f12"}


def key_name(key) -> str | None:
    """pynput key -> the name the input backend understands ('a', 'space', 'enter'...)."""
    ch = getattr(key, "char", None)
    if ch:
        return ch.lower() if len(ch) == 1 else ch
    name = getattr(key, "name", None)
    return name


def to_steps(events, origin, size, min_wait: float = 0.0) -> list[dict]:
    """[(t, "click", sx, sy) | (t, "key", name)] in screen coords -> macro steps."""
    ox, oy = origin
    w, h = size
    steps, last_t = [], None
    for ev in sorted(events, key=lambda e: e[0]):
        t, kind = ev[0], ev[1]
        if kind == "click":
            x, y = (ev[2] - ox) / w, (ev[3] - oy) / h
            if not (0 <= x <= 1 and 0 <= y <= 1):
                continue
            step = {"kind": "click", "x": round(x, 4), "y": round(y, 4)}
        elif kind == "key":
            if not ev[2] or ev[2] in STOP_KEYS:
                continue
            step = {"kind": "key", "key": ev[2]}
        else:
            continue
        wait = 0.0 if last_t is None else max(min_wait, t - last_t)
        step["wait"] = round(wait, 2)
        steps.append(step)
        last_t = t
    return steps


class Recorder:
    def __init__(self, origin, size):
        self.origin, self.size = origin, size
        self.events: list[tuple] = []
        self.started = None
        self._lock = threading.Lock()
        self._listeners = []

    def add_key(self, key):
        """Feed a key press from an existing keyboard listener (one global listener per
        process is the reliable setup on X11)."""
        with self._lock:
            self.events.append((time.perf_counter(), "key", key_name(key)))

    def start(self, own_keyboard: bool = True):
        from pynput import keyboard, mouse   # raises if no desktop (e.g. a headless server)

        self.started = time.perf_counter()

        def on_click(x, y, button, pressed):
            if pressed and button == mouse.Button.left:
                with self._lock:
                    self.events.append((time.perf_counter(), "click", x, y))

        self._listeners = [mouse.Listener(on_click=on_click)]
        if own_keyboard:
            self._listeners.append(keyboard.Listener(on_press=self.add_key))
        for lst in self._listeners:
            lst.daemon = True
            lst.start()

    def stop(self) -> list[dict]:
        for lst in self._listeners:
            lst.stop()
        self._listeners = []
        with self._lock:
            return to_steps(self.events, self.origin, self.size)
