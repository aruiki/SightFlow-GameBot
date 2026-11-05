"""REST: dataset capture, annotation, autolabel and the training job."""
import base64
import json
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.environ.setdefault("SDL_VIDEODRIVER", "dummy")

import cv2  # noqa: E402
import numpy as np  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

import sightflow.server as server  # noqa: E402


def project(tmp_path: Path) -> Path:
    src = json.loads((ROOT / "demo" / "arena_project.json").read_text(encoding="utf-8"))
    p = tmp_path / "proj.json"
    p.write_text(json.dumps(src), encoding="utf-8")
    return p


def image_b64() -> str:
    img = np.full((240, 320, 3), 20, np.uint8)
    cv2.rectangle(img, (50, 50), (150, 140), (0, 0, 220), -1)
    ok, buf = cv2.imencode(".png", img)
    assert ok
    return base64.b64encode(buf.tobytes()).decode()


def wait_running(client, timeout=5.0):
    end = time.time() + timeout
    while time.time() < end:
        st = client.get("/api/state").json()
        if st.get("status") == "running":
            return st
        time.sleep(0.1)
    return client.get("/api/state").json()


def test_dataset_api_capture_annotate_delete(tmp_path):
    app = server.create_app(project(tmp_path))
    client = TestClient(app)
    wait_running(client)

    r = client.get("/api/dataset", params={"name": "ds"})
    assert r.status_code == 200 and r.json()["images"] == 0

    r = client.post("/api/dataset/capture", json={"dataset": "ds", "image": image_b64()})
    assert r.status_code == 200, r.text
    frame_id = r.json()["id"]
    assert (tmp_path / "datasets" / "ds" / frame_id).exists()

    r = client.get("/api/dataset", params={"name": "ds"})
    body = r.json()
    assert body["images"] == 1 and body["frames"][0]["id"] == frame_id
    assert body["frames"][0]["thumbnail"]

    r = client.get("/api/dataset/frame", params={"name": "ds", "frame": frame_id})
    assert r.status_code == 200 and r.json()["image"] and r.json()["boxes"] == []

    boxes = [{"cls": 0, "x": 0.5, "y": 0.5, "w": 0.25, "h": 0.2}]
    r = client.put("/api/dataset/labels", json={"dataset": "ds", "frame": frame_id, "boxes": boxes})
    assert r.status_code == 200 and r.json()["saved"] == 1

    r = client.get("/api/dataset/frame", params={"name": "ds", "frame": frame_id})
    assert r.json()["boxes"][0]["x"] == 0.5

    r = client.post("/api/dataset/autolabel", json={"dataset": "ds", "frame": frame_id})
    assert r.status_code == 200, r.text
    assert r.json()["frames"] == 1

    r = client.get("/api/dataset/export", params={"name": "ds"})
    assert r.status_code == 200 and "names:" in r.json()["yaml"]

    r = client.delete("/api/dataset/frame", params={"name": "ds", "frame": frame_id})
    assert r.status_code == 200 and r.json()["ok"] is True
    assert client.get("/api/dataset", params={"name": "ds"}).json()["images"] == 0


def test_train_job_with_fake_trainer(tmp_path, monkeypatch):
    calls = {}

    def fake_train(data_yaml, name, model, epochs, imgsz, batch, project_dir=None,
                   models_dir=None, on_progress=None):
        calls.update(dict(name=name, model=model, epochs=epochs, data=str(data_yaml)))
        if on_progress:
            on_progress(1.0, "fake done")
        return Path(models_dir or tmp_path) / "fake.pt"

    monkeypatch.setattr(server, "train_yolo", fake_train)
    app = server.create_app(project(tmp_path))
    client = TestClient(app)
    wait_running(client)
    client.post("/api/dataset/capture", json={"dataset": "ds", "image": image_b64()})

    r = client.post("/api/train", json={"dataset": "ds", "model": "yolo11n.pt",
                                        "epochs": 1, "imgsz": 320, "batch": 1})
    assert r.status_code == 200, r.text
    end = time.time() + 5
    state = {}
    while time.time() < end:
        state = client.get("/api/train").json()
        if state.get("status") in ("done", "error"):
            break
        time.sleep(0.05)
    assert state.get("status") == "done", state
    assert state.get("model") == "fake.pt"
    assert calls["name"] == "ds" and calls["epochs"] == 1


def test_record_api_start_stop(tmp_path):
    app = server.create_app(project(tmp_path))
    client = TestClient(app)
    wait_running(client)
    client.post("/api/mode", json={"mode": "watch"})
    time.sleep(0.6)
    r = client.post("/api/dataset/record", json={"dataset": "rec", "interval": 0.2})
    assert r.status_code == 200 and r.json()["running"] is True
    time.sleep(0.9)
    r = client.get("/api/dataset/record")
    assert r.json()["running"] is True and r.json()["count"] >= 1
    r = client.post("/api/dataset/record", json={"action": "stop"})
    assert r.status_code == 200 and r.json()["running"] is False
    assert client.get("/api/dataset", params={"name": "rec"}).json()["images"] >= 1


def test_playback_pause_step_resume(tmp_path):
    app = server.create_app(project(tmp_path))
    client = TestClient(app)
    wait_running(client)
    client.post("/api/mode", json={"mode": "watch"})
    r = client.post("/api/playback", json={"action": "pause"})
    assert r.status_code == 200 and r.json()["paused"] is True
    assert client.get("/api/state").json()["paused"] is True
    r = client.post("/api/playback", json={"action": "step"})
    assert r.status_code == 200 and r.json()["paused"] is True
    r = client.post("/api/playback", json={"action": "resume"})
    assert r.status_code == 200 and r.json()["paused"] is False
    r = client.post("/api/playback", json={"action": "bogus"})
    assert r.status_code == 400


def test_train_validation_errors(tmp_path):
    app = server.create_app(project(tmp_path))
    client = TestClient(app)
    r = client.post("/api/train", json={"dataset": "empty", "epochs": 1})
    assert r.status_code == 400
    assert "no images" in r.json()["error"]

    client.post("/api/dataset/capture", json={"dataset": "ds", "image": image_b64()})
    r = client.post("/api/train", json={"dataset": "ds", "epochs": 0})
    assert r.status_code == 400 and "epochs" in r.json()["error"]
