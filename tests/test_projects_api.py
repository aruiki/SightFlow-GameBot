"""Project management endpoints used by the web UI's project menu."""
import json

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("httpx")
from fastapi.testclient import TestClient  # noqa: E402

from sightflow import server  # noqa: E402


def test_new_open_rename(tmp_path, monkeypatch):
    monkeypatch.setattr(server, "PROJECTS", tmp_path / "projects")
    monkeypatch.setattr(server, "DEFAULT_PROJECT", tmp_path / "none.json")
    first = tmp_path / "a.json"
    first.write_text(json.dumps(server.new_project("first")), encoding="utf-8")
    c = TestClient(server.create_app(first))

    assert [p["name"] for p in c.get("/api/projects").json()["projects"]] == ["first"]

    assert c.post("/api/projects/new", json={"name": "テスト ゲーム"}).json()["ok"]
    projects = c.get("/api/projects").json()["projects"]
    assert {p["name"] for p in projects} == {"first", "テスト ゲーム"}
    assert c.get("/api/state").json()["project"]["name"] == "テスト ゲーム"

    c.put("/api/project/name", json={"name": "renamed"})
    assert c.get("/api/state").json()["project"]["name"] == "renamed"

    other = next(p for p in projects if p["name"] == "first")
    assert c.post("/api/projects/open", json={"file": other["file"]}).json()["ok"]
    assert c.get("/api/state").json()["project"]["name"] == "first"
    # files outside the known list are refused
    assert c.post("/api/projects/open", json={"file": "/etc/passwd"}).status_code == 404


def test_cut_out_image_becomes_a_template_detector(tmp_path, monkeypatch):
    import numpy as np

    monkeypatch.setattr(server, "DEFAULT_PROJECT", tmp_path / "none.json")
    proj = tmp_path / "g.json"
    p = server.new_project("g")
    p["settings"]["detector"] = {"type": "none"}
    p["settings"]["source"] = {"type": "demo"}
    proj.write_text(json.dumps(p), encoding="utf-8")
    c = TestClient(server.create_app(proj))
    engine = c.app.state.engine
    frame = np.zeros((100, 120, 3), np.uint8)
    frame[40:60, 50:70] = (0, 200, 255)
    frame[45:50, 55:60] = (255, 0, 0)
    engine.last_frame = frame

    r = c.post("/api/templates", json={"label": "ボタン", "x": 50, "y": 40, "w": 20, "h": 20}).json()
    assert r["ok"], r
    images = r["settings"]["detector"]["images"]
    assert images[0]["label"] == "ボタン" and (tmp_path / images[0]["image"]).is_file()
    assert c.get("/api/templates/image", params={"label": "ボタン"}).status_code == 200

    specs = server.detector_specs(r["settings"]["detector"], proj.parent)
    from sightflow.detectors import build_detectors
    (det,) = build_detectors(specs, tmp_path)
    found = det.detect(frame)
    assert found and found[0].label == "ボタン" and abs(found[0].x - 60) < 1
