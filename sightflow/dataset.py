"""Capture frames, draw YOLO boxes, autolabel and train a model.

Everything lives in ``<project>/datasets/<name>/``::

    images/train/*.jpg   images/val/*.jpg
    labels/train/*.txt   labels/val/*.txt
    classes.json         data.yaml

Boxes are YOLO normalised rows: ``class cx cy w h`` (0..1, centre + size).
The class order is written to ``classes.json`` and ``data.yaml`` so an older
dataset never changes meaning when the detector labels are edited.
"""
from __future__ import annotations

import json
import re
import shutil
import threading
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Sequence

import cv2
import numpy as np
import yaml

NAME_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
IMAGE_EXTS = (".jpg", ".jpeg", ".png")


def sanitize_name(name: str | None, fallback: str = "dataset") -> str:
    s = re.sub(r"[^A-Za-z0-9_-]+", "_", str(name or "")).strip("_")
    return s[:64] or fallback


def _clamp(v: float) -> float:
    return max(0.0, min(1.0, float(v)))


@dataclass
class Box:
    cls: int
    x: float
    y: float
    w: float
    h: float

    def yolo(self) -> str:
        return (f"{int(self.cls)} {_clamp(self.x):.6f} {_clamp(self.y):.6f} "
                f"{_clamp(self.w):.6f} {_clamp(self.h):.6f}")

    def dict(self) -> dict:
        return {"cls": int(self.cls), "x": round(self.x, 4), "y": round(self.y, 4),
                "w": round(self.w, 4), "h": round(self.h, 4)}


