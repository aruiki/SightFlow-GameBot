"""Detection results and the per-label groups exposed to flow conditions."""
from __future__ import annotations

import math
from dataclasses import dataclass, field


@dataclass
class Detection:
    """One detected object. x/y are the box CENTER in frame pixels."""

    label: str
    x: float
    y: float
    w: float
    h: float
    score: float = 1.0
    exists: bool = True
    # Filled in relative to the anchor ("me") when the context is built.
    dx: float = 0.0
    dy: float = 0.0
    distance: float = math.inf

    @property
    def area(self) -> float:
        return self.w * self.h

    @property
    def left(self) -> float:
        return self.x - self.w / 2

    @property
    def top(self) -> float:
        return self.y - self.h / 2

    @property
    def angle(self) -> float:
        """Direction from the anchor in degrees (0 = right, 90 = down)."""
        return math.degrees(math.atan2(self.dy, self.dx))

    def __bool__(self) -> bool:
        return self.exists


def missing(label: str) -> Detection:
    """Placeholder returned when nothing was found. Every comparison on it is
    harmless: distance is inf, exists is False, so `enemy.nearest.distance < 100`
    is simply False instead of raising."""
    return Detection(label, 0.0, 0.0, 0.0, 0.0, 0.0, exists=False)


@dataclass
class LabelGroup:
    """All detections of one label, sorted nearest-first relative to the anchor."""

    label: str
    items: list[Detection] = field(default_factory=list)

    @property
    def count(self) -> int:
        return len(self.items)

    @property
    def any(self) -> bool:
        return bool(self.items)

    @property
    def nearest(self) -> Detection:
        return self.items[0] if self.items else missing(self.label)

    @property
    def farthest(self) -> Detection:
        return self.items[-1] if self.items else missing(self.label)

    @property
    def best(self) -> Detection:
        """Highest-scoring (then largest) detection, ignoring distance."""
        if not self.items:
            return missing(self.label)
        return max(self.items, key=lambda d: (d.score, d.area))

    def within(self, radius: float) -> int:
        """Number of detections closer than `radius` pixels to the anchor."""
        return sum(1 for d in self.items if d.distance < radius)

    def __bool__(self) -> bool:
        return self.any

    def __len__(self) -> int:
        return self.count
