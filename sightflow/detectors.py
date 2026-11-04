"""Detector plugins. Each turns a BGR frame into a list of Detections.

Built in:
  color     HSV colour range + contour blobs (no training needed)
  template  OpenCV template matching against a cropped sprite image
  yolo      Ultralytics YOLO model (optional dependency, one model -> many labels)

Register your own with @register("name").
"""
from __future__ import annotations

from pathlib import Path
from typing import Callable

import cv2
import numpy as np

from .detection import Detection

_REGISTRY: dict[str, Callable[[dict, Path], "Detector"]] = {}


def register(name: str):
    def deco(cls):
        _REGISTRY[name] = cls
        return cls

    return deco


def build_detectors(specs: list[dict], base_dir: Path) -> list["Detector"]:
    out = []
    for spec in specs:
        kind = spec.get("type")
        if kind not in _REGISTRY:
            raise ValueError(f"unknown detector type {kind!r}; known: {sorted(_REGISTRY)}")
        out.append(_REGISTRY[kind](spec, base_dir))
    return out


class Detector:
    """Base class. Handles the optional `roi: [x, y, w, h]` crop and `max_count`."""

    def __init__(self, spec: dict, base_dir: Path):
        self.spec = spec
        self.roi = spec.get("roi")
        self.max_count = spec.get("max_count")

    def detect(self, frame: np.ndarray) -> list[Detection]:
        ox = oy = 0
        img = frame
        if self.roi:
            x, y, w, h = (int(v) for v in self.roi)
            img = frame[y : y + h, x : x + w]
            ox, oy = x, y
        dets = self._detect(img)
        for d in dets:
            d.x += ox
            d.y += oy
        if self.max_count is not None:
            dets = sorted(dets, key=lambda d: (d.score, d.area), reverse=True)[: self.max_count]
        return dets

    def _detect(self, img: np.ndarray) -> list[Detection]:
        raise NotImplementedError


@register("color")
class ColorDetector(Detector):
    """Blobs whose HSV colour falls in one or more ranges.

    hsv: [[h_lo, s_lo, v_lo], [h_hi, s_hi, v_hi]]   (OpenCV scale: H 0-179)
         or a list of such pairs (e.g. red wraps around H=0/179).
    """

    def __init__(self, spec, base_dir):
        super().__init__(spec, base_dir)
        self.label = spec["label"]
        ranges = spec["hsv"]
        if not isinstance(ranges[0][0], (list, tuple)):
            ranges = [ranges]
        self.ranges = [(np.array(lo, np.uint8), np.array(hi, np.uint8)) for lo, hi in ranges]
        self.min_area = float(spec.get("min_area", 30))
        self.max_area = float(spec.get("max_area", float("inf")))
        self.morph = int(spec.get("morph", 3))

    def _detect(self, img):
        hsv = cv2.cvtColor(img, cv2.COLOR_BGR2HSV)
        mask = np.zeros(hsv.shape[:2], np.uint8)
        for lo, hi in self.ranges:
            mask |= cv2.inRange(hsv, lo, hi)
        if self.morph > 1:
            kernel = np.ones((self.morph, self.morph), np.uint8)
            mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, kernel)
        contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        out = []
        for c in contours:
            area = cv2.contourArea(c)
            if not (self.min_area <= area <= self.max_area):
                continue
            x, y, w, h = cv2.boundingRect(c)
            fill = area / max(w * h, 1)
            out.append(Detection(self.label, x + w / 2, y + h / 2, w, h, score=round(fill, 3)))
        return out


@register("template")
class TemplateDetector(Detector):
    """Matches a sprite image. `image` is relative to the config file."""

    def __init__(self, spec, base_dir):
        super().__init__(spec, base_dir)
        self.label = spec["label"]
        path = (base_dir / spec["image"]).resolve()
        tpl = cv2.imread(str(path), cv2.IMREAD_COLOR)
        if tpl is None:
            raise FileNotFoundError(path)
        self.tpl = tpl
        self.threshold = float(spec.get("threshold", 0.8))

    def _detect(self, img):
        th, tw = self.tpl.shape[:2]
        if img.shape[0] < th or img.shape[1] < tw:
            return []
        res = cv2.matchTemplate(img, self.tpl, cv2.TM_CCOEFF_NORMED)
        ys, xs = np.where(res >= self.threshold)
        cands = sorted(zip(res[ys, xs], xs, ys), reverse=True)
        out: list[Detection] = []
        for score, x, y in cands:  # greedy non-max suppression
            cx, cy = x + tw / 2, y + th / 2
            if all(abs(cx - d.x) > tw / 2 or abs(cy - d.y) > th / 2 for d in out):
                out.append(Detection(self.label, cx, cy, tw, th, float(score)))
        return out


@register("yolo")
class YoloDetector(Detector):
    """labels: {flow_label: class_id_or_name}. Requires `pip install ultralytics`."""

    def __init__(self, spec, base_dir):
        super().__init__(spec, base_dir)
        try:
            from ultralytics import YOLO
        except ImportError as e:  # pragma: no cover - optional
            raise ImportError("yolo detector needs: pip install ultralytics") from e
        model = spec["model"]
        path = base_dir / model
        self.model = YOLO(str(path) if path.exists() else model)
        self.conf = float(spec.get("conf", 0.4))
        names = self.model.names
        self.map = {}
        for flow_label, cls in spec["labels"].items():
            if isinstance(cls, str):
                cls = next(k for k, v in names.items() if v == cls)
            self.map[int(cls)] = flow_label

    def _detect(self, img):
        res = self.model.predict(img, conf=self.conf, verbose=False)[0]
        out = []
        for box, cls, conf in zip(res.boxes.xywh.tolist(), res.boxes.cls.tolist(), res.boxes.conf.tolist()):
            label = self.map.get(int(cls))
            if label:
                x, y, w, h = box
                out.append(Detection(label, x, y, w, h, float(conf)))
        return out
