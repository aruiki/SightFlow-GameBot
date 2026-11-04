"""Lightweight multi-object tracker that gives every detection a stable ID.

Used for detectors that do not track by themselves (color, template). YOLO
detectors use Ultralytics' ByteTrack instead and arrive here with IDs set.

Greedy matching per label on centre distance; a track survives `max_missing`
frames without a match (objects briefly hidden behind a bush keep their ID).
"""
from __future__ import annotations

import itertools
import math

from .detection import Detection


class CentroidTracker:
    def __init__(self, max_jump: float = 80.0, max_missing: int = 10):
        self.max_jump = max_jump
        self.max_missing = max_missing
        self._ids = itertools.count(1)
        self._tracks: dict[int, dict] = {}  # id -> {label, x, y, missing}

    def reset(self):
        self._tracks.clear()
        self._ids = itertools.count(1)

    def update(self, dets: list[Detection]) -> list[Detection]:
        untracked = [d for d in dets if d.id < 0]
        pairs = []
        for tid, t in self._tracks.items():
            for i, d in enumerate(untracked):
                if d.label == t["label"]:
                    dist = math.hypot(d.x - t["x"], d.y - t["y"])
                    if dist <= self.max_jump:
                        pairs.append((dist, tid, i))
        pairs.sort()
        used_t, used_d = set(), set()
        for _, tid, i in pairs:
            if tid in used_t or i in used_d:
                continue
            used_t.add(tid)
            used_d.add(i)
            untracked[i].id = tid
        for i, d in enumerate(untracked):
            if i not in used_d:
                d.id = next(self._ids)
                self._tracks[d.id] = {"label": d.label}
        for tid in list(self._tracks):
            if tid in used_t or any(d.id == tid for d in untracked):
                self._tracks[tid]["missing"] = 0
            else:
                self._tracks[tid]["missing"] = self._tracks[tid].get("missing", 0) + 1
                if self._tracks[tid]["missing"] > self.max_missing:
                    del self._tracks[tid]
        for d in untracked:
            self._tracks[d.id].update(x=d.x, y=d.y)
        return dets
