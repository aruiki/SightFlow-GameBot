"""Region (polygon) detection, gas masks and per-label confidence."""
import sys
from pathlib import Path

import cv2
import numpy as np
import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from sightflow.behavior import GasField, _inside  # noqa: E402
from sightflow.detection import Detection, to_dict  # noqa: E402
from sightflow.detectors import Detector, build_detectors  # noqa: E402


def region_frame():
    img = np.full((240, 320, 3), 20, np.uint8)
    cv2.rectangle(img, (100, 80), (150, 130), (0, 255, 0), -1)   # HSV ~60
    return img


def test_region_detector_returns_a_polygon():
    det = build_detectors([{
        "type": "region", "label": "bush",
        "hsv": [[55, 120, 90], [75, 255, 255]], "min_area": 500,
    }], ROOT)[0]
    dets = det.detect(region_frame())
    assert len(dets) == 1
    d = dets[0]
    assert d.region is True and d.polygon and len(d.polygon) >= 3
    assert d.x == 125 and d.y == 105
    assert d.w in (50, 51) and d.h in (50, 51)
    payload = to_dict(d)
    assert payload["region"] is True and len(payload["polygon"]) >= 3


def test_region_detector_applies_offset():
    det = build_detectors([{
        "type": "region", "label": "gas", "offset": [10, -5],
        "hsv": [[55, 120, 90], [75, 255, 255]], "min_area": 500,
    }], ROOT)[0]
    dets = det.detect(region_frame())
    assert len(dets) == 1
    assert dets[0].x == pytest.approx(135, abs=1.5)
    assert dets[0].y == pytest.approx(100, abs=1.5)


def poly_det(label, points):
    xs = [p[0] for p in points]
    ys = [p[1] for p in points]
    d = Detection(label, (min(xs) + max(xs)) / 2, (min(ys) + max(ys)) / 2,
                  max(xs) - min(xs), max(ys) - min(ys), score=1.0)
    d.region = True
    d.polygon = points
    return d


def test_gas_polygon_gives_clearance_and_escape():
    gas = GasField([poly_det("gas", [(0, 0), (100, 0), (100, 100), (0, 100)])],
                   frame_size=(320, 240))
    assert gas.covers(50, 50) is True
    assert gas.covers(150, 50) is False
    assert gas.clearance(50, 50) == 0
    assert gas.clearance(150, 50) == pytest.approx(50, abs=1.5)
    ex, ey = gas.escape_dir(150, 50)
    assert ex > 0.9 and abs(ey) < 0.2


def test_polygon_inside_is_not_the_bounding_box():
    # L shape: the missing top-right part is inside the bbox but outside the region.
    poly = [(0, 0), (50, 0), (50, 50), (100, 50), (100, 100), (0, 100)]
    bush = poly_det("bush", poly)
    assert _inside(bush, 25, 75) is True
    assert _inside(bush, 75, 25) is False


def test_per_label_confidence_filters_detections(tmp_path):
    class Fake(Detector):
        def _detect(self, img):
            return [Detection("low", 10, 10, 5, 5, score=0.4),
                    Detection("high", 20, 20, 5, 5, score=0.9)]

    fake = Fake({"label_conf": {"low": 0.5}}, tmp_path)
    assert [d.label for d in fake.detect(region_frame())] == ["high"]


def test_template_has_region_detectors_for_bush_and_gas():
    import json
    t = json.loads((ROOT / "templates" / "brawl_like.json").read_text(encoding="utf-8"))
    det = t["settings"]["detector"]
    assert [c["label"] for c in det["color"]] == ["player", "enemy"]
    assert {c["label"] for c in det["region"]} == {"bush", "gas"}
    assert all(c["type"] == "region" for c in det["region"])