class DatasetStore:
    def __init__(self, project_dir: str | Path, name: str, classes: Sequence[str] = ()):
        self.project_dir = Path(project_dir)
        self.name = sanitize_name(name)
        self.root = self.project_dir / "datasets" / self.name
        self.root.mkdir(parents=True, exist_ok=True)
        self.classes = self._init_classes(classes)
        for split in ("train", "val"):
            (self.root / "images" / split).mkdir(parents=True, exist_ok=True)
            (self.root / "labels" / split).mkdir(parents=True, exist_ok=True)
        self.write_meta()

    # --------------------------------------------------------------- metadata
    def _init_classes(self, classes: Sequence[str]) -> list[str]:
        meta = self.root / "classes.json"
        if meta.exists():
            try:
                old = json.loads(meta.read_text(encoding="utf-8"))
                if isinstance(old, list) and old and old != ["object"]:
                    return [str(c) for c in old]
            except (OSError, ValueError):
                pass
        clean = [str(c) for c in dict.fromkeys(classes) if str(c).strip()]
        return clean or ["object"]

    def write_meta(self):
        (self.root / "classes.json").write_text(
            json.dumps(self.classes, ensure_ascii=False, indent=2), encoding="utf-8")
        self.write_data_yaml()

    def write_data_yaml(self):
        val_has = any((self.root / "images" / "val").glob("*.*"))
        data = {
            "path": str(self.root.resolve()),
            "train": "images/train",
            "val": "images/val" if val_has else "images/train",
            "names": {i: c for i, c in enumerate(self.classes)},
        }
        (self.root / "data.yaml").write_text(
            yaml.safe_dump(data, sort_keys=False, allow_unicode=True), encoding="utf-8")

    @property
    def data_yaml(self) -> Path:
        return self.root / "data.yaml"

    def class_id(self, label: str) -> int | None:
        try:
            return self.classes.index(str(label))
        except ValueError:
            return None

    # ------------------------------------------------------------------ paths
    def _safe(self, rel: str | Path) -> Path:
        rel = str(rel).replace("\\", "/").lstrip("/")
        p = (self.root / rel).resolve()
        if not p.is_relative_to(self.root.resolve()):
            raise ValueError("path escapes the dataset")
        return p

    def _label_path(self, image_rel: str) -> Path:
        rel = str(image_rel).replace("\\", "/")
        parts = rel.split("/")
        if len(parts) != 3 or parts[0] != "images":
            raise ValueError("bad image id")
        return self._safe(f"labels/{parts[1]}/{Path(parts[2]).stem}.txt")

    def list_images(self) -> list[Path]:
        out: list[Path] = []
        for split in ("train", "val"):
            out += [p for p in (self.root / "images" / split).iterdir()
                    if p.is_file() and p.suffix.lower() in IMAGE_EXTS]
        return sorted(out, key=lambda p: p.stat().st_mtime, reverse=True)

    def _rel(self, p: Path) -> str:
        return p.relative_to(self.root).as_posix()

    @staticmethod
    def split_of(image_rel: str) -> str:
        parts = str(image_rel).replace("\\", "/").split("/")
        return parts[1] if len(parts) > 1 else "train"

    # ------------------------------------------------------------------ frames
    def capture(self, frame: np.ndarray, split: str | None = None) -> dict:
        if frame is None or frame.size == 0:
            raise ValueError("no frame")
        n = len(self.list_images())
        split = split if split in ("train", "val") else ("val" if n % 5 == 4 else "train")
        name = f"{int(time.time() * 1000)}_{uuid.uuid4().hex[:6]}.jpg"
        rel = f"images/{split}/{name}"
        path = self._safe(rel)
        if not cv2.imwrite(str(path), frame, [cv2.IMWRITE_JPEG_QUALITY, 92]):
            raise OSError(f"could not write {path}")
        return {"id": rel, "split": split, "boxes": 0, "file": name}

    def read(self, image_rel: str) -> np.ndarray | None:
        p = self._safe(image_rel)
        return cv2.imread(str(p)) if p.exists() else None

    def load_boxes(self, image_rel: str) -> list[dict]:
        p = self._label_path(image_rel)
        if not p.exists():
            return []
        out = []
        for line in p.read_text(encoding="utf-8").splitlines():
            parts = line.split()
            if len(parts) < 5:
                continue
            try:
                cls = int(float(parts[0]))
                x, y, w, h = (float(v) for v in parts[1:5])
            except ValueError:
                continue
            if 0 <= cls < len(self.classes):
                out.append(Box(cls, _clamp(x), _clamp(y), _clamp(w), _clamp(h)).dict())
        return out

    def save_boxes(self, image_rel: str, boxes: Iterable[dict]) -> int:
        rows = []
        for b in boxes:
            cls = int(b.get("cls", -1))
            if not (0 <= cls < len(self.classes)):
                continue
            w, h = _clamp(b.get("w", 0)), _clamp(b.get("h", 0))
            if w < 0.002 or h < 0.002:
                continue
            rows.append(Box(cls, _clamp(b.get("x", 0)), _clamp(b.get("y", 0)), w, h).yolo())
        p = self._label_path(image_rel)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("\n".join(rows) + ("\n" if rows else ""), encoding="utf-8")
        return len(rows)

    def delete(self, image_rel: str) -> bool:
        p = self._safe(image_rel)
        if not p.exists():
            return False
        p.unlink()
        label = self._label_path(image_rel)
        if label.exists():
            label.unlink()
        return True

    def frames(self, limit: int = 200) -> list[dict]:
        out = []
        for p in self.list_images()[:limit]:
            rel = self._rel(p)
            out.append({"id": rel, "split": self.split_of(rel), "boxes": len(self.load_boxes(rel))})
        return out

    def stats(self) -> dict:
        imgs = self.list_images()
        boxes = sum(len(self.load_boxes(self._rel(p))) for p in imgs)
        labeled = sum(1 for p in imgs if self._label_path(self._rel(p)).exists())
        return {"name": self.name, "classes": list(self.classes),
                "images": len(imgs), "labeled": labeled, "boxes": boxes,
                "train": len(list((self.root / "images" / "train").glob("*.*"))),
                "val": len(list((self.root / "images" / "val").glob("*.*"))),
                "path": str(self.root)}

    # --------------------------------------------------------------- autolabel
    def autolabel(self, image_rel: str, detections: Sequence,
                  class_map: dict[str, int] | None = None) -> int:
        frame = self.read(image_rel)
        if frame is None:
            return 0
        h, w = frame.shape[:2]
        rows = []
        for d in detections:
            if not getattr(d, "exists", True):
                continue
            label = str(getattr(d, "label", ""))
            cls = (class_map or {}).get(label, self.class_id(label))
            if cls is None:
                continue
            bw = max(2.0, float(getattr(d, "w", 2)))
            bh = max(2.0, float(getattr(d, "h", 2)))
            x, y = float(d.x), float(d.y)
            x0, x1 = max(0.0, x - bw / 2), min(w - 1.0, x + bw / 2)
            y0, y1 = max(0.0, y - bh / 2), min(h - 1.0, y + bh / 2)
            if x1 - x0 < 2 or y1 - y0 < 2:
                continue
            rows.append(Box(cls, (x0 + x1) / 2 / w, (y0 + y1) / 2 / h,
                            (x1 - x0) / w, (y1 - y0) / h).dict())
        return self.save_boxes(image_rel, rows)


