"""In-process bridge so sightflow can drive the arena headless and faster than
real time. The bot still only sees rendered PIXELS: it never reads game state.

Used by arena_sim.yaml as:  source: {type: python, factory: "bridge:make_sim"}
"""
from __future__ import annotations

import os

import numpy as np

from arena import H, W, Arena


class SimBridge:
    """Acts as both a sightflow frame source and its input backend."""

    origin = (0, 0)

    def __init__(self, seed: int = 0, dt: float = 0.05):
        os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
        import pygame

        self._pg = pygame
        self.surface = pygame.Surface((W, H))
        self.game = Arena(seed)
        self.dt = dt
        self._move = None
        self._clicks: list = []
        self._keys: list = []
        self._first = True

    # ---- source
    def grab(self) -> np.ndarray:
        if not self._first:  # apply the inputs decided on the previous frame
            self.game.step(self.dt, self._move, self._clicks, self._keys)
        self._first = False
        self._clicks, self._keys = [], []
        self.game.render(self.surface)
        rgb = self._pg.surfarray.array3d(self.surface).swapaxes(0, 1)
        return np.ascontiguousarray(rgb[:, :, ::-1])  # -> BGR

    def time(self) -> float:
        return self.game.t

    def close(self):
        pass

    # ---- input
    def input_backend(self):
        return self

    def set_move(self, vec):
        self._move = vec

    def tap(self, key):
        self._keys.append(str(key).lower())

    def hold(self, key, seconds):
        self._keys.append(str(key).lower())

    def click(self, x, y):
        self._clicks.append((x, y))

    def drag(self, fx, fy, tx, ty, seconds=0.15):
        """Touch-style aim: fire from the player in the swipe direction."""
        p = self.game.player
        self._clicks.append((p.x + (tx - fx) * 10, p.y + (ty - fy) * 10))


def make_sim(seed: int = 0, dt: float = 0.05):
    return SimBridge(seed, dt)
