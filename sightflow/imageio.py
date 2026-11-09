"""Image files that also work with Japanese (non-ASCII) paths.

cv2.imread / cv2.imwrite cannot open paths such as C:\\Users\\me\\ドキュメント\\... on
Windows: imread returns None and imwrite silently returns False. Reading and
writing the bytes in Python and letting OpenCV only (de)code them avoids that.
"""
from __future__ import annotations

from pathlib import Path

import cv2
import numpy as np


def imread(path, flags: int = cv2.IMREAD_COLOR):
    """Like cv2.imread (returns None when the file is missing or unreadable)."""
    try:
        data = np.fromfile(str(path), dtype=np.uint8)
    except OSError:
        return None
    if data.size == 0:
        return None
    return cv2.imdecode(data, flags)


def imwrite(path, img, params=None) -> bool:
    """Like cv2.imwrite; the format comes from the file extension."""
    path = Path(path)
    ok, buf = cv2.imencode(path.suffix or ".png", img, params or [])
    if not ok:
        return False
    try:
        buf.tofile(str(path))
    except OSError:
        return False
    return True
