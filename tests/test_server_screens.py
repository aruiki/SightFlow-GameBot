"""REST API for screen registration / testing and model listing."""
import base64
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.environ.setdefault("SDL_VIDEODRIVER", "dummy")

import cv2  # noqa: E402
import numpy as np  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from sightflow.server import create_app  # noqa: E402


def png_b64(img) -> str:
    ok, buf = cv2.imencode(".png", img)
    assert ok
    return base64.b64encode(buf.tobytes()).decode()


def frame(x=50):
    img = np.full((240, 320, 3), 20, np.uint8)
    cv2.rectangle(img, (x, 40), (x + 120, 140), (250, 250, 250), -1)
    cv2.putText(img, "TITLE", (x + 10, 110), cv2.FONT_HERSHEY_SIMPLEX, 0.9, (0, 0, 0), 2)
    return img


def project(tmp_path: Path) -> Path:
    src = json.loads((ROOT / "demo" / "arena_project.json").read_text(encoding="utf-8"))
    p = tmp_path / "proj.json"
    p.write_text(json.dumps(src), encoding="utf-8")
    return p


def test_screen_api_roundtrip(tmp_path):
    app = create_app(project(tmp_path))
    client = TestClient(app)

    r = client.get("/api/screens")
    assert r.status_code == 200 and r.json()["screens"] == []

    image = png_b64(frame())
    r = client.post("/api/screens", json={"name": "title", "image": image,
                                          "threshold": 0.8, "enter_frames": 1})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["ok"] is True
    sid = body["screen"]["id"]
    assert (tmp_path / "screens" / f"{sid}.png").exists()

    r = client.get("/api/screens")
    assert r.status_code == 200
    screens = r.json()["screens"]
    assert len(screens) == 1
    assert screens[0]["name"] == "title"
    assert screens[0]["thumbnail"]

    r = client.put(f"/api/screens/{sid}", json={"threshold": 0.9, "name": "title2"})
    assert r.status_code == 200 and r.json()["screen"]["threshold"] == 0.9
    assert r.json()["screen"]["name"] == "title2"

    r = client.post(f"/api/screens/{sid}/test", json={"image": image})
    assert r.status_code == 200 and r.json()["score"] > 0.9

    r = client.post(f"/api/screens/{sid}/capture", json={"image": image})
    assert r.status_code == 200 and r.json()["ok"] is True

    r = client.post(f"/api/screens/{sid}/templates", json={"image": image})
    assert r.status_code == 200 and r.json()["screen"]["template_count"] == 2
    r = client.get("/api/screens")
    assert r.json()["screens"][0]["template_count"] == 2
    r = client.delete(f"/api/screens/{sid}/templates/1")
    assert r.status_code == 200 and r.json()["screen"]["template_count"] == 1
    r = client.delete(f"/api/screens/{sid}/templates/0")
    assert r.status_code == 400

    r = client.get("/api/state")
    assert r.status_code == 200
    assert r.json()["screens"][0]["id"] == sid

    r = client.delete(f"/api/screens/{sid}")
    assert r.status_code == 200 and r.json()["ok"] is True
    assert not (tmp_path / "screens" / f"{sid}.png").exists()
    assert client.get("/api/screens").json()["screens"] == []


def test_screens_need_a_valid_image(tmp_path):
    app = create_app(project(tmp_path))
    client = TestClient(app)
    r = client.post("/api/screens", json={"name": "title", "image": "not base64 !!"})
    assert r.status_code == 400


def test_models_list_and_unknown_screen(tmp_path):
    app = create_app(project(tmp_path))
    client = TestClient(app)
    r = client.get("/api/models")
    assert r.status_code == 200 and isinstance(r.json()["models"], list)
    r = client.put("/api/screens/nope", json={"threshold": 0.5})
    assert r.status_code == 400
    r = client.delete("/api/screens/nope")
    assert r.status_code == 200 and r.json()["ok"] is False