def train_yolo(data_yaml: str | Path, name: str, model: str = "yolo11n.pt",
               epochs: int = 40, imgsz: int = 960, batch: int = 8,
               project_dir: str | Path | None = None, models_dir: str | Path | None = None,
               on_progress=None) -> Path:
    """Train ultralytics YOLO and copy best.pt into models/<name>.pt."""
    from ultralytics import YOLO

    data_yaml = Path(data_yaml).resolve()
    if not data_yaml.exists():
        raise FileNotFoundError(f"data.yaml not found: {data_yaml}")
    project_dir = Path(project_dir or data_yaml.parent.parent.parent)
    models_dir = Path(models_dir or (project_dir / "models"))
    models_dir.mkdir(parents=True, exist_ok=True)
    runs = Path(project_dir) / "runs"
    yolo = YOLO(model)

    def _cb(trainer):
        if on_progress is None:
            return
        epoch = int(getattr(trainer, "epoch", 0)) + 1
        metrics = getattr(trainer, "metrics", {}) or {}
        m50 = metrics.get("metrics/mAP50(B)") if isinstance(metrics, dict) else None
        msg = f"epoch {epoch}/{epochs}"
        if m50 is not None:
            msg += f"  mAP50 {float(m50):.3f}"
        on_progress(min(1.0, epoch / max(1, epochs)), msg)

    try:
        yolo.add_callback("on_train_epoch_end", _cb)
    except Exception:
        pass
    yolo.train(data=str(data_yaml), epochs=int(epochs), imgsz=int(imgsz), batch=int(batch),
               project=str(runs), name=name, exist_ok=True, verbose=False)

    best = Path(getattr(getattr(yolo, "trainer", None), "best", "") or
                (runs / name / "weights" / "best.pt"))
    if not best.exists():
        found = sorted((runs / name / "weights").glob("best.*"))
        if not found:
            raise FileNotFoundError("training finished but best.pt was not found")
        best = found[0]
    dest = models_dir / f"{sanitize_name(name, 'model')}.pt"
    shutil.copyfile(best, dest)
    if on_progress:
        on_progress(1.0, f"saved {dest.name}")
    return dest


class FrameRecorder:
    """Capture the current frame every `interval` seconds while running."""

    def __init__(self, store: DatasetStore, get_frame, interval: float = 2.0):
        self.store = store
        self.get_frame = get_frame
        self.interval = max(0.1, float(interval))
        self.count = 0
        self.error: str | None = None
        self.started: float | None = None
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    @property
    def running(self) -> bool:
        return bool(self._thread and self._thread.is_alive())

    def start(self):
        if self.running:
            return
        self._stop.clear()
        self.error = None
        self.count = 0
        self.started = time.time()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def _run(self):
        while not self._stop.is_set():
            try:
                frame = self.get_frame()
                if frame is not None:
                    self.store.capture(frame)
                    self.count += 1
            except Exception as e:      # keep recording after a bad frame
                self.error = f"{type(e).__name__}: {e}"
            self._stop.wait(self.interval)

    def stop(self):
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=2.0)

    def status(self) -> dict:
        return {"running": self.running, "count": self.count,
                "interval": self.interval, "error": self.error,
                "name": self.store.name, "stats": self.store.stats()}
