"""Self-motion estimate and stuck detection.

Works for both kinds of camera:
  * fixed camera   - "me" moves across the screen
  * follow camera  - "me" stays near the centre and the WORLD scrolls instead

Real movement = (me's screen movement) - (background scroll). The background
scroll is estimated with phase correlation on small grayscale frames.

Exposed to flow conditions:
  speed        estimated real movement speed of me, px/s (smoothed)
  stuck_time   seconds we have been trying to move but barely moving
  last_move    the movement direction sent last tick (.x, .y; 0,0 if none)
"""
from __future__ import annotations

import math
from dataclasses import dataclass

import cv2
import numpy as np

from .detection import Detection


@dataclass
class Vec:
    x: float = 0.0
    y: float = 0.0

    @property
    def any(self) -> bool:
        return self.x != 0 or self.y != 0


class MotionTracker:
    def __init__(self, min_speed: float = 25.0, camera: bool = True, width: int = 160,
                 smoothing: float = 0.5):
        self.min_speed = min_speed      # below this (px/s) while moving = stuck
        self.camera = camera            # estimate background scroll
        self.width = width
        self.smoothing = smoothing
        self.reset()

    def reset(self):
        self._gray = None
        self._me = None
        self._t = None
        self.speed = 0.0
        self.stuck_time = 0.0
        self.scroll = (0.0, 0.0)
        self.last_move = Vec()

    def _small(self, frame: np.ndarray):
        h, w = frame.shape[:2]
        scale = self.width / w
        g = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        g = cv2.resize(g, (self.width, max(1, int(h * scale))), interpolation=cv2.INTER_AREA)
        return np.float32(g), scale

    def update(self, frame: np.ndarray, me: Detection, now: float):
        """Call once per tick BEFORE the flow decides (uses last tick's move)."""
        small, scale = self._small(frame) if self.camera else (None, 1.0)
        if self._t is not None:
            dt = max(now - self._t, 1e-6)
            sx = sy = 0.0
            if self.camera and self._gray is not None and self._gray.shape == small.shape:
                (sx, sy), resp = cv2.phaseCorrelate(self._gray, small)
                if resp < 0.05:  # no reliable match (scene cut, menu...)
                    sx = sy = 0.0
                sx, sy = sx / scale, sy / scale
            mx = my = 0.0
            if me.exists and self._me is not None:
                mx, my = me.x - self._me[0], me.y - self._me[1]
            self.scroll = (sx, sy)
            inst = math.hypot(mx - sx, my - sy) / dt
            a = self.smoothing
            self.speed = a * self.speed + (1 - a) * inst
            if self.last_move.any and self.speed < self.min_speed:
                self.stuck_time += dt
            else:
                self.stuck_time = 0.0
        self._gray = small
        self._me = (me.x, me.y) if me.exists else None
        self._t = now

    def after_decision(self, move):
        self.last_move = Vec(*move) if move is not None else Vec()

    def names(self) -> dict:
        return {"speed": self.speed, "stuck_time": self.stuck_time, "last_move": self.last_move}
