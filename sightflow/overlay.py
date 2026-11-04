"""Debug overlay: boxes per label, anchor lines, current state and fired rule."""
from __future__ import annotations

import zlib

import cv2
import numpy as np

from .detection import Detection, LabelGroup


def _color(label: str) -> tuple[int, int, int]:
    h = zlib.crc32(label.encode()) % 180
    hsv = np.uint8([[[h, 200, 255]]])
    b, g, r = cv2.cvtColor(hsv, cv2.COLOR_HSV2BGR)[0, 0]
    return int(b), int(g), int(r)


def draw(frame: np.ndarray, groups: dict[str, LabelGroup], me: Detection,
         state: str, rule: str | None, fps: float | None = None,
         move=None, clicks=()) -> np.ndarray:
    img = frame.copy()
    for label, g in groups.items():
        c = _color(label)
        for i, d in enumerate(g.items):
            p1 = (int(d.left), int(d.top))
            p2 = (int(d.left + d.w), int(d.top + d.h))
            cv2.rectangle(img, p1, p2, c, 1)
            cv2.putText(img, label, (p1[0], p1[1] - 3), cv2.FONT_HERSHEY_SIMPLEX, 0.35, c, 1)
            if i == 0 and me.exists and d is not me:
                cv2.line(img, (int(me.x), int(me.y)), (int(d.x), int(d.y)), c, 1)
    if me.exists and move is not None:
        end = (int(me.x + move[0] * 40), int(me.y + move[1] * 40))
        cv2.arrowedLine(img, (int(me.x), int(me.y)), end, (255, 255, 255), 2, tipLength=0.3)
    for x, y in clicks:
        cv2.drawMarker(img, (int(x), int(y)), (0, 255, 255), cv2.MARKER_CROSS, 16, 2)
    lines = [f"state: {state}", f"rule: {rule or '-'}"]
    if fps is not None:
        lines.append(f"fps: {fps:.1f}")
    y = img.shape[0] - 10 - 16 * (len(lines) - 1)
    for line in lines:
        cv2.putText(img, line, (8, y), cv2.FONT_HERSHEY_SIMPLEX, 0.45, (0, 0, 0), 3)
        cv2.putText(img, line, (8, y), cv2.FONT_HERSHEY_SIMPLEX, 0.45, (255, 255, 255), 1)
        y += 16
    return img
