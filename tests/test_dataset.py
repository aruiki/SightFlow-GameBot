"""Dataset capture, YOLO boxes, autolabel and metadata."""
import json
import sys
import time
from pathlib import Path

import cv2
import numpy as np
import pytest
import yaml

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from sightflow.dataset import DatasetStore, FrameRecorder, sanitize_name  # noqa: E402
from sightflow.detection import Detection  # noqa: E402


def frame(color=(30, 30, 30)):
    img = np.full((240, 320, 3), color, np.uint8)
    cv2.rectangle(img, (40, 40), (120, 100), (0, 0, 220), -1)
    return img


def test_capture_split_and_metadata(tmp_path):
    store = DatasetStore(tmp_path, "my game", ["enemy", "bush"])
    assert store.name == "my_game"
    for _ in range(5):
        store.capture(frame())
    stats = store.stats()
    assert stats["images"] == 5
    assert stats["train"] == 4 and stats["val"] == 1     # every 5th frame goes to val

    data = yaml.safe_load(store.data_yaml.read_text(encoding="utf-8"))
    assert data["names"] == {0: "enemy", 1: "bush"}
    assert (store.root / "classes.json").exists()
    assert json.loads((store.root / "classes.json").read_text(encoding="utf-8")) == ["enemy", "bush"]


def test_data_yaml_falls_back_to_train_when_no_val(tmp_path):
    store = DatasetStore(tmp_path, "ds", ["enemy"])
    store.capture(frame())
    data = yaml.safe_load(store.data_yaml.read_text(encoding="utf-8"))
    assert data["val"] == "images/train"


def test_boxes_roundtrip_and_validation(tmp_path):
    store = DatasetStore(tmp_path, "ds", ["enemy", "bush"])
    item = store.capture(frame())
    saved = store.save_boxes(item["id"], [
        {"cls": 0, "x": 0.5, "y": 0.5, "w": 0.25, "h": 0.2},
        {"cls": 1, "x": 0.25, "y": 0.25, "w": 0.1, "h": 0.1},
        {"cls": 9, "x": 0.5, "y": 0.5, "w": 0.2, "h": 0.2},     # unknown class
        {"cls": 0, "x": 0.5, "y": 0.5, "w": 0.0, "h": 0.1},      # too small
    ])
    assert saved == 2
    boxes = store.load_boxes(item["id"])
    assert [b["cls"] for b in boxes] == [0, 1]
    assert boxes[0]["x"] == pytest.approx(0.5)
    label = store.root / "labels" / item["split"] / (Path(item["id"]).stem + ".txt")
    assert label.read_text(encoding="utf-8").count("\n") == 2


def test_autolabel_from_detections(tmp_path):
    store = DatasetStore(tmp_path, "ds", ["enemy", "bush"])
    item = store.capture(frame())
    dets = [Detection("enemy", 80, 70, 40, 30, score=0.9, id=1),
            Detection("bush", 200, 120, 60, 40, score=1.0, id=2),
            Detection("coin", 250, 200, 10, 10, score=1.0, id=3)]
    n = store.autolabel(item["id"], dets)
    assert n == 2
    boxes = store.load_boxes(item["id"])
    assert {b["cls"] for b in boxes} == {0, 1}
    assert all(0 <= b[k] <= 1 for b in boxes for k in ("x", "y", "w", "h"))


def test_delete_and_path_safety(tmp_path):
    store = DatasetStore(tmp_path, "ds", ["enemy"])
    item = store.capture(frame())
    assert store.read(item["id"]) is not None
    assert store.delete(item["id"]) is True
    assert store.read(item["id"]) is None
    assert store.delete(item["id"]) is False

    with pytest.raises(ValueError):
        store.read("../outside.jpg")
    with pytest.raises(ValueError):
        store.load_boxes("not/images/thing.jpg")


def test_class_order_is_stable_when_detector_labels_change(tmp_path):
    first = DatasetStore(tmp_path, "ds", ["enemy", "bush"])
    first.capture(frame())
    second = DatasetStore(tmp_path, "ds", ["bush", "coin"])   # detector labels edited later
    assert second.classes == ["enemy", "bush"]                 # old dataset keeps its meaning


def test_sanitize_name():
    assert sanitize_name("my game!") == "my_game"
    assert sanitize_name("") == "dataset"


def test_frame_recorder_captures_periodically(tmp_path):
    store = DatasetStore(tmp_path, "rec", ["enemy"])
    rec = FrameRecorder(store, lambda: frame(), interval=0.05)
    rec.start()
    time.sleep(0.22)
    rec.stop()
    assert rec.count >= 2
    assert store.stats()["images"] == rec.count
    assert rec.status()["running"] is False
